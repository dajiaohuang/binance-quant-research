from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from typing import Any

from ..jquants_v2_bars_monthly_v1 import loader as v1_loader
from ..jquants_v2_bars_monthly_v3.contracts import MONTH_PLAN_SCHEMA_VERSION
from ..jquants_v2_bars_monthly_v4 import monthly as v4_monthly
from ..jquants_v2_bars_monthly_v4 import source as v4_source
from ..jquants_v2_bars_monthly_v4.contracts import (
    API_HOST,
    API_KEY_ENV,
    BAR_RESPONSE_CAP_BYTES,
    BARS_PATH,
    MAX_PAGES_PER_QUERY,
    NETWORK_DATE_COUNT,
    PAGE_KEY,
    REUSE_DATES,
    SESSION_DATE_COUNT,
    ContractError,
    canonical_json_bytes,
    json_file_bytes,
    sha256_bytes,
    strict_json,
)
from .contracts import (
    BATCH_ID,
    EXPECTED_ADOPTED_DATES,
    EXPECTED_FIRST_NETWORK_DATE,
    EXPECTED_PARTIAL_RAW_TREE_SHA256,
    FAILED_ATTEMPT_ID,
    FAILED_BATCH_ID,
    FAILED_STAGING_RELATIVE,
    RECOVERY_ATTEMPT_ID,
    RECOVERY_CATALOG_SCHEMA,
    RECOVERY_REGISTRY_SCHEMA,
    VERSION,
    _RECOVERY_DOMAIN,
)


def _failed_root(repo_root: Path, override: Path | None = None) -> Path:
    if override is not None:
        return override
    expected = repo_root / FAILED_STAGING_RELATIVE
    try:
        resolved = expected.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ContractError("FAILED_STAGING_MISSING") from exc
    if resolved != expected.resolve(strict=True):
        raise ContractError("FAILED_STAGING_PATH")
    return expected


def _validate_failed_attempt(
    repo_root: Path,
    override: Path | None = None,
    *,
    require_frozen_partial_hash: bool = True,
) -> dict[str, object]:
    snapshot = v4_source._source_snapshot(repo_root)
    plan = snapshot.plans[0]
    root = _failed_root(repo_root, override)
    v4_monthly._strict_directory(root)
    reservation = strict_json(
        v4_monthly._safe_shard_file(root, "attempt.reservation.json").read_bytes()
    )
    if reservation != {
        "attempt_id": FAILED_ATTEMPT_ID,
        "batch_id": FAILED_BATCH_ID,
        "month": "2024-07",
    }:
        raise ContractError("FAILED_ATTEMPT_RESERVATION")
    for name in ("responses", "response_receipts", "date_manifests"):
        v4_monthly._strict_directory(root / name)

    receipt_paths = sorted((root / "response_receipts").glob("*.receipt.json"))
    if not receipt_paths:
        raise ContractError("NO_COMPLETE_LEAVES")
    rows_by_date: dict[str, list[Any]] = {}
    receipts_by_date: dict[str, list[dict[str, Any]]] = {}
    next_by_date: dict[str, str | None] = {}
    page_by_date: dict[str, int] = {}
    receipts: list[dict[str, Any]] = []
    raw_paths: set[str] = set()
    for request_ordinal, receipt_path in enumerate(receipt_paths, 1):
        receipt_relative_on_disk = receipt_path.relative_to(root).as_posix()
        receipt_raw = v4_monthly._safe_shard_file(root, receipt_relative_on_disk).read_bytes()
        receipt = strict_json(receipt_raw)
        v1_loader._exact_keys(
            receipt, v4_monthly.RECEIPT_KEYS, "RECOVERY_RECEIPT_SCHEMA"
        )
        if receipt["request_ordinal"] != request_ordinal:
            raise ContractError("RECOVERY_REQUEST_SEQUENCE")
        date_ordinal = receipt["date_ordinal"]
        if type(date_ordinal) is not int or not 1 <= date_ordinal <= len(plan.network_dates):
            raise ContractError("RECOVERY_DATE_ORDINAL")
        day = plan.network_dates[date_ordinal - 1]
        pages = page_by_date.get(day, 0) + 1
        if pages > MAX_PAGES_PER_QUERY:
            raise ContractError("RECOVERY_PAGE_CAP")
        prior = next_by_date.get(day)
        if pages > 1 and prior is None:
            raise ContractError("RECOVERY_PAGINATION_WITHOUT_PRIOR")
        expected_parameters = {"date": day}
        if prior is not None:
            expected_parameters[PAGE_KEY] = prior
        expected_raw = (
            f"responses/{date_ordinal:03d}_{day.replace('-', '')}_page_{pages:04d}.json"
        )
        expected_receipt = (
            f"response_receipts/{request_ordinal:04d}_{date_ordinal:03d}_"
            f"{day.replace('-', '')}_page_{pages:04d}.receipt.json"
        )
        if (
            receipt["attempt_id"] != FAILED_ATTEMPT_ID
            or receipt["batch_id"] != FAILED_BATCH_ID
            or receipt["month"] != "2024-07"
            or receipt["schema_version"] != v4_monthly.MONTH_RECEIPT_SCHEMA
            or receipt["experiment_version"] != v4_monthly.VERSION
            or receipt["api_host"] != API_HOST
            or receipt["path"] != BARS_PATH
            or receipt["parameters"] != expected_parameters
            or receipt["page_number"] != pages
            or receipt["plan_sha256"] != plan.plan_sha256
            or receipt["source_binding_sha256"] != snapshot.binding_sha256
            or receipt["cap_bytes"] != BAR_RESPONSE_CAP_BYTES
            or receipt["status"] != 200
            or receipt["redirected"] is not False
            or receipt["content_type"] not in v4_monthly.ALLOWED_CONTENT_TYPES
            or receipt["raw_relative_path"] != expected_raw
            or receipt["receipt_relative_path"] != expected_receipt
            or receipt_relative_on_disk != expected_receipt
        ):
            raise ContractError("RECOVERY_RECEIPT_BINDING")
        raw_path = v4_monthly._safe_shard_file(root, expected_raw)
        raw = raw_path.read_bytes()
        if len(raw) != receipt["body_bytes"] or sha256_bytes(raw) != receipt["body_sha256"]:
            raise ContractError("RECOVERY_RAW_BINDING")
        query = v4_monthly._query_plan(day, date_ordinal)
        parsed = v1_loader.parse_page(query, pages, receipt["received_at_ms"], raw)
        rows_by_date.setdefault(day, []).extend(parsed.bars)
        receipts_by_date.setdefault(day, []).append(receipt | {"_receipt_sha256": sha256_bytes(receipt_raw)})
        next_by_date[day] = parsed.next_key
        page_by_date[day] = pages
        receipts.append(receipt)
        raw_paths.add(expected_raw)

    if any(value is not None for value in next_by_date.values()):
        raise ContractError("RECOVERY_PARTIAL_PAGE_CHAIN")
    date_ordinals = [item["date_ordinal"] for item in receipts]
    unique_ordinals = tuple(dict.fromkeys(date_ordinals))
    if unique_ordinals != tuple(range(1, len(unique_ordinals) + 1)):
        raise ContractError("RECOVERY_NONCONTIGUOUS_PREFIX")
    adopted_dates = tuple(plan.network_dates[index - 1] for index in unique_ordinals)
    if adopted_dates != EXPECTED_ADOPTED_DATES:
        raise ContractError("RECOVERY_ADOPTED_DATE_SET")

    entries: list[dict[str, object]] = []
    for day in adopted_dates:
        rows = v4_monthly._validate_identity(rows_by_date.get(day, ()), day)
        expected_manifest = v4_monthly._date_summary(
            day, rows, receipts_by_date[day]
        )
        manifest_path = v4_monthly._safe_shard_file(
            root, f"date_manifests/{day}.json"
        )
        manifest_raw = manifest_path.read_bytes()
        if strict_json(manifest_raw) != expected_manifest:
            raise ContractError("RECOVERY_DATE_MANIFEST_BINDING")
        pages: list[dict[str, object]] = []
        for receipt in receipts_by_date[day]:
            pages.append(
                {
                    "body_bytes": receipt["body_bytes"],
                    "body_sha256": receipt["body_sha256"],
                    "page_number": receipt["page_number"],
                    "raw_relative_path": (
                        f"{FAILED_STAGING_RELATIVE}/{receipt['raw_relative_path']}"
                    ),
                    "receipt_relative_path": (
                        f"{FAILED_STAGING_RELATIVE}/{receipt['receipt_relative_path']}"
                    ),
                    "receipt_sha256": receipt["_receipt_sha256"],
                }
            )
        entries.append(
            {
                "date_manifest_relative_path": (
                    f"{FAILED_STAGING_RELATIVE}/date_manifests/{day}.json"
                ),
                "date_manifest_sha256": sha256_bytes(manifest_raw),
                "page_count": len(pages),
                "pages": pages,
                "row_count": len(rows),
                "session_date": day,
                "source_kind": "EXP010_ATTEMPT001_COMPLETE_DAY_POINTER",
            }
        )

    actual_raw = {
        path.relative_to(root).as_posix()
        for path in (root / "responses").glob("*.json")
    }
    actual_dates = {
        path.relative_to(root).as_posix()
        for path in (root / "date_manifests").glob("*.json")
    }
    if actual_raw != raw_paths or actual_dates != {
        f"date_manifests/{day}.json" for day in adopted_dates
    }:
        raise ContractError("RECOVERY_ORPHAN_OR_MISSING_FILE")
    raw_entries = sorted(
        [item["raw_relative_path"].split(FAILED_STAGING_RELATIVE + "/", 1)[1], item["body_sha256"], item["body_bytes"]]
        for entry in entries
        for item in entry["pages"]
    )
    partial_sha = sha256_bytes(canonical_json_bytes(raw_entries))
    if require_frozen_partial_hash and partial_sha != EXPECTED_PARTIAL_RAW_TREE_SHA256:
        raise ContractError("RECOVERY_PARTIAL_RAW_TREE_DRIFT")
    core: dict[str, object] = {
        "adopted_entries": entries,
        "failed_attempt_id": FAILED_ATTEMPT_ID,
        "failed_staging_relative": FAILED_STAGING_RELATIVE,
        "partial_raw_tree_sha256": partial_sha,
        "schema_version": RECOVERY_REGISTRY_SCHEMA,
        "source_binding_sha256": snapshot.binding_sha256,
    }
    document = core | {
        "binding_sha256": sha256_bytes(_RECOVERY_DOMAIN + canonical_json_bytes(core))
    }
    body = json_file_bytes(document)
    return {
        "document": document,
        "document_sha256": sha256_bytes(body),
        "snapshot": snapshot,
    }


def _recovery_plans(snapshot: Any, registry: dict[str, Any], registry_sha: str) -> tuple[Any, ...]:
    adopted = tuple(item["session_date"] for item in registry["adopted_entries"])
    first = snapshot.plans[0]
    if adopted != EXPECTED_ADOPTED_DATES or any(day not in first.network_dates for day in adopted):
        raise ContractError("RECOVERY_PLAN_ADOPTION")
    pointers = tuple(registry["adopted_entries"])
    reuse_entries = tuple(sorted(first.reuse_entries + pointers, key=lambda item: item["session_date"]))
    remaining = tuple(day for day in first.network_dates if day not in set(adopted))
    core = {
        "bootstrap_plan_sha256": first.projection()["bootstrap_plan_sha256"],
        "month": first.month,
        "network_dates": list(remaining),
        "registry_artifact_sha256": registry_sha,
        "reuse_entries": list(reuse_entries),
        "schema_version": MONTH_PLAN_SCHEMA_VERSION,
        "session_dates": list(first.session_dates),
    }
    recovered_first = replace(
        first,
        reuse_entries=reuse_entries,
        network_dates=remaining,
        registry_artifact_sha256=registry_sha,
        plan_sha256=sha256_bytes(canonical_json_bytes(core)),
    )
    plans = (recovered_first,) + snapshot.plans[1:]
    if (
        plans[0].network_dates[0] != EXPECTED_FIRST_NETWORK_DATE
        or set(adopted) & set(day for plan in plans for day in plan.network_dates)
        or sum(len(plan.network_dates) for plan in plans) != NETWORK_DATE_COUNT - len(adopted)
    ):
        raise ContractError("RECOVERY_DUPLICATE_OR_COVERAGE")
    return plans


def verify_recovery_preflight(repo_root: Path) -> dict[str, object]:
    result = _validate_failed_attempt(repo_root)
    registry = result["document"]
    plans = _recovery_plans(result["snapshot"], registry, result["document_sha256"])
    return {
        "adopted_dates": [item["session_date"] for item in registry["adopted_entries"]],
        "first_network_date": plans[0].network_dates[0],
        "network_date_count": sum(len(plan.network_dates) for plan in plans),
        "recovery_registry_sha256": result["document_sha256"],
        "source_binding_sha256": result["snapshot"].binding_sha256,
        "verdict": "PASS_POINTER_ADOPTION_NO_COPY_NO_DUPLICATE",
    }


def reserve_recovery_batch(repo_root: Path) -> dict[str, object]:
    result = _validate_failed_attempt(repo_root)
    v4_monthly.reserve_batch_and_emit_source_binding(repo_root, BATCH_ID)
    path = v4_monthly._raw_root(repo_root) / "batches" / f"{BATCH_ID}.recovery_registry.json"
    v4_monthly.write_once(path, json_file_bytes(result["document"]))
    ledger = v4_monthly._raw_root(repo_root) / "batches" / f"{BATCH_ID}.ledger.jsonl"
    v4_monthly._append(ledger, {"batch_id": BATCH_ID, "event": "RECOVERY_POINTER_REGISTRY_EMITTED"})
    return {
        "batch_id": BATCH_ID,
        "recovery_registry_sha256": result["document_sha256"],
        "verdict": "PASS_RECOVERY_BATCH_RESERVED",
    }


def _adopt_recovery_batch(repo_root: Path, result: dict[str, Any]) -> Path:
    ledger = v4_monthly._adopt_batch(repo_root, BATCH_ID, result["snapshot"])
    path = v4_monthly._raw_root(repo_root) / "batches" / f"{BATCH_ID}.recovery_registry.json"
    actual = v4_monthly._safe_shard_file(path.parent, path.name).read_bytes()
    if sha256_bytes(actual) != result["document_sha256"] or strict_json(actual) != result["document"]:
        raise ContractError("RECOVERY_BATCH_REGISTRY_BINDING")
    return ledger


def _completed_prefix(repo_root: Path, snapshot: Any, plans: tuple[Any, ...]) -> int:
    missing = False
    count = 0
    for plan in plans:
        final = v4_monthly._raw_root(repo_root) / "months" / plan.month / "final"
        if final.exists():
            if missing:
                raise ContractError("RECOVERY_NONCHRONOLOGICAL_SHARDS")
            v4_monthly.validate_month_shard(repo_root, final, snapshot, plan)
            count += 1
        else:
            missing = True
    return count


def dry_recovery_plan(repo_root: Path) -> dict[str, object]:
    result = _validate_failed_attempt(repo_root)
    plans = _recovery_plans(result["snapshot"], result["document"], result["document_sha256"])
    completed = _completed_prefix(repo_root, result["snapshot"], plans)
    return {
        "adopted_dates": list(EXPECTED_ADOPTED_DATES),
        "batch_id": BATCH_ID,
        "completed_immutable_month_prefix": completed,
        "first_network_date": plans[completed].network_dates[0] if completed < len(plans) else None,
        "key_reads": 0,
        "network_date_count": sum(len(plan.network_dates) for plan in plans),
        "network_requests": 0,
        "no_duplicate_adopted_dates": not bool(set(EXPECTED_ADOPTED_DATES) & set(day for plan in plans for day in plan.network_dates)),
        "recovery_attempt_id": RECOVERY_ATTEMPT_ID,
        "recovery_registry_sha256": result["document_sha256"],
        "version": VERSION,
    }


def _build_catalog(repo_root: Path, result: dict[str, Any], plans: tuple[Any, ...]) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for plan in plans:
        final = v4_monthly._raw_root(repo_root) / "months" / plan.month / "final"
        item = v4_monthly.validate_month_shard(repo_root, final, result["snapshot"], plan)
        entries.append({
            "manifest_sha256": item["manifest_sha256"],
            "month": plan.month,
            "network_date_count": item["network_date_count"],
            "raw_tree_sha256": item["raw_tree_sha256"],
            "relative_path": final.relative_to(repo_root).as_posix(),
            "request_count": item["request_count"],
            "row_count": item["row_count"],
        })
    network_count = sum(int(item["network_date_count"]) for item in entries)
    if len(entries) != 23 or network_count != NETWORK_DATE_COUNT - len(EXPECTED_ADOPTED_DATES):
        raise ContractError("RECOVERY_CATALOG_COVERAGE")
    core = {
        "adopted_date_count": len(EXPECTED_ADOPTED_DATES),
        "adopted_dates": list(EXPECTED_ADOPTED_DATES),
        "bootstrap_reuse_dates": list(REUSE_DATES),
        "entries": entries,
        "month_count": len(entries),
        "network_date_count": network_count,
        "recovery_registry_sha256": result["document_sha256"],
        "schema_version": RECOVERY_CATALOG_SCHEMA,
        "session_date_count": SESSION_DATE_COUNT,
        "source_binding_sha256": result["snapshot"].binding_sha256,
        "status": "COMPLETE_23_MONTHS_WITH_POINTER_ADOPTION",
    }
    body = json_file_bytes(core)
    catalog_sha = sha256_bytes(body)
    path = v4_monthly._raw_root(repo_root) / "catalogs" / f"recovery_catalog_{catalog_sha}.json"
    if path.exists():
        if path.read_bytes() != body:
            raise ContractError("RECOVERY_CATALOG_COLLISION")
    else:
        v4_monthly.write_once(path, body)
    return {
        "adopted_date_count": len(EXPECTED_ADOPTED_DATES),
        "catalog_relative_path": path.relative_to(repo_root).as_posix(),
        "catalog_sha256": catalog_sha,
        "month_count": len(entries),
        "network_date_count": network_count,
    }


def launch_formal(repo_root: Path, *, pre_reserved: bool = False, transport: Any = None) -> dict[str, object]:
    startup = _validate_failed_attempt(repo_root)
    plans = _recovery_plans(startup["snapshot"], startup["document"], startup["document_sha256"])
    if plans[0].network_dates[0] != EXPECTED_FIRST_NETWORK_DATE:
        raise ContractError("RECOVERY_FIRST_NETWORK_DATE")
    if not pre_reserved:
        reserve_recovery_batch(repo_root)
    ledger = _adopt_recovery_batch(repo_root, startup)
    key = os.environ.pop(API_KEY_ENV, "")
    if not key:
        v4_monthly._append(ledger, {"batch_id": BATCH_ID, "error_code": "API_KEY_MISSING", "event": "STOPPED_FIRST_FAILURE"})
        raise ContractError("API_KEY_MISSING")
    active_transport = transport or v4_monthly.HttpsTransport()
    try:
        completed = _completed_prefix(repo_root, startup["snapshot"], plans)
        for plan in plans[completed:]:
            current = _validate_failed_attempt(repo_root)
            if current["document_sha256"] != startup["document_sha256"] or current["snapshot"].binding_sha256 != startup["snapshot"].binding_sha256:
                raise ContractError("RECOVERY_SOURCE_DRIFT")
            current_plans = _recovery_plans(current["snapshot"], current["document"], current["document_sha256"])
            current_plan = next(item for item in current_plans if item.month == plan.month)
            if current_plan.projection() != plan.projection():
                raise ContractError("RECOVERY_PLAN_DRIFT")
            staging, final, attempt_id = v4_monthly._reserve_month(repo_root, BATCH_ID, plan.month)
            v4_monthly._collect_month(
                repo_root, current["snapshot"], current_plan, staging, BATCH_ID,
                attempt_id, key, transport=active_transport,
            )
            v4_monthly.validate_month_shard(repo_root, staging, current["snapshot"], current_plan)
            v4_monthly._publish(staging, final)
            v4_monthly._append(ledger, {"attempt_id": attempt_id, "batch_id": BATCH_ID, "event": "MONTH_PUBLISHED", "month": plan.month})
        catalog = _build_catalog(repo_root, startup, plans)
        v4_monthly._append(ledger, {"batch_id": BATCH_ID, "catalog_sha256": catalog["catalog_sha256"], "event": "RECOVERY_CATALOG_PUBLISHED"})
        return catalog | {"batch_id": BATCH_ID, "status": "COMPLETE"}
    except BaseException as exc:
        code = exc.code if isinstance(exc, ContractError) else type(exc).__name__
        v4_monthly._append(ledger, {"batch_id": BATCH_ID, "error_code": code, "event": "STOPPED_FIRST_FAILURE"})
        raise
    finally:
        key = ""


__all__ = ["dry_recovery_plan", "launch_formal", "verify_recovery_preflight"]
