from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import torch

from .checkpoint import CheckpointBindings, load_checkpoint, save_checkpoint, verify_frozen_implementation_tree
from .contracts import SSPTConfigV2, StableLabelRegistry, SymbolDailySeries
from .data import TrainingFeaturePartition, TrainOnlyMinMax, build_cross_section_batch, build_feature_matrix
from .losses import fine_tune_loss, pretrain_loss
from .model import FreezeMode, SSPTInferenceRequestV2, SSPTModelV2, deterministic_map_view


ROOT = Path(__file__).resolve().parents[4]


def _synthetic_contract() -> tuple[SSPTConfigV2, StableLabelRegistry, StableLabelRegistry, TrainOnlyMinMax, object]:
    config = SSPTConfigV2(dropout=0.1, scc_classes=4, ssc_classes=3)
    count = 48
    calendar = tuple((f"S{index:03d}", 1_700_000_000_000 + index * 86_400_000) for index in range(count))
    symbols = ("AAA", "BBB", "CCC", "DDD")
    sectors = ("ENERGY", "FINANCE", "TECH", "TECH")
    series: list[SymbolDailySeries] = []
    raw_features: list[np.ndarray] = []
    for asset_index, (symbol, sector) in enumerate(zip(symbols, sectors, strict=True)):
        base = 100.0 + asset_index * 10.0 + np.arange(count, dtype=np.float64) * (0.2 + asset_index * 0.01)
        ohlcv = np.column_stack((base, base + 1.0, base - 1.0, base + 0.25, 1_000.0 + np.arange(count)))
        item = SymbolDailySeries(
            market_id="SYNTHETIC_SPOT",
            symbol=symbol,
            session_ids=tuple(identifier for identifier, _ in calendar),
            session_times_ms=tuple(timestamp for _, timestamp in calendar),
            feature_known_at_ms=tuple(timestamp for _, timestamp in calendar),
            ohlcv=ohlcv,
            sector_label=sector,
            sector_known_at_ms=calendar[0][1],
        )
        series.append(item)
        raw_features.append(build_feature_matrix(ohlcv))
    training_features = np.stack([np.stack([features[:16] for features in raw_features])])
    partition = TrainingFeaturePartition(
        kind="TRAIN",
        features=training_features,
        formation_times_ms=(calendar[44][1],),
        label_end_times_ms=(calendar[45][1],),
        train_end_exclusive_ms=calendar[46][1],
        data_provenance_sha256=hashlib.sha256(b"SYNTHETIC_TRAINING_PARTITION").hexdigest(),
    )
    scaler = TrainOnlyMinMax().fit(partition)
    scc = StableLabelRegistry.from_labels(
        "SCC_SYMBOL_IDENTITY_V1",
        symbols,
        authority_id="SYNTHETIC_REGISTRY",
        training_partition_id="TRAIN_SYNTHETIC",
        known_at_ms=calendar[0][1],
    )
    ssc = StableLabelRegistry.from_labels(
        "SSC_SECTOR_V1",
        tuple(sorted(set(sectors), key=lambda value: value.encode("utf-8"))),
        authority_id="SYNTHETIC_REGISTRY",
        training_partition_id="TRAIN_SYNTHETIC",
        known_at_ms=calendar[0][1],
    )
    batch = build_cross_section_batch(
        series,
        expected_calendar=calendar,
        market_id="SYNTHETIC_SPOT",
        formation_time_ms=calendar[44][1],
        lookback=config.lookback,
        scaler=scaler,
        scc_registry=scc,
        ssc_registry=ssc,
    )
    return config, scc, ssc, scaler, batch


def _checkpoint_bindings() -> CheckpointBindings:
    implementation = verify_frozen_implementation_tree(
        ROOT,
        ROOT / "experiments/exp_20260827_011/artifacts/frozen_hashes.json",
    )
    def digest(relative: str) -> str:
        return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    return CheckpointBindings(
        implementation_tree_sha256=implementation,
        source_contract_sha256=digest("experiments/exp_20260827_011/artifacts/source_contract.json"),
        schema_sha256=digest("experiments/exp_20260827_011/artifacts/schema.json"),
        parameters_sha256=digest("experiments/exp_20260827_011/parameters.json"),
    )


def run_smoke(device_name: str) -> dict[str, object]:
    if device_name not in ("cpu", "cuda:0"):
        raise ValueError("device")
    if device_name == "cuda:0" and not torch.cuda.is_available():
        raise RuntimeError("CUDA_UNAVAILABLE")
    bindings_start = _checkpoint_bindings()
    torch.manual_seed(20260827)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(20260827)
    torch.use_deterministic_algorithms(True)
    device = torch.device(device_name)
    config, scc, ssc, scaler, batch = _synthetic_contract()
    model = SSPTModelV2(config, scc_registry=scc, ssc_registry=ssc, scaler_sha256=scaler.sha256).to(device)
    map_view = deterministic_map_view(batch, seed=20260827, mask_rate=config.map_mask_rate)
    if device_name == "cuda:0":
        torch.cuda.reset_peak_memory_stats(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    optimizer.zero_grad(set_to_none=True)
    before = {name: parameter.detach().clone() for name, parameter in model.named_parameters()}
    pretrain = pretrain_loss(model.pretrain(batch, map_view), batch, map_view, alpha=1.0, beta=1.0, gamma=1.0)
    pretrain.backward()
    optimizer.step()
    pretrain_updated = any(not torch.equal(before[name], parameter.detach()) for name, parameter in model.named_parameters())
    freeze = model.set_freeze_mode(FreezeMode.EMBEDDING_ATTENTION)
    frozen_before = {name: parameter.detach().clone() for name, parameter in model.named_parameters() if not parameter.requires_grad}
    trainable_before = {name: parameter.detach().clone() for name, parameter in model.named_parameters() if parameter.requires_grad}
    optimizer = torch.optim.Adam((parameter for parameter in model.parameters() if parameter.requires_grad), lr=1e-3)
    optimizer.zero_grad(set_to_none=True)
    fine = fine_tune_loss(model.fine_tune_scores(batch), batch, epsilon=1.0)
    fine.backward()
    optimizer.step()
    frozen_unchanged = all(torch.equal(value, dict(model.named_parameters())[name].detach()) for name, value in frozen_before.items())
    trainable_updated = any(not torch.equal(value, dict(model.named_parameters())[name].detach()) for name, value in trainable_before.items())
    request = SSPTInferenceRequestV2(batch.as_inference_batch())
    device_prediction_one = model.predict(request)
    device_prediction_two = model.predict(request)
    device_inference_deterministic = (
        str(next(model.parameters()).device) == device_name
        and torch.equal(device_prediction_one, device_prediction_two)
        and bool(torch.isfinite(device_prediction_one).all())
    )
    if device_name == "cuda:0":
        torch.cuda.synchronize(device)
    model_cpu = model.cpu()
    prediction_one = model_cpu.predict(request)
    prediction_two = model_cpu.predict(request)
    with tempfile.TemporaryDirectory() as temp_directory:
        checkpoint = Path(temp_directory) / "checkpoint"
        bindings_checkpoint = _checkpoint_bindings()
        if bindings_checkpoint != bindings_start:
            raise RuntimeError("IMPLEMENTATION_BINDING_DRIFT")
        manifest = save_checkpoint(model_cpu, checkpoint, scaler=scaler, freeze_mode=FreezeMode.EMBEDDING_ATTENTION, bindings=bindings_checkpoint)
        rebuilt = load_checkpoint(
            checkpoint,
            expected_config=config,
            expected_scc_registry=scc,
            expected_ssc_registry=ssc,
            expected_scaler=scaler,
            expected_freeze_mode=FreezeMode.EMBEDDING_ATTENTION,
            expected_bindings=bindings_checkpoint,
            expected_manifest_id=manifest["manifest_id"],
            expected_weights_sha256=manifest["weights"]["sha256"],
        )
        roundtrip = rebuilt.predict(request)
    peak = int(torch.cuda.max_memory_allocated(device)) if device_name == "cuda:0" else 0
    if peak >= 2 * 1024**3:
        raise RuntimeError("GPU_CAP")
    if not pretrain_updated or not frozen_unchanged or not trainable_updated or not device_inference_deterministic or not torch.equal(prediction_one, prediction_two) or not torch.equal(prediction_one, roundtrip) or not bool(torch.isfinite(roundtrip).all()):
        raise RuntimeError("SMOKE_CONTRACT")
    return {
        "artifact_role": "DEVELOPMENT_SYNTHETIC_SMOKE",
        "device": device_name,
        "device_inference_deterministic": device_inference_deterministic,
        "fine_tune_loss": float(fine.detach().cpu()),
        "finite": True,
        "freeze_state_sha256": freeze.state_sha256,
        "frozen_unchanged_bitwise": frozen_unchanged,
        "map_prf_sha256": map_view.prf_sha256,
        "implementation_tree_sha256": bindings_start.implementation_tree_sha256,
        "network_request_count": 0,
        "peak_cuda_bytes": peak,
        "pretrain_loss": float(pretrain.detach().cpu()),
        "pretrain_updated": pretrain_updated,
        "prediction_deterministic_bitwise": True,
        "real_data_accessed": False,
        "trainable_updated": trainable_updated,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=("cpu", "cuda:0"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    metrics = run_smoke(arguments.device)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(metrics, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
