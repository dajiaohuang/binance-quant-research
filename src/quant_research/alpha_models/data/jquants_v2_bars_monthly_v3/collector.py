from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from ..jquants_v2_bars_monthly_v1 import collector as v1_collector
from ..jquants_v2_bars_monthly_v1 import loader as v1_loader
from .contracts import (
    API_BASE,
    API_HOST,
    API_KEY_ENV,
    BOOTSTRAP_GLOBAL_HTTP_CAP,
    BOOTSTRAP_PLAN_SHA256,
    BOOTSTRAP_QUERY_PLANS,
    BOOTSTRAP_RUN_ID,
    EXPERIMENT_ID,
    MAX_PAGES_PER_QUERY,
    MIN_SEND_SPACING_NS,
    PAGE_KEY,
    RECEIPT_SCHEMA_VERSION,
    ContractError,
    canonical_json_bytes,
    json_file_bytes,
    sha256_bytes,
    strict_json,
    validate_key,
)
from .loader import (
    _expected_registry_document,
    _preflight_binding_document,
    read_only_reuse_preflight,
    verify_attempt_preflight_binding,
)
from .planner import build_trusted_month_plans


Transport = v1_collector.Transport
HttpsTransport = v1_collector.HttpsTransport
write_once = v1_collector.write_once
ALLOWED_CONTENT_TYPES = v1_collector.ALLOWED_CONTENT_TYPES


def reserve_attempt(raw_runs_root: Path) -> tuple[Path, Path]:
    raw_runs_root.mkdir(parents=True, exist_ok=True)
    final = raw_runs_root / BOOTSTRAP_RUN_ID
    staging = raw_runs_root / f".{BOOTSTRAP_RUN_ID}.staging"
    if final.exists() or staging.exists():
        raise ContractError("ATTEMPT_EXISTS")
    staging.mkdir(exist_ok=False)
    write_once(staging / "attempt.reservation", json_file_bytes({"experiment_id": EXPERIMENT_ID, "run_id": BOOTSTRAP_RUN_ID}))
    write_once(staging / "attempt_ledger.jsonl", canonical_json_bytes({"event": "RESERVED", "run_id": BOOTSTRAP_RUN_ID}) + b"\n")
    return staging, final


def _append_ledger(staging: Path, event: Mapping[str, Any]) -> None:
    with (staging / "attempt_ledger.jsonl").open("ab") as stream:
        stream.write(canonical_json_bytes(dict(event)) + b"\n")
        stream.flush(); os.fsync(stream.fileno())


def _utc_iso(value_ns: int) -> str:
    return datetime.fromtimestamp(value_ns / 1_000_000_000, timezone.utc).isoformat().replace("+00:00", "Z")


def reserve_and_emit_source_binding(repo_root: Path) -> dict[str, object]:
    read_only_reuse_preflight(repo_root)
    raw_root = repo_root / "data/raw/jquants_v2_bars_monthly_v3/runs"
    staging, _ = reserve_attempt(raw_root)
    document = _preflight_binding_document(repo_root)
    write_once(staging / "preflight_source_binding.json", json_file_bytes(document))
    _append_ledger(staging, {"event": "ATTEMPT_OWNED_SOURCE_BINDING_EMITTED", "run_id": BOOTSTRAP_RUN_ID})
    return {"binding_sha256": document["binding_sha256"], "run_id": BOOTSTRAP_RUN_ID, "verdict": "PASS_RESERVED_AND_EMITTED"}


def _write_outputs(staging: Path, repo_root: Path, bundle: v1_loader.BootstrapBundle) -> dict[str, Any]:
    registry_document = _expected_registry_document(repo_root, staging, bundle)
    registry_body = json_file_bytes(registry_document)
    registry_sha = sha256_bytes(registry_body)
    registry_path = staging / "reuse_registry.json"
    write_once(registry_path, registry_body)
    plans = build_trusted_month_plans(repo_root, staging, registry_path, registry_sha)
    write_once(staging / "calendar_sessions.json", json_file_bytes({
        "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
        "session_dates": list(bundle.session_dates),
        "session_list_sha256": sha256_bytes(canonical_json_bytes(list(bundle.session_dates))),
    }))
    for plan in plans:
        write_once(staging / "monthly_plans" / f"{plan.month}.json", json_file_bytes(plan.projection()))
    write_once(staging / "edge_manifests" / "first.json", json_file_bytes(v1_loader.bar_summary(bundle.first_bars)))
    write_once(staging / "edge_manifests" / "last.json", json_file_bytes(v1_loader.bar_summary(bundle.last_bars)))
    entries = registry_document["entries"]
    assert type(entries) is list
    summary = v1_loader.bundle_summary(bundle) | {
        "reuse_dates": [item["session_date"] for item in entries],
        "reuse_registry_artifact_sha256": registry_sha,
        "reuse_registry_binding_sha256": registry_document["binding_sha256"],
        "reuse_source_kinds": [item["source_kind"] for item in entries],
    }
    write_once(staging / "summary.json", json_file_bytes(summary))
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in staging.rglob("*") if item.is_file() and item.name != "acquisition_manifest.json"):
        body = path.read_bytes()
        files.append({"bytes": len(body), "relative_path": path.relative_to(staging).as_posix(), "sha256": sha256_bytes(body)})
    manifest = {
        "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
        "files": files,
        "raw_tree_sha256": bundle.raw_tree_sha256,
        "run_id": BOOTSTRAP_RUN_ID,
        "status": "SOURCE_BOUND_BOOTSTRAP_VALIDATED",
    }
    write_once(staging / "acquisition_manifest.json", json_file_bytes(manifest))
    return summary


def collect_bootstrap(
    repo_root: Path,
    staging: Path,
    api_key: str,
    *,
    transport: Transport,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    sleep_seconds: Callable[[float], None] = time.sleep,
    utc_ns: Callable[[], int] = time.time_ns,
) -> dict[str, Any]:
    verify_attempt_preflight_binding(repo_root, staging)
    key = validate_key(api_key)
    guard = monotonic_ns()
    domain = v1_collector._clock_domain(BOOTSTRAP_RUN_ID, guard)
    pacer = v1_collector.MonotonicPacer(BOOTSTRAP_RUN_ID, guard, domain, monotonic_ns, sleep_seconds)
    write_once(staging / "query_plan.json", json_file_bytes({"plan_sha256": BOOTSTRAP_PLAN_SHA256, "queries": [item.projection() for item in BOOTSTRAP_QUERY_PLANS]}))
    pages = []
    receipts: list[dict[str, Any]] = []
    request_ordinal = 0
    try:
        for plan in BOOTSTRAP_QUERY_PLANS:
            page_key: str | None = None
            for page_number in range(1, MAX_PAGES_PER_QUERY + 1):
                request_ordinal += 1
                if request_ordinal > BOOTSTRAP_GLOBAL_HTTP_CAP:
                    raise ContractError("GLOBAL_HTTP_CAP")
                timing = pacer.wait_to_send()
                sent_utc_ns = utc_ns()
                response = transport.request(API_HOST, v1_collector._request_path(plan, page_key), {"x-api-key": key, "Accept": "application/json"})
                received_utc_ns = utc_ns()
                body = v1_collector._read_capped(response, plan.cap_bytes)
                raw_relative = f"responses/{plan.ordinal:02d}_{plan.query_id}_page_{page_number:04d}.json"
                receipt_relative = f"response_receipts/{request_ordinal:04d}_{plan.ordinal:02d}_{plan.query_id}_page_{page_number:04d}.receipt.json"
                write_once(staging / raw_relative, body)
                content_type = (response.getheader("Content-Type", "") or "").split(";", 1)[0].strip().lower()
                redirected = 300 <= response.status < 400 or response.getheader("Location") is not None
                receipt = {
                    "api_host": API_HOST,
                    "body_bytes": len(body),
                    "body_sha256": sha256_bytes(body),
                    "cap_bytes": plan.cap_bytes,
                    "clock_domain_id": domain,
                    "content_type": content_type,
                    **timing,
                    "guard_base_monotonic_ns": guard,
                    "page_number": page_number,
                    "parameters": dict(plan.parameters) | ({PAGE_KEY: page_key} if page_key else {}),
                    "path": plan.path,
                    "query_id": plan.query_id,
                    "query_ordinal": plan.ordinal,
                    "raw_relative_path": raw_relative,
                    "receipt_relative_path": receipt_relative,
                    "received_at_ms": received_utc_ns // 1_000_000,
                    "received_at_utc": _utc_iso(received_utc_ns),
                    "redirected": redirected,
                    "request_ordinal": request_ordinal,
                    "run_id": BOOTSTRAP_RUN_ID,
                    "schema_version": RECEIPT_SCHEMA_VERSION,
                    "sent_at_utc": _utc_iso(sent_utc_ns),
                    "status": response.status,
                }
                write_once(staging / receipt_relative, json_file_bytes(receipt))
                receipts.append(receipt)
                if redirected:
                    raise ContractError("REDIRECT")
                if response.status != 200:
                    raise ContractError(f"HTTP_{response.status}")
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise ContractError("CONTENT_TYPE")
                parsed = v1_loader.parse_page(plan, page_number, receipt["received_at_ms"], body)
                pages.append(parsed)
                if parsed.next_key is None:
                    break
                page_key = parsed.next_key
            else:
                raise ContractError("PAGE_CAP")
        bundle = v1_loader.merge_bootstrap(pages, receipts)
        _append_ledger(staging, {"event": "COLLECTED_SOURCE_BOUND_AND_VALIDATED", "request_count": request_ordinal, "run_id": BOOTSTRAP_RUN_ID})
        return _write_outputs(staging, repo_root, bundle)
    except BaseException as exc:
        code = exc.code if isinstance(exc, ContractError) else type(exc).__name__
        _append_ledger(staging, {"error_code": code, "event": "STOPPED_FIRST_FAILURE", "request_count": request_ordinal, "run_id": BOOTSTRAP_RUN_ID})
        raise
    finally:
        key = ""


def publish_bootstrap(staging: Path, final: Path) -> None:
    required = ("query_plan.json", "calendar_sessions.json", "reuse_registry.json", "summary.json", "acquisition_manifest.json")
    if any(not (staging / item).is_file() for item in required):
        raise ContractError("PUBLISH_INCOMPLETE")
    if final.exists():
        raise ContractError("PUBLISH_NO_CLOBBER")
    os.rename(staging, final)


def dry_plan(repo_root: Path) -> dict[str, Any]:
    preflight = read_only_reuse_preflight(repo_root)
    raw_root = repo_root / "data/raw/jquants_v2_bars_monthly_v3/runs"
    return {
        "api_base": API_BASE,
        "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
        "bootstrap_queries": [item.projection() for item in BOOTSTRAP_QUERY_PLANS],
        "exact_once_available": not (raw_root / BOOTSTRAP_RUN_ID).exists() and not (raw_root / f".{BOOTSTRAP_RUN_ID}.staging").exists(),
        "key_source": f"environment:{API_KEY_ENV}",
        "monthly_cli_available": False,
        "monthly_network_authorized": False,
        "network_requests": 0,
        "preflight_binding_sha256": preflight["binding_sha256"],
        "preflight_reuse_verdict": preflight["verdict"],
        "run_id": BOOTSTRAP_RUN_ID,
    }


def _adopt_reserved_attempt(repo_root: Path) -> tuple[Path, Path]:
    raw_root = repo_root / "data/raw/jquants_v2_bars_monthly_v3/runs"
    staging = raw_root / f".{BOOTSTRAP_RUN_ID}.staging"
    final = raw_root / BOOTSTRAP_RUN_ID
    if final.exists() or not staging.is_dir() or staging.is_symlink():
        raise ContractError("PRE_RESERVED_ATTEMPT_MISSING")
    try:
        v1_loader._safe_file(staging, "attempt.reservation")
        verify_attempt_preflight_binding(repo_root, staging)
    except ContractError as exc:
        raise ContractError("PRE_RESERVED_ATTEMPT_INCOMPLETE") from exc
    return staging, final


def launch_formal(repo_root: Path, transport: Transport | None = None, *, pre_reserved: bool = False) -> dict[str, Any]:
    read_only_reuse_preflight(repo_root)
    if pre_reserved:
        staging, final = _adopt_reserved_attempt(repo_root)
    else:
        raw_root = repo_root / "data/raw/jquants_v2_bars_monthly_v3/runs"
        staging, final = reserve_attempt(raw_root)
        write_once(staging / "preflight_source_binding.json", json_file_bytes(_preflight_binding_document(repo_root)))
        _append_ledger(staging, {"event": "ATTEMPT_OWNED_SOURCE_BINDING_EMITTED", "run_id": BOOTSTRAP_RUN_ID})
    key = os.environ.pop(API_KEY_ENV, "")
    if not key:
        _append_ledger(staging, {"error_code": "API_KEY_MISSING", "event": "STOPPED_FIRST_FAILURE", "request_count": 0, "run_id": BOOTSTRAP_RUN_ID})
        raise ContractError("API_KEY_MISSING")
    try:
        result = collect_bootstrap(repo_root, staging, key, transport=transport or HttpsTransport())
        publish_bootstrap(staging, final)
        return result
    finally:
        key = ""
