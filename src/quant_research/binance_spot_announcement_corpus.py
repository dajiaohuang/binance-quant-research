"""Freeze a Binance CMS announcement corpus without deriving eligibility.

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
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import quant_research.binance_spot_pit as pit_transport
from quant_research.binance_spot_pit import (
    RETRYABLE_HTTP_STATUSES,
    TIME_URL,
    TransportResponse,
    _epoch_ms,
    _parse_utc,
    _safe_transport_error,
    _selected_headers,
    _sha256_bytes,
    _sha256_file,
    _stable_json_bytes,
    _write_once,
)


EXTRACTOR_VERSION = "binance_spot_announcement_v1"
FROZEN_PIT_TRANSPORT_SHA256 = (
    "427b9eab83f14798fdb9b6465dddad397081d6a2c094fb27aa229fd94aee2264"
)
LIST_BASE = "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query"
DETAIL_BASE = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
CATALOG_IDS = (48, 161)
PAGE_SIZE = 50
CODE_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
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
    "User-Agent": "quant-binance-announcement-corpus/1",
}


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
class LoadedCorpus:
    run_id: str
    terminal_status: str
    artifact_state: str | None
    inventory: tuple[dict[str, Any], ...]
    details: tuple[dict[str, Any], ...]
    summary_sha256: str


Fetcher = Callable[[urllib.request.Request, float], TransportResponse]
Sleeper = Callable[[float], None]
Preflight = Callable[[], None]


class _CorpusNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _bounded_default_fetcher(
    request: urllib.request.Request, timeout: float, max_response_bytes: int
) -> TransportResponse:
    started = pit_transport._utc_now()
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
            completed = pit_transport._utc_now()
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
        raise CorpusContractError("articleCode must be 32 lower-case hex")
    return f"{DETAIL_BASE}?articleCode={code}"


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
    if _parse_utc(response.response_completed_at_utc, "response completion") < _parse_utc(
        response.request_started_at_utc, "request start"
    ):
        raise CorpusContractError("response completion precedes request start")


def _response_outcome(
    *,
    request_headers_valid: bool,
    response: TransportResponse,
    canonical_url: str,
    max_response_bytes: int,
    cumulative_response_bytes: int,
    max_total_response_bytes: int,
) -> str:
    selected = _selected_headers(response.headers)
    content_length = selected.get("Content-Length")
    if not request_headers_valid:
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
        "outcome": outcome,
    }


def _acquire(
    *,
    raw_run: Path,
    sequence: int,
    logical_key: str,
    kind: str,
    url: str,
    fetcher: Fetcher,
    timeout_seconds: float,
    max_attempts: int,
    max_wire_attempts: int,
    wire_attempt_counter: list[int],
    max_response_bytes: int,
    total_counter: list[int],
    max_total_response_bytes: int,
    sleeper: Sleeper,
    pacing_seconds: float,
    preflight: Preflight,
) -> tuple[TransportResponse, dict[str, Any]]:
    request, parameters = _request(url, kind)
    attempt_rows: list[dict[str, Any]] = []
    directory = raw_run / "requests" / logical_key
    for attempt in range(1, max_attempts + 1):
        preflight()
        if wire_attempt_counter[0] >= max_wire_attempts:
            raise CorpusContractError("global wire-attempt cap exhausted")
        wire_attempt_counter[0] += 1
        try:
            response = fetcher(request, timeout_seconds)
        except Exception as exc:
            preflight()
            category, safe_message = _safe_transport_error(exc)
            sidecar = directory / f"attempt_{attempt:04d}.request.json"
            _write_once(
                sidecar,
                _stable_json_bytes(
                    {
                        "request": {
                            "method": "GET",
                            "canonical_url": url,
                            "canonical_parameters": parameters,
                            "headers": dict(sorted(request.header_items())),
                            "authentication": "NONE",
                        },
                        "response": None,
                        "outcome": "TRANSPORT_ERROR",
                        "error_category": category,
                        "safe_error_message": safe_message,
                    },
                    pretty=True,
                ),
            )
            raise CorpusHttpError(f"{logical_key} transport failure") from exc
        preflight()
        if type(response) is not TransportResponse:
            raise CorpusHttpError("fetcher returned invalid response")
        _response_times(response)
        try:
            _validate_request_evidence_headers(
                dict(request.header_items()), kind, url
            )
        except CorpusContractError:
            request_headers_valid = False
        else:
            request_headers_valid = True
        total_counter[0] += len(response.body)
        outcome = _response_outcome(
            request_headers_valid=request_headers_valid,
            response=response,
            canonical_url=url,
            max_response_bytes=max_response_bytes,
            cumulative_response_bytes=total_counter[0],
            max_total_response_bytes=max_total_response_bytes,
        )
        body_path = directory / f"attempt_{attempt:04d}.response"
        sidecar_path = directory / f"attempt_{attempt:04d}.request.json"
        body_sha = _write_once(body_path, response.body)
        sidecar_sha = _write_once(
            sidecar_path,
            _stable_json_bytes(
                _attempt_sidecar(request, parameters, response, outcome), pretty=True
            ),
        )
        attempt_rows.append(
            {
                "attempt": attempt,
                "body": body_path.as_posix(),
                "body_sha256": body_sha,
                "sidecar": sidecar_path.as_posix(),
                "sidecar_sha256": sidecar_sha,
                "outcome": outcome,
            }
        )
        if outcome == "OK":
            if pacing_seconds:
                sleeper(pacing_seconds)
            return response, {
                "sequence": sequence,
                "logical_key": logical_key,
                "kind": kind,
                "canonical_url": url,
                "canonical_parameters": parameters,
                "attempts": attempt_rows,
            }
        retryable = (
            response.status in RETRYABLE_HTTP_STATUSES
            and outcome == f"HTTP_{response.status}"
        )
        if retryable and attempt < max_attempts:
            sleeper(max(pacing_seconds, min(2 ** (attempt - 1), 8)))
            continue
        raise CorpusHttpError(f"{logical_key} failed closed: {outcome}")
    raise CorpusHttpError(f"{logical_key} exhausted attempts")


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


def _article_row(raw: object, catalog_id: int, page_no: int, index: int, raw_sha: str, completed: str) -> dict[str, Any]:
    if type(raw) is not dict:
        raise CorpusSchemaError("article must be an object")
    article_id = raw.get("id")
    code = raw.get("code")
    title = raw.get("title")
    release = _positive_ms(raw.get("releaseDate"), "releaseDate")
    if isinstance(article_id, bool) or not isinstance(article_id, (int, str)) or not str(article_id):
        raise CorpusSchemaError("invalid article id")
    if type(code) is not str or CODE_PATTERN.fullmatch(code) is None:
        raise CorpusSchemaError("invalid article code")
    if type(title) is not str or not title:
        raise CorpusSchemaError("invalid article title")
    return {
        "catalog_id": catalog_id,
        "article_id": str(article_id),
        "article_code": code,
        "title": title,
        "claimed_published_at_ms": release,
        "claimed_published_at_source_field": "releaseDate",
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
    if str(article_id) != expected["article_id"] or code != expected["article_code"]:
        raise CorpusSchemaError("detail id/code mismatch")
    if type(catalog_id) is not int or catalog_id != expected["catalog_id"]:
        raise CorpusSchemaError("detail catalog mismatch")
    if publish != expected["claimed_published_at_ms"]:
        raise CorpusSchemaError("detail publishDate mismatch")
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
        "detail_publish_date_ms": publish,
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
            "article_id": row["article_id"],
            "article_code": row["article_code"],
            "title": row["title"],
            "claimed_published_at_ms": row["claimed_published_at_ms"],
        }
        for row in rows
    ]
    return _sha256_bytes(_stable_json_bytes({"total": total, "rows": source_values}))


def _semantic_inventory(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "catalog_id": row["catalog_id"],
            "article_id": row["article_id"],
            "article_code": row["article_code"],
            "title": row["title"],
            "claimed_published_at_ms": row["claimed_published_at_ms"],
        }
        for row in rows
    ]


def _source_contract() -> dict[str, Any]:
    return {
        "version": EXTRACTOR_VERSION,
        "corpus_completeness_scope": "all articles visible through the frozen CMS catalogs during both complete acquisition passes; excludes deleted articles and historical versions not returned now",
        "catalog_ids": list(CATALOG_IDS),
        "list_url_template": LIST_BASE + "?type=1&catalogId={48|161}&pageNo=N&pageSize=50",
        "detail_url_template": DETAIL_BASE + "?articleCode={32_lower_hex}",
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
            "releaseDate": "claimed_published_at only",
            "publishDate": "claimed_published_at only",
            "detail_version_known_at": "detail response completion",
            "lastUpdateTime": "raw untrusted metadata; not version history",
        },
        "production_response_shape": {
            "list": "$.data.catalogs must contain exactly one object whose integer catalogId equals the request; total/articles come from $.data.catalogs[0]",
            "detail_body": "$.data.body; processed output retains SHA-256 and UTF-8 byte length only",
            "detail_catalog": "$.data.firstCatalogId must equal the selected list catalog",
            "detail_content_json": "optional JSON value; processed output retains presence plus canonical-JSON SHA-256 and byte length only",
        },
        "stability_rule": "two complete ordered traversals of every list page; totals, page shapes, per-page semantic hashes and full semantic inventory must match; pass 2 is not merged",
        "source_binding_rule": "required expected extractor SHA-256 and frozen PIT dependency SHA-256 are verified before lease creation, immediately before every wire attempt, immediately after every returned response before evidence acceptance, and before artifact completion",
        "wire_attempt_rule": "one shared positive global cap covers every fetch call across all logical requests and retries; the counter increments immediately before fetch and no attempt above the cap is issued",
        "wire_attempt_summary_fields": {
            "max_wire_attempts": "positive acquisition-wide runtime bound",
            "wire_attempt_count": "all fetch calls, including retry and transport-error attempts",
        },
        "forbidden_derivations": ["pair", "event", "effective time", "listing interval", "eligibility"],
    }


def _schema() -> dict[str, Any]:
    return {
        "version": EXTRACTOR_VERSION,
        "processed_artifacts": ["inventory.jsonl", "detail_index.jsonl", "corpus_summary.json", "schema.json", "source_contract.json"],
        "forbidden_artifacts": ["events.jsonl", "listing_intervals.jsonl", "alpha.json"],
        "inventory_key": ["catalog_id", "article_id", "article_code"],
        "detail_known_at_rule": "response completion; never claimed publishDate",
        "list_response_locator": "$.data.catalogs[0]",
        "detail_body_fields": ["body", "contentJson"],
        "stability_gate": "two complete list passes; pass 1 alone is processed inventory",
        "summary_wire_fields": ["wire_attempt_count", "max_wire_attempts"],
    }


def _module_sha() -> str:
    return _sha256_file(Path(__file__))


def _dependency_sha() -> str:
    return _sha256_file(Path(pit_transport.__file__))


def _assert_source_binding(expected_extractor_sha256: str) -> None:
    if _module_sha() != expected_extractor_sha256:
        raise CorpusIntegrityError("extractor source SHA-256 mismatch")
    if _dependency_sha() != FROZEN_PIT_TRANSPORT_SHA256:
        raise CorpusIntegrityError("frozen PIT transport dependency changed")


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
    claimed_release_start_ms: int,
    claimed_release_end_ms_exclusive: int,
    expected_totals: Mapping[int, int],
    expected_interval_counts: Mapping[int, int],
    max_pages_per_catalog: int,
    max_articles: int,
    max_response_bytes: int,
    max_total_response_bytes: int,
    timeout_seconds: float,
    max_attempts: int,
    max_wire_attempts: int,
    pacing_seconds: float,
    max_clock_skew_ms: int,
    fetcher: Fetcher | None = None,
    sleeper: Sleeper = time.sleep,
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
        type(claimed_release_start_ms) is not int
        or type(claimed_release_end_ms_exclusive) is not int
        or claimed_release_start_ms >= claimed_release_end_ms_exclusive
    ):
        raise CorpusContractError("invalid claimed-release interval")
    if (
        set(expected_totals) != set(CATALOG_IDS)
        or set(expected_interval_counts) != set(CATALOG_IDS)
        or any(type(value) is not int or value < 0 for value in expected_totals.values())
        or any(
            type(value) is not int or value < 0
            for value in expected_interval_counts.values()
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
        or isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
        or type(max_attempts) is not int
        or max_attempts < 1
        or type(max_wire_attempts) is not int
        or max_wire_attempts < 1
        or isinstance(pacing_seconds, bool)
        or not isinstance(pacing_seconds, (int, float))
        or pacing_seconds < 0
        or type(max_clock_skew_ms) is not int
        or max_clock_skew_ms < 0
    ):
        raise CorpusContractError("invalid acquisition bounds")
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

    counter = [0]
    wire_attempt_counter = [0]
    ledger: list[dict[str, Any]] = []
    sequence = 0

    def verify_source_binding() -> None:
        _assert_source_binding(expected_extractor_sha256)

    def acquire(logical_key: str, kind: str, url: str) -> TransportResponse:
        nonlocal sequence
        sequence += 1
        response, row = _acquire(
            raw_run=raw_run,
            sequence=sequence,
            logical_key=logical_key,
            kind=kind,
            url=url,
            fetcher=active_fetcher,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            max_wire_attempts=max_wire_attempts,
            wire_attempt_counter=wire_attempt_counter,
            max_response_bytes=max_response_bytes,
            total_counter=counter,
            max_total_response_bytes=max_total_response_bytes,
            sleeper=sleeper,
            pacing_seconds=pacing_seconds,
            preflight=verify_source_binding,
        )
        ledger.append(row)
        return response

    time_before = acquire("time_before", "time", TIME_URL)
    before_ms = _time_ms(time_before.body)
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
            first = acquire(
                f"catalog_{catalog_id}/pass_{pass_number}/page_0001",
                "list",
                _list_url(catalog_id, 1, page_size),
            )
            total, rows = _parse_list(
                first.body,
                catalog_id,
                1,
                _sha256_bytes(first.body),
                first.response_completed_at_utc,
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
                current = acquire(
                    f"catalog_{catalog_id}/pass_{pass_number}/page_{page:04d}",
                    "list",
                    _list_url(catalog_id, page, page_size),
                )
                current_total, current_rows = _parse_list(
                    current.body,
                    catalog_id,
                    page,
                    _sha256_bytes(current.body),
                    current.response_completed_at_utc,
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
                len({row["article_id"] for row in subset}) != len(subset)
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
    inventory = sorted(inventory_pass_1, key=lambda row: (row["catalog_id"], -row["claimed_published_at_ms"], row["article_code"]))
    selected = [
        row for row in inventory
        if claimed_release_start_ms <= row["claimed_published_at_ms"] < claimed_release_end_ms_exclusive
    ]
    if len(selected) > max_articles:
        raise CorpusContractError("selected article bound exceeded")
    details: list[dict[str, Any]] = []
    for row in sorted(selected, key=lambda item: (item["catalog_id"], item["article_code"])):
        response = acquire(f"details/{row['article_code']}", "detail", _detail_url(row["article_code"]))
        details.append(_parse_detail(response.body, row, _sha256_bytes(response.body), response.response_completed_at_utc))

    inventory_pass_2 = acquire_list_pass(2)
    time_after = acquire("time_after", "time", TIME_URL)
    after_ms = _time_ms(time_after.body)
    if before_ms > after_ms:
        raise CorpusContractError("Binance time bracket is reversed")
    local_times = []
    for row in ledger:
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

    interval_counts = {
        catalog_id: sum(row["catalog_id"] == catalog_id for row in selected)
        for catalog_id in catalog_ids
    }
    contract_failures: list[str] = []
    for catalog_id in catalog_ids:
        if pass_totals[1][catalog_id] != expected_totals[catalog_id]:
            contract_failures.append(f"CATALOG_{catalog_id}_TOTAL_MISMATCH")
        if interval_counts[catalog_id] != expected_interval_counts[catalog_id]:
            contract_failures.append(f"CATALOG_{catalog_id}_INTERVAL_COUNT_MISMATCH")
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
    ) + sum(expected_interval_counts.values())
    if len(ledger) != expected_request_count:
        contract_failures.append("REQUEST_COUNT_MISMATCH")

    verify_source_binding()
    ledger_path = raw_run / "request_ledger.jsonl"
    ledger_sha = _write_once(ledger_path, _stable_jsonl(ledger))
    inventory_path = processed_run / "inventory.jsonl"
    details_path = processed_run / "detail_index.jsonl"
    inventory_sha = _write_once(inventory_path, _stable_jsonl(inventory))
    detail_sha = _write_once(details_path, _stable_jsonl(sorted(details, key=lambda row: (row["catalog_id"], row["article_code"]))))
    terminal = "NEEDS_MORE_DATA" if not contract_failures else "INCONCLUSIVE"
    summary = {
        "run_id": run_id,
        "terminal_status": terminal,
        "artifact_state": "ANNOUNCEMENT_CORPUS_AVAILABLE" if not contract_failures else None,
        "semantics": "CORPUS_ONLY; NOT_EVENT_OR_ELIGIBILITY_EVIDENCE",
        "catalog_totals": {str(key): value for key, value in pass_totals[1].items()},
        "expected_totals": {str(key): expected_totals[key] for key in catalog_ids},
        "page_counts": {str(key): value for key, value in pass_page_counts[1].items()},
        "interval_counts": {str(key): value for key, value in interval_counts.items()},
        "expected_interval_counts": {
            str(key): expected_interval_counts[key] for key in catalog_ids
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
        "contract_failures": contract_failures,
        "response_bytes": counter[0],
        "request_count": len(ledger),
        "expected_request_count": expected_request_count,
        "wire_attempt_count": wire_attempt_counter[0],
        "max_wire_attempts": max_wire_attempts,
        "time_bracket_ms": {"before": before_ms, "after": after_ms},
        "max_clock_skew_ms": max_clock_skew_ms,
        "acquisition_bounds": {
            "max_pages_per_catalog": max_pages_per_catalog,
            "max_articles": max_articles,
            "max_response_bytes": max_response_bytes,
            "max_total_response_bytes": max_total_response_bytes,
            "timeout_seconds": timeout_seconds,
            "max_attempts": max_attempts,
            "max_wire_attempts": max_wire_attempts,
            "pacing_seconds": pacing_seconds,
        },
        "request_ledger": {"path": ledger_path.as_posix(), "sha256": ledger_sha},
        "inventory": {"path": inventory_path.as_posix(), "sha256": inventory_sha},
        "detail_index": {"path": details_path.as_posix(), "sha256": detail_sha},
        "extractor_source_sha256": _module_sha(),
        "pre_network_expected_extractor_sha256": expected_extractor_sha256,
        "transport_dependency_sha256": _dependency_sha(),
        "claimed_release_interval_ms": [claimed_release_start_ms, claimed_release_end_ms_exclusive],
        "historical_eligibility_ready": False,
    }
    verify_source_binding()
    _write_once(summary_output, _stable_json_bytes(summary, pretty=True))
    _write_once(schema_output, _stable_json_bytes(_schema(), pretty=True))
    _write_once(source_contract_output, _stable_json_bytes(_source_contract(), pretty=True))
    verify_source_binding()
    load_corpus(summary_output=summary_output, schema_output=schema_output, source_contract_output=source_contract_output)
    return summary


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorpusIntegrityError(f"cannot load {path}") from exc
    if type(value) is not dict:
        raise CorpusIntegrityError("trusted JSON must be an object")
    return value


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    body = path.read_bytes()
    try:
        rows = [json.loads(line) for line in body.decode("utf-8").splitlines()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorpusIntegrityError("invalid JSONL artifact") from exc
    if any(type(row) is not dict for row in rows) or _stable_jsonl(rows) != body:
        raise CorpusIntegrityError("JSONL is non-canonical")
    return rows, body


def _verified_response(
    entry: Mapping[str, Any],
    run_id: str,
    bounds: Mapping[str, Any],
    cumulative_response_bytes: list[int],
) -> TransportResponse:
    attempts = entry.get("attempts")
    logical_key = entry.get("logical_key")
    if (
        type(attempts) is not list
        or not attempts
        or len(attempts) > bounds["max_attempts"]
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
        if attempt.get("attempt") != number:
            raise CorpusIntegrityError("attempt gap")
        body_path = Path(attempt.get("body", ""))
        sidecar_path = Path(attempt.get("sidecar", ""))
        if body_path.parent != directory or body_path.name != f"attempt_{number:04d}.response" or sidecar_path != directory / f"attempt_{number:04d}.request.json":
            raise CorpusIntegrityError("attempt path mismatch")
        raw = body_path.read_bytes()
        sidecar_bytes = sidecar_path.read_bytes()
        if _sha256_bytes(raw) != attempt.get("body_sha256") or _sha256_bytes(sidecar_bytes) != attempt.get("sidecar_sha256"):
            raise CorpusIntegrityError("attempt hash mismatch")
        meta = _load_json(sidecar_path)
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
            raise CorpusIntegrityError("invalid attempt clock") from exc
        cumulative_response_bytes[0] += len(raw)
        recomputed_outcome = _response_outcome(
            request_headers_valid=request_headers_valid,
            response=reconstructed,
            canonical_url=entry["canonical_url"],
            max_response_bytes=bounds["max_response_bytes"],
            cumulative_response_bytes=cumulative_response_bytes[0],
            max_total_response_bytes=bounds["max_total_response_bytes"],
        )
        if recomputed_outcome != attempt.get("outcome"):
            raise CorpusIntegrityError("attempt outcome recomputation mismatch")
        if number < len(attempts) and not (
            response["status"] in RETRYABLE_HTTP_STATUSES
            and recomputed_outcome == f"HTTP_{response['status']}"
        ):
            raise CorpusIntegrityError("terminal outcome was retried")
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
    if _dependency_sha() != FROZEN_PIT_TRANSPORT_SHA256:
        raise CorpusIntegrityError("frozen PIT transport dependency changed")
    summary = _load_json(summary_output)
    if (
        summary.get("extractor_source_sha256") != _module_sha()
        or summary.get("pre_network_expected_extractor_sha256") != _module_sha()
        or summary.get("transport_dependency_sha256") != _dependency_sha()
    ):
        raise CorpusIntegrityError("source hash mismatch")
    if _load_json(schema_output) != _schema() or _load_json(source_contract_output) != _source_contract():
        raise CorpusIntegrityError("schema/source contract mismatch")
    bounds = summary.get("acquisition_bounds")
    if type(bounds) is not dict or set(bounds) != {
        "max_pages_per_catalog",
        "max_articles",
        "max_response_bytes",
        "max_total_response_bytes",
        "timeout_seconds",
        "max_attempts",
        "max_wire_attempts",
        "pacing_seconds",
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
        or isinstance(bounds["timeout_seconds"], bool)
        or not isinstance(bounds["timeout_seconds"], (int, float))
        or bounds["timeout_seconds"] <= 0
        or type(bounds["max_attempts"]) is not int
        or bounds["max_attempts"] < 1
        or type(bounds["max_wire_attempts"]) is not int
        or bounds["max_wire_attempts"] < 1
        or isinstance(bounds["pacing_seconds"], bool)
        or not isinstance(bounds["pacing_seconds"], (int, float))
        or bounds["pacing_seconds"] < 0
    ):
        raise CorpusIntegrityError("acquisition bounds invalid")
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
    responses = [
        _verified_response(row, summary["run_id"], bounds, outcome_bytes)
        for row in ledger
    ]
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

    inventory_path = Path(summary["inventory"]["path"])
    detail_path = Path(summary["detail_index"]["path"])
    if (
        inventory_path.name != "inventory.jsonl"
        or detail_path.name != "detail_index.jsonl"
        or inventory_path.parent != detail_path.parent
        or inventory_path.parent.name != summary["run_id"]
    ):
        raise CorpusIntegrityError("derived artifact path mismatch")
    for forbidden in _schema()["forbidden_artifacts"]:
        if (inventory_path.parent / forbidden).exists():
            raise CorpusIntegrityError("forbidden derived artifact exists")
    inventory, inventory_bytes = _load_jsonl(inventory_path)
    details, detail_bytes = _load_jsonl(detail_path)
    if _sha256_bytes(inventory_bytes) != summary["inventory"]["sha256"] or _sha256_bytes(detail_bytes) != summary["detail_index"]["sha256"]:
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
                len({row["article_id"] for row in subset}) != len(subset)
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
    rebuilt_inventory = sorted(rebuilt_pass_1, key=lambda row: (row["catalog_id"], -row["claimed_published_at_ms"], row["article_code"]))
    if rebuilt_inventory != inventory:
        raise CorpusIntegrityError("inventory rebuild mismatch")

    interval = summary.get("claimed_release_interval_ms")
    if (
        type(interval) is not list
        or len(interval) != 2
        or any(type(value) is not int for value in interval)
        or interval[0] >= interval[1]
    ):
        raise CorpusIntegrityError("claimed release interval invalid")
    selected = [
        row
        for row in rebuilt_inventory
        if interval[0] <= row["claimed_published_at_ms"] < interval[1]
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

    interval_counts = {
        catalog_id: sum(row["catalog_id"] == catalog_id for row in selected)
        for catalog_id in CATALOG_IDS
    }
    expected_totals = summary.get("expected_totals")
    expected_interval_counts = summary.get("expected_interval_counts")
    if (
        type(expected_totals) is not dict
        or type(expected_interval_counts) is not dict
        or set(expected_totals) != {str(key) for key in CATALOG_IDS}
        or set(expected_interval_counts) != {str(key) for key in CATALOG_IDS}
        or any(type(value) is not int or value < 0 for value in expected_totals.values())
        or any(
            type(value) is not int or value < 0
            for value in expected_interval_counts.values()
        )
    ):
        raise CorpusIntegrityError("expected count contract invalid")
    contract_failures: list[str] = []
    for catalog_id in CATALOG_IDS:
        key = str(catalog_id)
        if rebuilt_pass_totals[1][catalog_id] != expected_totals[key]:
            contract_failures.append(f"CATALOG_{catalog_id}_TOTAL_MISMATCH")
        if interval_counts[catalog_id] != expected_interval_counts[key]:
            contract_failures.append(
                f"CATALOG_{catalog_id}_INTERVAL_COUNT_MISMATCH"
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
    ) + sum(expected_interval_counts.values())
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
        or summary.get("interval_counts")
        != {str(key): value for key, value in interval_counts.items()}
        or summary.get("list_pass_stability") != rebuilt_stability
        or summary.get("expected_request_count") != expected_request_count
        or summary.get("contract_failures") != contract_failures
        or summary.get("terminal_status") != expected_terminal
        or summary.get("artifact_state") != expected_artifact_state
        or summary.get("semantics")
        != "CORPUS_ONLY; NOT_EVENT_OR_ELIGIBILITY_EVIDENCE"
    ):
        raise CorpusIntegrityError("summary semantic rebuild mismatch")
    if len(inventory) != summary["inventory_count"] or len(details) != summary["detail_count"]:
        raise CorpusIntegrityError("summary count mismatch")
    if summary.get("historical_eligibility_ready") is not False:
        raise CorpusIntegrityError("corpus cannot claim eligibility")
    return LoadedCorpus(
        run_id=summary["run_id"],
        terminal_status=summary["terminal_status"],
        artifact_state=summary["artifact_state"],
        inventory=tuple(inventory),
        details=tuple(details),
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
    parser.add_argument("--claimed-release-start-ms", type=int, required=True)
    parser.add_argument("--claimed-release-end-ms-exclusive", type=int, required=True)
    parser.add_argument("--expected-total", action="append", required=True)
    parser.add_argument("--expected-interval-count", action="append", required=True)
    parser.add_argument("--max-pages-per-catalog", type=int, required=True)
    parser.add_argument("--max-articles", type=int, required=True)
    parser.add_argument("--max-response-bytes", type=int, required=True)
    parser.add_argument("--max-total-response-bytes", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--max-attempts", type=int, required=True)
    parser.add_argument("--max-wire-attempts", type=int, required=True)
    parser.add_argument("--pacing-seconds", type=float, required=True)
    parser.add_argument("--max-clock-skew-ms", type=int, required=True)
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
            claimed_release_start_ms=args.claimed_release_start_ms,
            claimed_release_end_ms_exclusive=args.claimed_release_end_ms_exclusive,
            expected_totals=_pairs(args.expected_total),
            expected_interval_counts=_pairs(args.expected_interval_count),
            max_pages_per_catalog=args.max_pages_per_catalog,
            max_articles=args.max_articles,
            max_response_bytes=args.max_response_bytes,
            max_total_response_bytes=args.max_total_response_bytes,
            timeout_seconds=args.timeout_seconds,
            max_attempts=args.max_attempts,
            max_wire_attempts=args.max_wire_attempts,
            pacing_seconds=args.pacing_seconds,
            max_clock_skew_ms=args.max_clock_skew_ms,
        )
    except CorpusError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["terminal_status"] == "NEEDS_MORE_DATA" else 2


if __name__ == "__main__":
    raise SystemExit(main())
