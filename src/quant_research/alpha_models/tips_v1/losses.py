from __future__ import annotations

import torch
from torch.nn import functional as F


def _vector(value: torch.Tensor, name: str) -> torch.Tensor:
    if type(value) is not torch.Tensor or value.ndim != 1 or value.numel() < 2 or not torch.isfinite(value).all():
        raise ValueError(name)
    return value


def pairwise_soft_rank(logits: torch.Tensor, *, a: float = 1.0, valid_mask: torch.Tensor | None = None) -> torch.Tensor:
    logits = _vector(logits, "logits")
    if type(a) not in (int, float) or type(a) is bool or float(a) != 1.0:
        raise ValueError("a")
    if valid_mask is not None:
        if type(valid_mask) is not torch.Tensor or valid_mask.dtype is not torch.bool or valid_mask.ndim != 1 or valid_mask.shape != logits.shape:
            raise ValueError("valid_mask")
        if int(valid_mask.sum().item()) < 2:
            raise ValueError("insufficient_cross_section")
        logits = logits[valid_mask]
    # Stable sigmoid handles extreme finite logits; self-pairs contribute 0.5 uniformly.
    return torch.sigmoid(logits[:, None] - logits[None, :]).sum(dim=1)


def teacher_rank_loss(logits: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
    logits = _vector(logits, "logits")
    labels = _vector(labels, "labels")
    if logits.shape != labels.shape:
        raise ValueError("shape")
    predicted = pairwise_soft_rank(logits)
    target = pairwise_soft_rank(labels.detach())
    px = predicted - predicted.mean()
    py = target - target.mean()
    denominator = torch.linalg.vector_norm(px) * torch.linalg.vector_norm(py)
    if float(denominator.detach().cpu()) <= torch.finfo(logits.dtype).eps:
        return logits.sum() * 0.0
    return -(px * py).sum() / denominator


def distillation_target(teacher_logits: tuple[torch.Tensor, ...], *, temperature: float = 0.01, smoothing: float = 0.9) -> torch.Tensor:
    if type(teacher_logits) is not tuple or len(teacher_logits) != 7:
        raise ValueError("seven_teachers")
    checked = tuple(_vector(item, "teacher_logits") for item in teacher_logits)
    if any(item.shape != checked[0].shape for item in checked):
        raise ValueError("teacher_shape")
    if type(temperature) not in (int, float) or float(temperature) != 0.01 or type(smoothing) not in (int, float) or float(smoothing) != 0.9:
        raise ValueError("distillation_config")
    mean_logits = torch.stack(tuple(item.detach() for item in checked)).mean(dim=0)
    probability = torch.softmax(mean_logits / float(temperature), dim=0)
    return (1.0 - float(smoothing)) * probability + float(smoothing) / probability.numel()


def distillation_loss(student_logits: torch.Tensor, teacher_logits: tuple[torch.Tensor, ...]) -> torch.Tensor:
    student_logits = _vector(student_logits, "student_logits")
    target = distillation_target(teacher_logits)
    if student_logits.shape != target.shape:
        raise ValueError("student_shape")
    return -(target * F.log_softmax(student_logits, dim=0)).sum()
