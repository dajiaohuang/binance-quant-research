from __future__ import annotations

import math

import torch

from .contracts import TeacherKind


ALIBI_SLOPES = (2.0**-8, 2.0**-4, 2.0 ** (-8.0 / 3.0), 2.0**-2)
PERIODS = (5, 10, 15, 20)


def temporal_bias(kind: TeacherKind, *, length: int, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
    if type(kind) is not TeacherKind or type(length) is not int or length not in (19, 20):
        raise ValueError("bias_request")
    result = torch.zeros((4, length, length), device=device, dtype=dtype)
    if kind is TeacherKind.PAST_CAUSAL:
        result.masked_fill_(torch.triu(torch.ones(length, length, device=device, dtype=torch.bool), diagonal=1)[None], -torch.inf)
    elif kind is TeacherKind.FUTURE_REVERSE_SELF_SAFE:
        # LOCAL_DISCLOSED_CHOICE: retain the diagonal so the final row is never all-masked.
        result.masked_fill_(torch.tril(torch.ones(length, length, device=device, dtype=torch.bool), diagonal=-1)[None], -torch.inf)
    elif kind is TeacherKind.ALIBI:
        distance = torch.abs(torch.arange(length, device=device)[:, None] - torch.arange(length, device=device)[None, :]).to(dtype)
        for head, slope in enumerate(ALIBI_SLOPES):
            result[head] = -slope * distance
    elif kind is TeacherKind.FIXED_PERIODIC_LOCAL_SINUSOIDAL:
        # LOCAL_DISCLOSED_CHOICE: per-head symmetric cosine pairwise bias.
        offset = torch.arange(length, device=device)[:, None] - torch.arange(length, device=device)[None, :]
        for head, period in enumerate(PERIODS):
            result[head] = torch.cos(offset.to(dtype) * (2.0 * math.pi / period))
    elif kind in (TeacherKind.PATCH_LEN2_STRIDE1, TeacherKind.LEARNED_RPB, TeacherKind.VANILLA):
        pass
    else:
        raise ValueError("teacher_kind")
    return result


def learned_rpb_indices(length: int, *, device: torch.device) -> torch.Tensor:
    if type(length) is not int or length != 20:
        raise ValueError("rpb_length")
    offset = torch.arange(length, device=device)[:, None] - torch.arange(length, device=device)[None, :]
    return offset + 19
