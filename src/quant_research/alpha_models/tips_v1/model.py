from __future__ import annotations

import hashlib

import numpy as np
import torch
from torch import nn

from .biases import learned_rpb_indices, temporal_bias
from .contracts import TIPSConfig, TIPSInferenceBatch, TeacherKind


def _tensor_digest(state: dict[str, torch.Tensor]) -> str:
    digest = hashlib.sha256()
    for name in sorted(state):
        tensor = state[name].detach().cpu().contiguous()
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(str(tensor.dtype).encode("ascii") + b"\0")
        digest.update(str(tuple(tensor.shape)).encode("ascii") + b"\0")
        digest.update(tensor.numpy().tobytes(order="C"))
    return digest.hexdigest()


class TemporalBlock(nn.Module):
    def __init__(self, config: TIPSConfig) -> None:
        super().__init__()
        self.norm1 = nn.LayerNorm(config.d_model)
        self.attention = nn.MultiheadAttention(config.d_model, config.heads, dropout=config.dropout, batch_first=True)
        self.norm2 = nn.LayerNorm(config.d_model)
        self.ffn = nn.Sequential(
            nn.Linear(config.d_model, config.ffn_dim),
            nn.GELU(),
            nn.Linear(config.ffn_dim, config.d_model),
        )

    def forward(self, values: torch.Tensor, bias: torch.Tensor) -> torch.Tensor:
        batch_size = values.shape[0]
        normalized = self.norm1(values)
        attention_mask = bias.repeat(batch_size, 1, 1)
        attended, _ = self.attention(normalized, normalized, normalized, attn_mask=attention_mask, need_weights=False)
        values = values + attended
        return values + self.ffn(self.norm2(values))


class TIPSBackbone(nn.Module):
    """Independent clean-room temporal encoder producing one logit per symbol."""

    def __init__(self, config: TIPSConfig, kind: TeacherKind, *, role: str) -> None:
        super().__init__()
        if type(config) is not TIPSConfig or type(kind) is not TeacherKind or role not in ("TEACHER", "STUDENT"):
            raise ValueError("model_identity")
        if role == "STUDENT" and kind is not TeacherKind.VANILLA:
            raise ValueError("student_is_vanilla")
        self.config = config
        self.kind = kind
        self.role = role
        input_width = 16 if kind is TeacherKind.PATCH_LEN2_STRIDE1 else 8
        token_count = 19 if kind is TeacherKind.PATCH_LEN2_STRIDE1 else 20
        self.input_projection = nn.Linear(input_width, config.d_model)
        self.position = nn.Parameter(torch.zeros(1, token_count, config.d_model))
        nn.init.normal_(self.position, mean=0.0, std=0.02)
        self.blocks = nn.ModuleList([TemporalBlock(config) for _ in range(config.layers)])
        self.readout = nn.Linear(config.d_model, 1)
        self.relative_bias = nn.Parameter(torch.zeros(config.heads, 39)) if kind is TeacherKind.LEARNED_RPB else None

    def _tokens(self, features: torch.Tensor) -> torch.Tensor:
        if self.kind is TeacherKind.PATCH_LEN2_STRIDE1:
            features = torch.cat((features[:, :-1, :], features[:, 1:, :]), dim=-1)
        return self.input_projection(features) + self.position

    def _bias(self, length: int, tokens: torch.Tensor) -> torch.Tensor:
        bias = temporal_bias(self.kind, length=length, device=tokens.device, dtype=tokens.dtype)
        if self.kind is TeacherKind.LEARNED_RPB:
            assert self.relative_bias is not None
            indices = learned_rpb_indices(length, device=tokens.device)
            bias = bias + self.relative_bias[:, indices]
        return bias

    def forward(self, batch: TIPSInferenceBatch) -> torch.Tensor:
        if not isinstance(batch, TIPSInferenceBatch):
            raise TypeError("inference_batch")
        device = self.position.device
        features = torch.as_tensor(np.array(batch.features, copy=True), dtype=self.position.dtype, device=device)
        tokens = self._tokens(features)
        bias = self._bias(tokens.shape[1], tokens)
        for block in self.blocks:
            tokens = block(tokens, bias)
        # LOCAL_DISCLOSED_CHOICE: stable temporal mean pooling.
        return self.readout(tokens.mean(dim=1)).squeeze(-1)

    @property
    def state_sha256(self) -> str:
        return _tensor_digest(self.state_dict())

    def freeze(self) -> None:
        self.eval()
        for parameter in self.parameters():
            parameter.grad = None
            parameter.requires_grad_(False)

    def assert_frozen(self) -> None:
        if self.training or any(parameter.requires_grad for parameter in self.parameters()):
            raise RuntimeError("model_not_frozen")


def state_dict_sha256(model: nn.Module) -> str:
    return _tensor_digest(model.state_dict())
