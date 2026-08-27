"""Acquire and validate a frozen Binance Spot monthly Kline payload inventory.

The workflow is intentionally narrower than a historical trading-universe builder.
It proves only that an exact, frozen archive object supplied a structurally valid
Kline row.  It never infers historical listing, permission, status, eligibility,
or executability from archive availability.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, BinaryIO

from quant_research.binance_spot_archive import sha256_bytes, symbol_evidence_directory_name


DEFAULT_SOURCE_BASE_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ROOT_PREFIX = "data/spot/monthly/klines/"
INTERVAL = "1h"
INTERVAL_MILLISECONDS = 3_600_000
CLOSE_TIME_POLICY_EXACT = "exact"
CLOSE_TIME_POLICY_WITHIN_OPEN_INTERVAL = "within_open_interval"
CLOSE_TIME_POLICIES = frozenset(
    {CLOSE_TIME_POLICY_EXACT, CLOSE_TIME_POLICY_WITHIN_OPEN_INTERVAL}
)
PANEL_START = datetime(2022, 12, 1, tzinfo=timezone.utc)
PANEL_END = datetime(2025, 1, 1, tzinfo=timezone.utc)
PANEL_START_MS = int(PANEL_START.timestamp() * 1000)
PANEL_END_MS = int(PANEL_END.timestamp() * 1000)
EXPECTED_PANEL_HOURS = 18_288
EXPECTED_CANDIDATE_SYMBOLS = 723
EXPECTED_PANEL_CELLS = 13_222_224
EXPECTED_OBJECTS = 18_480
EXPECTED_PAIRS = 9_240
EXPECTED_SYMBOL_LIST_SHA256 = (
    "abcfbaa4b3a44a2336de962c1da2495d254b4bf37800def41af8c66cba20d121"
)
MAX_UNCOMPRESSED_BYTES = 5_000_000
MAX_COMPRESSION_RATIO = 1_000
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
ALLOWED_ZIP_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
DECIMAL_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
INTEGER_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)$")
MILLISECOND_PATTERN = re.compile(r"^[0-9]{13}$")
CHECKSUM_PATTERN = re.compile(
    r"^(?P<sha256>[0-9a-fA-F]{64})[ \t]+(?P<binary>\*)?(?P<name>[^ \t\r\n]+)$"
)
NORMALIZED_COLUMNS = (
    "open_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time_ms",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
    "symbol",
    "source_key",
    "source_zip_sha256",
)
COVERAGE_STATES = {
    "A": "ARCHIVE_KLINE_AVAILABLE",
    "N": "NO_ARCHIVE_OBJECT",
    "M": "VALIDATED_OBJECT_ROW_MISSING",
    "U": "OBJECT_INVALID_OR_UNVERIFIED",
}


class PayloadError(RuntimeError):
    """Base class for payload contract, acquisition, and validation failures."""

    code = "PAYLOAD_ERROR"

    def __init__(self, message: str, *, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.evidence = dict(evidence) if evidence is not None else None


class ContractError(PayloadError):
    code = "CONTRACT_ERROR"


class SourceChangedError(PayloadError):
    code = "SOURCE_CHANGED"


class DownloadError(PayloadError):
    code = "DOWNLOAD_ERROR"


class ExistingObjectError(PayloadError):
    code = "EXISTING_OBJECT_INVALID"


class ChecksumError(PayloadError):
    code = "CHECKSUM_ERROR"


class ZipValidationError(PayloadError):
    code = "ZIP_VALIDATION_ERROR"


class CsvValidationError(PayloadError):
    code = "CSV_VALIDATION_ERROR"


@dataclass(frozen=True)
class InventoryObject:
    key: str
    symbol: str
    month: str
    object_type: str
    size: int
    etag: str
    last_modified: str


@dataclass(frozen=True)
class PayloadPair:
    symbol: str
    month: str
    zip_object: InventoryObject
    checksum_object: InventoryObject


@dataclass(frozen=True)
class FrozenInputs:
    pairs: tuple[PayloadPair, ...]
    candidate_symbols: tuple[str, ...]
    symbol_list_sha256: str
    inventory_sha256: str
    symbol_index_sha256: str
    summary_sha256: str


@dataclass(frozen=True)
class DownloadEvidence:
    key: str
    url: str
    local_path: str
    frozen_etag: str
    frozen_size: int
    frozen_last_modified_utc: str
    attempts: int
    fetched_at_utc: str | None
    http_status: int | None
    response_etag: str | None
    response_content_length: int | None
    response_last_modified_utc: str | None
    downloaded_bytes: int
    local_sha256: str
    reused_local: bool
    attempt_log: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class NonNominalCloseEvent:
    open_time_ms: int
    actual_close_time_ms: int
    nominal_close_time_ms: int
    shortfall_ms: int


@dataclass(frozen=True)
class KlineValidation:
    rows: tuple[tuple[str, ...], ...]
    member_name: str
    compressed_bytes: int
    uncompressed_bytes: int
    compression_method: int
    row_count: int
    first_open_time_ms: int
    last_open_time_ms: int
    missing_internal_hours: int
    leading_missing_hours: int
    trailing_missing_hours: int
    zero_volume_rows: int
    zero_trade_rows: int
    ignore_values: tuple[str, ...]
    non_nominal_close_events: tuple[NonNominalCloseEvent, ...]


UrlOpen = Callable[..., Any]
Sleeper = Callable[[float], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(DOWNLOAD_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (text + "\n").encode("utf-8")


def _write_temp_and_commit(path: Path, writer: Callable[[BinaryIO], None]) -> str:
    """Write a derived artifact atomically, refusing a non-identical overwrite."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b", prefix=f".{path.name}.", suffix=".partial", dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            writer(temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path = Path(temporary_name)
        temporary_hash = _sha256_file(temporary_path)
        if path.exists():
            if _sha256_file(path) != temporary_hash:
                raise ExistingObjectError(f"refusing to overwrite non-identical artifact {path}")
            temporary_path.unlink()
            return temporary_hash
        os.replace(temporary_path, path)
        temporary_name = None
        return temporary_hash
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _write_json(path: Path, value: Any, *, pretty: bool = True) -> str:
    payload = _stable_json_bytes(value, pretty=pretty)
    return _write_temp_and_commit(path, lambda stream: stream.write(payload))


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> str:
    ordered = list(rows)

    def writer(stream: BinaryIO) -> None:
        for row in ordered:
            stream.write(_stable_json_bytes(dict(row), pretty=False))

    return _write_temp_and_commit(path, writer)


def _verify_sha256(path: Path, expected: str, label: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ContractError(f"invalid frozen SHA-256 for {label}: {expected!r}")
    if not path.is_file():
        raise ContractError(f"missing frozen {label}: {path}")
    actual = _sha256_file(path)
    if actual != expected:
        raise ContractError(
            f"frozen {label} SHA-256 mismatch: expected {expected}, received {actual}"
        )
    return actual


def _normalize_etag(value: str | None) -> str:
    if value is None:
        raise SourceChangedError("response is missing ETag")
    normalized = value.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        normalized = normalized[1:-1]
    if not re.fullmatch(r"[0-9a-fA-F]{32}(?:-[0-9]+)?", normalized):
        raise SourceChangedError(f"invalid ETag {value!r}")
    return normalized.lower()


def _quoted_etag(value: str) -> str:
    return f'"{_normalize_etag(value)}"'


def _parse_utc_instant(value: str, *, http_date: bool = False) -> datetime:
    try:
        parsed = parsedate_to_datetime(value) if http_date else datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise SourceChangedError(f"invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _symbol_list_bytes(symbols: Sequence[str]) -> bytes:
    return (("\n".join(symbols)) + "\n").encode("utf-8")


def load_frozen_inputs(
    *,
    inventory_path: Path,
    inventory_sha256: str,
    symbol_index_path: Path,
    symbol_index_sha256: str,
    summary_path: Path,
    summary_sha256: str,
    expected_objects: int = EXPECTED_OBJECTS,
    expected_pairs: int = EXPECTED_PAIRS,
    expected_candidate_symbols: int = EXPECTED_CANDIDATE_SYMBOLS,
    expected_symbol_list_sha256: str = EXPECTED_SYMBOL_LIST_SHA256,
) -> FrozenInputs:
    """Load and cross-check the exact inventory, symbol index, and summary contract."""

    _verify_sha256(inventory_path, inventory_sha256, "inventory")
    _verify_sha256(symbol_index_path, symbol_index_sha256, "symbol index")
    _verify_sha256(summary_path, summary_sha256, "inventory summary")

    candidates: list[str] = []
    seen_symbols: set[str] = set()
    with symbol_index_path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(
                    f"invalid symbol-index JSON at line {line_number}: {exc}"
                ) from exc
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or not symbol or symbol in seen_symbols:
                raise ContractError(f"invalid or duplicate symbol-index symbol at line {line_number}")
            seen_symbols.add(symbol)
            if row.get("suffix_candidate") is True:
                candidates.append(symbol)
    candidate_symbols = tuple(sorted(candidates))
    if len(candidate_symbols) != expected_candidate_symbols:
        raise ContractError(
            f"candidate-symbol count mismatch: expected {expected_candidate_symbols}, "
            f"received {len(candidate_symbols)}"
        )
    symbol_list_sha = sha256_bytes(_symbol_list_bytes(candidate_symbols))
    if symbol_list_sha != expected_symbol_list_sha256:
        raise ContractError(
            f"candidate-symbol list SHA-256 mismatch: expected "
            f"{expected_symbol_list_sha256}, received {symbol_list_sha}"
        )

    records: list[InventoryObject] = []
    seen_keys: set[str] = set()
    with inventory_path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(f"invalid inventory JSON at line {line_number}: {exc}") from exc
            required = {
                "key", "symbol", "month", "object_type", "size", "etag", "last_modified"
            }
            if not required.issubset(row):
                raise ContractError(f"inventory line {line_number} lacks required fields")
            key = row["key"]
            symbol = row["symbol"]
            month = row["month"]
            object_type = row["object_type"]
            if not isinstance(key, str) or key in seen_keys:
                raise ContractError(f"invalid or duplicate inventory key at line {line_number}")
            if symbol not in candidate_symbols:
                raise ContractError(f"inventory symbol {symbol!r} is not a frozen candidate")
            if not re.fullmatch(r"20(?:22-12|23-(?:0[1-9]|1[0-2])|24-(?:0[1-9]|1[0-2]))", month):
                raise ContractError(f"inventory month {month!r} is outside the frozen range")
            suffix = ".zip.CHECKSUM" if object_type == "checksum" else ".zip"
            if object_type not in {"zip", "checksum"}:
                raise ContractError(f"invalid inventory object type {object_type!r}")
            expected_key = (
                f"{ROOT_PREFIX}{symbol}/{INTERVAL}/{symbol}-{INTERVAL}-{month}{suffix}"
            )
            if key != expected_key:
                raise ContractError(f"inventory key does not match frozen fields: {key!r}")
            size = row["size"]
            etag = row["etag"]
            last_modified = row["last_modified"]
            if not isinstance(size, int) or size < 0:
                raise ContractError(f"invalid inventory size for {key!r}")
            if not isinstance(etag, str) or not isinstance(last_modified, str):
                raise ContractError(f"missing identity metadata for {key!r}")
            _normalize_etag(etag)
            _parse_utc_instant(last_modified)
            seen_keys.add(key)
            records.append(
                InventoryObject(
                    key=key,
                    symbol=symbol,
                    month=month,
                    object_type=object_type,
                    size=size,
                    etag=_normalize_etag(etag),
                    last_modified=last_modified,
                )
            )
    if len(records) != expected_objects:
        raise ContractError(
            f"inventory record count mismatch: expected {expected_objects}, received {len(records)}"
        )

    grouped: dict[tuple[str, str], dict[str, InventoryObject]] = defaultdict(dict)
    for record in records:
        identity = (record.symbol, record.month)
        if record.object_type in grouped[identity]:
            raise ContractError(f"duplicate {record.object_type} for {identity}")
        grouped[identity][record.object_type] = record
    pairs: list[PayloadPair] = []
    for (symbol, month), objects in sorted(grouped.items()):
        if set(objects) != {"zip", "checksum"}:
            raise ContractError(f"incomplete ZIP/CHECKSUM pair for {(symbol, month)}")
        zip_object = objects["zip"]
        checksum_object = objects["checksum"]
        if checksum_object.key != zip_object.key + ".CHECKSUM":
            raise ContractError(f"checksum key mismatch for {zip_object.key!r}")
        pairs.append(PayloadPair(symbol, month, zip_object, checksum_object))
    if len(pairs) != expected_pairs:
        raise ContractError(
            f"payload pair count mismatch: expected {expected_pairs}, received {len(pairs)}"
        )

    with summary_path.open("r", encoding="utf-8") as stream:
        summary = json.load(stream)
    inventory_summary = summary.get("inventory", {})
    archive_summary = summary.get("archive", {})
    expected_summary = {
        "zip_count": expected_pairs,
        "checksum_count": expected_pairs,
        "inventory_record_count": expected_objects,
    }
    for field, expected in expected_summary.items():
        if inventory_summary.get(field) != expected:
            raise ContractError(
                f"inventory summary {field} mismatch: expected {expected}, "
                f"received {inventory_summary.get(field)!r}"
            )
    if archive_summary.get("candidate_symbol_count") != expected_candidate_symbols:
        raise ContractError("inventory summary candidate-symbol count mismatch")
    if inventory_summary.get("inventory_jsonl_sha256") != inventory_sha256:
        raise ContractError("inventory summary contains a different inventory SHA-256")
    if archive_summary.get("symbol_index_sha256") != symbol_index_sha256:
        raise ContractError("inventory summary contains a different symbol-index SHA-256")

    return FrozenInputs(
        pairs=tuple(pairs),
        candidate_symbols=candidate_symbols,
        symbol_list_sha256=symbol_list_sha,
        inventory_sha256=inventory_sha256,
        symbol_index_sha256=symbol_index_sha256,
        summary_sha256=summary_sha256,
    )


def _response_header(response: Any, name: str) -> str | None:
    value = response.headers.get(name)
    return str(value) if value is not None else None


def _retry_delay(attempt: int, response: Any | None = None) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None and str(retry_after).isdigit():
            return min(float(retry_after), 60.0)
    return float(min(2 ** (attempt - 1), 8))


def _attempt_failure_evidence(
    record: InventoryObject,
    *,
    url: str,
    destination: Path,
    attempt_log: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "key": record.key,
        "url": url,
        "local_path": str(destination),
        "frozen_etag": record.etag,
        "frozen_size": record.size,
        "frozen_last_modified_utc": _parse_utc_instant(record.last_modified).isoformat(),
        "attempt_log": [dict(value) for value in attempt_log],
    }


def acquire_object(
    record: InventoryObject,
    *,
    destination: Path,
    source_base_url: str = DEFAULT_SOURCE_BASE_URL,
    timeout_seconds: int = 30,
    max_attempts: int = 5,
    opener: UrlOpen = urllib.request.urlopen,
    sleeper: Sleeper = time.sleep,
) -> DownloadEvidence:
    """Conditionally fetch one frozen S3 object and atomically preserve its bytes."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    url = f"{source_base_url.rstrip('/')}/{urllib.parse.quote(record.key, safe='/')}"
    existing_hash: str | None = None
    if destination.exists():
        actual_size = destination.stat().st_size
        if actual_size != record.size:
            raise ExistingObjectError(
                f"existing object size mismatch for {destination}: expected {record.size}, "
                f"received {actual_size}"
            )
        existing_hash = _sha256_file(destination)

    last_error: BaseException | None = None
    attempt_log: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        attempt_record: dict[str, Any] = {
            "attempt": attempt,
            "started_at_utc": _utc_now(),
            "http_status": None,
            "response_headers": {},
            "response_url": None,
            "downloaded_bytes": 0,
            "outcome": "STARTED",
            "error": None,
        }
        attempt_log.append(attempt_record)
        request = urllib.request.Request(
            url,
            headers={
                "If-Match": _quoted_etag(record.etag),
                "User-Agent": "binance-quant-research-payload/1.0",
                "Accept-Encoding": "identity",
            },
            method="GET",
        )
        temporary_name: str | None = None
        try:
            response = opener(request, timeout=timeout_seconds)
            with response:
                status_value = getattr(response, "status", None)
                status_code = int(status_value if status_value is not None else response.getcode())
                attempt_record["http_status"] = status_code
                attempt_record["response_url"] = (
                    response.geturl() if hasattr(response, "geturl") else url
                )
                attempt_record["response_headers"] = {
                    name.lower(): _response_header(response, name)
                    for name in ("ETag", "Content-Length", "Last-Modified", "Retry-After")
                    if _response_header(response, name) is not None
                }
                if status_code != 200:
                    if status_code == 412:
                        raise SourceChangedError(f"If-Match failed for {record.key}")
                    if status_code in RETRYABLE_HTTP_STATUSES and attempt < max_attempts:
                        last_error = DownloadError(f"HTTP {status_code} for {record.key}")
                        attempt_record["outcome"] = "RETRYABLE_HTTP"
                        attempt_record["error"] = str(last_error)
                        sleeper(_retry_delay(attempt, response))
                        continue
                    raise DownloadError(f"HTTP {status_code} for {record.key}")
                if attempt_record["response_url"] != url:
                    raise SourceChangedError(
                        f"unexpected redirect for {record.key}: expected {url}, "
                        f"received {attempt_record['response_url']}"
                    )
                response_etag = _normalize_etag(_response_header(response, "ETag"))
                if response_etag != record.etag:
                    raise SourceChangedError(
                        f"ETag changed for {record.key}: expected {record.etag}, "
                        f"received {response_etag}"
                    )
                content_length_text = _response_header(response, "Content-Length")
                if content_length_text is None or not content_length_text.isdigit():
                    raise SourceChangedError(f"missing or invalid Content-Length for {record.key}")
                content_length = int(content_length_text)
                if content_length != record.size:
                    raise SourceChangedError(
                        f"Content-Length changed for {record.key}: expected {record.size}, "
                        f"received {content_length}"
                    )
                last_modified_text = _response_header(response, "Last-Modified")
                if last_modified_text is None:
                    raise SourceChangedError(f"missing Last-Modified for {record.key}")
                frozen_modified = _parse_utc_instant(record.last_modified)
                response_modified = _parse_utc_instant(last_modified_text, http_date=True)
                if response_modified != frozen_modified:
                    raise SourceChangedError(
                        f"Last-Modified changed for {record.key}: expected "
                        f"{frozen_modified.isoformat()}, received {response_modified.isoformat()}"
                    )
                digest = hashlib.sha256()
                downloaded = 0
                with tempfile.NamedTemporaryFile(
                    mode="w+b", prefix=f".{destination.name}.", suffix=".partial",
                    dir=destination.parent, delete=False,
                ) as temporary:
                    temporary_name = temporary.name
                    while True:
                        chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        temporary.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        attempt_record["downloaded_bytes"] = downloaded
                    temporary.flush()
                    os.fsync(temporary.fileno())
                if downloaded != record.size:
                    truncation = (
                        f"truncated object {record.key}: expected {record.size}, "
                        f"received {downloaded}"
                    )
                    attempt_record["outcome"] = "TRUNCATED_RESPONSE"
                    attempt_record["error"] = truncation
                    last_error = DownloadError(truncation)
                    if attempt < max_attempts:
                        sleeper(_retry_delay(attempt))
                        continue
                    raise DownloadError(truncation)
                downloaded_hash = digest.hexdigest()
                reused_local = False
                if destination.exists():
                    current_hash = _sha256_file(destination)
                    if existing_hash is None or current_hash != existing_hash:
                        raise ExistingObjectError(
                            f"destination appeared or changed during download: {destination}"
                        )
                    if downloaded_hash != existing_hash:
                        raise ExistingObjectError(
                            f"contract-bound local bytes differ from the frozen source for "
                            f"{record.key}"
                        )
                    Path(temporary_name).unlink()
                    temporary_name = None
                    reused_local = True
                else:
                    os.replace(temporary_name, destination)
                    temporary_name = None
                attempt_record["outcome"] = "SUCCESS"
                return DownloadEvidence(
                    key=record.key,
                    url=url,
                    local_path=str(destination),
                    frozen_etag=record.etag,
                    frozen_size=record.size,
                    frozen_last_modified_utc=frozen_modified.isoformat(),
                    attempts=attempt,
                    fetched_at_utc=_utc_now(),
                    http_status=status_code,
                    response_etag=response_etag,
                    response_content_length=content_length,
                    response_last_modified_utc=response_modified.isoformat(),
                    downloaded_bytes=downloaded,
                    local_sha256=downloaded_hash,
                    reused_local=reused_local,
                    attempt_log=tuple(dict(value) for value in attempt_log),
                )
        except urllib.error.HTTPError as exc:
            attempt_record["http_status"] = exc.code
            attempt_record["response_headers"] = {
                str(name).lower(): str(value)
                for name, value in (exc.headers.items() if exc.headers is not None else [])
            }
            attempt_record["outcome"] = "HTTP_ERROR"
            attempt_record["error"] = f"HTTP {exc.code}"
            if exc.code == 412:
                raise SourceChangedError(
                    f"If-Match failed for {record.key}",
                    evidence=_attempt_failure_evidence(
                        record, url=url, destination=destination, attempt_log=attempt_log
                    ),
                ) from exc
            if exc.code not in RETRYABLE_HTTP_STATUSES or attempt == max_attempts:
                raise DownloadError(
                    f"HTTP {exc.code} for {record.key}",
                    evidence=_attempt_failure_evidence(
                        record, url=url, destination=destination, attempt_log=attempt_log
                    ),
                ) from exc
            last_error = exc
            sleeper(_retry_delay(attempt, exc))
        except SourceChangedError as exc:
            attempt_record["outcome"] = "SOURCE_CHANGED"
            attempt_record["error"] = str(exc)
            if exc.evidence is None:
                exc.evidence = _attempt_failure_evidence(
                    record, url=url, destination=destination, attempt_log=attempt_log
                )
            raise
        except DownloadError as exc:
            attempt_record["outcome"] = "DOWNLOAD_ERROR"
            attempt_record["error"] = str(exc)
            if exc.evidence is None:
                exc.evidence = _attempt_failure_evidence(
                    record, url=url, destination=destination, attempt_log=attempt_log
                )
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            attempt_record["outcome"] = "NETWORK_ERROR"
            attempt_record["error"] = f"{type(exc).__name__}: {exc}"
            last_error = exc
            if attempt == max_attempts:
                raise DownloadError(
                    f"network acquisition failed for {record.key} after {attempt} attempts: {exc}",
                    evidence=_attempt_failure_evidence(
                        record, url=url, destination=destination, attempt_log=attempt_log
                    ),
                ) from exc
            sleeper(_retry_delay(attempt))
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)
    raise DownloadError(
        f"acquisition failed for {record.key}: {last_error}",
        evidence=_attempt_failure_evidence(
            record, url=url, destination=destination, attempt_log=attempt_log
        ),
    )


def parse_checksum(payload: bytes, *, expected_basename: str) -> str:
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ChecksumError("CHECKSUM is not ASCII") from exc
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise ChecksumError("CHECKSUM must contain exactly one non-empty record")
    match = CHECKSUM_PATTERN.fullmatch(lines[0])
    if match is None:
        raise ChecksumError("CHECKSUM record has an invalid format")
    name = match.group("name")
    if name != expected_basename or any(value in name for value in ("/", "\\", "\x00")):
        raise ChecksumError(
            f"CHECKSUM filename mismatch: expected {expected_basename!r}, received {name!r}"
        )
    return match.group("sha256").lower()


def _month_bounds_ms(month: str) -> tuple[int, int]:
    year, month_number = (int(value) for value in month.split("-"))
    start = datetime(year, month_number, 1, tzinfo=timezone.utc)
    if month_number == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month_number + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _parse_nonnegative_decimal(value: str, *, field: str, positive: bool = False) -> Decimal:
    if DECIMAL_PATTERN.fullmatch(value) is None:
        raise CsvValidationError(f"invalid canonical decimal for {field}: {value!r}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CsvValidationError(f"invalid decimal for {field}: {value!r}") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        raise CsvValidationError(f"invalid value for {field}: {value!r}")
    return parsed


def _validate_kline_row(
    row: Sequence[str],
    *,
    month_start_ms: int,
    month_end_ms: int,
    previous_open_time_ms: int | None,
    close_time_policy: str = CLOSE_TIME_POLICY_EXACT,
) -> tuple[int, tuple[str, ...], bool, bool, NonNominalCloseEvent | None]:
    if len(row) != 12:
        raise CsvValidationError(f"Kline row has {len(row)} columns, expected 12")
    if MILLISECOND_PATTERN.fullmatch(row[0]) is None:
        raise CsvValidationError(f"open_time is not a 13-digit millisecond timestamp: {row[0]!r}")
    if MILLISECOND_PATTERN.fullmatch(row[6]) is None:
        raise CsvValidationError(f"close_time is not a 13-digit millisecond timestamp: {row[6]!r}")
    open_time = int(row[0])
    close_time = int(row[6])
    nominal_close_time = open_time + INTERVAL_MILLISECONDS - 1
    if open_time % INTERVAL_MILLISECONDS != 0:
        raise CsvValidationError(f"open_time is not aligned to a UTC hour: {open_time}")
    if not month_start_ms <= open_time < month_end_ms:
        raise CsvValidationError(f"open_time spills outside the declared month: {open_time}")
    if close_time_policy not in CLOSE_TIME_POLICIES:
        raise ContractError(f"unsupported close_time_policy {close_time_policy!r}")
    non_nominal_event: NonNominalCloseEvent | None = None
    if close_time_policy == CLOSE_TIME_POLICY_EXACT:
        if close_time != nominal_close_time:
            raise CsvValidationError(
                f"close_time does not equal open_time + 3,599,999: {close_time}"
            )
    elif not open_time <= close_time <= nominal_close_time:
        raise CsvValidationError(
            "close_time lies outside its open-time interval: "
            f"open={open_time}, close={close_time}, nominal={nominal_close_time}"
        )
    elif close_time != nominal_close_time:
        non_nominal_event = NonNominalCloseEvent(
            open_time_ms=open_time,
            actual_close_time_ms=close_time,
            nominal_close_time_ms=nominal_close_time,
            shortfall_ms=nominal_close_time - close_time,
        )
    if previous_open_time_ms is not None:
        if open_time <= previous_open_time_ms:
            raise CsvValidationError(
                f"Kline timestamps are duplicate or unsorted: {previous_open_time_ms}, {open_time}"
            )
        if (open_time - previous_open_time_ms) % INTERVAL_MILLISECONDS != 0:
            raise CsvValidationError("Kline timestamp difference is not an integer number of hours")

    open_price = _parse_nonnegative_decimal(row[1], field="open", positive=True)
    high = _parse_nonnegative_decimal(row[2], field="high", positive=True)
    low = _parse_nonnegative_decimal(row[3], field="low", positive=True)
    close = _parse_nonnegative_decimal(row[4], field="close", positive=True)
    volume = _parse_nonnegative_decimal(row[5], field="volume")
    quote_volume = _parse_nonnegative_decimal(row[7], field="quote_asset_volume")
    if INTEGER_PATTERN.fullmatch(row[8]) is None:
        raise CsvValidationError(f"number_of_trades is not a nonnegative integer: {row[8]!r}")
    number_of_trades = int(row[8])
    taker_base = _parse_nonnegative_decimal(row[9], field="taker_buy_base_asset_volume")
    taker_quote = _parse_nonnegative_decimal(row[10], field="taker_buy_quote_asset_volume")
    _parse_nonnegative_decimal(row[11], field="ignore")
    if not (low <= open_price <= high and low <= close <= high):
        raise CsvValidationError("OHLC ordering is invalid")
    if taker_base > volume:
        raise CsvValidationError("taker-buy base volume exceeds base volume")
    if taker_quote > quote_volume:
        raise CsvValidationError("taker-buy quote volume exceeds quote volume")
    return (
        open_time,
        tuple(row),
        volume == 0,
        number_of_trades == 0,
        non_nominal_event,
    )


def validate_kline_zip(
    path: Path,
    *,
    symbol: str,
    month: str,
    max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: int = MAX_COMPRESSION_RATIO,
    close_time_policy: str = CLOSE_TIME_POLICY_EXACT,
) -> KlineValidation:
    """Read one immutable byte snapshot and validate its ZIP and Kline contents."""

    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ZipValidationError(f"cannot read ZIP {path}: {exc}") from exc
    return validate_kline_zip_bytes(
        payload,
        symbol=symbol,
        month=month,
        max_uncompressed_bytes=max_uncompressed_bytes,
        max_compression_ratio=max_compression_ratio,
        close_time_policy=close_time_policy,
    )


def validate_kline_zip_bytes(
    payload: bytes,
    *,
    symbol: str,
    month: str,
    max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
    max_compression_ratio: int = MAX_COMPRESSION_RATIO,
    close_time_policy: str = CLOSE_TIME_POLICY_EXACT,
) -> KlineValidation:
    """Validate ZIP safety, CRC, exact member identity, and every Kline row."""

    expected_member = f"{symbol}-{INTERVAL}-{month}.csv"
    if close_time_policy not in CLOSE_TIME_POLICIES:
        raise ContractError(f"unsupported close_time_policy {close_time_policy!r}")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (OSError, zipfile.BadZipFile) as exc:
        raise ZipValidationError(f"invalid ZIP payload: {exc}") from exc
    with archive:
        members = archive.infolist()
        if len(members) != 1:
            raise ZipValidationError(f"ZIP must contain exactly one member, received {len(members)}")
        member = members[0]
        if member.is_dir() or member.filename != expected_member:
            raise ZipValidationError(
                f"ZIP member mismatch: expected {expected_member!r}, received {member.filename!r}"
            )
        if (
            member.filename.startswith(("/", "\\"))
            or "\\" in member.filename
            or ":" in member.filename
            or any(part in {"", ".", ".."} for part in member.filename.split("/"))
        ):
            raise ZipValidationError(f"unsafe ZIP member path {member.filename!r}")
        unix_mode = (member.external_attr >> 16) & 0xFFFF
        if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
            raise ZipValidationError("ZIP member is a symbolic link")
        if member.flag_bits & 0x1:
            raise ZipValidationError("encrypted ZIP members are not allowed")
        if member.compress_type not in ALLOWED_ZIP_METHODS:
            raise ZipValidationError(f"unsupported ZIP compression method {member.compress_type}")
        if member.file_size > max_uncompressed_bytes:
            raise ZipValidationError(
                f"ZIP member exceeds uncompressed-size gate: {member.file_size}"
            )
        ratio = member.file_size / max(member.compress_size, 1)
        if ratio > max_compression_ratio:
            raise ZipValidationError(f"ZIP compression ratio exceeds gate: {ratio}")

        month_start, month_end = _month_bounds_ms(month)
        rows: list[tuple[str, ...]] = []
        previous: int | None = None
        missing_internal = 0
        zero_volume = 0
        zero_trades = 0
        ignore_values: set[str] = set()
        non_nominal_close_events: list[NonNominalCloseEvent] = []
        try:
            with archive.open(member, "r") as binary:
                with io.TextIOWrapper(binary, encoding="utf-8", errors="strict", newline="") as text:
                    reader = csv.reader(text, strict=True)
                    for line_number, row in enumerate(reader, start=1):
                        if not row:
                            raise CsvValidationError(f"blank CSV row at line {line_number}")
                        (
                            open_time,
                            frozen_row,
                            row_zero_volume,
                            row_zero_trades,
                            non_nominal_event,
                        ) = _validate_kline_row(
                            row,
                            month_start_ms=month_start,
                            month_end_ms=month_end,
                            previous_open_time_ms=previous,
                            close_time_policy=close_time_policy,
                        )
                        if previous is not None:
                            missing_internal += (
                                (open_time - previous) // INTERVAL_MILLISECONDS - 1
                            )
                        rows.append(frozen_row)
                        previous = open_time
                        zero_volume += int(row_zero_volume)
                        zero_trades += int(row_zero_trades)
                        ignore_values.add(row[11])
                        if non_nominal_event is not None:
                            non_nominal_close_events.append(non_nominal_event)
        except (UnicodeDecodeError, csv.Error, zipfile.BadZipFile, RuntimeError) as exc:
            raise CsvValidationError(f"failed reading ZIP CSV member: {exc}") from exc
        if not rows:
            raise CsvValidationError("Kline CSV contains no rows")

        first = int(rows[0][0])
        last = int(rows[-1][0])
        leading = (first - month_start) // INTERVAL_MILLISECONDS
        trailing = (month_end - (last + INTERVAL_MILLISECONDS)) // INTERVAL_MILLISECONDS
        return KlineValidation(
            rows=tuple(rows),
            member_name=member.filename,
            compressed_bytes=member.compress_size,
            uncompressed_bytes=member.file_size,
            compression_method=member.compress_type,
            row_count=len(rows),
            first_open_time_ms=first,
            last_open_time_ms=last,
            missing_internal_hours=missing_internal,
            leading_missing_hours=leading,
            trailing_missing_hours=trailing,
            zero_volume_rows=zero_volume,
            zero_trade_rows=zero_trades,
            ignore_values=tuple(sorted(ignore_values)),
            non_nominal_close_events=tuple(non_nominal_close_events),
        )


def raw_object_paths(raw_root: Path, pair: PayloadPair) -> tuple[Path, Path]:
    """Return the contract-bound local ZIP and CHECKSUM paths for one pair."""

    directory = raw_root / symbol_evidence_directory_name(pair.symbol) / pair.month
    return directory / "payload.zip", directory / "payload.zip.CHECKSUM"


def _object_paths(raw_root: Path, pair: PayloadPair) -> tuple[Path, Path]:
    """Backward-compatible private alias used by the exp004 acquisition path."""

    return raw_object_paths(raw_root, pair)


def _validation_without_rows(value: KlineValidation) -> dict[str, Any]:
    result = asdict(value)
    result.pop("rows")
    return result


def process_pair(
    pair: PayloadPair,
    *,
    raw_root: Path,
    source_base_url: str,
    timeout_seconds: int,
    max_attempts: int,
    opener: UrlOpen = urllib.request.urlopen,
    sleeper: Sleeper = time.sleep,
) -> dict[str, Any]:
    """Acquire and atomically validate one symbol-month pair."""

    zip_path, checksum_path = _object_paths(raw_root, pair)
    base: dict[str, Any] = {
        "symbol": pair.symbol,
        "month": pair.month,
        "zip_key": pair.zip_object.key,
        "checksum_key": pair.checksum_object.key,
        "status": "U",
        "failure_code": None,
        "failure_reason": None,
    }
    try:
        checksum_evidence = acquire_object(
            pair.checksum_object,
            destination=checksum_path,
            source_base_url=source_base_url,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            opener=opener,
            sleeper=sleeper,
        )
        base["checksum_evidence"] = asdict(checksum_evidence)
        zip_evidence = acquire_object(
            pair.zip_object,
            destination=zip_path,
            source_base_url=source_base_url,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            opener=opener,
            sleeper=sleeper,
        )
        base["zip_evidence"] = asdict(zip_evidence)
        expected_sha = parse_checksum(
            checksum_path.read_bytes(), expected_basename=Path(pair.zip_object.key).name
        )
        zip_bytes = zip_path.read_bytes()
        actual_sha = sha256_bytes(zip_bytes)
        if actual_sha != expected_sha:
            raise ChecksumError(
                f"ZIP SHA-256 mismatch for {pair.zip_object.key}: expected {expected_sha}, "
                f"received {actual_sha}"
            )
        validation = validate_kline_zip_bytes(
            zip_bytes, symbol=pair.symbol, month=pair.month
        )
        base.update(
            {
                "status": "VALID",
                "checksum_expected_sha256": expected_sha,
                "zip_sha256": actual_sha,
                "zip_local_path": str(zip_path),
                "checksum_local_path": str(checksum_path),
                "validation": _validation_without_rows(validation),
            }
        )
    except PayloadError as exc:
        base["failure_code"] = exc.code
        base["failure_reason"] = str(exc)
        if exc.evidence is not None:
            base["failure_evidence"] = exc.evidence
    except Exception as exc:  # preserved as evidence; never interpreted as valid
        base["failure_code"] = "UNEXPECTED_ERROR"
        base["failure_reason"] = f"{type(exc).__name__}: {exc}"
    receipt_directory = zip_path.parent / "receipts"
    receipt_path = receipt_directory / (
        f"attempt_{time.time_ns()}_{uuid.uuid4().hex}.json"
    )
    base["receipt_path"] = str(receipt_path)
    try:
        _write_json(receipt_path, base, pretty=True)
    except Exception as exc:
        base["status"] = "U"
        base["failure_code"] = "EVIDENCE_WRITE_ERROR"
        base["failure_reason"] = f"{type(exc).__name__}: {exc}"
    return base


def _run_contract(
    *, frozen: FrozenInputs, source_base_url: str, timeout_seconds: int,
    max_attempts: int, max_workers: int,
) -> dict[str, Any]:
    return {
        "contract_version": 1,
        "source_file_sha256": _sha256_file(Path(__file__)),
        "inventory_sha256": frozen.inventory_sha256,
        "symbol_index_sha256": frozen.symbol_index_sha256,
        "summary_sha256": frozen.summary_sha256,
        "symbol_list_sha256": frozen.symbol_list_sha256,
        "source_base_url": source_base_url.rstrip("/"),
        "timeout_seconds": timeout_seconds,
        "max_attempts": max_attempts,
        "max_workers": max_workers,
        "pair_count": len(frozen.pairs),
        "candidate_symbol_count": len(frozen.candidate_symbols),
    }


def _ensure_run_contract(raw_root: Path, contract: Mapping[str, Any]) -> None:
    raw_root.mkdir(parents=True, exist_ok=True)
    path = raw_root / "run_contract.json"
    payload = _stable_json_bytes(dict(contract), pretty=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ContractError(f"raw-root run contract differs; refusing resume: {path}")
        return
    _write_temp_and_commit(path, lambda stream: stream.write(payload))


def _deterministic_gzip_writer(path: Path, callback: Callable[[io.TextIOBase], None]) -> str:
    def writer(binary: BinaryIO) -> None:
        with gzip.GzipFile(filename="", mode="wb", fileobj=binary, mtime=0) as compressed:
            with io.TextIOWrapper(
                compressed, encoding="utf-8", newline="", write_through=True
            ) as text:
                callback(text)

    return _write_temp_and_commit(path, writer)


def build_derived_outputs(
    *,
    frozen: FrozenInputs,
    quality_records: list[dict[str, Any]],
    processed_root: Path,
    coverage_schema_output: Path,
    derived_index_output: Path,
    panel_start_ms: int = PANEL_START_MS,
    panel_end_ms: int = PANEL_END_MS,
    close_time_policy: str = CLOSE_TIME_POLICY_EXACT,
) -> dict[str, Any]:
    """Build deterministic normalized Klines and a reason-coded availability panel."""

    if (panel_end_ms - panel_start_ms) % INTERVAL_MILLISECONDS != 0:
        raise ContractError("panel boundary is not an integer number of hours")
    panel_hours = (panel_end_ms - panel_start_ms) // INTERVAL_MILLISECONDS
    states = {
        symbol: bytearray(b"N" * panel_hours) for symbol in frozen.candidate_symbols
    }
    quality_by_identity = {
        (row["symbol"], row["month"]): row for row in quality_records
    }
    pairs_by_symbol: dict[str, list[PayloadPair]] = defaultdict(list)
    for pair in frozen.pairs:
        pairs_by_symbol[pair.symbol].append(pair)

    normalized_root = processed_root / "klines_1h_by_symbol"
    derived_rows: list[dict[str, Any]] = []
    total_normalized_rows = 0
    for symbol in frozen.candidate_symbols:
        valid_chunks: list[tuple[PayloadPair, KlineValidation, str]] = []
        for pair in sorted(pairs_by_symbol.get(symbol, []), key=lambda value: value.month):
            quality = quality_by_identity[(symbol, pair.month)]
            month_start, month_end = _month_bounds_ms(pair.month)
            start_index = (month_start - panel_start_ms) // INTERVAL_MILLISECONDS
            end_index = (month_end - panel_start_ms) // INTERVAL_MILLISECONDS
            if not (0 <= start_index < end_index <= panel_hours):
                raise ContractError(f"pair month lies outside panel: {(symbol, pair.month)}")
            state = ord("M") if quality["status"] == "VALID" else ord("U")
            states[symbol][start_index:end_index] = bytes([state]) * (end_index - start_index)
            if quality["status"] != "VALID":
                continue
            try:
                zip_bytes = Path(quality["zip_local_path"]).read_bytes()
                current_sha = sha256_bytes(zip_bytes)
                if current_sha != quality["zip_sha256"]:
                    raise ChecksumError(
                        f"ZIP bytes changed after validation for {pair.zip_object.key}: "
                        f"expected {quality['zip_sha256']}, received {current_sha}"
                    )
                validation = validate_kline_zip_bytes(
                    zip_bytes,
                    symbol=symbol,
                    month=pair.month,
                    close_time_policy=close_time_policy,
                )
            except (PayloadError, OSError) as exc:
                quality["status"] = "U"
                quality["failure_code"] = "DERIVATION_REVALIDATION_FAILED"
                quality["failure_reason"] = (
                    str(exc)
                    if isinstance(exc, PayloadError)
                    else f"{type(exc).__name__}: {exc}"
                )
                states[symbol][start_index:end_index] = b"U" * (end_index - start_index)
                continue
            for row in validation.rows:
                index = (int(row[0]) - panel_start_ms) // INTERVAL_MILLISECONDS
                states[symbol][index] = ord("A")
            valid_chunks.append((pair, validation, quality["zip_sha256"]))

        normalized_path: Path | None = None
        normalized_sha: str | None = None
        normalized_row_count = sum(value.row_count for _, value, _ in valid_chunks)
        if normalized_row_count:
            normalized_path = normalized_root / f"{symbol_evidence_directory_name(symbol)}.csv.gz"

            def write_symbol(text: io.TextIOBase) -> None:
                writer = csv.writer(text, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
                writer.writerow(NORMALIZED_COLUMNS)
                previous: int | None = None
                for pair, validation, zip_sha in valid_chunks:
                    for row in validation.rows:
                        open_time = int(row[0])
                        if previous is not None and open_time <= previous:
                            raise ContractError(f"cross-month normalized order failed for {symbol}")
                        previous = open_time
                        writer.writerow((*row, symbol, pair.zip_object.key, zip_sha))

            normalized_sha = _deterministic_gzip_writer(normalized_path, write_symbol)
            total_normalized_rows += normalized_row_count
        derived_rows.append(
            {
                "symbol": symbol,
                "normalized_path": str(normalized_path) if normalized_path else None,
                "normalized_sha256": normalized_sha,
                "normalized_rows": normalized_row_count,
            }
        )

    panel_path = processed_root / "archive_kline_available_1h_2022-12_2024-12.csv.gz"

    def write_panel(text: io.TextIOBase) -> None:
        writer = csv.writer(text, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(("open_time_utc", *frozen.candidate_symbols))
        for index in range(panel_hours):
            timestamp_ms = panel_start_ms + index * INTERVAL_MILLISECONDS
            timestamp = datetime.fromtimestamp(
                timestamp_ms / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:00:00Z")
            writer.writerow((timestamp, *(chr(states[symbol][index]) for symbol in frozen.candidate_symbols)))

    panel_sha = _deterministic_gzip_writer(panel_path, write_panel)
    state_counts = {state: 0 for state in COVERAGE_STATES}
    for symbol_states in states.values():
        for state in COVERAGE_STATES:
            state_counts[state] += symbol_states.count(ord(state))
    panel_cells = panel_hours * len(frozen.candidate_symbols)
    if sum(state_counts.values()) != panel_cells:
        raise ContractError("coverage state counts do not equal panel dimensions")

    schema = {
        "semantics": (
            "Archive Kline evidence only; never historical TRADING, permission, "
            "listing, eligibility, or executability"
        ),
        "format": "deterministic gzip CSV",
        "encoding": "UTF-8 without BOM",
        "newline": "LF",
        "gzip_mtime": 0,
        "panel_path": str(panel_path),
        "panel_sha256": panel_sha,
        "panel_start_utc": datetime.fromtimestamp(
            panel_start_ms / 1000, tz=timezone.utc
        ).isoformat(),
        "panel_end_utc_exclusive": datetime.fromtimestamp(
            panel_end_ms / 1000, tz=timezone.utc
        ).isoformat(),
        "hours": panel_hours,
        "symbols": len(frozen.candidate_symbols),
        "cells": panel_cells,
        "symbol_order": "ascending Unicode code point",
        "symbol_list_sha256": frozen.symbol_list_sha256,
        "states": COVERAGE_STATES,
        "object_atomicity": (
            "Any hard failure makes the entire symbol-month U and emits no normalized rows"
        ),
        "close_time_policy": close_time_policy,
    }
    coverage_schema_sha = _write_json(coverage_schema_output, schema, pretty=True)
    derived_index_sha = _write_jsonl(derived_index_output, derived_rows)
    return {
        "panel_path": str(panel_path),
        "panel_sha256": panel_sha,
        "coverage_schema_sha256": coverage_schema_sha,
        "derived_index_sha256": derived_index_sha,
        "panel_hours": panel_hours,
        "panel_symbols": len(frozen.candidate_symbols),
        "panel_cells": panel_cells,
        "state_counts": state_counts,
        "normalized_rows": total_normalized_rows,
        "normalized_symbols": sum(row["normalized_path"] is not None for row in derived_rows),
    }


def run_payload_acquisition(
    *,
    inventory_path: Path,
    inventory_sha256: str,
    symbol_index_path: Path,
    symbol_index_sha256: str,
    summary_path: Path,
    summary_sha256: str,
    raw_root: Path,
    processed_root: Path,
    payload_summary_output: Path,
    object_quality_output: Path,
    coverage_schema_output: Path,
    derived_index_output: Path,
    source_base_url: str = DEFAULT_SOURCE_BASE_URL,
    max_workers: int = 16,
    timeout_seconds: int = 30,
    max_attempts: int = 5,
) -> tuple[dict[str, Any], int]:
    if max_workers < 1 or timeout_seconds < 1 or max_attempts < 1:
        raise ContractError("workers, timeout, and attempts must be positive")
    frozen = load_frozen_inputs(
        inventory_path=inventory_path,
        inventory_sha256=inventory_sha256,
        symbol_index_path=symbol_index_path,
        symbol_index_sha256=symbol_index_sha256,
        summary_path=summary_path,
        summary_sha256=summary_sha256,
    )
    contract = _run_contract(
        frozen=frozen,
        source_base_url=source_base_url,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        max_workers=max_workers,
    )
    _ensure_run_contract(raw_root, contract)
    started_at = _utc_now()
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                process_pair,
                pair,
                raw_root=raw_root,
                source_base_url=source_base_url,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
            ): pair
            for pair in frozen.pairs
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            records.append(future.result())
            if completed % 250 == 0 or completed == len(futures):
                print(f"payload pairs processed: {completed}/{len(futures)}", flush=True)
    records.sort(key=lambda row: (row["symbol"], row["month"]))
    derived = build_derived_outputs(
        frozen=frozen,
        quality_records=records,
        processed_root=processed_root,
        coverage_schema_output=coverage_schema_output,
        derived_index_output=derived_index_output,
    )
    quality_sha = _write_jsonl(object_quality_output, records)
    valid = sum(row["status"] == "VALID" for row in records)
    invalid = len(records) - valid
    failure_counts: dict[str, int] = defaultdict(int)
    for row in records:
        if row["failure_code"]:
            failure_counts[row["failure_code"]] += 1
    decision = "NEEDS_MORE_DATA" if invalid == 0 else "INCONCLUSIVE"
    payload_summary = {
        "started_at_utc": started_at,
        "ended_at_utc": _utc_now(),
        "decision": decision,
        "archive_semantics": (
            "ARCHIVE_KLINE_AVAILABLE only; not historical trading or eligibility"
        ),
        "pairs_expected": len(frozen.pairs),
        "pairs_valid": valid,
        "pairs_invalid_or_unverified": invalid,
        "failure_counts": dict(sorted(failure_counts.items())),
        "object_quality_sha256": quality_sha,
        "frozen_inputs": asdict(frozen) | {"pairs": f"{len(frozen.pairs)} frozen pairs"},
        "derived": derived,
    }
    payload_summary_sha = _write_json(payload_summary_output, payload_summary, pretty=True)
    payload_summary["payload_summary_sha256"] = payload_summary_sha
    return payload_summary, 0 if invalid == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Acquire and validate an exact frozen Binance Spot Kline inventory."
    )
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--symbol-index", required=True, type=Path)
    parser.add_argument("--symbol-index-sha256", required=True)
    parser.add_argument("--inventory-summary", required=True, type=Path)
    parser.add_argument("--inventory-summary-sha256", required=True)
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--processed-root", required=True, type=Path)
    parser.add_argument("--payload-summary-output", required=True, type=Path)
    parser.add_argument("--object-quality-output", required=True, type=Path)
    parser.add_argument("--coverage-schema-output", required=True, type=Path)
    parser.add_argument("--derived-index-output", required=True, type=Path)
    parser.add_argument("--source-base-url", default=DEFAULT_SOURCE_BASE_URL)
    parser.add_argument("--max-workers", required=True, type=int)
    parser.add_argument("--request-timeout-seconds", required=True, type=int)
    parser.add_argument("--max-attempts", required=True, type=int)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(arguments)
    try:
        summary, exit_code = run_payload_acquisition(
            inventory_path=options.inventory,
            inventory_sha256=options.inventory_sha256,
            symbol_index_path=options.symbol_index,
            symbol_index_sha256=options.symbol_index_sha256,
            summary_path=options.inventory_summary,
            summary_sha256=options.inventory_summary_sha256,
            raw_root=options.raw_root,
            processed_root=options.processed_root,
            payload_summary_output=options.payload_summary_output,
            object_quality_output=options.object_quality_output,
            coverage_schema_output=options.coverage_schema_output,
            derived_index_output=options.derived_index_output,
            source_base_url=options.source_base_url,
            max_workers=options.max_workers,
            timeout_seconds=options.request_timeout_seconds,
            max_attempts=options.max_attempts,
        )
    except PayloadError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
