from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contracts import (
    API_BASE,
    API_HOST,
    API_KEY_ENV,
    EXPERIMENT_ID,
    GLOBAL_HTTP_CAP,
    MAX_PAGES_PER_QUERY,
    PAGINATION_PARAMETER,
    QUERY_PLANS,
    QUERY_PLAN_SHA256,
    RUN_ID,
    VERSION,
    ContractError,
    QueryPlan,
    canonical_json_bytes,
    canonical_json_file_bytes,
    sha256_bytes,
    validate_api_key,
)
from .loader import ParsedPage, merge_pages, parse_page


class CollectorError(RuntimeError):
    def __init__(self, code: str) -> None:
        if type(code) is not str or not code or not code.isascii():
            code = "COLLECTOR_ERROR"
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class HTTPPage:
    status: int
    content_type: str
    body: bytes
    final_url: str
    redirect_count: int = 0


class Transport(Protocol):
    def __call__(self, url: str, api_key: str, cap_bytes: int) -> HTTPPage: ...


Clock = Callable[[], int]


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _read_limited(response, cap_bytes: int) -> bytes:  # type: ignore[no-untyped-def]
    body = response.read(cap_bytes + 1)
    if len(body) > cap_bytes:
        raise CollectorError("RESPONSE_TOO_LARGE")
    return body


def urllib_transport(url: str, api_key: str, cap_bytes: int) -> HTTPPage:
    validate_api_key(api_key)
    opener = build_opener(_RejectRedirects())
    request = Request(url, method="GET", headers={"x-api-key": api_key})
    try:
        with opener.open(request, timeout=30.0) as response:
            body = _read_limited(response, cap_bytes)
            return HTTPPage(
                status=int(response.status),
                content_type=response.headers.get("Content-Type", ""),
                body=body,
                final_url=response.geturl(),
                redirect_count=0,
            )
    except HTTPError as exc:
        try:
            body = _read_limited(exc, cap_bytes)
        except Exception as nested:
            raise CollectorError("TRANSPORT") from nested
        return HTTPPage(
            status=int(exc.code),
            content_type=exc.headers.get("Content-Type", "") if exc.headers is not None else "",
            body=body,
            final_url=url,
            redirect_count=0,
        )
    except (URLError, TimeoutError, OSError) as exc:
        raise CollectorError("TRANSPORT") from exc


def _build_url(plan: QueryPlan, pagination_key: str | None = None) -> str:
    pairs = list(plan.parameters.items())
    if pagination_key is not None:
        if type(pagination_key) is not str or not pagination_key:
            raise CollectorError("PAGINATION_KEY")
        pairs.append((PAGINATION_PARAMETER, pagination_key))
    url = f"{API_BASE}{plan.path}?{urlencode(pairs)}"
    _validate_url(plan, url, pagination_key)
    return url


def _validate_url(plan: QueryPlan, url: str, pagination_key: str | None) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != API_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.path != plan.path
        or parsed.fragment
    ):
        raise CollectorError("REQUEST_URL")
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if len({key for key, _ in pairs}) != len(pairs):
        raise CollectorError("REQUEST_QUERY_DUPLICATE")
    expected = list(plan.parameters.items())
    if pagination_key is not None:
        expected.append((PAGINATION_PARAMETER, pagination_key))
    if pairs != expected:
        raise CollectorError("REQUEST_QUERY_MUTATION")


def _validate_http_page(plan: QueryPlan, requested_url: str, page: HTTPPage) -> None:
    if type(page) is not HTTPPage:
        raise CollectorError("TRANSPORT_TYPE")
    if page.final_url != requested_url or page.redirect_count != 0 or 300 <= page.status < 400:
        raise CollectorError("REDIRECT")
    content_type = page.content_type.split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise CollectorError("CONTENT_TYPE")
    if type(page.body) is not bytes or len(page.body) > plan.response_cap_bytes:
        raise CollectorError("RESPONSE_TOO_LARGE")


def _is_reparse_or_link(path: Path) -> bool:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _ensure_ordinary_parents(path: Path, stop: Path) -> None:
    resolved_stop = Path(os.path.abspath(stop))
    current = Path(os.path.abspath(path))
    if os.path.commonpath((str(resolved_stop), str(current))) != str(resolved_stop):
        raise CollectorError("PATH_ESCAPE")
    while True:
        if _is_reparse_or_link(current) or not current.is_dir():
            raise CollectorError("PATH_TYPE")
        if current == resolved_stop:
            break
        current = current.parent


def _write_atomic(directory: Path, name: str, payload: bytes) -> Path:
    if type(name) is not str or not name or "/" in name or "\\" in name:
        raise CollectorError("OUTPUT_NAME")
    target = directory / name
    if target.exists() or target.is_symlink():
        raise CollectorError("OUTPUT_PREEXISTENCE")
    temporary = directory / f".{name}.{os.getpid()}.{time.time_ns()}.tmp"
    if os.path.splitdrive(str(target.resolve(strict=False)))[0].casefold() != os.path.splitdrive(str(temporary.resolve(strict=False)))[0].casefold():
        raise CollectorError("OUTPUT_VOLUME")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def _canonical_jsonl(rows: list[dict[str, object]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def collect_pages(
    *,
    api_key: str,
    transport: Transport,
    clock: Clock,
    staging: Path,
) -> tuple[list[ParsedPage], list[dict[str, object]], list[dict[str, object]]]:
    validate_api_key(api_key)
    parsed_pages: list[ParsedPage] = []
    receipts: list[dict[str, object]] = []
    raw_entries: list[dict[str, object]] = []
    request_ordinal = 0
    response_dir = staging / "responses"
    response_dir.mkdir(exist_ok=False)
    for plan in QUERY_PLANS:
        page_number = 1
        pagination_key: str | None = None
        seen_keys: set[str] = set()
        while True:
            if page_number > MAX_PAGES_PER_QUERY:
                raise CollectorError("PAGINATION_PAGE_CAP")
            if request_ordinal >= GLOBAL_HTTP_CAP:
                raise CollectorError("HTTP_REQUEST_CAP")
            url = _build_url(plan, pagination_key)
            request_ordinal += 1
            sent_at_ms = clock()
            response = transport(url, api_key, plan.response_cap_bytes)
            received_at_ms = clock()
            if received_at_ms < sent_at_ms:
                raise CollectorError("CLIENT_CLOCK")
            _validate_http_page(plan, url, response)
            parsed = parse_page(
                plan,
                page_number=page_number,
                status=response.status,
                body=response.body,
                received_at_ms=received_at_ms,
            )
            relative = f"responses/{plan.ordinal:02d}_{plan.query_id}_page_{page_number:04d}.json"
            target = _write_atomic(response_dir, Path(relative).name, response.body)
            raw_sha = sha256_bytes(response.body)
            raw_entries.append({
                "bytes": len(response.body),
                "http_request_ordinal": request_ordinal,
                "logical_query_ordinal": plan.ordinal,
                "page_number": page_number,
                "path": relative,
                "sha256": raw_sha,
            })
            receipts.append({
                "api_key_header_sent": True,
                "body_bytes": len(response.body),
                "body_sha256": raw_sha,
                "client_received_at_ms": received_at_ms,
                "client_sent_at_ms": sent_at_ms,
                "http_request_ordinal": request_ordinal,
                "http_status": response.status,
                "logical_query_id": plan.query_id,
                "logical_query_ordinal": plan.ordinal,
                "method": "GET",
                "page_number": page_number,
                "path": relative,
                "query_parameters_sha256": sha256_bytes(canonical_json_bytes(dict(parse_qsl(urlsplit(url).query)))),
                "redirect_count": 0,
            })
            if target.stat().st_size != len(response.body):
                raise CollectorError("PUBLISH_BYTES")
            parsed_pages.append(parsed)
            next_key = parsed.pagination_key
            if next_key is None:
                break
            if next_key in seen_keys:
                raise CollectorError("PAGINATION_LOOP")
            seen_keys.add(next_key)
            pagination_key = next_key
            page_number += 1
    if request_ordinal > GLOBAL_HTTP_CAP:
        raise CollectorError("HTTP_REQUEST_CAP")
    return parsed_pages, receipts, raw_entries


def _tree_sha(entries: list[dict[str, object]]) -> str:
    ordered = sorted(entries, key=lambda row: str(row["path"]).encode("utf-8"))
    return sha256_bytes(canonical_json_bytes(ordered))


def collect_and_publish(
    *,
    run_root: Path,
    api_key: str,
    transport: Transport = urllib_transport,
    clock: Clock = lambda: time.time_ns() // 1_000_000,
) -> dict[str, object]:
    validate_api_key(api_key)
    run_root = Path(os.path.abspath(run_root))
    parent = run_root.parent
    parent.mkdir(parents=True, exist_ok=True)
    _ensure_ordinary_parents(parent, parent.anchor and Path(parent.anchor) or parent)
    final = run_root
    staging = parent / f".{RUN_ID}.staging"
    control = parent / f".{RUN_ID}.control"
    if final.exists() or staging.exists() or control.exists():
        raise CollectorError("PREEXISTENCE")
    control.mkdir()
    lease = {
        "experiment_id": EXPERIMENT_ID,
        "query_plan_sha256": QUERY_PLAN_SHA256,
        "run_id": RUN_ID,
        "version": VERSION,
    }
    try:
        descriptor = os.open(control / "lease.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_file_bytes(lease))
            handle.flush()
            os.fsync(handle.fileno())
        staging.mkdir()
        pages, receipts, raw_entries = collect_pages(api_key=api_key, transport=transport, clock=clock, staging=staging)
        loaded = merge_pages(pages)
        query_plan_bytes = canonical_json_file_bytes({
            "global_http_cap": GLOBAL_HTTP_CAP,
            "logical_query_count": len(QUERY_PLANS),
            "queries": [plan.projection() for plan in QUERY_PLANS],
            "query_plan_sha256": QUERY_PLAN_SHA256,
            "retry_count": 0,
        })
        receipt_bytes = _canonical_jsonl(receipts)
        manifest = {
            "experiment_id": EXPERIMENT_ID,
            "http_request_count": len(receipts),
            "logical_query_count": len(QUERY_PLANS),
            "page_count": len(pages),
            "query_plan_sha256": QUERY_PLAN_SHA256,
            "raw_files": sorted(raw_entries, key=lambda row: str(row["path"]).encode("utf-8")),
            "raw_tree_sha256": _tree_sha(raw_entries),
            "retry_count": 0,
            "run_id": RUN_ID,
            "schema_version": VERSION,
        }
        summary = {
            "artifact_state": "JQUANTS_V2_FREE_SOURCE_PROBE_ACQUIRED",
            "calendar_row_count": len(loaded.calendar_days),
            "daily_bar_row_count": len(loaded.bars),
            "empirical_authorized": False,
            "experiment_id": EXPERIMENT_ID,
            "historical_eligibility_ready": False,
            "http_request_count": len(receipts),
            "logical_query_count": len(QUERY_PLANS),
            "master_row_count": len(loaded.master_rows),
            "raw_tree_sha256": manifest["raw_tree_sha256"],
            "retry_count": 0,
            "run_id": RUN_ID,
            "strict_eligible_count": 0,
            "terminal_status": "NEEDS_MORE_DATA",
        }
        _write_atomic(staging, "query_plan.json", query_plan_bytes)
        _write_atomic(staging, "receipts.jsonl", receipt_bytes)
        _write_atomic(staging, "acquisition_manifest.json", canonical_json_file_bytes(manifest))
        _write_atomic(staging, "summary.json", canonical_json_file_bytes(summary))
        for entry in raw_entries:
            raw_path = staging / str(entry["path"])
            raw = raw_path.read_bytes()
            if len(raw) != entry["bytes"] or sha256_bytes(raw) != entry["sha256"]:
                raise CollectorError("RAW_TREE")
        authorization = {
            "experiment_id": EXPERIMENT_ID,
            "final_path": str(final),
            "raw_tree_sha256": manifest["raw_tree_sha256"],
            "run_id": RUN_ID,
        }
        descriptor = os.open(control / "authorization.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_file_bytes(authorization))
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.splitdrive(str(staging))[0].casefold() != os.path.splitdrive(str(final))[0].casefold():
            raise CollectorError("PROMOTION_VOLUME")
        os.replace(staging, final)
        return summary
    except Exception as exc:
        code = exc.code if isinstance(exc, (CollectorError, ContractError)) else "INTERNAL"
        failure = {
            "experiment_id": EXPERIMENT_ID,
            "failure_code": code,
            "run_id": RUN_ID,
        }
        if (control / "lease.json").exists() and not (control / "authorization.json").exists():
            try:
                descriptor = os.open(control / "failure.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(canonical_json_file_bytes(failure))
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError:
                pass
        raise CollectorError(code) from None


def dry_plan_projection() -> dict[str, object]:
    return {
        "api_base": API_BASE,
        "execute": False,
        "global_http_cap": GLOBAL_HTTP_CAP,
        "logical_query_count": len(QUERY_PLANS),
        "max_pages_per_query": MAX_PAGES_PER_QUERY,
        "network_request_count": 0,
        "query_plan_sha256": QUERY_PLAN_SHA256,
        "queries": [plan.projection() for plan in QUERY_PLANS],
        "retry_count": 0,
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jquants-v2-source-probe")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-plan", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_plan:
        sys.stdout.buffer.write(canonical_json_file_bytes(dry_plan_projection()))
        return 0
    raw_key = os.environ.pop(API_KEY_ENV, None)
    if raw_key is None:
        return 11
    try:
        key = validate_api_key(raw_key)
    except ContractError:
        raw_key = None
        return 11
    raw_key = None
    final = _repo_root() / "data" / "raw" / "jquants_v2" / "runs" / RUN_ID
    try:
        collect_and_publish(run_root=final, api_key=key)
        return 0
    except CollectorError:
        return 20
    finally:
        key = ""
        os.environ.pop(API_KEY_ENV, None)


if __name__ == "__main__":
    raise SystemExit(main())
