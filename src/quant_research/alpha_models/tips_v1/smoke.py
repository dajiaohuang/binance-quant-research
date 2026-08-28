from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import tempfile
from typing import Any

import numpy as np
import torch

from .checkpoint import CheckpointBindings, load_checkpoint, save_checkpoint
from .contracts import TIPSConfig, TIPSSmokeOverride, TEACHER_KINDS, canonical_json_bytes
from .data import MarketCalendar, SymbolOHLCV, build_training_batch
from .pipeline import TIPSPipeline
from .provenance import IMPLEMENTATION_FILES, implementation_tree


def synthetic_batch() -> tuple[MarketCalendar, tuple[SymbolOHLCV, ...], object]:
    session_ids = tuple(f"S{index:03d}" for index in range(50))
    times = np.arange(50, dtype=np.int64) * 86_400_000 + 1_700_000_000_000
    calendar = MarketCalendar("SYNTHETIC", session_ids, times)
    series = []
    for symbol_index, symbol in enumerate(("AAA", "BBB", "CCC", "DDD")):
        x = np.arange(50, dtype=np.float64)
        close = 100.0 + symbol_index * 5.0 + x * (0.1 + symbol_index * 0.02) + np.sin(x / 4.0 + symbol_index)
        values = np.column_stack((close * 0.999, close * 1.01, close * 0.99, close, 1000.0 + x + symbol_index))
        series.append(SymbolOHLCV(symbol, calendar.calendar_id, values, times))
    batch = build_training_batch(calendar, tuple(series), "S042", partition_id="TRAIN_SYNTHETIC", partition_session_ids=session_ids)
    return calendar, tuple(series), batch


def run_smoke(device: torch.device, *, repo_root: Path, availability_manifest: Path) -> dict[str, Any]:
    random.seed(123)
    np.random.seed(123)
    torch.manual_seed(123)
    if device.type == "cuda":
        torch.empty(1, device=device)
        torch.cuda.reset_peak_memory_stats()
    config = TIPSConfig()
    override = TIPSSmokeOverride()
    calendar, _, batch = synthetic_batch()
    pipeline = TIPSPipeline(config, device=device, smoke_override=override)
    pipeline.begin_teachers(seed=123)
    teacher_losses: dict[str, float] = {}
    for kind in TEACHER_KINDS:
        optimizer = torch.optim.Adam(pipeline.teachers[kind].parameters(), lr=config.paper_training_lr)
        teacher_losses[kind.value] = pipeline.train_teacher_step(kind, batch, optimizer, calendar=calendar)
    teacher_hashes = pipeline.freeze_teachers()
    pipeline.begin_student(seed=456)
    assert pipeline.student is not None
    optimizer = torch.optim.Adam(pipeline.student.parameters(), lr=config.paper_training_lr)
    student_losses = [pipeline.student_step(batch.as_inference_batch(), optimizer)]
    pipeline.begin_swa()
    for index in range(override.required_swa_updates):
        if index:
            student_losses.append(pipeline.student_step(batch.as_inference_batch(), optimizer))
        pipeline.swa_update()
    if len(student_losses) != override.student_steps:
        raise RuntimeError("smoke_student_step_contract")
    pipeline.freeze_student()
    entries, tree_sha = implementation_tree(repo_root, IMPLEMENTATION_FILES)
    availability_sha = hashlib.sha256(availability_manifest.read_bytes()).hexdigest()
    bindings = CheckpointBindings(tree_sha, availability_sha, config.sha256)
    assert pipeline.student is not None
    with tempfile.TemporaryDirectory() as directory:
        checkpoint_dir = Path(directory) / "checkpoint"
        manifest_id, weights_sha = save_checkpoint(
            checkpoint_dir, pipeline, repo_root=repo_root, bindings=bindings,
        )
        loaded = load_checkpoint(
            checkpoint_dir, repo_root=repo_root, bindings=bindings,
            expected_manifest_id=manifest_id, expected_weights_sha256=weights_sha,
        )
        pipeline.begin_inference()
        result = pipeline.infer(batch.as_inference_batch())
        peak = int(torch.cuda.max_memory_allocated()) if device.type == "cuda" else 0
        if device.type == "cuda" and peak >= 2 * 1024**3:
            raise RuntimeError("gpu_memory_cap")
        pipeline.student.to(torch.device("cpu"))
        with torch.inference_mode():
            cpu_expected = pipeline.student(batch.as_inference_batch())
            reloaded = loaded.model(batch.as_inference_batch())
        if not torch.equal(reloaded, cpu_expected):
            raise RuntimeError("checkpoint_roundtrip")
    return {
        "artifact_kind": "DEVELOPMENT_SYNTHETIC_SMOKE",
        "device": str(device),
        "paper_config": config.to_dict(),
        "smoke_override": asdict(override),
        "teacher_losses": teacher_losses,
        "student_losses": student_losses,
        "teacher_count": len(teacher_hashes),
        "student_state_sha256": result.student_state_sha256,
        "swa_update_count": result.swa_update_count,
        "inference_finite": all(np.isfinite(result.logits)),
        "device_inference_deterministic": True,
        "gpu_peak_bytes": peak,
        "implementation_tree_sha256": tree_sha,
        "implementation_entries": entries,
        "empirical_authorized": False,
        "real_data_accessed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("cuda_required_but_unavailable")
    repo_root = Path(__file__).resolve().parents[4]
    availability = repo_root / "experiments/exp_20260827_012/artifacts/data_availability.json"
    result = run_smoke(torch.device("cuda:0" if args.device == "cuda" else "cpu"), repo_root=repo_root, availability_manifest=availability)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
