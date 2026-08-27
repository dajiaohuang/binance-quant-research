"""Trusted loader for the forward Spot schedule/current-metadata snapshot."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import stat
from typing import Any, Mapping, Sequence


EXPERIMENT_ID = "exp_20260826_008"
RUN_ID = "exp_20260826_008_formal_001"
VERSION = "binance_spot_forward_schedule_pit_v1"
SEMANTICS = (
    "CURRENT_VISIBLE_FORWARD_SCHEDULE_CLAIMS_AND_CURRENT_SPOT_METADATA_ONLY;"
    "PLANNED_AT_CLAIM_NOT_EFFECTIVE_AT_OR_ELIGIBILITY"
)
ARTIFACT_STATE = "FORWARD_SPOT_SCHEDULE_PIT_SNAPSHOT_COMPLETE"

BINDING_PATHS = {
    "wrapper": "src/quant_research/binance_spot_forward_schedule_pit_v1_wrapper.ps1",
    "collector": "src/quant_research/binance_spot_forward_schedule_pit_v1.py",
    "loader": "src/quant_research/binance_spot_forward_schedule_pit_v1_loader.py",
    "source_contract": "experiments/exp_20260826_008/artifacts/source_contract.json",
    "schema": "experiments/exp_20260826_008/artifacts/schema.json",
    "parameters": "experiments/exp_20260826_008/parameters.json",
    "tests": "tests/test_binance_spot_forward_schedule_pit_v1.py",
}
FLAG_ORDER = (
    ("wrapper", "-ExpectedWrapperSha256"),
    ("collector", "-ExpectedCollectorSha256"),
    ("loader", "-ExpectedLoaderSha256"),
    ("source_contract", "-ExpectedSourceContractSha256"),
    ("schema", "-ExpectedSchemaSha256"),
    ("parameters", "-ExpectedParametersSha256"),
    ("tests", "-ExpectedTestsSha256"),
)

ENDPOINTS = (
    (1, "time_before", "https://api.binance.com/api/v3/time", False),
    (2, "open_symbol_list", "https://api.binance.com/sapi/v1/spot/open-symbol-list", True),
    (3, "delist_schedule", "https://api.binance.com/sapi/v1/spot/delist-schedule", True),
    (4, "exchange_info", "https://api.binance.com/api/v3/exchangeInfo?showPermissionSets=true", False),
    (5, "time_after", "https://api.binance.com/api/v3/time", False),
)
RAW_NAMES = {
    endpoint_id: f"requests/{ordinal:03d}_{endpoint_id}.response"
    for ordinal, endpoint_id, _url, _key in ENDPOINTS
}
RECEIPT_NAMES = {
    endpoint_id: f"requests/{ordinal:03d}_{endpoint_id}.receipt.json"
    for ordinal, endpoint_id, _url, _key in ENDPOINTS
}
ROOT_NAMES = {"requests", "plans.jsonl", "current_symbols.jsonl", "joins.jsonl", "summary.json"}
REQUEST_NAMES = {
    pathlib.PurePosixPath(path).name
    for path in (*RAW_NAMES.values(), *RECEIPT_NAMES.values())
}
RECEIPT_KEYS = {
    "ordinal", "endpoint_id", "method", "url", "client_send_utc_ms",
    "client_recv_utc_ms", "http_status", "body_bytes", "body_sha256",
    "api_key_header_sent",
}
PLAN_KEYS = {
    "kind", "planned_at_claim_ms", "symbol", "source_endpoint_id",
    "source_body_sha256", "source_record_index", "source_symbol_index",
    "row_sha256",
}
CURRENT_KEYS = {
    "symbol", "status", "baseAsset", "quoteAsset", "permissionSets",
    "source_body_sha256", "source_record_index", "row_sha256",
}
JOIN_KEYS = {
    "kind", "planned_at_claim_ms", "symbol", "join_status",
    "plan_row_sha256", "current_symbol_row_sha256", "row_sha256",
}
SUMMARY_KEYS = {
    "experiment_id", "run_id", "version", "semantics", "terminal_status",
    "artifact_state", "request_count", "request_receipts", "time_bracket",
    "plan_count", "current_symbol_count", "join_count",
    "join_status_counts", "code_bindings", "output_artifacts",
    "artifact_tree_sha256", "historical_eligibility_ready",
    "eligibility_evaluated", "strict_eligible_count",
}
TIME_BRACKET_KEYS = {
    "server_time_before_ms", "server_time_after_ms", "server_time_delta_ms",
    "observation_only",
}
ARTIFACT_KEYS = {"path", "bytes", "sha256", "rows"}
LEASE_KEYS = {
    "experiment_id", "run_id", "formal_command_sha256",
    "expected_bindings_sha256",
}
AUTH_KEYS = {
    "experiment_id", "run_id", "summary_sha256", "artifact_tree_sha256",
    "final_tree_sha256",
}
HEX = set("0123456789abcdef")


class SnapshotLoadError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class LoadedSnapshot:
    terminal_status: str
    artifact_state: str
    plan_count: int
    current_symbol_count: int
    join_count: int
    artifact_tree_sha256: str
    strict_eligible_count: int
    summary: Mapping[str, Any]


def _fail(message: str) -> None:
    raise SnapshotLoadError(message)


def _hex64(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(ch in HEX for ch in value)


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate JSON key")
        result[key] = value
    return result


def _strict_json(raw: bytes) -> Any:
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("JSON BOM")
    try:
        text = raw.decode("utf-8", errors="strict")
        return json.loads(
            text, object_pairs_hook=_object_pairs,
            parse_constant=lambda _value: _fail("nonfinite JSON number"),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        _fail("invalid JSON")


def _compact(value: Any, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return raw + (b"\n" if newline else b"")


def _pretty(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _utf8(value: str) -> bytes:
    return value.encode("utf-8")


def _exact_keys(value: Any, keys: set[str], label: str) -> None:
    if type(value) is not dict or set(value) != keys:
        _fail(f"{label} keyset mismatch")


def _plain_directory(path: pathlib.Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not bool(attributes & reparse_flag)
    )


def _plain_file(path: pathlib.Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not bool(attributes & reparse_flag)
    )


def _load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        _fail(f"{path.name} final LF missing")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        value = _strict_json(line)
        if type(value) is not dict:
            _fail(f"{path.name} row type")
        rows.append(value)
    if b"".join(_compact(row, newline=True) for row in rows) != raw:
        _fail(f"{path.name} noncanonical")
    return rows


def _row_with_sha(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["row_sha256"] = _sha(_compact(row))
    return result


def _parse_time(raw: bytes) -> int:
    value = _strict_json(raw)
    if (
        type(value) is not dict or set(value) != {"serverTime"}
        or type(value["serverTime"]) is not int or value["serverTime"] < 0
    ):
        _fail("time schema")
    return value["serverTime"]


def _parse_schedule(raw: bytes, kind: str, time_key: str, endpoint_id: str) -> list[dict[str, Any]]:
    value = _strict_json(raw)
    if type(value) is not list:
        _fail("schedule schema")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    body_sha = _sha(raw)
    for record_index, item in enumerate(value):
        if type(item) is not dict or set(item) != {time_key, "symbols"}:
            _fail("schedule row schema")
        planned = item[time_key]
        symbols = item["symbols"]
        if (
            type(planned) is not int or planned < 0 or type(symbols) is not list
            or any(type(symbol) is not str or not symbol for symbol in symbols)
        ):
            _fail("schedule value schema")
        for symbol_index, symbol in enumerate(symbols):
            key = (kind, planned, symbol)
            if key in seen:
                _fail("duplicate schedule key")
            seen.add(key)
            rows.append(_row_with_sha({
                "kind": kind, "planned_at_claim_ms": planned,
                "symbol": symbol, "source_endpoint_id": endpoint_id,
                "source_body_sha256": body_sha,
                "source_record_index": record_index,
                "source_symbol_index": symbol_index,
            }))
    return rows


def _parse_exchange(raw: bytes) -> list[dict[str, Any]]:
    value = _strict_json(raw)
    if type(value) is not dict or type(value.get("symbols")) is not list:
        _fail("exchangeInfo schema")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    body_sha = _sha(raw)
    for record_index, item in enumerate(value["symbols"]):
        required = {"symbol", "status", "baseAsset", "quoteAsset", "permissionSets"}
        if type(item) is not dict or not required.issubset(item):
            _fail("exchangeInfo row schema")
        symbol = item["symbol"]
        groups = item["permissionSets"]
        if (
            any(type(item[name]) is not str or not item[name] for name in (
                "symbol", "status", "baseAsset", "quoteAsset",
            ))
            or type(groups) is not list
            or any(type(group) is not list or any(type(p) is not str for p in group) for group in groups)
            or symbol in seen
        ):
            _fail("exchangeInfo row value")
        seen.add(symbol)
        rows.append(_row_with_sha({
            "symbol": symbol, "status": item["status"],
            "baseAsset": item["baseAsset"], "quoteAsset": item["quoteAsset"],
            "permissionSets": groups, "source_body_sha256": body_sha,
            "source_record_index": record_index,
        }))
    rows.sort(key=lambda row: _utf8(row["symbol"]))
    return rows


def _rebuild(raw_by_id: Mapping[str, bytes]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int, int]:
    before = _parse_time(raw_by_id["time_before"])
    after = _parse_time(raw_by_id["time_after"])
    if after < before or after - before > 180_000:
        _fail("server time bracket")
    plans = _parse_schedule(raw_by_id["open_symbol_list"], "OPEN", "openTime", "open_symbol_list")
    plans += _parse_schedule(raw_by_id["delist_schedule"], "DELIST", "delistTime", "delist_schedule")
    plans.sort(key=lambda row: (_utf8(row["kind"]), row["planned_at_claim_ms"], _utf8(row["symbol"])))
    current = _parse_exchange(raw_by_id["exchange_info"])
    current_by_symbol = {row["symbol"]: row for row in current}
    joins = []
    for plan in plans:
        match = current_by_symbol.get(plan["symbol"])
        joins.append(_row_with_sha({
            "kind": plan["kind"], "planned_at_claim_ms": plan["planned_at_claim_ms"],
            "symbol": plan["symbol"],
            "join_status": "MATCHED" if match is not None else "MISSING",
            "plan_row_sha256": plan["row_sha256"],
            "current_symbol_row_sha256": match["row_sha256"] if match is not None else None,
        }))
    return plans, current, joins, before, after


def _validate_row_sha(row: Mapping[str, Any], keys: set[str], label: str) -> None:
    _exact_keys(row, keys, label)
    if not _hex64(row["row_sha256"]):
        _fail(f"{label} row SHA type")
    core = {key: value for key, value in row.items() if key != "row_sha256"}
    if _sha(_compact(core)) != row["row_sha256"]:
        _fail(f"{label} row SHA mismatch")


def _artifact_tree(entries: Sequence[Mapping[str, Any]]) -> str:
    material = b"".join(
        (
            f"{item['path']}\0{item['rows']}\0{item['bytes']}\0"
            f"{item['sha256']}\n"
        ).encode("utf-8")
        for item in entries
    )
    return _sha(material)


def _final_tree(root: pathlib.Path) -> str:
    entries = []
    for path in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: _utf8(item.relative_to(root).as_posix()),
    ):
        if not _plain_file(path):
            _fail("symlink in final tree")
        raw = path.read_bytes()
        entries.append({
            "path": path.relative_to(root).as_posix(), "bytes": len(raw),
            "sha256": _sha(raw),
        })
    return _sha(_compact(entries))


def validate_directory(
    root: pathlib.Path,
    *, expected_code_bindings: Mapping[str, Mapping[str, str]] | None = None,
) -> LoadedSnapshot:
    if not _plain_directory(root):
        _fail("snapshot root")
    root_entries = list(root.iterdir())
    if {item.name for item in root_entries} != ROOT_NAMES:
        _fail("root basename allowlist")
    request_root = root / "requests"
    if not _plain_directory(request_root):
        _fail("requests directory")
    request_entries = list(request_root.iterdir())
    if (
        {item.name for item in request_entries} != REQUEST_NAMES
        or any(not _plain_file(item) for item in request_entries)
    ):
        _fail("request basename allowlist")
    for name in ("plans.jsonl", "current_symbols.jsonl", "joins.jsonl", "summary.json"):
        path = root / name
        if not _plain_file(path):
            _fail("derived artifact type")

    raw_by_id: dict[str, bytes] = {}
    receipts: list[dict[str, Any]] = []
    previous_recv: int | None = None
    for ordinal, endpoint_id, url, key_sent in ENDPOINTS:
        raw_path = root.joinpath(*pathlib.PurePosixPath(RAW_NAMES[endpoint_id]).parts)
        receipt_path = root.joinpath(*pathlib.PurePosixPath(RECEIPT_NAMES[endpoint_id]).parts)
        body = raw_path.read_bytes()
        receipt_raw = receipt_path.read_bytes()
        receipt = _strict_json(receipt_raw)
        _exact_keys(receipt, RECEIPT_KEYS, "receipt")
        if _pretty(receipt) != receipt_raw:
            _fail("receipt noncanonical")
        if (
            receipt["ordinal"] != ordinal or receipt["endpoint_id"] != endpoint_id
            or receipt["method"] != "GET" or receipt["url"] != url
            or type(receipt["client_send_utc_ms"]) is not int
            or type(receipt["client_recv_utc_ms"]) is not int
            or receipt["client_send_utc_ms"] < 0
            or receipt["client_recv_utc_ms"] <= receipt["client_send_utc_ms"]
            or (previous_recv is not None and receipt["client_send_utc_ms"] <= previous_recv)
            or receipt["http_status"] != 200
            or receipt["body_bytes"] != len(body)
            or receipt["body_sha256"] != _sha(body)
            or receipt["api_key_header_sent"] is not key_sent
        ):
            _fail("receipt/body contract")
        raw_by_id[endpoint_id] = body
        receipts.append(receipt)
        previous_recv = receipt["client_recv_utc_ms"]

    rebuilt_plans, rebuilt_current, rebuilt_joins, before, after = _rebuild(raw_by_id)
    plans = _load_jsonl(root / "plans.jsonl")
    current = _load_jsonl(root / "current_symbols.jsonl")
    joins = _load_jsonl(root / "joins.jsonl")
    for row in plans:
        _validate_row_sha(row, PLAN_KEYS, "plan")
    for row in current:
        _validate_row_sha(row, CURRENT_KEYS, "current")
    for row in joins:
        _validate_row_sha(row, JOIN_KEYS, "join")
    if plans != rebuilt_plans or current != rebuilt_current or joins != rebuilt_joins:
        _fail("derived rebuild mismatch")

    summary_raw = (root / "summary.json").read_bytes()
    summary = _strict_json(summary_raw)
    _exact_keys(summary, SUMMARY_KEYS, "summary")
    if _pretty(summary) != summary_raw:
        _fail("summary noncanonical")
    if (
        summary["experiment_id"] != EXPERIMENT_ID or summary["run_id"] != RUN_ID
        or summary["version"] != VERSION or summary["semantics"] != SEMANTICS
        or summary["terminal_status"] != "NEEDS_MORE_DATA"
        or summary["artifact_state"] != ARTIFACT_STATE
        or summary["historical_eligibility_ready"] is not False
        or summary["eligibility_evaluated"] is not False
        or summary["strict_eligible_count"] != 0
        or summary["request_count"] != 5 or summary["request_receipts"] != receipts
    ):
        _fail("summary fixed fields")
    _exact_keys(summary["time_bracket"], TIME_BRACKET_KEYS, "time bracket")
    if summary["time_bracket"] != {
        "server_time_before_ms": before,
        "server_time_after_ms": after,
        "server_time_delta_ms": after - before,
        "observation_only": True,
    }:
        _fail("summary time bracket")
    counts = {
        "MATCHED": sum(row["join_status"] == "MATCHED" for row in joins),
        "MISSING": sum(row["join_status"] == "MISSING" for row in joins),
    }
    if (
        summary["plan_count"] != len(plans)
        or summary["current_symbol_count"] != len(current)
        or summary["join_count"] != len(joins)
        or summary["join_status_counts"] != counts
        or len(joins) != len(plans)
    ):
        _fail("summary count closure")
    bindings = summary["code_bindings"]
    if type(bindings) is not dict or set(bindings) != set(BINDING_PATHS):
        _fail("binding keyset")
    for name, relative in BINDING_PATHS.items():
        item = bindings[name]
        if type(item) is not dict or set(item) != {"path", "sha256"}:
            _fail("binding row")
        if item["path"] != relative or not _hex64(item["sha256"]):
            _fail("binding value")
    if expected_code_bindings is not None and bindings != expected_code_bindings:
        _fail("expected binding mismatch")

    artifact_names = [*RAW_NAMES.values(), *RECEIPT_NAMES.values(), "plans.jsonl", "current_symbols.jsonl", "joins.jsonl"]
    row_counts = {"plans.jsonl": len(plans), "current_symbols.jsonl": len(current), "joins.jsonl": len(joins)}
    expected_entries = []
    for name in sorted(artifact_names, key=_utf8):
        raw = root.joinpath(*pathlib.PurePosixPath(name).parts).read_bytes()
        expected_entries.append({
            "path": name, "bytes": len(raw), "sha256": _sha(raw),
            "rows": row_counts.get(name),
        })
    if type(summary["output_artifacts"]) is not list:
        _fail("output artifact list")
    for item in summary["output_artifacts"]:
        _exact_keys(item, ARTIFACT_KEYS, "output artifact")
    if summary["output_artifacts"] != expected_entries:
        _fail("output artifact mismatch")
    tree = _artifact_tree(expected_entries)
    if summary["artifact_tree_sha256"] != tree:
        _fail("artifact tree mismatch")
    return LoadedSnapshot(
        summary["terminal_status"], summary["artifact_state"], len(plans),
        len(current), len(joins), tree, summary["strict_eligible_count"], summary,
    )


def _canonical_authority(path: pathlib.Path, keys: set[str]) -> dict[str, Any]:
    raw = path.read_bytes()
    value = _strict_json(raw)
    _exact_keys(value, keys, path.name)
    if _pretty(value) != raw:
        _fail("authority noncanonical")
    return value


def _formal_command_sha(bindings: Mapping[str, Mapping[str, str]]) -> str:
    parts = [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File",
        r"src\quant_research\binance_spot_forward_schedule_pit_v1_wrapper.ps1",
    ]
    for name, parameter in FLAG_ORDER:
        parts.extend((parameter, bindings[name]["sha256"]))
    return _sha(" ".join(parts).encode("utf-8"))


def load_committed(repo_root: pathlib.Path) -> LoadedSnapshot:
    repo_root = repo_root.resolve()
    run_parent = repo_root / f"data/raw/{VERSION}/runs"
    final_root = run_parent / RUN_ID
    staging_root = run_parent / f".{RUN_ID}.staging"
    control_root = run_parent / f".{RUN_ID}.control"
    if (
        not _plain_directory(final_root) or staging_root.exists()
        or not _plain_directory(control_root)
        or (control_root / "failure.json").exists()
        or {item.name for item in control_root.iterdir()} != {"lease.json", "authorization.json"}
        or any(
            not _plain_file(control_root / name)
            for name in ("lease.json", "authorization.json")
        )
    ):
        _fail("committed lifecycle state")
    lease = _canonical_authority(control_root / "lease.json", LEASE_KEYS)
    authorization = _canonical_authority(control_root / "authorization.json", AUTH_KEYS)
    if (
        lease["experiment_id"] != EXPERIMENT_ID or lease["run_id"] != RUN_ID
        or authorization["experiment_id"] != EXPERIMENT_ID
        or authorization["run_id"] != RUN_ID
    ):
        _fail("authority identity")
    summary = _strict_json((final_root / "summary.json").read_bytes())
    bindings = summary.get("code_bindings") if type(summary) is dict else None
    if type(bindings) is not dict or set(bindings) != set(BINDING_PATHS):
        _fail("live binding map")
    for name, relative in BINDING_PATHS.items():
        path = repo_root.joinpath(*pathlib.PurePosixPath(relative).parts)
        if type(bindings[name]) is not dict or set(bindings[name]) != {
            "path", "sha256",
        }:
            _fail("live binding row")
        if (
            bindings[name] != {"path": relative, "sha256": bindings[name].get("sha256")}
            or not path.is_file() or _sha(path.read_bytes()) != bindings[name]["sha256"]
        ):
            _fail("live binding mismatch")
    if (
        _sha(_compact(bindings)) != lease["expected_bindings_sha256"]
        or _formal_command_sha(bindings) != lease["formal_command_sha256"]
    ):
        _fail("lease binding mismatch")
    loaded = validate_directory(final_root, expected_code_bindings=bindings)
    if (
        _sha((final_root / "summary.json").read_bytes()) != authorization["summary_sha256"]
        or loaded.artifact_tree_sha256 != authorization["artifact_tree_sha256"]
        or _final_tree(final_root) != authorization["final_tree_sha256"]
    ):
        _fail("authorization mismatch")
    return loaded
