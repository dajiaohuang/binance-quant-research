from __future__ import annotations

from pathlib import Path

from ..jquants_v2_bars_monthly_v1.contracts import (
    API_BASE,
    API_HOST,
    API_KEY_ENV,
    ALLOWED_PATHS,
    BAR_FIELDS,
    BOOTSTRAP_CIVIL_DATE_COUNT,
    BOOTSTRAP_FROM,
    BOOTSTRAP_GLOBAL_HTTP_CAP,
    BOOTSTRAP_MONTH_COUNT,
    BOOTSTRAP_PLAN_SHA256,
    BOOTSTRAP_QUERY_PLANS,
    BOOTSTRAP_SESSION_MAX,
    BOOTSTRAP_SESSION_MIN,
    BOOTSTRAP_TO,
    CALENDAR_FIELDS,
    CALENDAR_PATH,
    EXP005_Q04_RAW_BYTES,
    EXP005_Q04_RAW_RELATIVE,
    EXP005_Q04_RAW_SHA256,
    EXP005_Q04_RECEIPT_BYTES,
    EXP005_Q04_RECEIPT_RELATIVE,
    EXP005_Q04_RECEIPT_SHA256,
    EXP006_CLOSURE_RELATIVE,
    EXP006_CLOSURE_SHA256,
    FIRST_BAR_DATE,
    LAST_BAR_DATE,
    MAX_PAGES_PER_QUERY,
    MIN_SEND_SPACING_NS,
    PAGE_KEY,
    PREMIUM_BAR_FIELDS,
    CalendarDay,
    ContractError,
    DailyBar,
    MonthPlan,
    QueryPlan,
    canonical_json_bytes,
    code_text,
    date_text,
    exact_int,
    finite,
    inclusive_dates,
    json_file_bytes,
    month_text,
    policy_time_ms,
    sha256_bytes,
    strict_json,
    symbol_from_code,
    text,
    validate_attempt_id,
    validate_clock_domain,
    validate_key,
)


EXPERIMENT_ID = "exp_20260828_008"
PARENT_EXPERIMENT_ID = "exp_20260828_007"
BOOTSTRAP_RUN_ID = "exp_20260828_008_bootstrap_formal_001"
VERSION = "JQUANTS_V2_BARS_MONTHLY_V2"
RECEIPT_SCHEMA_VERSION = "JQUANTS_V2_BARS_MONTHLY_V2_RECEIPT_V1"
REUSE_REGISTRY_SCHEMA_VERSION = "JQUANTS_V2_BARS_MONTHLY_V2_REUSE_REGISTRY_V1"
MONTH_PLAN_SCHEMA_VERSION = "JQUANTS_V2_BARS_MONTHLY_V2_MONTH_PLAN_V1"
REQUIRED_REUSE_DATES = (FIRST_BAR_DATE, "2025-03-28", LAST_BAR_DATE)
REUSE_SOURCE_KINDS = (
    "BOOTSTRAP_BOUNDARY_FIRST",
    "EXP005_Q04_REUSE",
    "BOOTSTRAP_BOUNDARY_LAST",
)


def exact_source_constants() -> dict[str, object]:
    return {
        "closure": {
            "relative_path": EXP006_CLOSURE_RELATIVE,
            "sha256": EXP006_CLOSURE_SHA256,
        },
        "raw": {
            "bytes": EXP005_Q04_RAW_BYTES,
            "relative_path": EXP005_Q04_RAW_RELATIVE,
            "sha256": EXP005_Q04_RAW_SHA256,
        },
        "receipt": {
            "bytes": EXP005_Q04_RECEIPT_BYTES,
            "relative_path": EXP005_Q04_RECEIPT_RELATIVE,
            "sha256": EXP005_Q04_RECEIPT_SHA256,
        },
    }
