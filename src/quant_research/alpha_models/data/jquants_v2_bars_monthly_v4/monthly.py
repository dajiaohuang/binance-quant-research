from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import time
from typing import Any, Callable, Iterable, Mapping

from ..jquants_v2_bars_monthly_v1 import collector as v1_collector
from ..jquants_v2_bars_monthly_v1 import loader as v1_loader
from .contracts import (
    API_BASE,
    API_HOST,
    API_KEY_ENV,
    BAR_RESPONSE_CAP_BYTES,
    BARS_PATH,
    BATCH_SOURCE_BINDING_SCHEMA,
    EXP009_ACQUISITION_MANIFEST_SHA256,
    EXP009_REGISTRY_SHA256,
    GLOBAL_CATALOG_SCHEMA,
    MAX_PAGES_PER_QUERY,
    MIN_SEND_SPACING_NS,
    MONTH_COUNT,
    MONTH_MANIFEST_SCHEMA,
    MONTH_RECEIPT_SCHEMA,
    MONTH_SOURCE_BINDING_SCHEMA,
    NETWORK_DATE_COUNT,
    PAGE_KEY,
    REUSE_DATES,
    SESSION_DATE_COUNT,
    VERSION,
    ContractError,
    QueryPlan,
    attempt_id_for,
    canonical_json_bytes,
    exact_int,
    json_file_bytes,
    sha256_bytes,
    strict_json,
    text,
    validate_batch_id,
    validate_clock_domain,
    validate_key,
)
from .source import _SourceSnapshot, _source_snapshot


Transport = v1_collector.Transport
HttpsTransport = v1_collector.HttpsTransport
write_once = v1_collector.write_once
ALLOWED_CONTENT_TYPES = v1_collector.ALLOWED_CONTENT_TYPES

RECEIPT_KEYS = frozenset(
    (
        "api_host", "attempt_id", "batch_id", "body_bytes", "body_sha256",
        "cap_bytes", "clock_domain_id", "content_type", "date_ordinal",
        "deadline_monotonic_ns", "experiment_version", "guard_base_monotonic_ns",
        "month", "page_number", "parameters", "path", "plan_sha256",
        "post_wait_monotonic_ns", "pre_wait_monotonic_ns",
        "previous_send_monotonic_ns", "raw_relative_path", "received_at_ms",
        "received_at_utc", "receipt_relative_path", "redirected",
        "request_ordinal", "requested_wait_ns", "schema_version",
        "send_monotonic_ns", "sent_at_utc", "source_binding_sha256",
        "spacing_ns", "status",
    )
)
MONTH_MANIFEST_KEYS = frozenset(
    (
        "attempt_id", "batch_id", "files", "month", "month_plan_sha256",
        "network_date_count", "raw_tree_sha256", "request_count",
        "reuse_date_count", "schema_version", "source_binding_sha256", "status",
    )
)
MONTH_SUMMARY_KEYS = frozenset(
    (
        "attempt_id", "batch_id", "date_count", "month", "null_bar_count",
        "pacing", "raw_tree_sha256", "request_count", "reuse_entries",
        "row_count", "source_binding_sha256", "traded_count",
    )
)


def _raw_root(repo_root: Path) -> Path:
    return repo_root / "data/raw/jquants_v2_bars_monthly_v4"


def _utc_iso(value_ns: int) -> str:
    return (
        datetime.fromtimestamp(value_ns / 1_000_000_000, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _append(path: Path, event: Mapping[str, Any]) -> None:
    with path.open("ab") as stream:
        stream.write(canonical_json_bytes(dict(event)) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _batch_binding(snapshot: _SourceSnapshot, batch_id: str) -> dict[str, object]:
    return {
        "batch_id": validate_batch_id(batch_id),
        "schema_version": BATCH_SOURCE_BINDING_SCHEMA,
        "source": snapshot.binding_document,
        "source_binding_sha256": snapshot.binding_sha256,
    }


def reserve_batch_and_emit_source_binding(repo_root: Path, batch_id: str) -> dict[str, object]:
    snapshot = _source_snapshot(repo_root)
    batch_id = validate_batch_id(batch_id)
    batches = _raw_root(repo_root) / "batches"
    write_once(
        batches / f"{batch_id}.reservation.lock",
        json_file_bytes({"batch_id": batch_id, "version": VERSION}),
    )
    binding = _batch_binding(snapshot, batch_id)
    write_once(batches / f"{batch_id}.source_binding.json", json_file_bytes(binding))
    write_once(
        batches / f"{batch_id}.ledger.jsonl",
        canonical_json_bytes({"batch_id": batch_id, "event": "RESERVED_SOURCE_BOUND"})
        + b"\n",
    )
    return {
        "batch_id": batch_id,
        "source_binding_sha256": snapshot.binding_sha256,
        "verdict": "PASS_BATCH_RESERVED_SOURCE_BOUND",
    }


def _adopt_batch(repo_root: Path, batch_id: str, snapshot: _SourceSnapshot) -> Path:
    batches = _raw_root(repo_root) / "batches"
    expected = _batch_binding(snapshot, batch_id)
    reservation = v1_loader._safe_file(batches, f"{batch_id}.reservation.lock")
    if strict_json(reservation.read_bytes()) != {"batch_id": batch_id, "version": VERSION}:
        raise ContractError("BATCH_RESERVATION_BINDING")
    binding = v1_loader._safe_file(batches, f"{batch_id}.source_binding.json")
    if strict_json(binding.read_bytes()) != expected:
        raise ContractError("BATCH_SOURCE_BINDING")
    ledger = v1_loader._safe_file(batches, f"{batch_id}.ledger.jsonl")
    return ledger


def _month_paths(repo_root: Path, month: str, attempt_id: str) -> tuple[Path, Path, Path]:
    month_root = _raw_root(repo_root) / "months" / month
    return (
        month_root / f".{attempt_id}.staging",
        month_root / "final",
        month_root / "attempts" / f"{attempt_id}.reservation.json",
    )


def _reserve_month(repo_root: Path, batch_id: str, month: str) -> tuple[Path, Path, str]:
    attempt_id = attempt_id_for(batch_id, month)
    staging, final, reservation = _month_paths(repo_root, month, attempt_id)
    if final.exists() or staging.exists():
        raise ContractError("MONTH_ATTEMPT_OR_FINAL_EXISTS")
    write_once(
        reservation,
        json_file_bytes(
            {"attempt_id": attempt_id, "batch_id": batch_id, "month": month}
        ),
    )
    staging.mkdir(parents=True, exist_ok=False)
    write_once(
        staging / "attempt.reservation.json",
        json_file_bytes(
            {"attempt_id": attempt_id, "batch_id": batch_id, "month": month}
        ),
    )
    write_once(
        staging / "attempt_ledger.jsonl",
        canonical_json_bytes(
            {"attempt_id": attempt_id, "batch_id": batch_id, "event": "RESERVED"}
        )
        + b"\n",
    )
    return staging, final, attempt_id


def _query_plan(day: str, ordinal: int) -> QueryPlan:
    return QueryPlan(
        ordinal,
        f"D{ordinal:03d}_{day.replace('-', '')}",
        BARS_PATH,
        {"date": day},
        "ALL_MARKET_DATE_ONLY_EXACT_FREE18",
        BAR_RESPONSE_CAP_BYTES,
    )


def _month_source_binding(snapshot: _SourceSnapshot, plan: Any) -> dict[str, object]:
    return {
        "month": plan.month,
        "month_plan_sha256": plan.plan_sha256,
        "schema_version": MONTH_SOURCE_BINDING_SCHEMA,
        "source": snapshot.binding_document,
        "source_binding_sha256": snapshot.binding_sha256,
    }


def _request_parameters(day: str, page_key: str | None) -> dict[str, str]:
    result = {"date": day}
    if page_key is not None:
        result[PAGE_KEY] = page_key
    return result


def _validate_identity(rows: Iterable[Any], day: str) -> tuple[Any, ...]:
    output = tuple(rows)
    if not output or any(item.session_date != day for item in output):
        raise ContractError("DATE_EMPTY_OR_MISMATCH")
    identities = tuple((item.session_date, item.raw_code) for item in output)
    if identities != tuple(sorted(set(identities))):
        raise ContractError("BAR_ORDER_OR_DUPLICATE")
    return output


def _date_summary(day: str, rows: tuple[Any, ...], pages: list[dict[str, Any]]) -> dict[str, object]:
    traded = sum(item.traded for item in rows)
    identities = [(item.session_date, item.raw_code) for item in rows]
    return {
        "date": day,
        "first_code": rows[0].raw_code,
        "identity_sha256": sha256_bytes(canonical_json_bytes(identities)),
        "last_code": rows[-1].raw_code,
        "null_bar_count": len(rows) - traded,
        "pages": [
            {
                "body_bytes": item["body_bytes"],
                "body_sha256": item["body_sha256"],
                "page_number": item["page_number"],
                "raw_relative_path": item["raw_relative_path"],
                "receipt_relative_path": item["receipt_relative_path"],
            }
            for item in pages
        ],
        "row_count": len(rows),
        "traded_count": traded,
    }


def _receipt_timing(receipts: list[dict[str, Any]], attempt_id: str) -> dict[str, object]:
    if not receipts:
        raise ContractError("NO_RECEIPTS")
    guard = exact_int(receipts[0].get("guard_base_monotonic_ns"), "GUARD_BASE")
    domain = validate_clock_domain(receipts[0].get("clock_domain_id"))
    previous: int | None = None
    sends: list[int] = []
    for ordinal, receipt in enumerate(receipts, 1):
        v1_loader._exact_keys(receipt, RECEIPT_KEYS, "MONTH_RECEIPT_SCHEMA")
        if (
            receipt["attempt_id"] != attempt_id
            or receipt["request_ordinal"] != ordinal
            or receipt["clock_domain_id"] != domain
            or receipt["guard_base_monotonic_ns"] != guard
            or receipt["schema_version"] != MONTH_RECEIPT_SCHEMA
        ):
            raise ContractError("MONTH_RECEIPT_AUTHORITY")
        spacing = exact_int(receipt["spacing_ns"], "SPACING", 1)
        if spacing != MIN_SEND_SPACING_NS:
            raise ContractError("SPACING_POLICY")
        expected_previous = previous
        if receipt["previous_send_monotonic_ns"] != expected_previous:
            raise ContractError("PREVIOUS_SEND")
        deadline = (guard if previous is None else previous) + spacing
        if receipt["deadline_monotonic_ns"] != deadline:
            raise ContractError("DEADLINE")
        pre = exact_int(receipt["pre_wait_monotonic_ns"], "PRE_WAIT")
        if receipt["requested_wait_ns"] != max(0, deadline - pre):
            raise ContractError("REQUESTED_WAIT")
        post = exact_int(receipt["post_wait_monotonic_ns"], "POST_WAIT")
        sent = exact_int(receipt["send_monotonic_ns"], "SEND")
        if post < deadline or sent < post or sent - (guard if previous is None else previous) < spacing:
            raise ContractError("SPACING_SHORT")
        previous = sent
        sends.append(sent)
    v1_loader.validate_rolling_five_per_minute(sends)
    return {
        "clock_domain_id": domain,
        "first_request_full_cooldown": True,
        "request_count": len(receipts),
        "spacing_ns": MIN_SEND_SPACING_NS,
    }


def _collect_month(
    repo_root: Path,
    snapshot: _SourceSnapshot,
    plan: Any,
    staging: Path,
    batch_id: str,
    attempt_id: str,
    api_key: str,
    *,
    transport: Transport,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    sleep_seconds: Callable[[float], None] = time.sleep,
    utc_ns: Callable[[], int] = time.time_ns,
) -> dict[str, object]:
    key = validate_key(api_key)
    source_binding = _month_source_binding(snapshot, plan)
    write_once(staging / "source_binding.json", json_file_bytes(source_binding))
    write_once(staging / "month_plan.json", json_file_bytes(plan.projection()))
    ledger = staging / "attempt_ledger.jsonl"
    guard = monotonic_ns()
    domain = v1_collector._clock_domain(attempt_id, guard)
    pacer = v1_collector.MonotonicPacer(
        attempt_id, guard, domain, monotonic_ns, sleep_seconds
    )
    receipts: list[dict[str, Any]] = []
    date_summaries: list[dict[str, object]] = []
    request_ordinal = 0
    total_rows = 0
    try:
        for date_ordinal, day in enumerate(plan.network_dates, 1):
            query = _query_plan(day, date_ordinal)
            prior_key: str | None = None
            parsed_pages = []
            date_receipts: list[dict[str, Any]] = []
            for page_number in range(1, MAX_PAGES_PER_QUERY + 1):
                request_ordinal += 1
                timing = pacer.wait_to_send()
                sent_utc_ns = utc_ns()
                response = transport.request(
                    API_HOST,
                    v1_collector._request_path(query, prior_key),
                    {"x-api-key": key, "Accept": "application/json"},
                )
                received_utc_ns = utc_ns()
                body = v1_collector._read_capped(response, query.cap_bytes)
                raw_relative = (
                    f"responses/{date_ordinal:03d}_{day.replace('-', '')}_"
                    f"page_{page_number:04d}.json"
                )
                receipt_relative = (
                    f"response_receipts/{request_ordinal:04d}_{date_ordinal:03d}_"
                    f"{day.replace('-', '')}_page_{page_number:04d}.receipt.json"
                )
                # The body and safe receipt are durable before any HTTP/schema decision.
                write_once(staging / raw_relative, body)
                content_type = (
                    (response.getheader("Content-Type", "") or "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                redirected = (
                    300 <= response.status < 400
                    or response.getheader("Location") is not None
                )
                receipt = {
                    "api_host": API_HOST,
                    "attempt_id": attempt_id,
                    "batch_id": batch_id,
                    "body_bytes": len(body),
                    "body_sha256": sha256_bytes(body),
                    "cap_bytes": query.cap_bytes,
                    "clock_domain_id": domain,
                    "content_type": content_type,
                    "date_ordinal": date_ordinal,
                    **timing,
                    "experiment_version": VERSION,
                    "guard_base_monotonic_ns": guard,
                    "month": plan.month,
                    "page_number": page_number,
                    "parameters": _request_parameters(day, prior_key),
                    "path": BARS_PATH,
                    "plan_sha256": plan.plan_sha256,
                    "raw_relative_path": raw_relative,
                    "received_at_ms": received_utc_ns // 1_000_000,
                    "received_at_utc": _utc_iso(received_utc_ns),
                    "receipt_relative_path": receipt_relative,
                    "redirected": redirected,
                    "request_ordinal": request_ordinal,
                    "schema_version": MONTH_RECEIPT_SCHEMA,
                    "sent_at_utc": _utc_iso(sent_utc_ns),
                    "source_binding_sha256": snapshot.binding_sha256,
                    "status": response.status,
                }
                write_once(staging / receipt_relative, json_file_bytes(receipt))
                receipts.append(receipt)
                date_receipts.append(receipt)
                if redirected:
                    raise ContractError("REDIRECT")
                if response.status != 200:
                    raise ContractError(f"HTTP_{response.status}")
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise ContractError("CONTENT_TYPE")
                parsed = v1_loader.parse_page(
                    query, page_number, receipt["received_at_ms"], body
                )
                parsed_pages.append(parsed)
                if parsed.next_key is None:
                    break
                prior_key = parsed.next_key
            else:
                raise ContractError("PAGE_CAP")
            rows = _validate_identity(
                (row for parsed in parsed_pages for row in parsed.bars), day
            )
            summary = _date_summary(day, rows, date_receipts)
            write_once(
                staging / "date_manifests" / f"{day}.json", json_file_bytes(summary)
            )
            date_summaries.append(summary)
            total_rows += len(rows)
        pacing = _receipt_timing(receipts, attempt_id)
        _append(
            ledger,
            {
                "attempt_id": attempt_id,
                "event": "COLLECTED_SOURCE_BOUND_AND_VALIDATED",
                "request_count": request_ordinal,
            },
        )
        raw_entries = sorted(
            (
                item["raw_relative_path"],
                item["body_sha256"],
                item["body_bytes"],
            )
            for item in receipts
        )
        raw_tree_sha = sha256_bytes(canonical_json_bytes(raw_entries))
        summary = {
            "attempt_id": attempt_id,
            "batch_id": batch_id,
            "date_count": len(plan.network_dates),
            "month": plan.month,
            "null_bar_count": sum(int(item["null_bar_count"]) for item in date_summaries),
            "pacing": pacing,
            "raw_tree_sha256": raw_tree_sha,
            "request_count": request_ordinal,
            "reuse_entries": list(plan.reuse_entries),
            "row_count": total_rows,
            "source_binding_sha256": snapshot.binding_sha256,
            "traded_count": sum(int(item["traded_count"]) for item in date_summaries),
        }
        write_once(staging / "summary.json", json_file_bytes(summary))
        files: list[dict[str, object]] = []
        for path in sorted(
            item
            for item in staging.rglob("*")
            if item.is_file() and item.name != "month_manifest.json"
        ):
            raw = path.read_bytes()
            files.append(
                {
                    "bytes": len(raw),
                    "relative_path": path.relative_to(staging).as_posix(),
                    "sha256": sha256_bytes(raw),
                }
            )
        manifest = {
            "attempt_id": attempt_id,
            "batch_id": batch_id,
            "files": files,
            "month": plan.month,
            "month_plan_sha256": plan.plan_sha256,
            "network_date_count": len(plan.network_dates),
            "raw_tree_sha256": raw_tree_sha,
            "request_count": request_ordinal,
            "reuse_date_count": len(plan.reuse_entries),
            "schema_version": MONTH_MANIFEST_SCHEMA,
            "source_binding_sha256": snapshot.binding_sha256,
            "status": "IMMUTABLE_MONTH_SHARD_VALIDATED",
        }
        write_once(staging / "month_manifest.json", json_file_bytes(manifest))
        return summary
    except BaseException as exc:
        code = exc.code if isinstance(exc, ContractError) else type(exc).__name__
        _append(
            ledger,
            {
                "attempt_id": attempt_id,
                "error_code": code,
                "event": "STOPPED_FIRST_FAILURE",
                "request_count": request_ordinal,
            },
        )
        raise
    finally:
        key = ""


def _strict_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ContractError("DIRECTORY_MISSING") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ContractError("UNTRUSTED_DIRECTORY")


def _safe_shard_file(root: Path, relative: str) -> Path:
    path = v1_loader._safe_file(root, relative)
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ContractError("SHARD_PATH") from exc
    if resolved.parent == resolved_root:
        return path
    if resolved_root not in resolved.parents:
        raise ContractError("SHARD_PATH_ESCAPE")
    current = path.parent
    while current != root:
        _strict_directory(current)
        current = current.parent
    return path


def validate_month_shard(
    repo_root: Path, root: Path, snapshot: _SourceSnapshot, plan: Any
) -> dict[str, Any]:
    _strict_directory(root)
    if strict_json(_safe_shard_file(root, "month_plan.json").read_bytes()) != plan.projection():
        raise ContractError("MONTH_PLAN_BINDING")
    if strict_json(_safe_shard_file(root, "source_binding.json").read_bytes()) != _month_source_binding(snapshot, plan):
        raise ContractError("MONTH_SOURCE_BINDING")
    manifest_raw = _safe_shard_file(root, "month_manifest.json").read_bytes()
    manifest = strict_json(manifest_raw)
    v1_loader._exact_keys(manifest, MONTH_MANIFEST_KEYS, "MONTH_MANIFEST_SCHEMA")
    if (
        manifest.get("schema_version") != MONTH_MANIFEST_SCHEMA
        or manifest.get("status") != "IMMUTABLE_MONTH_SHARD_VALIDATED"
        or manifest.get("month") != plan.month
        or manifest.get("month_plan_sha256") != plan.plan_sha256
        or manifest.get("source_binding_sha256") != snapshot.binding_sha256
        or manifest.get("network_date_count") != len(plan.network_dates)
        or manifest.get("reuse_date_count") != len(plan.reuse_entries)
    ):
        raise ContractError("MONTH_MANIFEST_AUTHORITY")
    files = manifest.get("files")
    if type(files) is not list:
        raise ContractError("MONTH_MANIFEST_FILES")
    seen: set[str] = set()
    for entry in files:
        row = v1_loader._exact_keys(
            entry, frozenset(("bytes", "relative_path", "sha256")), "MONTH_FILE_SCHEMA"
        )
        relative = row["relative_path"]
        if relative in seen or relative == "month_manifest.json":
            raise ContractError("MONTH_FILE_DUPLICATE")
        seen.add(relative)
        raw = _safe_shard_file(root, relative).read_bytes()
        if len(raw) != row["bytes"] or sha256_bytes(raw) != row["sha256"]:
            raise ContractError("MONTH_FILE_HASH")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "month_manifest.json"
    }
    if actual != seen:
        raise ContractError("MONTH_FILE_SET")

    receipt_dir = root / "response_receipts"
    _strict_directory(receipt_dir)
    receipt_paths = sorted(receipt_dir.glob("*.receipt.json"))
    receipts: list[dict[str, Any]] = []
    rows_by_date: dict[str, list[Any]] = {day: [] for day in plan.network_dates}
    next_by_date: dict[str, str | None] = {day: None for day in plan.network_dates}
    page_by_date: dict[str, int] = {day: 0 for day in plan.network_dates}
    attempt_id = text(manifest.get("attempt_id"), "ATTEMPT_ID")
    batch_id = validate_batch_id(manifest.get("batch_id"))
    if attempt_id != attempt_id_for(batch_id, plan.month):
        raise ContractError("ATTEMPT_BINDING")
    for request_ordinal, path in enumerate(receipt_paths, 1):
        receipt = strict_json(path.read_bytes())
        v1_loader._exact_keys(receipt, RECEIPT_KEYS, "MONTH_RECEIPT_SCHEMA")
        date_ordinal = exact_int(receipt["date_ordinal"], "DATE_ORDINAL", 1)
        if date_ordinal > len(plan.network_dates):
            raise ContractError("DATE_ORDINAL")
        day = plan.network_dates[date_ordinal - 1]
        page_by_date[day] += 1
        page = page_by_date[day]
        prior = next_by_date[day]
        if page > 1 and prior is None:
            raise ContractError("PAGINATION_WITHOUT_PRIOR")
        expected_raw = f"responses/{date_ordinal:03d}_{day.replace('-', '')}_page_{page:04d}.json"
        expected_receipt = f"response_receipts/{request_ordinal:04d}_{date_ordinal:03d}_{day.replace('-', '')}_page_{page:04d}.receipt.json"
        if (
            receipt["attempt_id"] != attempt_id
            or receipt["batch_id"] != batch_id
            or receipt["month"] != plan.month
            or receipt["request_ordinal"] != request_ordinal
            or receipt["page_number"] != page
            or receipt["parameters"] != _request_parameters(day, prior)
            or receipt["path"] != BARS_PATH
            or receipt["api_host"] != API_HOST
            or receipt["plan_sha256"] != plan.plan_sha256
            or receipt["source_binding_sha256"] != snapshot.binding_sha256
            or receipt["experiment_version"] != VERSION
            or receipt["raw_relative_path"] != expected_raw
            or receipt["receipt_relative_path"] != expected_receipt
            or path.relative_to(root).as_posix() != expected_receipt
            or receipt["cap_bytes"] != BAR_RESPONSE_CAP_BYTES
            or receipt["redirected"] is not False
            or receipt["status"] != 200
            or receipt["content_type"] not in ALLOWED_CONTENT_TYPES
        ):
            raise ContractError("MONTH_RECEIPT_BINDING")
        raw = _safe_shard_file(root, expected_raw).read_bytes()
        if len(raw) != receipt["body_bytes"] or sha256_bytes(raw) != receipt["body_sha256"]:
            raise ContractError("MONTH_RAW_BINDING")
        query = _query_plan(day, date_ordinal)
        parsed = v1_loader.parse_page(query, page, receipt["received_at_ms"], raw)
        rows_by_date[day].extend(parsed.bars)
        next_by_date[day] = parsed.next_key
        receipts.append(receipt)
    date_ordinals = [int(item["date_ordinal"]) for item in receipts]
    if date_ordinals != sorted(date_ordinals) or set(date_ordinals) != set(range(1, len(plan.network_dates) + 1)):
        raise ContractError("DATE_REQUEST_ORDER")
    if any(value is not None for value in next_by_date.values()):
        raise ContractError("PAGINATION_INCOMPLETE")
    if any(value < 1 or value > MAX_PAGES_PER_QUERY for value in page_by_date.values()):
        raise ContractError("PAGE_COVERAGE")
    if [item["request_ordinal"] for item in receipts] != list(range(1, len(receipts) + 1)):
        raise ContractError("REQUEST_SEQUENCE")
    checked_date_summaries: list[dict[str, object]] = []
    for day in plan.network_dates:
        rows = _validate_identity(rows_by_date[day], day)
        expected_summary = _date_summary(
            day,
            rows,
            [item for item in receipts if item["parameters"]["date"] == day],
        )
        actual_summary = strict_json(_safe_shard_file(root, f"date_manifests/{day}.json").read_bytes())
        if actual_summary != expected_summary:
            raise ContractError("DATE_MANIFEST_BINDING")
        checked_date_summaries.append(expected_summary)
    pacing = _receipt_timing(receipts, attempt_id)
    raw_entries = sorted(
        (item["raw_relative_path"], item["body_sha256"], item["body_bytes"])
        for item in receipts
    )
    raw_tree_sha = sha256_bytes(canonical_json_bytes(raw_entries))
    summary = strict_json(_safe_shard_file(root, "summary.json").read_bytes())
    v1_loader._exact_keys(summary, MONTH_SUMMARY_KEYS, "MONTH_SUMMARY_SCHEMA")
    expected_summary = {
        "attempt_id": attempt_id,
        "batch_id": batch_id,
        "date_count": len(plan.network_dates),
        "month": plan.month,
        "null_bar_count": sum(int(item["null_bar_count"]) for item in checked_date_summaries),
        "pacing": pacing,
        "raw_tree_sha256": raw_tree_sha,
        "request_count": len(receipts),
        "reuse_entries": list(plan.reuse_entries),
        "row_count": sum(int(item["row_count"]) for item in checked_date_summaries),
        "source_binding_sha256": snapshot.binding_sha256,
        "traded_count": sum(int(item["traded_count"]) for item in checked_date_summaries),
    }
    if summary != expected_summary or manifest.get("raw_tree_sha256") != raw_tree_sha or manifest.get("request_count") != len(receipts):
        raise ContractError("MONTH_SUMMARY_BINDING")
    return manifest | {
        "manifest_sha256": sha256_bytes(manifest_raw),
        "row_count": summary["row_count"],
    }


def _publish(staging: Path, final: Path) -> None:
    if final.exists() or not (staging / "month_manifest.json").is_file():
        raise ContractError("PUBLISH_NO_CLOBBER_OR_INCOMPLETE")
    os.rename(staging, final)


def _completed_prefix(repo_root: Path, snapshot: _SourceSnapshot) -> int:
    missing_seen = False
    count = 0
    for plan in snapshot.plans:
        final = _raw_root(repo_root) / "months" / plan.month / "final"
        if final.exists():
            if missing_seen:
                raise ContractError("NON_CHRONOLOGICAL_SHARDS")
            validate_month_shard(repo_root, final, snapshot, plan)
            count += 1
        else:
            missing_seen = True
    return count


def build_global_catalog(repo_root: Path) -> dict[str, object]:
    snapshot = _source_snapshot(repo_root)
    entries: list[dict[str, object]] = []
    for plan in snapshot.plans:
        final = _raw_root(repo_root) / "months" / plan.month / "final"
        validated = validate_month_shard(repo_root, final, snapshot, plan)
        entries.append(
            {
                "manifest_sha256": validated["manifest_sha256"],
                "month": plan.month,
                "network_date_count": validated["network_date_count"],
                "raw_tree_sha256": validated["raw_tree_sha256"],
                "relative_path": final.relative_to(repo_root).as_posix(),
                "request_count": validated["request_count"],
                "row_count": validated["row_count"],
            }
        )
    if len(entries) != MONTH_COUNT or sum(int(item["network_date_count"]) for item in entries) != NETWORK_DATE_COUNT:
        raise ContractError("CATALOG_COVERAGE")
    core = {
        "acquisition_manifest_sha256": EXP009_ACQUISITION_MANIFEST_SHA256,
        "entries": entries,
        "month_count": MONTH_COUNT,
        "network_date_count": NETWORK_DATE_COUNT,
        "registry_artifact_sha256": EXP009_REGISTRY_SHA256,
        "reuse_dates": list(REUSE_DATES),
        "schema_version": GLOBAL_CATALOG_SCHEMA,
        "session_date_count": SESSION_DATE_COUNT,
        "source_binding_sha256": snapshot.binding_sha256,
        "status": "COMPLETE_23_IMMUTABLE_MONTH_SHARDS",
    }
    body = json_file_bytes(core)
    catalog_sha = sha256_bytes(body)
    path = _raw_root(repo_root) / "catalogs" / f"catalog_{catalog_sha}.json"
    if path.exists():
        if path.read_bytes() != body:
            raise ContractError("CATALOG_COLLISION")
    else:
        write_once(path, body)
    return {
        "catalog_relative_path": path.relative_to(repo_root).as_posix(),
        "catalog_sha256": catalog_sha,
        "month_count": len(entries),
        "network_date_count": sum(int(item["network_date_count"]) for item in entries),
        "source_binding_sha256": snapshot.binding_sha256,
    }


def dry_plan(repo_root: Path) -> dict[str, object]:
    snapshot = _source_snapshot(repo_root)
    completed = _completed_prefix(repo_root, snapshot)
    return {
        "api_base": API_BASE,
        "chronological_months": [plan.month for plan in snapshot.plans],
        "completed_immutable_month_prefix": completed,
        "first_request_full_cooldown_ns_per_attempt": MIN_SEND_SPACING_NS,
        "key_source": f"environment:{API_KEY_ENV}",
        "max_pages_per_date": MAX_PAGES_PER_QUERY,
        "month_count": len(snapshot.plans),
        "monthly_network_authorized_for_dry_plan": False,
        "network_date_count": sum(len(plan.network_dates) for plan in snapshot.plans),
        "network_requests": 0,
        "reuse_dates": list(REUSE_DATES),
        "session_date_count": sum(len(plan.session_dates) for plan in snapshot.plans),
        "source_binding_sha256": snapshot.binding_sha256,
        "version": VERSION,
    }


def launch_formal(
    repo_root: Path,
    batch_id: str,
    transport: Transport | None = None,
    *,
    pre_reserved: bool = False,
) -> dict[str, object]:
    batch_id = validate_batch_id(batch_id)
    startup = _source_snapshot(repo_root)
    if not pre_reserved:
        reserve_batch_and_emit_source_binding(repo_root, batch_id)
    batch_ledger = _adopt_batch(repo_root, batch_id, startup)
    key = os.environ.pop(API_KEY_ENV, "")
    if not key:
        _append(batch_ledger, {"batch_id": batch_id, "error_code": "API_KEY_MISSING", "event": "STOPPED_FIRST_FAILURE"})
        raise ContractError("API_KEY_MISSING")
    active_transport = transport or HttpsTransport()
    try:
        completed = _completed_prefix(repo_root, startup)
        for plan in startup.plans[completed:]:
            current = _source_snapshot(repo_root)
            if current.binding_sha256 != startup.binding_sha256:
                raise ContractError("SOURCE_DRIFT_BEFORE_MONTH")
            current_plan = next(item for item in current.plans if item.month == plan.month)
            if current_plan.projection() != plan.projection():
                raise ContractError("MONTH_PLAN_DRIFT")
            staging, final, attempt_id = _reserve_month(repo_root, batch_id, plan.month)
            _collect_month(
                repo_root,
                current,
                current_plan,
                staging,
                batch_id,
                attempt_id,
                key,
                transport=active_transport,
            )
            validate_month_shard(repo_root, staging, current, current_plan)
            _publish(staging, final)
            _append(
                batch_ledger,
                {"attempt_id": attempt_id, "batch_id": batch_id, "event": "MONTH_PUBLISHED", "month": plan.month},
            )
        catalog = build_global_catalog(repo_root)
        _append(batch_ledger, {"batch_id": batch_id, "catalog_sha256": catalog["catalog_sha256"], "event": "GLOBAL_CATALOG_PUBLISHED"})
        return catalog | {"batch_id": batch_id, "status": "COMPLETE"}
    except BaseException as exc:
        code = exc.code if isinstance(exc, ContractError) else type(exc).__name__
        _append(batch_ledger, {"batch_id": batch_id, "error_code": code, "event": "STOPPED_FIRST_FAILURE"})
        raise
    finally:
        key = ""


__all__ = ["build_global_catalog", "dry_plan", "launch_formal"]
