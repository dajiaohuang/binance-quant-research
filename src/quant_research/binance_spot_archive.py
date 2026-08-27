"""Auditable Binance Spot archive inventory discovery.

This module inventories public S3 object metadata only.  It deliberately does
not download Kline ZIP or CHECKSUM payloads, parse candles, construct panels,
or infer historical exchange trading status from archive availability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BUCKET = "data.binance.vision"
DEFAULT_S3_BASE_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DEFAULT_EXCHANGE_INFO_URL = (
    "https://data-api.binance.vision/api/v3/exchangeInfo"
    "?permissions=SPOT&symbolStatus=TRADING"
)
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_MAX_ATTEMPTS = 3
ROOT_KLINE_PREFIX = "data/spot/monthly/klines/"
OBSERVED_SEMANTICS = (
    "archive_object_observed_at_inventory_fetch; not historical exchange trading status"
)
EXCHANGE_INFO_SEMANTICS = (
    "current exchangeInfo snapshot observed at fetch; never used as a historical universe filter"
)
MONTH_PATTERN = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])$")
QUOTE_SUFFIX_PATTERN = re.compile(r"^[A-Z0-9]+$")
SELECTED_HTTP_HEADERS = frozenset(
    {
        "content-length",
        "content-type",
        "date",
        "etag",
        "last-modified",
        "x-amz-id-2",
        "x-amz-request-id",
        "x-cache",
    }
)


class InventoryError(RuntimeError):
    """Raised when inventory evidence is incomplete, inconsistent, or invalid."""


@dataclass(frozen=True, order=True)
class YearMonth:
    """Calendar month used for inclusive archive range selection."""

    year: int
    month: int

    @classmethod
    def parse(cls, value: str) -> "YearMonth":
        match = MONTH_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError(f"invalid month {value!r}; expected YYYY-MM")
        return cls(int(match.group("year")), int(match.group("month")))

    def next(self) -> "YearMonth":
        if self.month == 12:
            return YearMonth(self.year + 1, 1)
        return YearMonth(self.year, self.month + 1)

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


@dataclass(frozen=True)
class HttpResponse:
    """Captured HTTP response with a caller-supplied observation timestamp."""

    status: int
    headers: Mapping[str, str]
    body: bytes
    fetched_at_utc: str
    attempts: int = 1


@dataclass(frozen=True)
class S3Object:
    """One object returned by ListObjectsV2."""

    key: str
    size: int
    etag: str | None
    last_modified: str | None
    observed_at_utc: str


@dataclass(frozen=True)
class S3Listing:
    """Complete, validated result of one paginated prefix listing."""

    common_prefixes: tuple[str, ...]
    objects: tuple[S3Object, ...]
    page_count: int


@dataclass(frozen=True)
class FilteredArchiveObjects:
    """Strictly matched archive records and explicitly rejected keys."""

    records: tuple[dict[str, Any], ...]
    rejected_keys: tuple[str, ...]
    out_of_range_keys: tuple[str, ...]


@dataclass(frozen=True)
class ExchangeInfoSnapshot:
    """Current exchangeInfo evidence, retained only for archive comparison."""

    sha256: str
    fetched_at_utc: str
    symbols: tuple[str, ...]
    quote_symbols: tuple[str, ...]


Fetcher = Callable[[str], HttpResponse]


def sha256_bytes(value: bytes) -> str:
    """Return a lower-case SHA-256 digest for exact bytes."""

    return hashlib.sha256(value).hexdigest()


def month_range(start: YearMonth, end: YearMonth) -> tuple[YearMonth, ...]:
    """Return every calendar month in an inclusive range."""

    if end < start:
        raise ValueError(f"end month {end} precedes start month {start}")
    values: list[YearMonth] = []
    current = start
    while current <= end:
        values.append(current)
        current = current.next()
    return tuple(values)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_fetch(
    url: str,
    *,
    timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> HttpResponse:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "binance-quant-research-inventory/1.0"},
        method="GET",
    )
    for attempt in range(1, max_attempts + 1):
        try:
            response = urllib.request.urlopen(request, timeout=timeout_seconds)
        except urllib.error.HTTPError as exc:
            return HttpResponse(
                status=exc.code,
                headers=dict(exc.headers.items()),
                body=exc.read(),
                fetched_at_utc=_utc_now(),
                attempts=attempt,
            )
        except (urllib.error.URLError, TimeoutError):
            if attempt == max_attempts:
                raise
            continue
        with response:
            return HttpResponse(
                status=response.status,
                headers=dict(response.headers.items()),
                body=response.read(),
                fetched_at_utc=_utc_now(),
                attempts=attempt,
            )
    raise AssertionError("unreachable fetch attempt state")


def _selected_headers(headers: Mapping[str, str]) -> dict[str, str]:
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    return {
        key: normalized[key]
        for key in sorted(normalized)
        if key in SELECTED_HTTP_HEADERS
    }


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _stable_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (text + "\n").encode("utf-8")


def _write_json(path: Path, value: Any, *, pretty: bool = True) -> None:
    _write_bytes(path, _stable_json_bytes(value, pretty=pretty))


def _xml_namespace(root: ET.Element) -> str:
    if root.tag.startswith("{"):
        return root.tag[1:].split("}", 1)[0]
    return ""


def _qualified(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}" if namespace else name


def _child_text(
    element: ET.Element,
    namespace: str,
    name: str,
    *,
    required: bool = False,
) -> str | None:
    child = element.find(_qualified(namespace, name))
    value = child.text if child is not None else None
    if value is not None:
        value = value.strip()
    if required and not value:
        raise InventoryError(f"ListObjectsV2 XML is missing required {name}")
    return value or None


class S3InventoryClient:
    """Minimal ListObjectsV2 client that preserves every raw response page."""

    def __init__(
        self,
        *,
        bucket: str = DEFAULT_BUCKET,
        base_url: str = DEFAULT_S3_BASE_URL,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.bucket = bucket
        self.base_url = base_url.rstrip("/")
        self.fetcher = fetcher or _default_fetch

    def list_objects(
        self,
        *,
        prefix: str,
        raw_directory: Path,
        delimiter: str | None = None,
    ) -> S3Listing:
        """Fetch and validate a complete ListObjectsV2 token chain."""

        raw_directory.mkdir(parents=True, exist_ok=True)
        continuation_token: str | None = None
        seen_tokens: set[str] = set()
        seen_prefixes: set[str] = set()
        seen_keys: set[str] = set()
        prefixes: list[str] = []
        objects: list[S3Object] = []
        page_number = 0

        while True:
            page_number += 1
            parameters: dict[str, str] = {
                "list-type": "2",
                "max-keys": "1000",
                "prefix": prefix,
            }
            if delimiter is not None:
                parameters["delimiter"] = delimiter
            if continuation_token is not None:
                if continuation_token in seen_tokens:
                    raise InventoryError(
                        f"duplicate continuation token requested for prefix {prefix!r}: "
                        f"{continuation_token!r}"
                    )
                seen_tokens.add(continuation_token)
                parameters["continuation-token"] = continuation_token

            url = f"{self.base_url}?{urllib.parse.urlencode(parameters)}"
            response = self.fetcher(url)
            page_stem = f"page_{page_number:04d}"
            raw_path = raw_directory / f"{page_stem}.xml"
            sidecar_path = raw_directory / f"{page_stem}.request.json"
            _write_bytes(raw_path, response.body)
            sidecar: dict[str, Any] = {
                "fetched_at_utc": response.fetched_at_utc,
                "request": {
                    "method": "GET",
                    "parameters": parameters,
                    "url": url,
                },
                "response": {
                    "attempts": response.attempts,
                    "body_sha256": sha256_bytes(response.body),
                    "headers": _selected_headers(response.headers),
                    "status": response.status,
                },
            }
            _write_json(sidecar_path, sidecar)
            if response.status != 200:
                raise InventoryError(
                    f"ListObjectsV2 returned HTTP {response.status} for prefix {prefix!r}; "
                    f"evidence saved to {raw_path}"
                )

            try:
                root = ET.fromstring(response.body)
            except ET.ParseError as exc:
                raise InventoryError(
                    f"ListObjectsV2 returned invalid XML for prefix {prefix!r}; "
                    f"evidence saved to {raw_path}: {exc}"
                ) from exc
            if root.tag.rsplit("}", 1)[-1] != "ListBucketResult":
                raise InventoryError(
                    f"unexpected ListObjectsV2 root element {root.tag!r} for prefix {prefix!r}"
                )

            namespace = _xml_namespace(root)
            response_prefix = _child_text(root, namespace, "Prefix") or ""
            if response_prefix != prefix:
                raise InventoryError(
                    f"ListObjectsV2 prefix mismatch: requested {prefix!r}, "
                    f"received {response_prefix!r}"
                )
            response_token = _child_text(root, namespace, "ContinuationToken")
            if continuation_token is None:
                if response_token is not None:
                    raise InventoryError(
                        f"unexpected ContinuationToken on first page for prefix {prefix!r}"
                    )
            elif response_token != continuation_token:
                raise InventoryError(
                    f"continuation token chain mismatch for prefix {prefix!r}: "
                    f"requested {continuation_token!r}, received {response_token!r}"
                )

            for common in root.findall(_qualified(namespace, "CommonPrefixes")):
                common_prefix = _child_text(
                    common, namespace, "Prefix", required=True
                )
                assert common_prefix is not None
                if common_prefix in seen_prefixes:
                    raise InventoryError(
                        f"duplicate CommonPrefix {common_prefix!r} for prefix {prefix!r}"
                    )
                seen_prefixes.add(common_prefix)
                prefixes.append(common_prefix)

            for content in root.findall(_qualified(namespace, "Contents")):
                key = _child_text(content, namespace, "Key", required=True)
                size_text = _child_text(content, namespace, "Size", required=True)
                assert key is not None and size_text is not None
                if key in seen_keys:
                    raise InventoryError(f"duplicate object key {key!r} for prefix {prefix!r}")
                try:
                    size = int(size_text)
                except ValueError as exc:
                    raise InventoryError(f"invalid object size {size_text!r} for {key!r}") from exc
                if size < 0:
                    raise InventoryError(f"negative object size {size} for {key!r}")
                seen_keys.add(key)
                etag = _child_text(content, namespace, "ETag")
                if etag is not None and len(etag) >= 2 and etag[0] == etag[-1] == '"':
                    etag = etag[1:-1]
                objects.append(
                    S3Object(
                        key=key,
                        size=size,
                        etag=etag,
                        last_modified=_child_text(content, namespace, "LastModified"),
                        observed_at_utc=response.fetched_at_utc,
                    )
                )

            key_count_text = _child_text(root, namespace, "KeyCount", required=True)
            assert key_count_text is not None
            try:
                key_count = int(key_count_text)
            except ValueError as exc:
                raise InventoryError(
                    f"invalid KeyCount value {key_count_text!r} for prefix {prefix!r}"
                ) from exc
            page_item_count = len(
                root.findall(_qualified(namespace, "CommonPrefixes"))
            ) + len(root.findall(_qualified(namespace, "Contents")))
            if key_count != page_item_count:
                raise InventoryError(
                    f"KeyCount mismatch for prefix {prefix!r}: XML says {key_count}, "
                    f"parsed {page_item_count}"
                )

            truncated_text = _child_text(
                root, namespace, "IsTruncated", required=True
            )
            if truncated_text not in {"true", "false"}:
                raise InventoryError(
                    f"invalid IsTruncated value {truncated_text!r} for prefix {prefix!r}"
                )
            next_token = _child_text(root, namespace, "NextContinuationToken")
            sidecar["pagination"] = {
                "continuation_token": response_token,
                "is_truncated": truncated_text == "true",
                "key_count": key_count,
                "next_continuation_token": next_token,
            }
            _write_json(sidecar_path, sidecar)
            if truncated_text == "false":
                if next_token is not None:
                    raise InventoryError(
                        f"terminal page unexpectedly contains NextContinuationToken for "
                        f"prefix {prefix!r}"
                    )
                break
            if next_token is None:
                raise InventoryError(
                    f"truncated page lacks NextContinuationToken for prefix {prefix!r}"
                )
            if next_token == continuation_token or next_token in seen_tokens:
                raise InventoryError(
                    f"duplicate continuation token {next_token!r} for prefix {prefix!r}"
                )
            continuation_token = next_token

        return S3Listing(
            common_prefixes=tuple(sorted(prefixes)),
            objects=tuple(sorted(objects, key=lambda value: value.key)),
            page_count=page_number,
        )


def discover_archive_symbols(
    common_prefixes: Iterable[str],
    *,
    root_prefix: str = ROOT_KLINE_PREFIX,
) -> tuple[str, ...]:
    """Extract exact Binance symbol path segments from root CommonPrefixes."""

    symbols: list[str] = []
    seen: set[str] = set()
    for prefix in common_prefixes:
        if not prefix.startswith(root_prefix) or not prefix.endswith("/"):
            raise InventoryError(f"invalid symbol CommonPrefix {prefix!r}")
        symbol = prefix[len(root_prefix) : -1]
        if not symbol or "/" in symbol:
            raise InventoryError(f"invalid archive symbol path segment {symbol!r}")
        if symbol in seen:
            raise InventoryError(f"duplicate archive symbol {symbol!r}")
        seen.add(symbol)
        symbols.append(symbol)
    return tuple(sorted(symbols))


def symbol_evidence_directory_name(symbol: str) -> str:
    """Map an arbitrary S3 symbol segment to a Windows-safe evidence directory."""

    return f"sha256_{sha256_bytes(symbol.encode('utf-8'))}"


def filter_archive_objects(
    objects: Iterable[S3Object],
    *,
    bucket: str,
    symbol: str,
    interval: str,
    start_month: YearMonth,
    end_month: YearMonth,
) -> FilteredArchiveObjects:
    """Strictly select monthly ZIP/CHECKSUM object metadata in the frozen range."""

    escaped_symbol = re.escape(symbol)
    escaped_interval = re.escape(interval)
    pattern = re.compile(
        rf"^{re.escape(ROOT_KLINE_PREFIX)}{escaped_symbol}/{escaped_interval}/"
        rf"{escaped_symbol}-{escaped_interval}-"
        rf"(?P<month>[0-9]{{4}}-(?:0[1-9]|1[0-2]))"
        rf"\.zip(?P<checksum>\.CHECKSUM)?$"
    )
    records: list[dict[str, Any]] = []
    rejected: list[str] = []
    out_of_range: list[str] = []
    seen_identity: set[tuple[str, str]] = set()

    for item in sorted(objects, key=lambda value: value.key):
        match = pattern.fullmatch(item.key)
        if match is None:
            rejected.append(item.key)
            continue
        archive_month = YearMonth.parse(match.group("month"))
        if archive_month < start_month or archive_month > end_month:
            out_of_range.append(item.key)
            continue
        object_type = "checksum" if match.group("checksum") else "zip"
        identity = (str(archive_month), object_type)
        if identity in seen_identity:
            raise InventoryError(
                f"duplicate {object_type} object for {symbol} {archive_month}"
            )
        seen_identity.add(identity)
        records.append(
            {
                "bucket": bucket,
                "etag": item.etag,
                "interval": interval,
                "key": item.key,
                "last_modified": item.last_modified,
                "month": str(archive_month),
                "object_type": object_type,
                "observed_at_utc": item.observed_at_utc,
                "observed_semantics": OBSERVED_SEMANTICS,
                "size": item.size,
                "symbol": symbol,
            }
        )

    return FilteredArchiveObjects(
        records=tuple(sorted(records, key=lambda value: value["key"])),
        rejected_keys=tuple(sorted(rejected)),
        out_of_range_keys=tuple(sorted(out_of_range)),
    )


def fetch_exchange_info_snapshot(
    *,
    raw_root: Path,
    quote_suffix: str,
    url: str = DEFAULT_EXCHANGE_INFO_URL,
    fetcher: Fetcher | None = None,
) -> ExchangeInfoSnapshot:
    """Preserve current exchangeInfo bytes without using them as a history filter."""

    fetch = fetcher or _default_fetch
    response = fetch(url)
    raw_path = raw_root / "exchange_info" / "exchange_info.json"
    sidecar_path = raw_root / "exchange_info" / "exchange_info.request.json"
    _write_bytes(raw_path, response.body)
    body_hash = sha256_bytes(response.body)
    _write_json(
        sidecar_path,
        {
            "fetched_at_utc": response.fetched_at_utc,
            "observed_semantics": EXCHANGE_INFO_SEMANTICS,
            "request": {"method": "GET", "url": url},
            "response": {
                "attempts": response.attempts,
                "body_sha256": body_hash,
                "headers": _selected_headers(response.headers),
                "status": response.status,
            },
        },
    )
    if response.status != 200:
        raise InventoryError(
            f"exchangeInfo returned HTTP {response.status}; evidence saved to {raw_path}"
        )
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryError(
            f"exchangeInfo returned invalid JSON; evidence saved to {raw_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise InventoryError("exchangeInfo JSON must contain a symbols array")

    symbols: set[str] = set()
    quote_symbols: set[str] = set()
    for index, item in enumerate(payload["symbols"]):
        if not isinstance(item, dict) or not isinstance(item.get("symbol"), str):
            raise InventoryError(f"exchangeInfo symbols[{index}] has no string symbol")
        symbol = item["symbol"]
        if symbol in symbols:
            raise InventoryError(f"exchangeInfo contains duplicate symbol {symbol!r}")
        symbols.add(symbol)
        if item.get("quoteAsset") == quote_suffix:
            quote_symbols.add(symbol)

    return ExchangeInfoSnapshot(
        sha256=body_hash,
        fetched_at_utc=response.fetched_at_utc,
        symbols=tuple(sorted(symbols)),
        quote_symbols=tuple(sorted(quote_symbols)),
    )


def serialize_inventory(records: Iterable[Mapping[str, Any]]) -> bytes:
    """Serialize inventory rows in deterministic key order and record order."""

    ordered = sorted(records, key=lambda value: str(value["key"]))
    return b"".join(_stable_json_bytes(dict(record)) for record in ordered)


def serialize_symbol_index(
    symbols: Iterable[str], *, quote_suffix: str
) -> bytes:
    """Serialize exact archive symbol strings without Unicode normalization."""

    rows = []
    for symbol in sorted(symbols):
        encoded = symbol.encode("utf-8")
        rows.append(
            {
                "archive_observed": True,
                "observed_semantics": OBSERVED_SEMANTICS,
                "raw_evidence_directory": symbol_evidence_directory_name(symbol),
                "suffix_candidate": symbol.endswith(quote_suffix),
                "symbol": symbol,
                "symbol_sha256": sha256_bytes(encoded),
                "symbol_utf8_hex": encoded.hex(),
            }
        )
    return b"".join(_stable_json_bytes(row) for row in rows)


def build_inventory_summary(
    *,
    records: Iterable[Mapping[str, Any]],
    all_archive_symbols: Sequence[str],
    candidate_symbols: Sequence[str],
    rejected_keys: Sequence[str],
    out_of_range_keys: Sequence[str],
    inventory_bytes: bytes,
    symbol_index_bytes: bytes,
    exchange_info: ExchangeInfoSnapshot,
    bucket: str,
    interval: str,
    quote_suffix: str,
    start_month: YearMonth,
    end_month: YearMonth,
) -> dict[str, Any]:
    """Pair ZIP/CHECKSUM objects and disclose coverage gaps without dropping symbols."""

    expected_months = tuple(str(value) for value in month_range(start_month, end_month))
    by_symbol: dict[str, list[Mapping[str, Any]]] = {
        symbol: [] for symbol in sorted(candidate_symbols)
    }
    materialized_records = sorted(records, key=lambda value: str(value["key"]))
    for record in materialized_records:
        symbol = str(record["symbol"])
        if symbol not in by_symbol:
            raise InventoryError(f"record references non-candidate symbol {symbol!r}")
        by_symbol[symbol].append(record)

    symbol_summaries: list[dict[str, Any]] = []
    total_zip_bytes = 0
    total_checksum_bytes = 0
    total_zip_count = 0
    total_checksum_count = 0
    total_missing_checksums = 0

    for symbol in sorted(by_symbol):
        zip_records: dict[str, Mapping[str, Any]] = {}
        checksum_records: dict[str, Mapping[str, Any]] = {}
        for record in by_symbol[symbol]:
            month = str(record["month"])
            target = zip_records if record["object_type"] == "zip" else checksum_records
            if month in target:
                raise InventoryError(
                    f"summary received duplicate {record['object_type']} for {symbol} {month}"
                )
            target[month] = record

        observed_zip_months = sorted(zip_records)
        missing_checksums = sorted(set(zip_records) - set(checksum_records))
        orphan_checksums = sorted(set(checksum_records) - set(zip_records))
        missing_months = [month for month in expected_months if month not in zip_records]
        leading: list[str] = []
        internal: list[str] = []
        trailing: list[str] = []
        all_missing: list[str] = []
        if observed_zip_months:
            first_month = observed_zip_months[0]
            last_month = observed_zip_months[-1]
            leading = [month for month in missing_months if month < first_month]
            internal = [
                month for month in missing_months if first_month < month < last_month
            ]
            trailing = [month for month in missing_months if month > last_month]
        else:
            first_month = None
            last_month = None
            all_missing = list(expected_months)

        zip_bytes = sum(int(record["size"]) for record in zip_records.values())
        checksum_bytes = sum(
            int(record["size"]) for record in checksum_records.values()
        )
        total_zip_bytes += zip_bytes
        total_checksum_bytes += checksum_bytes
        total_zip_count += len(zip_records)
        total_checksum_count += len(checksum_records)
        total_missing_checksums += len(missing_checksums)
        symbol_summaries.append(
            {
                "all_missing_months": all_missing,
                "checksum_count": len(checksum_records),
                "checksum_total_bytes": checksum_bytes,
                "first_zip_month": first_month,
                "internal_missing_months": internal,
                "last_zip_month": last_month,
                "leading_missing_months": leading,
                "missing_checksum_months": missing_checksums,
                "observed_semantics": OBSERVED_SEMANTICS,
                "orphan_checksum_months": orphan_checksums,
                "raw_evidence_directory": symbol_evidence_directory_name(symbol),
                "symbol": symbol,
                "trailing_missing_months": trailing,
                "zip_count": len(zip_records),
                "zip_total_bytes": zip_bytes,
            }
        )

    archive_candidates = set(candidate_symbols)
    current_quote_symbols = set(exchange_info.quote_symbols)
    return {
        "archive": {
            "all_symbol_count": len(all_archive_symbols),
            "candidate_symbol_count": len(candidate_symbols),
            "interval": interval,
            "quote_suffix": quote_suffix,
            "root_prefix": ROOT_KLINE_PREFIX,
            "symbol_index_record_count": len(all_archive_symbols),
            "symbol_index_sha256": sha256_bytes(symbol_index_bytes),
        },
        "bucket": bucket,
        "exchange_info_snapshot": {
            "archive_candidates_not_current": sorted(
                archive_candidates - current_quote_symbols
            ),
            "current_quote_symbols_not_archive": sorted(
                current_quote_symbols - archive_candidates
            ),
            "current_symbol_count": len(exchange_info.symbols),
            "current_quote_symbol_count": len(exchange_info.quote_symbols),
            "fetched_at_utc": exchange_info.fetched_at_utc,
            "observed_semantics": EXCHANGE_INFO_SEMANTICS,
            "sha256": exchange_info.sha256,
        },
        "inventory": {
            "checksum_count": total_checksum_count,
            "checksum_total_bytes": total_checksum_bytes,
            "end_month": str(end_month),
            "inventory_jsonl_sha256": sha256_bytes(inventory_bytes),
            "inventory_record_count": len(materialized_records),
            "missing_checksum_count": total_missing_checksums,
            "out_of_range_key_count": len(out_of_range_keys),
            "out_of_range_keys": sorted(out_of_range_keys),
            "rejected_key_count": len(rejected_keys),
            "rejected_keys": sorted(rejected_keys),
            "start_month": str(start_month),
            "zip_count": total_zip_count,
            "zip_total_bytes": total_zip_bytes,
        },
        "symbols": symbol_summaries,
    }


def run_inventory(
    *,
    raw_root: Path,
    inventory_output: Path,
    summary_output: Path,
    interval: str,
    quote_suffix: str,
    start_month: YearMonth,
    end_month: YearMonth,
    max_workers: int,
    bucket: str = DEFAULT_BUCKET,
    s3_base_url: str = DEFAULT_S3_BASE_URL,
    exchange_info_url: str = DEFAULT_EXCHANGE_INFO_URL,
    request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    fetcher: Fetcher | None = None,
) -> dict[str, Any]:
    """Run the metadata-only inventory workflow and write deterministic outputs."""

    if interval != "1h":
        raise ValueError("this frozen inventory implementation only permits interval 1h")
    if not quote_suffix or QUOTE_SUFFIX_PATTERN.fullmatch(quote_suffix) is None:
        raise ValueError("quote suffix must be a non-empty uppercase alphanumeric value")
    if max_workers <= 0:
        raise ValueError("max-workers must be positive")
    if request_timeout_seconds <= 0:
        raise ValueError("request-timeout-seconds must be positive")
    if max_attempts <= 0:
        raise ValueError("max-attempts must be positive")
    month_range(start_month, end_month)

    for output in (raw_root, inventory_output, summary_output):
        if output.exists():
            raise InventoryError(f"refusing to overwrite existing inventory evidence: {output}")

    effective_fetcher = fetcher
    if effective_fetcher is None:
        effective_fetcher = lambda url: _default_fetch(
            url,
            timeout_seconds=request_timeout_seconds,
            max_attempts=max_attempts,
        )

    client = S3InventoryClient(
        bucket=bucket,
        base_url=s3_base_url,
        fetcher=effective_fetcher,
    )
    root_listing = client.list_objects(
        prefix=ROOT_KLINE_PREFIX,
        delimiter="/",
        raw_directory=raw_root / "archive_root",
    )
    all_symbols = discover_archive_symbols(root_listing.common_prefixes)
    candidate_symbols = tuple(
        symbol for symbol in all_symbols if symbol.endswith(quote_suffix)
    )
    symbol_index_bytes = serialize_symbol_index(
        all_symbols, quote_suffix=quote_suffix
    )
    _write_bytes(raw_root / "symbol_index.jsonl", symbol_index_bytes)
    exchange_info = fetch_exchange_info_snapshot(
        raw_root=raw_root,
        quote_suffix=quote_suffix,
        url=exchange_info_url,
        fetcher=effective_fetcher,
    )

    def inventory_symbol(symbol: str) -> FilteredArchiveObjects:
        prefix = f"{ROOT_KLINE_PREFIX}{symbol}/{interval}/"
        listing = client.list_objects(
            prefix=prefix,
            raw_directory=(
                raw_root / "symbols" / symbol_evidence_directory_name(symbol)
            ),
        )
        if listing.common_prefixes:
            raise InventoryError(
                f"unexpected nested CommonPrefixes under interval prefix {prefix!r}"
            )
        return filter_archive_objects(
            listing.objects,
            bucket=bucket,
            symbol=symbol,
            interval=interval,
            start_month=start_month,
            end_month=end_month,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        per_symbol = tuple(executor.map(inventory_symbol, candidate_symbols))

    records: list[dict[str, Any]] = []
    rejected_keys: list[str] = []
    out_of_range_keys: list[str] = []
    seen_keys: set[str] = set()
    for filtered in per_symbol:
        for record in filtered.records:
            key = str(record["key"])
            if key in seen_keys:
                raise InventoryError(f"duplicate inventory key across symbols: {key!r}")
            seen_keys.add(key)
            records.append(record)
        rejected_keys.extend(filtered.rejected_keys)
        out_of_range_keys.extend(filtered.out_of_range_keys)

    inventory_bytes = serialize_inventory(records)
    _write_bytes(inventory_output, inventory_bytes)
    summary = build_inventory_summary(
        records=records,
        all_archive_symbols=all_symbols,
        candidate_symbols=candidate_symbols,
        rejected_keys=rejected_keys,
        out_of_range_keys=out_of_range_keys,
        inventory_bytes=inventory_bytes,
        symbol_index_bytes=symbol_index_bytes,
        exchange_info=exchange_info,
        bucket=bucket,
        interval=interval,
        quote_suffix=quote_suffix,
        start_month=start_month,
        end_month=end_month,
    )
    _write_json(summary_output, summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory Binance Spot monthly archive metadata without downloading "
            "Kline ZIP or CHECKSUM payloads."
        )
    )
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--inventory-output", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--interval", required=True, choices=("1h",))
    parser.add_argument("--quote-suffix", required=True)
    parser.add_argument("--start-month", required=True, type=YearMonth.parse)
    parser.add_argument("--end-month", required=True, type=YearMonth.parse)
    parser.add_argument("--max-workers", required=True, type=int)
    parser.add_argument(
        "--request-timeout-seconds",
        default=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        type=int,
    )
    parser.add_argument("--max-attempts", default=DEFAULT_MAX_ATTEMPTS, type=int)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--s3-base-url", default=DEFAULT_S3_BASE_URL)
    parser.add_argument("--exchange-info-url", default=DEFAULT_EXCHANGE_INFO_URL)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    try:
        summary = run_inventory(
            raw_root=args.raw_root,
            inventory_output=args.inventory_output,
            summary_output=args.summary_output,
            interval=args.interval,
            quote_suffix=args.quote_suffix,
            start_month=args.start_month,
            end_month=args.end_month,
            max_workers=args.max_workers,
            bucket=args.bucket,
            s3_base_url=args.s3_base_url,
            exchange_info_url=args.exchange_info_url,
            request_timeout_seconds=args.request_timeout_seconds,
            max_attempts=args.max_attempts,
        )
    except (InventoryError, OSError, ValueError) as exc:
        raise SystemExit(f"inventory failed: {exc}") from exc
    print(json.dumps(summary["inventory"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
