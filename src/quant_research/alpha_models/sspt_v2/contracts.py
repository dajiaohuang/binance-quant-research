from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
import math
import re
from typing import Any

import numpy as np


FEATURE_NAMES = tuple(
    f"{column}_{suffix}"
    for column in ("open", "high", "low", "close", "volume")
    for suffix in ("ma5", "ma10", "ma20", "ma30", "raw")
)
FEATURE_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(FEATURE_NAMES, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
).hexdigest()
SYMBOL_RE = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}\Z", re.ASCII)
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _exact_int(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(name)
    return value


def _finite_float(value: object, name: str, *, minimum: float | None = None) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise ValueError(name)
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(name)
    return result


def validate_symbol(symbol: object) -> str:
    if type(symbol) is not str or SYMBOL_RE.fullmatch(symbol) is None:
        raise ValueError("symbol")
    return symbol


def validate_hex64(value: object, name: str) -> str:
    if type(value) is not str or HEX64_RE.fullmatch(value) is None:
        raise ValueError(name)
    return value


@dataclass(frozen=True)
class SSPTConfigV2:
    lookback: int = 16
    feature_count: int = 25
    d_model: int = 128
    heads: int = 4
    layers: int = 2
    ffn_dim: int = 512
    dropout: float = 0.1
    scc_classes: int = 2
    ssc_classes: int = 2
    map_mask_rate: float = 0.3

    def __post_init__(self) -> None:
        if type(self.lookback) is not int or self.lookback not in (16, 32):
            raise ValueError("lookback")
        exact = {
            "feature_count": (self.feature_count, 25),
            "d_model": (self.d_model, 128),
            "heads": (self.heads, 4),
            "layers": (self.layers, 2),
            "ffn_dim": (self.ffn_dim, 512),
        }
        for name, (actual, expected) in exact.items():
            if type(actual) is not int or actual != expected:
                raise ValueError(name)
        for name in ("scc_classes", "ssc_classes"):
            _exact_int(getattr(self, name), name, minimum=2)
        dropout = _finite_float(self.dropout, "dropout", minimum=0.0)
        rate = _finite_float(self.map_mask_rate, "map_mask_rate", minimum=0.0)
        if dropout >= 1.0 or not 0.0 < rate < 1.0:
            raise ValueError("probability")

    @classmethod
    def from_dict(cls, value: object) -> SSPTConfigV2:
        if type(value) is not dict or set(value) != {field.name for field in fields(cls)}:
            raise ValueError("config_keys")
        return cls(**value)

    def to_dict(self) -> dict[str, object]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True)
class StableLabelRegistry:
    registry_id: str
    labels: tuple[str, ...]
    authority_id: str
    training_partition_id: str
    known_at_ms: int

    def __post_init__(self) -> None:
        for name in ("registry_id", "authority_id", "training_partition_id"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip() or any(ord(char) < 0x20 for char in value):
                raise ValueError(name)
        _exact_int(self.known_at_ms, "registry_known_at_ms")
        if type(self.labels) is not tuple or len(self.labels) < 2:
            raise ValueError("labels")
        for label in self.labels:
            if type(label) is not str or not label or label != label.strip() or any(ord(char) < 0x20 for char in label):
                raise ValueError("label")
        if len(set(self.labels)) != len(self.labels) or self.labels != tuple(sorted(self.labels, key=lambda x: x.encode("utf-8"))):
            raise ValueError("registry_order")

    @classmethod
    def from_labels(
        cls,
        registry_id: str,
        labels: tuple[str, ...],
        *,
        authority_id: str,
        training_partition_id: str,
        known_at_ms: int,
    ) -> StableLabelRegistry:
        if type(labels) is not tuple:
            raise ValueError("labels")
        return cls(
            registry_id,
            tuple(sorted(labels, key=lambda x: x.encode("utf-8"))),
            authority_id,
            training_partition_id,
            known_at_ms,
        )

    def encode(self, label: object) -> int:
        if type(label) is not str:
            raise ValueError("label")
        try:
            return self.labels.index(label)
        except ValueError as exc:
            raise ValueError("unknown_label") from exc

    def projection(self) -> dict[str, object]:
        return {
            "authority_id": self.authority_id,
            "known_at_ms": self.known_at_ms,
            "labels": list(self.labels),
            "registry_id": self.registry_id,
            "training_partition_id": self.training_partition_id,
        }

    @property
    def sha256(self) -> str:
        return sha256_canonical(self.projection())


def _readonly_array(value: object, *, ndim: int, name: str, dtype: np.dtype[Any]) -> np.ndarray:
    array = np.asarray(value, dtype=dtype)
    if array.ndim != ndim or not np.isfinite(array).all():
        raise ValueError(name)
    result = np.array(array, dtype=dtype, copy=True, order="C")
    result.setflags(write=False)
    return result


def _readonly_int64_array(value: object, *, ndim: int, name: str) -> np.ndarray:
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


@dataclass(frozen=True)
class SymbolDailySeries:
    market_id: str
    symbol: str
    session_ids: tuple[str, ...]
    session_times_ms: tuple[int, ...]
    feature_known_at_ms: tuple[int, ...]
    ohlcv: np.ndarray
    sector_label: str
    sector_known_at_ms: int

    def __post_init__(self) -> None:
        validate_symbol(self.symbol)
        if type(self.market_id) is not str or not self.market_id or self.market_id != self.market_id.strip():
            raise ValueError("market_id")
        if type(self.session_ids) is not tuple or type(self.session_times_ms) is not tuple or type(self.feature_known_at_ms) is not tuple:
            raise ValueError("clock_type")
        if len(self.session_times_ms) < 2 or len(self.feature_known_at_ms) != len(self.session_times_ms) or len(self.session_ids) != len(self.session_times_ms):
            raise ValueError("clock_length")
        previous = -1
        for session_id, session, known in zip(self.session_ids, self.session_times_ms, self.feature_known_at_ms, strict=True):
            if type(session_id) is not str or not session_id or session_id != session_id.strip():
                raise ValueError("session_id")
            _exact_int(session, "session_time_ms")
            _exact_int(known, "known_at_ms")
            if session <= previous or known > session:
                raise ValueError("clock_order")
            previous = session
        if len(set(self.session_ids)) != len(self.session_ids):
            raise ValueError("session_id_unique")
        if type(self.sector_label) is not str or not self.sector_label:
            raise ValueError("label_type")
        _exact_int(self.sector_known_at_ms, "sector_known_at_ms")
        array = _readonly_array(self.ohlcv, ndim=2, name="ohlcv", dtype=np.dtype("float64"))
        if array.shape != (len(self.session_times_ms), 5):
            raise ValueError("ohlcv_shape")
        open_, high, low, close, volume = (array[:, index] for index in range(5))
        if np.any(open_ <= 0) or np.any(high <= 0) or np.any(low <= 0) or np.any(close <= 0) or np.any(volume < 0):
            raise ValueError("ohlcv_domain")
        if np.any(high < np.maximum.reduce([open_, low, close])) or np.any(low > np.minimum.reduce([open_, high, close])):
            raise ValueError("ohlc_consistency")
        object.__setattr__(self, "ohlcv", array)


@dataclass(frozen=True)
class CrossSectionClock:
    market_id: str
    calendar_session_ids: tuple[str, ...]
    calendar_times_ms: tuple[int, ...]
    formation_session_id: str
    next_session_id: str
    formation_time_ms: int
    next_session_time_ms: int
    calendar_sha256: str

    def __post_init__(self) -> None:
        for name in ("market_id", "formation_session_id", "next_session_id"):
            value = getattr(self, name)
            if type(value) is not str or not value or value != value.strip():
                raise ValueError(name)
        if type(self.calendar_session_ids) is not tuple or type(self.calendar_times_ms) is not tuple or len(self.calendar_session_ids) != len(self.calendar_times_ms) or len(self.calendar_times_ms) < 2:
            raise ValueError("calendar")
        previous = -1
        for session_id, timestamp in zip(self.calendar_session_ids, self.calendar_times_ms, strict=True):
            if type(session_id) is not str or not session_id or session_id != session_id.strip():
                raise ValueError("calendar_session_id")
            _exact_int(timestamp, "calendar_time")
            if timestamp <= previous:
                raise ValueError("calendar_order")
            previous = timestamp
        if len(set(self.calendar_session_ids)) != len(self.calendar_session_ids):
            raise ValueError("calendar_unique")
        _exact_int(self.formation_time_ms, "formation_time_ms")
        _exact_int(self.next_session_time_ms, "next_session_time_ms")
        if self.next_session_time_ms <= self.formation_time_ms or self.next_session_id == self.formation_session_id:
            raise ValueError("next_session")
        validate_hex64(self.calendar_sha256, "calendar_sha256")
        calendar_projection = [
            {"session_id": identifier, "time_ms": timestamp}
            for identifier, timestamp in zip(self.calendar_session_ids, self.calendar_times_ms, strict=True)
        ]
        if self.calendar_sha256 != sha256_canonical(calendar_projection):
            raise ValueError("calendar_sha256")
        try:
            index = self.calendar_session_ids.index(self.formation_session_id)
        except ValueError as exc:
            raise ValueError("formation_session") from exc
        if index + 1 >= len(self.calendar_session_ids) or self.calendar_session_ids[index + 1] != self.next_session_id or self.calendar_times_ms[index] != self.formation_time_ms or self.calendar_times_ms[index + 1] != self.next_session_time_ms:
            raise ValueError("calendar_adjacency")


@dataclass(frozen=True)
class CrossSectionInferenceBatch:
    clock: CrossSectionClock
    symbols: tuple[str, ...]
    feature_session_ids: tuple[tuple[str, ...], ...]
    feature_times_ms: np.ndarray
    feature_known_at_ms: np.ndarray
    features: np.ndarray
    feature_schema_sha256: str
    scaler_sha256: str
    scc_registry_sha256: str
    ssc_registry_sha256: str

    def __post_init__(self) -> None:
        if type(self.clock) is not CrossSectionClock:
            raise ValueError("clock")
        if type(self.symbols) is not tuple or len(self.symbols) < 2:
            raise ValueError("symbols")
        for symbol in self.symbols:
            validate_symbol(symbol)
        if len(set(self.symbols)) != len(self.symbols) or self.symbols != tuple(sorted(self.symbols, key=lambda x: x.encode("utf-8"))):
            raise ValueError("symbol_order")
        features = _readonly_array(self.features, ndim=3, name="features", dtype=np.dtype("float64"))
        times = _readonly_int64_array(self.feature_times_ms, ndim=2, name="feature_times")
        known = _readonly_int64_array(self.feature_known_at_ms, ndim=2, name="feature_known")
        count, lookback, width = features.shape
        if count != len(self.symbols) or width != 25 or times.shape != (count, lookback) or known.shape != (count, lookback):
            raise ValueError("batch_shape")
        if type(self.feature_session_ids) is not tuple or len(self.feature_session_ids) != count:
            raise ValueError("feature_session_ids")
        canonical_ids = self.feature_session_ids[0]
        if type(canonical_ids) is not tuple or len(canonical_ids) != lookback:
            raise ValueError("feature_session_ids")
        for row, ids in enumerate(self.feature_session_ids):
            if ids != canonical_ids or len(set(ids)) != len(ids):
                raise ValueError("cross_section_session_alignment")
            if not np.array_equal(times[row], times[0]):
                raise ValueError("cross_section_time_alignment")
        formation_index = self.clock.calendar_session_ids.index(self.clock.formation_session_id)
        expected_ids = self.clock.calendar_session_ids[formation_index - lookback + 1 : formation_index + 1]
        expected_times = np.asarray(self.clock.calendar_times_ms[formation_index - lookback + 1 : formation_index + 1], dtype=np.int64)
        if formation_index < lookback - 1 or canonical_ids != expected_ids or not np.array_equal(times[0], expected_times):
            raise ValueError("feature_calendar_window")
        if int(times[0, -1]) != self.clock.formation_time_ms or np.any(known > times) or np.any(known > self.clock.formation_time_ms):
            raise ValueError("feature_clock")
        validate_hex64(self.feature_schema_sha256, "feature_schema_sha256")
        validate_hex64(self.scaler_sha256, "scaler_sha256")
        validate_hex64(self.scc_registry_sha256, "scc_registry_sha256")
        validate_hex64(self.ssc_registry_sha256, "ssc_registry_sha256")
        object.__setattr__(self, "features", features)
        object.__setattr__(self, "feature_times_ms", times)
        object.__setattr__(self, "feature_known_at_ms", known)


@dataclass(frozen=True)
class CrossSectionTrainingBatch(CrossSectionInferenceBatch):
    raw_close_window: np.ndarray
    close_t_raw: np.ndarray
    close_next_raw: np.ndarray
    next_session_returns: np.ndarray
    scc_targets: np.ndarray
    ssc_targets: np.ndarray
    sector_label_known_at_ms: tuple[int, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        raw_close = _readonly_array(self.raw_close_window, ndim=2, name="raw_close_window", dtype=np.dtype("float64"))
        close_t = _readonly_array(self.close_t_raw, ndim=1, name="close_t_raw", dtype=np.dtype("float64"))
        close_next = _readonly_array(self.close_next_raw, ndim=1, name="close_next_raw", dtype=np.dtype("float64"))
        returns = _readonly_array(self.next_session_returns, ndim=1, name="returns", dtype=np.dtype("float64"))
        scc = _readonly_int64_array(self.scc_targets, ndim=1, name="scc_targets")
        ssc = _readonly_int64_array(self.ssc_targets, ndim=1, name="ssc_targets")
        count = len(self.symbols)
        if raw_close.shape != (count, self.features.shape[1]) or close_t.shape != (count,) or close_next.shape != (count,) or returns.shape != (count,) or scc.shape != (count,) or ssc.shape != (count,):
            raise ValueError("batch_shape")
        if np.any(raw_close <= 0) or np.any(close_t <= 0) or np.any(close_next <= 0) or not np.array_equal(raw_close[:, -1], close_t):
            raise ValueError("close_domain")
        expected_returns = close_next / close_t - 1.0
        if not np.array_equal(returns, expected_returns):
            raise ValueError("return_identity")
        if type(self.sector_label_known_at_ms) is not tuple or len(self.sector_label_known_at_ms) != count:
            raise ValueError("sector_label_known_at")
        for known in self.sector_label_known_at_ms:
            _exact_int(known, "sector_label_known_at")
            if known > self.clock.formation_time_ms:
                raise ValueError("future_sector_label")
        object.__setattr__(self, "raw_close_window", raw_close)
        object.__setattr__(self, "close_t_raw", close_t)
        object.__setattr__(self, "close_next_raw", close_next)
        object.__setattr__(self, "next_session_returns", returns)
        object.__setattr__(self, "scc_targets", scc)
        object.__setattr__(self, "ssc_targets", ssc)

    @property
    def size(self) -> int:
        return len(self.symbols)

    def as_inference_batch(self) -> CrossSectionInferenceBatch:
        return CrossSectionInferenceBatch(
            clock=self.clock,
            symbols=self.symbols,
            feature_session_ids=self.feature_session_ids,
            feature_times_ms=self.feature_times_ms,
            feature_known_at_ms=self.feature_known_at_ms,
            features=self.features,
            feature_schema_sha256=self.feature_schema_sha256,
            scaler_sha256=self.scaler_sha256,
            scc_registry_sha256=self.scc_registry_sha256,
            ssc_registry_sha256=self.ssc_registry_sha256,
        )
