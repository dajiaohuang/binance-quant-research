from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from .contracts import (
    BAR_FIELDS, CALENDAR_FIELDS, MASTER_FIELDS, GLOBAL_HTTP_CAP, MAX_PAGES,
    PAGE_KEY, PLAN_SHA256, QUERY_PLANS, RUN_ID, VERSION,
    CalendarDay, DailyBar, MasterRow, PresenceResolution, ProbeError, QueryPlan,
    canonical_json_bytes, code_text, date_text, exact_int, finite, json_file_bytes, policy_time_ms,
    sha256_bytes, strict_json, symbol_from_code, text,
)


EXPECTED_9433_DATES = ("2025-03-27", "2025-03-28", "2025-03-31", "2025-04-01", "2025-04-02")


@dataclass(frozen=True)
class ParsedPage:
    query_id: str
    page_number: int
    records: tuple[CalendarDay | MasterRow | DailyBar, ...]
    pagination_key: str | None
    raw_sha256: str
    received_at_ms: int


@dataclass(frozen=True)
class LoadedProbe:
    calendar: tuple[CalendarDay, ...]
    masters: tuple[MasterRow, ...]
    bars: tuple[DailyBar, ...]
    page_count: int
    http_count: int
    raw_tree_sha256: str


@dataclass(frozen=True)
class RebuildResult:
    loaded: LoadedProbe
    final_tree_entries: tuple[dict[str, object], ...]
    final_tree_sha256: str


def _keys(value: object, expected: set[str] | frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(expected): raise ProbeError(code)
    return value


def _envelope(raw: bytes) -> tuple[list[Any], str | None]:
    value = strict_json(raw)
    if set(value) not in ({"data"}, {"data", PAGE_KEY}) or type(value["data"]) is not list: raise ProbeError("RESPONSE_ENVELOPE")
    key = value.get(PAGE_KEY)
    if key is not None and (type(key) is not str or not key or len(key)>2048 or any(ord(c)<0x21 or ord(c)>0x7E for c in key)): raise ProbeError("PAGINATION_KEY")
    return value["data"], key


def _nullable(value: object, code: str) -> float | None:
    return None if value is None else finite(value, code)


def _calendar(value: object, raw_sha: str, received: int) -> CalendarDay:
    row=_keys(value,CALENDAR_FIELDS,"CALENDAR_FIELDS"); day=date_text(row["Date"]); policy=policy_time_ms(day,0,0)
    return CalendarDay(day,text(row["HolDiv"],"HOLDIV"),policy,received,max(policy,received),raw_sha)


def _master(value: object, raw_sha: str, received: int) -> MasterRow:
    row=_keys(value,MASTER_FIELDS,"MASTER_FIELDS"); day=date_text(row["Date"]); raw_code=code_text(row["Code"]); policy=policy_time_ms(day,8,0)
    values=[text(row[key],key.upper()) for key in ("CoName","CoNameEn","S17","S17Nm","S33","S33Nm","ScaleCat","Mkt","MktNm","Mrgn","MrgnNm","SecType","SecTypeNm")]
    return MasterRow(day,raw_code,symbol_from_code(raw_code),*values,policy,received,max(policy,received),raw_sha)


def _bar(value: object, raw_sha: str, received: int) -> DailyBar:
    row=_keys(value,BAR_FIELDS,"BAR_FIELDS"); day=date_text(row["Date"]); raw_code=code_text(row["Code"]); policy=policy_time_ms(day,16,30)
    return DailyBar(day,raw_code,symbol_from_code(raw_code),_nullable(row["O"],"O"),_nullable(row["H"],"H"),_nullable(row["L"],"L"),_nullable(row["C"],"C"),_nullable(row["Vo"],"VO"),_nullable(row["Va"],"VA"),finite(row["AdjFactor"],"FACTOR",positive=True),text(row["ExRT"],"EXRT"),_nullable(row["AdjO"],"ADJO"),_nullable(row["AdjH"],"ADJH"),_nullable(row["AdjL"],"ADJL"),_nullable(row["AdjC"],"ADJC"),_nullable(row["AdjVo"],"ADJVO"),policy,received,max(policy,received),raw_sha)


def parse_page(plan: QueryPlan, *, page_number: int, status: int, body: bytes, received_at_ms: int) -> ParsedPage:
    exact_int(page_number,"PAGE",1); exact_int(received_at_ms,"RECEIVED");
    if status != 200: raise ProbeError("HTTP_STATUS")
    data,key=_envelope(body); raw_sha=sha256_bytes(body)
    if plan.path.endswith("calendar"): records=tuple(_calendar(row,raw_sha,received_at_ms) for row in data)
    elif plan.path.endswith("master"): records=tuple(_master(row,raw_sha,received_at_ms) for row in data)
    else: records=tuple(_bar(row,raw_sha,received_at_ms) for row in data)
    for row in records:
        if plan.ordinal==1 and not plan.parameters["from"]<=row.session_date<=plan.parameters["to"]: raise ProbeError("CALENDAR_RANGE")  # type: ignore[union-attr]
        if plan.ordinal==2 and (row.snapshot_date!="2025-03-31" or row.symbol!="9433"): raise ProbeError("MASTER_NORMAL")  # type: ignore[union-attr]
        if plan.ordinal==3 and (row.snapshot_date!="2025-03-31" or row.symbol!="9433"): raise ProbeError("MASTER_NONTRADING_MAPPING")  # type: ignore[union-attr]
        if plan.ordinal==4 and row.session_date!="2025-03-28": raise ProbeError("BARS_DATE")  # type: ignore[union-attr]
        if plan.ordinal==5 and (row.symbol!="9433" or row.session_date not in EXPECTED_9433_DATES): raise ProbeError("BARS_9433_RANGE")  # type: ignore[union-attr]
    return ParsedPage(plan.query_id,page_number,records,key,raw_sha,received_at_ms)


def _dates(start: str,end: str) -> tuple[str,...]:
    current=date.fromisoformat(start); final=date.fromisoformat(end); out=[]
    while current<=final: out.append(current.isoformat()); current+=timedelta(days=1)
    return tuple(out)


def merge_and_validate(pages: Iterable[ParsedPage]) -> LoadedProbe:
    ordered=tuple(pages)
    if not ordered: raise ProbeError("NO_PAGES")
    grouped={plan.query_id:[] for plan in QUERY_PLANS}
    for page in ordered:
        if page.query_id not in grouped: raise ProbeError("QUERY_ID")
        grouped[page.query_id].extend(page.records)
    if any(not grouped[plan.query_id] for plan in QUERY_PLANS): raise ProbeError("QUERY_EMPTY")
    calendar=list(grouped[QUERY_PLANS[0].query_id])
    normal_master=list(grouped[QUERY_PLANS[1].query_id]); mapped_master=list(grouped[QUERY_PLANS[2].query_id])
    all_bars=list(grouped[QUERY_PLANS[3].query_id]); range_bars=list(grouped[QUERY_PLANS[4].query_id])
    if not all(isinstance(row,CalendarDay) for row in calendar): raise ProbeError("CALENDAR_TYPE")
    if not all(isinstance(row,MasterRow) for row in normal_master+mapped_master): raise ProbeError("MASTER_TYPE")
    if not all(isinstance(row,DailyBar) for row in all_bars+range_bars): raise ProbeError("BAR_TYPE")
    calendar.sort(key=lambda x:x.session_date)  # type: ignore[attr-defined]
    if tuple(row.session_date for row in calendar)!=_dates("2025-03-28","2025-03-31"): raise ProbeError("CALENDAR_COVERAGE")
    if len(normal_master)!=1 or len(mapped_master)!=1: raise ProbeError("MASTER_EXACT_ONE")
    master_fields=("snapshot_date","raw_code","symbol","company_name","company_name_en","sector17_code","sector17_name","sector33_code","sector33_name","scale_category","market_code","market_name","margin_code","margin_name","security_type_code","security_type_name")
    if tuple(getattr(normal_master[0],name) for name in master_fields)!=tuple(getattr(mapped_master[0],name) for name in master_fields): raise ProbeError("NONTRADING_MAPPING_MISMATCH")
    if len({(x.session_date,x.symbol) for x in all_bars})!=len(all_bars): raise ProbeError("DUPLICATE_Q04_BAR")  # type: ignore[attr-defined]
    if len({(x.session_date,x.symbol) for x in range_bars})!=len(range_bars): raise ProbeError("DUPLICATE_Q05_BAR")  # type: ignore[attr-defined]
    range_bars.sort(key=lambda x:x.session_date)  # type: ignore[attr-defined]
    if tuple(x.session_date for x in range_bars)!=EXPECTED_9433_DATES: raise ProbeError("Q05_COVERAGE")  # type: ignore[attr-defined]
    bar_fields=("session_date","raw_code","symbol","o","h","low","c","volume","amount","adjustment_factor","ex_right","adjusted_o","adjusted_h","adjusted_low","adjusted_c","adjusted_volume")
    overlap={(x.session_date,x.symbol):x for x in all_bars}  # type: ignore[attr-defined]
    for row in range_bars:
        other=overlap.get((row.session_date,row.symbol))
        if other is not None and tuple(getattr(other,name) for name in bar_fields)!=tuple(getattr(row,name) for name in bar_fields): raise ProbeError("CROSS_QUERY_BAR_MISMATCH")
    split=[x for x in range_bars if x.session_date=="2025-03-28"]  # type: ignore[attr-defined]
    if len(split)!=1 or split[0].adjustment_factor!=0.5 or split[0].ex_right!="1": raise ProbeError("SPLIT_EXPECTATION")
    masters=normal_master
    range_keys={(x.session_date,x.symbol) for x in range_bars}  # type: ignore[attr-defined]
    bars=[x for x in all_bars if (x.session_date,x.symbol) not in range_keys]+range_bars  # type: ignore[attr-defined]
    masters.sort(key=lambda x:(x.snapshot_date,x.symbol))  # type: ignore[attr-defined]
    bars.sort(key=lambda x:(x.session_date,x.symbol))  # type: ignore[attr-defined]
    tree=[{"page":p.page_number,"query_id":p.query_id,"raw_sha256":p.raw_sha256} for p in ordered]
    return LoadedProbe(tuple(calendar),tuple(masters),tuple(bars),len(ordered),len(ordered),sha256_bytes(canonical_json_bytes(tree)))


def listing_presence_for_formal_probe() -> PresenceResolution:
    """The fixed master queries are code-filtered and can never prove membership."""
    return PresenceResolution("UNKNOWN",(),"CODE_FILTERED_MASTER_CANNOT_PROVE_LISTING_PRESENCE")


def master_observed_at(rows:Iterable[MasterRow],*,symbol:str,snapshot_date:str,formation_time_ms:int)->MasterRow:
    matches=tuple(row for row in rows if row.symbol==symbol_from_code(symbol) and row.snapshot_date==snapshot_date and row.available_at_ms<=formation_time_ms)
    if len(matches)!=1: raise ProbeError("MASTER_EXACT_ONE_OBSERVED")
    return matches[0]


def causal_prices(rows:Iterable[DailyBar],*,symbol:str,formation_time_ms:int)->dict[str,tuple[float,float,float,float,float]]:
    selected=sorted((row for row in rows if row.symbol==symbol_from_code(symbol) and row.available_at_ms<=formation_time_ms),key=lambda x:x.session_date)
    if len({row.session_date for row in selected})!=len(selected): raise ProbeError("DUPLICATE_BAR")
    result={}
    for index,row in enumerate(selected):
        if not row.traded: continue
        scale=1.0
        for later in selected[index+1:]: scale*=later.adjustment_factor
        if not (scale>0): raise ProbeError("CAUSAL_SCALE")
        assert row.o is not None and row.h is not None and row.low is not None and row.c is not None
        volume=0.0 if row.volume is None else row.volume
        result[row.session_date]=(row.o*scale,row.h*scale,row.low*scale,row.c*scale,volume/scale)
    return result


def _jsonl(raw: bytes) -> list[dict[str,Any]]:
    if not raw or not raw.endswith(b"\n") or b"\r" in raw: raise ProbeError("JSONL")
    return [strict_json(line) for line in raw[:-1].split(b"\n")]


def trusted_rebuild(staging: Path) -> RebuildResult:
    if not isinstance(staging,Path) or not staging.is_dir() or staging.is_symlink(): raise ProbeError("STAGING")
    expected_top={"responses","query_plan.json","receipts.jsonl","acquisition_manifest.json","summary.json"}
    if {p.name for p in staging.iterdir()}!=expected_top: raise ProbeError("TREE_TOP")
    plan_raw=(staging/"query_plan.json").read_bytes(); plan_doc=strict_json(plan_raw)
    if plan_raw!=json_file_bytes(plan_doc): raise ProbeError("PLAN_CANONICAL")
    expected_plan={"global_http_cap":GLOBAL_HTTP_CAP,"logical_query_count":5,"max_pages_per_query":MAX_PAGES,"plan_sha256":PLAN_SHA256,"queries":[x.projection() for x in QUERY_PLANS],"retry_count":0}
    if plan_doc!=expected_plan: raise ProbeError("PLAN_BINDING")
    manifest_raw=(staging/"acquisition_manifest.json").read_bytes(); manifest=strict_json(manifest_raw); receipts_raw=(staging/"receipts.jsonl").read_bytes(); receipts=_jsonl(receipts_raw)
    if manifest_raw!=json_file_bytes(manifest) or receipts_raw!=b"".join(canonical_json_bytes(row)+b"\n" for row in receipts): raise ProbeError("OUTPUT_CANONICAL")
    if set(manifest)!={"experiment_id","http_request_count","logical_query_count","plan_sha256","raw_files","raw_tree_sha256","retry_count","run_id","version"}: raise ProbeError("MANIFEST_KEYS")
    if manifest["run_id"]!=RUN_ID or manifest["version"]!=VERSION or manifest["plan_sha256"]!=PLAN_SHA256 or manifest["logical_query_count"]!=5 or manifest["retry_count"]!=0: raise ProbeError("MANIFEST_BINDING")
    if not 5<=len(receipts)<=GLOBAL_HTTP_CAP or manifest["http_request_count"]!=len(receipts): raise ProbeError("HTTP_COUNT")
    raw_files=manifest["raw_files"]
    if type(raw_files) is not list or len(raw_files)!=len(receipts): raise ProbeError("RAW_COUNT")
    entry_keys={"bytes","http_ordinal","page_number","path","query_ordinal","sha256"}
    if any(type(row) is not dict or set(row)!=entry_keys for row in raw_files): raise ProbeError("RAW_ENTRY")
    entry_by_path={row["path"]:row for row in raw_files}
    response_paths=tuple((staging/"responses").iterdir())
    if any(not p.is_file() or p.is_symlink() for p in response_paths): raise ProbeError("RAW_FILE_TYPE")
    actual={p.relative_to(staging).as_posix() for p in response_paths}
    if set(entry_by_path)!=actual or len(entry_by_path)!=len(raw_files): raise ProbeError("RAW_BIJECTION")
    pages=[]; previous_query=0; previous_page=0; previous_key=None; previous_sent=None
    expected_receipt_keys={"body_bytes","body_sha256","content_type","http_ordinal","page_number","path","query_id","query_ordinal","request_parameters","request_parameters_sha256","sent_at_ms","received_at_ms","status","redirect_count","pacing_wait_ms"}
    for ordinal,receipt in enumerate(receipts,1):
        if set(receipt)!=expected_receipt_keys or receipt["http_ordinal"]!=ordinal or receipt["redirect_count"]!=0 or receipt["status"]!=200 or receipt["content_type"]!="application/json": raise ProbeError("RECEIPT")
        q=exact_int(receipt["query_ordinal"],"QUERY_ORDINAL",1); page=exact_int(receipt["page_number"],"PAGE",1)
        if q>5 or receipt["query_id"]!=QUERY_PLANS[q-1].query_id: raise ProbeError("QUERY_ORDER")
        if q==previous_query:
            if page!=previous_page+1 or page>MAX_PAGES: raise ProbeError("PAGE_SEQUENCE")
        else:
            if previous_query and previous_key is not None: raise ProbeError("INCOMPLETE_PREVIOUS_QUERY")
            if q!=previous_query+1 or page!=1: raise ProbeError("QUERY_SEQUENCE")
            previous_key=None
        params=receipt["request_parameters"]
        if type(params) is not dict or receipt["request_parameters_sha256"]!=sha256_bytes(canonical_json_bytes(params)): raise ProbeError("PARAMETER_SHA")
        expected=dict(QUERY_PLANS[q-1].parameters)
        if page>1:
            if previous_key is None: raise ProbeError("PAGINATION_MISSING_PRIOR")
            expected[PAGE_KEY]=previous_key
        if params!=expected: raise ProbeError("PAGINATION_CHAIN")
        sent=exact_int(receipt["sent_at_ms"],"SENT"); received=exact_int(receipt["received_at_ms"],"RECEIVED")
        if sent>received: raise ProbeError("CLOCK")
        if previous_sent is not None and sent-previous_sent<13_000: raise ProbeError("RATE_PACING")
        previous_sent=sent
        path=receipt["path"]
        if type(path) is not str or path not in entry_by_path or Path(path).is_absolute() or ".." in Path(path).parts or Path(path).as_posix()!=path: raise ProbeError("RAW_RECEIPT")
        entry=entry_by_path[path]
        if entry["http_ordinal"]!=ordinal or entry["query_ordinal"]!=q or entry["page_number"]!=page: raise ProbeError("RAW_ORDINAL")
        raw_path=staging/path
        if not raw_path.is_file() or raw_path.is_symlink(): raise ProbeError("RAW_FILE_TYPE")
        raw=raw_path.read_bytes()
        if len(raw)!=entry.get("bytes") or sha256_bytes(raw)!=entry.get("sha256") or receipt["body_bytes"]!=len(raw) or receipt["body_sha256"]!=sha256_bytes(raw): raise ProbeError("RAW_HASH")
        parsed=parse_page(QUERY_PLANS[q-1],page_number=page,status=200,body=raw,received_at_ms=received)
        previous_key=parsed.pagination_key; previous_query=q; previous_page=page; pages.append(parsed)
    if previous_query!=5 or previous_key is not None: raise ProbeError("INCOMPLETE_QUERIES")
    raw_entries=sorted(raw_files,key=lambda x:x["path"].encode("utf-8"))
    if manifest["raw_tree_sha256"]!=sha256_bytes(canonical_json_bytes(raw_entries)): raise ProbeError("RAW_TREE")
    loaded=merge_and_validate(pages)
    summary_raw=(staging/"summary.json").read_bytes(); summary=strict_json(summary_raw)
    if summary_raw!=json_file_bytes(summary): raise ProbeError("SUMMARY_CANONICAL")
    expected_summary={"artifact_state":"JQUANTS_V2_FREE_SOURCE_PROBE_ACQUIRED","bar_rows":len(loaded.bars),"calendar_rows":len(loaded.calendar),"empirical_authorized":False,"experiment_id":"exp_20260828_004","historical_eligibility_ready":False,"http_request_count":len(receipts),"logical_query_count":5,"master_rows":len(loaded.masters),"raw_tree_sha256":manifest["raw_tree_sha256"],"retry_count":0,"run_id":RUN_ID,"strict_eligible_count":0,"terminal_status":"NEEDS_MORE_DATA"}
    if summary!=expected_summary: raise ProbeError("SUMMARY")
    files=[]
    for path in sorted((p for p in staging.rglob("*") if p.is_file()),key=lambda p:p.relative_to(staging).as_posix().encode("utf-8")):
        raw=path.read_bytes(); files.append({"bytes":len(raw),"path":path.relative_to(staging).as_posix(),"sha256":sha256_bytes(raw)})
    return RebuildResult(loaded,tuple(files),sha256_bytes(canonical_json_bytes(files)))
