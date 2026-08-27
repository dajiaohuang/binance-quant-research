"""Fail-closed Binance Spot current/forward point-in-time snapshots.

The collector deliberately does not construct a historical universe.  A current
``exchangeInfo`` response supplies source-bound status, permission, and quote
facts only from its response-completion clock onward.  Binance does not supply a
listing interval in this endpoint, so every strict eligibility decision remains
closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
from typing import Any, BinaryIO

from quant_research.hierarchical_alpha import (
    EvidenceKind,
    EvidenceReference,
    MarketType,
    PITEligibilityEvidence,
    PITEligibilitySnapshot,
    PermissionState,
    TradingStatus,
    Venue,
    require_pit_eligibility,
)


EXTRACTOR_VERSION = "binance_spot_pit_v1"
TIME_URL = "https://data-api.binance.vision/api/v3/time"
EXCHANGE_INFO_URL = (
    "https://data-api.binance.vision/api/v3/exchangeInfo?showPermissionSets=true"
)
FROZEN_SYMBOL_INDEX_SHA256 = (
    "0b6df35cab25c9e393f901c923c0412084afbfdc956b171e1bef655907808c16"
)
DEFAULT_SYMBOL_INDEX = Path("data/raw/binance_spot_v2/inventory/symbol_index.jsonl")
DEFAULT_RAW_ROOT = Path("data/raw/binance_spot_pit_v1/snapshots")
DEFAULT_PROCESSED_ROOT = Path("data/processed/binance_spot_pit_v1/snapshots")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
VALID_STATUSES = frozenset({"TRADING", "HALT", "BREAK"})
FORBIDDEN_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-mbx-apikey",
        "x-mbx-api-key",
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
MAX_RESPONSE_BYTES = 20_000_000
DEFAULT_MAX_CLOCK_SKEW_MS = 300_000


class PITSnapshotError(RuntimeError):
    """Base error for a PIT snapshot contract or integrity failure."""


class PITContractError(PITSnapshotError):
    pass


class PITHttpError(PITSnapshotError):
    pass


class PITSchemaError(PITSnapshotError):
    pass


class PITClockError(PITSnapshotError):
    pass


class PITIntegrityError(PITSnapshotError):
    pass


class PITExistingEvidenceError(PITSnapshotError):
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
class LoadedSnapshot:
    snapshot_id: str
    known_at_utc: str
    known_at_ms: int
    artifact_sha256: str
    raw_exchange_info_sha256: str
    memberships: tuple[dict[str, Any], ...]


Fetcher = Callable[[urllib.request.Request, float], TransportResponse]
Sleeper = Callable[[float], None]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Return the original 30x as HTTPError without issuing a second request."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: BinaryIO,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, label: str) -> datetime:
    if type(value) is not str or not value:
        raise PITClockError(f"{label} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PITClockError(f"invalid {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PITClockError(f"{label} must have UTC offset")
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


def _canonical_record_sha256(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(_stable_json_bytes(value))


def _write_once(path: Path, body: bytes) -> str:
    """Atomically create one immutable file and refuse any overwrite."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise PITExistingEvidenceError(f"refusing to overwrite {path}")
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
            raise PITExistingEvidenceError(f"refusing to overwrite {path}")
        os.replace(temporary_path, path)
        temporary_name = None
        return _sha256_bytes(body)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _canonical_url(url: str, kind: str) -> tuple[str, dict[str, str]]:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "data-api.binance.vision"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise PITContractError(f"invalid {kind} endpoint")
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if kind == "time":
        if parsed.path != "/api/v3/time" or query_pairs:
            raise PITContractError("time endpoint must have no parameters")
        canonical = TIME_URL
        parameters: dict[str, str] = {}
    elif kind == "exchange_info":
        if parsed.path != "/api/v3/exchangeInfo" or query_pairs != [
            ("showPermissionSets", "true")
        ]:
            raise PITContractError(
                "exchangeInfo must be complete and contain only showPermissionSets=true"
            )
        canonical = EXCHANGE_INFO_URL
        parameters = {"showPermissionSets": "true"}
    else:
        raise PITContractError(f"unknown endpoint kind {kind!r}")
    if url != canonical:
        raise PITContractError(f"non-canonical {kind} URL")
    return canonical, parameters


def _request(url: str, kind: str) -> tuple[urllib.request.Request, dict[str, str]]:
    canonical, parameters = _canonical_url(url, kind)
    request = urllib.request.Request(
        canonical,
        method="GET",
        headers={"Accept-Encoding": "identity", "User-Agent": "quant-binance-spot-pit/1"},
    )
    for name, _value in request.header_items():
        if name.lower() in FORBIDDEN_HEADER_NAMES:
            raise PITContractError(f"forbidden authentication header {name!r}")
    return request, parameters


def _default_fetcher(request: urllib.request.Request, timeout: float) -> TransportResponse:
    started = _utc_now()
    try:
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirectHandler()
            )
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            completed = _utc_now()
            return TransportResponse(
                status=int(response.status),
                headers=dict(response.headers.items()),
                body=body,
                final_url=response.geturl(),
                request_started_at_utc=started,
                response_completed_at_utc=completed,
            )
    except urllib.error.URLError as exc:
        raise PITHttpError(f"transport failure: {exc}") from exc


def _selected_headers(headers: Mapping[str, str]) -> dict[str, str]:
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    return {
        name: lowered[name.lower()]
        for name in SELECTED_RESPONSE_HEADERS
        if name.lower() in lowered
    }


def _safe_transport_error(exc: Exception) -> tuple[str, str]:
    """Map arbitrary transport exceptions to non-secret, bounded evidence."""

    if isinstance(exc, TimeoutError):
        return "TIMEOUT", "public endpoint request timed out"
    if isinstance(exc, urllib.error.URLError):
        return "URL_ERROR", "public endpoint transport failed"
    if isinstance(exc, OSError):
        return "OS_ERROR", "public endpoint transport failed"
    return "TRANSPORT_ERROR", "public endpoint transport failed"


def _response_times(response: TransportResponse) -> None:
    started = _parse_utc(response.request_started_at_utc, "request_started_at_utc")
    completed = _parse_utc(
        response.response_completed_at_utc, "response_completed_at_utc"
    )
    if completed < started:
        raise PITClockError("response completion precedes request start")


def _attempt_sidecar(
    request: urllib.request.Request,
    parameters: Mapping[str, str],
    response: TransportResponse,
    *,
    outcome: str,
) -> dict[str, Any]:
    return {
        "request": {
            "method": request.get_method(),
            "canonical_url": request.full_url,
            "canonical_parameters": dict(parameters),
            "headers": dict(sorted(request.header_items())),
            "authentication": "NONE",
        },
        "response": {
            "status": response.status,
            "final_url": response.final_url,
            "selected_headers": _selected_headers(response.headers),
            "request_started_at_utc": response.request_started_at_utc,
            "response_completed_at_utc": response.response_completed_at_utc,
            "body_bytes": len(response.body),
            "body_sha256": _sha256_bytes(response.body),
        },
        "outcome": outcome,
    }


def _acquire(
    *,
    raw_snapshot_dir: Path,
    label: str,
    kind: str,
    url: str,
    fetcher: Fetcher,
    timeout_seconds: float,
    max_attempts: int,
    sleeper: Sleeper,
) -> tuple[TransportResponse, Path, Path, tuple[dict[str, Any], ...]]:
    request, parameters = _request(url, kind)
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        local_started = _utc_now()
        try:
            response = fetcher(request, timeout_seconds)
        except Exception as exc:
            local_completed = _utc_now()
            error_category, safe_error_message = _safe_transport_error(exc)
            attempt_dir = raw_snapshot_dir / "requests" / label
            sidecar_path = attempt_dir / f"attempt_{attempt:04d}.request.json"
            _write_once(
                sidecar_path,
                _stable_json_bytes(
                    {
                        "request": {
                            "method": request.get_method(),
                            "canonical_url": request.full_url,
                            "canonical_parameters": dict(parameters),
                            "headers": dict(sorted(request.header_items())),
                            "authentication": "NONE",
                        },
                        "response": None,
                        "attempt_started_at_utc": local_started,
                        "attempt_completed_at_utc": local_completed,
                        "outcome": "TRANSPORT_ERROR",
                        "error_category": error_category,
                        "safe_error_message": safe_error_message,
                    },
                    pretty=True,
                ),
            )
            raise PITHttpError(f"{label} transport failure; receipt preserved") from exc
        if type(response) is not TransportResponse:
            raise PITHttpError("fetcher returned an invalid response")
        _response_times(response)
        if 300 <= response.status < 400 or response.final_url != request.full_url:
            outcome = "REDIRECT_REJECTED"
        elif len(response.body) > MAX_RESPONSE_BYTES:
            outcome = "OVERSIZED_RESPONSE"
        else:
            content_length = _selected_headers(response.headers).get("Content-Length")
            if content_length is not None:
                try:
                    length = int(content_length)
                except ValueError:
                    outcome = "INVALID_CONTENT_LENGTH"
                else:
                    outcome = (
                        "OK" if response.status == 200 and length == len(response.body)
                        else "CONTENT_LENGTH_MISMATCH"
                        if length != len(response.body)
                        else f"HTTP_{response.status}"
                    )
            else:
                outcome = (
                    "MISSING_CONTENT_LENGTH"
                    if response.status == 200
                    else f"HTTP_{response.status}"
                )
        attempt_dir = raw_snapshot_dir / "requests" / label
        body_path = attempt_dir / f"attempt_{attempt:04d}.response"
        sidecar_path = attempt_dir / f"attempt_{attempt:04d}.request.json"
        body_sha256 = _write_once(body_path, response.body)
        sidecar_sha256 = _write_once(
            sidecar_path,
            _stable_json_bytes(
                _attempt_sidecar(request, parameters, response, outcome=outcome),
                pretty=True,
            ),
        )
        attempts.append(
            {
                "attempt": attempt,
                "body": body_path.as_posix(),
                "body_sha256": body_sha256,
                "sidecar": sidecar_path.as_posix(),
                "sidecar_sha256": sidecar_sha256,
                "outcome": outcome,
            }
        )
        if outcome == "OK":
            return response, body_path, sidecar_path, tuple(attempts)
        retryable = response.status in RETRYABLE_HTTP_STATUSES
        if retryable and attempt < max_attempts:
            sleeper(min(2 ** (attempt - 1), 8))
            continue
        raise PITHttpError(f"{label} failed closed: {outcome}")
    raise PITHttpError(f"{label} exhausted attempts")


def _decode_json(body: bytes, label: str) -> Any:
    try:
        text = body.decode("utf-8")
        return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PITSchemaError(f"invalid {label} JSON") from exc


def _server_time(body: bytes, label: str) -> int:
    payload = _decode_json(body, label)
    value = payload.get("serverTime") if type(payload) is dict else None
    if type(value) is not int or value <= 0:
        raise PITSchemaError(f"{label}.serverTime must be a positive integer")
    return value


def _string_list(value: object, label: str) -> list[str]:
    if type(value) is not list or any(type(item) is not str or not item for item in value):
        raise PITSchemaError(f"{label} must be a list of non-empty strings")
    return list(value)


def _exchange_records(body: bytes) -> tuple[int, list[dict[str, Any]]]:
    payload = _decode_json(body, "exchangeInfo")
    if type(payload) is not dict:
        raise PITSchemaError("exchangeInfo must be an object")
    server_time = payload.get("serverTime")
    if type(server_time) is not int or server_time <= 0:
        raise PITSchemaError("exchangeInfo.serverTime must be a positive integer")
    symbols = payload.get("symbols")
    if type(symbols) is not list:
        raise PITSchemaError("exchangeInfo.symbols must be a list")
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    for index, raw in enumerate(symbols):
        if type(raw) is not dict:
            raise PITSchemaError(f"symbols[{index}] must be an object")
        symbol = raw.get("symbol")
        status = raw.get("status")
        quote = raw.get("quoteAsset")
        spot = raw.get("isSpotTradingAllowed")
        if type(symbol) is not str or not symbol or symbol in seen:
            raise PITSchemaError(f"invalid or duplicate symbol at symbols[{index}]")
        if type(status) is not str or status not in VALID_STATUSES:
            raise PITSchemaError(f"unknown status at symbols[{index}]")
        if type(quote) is not str or not quote:
            raise PITSchemaError(f"missing quoteAsset at symbols[{index}]")
        if type(spot) is not bool:
            raise PITSchemaError(f"missing isSpotTradingAllowed at symbols[{index}]")
        permissions = _string_list(raw.get("permissions"), f"symbols[{index}].permissions")
        permission_sets_raw = raw.get("permissionSets")
        if type(permission_sets_raw) is not list:
            raise PITSchemaError(f"symbols[{index}].permissionSets must be a list")
        permission_sets = [
            _string_list(item, f"symbols[{index}].permissionSets[{set_index}]")
            for set_index, item in enumerate(permission_sets_raw)
        ]
        seen.add(symbol)
        normalized = dict(raw)
        normalized["permissions"] = permissions
        normalized["permissionSets"] = permission_sets
        records.append(normalized)
    return server_time, records


def load_archive_candidates(
    path: Path, *, expected_sha256: str = FROZEN_SYMBOL_INDEX_SHA256
) -> tuple[str, ...]:
    if not SHA256_PATTERN.fullmatch(expected_sha256):
        raise PITContractError("invalid frozen symbol-index SHA-256")
    actual = _sha256_file(path)
    if actual != expected_sha256:
        raise PITIntegrityError(
            f"symbol-index hash mismatch: expected {expected_sha256}, got {actual}"
        )
    candidates: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PITSchemaError(f"invalid symbol-index JSONL line {line_number}") from exc
            if type(item) is not dict:
                raise PITSchemaError(f"invalid symbol-index row {line_number}")
            symbol = item.get("symbol")
            suffix_candidate = item.get("suffix_candidate")
            if type(symbol) is not str or not symbol or symbol in seen:
                raise PITSchemaError(f"invalid or duplicate archive symbol at line {line_number}")
            if type(suffix_candidate) is not bool:
                raise PITSchemaError(f"missing suffix_candidate at line {line_number}")
            seen.add(symbol)
            if suffix_candidate:
                candidates.append(symbol)
    return tuple(sorted(candidates))


def _evidence_binding(
    kind: str, raw_sha256: str, known_at: str, field_values: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "kind": kind,
        "source_raw_sha256": raw_sha256,
        "known_at_utc": known_at,
        "field_values": dict(field_values),
    }


def _membership_rows(
    records: Sequence[dict[str, Any]],
    archive_candidates: Sequence[str],
    *,
    raw_sha256: str,
    known_at_utc: str,
    extractor_sha256: str,
) -> list[dict[str, Any]]:
    by_symbol = {record["symbol"]: (index, record) for index, record in enumerate(records)}
    rows: list[dict[str, Any]] = []
    for symbol in sorted(set(by_symbol) | set(archive_candidates)):
        archive_candidate = symbol in archive_candidates
        if symbol in by_symbol:
            index, record = by_symbol[symbol]
            status = record["status"]
            spot = record["isSpotTradingAllowed"]
            quote = record["quoteAsset"]
            permissions = record["permissions"]
            permission_sets = record["permissionSets"]
            bindings = [
                _evidence_binding(
                    EvidenceKind.VENUE_MARKET_STATUS.value,
                    raw_sha256,
                    known_at_utc,
                    {"status": status},
                ),
                _evidence_binding(
                    EvidenceKind.SPOT_PERMISSION.value,
                    raw_sha256,
                    known_at_utc,
                    {
                        "isSpotTradingAllowed": spot,
                        "permissions": permissions,
                        "permissionSets": permission_sets,
                    },
                ),
                _evidence_binding(
                    EvidenceKind.QUOTE_ASSET_RULE.value,
                    raw_sha256,
                    known_at_utc,
                    {"quoteAsset": quote},
                ),
            ]
            row = {
                "symbol": symbol,
                "archive_candidate": archive_candidate,
                "current_response_observed": True,
                "raw_status": status,
                "raw_quote_asset": quote,
                "raw_is_spot_trading_allowed": spot,
                "raw_permissions": permissions,
                "raw_permission_sets": permission_sets,
                "json_record_locator": f"$.symbols[{index}]",
                "raw_exchange_info_sha256": raw_sha256,
                "canonical_raw_record_sha256": _canonical_record_sha256(record),
                "extractor_version": EXTRACTOR_VERSION,
                "extractor_source_sha256": extractor_sha256,
                "known_at_utc": known_at_utc,
                "listing_from_ms": None,
                "listing_to_ms_exclusive": None,
                "strict_eligible": False,
                "strict_reasons": ["MISSING_LISTING_WINDOW", "UNKNOWN_LISTING_WINDOW"],
                "evidence_bindings": bindings,
            }
        else:
            bindings = [
                _evidence_binding(
                    EvidenceKind.VENUE_MARKET_STATUS.value,
                    raw_sha256,
                    known_at_utc,
                    {"status": None, "state": "UNKNOWN", "response_observed": False},
                ),
                _evidence_binding(
                    EvidenceKind.SPOT_PERMISSION.value,
                    raw_sha256,
                    known_at_utc,
                    {
                        "isSpotTradingAllowed": None,
                        "permissions": None,
                        "permissionSets": None,
                        "state": "UNKNOWN",
                        "response_observed": False,
                    },
                ),
                _evidence_binding(
                    EvidenceKind.QUOTE_ASSET_RULE.value,
                    raw_sha256,
                    known_at_utc,
                    {"quoteAsset": None, "state": "UNKNOWN", "response_observed": False},
                ),
            ]
            row = {
                "symbol": symbol,
                "archive_candidate": True,
                "current_response_observed": False,
                "raw_status": None,
                "raw_quote_asset": None,
                "raw_is_spot_trading_allowed": None,
                "raw_permissions": None,
                "raw_permission_sets": None,
                "json_record_locator": None,
                "raw_exchange_info_sha256": raw_sha256,
                "canonical_raw_record_sha256": None,
                "extractor_version": EXTRACTOR_VERSION,
                "extractor_source_sha256": extractor_sha256,
                "known_at_utc": known_at_utc,
                "listing_from_ms": None,
                "listing_to_ms_exclusive": None,
                "strict_eligible": False,
                "strict_reasons": [
                    "MISSING_LISTING_WINDOW",
                    "UNKNOWN_LISTING_WINDOW",
                    "UNKNOWN_QUOTE_ASSET",
                    "UNKNOWN_SPOT_PERMISSION",
                    "UNKNOWN_TRADING_STATUS",
                ],
                "evidence_bindings": bindings,
            }
        rows.append(row)
    return rows


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_stable_json_bytes(row) for row in rows)


def _validate_clock_bracket(
    before: TransportResponse,
    exchange: TransportResponse,
    after: TransportResponse,
    *,
    max_clock_skew_ms: int,
) -> tuple[int, int, int, int]:
    local_sequence = (
        _parse_utc(before.request_started_at_utc, "time_before request start"),
        _parse_utc(before.response_completed_at_utc, "time_before response completion"),
        _parse_utc(exchange.request_started_at_utc, "exchangeInfo request start"),
        _parse_utc(exchange.response_completed_at_utc, "exchangeInfo response completion"),
        _parse_utc(after.request_started_at_utc, "time_after request start"),
        _parse_utc(after.response_completed_at_utc, "time_after response completion"),
    )
    if any(later < earlier for earlier, later in zip(local_sequence, local_sequence[1:])):
        raise PITClockError(
            "local request/response clocks are not monotone across the time bracket"
        )
    before_ms = _server_time(before.body, "time_before")
    exchange_ms, _records = _exchange_records(exchange.body)
    after_ms = _server_time(after.body, "time_after")
    known_at_ms = _epoch_ms(exchange.response_completed_at_utc, "known_at")
    if not before_ms <= exchange_ms <= after_ms:
        raise PITClockError("Binance server-time bracket is not ordered")
    if max_clock_skew_ms < 0:
        raise PITContractError("max_clock_skew_ms must be non-negative")
    for label, server_ms, response in (
        ("time_before", before_ms, before),
        ("exchange_info", exchange_ms, exchange),
        ("time_after", after_ms, after),
    ):
        completed_ms = _epoch_ms(response.response_completed_at_utc, f"{label} completion")
        if abs(server_ms - completed_ms) > max_clock_skew_ms:
            raise PITClockError(f"{label} exceeds permitted local/server clock skew")
    return before_ms, exchange_ms, after_ms, known_at_ms


def _schema_document() -> dict[str, Any]:
    return {
        "version": EXTRACTOR_VERSION,
        "semantics": "CURRENT_OR_FORWARD_ONLY; NOT_HISTORICAL_ELIGIBILITY",
        "listing_semantics": "listing_from_ms and listing_to_ms_exclusive are always null",
        "permission_semantics": (
            "isSpotTradingAllowed is the sole eligibility permission predicate; "
            "permissions and permissionSets are retained verbatim as source evidence only"
        ),
        "row_fields": [
            "symbol",
            "archive_candidate",
            "current_response_observed",
            "raw_status",
            "raw_quote_asset",
            "raw_is_spot_trading_allowed",
            "raw_permissions",
            "raw_permission_sets",
            "json_record_locator",
            "raw_exchange_info_sha256",
            "canonical_raw_record_sha256",
            "extractor_version",
            "extractor_source_sha256",
            "known_at_utc",
            "listing_from_ms",
            "listing_to_ms_exclusive",
            "strict_eligible",
            "strict_reasons",
            "evidence_bindings",
        ],
    }


def run_snapshot(
    *,
    snapshot_id: str,
    raw_root: Path = DEFAULT_RAW_ROOT,
    processed_root: Path = DEFAULT_PROCESSED_ROOT,
    symbol_index_path: Path = DEFAULT_SYMBOL_INDEX,
    symbol_index_sha256: str = FROZEN_SYMBOL_INDEX_SHA256,
    summary_output: Path,
    schema_output: Path,
    gate_output: Path,
    time_url: str = TIME_URL,
    exchange_info_url: str = EXCHANGE_INFO_URL,
    timeout_seconds: float = 30.0,
    max_attempts: int = 3,
    max_clock_skew_ms: int = DEFAULT_MAX_CLOCK_SKEW_MS,
    fetcher: Fetcher = _default_fetcher,
    sleeper: Sleeper = time.sleep,
) -> dict[str, Any]:
    if type(snapshot_id) is not str or not re.fullmatch(r"[A-Za-z0-9._-]+", snapshot_id):
        raise PITContractError("snapshot_id must be an explicit safe identifier")
    if max_attempts < 1 or timeout_seconds <= 0:
        raise PITContractError("invalid transport limits")
    raw_snapshot_dir = raw_root / snapshot_id
    processed_snapshot_dir = processed_root / snapshot_id
    targets = (processed_snapshot_dir, summary_output, schema_output, gate_output)
    if any(path.exists() for path in targets):
        raise PITExistingEvidenceError("snapshot outputs already exist")
    raw_snapshot_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw_snapshot_dir.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise PITExistingEvidenceError(
            f"snapshot id {snapshot_id!r} already has an exclusive raw lease"
        ) from exc
    archive_candidates = load_archive_candidates(
        symbol_index_path, expected_sha256=symbol_index_sha256
    )
    _canonical_url(time_url, "time")
    _canonical_url(exchange_info_url, "exchange_info")

    before, before_body, before_sidecar, before_attempts = _acquire(
        raw_snapshot_dir=raw_snapshot_dir,
        label="time_before",
        kind="time",
        url=time_url,
        fetcher=fetcher,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        sleeper=sleeper,
    )
    exchange, exchange_body, exchange_sidecar, exchange_attempts = _acquire(
        raw_snapshot_dir=raw_snapshot_dir,
        label="exchange_info",
        kind="exchange_info",
        url=exchange_info_url,
        fetcher=fetcher,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        sleeper=sleeper,
    )
    after, after_body, after_sidecar, after_attempts = _acquire(
        raw_snapshot_dir=raw_snapshot_dir,
        label="time_after",
        kind="time",
        url=time_url,
        fetcher=fetcher,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        sleeper=sleeper,
    )
    before_ms, exchange_ms, after_ms, known_at_ms = _validate_clock_bracket(
        before, exchange, after, max_clock_skew_ms=max_clock_skew_ms
    )
    _server_time(before.body, "time_before")
    _server_time(after.body, "time_after")
    _exchange_server_ms, records = _exchange_records(exchange.body)
    raw_sha = _sha256_bytes(exchange.body)
    extractor_sha = _sha256_file(Path(__file__))
    rows = _membership_rows(
        records,
        archive_candidates,
        raw_sha256=raw_sha,
        known_at_utc=exchange.response_completed_at_utc,
        extractor_sha256=extractor_sha,
    )
    memberships_path = processed_snapshot_dir / "memberships.jsonl"
    artifact_sha = _write_once(memberships_path, _jsonl_bytes(rows))
    relative = lambda path: path.as_posix()
    summary = {
        "snapshot_id": snapshot_id,
        "semantics": "CURRENT_OR_FORWARD_ONLY; NOT_HISTORICAL_ELIGIBILITY",
        "known_at_utc": exchange.response_completed_at_utc,
        "known_at_ms": known_at_ms,
        "server_time_bracket_ms": {
            "before": before_ms,
            "exchange_info": exchange_ms,
            "after": after_ms,
        },
        "raw_requests": {
            "time_before": {
                "body": relative(before_body),
                "sidecar": relative(before_sidecar),
                "sha256": _sha256_bytes(before.body),
                "attempts": list(before_attempts),
            },
            "exchange_info": {
                "body": relative(exchange_body),
                "sidecar": relative(exchange_sidecar),
                "sha256": raw_sha,
                "attempts": list(exchange_attempts),
            },
            "time_after": {
                "body": relative(after_body),
                "sidecar": relative(after_sidecar),
                "sha256": _sha256_bytes(after.body),
                "attempts": list(after_attempts),
            },
        },
        "memberships_path": relative(memberships_path),
        "memberships_sha256": artifact_sha,
        "membership_count": len(rows),
        "current_response_symbol_count": len(records),
        "archive_candidate_count": len(archive_candidates),
        "archive_only_unknown_count": sum(
            1 for row in rows if not row["current_response_observed"]
        ),
        "strict_eligible_count": 0,
        "transport_contract": {
            "timeout_seconds": timeout_seconds,
            "max_attempts": max_attempts,
            "max_clock_skew_ms": max_clock_skew_ms,
        },
        "extractor_version": EXTRACTOR_VERSION,
        "extractor_source_sha256": extractor_sha,
        "symbol_index": {"path": relative(symbol_index_path), "sha256": symbol_index_sha256},
    }
    _write_once(summary_output, _stable_json_bytes(summary, pretty=True))
    _write_once(schema_output, _stable_json_bytes(_schema_document(), pretty=True))
    loaded = load_snapshot(
        summary_output=summary_output,
        symbol_index_path=symbol_index_path,
        symbol_index_sha256=symbol_index_sha256,
    )
    gate = strict_gate_result(loaded, formation_time_ms=loaded.known_at_ms)
    if gate["eligible_count"] != 0:
        raise PITIntegrityError("listing-unknown snapshot unexpectedly opened strict gate")
    _write_once(gate_output, _stable_json_bytes(gate, pretty=True))
    return summary


def _load_json_file(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PITIntegrityError(f"cannot load trusted {label}") from exc


def load_snapshot(
    *,
    summary_output: Path,
    symbol_index_path: Path = DEFAULT_SYMBOL_INDEX,
    symbol_index_sha256: str = FROZEN_SYMBOL_INDEX_SHA256,
) -> LoadedSnapshot:
    summary = _load_json_file(summary_output, "snapshot summary")
    if type(summary) is not dict:
        raise PITIntegrityError("snapshot summary must be an object")
    required = (
        "snapshot_id",
        "known_at_utc",
        "known_at_ms",
        "raw_requests",
        "memberships_path",
        "memberships_sha256",
        "extractor_source_sha256",
    )
    if any(key not in summary for key in required):
        raise PITIntegrityError("snapshot summary is incomplete")
    known_at = summary["known_at_utc"]
    known_at_ms = _epoch_ms(known_at, "known_at")
    if summary["known_at_ms"] != known_at_ms:
        raise PITIntegrityError("known_at milliseconds mismatch")
    raw_requests = summary["raw_requests"]
    if type(raw_requests) is not dict:
        raise PITIntegrityError("missing raw request references")
    verified: dict[str, TransportResponse] = {}
    for label, expected_url in (
        ("time_before", TIME_URL),
        ("exchange_info", EXCHANGE_INFO_URL),
        ("time_after", TIME_URL),
    ):
        reference = raw_requests.get(label)
        if type(reference) is not dict:
            raise PITIntegrityError(f"missing {label} raw reference")
        attempt_ledger = reference.get("attempts")
        if type(attempt_ledger) is not list or not attempt_ledger:
            raise PITIntegrityError(f"missing {label} root-bound attempt ledger")
        successful_body = Path(reference.get("body", ""))
        successful_sidecar = Path(reference.get("sidecar", ""))
        attempt_directory = successful_body.parent
        if (
            attempt_directory.name != label
            or attempt_directory.parent.name != "requests"
            or attempt_directory.parent.parent.name != summary["snapshot_id"]
        ):
            raise PITIntegrityError(f"{label} attempt ledger escapes its snapshot root")
        for expected_attempt, ledger_item in enumerate(attempt_ledger, start=1):
            if type(ledger_item) is not dict or ledger_item.get("attempt") != expected_attempt:
                raise PITIntegrityError(f"{label} attempt sequence has a gap or duplicate")
            attempt_body = Path(ledger_item.get("body", ""))
            attempt_sidecar = Path(ledger_item.get("sidecar", ""))
            if (
                attempt_body != attempt_directory / f"attempt_{expected_attempt:04d}.response"
                or attempt_sidecar
                != attempt_directory / f"attempt_{expected_attempt:04d}.request.json"
                or not attempt_body.is_file()
                or not attempt_sidecar.is_file()
            ):
                raise PITIntegrityError(f"{label} attempt paths are not root-bound")
            attempt_raw = attempt_body.read_bytes()
            attempt_sidecar_bytes = attempt_sidecar.read_bytes()
            if (
                _sha256_bytes(attempt_raw) != ledger_item.get("body_sha256")
                or _sha256_bytes(attempt_sidecar_bytes)
                != ledger_item.get("sidecar_sha256")
            ):
                raise PITIntegrityError(f"{label} attempt evidence SHA-256 mismatch")
            attempt_meta = _load_json_file(attempt_sidecar, f"{label} attempt sidecar")
            try:
                attempt_request = attempt_meta["request"]
                attempt_response = attempt_meta["response"]
                attempt_status = attempt_response["status"]
                attempt_headers = attempt_response["selected_headers"]
            except (KeyError, TypeError) as exc:
                raise PITIntegrityError(f"{label} attempt sidecar is incomplete") from exc
            expected_parameters = (
                {"showPermissionSets": "true"} if label == "exchange_info" else {}
            )
            if (
                attempt_meta.get("outcome") != ledger_item.get("outcome")
                or attempt_request.get("method") != "GET"
                or attempt_request.get("canonical_url") != expected_url
                or attempt_request.get("canonical_parameters") != expected_parameters
                or attempt_request.get("authentication") != "NONE"
                or any(
                    str(name).lower() in FORBIDDEN_HEADER_NAMES
                    for name in attempt_request.get("headers", {})
                )
                or attempt_response.get("final_url") != expected_url
                or attempt_response.get("body_sha256") != _sha256_bytes(attempt_raw)
                or attempt_response.get("body_bytes") != len(attempt_raw)
            ):
                raise PITIntegrityError(f"{label} attempt contract mismatch")
            if attempt_status == 200 and "Content-Length" not in attempt_headers:
                raise PITIntegrityError(f"missing {label} attempt Content-Length")
            is_final_attempt = expected_attempt == len(attempt_ledger)
            if is_final_attempt:
                if attempt_status != 200 or ledger_item.get("outcome") != "OK":
                    raise PITIntegrityError(f"{label} final attempt is not successful")
            elif (
                attempt_status not in RETRYABLE_HTTP_STATUSES
                or ledger_item.get("outcome") == "OK"
            ):
                raise PITIntegrityError(f"{label} non-final attempt is not retryable")
        final_attempt = attempt_ledger[-1]
        if (
            successful_body != Path(final_attempt["body"])
            or successful_sidecar != Path(final_attempt["sidecar"])
            or reference.get("sha256") != final_attempt.get("body_sha256")
        ):
            raise PITIntegrityError(f"{label} successful attempt is not ledger-bound")
        referenced_body = Path(reference.get("body", ""))
        referenced_sidecar = Path(reference.get("sidecar", ""))
        if not referenced_body.is_file() or not referenced_sidecar.is_file():
            raise PITIntegrityError(f"referenced {label} evidence is missing")
        referenced_raw = referenced_body.read_bytes()
        referenced_sha = _sha256_bytes(referenced_raw)
        if referenced_sha != reference.get("sha256"):
            raise PITIntegrityError(f"{label} raw SHA-256 mismatch")
        referenced_meta = _load_json_file(referenced_sidecar, f"{label} sidecar")
        try:
            request_meta = referenced_meta["request"]
            response_meta = referenced_meta["response"]
            method = request_meta["method"]
            url = request_meta["canonical_url"]
            parameters = request_meta["canonical_parameters"]
            authentication = request_meta["authentication"]
            headers = request_meta["headers"]
            status = response_meta["status"]
            final_url = response_meta["final_url"]
            body_sha = response_meta["body_sha256"]
            body_bytes = response_meta["body_bytes"]
            started = response_meta["request_started_at_utc"]
            completed = response_meta["response_completed_at_utc"]
            response_headers = response_meta["selected_headers"]
        except (KeyError, TypeError) as exc:
            raise PITIntegrityError(f"{label} sidecar is incomplete") from exc
        expected_parameters = (
            {"showPermissionSets": "true"} if label == "exchange_info" else {}
        )
        if (
            method != "GET"
            or url != expected_url
            or parameters != expected_parameters
            or authentication != "NONE"
            or status != 200
            or final_url != expected_url
            or body_sha != referenced_sha
            or body_bytes != len(referenced_raw)
            or referenced_meta.get("outcome") != "OK"
            or type(headers) is not dict
            or any(str(name).lower() in FORBIDDEN_HEADER_NAMES for name in headers)
        ):
            raise PITIntegrityError(f"{label} sidecar contract mismatch")
        content_length = response_headers.get("Content-Length")
        if content_length is None:
            raise PITIntegrityError(f"missing {label} Content-Length")
        try:
            parsed_content_length = int(content_length)
        except (TypeError, ValueError) as exc:
            raise PITIntegrityError(f"invalid {label} Content-Length") from exc
        if parsed_content_length != len(referenced_raw):
            raise PITIntegrityError(f"{label} Content-Length mismatch")
        transport = TransportResponse(
            status=status,
            headers=response_headers,
            body=referenced_raw,
            final_url=final_url,
            request_started_at_utc=started,
            response_completed_at_utc=completed,
        )
        _response_times(transport)
        verified[label] = transport

    exchange_ref = raw_requests["exchange_info"]
    raw_path = Path(exchange_ref["body"])
    memberships_path = Path(summary["memberships_path"])
    if not memberships_path.is_file():
        raise PITIntegrityError("referenced snapshot artifact is missing")
    raw = raw_path.read_bytes()
    raw_sha = _sha256_bytes(raw)
    if verified["exchange_info"].response_completed_at_utc != known_at:
        raise PITIntegrityError("known_at is not exchangeInfo response completion")
    transport_contract = summary.get("transport_contract")
    if type(transport_contract) is not dict or type(
        transport_contract.get("max_clock_skew_ms")
    ) is not int:
        raise PITIntegrityError("missing transport clock contract")
    before_ms, exchange_ms, after_ms, verified_known_at_ms = _validate_clock_bracket(
        verified["time_before"],
        verified["exchange_info"],
        verified["time_after"],
        max_clock_skew_ms=transport_contract["max_clock_skew_ms"],
    )
    if verified_known_at_ms != known_at_ms or summary.get("server_time_bracket_ms") != {
        "before": before_ms,
        "exchange_info": exchange_ms,
        "after": after_ms,
    }:
        raise PITIntegrityError("server-time bracket summary mismatch")
    _server_ms, records = _exchange_records(raw)
    archive_candidates = load_archive_candidates(
        symbol_index_path, expected_sha256=symbol_index_sha256
    )
    membership_bytes = memberships_path.read_bytes()
    artifact_sha = _sha256_bytes(membership_bytes)
    if artifact_sha != summary["memberships_sha256"]:
        raise PITIntegrityError("membership artifact SHA-256 mismatch")
    rows: list[dict[str, Any]] = []
    try:
        for line in membership_bytes.decode("utf-8").splitlines():
            row = json.loads(line)
            if type(row) is not dict:
                raise ValueError("non-object row")
            rows.append(row)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PITIntegrityError("invalid membership artifact JSONL") from exc
    extractor_sha = summary["extractor_source_sha256"]
    if not isinstance(extractor_sha, str) or not SHA256_PATTERN.fullmatch(extractor_sha):
        raise PITIntegrityError("invalid extractor source SHA-256")
    if extractor_sha != _sha256_file(Path(__file__)):
        raise PITIntegrityError("extractor source SHA-256 does not match trusted loader")
    expected_rows = _membership_rows(
        records,
        archive_candidates,
        raw_sha256=raw_sha,
        known_at_utc=known_at,
        extractor_sha256=extractor_sha,
    )
    if _jsonl_bytes(rows) != membership_bytes or rows != expected_rows:
        raise PITIntegrityError("membership row, locator, provenance, or ordering mismatch")
    if summary.get("membership_count") != len(rows) or summary.get("strict_eligible_count") != 0:
        raise PITIntegrityError("snapshot summary counts mismatch")
    return LoadedSnapshot(
        snapshot_id=summary["snapshot_id"],
        known_at_utc=known_at,
        known_at_ms=known_at_ms,
        artifact_sha256=artifact_sha,
        raw_exchange_info_sha256=raw_sha,
        memberships=tuple(rows),
    )


def to_alpha_snapshot(
    loaded: LoadedSnapshot, *, formation_time_ms: int, expected_quote_asset: str = "USDT"
) -> PITEligibilitySnapshot:
    if formation_time_ms < loaded.known_at_ms:
        raise PITClockError("current evidence cannot be used before its known_at")
    memberships: list[PITEligibilityEvidence] = []
    for row in loaded.memberships:
        status = (
            TradingStatus.UNKNOWN
            if row["raw_status"] is None
            else TradingStatus.TRADING
            if row["raw_status"] == "TRADING"
            else TradingStatus.NOT_TRADING
        )
        permission = (
            PermissionState.UNKNOWN
            if row["raw_is_spot_trading_allowed"] is None
            else PermissionState.ENABLED
            if row["raw_is_spot_trading_allowed"]
            else PermissionState.DISABLED
        )
        references = tuple(
            EvidenceReference(
                kind=EvidenceKind(binding["kind"]),
                known_at_ms=loaded.known_at_ms,
                sha256=binding["source_raw_sha256"],
            )
            for binding in row["evidence_bindings"]
        )
        memberships.append(
            PITEligibilityEvidence(
                symbol=row["symbol"],
                formation_time_ms=formation_time_ms,
                venue=Venue.BINANCE,
                market_type=MarketType.SPOT,
                trading_status=status,
                spot_permission=permission,
                quote_asset=row["raw_quote_asset"],
                listing=None,
                evidence=references,
            )
        )
    return PITEligibilitySnapshot(
        formation_time_ms=formation_time_ms,
        expected_venue=Venue.BINANCE,
        expected_market_type=MarketType.SPOT,
        expected_quote_asset=expected_quote_asset,
        memberships=tuple(memberships),
        artifact_sha256=loaded.artifact_sha256,
    )


def strict_gate_result(loaded: LoadedSnapshot, *, formation_time_ms: int) -> dict[str, Any]:
    snapshot = to_alpha_snapshot(loaded, formation_time_ms=formation_time_ms)
    symbols = tuple(row["symbol"] for row in loaded.memberships)
    decisions = require_pit_eligibility(
        snapshot, formation_time_ms=formation_time_ms, symbols=symbols
    )
    eligible = sorted(symbol for symbol, decision in decisions.items() if decision.eligible)
    return {
        "snapshot_id": loaded.snapshot_id,
        "formation_time_ms": formation_time_ms,
        "known_at_ms": loaded.known_at_ms,
        "requested_count": len(symbols),
        "eligible_count": len(eligible),
        "eligible_symbols": eligible,
        "all_reasons_include_unknown_listing_window": all(
            "UNKNOWN_LISTING_WINDOW" in decision.reasons
            for decision in decisions.values()
        ),
        "decision": "NEEDS_MORE_DATA" if not eligible else "CONTRACT_VIOLATION",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--processed-root", type=Path, default=DEFAULT_PROCESSED_ROOT)
    parser.add_argument("--symbol-index", type=Path, default=DEFAULT_SYMBOL_INDEX)
    parser.add_argument("--symbol-index-sha256", default=FROZEN_SYMBOL_INDEX_SHA256)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--membership-schema-output", dest="schema_output", type=Path, required=True)
    parser.add_argument("--gate-result-output", dest="gate_output", type=Path, required=True)
    parser.add_argument("--time-url", default=TIME_URL)
    parser.add_argument("--exchange-info-url", default=EXCHANGE_INFO_URL)
    parser.add_argument("--request-timeout-seconds", dest="timeout_seconds", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-clock-skew-ms", type=int, default=DEFAULT_MAX_CLOCK_SKEW_MS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_snapshot(
            snapshot_id=args.snapshot_id,
            raw_root=args.raw_root,
            processed_root=args.processed_root,
            symbol_index_path=args.symbol_index,
            symbol_index_sha256=args.symbol_index_sha256,
            summary_output=args.summary_output,
            schema_output=args.schema_output,
            gate_output=args.gate_output,
            time_url=args.time_url,
            exchange_info_url=args.exchange_info_url,
            timeout_seconds=args.timeout_seconds,
            max_attempts=args.max_attempts,
            max_clock_skew_ms=args.max_clock_skew_ms,
        )
    except PITSnapshotError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
