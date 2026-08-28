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
    validate_key,
)
from .loader import mint_full_registry, verify_exp005_reuse
from .planner import build_verified_month_plans


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
    path = staging / "attempt_ledger.jsonl"
    with path.open("ab") as stream:
        stream.write(canonical_json_bytes(dict(event)) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _utc_iso(value_ns: int) -> str:
    return datetime.fromtimestamp(value_ns / 1_000_000_000, timezone.utc).isoformat().replace("+00:00", "Z")


def _write_outputs(staging: Path, bundle: v1_loader.BootstrapBundle, registry: Any) -> dict[str, Any]:
    plans = build_verified_month_plans(bundle.month_plans, registry)
    write_once(staging / "calendar_sessions.json", json_file_bytes({
        "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
        "session_dates": list(bundle.session_dates),
        "session_list_sha256": sha256_bytes(canonical_json_bytes(list(bundle.session_dates))),
    }))
    write_once(staging / "reuse_registry.json", json_file_bytes(registry.projection()))
    for plan in plans:
        write_once(staging / "monthly_plans" / f"{plan.month}.json", json_file_bytes(plan.projection()))
    write_once(staging / "edge_manifests" / "first.json", json_file_bytes(v1_loader.bar_summary(bundle.first_bars)))
    write_once(staging / "edge_manifests" / "last.json", json_file_bytes(v1_loader.bar_summary(bundle.last_bars)))
    summary = v1_loader.bundle_summary(bundle) | {
        "reuse_dates": [item.session_date for item in registry.entries],
        "reuse_registry_sha256": registry.registry_sha256,
        "reuse_source_kinds": [item.source_kind for item in registry.entries],
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
    preflight_registry = verify_exp005_reuse(repo_root)
    try:
        preflight_path = v1_loader._safe_file(staging, "preflight_reuse_registry.json")
    except ContractError:
        raise ContractError("ATTEMPT_OWNED_REUSE_REGISTRY_MISSING")
    from ..jquants_v2_bars_monthly_v1.contracts import strict_json
    if strict_json(preflight_path.read_bytes()) != preflight_registry.projection():
        raise ContractError("ATTEMPT_OWNED_REUSE_REGISTRY_MISMATCH")
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
                    "send_monotonic_ns": timing["send_monotonic_ns"],
                    "sent_at_utc": _utc_iso(sent_utc_ns),
                    "spacing_ns": MIN_SEND_SPACING_NS,
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
        registry = mint_full_registry(repo_root, staging, bundle)
        _append_ledger(staging, {"event": "COLLECTED_REUSE_BOUND_AND_VALIDATED", "request_count": request_ordinal, "run_id": BOOTSTRAP_RUN_ID})
        return _write_outputs(staging, bundle, registry)
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
    registry = verify_exp005_reuse(repo_root)
    raw_root = repo_root / "data/raw/jquants_v2_bars_monthly_v2/runs"
    return {
        "api_base": API_BASE,
        "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
        "bootstrap_queries": [item.projection() for item in BOOTSTRAP_QUERY_PLANS],
        "exact_once_available": not (raw_root / BOOTSTRAP_RUN_ID).exists() and not (raw_root / f".{BOOTSTRAP_RUN_ID}.staging").exists(),
        "key_source": f"environment:{API_KEY_ENV}",
        "monthly_cli_available": False,
        "monthly_network_authorized": False,
        "network_requests": 0,
        "preflight_reuse_source_date": registry.entries[0].session_date,
        "preflight_reuse_verdict": "PASS_READ_ONLY_NO_REGISTRY_EMITTED",
        "run_id": BOOTSTRAP_RUN_ID,
    }


def reuse_preflight_check(repo_root: Path) -> dict[str, Any]:
    registry = verify_exp005_reuse(repo_root)
    return {"source_date": registry.entries[0].session_date, "verdict": "PASS_READ_ONLY_NO_REGISTRY_EMITTED"}


def reserve_and_emit_reuse(repo_root: Path) -> dict[str, Any]:
    verify_exp005_reuse(repo_root)
    raw_root = repo_root / "data/raw/jquants_v2_bars_monthly_v2/runs"
    staging, _ = reserve_attempt(raw_root)
    registry = verify_exp005_reuse(repo_root)
    write_once(staging / "preflight_reuse_registry.json", json_file_bytes(registry.projection()))
    _append_ledger(staging, {"event": "ATTEMPT_OWNED_REUSE_REGISTRY_EMITTED", "run_id": BOOTSTRAP_RUN_ID})
    return {"registry_sha256": registry.registry_sha256, "run_id": BOOTSTRAP_RUN_ID, "verdict": "PASS_RESERVED_AND_EMITTED"}


def _adopt_reserved_attempt(repo_root: Path, registry: Any) -> tuple[Path, Path]:
    raw_root = repo_root / "data/raw/jquants_v2_bars_monthly_v2/runs"
    staging = raw_root / f".{BOOTSTRAP_RUN_ID}.staging"
    final = raw_root / BOOTSTRAP_RUN_ID
    if final.exists() or not staging.is_dir() or staging.is_symlink():
        raise ContractError("PRE_RESERVED_ATTEMPT_MISSING")
    try:
        v1_loader._safe_file(staging, "attempt.reservation")
        preflight = v1_loader._safe_file(staging, "preflight_reuse_registry.json")
    except ContractError:
        raise ContractError("PRE_RESERVED_ATTEMPT_INCOMPLETE")
    from ..jquants_v2_bars_monthly_v1.contracts import strict_json
    if strict_json(preflight.read_bytes()) != registry.projection():
        raise ContractError("ATTEMPT_OWNED_REUSE_REGISTRY_MISMATCH")
    return staging, final


def launch_formal(repo_root: Path, transport: Transport | None = None, *, pre_reserved: bool = False) -> dict[str, Any]:
    registry = verify_exp005_reuse(repo_root)
    if pre_reserved:
        staging, final = _adopt_reserved_attempt(repo_root, registry)
    else:
        raw_root = repo_root / "data/raw/jquants_v2_bars_monthly_v2/runs"
        staging, final = reserve_attempt(raw_root)
        write_once(staging / "preflight_reuse_registry.json", json_file_bytes(registry.projection()))
        _append_ledger(staging, {"event": "ATTEMPT_OWNED_REUSE_REGISTRY_EMITTED", "run_id": BOOTSTRAP_RUN_ID})
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
