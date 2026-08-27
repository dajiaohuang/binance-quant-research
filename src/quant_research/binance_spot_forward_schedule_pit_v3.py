"""One-shot Binance forward Spot schedule/current-metadata PIT collector.

The production entry point is the frozen PowerShell wrapper.  This module is
also intentionally transport-injectable so Phase 2 tests never touch a wire.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import pathlib
import stat
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Sequence


EXPERIMENT_ID = "exp_20260826_010"
RUN_ID = "exp_20260826_010_formal_001"
VERSION = "binance_spot_forward_schedule_pit_v3"
SEMANTICS = (
    "CURRENT_VISIBLE_FORWARD_SCHEDULE_CLAIMS_AND_CURRENT_SPOT_METADATA_ONLY;"
    "PLANNED_AT_CLAIM_NOT_EFFECTIVE_AT_OR_ELIGIBILITY"
)
ARTIFACT_STATE = "FORWARD_SPOT_SCHEDULE_PIT_SNAPSHOT_COMPLETE"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RAW_ROOT = REPO_ROOT / "data/raw"
VERSION_ROOT = RAW_ROOT / VERSION
RUNS_ROOT = VERSION_ROOT / "runs"
FINAL_ROOT = RUNS_ROOT / RUN_ID
STAGING_ROOT = RUNS_ROOT / f".{RUN_ID}.staging"
CONTROL_ROOT = RUNS_ROOT / f".{RUN_ID}.control"

BINDING_SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "--expected-wrapper-sha256", "wrapper",
        "src/quant_research/binance_spot_forward_schedule_pit_v3_wrapper.ps1",
    ),
    (
        "--expected-collector-sha256", "collector",
        "src/quant_research/binance_spot_forward_schedule_pit_v3.py",
    ),
    (
        "--expected-loader-sha256", "loader",
        "src/quant_research/binance_spot_forward_schedule_pit_v3_loader.py",
    ),
    (
        "--expected-source-contract-sha256", "source_contract",
        "experiments/exp_20260826_010/artifacts/source_contract.json",
    ),
    (
        "--expected-schema-sha256", "schema",
        "experiments/exp_20260826_010/artifacts/schema.json",
    ),
    (
        "--expected-parameters-sha256", "parameters",
        "experiments/exp_20260826_010/parameters.json",
    ),
    (
        "--expected-tests-sha256", "tests",
        "tests/test_binance_spot_forward_schedule_pit_v3.py",
    ),
)

@dataclasses.dataclass(frozen=True)
class Endpoint:
    ordinal: int
    endpoint_id: str
    url: str
    api_key_header: bool
    body_cap: int


ENDPOINTS = (
    Endpoint(1, "time_before", "https://api.binance.com/api/v3/time", False, 65_536),
    Endpoint(
        2, "open_symbol_list",
        "https://api.binance.com/sapi/v1/spot/open-symbol-list", True,
        2_097_152,
    ),
    Endpoint(
        3, "delist_schedule",
        "https://api.binance.com/sapi/v1/spot/delist-schedule", True,
        2_097_152,
    ),
    Endpoint(
        4, "exchange_info",
        "https://api.binance.com/api/v3/exchangeInfo?showPermissionSets=true",
        False, 33_554_432,
    ),
    Endpoint(5, "time_after", "https://api.binance.com/api/v3/time", False, 65_536),
)
MAX_TOTAL_BODY_BYTES = 41_943_040
REQUEST_TIMEOUT_SECONDS = 30.0
TOTAL_WALL_SECONDS = 180.0
API_KEY_ENV = "BINANCE_READ_ONLY_API_KEY"

EXIT_PREEXISTENCE = 10
EXIT_PRECONDITION = 11
EXIT_SOURCE_BINDING = 20
EXIT_INFRASTRUCTURE = 24
EXIT_TRANSPORT = 30
EXIT_HTTP_STATUS = 31
EXIT_JSON_SCHEMA = 32
EXIT_TIME_BRACKET = 33
EXIT_OUTPUT_INTEGRITY = 34
EXIT_PROMOTION = 35

FAILURE_EXIT = {
    "PRECONDITION": EXIT_PRECONDITION,
    "SOURCE_BINDING": EXIT_SOURCE_BINDING,
    "INFRASTRUCTURE": EXIT_INFRASTRUCTURE,
    "TRANSPORT": EXIT_TRANSPORT,
    "HTTP_STATUS": EXIT_HTTP_STATUS,
    "JSON_SCHEMA": EXIT_JSON_SCHEMA,
    "TIME_BRACKET": EXIT_TIME_BRACKET,
    "OUTPUT_INTEGRITY": EXIT_OUTPUT_INTEGRITY,
    "PROMOTION": EXIT_PROMOTION,
}

RAW_NAMES = {
    endpoint.endpoint_id: (
        f"requests/{endpoint.ordinal:03d}_{endpoint.endpoint_id}.response"
    )
    for endpoint in ENDPOINTS
}
RECEIPT_NAMES = {
    endpoint.endpoint_id: (
        f"requests/{endpoint.ordinal:03d}_{endpoint.endpoint_id}.receipt.json"
    )
    for endpoint in ENDPOINTS
}
DERIVED_NAMES = ("plans.jsonl", "current_symbols.jsonl", "joins.jsonl")


class CollectorError(RuntimeError):
    def __init__(self, code: str, *, failure_authorized: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = FAILURE_EXIT[code]
        self.failure_authorized = failure_authorized


@dataclasses.dataclass(frozen=True)
class TransportResponse:
    http_status: int
    body: bytes
    final_url: str


Transport = Callable[[str, Mapping[str, str], float, int], TransportResponse]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str,
        headers: Any, newurl: str,
    ) -> None:
        return None


def _default_transport(
    url: str, headers: Mapping[str, str], timeout: float, body_cap: int,
) -> TransportResponse:
    request = urllib.request.Request(
        url=url, headers=dict(headers), method="GET",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectHandler(),
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(body_cap + 1)
            return TransportResponse(
                int(response.status), body, str(response.geturl()),
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(body_cap + 1)
        return TransportResponse(int(exc.code), body, str(exc.geturl()))


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_compact(value: Any, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return raw + (b"\n" if newline else b"")


def canonical_pretty(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def strict_json(raw: bytes) -> Any:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("JSON BOM")
    text = raw.decode("utf-8", errors="strict")

    def reject_constant(_value: str) -> None:
        raise ValueError("nonfinite JSON number")

    return json.loads(
        text, object_pairs_hook=_object_pairs, parse_constant=reject_constant,
    )


def strict_jsonl(raw: bytes) -> list[dict[str, Any]]:
    if raw and not raw.endswith(b"\n"):
        raise ValueError("JSONL final LF missing")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        value = strict_json(line)
        if type(value) is not dict:
            raise ValueError("JSONL row is not object")
        rows.append(value)
    return rows


def _hex64(value: str) -> str:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise argparse.ArgumentTypeError("expected lowercase SHA-256")
    return value


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    expected_flags = [item[0] for item in BINDING_SPECS]
    if len(argv) != len(expected_flags) * 2 or list(argv[0::2]) != expected_flags:
        raise CollectorError("SOURCE_BINDING")
    parser = argparse.ArgumentParser(allow_abbrev=False)
    for flag in expected_flags:
        parser.add_argument(flag, required=True, type=_hex64)
    try:
        return parser.parse_args(list(argv))
    except SystemExit:
        raise CollectorError("SOURCE_BINDING") from None


def expected_bindings(args: argparse.Namespace) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for flag, name, path in BINDING_SPECS:
        attr = flag.removeprefix("--").replace("-", "_")
        result[name] = {"path": path, "sha256": getattr(args, attr)}
    return result


def verify_bindings(
    repo_root: pathlib.Path, bindings: Mapping[str, Mapping[str, str]],
) -> None:
    if set(bindings) != {item[1] for item in BINDING_SPECS}:
        raise CollectorError("SOURCE_BINDING")
    for _flag, name, relative in BINDING_SPECS:
        if bindings[name].get("path") != relative:
            raise CollectorError("SOURCE_BINDING")
        path = repo_root.joinpath(*pathlib.PurePosixPath(relative).parts)
        if not path.is_file() or sha256_file(path) != bindings[name].get("sha256"):
            raise CollectorError("SOURCE_BINDING")


def formal_command(bindings: Mapping[str, Mapping[str, str]]) -> str:
    parts = [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File",
        r"src\quant_research\binance_spot_forward_schedule_pit_v3_wrapper.ps1",
    ]
    for _flag, name, _path in BINDING_SPECS:
        parameter = "-Expected" + "".join(
            part.title() for part in name.split("_")
        ) + "Sha256"
        parts.extend((parameter, bindings[name]["sha256"]))
    return " ".join(parts) + "; $nativeExitCode=$LASTEXITCODE; exit $nativeExitCode"


def take_api_key(environ: dict[str, str] | os._Environ[str]) -> str:
    value = environ.pop(API_KEY_ENV, None)
    if (
        type(value) is not str or not value or value != value.strip()
        or "\r" in value or "\n" in value or "\x00" in value
    ):
        raise CollectorError("PRECONDITION")
    return value


def _write_once(path: pathlib.Path, raw: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _lexists(path: pathlib.Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _normalized(path: pathlib.Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _same_path(left: pathlib.Path, right: pathlib.Path) -> bool:
    return _normalized(left) == _normalized(right)


def _drive(path: pathlib.Path) -> str:
    return os.path.normcase(os.path.splitdrive(_normalized(path))[0])


def _ensure_plain_directory(path: pathlib.Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise CollectorError("INFRASTRUCTURE") from None
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise CollectorError("INFRASTRUCTURE")


def validate_parent_layout(*, shared_must_exist: bool) -> None:
    expected_raw = REPO_ROOT / "data/raw"
    expected_version = expected_raw / VERSION
    expected_runs = expected_version / "runs"
    expected_final = expected_runs / RUN_ID
    expected_staging = expected_runs / f".{RUN_ID}.staging"
    expected_control = expected_runs / f".{RUN_ID}.control"
    pairs = (
        (RAW_ROOT, expected_raw), (VERSION_ROOT, expected_version),
        (RUNS_ROOT, expected_runs), (FINAL_ROOT, expected_final),
        (STAGING_ROOT, expected_staging), (CONTROL_ROOT, expected_control),
    )
    if any(not _same_path(actual, expected) for actual, expected in pairs):
        raise CollectorError("INFRASTRUCTURE")
    paths = (
        REPO_ROOT, RAW_ROOT, VERSION_ROOT, RUNS_ROOT,
        FINAL_ROOT, STAGING_ROOT, CONTROL_ROOT,
    )
    if len({_drive(path) for path in paths}) != 1:
        raise CollectorError("INFRASTRUCTURE")
    try:
        common = os.path.commonpath([_normalized(path) for path in paths])
    except ValueError:
        raise CollectorError("INFRASTRUCTURE") from None
    if os.path.normcase(common) != _normalized(REPO_ROOT):
        raise CollectorError("INFRASTRUCTURE")
    if (
        not _same_path(FINAL_ROOT.parent, RUNS_ROOT)
        or not _same_path(STAGING_ROOT.parent, RUNS_ROOT)
        or not _same_path(CONTROL_ROOT.parent, RUNS_ROOT)
        or len({_normalized(FINAL_ROOT), _normalized(STAGING_ROOT), _normalized(CONTROL_ROOT)}) != 3
    ):
        raise CollectorError("INFRASTRUCTURE")
    for required in (REPO_ROOT, REPO_ROOT / "data", RAW_ROOT):
        _ensure_plain_directory(required)
    for shared in (VERSION_ROOT, RUNS_ROOT):
        if _lexists(shared):
            _ensure_plain_directory(shared)
        elif shared_must_exist:
            raise CollectorError("INFRASTRUCTURE")


def bootstrap_shared_parents() -> None:
    validate_parent_layout(shared_must_exist=False)
    try:
        VERSION_ROOT.mkdir(parents=False, exist_ok=True)
        RUNS_ROOT.mkdir(parents=False, exist_ok=True)
    except OSError:
        raise CollectorError("INFRASTRUCTURE") from None
    validate_parent_layout(shared_must_exist=True)


def _row_with_sha(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["row_sha256"] = sha256_bytes(canonical_compact(row))
    return result


def _valid_nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _parse_time(body: bytes) -> int:
    try:
        value = strict_json(body)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise CollectorError("JSON_SCHEMA") from None
    if type(value) is not dict or set(value) != {"serverTime"}:
        raise CollectorError("JSON_SCHEMA")
    if not _valid_nonnegative_int(value["serverTime"]):
        raise CollectorError("JSON_SCHEMA")
    return value["serverTime"]


def _parse_schedule(body: bytes, *, kind: str, time_key: str, endpoint_id: str) -> list[dict[str, Any]]:
    try:
        value = strict_json(body)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise CollectorError("JSON_SCHEMA") from None
    if type(value) is not list:
        raise CollectorError("JSON_SCHEMA")
    rows: list[dict[str, Any]] = []
    body_sha = sha256_bytes(body)
    seen: set[tuple[str, int, str]] = set()
    for record_index, item in enumerate(value):
        if type(item) is not dict or set(item) != {time_key, "symbols"}:
            raise CollectorError("JSON_SCHEMA")
        planned = item[time_key]
        symbols = item["symbols"]
        if (
            not _valid_nonnegative_int(planned) or type(symbols) is not list
            or any(type(symbol) is not str or not symbol for symbol in symbols)
        ):
            raise CollectorError("JSON_SCHEMA")
        for symbol_index, symbol in enumerate(symbols):
            key = (kind, planned, symbol)
            if key in seen:
                raise CollectorError("JSON_SCHEMA")
            seen.add(key)
            rows.append(_row_with_sha({
                "kind": kind,
                "planned_at_claim_ms": planned,
                "symbol": symbol,
                "source_endpoint_id": endpoint_id,
                "source_body_sha256": body_sha,
                "source_record_index": record_index,
                "source_symbol_index": symbol_index,
            }))
    return rows


def _parse_exchange_info(body: bytes) -> list[dict[str, Any]]:
    try:
        value = strict_json(body)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise CollectorError("JSON_SCHEMA") from None
    if type(value) is not dict or type(value.get("symbols")) is not list:
        raise CollectorError("JSON_SCHEMA")
    rows: list[dict[str, Any]] = []
    body_sha = sha256_bytes(body)
    seen: set[str] = set()
    for record_index, item in enumerate(value["symbols"]):
        if type(item) is not dict:
            raise CollectorError("JSON_SCHEMA")
        required = {"symbol", "status", "baseAsset", "quoteAsset", "permissionSets"}
        if not required.issubset(item):
            raise CollectorError("JSON_SCHEMA")
        symbol = item["symbol"]
        permission_sets = item["permissionSets"]
        if (
            any(type(item[name]) is not str or not item[name] for name in (
                "symbol", "status", "baseAsset", "quoteAsset",
            ))
            or type(permission_sets) is not list
            or any(
                type(group) is not list
                or any(type(permission) is not str for permission in group)
                for group in permission_sets
            )
            or symbol in seen
        ):
            raise CollectorError("JSON_SCHEMA")
        seen.add(symbol)
        rows.append(_row_with_sha({
            "symbol": symbol,
            "status": item["status"],
            "baseAsset": item["baseAsset"],
            "quoteAsset": item["quoteAsset"],
            "permissionSets": permission_sets,
            "source_body_sha256": body_sha,
            "source_record_index": record_index,
        }))
    return rows


def _utf8(value: str) -> bytes:
    return value.encode("utf-8")


def derive_rows(bodies: Mapping[str, bytes]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int, int]:
    before = _parse_time(bodies["time_before"])
    after = _parse_time(bodies["time_after"])
    if after < before or after - before > 180_000:
        raise CollectorError("TIME_BRACKET")
    plans = _parse_schedule(
        bodies["open_symbol_list"], kind="OPEN", time_key="openTime",
        endpoint_id="open_symbol_list",
    ) + _parse_schedule(
        bodies["delist_schedule"], kind="DELIST", time_key="delistTime",
        endpoint_id="delist_schedule",
    )
    plans.sort(key=lambda row: (_utf8(row["kind"]), row["planned_at_claim_ms"], _utf8(row["symbol"])))
    current = _parse_exchange_info(bodies["exchange_info"])
    current.sort(key=lambda row: _utf8(row["symbol"]))
    current_by_symbol = {row["symbol"]: row for row in current}
    joins: list[dict[str, Any]] = []
    for plan in plans:
        match = current_by_symbol.get(plan["symbol"])
        joins.append(_row_with_sha({
            "kind": plan["kind"],
            "planned_at_claim_ms": plan["planned_at_claim_ms"],
            "symbol": plan["symbol"],
            "join_status": "MATCHED" if match is not None else "MISSING",
            "plan_row_sha256": plan["row_sha256"],
            "current_symbol_row_sha256": (
                match["row_sha256"] if match is not None else None
            ),
        }))
    return plans, current, joins, before, after


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_compact(row, newline=True) for row in rows)


def _artifact_entries(root: pathlib.Path, names: Sequence[str], row_counts: Mapping[str, int | None]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name in sorted(names, key=_utf8):
        raw = root.joinpath(*pathlib.PurePosixPath(name).parts).read_bytes()
        entries.append({
            "path": name, "bytes": len(raw), "sha256": sha256_bytes(raw),
            "rows": row_counts.get(name),
        })
    return entries


def artifact_tree_sha256(entries: Sequence[Mapping[str, Any]]) -> str:
    material = b"".join(
        (
            f"{item['path']}\0{item['rows']}\0{item['bytes']}\0"
            f"{item['sha256']}\n"
        ).encode("utf-8")
        for item in entries
    )
    return sha256_bytes(material)


def _safe_request(
    endpoint: Endpoint,
    *, api_key: str,
    transport: Transport,
    monotonic: Callable[[], float],
    utc_ms: Callable[[], int],
    deadline: float,
    previous_recv_ms: int | None,
) -> tuple[bytes, dict[str, Any]]:
    start = monotonic()
    remaining = deadline - start
    if not math.isfinite(start) or remaining <= 0:
        raise CollectorError("TRANSPORT")
    timeout = min(REQUEST_TIMEOUT_SECONDS, remaining)
    send_ms = utc_ms()
    if (
        not _valid_nonnegative_int(send_ms)
        or (previous_recv_ms is not None and send_ms <= previous_recv_ms)
    ):
        raise CollectorError("TRANSPORT")
    headers = {"X-MBX-APIKEY": api_key} if endpoint.api_key_header else {}
    failed = False
    response: TransportResponse | None = None
    try:
        response = transport(endpoint.url, headers, timeout, endpoint.body_cap)
    except BaseException:
        failed = True
    if failed:
        raise CollectorError("TRANSPORT") from None
    recv_ms = utc_ms()
    end = monotonic()
    if (
        type(response) is not TransportResponse
        or not _valid_nonnegative_int(recv_ms) or recv_ms <= send_ms
        or not math.isfinite(end) or end > deadline
        or type(response.http_status) is not int
        or type(response.body) is not bytes
        or type(response.final_url) is not str
        or response.final_url != endpoint.url
        or len(response.body) > endpoint.body_cap
        or api_key.encode("utf-8") in response.body
    ):
        raise CollectorError("TRANSPORT")
    receipt = {
        "ordinal": endpoint.ordinal,
        "endpoint_id": endpoint.endpoint_id,
        "method": "GET",
        "url": endpoint.url,
        "client_send_utc_ms": send_ms,
        "client_recv_utc_ms": recv_ms,
        "http_status": response.http_status,
        "body_bytes": len(response.body),
        "body_sha256": sha256_bytes(response.body),
        "api_key_header_sent": endpoint.api_key_header,
    }
    return response.body, receipt


def collect_to_staging(
    *, api_key: str, transport: Transport = _default_transport,
    monotonic: Callable[[], float] = time.monotonic,
    utc_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    request_root = STAGING_ROOT / "requests"
    request_root.mkdir(parents=False, exist_ok=False)
    deadline = monotonic() + TOTAL_WALL_SECONDS
    bodies: dict[str, bytes] = {}
    receipts: list[dict[str, Any]] = []
    total_bytes = 0
    previous_recv: int | None = None
    for endpoint in ENDPOINTS:
        body, receipt = _safe_request(
            endpoint, api_key=api_key, transport=transport,
            monotonic=monotonic, utc_ms=utc_ms, deadline=deadline,
            previous_recv_ms=previous_recv,
        )
        total_bytes += len(body)
        if total_bytes > MAX_TOTAL_BODY_BYTES:
            raise CollectorError("TRANSPORT")
        raw_path = STAGING_ROOT.joinpath(*pathlib.PurePosixPath(RAW_NAMES[endpoint.endpoint_id]).parts)
        receipt_path = STAGING_ROOT.joinpath(*pathlib.PurePosixPath(RECEIPT_NAMES[endpoint.endpoint_id]).parts)
        _write_once(raw_path, body)
        _write_once(receipt_path, canonical_pretty(receipt))
        bodies[endpoint.endpoint_id] = body
        receipts.append(receipt)
        previous_recv = receipt["client_recv_utc_ms"]
        if receipt["http_status"] != 200:
            raise CollectorError("HTTP_STATUS")
    return bodies, receipts


def build_payload(
    bodies: Mapping[str, bytes], receipts: Sequence[Mapping[str, Any]],
    bindings: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    plans, current, joins, before, after = derive_rows(bodies)
    payload_bytes = {
        "plans.jsonl": _jsonl(plans),
        "current_symbols.jsonl": _jsonl(current),
        "joins.jsonl": _jsonl(joins),
    }
    for name, raw in payload_bytes.items():
        _write_once(STAGING_ROOT / name, raw)
    names = [
        *(RAW_NAMES[endpoint.endpoint_id] for endpoint in ENDPOINTS),
        *(RECEIPT_NAMES[endpoint.endpoint_id] for endpoint in ENDPOINTS),
        *DERIVED_NAMES,
    ]
    row_counts: dict[str, int | None] = {name: None for name in names}
    row_counts.update({
        "plans.jsonl": len(plans),
        "current_symbols.jsonl": len(current),
        "joins.jsonl": len(joins),
    })
    entries = _artifact_entries(STAGING_ROOT, names, row_counts)
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "run_id": RUN_ID,
        "version": VERSION,
        "semantics": SEMANTICS,
        "terminal_status": "NEEDS_MORE_DATA",
        "artifact_state": ARTIFACT_STATE,
        "request_count": len(receipts),
        "request_receipts": list(receipts),
        "time_bracket": {
            "server_time_before_ms": before,
            "server_time_after_ms": after,
            "server_time_delta_ms": after - before,
            "observation_only": True,
        },
        "plan_count": len(plans),
        "current_symbol_count": len(current),
        "join_count": len(joins),
        "join_status_counts": {
            "MATCHED": sum(row["join_status"] == "MATCHED" for row in joins),
            "MISSING": sum(row["join_status"] == "MISSING" for row in joins),
        },
        "code_bindings": dict(bindings),
        "output_artifacts": entries,
        "artifact_tree_sha256": artifact_tree_sha256(entries),
        "historical_eligibility_ready": False,
        "eligibility_evaluated": False,
        "strict_eligible_count": 0,
    }
    return {"summary": summary, "summary_bytes": canonical_pretty(summary)}


def final_tree_sha256(root: pathlib.Path) -> str:
    entries: list[dict[str, Any]] = []
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: _utf8(item.relative_to(root).as_posix()),
    ):
        raw = path.read_bytes()
        entries.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": len(raw), "sha256": sha256_bytes(raw),
        })
    return sha256_bytes(canonical_compact(entries))


def _failure(code: str, exit_code: int) -> None:
    _write_once(CONTROL_ROOT / "failure.json", canonical_pretty({
        "experiment_id": EXPERIMENT_ID,
        "run_id": RUN_ID,
        "failure_code": code,
        "exit_code": exit_code,
    }))


def _execute(
    args: argparse.Namespace, api_key: str, *,
    transport: Transport = _default_transport,
    monotonic: Callable[[], float] = time.monotonic,
    utc_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
) -> int:
    bindings = expected_bindings(args)
    verify_bindings(REPO_ROOT, bindings)
    if any(_lexists(path) for path in (FINAL_ROOT, STAGING_ROOT, CONTROL_ROOT)):
        return EXIT_PREEXISTENCE
    bootstrap_shared_parents()
    if any(_lexists(path) for path in (FINAL_ROOT, STAGING_ROOT, CONTROL_ROOT)):
        return EXIT_PREEXISTENCE
    try:
        CONTROL_ROOT.mkdir(parents=False, exist_ok=False)
    except OSError:
        raise CollectorError("INFRASTRUCTURE") from None
    formal_sha = sha256_bytes(formal_command(bindings).encode("utf-8"))
    expected_sha = sha256_bytes(canonical_compact(bindings))
    try:
        _write_once(CONTROL_ROOT / "lease.json", canonical_pretty({
            "experiment_id": EXPERIMENT_ID,
            "run_id": RUN_ID,
            "formal_command_sha256": formal_sha,
            "expected_bindings_sha256": expected_sha,
        }))
    except OSError:
        raise CollectorError("INFRASTRUCTURE") from None
    try:
        try:
            STAGING_ROOT.mkdir(parents=False, exist_ok=False)
        except OSError:
            raise CollectorError("INFRASTRUCTURE") from None
        bodies, receipts = collect_to_staging(
            api_key=api_key, transport=transport,
            monotonic=monotonic, utc_ms=utc_ms,
        )
        payload = build_payload(bodies, receipts, bindings)
        _write_once(STAGING_ROOT / "summary.json", payload["summary_bytes"])
        import binance_spot_forward_schedule_pit_v3_loader as trusted_loader
        loaded = trusted_loader.validate_directory(
            STAGING_ROOT, expected_code_bindings=bindings,
        )
        if (
            loaded.terminal_status != "NEEDS_MORE_DATA"
            or loaded.strict_eligible_count != 0
        ):
            raise CollectorError("OUTPUT_INTEGRITY")
        verify_bindings(REPO_ROOT, bindings)
        validate_parent_layout(shared_must_exist=True)
        authorization = {
            "experiment_id": EXPERIMENT_ID,
            "run_id": RUN_ID,
            "summary_sha256": sha256_file(STAGING_ROOT / "summary.json"),
            "artifact_tree_sha256": loaded.artifact_tree_sha256,
            "final_tree_sha256": final_tree_sha256(STAGING_ROOT),
        }
        _write_once(
            CONTROL_ROOT / "authorization.json",
            canonical_pretty(authorization),
        )
        try:
            os.rename(STAGING_ROOT, FINAL_ROOT)
        except OSError:
            (CONTROL_ROOT / "authorization.json").unlink(missing_ok=True)
            raise CollectorError("PROMOTION") from None
        return 0
    except CollectorError as exc:
        exc.failure_authorized = True
        raise
    except BaseException:
        raise CollectorError(
            "INFRASTRUCTURE", failure_authorized=True,
        ) from None


def main(
    argv: Sequence[str] | None = None, *,
    transport: Transport = _default_transport,
    monotonic: Callable[[], float] = time.monotonic,
    utc_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    environ: dict[str, str] | os._Environ[str] = os.environ,
) -> int:
    try:
        api_key = take_api_key(environ)
    except CollectorError as exc:
        return exc.exit_code
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        return _execute(
            args, api_key, transport=transport,
            monotonic=monotonic, utc_ms=utc_ms,
        )
    except CollectorError as exc:
        if (
            exc.failure_authorized and CONTROL_ROOT.is_dir()
            and (CONTROL_ROOT / "lease.json").is_file()
            and not (CONTROL_ROOT / "failure.json").exists()
            and not (CONTROL_ROOT / "authorization.json").exists()
            and not FINAL_ROOT.exists()
        ):
            _failure(exc.code, exc.exit_code)
        return exc.exit_code
    finally:
        api_key = ""


if __name__ == "__main__":
    raise SystemExit(main())
