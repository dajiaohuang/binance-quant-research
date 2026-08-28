from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping
from zoneinfo import ZoneInfo


API_BASE = "https://api.jquants.com"
API_HOST = "api.jquants.com"
API_KEY_ENV = "JQUANTS_API_KEY"
EXPERIMENT_ID = "exp_20260828_003"
RUN_ID = "exp_20260828_003_formal_001"
VERSION = "JQUANTS_V2_FREE_SOURCE_PROBE_OFFLINE_CONTRACT_V2"
MAX_PAGES = 25
GLOBAL_HTTP_CAP = 60
PAGE_KEY = "pagination_key"
PATHS = ("/v2/markets/calendar", "/v2/equities/master", "/v2/equities/bars/daily")
KEY_RE = re.compile(r"[A-Za-z0-9_-]{1,512}\Z", re.ASCII)
CODE_RE = re.compile(r"[0-9A-Z]{4,5}\Z", re.ASCII)
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
JST = ZoneInfo("Asia/Tokyo")
CALENDAR_FIELDS = frozenset(("Date", "HolDiv"))
MASTER_FIELDS = frozenset(("Date", "Code", "CoName", "CoNameEn", "S17", "S17Nm", "S33", "S33Nm", "ScaleCat", "Mkt", "MktNm", "Mrgn", "MrgnNm", "SecType", "SecTypeNm"))
BAR_FIELDS = frozenset(("Date", "Code", "O", "H", "L", "C", "Vo", "Va", "AdjFactor", "ExRT", "AdjO", "AdjH", "AdjL", "AdjC", "AdjVo"))


class ProbeError(ValueError):
    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code and code.isascii() else "PROBE_ERROR"
        super().__init__(safe)
        self.code = safe


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def json_file_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _constant(_: str) -> None:
    raise ProbeError("JSON_NONFINITE")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProbeError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _scalars(value: Any) -> None:
    if type(value) is str and any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ProbeError("JSON_SURROGATE")
    if type(value) is list:
        for item in value:
            _scalars(item)
    if type(value) is dict:
        for key, item in value.items():
            _scalars(key)
            _scalars(item)


def strict_json(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or raw.startswith(b"\xef\xbb\xbf"):
        raise ProbeError("JSON_ENCODING")
    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs, parse_constant=_constant)
    except ProbeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError("JSON_INVALID") from exc
    if type(value) is not dict:
        raise ProbeError("JSON_TOPLEVEL")
    _scalars(value)
    return value


def exact_int(value: object, code: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ProbeError(code)
    return value


def finite(value: object, code: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or type(value) is bool:
        raise ProbeError(code)
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ProbeError(code)
    return result


def text(value: object, code: str) -> str:
    if type(value) is not str or not value or value != value.strip() or any(ord(c) < 0x20 for c in value):
        raise ProbeError(code)
    return value


def date_text(value: object, code: str = "DATE") -> str:
    if type(value) is not str or DATE_RE.fullmatch(value) is None:
        raise ProbeError(code)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ProbeError(code) from exc
    if parsed.isoformat() != value:
        raise ProbeError(code)
    return value


def code_text(value: object) -> str:
    if type(value) is not str or CODE_RE.fullmatch(value) is None:
        raise ProbeError("CODE")
    return value


def symbol_from_code(value: object) -> str:
    code = code_text(value)
    return code[:4] if len(code) == 5 and code.endswith("0") else code


def policy_time_ms(day: str, hour: int, minute: int) -> int:
    stamp = datetime.combine(date.fromisoformat(date_text(day)), time(hour, minute), JST).astimezone(timezone.utc)
    return int(stamp.timestamp() * 1000)


def validate_key(value: object) -> str:
    if type(value) is not str or KEY_RE.fullmatch(value) is None:
        raise ProbeError("API_KEY_INVALID")
    return value


@dataclass(frozen=True)
class QueryPlan:
    ordinal: int
    query_id: str
    path: str
    parameters: Mapping[str, str]
    coverage: str
    cap_bytes: int

    def __post_init__(self) -> None:
        exact_int(self.ordinal, "QUERY_ORDINAL", 1)
        text(self.query_id, "QUERY_ID")
        if self.path not in PATHS or type(self.parameters) not in (dict, MappingProxyType):
            raise ProbeError("QUERY")
        frozen: dict[str, str] = {}
        for key in sorted(self.parameters):
            value = self.parameters[key]
            if type(key) is not str or type(value) is not str or not key or not value or key == PAGE_KEY:
                raise ProbeError("QUERY_PARAMETERS")
            frozen[key] = value
        exact_int(self.cap_bytes, "CAP", 1)
        text(self.coverage, "COVERAGE")
        object.__setattr__(self, "parameters", MappingProxyType(frozen))

    def projection(self) -> dict[str, object]:
        return {"cap_bytes": self.cap_bytes, "coverage": self.coverage, "max_pages": MAX_PAGES, "ordinal": self.ordinal, "parameters": dict(self.parameters), "path": self.path, "query_id": self.query_id}


QUERY_PLANS = (
    QueryPlan(1, "Q01_CALENDAR", PATHS[0], {"from": "2025-03-28", "to": "2025-03-31"}, "EVERY_CIVIL_DATE_EXACTLY_ONCE_AND_NONEMPTY", 1_048_576),
    QueryPlan(2, "Q02_MASTER_NORMAL", PATHS[1], {"code": "9433", "date": "2025-03-31"}, "NONEMPTY_ALL_ROWS_EXACT_CODE_AND_DATE", 8_388_608),
    QueryPlan(3, "Q03_MASTER_NONTRADING_MAPPING", PATHS[1], {"code": "9433", "date": "2025-03-30"}, "HTTP200_NONEMPTY_CODE_9433_RETURNED_DATE_2025-03-31", 8_388_608),
    QueryPlan(4, "Q04_ALL_BARS_NORMAL", PATHS[2], {"date": "2025-03-28"}, "NONEMPTY_ALL_ROWS_EXACT_DATE", 67_108_864),
    QueryPlan(5, "Q05_9433_SPLIT_RANGE", PATHS[2], {"code": "9433", "from": "2025-03-27", "to": "2025-04-02"}, "NONEMPTY_9433_EXPECTED_SESSIONS_AND_2025-03-28_SPLIT_FIELDS", 67_108_864),
)
PLAN_SHA256 = sha256_bytes(canonical_json_bytes([plan.projection() for plan in QUERY_PLANS]))


@dataclass(frozen=True)
class CalendarDay:
    session_date: str
    holiday_division: str
    policy_observation_ms: int
    received_at_ms: int
    available_at_ms: int
    raw_sha256: str

    def __post_init__(self) -> None:
        date_text(self.session_date)
        if self.holiday_division not in ("0", "1", "2", "3"):
            raise ProbeError("HOLDIV")
        exact_int(self.policy_observation_ms, "POLICY_TIME")
        exact_int(self.received_at_ms, "RECEIVED")
        if self.policy_observation_ms != policy_time_ms(self.session_date, 0, 0):
            raise ProbeError("POLICY_TIME")
        if self.available_at_ms != max(self.policy_observation_ms, self.received_at_ms):
            raise ProbeError("AVAILABLE_TIME")
        if HEX64_RE.fullmatch(self.raw_sha256) is None:
            raise ProbeError("RAW_SHA")

    @property
    def is_session(self) -> bool:
        return self.holiday_division == "0"


@dataclass(frozen=True)
class MasterRow:
    snapshot_date: str
    raw_code: str
    symbol: str
    company_name: str
    company_name_en: str
    sector17_code: str
    sector17_name: str
    sector33_code: str
    sector33_name: str
    scale_category: str
    market_code: str
    market_name: str
    margin_code: str
    margin_name: str
    security_type_code: str
    security_type_name: str
    policy_observation_ms: int
    received_at_ms: int
    available_at_ms: int
    raw_sha256: str

    def __post_init__(self) -> None:
        date_text(self.snapshot_date); code_text(self.raw_code)
        if self.symbol != symbol_from_code(self.raw_code): raise ProbeError("SYMBOL")
        for name in ("company_name","company_name_en","sector17_code","sector17_name","sector33_code","sector33_name","scale_category","market_code","market_name","margin_code","margin_name","security_type_code","security_type_name"):
            text(getattr(self, name), "MASTER_TEXT")
        if self.policy_observation_ms != policy_time_ms(self.snapshot_date, 8, 0): raise ProbeError("POLICY_TIME")
        if self.available_at_ms != max(self.policy_observation_ms, self.received_at_ms): raise ProbeError("AVAILABLE_TIME")
        if HEX64_RE.fullmatch(self.raw_sha256) is None: raise ProbeError("RAW_SHA")


@dataclass(frozen=True)
class DailyBar:
    session_date: str
    raw_code: str
    symbol: str
    o: float | None
    h: float | None
    low: float | None
    c: float | None
    volume: float | None
    amount: float | None
    adjustment_factor: float
    ex_right: str
    adjusted_o: float | None
    adjusted_h: float | None
    adjusted_low: float | None
    adjusted_c: float | None
    adjusted_volume: float | None
    policy_observation_ms: int
    received_at_ms: int
    available_at_ms: int
    raw_sha256: str

    def __post_init__(self) -> None:
        date_text(self.session_date); code_text(self.raw_code)
        if self.symbol != symbol_from_code(self.raw_code): raise ProbeError("SYMBOL")
        factor = finite(self.adjustment_factor, "FACTOR", positive=True)
        if self.ex_right not in ("0", "1"): raise ProbeError("EXRT")
        if factor != self.adjustment_factor: raise ProbeError("FACTOR")
        raw = (self.o, self.h, self.low, self.c); adj = (self.adjusted_o, self.adjusted_h, self.adjusted_low, self.adjusted_c)
        if all(item is None for item in raw):
            if any(item is not None for item in adj) or self.volume not in (None,0,0.0) or self.amount not in (None,0,0.0) or self.adjusted_volume not in (None,0,0.0): raise ProbeError("NULL_BAR")
        elif any(item is None for item in raw + adj): raise ProbeError("PARTIAL_BAR")
        else:
            o,h,low,c = (finite(item,"OHLC",positive=True) for item in raw); ao,ah,al,ac = (finite(item,"ADJOHLC",positive=True) for item in adj)
            if h < max(o,low,c) or low > min(o,h,c) or ah < max(ao,al,ac) or al > min(ao,ah,ac): raise ProbeError("OHLC")
            vo=finite(self.volume,"VOLUME"); va=finite(self.amount,"AMOUNT"); avo=finite(self.adjusted_volume,"ADJVOLUME")
            if min(vo,va,avo)<0: raise ProbeError("FLOW")
            ratios=(ao/o,ah/h,al/low,ac/c)
            if max(ratios)-min(ratios)>max(1e-8,abs(sum(ratios)/4)*1e-6): raise ProbeError("ADJUSTED_RATIO")
        if self.policy_observation_ms != policy_time_ms(self.session_date, 16, 30): raise ProbeError("POLICY_TIME")
        if self.available_at_ms != max(self.policy_observation_ms, self.received_at_ms): raise ProbeError("AVAILABLE_TIME")
        if HEX64_RE.fullmatch(self.raw_sha256) is None: raise ProbeError("RAW_SHA")

    @property
    def traded(self) -> bool:
        return self.c is not None


@dataclass(frozen=True)
class PresenceResolution:
    status: str
    intervals: tuple[tuple[str, str, str], ...]
    reason: str
