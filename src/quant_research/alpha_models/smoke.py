"""CLI for bounded synthetic DDGL smoke checks only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import torch

from .contracts import load_ddgl_config, make_synthetic_examples
from .training import (
    DDGLResourceUnavailable,
    fit_synthetic,
    infer,
    load_checkpoint,
    save_checkpoint,
)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_once(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _prediction_digest(predictions: object) -> str:
    return hashlib.sha256(
        json.dumps(
            predictions,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=lambda item: item.__dict__,
        ).encode("utf-8")
    ).hexdigest()


def run(config_path: Path, output_path: Path, checkpoint_path: Path) -> dict[str, object]:
    if output_path.exists() or checkpoint_path.exists():
        raise FileExistsError("smoke outputs refuse overwrite")
    config = load_ddgl_config(config_path)
    examples = make_synthetic_examples(config)
    common = {
        "classification": "METHOD_REPRODUCTION_ON_NEW_DATA",
        "config_path": config_path.as_posix(),
        "config_sha256": config.config_sha256,
        "empirical_authorized": False,
        "experiment_id": config.experiment_id,
        "historical_eligibility_ready": False,
        "ic_evaluated": False,
        "pnl_evaluated": False,
        "real_data_accessed": False,
        "source_status": config.source.source_status,
        "stage": config.stage,
        "strict_eligible_count": 0,
        "torch_cuda_runtime": torch.version.cuda,
        "torch_version": torch.__version__,
    }
    try:
        result = fit_synthetic(config, examples)
    except DDGLResourceUnavailable as error:
        metrics = {
            **common,
            "artifact_state": "GPU_SMOKE_NOT_RUN_RESOURCE_GATE",
            "resource_gate_reason": str(error),
            "terminal_status": "NEEDS_MORE_DATA",
        }
        _write_once(output_path, _canonical_json(metrics))
        return metrics
    predictions = infer(result.model, tuple(item.inputs for item in examples), config)
    checkpoint_sha256 = save_checkpoint(result.model, config, checkpoint_path)
    restored = load_checkpoint(config, checkpoint_path, result.device_used)
    restored_predictions = infer(
        restored, tuple(item.inputs for item in examples), config
    )
    original_digest = _prediction_digest(predictions)
    restored_digest = _prediction_digest(restored_predictions)
    if original_digest != restored_digest:
        raise RuntimeError("checkpoint roundtrip prediction mismatch")
    metrics = {
        **common,
        "artifact_state": "SYNTHETIC_CONTRACT_VERIFIED",
        "checkpoint_path": checkpoint_path.as_posix(),
        "checkpoint_sha256": checkpoint_sha256,
        "device_used": result.device_used,
        "final_loss": result.final_loss,
        "initial_loss": result.initial_loss,
        "num_assets": config.synthetic.num_assets,
        "num_samples": config.synthetic.num_samples,
        "parameters_updated": result.parameters_updated,
        "peak_vram_mib": result.peak_vram_mib,
        "precision_used": result.precision_used,
        "prediction_sha256": original_digest,
        "roundtrip_prediction_sha256": restored_digest,
        "terminal_status": "NEEDS_MORE_DATA",
    }
    if not result.parameters_updated:
        raise RuntimeError("synthetic training did not update parameters")
    _write_once(output_path, _canonical_json(metrics))
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        metrics = run(arguments.config, arguments.output, arguments.checkpoint)
    except Exception as error:
        print(f"synthetic smoke failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(metrics, sort_keys=True, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
