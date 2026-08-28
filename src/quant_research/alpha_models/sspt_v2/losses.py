from __future__ import annotations

import math

import numpy as np
import torch
from torch.nn import functional as F

from .contracts import CrossSectionTrainingBatch
from .model import MAPView, deterministic_map_view


def _weight(value: object, name: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(name)
    return float(value)


def pretrain_loss(
    outputs: tuple[torch.Tensor, torch.Tensor, torch.Tensor],
    batch: CrossSectionTrainingBatch,
    map_view: MAPView,
    *,
    alpha: float = 1.0,
    beta: float = 1.0,
    gamma: float = 0.0,
) -> torch.Tensor:
    if type(batch) is not CrossSectionTrainingBatch or type(map_view) is not MAPView or type(outputs) is not tuple or len(outputs) != 3:
        raise TypeError("pretrain_contract")
    scc_logits, ssc_logits, map_prediction = outputs
    if scc_logits.shape[0] != batch.size or ssc_logits.shape[0] != batch.size or map_prediction.shape != (batch.size,):
        raise ValueError("pretrain_shape")
    expected_view = deterministic_map_view(batch, seed=map_view.seed, mask_rate=map_view.mask_rate)
    if map_view.prf_sha256 != expected_view.prf_sha256 or not np.array_equal(map_view.mask, expected_view.mask) or not np.array_equal(map_view.target_raw_close_mean, expected_view.target_raw_close_mean):
        raise ValueError("map_view_binding")
    map_targets = torch.tensor(np.array(map_view.target_raw_close_mean, copy=True), dtype=map_prediction.dtype, device=map_prediction.device)
    if not all(bool(torch.isfinite(tensor).all()) for tensor in outputs) or not bool(torch.isfinite(map_targets).all()):
        raise ValueError("pretrain_finite")
    device = scc_logits.device
    scc = torch.tensor(np.array(batch.scc_targets, copy=True), dtype=torch.long, device=device)
    ssc = torch.tensor(np.array(batch.ssc_targets, copy=True), dtype=torch.long, device=device)
    weights = (_weight(alpha, "alpha"), _weight(beta, "beta"), _weight(gamma, "gamma"))
    if not any(weight > 0 for weight in weights):
        raise ValueError("all_zero_weights")
    return weights[0] * F.cross_entropy(scc_logits, scc) + weights[1] * F.cross_entropy(ssc_logits, ssc) + weights[2] * F.mse_loss(map_prediction, map_targets)


def fine_tune_loss(
    predictions: torch.Tensor,
    batch: CrossSectionTrainingBatch,
    *,
    epsilon: float = 1.0,
) -> torch.Tensor:
    if type(batch) is not CrossSectionTrainingBatch or type(predictions) is not torch.Tensor:
        raise TypeError("fine_tune_contract")
    if predictions.shape != (batch.size,):
        raise ValueError("fine_tune_shape")
    if not bool(torch.isfinite(predictions).all()):
        raise ValueError("prediction_finite")
    prediction = predictions
    target = torch.tensor(np.array(batch.next_session_returns, copy=True), dtype=predictions.dtype, device=predictions.device)
    squared_error = torch.sum((prediction - target) ** 2)
    prediction_difference = prediction[:, None] - prediction[None, :]
    true_difference = target[:, None] - target[None, :]
    ranking = torch.sum(torch.relu(-(prediction_difference * true_difference)))
    return squared_error + _weight(epsilon, "epsilon") * ranking
