from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from safetensors import safe_open
from safetensors.torch import load_file
import torch

from .kronos_supply_chain_v2 import (
    ACQUISITION_MANIFEST,
    EXPECTED_MANIFEST,
    RAW_ROOT,
    ROOT,
    VENDOR_ROOT,
    _sha256_path,
    _strict_json_bytes,
    load_expected_manifest,
    verify_raw_tree,
    verify_vendored_sources,
)


PAIRINGS = {
    "Kronos-mini": ("f4e68697d9d5aed55cef5c96aabc3376bcad9f81", "Kronos-Tokenizer-2k", "26966d0035065a0cae0ebad7af8ece35bc1fb51c", 2048),
    "Kronos-small": ("901c26c1332695a2a8f243eb2f37243a37bea320", "Kronos-Tokenizer-base", "0e0117387f39004a9016484a186a908917e22426", 512),
    "Kronos-base": ("2b554741eca47781b64468546e77fef3e85130e6", "Kronos-Tokenizer-base", "0e0117387f39004a9016484a186a908917e22426", 512),
}


@dataclass(frozen=True)
class KronosRequest:
    values: tuple[tuple[float, ...], ...]
    context_timestamps_ms: tuple[int, ...]
    future_timestamps_ms: tuple[int, ...]
    formation_time_ms: int
    known_at_ms: int
    labels: None = None


def _validate_request(request: KronosRequest, maximum: int) -> None:
    if type(request) is not KronosRequest or request.labels is not None:
        raise ValueError("request_or_label")
    if type(request.formation_time_ms) is not int or type(request.known_at_ms) is not int or request.known_at_ms > request.formation_time_ms:
        raise ValueError("clock")
    if not request.values or len(request.values) > maximum or len(request.values) != len(request.context_timestamps_ms) or not request.future_timestamps_ms:
        raise ValueError("shape")
    if any(type(timestamp) is not int for timestamp in request.context_timestamps_ms + request.future_timestamps_ms):
        raise ValueError("timestamp_type")
    if any(right <= left for left, right in zip(request.context_timestamps_ms, request.context_timestamps_ms[1:])):
        raise ValueError("context_timestamp_order")
    if any(right <= left for left, right in zip(request.future_timestamps_ms, request.future_timestamps_ms[1:])):
        raise ValueError("future_timestamp_order")
    if request.context_timestamps_ms[-1] > request.formation_time_ms or request.future_timestamps_ms[0] <= request.formation_time_ms:
        raise ValueError("formation")
    for row in request.values:
        if len(row) != 6 or not all(np.isfinite(value) for value in row):
            raise ValueError("ohlcva")
        open_, high, low, close, volume, amount = row
        if min(open_, high, low, close) <= 0 or high < max(open_, low, close) or low > min(open_, high, close) or volume < 0 or amount < 0:
            raise ValueError("market_schema")


def _verify_acquisition(expected: dict[str, object], path: Path = ACQUISITION_MANIFEST) -> dict[str, object]:
    document = _strict_json_bytes(path.read_bytes())
    keys = {
        "application_network_policy", "expected_manifest_sha256", "files",
        "logical_request_count", "network_retry_count", "raw_file_count",
        "raw_total_bytes", "schema_version", "vendored_sources",
    }
    if type(document) is not dict or set(document) != keys:
        raise ValueError("acquisition_schema")
    if (
        document["schema_version"] != "KRONOS_ACQUISITION_MANIFEST_V2"
        or document["expected_manifest_sha256"] != expected["expected_manifest_sha256"]
        or document["logical_request_count"] != 19
        or document["network_retry_count"] != 0
        or document["raw_file_count"] != 19
        or document["raw_total_bytes"] != 562_430_552
    ):
        raise ValueError("acquisition_constants")
    rows = document["files"]
    if type(rows) is not list or len(rows) != 19:
        raise ValueError("acquisition_rows")
    expected_by_path = {row["path"]: row for row in expected["files"]}
    seen: set[str] = set()
    receipt_keys = {"bytes", "final_host", "final_path", "http_status", "immutable_url", "ordinal", "path", "redirect_count", "sha256"}
    for row in rows:
        if type(row) is not dict or set(row) != receipt_keys or row["path"] in seen:
            raise ValueError("acquisition_receipt_schema")
        expected_row = expected_by_path.get(row["path"])
        if expected_row is None or row["bytes"] != expected_row["expected_bytes"] or row["sha256"] != expected_row["expected_sha256"] or row["immutable_url"] != expected_row["immutable_url"] or row["ordinal"] != expected_row["ordinal"]:
            raise ValueError("acquisition_receipt_binding")
        if row["http_status"] != 200 or type(row["redirect_count"]) is not int or row["redirect_count"] not in (0, 1):
            raise ValueError("acquisition_transport")
        if "?" in row["final_path"] or row["final_host"] not in {"huggingface.co", "us.aws.cdn.hf.co", "raw.githubusercontent.com"}:
            raise ValueError("acquisition_sanitization")
        seen.add(row["path"])
    if seen != set(expected_by_path):
        raise ValueError("acquisition_bijection")
    return document


def _checkpoint_rows(expected: dict[str, object], model_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    by_path = {ROOT / str(row["path"]): row for row in expected["files"]}
    config_path = model_path / "config.json"
    weights_path = model_path / "model.safetensors"
    try:
        return by_path[config_path], by_path[weights_path]
    except KeyError as error:
        raise ValueError("checkpoint_not_expected") from error


def _load_direct(model_class, checkpoint_path: Path, expected: dict[str, object]):  # noqa: ANN001
    config_row, weights_row = _checkpoint_rows(expected, checkpoint_path)
    config_path = checkpoint_path / "config.json"
    weights_path = checkpoint_path / "model.safetensors"
    for path, row in ((config_path, config_row), (weights_path, weights_row)):
        if not path.is_file() or path.stat().st_size != row["expected_bytes"] or _sha256_path(path) != row["expected_sha256"]:
            raise ValueError("checkpoint_binding")
    config = _strict_json_bytes(config_path.read_bytes())
    if type(config) is not dict:
        raise ValueError("checkpoint_config")
    parameters = inspect.signature(model_class.__init__).parameters
    expected_config_keys = {name for name in parameters if name != "self"}
    if set(config) != expected_config_keys:
        raise ValueError("checkpoint_config_keys")
    with safe_open(weights_path, framework="pt", device="cpu") as handle:
        keys = tuple(sorted(handle.keys()))
        if not keys:
            raise ValueError("checkpoint_empty")
        tensor_metadata = tuple((key, str(handle.get_tensor(key).dtype), tuple(handle.get_tensor(key).shape)) for key in keys)
        if any(any(type(dimension) is not int or dimension <= 0 for dimension in shape) for _, _, shape in tensor_metadata):
            raise ValueError("checkpoint_tensor_shape")
    model = model_class(**config)
    state = load_file(weights_path, device="cpu")
    model.load_state_dict(state, strict=True)
    return model, {
        "config_sha256": config_row["expected_sha256"],
        "tensor_count": len(tensor_metadata),
        "weights_sha256": weights_row["expected_sha256"],
    }


def load_offline(
    checkpoint_root: Path,
    model_name: str = "Kronos-mini",
    device: str = "cpu",
    tokenizer_name: str | None = None,
):
    root = checkpoint_root.resolve()
    if root != RAW_ROOT.resolve():
        raise ValueError("checkpoint_root_not_frozen_v2")
    if model_name not in PAIRINGS:
        raise ValueError("unsupported_pair")
    model_revision, expected_tokenizer, tokenizer_revision, maximum = PAIRINGS[model_name]
    if tokenizer_name is not None and tokenizer_name != expected_tokenizer:
        raise ValueError("unsupported_pair")
    expected = load_expected_manifest()
    verify_raw_tree(expected)
    verify_vendored_sources(expected)
    acquisition = _verify_acquisition(expected)
    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        module = importlib.import_module("model")
        model, model_evidence = _load_direct(module.Kronos, root / model_name / model_revision, expected)
        tokenizer, tokenizer_evidence = _load_direct(module.KronosTokenizer, root / expected_tokenizer / tokenizer_revision, expected)
    finally:
        if sys.path and sys.path[0] == str(VENDOR_ROOT):
            sys.path.pop(0)
    model.eval()
    tokenizer.eval()
    predictor = module.KronosPredictor(model, tokenizer, device=device, max_context=maximum)
    return predictor, {
        "acquisition_manifest_sha256": _sha256_path(ACQUISITION_MANIFEST),
        "expected_manifest_sha256": expected["expected_manifest_sha256"],
        "logical_request_count": acquisition["logical_request_count"],
        "model": model_evidence,
        "tokenizer": tokenizer_evidence,
    }


def infer(predictor, request: KronosRequest, seed: int = 20260827) -> tuple[pd.DataFrame, str]:  # noqa: ANN001
    _validate_request(request, predictor.max_context)
    torch.manual_seed(seed)
    columns = ["open", "high", "low", "close", "volume", "amount"]
    frame = pd.DataFrame(np.asarray(request.values), columns=columns)
    context = pd.Series(pd.to_datetime(request.context_timestamps_ms, unit="ms", utc=True))
    future = pd.Series(pd.to_datetime(request.future_timestamps_ms, unit="ms", utc=True))
    output = predictor.predict(frame, context, future, len(future), sample_count=1, verbose=False)
    if output.shape != (len(future), 6) or not np.isfinite(output.to_numpy()).all():
        raise ValueError("output")
    output_sha = hashlib.sha256(output.to_csv(index=True).encode("utf-8")).hexdigest()
    preimage = json.dumps(
        {"formation_time_ms": request.formation_time_ms, "horizon": len(future), "known_at_ms": request.known_at_ms, "output_sha256": output_sha},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return output, hashlib.sha256(preimage).hexdigest()
