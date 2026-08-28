from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from quant_research.alpha_models.sspt_v2.contracts import (
    CrossSectionTrainingBatch,
    StableLabelRegistry,
    SymbolDailySeries,
)
from quant_research.alpha_models.sspt_v2.data import TrainOnlyMinMax, build_cross_section_batch
from quant_research.alpha_models.tips_v1.contracts import TIPSInferenceBatch, TIPSTrainingBatch
from quant_research.alpha_models.tips_v1.data import (
    MarketCalendar,
    SymbolOHLCV,
    build_inference_batch,
    build_training_batch,
)

from .contracts import CalendarDay, ContractError, DailyBar, MasterRow, canonical_equity_code, jst_known_at_ms, strict_date
from .loader import causal_adjust_bars, master_at_formation, official_sessions


MARKET_ID = "JPX_JQUANTS_V2"


@dataclass(frozen=True)
class SSPTAdapterResult:
    batch: CrossSectionTrainingBatch
    formation_date: str
    master_row_sha256s: tuple[str, ...]
    earliest_execution_session_id: str
    same_close_execution_allowed: bool = False


@dataclass(frozen=True)
class TIPSInferenceAdapterResult:
    batch: TIPSInferenceBatch
    formation_date: str
    master_row_sha256s: tuple[str, ...]
    earliest_execution_session_id: str
    same_close_execution_allowed: bool = False


@dataclass(frozen=True)
class TIPSTrainingAdapterResult:
    batch: TIPSTrainingBatch
    formation_date: str
    master_row_sha256s: tuple[str, ...]
    earliest_execution_session_id: str
    same_close_execution_allowed: bool = False


def _calendar_projection(session_dates: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    if type(session_dates) is not tuple or len(session_dates) < 2 or len(set(session_dates)) != len(session_dates):
        raise ContractError("SESSION_DATES")
    if session_dates != tuple(sorted(session_dates)):
        raise ContractError("SESSION_ORDER")
    return tuple((strict_date(day), jst_known_at_ms(day, 16, 30)) for day in session_dates)


def _session_dates(calendar_days: Iterable[CalendarDay]) -> tuple[str, ...]:
    sessions = official_sessions(calendar_days)
    dates = tuple(row.session_date for row in sessions)
    if len(dates) < 2:
        raise ContractError("OFFICIAL_CALENDAR_SCOPE")
    return dates


def _master_scope(
    master_rows: Iterable[MasterRow],
    *,
    symbols: tuple[str, ...],
    formation_date: str,
    formation_time_ms: int,
) -> tuple[MasterRow, ...]:
    if type(symbols) is not tuple or len(symbols) < 2:
        raise ContractError("SYMBOL_SCOPE")
    canonical = tuple(canonical_equity_code(symbol) for symbol in symbols)
    if canonical != tuple(sorted(canonical, key=lambda value: value.encode("utf-8"))) or len(set(canonical)) != len(canonical):
        raise ContractError("SYMBOL_SCOPE")
    rows = tuple(
        master_at_formation(master_rows, symbol=symbol, formation_date=formation_date, formation_time_ms=formation_time_ms)
        for symbol in canonical
    )
    return rows


def _series_arrays(
    bars: Iterable[DailyBar],
    *,
    symbols: tuple[str, ...],
    session_dates: tuple[str, ...],
    formation_date: str,
    label_steps: int,
) -> dict[str, np.ndarray]:
    if type(label_steps) is not int or label_steps < 1:
        raise ContractError("LABEL_STEPS")
    calendar = _calendar_projection(session_dates)
    try:
        formation_index = session_dates.index(formation_date)
    except ValueError as exc:
        raise ContractError("FORMATION_SESSION") from exc
    if formation_index + label_steps >= len(session_dates):
        raise ContractError("LABEL_PATH_MISSING")
    formation_time = calendar[formation_index][1]
    by_key: dict[tuple[str, str], DailyBar] = {}
    all_bars = tuple(bars)
    for row in all_bars:
        key = (row.symbol, row.session_date)
        if key in by_key:
            raise ContractError("DUPLICATE_BAR")
        by_key[key] = row
    adjusted = {(row.symbol, row.session_date): row for row in causal_adjust_bars(all_bars, formation_time_ms=formation_time)}
    arrays: dict[str, np.ndarray] = {}
    for symbol in symbols:
        rows: list[list[float]] = []
        for index, day in enumerate(session_dates):
            raw = by_key.get((symbol, day))
            if raw is None or not raw.traded:
                raise ContractError("MISSING_OR_NULL_BAR")
            if index <= formation_index:
                view = adjusted.get((symbol, day))
                if view is None:
                    raise ContractError("CAUSAL_BAR_MISSING")
                rows.append([view.adjusted_open, view.adjusted_high, view.adjusted_low, view.adjusted_close, view.adjusted_volume])
            else:
                if index <= formation_index + label_steps and raw.adjustment_factor != 1.0:
                    raise ContractError("CORPORATE_ACTION_IN_LABEL_PATH")
                assert raw.open is not None and raw.high is not None and raw.low is not None and raw.close is not None
                rows.append([raw.open, raw.high, raw.low, raw.close, 0.0 if raw.volume is None else raw.volume])
        array = np.asarray(rows, dtype=np.float64)
        if array.shape != (len(session_dates), 5) or not np.isfinite(array).all():
            raise ContractError("SERIES_ARRAY")
        arrays[symbol] = array
    return arrays


def build_sspt_training_adapter(
    *,
    bars: Iterable[DailyBar],
    master_rows: Iterable[MasterRow],
    calendar_days: Iterable[CalendarDay],
    symbols: tuple[str, ...],
    formation_date: str,
    lookback: int,
    scaler: TrainOnlyMinMax,
    scc_registry: StableLabelRegistry,
    ssc_registry: StableLabelRegistry,
) -> SSPTAdapterResult:
    session_dates = _session_dates(calendar_days)
    calendar = _calendar_projection(session_dates)
    formation_time = dict(calendar).get(formation_date)
    if formation_time is None:
        raise ContractError("FORMATION_SESSION")
    masters = _master_scope(master_rows, symbols=symbols, formation_date=formation_date, formation_time_ms=formation_time)
    arrays = _series_arrays(bars, symbols=symbols, session_dates=session_dates, formation_date=formation_date, label_steps=1)
    series = tuple(
        SymbolDailySeries(
            market_id=MARKET_ID,
            symbol=row.symbol,
            session_ids=session_dates,
            session_times_ms=tuple(timestamp for _, timestamp in calendar),
            feature_known_at_ms=tuple(timestamp for _, timestamp in calendar),
            ohlcv=arrays[row.symbol],
            sector_label=row.sector17_code,
            sector_known_at_ms=row.known_at_ms,
        )
        for row in masters
    )
    batch = build_cross_section_batch(
        series,
        expected_calendar=calendar,
        market_id=MARKET_ID,
        formation_time_ms=formation_time,
        lookback=lookback,
        scaler=scaler,
        scc_registry=scc_registry,
        ssc_registry=ssc_registry,
    )
    index = session_dates.index(formation_date)
    return SSPTAdapterResult(
        batch=batch,
        formation_date=formation_date,
        master_row_sha256s=tuple(row.raw_sha256 for row in masters),
        earliest_execution_session_id=session_dates[index + 1],
    )


def _tips_inputs(
    *,
    bars: Iterable[DailyBar],
    master_rows: Iterable[MasterRow],
    session_dates: tuple[str, ...],
    symbols: tuple[str, ...],
    formation_date: str,
    label_steps: int,
) -> tuple[MarketCalendar, tuple[SymbolOHLCV, ...], tuple[MasterRow, ...]]:
    calendar_projection = _calendar_projection(session_dates)
    formation_time = dict(calendar_projection).get(formation_date)
    if formation_time is None:
        raise ContractError("FORMATION_SESSION")
    masters = _master_scope(master_rows, symbols=symbols, formation_date=formation_date, formation_time_ms=formation_time)
    arrays = _series_arrays(bars, symbols=symbols, session_dates=session_dates, formation_date=formation_date, label_steps=label_steps)
    calendar = MarketCalendar(
        market_id=MARKET_ID,
        session_ids=session_dates,
        close_times_ms=np.asarray([timestamp for _, timestamp in calendar_projection], dtype=np.int64),
    )
    series = tuple(
        SymbolOHLCV(
            symbol=row.symbol,
            calendar_id=calendar.calendar_id,
            values=arrays[row.symbol],
            known_at_ms=np.asarray([timestamp for _, timestamp in calendar_projection], dtype=np.int64),
        )
        for row in masters
    )
    return calendar, series, masters


def build_tips_inference_adapter(
    *,
    bars: Iterable[DailyBar],
    master_rows: Iterable[MasterRow],
    calendar_days: Iterable[CalendarDay],
    symbols: tuple[str, ...],
    formation_date: str,
    partition_id: str,
) -> TIPSInferenceAdapterResult:
    session_dates = _session_dates(calendar_days)
    calendar, series, masters = _tips_inputs(
        bars=bars,
        master_rows=master_rows,
        session_dates=session_dates,
        symbols=symbols,
        formation_date=formation_date,
        label_steps=1,
    )
    batch = build_inference_batch(calendar, series, formation_date, partition_id=partition_id)
    index = session_dates.index(formation_date)
    return TIPSInferenceAdapterResult(batch, formation_date, tuple(row.raw_sha256 for row in masters), session_dates[index + 1])


def build_tips_training_adapter(
    *,
    bars: Iterable[DailyBar],
    master_rows: Iterable[MasterRow],
    calendar_days: Iterable[CalendarDay],
    symbols: tuple[str, ...],
    formation_date: str,
    partition_id: str,
    partition_session_ids: tuple[str, ...],
) -> TIPSTrainingAdapterResult:
    session_dates = _session_dates(calendar_days)
    calendar, series, masters = _tips_inputs(
        bars=bars,
        master_rows=master_rows,
        session_dates=session_dates,
        symbols=symbols,
        formation_date=formation_date,
        label_steps=4,
    )
    batch = build_training_batch(
        calendar,
        series,
        formation_date,
        partition_id=partition_id,
        partition_session_ids=partition_session_ids,
    )
    index = session_dates.index(formation_date)
    return TIPSTrainingAdapterResult(batch, formation_date, tuple(row.raw_sha256 for row in masters), session_dates[index + 1])
