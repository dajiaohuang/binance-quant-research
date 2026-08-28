from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable

import numpy as np

from .contracts import (
    FEATURE_SCHEMA_SHA256,
    CrossSectionClock,
    CrossSectionTrainingBatch,
    StableLabelRegistry,
    SymbolDailySeries,
    _exact_int,
    canonical_json_bytes,
    sha256_canonical,
    validate_hex64,
)


WINDOWS = (5, 10, 20, 30)


class TrainOnlyMinMax:
    """Fit-once scaler whose immutable state is bound into checkpoints."""

    def __init__(self) -> None:
        self._fitted = False

    def fit(self, partition: TrainingFeaturePartition) -> TrainOnlyMinMax:
        if self._fitted:
            raise ValueError("scaler_fit_once")
        if type(partition) is not TrainingFeaturePartition or partition.kind != "TRAIN":
            raise ValueError("training_partition")
        values = partition.features
        flat = values.reshape(-1, 25)
        self._minimum = np.min(flat, axis=0)
        self._maximum = np.max(flat, axis=0)
        self._minimum.setflags(write=False)
        self._maximum.setflags(write=False)
        self._training_end_time_ms = partition.train_end_exclusive_ms
        self._training_partition_sha256 = partition.partition_sha256
        self._fitted = True
        return self

    @property
    def fitted(self) -> bool:
        return self._fitted

    @property
    def training_end_time_ms(self) -> int:
        if not self._fitted:
            raise ValueError("scaler_unfitted")
        return self._training_end_time_ms

    def transform(self, features: object) -> np.ndarray:
        if not self._fitted:
            raise ValueError("scaler_unfitted")
        values = np.asarray(features, dtype=np.float64)
        if values.ndim < 2 or values.shape[-1] != 25 or not np.isfinite(values).all():
            raise ValueError("features")
        denominator = self._maximum - self._minimum
        transformed = np.zeros_like(values, dtype=np.float64)
        np.divide(values - self._minimum, denominator, out=transformed, where=denominator > 0)
        result = np.array(transformed, dtype=np.float64, copy=True, order="C")
        result.setflags(write=False)
        return result

    def state(self) -> dict[str, object]:
        if not self._fitted:
            raise ValueError("scaler_unfitted")
        return {
            "constant_column_policy": "MAP_TO_ZERO",
            "feature_schema_sha256": FEATURE_SCHEMA_SHA256,
            "maximum": self._maximum.tolist(),
            "minimum": self._minimum.tolist(),
            "training_partition_sha256": self._training_partition_sha256,
            "training_end_time_ms": self._training_end_time_ms,
        }

    @classmethod
    def from_state(cls, state: object) -> TrainOnlyMinMax:
        if type(state) is not dict or set(state) != {
            "constant_column_policy",
            "feature_schema_sha256",
            "maximum",
            "minimum",
            "training_partition_sha256",
            "training_end_time_ms",
        }:
            raise ValueError("scaler_state")
        if state["constant_column_policy"] != "MAP_TO_ZERO" or state["feature_schema_sha256"] != FEATURE_SCHEMA_SHA256:
            raise ValueError("scaler_contract")
        minimum = np.asarray(state["minimum"], dtype=np.float64)
        maximum = np.asarray(state["maximum"], dtype=np.float64)
        if minimum.shape != (25,) or maximum.shape != (25,) or not np.isfinite(minimum).all() or not np.isfinite(maximum).all() or np.any(maximum < minimum):
            raise ValueError("scaler_values")
        instance = cls()
        instance._minimum = np.array(minimum, copy=True)
        instance._maximum = np.array(maximum, copy=True)
        instance._minimum.setflags(write=False)
        instance._maximum.setflags(write=False)
        instance._training_end_time_ms = _exact_int(state["training_end_time_ms"], "training_end_time_ms")
        if type(state["training_partition_sha256"]) is not str or len(state["training_partition_sha256"]) != 64:
            raise ValueError("training_partition_sha256")
        instance._training_partition_sha256 = state["training_partition_sha256"]
        instance._fitted = True
        return instance

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.state())).hexdigest()


@dataclass(frozen=True)
class TrainingFeaturePartition:
    kind: str
    features: np.ndarray
    formation_times_ms: tuple[int, ...]
    label_end_times_ms: tuple[int, ...]
    train_end_exclusive_ms: int
    data_provenance_sha256: str
    feature_schema_sha256: str = FEATURE_SCHEMA_SHA256

    def __post_init__(self) -> None:
        if self.kind != "TRAIN" or type(self.formation_times_ms) is not tuple or type(self.label_end_times_ms) is not tuple:
            raise ValueError("partition_kind")
        _exact_int(self.train_end_exclusive_ms, "train_end_exclusive_ms")
        validate_hex64(self.data_provenance_sha256, "data_provenance_sha256")
        if self.feature_schema_sha256 != FEATURE_SCHEMA_SHA256:
            raise ValueError("partition_provenance")
        values = np.asarray(self.features, dtype=np.float64)
        if values.ndim != 4 or values.shape[-1] != 25 or values.shape[0] != len(self.formation_times_ms) or values.shape[0] != len(self.label_end_times_ms) or values.size == 0 or not np.isfinite(values).all():
            raise ValueError("partition_features")
        previous = -1
        for formation, label_end in zip(self.formation_times_ms, self.label_end_times_ms, strict=True):
            _exact_int(formation, "formation_time")
            _exact_int(label_end, "label_end_time")
            if formation <= previous or label_end <= formation or label_end >= self.train_end_exclusive_ms:
                raise ValueError("partition_purge")
            previous = formation
        frozen = np.array(values, copy=True, order="C")
        frozen.setflags(write=False)
        object.__setattr__(self, "features", frozen)

    @property
    def partition_sha256(self) -> str:
        projection = {
            "data_provenance_sha256": self.data_provenance_sha256,
            "feature_bytes_sha256": hashlib.sha256(self.features.tobytes(order="C")).hexdigest(),
            "feature_schema_sha256": self.feature_schema_sha256,
            "formation_times_ms": list(self.formation_times_ms),
            "kind": self.kind,
            "label_end_times_ms": list(self.label_end_times_ms),
            "train_end_exclusive_ms": self.train_end_exclusive_ms,
        }
        return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


def build_feature_matrix(ohlcv: object) -> np.ndarray:
    """Return fully warmed features only; row zero corresponds to raw session 29."""
    values = np.asarray(ohlcv, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 5 or len(values) < 30 or not np.isfinite(values).all():
        raise ValueError("ohlcv")
    rows: list[list[float]] = []
    for end in range(29, len(values)):
        row: list[float] = []
        for column in range(5):
            for width in WINDOWS:
                row.append(float(np.mean(values[end - width + 1 : end + 1, column])))
            row.append(float(values[end, column]))
        rows.append(row)
    result = np.asarray(rows, dtype=np.float64)
    if result.shape != (len(values) - 29, 25) or not np.isfinite(result).all():
        raise ValueError("feature_result")
    result.setflags(write=False)
    return result


def build_cross_section_batch(
    series: Iterable[SymbolDailySeries],
    *,
    expected_calendar: tuple[tuple[str, int], ...],
    market_id: str,
    formation_time_ms: int,
    lookback: int,
    scaler: TrainOnlyMinMax,
    scc_registry: StableLabelRegistry,
    ssc_registry: StableLabelRegistry,
) -> CrossSectionTrainingBatch:
    if type(expected_calendar) is not tuple or len(expected_calendar) < 2 or type(market_id) is not str or not market_id:
        raise ValueError("expected_calendar")
    expected_session_ids: list[str] = []
    expected_sessions_ms: list[int] = []
    previous = -1
    for item in expected_calendar:
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str or not item[0]:
            raise ValueError("expected_session")
        session_id, session = item
        _exact_int(session, "expected_session")
        if session <= previous or session_id in expected_session_ids:
            raise ValueError("expected_session_order")
        previous = session
        expected_session_ids.append(session_id)
        expected_sessions_ms.append(session)
    expected_session_ids_tuple = tuple(expected_session_ids)
    expected_sessions_ms_tuple = tuple(expected_sessions_ms)
    _exact_int(formation_time_ms, "formation_time_ms")
    if type(lookback) is not int or lookback not in (16, 32):
        raise ValueError("lookback")
    try:
        formation_index = expected_sessions_ms_tuple.index(formation_time_ms)
    except ValueError as exc:
        raise ValueError("formation_session") from exc
    if formation_index < 29 + lookback - 1:
        raise ValueError("partial_warmup")
    if formation_index + 1 >= len(expected_sessions_ms_tuple):
        raise ValueError("next_session_missing")
    ordered = tuple(sorted(tuple(series), key=lambda item: item.symbol.encode("utf-8")))
    if not ordered or len({item.symbol for item in ordered}) != len(ordered):
        raise ValueError("series_scope")
    symbols = tuple(item.symbol for item in ordered)
    if len(symbols) < 2:
        raise ValueError("cross_section_breadth")
    if scc_registry.labels != symbols:
        raise ValueError("scc_symbol_registry")
    if scc_registry.known_at_ms > formation_time_ms or ssc_registry.known_at_ms > formation_time_ms:
        raise ValueError("future_registry")
    feature_windows: list[np.ndarray] = []
    feature_session_ids: list[tuple[str, ...]] = []
    feature_times: list[np.ndarray] = []
    feature_known: list[np.ndarray] = []
    raw_close_windows: list[np.ndarray] = []
    close_t_raw: list[float] = []
    close_next_raw: list[float] = []
    returns: list[float] = []
    scc_targets: list[int] = []
    ssc_targets: list[int] = []
    sector_known: list[int] = []
    raw_start = formation_index - lookback + 1
    for item in ordered:
        if item.market_id != market_id or item.session_ids != expected_session_ids_tuple or item.session_times_ms != expected_sessions_ms_tuple:
            raise ValueError("missing_or_extra_session")
        if item.feature_known_at_ms[formation_index] > formation_time_ms:
            raise ValueError("future_feature")
        matrix = build_feature_matrix(item.ohlcv)
        warmed_end = formation_index - 29
        start = warmed_end - lookback + 1
        raw_window = matrix[start : warmed_end + 1]
        if raw_window.shape != (lookback, 25):
            raise ValueError("partial_window")
        feature_windows.append(scaler.transform(raw_window))
        current_close = float(item.ohlcv[formation_index, 3])
        next_close = float(item.ohlcv[formation_index + 1, 3])
        feature_session_ids.append(expected_session_ids_tuple[raw_start : formation_index + 1])
        feature_times.append(np.asarray(expected_sessions_ms_tuple[raw_start : formation_index + 1], dtype=np.int64))
        feature_known.append(np.asarray(item.feature_known_at_ms[raw_start : formation_index + 1], dtype=np.int64))
        raw_close_windows.append(np.asarray(item.ohlcv[raw_start : formation_index + 1, 3], dtype=np.float64))
        close_t_raw.append(current_close)
        close_next_raw.append(next_close)
        returns.append(next_close / current_close - 1.0)
        scc_targets.append(scc_registry.encode(item.symbol))
        ssc_targets.append(ssc_registry.encode(item.sector_label))
        sector_known.append(item.sector_known_at_ms)
    clock = CrossSectionClock(
        market_id=market_id,
        calendar_session_ids=expected_session_ids_tuple,
        calendar_times_ms=expected_sessions_ms_tuple,
        formation_session_id=expected_session_ids_tuple[formation_index],
        next_session_id=expected_session_ids_tuple[formation_index + 1],
        formation_time_ms=formation_time_ms,
        next_session_time_ms=expected_sessions_ms_tuple[formation_index + 1],
        calendar_sha256=sha256_canonical(
            [{"session_id": identifier, "time_ms": timestamp} for identifier, timestamp in expected_calendar]
        ),
    )
    close_t_array = np.asarray(close_t_raw, dtype=np.float64)
    close_next_array = np.asarray(close_next_raw, dtype=np.float64)
    return CrossSectionTrainingBatch(
        clock=clock,
        symbols=symbols,
        feature_session_ids=tuple(feature_session_ids),
        feature_times_ms=np.stack(feature_times),
        feature_known_at_ms=np.stack(feature_known),
        features=np.stack(feature_windows),
        feature_schema_sha256=FEATURE_SCHEMA_SHA256,
        scaler_sha256=scaler.sha256,
        scc_registry_sha256=scc_registry.sha256,
        ssc_registry_sha256=ssc_registry.sha256,
        raw_close_window=np.stack(raw_close_windows),
        close_t_raw=close_t_array,
        close_next_raw=close_next_array,
        next_session_returns=close_next_array / close_t_array - 1.0,
        scc_targets=np.asarray(scc_targets, dtype=np.int64),
        ssc_targets=np.asarray(ssc_targets, dtype=np.int64),
        sector_label_known_at_ms=tuple(sector_known),
    )


@dataclass(frozen=True)
class PurgedSplit:
    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    purged_indices: tuple[int, ...]


def purged_time_split(
    formation_times_ms: tuple[int, ...],
    label_end_times_ms: tuple[int, ...],
    *,
    train_end_exclusive_ms: int,
    validation_end_exclusive_ms: int,
) -> PurgedSplit:
    if type(formation_times_ms) is not tuple or type(label_end_times_ms) is not tuple or len(formation_times_ms) != len(label_end_times_ms):
        raise ValueError("split_clock")
    _exact_int(train_end_exclusive_ms, "train_end")
    _exact_int(validation_end_exclusive_ms, "validation_end")
    if validation_end_exclusive_ms <= train_end_exclusive_ms:
        raise ValueError("split_boundary")
    train: list[int] = []
    validation: list[int] = []
    test: list[int] = []
    purged: list[int] = []
    previous = -1
    for index, (formation, label_end) in enumerate(zip(formation_times_ms, label_end_times_ms, strict=True)):
        _exact_int(formation, "formation")
        _exact_int(label_end, "label_end")
        if formation <= previous or label_end <= formation:
            raise ValueError("split_order")
        previous = formation
        if formation < train_end_exclusive_ms:
            (train if label_end < train_end_exclusive_ms else purged).append(index)
        elif formation < validation_end_exclusive_ms:
            (validation if label_end < validation_end_exclusive_ms else purged).append(index)
        else:
            test.append(index)
    return PurgedSplit(tuple(train), tuple(validation), tuple(test), tuple(purged))
