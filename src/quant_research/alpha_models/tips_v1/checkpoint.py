from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import torch
from safetensors import safe_open
from safetensors.torch import load_file, save_file

from .contracts import (
    BIAS_REGISTRY_SHA256,
    FEATURE_SCHEMA_SHA256,
    PipelineState,
    TIPSConfig,
    TeacherKind,
    TEACHER_KINDS,
    canonical_json_bytes,
    sha256_canonical,
    validate_hex64,
)
from .model import TIPSBackbone
from .pipeline import FrozenSWAStudent, StudentStepReceipt, SWASnapshot, TIPSPipeline
from .provenance import IMPLEMENTATION_FILES, verify_implementation_tree


@dataclass(frozen=True)
class CheckpointBindings:
    implementation_tree_sha256: str
    availability_manifest_sha256: str
    config_sha256: str
    feature_schema_sha256: str = FEATURE_SCHEMA_SHA256
    bias_registry_sha256: str = BIAS_REGISTRY_SHA256
    rank_contract_id: str = "PAIRWISE_SIGMOID_SOFT_RANK_V1"
    label_q: int = 5

    def __post_init__(self) -> None:
        for name in ("implementation_tree_sha256", "availability_manifest_sha256", "config_sha256", "feature_schema_sha256", "bias_registry_sha256"):
            validate_hex64(getattr(self, name), name)
        if self.rank_contract_id != "PAIRWISE_SIGMOID_SOFT_RANK_V1" or type(self.label_q) is not int or self.label_q != 5:
            raise ValueError("checkpoint_contract")


def _strict_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("manifest_bom")
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result
    parsed = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    if type(parsed) is not dict or canonical_json_bytes(parsed) != raw:
        raise ValueError("manifest_canonical")
    return parsed


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tensor_manifest(path: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    with safe_open(path, framework="pt", device="cpu") as handle:
        for key in sorted(handle.keys()):
            tensor = handle.get_tensor(key)
            if not torch.isfinite(tensor).all():
                raise ValueError("nonfinite_tensor")
            entries.append({"key": key, "dtype": str(tensor.dtype), "shape": list(tensor.shape)})
    return entries


def save_checkpoint(
    output_dir: Path,
    pipeline: TIPSPipeline,
    *,
    repo_root: Path,
    bindings: CheckpointBindings,
) -> tuple[str, str]:
    if type(pipeline) is not TIPSPipeline:
        raise ValueError("typed_tips_pipeline")
    if pipeline.state is not PipelineState.STUDENT_FROZEN:
        raise ValueError("pipeline_not_student_frozen")
    frozen = pipeline.frozen_swa_student()
    model = frozen.model
    teacher_state_hashes = frozen.teacher_state_hashes
    verify_implementation_tree(repo_root, IMPLEMENTATION_FILES, bindings.implementation_tree_sha256)
    if model.config.sha256 != bindings.config_sha256:
        raise ValueError("config_binding")
    tensors = {f"final::{name}": value.detach().cpu().contiguous() for name, value in model.state_dict().items()}
    for index, snapshot in enumerate(frozen.snapshots):
        for name, value in snapshot.tensors:
            tensors[f"snapshot::{index:04d}::{name}"] = value.detach().cpu().contiguous()
    if any(not torch.isfinite(value).all() for value in tensors.values()):
        raise ValueError("nonfinite_tensor")
    output_dir.mkdir(parents=True, exist_ok=False)
    weights = output_dir / "model.safetensors"
    manifest_path = output_dir / "manifest.json"
    temp_weights = output_dir / ".model.safetensors.tmp"
    save_file(tensors, temp_weights)
    os.replace(temp_weights, weights)
    weights_bytes = weights.stat().st_size
    weights_sha = _sha(weights)
    body: dict[str, Any] = {
        "schema_version": "TIPS_CHECKPOINT_V2",
        "config": model.config.to_dict(),
        "config_sha256": bindings.config_sha256,
        "implementation_tree_sha256": bindings.implementation_tree_sha256,
        "availability_manifest_sha256": bindings.availability_manifest_sha256,
        "feature_schema_sha256": bindings.feature_schema_sha256,
        "bias_registry_sha256": bindings.bias_registry_sha256,
        "rank_contract_id": bindings.rank_contract_id,
        "label_q": bindings.label_q,
        "teacher_state_hashes": {key: teacher_state_hashes[key] for key in sorted(teacher_state_hashes)},
        "student_kind": TeacherKind.VANILLA.value,
        "student_role": "STUDENT_SWA",
        "final_student_state_sha256": frozen.final_state_sha256,
        "swa_update_count": frozen.swa_update_count,
        "required_swa_updates": frozen.required_swa_updates,
        "swa_policy_id": frozen.swa_policy_id,
        "synthetic_only": frozen.synthetic_only,
        "swa_update_ids": list(frozen.swa_update_ids),
        "swa_state_sha256s": list(frozen.swa_state_sha256s),
        "swa_trajectory_proof_sha256": frozen.swa_trajectory_proof_sha256,
        "swa_step_receipts": [
            {
                "receipt_id": receipt.receipt_id,
                "step_index": receipt.step_index,
                "student_state_sha256": receipt.student_state_sha256,
            }
            for receipt in frozen.swa_step_receipts
        ],
        "snapshot_tensor_encoding": "snapshot::{zero_padded_index_4}::{state_key}",
        "final_tensor_encoding": "final::{state_key}",
        "weights_file": "model.safetensors",
        "weights_bytes": weights_bytes,
        "weights_sha256": weights_sha,
        "tensor_manifest": _tensor_manifest(weights),
    }
    body["manifest_id"] = sha256_canonical(body)
    raw = canonical_json_bytes(body)
    with manifest_path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return body["manifest_id"], weights_sha


def load_checkpoint(
    output_dir: Path,
    *,
    repo_root: Path,
    bindings: CheckpointBindings,
    expected_manifest_id: str,
    expected_weights_sha256: str,
) -> FrozenSWAStudent:
    validate_hex64(expected_manifest_id, "expected_manifest_id")
    validate_hex64(expected_weights_sha256, "expected_weights_sha256")
    if output_dir.is_symlink() or not output_dir.is_dir() or {item.name for item in output_dir.iterdir()} != {"manifest.json", "model.safetensors"}:
        raise ValueError("checkpoint_tree")
    if any(item.is_symlink() for item in output_dir.iterdir()):
        raise ValueError("checkpoint_symlink")
    verify_implementation_tree(repo_root, IMPLEMENTATION_FILES, bindings.implementation_tree_sha256)
    manifest = _strict_json(output_dir / "manifest.json")
    manifest_id = manifest.pop("manifest_id", None)
    if manifest_id != expected_manifest_id or sha256_canonical(manifest) != expected_manifest_id:
        raise ValueError("external_manifest_binding")
    expected_keys = {
        "schema_version", "config", "config_sha256", "implementation_tree_sha256", "availability_manifest_sha256",
        "feature_schema_sha256", "bias_registry_sha256", "rank_contract_id", "label_q", "teacher_state_hashes",
        "student_kind", "student_role", "swa_update_count", "weights_file", "weights_bytes", "weights_sha256", "tensor_manifest",
        "final_student_state_sha256", "swa_update_ids", "swa_state_sha256s", "swa_trajectory_proof_sha256",
        "required_swa_updates", "swa_policy_id", "synthetic_only", "swa_step_receipts",
        "snapshot_tensor_encoding", "final_tensor_encoding",
    }
    if set(manifest) != expected_keys:
        raise ValueError("manifest_keys")
    for field in ("implementation_tree_sha256", "availability_manifest_sha256", "config_sha256", "feature_schema_sha256", "bias_registry_sha256", "rank_contract_id", "label_q"):
        if manifest[field] != getattr(bindings, field):
            raise ValueError("manifest_binding")
    if manifest["schema_version"] != "TIPS_CHECKPOINT_V2" or manifest["student_kind"] != TeacherKind.VANILLA.value or manifest["student_role"] != "STUDENT_SWA":
        raise ValueError("checkpoint_identity")
    if manifest["snapshot_tensor_encoding"] != "snapshot::{zero_padded_index_4}::{state_key}" or manifest["final_tensor_encoding"] != "final::{state_key}":
        raise ValueError("tensor_encoding")
    if type(manifest["swa_update_count"]) is not int or manifest["swa_update_count"] != manifest["required_swa_updates"]:
        raise ValueError("swa_update_count")
    update_ids = manifest["swa_update_ids"]
    state_hashes = manifest["swa_state_sha256s"]
    if type(update_ids) is not list or type(state_hashes) is not list or len(update_ids) != manifest["swa_update_count"] or len(state_hashes) != len(update_ids):
        raise ValueError("swa_trajectory")
    if any(type(item) is not str or not item for item in update_ids) or len(set(update_ids)) != len(update_ids):
        raise ValueError("swa_update_ids")
    if any(validate_hex64(item, "swa_state_sha") != item for item in state_hashes) or len(set(state_hashes)) != len(state_hashes):
        raise ValueError("swa_state_sha256s")
    proof = sha256_canonical({"state_sha256s": state_hashes, "update_ids": update_ids})
    if proof != manifest["swa_trajectory_proof_sha256"]:
        raise ValueError("swa_trajectory_proof")
    if type(manifest["synthetic_only"]) is not bool or type(manifest["swa_policy_id"]) is not str:
        raise ValueError("swa_policy")
    policy = (
        (2, True, "SYNTHETIC_SMOKE_OVERRIDE_V1")
        if manifest["synthetic_only"]
        else (10, False, "PAPER_FINAL_10_EPOCHS_LOCAL_MAPPING_V1")
    )
    if (manifest["required_swa_updates"], manifest["synthetic_only"], manifest["swa_policy_id"]) != policy:
        raise ValueError("swa_policy")
    receipt_rows = manifest["swa_step_receipts"]
    if type(receipt_rows) is not list or len(receipt_rows) != len(update_ids):
        raise ValueError("swa_step_receipts")
    receipts: list[StudentStepReceipt] = []
    for index, row in enumerate(receipt_rows):
        if type(row) is not dict or set(row) != {"receipt_id", "step_index", "student_state_sha256"}:
            raise ValueError("swa_step_receipt")
        receipt = StudentStepReceipt(row["step_index"], row["student_state_sha256"])
        if row["receipt_id"] != receipt.receipt_id or update_ids[index] != receipt.receipt_id:
            raise ValueError("swa_step_receipt_identity")
        if receipts and receipt.step_index <= receipts[-1].step_index:
            raise ValueError("swa_step_receipt_order")
        receipts.append(receipt)
    teacher_hashes = manifest["teacher_state_hashes"]
    if type(teacher_hashes) is not dict or set(teacher_hashes) != {kind.value for kind in TEACHER_KINDS}:
        raise ValueError("teacher_hashes")
    for value in teacher_hashes.values():
        validate_hex64(value, "teacher_sha")
    weights = output_dir / "model.safetensors"
    if manifest["weights_file"] != weights.name or manifest["weights_bytes"] != weights.stat().st_size:
        raise ValueError("weights_size")
    actual_weights_sha = _sha(weights)
    if actual_weights_sha != expected_weights_sha256 or manifest["weights_sha256"] != expected_weights_sha256:
        raise ValueError("external_weights_binding")
    if manifest["tensor_manifest"] != _tensor_manifest(weights):
        raise ValueError("tensor_manifest")
    config = TIPSConfig.from_dict(manifest["config"])
    if config.sha256 != bindings.config_sha256:
        raise ValueError("config")
    model = TIPSBackbone(config, TeacherKind.VANILLA, role="STUDENT")
    loaded_tensors = load_file(weights, device="cpu")
    tensors = {key: value.clone().contiguous() for key, value in loaded_tensors.items()}
    del loaded_tensors
    state_keys = tuple(sorted(model.state_dict()))
    expected_tensor_keys = {f"final::{name}" for name in state_keys}
    expected_tensor_keys.update(f"snapshot::{index:04d}::{name}" for index in range(len(update_ids)) for name in state_keys)
    if set(tensors) != expected_tensor_keys or any(not torch.isfinite(value).all() for value in tensors.values()):
        raise ValueError("tensor_keys")
    final_tensors = {name: tensors[f"final::{name}"] for name in state_keys}
    model.load_state_dict(final_tensors, strict=True)
    model.freeze()
    if model.state_sha256 != manifest["final_student_state_sha256"]:
        raise ValueError("final_student_state_sha256")
    snapshots = tuple(
        SWASnapshot(update_ids[index], receipts[index], tuple((name, tensors[f"snapshot::{index:04d}::{name}"]) for name in state_keys))
        for index in range(len(update_ids))
    )
    frozen = FrozenSWAStudent(
        model=model,
        teacher_state_hashes=teacher_hashes,
        snapshots=snapshots,
        required_swa_updates=manifest["required_swa_updates"],
        synthetic_only=manifest["synthetic_only"],
        swa_policy_id=manifest["swa_policy_id"],
    )
    if frozen.swa_state_sha256s != tuple(state_hashes) or frozen.swa_trajectory_proof_sha256 != proof:
        raise ValueError("snapshot_manifest_identity")
    return frozen
