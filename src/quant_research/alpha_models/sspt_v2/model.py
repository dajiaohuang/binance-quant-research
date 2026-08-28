from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib

import numpy as np
import torch
from torch import nn

from .contracts import (
    CrossSectionInferenceBatch,
    CrossSectionTrainingBatch,
    SSPTConfigV2,
    StableLabelRegistry,
    _exact_int,
    sha256_canonical,
    validate_hex64,
)


class FreezeMode(str, Enum):
    NONE = "NONE"
    EMBEDDING = "EMBEDDING"
    EMBEDDING_ATTENTION = "EMBEDDING_ATTENTION"
    BACKBONE = "BACKBONE"


@dataclass(frozen=True)
class FreezeState:
    mode: FreezeMode
    frozen_parameter_names: tuple[str, ...]
    trainable_parameter_names: tuple[str, ...]
    state_sha256: str


@dataclass(frozen=True)
class MAPView:
    mask: np.ndarray
    target_raw_close_mean: np.ndarray
    prf_sha256: str
    seed: int
    mask_rate: float

    def __post_init__(self) -> None:
        mask = np.array(self.mask, dtype=np.bool_, copy=True, order="C")
        target = np.array(self.target_raw_close_mean, dtype=np.float64, copy=True, order="C")
        if mask.ndim != 2 or target.shape != (mask.shape[0],) or not np.isfinite(target).all() or not np.all(mask.any(axis=1)):
            raise ValueError("map_view")
        mask.setflags(write=False)
        target.setflags(write=False)
        _exact_int(self.seed, "seed")
        if type(self.mask_rate) not in (int, float) or isinstance(self.mask_rate, bool) or not 0.0 < float(self.mask_rate) < 1.0:
            raise ValueError("mask_rate")
        object.__setattr__(self, "mask", mask)
        object.__setattr__(self, "target_raw_close_mean", target)


def deterministic_map_view(batch: CrossSectionTrainingBatch, *, seed: int, mask_rate: float) -> MAPView:
    if type(batch) is not CrossSectionTrainingBatch:
        raise TypeError("batch")
    _exact_int(seed, "seed")
    if type(mask_rate) not in (int, float) or isinstance(mask_rate, bool) or not 0.0 < float(mask_rate) < 1.0:
        raise ValueError("mask_rate")
    exact_count = int(float(mask_rate) * batch.features.shape[1])
    if exact_count < 1:
        raise ValueError("mask_count")
    mask = np.zeros((batch.size, batch.features.shape[1]), dtype=np.bool_)
    all_digests: list[list[bytes]] = []
    for row, symbol in enumerate(batch.symbols):
        row_digests: list[bytes] = []
        for column, session_id in enumerate(batch.feature_session_ids[row]):
            preimage = f"SSPT_MAP_SHA256_V1\0{seed}\0{batch.clock.market_id}\0{symbol}\0{session_id}".encode("utf-8")
            digest = hashlib.sha256(preimage).digest()
            row_digests.append(digest)
        for selected in sorted(range(len(row_digests)), key=lambda index: row_digests[index])[:exact_count]:
            mask[row, selected] = True
        all_digests.append(row_digests)
    target = np.asarray([float(np.mean(batch.raw_close_window[row])) for row in range(batch.size)], dtype=np.float64)
    evidence = {
        "mask": mask.astype(np.uint8).tolist(),
        "mask_rate": float(mask_rate),
        "prf": "SHA256_RANK_TOPK_V1",
        "seed": seed,
        "source": "PRE_MASK_RAW_CLOSE",
        "target": target.tolist(),
    }
    return MAPView(mask, target, sha256_canonical(evidence), seed, float(mask_rate))


@dataclass(frozen=True)
class SSPTInferenceRequestV2:
    batch: CrossSectionInferenceBatch

    def validate(self, config: SSPTConfigV2) -> None:
        if type(self.batch) is not CrossSectionInferenceBatch:
            raise ValueError("inference_batch")
        if self.batch.features.shape[1:] != (config.lookback, config.feature_count):
            raise ValueError("inference_shape")


class SSPTModelV2(nn.Module):
    def __init__(
        self,
        config: SSPTConfigV2,
        *,
        scc_registry: StableLabelRegistry,
        ssc_registry: StableLabelRegistry,
        scaler_sha256: str,
    ) -> None:
        super().__init__()
        if type(config) is not SSPTConfigV2 or type(scc_registry) is not StableLabelRegistry or type(ssc_registry) is not StableLabelRegistry:
            raise TypeError("model_contract")
        self.config = config
        if config.scc_classes != len(scc_registry.labels) or config.ssc_classes != len(ssc_registry.labels):
            raise ValueError("registry_class_count")
        validate_hex64(scaler_sha256, "scaler_sha256")
        self.scc_registry = scc_registry
        self.ssc_registry = ssc_registry
        self.scaler_sha256 = scaler_sha256
        self.feature_embedding = nn.Linear(config.feature_count, config.d_model)
        self.position_embedding = nn.Parameter(torch.zeros(1, config.lookback, config.d_model))
        self.mask_token = nn.Parameter(torch.zeros(1, 1, config.d_model))
        layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.heads,
            dim_feedforward=config.ffn_dim,
            dropout=config.dropout,
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, config.layers)
        self.scc_head = nn.Linear(config.d_model, config.scc_classes)
        self.ssc_head = nn.Linear(config.d_model, config.ssc_classes)
        self.map_head = nn.Linear(config.d_model, 1)
        self.return_head = nn.Linear(config.d_model, 1)
        self._freeze_mode = FreezeMode.NONE

    def _validate_features(self, features: torch.Tensor) -> None:
        if type(features) is not torch.Tensor or features.ndim != 3 or tuple(features.shape[1:]) != (self.config.lookback, self.config.feature_count):
            raise ValueError("features")
        if not torch.is_floating_point(features) or not bool(torch.isfinite(features).all()):
            raise ValueError("features")

    def _validate_batch_contract(self, batch: CrossSectionInferenceBatch) -> None:
        if not isinstance(batch, CrossSectionInferenceBatch):
            raise TypeError("batch")
        if batch.features.shape[1:] != (self.config.lookback, self.config.feature_count):
            raise ValueError("batch_shape")
        if batch.scaler_sha256 != self.scaler_sha256 or batch.scc_registry_sha256 != self.scc_registry.sha256 or batch.ssc_registry_sha256 != self.ssc_registry.sha256:
            raise ValueError("batch_binding")
    def encode(self, features: torch.Tensor, map_mask: torch.Tensor | None = None) -> torch.Tensor:
        self._validate_features(features)
        embedded = self.feature_embedding(features)
        if map_mask is not None:
            if type(map_mask) is not torch.Tensor or map_mask.dtype is not torch.bool or tuple(map_mask.shape) != tuple(features.shape[:2]):
                raise ValueError("map_mask")
            embedded = torch.where(map_mask.unsqueeze(-1), self.mask_token.expand_as(embedded), embedded)
        encoded = self.encoder(embedded + self.position_embedding[:, : features.shape[1]])
        return encoded.mean(dim=1)

    def pretrain_tensors(self, features: torch.Tensor, map_mask: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        representation = self.encode(features, map_mask)
        return (
            self.scc_head(representation),
            self.ssc_head(representation),
            self.map_head(representation).squeeze(-1),
        )

    def forward_tensors(self, features: torch.Tensor) -> torch.Tensor:
        return self.return_head(self.encode(features)).squeeze(-1)

    def pretrain(self, batch: CrossSectionTrainingBatch, map_view: MAPView) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        if type(batch) is not CrossSectionTrainingBatch or type(map_view) is not MAPView:
            raise TypeError("pretrain_batch")
        self._validate_batch_contract(batch)
        device = next(self.parameters()).device
        features = torch.tensor(np.array(batch.features, copy=True), dtype=torch.float32, device=device)
        mask = torch.tensor(np.array(map_view.mask, copy=True), dtype=torch.bool, device=device)
        return self.pretrain_tensors(features, mask)

    def fine_tune_scores(self, batch: CrossSectionTrainingBatch) -> torch.Tensor:
        if type(batch) is not CrossSectionTrainingBatch:
            raise TypeError("fine_tune_batch")
        self._validate_batch_contract(batch)
        device = next(self.parameters()).device
        features = torch.tensor(np.array(batch.features, copy=True), dtype=torch.float32, device=device)
        return self.forward_tensors(features)

    def predict(self, request: SSPTInferenceRequestV2) -> torch.Tensor:
        if type(request) is not SSPTInferenceRequestV2:
            raise TypeError("request")
        request.validate(self.config)
        self._validate_batch_contract(request.batch)
        prior_training = self.training
        self.eval()
        device = next(self.parameters()).device
        try:
            with torch.inference_mode():
                features = torch.tensor(np.array(request.batch.features, copy=True), dtype=torch.float32, device=device)
                result = self.forward_tensors(features).detach().cpu()
        finally:
            self.train(prior_training)
        return result

    def set_freeze_mode(self, mode: FreezeMode) -> FreezeState:
        if type(mode) is not FreezeMode:
            raise ValueError("freeze_mode")
        for parameter in self.parameters():
            parameter.requires_grad = True
        freeze_prefixes: tuple[str, ...]
        if mode is FreezeMode.NONE:
            freeze_prefixes = ()
        elif mode is FreezeMode.EMBEDDING:
            freeze_prefixes = ("feature_embedding.", "position_embedding", "mask_token")
        elif mode is FreezeMode.EMBEDDING_ATTENTION:
            freeze_prefixes = ("feature_embedding.", "position_embedding", "mask_token") + tuple(
                f"encoder.layers.{index}.self_attn." for index in range(self.config.layers)
            )
        else:
            freeze_prefixes = ("feature_embedding.", "position_embedding", "mask_token", "encoder.")
        for name, parameter in self.named_parameters():
            if any(name == prefix or name.startswith(prefix) for prefix in freeze_prefixes):
                parameter.requires_grad = False
        self._freeze_mode = mode
        frozen = tuple(name for name, parameter in self.named_parameters() if not parameter.requires_grad)
        trainable = tuple(name for name, parameter in self.named_parameters() if parameter.requires_grad)
        projection = {"frozen_parameter_names": list(frozen), "mode": mode.value, "trainable_parameter_names": list(trainable)}
        return FreezeState(mode, frozen, trainable, sha256_canonical(projection))

    @property
    def freeze_mode(self) -> FreezeMode:
        return self._freeze_mode
