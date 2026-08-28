from __future__ import annotations

from contextlib import contextmanager
import hashlib
import inspect
import json
import os
from pathlib import Path
import socket
import sys
import types
import urllib.request

import numpy as np
import pandas as pd
from safetensors import safe_open
from safetensors.torch import load_file
import torch


ROOT = Path(__file__).resolve().parents[4]
RAW_ROOT = ROOT / "data/raw/kronos_official_v2"
EXPECTED_MANIFEST = ROOT / "experiments/exp_20260827_009/expected_manifest.json"
ACQUISITION_MANIFEST = ROOT / "experiments/exp_20260827_009/artifacts/acquisition_manifest.json"
RESULT_PATH = ROOT / "experiments/exp_20260827_010/artifacts/offline_reverification_result.json"
VENDOR_ROOT = ROOT / "third_party/kronos/67b630e67f6a18c9e9be918d9b4337c960db1e9a"
EXPECTED_MANIFEST_SHA256 = "3d2b370f172b17f090099d8ef2e3d9e530ed4d6cf4cb3c2ef57d04db7747d52e"
ACQUISITION_MANIFEST_SHA256 = "811cd603d640a7acfab7776e3a97a9a753a0e3d053670587eb700f89167419c6"
EXPECTED_TOTAL_BYTES = 562_430_552
FROZEN_EXP009_INPUTS = {
    ROOT / "src/quant_research/alpha_models/external/kronos_supply_chain_v2.py": "0602f1a2daf290f4da4f2087d10406f9f0184ff04c1f0a31b92dc4c503710ec0",
    ROOT / "src/quant_research/alpha_models/external/kronos_adapter_v2.py": "215f5cccd7d6802e1499d057799a63334ac46dce280a25a64055cd983c3886fa",
    ROOT / "tests/test_kronos_supply_chain_v2.py": "c0b54f2566d4f438d8a7e29522421cc176ac3ff997bcce74d15a0f532fff87b0",
}
PAIRINGS = {
    "Kronos-mini": ("f4e68697d9d5aed55cef5c96aabc3376bcad9f81", "Kronos-Tokenizer-2k", "26966d0035065a0cae0ebad7af8ece35bc1fb51c", 2048),
    "Kronos-small": ("901c26c1332695a2a8f243eb2f37243a37bea320", "Kronos-Tokenizer-base", "0e0117387f39004a9016484a186a908917e22426", 512),
}
SYNTHETIC_PACKAGE = "_kronos_exp010_verified_vendor"


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"nonfinite:{value}")


def _strict_json(raw: bytes) -> object:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("bom")
    return json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_pairs, parse_constant=_reject_constant)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(document: object) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _check_external_hash(path: Path, expected: str, code: str) -> bytes:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(code)
    return raw


def _load_bound_manifests(expected_sha256: str, acquisition_sha256: str) -> tuple[dict[str, object], dict[str, object]]:
    if expected_sha256 != EXPECTED_MANIFEST_SHA256 or acquisition_sha256 != ACQUISITION_MANIFEST_SHA256:
        raise ValueError("cli_manifest_binding")
    expected = _strict_json(_check_external_hash(EXPECTED_MANIFEST, expected_sha256, "expected_manifest_hash"))
    acquisition = _strict_json(_check_external_hash(ACQUISITION_MANIFEST, acquisition_sha256, "acquisition_manifest_hash"))
    if type(expected) is not dict or set(expected) != {"files", "row_count", "schema_version", "total_bytes"}:
        raise ValueError("expected_manifest_schema")
    if expected["row_count"] != 19 or expected["total_bytes"] != EXPECTED_TOTAL_BYTES or type(expected["files"]) is not list or len(expected["files"]) != 19:
        raise ValueError("expected_manifest_constants")
    acquisition_keys = {
        "application_network_policy", "expected_manifest_sha256", "files",
        "logical_request_count", "network_retry_count", "raw_file_count",
        "raw_total_bytes", "schema_version", "vendored_sources",
    }
    if type(acquisition) is not dict or set(acquisition) != acquisition_keys:
        raise ValueError("acquisition_manifest_schema")
    if (
        acquisition["expected_manifest_sha256"] != expected_sha256
        or acquisition["logical_request_count"] != 19
        or acquisition["network_retry_count"] != 0
        or acquisition["raw_file_count"] != 19
        or acquisition["raw_total_bytes"] != EXPECTED_TOTAL_BYTES
    ):
        raise ValueError("acquisition_manifest_constants")
    return expected, acquisition


def _verify_exp009_sources() -> None:
    for path, expected_sha in FROZEN_EXP009_INPUTS.items():
        if not path.is_file() or _sha256(path) != expected_sha:
            raise ValueError("exp009_input_drift")


def _verify_raw(expected: dict[str, object], acquisition: dict[str, object]) -> list[dict[str, object]]:
    rows = expected["files"]
    expected_paths = {str(row["path"]) for row in rows}
    actual_paths = {path.relative_to(ROOT).as_posix() for path in RAW_ROOT.rglob("*") if path.is_file()}
    if actual_paths != expected_paths:
        raise ValueError("raw_path_bijection")
    receipts = {row["path"]: row for row in acquisition["files"]}
    if len(receipts) != 19 or set(receipts) != expected_paths:
        raise ValueError("receipt_bijection")
    verified: list[dict[str, object]] = []
    for ordinal, row in enumerate(rows, 1):
        if row["ordinal"] != ordinal:
            raise ValueError("ordinal")
        path = ROOT / str(row["path"])
        expected_bytes = row["expected_bytes"]
        sha256 = hashlib.sha256()
        git_blob = None
        if row["source_kind"] in {"HF_GIT_BLOB", "GITHUB_BLOB"}:
            git_blob = hashlib.sha1()
            git_blob.update(b"blob " + str(expected_bytes).encode("ascii") + b"\0")
        total = 0
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                total += len(block)
                if total > expected_bytes:
                    raise ValueError("raw_oversize")
                sha256.update(block)
                if git_blob is not None:
                    git_blob.update(block)
        if total != expected_bytes or sha256.hexdigest() != row["expected_sha256"]:
            raise ValueError("raw_binding")
        if row["source_kind"] == "HF_LFS":
            if row["authority_oid"] != row["expected_sha256"]:
                raise ValueError("lfs_oid")
        elif git_blob is None or git_blob.hexdigest() != row["authority_oid"]:
            raise ValueError("git_blob_oid")
        receipt = receipts[row["path"]]
        if receipt["bytes"] != total or receipt["sha256"] != sha256.hexdigest() or receipt["ordinal"] != ordinal:
            raise ValueError("receipt_binding")
        verified.append({"bytes": total, "path": row["path"], "sha256": sha256.hexdigest()})
    if sum(row["bytes"] for row in verified) != EXPECTED_TOTAL_BYTES:
        raise ValueError("raw_total")
    return verified


def _source_rows(expected: dict[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    marker = "/source/67b630e67f6a18c9e9be918d9b4337c960db1e9a/"
    for row in expected["files"]:
        if marker in row["path"]:
            result[row["path"].split(marker, 1)[1]] = row
    return result


def _read_verified_source(path: Path, expected_row: dict[str, object]) -> str:
    raw = path.read_bytes()
    if len(raw) != expected_row["expected_bytes"] or hashlib.sha256(raw).hexdigest() != expected_row["expected_sha256"]:
        raise ValueError("vendor_source_binding")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("vendor_source_bom")
    return raw.decode("utf-8", errors="strict")


def _exec_source(name: str, source: str, path: Path, package: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = str(path.resolve())
    module.__cached__ = None
    module.__package__ = package
    module.__loader__ = None
    sys.modules[name] = module
    code = compile(source, str(path.resolve()), "exec", dont_inherit=True, optimize=0)
    exec(code, module.__dict__)
    return module


@contextmanager
def _verified_vendor_modules(expected: dict[str, object]):
    names = (SYNTHETIC_PACKAGE, f"{SYNTHETIC_PACKAGE}.module", f"{SYNTHETIC_PACKAGE}.kronos")
    if any(name in sys.modules for name in names):
        raise ValueError("synthetic_module_collision")
    rows = _source_rows(expected)
    required = {"LICENSE", "model/__init__.py", "model/kronos.py", "model/module.py"}
    if set(rows) != required:
        raise ValueError("vendor_source_manifest")
    for relative in required:
        _read_verified_source(VENDOR_ROOT / relative, rows[relative])
    package = types.ModuleType(SYNTHETIC_PACKAGE)
    package.__file__ = str((VENDOR_ROOT / "model/__init__.py").resolve())
    package.__cached__ = None
    package.__package__ = SYNTHETIC_PACKAGE
    package.__path__ = [str((VENDOR_ROOT / "model").resolve())]
    sys.modules[SYNTHETIC_PACKAGE] = package
    missing = object()
    prior_aliases = {name: sys.modules.get(name, missing) for name in ("model", "model.module")}
    sys.modules["model"] = package
    try:
        module = _exec_source(
            f"{SYNTHETIC_PACKAGE}.module",
            _read_verified_source(VENDOR_ROOT / "model/module.py", rows["model/module.py"]),
            VENDOR_ROOT / "model/module.py",
            SYNTHETIC_PACKAGE,
        )
        sys.modules["model.module"] = module
        kronos = _exec_source(
            f"{SYNTHETIC_PACKAGE}.kronos",
            _read_verified_source(VENDOR_ROOT / "model/kronos.py", rows["model/kronos.py"]),
            VENDOR_ROOT / "model/kronos.py",
            SYNTHETIC_PACKAGE,
        )
        yield kronos
    finally:
        for name, prior in prior_aliases.items():
            if prior is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior
        for name in reversed(names):
            sys.modules.pop(name, None)


def _checkpoint_rows(expected: dict[str, object], checkpoint: Path) -> tuple[dict[str, object], dict[str, object]]:
    index = {ROOT / row["path"]: row for row in expected["files"]}
    try:
        return index[checkpoint / "config.json"], index[checkpoint / "model.safetensors"]
    except KeyError as error:
        raise ValueError("checkpoint_not_expected") from error


def _load_checkpoint(model_class, checkpoint: Path, expected: dict[str, object]):  # noqa: ANN001
    config_row, weights_row = _checkpoint_rows(expected, checkpoint)
    config_path, weights_path = checkpoint / "config.json", checkpoint / "model.safetensors"
    if config_path.stat().st_size != config_row["expected_bytes"] or _sha256(config_path) != config_row["expected_sha256"]:
        raise ValueError("config_binding")
    if weights_path.stat().st_size != weights_row["expected_bytes"] or _sha256(weights_path) != weights_row["expected_sha256"]:
        raise ValueError("weights_binding")
    config = _strict_json(config_path.read_bytes())
    constructor_keys = {key for key in inspect.signature(model_class.__init__).parameters if key != "self"}
    if type(config) is not dict or set(config) != constructor_keys:
        raise ValueError("config_schema")
    with safe_open(weights_path, framework="pt", device="cpu") as handle:
        keys = tuple(sorted(handle.keys()))
        metadata = tuple((key, str(handle.get_tensor(key).dtype), tuple(handle.get_tensor(key).shape)) for key in keys)
    if not metadata or any(any(type(size) is not int or size <= 0 for size in shape) for _, _, shape in metadata):
        raise ValueError("tensor_schema")
    model = model_class(**config)
    model.load_state_dict(load_file(weights_path, device="cpu"), strict=True)
    model.eval()
    return model, {"config_sha256": config_row["expected_sha256"], "tensor_count": len(metadata), "weights_sha256": weights_row["expected_sha256"]}


def _predictor(kronos, expected: dict[str, object], model_name: str):  # noqa: ANN001
    model_revision, tokenizer_name, tokenizer_revision, maximum = PAIRINGS[model_name]
    model, model_evidence = _load_checkpoint(kronos.Kronos, RAW_ROOT / model_name / model_revision, expected)
    tokenizer, tokenizer_evidence = _load_checkpoint(kronos.KronosTokenizer, RAW_ROOT / tokenizer_name / tokenizer_revision, expected)
    tokenizer.eval()
    return kronos.KronosPredictor(model, tokenizer, device="cpu", max_context=maximum), {"model": model_evidence, "tokenizer": tokenizer_evidence}


@contextmanager
def _network_blocked():
    import huggingface_hub
    original_socket = socket.socket
    original_getaddrinfo = socket.getaddrinfo
    original_urlopen = urllib.request.urlopen
    original_hf_download = huggingface_hub.hf_hub_download

    def blocked(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("network_forbidden")

    socket.socket = blocked
    socket.getaddrinfo = blocked
    urllib.request.urlopen = blocked
    huggingface_hub.hf_hub_download = blocked
    try:
        yield
    finally:
        socket.socket = original_socket
        socket.getaddrinfo = original_getaddrinfo
        urllib.request.urlopen = original_urlopen
        huggingface_hub.hf_hub_download = original_hf_download


def _run_model_checks(expected: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    sample_root = RAW_ROOT / "samples/tests/data"
    frame = pd.read_csv(sample_root / "regression_input.csv", parse_dates=["timestamps"])
    columns = ["open", "high", "low", "close", "volume", "amount"]
    with _network_blocked(), _verified_vendor_modules(expected) as kronos:
        mini, mini_evidence = _predictor(kronos, expected, "Kronos-mini")
        torch.manual_seed(20260827)
        with torch.inference_mode():
            mini_output = mini.predict(
                frame.iloc[:32][columns], frame.timestamps.iloc[:32],
                frame.timestamps.iloc[32:34].reset_index(drop=True), 2,
                sample_count=1, verbose=False,
            )
        if mini_output.shape != (2, 6) or not np.isfinite(mini_output.to_numpy()).all():
            raise ValueError("mini_output")
        small, small_evidence = _predictor(kronos, expected, "Kronos-small")
        np.random.seed(123)
        torch.manual_seed(123)
        with torch.inference_mode():
            actual = small.predict(
                frame.iloc[:256][columns], frame.timestamps.iloc[:256],
                frame.timestamps.iloc[256:264].reset_index(drop=True), 8,
                T=1.0, top_k=1, top_p=1.0, sample_count=1, verbose=False,
            )
        golden = pd.read_csv(sample_root / "regression_output_256.csv")
        np.testing.assert_allclose(actual[columns].to_numpy(), golden[columns].to_numpy(), rtol=2e-5, atol=2e-5)
    mini_result = {"checkpoint": mini_evidence, "finite": True, "rows": 2}
    small_result = {"checkpoint": small_evidence, "fixture_sha256": _sha256(sample_root / "regression_output_256.csv"), "rows": 8}
    return mini_result, small_result


def verify_offline(expected_manifest_sha256: str, acquisition_manifest_sha256: str) -> dict[str, object]:
    _verify_exp009_sources()
    expected, acquisition = _load_bound_manifests(expected_manifest_sha256, acquisition_manifest_sha256)
    raw = _verify_raw(expected, acquisition)
    source_rows = _source_rows(expected)
    source_evidence = [
        {"bytes": source_rows[path]["expected_bytes"], "path": path, "sha256": source_rows[path]["expected_sha256"]}
        for path in sorted(source_rows)
    ]
    mini, small = _run_model_checks(expected)
    end_expected, end_acquisition = _load_bound_manifests(expected_manifest_sha256, acquisition_manifest_sha256)
    if end_expected != expected or end_acquisition != acquisition:
        raise ValueError("manifests_changed_during_verification")
    end_raw = _verify_raw(end_expected, end_acquisition)
    if end_raw != raw:
        raise ValueError("raw_changed_during_verification")
    return {
        "acquisition_manifest_sha256": acquisition_manifest_sha256,
        "expected_manifest_sha256": expected_manifest_sha256,
        "mini_inference": mini,
        "network_request_count": 0,
        "raw_file_count": len(raw),
        "raw_total_bytes": sum(row["bytes"] for row in raw),
        "raw_verification_passes": 2,
        "schema_version": "KRONOS_OFFLINE_REVERIFICATION_RESULT_V1",
        "small_golden": small,
        "source_files": source_evidence,
        "terminal_status": "NEEDS_MORE_DATA",
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-acquisition-manifest-sha256", required=True)
    args = parser.parse_args(argv)
    if RESULT_PATH.exists():
        raise FileExistsError("result_preexists")
    result = verify_offline(args.expected_manifest_sha256, args.expected_acquisition_manifest_sha256)
    descriptor = os.open(RESULT_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(_json_bytes(result))
        handle.flush()
        os.fsync(handle.fileno())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
