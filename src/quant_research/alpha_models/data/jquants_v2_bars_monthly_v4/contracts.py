from __future__ import annotations

import re

from ..jquants_v2_bars_monthly_v1.contracts import (
    API_BASE,
    API_HOST,
    API_KEY_ENV,
    BAR_FIELDS,
    BARS_PATH,
    MAX_PAGES_PER_QUERY,
    MIN_SEND_SPACING_NS,
    PAGE_KEY,
    ContractError,
    QueryPlan,
    canonical_json_bytes,
    date_text,
    exact_int,
    json_file_bytes,
    month_text,
    sha256_bytes,
    strict_json,
    text,
    validate_clock_domain,
    validate_key,
)


EXPERIMENT_ID = "exp_20260828_010"
VERSION = "JQUANTS_V2_BARS_MONTHLY_V4"
BOOTSTRAP_ROOT_RELATIVE = (
    "data/raw/jquants_v2_bars_monthly_v3/runs/"
    "exp_20260828_009_bootstrap_formal_001"
)
EXP009_POSTFLIGHT_RELATIVE = (
    "experiments/exp_20260828_009/artifacts/postflight_closure_manifest.json"
)
EXP009_POSTFLIGHT_SHA256 = (
    "d850789b436bb918b3c621085a0ea7cc76b4c9ba6ab3c7d5a2a60373ffae650a"
)
EXP009_ACQUISITION_MANIFEST_SHA256 = (
    "2b2ffae4f948124cb949c213d5c1ad34ac6e83ebdc3a1486bbf079fc221929df"
)
EXP009_RAW_TREE_SHA256 = (
    "5eaed53748fd46141987d37032b99a1f812ca35251f101beecbd25d3eead84f8"
)
EXP009_REGISTRY_SHA256 = (
    "5e1ac6c740d51281f3840fa998e14d315d3aba114ed4f5ba73aa11337af773bc"
)
EXP009_SESSION_ARTIFACT_SHA256 = (
    "6a0f03df72639931ec953739980e91bff72310953979cecc40c543e807ff5a4a"
)
EXP009_SESSION_LIST_SHA256 = (
    "e51e2d635155f34119e18bf4e9cc9d6640ae00c73b212ed67dd4f09e42ea90e5"
)
REUSE_DATES = ("2024-07-01", "2025-03-28", "2026-05-29")
SESSION_DATE_COUNT = 465
NETWORK_DATE_COUNT = 462
MONTH_COUNT = 23
BAR_RESPONSE_CAP_BYTES = 67_108_864
MONTH_RECEIPT_SCHEMA = "JQUANTS_V2_BARS_MONTHLY_V4_RECEIPT_V1"
MONTH_MANIFEST_SCHEMA = "JQUANTS_V2_BARS_MONTHLY_V4_MONTH_MANIFEST_V1"
MONTH_SOURCE_BINDING_SCHEMA = "JQUANTS_V2_BARS_MONTHLY_V4_MONTH_SOURCE_BINDING_V1"
BATCH_SOURCE_BINDING_SCHEMA = "JQUANTS_V2_BARS_MONTHLY_V4_BATCH_SOURCE_BINDING_V1"
GLOBAL_CATALOG_SCHEMA = "JQUANTS_V2_BARS_MONTHLY_V4_GLOBAL_CATALOG_V1"
BATCH_RE = re.compile(r"exp_20260828_010_monthly_formal_[0-9]{3}\Z", re.ASCII)
ATTEMPT_RE = re.compile(r"jquants-bars-[0-9]{6}-attempt[0-9]{3}\Z", re.ASCII)
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def validate_batch_id(value: object) -> str:
    if type(value) is not str or BATCH_RE.fullmatch(value) is None:
        raise ContractError("BATCH_ID")
    return value


def attempt_id_for(batch_id: str, month: str) -> str:
    validate_batch_id(batch_id)
    month_text(month)
    attempt = f"jquants-bars-{month.replace('-', '')}-attempt{batch_id[-3:]}"
    if ATTEMPT_RE.fullmatch(attempt) is None:
        raise ContractError("ATTEMPT_ID")
    return attempt


__all__ = [
    "API_BASE", "API_HOST", "API_KEY_ENV", "BAR_FIELDS", "BARS_PATH",
    "MAX_PAGES_PER_QUERY", "MIN_SEND_SPACING_NS", "PAGE_KEY", "ContractError",
    "QueryPlan", "canonical_json_bytes", "date_text", "exact_int",
    "json_file_bytes", "month_text", "sha256_bytes", "strict_json", "text",
    "validate_clock_domain", "validate_key",
]
