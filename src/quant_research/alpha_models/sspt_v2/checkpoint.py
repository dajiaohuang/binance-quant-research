from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from typing import Any
from dataclasses import dataclass

import torch
from safetensors.torch import load as load_safetensors
from safetensors.torch import save as save_safetensors

from .contracts import SSPTConfigV2, StableLabelRegistry, canonical_json_bytes, sha256_canonical, validate_hex64
from .data import TrainOnlyMinMax
from .model import FreezeMode, SSPTModelV2


CHECKPOINT_FILES = {"manifest.json", "model.safetensors"}
REPARSE_POINT = 0x400
SSPT_V2_IMPLEMENTATION_PATHS = (
    "src/quant_research/alpha_models/sspt_v2/__init__.py",
    "src/quant_research/alpha_models/sspt_v2/checkpoint.py",
    "src/quant_research/alpha_models/sspt_v2/contracts.py",
    "src/quant_research/alpha_models/sspt_v2/data.py",
    "src/quant_research/alpha_models/sspt_v2/losses.py",
    "src/quant_research/alpha_models/sspt_v2/model.py",
    "src/quant_research/alpha_models/sspt_v2/smoke.py",
)


@dataclass(frozen=True)
class CheckpointBindings:
    implementation_tree_sha256: str
    source_contract_sha256: str
    schema_sha256: str
    parameters_sha256: str

    def __post_init__(self) -> None:
        for name in ("implementation_tree_sha256", "source_contract_sha256", "schema_sha256", "parameters_sha256"):
            validate_hex64(getattr(self, name), name)

    def projection(self) -> dict[str, str]:
        return {
            "implementation_tree_sha256": self.implementation_tree_sha256,
            "parameters_sha256": self.parameters_sha256,
            "schema_sha256": self.schema_sha256,
            "source_contract_sha256": self.source_contract_sha256,
        }


@dataclass(frozen=True)
class LoadedSSPTBundle:
    model: SSPTModelV2
    scaler: TrainOnlyMinMax
    scc_registry: StableLabelRegistry
    ssc_registry: StableLabelRegistry
    bindings: CheckpointBindings

    def predict(self, request: object) -> torch.Tensor:
        return self.model.predict(request)


def _is_reparse(stat_result: os.stat_result) -> bool:
    return bool(getattr(stat_result, "st_file_attributes", 0) & REPARSE_POINT)


def _check_existing_plain(path: Path, *, directory: bool) -> None:
    current = path.absolute()
    parents = tuple(reversed(current.parents)) + (current,)
    for component in parents:
        if not component.exists():
            continue
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise ValueError("symlink_or_reparse")
    info = current.lstat()
    if directory and not stat.S_ISDIR(info.st_mode):
        raise ValueError("not_directory")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise ValueError("not_file")


def implementation_tree_sha256(entries: object) -> str:
    if type(entries) is not list or not entries:
        raise ValueError("implementation_entries")
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in entries:
        if type(row) is not dict or set(row) != {"path", "bytes", "sha256"}:
            raise ValueError("implementation_entry")
        path = row["path"]
        byte_count = row["bytes"]
        digest = row["sha256"]
        if type(path) is not str or not path or "\\" in path:
            raise ValueError("implementation_path")
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or any(part in ("", ".", "..") for part in parsed.parts) or path in seen:
            raise ValueError("implementation_path")
        if type(byte_count) is not int or byte_count < 0:
            raise ValueError("implementation_bytes")
        validate_hex64(digest, "implementation_sha256")
        seen.add(path)
        normalized.append({"path": path, "bytes": byte_count, "sha256": digest})
    normalized.sort(key=lambda row: str(row["path"]).encode("utf-8"))
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


def verify_frozen_implementation_tree(repo_root: Path, frozen_hashes_path: Path) -> str:
    if not isinstance(repo_root, Path) or not isinstance(frozen_hashes_path, Path):
        raise TypeError("implementation_binding_path")
    root = repo_root.resolve(strict=True)
    _check_existing_plain(root, directory=True)
    _check_existing_plain(frozen_hashes_path, directory=False)
    frozen = _strict_manifest(frozen_hashes_path.read_bytes())
    if "implementation_files" not in frozen or "implementation_tree_sha256" not in frozen:
        raise ValueError("implementation_manifest_keys")
    entries = frozen["implementation_files"]
    expected_tree = frozen["implementation_tree_sha256"]
    validate_hex64(expected_tree, "implementation_tree_sha256")
    if type(entries) is not list:
        raise ValueError("implementation_entries")
    paths = [row.get("path") if type(row) is dict else None for row in entries]
    if paths != list(SSPT_V2_IMPLEMENTATION_PATHS):
        raise ValueError("implementation_paths")
    for row in entries:
        relative = PurePosixPath(row["path"])
        candidate = root.joinpath(*relative.parts)
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("implementation_path_escape") from exc
        _check_existing_plain(candidate, directory=False)
        payload = candidate.read_bytes()
        if len(payload) != row["bytes"]:
            raise ValueError("implementation_bytes_drift")
        if hashlib.sha256(payload).hexdigest() != row["sha256"]:
            raise ValueError("implementation_sha256_drift")
    observed_tree = implementation_tree_sha256(entries)
    if observed_tree != expected_tree:
        raise ValueError("implementation_tree_drift")
    return observed_tree


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _tensor_manifest(tensors: dict[str, torch.Tensor]) -> list[dict[str, object]]:
    return [
        {"dtype": str(tensors[name].dtype).removeprefix("torch."), "name": name, "shape": list(tensors[name].shape)}
        for name in sorted(tensors, key=lambda value: value.encode("utf-8"))
    ]


def _registry_from_projection(value: object) -> StableLabelRegistry:
    if type(value) is not dict or set(value) != {"authority_id", "known_at_ms", "labels", "registry_id", "training_partition_id"}:
        raise ValueError("registry_manifest")
    labels = value["labels"]
    if type(labels) is not list:
        raise ValueError("registry_labels")
    return StableLabelRegistry(
        registry_id=value["registry_id"],
        labels=tuple(labels),
        authority_id=value["authority_id"],
        training_partition_id=value["training_partition_id"],
        known_at_ms=value["known_at_ms"],
    )


def _freeze_projection(model: SSPTModelV2, mode: FreezeMode) -> dict[str, object]:
    state = model.set_freeze_mode(mode)
    return {
        "frozen_parameter_names": list(state.frozen_parameter_names),
        "mode": state.mode.value,
        "state_sha256": state.state_sha256,
        "trainable_parameter_names": list(state.trainable_parameter_names),
    }


def _strict_manifest(raw: bytes) -> dict[str, object]:
    if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
        raise ValueError("manifest_encoding")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ValueError("manifest_utf8") from exc

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("manifest_duplicate")
        return dict(pairs)

    try:
        value = json.loads(text, object_pairs_hook=hook, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("manifest_json") from exc
    if type(value) is not dict or canonical_json_bytes(value) + b"\n" != raw:
        raise ValueError("manifest_canonical")
    return value


def _build_manifest(
    model: SSPTModelV2,
    weights_bytes: bytes,
    tensors: dict[str, torch.Tensor],
    scaler: TrainOnlyMinMax,
    freeze_mode: FreezeMode,
    bindings: CheckpointBindings,
) -> dict[str, object]:
    config = model.config.to_dict()
    scc = model.scc_registry.projection()
    ssc = model.ssc_registry.projection()
    scaler_state = scaler.state()
    projection: dict[str, object] = {
        "bindings": bindings.projection(),
        "bindings_sha256": sha256_canonical(bindings.projection()),
        "config": config,
        "config_sha256": sha256_canonical(config),
        "freeze": _freeze_projection(model, freeze_mode),
        "model_class": "SSPTModelV2",
        "scc_registry": scc,
        "scc_registry_sha256": sha256_canonical(scc),
        "schema_version": "SSPT_DUAL_FILE_CHECKPOINT_V1",
        "scaler": scaler_state,
        "scaler_sha256": sha256_canonical(scaler_state),
        "ssc_registry": ssc,
        "ssc_registry_sha256": sha256_canonical(ssc),
        "weights": {
            "bytes": len(weights_bytes),
            "sha256": hashlib.sha256(weights_bytes).hexdigest(),
            "tensors": _tensor_manifest(tensors),
        },
    }
    projection["manifest_id"] = sha256_canonical(projection)
    return projection


def _validate_manifest_keys(manifest: dict[str, object]) -> None:
    if set(manifest) != {
        "config",
        "config_sha256",
        "bindings",
        "bindings_sha256",
        "freeze",
        "manifest_id",
        "model_class",
        "scc_registry",
        "scc_registry_sha256",
        "schema_version",
        "scaler",
        "scaler_sha256",
        "ssc_registry",
        "ssc_registry_sha256",
        "weights",
    }:
        raise ValueError("manifest_keys")
    without_id = dict(manifest)
    manifest_id = without_id.pop("manifest_id")
    if manifest_id != sha256_canonical(without_id):
        raise ValueError("manifest_id")


def save_checkpoint(
    model: SSPTModelV2,
    target: Path,
    *,
    scaler: TrainOnlyMinMax,
    freeze_mode: FreezeMode,
    bindings: CheckpointBindings,
) -> dict[str, object]:
    if type(model) is not SSPTModelV2 or not isinstance(target, Path) or type(scaler) is not TrainOnlyMinMax or type(freeze_mode) is not FreezeMode or type(bindings) is not CheckpointBindings:
        raise TypeError("checkpoint_contract")
    if model.scaler_sha256 != scaler.sha256:
        raise ValueError("model_scaler_binding")
    parent = target.parent
    _check_existing_plain(parent, directory=True)
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    staging = parent / f".{target.name}.staging"
    if staging.exists() or staging.is_symlink():
        raise FileExistsError(staging)
    os.mkdir(staging)
    try:
        state = {name: tensor.detach().cpu().contiguous() for name, tensor in model.state_dict().items()}
        if not all(bool(torch.isfinite(tensor).all()) for tensor in state.values()):
            raise ValueError("nonfinite_tensor")
        weights_bytes = save_safetensors(state)
        _write_exclusive(staging / "model.safetensors", weights_bytes)
        manifest = _build_manifest(model, weights_bytes, state, scaler, freeze_mode, bindings)
        _write_exclusive(staging / "manifest.json", canonical_json_bytes(manifest) + b"\n")
        load_checkpoint(
            staging,
            expected_config=model.config,
            expected_scc_registry=model.scc_registry,
            expected_ssc_registry=model.ssc_registry,
            expected_scaler=scaler,
            expected_freeze_mode=freeze_mode,
            expected_bindings=bindings,
            expected_manifest_id=manifest["manifest_id"],
            expected_weights_sha256=manifest["weights"]["sha256"],
        )
        os.rename(staging, target)
        return manifest
    except BaseException:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging)
        raise


def load_checkpoint(
    path: Path,
    *,
    expected_config: SSPTConfigV2,
    expected_scc_registry: StableLabelRegistry,
    expected_ssc_registry: StableLabelRegistry,
    expected_scaler: TrainOnlyMinMax,
    expected_freeze_mode: FreezeMode,
    expected_bindings: CheckpointBindings,
    expected_manifest_id: str,
    expected_weights_sha256: str,
) -> LoadedSSPTBundle:
    if not isinstance(path, Path) or type(expected_config) is not SSPTConfigV2 or type(expected_scc_registry) is not StableLabelRegistry or type(expected_ssc_registry) is not StableLabelRegistry or type(expected_scaler) is not TrainOnlyMinMax or type(expected_freeze_mode) is not FreezeMode or type(expected_bindings) is not CheckpointBindings:
        raise TypeError("checkpoint_contract")
    validate_hex64(expected_manifest_id, "expected_manifest_id")
    validate_hex64(expected_weights_sha256, "expected_weights_sha256")
    _check_existing_plain(path, directory=True)
    entries = tuple(sorted(os.scandir(path), key=lambda entry: entry.name.encode("utf-8")))
    if {entry.name for entry in entries} != CHECKPOINT_FILES or len(entries) != 2:
        raise ValueError("checkpoint_files")
    for entry in entries:
        info = entry.stat(follow_symlinks=False)
        if entry.is_symlink() or _is_reparse(info) or not entry.is_file(follow_symlinks=False):
            raise ValueError("checkpoint_file_type")
    manifest_raw = (path / "manifest.json").read_bytes()
    weights_raw = (path / "model.safetensors").read_bytes()
    manifest = _strict_manifest(manifest_raw)
    _validate_manifest_keys(manifest)
    if manifest["manifest_id"] != expected_manifest_id:
        raise ValueError("external_manifest_identity")
    if manifest["schema_version"] != "SSPT_DUAL_FILE_CHECKPOINT_V1" or manifest["model_class"] != "SSPTModelV2":
        raise ValueError("checkpoint_identity")
    config = SSPTConfigV2.from_dict(manifest["config"])
    if manifest["bindings"] != expected_bindings.projection() or manifest["bindings_sha256"] != sha256_canonical(expected_bindings.projection()):
        raise ValueError("code_contract_binding")
    scc_registry = _registry_from_projection(manifest["scc_registry"])
    ssc_registry = _registry_from_projection(manifest["ssc_registry"])
    scaler = TrainOnlyMinMax.from_state(manifest["scaler"])
    if manifest["config_sha256"] != sha256_canonical(config.to_dict()) or manifest["scc_registry_sha256"] != scc_registry.sha256 or manifest["ssc_registry_sha256"] != ssc_registry.sha256 or manifest["scaler_sha256"] != scaler.sha256:
        raise ValueError("nested_hash")
    if config != expected_config or scc_registry != expected_scc_registry or ssc_registry != expected_ssc_registry or scaler.state() != expected_scaler.state():
        raise ValueError("expected_binding")
    weights = manifest["weights"]
    if type(weights) is not dict or set(weights) != {"bytes", "sha256", "tensors"} or type(weights["bytes"]) is not int or weights["bytes"] != len(weights_raw) or weights["sha256"] != hashlib.sha256(weights_raw).hexdigest():
        raise ValueError("weights_binding")
    if weights["sha256"] != expected_weights_sha256:
        raise ValueError("external_weights_identity")
    try:
        tensors = load_safetensors(weights_raw)
    except Exception as exc:
        raise ValueError("safetensors") from exc
    if weights["tensors"] != _tensor_manifest(tensors):
        raise ValueError("tensor_manifest")
    if not all(bool(torch.isfinite(tensor).all()) for tensor in tensors.values()):
        raise ValueError("nonfinite_tensor")
    model = SSPTModelV2(config, scc_registry=scc_registry, ssc_registry=ssc_registry, scaler_sha256=scaler.sha256)
    model.load_state_dict(tensors, strict=True)
    freeze_projection = _freeze_projection(model, expected_freeze_mode)
    if manifest["freeze"] != freeze_projection:
        raise ValueError("freeze_binding")
    return LoadedSSPTBundle(model, scaler, scc_registry, ssc_registry, expected_bindings)
