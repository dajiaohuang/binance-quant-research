from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quant_research.alpha_models.data.jquants_v2_v4.contracts import (
    GLOBAL_HTTP_CAP,
    MAX_PAGES,
    PAGE_KEY,
    PLAN_SHA256,
    QUERY_PLANS,
    canonical_json_bytes,
    exact_int,
    json_file_bytes,
    sha256_bytes,
    strict_json,
)
from quant_research.alpha_models.data.jquants_v2_v4.loader import (
    DailyBar,
    MasterRow,
    merge_and_validate,
    parse_page,
)


RECOVERY_EXPERIMENT_ID = "exp_20260828_006"
SOURCE_EXPERIMENT_ID = "exp_20260828_005"
SOURCE_RUN_ID = "exp_20260828_005_formal_001"
SOURCE_STAGING_RELATIVE = Path(
    "data/raw/jquants_v2_v4/runs/.exp_20260828_005_formal_001.staging"
)
SOURCE_CONTROL_RELATIVE = Path(
    "data/raw/jquants_v2_v4/runs/.exp_20260828_005_formal_001.control"
)
SOURCE_FINAL_RELATIVE = Path(
    "data/raw/jquants_v2_v4/runs/exp_20260828_005_formal_001"
)
SOURCE_FORMAL_CONTROL_RELATIVE = Path("experiments/exp_20260828_005/formal_control")
EXPECTED_RAW_TREE_SHA256 = "d14273cc49e9de82b0295e9ab76db8c01eea02e5f4e0af940400b64550b8209c"
EXPECTED_FREEZE_MANIFEST_SHA256 = "75c22f6a0ef8f46e2e24514603261a07a3b6437ae64f62523e6f6c50b10a37e8"
PACING_STATUS = "RUNTIME_MONOTONIC_GUARD_INFERRED_NOT_REPLAYABLE"
ARTIFACT_STATE = "JQUANTS_V2_FREE_SOURCE_PROBE_OFFLINE_RECOVERED"
TERMINAL_STATUS = "NEEDS_MORE_DATA"

EXPECTED_TOP = {
    "responses",
    "response_receipts",
    "query_plan.json",
    "receipts.jsonl",
    "acquisition_manifest.json",
    "summary.json",
}
RECEIPT_KEYS = {
    "body_bytes",
    "body_sha256",
    "content_type",
    "http_ordinal",
    "page_number",
    "path",
    "prior_pagination_key_sha256",
    "query_id",
    "query_ordinal",
    "request_parameters_sha256",
    "sent_at_ms",
    "received_at_ms",
    "status",
    "redirect_count",
    "pacing_wait_ms",
}
RAW_ENTRY_KEYS = {"bytes", "http_ordinal", "page_number", "path", "query_ordinal", "sha256"}


class RecoveryError(ValueError):
    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code.isascii() and code else "RECOVERY_ERROR"
        super().__init__(safe)
        self.code = safe


@dataclass(frozen=True)
class RecoveryResult:
    recovery_manifest: dict[str, object]
    source_summary: dict[str, object]
    adapter_pointer: dict[str, object]

    def bundle(self) -> dict[str, object]:
        return {
            "adapter_pointer": self.adapter_pointer,
            "recovery_manifest": self.recovery_manifest,
            "source_summary": self.source_summary,
        }


def _canonical_document(path: Path) -> tuple[bytes, dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise RecoveryError("SOURCE_FILE_TYPE")
    raw = path.read_bytes()
    document = strict_json(raw)
    if raw != json_file_bytes(document):
        raise RecoveryError("SOURCE_NOT_CANONICAL")
    return raw, document


def _canonical_compact_document(path: Path) -> tuple[bytes, dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise RecoveryError("SOURCE_FILE_TYPE")
    raw = path.read_bytes()
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise RecoveryError("SOURCE_NOT_CANONICAL")
    document = strict_json(raw[:-1])
    if raw != canonical_json_bytes(document) + b"\n":
        raise RecoveryError("SOURCE_NOT_CANONICAL")
    return raw, document


def _canonical_jsonl(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    if not path.is_file() or path.is_symlink():
        raise RecoveryError("SOURCE_FILE_TYPE")
    raw = path.read_bytes()
    if not raw or not raw.endswith(b"\n") or b"\r" in raw:
        raise RecoveryError("SOURCE_JSONL")
    rows = [strict_json(line) for line in raw[:-1].split(b"\n")]
    if raw != b"".join(canonical_json_bytes(row) + b"\n" for row in rows):
        raise RecoveryError("SOURCE_JSONL_CANONICAL")
    return raw, rows


def _inside_repo(repo_root: Path, relative: Path) -> Path:
    root = repo_root.resolve(strict=True)
    candidate = root / relative
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RecoveryError("SOURCE_OUTSIDE_REPOSITORY") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RecoveryError("SOURCE_SYMLINK")
    return candidate


def _exact_source_controls(repo_root: Path) -> dict[str, object]:
    control = _inside_repo(repo_root, SOURCE_CONTROL_RELATIVE)
    if {path.name for path in control.iterdir()} != {"failure.json", "lease.json"}:
        raise RecoveryError("CONTROL_TREE")
    failure_raw, failure = _canonical_document(control / "failure.json")
    lease_raw, lease = _canonical_document(control / "lease.json")
    if failure != {
        "experiment_id": SOURCE_EXPERIMENT_ID,
        "failure_code": "RATE_PACING",
        "run_id": SOURCE_RUN_ID,
    }:
        raise RecoveryError("CONTROL_FAILURE")
    if lease != {
        "expected_freeze_manifest_sha256": EXPECTED_FREEZE_MANIFEST_SHA256,
        "experiment_id": SOURCE_EXPERIMENT_ID,
        "run_id": SOURCE_RUN_ID,
    }:
        raise RecoveryError("CONTROL_LEASE")
    final = repo_root.resolve(strict=True) / SOURCE_FINAL_RELATIVE
    authorization = control / "authorization.json"
    if final.exists() or authorization.exists():
        raise RecoveryError("FORBIDDEN_PROMOTION_STATE")

    formal_control = _inside_repo(repo_root, SOURCE_FORMAL_CONTROL_RELATIVE)
    reservation_raw, reservation = _canonical_compact_document(
        formal_control / f"{SOURCE_RUN_ID}.reservation.lock"
    )
    if reservation != {"experiment_id": SOURCE_EXPERIMENT_ID, "run_id": SOURCE_RUN_ID}:
        raise RecoveryError("RESERVATION_BINDING")
    ledger_raw, ledger = _canonical_jsonl(formal_control / f"{SOURCE_RUN_ID}.stage_ledger.jsonl")
    expected_ledger = [
        {"event": "PASS", "exit_code": None, "seq": 1, "stage": "SELF_HASH"},
        {"event": "START", "exit_code": None, "seq": 2, "stage": "FREEZE_PREFLIGHT"},
        {"event": "PASS", "exit_code": None, "seq": 3, "stage": "FREEZE_PREFLIGHT"},
        {"event": "START", "exit_code": None, "seq": 4, "stage": "ENV_FILE_READ"},
        {"event": "PASS", "exit_code": None, "seq": 5, "stage": "ENV_FILE_READ"},
        {"event": "PASS", "exit_code": None, "seq": 6, "stage": "VALIDATE"},
        {"event": "START", "exit_code": None, "seq": 7, "stage": "COLLECTOR"},
        {"event": "EXIT", "exit_code": 20, "seq": 8, "stage": "COLLECTOR"},
        {"event": "PASS", "exit_code": None, "seq": 9, "stage": "FINAL_CLEANUP"},
    ]
    if ledger != expected_ledger:
        raise RecoveryError("LEDGER_BINDING")
    return {
        "authorization_present": False,
        "control_present": True,
        "failure_code": "RATE_PACING",
        "failure_sha256": sha256_bytes(failure_raw),
        "final_present": False,
        "lease_sha256": sha256_bytes(lease_raw),
        "ledger_sha256": sha256_bytes(ledger_raw),
        "reservation_sha256": sha256_bytes(reservation_raw),
        "staging_present": True,
    }


def validate_source_probe(repo_root: Path) -> RecoveryResult:
    if not isinstance(repo_root, Path):
        raise RecoveryError("REPOSITORY_ROOT")
    root = repo_root.resolve(strict=True)
    staging = _inside_repo(root, SOURCE_STAGING_RELATIVE)
    if not staging.is_dir() or {path.name for path in staging.iterdir()} != EXPECTED_TOP:
        raise RecoveryError("STAGING_TREE")
    controls = _exact_source_controls(root)

    plan_raw, plan = _canonical_document(staging / "query_plan.json")
    expected_plan = {
        "global_http_cap": GLOBAL_HTTP_CAP,
        "logical_query_count": 5,
        "max_pages_per_query": MAX_PAGES,
        "plan_sha256": PLAN_SHA256,
        "queries": [item.projection() for item in QUERY_PLANS],
        "retry_count": 0,
    }
    if plan != expected_plan:
        raise RecoveryError("QUERY_PLAN_BINDING")

    manifest_raw, manifest = _canonical_document(staging / "acquisition_manifest.json")
    expected_manifest_keys = {
        "experiment_id",
        "http_request_count",
        "logical_query_count",
        "plan_sha256",
        "raw_files",
        "raw_tree_sha256",
        "retry_count",
        "run_id",
        "version",
    }
    if set(manifest) != expected_manifest_keys:
        raise RecoveryError("ACQUISITION_MANIFEST_KEYS")
    if (
        manifest["experiment_id"] != SOURCE_EXPERIMENT_ID
        or manifest["run_id"] != SOURCE_RUN_ID
        or manifest["logical_query_count"] != 5
        or manifest["http_request_count"] != 5
        or manifest["retry_count"] != 0
        or manifest["plan_sha256"] != PLAN_SHA256
        or manifest["raw_tree_sha256"] != EXPECTED_RAW_TREE_SHA256
    ):
        raise RecoveryError("ACQUISITION_MANIFEST_BINDING")

    receipts_raw, receipts = _canonical_jsonl(staging / "receipts.jsonl")
    raw_files = manifest["raw_files"]
    if type(raw_files) is not list or len(raw_files) != 5 or len(receipts) != 5:
        raise RecoveryError("SOURCE_COUNT")
    if any(type(row) is not dict or set(row) != RAW_ENTRY_KEYS for row in raw_files):
        raise RecoveryError("RAW_ENTRY")
    if any(type(row) is not dict or set(row) != RECEIPT_KEYS for row in receipts):
        raise RecoveryError("RECEIPT_KEYS")
    by_path = {str(row["path"]): row for row in raw_files}
    if len(by_path) != 5:
        raise RecoveryError("RAW_PATH_DUPLICATE")

    response_dir = staging / "responses"
    sidecar_dir = staging / "response_receipts"
    if response_dir.is_symlink() or sidecar_dir.is_symlink():
        raise RecoveryError("SOURCE_SYMLINK")
    response_paths = tuple(response_dir.iterdir())
    sidecar_paths = tuple(sidecar_dir.iterdir())
    if any(not path.is_file() or path.is_symlink() for path in response_paths + sidecar_paths):
        raise RecoveryError("SOURCE_FILE_TYPE")
    actual_response_paths = {path.relative_to(staging).as_posix() for path in response_paths}
    if actual_response_paths != set(by_path) or len(sidecar_paths) != 5:
        raise RecoveryError("RAW_BIJECTION")

    pages = []
    wall_clock_gaps: list[int] = []
    pacing_waits: list[int] = []
    previous_sent: int | None = None
    source_files: list[dict[str, object]] = []
    for ordinal, (query, receipt) in enumerate(zip(QUERY_PLANS, receipts, strict=True), 1):
        expected_path = f"responses/{ordinal:02d}_{query.query_id}_page_0001.json"
        if (
            receipt["http_ordinal"] != ordinal
            or receipt["query_ordinal"] != ordinal
            or receipt["query_id"] != query.query_id
            or receipt["page_number"] != 1
            or receipt["path"] != expected_path
            or receipt["prior_pagination_key_sha256"] is not None
            or receipt["request_parameters_sha256"]
            != sha256_bytes(canonical_json_bytes(dict(query.parameters)))
            or receipt["status"] != 200
            or receipt["content_type"] != "application/json"
            or receipt["redirect_count"] != 0
        ):
            raise RecoveryError("RECEIPT_BINDING")
        sent = exact_int(receipt["sent_at_ms"], "SENT")
        received = exact_int(receipt["received_at_ms"], "RECEIVED")
        wait = exact_int(receipt["pacing_wait_ms"], "PACING_WAIT")
        if sent > received:
            raise RecoveryError("RECEIPT_CLOCK_ORDER")
        if previous_sent is not None:
            wall_clock_gaps.append(sent - previous_sent)
        previous_sent = sent
        pacing_waits.append(wait)

        entry = by_path[expected_path]
        if (
            entry["http_ordinal"] != ordinal
            or entry["query_ordinal"] != ordinal
            or entry["page_number"] != 1
        ):
            raise RecoveryError("RAW_ENTRY_BINDING")
        raw_path = staging / expected_path
        raw = raw_path.read_bytes()
        digest = sha256_bytes(raw)
        if (
            len(raw) != entry["bytes"]
            or digest != entry["sha256"]
            or len(raw) != receipt["body_bytes"]
            or digest != receipt["body_sha256"]
            or len(raw) > query.cap_bytes
        ):
            raise RecoveryError("RAW_HASH_BINDING")

        sidecar_name = (
            f"{ordinal:04d}_{ordinal:02d}_{query.query_id}_page_0001.receipt.json"
        )
        sidecar_raw, sidecar = _canonical_document(sidecar_dir / sidecar_name)
        if sidecar != receipt:
            raise RecoveryError("RECEIPT_SIDECAR_BINDING")
        page = parse_page(
            query,
            page_number=1,
            status=200,
            body=raw,
            received_at_ms=received,
        )
        if page.pagination_key is not None:
            raise RecoveryError("UNEXPECTED_PAGINATION")
        pages.append(page)
        source_files.append(
            {
                "body_bytes": len(raw),
                "body_sha256": digest,
                "path": expected_path,
                "query_id": query.query_id,
                "receipt_sha256": sha256_bytes(sidecar_raw),
            }
        )

    ordered_entries = sorted(raw_files, key=lambda item: str(item["path"]).encode("utf-8"))
    if sha256_bytes(canonical_json_bytes(ordered_entries)) != EXPECTED_RAW_TREE_SHA256:
        raise RecoveryError("RAW_TREE_BINDING")
    loaded = merge_and_validate(pages)
    if loaded.page_count != 5 or loaded.http_count != 5:
        raise RecoveryError("LOADED_PAGE_COUNT")

    summary_raw, source_summary_document = _canonical_document(staging / "summary.json")
    expected_source_summary = {
        "artifact_state": "JQUANTS_V2_FREE_SOURCE_PROBE_ACQUIRED",
        "bar_rows": 4414,
        "calendar_rows": 4,
        "empirical_authorized": False,
        "experiment_id": SOURCE_EXPERIMENT_ID,
        "historical_eligibility_ready": False,
        "http_request_count": 5,
        "logical_query_count": 5,
        "master_rows": 1,
        "raw_tree_sha256": EXPECTED_RAW_TREE_SHA256,
        "retry_count": 0,
        "run_id": SOURCE_RUN_ID,
        "strict_eligible_count": 0,
        "terminal_status": TERMINAL_STATUS,
    }
    if source_summary_document != expected_source_summary:
        raise RecoveryError("SOURCE_SUMMARY_BINDING")

    calendar = pages[0].records
    q02 = pages[1].records
    q03 = pages[2].records
    q04 = pages[3].records
    q05 = pages[4].records
    if not all(isinstance(row, MasterRow) for row in q02 + q03):
        raise RecoveryError("MASTER_SCHEMA")
    if not all(isinstance(row, DailyBar) for row in q04 + q05):
        raise RecoveryError("BAR_SCHEMA")
    q04_bars = tuple(row for row in q04 if isinstance(row, DailyBar))
    q05_bars = tuple(row for row in q05 if isinstance(row, DailyBar))
    merged_bars = loaded.bars
    master_identity_fields = (
        "snapshot_date",
        "raw_code",
        "symbol",
        "company_name",
        "company_name_en",
        "sector17_code",
        "sector17_name",
        "sector33_code",
        "sector33_name",
        "scale_category",
        "market_code",
        "market_name",
        "margin_code",
        "margin_name",
        "product_category",
    )
    master_identity_equal = tuple(
        getattr(q02[0], field) for field in master_identity_fields
    ) == tuple(getattr(q03[0], field) for field in master_identity_fields)
    if not master_identity_equal:
        raise RecoveryError("MASTER_MAPPING")

    source_summary = {
        "artifact_state": ARTIFACT_STATE,
        "calendar": {
            "civil_date_rows": len(calendar),
            "non_session_rows": sum(not row.is_session for row in calendar),
            "preregistered_holiday_pattern_matches": True,
            "session_rows": sum(row.is_session for row in calendar),
        },
        "daily_bars": {
            "merged_distinct_rows": len(merged_bars),
            "q04_distinct_symbols": len({row.symbol for row in q04_bars}),
            "q04_null_ohlc_rows": sum(not row.traded for row in q04_bars),
            "q04_rows": len(q04_bars),
            "q05_expected_session_coverage_matches": True,
            "q05_rows": len(q05_bars),
            "split_factor_and_ex_right_expectation_matches": True,
        },
        "listing_presence": "UNKNOWN",
        "master": {
            "normal_query_rows": len(q02),
            "nontrading_query_rows": len(q03),
            "nontrading_requested_date_mapped_to_next_business_date": True,
            "product_category_expectation_matches": True,
            "sector17_nonempty": bool(q02[0].sector17_code),
            "sector33_nonempty": bool(q02[0].sector33_code),
            "unique_merged_rows": len(loaded.masters),
        },
        "source_experiment_id": SOURCE_EXPERIMENT_ID,
        "source_run_id": SOURCE_RUN_ID,
        "terminal_status": TERMINAL_STATUS,
    }
    recovery_manifest = {
        "artifact_state": ARTIFACT_STATE,
        "authorization": {
            "backtest_authorized": False,
            "empirical_authorized": False,
            "historical_eligibility_ready": False,
            "ic_authorized": False,
            "listing_presence": "UNKNOWN",
            "pnl_authorized": False,
            "strict_eligible_count": 0,
            "training_authorized": False,
        },
        "control_state": controls,
        "experiment_id": RECOVERY_EXPERIMENT_ID,
        "pacing": {
            "minimum_request_spacing_ms_preregistered": 13_000,
            "receipt_wait_ms": pacing_waits,
            "status": PACING_STATUS,
            "wall_clock_send_gap_ms": wall_clock_gaps,
        },
        "source_binding": {
            "acquisition_manifest_sha256": sha256_bytes(manifest_raw),
            "query_plan_sha256": sha256_bytes(plan_raw),
            "raw_tree_sha256": EXPECTED_RAW_TREE_SHA256,
            "receipts_jsonl_sha256": sha256_bytes(receipts_raw),
            "source_experiment_id": SOURCE_EXPERIMENT_ID,
            "source_run_id": SOURCE_RUN_ID,
            "source_staging_path": SOURCE_STAGING_RELATIVE.as_posix(),
            "source_summary_sha256": sha256_bytes(summary_raw),
        },
        "source_files": source_files,
        "terminal_status": TERMINAL_STATUS,
        "validation": {
            "all_five_queries_semantically_valid": True,
            "all_raw_hashes_match": True,
            "direct_http_200_json_no_redirect": True,
            "http_request_count": 5,
            "pagination_key_count": 0,
            "raw_receipt_bijection": True,
            "receipt_count": 5,
            "retry_count": 0,
            "schema_valid": True,
        },
    }
    adapter_pointer = {
        "adapter_callable": (
            "quant_research.alpha_models.data.jquants_v2_recovery."
            "validate_source_probe"
        ),
        "artifact_state": ARTIFACT_STATE,
        "authorization_ceiling": "SOURCE_PROBE_ONLY_NEEDS_MORE_DATA",
        "experiment_id": RECOVERY_EXPERIMENT_ID,
        "licensed_raw_policy": "LOCAL_GITIGNORED_SOURCE_PATH_ONLY",
        "pacing_status": PACING_STATUS,
        "raw_tree_sha256": EXPECTED_RAW_TREE_SHA256,
        "source_experiment_id": SOURCE_EXPERIMENT_ID,
        "source_run_id": SOURCE_RUN_ID,
        "source_staging_path": SOURCE_STAGING_RELATIVE.as_posix(),
    }
    return RecoveryResult(recovery_manifest, source_summary, adapter_pointer)
