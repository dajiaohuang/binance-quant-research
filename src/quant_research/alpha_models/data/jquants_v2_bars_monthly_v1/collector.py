from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlencode

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
    ContractError,
    QueryPlan,
    canonical_json_bytes,
    json_file_bytes,
    sha256_bytes,
    strict_json,
    validate_clock_domain,
    validate_key,
)
from .loader import bundle_summary, merge_bootstrap, parse_page


ALLOWED_CONTENT_TYPES = frozenset(("application/json", "application/problem+json"))
RECEIPT_SCHEMA_VERSION = "JQUANTS_V2_BARS_MONTHLY_RECEIPT_V1"


class ResponseLike(Protocol):
    status: int

    def getheader(self, name: str, default: str | None = None) -> str | None: ...
    def read(self, amount: int | None = None) -> bytes: ...


class Transport(Protocol):
    def request(self, host: str, path_and_query: str, headers: Mapping[str, str]) -> ResponseLike: ...


class HttpsTransport:
    def request(self, host: str, path_and_query: str, headers: Mapping[str, str]) -> ResponseLike:
        if host != API_HOST:
            raise ContractError("HOST_ALLOWLIST")
        connection = http.client.HTTPSConnection(host, timeout=60, context=ssl.create_default_context())
        connection.request("GET", path_and_query, headers=dict(headers))
        return connection.getresponse()


def write_once(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def reserve_attempt(raw_runs_root: Path, run_id: str) -> tuple[Path, Path]:
    if run_id != BOOTSTRAP_RUN_ID:
        raise ContractError("BOOTSTRAP_RUN_ID")
    raw_runs_root.mkdir(parents=True, exist_ok=True)
    final = raw_runs_root / run_id
    staging = raw_runs_root / f".{run_id}.staging"
    if final.exists() or staging.exists():
        raise ContractError("ATTEMPT_EXISTS")
    staging.mkdir(exist_ok=False)
    write_once(staging / "attempt.reservation", json_file_bytes({"experiment_id": EXPERIMENT_ID, "run_id": run_id}))
    write_once(staging / "attempt_ledger.jsonl", canonical_json_bytes({"event": "RESERVED", "run_id": run_id}) + b"\n")
    return staging, final


def _append_ledger(staging: Path, event: Mapping[str, Any]) -> None:
    path = staging / "attempt_ledger.jsonl"
    with path.open("ab") as stream:
        stream.write(canonical_json_bytes(dict(event)) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _read_capped(response: ResponseLike, cap_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(1_048_576, cap_bytes + 1 - total))
        if not chunk:
            break
        if type(chunk) is not bytes:
            raise ContractError("BODY_TYPE")
        chunks.append(chunk)
        total += len(chunk)
        if total > cap_bytes:
            raise ContractError("BODY_TOO_LARGE")
    return b"".join(chunks)


def _request_path(plan: QueryPlan, page_key: str | None) -> str:
    parameters = dict(plan.parameters)
    if page_key is not None:
        parameters[PAGE_KEY] = page_key
    return f"{plan.path}?{urlencode(parameters)}"


class MonotonicPacer:
    def __init__(
        self,
        run_id: str,
        guard_base_monotonic_ns: int,
        clock_domain_id: str,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        sleep_seconds: Callable[[float], None] = time.sleep,
    ) -> None:
        self.run_id = run_id
        self.guard_base = guard_base_monotonic_ns
        self.clock_domain_id = validate_clock_domain(clock_domain_id)
        self.monotonic_ns = monotonic_ns
        self.sleep_seconds = sleep_seconds
        self.previous_send: int | None = None

    def wait_to_send(self) -> dict[str, int | None]:
        previous = self.previous_send
        deadline = (self.guard_base if previous is None else previous) + MIN_SEND_SPACING_NS
        pre_wait = self.monotonic_ns()
        requested = max(0, deadline - pre_wait)
        if requested:
            self.sleep_seconds(requested / 1_000_000_000)
        post_wait = self.monotonic_ns()
        if post_wait < deadline:
            raise ContractError("MONOTONIC_SLEEP_SHORT")
        sent = self.monotonic_ns()
        if sent < post_wait or sent - (self.guard_base if previous is None else previous) < MIN_SEND_SPACING_NS:
            raise ContractError("MONOTONIC_SEND_SHORT")
        self.previous_send = sent
        return {
            "deadline_monotonic_ns": deadline,
            "post_wait_monotonic_ns": post_wait,
            "pre_wait_monotonic_ns": pre_wait,
            "previous_send_monotonic_ns": previous,
            "requested_wait_ns": requested,
            "send_monotonic_ns": sent,
            "spacing_ns": MIN_SEND_SPACING_NS,
        }


def _clock_domain(run_id: str, guard: int) -> str:
    value = hashlib.sha256(f"{run_id}|{guard}|{os.getpid()}".encode("ascii")).hexdigest()[:24]
    return f"python-monotonic-{value}"


def _utc_iso(value_ns: int) -> str:
    return datetime.fromtimestamp(value_ns / 1_000_000_000, timezone.utc).isoformat().replace("+00:00", "Z")


def collect_bootstrap(
    staging: Path,
    api_key: str,
    *,
    transport: Transport,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
    sleep_seconds: Callable[[float], None] = time.sleep,
    utc_ns: Callable[[], int] = time.time_ns,
) -> dict[str, Any]:
    key = validate_key(api_key)
    guard = monotonic_ns()
    domain = _clock_domain(BOOTSTRAP_RUN_ID, guard)
    pacer = MonotonicPacer(BOOTSTRAP_RUN_ID, guard, domain, monotonic_ns, sleep_seconds)
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
                path_and_query = _request_path(plan, page_key)
                response = transport.request(API_HOST, path_and_query, {"x-api-key": key, "Accept": "application/json"})
                received_utc_ns = utc_ns()
                body = _read_capped(response, plan.cap_bytes)
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
                parsed = parse_page(plan, page_number, receipt["received_at_ms"], body)
                pages.append(parsed)
                if parsed.next_key is None:
                    break
                page_key = parsed.next_key
            else:
                raise ContractError("PAGE_CAP")
        bundle = merge_bootstrap(pages, receipts)
        _append_ledger(staging, {"event": "COLLECTED_AND_VALIDATED", "request_count": request_ordinal, "run_id": BOOTSTRAP_RUN_ID})
        _write_bootstrap_outputs(staging, bundle)
        return bundle_summary(bundle)
    except BaseException as exc:
        code = exc.code if isinstance(exc, ContractError) else type(exc).__name__
        _append_ledger(staging, {"error_code": code, "event": "STOPPED_FIRST_FAILURE", "request_count": request_ordinal, "run_id": BOOTSTRAP_RUN_ID})
        raise
    finally:
        key = ""


def _bar_manifest(rows: tuple[Any, ...]) -> dict[str, Any]:
    from .loader import bar_summary
    return bar_summary(rows)


def _write_bootstrap_outputs(staging: Path, bundle: Any) -> None:
    write_once(staging / "calendar_sessions.json", json_file_bytes({
        "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
        "session_dates": list(bundle.session_dates),
        "session_list_sha256": sha256_bytes(canonical_json_bytes(list(bundle.session_dates))),
    }))
    for plan in bundle.month_plans:
        write_once(staging / "monthly_plans" / f"{plan.month}.json", json_file_bytes(plan.projection()))
    write_once(staging / "edge_manifests" / "first.json", json_file_bytes(_bar_manifest(bundle.first_bars)))
    write_once(staging / "edge_manifests" / "last.json", json_file_bytes(_bar_manifest(bundle.last_bars)))
    summary = bundle_summary(bundle)
    write_once(staging / "summary.json", json_file_bytes(summary))
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in staging.rglob("*") if item.is_file() and item.name != "acquisition_manifest.json"):
        relative = path.relative_to(staging).as_posix()
        body = path.read_bytes()
        files.append({"bytes": len(body), "relative_path": relative, "sha256": sha256_bytes(body)})
    manifest = {
        "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
        "files": files,
        "raw_tree_sha256": bundle.raw_tree_sha256,
        "run_id": BOOTSTRAP_RUN_ID,
        "status": "SOURCE_BOUND_BOOTSTRAP_VALIDATED",
    }
    write_once(staging / "acquisition_manifest.json", json_file_bytes(manifest))


def publish_bootstrap(staging: Path, final: Path) -> None:
    required = ("query_plan.json", "calendar_sessions.json", "summary.json", "acquisition_manifest.json")
    if any(not (staging / item).is_file() for item in required):
        raise ContractError("PUBLISH_INCOMPLETE")
    if final.exists():
        raise ContractError("PUBLISH_NO_CLOBBER")
    os.rename(staging, final)


def dry_plan(repo_root: Path) -> dict[str, Any]:
    raw_root = repo_root / "data" / "raw" / "jquants_v2_bars_monthly_v1" / "runs"
    staging = raw_root / f".{BOOTSTRAP_RUN_ID}.staging"
    final = raw_root / BOOTSTRAP_RUN_ID
    return {
        "api_base": API_BASE,
        "bootstrap_global_http_cap": BOOTSTRAP_GLOBAL_HTTP_CAP,
        "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
        "bootstrap_queries": [item.projection() for item in BOOTSTRAP_QUERY_PLANS],
        "exact_once_available": not staging.exists() and not final.exists(),
        "first_request_full_cooldown_ns": MIN_SEND_SPACING_NS,
        "key_source": f"environment:{API_KEY_ENV}",
        "monthly_network_authorized": False,
        "network_requests": 0,
        "raw_root": raw_root.relative_to(repo_root).as_posix(),
        "run_id": BOOTSTRAP_RUN_ID,
    }


def launch_formal(repo_root: Path, transport: Transport | None = None) -> dict[str, Any]:
    raw_root = repo_root / "data" / "raw" / "jquants_v2_bars_monthly_v1" / "runs"
    staging, final = reserve_attempt(raw_root, BOOTSTRAP_RUN_ID)
    key = os.environ.pop(API_KEY_ENV, "")
    if not key:
        _append_ledger(staging, {"error_code": "API_KEY_MISSING", "event": "STOPPED_FIRST_FAILURE", "request_count": 0, "run_id": BOOTSTRAP_RUN_ID})
        raise ContractError("API_KEY_MISSING")
    try:
        summary = collect_bootstrap(staging, key, transport=transport or HttpsTransport())
        publish_bootstrap(staging, final)
        return summary
    finally:
        key = ""
