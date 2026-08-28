from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
import os
import stat
from typing import Any, Iterable, Mapping

from .contracts import (
    BAR_FIELDS,
    BOOTSTRAP_CIVIL_DATE_COUNT,
    BOOTSTRAP_FROM,
    BOOTSTRAP_GLOBAL_HTTP_CAP,
    BOOTSTRAP_MONTH_COUNT,
    BOOTSTRAP_PLAN_SHA256,
    BOOTSTRAP_QUERY_PLANS,
    BOOTSTRAP_RUN_ID,
    BOOTSTRAP_SESSION_MAX,
    BOOTSTRAP_SESSION_MIN,
    BOOTSTRAP_TO,
    CALENDAR_FIELDS,
    CALENDAR_PATH,
    EXP005_Q04_RAW_BYTES,
    EXP005_Q04_RAW_RELATIVE,
    EXP005_Q04_RAW_SHA256,
    EXP005_Q04_RECEIPT_BYTES,
    EXP005_Q04_RECEIPT_RELATIVE,
    EXP005_Q04_RECEIPT_SHA256,
    EXP006_CLOSURE_RELATIVE,
    EXP006_CLOSURE_SHA256,
    LAST_BAR_DATE,
    MAX_PAGES_PER_QUERY,
    MIN_SEND_SPACING_NS,
    PAGE_KEY,
    PREMIUM_BAR_FIELDS,
    CalendarDay,
    ContractError,
    DailyBar,
    MonthPlan,
    QueryPlan,
    canonical_json_bytes,
    date_text,
    exact_int,
    finite,
    inclusive_dates,
    policy_time_ms,
    sha256_bytes,
    strict_json,
    symbol_from_code,
    text,
    validate_clock_domain,
)


RECEIPT_REQUIRED = frozenset(
    (
        "run_id",
        "schema_version",
        "api_host",
        "cap_bytes",
        "clock_domain_id",
        "guard_base_monotonic_ns",
        "deadline_monotonic_ns",
        "pre_wait_monotonic_ns",
        "requested_wait_ns",
        "post_wait_monotonic_ns",
        "previous_send_monotonic_ns",
        "send_monotonic_ns",
        "spacing_ns",
        "sent_at_utc",
        "received_at_utc",
        "received_at_ms",
        "request_ordinal",
        "query_ordinal",
        "query_id",
        "page_number",
        "path",
        "parameters",
        "status",
        "content_type",
        "redirected",
        "body_bytes",
        "body_sha256",
        "raw_relative_path",
        "receipt_relative_path",
    )
)


@dataclass(frozen=True)
class ParsedPage:
    query_id: str
    page_number: int
    received_at_ms: int
    raw_sha256: str
    next_key: str | None
    calendar: tuple[CalendarDay, ...]
    bars: tuple[DailyBar, ...]


@dataclass(frozen=True)
class BootstrapBundle:
    run_id: str
    clock_domain_id: str
    calendar: tuple[CalendarDay, ...]
    session_dates: tuple[str, ...]
    month_plans: tuple[MonthPlan, ...]
    first_bars: tuple[DailyBar, ...]
    last_bars: tuple[DailyBar, ...]
    receipts: tuple[Mapping[str, Any], ...]
    raw_tree_sha256: str


def _exact_keys(value: object, expected: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(expected):
        raise ContractError(code)
    return value


def _nullable_number(value: object, code: str) -> float | None:
    return None if value is None else finite(value, code)


def _pagination(envelope: dict[str, Any]) -> str | None:
    value = envelope.get(PAGE_KEY)
    if value is None:
        return None
    return text(value, "PAGINATION_KEY")


def parse_page(plan: QueryPlan, page_number: int, received_at_ms: int, raw: bytes) -> ParsedPage:
    exact_int(page_number, "PAGE_NUMBER", 1)
    exact_int(received_at_ms, "RECEIVED")
    raw_sha = sha256_bytes(raw)
    envelope = strict_json(raw)
    allowed = frozenset(("data", PAGE_KEY)) if PAGE_KEY in envelope else frozenset(("data",))
    _exact_keys(envelope, allowed, "ENVELOPE_SCHEMA")
    rows = envelope["data"]
    if type(rows) is not list:
        raise ContractError("DATA_ARRAY")
    next_key = _pagination(envelope)
    if plan.path == CALENDAR_PATH:
        output: list[CalendarDay] = []
        for row in rows:
            item = _exact_keys(row, CALENDAR_FIELDS, "CALENDAR_SCHEMA")
            session_date = date_text(item["Date"])
            output.append(
                CalendarDay(
                    session_date=session_date,
                    holiday_division=text(item["HolDiv"], "HOLDIV"),
                    policy_observation_ms=policy_time_ms(session_date, 0, 0),
                    received_at_ms=received_at_ms,
                    available_at_ms=max(policy_time_ms(session_date, 0, 0), received_at_ms),
                    raw_sha256=raw_sha,
                )
            )
        return ParsedPage(plan.query_id, page_number, received_at_ms, raw_sha, next_key, tuple(output), ())

    output_bars: list[DailyBar] = []
    expected_date = plan.parameters["date"]
    for row in rows:
        item = _exact_keys(row, BAR_FIELDS, "FREE18_SCHEMA")
        if set(item) & PREMIUM_BAR_FIELDS:
            raise ContractError("PREMIUM_FIELD")
        session_date = date_text(item["Date"])
        if session_date != expected_date:
            raise ContractError("BOUNDARY_DATE")
        raw_code = text(item["Code"], "CODE")
        received_policy = policy_time_ms(session_date, 16, 30)
        output_bars.append(
            DailyBar(
                session_date=session_date,
                raw_code=raw_code,
                symbol=symbol_from_code(raw_code),
                o=_nullable_number(item["O"], "O"),
                h=_nullable_number(item["H"], "H"),
                low=_nullable_number(item["L"], "L"),
                c=_nullable_number(item["C"], "C"),
                upper_limit=text(item["UL"], "UL"),
                lower_limit=text(item["LL"], "LL"),
                volume=_nullable_number(item["Vo"], "VO"),
                amount=_nullable_number(item["Va"], "VA"),
                adjustment_factor=finite(item["AdjFactor"], "FACTOR", positive=True),
                adjusted_o=_nullable_number(item["AdjO"], "ADJO"),
                adjusted_h=_nullable_number(item["AdjH"], "ADJH"),
                adjusted_low=_nullable_number(item["AdjL"], "ADJL"),
                adjusted_c=_nullable_number(item["AdjC"], "ADJC"),
                adjusted_volume=_nullable_number(item["AdjVo"], "ADJVO"),
                market_cap=_nullable_number(item["MktCap"], "MKTCAP"),
                ex_right_type=None if item["ExRT"] is None else text(item["ExRT"], "EXRT"),
                policy_observation_ms=received_policy,
                received_at_ms=received_at_ms,
                available_at_ms=max(received_policy, received_at_ms),
                raw_sha256=raw_sha,
            )
        )
    return ParsedPage(plan.query_id, page_number, received_at_ms, raw_sha, next_key, (), tuple(output_bars))


def _merge_pages(pages: Iterable[ParsedPage]) -> dict[str, list[ParsedPage]]:
    grouped: dict[str, list[ParsedPage]] = defaultdict(list)
    for page in pages:
        grouped[page.query_id].append(page)
    expected_ids = {item.query_id for item in BOOTSTRAP_QUERY_PLANS}
    if set(grouped) != expected_ids:
        raise ContractError("BOOTSTRAP_QUERY_COVERAGE")
    for query_id, group in grouped.items():
        group.sort(key=lambda item: item.page_number)
        if len(group) > MAX_PAGES_PER_QUERY or [item.page_number for item in group] != list(range(1, len(group) + 1)):
            raise ContractError("PAGE_SEQUENCE")
        for index, page in enumerate(group):
            if (index + 1 < len(group)) != (page.next_key is not None):
                raise ContractError("PAGINATION_CHAIN")
    if sum(map(len, grouped.values())) > BOOTSTRAP_GLOBAL_HTTP_CAP:
        raise ContractError("GLOBAL_HTTP_CAP")
    return grouped


def build_month_plans(session_dates: tuple[str, ...]) -> tuple[MonthPlan, ...]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for session_date in session_dates:
        grouped[session_date[:7]].append(session_date)
    session_sha = sha256_bytes(canonical_json_bytes(list(session_dates)))
    output = tuple(
        MonthPlan(month, tuple(values), BOOTSTRAP_PLAN_SHA256, session_sha)
        for month, values in sorted(grouped.items())
    )
    if len(output) != BOOTSTRAP_MONTH_COUNT:
        raise ContractError("MONTH_COUNT")
    return output


def merge_bootstrap(pages: Iterable[ParsedPage], receipts: Iterable[Mapping[str, Any]]) -> BootstrapBundle:
    grouped = _merge_pages(pages)
    calendar = tuple(row for page in grouped["Q01_CALENDAR"] for row in page.calendar)
    expected_dates = inclusive_dates(BOOTSTRAP_FROM, BOOTSTRAP_TO)
    if len(calendar) != BOOTSTRAP_CIVIL_DATE_COUNT or tuple(row.session_date for row in calendar) != expected_dates:
        raise ContractError("CALENDAR_COVERAGE")
    session_dates = tuple(row.session_date for row in calendar if row.is_tse_session)
    if not BOOTSTRAP_SESSION_MIN <= len(session_dates) <= BOOTSTRAP_SESSION_MAX:
        raise ContractError("SESSION_COUNT")
    month_plans = build_month_plans(session_dates)
    first_bars = tuple(row for page in grouped["Q02_BARS_FIRST"] for row in page.bars)
    last_bars = tuple(row for page in grouped["Q03_BARS_LAST"] for row in page.bars)
    if not first_bars or not last_bars:
        raise ContractError("BOUNDARY_EMPTY")
    for rows in (first_bars, last_bars):
        identities = [(item.session_date, item.raw_code) for item in rows]
        if identities != sorted(set(identities)):
            raise ContractError("BAR_ORDER_OR_DUPLICATE")
    checked_receipts = tuple(receipts)
    if len(checked_receipts) != sum(map(len, grouped.values())):
        raise ContractError("RECEIPT_COUNT")
    if any(type(item) is not dict for item in checked_receipts):
        raise ContractError("RECEIPT_TYPE")
    replay = replay_monotonic_receipts(checked_receipts)
    raw_entries = sorted(
        {
            (str(item["raw_relative_path"]), str(item["body_sha256"]), int(item["body_bytes"]))
            for item in checked_receipts
        }
    )
    return BootstrapBundle(
        run_id=replay["run_id"],
        clock_domain_id=replay["clock_domain_id"],
        calendar=calendar,
        session_dates=session_dates,
        month_plans=month_plans,
        first_bars=first_bars,
        last_bars=last_bars,
        receipts=checked_receipts,
        raw_tree_sha256=sha256_bytes(canonical_json_bytes(raw_entries)),
    )


def replay_monotonic_receipts(receipts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(receipts)
    if not rows:
        raise ContractError("NO_RECEIPTS")
    rows.sort(key=lambda item: exact_int(item.get("request_ordinal"), "REQUEST_ORDINAL", 1))
    if [item["request_ordinal"] for item in rows] != list(range(1, len(rows) + 1)):
        raise ContractError("REQUEST_SEQUENCE")
    if len(rows) > BOOTSTRAP_GLOBAL_HTTP_CAP:
        raise ContractError("GLOBAL_HTTP_CAP")
    run_id = text(rows[0].get("run_id"), "RUN_ID")
    clock_domain = validate_clock_domain(rows[0].get("clock_domain_id"))
    guard_base = exact_int(rows[0].get("guard_base_monotonic_ns"), "GUARD_BASE")
    previous_send: int | None = None
    sends: list[int] = []
    for index, raw in enumerate(rows):
        row = _exact_keys(raw, RECEIPT_REQUIRED, "RECEIPT_SCHEMA")
        if row["run_id"] != run_id or row["clock_domain_id"] != clock_domain:
            raise ContractError("CLOCK_DOMAIN_MISMATCH")
        if exact_int(row["guard_base_monotonic_ns"], "GUARD_BASE") != guard_base:
            raise ContractError("GUARD_BASE_MISMATCH")
        spacing = exact_int(row["spacing_ns"], "SPACING", 1)
        if spacing != MIN_SEND_SPACING_NS:
            raise ContractError("SPACING_POLICY")
        deadline = exact_int(row["deadline_monotonic_ns"], "DEADLINE")
        expected_previous = None if index == 0 else previous_send
        if row["previous_send_monotonic_ns"] != expected_previous:
            raise ContractError("PREVIOUS_SEND")
        expected_deadline = (guard_base if expected_previous is None else expected_previous) + spacing
        if deadline != expected_deadline:
            raise ContractError("DEADLINE")
        pre_wait = exact_int(row["pre_wait_monotonic_ns"], "PRE_WAIT")
        requested = exact_int(row["requested_wait_ns"], "REQUESTED_WAIT")
        if requested != max(0, deadline - pre_wait):
            raise ContractError("REQUESTED_WAIT")
        post_wait = exact_int(row["post_wait_monotonic_ns"], "POST_WAIT")
        sent = exact_int(row["send_monotonic_ns"], "SEND")
        if post_wait < deadline or sent < post_wait or sent - (guard_base if index == 0 else previous_send) < spacing:
            raise ContractError("SPACING_SHORT")
        previous_send = sent
        sends.append(sent)
    validate_rolling_five_per_minute(sends)
    return {
        "clock_domain_id": clock_domain,
        "first_request_full_cooldown": True,
        "request_count": len(rows),
        "run_id": run_id,
        "spacing_ns": MIN_SEND_SPACING_NS,
        "verdict": "PASS",
    }


def validate_rolling_five_per_minute(send_monotonic_ns: Iterable[int]) -> None:
    rolling: deque[int] = deque()
    previous: int | None = None
    for raw in send_monotonic_ns:
        sent = exact_int(raw, "SEND")
        if previous is not None and sent < previous:
            raise ContractError("SEND_ORDER")
        previous = sent
        rolling.append(sent)
        while rolling and sent - rolling[0] >= 60_000_000_000:
            rolling.popleft()
        if len(rolling) > 5:
            raise ContractError("ROLLING_FIVE_PER_MINUTE")


def _safe_file(root: Path, relative: object) -> Path:
    if type(relative) is not str or not relative or "\\" in relative:
        raise ContractError("RELATIVE_PATH")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or rel.as_posix() != relative:
        raise ContractError("RELATIVE_PATH")
    path = root / rel
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ContractError("FILE_MISSING") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or attributes & 0x400:
        raise ContractError("FILE_REPARSE")
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved_root != resolved and resolved_root not in resolved.parents:
        raise ContractError("PATH_ESCAPE")
    return path


def _plan_from_projection(value: object, expected: QueryPlan) -> None:
    if value != expected.projection():
        raise ContractError("QUERY_PLAN_MISMATCH")


def load_bootstrap_tree(root: Path, *, require_manifest: bool = True) -> BootstrapBundle:
    if not root.is_dir() or root.is_symlink():
        raise ContractError("BOOTSTRAP_ROOT")
    query_plan_path = _safe_file(root, "query_plan.json")
    query_plan_body = query_plan_path.read_bytes()
    query_doc = strict_json(query_plan_body)
    if set(query_doc) != {"plan_sha256", "queries"} or query_doc["plan_sha256"] != BOOTSTRAP_PLAN_SHA256:
        raise ContractError("QUERY_PLAN_SCHEMA")
    if type(query_doc["queries"]) is not list or len(query_doc["queries"]) != len(BOOTSTRAP_QUERY_PLANS):
        raise ContractError("QUERY_PLAN_COUNT")
    for value, expected in zip(query_doc["queries"], BOOTSTRAP_QUERY_PLANS, strict=True):
        _plan_from_projection(value, expected)

    receipt_dir = root / "response_receipts"
    if not receipt_dir.is_dir() or receipt_dir.is_symlink():
        raise ContractError("RECEIPT_DIRECTORY")
    receipt_paths = sorted(receipt_dir.glob("*.receipt.json"))
    if not receipt_paths or len(receipt_paths) > BOOTSTRAP_GLOBAL_HTTP_CAP:
        raise ContractError("RECEIPT_COUNT")
    receipts: list[dict[str, Any]] = []
    pages: list[ParsedPage] = []
    plans = {item.query_id: item for item in BOOTSTRAP_QUERY_PLANS}
    next_keys: dict[str, str | None] = {item.query_id: None for item in BOOTSTRAP_QUERY_PLANS}
    page_counts: dict[str, int] = defaultdict(int)
    for request_ordinal, path in enumerate(receipt_paths, 1):
        relative_receipt = path.relative_to(root).as_posix()
        safe_path = _safe_file(root, relative_receipt)
        receipt = strict_json(safe_path.read_bytes())
        _exact_keys(receipt, RECEIPT_REQUIRED, "RECEIPT_SCHEMA")
        if receipt["schema_version"] != "JQUANTS_V2_BARS_MONTHLY_RECEIPT_V1" or receipt["api_host"] != "api.jquants.com":
            raise ContractError("RECEIPT_AUTHORITY")
        if receipt["run_id"] != BOOTSTRAP_RUN_ID or receipt["request_ordinal"] != request_ordinal:
            raise ContractError("REQUEST_SEQUENCE")
        query_id = receipt["query_id"]
        if query_id not in plans:
            raise ContractError("QUERY_ID")
        plan = plans[query_id]
        page_counts[query_id] += 1
        page_number = page_counts[query_id]
        if receipt["query_ordinal"] != plan.ordinal or receipt["page_number"] != page_number or receipt["path"] != plan.path:
            raise ContractError("RECEIPT_BINDING")
        expected_parameters = dict(plan.parameters)
        if page_number > 1:
            prior = next_keys[query_id]
            if prior is None:
                raise ContractError("PAGINATION_WITHOUT_PRIOR")
            expected_parameters[PAGE_KEY] = prior
        if receipt["parameters"] != expected_parameters or receipt["cap_bytes"] != plan.cap_bytes:
            raise ContractError("PARAMETER_BINDING")
        expected_raw = f"responses/{plan.ordinal:02d}_{query_id}_page_{page_number:04d}.json"
        expected_receipt = f"response_receipts/{request_ordinal:04d}_{plan.ordinal:02d}_{query_id}_page_{page_number:04d}.receipt.json"
        if receipt["raw_relative_path"] != expected_raw or receipt["receipt_relative_path"] != expected_receipt or relative_receipt != expected_receipt:
            raise ContractError("RECEIPT_PATH_BINDING")
        raw = _safe_file(root, expected_raw).read_bytes()
        if len(raw) != receipt["body_bytes"] or sha256_bytes(raw) != receipt["body_sha256"]:
            raise ContractError("RAW_HASH")
        if receipt["redirected"] is not False or receipt["status"] != 200 or receipt["content_type"] not in ("application/json", "application/problem+json"):
            raise ContractError("HTTP_RECEIPT")
        parsed = parse_page(plan, page_number, receipt["received_at_ms"], raw)
        next_keys[query_id] = parsed.next_key
        pages.append(parsed)
        receipts.append(receipt)
    if any(next_keys.values()):
        raise ContractError("PAGINATION_INCOMPLETE")
    bundle = merge_bootstrap(pages, receipts)
    if require_manifest:
        verify_acquisition_manifest(root, bundle)
    return bundle


def verify_acquisition_manifest(root: Path, bundle: BootstrapBundle) -> None:
    manifest = strict_json(_safe_file(root, "acquisition_manifest.json").read_bytes())
    if set(manifest) != {"bootstrap_plan_sha256", "files", "raw_tree_sha256", "run_id", "status"}:
        raise ContractError("ACQUISITION_MANIFEST_SCHEMA")
    if (
        manifest["bootstrap_plan_sha256"] != BOOTSTRAP_PLAN_SHA256
        or manifest["raw_tree_sha256"] != bundle.raw_tree_sha256
        or manifest["run_id"] != BOOTSTRAP_RUN_ID
        or manifest["status"] != "SOURCE_BOUND_BOOTSTRAP_VALIDATED"
        or type(manifest["files"]) is not list
    ):
        raise ContractError("ACQUISITION_MANIFEST_BINDING")
    seen: set[str] = set()
    for entry in manifest["files"]:
        row = _exact_keys(entry, frozenset(("bytes", "relative_path", "sha256")), "ACQUISITION_FILE_SCHEMA")
        relative = row["relative_path"]
        if relative in seen or relative == "acquisition_manifest.json":
            raise ContractError("ACQUISITION_FILE_DUPLICATE")
        seen.add(relative)
        body = _safe_file(root, relative).read_bytes()
        if len(body) != row["bytes"] or sha256_bytes(body) != row["sha256"]:
            raise ContractError("ACQUISITION_FILE_HASH")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "acquisition_manifest.json"
    }
    if actual != seen:
        raise ContractError("ACQUISITION_FILE_SET")


def verify_reuse_source(repo_root: Path) -> dict[str, Any]:
    checks = (
        (EXP005_Q04_RAW_RELATIVE, EXP005_Q04_RAW_BYTES, EXP005_Q04_RAW_SHA256),
        (EXP005_Q04_RECEIPT_RELATIVE, EXP005_Q04_RECEIPT_BYTES, EXP005_Q04_RECEIPT_SHA256),
        (EXP006_CLOSURE_RELATIVE, None, EXP006_CLOSURE_SHA256),
    )
    evidence: list[dict[str, Any]] = []
    for relative, expected_size, expected_sha in checks:
        path = (repo_root / relative).resolve(strict=True)
        if repo_root.resolve() not in path.parents:
            raise ContractError("REUSE_PATH")
        body = path.read_bytes()
        if (expected_size is not None and len(body) != expected_size) or sha256_bytes(body) != expected_sha:
            raise ContractError("REUSE_HASH")
        evidence.append({"bytes": len(body), "relative_path": relative, "sha256": expected_sha})
    raw = (repo_root / EXP005_Q04_RAW_RELATIVE).read_bytes()
    plan = QueryPlan(1, "REUSE_2025_03_28", "/v2/equities/bars/daily", {"date": "2025-03-28"}, "SOURCE_BOUND_REUSE", 67_108_864)
    parsed = parse_page(plan, 1, 0, raw)
    if not parsed.bars:
        raise ContractError("REUSE_EMPTY")
    return {
        "bars": len(parsed.bars),
        "date": "2025-03-28",
        "evidence": evidence,
        "mode": "SOURCE_BOUND_POINTER_NO_COPY",
        "raw_sha256": parsed.raw_sha256,
    }


def bar_summary(rows: tuple[DailyBar, ...]) -> dict[str, Any]:
    traded = sum(item.traded for item in rows)
    return {
        "date": rows[0].session_date,
        "first_code": rows[0].raw_code,
        "last_code": rows[-1].raw_code,
        "row_count": len(rows),
        "traded_count": traded,
        "null_bar_count": len(rows) - traded,
        "source_raw_sha256": rows[0].raw_sha256,
    }


def bundle_summary(bundle: BootstrapBundle) -> dict[str, Any]:
    return {
        "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
        "calendar": {
            "civil_date_count": len(bundle.calendar),
            "first_date": bundle.calendar[0].session_date,
            "last_date": bundle.calendar[-1].session_date,
            "session_count": len(bundle.session_dates),
            "session_list_sha256": sha256_bytes(canonical_json_bytes(list(bundle.session_dates))),
        },
        "clock_domain_id": bundle.clock_domain_id,
        "edge_days": [bar_summary(bundle.first_bars), bar_summary(bundle.last_bars)],
        "month_count": len(bundle.month_plans),
        "raw_tree_sha256": bundle.raw_tree_sha256,
        "request_count": len(bundle.receipts),
        "run_id": bundle.run_id,
    }
