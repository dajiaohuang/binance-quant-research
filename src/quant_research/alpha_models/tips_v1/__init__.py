"""Clean-room, synthetic-only TIPS method contract.

This package deliberately contains no upstream code and no data acquisition path.
"""

from .contracts import (
    FEATURE_ORDER,
    FEATURE_SCHEMA_SHA256,
    BIAS_REGISTRY_SHA256,
    PipelineState,
    TIPSConfig,
    TIPSInferenceBatch,
    TIPSSmokeOverride,
    TIPSTrainingBatch,
    TeacherKind,
)
from .data import MarketCalendar, SymbolOHLCV, build_inference_batch, build_training_batch
from .losses import distillation_loss, pairwise_soft_rank, teacher_rank_loss
from .model import TIPSBackbone
from .pipeline import StudentStepReceipt, SWASnapshot, TIPSPipeline, validate_swa_snapshot

__all__ = [
    "BIAS_REGISTRY_SHA256",
    "FEATURE_ORDER",
    "FEATURE_SCHEMA_SHA256",
    "MarketCalendar",
    "PipelineState",
    "SymbolOHLCV",
    "StudentStepReceipt",
    "SWASnapshot",
    "TIPSBackbone",
    "TIPSConfig",
    "TIPSInferenceBatch",
    "TIPSPipeline",
    "TIPSSmokeOverride",
    "TIPSTrainingBatch",
    "TeacherKind",
    "build_inference_batch",
    "build_training_batch",
    "distillation_loss",
    "pairwise_soft_rank",
    "teacher_rank_loss",
    "validate_swa_snapshot",
]
