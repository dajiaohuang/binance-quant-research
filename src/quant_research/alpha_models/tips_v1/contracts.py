from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any

import numpy as np


FEATURE_ORDER = (
    "open_z20",
    "high_z20",
    "low_z20",
    "close_z20",
    "volume_z20",
    "ma5_over_close_minus_1",
    "ma10_over_close_minus_1",
    "ma20_over_close_minus_1",
)
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,63}\Z")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


FEATURE_SCHEMA_SHA256 = sha256_canonical({"feature_order": list(FEATURE_ORDER), "lookback": 20})


def validate_hex64(value: object, name: str) -> str:
    if type(value) is not str or HEX64.fullmatch(value) is None:
        raise ValueError(name)
    return value


def exact_int(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(name)
    return value


def finite_float(value: object, name: str, *, minimum: float | None = None) -> float:
    if type(value) not in (int, float) or type(value) is bool:
        raise ValueError(name)
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(name)
    return result


def validate_symbol(value: object) -> str:
    if type(value) is not str or SYMBOL.fullmatch(value) is None:
        raise ValueError("symbol")
    return value


def feature_row_binding_sha256(symbol: str, features: np.ndarray, known_at_ms: np.ndarray) -> str:
    validate_symbol(symbol)
    feature = np.asarray(features, dtype=np.float64, order="C")
    known = np.asarray(known_at_ms, dtype=np.int64, order="C")
    digest = hashlib.sha256(symbol.encode("utf-8") + b"\0")
    digest.update(feature.tobytes(order="C"))
    digest.update(known.tobytes(order="C"))
    return digest.hexdigest()


def label_row_binding_sha256(symbol: str, close_t: float, close_future: float, label: float) -> str:
    validate_symbol(symbol)
    values = np.asarray((close_t, close_future, label), dtype=np.float64)
    return hashlib.sha256(symbol.encode("utf-8") + b"\0" + values.tobytes(order="C")).hexdigest()


def readonly_float_array(value: object, *, ndim: int, name: str) -> np.ndarray:
    source = np.asarray(value)
    if source.ndim != ndim or source.dtype.kind not in ("f", "i", "u"):
        raise ValueError(name)
    result = np.array(source, dtype=np.float64, copy=True, order="C")
    if not np.isfinite(result).all():
        raise ValueError(name)
    result.setflags(write=False)
    return result


def readonly_int_array(value: object, *, ndim: int, name: str) -> np.ndarray:
    source = np.asarray(value)
    if source.ndim != ndim or source.dtype.kind not in ("i", "u"):
        raise ValueError(name)
    if source.dtype.kind == "u" and source.size and int(np.max(source)) > np.iinfo(np.int64).max:
        raise ValueError(name)
    result = np.array(source, dtype=np.int64, copy=True, order="C")
    if np.any(result < 0):
        raise ValueError(name)
    result.setflags(write=False)
    return result


class TeacherKind(str, Enum):
    PAST_CAUSAL = "PAST_CAUSAL"
    FUTURE_REVERSE_SELF_SAFE = "FUTURE_REVERSE_SELF_SAFE"
    PATCH_LEN2_STRIDE1 = "PATCH_LEN2_STRIDE1"
    ALIBI = "ALIBI"
    FIXED_PERIODIC_LOCAL_SINUSOIDAL = "FIXED_PERIODIC_LOCAL_SINUSOIDAL"
    LEARNED_RPB = "LEARNED_RPB"
    VANILLA = "VANILLA"


TEACHER_KINDS = tuple(TeacherKind)
BIAS_REGISTRY = (
    {"id": "PAST_CAUSAL", "formula": "bias[h,i,j]=0 if j<=i else -inf", "status": "METHOD_CONTRACT"},
    {"id": "FUTURE_REVERSE_SELF_SAFE", "formula": "bias[h,i,j]=0 if j>=i else -inf", "status": "LOCAL_DISCLOSED_CHOICE"},
    {"id": "PATCH_LEN2_STRIDE1", "formula": "token[i]=concat(x[i],x[i+1]);i=0..18", "status": "METHOD_CONTRACT"},
    {"id": "ALIBI", "formula": "bias[h,i,j]=-slope[h]*abs(i-j);slopes=2^-8,2^-4,2^(-8/3),2^-2", "status": "METHOD_CONTRACT"},
    {"id": "FIXED_PERIODIC_LOCAL_SINUSOIDAL", "formula": "bias[h,i,j]=cos(2*pi*(i-j)/period[h]);periods=5,10,15,20", "status": "LOCAL_DISCLOSED_CHOICE"},
    {"id": "LEARNED_RPB", "formula": "learned_bias[h,offset+19];offset=i-j in [-19,19]", "status": "METHOD_CONTRACT"},
    {"id": "VANILLA", "formula": "bias[h,i,j]=0", "status": "METHOD_CONTRACT"},
)
BIAS_REGISTRY_SHA256 = sha256_canonical(BIAS_REGISTRY)


class PipelineState(str, Enum):
    INIT = "INIT"
    TEACHERS_TRAINING = "TEACHERS_TRAINING"
    SEVEN_TEACHERS_FROZEN = "SEVEN_TEACHERS_FROZEN"
    STUDENT_DISTILLING = "STUDENT_DISTILLING"
    SWA_ACTIVE = "SWA_ACTIVE"
    STUDENT_FROZEN = "STUDENT_FROZEN"
    INFERENCE = "INFERENCE"


@dataclass(frozen=True)
class TIPSConfig:
    lookback: int = 20
    feature_count: int = 8
    d_model: int = 64
    layers: int = 2
    heads: int = 4
    ffn_dim: int = 256
    dropout: float = 0.0
    label_q: int = 5
    rank_a: float = 1.0
    student_temperature: float = 0.01
    student_smoothing: float = 0.9
    paper_teacher_epochs: int = 100
    paper_student_epochs: int = 20
    paper_training_lr: float = 1e-4
    effective_batch_size: int = 256
    swa_final_epochs: int = 10
    required_swa_updates: int = 10

    def __post_init__(self) -> None:
        exact = {
            "lookback": 20,
            "feature_count": 8,
            "d_model": 64,
            "layers": 2,
            "heads": 4,
            "ffn_dim": 256,
            "label_q": 5,
            "paper_teacher_epochs": 100,
            "paper_student_epochs": 20,
            "effective_batch_size": 256,
            "swa_final_epochs": 10,
            "required_swa_updates": 10,
        }
        for name, expected in exact.items():
            if type(getattr(self, name)) is not int or getattr(self, name) != expected:
                raise ValueError(name)
        if finite_float(self.dropout, "dropout", minimum=0.0) != 0.0:
            raise ValueError("dropout")
        if finite_float(self.rank_a, "rank_a", minimum=0.0) != 1.0:
            raise ValueError("rank_a")
        if finite_float(self.student_temperature, "temperature", minimum=0.0) != 0.01:
            raise ValueError("temperature")
        if finite_float(self.student_smoothing, "smoothing", minimum=0.0) != 0.9:
            raise ValueError("smoothing")
        if finite_float(self.paper_training_lr, "lr", minimum=0.0) != 1e-4:
            raise ValueError("lr")

    def to_dict(self) -> dict[str, object]:
        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_dict(cls, value: object) -> TIPSConfig:
        names = {field.name for field in fields(cls)}
        if type(value) is not dict or set(value) != names:
            raise ValueError("config_keys")
        return cls(**value)

    @property
    def sha256(self) -> str:
        return sha256_canonical(self.to_dict())


@dataclass(frozen=True)
class TIPSSmokeOverride:
    teacher_steps_each: int = 1
    student_steps: int = 2
    required_swa_updates: int = 2
    synthetic_only: bool = True

    def __post_init__(self) -> None:
        for name in ("teacher_steps_each", "student_steps", "required_swa_updates"):
            exact_int(getattr(self, name), name, minimum=1)
        if self.required_swa_updates != 2:
            raise ValueError("required_swa_updates")
        if self.synthetic_only is not True:
            raise ValueError("synthetic_only")


@dataclass(frozen=True)
class TIPSInferenceBatch:
    market_id: str
    formation_session_id: str
    formation_time_ms: int
    symbols: tuple[str, ...]
    feature_row_symbols: tuple[str, ...]
    feature_row_binding_sha256s: tuple[str, ...]
    feature_session_ids: tuple[str, ...]
    feature_times_ms: tuple[int, ...]
    feature_known_at_ms: np.ndarray
    features: np.ndarray
    partition_id: str
    calendar_id: str
    feature_schema_sha256: str = FEATURE_SCHEMA_SHA256

    def __post_init__(self) -> None:
        for name in ("market_id", "formation_session_id", "partition_id"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError(name)
        validate_hex64(self.calendar_id, "calendar_id")
        exact_int(self.formation_time_ms, "formation_time_ms")
        if type(self.symbols) is not tuple or len(self.symbols) < 2:
            raise ValueError("symbols")
        for symbol in self.symbols:
            validate_symbol(symbol)
        if self.symbols != tuple(sorted(self.symbols, key=lambda item: item.encode("utf-8"))) or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbol_order")
        if type(self.feature_row_symbols) is not tuple or self.feature_row_symbols != self.symbols:
            raise ValueError("feature_row_symbols")
        if type(self.feature_session_ids) is not tuple or len(self.feature_session_ids) != 20 or len(set(self.feature_session_ids)) != 20:
            raise ValueError("feature_sessions")
        if type(self.feature_times_ms) is not tuple or len(self.feature_times_ms) != 20:
            raise ValueError("feature_times")
        previous = -1
        for session_id, timestamp in zip(self.feature_session_ids, self.feature_times_ms, strict=True):
            if type(session_id) is not str or not session_id:
                raise ValueError("feature_session_id")
            exact_int(timestamp, "feature_time")
            if timestamp <= previous:
                raise ValueError("feature_time_order")
            previous = timestamp
        if self.feature_session_ids[-1] != self.formation_session_id or self.feature_times_ms[-1] != self.formation_time_ms:
            raise ValueError("formation_alignment")
        features = readonly_float_array(self.features, ndim=3, name="features")
        known = readonly_int_array(self.feature_known_at_ms, ndim=2, name="feature_known_at")
        if features.shape != (len(self.symbols), 20, 8) or known.shape != (len(self.symbols), 20):
            raise ValueError("batch_shape")
        expected_row_bindings = tuple(feature_row_binding_sha256(symbol, features[index], known[index]) for index, symbol in enumerate(self.symbols))
        if type(self.feature_row_binding_sha256s) is not tuple or self.feature_row_binding_sha256s != expected_row_bindings:
            raise ValueError("feature_row_binding")
        expected_times = np.asarray(self.feature_times_ms, dtype=np.int64)
        if np.any(known > expected_times[None, :]) or np.any(known > self.formation_time_ms):
            raise ValueError("feature_known_at")
        if self.feature_schema_sha256 != FEATURE_SCHEMA_SHA256:
            raise ValueError("feature_schema")
        object.__setattr__(self, "features", features)
        object.__setattr__(self, "feature_known_at_ms", known)

    @property
    def size(self) -> int:
        return len(self.symbols)


@dataclass(frozen=True)
class TIPSTrainingBatch(TIPSInferenceBatch):
    partition_kind: str = "TRAIN"
    label_session_id: str = ""
    label_time_ms: int = 0
    label_known_at_ms: int = 0
    close_t_raw: np.ndarray | None = None
    close_t_plus_4_raw: np.ndarray | None = None
    labels: np.ndarray | None = None
    label_q: int = 5
    label_path_session_ids: tuple[str, ...] = ()
    partition_session_ids: tuple[str, ...] = ()
    label_row_symbols: tuple[str, ...] = ()
    label_row_binding_sha256s: tuple[str, ...] = ()
    label_path_times_ms: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.partition_kind != "TRAIN" or type(self.label_session_id) is not str or not self.label_session_id:
            raise ValueError("training_partition")
        if type(self.label_q) is not int or self.label_q != 5:
            raise ValueError("label_q")
        if type(self.label_path_session_ids) is not tuple or len(self.label_path_session_ids) != 5:
            raise ValueError("label_path")
        if self.label_path_session_ids[0] != self.formation_session_id or self.label_path_session_ids[-1] != self.label_session_id or len(set(self.label_path_session_ids)) != 5:
            raise ValueError("label_path")
        if type(self.partition_session_ids) is not tuple or len(set(self.partition_session_ids)) != len(self.partition_session_ids):
            raise ValueError("partition_sessions")
        partition = set(self.partition_session_ids)
        if any(session not in partition for session in self.label_path_session_ids):
            raise ValueError("purged_label_boundary")
        if type(self.label_row_symbols) is not tuple or self.label_row_symbols != self.symbols:
            raise ValueError("label_row_symbols")
        if type(self.label_path_times_ms) is not tuple or len(self.label_path_times_ms) != 5:
            raise ValueError("label_path_times")
        previous = -1
        for timestamp in self.label_path_times_ms:
            exact_int(timestamp, "label_path_time")
            if timestamp <= previous:
                raise ValueError("label_path_times")
            previous = timestamp
        if self.label_path_times_ms[0] != self.formation_time_ms or self.label_path_times_ms[-1] != self.label_time_ms:
            raise ValueError("label_path_times")
        exact_int(self.label_time_ms, "label_time_ms")
        exact_int(self.label_known_at_ms, "label_known_at_ms")
        if self.label_time_ms <= self.formation_time_ms or self.label_known_at_ms > self.label_time_ms:
            raise ValueError("label_clock")
        close_t = readonly_float_array(self.close_t_raw, ndim=1, name="close_t")
        close_future = readonly_float_array(self.close_t_plus_4_raw, ndim=1, name="close_future")
        labels = readonly_float_array(self.labels, ndim=1, name="labels")
        if close_t.shape != (self.size,) or close_future.shape != (self.size,) or labels.shape != (self.size,):
            raise ValueError("label_shape")
        if np.any(close_t <= 0) or np.any(close_future <= 0) or not np.array_equal(labels, close_future / close_t - 1.0):
            raise ValueError("label_identity")
        expected_label_bindings = tuple(label_row_binding_sha256(symbol, close_t[index], close_future[index], labels[index]) for index, symbol in enumerate(self.symbols))
        if type(self.label_row_binding_sha256s) is not tuple or self.label_row_binding_sha256s != expected_label_bindings:
            raise ValueError("label_row_binding")
        object.__setattr__(self, "close_t_raw", close_t)
        object.__setattr__(self, "close_t_plus_4_raw", close_future)
        object.__setattr__(self, "labels", labels)

    def as_inference_batch(self) -> TIPSInferenceBatch:
        return TIPSInferenceBatch(
            market_id=self.market_id,
            formation_session_id=self.formation_session_id,
            formation_time_ms=self.formation_time_ms,
            symbols=self.symbols,
            feature_row_symbols=self.feature_row_symbols,
            feature_row_binding_sha256s=self.feature_row_binding_sha256s,
            feature_session_ids=self.feature_session_ids,
            feature_times_ms=self.feature_times_ms,
            feature_known_at_ms=self.feature_known_at_ms,
            features=self.features,
            partition_id=self.partition_id,
            calendar_id=self.calendar_id,
            feature_schema_sha256=self.feature_schema_sha256,
        )
