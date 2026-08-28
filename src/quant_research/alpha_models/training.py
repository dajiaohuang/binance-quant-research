"""Deterministic synthetic-only training and inference for DDGLNet."""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import torch
from torch import Tensor

from .contracts import (
    DDGLConfig,
    DDGLContractError,
    DDGLInputBatch,
    DDGLTrainingExample,
    validate_input,
    validate_training_example,
)
from .ddgl_net import DDGLNet


class DDGLResourceUnavailable(RuntimeError):
    """A preregistered compute gate prevented execution."""


@dataclass(frozen=True)
class DDGLTrainingResult:
    model: DDGLNet
    initial_loss: float
    final_loss: float
    parameters_updated: bool
    precision_used: str
    device_used: str
    peak_vram_mib: float


@dataclass(frozen=True)
class DDGLPrediction:
    symbols: tuple[str, ...]
    formation_time_ms: int
    known_at_ms: int
    horizon_hours: int
    values: tuple[float, ...]
    input_provenance_sha256: str


def set_deterministic_seed(seed: int) -> None:
    if type(seed) is not int or seed < 0:
        raise DDGLContractError("seed must be an exact nonnegative integer")
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True


def _resolve_device(config: DDGLConfig) -> torch.device:
    if config.runtime.device == "cpu":
        return torch.device("cpu")
    if not torch.cuda.is_available():
        raise DDGLResourceUnavailable("CUDA runtime is unavailable")
    device = torch.device("cuda", torch.cuda.current_device())
    free_bytes, total_bytes = torch.cuda.mem_get_info(device)
    free_mib = free_bytes // (1024 * 1024)
    if free_mib < config.runtime.min_free_vram_mib:
        raise DDGLResourceUnavailable(
            f"free VRAM {free_mib} MiB is below frozen resource gate"
        )
    torch.cuda.set_per_process_memory_fraction(
        min(1.0, config.runtime.max_vram_mib * 1024 * 1024 / total_bytes),
        device,
    )
    return device


def _precision(config: DDGLConfig, device: torch.device) -> str:
    if config.optimization.precision == "FP32":
        return "FP32"
    supported = device.type == "cuda" and torch.cuda.is_bf16_supported()
    if supported:
        return "BF16"
    if config.optimization.allow_precision_fallback:
        return "FP32_FALLBACK"
    raise DDGLResourceUnavailable("BF16 requested but unsupported")


def _input_tensors(inputs: Sequence[DDGLInputBatch], device: torch.device) -> tuple[Tensor, Tensor, Tensor]:
    coarse = torch.tensor(
        [item.coarse_values for item in inputs], dtype=torch.float32, device=device
    )
    fine = torch.tensor(
        [item.fine_values for item in inputs], dtype=torch.float32, device=device
    )
    global_market = torch.tensor(
        [item.global_market_values for item in inputs],
        dtype=torch.float32,
        device=device,
    )
    return coarse, fine, global_market


def _loss_for_examples(
    model: DDGLNet,
    examples: Sequence[DDGLTrainingExample],
    device: torch.device,
    precision: str,
) -> Tensor:
    inputs = [example.inputs for example in examples]
    coarse, fine, global_market = _input_tensors(inputs, device)
    labels = torch.tensor(
        [example.labels.values for example in examples],
        dtype=torch.float32,
        device=device,
    )
    with torch.autocast(
        device_type=device.type,
        dtype=torch.bfloat16,
        enabled=precision == "BF16",
    ):
        prediction = model(coarse, fine, global_market)
        return torch.nn.functional.mse_loss(prediction.float(), labels)


def fit_synthetic(
    config: DDGLConfig,
    examples: Sequence[DDGLTrainingExample],
) -> DDGLTrainingResult:
    if type(config) is not DDGLConfig or config.empirical_authorized:
        raise DDGLContractError("trainer requires exact synthetic-only config")
    if not examples:
        raise DDGLContractError("training requires non-empty synthetic examples")
    checked = tuple(validate_training_example(item, config) for item in examples)
    device = _resolve_device(config)
    precision = _precision(config, device)
    set_deterministic_seed(config.seed)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    model = DDGLNet(config).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.optimization.learning_rate,
        weight_decay=config.optimization.weight_decay,
    )
    initial_state = tuple(parameter.detach().cpu().clone() for parameter in model.parameters())
    model.train()
    with torch.no_grad():
        initial_loss = float(_loss_for_examples(model, checked, device, precision).item())
    batch_size = config.optimization.batch_size
    for _ in range(config.optimization.epochs):
        for start in range(0, len(checked), batch_size):
            batch = checked[start : start + batch_size]
            optimizer.zero_grad(set_to_none=True)
            loss = _loss_for_examples(model, batch, device, precision)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                model.parameters(), config.optimization.grad_clip_norm
            )
            optimizer.step()
    model.eval()
    with torch.no_grad():
        final_loss = float(_loss_for_examples(model, checked, device, precision).item())
    updated = any(
        not torch.equal(before, after.detach().cpu())
        for before, after in zip(initial_state, model.parameters(), strict=True)
    )
    peak_mib = 0.0
    if device.type == "cuda":
        peak_mib = torch.cuda.max_memory_allocated(device) / (1024 * 1024)
        if peak_mib > config.runtime.max_vram_mib:
            raise DDGLResourceUnavailable(
                f"observed peak {peak_mib:.3f} MiB exceeds frozen cap"
            )
    if not all(math.isfinite(value) for value in (initial_loss, final_loss, peak_mib)):
        raise DDGLContractError("training produced non-finite metrics")
    return DDGLTrainingResult(
        model=model,
        initial_loss=initial_loss,
        final_loss=final_loss,
        parameters_updated=updated,
        precision_used=precision,
        device_used=device.type,
        peak_vram_mib=peak_mib,
    )


def infer(
    model: DDGLNet,
    inputs: Sequence[DDGLInputBatch],
    config: DDGLConfig,
) -> tuple[DDGLPrediction, ...]:
    if type(model) is not DDGLNet or not inputs:
        raise DDGLContractError("inference requires DDGLNet and non-empty inputs")
    if model.config.config_sha256 != config.config_sha256:
        raise DDGLContractError("model and inference config binding mismatch")
    checked = tuple(validate_input(item, config) for item in inputs)
    device = next(model.parameters()).device
    precision = _precision(config, device)
    coarse, fine, global_market = _input_tensors(checked, device)
    model.eval()
    with torch.no_grad(), torch.autocast(
        device_type=device.type,
        dtype=torch.bfloat16,
        enabled=precision == "BF16",
    ):
        values = model(coarse, fine, global_market).float().cpu()
    result: list[DDGLPrediction] = []
    for index, item in enumerate(checked):
        row = tuple(float(value) for value in values[index].tolist())
        if not all(math.isfinite(value) for value in row):
            raise DDGLContractError("inference produced non-finite output")
        result.append(
            DDGLPrediction(
                symbols=item.symbols,
                formation_time_ms=item.formation_time_ms,
                known_at_ms=max(
                    item.coarse_known_at_ms[-1],
                    item.fine_known_at_ms[-1],
                    item.global_known_at_ms[-1],
                ),
                horizon_hours=item.horizon_hours,
                values=row,
                input_provenance_sha256=item.provenance_sha256,
            )
        )
    return tuple(result)


def save_checkpoint(model: DDGLNet, config: DDGLConfig, path: str | Path) -> str:
    target = Path(path)
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {"config_sha256": config.config_sha256, "state_dict": model.state_dict()},
        target,
    )
    return hashlib.sha256(target.read_bytes()).hexdigest()


def load_checkpoint(config: DDGLConfig, path: str | Path, device: str = "cpu") -> DDGLNet:
    if device not in {"cpu", "cuda"}:
        raise DDGLContractError("checkpoint device must be cpu or cuda")
    payload = torch.load(path, map_location=device, weights_only=True)
    if type(payload) is not dict or set(payload) != {"config_sha256", "state_dict"}:
        raise DDGLContractError("checkpoint envelope mismatch")
    if payload["config_sha256"] != config.config_sha256:
        raise DDGLContractError("checkpoint config binding mismatch")
    if not isinstance(payload["state_dict"], dict):
        raise DDGLContractError("checkpoint state_dict mismatch")
    model = DDGLNet(config).to(device)
    model.load_state_dict(payload["state_dict"], strict=True)
    model.eval()
    return model


__all__ = [
    "DDGLPrediction",
    "DDGLResourceUnavailable",
    "DDGLTrainingResult",
    "fit_synthetic",
    "infer",
    "load_checkpoint",
    "save_checkpoint",
    "set_deterministic_seed",
]
