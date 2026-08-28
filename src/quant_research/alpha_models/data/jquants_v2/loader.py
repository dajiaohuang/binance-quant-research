from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Iterable

from .contracts import (
    BAR_FIELDS,
    CALENDAR_FIELDS,
    MASTER_FIELDS,
    GLOBAL_HTTP_CAP,
    PAGINATION_PARAMETER,
    CausalAdjustedBar,
    CalendarDay,
    ContractError,
    DailyBar,
    ListingSpell,
    MasterRow,
    QueryPlan,
    QUERY_PLANS,
    QUERY_PLAN_SHA256,
    RUN_ID,
    VERSION,
    canonical_json_bytes,
    canonical_equity_code,
    exact_int,
    finite_number,
    jst_known_at_ms,
    nonempty_text,
    sha256_bytes,
    strict_date,
    strict_json_object,
    validate_code,
)


@dataclass(frozen=True)
class ParsedPage:
    query_id: str
    page_number: int
    status: int
    records: tuple[CalendarDay | MasterRow | DailyBar, ...]
    pagination_key: str | None
    raw_sha256: str
    received_at_ms: int
    expected_rejection: bool = False


@dataclass(frozen=True)
class LoadedRun:
    calendar_days: tuple[CalendarDay, ...]
    master_rows: tuple[MasterRow, ...]
    bars: tuple[DailyBar, ...]
    raw_tree_sha256: str
    page_count: int


def _exact_keys(value: object, expected: set[str] | frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(expected):
        raise ContractError(code)
    return value


def _response_envelope(body: bytes) -> tuple[list[Any], str | None]:
    obj = strict_json_object(body)
    if set(obj) not in ({"data"}, {"data", PAGINATION_PARAMETER}):
        raise ContractError("RESPONSE_KEYS")
    data = obj["data"]
    if type(data) is not list:
        raise ContractError("RESPONSE_DATA")
    key = obj.get(PAGINATION_PARAMETER)
    if key is not None:
        if type(key) is not str or not key or len(key) > 2048 or any(ord(char) < 0x21 or ord(char) > 0x7E for char in key):
            raise ContractError("PAGINATION_KEY")
    return data, key


def _nullable_number(value: object, name: str) -> float | None:
    if value is None:
        return None
    return finite_number(value, name)


def _calendar_row(value: object, raw_sha: str, received_at_ms: int) -> CalendarDay:
    row = _exact_keys(value, CALENDAR_FIELDS, "CALENDAR_FIELDS")
    return CalendarDay(
        session_date=strict_date(row["Date"]),
        holiday_division=nonempty_text(row["HolDiv"], "HOLDIV"),
        received_at_ms=received_at_ms,
        raw_sha256=raw_sha,
    )


def _master_row(value: object, raw_sha: str, received_at_ms: int) -> MasterRow:
    row = _exact_keys(value, MASTER_FIELDS, "MASTER_FIELDS")
    snapshot_date = strict_date(row["Date"])
    raw_code = validate_code(row["Code"])
    return MasterRow(
        snapshot_date=snapshot_date,
        raw_code=raw_code,
        symbol=canonical_equity_code(raw_code),
        company_name=nonempty_text(row["CoName"], "CONAME"),
        company_name_en=nonempty_text(row["CoNameEn"], "CONAME_EN"),
        sector17_code=nonempty_text(row["S17"], "S17"),
        sector17_name=nonempty_text(row["S17Nm"], "S17NM"),
        sector33_code=nonempty_text(row["S33"], "S33"),
        sector33_name=nonempty_text(row["S33Nm"], "S33NM"),
        scale_category=nonempty_text(row["ScaleCat"], "SCALE"),
        market_code=nonempty_text(row["Mkt"], "MARKET"),
        market_name=nonempty_text(row["MktNm"], "MARKET_NAME"),
        margin_code=nonempty_text(row["Mrgn"], "MARGIN"),
        margin_name=nonempty_text(row["MrgnNm"], "MARGIN_NAME"),
        security_type_code=nonempty_text(row["SecType"], "SECTYPE"),
        security_type_name=nonempty_text(row["SecTypeNm"], "SECTYPE_NAME"),
        known_at_ms=jst_known_at_ms(snapshot_date, 8, 0),
        received_at_ms=received_at_ms,
        raw_sha256=raw_sha,
    )


def _bar_row(value: object, raw_sha: str, received_at_ms: int) -> DailyBar:
    row = _exact_keys(value, BAR_FIELDS, "BAR_FIELDS")
    session_date = strict_date(row["Date"])
    raw_code = validate_code(row["Code"])
    return DailyBar(
        session_date=session_date,
        raw_code=raw_code,
        symbol=canonical_equity_code(raw_code),
        open=_nullable_number(row["O"], "OPEN"),
        high=_nullable_number(row["H"], "HIGH"),
        low=_nullable_number(row["L"], "LOW"),
        close=_nullable_number(row["C"], "CLOSE"),
        volume=_nullable_number(row["Vo"], "VOLUME"),
        amount=_nullable_number(row["Va"], "AMOUNT"),
        adjustment_factor=finite_number(row["AdjFactor"], "ADJFACTOR", positive=True),
        adjusted_open=_nullable_number(row["AdjO"], "ADJ_OPEN"),
        adjusted_high=_nullable_number(row["AdjH"], "ADJ_HIGH"),
        adjusted_low=_nullable_number(row["AdjL"], "ADJ_LOW"),
        adjusted_close=_nullable_number(row["AdjC"], "ADJ_CLOSE"),
        adjusted_volume=_nullable_number(row["AdjVo"], "ADJ_VOLUME"),
        known_at_ms=jst_known_at_ms(session_date, 16, 30),
        received_at_ms=received_at_ms,
        raw_sha256=raw_sha,
    )


def parse_page(
    plan: QueryPlan,
    *,
    page_number: int,
    status: int,
    body: bytes,
    received_at_ms: int,
) -> ParsedPage:
    if type(plan) is not QueryPlan:
        raise ContractError("QUERY_PLAN")
    exact_int(page_number, "PAGE_NUMBER", 1)
    exact_int(status, "HTTP_STATUS", 100)
    exact_int(received_at_ms, "RECEIVED_AT")
    raw_sha = sha256_bytes(body)
    if plan.result_contract == "EXPECTED_REJECTION":
        if status != 400 or page_number != 1:
            raise ContractError("EXPECTED_REJECTION_STATUS")
        error = strict_json_object(body)
        if set(error) not in ({"message"}, {"code", "message"}):
            raise ContractError("EXPECTED_REJECTION_BODY")
        nonempty_text(error["message"], "REJECTION_MESSAGE")
        if "code" in error:
            nonempty_text(error["code"], "REJECTION_CODE")
        return ParsedPage(plan.query_id, page_number, status, (), None, raw_sha, received_at_ms, True)
    if status != 200:
        raise ContractError("HTTP_STATUS")
    data, pagination_key = _response_envelope(body)
    if plan.path == "/v2/markets/calendar":
        records = tuple(_calendar_row(row, raw_sha, received_at_ms) for row in data)
    elif plan.path == "/v2/equities/master":
        records = tuple(_master_row(row, raw_sha, received_at_ms) for row in data)
    elif plan.path == "/v2/equities/bars/daily":
        records = tuple(_bar_row(row, raw_sha, received_at_ms) for row in data)
    else:
        raise ContractError("QUERY_PATH")
    _validate_plan_records(plan, records)
    return ParsedPage(plan.query_id, page_number, status, records, pagination_key, raw_sha, received_at_ms)


def _validate_plan_records(plan: QueryPlan, records: tuple[object, ...]) -> None:
    if plan.result_contract == "EXACT_DATE_RANGE":
        start = plan.parameters["from"]
        end = plan.parameters["to"]
        if any(not isinstance(row, CalendarDay) or not start <= row.session_date <= end for row in records):
            raise ContractError("CALENDAR_RANGE")
    elif plan.result_contract == "EXACT_RESPONSE_DATE":
        expected = plan.parameters["date"]
        for row in records:
            actual = row.snapshot_date if isinstance(row, MasterRow) else row.session_date if isinstance(row, DailyBar) else None
            if actual != expected:
                raise ContractError("RESPONSE_DATE")
    elif plan.result_contract == "EXACT_CODE_AND_DATE_RANGE":
        expected_code = canonical_equity_code(plan.parameters["code"])
        start = plan.parameters["from"]
        end = plan.parameters["to"]
        if any(not isinstance(row, DailyBar) or row.symbol != expected_code or not start <= row.session_date <= end for row in records):
            raise ContractError("BAR_RANGE")


def merge_pages(pages: Iterable[ParsedPage]) -> LoadedRun:
    ordered = tuple(pages)
    if not ordered:
        raise ContractError("NO_PAGES")
    calendar: list[CalendarDay] = []
    masters: list[MasterRow] = []
    bars: list[DailyBar] = []
    tree_rows: list[dict[str, object]] = []
    for page in ordered:
        tree_rows.append({"page": page.page_number, "query_id": page.query_id, "raw_sha256": page.raw_sha256})
        for record in page.records:
            if isinstance(record, CalendarDay):
                calendar.append(record)
            elif isinstance(record, MasterRow):
                masters.append(record)
            elif isinstance(record, DailyBar):
                bars.append(record)
            else:
                raise ContractError("RECORD_TYPE")
    calendar.sort(key=lambda row: row.session_date)
    masters.sort(key=lambda row: (row.snapshot_date, row.symbol.encode("utf-8")))
    bars.sort(key=lambda row: (row.session_date, row.symbol.encode("utf-8")))
    if len({row.session_date for row in calendar}) != len(calendar):
        raise ContractError("DUPLICATE_CALENDAR")
    if len({(row.snapshot_date, row.symbol) for row in masters}) != len(masters):
        raise ContractError("DUPLICATE_MASTER")
    if len({(row.session_date, row.symbol) for row in bars}) != len(bars):
        raise ContractError("DUPLICATE_BAR")
    from .contracts import canonical_json_bytes, sha256_bytes
    return LoadedRun(tuple(calendar), tuple(masters), tuple(bars), sha256_bytes(canonical_json_bytes(tree_rows)), len(ordered))


def official_sessions(calendar: Iterable[CalendarDay]) -> tuple[CalendarDay, ...]:
    rows = tuple(sorted(calendar, key=lambda row: row.session_date))
    if not rows or len({row.session_date for row in rows}) != len(rows):
        raise ContractError("CALENDAR_IDENTITY")
    return tuple(row for row in rows if row.is_session)


def derive_listing_spells(rows: Iterable[MasterRow]) -> tuple[ListingSpell, ...]:
    snapshots: dict[str, dict[str, MasterRow]] = {}
    for row in rows:
        bucket = snapshots.setdefault(row.snapshot_date, {})
        if row.symbol in bucket:
            raise ContractError("DUPLICATE_MASTER")
        bucket[row.symbol] = row
    dates = tuple(sorted(snapshots))
    if len(dates) < 2:
        raise ContractError("ADJACENT_MASTER_REQUIRED")
    active: dict[str, tuple[str, MasterRow]] = {}
    spells: list[ListingSpell] = []
    for index, snapshot_date in enumerate(dates):
        current = snapshots[snapshot_date]
        missing = set(active) - set(current)
        for symbol in sorted(missing):
            start, prior = active.pop(symbol)
            spells.append(ListingSpell(symbol, start, dates[index - 1], snapshot_date, prior.market_code, prior.security_type_code))
        for symbol, row in current.items():
            if symbol in active:
                start, prior = active[symbol]
                identity_changed = (prior.company_name, prior.security_type_code) != (row.company_name, row.security_type_code)
                if identity_changed:
                    spells.append(ListingSpell(symbol, start, dates[index - 1], snapshot_date, prior.market_code, prior.security_type_code))
                    active[symbol] = (snapshot_date, row)
                else:
                    active[symbol] = (start, row)
            else:
                active[symbol] = (snapshot_date, row)
    for symbol in sorted(active):
        start, row = active[symbol]
        spells.append(ListingSpell(symbol, start, dates[-1], None, row.market_code, row.security_type_code))
    return tuple(sorted(spells, key=lambda item: (item.symbol.encode("utf-8"), item.first_snapshot_date)))


def master_at_formation(rows: Iterable[MasterRow], *, symbol: str, formation_date: str, formation_time_ms: int) -> MasterRow:
    canonical = canonical_equity_code(symbol)
    strict_date(formation_date)
    exact_int(formation_time_ms, "FORMATION_TIME")
    matches = tuple(row for row in rows if row.symbol == canonical and row.snapshot_date == formation_date and row.known_at_ms <= formation_time_ms)
    if len(matches) != 1:
        raise ContractError("MASTER_EXACT_ONE")
    return matches[0]


def causal_adjust_bars(rows: Iterable[DailyBar], *, formation_time_ms: int) -> tuple[CausalAdjustedBar, ...]:
    exact_int(formation_time_ms, "FORMATION_TIME")
    ordered = tuple(sorted(rows, key=lambda row: (row.symbol.encode("utf-8"), row.session_date)))
    result: list[CausalAdjustedBar] = []
    by_symbol: dict[str, list[DailyBar]] = {}
    for row in ordered:
        by_symbol.setdefault(row.symbol, []).append(row)
    for symbol, symbol_rows in by_symbol.items():
        if len({row.session_date for row in symbol_rows}) != len(symbol_rows):
            raise ContractError("DUPLICATE_BAR")
        visible = [row for row in symbol_rows if row.known_at_ms <= formation_time_ms]
        for index, row in enumerate(visible):
            if not row.traded:
                continue
            scale = 1.0
            sources = [row.raw_sha256]
            for later in visible[index + 1 :]:
                if later.adjustment_factor != 1.0:
                    scale *= later.adjustment_factor
                    sources.append(later.raw_sha256)
            if not math.isfinite(scale) or scale <= 0:
                raise ContractError("CAUSAL_SCALE")
            assert row.open is not None and row.high is not None and row.low is not None and row.close is not None
            volume = 0.0 if row.volume is None else row.volume
            result.append(CausalAdjustedBar(
                symbol=symbol,
                session_date=row.session_date,
                formation_time_ms=formation_time_ms,
                raw_open=row.open,
                raw_high=row.high,
                raw_low=row.low,
                raw_close=row.close,
                raw_volume=volume,
                causal_scale=scale,
                adjusted_open=row.open * scale,
                adjusted_high=row.high * scale,
                adjusted_low=row.low * scale,
                adjusted_close=row.close * scale,
                adjusted_volume=volume / scale,
                source_sha256s=tuple(sorted(set(sources))),
            ))
    return tuple(sorted(result, key=lambda row: (row.symbol.encode("utf-8"), row.session_date)))


def validate_pre_service_floor_fixture(*, requested_date: str, status: int, body: bytes) -> None:
    strict_date(requested_date)
    if requested_date >= "2024-07-01" or status != 400:
        raise ContractError("PRE_FLOOR_FIXTURE")
    value = strict_json_object(body)
    if set(value) != {"message"}:
        raise ContractError("PRE_FLOOR_BODY")
    nonempty_text(value["message"], "PRE_FLOOR_MESSAGE")


def _strict_jsonl(raw: bytes) -> list[dict[str, Any]]:
    if not raw or not raw.endswith(b"\n") or b"\r" in raw:
        raise ContractError("JSONL_ENCODING")
    rows: list[dict[str, Any]] = []
    for line in raw[:-1].split(b"\n"):
        rows.append(strict_json_object(line))
    return rows


def load_acquired_run(run_root: Path) -> LoadedRun:
    if not isinstance(run_root, Path) or not run_root.is_dir() or run_root.is_symlink():
        raise ContractError("RUN_ROOT")
    expected_top = {"responses", "query_plan.json", "receipts.jsonl", "acquisition_manifest.json", "summary.json"}
    if {path.name for path in run_root.iterdir()} != expected_top or not (run_root / "responses").is_dir():
        raise ContractError("RUN_TREE")
    plan_document = strict_json_object((run_root / "query_plan.json").read_bytes())
    if set(plan_document) != {"global_http_cap", "logical_query_count", "queries", "query_plan_sha256", "retry_count"}:
        raise ContractError("QUERY_PLAN_KEYS")
    if (
        plan_document["global_http_cap"] != GLOBAL_HTTP_CAP
        or plan_document["logical_query_count"] != len(QUERY_PLANS)
        or plan_document["queries"] != [plan.projection() for plan in QUERY_PLANS]
        or plan_document["query_plan_sha256"] != QUERY_PLAN_SHA256
        or plan_document["retry_count"] != 0
    ):
        raise ContractError("QUERY_PLAN_BINDING")
    manifest = strict_json_object((run_root / "acquisition_manifest.json").read_bytes())
    if set(manifest) != {
        "experiment_id", "http_request_count", "logical_query_count", "page_count",
        "query_plan_sha256", "raw_files", "raw_tree_sha256", "retry_count", "run_id", "schema_version",
    }:
        raise ContractError("MANIFEST_KEYS")
    if manifest["run_id"] != RUN_ID or manifest["schema_version"] != VERSION or manifest["query_plan_sha256"] != QUERY_PLAN_SHA256:
        raise ContractError("MANIFEST_BINDING")
    if manifest["logical_query_count"] != len(QUERY_PLANS) or manifest["retry_count"] != 0:
        raise ContractError("MANIFEST_COUNTS")
    raw_entries = manifest["raw_files"]
    if type(raw_entries) is not list or not raw_entries:
        raise ContractError("RAW_FILES")
    expected_entry_keys = {"bytes", "http_request_ordinal", "logical_query_ordinal", "page_number", "path", "sha256"}
    seen_paths: set[str] = set()
    normalized_entries: list[dict[str, object]] = []
    for value in raw_entries:
        row = _exact_keys(value, expected_entry_keys, "RAW_ENTRY")
        path = row["path"]
        if type(path) is not str or not path.startswith("responses/") or ".." in path.split("/") or "\\" in path or path in seen_paths:
            raise ContractError("RAW_PATH")
        seen_paths.add(path)
        exact_int(row["bytes"], "RAW_BYTES")
        exact_int(row["http_request_ordinal"], "HTTP_ORDINAL", 1)
        exact_int(row["logical_query_ordinal"], "QUERY_ORDINAL", 1)
        exact_int(row["page_number"], "PAGE_NUMBER", 1)
        if type(row["sha256"]) is not str or len(row["sha256"]) != 64:
            raise ContractError("RAW_SHA")
        payload_path = run_root / path
        if not payload_path.is_file() or payload_path.is_symlink():
            raise ContractError("RAW_MISSING")
        payload = payload_path.read_bytes()
        if len(payload) != row["bytes"] or sha256_bytes(payload) != row["sha256"]:
            raise ContractError("RAW_TAMPER")
        normalized_entries.append(dict(row))
    actual_response_paths = {path.relative_to(run_root).as_posix() for path in (run_root / "responses").iterdir() if path.is_file()}
    if actual_response_paths != seen_paths or any(path.is_dir() for path in (run_root / "responses").iterdir()):
        raise ContractError("RAW_BIJECTION")
    expected_tree = sha256_bytes(canonical_json_bytes(sorted(normalized_entries, key=lambda row: str(row["path"]).encode("utf-8"))))
    if manifest["raw_tree_sha256"] != expected_tree:
        raise ContractError("RAW_TREE_SHA")
    receipts = _strict_jsonl((run_root / "receipts.jsonl").read_bytes())
    expected_receipt_keys = {
        "api_key_header_sent", "body_bytes", "body_sha256", "client_received_at_ms", "client_sent_at_ms",
        "http_request_ordinal", "http_status", "logical_query_id", "logical_query_ordinal", "method",
        "page_number", "path", "query_parameters_sha256", "redirect_count",
    }
    if len(receipts) != len(raw_entries) or manifest["http_request_count"] != len(receipts) or manifest["page_count"] != len(receipts):
        raise ContractError("RECEIPT_COUNT")
    pages: list[ParsedPage] = []
    entry_by_path = {str(row["path"]): row for row in normalized_entries}
    for expected_ordinal, value in enumerate(receipts, 1):
        receipt = _exact_keys(value, expected_receipt_keys, "RECEIPT_KEYS")
        if receipt["http_request_ordinal"] != expected_ordinal or receipt["method"] != "GET" or receipt["api_key_header_sent"] is not True or receipt["redirect_count"] != 0:
            raise ContractError("RECEIPT_IDENTITY")
        query_ordinal = exact_int(receipt["logical_query_ordinal"], "QUERY_ORDINAL", 1)
        if query_ordinal > len(QUERY_PLANS):
            raise ContractError("QUERY_ORDINAL")
        plan = QUERY_PLANS[query_ordinal - 1]
        if receipt["logical_query_id"] != plan.query_id:
            raise ContractError("QUERY_ID")
        path = receipt["path"]
        if path not in entry_by_path:
            raise ContractError("RECEIPT_PATH")
        entry = entry_by_path[path]
        if receipt["body_bytes"] != entry["bytes"] or receipt["body_sha256"] != entry["sha256"]:
            raise ContractError("RECEIPT_BODY")
        payload = (run_root / str(path)).read_bytes()
        pages.append(parse_page(
            plan,
            page_number=exact_int(receipt["page_number"], "PAGE_NUMBER", 1),
            status=exact_int(receipt["http_status"], "HTTP_STATUS", 100),
            body=payload,
            received_at_ms=exact_int(receipt["client_received_at_ms"], "RECEIVED_AT"),
        ))
    loaded = merge_pages(pages)
    summary = strict_json_object((run_root / "summary.json").read_bytes())
    if set(summary) != {
        "artifact_state", "calendar_row_count", "daily_bar_row_count", "empirical_authorized",
        "experiment_id", "historical_eligibility_ready", "http_request_count", "logical_query_count",
        "master_row_count", "raw_tree_sha256", "retry_count", "run_id", "strict_eligible_count", "terminal_status",
    }:
        raise ContractError("SUMMARY_KEYS")
    expected_summary = {
        "artifact_state": "JQUANTS_V2_FREE_SOURCE_PROBE_ACQUIRED",
        "calendar_row_count": len(loaded.calendar_days),
        "daily_bar_row_count": len(loaded.bars),
        "empirical_authorized": False,
        "experiment_id": manifest["experiment_id"],
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
    if summary != expected_summary:
        raise ContractError("SUMMARY_BINDING")
    return loaded
