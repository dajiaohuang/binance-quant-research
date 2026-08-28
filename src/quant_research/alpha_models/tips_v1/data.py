from __future__ import annotations

from dataclasses import dataclass
import hashlib

import numpy as np

from .contracts import (
    FEATURE_SCHEMA_SHA256,
    TIPSInferenceBatch,
    TIPSTrainingBatch,
    exact_int,
    readonly_float_array,
    readonly_int_array,
    validate_symbol,
    feature_row_binding_sha256,
    label_row_binding_sha256,
)


def _calendar_id(session_ids: tuple[str, ...], times_ms: np.ndarray) -> str:
    digest = hashlib.sha256()
    for session_id, timestamp in zip(session_ids, times_ms, strict=True):
        digest.update(session_id.encode("utf-8") + b"\0" + str(int(timestamp)).encode("ascii") + b"\n")
    return digest.hexdigest()


@dataclass(frozen=True)
class MarketCalendar:
    market_id: str
    session_ids: tuple[str, ...]
    close_times_ms: np.ndarray
    calendar_id: str = ""

    def __post_init__(self) -> None:
        if type(self.market_id) is not str or not self.market_id or self.market_id != self.market_id.strip():
            raise ValueError("market_id")
        if type(self.session_ids) is not tuple or len(self.session_ids) < 43 or len(set(self.session_ids)) != len(self.session_ids):
            raise ValueError("session_ids")
        if any(type(item) is not str or not item for item in self.session_ids):
            raise ValueError("session_id")
        times = readonly_int_array(self.close_times_ms, ndim=1, name="close_times_ms")
        if times.shape != (len(self.session_ids),) or np.any(np.diff(times) <= 0):
            raise ValueError("calendar_order")
        identity = _calendar_id(self.session_ids, times)
        if self.calendar_id not in ("", identity):
            raise ValueError("calendar_id")
        object.__setattr__(self, "close_times_ms", times)
        object.__setattr__(self, "calendar_id", identity)

    def index(self, session_id: str) -> int:
        try:
            return self.session_ids.index(session_id)
        except ValueError as exc:
            raise ValueError("unknown_session") from exc

    def assert_label_within_partition(
        self,
        formation_session_id: str,
        label_session_id: str,
        partition_session_ids: tuple[str, ...],
    ) -> None:
        formation = self.index(formation_session_id)
        label = self.index(label_session_id)
        if label - formation != 4:
            raise ValueError("q5_session_identity")
        expected = tuple(self.session_ids[formation : label + 1])
        present = set(partition_session_ids)
        if any(item not in present for item in expected):
            raise ValueError("purged_label_boundary")

    def validate_training_batch(self, batch: TIPSTrainingBatch) -> None:
        if type(batch) is not TIPSTrainingBatch or batch.calendar_id != self.calendar_id or batch.market_id != self.market_id:
            raise ValueError("calendar_binding")
        formation = self.index(batch.formation_session_id)
        if formation + 4 >= len(self.session_ids):
            raise ValueError("q5_session_identity")
        expected_sessions = self.session_ids[formation : formation + 5]
        expected_times = tuple(int(item) for item in self.close_times_ms[formation : formation + 5])
        if batch.label_path_session_ids != expected_sessions or batch.label_path_times_ms != expected_times:
            raise ValueError("q5_calendar_path")
        if batch.label_session_id != expected_sessions[-1] or batch.label_time_ms != expected_times[-1]:
            raise ValueError("label_calendar")
        if batch.label_known_at_ms > batch.label_time_ms:
            raise ValueError("label_known_at")
        present = set(batch.partition_session_ids)
        if any(session not in present for session in expected_sessions):
            raise ValueError("purged_label_boundary")


@dataclass(frozen=True)
class SymbolOHLCV:
    symbol: str
    calendar_id: str
    values: np.ndarray
    known_at_ms: np.ndarray

    def __post_init__(self) -> None:
        validate_symbol(self.symbol)
        if type(self.calendar_id) is not str or len(self.calendar_id) != 64:
            raise ValueError("calendar_id")
        values = readonly_float_array(self.values, ndim=2, name="ohlcv")
        known = readonly_int_array(self.known_at_ms, ndim=1, name="known_at")
        if values.shape[1] != 5 or known.shape != (values.shape[0],):
            raise ValueError("ohlcv_shape")
        o, h, low, c, volume = values.T
        if np.any(o <= 0) or np.any(h <= 0) or np.any(low <= 0) or np.any(c <= 0) or np.any(volume < 0):
            raise ValueError("ohlcv_domain")
        if np.any(h < np.maximum.reduce([o, c, low])) or np.any(low > np.minimum.reduce([o, c, h])):
            raise ValueError("ohlc_identity")
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "known_at_ms", known)


def _zscore(window: np.ndarray) -> float:
    std = float(np.std(window, ddof=0))
    return 0.0 if std == 0.0 else float((window[-1] - float(np.mean(window))) / std)


def feature_window(series: SymbolOHLCV, formation_index: int) -> np.ndarray:
    exact_int(formation_index, "formation_index")
    if formation_index < 38 or formation_index >= series.values.shape[0]:
        raise ValueError("complete_warmup_required")
    rows: list[list[float]] = []
    close = series.values[:, 3]
    for endpoint in range(formation_index - 19, formation_index + 1):
        z = [_zscore(series.values[endpoint - 19 : endpoint + 1, column]) for column in range(5)]
        ratios = [float(np.mean(close[endpoint - length + 1 : endpoint + 1]) / close[endpoint] - 1.0) for length in (5, 10, 20)]
        rows.append(z + ratios)
    result = np.asarray(rows, dtype=np.float64)
    if result.shape != (20, 8) or not np.isfinite(result).all():
        raise ValueError("features")
    return result


def _join(
    calendar: MarketCalendar,
    series: tuple[SymbolOHLCV, ...],
    formation_session_id: str,
) -> tuple[int, tuple[str, ...], np.ndarray, np.ndarray]:
    formation_index = calendar.index(formation_session_id)
    if type(series) is not tuple or len(series) < 2:
        raise ValueError("cross_section")
    ordered = tuple(sorted(series, key=lambda item: item.symbol.encode("utf-8")))
    symbols = tuple(item.symbol for item in ordered)
    if len(set(symbols)) != len(symbols):
        raise ValueError("duplicate_symbol")
    for item in ordered:
        if item.calendar_id != calendar.calendar_id or item.values.shape[0] != len(calendar.session_ids):
            raise ValueError("calendar_join")
        if np.any(item.known_at_ms > calendar.close_times_ms):
            raise ValueError("known_at")
    features = np.stack([feature_window(item, formation_index) for item in ordered])
    known = np.stack([item.known_at_ms[formation_index - 19 : formation_index + 1] for item in ordered])
    return formation_index, symbols, features, known


def build_inference_batch(
    calendar: MarketCalendar,
    series: tuple[SymbolOHLCV, ...],
    formation_session_id: str,
    *,
    partition_id: str,
) -> TIPSInferenceBatch:
    formation, symbols, features, known = _join(calendar, series, formation_session_id)
    return TIPSInferenceBatch(
        market_id=calendar.market_id,
        formation_session_id=formation_session_id,
        formation_time_ms=int(calendar.close_times_ms[formation]),
        symbols=symbols,
        feature_row_symbols=symbols,
        feature_row_binding_sha256s=tuple(feature_row_binding_sha256(symbol, features[index], known[index]) for index, symbol in enumerate(symbols)),
        feature_session_ids=calendar.session_ids[formation - 19 : formation + 1],
        feature_times_ms=tuple(int(item) for item in calendar.close_times_ms[formation - 19 : formation + 1]),
        feature_known_at_ms=known,
        features=features,
        partition_id=partition_id,
        calendar_id=calendar.calendar_id,
        feature_schema_sha256=FEATURE_SCHEMA_SHA256,
    )


def build_training_batch(
    calendar: MarketCalendar,
    series: tuple[SymbolOHLCV, ...],
    formation_session_id: str,
    *,
    partition_id: str,
    partition_session_ids: tuple[str, ...],
) -> TIPSTrainingBatch:
    formation, symbols, features, known = _join(calendar, series, formation_session_id)
    if formation + 4 >= len(calendar.session_ids):
        raise ValueError("label_unavailable")
    label_session = calendar.session_ids[formation + 4]
    calendar.assert_label_within_partition(formation_session_id, label_session, partition_session_ids)
    by_symbol = {item.symbol: item for item in series}
    close_t = np.asarray([by_symbol[symbol].values[formation, 3] for symbol in symbols])
    close_future = np.asarray([by_symbol[symbol].values[formation + 4, 3] for symbol in symbols])
    return TIPSTrainingBatch(
        market_id=calendar.market_id,
        formation_session_id=formation_session_id,
        formation_time_ms=int(calendar.close_times_ms[formation]),
        symbols=symbols,
        feature_row_symbols=symbols,
        feature_row_binding_sha256s=tuple(feature_row_binding_sha256(symbol, features[index], known[index]) for index, symbol in enumerate(symbols)),
        feature_session_ids=calendar.session_ids[formation - 19 : formation + 1],
        feature_times_ms=tuple(int(item) for item in calendar.close_times_ms[formation - 19 : formation + 1]),
        feature_known_at_ms=known,
        features=features,
        partition_id=partition_id,
        calendar_id=calendar.calendar_id,
        feature_schema_sha256=FEATURE_SCHEMA_SHA256,
        partition_kind="TRAIN",
        label_session_id=label_session,
        label_time_ms=int(calendar.close_times_ms[formation + 4]),
        label_known_at_ms=int(calendar.close_times_ms[formation + 4]),
        close_t_raw=close_t,
        close_t_plus_4_raw=close_future,
        labels=close_future / close_t - 1.0,
        label_q=5,
        label_path_session_ids=calendar.session_ids[formation : formation + 5],
        partition_session_ids=partition_session_ids,
        label_row_symbols=symbols,
        label_row_binding_sha256s=tuple(label_row_binding_sha256(symbol, close_t[index], close_future[index], (close_future / close_t - 1.0)[index]) for index, symbol in enumerate(symbols)),
        label_path_times_ms=tuple(int(item) for item in calendar.close_times_ms[formation : formation + 5]),
    )
