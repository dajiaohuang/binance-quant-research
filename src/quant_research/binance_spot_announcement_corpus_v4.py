"""Freeze Binance CMS claims with receipt-bound acquisition evidence.

Source publication timestamps are retained as claims.  The exact detail version
is known only at the response-completion clock of this acquisition.  This module
never parses article prose into pairs, events, effective times, or listing
intervals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXTRACTOR_VERSION = "binance_spot_announcement_v4"
TIME_URL = "https://data-api.binance.vision/api/v3/time"
LIST_BASE = "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query"
DETAIL_BASE = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
CATALOG_IDS = (48, 161)
PAGE_SIZE = 50
CODE_PATTERN = re.compile(r"^(?:[0-9a-f]{32}|[0-9]{1,64})$")
ARTICLE_ID_STRING_PATTERN = re.compile(r"^[0-9]{1,64}$")
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
ABSOLUTE_ATTEMPT_NUMBERS = (1, 2, 3, 4)
FROZEN_HTTP_429_BACKOFF_SECONDS = (30, 60, 120)
FROZEN_OTHER_RETRYABLE_BACKOFF_SECONDS = (1, 2)
BASE_LOGICAL_DELAY_NS = 1_000_000_000
TIMEOUT_CAP_NS = 30_000_000_000
WALL_BUDGET_NS = 10_800_000_000_000
MAX_WIRE_ATTEMPTS = 3_464
ATTEMPT_KEY_WIDTH = 8
RECEIPT_CORE_FIELDS = frozenset(
    {
        "run_id",
        "logical_key",
        "logical_sequence",
        "attempt_key",
        "attempt_no",
        "wire_attempt_global_no",
        "canonical_url",
        "outcome",
        "http_status",
        "retry_after_present",
        "accepted",
        "decision",
        "next_wire_scope",
        "next_attempt_no",
        "delay_rule",
        "requested_delay_ns",
        "pre_sleep_monotonic_ns",
        "post_sleep_monotonic_ns",
        "terminal_reason",
        "body_path",
        "body_sha256",
        "sidecar_path",
        "sidecar_sha256",
        "previous_receipt_sha256",
    }
)
SELECTED_RESPONSE_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Date",
    "Server",
    "X-MBX-Used-Weight",
    "Retry-After",
    "Location",
)
FORBIDDEN_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-mbx-apikey",
        "x-mbx-api-key",
    }
)
REQUEST_HEADERS = {
    "Accept-Encoding": "identity",
    "Accept-Language": "en-US,en;q=0.9",
    "Clienttype": "web",
    "lang": "en",
    "User-Agent": "quant-binance-announcement-corpus-v4/1",
}
SUMMARY_TOP_LEVEL_FIELDS = frozenset(
    {
        "run_id",
        "terminal_status",
        "artifact_state",
        "semantics",
        "catalog_totals",
        "expected_totals",
        "page_counts",
        "list_release_date_claim_interval_counts",
        "expected_list_release_date_claim_interval_counts",
        "list_pass_stability",
        "inventory_count",
        "detail_count",
        "time_claim_discrepancy_count",
        "contract_failures",
        "response_bytes",
        "request_count",
        "expected_request_count",
        "wire_attempt_count",
        "max_wire_attempts",
        "time_bracket_ms",
        "max_clock_skew_ms",
        "acquisition_bounds",
        "request_ledger",
        "raw_summary",
        "inventory",
        "detail_index",
        "time_claim_discrepancies",
        "extractor_source_sha256",
        "pre_network_expected_extractor_sha256",
        "list_release_date_claim_interval_ms",
        "historical_eligibility_ready",
    }
)


class CorpusError(RuntimeError):
    pass


class CorpusContractError(CorpusError):
    pass


class CorpusHttpError(CorpusError):
    pass


class CorpusSchemaError(CorpusError):
    pass


class CorpusIntegrityError(CorpusError):
    pass


class CorpusExistingError(CorpusError):
    pass


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str
    request_started_at_utc: str
    response_completed_at_utc: str


@dataclass(frozen=True)
class LoadedCorpus:
    run_id: str
    terminal_status: str
    artifact_state: str | None
    inventory: tuple[dict[str, Any], ...]
    details: tuple[dict[str, Any], ...]
    time_claim_discrepancies: tuple[dict[str, Any], ...]
    summary_sha256: str


@dataclass
class _PendingAttempt:
    response: TransportResponse
    row: dict[str, Any]
    receipt_path: Path
    request: urllib.request.Request
    parameters: dict[str, str]
    kind: str
    retry_after_present: bool
    prefetch_monotonic_ns: int
    postfetch_monotonic_ns: int


@dataclass
class _AcquisitionState:
    run_id: str
    raw_run: Path
    expected_extractor_sha256: str
    deadline_monotonic_ns: int
    max_response_bytes: int
    max_total_response_bytes: int
    fetcher: "Fetcher"
    sleeper: "Sleeper"
    monotonic_ns: "Monotonic"
    http_429_backoff_seconds: tuple[int, int, int]
    other_retryable_backoff_seconds: tuple[int, int]
    response_bytes: int = 0
    wire_attempt_count: int = 0
    logical_sequence: int = 0
    previous_receipt_sha256: str | None = None
    pending_ok: _PendingAttempt | None = None
    ledger: list[dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.ledger is None:
            self.ledger = []


Fetcher = Callable[[urllib.request.Request, float], TransportResponse]
Sleeper = Callable[[float], None]
Preflight = Callable[[], None]
Monotonic = Callable[[], int]


class _CorpusNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, label: str) -> datetime:
    if type(value) is not str or not value:
        raise CorpusContractError(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CorpusContractError(f"invalid {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise CorpusContractError(f"{label} must have UTC offset")
    return parsed.astimezone(timezone.utc)


def _epoch_ms(value: str, label: str) -> int:
    return int(_parse_utc(value, label).timestamp() * 1000)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    text = json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        sort_keys=True,
    )
    return (text + "\n").encode("utf-8")


def _reject_duplicate_json_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CorpusIntegrityError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _strict_json_loads(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_reject_duplicate_json_object)


def _write_once(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise CorpusExistingError(f"refusing to overwrite {path}")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{path.name}.",
            suffix=".partial",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(body)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path = Path(temporary_name)
        if path.exists():
            raise CorpusExistingError(f"refusing to overwrite {path}")
        os.replace(temporary_path, path)
        temporary_name = None
        return _sha256_bytes(body)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _selected_headers(headers: Mapping[str, str]) -> dict[str, str]:
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    return {
        name: lowered[name.lower()]
        for name in SELECTED_RESPONSE_HEADERS
        if name.lower() in lowered
    }


def _safe_transport_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, TimeoutError):
        return "TIMEOUT", "public endpoint request timed out"
    if isinstance(exc, urllib.error.URLError):
        return "URL_ERROR", "public endpoint transport failed"
    if isinstance(exc, OSError):
        return "OS_ERROR", "public endpoint transport failed"
    return "TRANSPORT_ERROR", "public endpoint transport failed"


def _bounded_default_fetcher(
    request: urllib.request.Request, timeout: float, max_response_bytes: int
) -> TransportResponse:
    started = _utc_now()
    try:
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _CorpusNoRedirectHandler()
            )
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read(max_response_bytes + 1)
            completed = _utc_now()
            return TransportResponse(
                status=int(response.status),
                headers=dict(response.headers.items()),
                body=body,
                final_url=response.geturl(),
                request_started_at_utc=started,
                response_completed_at_utc=completed,
            )
    except urllib.error.URLError:
        raise


def _stable_jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_stable_json_bytes(row) for row in rows)


def _list_url(catalog_id: int, page_no: int, page_size: int) -> str:
    if catalog_id not in CATALOG_IDS:
        raise CorpusContractError("catalogId is not frozen")
    if type(page_no) is not int or page_no < 1:
        raise CorpusContractError("pageNo must be positive")
    if page_size != PAGE_SIZE:
        raise CorpusContractError("pageSize must equal 50")
    return (
        f"{LIST_BASE}?type=1&catalogId={catalog_id}&pageNo={page_no}"
        f"&pageSize={page_size}"
    )


def _detail_url(code: str) -> str:
    if type(code) is not str or CODE_PATTERN.fullmatch(code) is None:
        raise CorpusContractError("articleCode violates opaque identifier grammar")
    encoded = urllib.parse.quote(code, safe="")
    return f"{DETAIL_BASE}?articleCode={encoded}"


def _validate_canonical_url(url: str, kind: str) -> dict[str, str]:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.binance.com", "data-api.binance.vision"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise CorpusContractError("invalid source URL")
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if kind == "time":
        if url != TIME_URL or pairs:
            raise CorpusContractError("invalid time URL")
        return {}
    if kind == "list":
        expected_names = ["type", "catalogId", "pageNo", "pageSize"]
        if parsed.path != urllib.parse.urlsplit(LIST_BASE).path or [k for k, _ in pairs] != expected_names:
            raise CorpusContractError("invalid list URL parameters")
        values = dict(pairs)
        if values["type"] != "1" or values["pageSize"] != "50":
            raise CorpusContractError("invalid list type/pageSize")
        try:
            expected = _list_url(int(values["catalogId"]), int(values["pageNo"]), 50)
        except ValueError as exc:
            raise CorpusContractError("invalid numeric list parameter") from exc
        if url != expected:
            raise CorpusContractError("non-canonical list URL")
        return values
    if kind == "detail":
        if parsed.path != urllib.parse.urlsplit(DETAIL_BASE).path or [k for k, _ in pairs] != ["articleCode"]:
            raise CorpusContractError("invalid detail URL parameters")
        code = pairs[0][1]
        if url != _detail_url(code):
            raise CorpusContractError("non-canonical detail URL")
        return {"articleCode": code}
    raise CorpusContractError("unknown request kind")


def _request(url: str, kind: str) -> tuple[urllib.request.Request, dict[str, str]]:
    parameters = _validate_canonical_url(url, kind)
    headers = {"Accept-Encoding": "identity", "User-Agent": REQUEST_HEADERS["User-Agent"]}
    if kind != "time":
        headers.update(
            {
                "Accept-Language": REQUEST_HEADERS["Accept-Language"],
                "Clienttype": REQUEST_HEADERS["Clienttype"],
                "lang": REQUEST_HEADERS["lang"],
            }
        )
    request = urllib.request.Request(url, method="GET", headers=headers)
    _validate_request_evidence_headers(dict(request.header_items()), kind, url)
    return request, parameters


def _validate_request_evidence_headers(
    headers: Mapping[str, Any], kind: str, url: str
) -> None:
    observed = {str(name).lower(): value for name, value in headers.items()}
    if any(name in FORBIDDEN_HEADERS for name in observed):
        raise CorpusContractError("authentication/proxy header is forbidden")
    expected_names = {"accept-encoding", "user-agent"}
    if kind != "time":
        expected_names.update({"accept-language", "clienttype", "lang"})
    permitted_names = expected_names | {"host"}
    if not expected_names.issubset(observed) or not set(observed).issubset(
        permitted_names
    ):
        raise CorpusContractError("request header set drift")
    if (
        observed.get("accept-encoding") != REQUEST_HEADERS["Accept-Encoding"]
        or observed.get("user-agent") != REQUEST_HEADERS["User-Agent"]
    ):
        raise CorpusContractError("request base header drift")
    if kind != "time" and (
        observed.get("accept-language") != REQUEST_HEADERS["Accept-Language"]
        or observed.get("clienttype") != REQUEST_HEADERS["Clienttype"]
        or observed.get("lang") != REQUEST_HEADERS["lang"]
    ):
        raise CorpusContractError("English CMS headers drift")
    canonical_host = urllib.parse.urlsplit(url).hostname
    if "host" in observed and observed["host"] != canonical_host:
        raise CorpusContractError("transport-injected Host is non-canonical")


def _selected_cms_headers(headers: Mapping[str, str]) -> dict[str, str]:
    selected = _selected_headers(headers)
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    for name in ("ETag", "Last-Modified", "Age", "Cache-Control"):
        if name.lower() in lowered:
            selected[name] = lowered[name.lower()]
    return selected


def _response_times(response: TransportResponse) -> None:
    if _parse_utc(
        response.response_completed_at_utc, "response completion"
    ) < _parse_utc(response.request_started_at_utc, "request start"):
        raise CorpusContractError("response completion precedes request start")


def _retry_after_present(headers: Mapping[str, Any]) -> bool:
    return any(str(name).lower() == "retry-after" for name in headers)


def _timeout_seconds_without_up_round(timeout_ns: int) -> float:
    if type(timeout_ns) is not int or timeout_ns <= 0:
        raise CorpusContractError("timeout nanoseconds must be positive")
    value = timeout_ns / 1_000_000_000
    if value * 1_000_000_000 > timeout_ns:
        value = math.nextafter(value, 0.0)
    return value


def _response_outcome(
    *,
    request_headers_valid: bool,
    response: TransportResponse,
    canonical_url: str,
    max_response_bytes: int,
    cumulative_response_bytes: int,
    max_total_response_bytes: int,
    response_clock_valid: bool = True,
) -> str:
    selected = _selected_headers(response.headers)
    content_length = selected.get("Content-Length")
    if not response_clock_valid:
        outcome = "INVALID_RESPONSE_CLOCK"
    elif not request_headers_valid:
        outcome = "REQUEST_HEADER_DRIFT"
    elif 300 <= response.status < 400 or response.final_url != canonical_url:
        outcome = "REDIRECT_REJECTED"
    elif len(response.body) > max_response_bytes:
        outcome = "OVERSIZED_RESPONSE"
    elif content_length is None:
        outcome = "MISSING_CONTENT_LENGTH"
    elif content_length is not None:
        try:
            declared = int(content_length)
        except (TypeError, ValueError):
            outcome = "INVALID_CONTENT_LENGTH"
        else:
            outcome = (
                "CONTENT_LENGTH_MISMATCH"
                if declared != len(response.body)
                else "OK"
                if response.status == 200
                else f"HTTP_{response.status}"
            )
    else:
        outcome = f"HTTP_{response.status}"
    if cumulative_response_bytes > max_total_response_bytes:
        return "TOTAL_RESPONSE_BYTES_EXCEEDED"
    return outcome


def _attempt_sidecar(
    request: urllib.request.Request,
    parameters: Mapping[str, str],
    response: TransportResponse,
    outcome: str,
    *,
    prefetch_monotonic_ns: int,
    postfetch_monotonic_ns: int,
    timeout_ns: int,
    deadline_monotonic_ns: int,
) -> dict[str, Any]:
    return {
        "request": {
            "method": "GET",
            "canonical_url": request.full_url,
            "canonical_parameters": dict(parameters),
            "headers": dict(sorted(request.header_items())),
            "authentication": "NONE",
        },
        "response": {
            "status": response.status,
            "final_url": response.final_url,
            "selected_headers": _selected_cms_headers(response.headers),
            "request_started_at_utc": response.request_started_at_utc,
            "response_completed_at_utc": response.response_completed_at_utc,
            "body_bytes": len(response.body),
            "body_sha256": _sha256_bytes(response.body),
        },
        "monotonic": {
            "prefetch_monotonic_ns": prefetch_monotonic_ns,
            "postfetch_monotonic_ns": postfetch_monotonic_ns,
            "timeout_ns": timeout_ns,
            "deadline_monotonic_ns": deadline_monotonic_ns,
            "semantics": "PROCESS_LOCAL_ACQUISITION_TIMING_CLAIMS",
        },
        "outcome": outcome,
    }


def _terminal_schedule(
    *,
    state: _AcquisitionState,
    reason: str,
    planned_logical_key: str | None,
    planned_logical_sequence: int | None,
    planned_attempt_no: int | None,
    planned_delay_ns: int | None = None,
    pre_sleep_monotonic_ns: int | None = None,
    post_sleep_monotonic_ns: int | None = None,
    observed_extractor_sha256: str | None = None,
) -> None:
    path = state.raw_run / "terminal_schedule.json"
    observed = observed_extractor_sha256 or _module_sha()
    _write_once(
        path,
        _stable_json_bytes(
            {
                "run_id": state.run_id,
                "reason": reason,
                "wire_attempt_count": state.wire_attempt_count,
                "last_receipt_sha256": state.previous_receipt_sha256,
                "planned_logical_key": planned_logical_key,
                "planned_logical_sequence": planned_logical_sequence,
                "planned_attempt_no": planned_attempt_no,
                "planned_delay_ns": planned_delay_ns,
                "pre_sleep_monotonic_ns": pre_sleep_monotonic_ns,
                "post_sleep_monotonic_ns": post_sleep_monotonic_ns,
                "expected_extractor_sha256": state.expected_extractor_sha256,
                "observed_extractor_sha256": observed,
            },
            pretty=True,
        ),
    )


def _source_or_terminal(
    state: _AcquisitionState,
    *,
    reason: str,
    logical_key: str | None,
    logical_sequence: int | None,
    attempt_no: int | None,
    delay_ns: int | None = None,
    pre_sleep_ns: int | None = None,
    post_sleep_ns: int | None = None,
) -> None:
    observed = _module_sha()
    if observed == state.expected_extractor_sha256:
        return
    _terminal_schedule(
        state=state,
        reason=reason,
        planned_logical_key=logical_key,
        planned_logical_sequence=logical_sequence,
        planned_attempt_no=attempt_no,
        planned_delay_ns=delay_ns,
        pre_sleep_monotonic_ns=pre_sleep_ns,
        post_sleep_monotonic_ns=post_sleep_ns,
        observed_extractor_sha256=observed,
    )
    raise CorpusIntegrityError("extractor source SHA-256 mismatch")


def _sleep_authorization(
    state: _AcquisitionState,
    *,
    logical_key: str,
    logical_sequence: int,
    attempt_no: int,
    delay_ns: int,
) -> tuple[int, int]:
    _source_or_terminal(
        state,
        reason="SOURCE_DRIFT_PRE_SLEEP",
        logical_key=logical_key,
        logical_sequence=logical_sequence,
        attempt_no=attempt_no,
        delay_ns=delay_ns,
    )
    pre = state.monotonic_ns()
    remaining = state.deadline_monotonic_ns - pre
    if pre >= state.deadline_monotonic_ns or delay_ns >= remaining:
        _terminal_schedule(
            state=state,
            reason="WALL_BUDGET_BEFORE_SLEEP",
            planned_logical_key=logical_key,
            planned_logical_sequence=logical_sequence,
            planned_attempt_no=attempt_no,
            planned_delay_ns=delay_ns,
            pre_sleep_monotonic_ns=pre,
        )
        raise CorpusContractError("wall budget exhausted before sleep")
    try:
        state.sleeper(delay_ns / 1_000_000_000)
    except Exception as exc:
        _terminal_schedule(
            state=state,
            reason="SLEEP_EXCEPTION",
            planned_logical_key=logical_key,
            planned_logical_sequence=logical_sequence,
            planned_attempt_no=attempt_no,
            planned_delay_ns=delay_ns,
            pre_sleep_monotonic_ns=pre,
        )
        raise CorpusContractError("scheduled sleep failed") from exc
    post = state.monotonic_ns()
    _source_or_terminal(
        state,
        reason="SOURCE_DRIFT_POST_SLEEP",
        logical_key=logical_key,
        logical_sequence=logical_sequence,
        attempt_no=attempt_no,
        delay_ns=delay_ns,
        pre_sleep_ns=pre,
        post_sleep_ns=post,
    )
    if post < pre + delay_ns or post >= state.deadline_monotonic_ns:
        _terminal_schedule(
            state=state,
            reason="INVALID_POST_SLEEP_MONOTONIC",
            planned_logical_key=logical_key,
            planned_logical_sequence=logical_sequence,
            planned_attempt_no=attempt_no,
            planned_delay_ns=delay_ns,
            pre_sleep_monotonic_ns=pre,
            post_sleep_monotonic_ns=post,
        )
        raise CorpusContractError("scheduled sleep violates monotonic contract")
    return pre, post


def _receipt_core(
    state: _AcquisitionState,
    pending: _PendingAttempt,
    *,
    accepted: bool,
    decision: str,
    next_wire_scope: str | None,
    next_attempt_no: int | None,
    delay_rule: str,
    requested_delay_ns: int,
    pre_sleep_monotonic_ns: int | None,
    post_sleep_monotonic_ns: int | None,
    terminal_reason: str | None,
) -> dict[str, Any]:
    attempt = pending.row["attempts"][-1]
    return {
        "run_id": state.run_id,
        "logical_key": pending.row["logical_key"],
        "logical_sequence": pending.row["sequence"],
        "attempt_key": attempt["attempt_key"],
        "attempt_no": attempt["attempt_no"],
        "wire_attempt_global_no": attempt["wire_attempt_global_no"],
        "canonical_url": pending.row["canonical_url"],
        "outcome": attempt["outcome"],
        "http_status": pending.response.status,
        "retry_after_present": pending.retry_after_present,
        "accepted": accepted,
        "decision": decision,
        "next_wire_scope": next_wire_scope,
        "next_attempt_no": next_attempt_no,
        "delay_rule": delay_rule,
        "requested_delay_ns": requested_delay_ns,
        "pre_sleep_monotonic_ns": pre_sleep_monotonic_ns,
        "post_sleep_monotonic_ns": post_sleep_monotonic_ns,
        "terminal_reason": terminal_reason,
        "body_path": attempt["body"],
        "body_sha256": attempt["body_sha256"],
        "sidecar_path": attempt["sidecar"],
        "sidecar_sha256": attempt["sidecar_sha256"],
        "previous_receipt_sha256": state.previous_receipt_sha256,
    }


def _commit_receipt(
    state: _AcquisitionState,
    pending: _PendingAttempt,
    **fields: Any,
) -> str:
    _source_or_terminal(
        state,
        reason="SOURCE_DRIFT_POST_RAW_PRE_RECEIPT",
        logical_key=pending.row["logical_key"],
        logical_sequence=pending.row["sequence"],
        attempt_no=pending.row["attempts"][-1]["attempt_no"],
        delay_ns=fields.get("requested_delay_ns"),
        pre_sleep_ns=fields.get("pre_sleep_monotonic_ns"),
        post_sleep_ns=fields.get("post_sleep_monotonic_ns"),
    )
    receipt = _receipt_core(state, pending, **fields)
    receipt_bytes = _stable_json_bytes(receipt, pretty=True)
    receipt_sha = _write_once(pending.receipt_path, receipt_bytes)
    attempt = pending.row["attempts"][-1]
    attempt["receipt"] = pending.receipt_path.as_posix()
    attempt["receipt_sha256"] = receipt_sha
    state.previous_receipt_sha256 = receipt_sha
    return receipt_sha


def _retry_delay(
    state: _AcquisitionState,
    *,
    status: int,
    attempt_no: int,
) -> tuple[int, str] | None:
    if status == 429 and attempt_no <= 3:
        seconds = state.http_429_backoff_seconds[attempt_no - 1]
        return seconds * 1_000_000_000, f"HTTP_429_ATTEMPT_{attempt_no}"
    if status != 429 and attempt_no <= 2:
        seconds = state.other_retryable_backoff_seconds[attempt_no - 1]
        return seconds * 1_000_000_000, f"OTHER_RETRYABLE_ATTEMPT_{attempt_no}"
    return None


def _acquire(
    *,
    state: _AcquisitionState,
    sequence: int,
    logical_key: str,
    kind: str,
    url: str,
) -> _PendingAttempt:
    request, parameters = _request(url, kind)
    attempt_rows: list[dict[str, Any]] = []
    logical_row = {
        "sequence": sequence,
        "logical_key": logical_key,
        "kind": kind,
        "canonical_url": url,
        "canonical_parameters": parameters,
        "attempts": attempt_rows,
    }
    directory = state.raw_run / "requests" / logical_key
    for attempt_no in ABSOLUTE_ATTEMPT_NUMBERS:
        _source_or_terminal(
            state,
            reason="SOURCE_DRIFT_PREFETCH",
            logical_key=logical_key,
            logical_sequence=sequence,
            attempt_no=attempt_no,
        )
        prefetch = state.monotonic_ns()
        if prefetch >= state.deadline_monotonic_ns:
            _terminal_schedule(
                state=state,
                reason="WALL_BUDGET_BEFORE_WIRE",
                planned_logical_key=logical_key,
                planned_logical_sequence=sequence,
                planned_attempt_no=attempt_no,
            )
            raise CorpusContractError("wall budget exhausted before wire")
        if state.wire_attempt_count >= MAX_WIRE_ATTEMPTS:
            _terminal_schedule(
                state=state,
                reason="WIRE_ATTEMPT_CAP_EXHAUSTED",
                planned_logical_key=logical_key,
                planned_logical_sequence=sequence,
                planned_attempt_no=attempt_no,
            )
            raise CorpusContractError("global wire-attempt cap exhausted")
        timeout_ns = min(
            TIMEOUT_CAP_NS, state.deadline_monotonic_ns - prefetch
        )
        if timeout_ns <= 0:
            _terminal_schedule(
                state=state,
                reason="NONPOSITIVE_TIMEOUT",
                planned_logical_key=logical_key,
                planned_logical_sequence=sequence,
                planned_attempt_no=attempt_no,
            )
            raise CorpusContractError("nonpositive request timeout")
        state.wire_attempt_count += 1
        wire_no = state.wire_attempt_count
        try:
            response = state.fetcher(
                request, _timeout_seconds_without_up_round(timeout_ns)
            )
        except Exception as exc:
            _terminal_schedule(
                state=state,
                reason="TRANSPORT_EXCEPTION",
                planned_logical_key=logical_key,
                planned_logical_sequence=sequence,
                planned_attempt_no=attempt_no,
            )
            category, safe_message = _safe_transport_error(exc)
            raise CorpusHttpError(
                f"{logical_key} transport failure: {category}: {safe_message}"
            ) from exc
        postfetch = state.monotonic_ns()
        _source_or_terminal(
            state,
            reason="SOURCE_DRIFT_POSTFETCH_PRE_RAW",
            logical_key=logical_key,
            logical_sequence=sequence,
            attempt_no=attempt_no,
        )
        if type(response) is not TransportResponse:
            _terminal_schedule(
                state=state,
                reason="INVALID_FETCHER_RESPONSE",
                planned_logical_key=logical_key,
                planned_logical_sequence=sequence,
                planned_attempt_no=attempt_no,
            )
            raise CorpusHttpError("fetcher returned invalid response")
        try:
            _response_times(response)
        except (CorpusError, TypeError, ValueError):
            response_clock_valid = False
        else:
            response_clock_valid = True
        try:
            _validate_request_evidence_headers(
                dict(request.header_items()), kind, url
            )
        except CorpusContractError:
            request_headers_valid = False
        else:
            request_headers_valid = True
        state.response_bytes += len(response.body)
        outcome = _response_outcome(
            request_headers_valid=request_headers_valid,
            response=response,
            canonical_url=url,
            max_response_bytes=state.max_response_bytes,
            cumulative_response_bytes=state.response_bytes,
            max_total_response_bytes=state.max_total_response_bytes,
            response_clock_valid=response_clock_valid,
        )
        if postfetch >= state.deadline_monotonic_ns:
            outcome = "WALL_DEADLINE_POSTFETCH"
        body_path = directory / f"attempt_{attempt_no:04d}.response"
        sidecar_path = directory / f"attempt_{attempt_no:04d}.request.json"
        receipt_path = directory / f"attempt_{attempt_no:04d}.receipt.json"
        body_sha = _write_once(body_path, response.body)
        sidecar_sha = _write_once(
            sidecar_path,
            _stable_json_bytes(
                _attempt_sidecar(
                    request,
                    parameters,
                    response,
                    outcome,
                    prefetch_monotonic_ns=prefetch,
                    postfetch_monotonic_ns=postfetch,
                    timeout_ns=timeout_ns,
                    deadline_monotonic_ns=state.deadline_monotonic_ns,
                ),
                pretty=True,
            ),
        )
        attempt_key = f"attempt_{wire_no:0{ATTEMPT_KEY_WIDTH}d}"
        attempt_rows.append(
            {
                "attempt_no": attempt_no,
                "attempt_key": attempt_key,
                "wire_attempt_global_no": wire_no,
                "body": body_path.as_posix(),
                "body_sha256": body_sha,
                "sidecar": sidecar_path.as_posix(),
                "sidecar_sha256": sidecar_sha,
                "receipt": None,
                "receipt_sha256": None,
                "outcome": outcome,
            }
        )
        pending = _PendingAttempt(
            response=response,
            row=logical_row,
            receipt_path=receipt_path,
            request=request,
            parameters=parameters,
            kind=kind,
            retry_after_present=_retry_after_present(response.headers),
            prefetch_monotonic_ns=prefetch,
            postfetch_monotonic_ns=postfetch,
        )
        _source_or_terminal(
            state,
            reason="SOURCE_DRIFT_POST_RAW_PRE_RECEIPT",
            logical_key=logical_key,
            logical_sequence=sequence,
            attempt_no=attempt_no,
        )
        if outcome == "OK":
            return pending
        retryable = (
            response.status in RETRYABLE_HTTP_STATUSES
            and outcome == f"HTTP_{response.status}"
        )
        terminal_reason = (
            "WALL_DEADLINE_POSTFETCH"
            if outcome == "WALL_DEADLINE_POSTFETCH"
            else "TERMINAL_OUTCOME"
        )
        delay: tuple[int, str] | None = None
        if retryable and pending.retry_after_present:
            terminal_reason = "RETRY_AFTER_PRESENT"
        elif retryable:
            delay = _retry_delay(
                state, status=response.status, attempt_no=attempt_no
            )
            if delay is None:
                terminal_reason = "RETRY_SCHEDULE_EXHAUSTED"
        if delay is None:
            _commit_receipt(
                state,
                pending,
                accepted=False,
                decision="NO_NEXT_WIRE",
                next_wire_scope=None,
                next_attempt_no=None,
                delay_rule="NONE_TERMINAL",
                requested_delay_ns=0,
                pre_sleep_monotonic_ns=None,
                post_sleep_monotonic_ns=None,
                terminal_reason=terminal_reason,
            )
            raise CorpusHttpError(
                f"{logical_key} failed closed: {outcome}"
            )
        delay_ns, delay_rule = delay
        pre_sleep, post_sleep = _sleep_authorization(
            state,
            logical_key=logical_key,
            logical_sequence=sequence,
            attempt_no=attempt_no + 1,
            delay_ns=delay_ns,
        )
        _commit_receipt(
            state,
            pending,
            accepted=False,
            decision="NEXT_WIRE",
            next_wire_scope="SAME_LOGICAL_RETRY",
            next_attempt_no=attempt_no + 1,
            delay_rule=delay_rule,
            requested_delay_ns=delay_ns,
            pre_sleep_monotonic_ns=pre_sleep,
            post_sleep_monotonic_ns=post_sleep,
            terminal_reason=None,
        )
    raise CorpusHttpError(f"{logical_key} exhausted attempts")


def _finalize_pending_for_next_logical(
    state: _AcquisitionState,
    *,
    next_logical_key: str,
    next_logical_sequence: int,
) -> None:
    pending = state.pending_ok
    if pending is None:
        return
    pre_sleep, post_sleep = _sleep_authorization(
        state,
        logical_key=next_logical_key,
        logical_sequence=next_logical_sequence,
        attempt_no=1,
        delay_ns=BASE_LOGICAL_DELAY_NS,
    )
    _commit_receipt(
        state,
        pending,
        accepted=True,
        decision="NEXT_WIRE",
        next_wire_scope="NEXT_LOGICAL",
        next_attempt_no=1,
        delay_rule="BASE_NEXT_LOGICAL",
        requested_delay_ns=BASE_LOGICAL_DELAY_NS,
        pre_sleep_monotonic_ns=pre_sleep,
        post_sleep_monotonic_ns=post_sleep,
        terminal_reason=None,
    )
    assert state.ledger is not None
    state.ledger.append(pending.row)
    state.pending_ok = None


def _accept(
    state: _AcquisitionState,
    *,
    logical_key: str,
    kind: str,
    url: str,
    parser: Callable[[TransportResponse], Any],
    final_wire: bool = False,
) -> Any:
    _finalize_pending_for_next_logical(
        state,
        next_logical_key=logical_key,
        next_logical_sequence=state.logical_sequence + 1,
    )
    state.logical_sequence += 1
    pending = _acquire(
        state=state,
        sequence=state.logical_sequence,
        logical_key=logical_key,
        kind=kind,
        url=url,
    )
    try:
        parsed = parser(pending.response)
    except Exception:
        _commit_receipt(
            state,
            pending,
            accepted=False,
            decision="NO_NEXT_WIRE",
            next_wire_scope=None,
            next_attempt_no=None,
            delay_rule="NONE_PARSER_ABORT",
            requested_delay_ns=0,
            pre_sleep_monotonic_ns=None,
            post_sleep_monotonic_ns=None,
            terminal_reason="COLLECTOR_ABORTED_BEFORE_NEXT_WIRE",
        )
        assert state.ledger is not None
        state.ledger.append(pending.row)
        raise
    if final_wire:
        _commit_receipt(
            state,
            pending,
            accepted=True,
            decision="NO_NEXT_WIRE",
            next_wire_scope=None,
            next_attempt_no=None,
            delay_rule="NONE_RUN_COMPLETE",
            requested_delay_ns=0,
            pre_sleep_monotonic_ns=None,
            post_sleep_monotonic_ns=None,
            terminal_reason="RUN_WIRE_COMPLETE",
        )
        assert state.ledger is not None
        state.ledger.append(pending.row)
    else:
        state.pending_ok = pending
    return parsed


def _json(body: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorpusSchemaError(f"invalid {label} JSON") from exc
    if type(value) is not dict:
        raise CorpusSchemaError(f"{label} must be an object")
    return value


def _envelope(body: bytes, label: str) -> dict[str, Any]:
    value = _json(body, label)
    if value.get("code") != "000000" or value.get("success") is not True or type(value.get("data")) is not dict:
        raise CorpusSchemaError(f"invalid {label} envelope")
    return value["data"]


def _positive_ms(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise CorpusSchemaError(f"{label} must be positive epoch milliseconds")
    return value


def _article_id(value: object) -> tuple[str, int | str]:
    if type(value) is int:
        if value <= 0 or len(str(value)) > 64:
            raise CorpusSchemaError("invalid integer article id")
        return "int", value
    if type(value) is str and ARTICLE_ID_STRING_PATTERN.fullmatch(value) is not None:
        return "str", value
    raise CorpusSchemaError("invalid article id")


def _article_id_key(row: Mapping[str, Any]) -> tuple[str, int | str]:
    tag = row.get("article_id_type")
    value = row.get("article_id")
    validated_tag, validated_value = _article_id(value)
    if tag != validated_tag:
        raise CorpusSchemaError("article id type tag mismatch")
    return validated_tag, validated_value


def _article_row(raw: object, catalog_id: int, page_no: int, index: int, raw_sha: str, completed: str) -> dict[str, Any]:
    if type(raw) is not dict:
        raise CorpusSchemaError("article must be an object")
    article_id = raw.get("id")
    code = raw.get("code")
    title = raw.get("title")
    release = _positive_ms(raw.get("releaseDate"), "releaseDate")
    article_id_type, article_id_value = _article_id(article_id)
    if type(code) is not str or CODE_PATTERN.fullmatch(code) is None:
        raise CorpusSchemaError("invalid article code")
    if type(title) is not str or not title:
        raise CorpusSchemaError("invalid article title")
    return {
        "catalog_id": catalog_id,
        "article_id_type": article_id_type,
        "article_id": article_id_value,
        "article_code": code,
        "title": title,
        "list_release_date_claim_ms": release,
        "list_page_no": page_no,
        "list_record_locator": f"$.data.catalogs[0].articles[{index}]",
        "list_raw_sha256": raw_sha,
        "list_response_completed_at_utc": completed,
        "raw_record_sha256": _sha256_bytes(_stable_json_bytes(raw)),
    }


def _parse_list(body: bytes, catalog_id: int, page_no: int, raw_sha: str, completed: str) -> tuple[int, list[dict[str, Any]]]:
    data = _envelope(body, "list")
    catalogs = data.get("catalogs")
    if type(catalogs) is not list or len(catalogs) != 1 or type(catalogs[0]) is not dict:
        raise CorpusSchemaError("list must contain exactly one catalog object")
    catalog = catalogs[0]
    if type(catalog.get("catalogId")) is not int or catalog["catalogId"] != catalog_id:
        raise CorpusSchemaError("list catalogId does not match request")
    total = catalog.get("total")
    articles = catalog.get("articles")
    if type(total) is not int or total < 0 or type(articles) is not list:
        raise CorpusSchemaError("invalid list total/articles")
    return total, [
        _article_row(item, catalog_id, page_no, index, raw_sha, completed)
        for index, item in enumerate(articles)
    ]


def _parse_detail(body: bytes, expected: Mapping[str, Any], raw_sha: str, completed: str) -> dict[str, Any]:
    data = _envelope(body, "detail")
    article_id = data.get("id")
    code = data.get("code")
    title = data.get("title")
    article_body = data.get("body")
    publish = _positive_ms(data.get("publishDate"), "publishDate")
    catalog_id = data.get("firstCatalogId")
    detail_id_type, detail_id_value = _article_id(article_id)
    if (
        detail_id_type != expected["article_id_type"]
        or detail_id_value != expected["article_id"]
        or code != expected["article_code"]
    ):
        raise CorpusSchemaError("detail id/code mismatch")
    if type(catalog_id) is not int or catalog_id != expected["catalog_id"]:
        raise CorpusSchemaError("detail catalog mismatch")
    if type(title) is not str or title != expected["title"] or type(article_body) is not str:
        raise CorpusSchemaError("detail title/body mismatch")
    content_json_present = "contentJson" in data
    content_json = data.get("contentJson")
    body_bytes = article_body.encode("utf-8")
    content_json_bytes = (
        _stable_json_bytes(content_json) if content_json_present else None
    )
    return {
        **dict(expected),
        "detail_title": title,
        "detail_publish_date_claim_ms": publish,
        "detail_publish_minus_list_release_claim_ms": (
            publish - expected["list_release_date_claim_ms"]
        ),
        "detail_first_catalog_id": catalog_id,
        "detail_last_update_time_raw": data.get("lastUpdateTime"),
        "detail_body_sha256": _sha256_bytes(body_bytes),
        "detail_body_utf8_bytes": len(body_bytes),
        "detail_content_json_present": content_json_present,
        "detail_content_json_sha256": (
            _sha256_bytes(content_json_bytes)
            if content_json_bytes is not None
            else None
        ),
        "detail_content_json_utf8_bytes": (
            len(content_json_bytes) if content_json_bytes is not None else None
        ),
        "detail_record_locator": "$.data",
        "detail_raw_sha256": raw_sha,
        "detail_version_known_at_utc": completed,
        "detail_version_known_at_ms": _epoch_ms(completed, "detail known_at"),
        "detail_version_semantics": "EXACT_VERSION_FIRST_KNOWN_AT_RESPONSE_COMPLETION",
    }


def _time_ms(body: bytes) -> int:
    value = _json(body, "time").get("serverTime")
    return _positive_ms(value, "serverTime")


def _validate_page_shape(rows: Sequence[dict[str, Any]], page: int, pages: int, total: int, page_size: int) -> None:
    expected = page_size if page < pages else total - page_size * (pages - 1)
    if expected == 0 and total > 0:
        expected = page_size
    if len(rows) != expected:
        raise CorpusSchemaError(f"page {page} length {len(rows)} != {expected}")


def _anchor_hash(total: int, rows: Sequence[Mapping[str, Any]]) -> str:
    source_values = [
        {
            "catalog_id": row["catalog_id"],
            "article_id_type": row["article_id_type"],
            "article_id": row["article_id"],
            "article_code": row["article_code"],
            "title": row["title"],
            "list_release_date_claim_ms": row["list_release_date_claim_ms"],
        }
        for row in rows
    ]
    return _sha256_bytes(_stable_json_bytes({"total": total, "rows": source_values}))


def _semantic_inventory(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "catalog_id": row["catalog_id"],
            "article_id_type": row["article_id_type"],
            "article_id": row["article_id"],
            "article_code": row["article_code"],
            "title": row["title"],
            "list_release_date_claim_ms": row["list_release_date_claim_ms"],
        }
        for row in rows
    ]


def _keyset_sha256(values: Sequence[str]) -> str:
    return _sha256_bytes(_stable_json_bytes(sorted(values)))


def _runtime_contract(
    *,
    run_id: str,
    expected_extractor_sha256: str,
    start_monotonic_ns: int,
    deadline_monotonic_ns: int,
    http_429_backoff_seconds: Sequence[int],
    other_retryable_backoff_seconds: Sequence[int],
    max_response_bytes: int,
    max_total_response_bytes: int,
) -> dict[str, Any]:
    return {
        "version": EXTRACTOR_VERSION,
        "run_id": run_id,
        "expected_extractor_sha256": expected_extractor_sha256,
        "observed_extractor_sha256": _module_sha(),
        "start_monotonic_ns": start_monotonic_ns,
        "deadline_monotonic_ns": deadline_monotonic_ns,
        "monotonic_semantics": "PROCESS_LOCAL_ACQUISITION_TIMING_CLAIMS",
        "wall_budget_ns": WALL_BUDGET_NS,
        "timeout_cap_ns": TIMEOUT_CAP_NS,
        "base_logical_delay_ns": BASE_LOGICAL_DELAY_NS,
        "max_wire_attempts": MAX_WIRE_ATTEMPTS,
        "absolute_attempt_numbers": list(ABSOLUTE_ATTEMPT_NUMBERS),
        "retryable_http_statuses": sorted(RETRYABLE_HTTP_STATUSES),
        "http_429_backoff_seconds": list(http_429_backoff_seconds),
        "other_retryable_backoff_seconds": list(
            other_retryable_backoff_seconds
        ),
        "retry_after_presence_rule": "TERMINAL_ON_ANY_CASE_INSENSITIVE_PRESENCE",
        "attempt_commit_order": ["body", "sidecar", "receipt"],
        "max_response_bytes": max_response_bytes,
        "max_total_response_bytes": max_total_response_bytes,
    }


def _attempts_in_wire_order(
    ledger: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    attempts = [
        dict(attempt)
        for entry in ledger
        for attempt in entry.get("attempts", [])
    ]
    attempts.sort(key=lambda row: row.get("wire_attempt_global_no", -1))
    return attempts


def _build_success_raw_summary(
    *,
    state: _AcquisitionState,
    runtime_contract_path: Path,
    runtime_contract_sha256: str,
    ledger_path: Path,
    ledger_sha256: str,
    selected_detail_keys: Sequence[str],
) -> dict[str, Any]:
    if (state.raw_run / "terminal_schedule.json").exists():
        raise CorpusIntegrityError("terminal schedule forbids success")
    if state.pending_ok is not None or state.ledger is None:
        raise CorpusIntegrityError("pending accepted response forbids success")
    attempts = _attempts_in_wire_order(state.ledger)
    if [row.get("wire_attempt_global_no") for row in attempts] != list(
        range(1, len(attempts) + 1)
    ):
        raise CorpusIntegrityError("wire attempt sequence is not contiguous")
    attempt_keys = [str(row.get("attempt_key")) for row in attempts]
    if len(set(attempt_keys)) != len(attempt_keys):
        raise CorpusIntegrityError("duplicate attempt key")
    body_paths = [Path(str(row.get("body"))) for row in attempts]
    sidecar_paths = [Path(str(row.get("sidecar"))) for row in attempts]
    receipt_paths = [Path(str(row.get("receipt"))) for row in attempts]
    if any(
        len({path.resolve() for path in paths}) != len(attempts)
        for paths in (body_paths, sidecar_paths, receipt_paths)
    ):
        raise CorpusIntegrityError("attempt artifact path is not one-to-one")
    if any(
        row.get("receipt") is None
        or row.get("receipt_sha256") is None
        or not body.exists()
        or not sidecar.exists()
        or not receipt.exists()
        for row, body, sidecar, receipt in zip(
            attempts, body_paths, sidecar_paths, receipt_paths
        )
    ):
        raise CorpusIntegrityError("attempt artifact bijection is incomplete")
    expected_files = {
        runtime_contract_path.resolve(),
        ledger_path.resolve(),
        *(path.resolve() for path in body_paths),
        *(path.resolve() for path in sidecar_paths),
        *(path.resolve() for path in receipt_paths),
    }
    observed_files = {
        path.resolve()
        for path in state.raw_run.rglob("*")
        if path.is_file()
        and path.resolve() != (state.raw_run / "summary.json").resolve()
    }
    if observed_files != expected_files:
        raise CorpusIntegrityError("raw success artifact bijection mismatch")
    previous: str | None = None
    receipt_tree: list[dict[str, Any]] = []
    accepted_detail_keys: list[str] = []
    outcome_counts: dict[str, int] = {}
    for entry in state.ledger:
        for attempt in entry["attempts"]:
            receipt_path = Path(attempt["receipt"])
            receipt_bytes = receipt_path.read_bytes()
            receipt_sha = _sha256_bytes(receipt_bytes)
            receipt = _load_json(receipt_path)
            if (
                receipt_sha != attempt["receipt_sha256"]
                or receipt.get("previous_receipt_sha256") != previous
                or receipt.get("attempt_key") != attempt["attempt_key"]
            ):
                raise CorpusIntegrityError("receipt chain mismatch")
            previous = receipt_sha
            receipt_tree.append(
                {
                    "attempt_key": attempt["attempt_key"],
                    "path": receipt_path.as_posix(),
                    "sha256": receipt_sha,
                }
            )
            outcome = str(attempt["outcome"])
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        final_receipt = _load_json(Path(entry["attempts"][-1]["receipt"]))
        if entry["kind"] == "detail" and final_receipt.get("accepted") is True:
            accepted_detail_keys.append(
                f"{entry['canonical_parameters']['articleCode']}"
            )
    if sorted(selected_detail_keys) != sorted(accepted_detail_keys):
        raise CorpusIntegrityError("selected/detail accepted keyset mismatch")
    if not state.ledger or state.ledger[-1].get("logical_key") != "time_after":
        raise CorpusIntegrityError("accepted time_after is not final logical request")
    final_receipt = _load_json(
        Path(state.ledger[-1]["attempts"][-1]["receipt"])
    )
    if (
        final_receipt.get("accepted") is not True
        or final_receipt.get("decision") != "NO_NEXT_WIRE"
        or final_receipt.get("terminal_reason") != "RUN_WIRE_COMPLETE"
    ):
        raise CorpusIntegrityError("accepted time_after final receipt mismatch")
    tree_rows = []
    for path in sorted(
        expected_files,
        key=lambda item: item.relative_to(state.raw_run.resolve()).as_posix(),
    ):
        relative = path.relative_to(state.raw_run.resolve()).as_posix()
        tree_rows.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    body_keys = [
        Path(row["body"]).parent.as_posix() + "/" + row["attempt_key"]
        for row in attempts
    ]
    sidecar_keys = [
        Path(row["sidecar"]).parent.as_posix() + "/" + row["attempt_key"]
        for row in attempts
    ]
    receipt_keys = [
        Path(row["receipt"]).parent.as_posix() + "/" + row["attempt_key"]
        for row in attempts
    ]
    normalized_attempt_keys = [
        Path(row["body"]).parent.as_posix() + "/" + row["attempt_key"]
        for row in attempts
    ]
    return {
        "version": EXTRACTOR_VERSION,
        "run_id": state.run_id,
        "success_complete": True,
        "runtime_contract": {
            "path": runtime_contract_path.as_posix(),
            "sha256": runtime_contract_sha256,
        },
        "request_ledger": {
            "path": ledger_path.as_posix(),
            "sha256": ledger_sha256,
        },
        "final_receipt_sha256": previous,
        "receipt_tree_sha256": _sha256_bytes(
            _stable_json_bytes(receipt_tree)
        ),
        "receipt_tree": receipt_tree,
        "raw_artifact_tree_sha256": _sha256_bytes(
            _stable_json_bytes(tree_rows)
        ),
        "raw_artifact_tree": tree_rows,
        "raw_tree_excludes": [
            "summary.json",
            "processed artifacts",
            "external corpus summary/schema/source contract",
        ],
        "selected_detail_keyset_sha256": _keyset_sha256(
            selected_detail_keys
        ),
        "accepted_detail_keyset_sha256": _keyset_sha256(
            accepted_detail_keys
        ),
        "attempt_keyset_sha256": _keyset_sha256(normalized_attempt_keys),
        "body_keyset_sha256": _keyset_sha256(body_keys),
        "sidecar_keyset_sha256": _keyset_sha256(sidecar_keys),
        "receipt_keyset_sha256": _keyset_sha256(receipt_keys),
        "logical_request_count": len(state.ledger),
        "wire_attempt_count": len(attempts),
        "outcome_counts": dict(sorted(outcome_counts.items())),
    }


def _source_contract() -> dict[str, Any]:
    return {
        "version": EXTRACTOR_VERSION,
        "corpus_completeness_scope": "all articles visible through the frozen CMS catalogs during both complete acquisition passes; excludes deleted articles and historical versions not returned now",
        "catalog_ids": list(CATALOG_IDS),
        "list_url_template": LIST_BASE + "?type=1&catalogId={48|161}&pageNo=N&pageSize=50",
        "detail_url_template": DETAIL_BASE + "?articleCode={validated_then_quote_safe_empty_opaque_code}",
        "time_url": TIME_URL,
        "authentication": "NONE",
        "locale": REQUEST_HEADERS["Accept-Language"],
        "fixed_request_headers": REQUEST_HEADERS,
        "transport_injected_request_headers": {
            "Host": "optional in Request evidence; if present, must exactly equal the canonical endpoint hostname"
        },
        "selected_response_version_headers": ["ETag", "Last-Modified", "Age", "Cache-Control", "Date"],
        "response_version_header_semantics": "transport/version clues only; never publish/effective/known_at",
        "temporal_semantics": {
            "releaseDate": "independent list_release_date_claim_ms only; selection clock claim, never effective time",
            "publishDate": "independent detail_publish_date_claim_ms only; never reconciled with list claim",
            "detail_publish_minus_list_release_claim_ms": "exact signed subtraction with no tolerance, reconciliation, ordering assumption, or magnitude bound",
            "detail_version_known_at": "detail response completion",
            "lastUpdateTime": "raw untrusted metadata; not version history",
        },
        "production_response_shape": {
            "list": "$.data.catalogs must contain exactly one object whose integer catalogId equals the request; total/articles come from $.data.catalogs[0]",
            "detail_body": "$.data.body; processed output retains SHA-256 and UTF-8 byte length only",
            "detail_catalog": "$.data.firstCatalogId must equal the selected list catalog",
            "detail_content_json": "optional JSON value; processed output retains presence plus canonical-JSON SHA-256 and byte length only",
        },
        "opaque_article_identifier_contract": {
            "code": "exact JSON str matching ^(?:[0-9a-f]{32}|[0-9]{1,64})$; no coercion or normalization",
            "code_kind": "forbidden; 32-digit overlap remains opaque",
            "detail_url": "validate, urllib.parse.quote(code, safe=''), exact parse/rebuild",
            "id_int": "type is int, bool excluded, positive, at most 64 decimal digits",
            "id_str": "exact [0-9]{1,64}; zero and leading zeros preserved",
            "id_equality": "list/detail exact type tag and value",
            "id_uniqueness": "type-aware",
        },
        "stability_rule": "two complete ordered traversals of every list page; totals, page shapes, per-page semantic hashes and full semantic inventory must match; pass 2 is not merged",
        "source_binding_rule": "self-contained extractor expected SHA-256 is checked before lease; before/after runtime contract; before/after every sleep; prefetch; postfetch/pre-raw; post-raw/pre-receipt; before/after raw summary; and before/after final artifacts",
        "implementation_dependencies": "stdlib only; no v3 helper/runtime import",
        "time_claim_discrepancy_rule": "time_claim_discrepancies.jsonl is the exact nonzero-delta subset of detail_index rows sorted by catalog_id/article_code; empty is valid; no expected discrepancy count",
        "forbidden_temporal_aliases": [
            "claimed_published_at_ms",
            "claimed_published_at_source_field",
            "detail_publish_date_ms",
            "claimed_release_interval_ms",
            "interval_counts",
        ],
        "retry_matrix": {
            "statuses": sorted(RETRYABLE_HTTP_STATUSES),
            "attempt_numbers": list(ABSOLUTE_ATTEMPT_NUMBERS),
            "http_429_delays_seconds": list(FROZEN_HTTP_429_BACKOFF_SECONDS),
            "other_retryable_delays_seconds": list(FROZEN_OTHER_RETRYABLE_BACKOFF_SECONDS),
            "retry_after": "any case-insensitive presence on a retryable status is terminal",
            "eligible_outcome": "only recomputed well-formed HTTP_<status>",
            "all_other_outcomes": "terminal",
        },
        "hardcoded_timing_policy": {
            "base_logical_delay_ns": BASE_LOGICAL_DELAY_NS,
            "timeout_cap_ns": TIMEOUT_CAP_NS,
            "wall_budget_ns": WALL_BUDGET_NS,
            "max_wire_attempts": MAX_WIRE_ATTEMPTS,
        },
        "receipt_rule": "attempt_NNNN.receipt.json is immutable canonical JSON written after its exact body+sidecar and any NEXT_WIRE sleep; receipts form one global SHA-256 chain",
        "terminal_schedule_rule": "write-once no-wire/receiptless controlled failure record; its presence forbids success and it is not an attempt, wire, or receipt-chain member",
        "raw_summary_rule": "raw summary binds runtime/ledger, final receipt head, ordered receipt tree, sorted raw-artifact tree, exact keysets, and counts; raw summary excludes itself; external and processed artifacts are outside the promised tree",
        "root_summary_integrity_rule": "corpus_summary.json must be exact canonical pretty JSON with exactly the generated top-level field set; unknown fields and duplicate keys fail closed",
        "success_bijection_rule": "selected detail keys exactly equal accepted detail keys and ledger attempt keys exactly equal body/sidecar/receipt keys with no orphan, pending, terminal, or extra artifact",
        "monotonic_rule": "process-local nanosecond claims only; validated from lease start through accepted time_after receipt under the absolute wall deadline",
        "wire_attempt_rule": "hardcoded cap 3464 covers every fetch; each wire needs the immediately preceding receipt authorization except the first",
        "wire_attempt_summary_fields": {
            "max_wire_attempts": "positive acquisition-wide runtime bound",
            "wire_attempt_count": "all fetch calls, including retry and transport-error attempts",
        },
        "forbidden_derivations": ["pair", "event", "effective time", "listing interval", "eligibility"],
    }


def _schema() -> dict[str, Any]:
    return {
        "version": EXTRACTOR_VERSION,
        "processed_artifacts": ["inventory.jsonl", "detail_index.jsonl", "time_claim_discrepancies.jsonl", "corpus_summary.json", "schema.json", "source_contract.json"],
        "forbidden_artifacts": ["events.jsonl", "listing_intervals.jsonl", "alpha.json"],
        "inventory_key": ["catalog_id", "article_id_type", "article_id", "article_code"],
        "article_code_regex": r"^(?:[0-9a-f]{32}|[0-9]{1,64})$",
        "article_id_types": ["int", "str"],
        "temporal_fields": ["list_release_date_claim_ms", "detail_publish_date_claim_ms", "detail_publish_minus_list_release_claim_ms"],
        "forbidden_processed_fields": ["code_kind", "claimed_published_at_ms", "claimed_published_at_source_field", "detail_publish_date_ms"],
        "forbidden_summary_fields": ["claimed_release_interval_ms", "interval_counts", "expected_interval_counts"],
        "detail_known_at_rule": "response completion; independent source claims never become effective times",
        "discrepancy_artifact_rule": "exact nonzero-delta detail-index subset sorted catalog_id/article_code; empty valid; SHA/path/count trusted-rebuilt",
        "list_response_locator": "$.data.catalogs[0]",
        "detail_body_fields": ["body", "contentJson"],
        "stability_gate": "two complete list passes; pass 1 alone is processed inventory",
        "summary_wire_fields": ["wire_attempt_count", "max_wire_attempts"],
        "summary_top_level_fields": sorted(SUMMARY_TOP_LEVEL_FIELDS),
        "raw_artifacts": [
            "runtime_contract.json",
            "request_ledger.jsonl",
            "summary.json",
            "requests/**/attempt_NNNN.response",
            "requests/**/attempt_NNNN.request.json",
            "requests/**/attempt_NNNN.receipt.json",
        ],
        "raw_summary_required_fields": [
            "runtime_contract",
            "request_ledger",
            "final_receipt_sha256",
            "receipt_tree_sha256",
            "raw_artifact_tree_sha256",
            "selected_detail_keyset_sha256",
            "accepted_detail_keyset_sha256",
            "attempt_keyset_sha256",
            "body_keyset_sha256",
            "sidecar_keyset_sha256",
            "receipt_keyset_sha256",
            "logical_request_count",
            "wire_attempt_count",
            "outcome_counts",
        ],
    }


def _module_sha() -> str:
    return _sha256_file(Path(__file__))


def _assert_source_binding(expected_extractor_sha256: str) -> None:
    if _module_sha() != expected_extractor_sha256:
        raise CorpusIntegrityError("extractor source SHA-256 mismatch")


def run_corpus(
    *,
    run_id: str,
    expected_extractor_sha256: str,
    raw_root: Path,
    processed_root: Path,
    summary_output: Path,
    schema_output: Path,
    source_contract_output: Path,
    catalog_ids: Sequence[int] = CATALOG_IDS,
    page_size: int = PAGE_SIZE,
    list_release_date_claim_start_ms: int,
    list_release_date_claim_end_ms_exclusive: int,
    expected_totals: Mapping[int, int],
    expected_list_release_date_claim_interval_counts: Mapping[int, int],
    max_pages_per_catalog: int,
    max_articles: int,
    max_response_bytes: int,
    max_total_response_bytes: int,
    max_clock_skew_ms: int,
    http_429_backoff_seconds: Sequence[int] = (
        FROZEN_HTTP_429_BACKOFF_SECONDS
    ),
    other_retryable_backoff_seconds: Sequence[int] = (
        FROZEN_OTHER_RETRYABLE_BACKOFF_SECONDS
    ),
    fetcher: Fetcher | None = None,
    sleeper: Sleeper = time.sleep,
    monotonic_ns: Monotonic = time.monotonic_ns,
) -> dict[str, Any]:
    if type(run_id) is not str or SAFE_ID_PATTERN.fullmatch(run_id) is None:
        raise CorpusContractError("invalid run_id")
    if (
        type(expected_extractor_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", expected_extractor_sha256) is None
    ):
        raise CorpusIntegrityError("invalid expected extractor SHA-256")
    _assert_source_binding(expected_extractor_sha256)
    if tuple(catalog_ids) != CATALOG_IDS or page_size != PAGE_SIZE:
        raise CorpusContractError("catalog/page contract drift")
    if (
        type(list_release_date_claim_start_ms) is not int
        or type(list_release_date_claim_end_ms_exclusive) is not int
        or list_release_date_claim_start_ms
        >= list_release_date_claim_end_ms_exclusive
    ):
        raise CorpusContractError("invalid list-release-date-claim interval")
    if (
        set(expected_totals) != set(CATALOG_IDS)
        or set(expected_list_release_date_claim_interval_counts)
        != set(CATALOG_IDS)
        or any(type(value) is not int or value < 0 for value in expected_totals.values())
        or any(
            type(value) is not int or value < 0
            for value in expected_list_release_date_claim_interval_counts.values()
        )
    ):
        raise CorpusContractError("expected counts must cover frozen catalogs")
    if (
        type(max_pages_per_catalog) is not int
        or max_pages_per_catalog < 1
        or type(max_articles) is not int
        or max_articles < 0
        or type(max_response_bytes) is not int
        or max_response_bytes < 1
        or type(max_total_response_bytes) is not int
        or max_total_response_bytes < max_response_bytes
        or type(max_clock_skew_ms) is not int
        or max_clock_skew_ms < 0
    ):
        raise CorpusContractError("invalid acquisition bounds")
    if (
        tuple(http_429_backoff_seconds)
        != FROZEN_HTTP_429_BACKOFF_SECONDS
        or tuple(other_retryable_backoff_seconds)
        != FROZEN_OTHER_RETRYABLE_BACKOFF_SECONDS
        or any(
            type(value) is not int or value <= 0
            for value in (
                *http_429_backoff_seconds,
                *other_retryable_backoff_seconds,
            )
        )
    ):
        raise CorpusContractError("retry schedule contract drift")
    active_fetcher: Fetcher = fetcher or (
        lambda request, timeout: _bounded_default_fetcher(
            request, timeout, max_response_bytes
        )
    )
    raw_run = raw_root / run_id
    processed_run = processed_root / run_id
    if any(path.exists() for path in (raw_run, processed_run, summary_output, schema_output, source_contract_output)):
        raise CorpusExistingError("run outputs already exist")
    raw_run.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw_run.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise CorpusExistingError("exclusive run lease already exists") from exc
    start_monotonic_ns = monotonic_ns()
    deadline_monotonic_ns = start_monotonic_ns + WALL_BUDGET_NS
    state = _AcquisitionState(
        run_id=run_id,
        raw_run=raw_run,
        expected_extractor_sha256=expected_extractor_sha256,
        deadline_monotonic_ns=deadline_monotonic_ns,
        max_response_bytes=max_response_bytes,
        max_total_response_bytes=max_total_response_bytes,
        fetcher=active_fetcher,
        sleeper=sleeper,
        monotonic_ns=monotonic_ns,
        http_429_backoff_seconds=tuple(http_429_backoff_seconds),
        other_retryable_backoff_seconds=tuple(
            other_retryable_backoff_seconds
        ),
    )
    _source_or_terminal(
        state,
        reason="SOURCE_DRIFT_PRE_RUNTIME_CONTRACT",
        logical_key=None,
        logical_sequence=None,
        attempt_no=None,
    )
    runtime_contract_path = raw_run / "runtime_contract.json"
    runtime_contract_sha = _write_once(
        runtime_contract_path,
        _stable_json_bytes(
            _runtime_contract(
                run_id=run_id,
                expected_extractor_sha256=expected_extractor_sha256,
                start_monotonic_ns=start_monotonic_ns,
                deadline_monotonic_ns=deadline_monotonic_ns,
                http_429_backoff_seconds=http_429_backoff_seconds,
                other_retryable_backoff_seconds=(
                    other_retryable_backoff_seconds
                ),
                max_response_bytes=max_response_bytes,
                max_total_response_bytes=max_total_response_bytes,
            ),
            pretty=True,
        ),
    )
    _source_or_terminal(
        state,
        reason="SOURCE_DRIFT_POST_RUNTIME_CONTRACT",
        logical_key=None,
        logical_sequence=None,
        attempt_no=None,
    )
    if monotonic_ns() >= deadline_monotonic_ns:
        _terminal_schedule(
            state=state,
            reason="WALL_BUDGET_AFTER_RUNTIME_CONTRACT",
            planned_logical_key="time_before",
            planned_logical_sequence=1,
            planned_attempt_no=1,
        )
        raise CorpusContractError("wall budget exhausted after runtime contract")

    before_ms, time_before = _accept(
        state,
        logical_key="time_before",
        kind="time",
        url=TIME_URL,
        parser=lambda response: (_time_ms(response.body), response),
    )
    pass_totals: dict[int, dict[int, int]] = {}
    pass_page_counts: dict[int, dict[int, int]] = {}
    pass_page_anchors: dict[int, dict[int, dict[int, str]]] = {}

    def acquire_list_pass(
        pass_number: int,
    ) -> list[dict[str, Any]]:
        pass_rows: list[dict[str, Any]] = []
        totals_for_pass: dict[int, int] = {}
        pages_for_pass: dict[int, int] = {}
        anchors_for_pass: dict[int, dict[int, str]] = {}
        for catalog_id in catalog_ids:
            total, rows = _accept(
                state,
                logical_key=(
                    f"catalog_{catalog_id}/pass_{pass_number}/page_0001"
                ),
                kind="list",
                url=_list_url(catalog_id, 1, page_size),
                parser=lambda response, catalog_id=catalog_id: _parse_list(
                    response.body,
                    catalog_id,
                    1,
                    _sha256_bytes(response.body),
                    response.response_completed_at_utc,
                ),
            )
            pages = math.ceil(total / page_size) if total else 1
            if pages > max_pages_per_catalog:
                raise CorpusContractError("catalog page bound exceeded")
            _validate_page_shape(rows, 1, pages, total, page_size)
            totals_for_pass[catalog_id] = total
            pages_for_pass[catalog_id] = pages
            anchors_for_pass[catalog_id] = {1: _anchor_hash(total, rows)}
            pass_rows.extend(rows)
            for page in range(2, pages + 1):
                current_total, current_rows = _accept(
                    state,
                    logical_key=(
                        f"catalog_{catalog_id}/pass_{pass_number}/"
                        f"page_{page:04d}"
                    ),
                    kind="list",
                    url=_list_url(catalog_id, page, page_size),
                    parser=lambda response, catalog_id=catalog_id, page=page: (
                        _parse_list(
                            response.body,
                            catalog_id,
                            page,
                            _sha256_bytes(response.body),
                            response.response_completed_at_utc,
                        )
                    ),
                )
                if current_total != total:
                    raise CorpusSchemaError("catalog total drift across pages")
                _validate_page_shape(
                    current_rows, page, pages, total, page_size
                )
                anchors_for_pass[catalog_id][page] = _anchor_hash(
                    current_total, current_rows
                )
                pass_rows.extend(current_rows)
        for catalog_id in catalog_ids:
            subset = [
                row for row in pass_rows if row["catalog_id"] == catalog_id
            ]
            if len(subset) != totals_for_pass[catalog_id]:
                raise CorpusSchemaError("pagination union count mismatch")
            if (
                len({_article_id_key(row) for row in subset}) != len(subset)
                or len({row["article_code"] for row in subset}) != len(subset)
            ):
                raise CorpusSchemaError("duplicate article id/code across pages")
        if len({row["article_code"] for row in pass_rows}) != len(pass_rows):
            raise CorpusSchemaError(
                "article code appears in multiple frozen catalogs"
            )
        pass_totals[pass_number] = totals_for_pass
        pass_page_counts[pass_number] = pages_for_pass
        pass_page_anchors[pass_number] = anchors_for_pass
        return pass_rows

    inventory_pass_1 = acquire_list_pass(1)
    inventory = sorted(
        inventory_pass_1,
        key=lambda row: (
            row["catalog_id"],
            -row["list_release_date_claim_ms"],
            row["article_code"],
        ),
    )
    selected = [
        row for row in inventory
        if list_release_date_claim_start_ms
        <= row["list_release_date_claim_ms"]
        < list_release_date_claim_end_ms_exclusive
    ]
    if len(selected) > max_articles:
        raise CorpusContractError("selected article bound exceeded")
    details: list[dict[str, Any]] = []
    for row in sorted(selected, key=lambda item: (item["catalog_id"], item["article_code"])):
        details.append(
            _accept(
                state,
                logical_key=f"details/{row['article_code']}",
                kind="detail",
                url=_detail_url(row["article_code"]),
                parser=lambda response, row=row: _parse_detail(
                    response.body,
                    row,
                    _sha256_bytes(response.body),
                    response.response_completed_at_utc,
                ),
            )
        )

    inventory_pass_2 = acquire_list_pass(2)
    after_ms, time_after = _accept(
        state,
        logical_key="time_after",
        kind="time",
        url=TIME_URL,
        parser=lambda response: (_time_ms(response.body), response),
        final_wire=True,
    )
    if before_ms > after_ms:
        raise CorpusContractError("Binance time bracket is reversed")
    local_times = []
    assert state.ledger is not None
    for row in state.ledger:
        last = row["attempts"][-1]
        meta = json.loads(Path(last["sidecar"]).read_text(encoding="utf-8"))
        local_times.extend(
            [
                _parse_utc(meta["response"]["request_started_at_utc"], "request start"),
                _parse_utc(meta["response"]["response_completed_at_utc"], "response completion"),
            ]
        )
    if any(b < a for a, b in zip(local_times, local_times[1:])):
        raise CorpusContractError("logical request clocks are not monotone")
    for response, server_ms in ((time_before, before_ms), (time_after, after_ms)):
        completed_ms = _epoch_ms(response.response_completed_at_utc, "time completion")
        if abs(completed_ms - server_ms) > max_clock_skew_ms:
            raise CorpusContractError("server/local clock skew exceeds bound")

    list_release_date_claim_interval_counts = {
        catalog_id: sum(row["catalog_id"] == catalog_id for row in selected)
        for catalog_id in catalog_ids
    }
    contract_failures: list[str] = []
    for catalog_id in catalog_ids:
        if pass_totals[1][catalog_id] != expected_totals[catalog_id]:
            contract_failures.append(f"CATALOG_{catalog_id}_TOTAL_MISMATCH")
        if (
            list_release_date_claim_interval_counts[catalog_id]
            != expected_list_release_date_claim_interval_counts[catalog_id]
        ):
            contract_failures.append(
                f"CATALOG_{catalog_id}_LIST_RELEASE_DATE_CLAIM_INTERVAL_COUNT_MISMATCH"
            )
        pass_1_semantic = _semantic_inventory(
            [row for row in inventory_pass_1 if row["catalog_id"] == catalog_id]
        )
        pass_2_semantic = _semantic_inventory(
            [row for row in inventory_pass_2 if row["catalog_id"] == catalog_id]
        )
        if (
            pass_totals[2][catalog_id] != pass_totals[1][catalog_id]
            or pass_page_counts[2][catalog_id]
            != pass_page_counts[1][catalog_id]
            or pass_page_anchors[2][catalog_id]
            != pass_page_anchors[1][catalog_id]
            or pass_2_semantic != pass_1_semantic
        ):
            contract_failures.append(f"CATALOG_{catalog_id}_FULL_PASS_DRIFT")

    expected_request_count = 2 + 2 * sum(
        math.ceil(expected_totals[catalog_id] / page_size)
        if expected_totals[catalog_id]
        else 1
        for catalog_id in catalog_ids
    ) + sum(expected_list_release_date_claim_interval_counts.values())
    if len(state.ledger) != expected_request_count:
        contract_failures.append("REQUEST_COUNT_MISMATCH")

    _source_or_terminal(
        state,
        reason="SOURCE_DRIFT_PRE_RAW_SUMMARY",
        logical_key=None,
        logical_sequence=None,
        attempt_no=None,
    )
    ledger_path = raw_run / "request_ledger.jsonl"
    ledger_sha = _write_once(ledger_path, _stable_jsonl(state.ledger))
    selected_detail_keys = [row["article_code"] for row in selected]
    raw_summary = _build_success_raw_summary(
        state=state,
        runtime_contract_path=runtime_contract_path,
        runtime_contract_sha256=runtime_contract_sha,
        ledger_path=ledger_path,
        ledger_sha256=ledger_sha,
        selected_detail_keys=selected_detail_keys,
    )
    raw_summary_path = raw_run / "summary.json"
    raw_summary_sha = _write_once(
        raw_summary_path, _stable_json_bytes(raw_summary, pretty=True)
    )
    _source_or_terminal(
        state,
        reason="SOURCE_DRIFT_POST_RAW_SUMMARY",
        logical_key=None,
        logical_sequence=None,
        attempt_no=None,
    )
    inventory_path = processed_run / "inventory.jsonl"
    details_path = processed_run / "detail_index.jsonl"
    discrepancy_path = processed_run / "time_claim_discrepancies.jsonl"
    sorted_details = sorted(
        details, key=lambda row: (row["catalog_id"], row["article_code"])
    )
    discrepancies = [
        row
        for row in sorted_details
        if row["detail_publish_minus_list_release_claim_ms"] != 0
    ]
    inventory_sha = _write_once(inventory_path, _stable_jsonl(inventory))
    detail_sha = _write_once(details_path, _stable_jsonl(sorted_details))
    discrepancy_sha = _write_once(
        discrepancy_path, _stable_jsonl(discrepancies)
    )
    terminal = "NEEDS_MORE_DATA" if not contract_failures else "INCONCLUSIVE"
    summary = {
        "run_id": run_id,
        "terminal_status": terminal,
        "artifact_state": "ANNOUNCEMENT_CORPUS_AVAILABLE" if not contract_failures else None,
        "semantics": "CORPUS_ONLY; NOT_EVENT_OR_ELIGIBILITY_EVIDENCE",
        "catalog_totals": {str(key): value for key, value in pass_totals[1].items()},
        "expected_totals": {str(key): expected_totals[key] for key in catalog_ids},
        "page_counts": {str(key): value for key, value in pass_page_counts[1].items()},
        "list_release_date_claim_interval_counts": {
            str(key): value
            for key, value in list_release_date_claim_interval_counts.items()
        },
        "expected_list_release_date_claim_interval_counts": {
            str(key): expected_list_release_date_claim_interval_counts[key]
            for key in catalog_ids
        },
        "list_pass_stability": {
            "pass_1_page_anchor_sha256": {
                str(catalog_id): {
                    str(page): value
                    for page, value in pass_page_anchors[1][catalog_id].items()
                }
                for catalog_id in catalog_ids
            },
            "pass_2_page_anchor_sha256": {
                str(catalog_id): {
                    str(page): value
                    for page, value in pass_page_anchors[2][catalog_id].items()
                }
                for catalog_id in catalog_ids
            },
            "pass_1_full_inventory_sha256": _sha256_bytes(
                _stable_json_bytes(_semantic_inventory(inventory_pass_1))
            ),
            "pass_2_full_inventory_sha256": _sha256_bytes(
                _stable_json_bytes(_semantic_inventory(inventory_pass_2))
            ),
            "pass_2_merged_into_inventory": False,
        },
        "inventory_count": len(inventory),
        "detail_count": len(details),
        "time_claim_discrepancy_count": len(discrepancies),
        "contract_failures": contract_failures,
        "response_bytes": state.response_bytes,
        "request_count": len(state.ledger),
        "expected_request_count": expected_request_count,
        "wire_attempt_count": state.wire_attempt_count,
        "max_wire_attempts": MAX_WIRE_ATTEMPTS,
        "time_bracket_ms": {"before": before_ms, "after": after_ms},
        "max_clock_skew_ms": max_clock_skew_ms,
        "acquisition_bounds": {
            "max_pages_per_catalog": max_pages_per_catalog,
            "max_articles": max_articles,
            "max_response_bytes": max_response_bytes,
            "max_total_response_bytes": max_total_response_bytes,
            "timeout_cap_ns": TIMEOUT_CAP_NS,
            "wall_budget_ns": WALL_BUDGET_NS,
            "base_logical_delay_ns": BASE_LOGICAL_DELAY_NS,
            "absolute_attempt_numbers": list(ABSOLUTE_ATTEMPT_NUMBERS),
            "max_wire_attempts": MAX_WIRE_ATTEMPTS,
            "http_429_backoff_seconds": list(
                http_429_backoff_seconds
            ),
            "other_retryable_backoff_seconds": list(
                other_retryable_backoff_seconds
            ),
        },
        "request_ledger": {"path": ledger_path.as_posix(), "sha256": ledger_sha},
        "raw_summary": {
            "path": raw_summary_path.as_posix(),
            "sha256": raw_summary_sha,
        },
        "inventory": {"path": inventory_path.as_posix(), "sha256": inventory_sha},
        "detail_index": {"path": details_path.as_posix(), "sha256": detail_sha},
        "time_claim_discrepancies": {
            "path": discrepancy_path.as_posix(),
            "sha256": discrepancy_sha,
            "count": len(discrepancies),
        },
        "extractor_source_sha256": _module_sha(),
        "pre_network_expected_extractor_sha256": expected_extractor_sha256,
        "list_release_date_claim_interval_ms": [
            list_release_date_claim_start_ms,
            list_release_date_claim_end_ms_exclusive,
        ],
        "historical_eligibility_ready": False,
    }
    if set(summary) != SUMMARY_TOP_LEVEL_FIELDS:
        raise CorpusIntegrityError("generated summary top-level field contract drift")
    _source_or_terminal(
        state,
        reason="SOURCE_DRIFT_PRE_FINAL_ARTIFACTS",
        logical_key=None,
        logical_sequence=None,
        attempt_no=None,
    )
    _write_once(summary_output, _stable_json_bytes(summary, pretty=True))
    _write_once(schema_output, _stable_json_bytes(_schema(), pretty=True))
    _write_once(source_contract_output, _stable_json_bytes(_source_contract(), pretty=True))
    _source_or_terminal(
        state,
        reason="SOURCE_DRIFT_POST_FINAL_ARTIFACTS",
        logical_key=None,
        logical_sequence=None,
        attempt_no=None,
    )
    load_corpus(summary_output=summary_output, schema_output=schema_output, source_contract_output=source_contract_output)
    return summary


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = _strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorpusIntegrityError(f"cannot load {path}") from exc
    if type(value) is not dict:
        raise CorpusIntegrityError("trusted JSON must be an object")
    return value


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    body = path.read_bytes()
    try:
        rows = [
            _strict_json_loads(line)
            for line in body.decode("utf-8").splitlines()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorpusIntegrityError("invalid JSONL artifact") from exc
    if any(type(row) is not dict for row in rows) or _stable_jsonl(rows) != body:
        raise CorpusIntegrityError("JSONL is non-canonical")
    return rows, body


def _verified_response(
    entry: Mapping[str, Any],
    run_id: str,
    runtime: Mapping[str, Any],
    cumulative_response_bytes: list[int],
    receipt_chain_state: list[str | None],
    wire_state: list[int],
    authorization_state: list[dict[str, Any] | None],
    receipt_tree: list[dict[str, Any]],
    outcome_counts: dict[str, int],
) -> TransportResponse:
    attempts = entry.get("attempts")
    logical_key = entry.get("logical_key")
    if (
        type(attempts) is not list
        or not attempts
        or len(attempts) > len(ABSOLUTE_ATTEMPT_NUMBERS)
        or type(logical_key) is not str
    ):
        raise CorpusIntegrityError("invalid attempt ledger")
    directory = Path(attempts[-1].get("body", "")).parent
    logical_parts = tuple(Path(logical_key).parts)
    if (
        run_id not in directory.parts
        or len(directory.parts) < len(logical_parts) + 2
        or tuple(directory.parts[-len(logical_parts) :]) != logical_parts
        or directory.parts[-len(logical_parts) - 1] != "requests"
        or directory.parts[-len(logical_parts) - 2] != run_id
    ):
        raise CorpusIntegrityError("attempt escapes run root")
    expected_parameters = _validate_canonical_url(entry.get("canonical_url", ""), entry.get("kind", ""))
    if entry.get("canonical_parameters") != expected_parameters:
        raise CorpusIntegrityError("ledger canonical parameters mismatch")
    for number, attempt in enumerate(attempts, start=1):
        if set(attempt) != {
            "attempt_no",
            "attempt_key",
            "wire_attempt_global_no",
            "body",
            "body_sha256",
            "sidecar",
            "sidecar_sha256",
            "receipt",
            "receipt_sha256",
            "outcome",
        } or attempt.get("attempt_no") != number:
            raise CorpusIntegrityError("attempt gap")
        body_path = Path(attempt.get("body", ""))
        sidecar_path = Path(attempt.get("sidecar", ""))
        receipt_path = Path(attempt.get("receipt", ""))
        if (
            body_path.parent != directory
            or body_path.name != f"attempt_{number:04d}.response"
            or sidecar_path
            != directory / f"attempt_{number:04d}.request.json"
            or receipt_path
            != directory / f"attempt_{number:04d}.receipt.json"
        ):
            raise CorpusIntegrityError("attempt path mismatch")
        raw = body_path.read_bytes()
        sidecar_bytes = sidecar_path.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
        if (
            _sha256_bytes(raw) != attempt.get("body_sha256")
            or _sha256_bytes(sidecar_bytes) != attempt.get("sidecar_sha256")
            or _sha256_bytes(receipt_bytes) != attempt.get("receipt_sha256")
        ):
            raise CorpusIntegrityError("attempt hash mismatch")
        meta = _load_json(sidecar_path)
        receipt = _load_json(receipt_path)
        if (
            _stable_json_bytes(meta, pretty=True) != sidecar_bytes
            or _stable_json_bytes(receipt, pretty=True) != receipt_bytes
            or set(receipt) != RECEIPT_CORE_FIELDS
        ):
            raise CorpusIntegrityError("non-canonical sidecar/receipt")
        wire_state[0] += 1
        wire_no = wire_state[0]
        attempt_key = f"attempt_{wire_no:0{ATTEMPT_KEY_WIDTH}d}"
        if (
            attempt.get("wire_attempt_global_no") != wire_no
            or attempt.get("attempt_key") != attempt_key
        ):
            raise CorpusIntegrityError("global wire key mismatch")
        request_meta = meta.get("request")
        if type(request_meta) is not dict or (
            meta.get("outcome") != attempt.get("outcome")
            or request_meta.get("method") != "GET"
            or request_meta.get("canonical_url") != entry.get("canonical_url")
            or request_meta.get("canonical_parameters") != expected_parameters
            or request_meta.get("authentication") != "NONE"
        ):
            raise CorpusIntegrityError("attempt sidecar mismatch")
        raw_headers = request_meta.get("headers")
        if type(raw_headers) is not dict:
            raise CorpusIntegrityError("invalid request headers")
        try:
            _validate_request_evidence_headers(
                raw_headers, entry.get("kind", ""), entry.get("canonical_url", "")
            )
        except CorpusContractError:
            request_headers_valid = False
        else:
            request_headers_valid = True
        response = meta.get("response")
        if type(response) is not dict or response.get("body_sha256") != _sha256_bytes(raw) or response.get("body_bytes") != len(raw):
            raise CorpusIntegrityError("response binding mismatch")
        selected_headers = response.get("selected_headers")
        if (
            type(response.get("status")) is not int
            or type(selected_headers) is not dict
            or type(response.get("final_url")) is not str
        ):
            raise CorpusIntegrityError("response metadata invalid")
        reconstructed = TransportResponse(
            status=response["status"],
            headers=selected_headers,
            body=raw,
            final_url=response["final_url"],
            request_started_at_utc=response.get("request_started_at_utc"),
            response_completed_at_utc=response.get("response_completed_at_utc"),
        )
        try:
            _response_times(reconstructed)
        except (CorpusError, TypeError, ValueError) as exc:
            response_clock_valid = False
        else:
            response_clock_valid = True
        monotonic = meta.get("monotonic")
        if type(monotonic) is not dict or set(monotonic) != {
            "prefetch_monotonic_ns",
            "postfetch_monotonic_ns",
            "timeout_ns",
            "deadline_monotonic_ns",
            "semantics",
        }:
            raise CorpusIntegrityError("attempt monotonic evidence missing")
        prefetch = monotonic.get("prefetch_monotonic_ns")
        postfetch = monotonic.get("postfetch_monotonic_ns")
        timeout_ns = monotonic.get("timeout_ns")
        deadline = runtime["deadline_monotonic_ns"]
        if (
            type(prefetch) is not int
            or type(postfetch) is not int
            or type(timeout_ns) is not int
            or monotonic.get("deadline_monotonic_ns") != deadline
            or monotonic.get("semantics")
            != "PROCESS_LOCAL_ACQUISITION_TIMING_CLAIMS"
            or prefetch < runtime["start_monotonic_ns"]
            or prefetch >= deadline
            or postfetch < prefetch
            or postfetch >= deadline
            or timeout_ns != min(TIMEOUT_CAP_NS, deadline - prefetch)
            or timeout_ns <= 0
        ):
            raise CorpusIntegrityError("attempt monotonic contract mismatch")
        authorization = authorization_state[0]
        if wire_no == 1:
            if authorization is not None:
                raise CorpusIntegrityError("first wire has prior authorization")
        else:
            if type(authorization) is not dict:
                raise CorpusIntegrityError("wire lacks receipt authorization")
            if prefetch < authorization["post_sleep_monotonic_ns"]:
                raise CorpusIntegrityError("wire precedes authorized sleep")
            if authorization["scope"] == "SAME_LOGICAL_RETRY":
                if (
                    logical_key != authorization["logical_key"]
                    or entry.get("sequence")
                    != authorization["logical_sequence"]
                    or number != authorization["next_attempt_no"]
                ):
                    raise CorpusIntegrityError("retry authorization mismatch")
            elif authorization["scope"] == "NEXT_LOGICAL":
                if (
                    entry.get("sequence")
                    != authorization["logical_sequence"] + 1
                    or number != 1
                ):
                    raise CorpusIntegrityError(
                        "next-logical authorization mismatch"
                    )
            else:
                raise CorpusIntegrityError("unknown receipt authorization")
        cumulative_response_bytes[0] += len(raw)
        recomputed_outcome = _response_outcome(
            request_headers_valid=request_headers_valid,
            response=reconstructed,
            canonical_url=entry["canonical_url"],
            max_response_bytes=runtime["max_response_bytes"],
            cumulative_response_bytes=cumulative_response_bytes[0],
            max_total_response_bytes=runtime["max_total_response_bytes"],
            response_clock_valid=response_clock_valid,
        )
        if recomputed_outcome != attempt.get("outcome"):
            raise CorpusIntegrityError("attempt outcome recomputation mismatch")
        retry_after = _retry_after_present(selected_headers)
        if (
            receipt.get("run_id") != run_id
            or receipt.get("logical_key") != logical_key
            or receipt.get("logical_sequence") != entry.get("sequence")
            or receipt.get("attempt_key") != attempt_key
            or receipt.get("attempt_no") != number
            or receipt.get("wire_attempt_global_no") != wire_no
            or receipt.get("canonical_url") != entry.get("canonical_url")
            or receipt.get("outcome") != recomputed_outcome
            or receipt.get("http_status") != response["status"]
            or receipt.get("retry_after_present") is not retry_after
            or receipt.get("body_path") != body_path.as_posix()
            or receipt.get("body_sha256") != _sha256_bytes(raw)
            or receipt.get("sidecar_path") != sidecar_path.as_posix()
            or receipt.get("sidecar_sha256") != _sha256_bytes(sidecar_bytes)
            or receipt.get("previous_receipt_sha256")
            != receipt_chain_state[0]
        ):
            raise CorpusIntegrityError("receipt evidence mismatch")
        receipt_sha = _sha256_bytes(receipt_bytes)
        receipt_chain_state[0] = receipt_sha
        receipt_tree.append(
            {
                "attempt_key": attempt_key,
                "path": receipt_path.as_posix(),
                "sha256": receipt_sha,
            }
        )
        outcome_counts[recomputed_outcome] = (
            outcome_counts.get(recomputed_outcome, 0) + 1
        )
        if number < len(attempts):
            if (
                response["status"] not in RETRYABLE_HTTP_STATUSES
                or recomputed_outcome != f"HTTP_{response['status']}"
                or retry_after
            ):
                raise CorpusIntegrityError("terminal outcome was retried")
            expected_delay = (
                FROZEN_HTTP_429_BACKOFF_SECONDS[number - 1]
                if response["status"] == 429 and number <= 3
                else FROZEN_OTHER_RETRYABLE_BACKOFF_SECONDS[number - 1]
                if response["status"] != 429 and number <= 2
                else None
            )
            expected_rule = (
                f"HTTP_429_ATTEMPT_{number}"
                if response["status"] == 429
                else f"OTHER_RETRYABLE_ATTEMPT_{number}"
            )
            if expected_delay is None:
                raise CorpusIntegrityError("retry exceeds absolute schedule")
            if (
                receipt.get("accepted") is not False
                or receipt.get("decision") != "NEXT_WIRE"
                or receipt.get("next_wire_scope")
                != "SAME_LOGICAL_RETRY"
                or receipt.get("next_attempt_no") != number + 1
                or receipt.get("delay_rule") != expected_rule
                or receipt.get("requested_delay_ns")
                != expected_delay * 1_000_000_000
                or receipt.get("terminal_reason") is not None
            ):
                raise CorpusIntegrityError("retry receipt decision mismatch")
        else:
            if (
                recomputed_outcome != "OK"
                or receipt.get("accepted") is not True
                or receipt.get("next_attempt_no")
                not in (1, None)
            ):
                raise CorpusIntegrityError("final attempt not accepted")
            if logical_key == "time_after":
                if (
                    receipt.get("decision") != "NO_NEXT_WIRE"
                    or receipt.get("next_wire_scope") is not None
                    or receipt.get("next_attempt_no") is not None
                    or receipt.get("delay_rule") != "NONE_RUN_COMPLETE"
                    or receipt.get("requested_delay_ns") != 0
                    or receipt.get("terminal_reason")
                    != "RUN_WIRE_COMPLETE"
                ):
                    raise CorpusIntegrityError("final time receipt mismatch")
            elif (
                receipt.get("decision") != "NEXT_WIRE"
                or receipt.get("next_wire_scope") != "NEXT_LOGICAL"
                or receipt.get("next_attempt_no") != 1
                or receipt.get("delay_rule") != "BASE_NEXT_LOGICAL"
                or receipt.get("requested_delay_ns")
                != BASE_LOGICAL_DELAY_NS
                or receipt.get("terminal_reason") is not None
            ):
                raise CorpusIntegrityError("accepted receipt decision mismatch")
        decision = receipt.get("decision")
        if decision == "NEXT_WIRE":
            requested = receipt.get("requested_delay_ns")
            pre_sleep = receipt.get("pre_sleep_monotonic_ns")
            post_sleep = receipt.get("post_sleep_monotonic_ns")
            if (
                type(requested) is not int
                or requested <= 0
                or type(pre_sleep) is not int
                or type(post_sleep) is not int
                or pre_sleep < postfetch
                or post_sleep < pre_sleep + requested
                or post_sleep >= deadline
            ):
                raise CorpusIntegrityError("receipt sleep evidence mismatch")
            authorization_state[0] = {
                "scope": receipt["next_wire_scope"],
                "logical_key": logical_key,
                "logical_sequence": entry["sequence"],
                "next_attempt_no": receipt["next_attempt_no"],
                "post_sleep_monotonic_ns": post_sleep,
            }
        else:
            if (
                receipt.get("requested_delay_ns") != 0
                or receipt.get("pre_sleep_monotonic_ns") is not None
                or receipt.get("post_sleep_monotonic_ns") is not None
            ):
                raise CorpusIntegrityError("terminal receipt slept")
            authorization_state[0] = None
    final = attempts[-1]
    meta = _load_json(Path(final["sidecar"]))
    response = meta["response"]
    if final.get("outcome") != "OK" or response.get("status") != 200 or response.get("final_url") != entry.get("canonical_url"):
        raise CorpusIntegrityError("final attempt not successful")
    raw = Path(final["body"]).read_bytes()
    return TransportResponse(
        status=200,
        headers=response["selected_headers"],
        body=raw,
        final_url=response["final_url"],
        request_started_at_utc=response["request_started_at_utc"],
        response_completed_at_utc=response["response_completed_at_utc"],
    )


def load_corpus(*, summary_output: Path, schema_output: Path, source_contract_output: Path) -> LoadedCorpus:
    summary_bytes = summary_output.read_bytes()
    summary = _load_json(summary_output)
    if _stable_json_bytes(summary, pretty=True) != summary_bytes:
        raise CorpusIntegrityError("root summary JSON bytes are non-canonical")
    if set(summary) != SUMMARY_TOP_LEVEL_FIELDS:
        raise CorpusIntegrityError("root summary top-level field set mismatch")
    if (
        summary.get("extractor_source_sha256") != _module_sha()
        or summary.get("pre_network_expected_extractor_sha256") != _module_sha()
    ):
        raise CorpusIntegrityError("source hash mismatch")
    if _load_json(schema_output) != _schema() or _load_json(source_contract_output) != _source_contract():
        raise CorpusIntegrityError("schema/source contract mismatch")
    if any(name in summary for name in _schema()["forbidden_summary_fields"]):
        raise CorpusIntegrityError("forbidden generic temporal summary alias")
    raw_summary_meta = summary.get("raw_summary")
    if (
        type(raw_summary_meta) is not dict
        or set(raw_summary_meta) != {"path", "sha256"}
        or type(raw_summary_meta.get("path")) is not str
        or re.fullmatch(
            r"[0-9a-f]{64}", str(raw_summary_meta.get("sha256", ""))
        )
        is None
    ):
        raise CorpusIntegrityError("raw summary binding missing")
    raw_summary_path = Path(raw_summary_meta["path"])
    raw_summary_bytes = raw_summary_path.read_bytes()
    raw_summary = _load_json(raw_summary_path)
    if (
        raw_summary_path.name != "summary.json"
        or raw_summary_path.parent.name != summary.get("run_id")
        or _sha256_bytes(raw_summary_bytes) != raw_summary_meta["sha256"]
        or _stable_json_bytes(raw_summary, pretty=True) != raw_summary_bytes
        or raw_summary.get("success_complete") is not True
        or raw_summary.get("run_id") != summary.get("run_id")
    ):
        raise CorpusIntegrityError("raw summary binding mismatch")
    raw_run = raw_summary_path.parent
    if (raw_run / "terminal_schedule.json").exists():
        raise CorpusIntegrityError("terminal schedule forbids success")
    runtime_meta = raw_summary.get("runtime_contract")
    if (
        type(runtime_meta) is not dict
        or set(runtime_meta) != {"path", "sha256"}
    ):
        raise CorpusIntegrityError("runtime contract binding missing")
    runtime_path = Path(str(runtime_meta.get("path", "")))
    runtime_bytes = runtime_path.read_bytes()
    runtime = _load_json(runtime_path)
    if (
        runtime_path != raw_run / "runtime_contract.json"
        or _sha256_bytes(runtime_bytes) != runtime_meta.get("sha256")
        or _stable_json_bytes(runtime, pretty=True) != runtime_bytes
        or runtime.get("version") != EXTRACTOR_VERSION
        or runtime.get("run_id") != summary.get("run_id")
        or runtime.get("expected_extractor_sha256") != _module_sha()
        or runtime.get("observed_extractor_sha256") != _module_sha()
        or runtime.get("wall_budget_ns") != WALL_BUDGET_NS
        or runtime.get("timeout_cap_ns") != TIMEOUT_CAP_NS
        or runtime.get("base_logical_delay_ns") != BASE_LOGICAL_DELAY_NS
        or runtime.get("max_wire_attempts") != MAX_WIRE_ATTEMPTS
        or runtime.get("absolute_attempt_numbers")
        != list(ABSOLUTE_ATTEMPT_NUMBERS)
        or runtime.get("retryable_http_statuses")
        != sorted(RETRYABLE_HTTP_STATUSES)
        or runtime.get("http_429_backoff_seconds")
        != list(FROZEN_HTTP_429_BACKOFF_SECONDS)
        or runtime.get("other_retryable_backoff_seconds")
        != list(FROZEN_OTHER_RETRYABLE_BACKOFF_SECONDS)
        or runtime.get("retry_after_presence_rule")
        != "TERMINAL_ON_ANY_CASE_INSENSITIVE_PRESENCE"
        or runtime.get("attempt_commit_order")
        != ["body", "sidecar", "receipt"]
        or type(runtime.get("start_monotonic_ns")) is not int
        or type(runtime.get("deadline_monotonic_ns")) is not int
        or runtime["deadline_monotonic_ns"]
        != runtime["start_monotonic_ns"] + WALL_BUDGET_NS
    ):
        raise CorpusIntegrityError("runtime contract mismatch")
    bounds = summary.get("acquisition_bounds")
    if type(bounds) is not dict or set(bounds) != {
        "max_pages_per_catalog",
        "max_articles",
        "max_response_bytes",
        "max_total_response_bytes",
        "timeout_cap_ns",
        "wall_budget_ns",
        "base_logical_delay_ns",
        "absolute_attempt_numbers",
        "max_wire_attempts",
        "http_429_backoff_seconds",
        "other_retryable_backoff_seconds",
    }:
        raise CorpusIntegrityError("acquisition bounds missing")
    if (
        type(bounds["max_pages_per_catalog"]) is not int
        or bounds["max_pages_per_catalog"] < 1
        or type(bounds["max_articles"]) is not int
        or bounds["max_articles"] < 0
        or type(bounds["max_response_bytes"]) is not int
        or bounds["max_response_bytes"] < 1
        or type(bounds["max_total_response_bytes"]) is not int
        or bounds["max_total_response_bytes"] < bounds["max_response_bytes"]
        or bounds["timeout_cap_ns"] != TIMEOUT_CAP_NS
        or bounds["wall_budget_ns"] != WALL_BUDGET_NS
        or bounds["base_logical_delay_ns"] != BASE_LOGICAL_DELAY_NS
        or bounds["absolute_attempt_numbers"]
        != list(ABSOLUTE_ATTEMPT_NUMBERS)
        or bounds["max_wire_attempts"] != MAX_WIRE_ATTEMPTS
        or bounds["http_429_backoff_seconds"]
        != list(FROZEN_HTTP_429_BACKOFF_SECONDS)
        or bounds["other_retryable_backoff_seconds"]
        != list(FROZEN_OTHER_RETRYABLE_BACKOFF_SECONDS)
        or runtime.get("max_response_bytes") != bounds["max_response_bytes"]
        or runtime.get("max_total_response_bytes")
        != bounds["max_total_response_bytes"]
    ):
        raise CorpusIntegrityError("acquisition bounds invalid")
    if raw_summary.get("request_ledger") != summary.get("request_ledger"):
        raise CorpusIntegrityError("raw/external ledger binding mismatch")
    ledger_path = Path(summary["request_ledger"]["path"])
    ledger, ledger_bytes = _load_jsonl(ledger_path)
    if _sha256_bytes(ledger_bytes) != summary["request_ledger"]["sha256"]:
        raise CorpusIntegrityError("request ledger hash mismatch")
    if [row.get("sequence") for row in ledger] != list(range(1, len(ledger) + 1)):
        raise CorpusIntegrityError("logical request sequence mismatch")
    if summary.get("request_count") != len(ledger):
        raise CorpusIntegrityError("request count mismatch")
    wire_attempt_count = sum(len(row.get("attempts", [])) for row in ledger)
    if (
        summary.get("wire_attempt_count") != wire_attempt_count
        or summary.get("max_wire_attempts") != bounds["max_wire_attempts"]
        or wire_attempt_count > bounds["max_wire_attempts"]
    ):
        raise CorpusIntegrityError("wire-attempt bound/count mismatch")
    outcome_bytes = [0]
    receipt_chain_state: list[str | None] = [None]
    wire_state = [0]
    authorization_state: list[dict[str, Any] | None] = [None]
    receipt_tree: list[dict[str, Any]] = []
    outcome_counts: dict[str, int] = {}
    responses = [
        _verified_response(
            row,
            summary["run_id"],
            runtime,
            outcome_bytes,
            receipt_chain_state,
            wire_state,
            authorization_state,
            receipt_tree,
            outcome_counts,
        )
        for row in ledger
    ]
    if authorization_state[0] is not None:
        raise CorpusIntegrityError("final wire left an authorization")
    attempt_bytes = outcome_bytes[0]
    local_times = []
    for entry in ledger:
        for attempt in entry["attempts"]:
            raw = Path(attempt["body"]).read_bytes()
            meta = _load_json(Path(attempt["sidecar"]))
            response_meta = meta["response"]
            local_times.extend(
                [
                    _parse_utc(response_meta["request_started_at_utc"], "request start"),
                    _parse_utc(response_meta["response_completed_at_utc"], "response completion"),
                ]
            )
    if attempt_bytes != summary.get("response_bytes"):
        raise CorpusIntegrityError("response byte total mismatch")
    if attempt_bytes > bounds["max_total_response_bytes"]:
        raise CorpusIntegrityError("attempt bytes exceed total bound")
    if any(current < previous for previous, current in zip(local_times, local_times[1:])):
        raise CorpusIntegrityError("attempt clocks are not monotone")

    inventory_meta = summary.get("inventory")
    detail_meta = summary.get("detail_index")
    discrepancy_meta = summary.get("time_claim_discrepancies")
    if (
        type(inventory_meta) is not dict
        or set(inventory_meta) != {"path", "sha256"}
        or type(detail_meta) is not dict
        or set(detail_meta) != {"path", "sha256"}
        or type(discrepancy_meta) is not dict
        or set(discrepancy_meta) != {"path", "sha256", "count"}
        or type(discrepancy_meta.get("count")) is not int
        or discrepancy_meta["count"] < 0
        or any(
            type(meta.get("path")) is not str
            or type(meta.get("sha256")) is not str
            or re.fullmatch(r"[0-9a-f]{64}", meta.get("sha256", "")) is None
            for meta in (inventory_meta, detail_meta, discrepancy_meta)
        )
    ):
        raise CorpusIntegrityError("derived artifact summary metadata invalid")
    inventory_path = Path(inventory_meta["path"])
    detail_path = Path(detail_meta["path"])
    discrepancy_path = Path(discrepancy_meta["path"])
    if (
        inventory_path.name != "inventory.jsonl"
        or detail_path.name != "detail_index.jsonl"
        or discrepancy_path.name != "time_claim_discrepancies.jsonl"
        or inventory_path.parent != detail_path.parent
        or inventory_path.parent != discrepancy_path.parent
        or inventory_path.parent.name != summary["run_id"]
    ):
        raise CorpusIntegrityError("derived artifact path mismatch")
    for forbidden in _schema()["forbidden_artifacts"]:
        if (inventory_path.parent / forbidden).exists():
            raise CorpusIntegrityError("forbidden derived artifact exists")
    inventory, inventory_bytes = _load_jsonl(inventory_path)
    details, detail_bytes = _load_jsonl(detail_path)
    discrepancies, discrepancy_bytes = _load_jsonl(discrepancy_path)
    if (
        _sha256_bytes(inventory_bytes) != inventory_meta["sha256"]
        or _sha256_bytes(detail_bytes) != detail_meta["sha256"]
        or _sha256_bytes(discrepancy_bytes)
        != discrepancy_meta["sha256"]
    ):
        raise CorpusIntegrityError("derived artifact hash mismatch")

    cursor = 0

    def consume(logical_key: str, kind: str) -> tuple[dict[str, Any], TransportResponse]:
        nonlocal cursor
        if cursor >= len(ledger):
            raise CorpusIntegrityError("logical request is missing")
        entry, response = ledger[cursor], responses[cursor]
        if entry.get("logical_key") != logical_key or entry.get("kind") != kind:
            raise CorpusIntegrityError("logical request order drift")
        cursor += 1
        return entry, response

    _before_entry, time_before = consume("time_before", "time")
    before_ms = _time_ms(time_before.body)
    rebuilt_pass_totals: dict[int, dict[int, int]] = {}
    rebuilt_pass_page_counts: dict[int, dict[int, int]] = {}
    rebuilt_pass_anchors: dict[int, dict[int, dict[int, str]]] = {}

    def consume_list_pass(pass_number: int) -> list[dict[str, Any]]:
        pass_rows: list[dict[str, Any]] = []
        totals_for_pass: dict[int, int] = {}
        pages_for_pass: dict[int, int] = {}
        anchors_for_pass: dict[int, dict[int, str]] = {}
        for catalog_id in CATALOG_IDS:
            first_entry, first_response = consume(
                f"catalog_{catalog_id}/pass_{pass_number}/page_0001", "list"
            )
            if first_entry["canonical_url"] != _list_url(
                catalog_id, 1, PAGE_SIZE
            ):
                raise CorpusIntegrityError("page-one URL drift")
            total, rows = _parse_list(
                first_response.body,
                catalog_id,
                1,
                _sha256_bytes(first_response.body),
                first_response.response_completed_at_utc,
            )
            pages = math.ceil(total / PAGE_SIZE) if total else 1
            if pages > bounds["max_pages_per_catalog"]:
                raise CorpusIntegrityError("catalog exceeds page bound")
            _validate_page_shape(rows, 1, pages, total, PAGE_SIZE)
            totals_for_pass[catalog_id] = total
            pages_for_pass[catalog_id] = pages
            anchors_for_pass[catalog_id] = {1: _anchor_hash(total, rows)}
            pass_rows.extend(rows)
            for page_no in range(2, pages + 1):
                page_entry, page_response = consume(
                    f"catalog_{catalog_id}/pass_{pass_number}/page_{page_no:04d}",
                    "list",
                )
                if page_entry["canonical_url"] != _list_url(
                    catalog_id, page_no, PAGE_SIZE
                ):
                    raise CorpusIntegrityError("pagination URL drift")
                current_total, current_rows = _parse_list(
                    page_response.body,
                    catalog_id,
                    page_no,
                    _sha256_bytes(page_response.body),
                    page_response.response_completed_at_utc,
                )
                if current_total != total:
                    raise CorpusIntegrityError("catalog total drift across pages")
                _validate_page_shape(
                    current_rows, page_no, pages, total, PAGE_SIZE
                )
                anchors_for_pass[catalog_id][page_no] = _anchor_hash(
                    current_total, current_rows
                )
                pass_rows.extend(current_rows)
        for catalog_id in CATALOG_IDS:
            subset = [
                row for row in pass_rows if row["catalog_id"] == catalog_id
            ]
            if len(subset) != totals_for_pass[catalog_id]:
                raise CorpusIntegrityError("pagination union count mismatch")
            if (
                len({_article_id_key(row) for row in subset}) != len(subset)
                or len({row["article_code"] for row in subset}) != len(subset)
            ):
                raise CorpusIntegrityError("duplicate article id/code across pages")
        if len({row["article_code"] for row in pass_rows}) != len(pass_rows):
            raise CorpusIntegrityError("article code occurs across catalogs")
        rebuilt_pass_totals[pass_number] = totals_for_pass
        rebuilt_pass_page_counts[pass_number] = pages_for_pass
        rebuilt_pass_anchors[pass_number] = anchors_for_pass
        return pass_rows

    rebuilt_pass_1 = consume_list_pass(1)
    rebuilt_inventory = sorted(
        rebuilt_pass_1,
        key=lambda row: (
            row["catalog_id"],
            -row["list_release_date_claim_ms"],
            row["article_code"],
        ),
    )
    if rebuilt_inventory != inventory:
        raise CorpusIntegrityError("inventory rebuild mismatch")

    interval = summary.get("list_release_date_claim_interval_ms")
    if (
        type(interval) is not list
        or len(interval) != 2
        or any(type(value) is not int for value in interval)
        or interval[0] >= interval[1]
    ):
        raise CorpusIntegrityError("list release-date claim interval invalid")
    selected = [
        row
        for row in rebuilt_inventory
        if interval[0] <= row["list_release_date_claim_ms"] < interval[1]
    ]
    if len(selected) > bounds["max_articles"]:
        raise CorpusIntegrityError("selected articles exceed bound")
    inventory_by_code = {row["article_code"]: row for row in inventory}
    rebuilt_details: list[dict[str, Any]] = []
    for expected in sorted(
        selected, key=lambda row: (row["catalog_id"], row["article_code"])
    ):
        entry, response = consume(
            f"details/{expected['article_code']}", "detail"
        )
        if entry["canonical_url"] != _detail_url(expected["article_code"]):
            raise CorpusIntegrityError("detail URL drift")
        rebuilt_details.append(
            _parse_detail(
                response.body,
                inventory_by_code[expected["article_code"]],
                _sha256_bytes(response.body),
                response.response_completed_at_utc,
            )
        )
    rebuilt_details = sorted(rebuilt_details, key=lambda row: (row["catalog_id"], row["article_code"]))
    if rebuilt_details != details:
        raise CorpusIntegrityError("detail index rebuild mismatch")
    rebuilt_discrepancies = [
        row
        for row in rebuilt_details
        if row["detail_publish_minus_list_release_claim_ms"] != 0
    ]
    if rebuilt_discrepancies != discrepancies:
        raise CorpusIntegrityError("time-claim discrepancy rebuild mismatch")
    if summary.get("time_claim_discrepancies") != {
        "path": discrepancy_path.as_posix(),
        "sha256": _sha256_bytes(discrepancy_bytes),
        "count": len(rebuilt_discrepancies),
    }:
        raise CorpusIntegrityError("time-claim discrepancy summary mismatch")

    rebuilt_pass_2 = consume_list_pass(2)
    _after_entry, time_after = consume("time_after", "time")
    after_ms = _time_ms(time_after.body)
    if cursor != len(ledger):
        raise CorpusIntegrityError("unexpected logical request")
    if before_ms > after_ms:
        raise CorpusIntegrityError("Binance time bracket is reversed")
    if summary.get("time_bracket_ms") != {
        "before": before_ms,
        "after": after_ms,
    }:
        raise CorpusIntegrityError("time bracket summary mismatch")
    max_clock_skew_ms = summary.get("max_clock_skew_ms")
    if type(max_clock_skew_ms) is not int or max_clock_skew_ms < 0:
        raise CorpusIntegrityError("clock skew bound invalid")
    for response, server_ms in ((time_before, before_ms), (time_after, after_ms)):
        if (
            abs(
                _epoch_ms(response.response_completed_at_utc, "time completion")
                - server_ms
            )
            > max_clock_skew_ms
        ):
            raise CorpusIntegrityError("server/local clock skew exceeds bound")

    list_release_date_claim_interval_counts = {
        catalog_id: sum(row["catalog_id"] == catalog_id for row in selected)
        for catalog_id in CATALOG_IDS
    }
    expected_totals = summary.get("expected_totals")
    expected_list_release_date_claim_interval_counts = summary.get(
        "expected_list_release_date_claim_interval_counts"
    )
    if (
        type(expected_totals) is not dict
        or type(expected_list_release_date_claim_interval_counts) is not dict
        or set(expected_totals) != {str(key) for key in CATALOG_IDS}
        or set(expected_list_release_date_claim_interval_counts)
        != {str(key) for key in CATALOG_IDS}
        or any(type(value) is not int or value < 0 for value in expected_totals.values())
        or any(
            type(value) is not int or value < 0
            for value in expected_list_release_date_claim_interval_counts.values()
        )
    ):
        raise CorpusIntegrityError("expected count contract invalid")
    contract_failures: list[str] = []
    for catalog_id in CATALOG_IDS:
        key = str(catalog_id)
        if rebuilt_pass_totals[1][catalog_id] != expected_totals[key]:
            contract_failures.append(f"CATALOG_{catalog_id}_TOTAL_MISMATCH")
        if (
            list_release_date_claim_interval_counts[catalog_id]
            != expected_list_release_date_claim_interval_counts[key]
        ):
            contract_failures.append(
                f"CATALOG_{catalog_id}_LIST_RELEASE_DATE_CLAIM_INTERVAL_COUNT_MISMATCH"
            )
        pass_1_semantic = _semantic_inventory(
            [row for row in rebuilt_pass_1 if row["catalog_id"] == catalog_id]
        )
        pass_2_semantic = _semantic_inventory(
            [row for row in rebuilt_pass_2 if row["catalog_id"] == catalog_id]
        )
        if (
            rebuilt_pass_totals[2][catalog_id]
            != rebuilt_pass_totals[1][catalog_id]
            or rebuilt_pass_page_counts[2][catalog_id]
            != rebuilt_pass_page_counts[1][catalog_id]
            or rebuilt_pass_anchors[2][catalog_id]
            != rebuilt_pass_anchors[1][catalog_id]
            or pass_2_semantic != pass_1_semantic
        ):
            contract_failures.append(f"CATALOG_{catalog_id}_FULL_PASS_DRIFT")

    expected_request_count = 2 + 2 * sum(
        math.ceil(expected_totals[str(catalog_id)] / PAGE_SIZE)
        if expected_totals[str(catalog_id)]
        else 1
        for catalog_id in CATALOG_IDS
    ) + sum(expected_list_release_date_claim_interval_counts.values())
    if len(ledger) != expected_request_count:
        contract_failures.append("REQUEST_COUNT_MISMATCH")

    rebuilt_stability = {
        "pass_1_page_anchor_sha256": {
            str(catalog_id): {
                str(page): value
                for page, value in rebuilt_pass_anchors[1][catalog_id].items()
            }
            for catalog_id in CATALOG_IDS
        },
        "pass_2_page_anchor_sha256": {
            str(catalog_id): {
                str(page): value
                for page, value in rebuilt_pass_anchors[2][catalog_id].items()
            }
            for catalog_id in CATALOG_IDS
        },
        "pass_1_full_inventory_sha256": _sha256_bytes(
            _stable_json_bytes(_semantic_inventory(rebuilt_pass_1))
        ),
        "pass_2_full_inventory_sha256": _sha256_bytes(
            _stable_json_bytes(_semantic_inventory(rebuilt_pass_2))
        ),
        "pass_2_merged_into_inventory": False,
    }

    expected_terminal = "NEEDS_MORE_DATA" if not contract_failures else "INCONCLUSIVE"
    expected_artifact_state = (
        "ANNOUNCEMENT_CORPUS_AVAILABLE" if not contract_failures else None
    )
    if (
        summary.get("catalog_totals")
        != {str(key): value for key, value in rebuilt_pass_totals[1].items()}
        or summary.get("page_counts")
        != {
            str(key): value for key, value in rebuilt_pass_page_counts[1].items()
        }
        or summary.get("list_release_date_claim_interval_counts")
        != {
            str(key): value
            for key, value in list_release_date_claim_interval_counts.items()
        }
        or summary.get("list_pass_stability") != rebuilt_stability
        or summary.get("expected_request_count") != expected_request_count
        or summary.get("contract_failures") != contract_failures
        or summary.get("terminal_status") != expected_terminal
        or summary.get("artifact_state") != expected_artifact_state
        or summary.get("semantics")
        != "CORPUS_ONLY; NOT_EVENT_OR_ELIGIBILITY_EVIDENCE"
    ):
        raise CorpusIntegrityError("summary semantic rebuild mismatch")
    if (
        len(inventory) != summary["inventory_count"]
        or len(details) != summary["detail_count"]
        or len(discrepancies) != summary["time_claim_discrepancy_count"]
    ):
        raise CorpusIntegrityError("summary count mismatch")
    if summary.get("historical_eligibility_ready") is not False:
        raise CorpusIntegrityError("corpus cannot claim eligibility")
    audit_state = _AcquisitionState(
        run_id=summary["run_id"],
        raw_run=raw_run,
        expected_extractor_sha256=_module_sha(),
        deadline_monotonic_ns=runtime["deadline_monotonic_ns"],
        max_response_bytes=runtime["max_response_bytes"],
        max_total_response_bytes=runtime["max_total_response_bytes"],
        fetcher=lambda _request, _timeout: (_ for _ in ()).throw(
            AssertionError("trusted loader must not fetch")
        ),
        sleeper=lambda _seconds: (_ for _ in ()).throw(
            AssertionError("trusted loader must not sleep")
        ),
        monotonic_ns=lambda: runtime["deadline_monotonic_ns"],
        http_429_backoff_seconds=FROZEN_HTTP_429_BACKOFF_SECONDS,
        other_retryable_backoff_seconds=(
            FROZEN_OTHER_RETRYABLE_BACKOFF_SECONDS
        ),
        response_bytes=attempt_bytes,
        wire_attempt_count=wire_attempt_count,
        logical_sequence=len(ledger),
        previous_receipt_sha256=receipt_chain_state[0],
        pending_ok=None,
        ledger=list(ledger),
    )
    recomputed_raw_summary = _build_success_raw_summary(
        state=audit_state,
        runtime_contract_path=runtime_path,
        runtime_contract_sha256=_sha256_bytes(runtime_bytes),
        ledger_path=ledger_path,
        ledger_sha256=_sha256_bytes(ledger_bytes),
        selected_detail_keys=[row["article_code"] for row in selected],
    )
    if raw_summary != recomputed_raw_summary:
        raise CorpusIntegrityError("raw summary trusted rebuild mismatch")
    if (
        raw_summary.get("final_receipt_sha256")
        != receipt_chain_state[0]
        or raw_summary.get("receipt_tree_sha256")
        != _sha256_bytes(_stable_json_bytes(receipt_tree))
        or raw_summary.get("outcome_counts")
        != dict(sorted(outcome_counts.items()))
    ):
        raise CorpusIntegrityError("receipt summary mismatch")
    return LoadedCorpus(
        run_id=summary["run_id"],
        terminal_status=summary["terminal_status"],
        artifact_state=summary["artifact_state"],
        inventory=tuple(inventory),
        details=tuple(details),
        time_claim_discrepancies=tuple(discrepancies),
        summary_sha256=_sha256_file(summary_output),
    )


def _pairs(values: Sequence[str]) -> dict[int, int]:
    result: dict[int, int] = {}
    for value in values:
        try:
            key_text, count_text = value.split("=", 1)
            key, count = int(key_text), int(count_text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("expected CATALOG=COUNT") from exc
        result[key] = count
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-extractor-sha256", required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--processed-root", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--schema-output", type=Path, required=True)
    parser.add_argument("--source-contract-output", type=Path, required=True)
    parser.add_argument("--catalog-id", action="append", type=int, required=True)
    parser.add_argument("--page-size", type=int, required=True)
    parser.add_argument(
        "--list-release-date-claim-start-ms", type=int, required=True
    )
    parser.add_argument(
        "--list-release-date-claim-end-ms-exclusive",
        type=int,
        required=True,
    )
    parser.add_argument("--expected-total", action="append", required=True)
    parser.add_argument(
        "--expected-list-release-date-claim-interval-count",
        action="append",
        required=True,
    )
    parser.add_argument("--max-pages-per-catalog", type=int, required=True)
    parser.add_argument("--max-articles", type=int, required=True)
    parser.add_argument("--max-response-bytes", type=int, required=True)
    parser.add_argument("--max-total-response-bytes", type=int, required=True)
    parser.add_argument("--max-clock-skew-ms", type=int, required=True)
    parser.add_argument(
        "--http-429-backoff-seconds",
        nargs=3,
        type=int,
        required=True,
    )
    parser.add_argument(
        "--other-retryable-backoff-seconds",
        nargs=2,
        type=int,
        required=True,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = run_corpus(
            run_id=args.run_id,
            expected_extractor_sha256=args.expected_extractor_sha256,
            raw_root=args.raw_root,
            processed_root=args.processed_root,
            summary_output=args.summary_output,
            schema_output=args.schema_output,
            source_contract_output=args.source_contract_output,
            catalog_ids=args.catalog_id,
            page_size=args.page_size,
            list_release_date_claim_start_ms=(
                args.list_release_date_claim_start_ms
            ),
            list_release_date_claim_end_ms_exclusive=(
                args.list_release_date_claim_end_ms_exclusive
            ),
            expected_totals=_pairs(args.expected_total),
            expected_list_release_date_claim_interval_counts=_pairs(
                args.expected_list_release_date_claim_interval_count
            ),
            max_pages_per_catalog=args.max_pages_per_catalog,
            max_articles=args.max_articles,
            max_response_bytes=args.max_response_bytes,
            max_total_response_bytes=args.max_total_response_bytes,
            max_clock_skew_ms=args.max_clock_skew_ms,
            http_429_backoff_seconds=args.http_429_backoff_seconds,
            other_retryable_backoff_seconds=(
                args.other_retryable_backoff_seconds
            ),
        )
    except CorpusError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["terminal_status"] == "NEEDS_MORE_DATA" else 2


if __name__ == "__main__":
    raise SystemExit(main())
