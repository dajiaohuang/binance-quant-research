"""Clean-room synthetic DDGL-style neural architecture.

This is a compact contract implementation, not copied community code and not
a claim of paper-faithful reproduction.  It implements only the preregistered
structural ideas: dynamic temporal/cross-sectional encoders, feature-wise
multi-scale fusion, and a base-plus-residual mixture of experts.
"""

from __future__ import annotations

import math

import torch
from torch import Tensor, nn

from .contracts import DDGLConfig, DDGLContractError


class DDGEEncoder(nn.Module):
    """Temporal encoder followed by a permutation-equivariant dynamic graph."""

    def __init__(self, input_dim: int, hidden_dim: int, top_k: int, dropout: float):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.top_k = top_k
        self.temporal = nn.GRU(input_dim, hidden_dim, batch_first=True)
        self.query = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.key = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.value = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.output = nn.Linear(hidden_dim, hidden_dim)
        self.dropout = nn.Dropout(dropout)
        self.norm = nn.LayerNorm(hidden_dim)

    def forward(self, values: Tensor) -> Tensor:
        if values.ndim != 4:
            raise DDGLContractError("DDGE input must be [batch,time,asset,feature]")
        batch, steps, assets, features = values.shape
        temporal_input = values.permute(0, 2, 1, 3).reshape(
            batch * assets, steps, features
        )
        _, hidden = self.temporal(temporal_input)
        encoded = hidden[-1].reshape(batch, assets, self.hidden_dim)

        query = self.query(encoded)
        key = self.key(encoded)
        logits = torch.matmul(query, key.transpose(-1, -2)) / math.sqrt(
            self.hidden_dim
        )
        if self.top_k < assets:
            threshold = logits.topk(self.top_k, dim=-1).values[..., -1:]
            logits = logits.masked_fill(logits < threshold, float("-inf"))
        weights = torch.softmax(logits, dim=-1)
        graph_message = torch.matmul(weights, self.value(encoded))
        return self.norm(encoded + self.dropout(self.output(graph_message)))


class DDGLNet(nn.Module):
    """Two-resolution graph encoder with market fusion and residual MoE."""

    def __init__(self, config: DDGLConfig):
        super().__init__()
        if type(config) is not DDGLConfig or config.empirical_authorized:
            raise DDGLContractError("model requires exact synthetic-only config")
        self.config = config
        spec = config.synthetic
        architecture = config.architecture
        hidden = architecture.hidden_dim
        self.coarse_encoder = DDGEEncoder(
            spec.coarse_features, hidden, architecture.graph_top_k, architecture.dropout
        )
        self.fine_encoder = DDGEEncoder(
            spec.fine_features, hidden, architecture.graph_top_k, architecture.dropout
        )
        self.global_encoder = nn.GRU(spec.global_features, hidden, batch_first=True)

        self.fusion_logits = nn.Sequential(
            nn.Linear(hidden * 3, hidden),
            nn.GELU(),
            nn.Linear(hidden, hidden * 3),
        )
        self.fusion_norm = nn.LayerNorm(hidden)
        self.base_head = nn.Linear(hidden, 1)
        self.experts = nn.ModuleList(
            nn.Sequential(
                nn.Linear(hidden, architecture.moe_hidden_dim),
                nn.GELU(),
                nn.Dropout(architecture.dropout),
                nn.Linear(architecture.moe_hidden_dim, 1),
            )
            for _ in range(architecture.moe_experts)
        )
        self.expert_gate = nn.Linear(hidden, architecture.moe_experts)

    def broadcast_global_market(self, global_market: Tensor, assets: int) -> Tensor:
        if global_market.ndim != 3:
            raise DDGLContractError(
                "global market input must be [batch,time,feature]"
            )
        _, hidden = self.global_encoder(global_market)
        return hidden[-1].unsqueeze(1).expand(-1, assets, -1)

    def forward(self, coarse: Tensor, fine: Tensor, global_market: Tensor) -> Tensor:
        if coarse.ndim != 4 or fine.ndim != 4:
            raise DDGLContractError("coarse/fine inputs must be rank four")
        if coarse.shape[0] != fine.shape[0] or coarse.shape[2] != fine.shape[2]:
            raise DDGLContractError("coarse/fine batch and asset dimensions must match")
        coarse_hidden = self.coarse_encoder(coarse)
        fine_hidden = self.fine_encoder(fine)
        global_hidden = self.broadcast_global_market(global_market, fine.shape[2])
        branches = torch.stack((fine_hidden, coarse_hidden, global_hidden), dim=-2)
        fusion_input = torch.cat((fine_hidden, coarse_hidden, global_hidden), dim=-1)
        logits = self.fusion_logits(fusion_input).reshape(
            *fusion_input.shape[:-1], 3, self.config.architecture.hidden_dim
        )
        fused = self.fusion_norm(
            (torch.softmax(logits, dim=-2) * branches).sum(dim=-2)
        )
        base = self.base_head(fused).squeeze(-1)
        residuals = torch.cat([expert(fused) for expert in self.experts], dim=-1)
        gates = torch.softmax(self.expert_gate(fused), dim=-1)
        return base + (gates * residuals).sum(dim=-1)


__all__ = ["DDGEEncoder", "DDGLNet"]
