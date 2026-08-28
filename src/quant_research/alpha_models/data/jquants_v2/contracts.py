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
EXPERIMENT_ID = "exp_20260828_002"
RUN_ID = "exp_20260828_002_formal_001"
VERSION = "JQUANTS_V2_FREE_SOURCE_PROBE_OFFLINE_CONTRACT_V1"
ALLOWED_PATHS = (
    "/v2/markets/calendar",
    "/v2/equities/master",
    "/v2/equities/bars/daily",
)
MAX_PAGES_PER_QUERY = 25
GLOBAL_HTTP_CAP = 60
PAGINATION_PARAMETER = "pagination_key"
HEX64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
CODE = re.compile(r"[0-9A-Z]{4,5}\Z", re.ASCII)
API_KEY = re.compile(r"[A-Za-z0-9_-]{1,512}\Z", re.ASCII)
DATE_TEXT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
JST = ZoneInfo("Asia/Tokyo")

CALENDAR_FIELDS = frozenset(("Date", "HolDiv"))
MASTER_FIELDS = frozenset(
    (
        "Date",
        "Code",
        "CoName",
        "CoNameEn",
        "S17",
        "S17Nm",
        "S33",
        "S33Nm",
        "ScaleCat",
        "Mkt",
        "MktNm",
        "Mrgn",
        "MrgnNm",
        "SecType",
        "SecTypeNm",
    )
)
BAR_FIELDS = frozenset(
    (
        "Date",
        "Code",
        "O",
        "H",
        "L",
        "C",
        "Vo",
        "Va",
        "AdjFactor",
        "AdjO",
        "AdjH",
        "AdjL",
        "AdjC",
        "AdjVo",
    )
)


class ContractError(ValueError):
    """Sanitized fail-closed contract error."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not code or not code.isascii():
            code = "CONTRACT_ERROR"
        super().__init__(code)
        self.code = code


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json_file_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8") + b"\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_constant(_: str) -> None:
    raise ContractError("JSON_NONFINITE")


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _validate_scalars(value: Any) -> None:
    if type(value) is str:
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ContractError("JSON_SURROGATE")
    elif type(value) is list:
        for item in value:
            _validate_scalars(item)
    elif type(value) is dict:
        for key, item in value.items():
            _validate_scalars(key)
            _validate_scalars(item)


def strict_json_object(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or raw.startswith(b"\xef\xbb\xbf"):
        raise ContractError("JSON_ENCODING")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_object_no_duplicates,
            parse_constant=_reject_constant,
        )
    except ContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("JSON_INVALID") from exc
    if type(value) is not dict:
        raise ContractError("JSON_TOPLEVEL")
    _validate_scalars(value)
    return value


def strict_date(value: object, name: str = "DATE") -> str:
    if type(value) is not str or DATE_TEXT.fullmatch(value) is None:
        raise ContractError(name)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(name) from exc
    if parsed.isoformat() != value:
        raise ContractError(name)
    return value


def exact_int(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ContractError(name)
    return value


def finite_number(value: object, name: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or type(value) is bool:
        raise ContractError(name)
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0.0):
        raise ContractError(name)
    return result


def nonempty_text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip() or any(ord(c) < 0x20 for c in value):
        raise ContractError(name)
    return value


def validate_code(value: object) -> str:
    if type(value) is not str or CODE.fullmatch(value) is None:
        raise ContractError("CODE")
    return value


def canonical_equity_code(value: object) -> str:
    code = validate_code(value)
    if len(code) == 5 and code.endswith("0"):
        return code[:4]
    return code


def validate_api_key(value: object) -> str:
    if type(value) is not str or API_KEY.fullmatch(value) is None:
        raise ContractError("API_KEY_INVALID")
    return value


def jst_known_at_ms(date_text: str, hour: int, minute: int) -> int:
    day = date.fromisoformat(strict_date(date_text))
    stamp = datetime.combine(day, time(hour, minute), tzinfo=JST).astimezone(timezone.utc)
    return int(stamp.timestamp() * 1000)


@dataclass(frozen=True)
class QueryPlan:
    ordinal: int
    query_id: str
    path: str
    parameters: Mapping[str, str]
    result_contract: str
    response_cap_bytes: int
    max_pages: int = MAX_PAGES_PER_QUERY

    def __post_init__(self) -> None:
        exact_int(self.ordinal, "QUERY_ORDINAL", 1)
        nonempty_text(self.query_id, "QUERY_ID")
        if self.path not in ALLOWED_PATHS:
            raise ContractError("QUERY_PATH")
        if type(self.parameters) not in (dict, MappingProxyType):
            raise ContractError("QUERY_PARAMETERS")
        frozen: dict[str, str] = {}
        for key in sorted(self.parameters):
            value = self.parameters[key]
            if type(key) is not str or type(value) is not str or not key or not value or key == PAGINATION_PARAMETER:
                raise ContractError("QUERY_PARAMETERS")
            frozen[key] = value
        if self.result_contract not in (
            "EXACT_DATE_RANGE",
            "EXACT_RESPONSE_DATE",
            "EXPECTED_REJECTION",
            "EXACT_CODE_AND_DATE_RANGE",
        ):
            raise ContractError("RESULT_CONTRACT")
        exact_int(self.response_cap_bytes, "RESPONSE_CAP", 1)
        if type(self.max_pages) is not int or self.max_pages != MAX_PAGES_PER_QUERY:
            raise ContractError("MAX_PAGES")
        object.__setattr__(self, "parameters", MappingProxyType(frozen))

    def projection(self) -> dict[str, object]:
        return {
            "max_pages": self.max_pages,
            "ordinal": self.ordinal,
            "parameters": dict(self.parameters),
            "path": self.path,
            "query_id": self.query_id,
            "response_cap_bytes": self.response_cap_bytes,
            "result_contract": self.result_contract,
        }


QUERY_PLANS = (
    QueryPlan(1, "Q01_CALENDAR", "/v2/markets/calendar", {"from": "2025-05-01", "to": "2025-06-30"}, "EXACT_DATE_RANGE", 1_048_576),
    QueryPlan(2, "Q02_MASTER_NORMAL", "/v2/equities/master", {"date": "2025-06-02"}, "EXACT_RESPONSE_DATE", 8_388_608),
    QueryPlan(3, "Q03_MASTER_WEEKEND_REJECTION", "/v2/equities/master", {"date": "2025-06-01"}, "EXPECTED_REJECTION", 1_048_576),
    QueryPlan(4, "Q04_ALL_BARS_NORMAL", "/v2/equities/bars/daily", {"date": "2025-06-02"}, "EXACT_RESPONSE_DATE", 67_108_864),
    QueryPlan(5, "Q05_6501_SPLIT_RANGE", "/v2/equities/bars/daily", {"code": "6501", "from": "2024-07-01", "to": "2026-06-30"}, "EXACT_CODE_AND_DATE_RANGE", 67_108_864),
)
QUERY_PLAN_SHA256 = sha256_bytes(canonical_json_bytes([item.projection() for item in QUERY_PLANS]))


@dataclass(frozen=True)
class CalendarDay:
    session_date: str
    holiday_division: str
    received_at_ms: int
    raw_sha256: str

    def __post_init__(self) -> None:
        strict_date(self.session_date)
        if self.holiday_division not in ("0", "1", "2", "3"):
            raise ContractError("HOLIDAY_DIVISION")
        exact_int(self.received_at_ms, "RECEIVED_AT")
        if type(self.raw_sha256) is not str or HEX64.fullmatch(self.raw_sha256) is None:
            raise ContractError("RAW_SHA")

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
    known_at_ms: int
    received_at_ms: int
    raw_sha256: str

    def __post_init__(self) -> None:
        strict_date(self.snapshot_date)
        validate_code(self.raw_code)
        if self.symbol != canonical_equity_code(self.raw_code):
            raise ContractError("MASTER_SYMBOL")
        for name in (
            "company_name", "company_name_en", "sector17_code", "sector17_name",
            "sector33_code", "sector33_name", "scale_category", "market_code",
            "market_name", "margin_code", "margin_name", "security_type_code",
            "security_type_name",
        ):
            nonempty_text(getattr(self, name), "MASTER_TEXT")
        exact_int(self.known_at_ms, "KNOWN_AT")
        exact_int(self.received_at_ms, "RECEIVED_AT")
        if self.known_at_ms > self.received_at_ms:
            raise ContractError("MASTER_RECEIPT_CLOCK")
        if type(self.raw_sha256) is not str or HEX64.fullmatch(self.raw_sha256) is None:
            raise ContractError("RAW_SHA")


@dataclass(frozen=True)
class DailyBar:
    session_date: str
    raw_code: str
    symbol: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    amount: float | None
    adjustment_factor: float
    adjusted_open: float | None
    adjusted_high: float | None
    adjusted_low: float | None
    adjusted_close: float | None
    adjusted_volume: float | None
    known_at_ms: int
    received_at_ms: int
    raw_sha256: str

    def __post_init__(self) -> None:
        strict_date(self.session_date)
        validate_code(self.raw_code)
        if self.symbol != canonical_equity_code(self.raw_code):
            raise ContractError("BAR_SYMBOL")
        exact_int(self.known_at_ms, "KNOWN_AT")
        exact_int(self.received_at_ms, "RECEIVED_AT")
        if self.known_at_ms > self.received_at_ms:
            raise ContractError("BAR_RECEIPT_CLOCK")
        factor = finite_number(self.adjustment_factor, "ADJUSTMENT_FACTOR", positive=True)
        raw = (self.open, self.high, self.low, self.close)
        adjusted = (self.adjusted_open, self.adjusted_high, self.adjusted_low, self.adjusted_close)
        if all(value is None for value in raw):
            if any(value is not None for value in adjusted):
                raise ContractError("NULL_TRADE_ADJUSTED")
            if self.volume not in (None, 0, 0.0) or self.amount not in (None, 0, 0.0) or self.adjusted_volume not in (None, 0, 0.0):
                raise ContractError("NULL_TRADE_VOLUME")
        elif any(value is None for value in raw) or any(value is None for value in adjusted):
            raise ContractError("PARTIAL_OHLC")
        else:
            o, h, low, c = (finite_number(value, "OHLC", positive=True) for value in raw)
            ao, ah, al, ac = (finite_number(value, "ADJUSTED_OHLC", positive=True) for value in adjusted)
            if h < max(o, low, c) or low > min(o, h, c) or ah < max(ao, al, ac) or al > min(ao, ah, ac):
                raise ContractError("OHLC_IDENTITY")
            volume = finite_number(self.volume, "VOLUME")
            amount = finite_number(self.amount, "AMOUNT")
            adjusted_volume = finite_number(self.adjusted_volume, "ADJUSTED_VOLUME")
            if volume < 0 or amount < 0 or adjusted_volume < 0:
                raise ContractError("NEGATIVE_FLOW")
            ratios = (ao / o, ah / h, al / low, ac / c)
            if max(ratios) - min(ratios) > max(1e-8, abs(sum(ratios) / 4.0) * 1e-6):
                raise ContractError("ADJUSTED_PRICE_RATIO")
            ratio = sum(ratios) / 4.0
            if volume > 0 and abs(adjusted_volume * ratio - volume) > max(1e-6, volume * 1e-6):
                raise ContractError("ADJUSTED_VOLUME_RATIO")
        if factor != self.adjustment_factor:
            raise ContractError("ADJUSTMENT_FACTOR")
        if type(self.raw_sha256) is not str or HEX64.fullmatch(self.raw_sha256) is None:
            raise ContractError("RAW_SHA")

    @property
    def traded(self) -> bool:
        return self.close is not None


@dataclass(frozen=True)
class ListingSpell:
    symbol: str
    first_snapshot_date: str
    last_snapshot_date: str
    next_snapshot_date_exclusive: str | None
    market_code: str
    security_type_code: str
    derivation: str = "ADJACENT_MASTER_SNAPSHOT_DIFFERENCE_ONLY"

    def __post_init__(self) -> None:
        canonical_equity_code(self.symbol)
        strict_date(self.first_snapshot_date)
        strict_date(self.last_snapshot_date)
        if self.next_snapshot_date_exclusive is not None:
            strict_date(self.next_snapshot_date_exclusive)
            if self.next_snapshot_date_exclusive <= self.last_snapshot_date:
                raise ContractError("SPELL_INTERVAL")
        nonempty_text(self.market_code, "MARKET_CODE")
        nonempty_text(self.security_type_code, "SECURITY_TYPE")
        if self.derivation != "ADJACENT_MASTER_SNAPSHOT_DIFFERENCE_ONLY":
            raise ContractError("SPELL_DERIVATION")


@dataclass(frozen=True)
class CausalAdjustedBar:
    symbol: str
    session_date: str
    formation_time_ms: int
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    raw_volume: float
    causal_scale: float
    adjusted_open: float
    adjusted_high: float
    adjusted_low: float
    adjusted_close: float
    adjusted_volume: float
    source_sha256s: tuple[str, ...]

