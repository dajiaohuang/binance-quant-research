"""Synthetic-only, clean-room SSPT typed method contract V2."""

from .contracts import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_SHA256,
    CrossSectionClock,
    CrossSectionInferenceBatch,
    CrossSectionTrainingBatch,
    SSPTConfigV2,
    StableLabelRegistry,
    SymbolDailySeries,
)
from .data import TrainingFeaturePartition, TrainOnlyMinMax, build_cross_section_batch, build_feature_matrix, purged_time_split
from .losses import fine_tune_loss, pretrain_loss
from .model import FreezeMode, SSPTInferenceRequestV2, SSPTModelV2, deterministic_map_view

__all__ = [
    "FEATURE_NAMES",
    "FEATURE_SCHEMA_SHA256",
    "CrossSectionClock",
    "CrossSectionInferenceBatch",
    "CrossSectionTrainingBatch",
    "FreezeMode",
    "SSPTConfigV2",
    "SSPTInferenceRequestV2",
    "SSPTModelV2",
    "StableLabelRegistry",
    "SymbolDailySeries",
    "TrainOnlyMinMax",
    "TrainingFeaturePartition",
    "build_cross_section_batch",
    "build_feature_matrix",
    "deterministic_map_view",
    "fine_tune_loss",
    "pretrain_loss",
    "purged_time_split",
]
