from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import numpy as np

from quant_research.alpha_models.sspt_v2.contracts import CrossSectionTrainingBatch, StableLabelRegistry, SymbolDailySeries
from quant_research.alpha_models.sspt_v2.data import TrainOnlyMinMax, build_cross_section_batch
from quant_research.alpha_models.tips_v1.contracts import TIPSTrainingBatch
from quant_research.alpha_models.tips_v1.data import MarketCalendar, SymbolOHLCV, build_training_batch

from .contracts import CalendarDay, DailyBar, MasterRow, ProbeError, symbol_from_code
from .loader import causal_prices, master_observed_at


MARKET_ID="JPX_JQUANTS_V2_OBSERVATION"


@dataclass(frozen=True)
class SSPTAdapterResult:
    batch:CrossSectionTrainingBatch
    observation_semantics:str="POLICY_DERIVED_OBSERVATION_NOT_SOURCE_PUBLICATION"
    same_close_execution_allowed:bool=False


@dataclass(frozen=True)
class TIPSAdapterResult:
    batch:TIPSTrainingBatch
    observation_semantics:str="POLICY_DERIVED_OBSERVATION_NOT_SOURCE_PUBLICATION"
    same_close_execution_allowed:bool=False


def _sessions(calendar:Iterable[CalendarDay])->tuple[CalendarDay,...]:
    rows=tuple(sorted((row for row in calendar if row.is_session),key=lambda x:x.session_date))
    if len(rows)<43 or len({x.session_date for x in rows})!=len(rows): raise ProbeError("CALENDAR_SCOPE")
    return rows


def _inputs(*,calendar:Iterable[CalendarDay],bars:Iterable[DailyBar],masters:Iterable[MasterRow],symbols:tuple[str,...],formation_date:str,label_steps:int)->tuple[tuple[CalendarDay,...],tuple[int,...],tuple[MasterRow,...],dict[str,np.ndarray]]:
    sessions=_sessions(calendar); dates=tuple(x.session_date for x in sessions)
    if symbols!=tuple(sorted((symbol_from_code(x) for x in symbols))) or len(symbols)<2 or len(set(symbols))!=len(symbols): raise ProbeError("SYMBOL_SCOPE")
    try: formation_index=dates.index(formation_date)
    except ValueError as exc: raise ProbeError("FORMATION") from exc
    if formation_index+label_steps>=len(dates): raise ProbeError("LABEL_PATH")
    all_bars=tuple(bars); by_key={(row.symbol,row.session_date):row for row in all_bars}
    if len(by_key)!=len(all_bars): raise ProbeError("DUPLICATE_BAR")
    formation_rows=tuple(by_key.get((symbol,formation_date)) for symbol in symbols)
    if any(row is None or not row.traded for row in formation_rows): raise ProbeError("FORMATION_BAR")  # type: ignore[union-attr]
    formation_time=max(row.available_at_ms for row in formation_rows if row is not None)
    master_scope=tuple(master_observed_at(masters,symbol=symbol,snapshot_date=formation_date,formation_time_ms=formation_time) for symbol in symbols)
    session_times=[]
    for day in dates:
        rows=tuple(by_key.get((symbol,day)) for symbol in symbols)
        if any(row is None for row in rows): raise ProbeError("MISSING_BAR")
        session_times.append(max(row.available_at_ms for row in rows if row is not None))
    if any(right<=left for left,right in zip(session_times,session_times[1:])): raise ProbeError("NONINCREASING_OBSERVATION_CLOCK")
    arrays={}
    for symbol in symbols:
        causal=causal_prices(all_bars,symbol=symbol,formation_time_ms=formation_time); values=[]
        for index,day in enumerate(dates):
            row=by_key.get((symbol,day))
            if row is None or not row.traded: raise ProbeError("MISSING_OR_NULL_BAR")
            if index<=formation_index:
                if day not in causal: raise ProbeError("BAR_NOT_OBSERVED")
                values.append(causal[day])
            else:
                if index<=formation_index+label_steps and row.adjustment_factor!=1.0: raise ProbeError("ACTION_IN_LABEL_PATH")
                assert row.o is not None and row.h is not None and row.low is not None and row.c is not None
                values.append((row.o,row.h,row.low,row.c,0.0 if row.volume is None else row.volume))
        arrays[symbol]=np.asarray(values,dtype=np.float64)
    return sessions,tuple(session_times),master_scope,arrays


def build_sspt_training(*,calendar:Iterable[CalendarDay],bars:Iterable[DailyBar],masters:Iterable[MasterRow],symbols:tuple[str,...],formation_date:str,lookback:int,scaler:TrainOnlyMinMax,scc_registry:StableLabelRegistry,ssc_registry:StableLabelRegistry)->SSPTAdapterResult:
    sessions,times,master_scope,arrays=_inputs(calendar=calendar,bars=bars,masters=masters,symbols=symbols,formation_date=formation_date,label_steps=1); dates=tuple(x.session_date for x in sessions); formation=dates.index(formation_date)
    series=tuple(SymbolDailySeries(MARKET_ID,row.symbol,dates,times,times,arrays[row.symbol],row.sector17_code,row.available_at_ms) for row in master_scope)
    batch=build_cross_section_batch(series,expected_calendar=tuple(zip(dates,times)),market_id=MARKET_ID,formation_time_ms=times[formation],lookback=lookback,scaler=scaler,scc_registry=scc_registry,ssc_registry=ssc_registry)
    return SSPTAdapterResult(batch)


def build_tips_training(*,calendar:Iterable[CalendarDay],bars:Iterable[DailyBar],masters:Iterable[MasterRow],symbols:tuple[str,...],formation_date:str,partition_id:str,partition_session_ids:tuple[str,...])->TIPSAdapterResult:
    sessions,session_times,_,arrays=_inputs(calendar=calendar,bars=bars,masters=masters,symbols=symbols,formation_date=formation_date,label_steps=4); dates=tuple(x.session_date for x in sessions); times=np.asarray(session_times,dtype=np.int64)
    market_calendar=MarketCalendar(MARKET_ID,dates,times); series=tuple(SymbolOHLCV(symbol,market_calendar.calendar_id,arrays[symbol],times) for symbol in symbols)
    return TIPSAdapterResult(build_training_batch(market_calendar,series,formation_date,partition_id=partition_id,partition_session_ids=partition_session_ids))
