from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping


API_BASE = "https://api.jquants.com"
API_HOST = "api.jquants.com"
API_KEY_ENV = "JQUANTS_API_KEY"
EXPERIMENT_ID = "exp_20260828_007"
BOOTSTRAP_RUN_ID = "exp_20260828_007_bootstrap_formal_001"
VERSION = "JQUANTS_V2_BARS_MONTHLY_V1"
CALENDAR_PATH = "/v2/markets/calendar"
BARS_PATH = "/v2/equities/bars/daily"
ALLOWED_PATHS = (CALENDAR_PATH, BARS_PATH)
PAGE_KEY = "pagination_key"
MAX_PAGES_PER_QUERY = 8
BOOTSTRAP_GLOBAL_HTTP_CAP = 20
MIN_SEND_SPACING_NS = 15_000_000_000
BOOTSTRAP_FROM = "2024-07-01"
BOOTSTRAP_TO = "2026-05-29"
BOOTSTRAP_CIVIL_DATE_COUNT = 698
BOOTSTRAP_MONTH_COUNT = 23
BOOTSTRAP_SESSION_MIN = 450
BOOTSTRAP_SESSION_MAX = 475
FIRST_BAR_DATE = BOOTSTRAP_FROM
LAST_BAR_DATE = BOOTSTRAP_TO
JST = timezone(timedelta(hours=9), name="JST")

CALENDAR_FIELDS = frozenset(("Date", "HolDiv"))
BAR_FIELDS = frozenset(
    (
        "Date",
        "Code",
        "O",
        "H",
        "L",
        "C",
        "UL",
        "LL",
        "Vo",
        "Va",
        "AdjFactor",
        "AdjO",
        "AdjH",
        "AdjL",
        "AdjC",
        "AdjVo",
        "MktCap",
        "ExRT",
    )
)
PREMIUM_BAR_FIELDS = frozenset(
    (
        "MO", "MH", "ML", "MC", "MUL", "MLL", "MVo", "MVa",
        "MAdjO", "MAdjH", "MAdjL", "MAdjC", "MAdjVo",
        "AO", "AH", "AL", "AC", "AUL", "ALL", "AVo", "AVa",
        "AAdjO", "AAdjH", "AAdjL", "AAdjC", "AAdjVo",
    )
)
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
KEY_RE = re.compile(r"[A-Za-z0-9_-]{1,512}\Z", re.ASCII)
CODE_RE = re.compile(r"[0-9A-Z]{4,5}\Z", re.ASCII)
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
MONTH_RE = re.compile(r"[0-9]{4}-[0-9]{2}\Z", re.ASCII)
ATTEMPT_RE = re.compile(r"jquants-bars-[0-9]{6}-attempt[0-9]{3}\Z", re.ASCII)
CLOCK_DOMAIN_RE = re.compile(r"[a-z0-9_-]{8,128}\Z", re.ASCII)

EXP005_Q04_RAW_RELATIVE = (
    "data/raw/jquants_v2_v4/runs/.exp_20260828_005_formal_001.staging/"
    "responses/04_Q04_ALL_BARS_NORMAL_page_0001.json"
)
EXP005_Q04_RAW_BYTES = 1_222_835
EXP005_Q04_RAW_SHA256 = "adac886e159f3979421b98e3d1b52fedafcafb68c931ef18f6a89597b035fad1"
EXP005_Q04_RECEIPT_RELATIVE = (
    "data/raw/jquants_v2_v4/runs/.exp_20260828_005_formal_001.staging/"
    "response_receipts/0004_04_Q04_ALL_BARS_NORMAL_page_0001.receipt.json"
)
EXP005_Q04_RECEIPT_BYTES = 583
EXP005_Q04_RECEIPT_SHA256 = "9ef6d7e175c925930306c353ddc1200ebd26ef0152833c3e9ec819fdc1aaa6e4"
EXP006_CLOSURE_RELATIVE = "experiments/exp_20260828_006/artifacts/closure_manifest.json"
EXP006_CLOSURE_SHA256 = "4e8488fa3ca8ec5636093edca43b1a709f8077803bac4f10e80951e726f98bf6"


class ContractError(ValueError):
    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code and code.isascii() else "CONTRACT_ERROR"
        super().__init__(safe)
        self.code = safe


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def json_file_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8") + b"\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _constant(_: str) -> None:
    raise ContractError("JSON_NONFINITE")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _scalars(value: Any) -> None:
    if type(value) is str and any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ContractError("JSON_SURROGATE")
    if type(value) is list:
        for item in value:
            _scalars(item)
    if type(value) is dict:
        for key, item in value.items():
            _scalars(key)
            _scalars(item)


def strict_json(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or raw.startswith(b"\xef\xbb\xbf"):
        raise ContractError("JSON_ENCODING")
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
    except ContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("JSON_INVALID") from exc
    if type(value) is not dict:
        raise ContractError("JSON_TOPLEVEL")
    _scalars(value)
    return value


def exact_int(value: object, code: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ContractError(code)
    return value


def finite(value: object, code: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or type(value) is bool:
        raise ContractError(code)
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ContractError(code)
    return result


def text(value: object, code: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or any(ord(char) < 0x20 for char in value)
    ):
        raise ContractError(code)
    return value


def date_text(value: object, code: str = "DATE") -> str:
    if type(value) is not str or DATE_RE.fullmatch(value) is None:
        raise ContractError(code)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(code) from exc
    if parsed.isoformat() != value:
        raise ContractError(code)
    return value


def month_text(value: object) -> str:
    if type(value) is not str or MONTH_RE.fullmatch(value) is None:
        raise ContractError("MONTH")
    try:
        parsed = date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise ContractError("MONTH") from exc
    if parsed.strftime("%Y-%m") != value:
        raise ContractError("MONTH")
    return value


def code_text(value: object) -> str:
    if type(value) is not str or CODE_RE.fullmatch(value) is None:
        raise ContractError("CODE")
    return value


def symbol_from_code(value: object) -> str:
    code = code_text(value)
    return code[:4] if len(code) == 5 and code.endswith("0") else code


def validate_key(value: object) -> str:
    if type(value) is not str or KEY_RE.fullmatch(value) is None:
        raise ContractError("API_KEY_INVALID")
    return value


def policy_time_ms(day: str, hour: int, minute: int) -> int:
    stamp = datetime.combine(
        date.fromisoformat(date_text(day)), time(hour, minute), JST
    ).astimezone(timezone.utc)
    return int(stamp.timestamp() * 1000)


def inclusive_dates(start: str, end: str) -> tuple[str, ...]:
    current = date.fromisoformat(date_text(start))
    final = date.fromisoformat(date_text(end))
    if current > final:
        raise ContractError("DATE_RANGE")
    output: list[str] = []
    while current <= final:
        output.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(output)


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
        if self.path not in ALLOWED_PATHS or type(self.parameters) not in (dict, MappingProxyType):
            raise ContractError("QUERY")
        frozen: dict[str, str] = {}
        for key in sorted(self.parameters):
            value = self.parameters[key]
            if (
                type(key) is not str
                or type(value) is not str
                or not key
                or not value
                or key == PAGE_KEY
            ):
                raise ContractError("QUERY_PARAMETERS")
            frozen[key] = value
        if self.path == CALENDAR_PATH:
            if set(frozen) != {"from", "to"}:
                raise ContractError("CALENDAR_QUERY")
            date_text(frozen["from"])
            date_text(frozen["to"])
        elif set(frozen) != {"date"}:
            raise ContractError("ALL_MARKET_DATE_ONLY")
        exact_int(self.cap_bytes, "CAP", 1)
        text(self.coverage, "COVERAGE")
        object.__setattr__(self, "parameters", MappingProxyType(frozen))

    def projection(self) -> dict[str, object]:
        return {
            "cap_bytes": self.cap_bytes,
            "coverage": self.coverage,
            "max_pages": MAX_PAGES_PER_QUERY,
            "ordinal": self.ordinal,
            "parameters": dict(self.parameters),
            "path": self.path,
            "query_id": self.query_id,
        }


BOOTSTRAP_QUERY_PLANS = (
    QueryPlan(
        1,
        "Q01_CALENDAR",
        CALENDAR_PATH,
        {"from": BOOTSTRAP_FROM, "to": BOOTSTRAP_TO},
        "EXACT_698_ORDERED_CIVIL_DATES_HOLDIV_ENUM_SESSION_COUNT_450_TO_475",
        4_194_304,
    ),
    QueryPlan(
        2,
        "Q02_BARS_FIRST",
        BARS_PATH,
        {"date": FIRST_BAR_DATE},
        "NONEMPTY_ALL_MARKET_EXACT_DATE_FREE18_REUSABLE_BOUNDARY_LEAF",
        67_108_864,
    ),
    QueryPlan(
        3,
        "Q03_BARS_LAST",
        BARS_PATH,
        {"date": LAST_BAR_DATE},
        "NONEMPTY_ALL_MARKET_EXACT_DATE_FREE18_REUSABLE_BOUNDARY_LEAF",
        67_108_864,
    ),
)
BOOTSTRAP_PLAN_SHA256 = sha256_bytes(
    canonical_json_bytes([plan.projection() for plan in BOOTSTRAP_QUERY_PLANS])
)


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
            raise ContractError("HOLDIV")
        exact_int(self.received_at_ms, "RECEIVED")
        if self.policy_observation_ms != policy_time_ms(self.session_date, 0, 0):
            raise ContractError("POLICY_TIME")
        if self.available_at_ms != max(self.policy_observation_ms, self.received_at_ms):
            raise ContractError("AVAILABLE_TIME")
        if HEX64_RE.fullmatch(self.raw_sha256) is None:
            raise ContractError("RAW_SHA")

    @property
    def is_tse_session(self) -> bool:
        return self.holiday_division in ("1", "2")


@dataclass(frozen=True)
class DailyBar:
    session_date: str
    raw_code: str
    symbol: str
    o: float | None
    h: float | None
    low: float | None
    c: float | None
    upper_limit: str
    lower_limit: str
    volume: float | None
    amount: float | None
    adjustment_factor: float
    adjusted_o: float | None
    adjusted_h: float | None
    adjusted_low: float | None
    adjusted_c: float | None
    adjusted_volume: float | None
    market_cap: float | None
    ex_right_type: str | None
    policy_observation_ms: int
    received_at_ms: int
    available_at_ms: int
    raw_sha256: str

    def __post_init__(self) -> None:
        date_text(self.session_date)
        code_text(self.raw_code)
        if self.symbol != symbol_from_code(self.raw_code):
            raise ContractError("SYMBOL")
        factor = finite(self.adjustment_factor, "FACTOR", positive=True)
        if factor != self.adjustment_factor:
            raise ContractError("FACTOR")
        if self.upper_limit not in ("0", "1") or self.lower_limit not in ("0", "1"):
            raise ContractError("LIMIT_FLAG")
        if self.ex_right_type not in (None, "1", "2", "3"):
            raise ContractError("EXRT")
        if self.market_cap is not None and finite(self.market_cap, "MARKET_CAP") < 0:
            raise ContractError("MARKET_CAP")
        raw = (self.o, self.h, self.low, self.c)
        adjusted = (self.adjusted_o, self.adjusted_h, self.adjusted_low, self.adjusted_c)
        if all(item is None for item in raw):
            if (
                any(item is not None for item in adjusted)
                or self.volume not in (None, 0, 0.0)
                or self.amount not in (None, 0, 0.0)
                or self.adjusted_volume not in (None, 0, 0.0)
                or self.market_cap is not None
            ):
                raise ContractError("NULL_BAR")
        elif any(item is None for item in raw + adjusted):
            raise ContractError("PARTIAL_BAR")
        else:
            o, h, low, c = (finite(item, "OHLC", positive=True) for item in raw)
            ao, ah, al, ac = (
                finite(item, "ADJUSTED_OHLC", positive=True) for item in adjusted
            )
            if h < max(o, low, c) or low > min(o, h, c):
                raise ContractError("OHLC")
            if ah < max(ao, al, ac) or al > min(ao, ah, ac):
                raise ContractError("ADJUSTED_OHLC")
            vo = finite(self.volume, "VOLUME")
            va = finite(self.amount, "AMOUNT")
            avo = finite(self.adjusted_volume, "ADJUSTED_VOLUME")
            if min(vo, va, avo) < 0:
                raise ContractError("FLOW")
        if self.policy_observation_ms != policy_time_ms(self.session_date, 16, 30):
            raise ContractError("POLICY_TIME")
        if self.available_at_ms != max(self.policy_observation_ms, self.received_at_ms):
            raise ContractError("AVAILABLE_TIME")
        if HEX64_RE.fullmatch(self.raw_sha256) is None:
            raise ContractError("RAW_SHA")

    @property
    def traded(self) -> bool:
        return self.c is not None


@dataclass(frozen=True)
class MonthPlan:
    month: str
    session_dates: tuple[str, ...]
    bootstrap_plan_sha256: str
    session_list_sha256: str

    def __post_init__(self) -> None:
        month_text(self.month)
        if type(self.session_dates) is not tuple or not self.session_dates:
            raise ContractError("MONTH_SESSIONS")
        checked = tuple(date_text(item) for item in self.session_dates)
        if checked != tuple(sorted(set(checked))) or any(not item.startswith(self.month) for item in checked):
            raise ContractError("MONTH_SESSIONS")
        if self.bootstrap_plan_sha256 != BOOTSTRAP_PLAN_SHA256:
            raise ContractError("BOOTSTRAP_PLAN_SHA")
        if HEX64_RE.fullmatch(self.session_list_sha256) is None:
            raise ContractError("SESSION_LIST_SHA")

    def projection(self) -> dict[str, object]:
        return {
            "bootstrap_plan_sha256": self.bootstrap_plan_sha256,
            "month": self.month,
            "session_dates": list(self.session_dates),
            "session_list_sha256": self.session_list_sha256,
        }


def validate_attempt_id(value: object, month: str) -> str:
    month_text(month)
    if type(value) is not str or ATTEMPT_RE.fullmatch(value) is None:
        raise ContractError("ATTEMPT_ID")
    if value[13:19] != month.replace("-", ""):
        raise ContractError("ATTEMPT_MONTH")
    return value


def validate_clock_domain(value: object) -> str:
    if type(value) is not str or CLOCK_DOMAIN_RE.fullmatch(value) is None:
        raise ContractError("CLOCK_DOMAIN")
    return value
