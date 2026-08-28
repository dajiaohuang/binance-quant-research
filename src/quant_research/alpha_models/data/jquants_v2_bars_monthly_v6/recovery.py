from __future__ import annotations

from dataclasses import replace
import hashlib
import http.client
import os
from pathlib import Path
import re
import ssl
from typing import Any, Callable, Mapping

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
from ..jquants_v2_bars_monthly_v5 import recovery as v5_recovery
from ..jquants_v2_bars_monthly_v5.contracts import FAILED_STAGING_RELATIVE
from .contracts import (
    BATCH_ID,
    CATALOG_SCHEMA,
    EXPECTED_ADOPTED_COUNT,
    EXPECTED_FIRST_NETWORK_DATE,
    EXPECTED_REMAINING_NETWORK_COUNT,
    REGISTRY_SCHEMA,
    VERSION,
    _DOMAIN,
)


STAGING_RE = re.compile(r"\.jquants-bars-([0-9]{6})-attempt([0-9]{3})\.staging\Z", re.ASCII)


class PersistentHttpsTransport:
    """One verified HTTPS connection per month; never retries a request."""

    def __init__(
        self,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._factory = connection_factory or (
            lambda: http.client.HTTPSConnection(
                API_HOST, timeout=60, context=ssl.create_default_context()
            )
        )
        self._connection: Any | None = None
        self.closed = False

    def request(
        self, host: str, path_and_query: str, headers: Mapping[str, str]
    ) -> Any:
        if host != API_HOST or not path_and_query.startswith("/v2/equities/bars/daily?"):
            raise ContractError("PERSISTENT_TRANSPORT_ALLOWLIST")
        if self.closed:
            raise ContractError("PERSISTENT_TRANSPORT_CLOSED")
        if self._connection is None:
            self._connection = self._factory()
        try:
            self._connection.request("GET", path_and_query, headers=dict(headers))
            return self._connection.getresponse()
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        connection, self._connection = self._connection, None
        self.closed = True
        if connection is not None:
            connection.close()


def _extract_day_entries(
    repo_root: Path,
    root: Path,
    snapshot: Any,
    plan: Any,
    expected_attempt_id: str,
    expected_batch_id: str,
) -> tuple[dict[str, object], ...]:
    v4_monthly._strict_directory(root)
    reservation = strict_json(
        v4_monthly._safe_shard_file(root, "attempt.reservation.json").read_bytes()
    )
    if reservation != {
        "attempt_id": expected_attempt_id,
        "batch_id": expected_batch_id,
        "month": plan.month,
    }:
        raise ContractError("GENERIC_ADOPTION_RESERVATION")
    for name in ("responses", "response_receipts", "date_manifests"):
        v4_monthly._strict_directory(root / name)
    receipt_paths = sorted((root / "response_receipts").glob("*.receipt.json"))
    if not receipt_paths:
        raise ContractError("GENERIC_ADOPTION_EMPTY")
    rows_by_date: dict[str, list[Any]] = {}
    receipts_by_date: dict[str, list[dict[str, Any]]] = {}
    next_by_date: dict[str, str | None] = {}
    pages_by_date: dict[str, int] = {}
    raw_seen: set[str] = set()
    receipts: list[dict[str, Any]] = []
    for request_ordinal, receipt_path in enumerate(receipt_paths, 1):
        receipt_relative = receipt_path.relative_to(root).as_posix()
        receipt_raw = v4_monthly._safe_shard_file(root, receipt_relative).read_bytes()
        receipt = strict_json(receipt_raw)
        v1_loader._exact_keys(receipt, v4_monthly.RECEIPT_KEYS, "GENERIC_RECEIPT_SCHEMA")
        date_ordinal = receipt["date_ordinal"]
        if type(date_ordinal) is not int or not 1 <= date_ordinal <= len(plan.network_dates):
            raise ContractError("GENERIC_DATE_ORDINAL")
        day = plan.network_dates[date_ordinal - 1]
        page = pages_by_date.get(day, 0) + 1
        prior = next_by_date.get(day)
        if page > MAX_PAGES_PER_QUERY or (page > 1 and prior is None):
            raise ContractError("GENERIC_PAGE_CHAIN")
        parameters = {"date": day}
        if prior is not None:
            parameters[PAGE_KEY] = prior
        raw_relative = f"responses/{date_ordinal:03d}_{day.replace('-', '')}_page_{page:04d}.json"
        expected_receipt = f"response_receipts/{request_ordinal:04d}_{date_ordinal:03d}_{day.replace('-', '')}_page_{page:04d}.receipt.json"
        if (
            receipt["request_ordinal"] != request_ordinal
            or receipt["attempt_id"] != expected_attempt_id
            or receipt["batch_id"] != expected_batch_id
            or receipt["month"] != plan.month
            or receipt["schema_version"] != v4_monthly.MONTH_RECEIPT_SCHEMA
            or receipt["experiment_version"] != v4_monthly.VERSION
            or receipt["api_host"] != API_HOST
            or receipt["path"] != BARS_PATH
            or receipt["parameters"] != parameters
            or receipt["page_number"] != page
            or receipt["plan_sha256"] != plan.plan_sha256
            or receipt["source_binding_sha256"] != snapshot.binding_sha256
            or receipt["cap_bytes"] != BAR_RESPONSE_CAP_BYTES
            or receipt["status"] != 200
            or receipt["redirected"] is not False
            or receipt["content_type"] not in v4_monthly.ALLOWED_CONTENT_TYPES
            or receipt["raw_relative_path"] != raw_relative
            or receipt["receipt_relative_path"] != expected_receipt
            or receipt_relative != expected_receipt
        ):
            raise ContractError("GENERIC_RECEIPT_BINDING")
        raw = v4_monthly._safe_shard_file(root, raw_relative).read_bytes()
        if len(raw) != receipt["body_bytes"] or sha256_bytes(raw) != receipt["body_sha256"]:
            raise ContractError("GENERIC_RAW_BINDING")
        parsed = v1_loader.parse_page(
            v4_monthly._query_plan(day, date_ordinal),
            page,
            receipt["received_at_ms"],
            raw,
        )
        rows_by_date.setdefault(day, []).extend(parsed.bars)
        receipts_by_date.setdefault(day, []).append(
            receipt | {"_receipt_sha256": sha256_bytes(receipt_raw)}
        )
        next_by_date[day] = parsed.next_key
        pages_by_date[day] = page
        raw_seen.add(raw_relative)
        receipts.append(receipt)
    if any(value is not None for value in next_by_date.values()):
        raise ContractError("GENERIC_PARTIAL_PAGE_CHAIN")
    ordinals = tuple(dict.fromkeys(int(item["date_ordinal"]) for item in receipts))
    if ordinals != tuple(range(1, len(ordinals) + 1)):
        raise ContractError("GENERIC_NONCONTIGUOUS_PREFIX")
    dates = tuple(plan.network_dates[index - 1] for index in ordinals)
    entries: list[dict[str, object]] = []
    source_root = root.relative_to(repo_root).as_posix()
    for day in dates:
        rows = v4_monthly._validate_identity(rows_by_date.get(day, ()), day)
        expected_manifest = v4_monthly._date_summary(day, rows, receipts_by_date[day])
        manifest_path = v4_monthly._safe_shard_file(root, f"date_manifests/{day}.json")
        manifest_raw = manifest_path.read_bytes()
        if strict_json(manifest_raw) != expected_manifest:
            raise ContractError("GENERIC_DATE_MANIFEST_BINDING")
        pages = [
            {
                "body_bytes": receipt["body_bytes"],
                "body_sha256": receipt["body_sha256"],
                "page_number": receipt["page_number"],
                "raw_relative_path": f"{source_root}/{receipt['raw_relative_path']}",
                "receipt_relative_path": f"{source_root}/{receipt['receipt_relative_path']}",
                "receipt_sha256": receipt["_receipt_sha256"],
            }
            for receipt in receipts_by_date[day]
        ]
        entries.append({
            "date_manifest_relative_path": f"{source_root}/date_manifests/{day}.json",
            "date_manifest_sha256": sha256_bytes(manifest_raw),
            "page_count": len(pages),
            "pages": pages,
            "row_count": len(rows),
            "session_date": day,
            "source_kind": "PRIOR_COMPLETE_DAY_POINTER",
        })
    actual_raw = {path.relative_to(root).as_posix() for path in (root / "responses").glob("*.json")}
    actual_manifests = {path.relative_to(root).as_posix() for path in (root / "date_manifests").glob("*.json")}
    if actual_raw != raw_seen or actual_manifests != {f"date_manifests/{day}.json" for day in dates}:
        raise ContractError("GENERIC_ORPHAN_OR_MISSING")
    return tuple(entries)


def _registry_from_entries(snapshot: Any, entries: list[dict[str, object]]) -> dict[str, Any]:
    ordered = sorted(entries, key=lambda item: item["session_date"])
    dates = [item["session_date"] for item in ordered]
    if len(dates) != len(set(dates)):
        raise ContractError("GENERIC_DUPLICATE_OR_AMBIGUOUS_DATE")
    if len(ordered) != EXPECTED_ADOPTED_COUNT:
        raise ContractError("GENERIC_ADOPTED_COUNT")
    core: dict[str, object] = {
        "adopted_entries": ordered,
        "schema_version": REGISTRY_SCHEMA,
        "source_binding_sha256": snapshot.binding_sha256,
    }
    document = core | {
        "binding_sha256": sha256_bytes(_DOMAIN + canonical_json_bytes(core))
    }
    body = json_file_bytes(document)
    return {
        "document": document,
        "document_sha256": sha256_bytes(body),
        "snapshot": snapshot,
    }


def _prior_state(repo_root: Path) -> dict[str, Any]:
    snapshot = v4_source._source_snapshot(repo_root)
    attempt1 = v5_recovery._validate_failed_attempt(repo_root)
    v5_plans = v5_recovery._recovery_plans(
        snapshot, attempt1["document"], attempt1["document_sha256"]
    )
    entries = [dict(item) for item in attempt1["document"]["adopted_entries"]]
    for item in entries:
        item["source_kind"] = "PRIOR_COMPLETE_DAY_POINTER"

    raw_root = v4_monthly._raw_root(repo_root)
    for month_root in sorted((raw_root / "months").glob("????-??")):
        final = month_root / "final"
        if not final.exists():
            continue
        manifest = strict_json(v4_monthly._safe_shard_file(final, "month_manifest.json").read_bytes())
        if manifest.get("batch_id") == BATCH_ID:
            continue
        month = month_root.name
        plan = next(item for item in v5_plans if item.month == month)
        v4_monthly.validate_month_shard(repo_root, final, snapshot, plan)
        entries.extend(
            _extract_day_entries(
                repo_root, final, snapshot, plan,
                manifest["attempt_id"], manifest["batch_id"],
            )
        )

    for staging in sorted((raw_root / "months").glob("????-??/.*.staging")):
        if staging.as_posix().endswith(FAILED_STAGING_RELATIVE.replace("data/raw/jquants_v2_bars_monthly_v4/", "")):
            continue
        match = STAGING_RE.fullmatch(staging.name)
        if match is None or match.group(2) == "003":
            continue
        reservation = strict_json(v4_monthly._safe_shard_file(staging, "attempt.reservation.json").read_bytes())
        ledger = v4_monthly._safe_shard_file(staging, "attempt_ledger.jsonl").read_text("utf-8")
        if "STOPPED_FIRST_FAILURE" not in ledger:
            raise ContractError("GENERIC_STAGING_NOT_FAILED")
        plan = next(item for item in v5_plans if item.month == reservation["month"])
        entries.extend(
            _extract_day_entries(
                repo_root, staging, snapshot, plan,
                reservation["attempt_id"], reservation["batch_id"],
            )
        )

    state = _registry_from_entries(snapshot, entries)
    state["v5_plans"] = v5_plans
    return state


def _plans(state: dict[str, Any]) -> tuple[Any, ...]:
    adopted_by_month: dict[str, list[dict[str, object]]] = {}
    for entry in state["document"]["adopted_entries"]:
        adopted_by_month.setdefault(entry["session_date"][:7], []).append(entry)
    output = [state["v5_plans"][0]]
    for original in state["snapshot"].plans[1:]:
        adopted = tuple(adopted_by_month.get(original.month, ()))
        adopted_dates = {item["session_date"] for item in adopted}
        if any(day not in original.network_dates for day in adopted_dates):
            raise ContractError("GENERIC_ADOPTION_OUTSIDE_PLAN")
        network = tuple(day for day in original.network_dates if day not in adopted_dates)
        reuse = tuple(sorted(original.reuse_entries + adopted, key=lambda item: item["session_date"]))
        core = {
            "bootstrap_plan_sha256": original.projection()["bootstrap_plan_sha256"],
            "month": original.month,
            "network_dates": list(network),
            "registry_artifact_sha256": state["document_sha256"],
            "reuse_entries": list(reuse),
            "schema_version": MONTH_PLAN_SCHEMA_VERSION,
            "session_dates": list(original.session_dates),
        }
        output.append(replace(
            original,
            network_dates=network,
            reuse_entries=reuse,
            registry_artifact_sha256=state["document_sha256"],
            plan_sha256=sha256_bytes(canonical_json_bytes(core)),
        ))
    return tuple(output)


def _completed_prefix(repo_root: Path, state: dict[str, Any], plans: tuple[Any, ...]) -> int:
    missing = False
    count = 0
    for plan in plans:
        final = v4_monthly._raw_root(repo_root) / "months" / plan.month / "final"
        if final.exists():
            if missing:
                raise ContractError("GENERIC_NONCHRONOLOGICAL_FINALS")
            v4_monthly.validate_month_shard(repo_root, final, state["snapshot"], plan)
            count += 1
        else:
            missing = True
    return count


def dry_recovery_plan(repo_root: Path) -> dict[str, object]:
    state = _prior_state(repo_root)
    plans = _plans(state)
    completed = _completed_prefix(repo_root, state, plans)
    remaining = tuple(day for plan in plans[completed:] for day in plan.network_dates)
    adopted = tuple(item["session_date"] for item in state["document"]["adopted_entries"])
    if (
        len(remaining) != EXPECTED_REMAINING_NETWORK_COUNT
        or remaining[0] != EXPECTED_FIRST_NETWORK_DATE
        or set(remaining) & set(adopted)
    ):
        raise ContractError("GENERIC_DRY_PLAN_DUPLICATE_OR_BOUNDARY")
    return {
        "adopted_network_date_count": len(adopted),
        "batch_id": BATCH_ID,
        "completed_immutable_month_prefix": completed,
        "first_network_date": remaining[0],
        "key_reads": 0,
        "network_requests": 0,
        "no_overlap": True,
        "recovery_registry_sha256": state["document_sha256"],
        "remaining_network_date_count": len(remaining),
        "version": VERSION,
    }


def verify_recovery_preflight(repo_root: Path) -> dict[str, object]:
    return dry_recovery_plan(repo_root) | {"verdict": "PASS_GENERIC_ADOPTION_NO_DUPLICATE"}


def reserve_recovery_batch(repo_root: Path) -> dict[str, object]:
    state = _prior_state(repo_root)
    plans = _plans(state)
    completed = _completed_prefix(repo_root, state, plans)
    remaining = tuple(day for plan in plans[completed:] for day in plan.network_dates)
    if not remaining or remaining[0] != EXPECTED_FIRST_NETWORK_DATE or len(remaining) != EXPECTED_REMAINING_NETWORK_COUNT:
        raise ContractError("GENERIC_RESERVATION_PLAN")
    v4_monthly.reserve_batch_and_emit_source_binding(repo_root, BATCH_ID)
    path = v4_monthly._raw_root(repo_root) / "batches" / f"{BATCH_ID}.operational_registry.json"
    v4_monthly.write_once(path, json_file_bytes(state["document"]))
    ledger = v4_monthly._raw_root(repo_root) / "batches" / f"{BATCH_ID}.ledger.jsonl"
    v4_monthly._append(ledger, {"batch_id": BATCH_ID, "event": "GENERIC_POINTER_REGISTRY_EMITTED"})
    return {"batch_id": BATCH_ID, "recovery_registry_sha256": state["document_sha256"], "verdict": "PASS_RESERVED"}


def _adopt_batch(repo_root: Path, state: dict[str, Any]) -> Path:
    ledger = v4_monthly._adopt_batch(repo_root, BATCH_ID, state["snapshot"])
    path = v4_monthly._raw_root(repo_root) / "batches" / f"{BATCH_ID}.operational_registry.json"
    raw = v4_monthly._safe_shard_file(path.parent, path.name).read_bytes()
    if sha256_bytes(raw) != state["document_sha256"] or strict_json(raw) != state["document"]:
        raise ContractError("GENERIC_BATCH_REGISTRY_BINDING")
    return ledger


def _build_catalog(repo_root: Path, state: dict[str, Any], plans: tuple[Any, ...]) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for plan in plans:
        final = v4_monthly._raw_root(repo_root) / "months" / plan.month / "final"
        item = v4_monthly.validate_month_shard(repo_root, final, state["snapshot"], plan)
        entries.append({
            "manifest_sha256": item["manifest_sha256"], "month": plan.month,
            "network_date_count": item["network_date_count"],
            "raw_tree_sha256": item["raw_tree_sha256"],
            "relative_path": final.relative_to(repo_root).as_posix(),
            "request_count": item["request_count"], "row_count": item["row_count"],
        })
    shard_dates = sum(int(item["network_date_count"]) for item in entries)
    if len(entries) != 23 or shard_dates != 451:
        raise ContractError("OPERATIONAL_CATALOG_COVERAGE")
    core = {
        "adopted_pointer_date_count": EXPECTED_ADOPTED_COUNT,
        "bootstrap_reuse_dates": list(REUSE_DATES),
        "entries": entries,
        "external_pointer_date_count": 11,
        "month_count": 23,
        "network_date_count": NETWORK_DATE_COUNT,
        "recovery_registry_sha256": state["document_sha256"],
        "schema_version": CATALOG_SCHEMA,
        "session_date_count": SESSION_DATE_COUNT,
        "shard_network_date_count": shard_dates,
        "source_binding_sha256": state["snapshot"].binding_sha256,
        "status": "COMPLETE_23_MONTHS_GENERIC_POINTER_ADOPTION",
    }
    body = json_file_bytes(core)
    catalog_sha = sha256_bytes(body)
    path = v4_monthly._raw_root(repo_root) / "catalogs" / f"operational_catalog_{catalog_sha}.json"
    if path.exists():
        if path.read_bytes() != body:
            raise ContractError("OPERATIONAL_CATALOG_COLLISION")
    else:
        v4_monthly.write_once(path, body)
    return {"catalog_relative_path": path.relative_to(repo_root).as_posix(), "catalog_sha256": catalog_sha, "month_count": 23, "network_date_count": NETWORK_DATE_COUNT}


def launch_formal(repo_root: Path, *, pre_reserved: bool = False) -> dict[str, object]:
    startup = _prior_state(repo_root)
    plans = _plans(startup)
    completed = _completed_prefix(repo_root, startup, plans)
    remaining = tuple(day for plan in plans[completed:] for day in plan.network_dates)
    if not remaining or remaining[0] != EXPECTED_FIRST_NETWORK_DATE or len(remaining) != EXPECTED_REMAINING_NETWORK_COUNT:
        raise ContractError("OPERATIONAL_FIRST_NETWORK_DATE")
    if not pre_reserved:
        reserve_recovery_batch(repo_root)
    ledger = _adopt_batch(repo_root, startup)
    key = os.environ.pop(API_KEY_ENV, "")
    if not key:
        v4_monthly._append(ledger, {"batch_id": BATCH_ID, "error_code": "API_KEY_MISSING", "event": "STOPPED_FIRST_FAILURE"})
        raise ContractError("API_KEY_MISSING")
    try:
        for plan in plans[completed:]:
            current = _prior_state(repo_root)
            if current["document_sha256"] != startup["document_sha256"] or current["snapshot"].binding_sha256 != startup["snapshot"].binding_sha256:
                raise ContractError("OPERATIONAL_SOURCE_DRIFT")
            current_plan = next(item for item in _plans(current) if item.month == plan.month)
            if current_plan.projection() != plan.projection():
                raise ContractError("OPERATIONAL_PLAN_DRIFT")
            staging, final, attempt_id = v4_monthly._reserve_month(repo_root, BATCH_ID, plan.month)
            transport = PersistentHttpsTransport()
            try:
                v4_monthly._collect_month(
                    repo_root, current["snapshot"], current_plan, staging,
                    BATCH_ID, attempt_id, key, transport=transport,
                )
            finally:
                transport.close()
            v4_monthly.validate_month_shard(repo_root, staging, current["snapshot"], current_plan)
            v4_monthly._publish(staging, final)
            v4_monthly._append(ledger, {"attempt_id": attempt_id, "batch_id": BATCH_ID, "event": "MONTH_PUBLISHED", "month": plan.month})
        catalog = _build_catalog(repo_root, startup, plans)
        v4_monthly._append(ledger, {"batch_id": BATCH_ID, "catalog_sha256": catalog["catalog_sha256"], "event": "OPERATIONAL_CATALOG_PUBLISHED"})
        return catalog | {"batch_id": BATCH_ID, "status": "COMPLETE"}
    except BaseException as exc:
        code = exc.code if isinstance(exc, ContractError) else type(exc).__name__
        v4_monthly._append(ledger, {"batch_id": BATCH_ID, "error_code": code, "event": "STOPPED_FIRST_FAILURE"})
        raise
    finally:
        key = ""


__all__ = ["PersistentHttpsTransport", "dry_recovery_plan", "launch_formal", "verify_recovery_preflight"]
