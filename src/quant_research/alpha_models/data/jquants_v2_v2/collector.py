from __future__ import annotations

from dataclasses import dataclass
import argparse
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contracts import (
    API_BASE, API_HOST, API_KEY_ENV, EXPERIMENT_ID, GLOBAL_HTTP_CAP, MAX_PAGES,
    PAGE_KEY, PLAN_SHA256, QUERY_PLANS, RUN_ID, VERSION,
    ProbeError, QueryPlan, canonical_json_bytes, json_file_bytes, sha256_bytes,
    strict_json, validate_key,
)
from .loader import merge_and_validate, parse_page, trusted_rebuild


MIN_REQUEST_SPACING_SECONDS = 13.0
EXPECTED_FREEZE_RELATIVE = Path("experiments/exp_20260828_003/artifacts/expected_freeze_manifest.json")
HEX64 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class CollectorFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        safe=code if type(code) is str and code and code.isascii() else "COLLECTOR_FAILURE"
        super().__init__(safe); self.code=safe


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    content_type: str
    body: bytes
    final_url: str
    redirect_count: int=0


class Transport(Protocol):
    def __call__(self,url:str,api_key:str,cap_bytes:int)->HTTPResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None  # type: ignore[no-untyped-def]


def _read(response,cap:int)->bytes:  # type: ignore[no-untyped-def]
    raw=response.read(cap+1)
    if len(raw)>cap: raise CollectorFailure("RESPONSE_CAP")
    return raw


def urllib_transport(url:str,api_key:str,cap_bytes:int)->HTTPResponse:
    validate_key(api_key); request=Request(url,method="GET",headers={"x-api-key":api_key}); opener=build_opener(_NoRedirect())
    try:
        with opener.open(request,timeout=30.0) as response:
            return HTTPResponse(int(response.status),response.headers.get("Content-Type",""),_read(response,cap_bytes),response.geturl(),0)
    except HTTPError as exc:
        return HTTPResponse(int(exc.code),exc.headers.get("Content-Type","") if exc.headers else "",_read(exc,cap_bytes),url,0)
    except (URLError,TimeoutError,OSError) as exc: raise CollectorFailure("TRANSPORT") from exc


def _url(plan:QueryPlan,key:str|None)->tuple[str,dict[str,str]]:
    params=dict(plan.parameters)
    if key is not None:
        if type(key) is not str or not key: raise CollectorFailure("PAGE_KEY")
        params[PAGE_KEY]=key
    pairs=list(params.items()); url=f"{API_BASE}{plan.path}?{urlencode(pairs)}"; parsed=urlsplit(url)
    if parsed.scheme!="https" or parsed.hostname!=API_HOST or parsed.port not in (None,443) or parsed.username is not None or parsed.password is not None or parsed.path!=plan.path or parsed.fragment: raise CollectorFailure("URL")
    if parse_qsl(parsed.query,keep_blank_values=True,strict_parsing=True)!=pairs: raise CollectorFailure("QUERY_MUTATION")
    return url,params


def _ordinary(path:Path)->bool:
    try: info=os.lstat(path)
    except OSError: return False
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not (getattr(info,"st_file_attributes",0)&getattr(stat,"FILE_ATTRIBUTE_REPARSE_POINT",0))


def verify_expected_freeze(repo_root:Path,expected_sha256:str)->dict[str,object]:
    if type(expected_sha256) is not str or HEX64.fullmatch(expected_sha256) is None: raise CollectorFailure("FREEZE_EXPECTED_SHA")
    path=repo_root/EXPECTED_FREEZE_RELATIVE
    if not _ordinary(path): raise CollectorFailure("FREEZE_PATH")
    raw=path.read_bytes()
    if sha256_bytes(raw)!=expected_sha256: raise CollectorFailure("FREEZE_MANIFEST_SHA")
    manifest=strict_json(raw)
    if raw!=json_file_bytes(manifest): raise CollectorFailure("FREEZE_CANONICAL")
    if set(manifest)!={"files","schema_version"} or manifest["schema_version"]!="JQUANTS_V2_V2_EXTERNAL_FREEZE_V1" or type(manifest["files"]) is not list: raise CollectorFailure("FREEZE_SCHEMA")
    seen=set()
    for entry in manifest["files"]:
        if type(entry) is not dict or set(entry)!={"bytes","path","sha256"} or type(entry["bytes"]) is not int or entry["bytes"]<0 or type(entry["sha256"]) is not str or HEX64.fullmatch(entry["sha256"]) is None: raise CollectorFailure("FREEZE_ENTRY")
        rel=entry["path"]
        if type(rel) is not str or rel.startswith(("/","\\")) or ".." in rel.replace("\\","/").split("/") or rel in seen: raise CollectorFailure("FREEZE_RELATIVE")
        seen.add(rel); target=repo_root/rel
        if not _ordinary(target): raise CollectorFailure("SOURCE_PATH")
        body=target.read_bytes()
        if len(body)!=entry["bytes"] or sha256_bytes(body)!=entry["sha256"]: raise CollectorFailure("SOURCE_DRIFT")
    required={
        "src/quant_research/alpha_models/data/jquants_v2_v2/__init__.py",
        "src/quant_research/alpha_models/data/jquants_v2_v2/contracts.py",
        "src/quant_research/alpha_models/data/jquants_v2_v2/collector.py",
        "src/quant_research/alpha_models/data/jquants_v2_v2/loader.py",
        "src/quant_research/alpha_models/data/jquants_v2_v2/adapters.py",
        "src/quant_research/alpha_models/data/jquants_v2_v2/launcher.ps1",
        "tests/test_jquants_v2_v2.py",
        "experiments/exp_20260828_003/artifacts/source_contract.json",
        "experiments/exp_20260828_003/artifacts/schema.json",
        "experiments/exp_20260828_003/parameters.json",
    }
    if seen!=required: raise CollectorFailure("FREEZE_FILESET")
    return {"manifest_sha256":expected_sha256,"files":len(seen)}


def _atomic(directory:Path,name:str,payload:bytes)->Path:
    target=directory/name
    if target.exists() or target.is_symlink(): raise CollectorFailure("OUTPUT_EXISTS")
    temp=directory/f".{name}.{os.getpid()}.{time.time_ns()}.tmp"
    if os.path.splitdrive(str(target.absolute()))[0].casefold()!=os.path.splitdrive(str(temp.absolute()))[0].casefold(): raise CollectorFailure("VOLUME")
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,"wb") as handle: handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp,target)
    except Exception:
        try: temp.unlink(missing_ok=True)
        except OSError: pass
        raise
    return target


def _jsonl(rows:list[dict[str,object]])->bytes: return b"".join(canonical_json_bytes(row)+b"\n" for row in rows)


def collect_pages(*,api_key:str,transport:Transport,utc_clock:Callable[[],int],monotonic:Callable[[],float],sleeper:Callable[[float],None],staging:Path)->tuple[list[object],list[dict[str,object]],list[dict[str,object]]]:
    validate_key(api_key); responses=staging/"responses"; responses.mkdir(exist_ok=False)
    pages=[]; receipts=[]; entries=[]; http_ordinal=0; last_send:float|None=None
    for plan in QUERY_PLANS:
        page=1; key=None; seen=set()
        while True:
            if page>MAX_PAGES: raise CollectorFailure("PAGE_CAP")
            if http_ordinal>=GLOBAL_HTTP_CAP: raise CollectorFailure("HTTP_CAP")
            waited=0.0
            if last_send is not None:
                waited=max(0.0,MIN_REQUEST_SPACING_SECONDS-(monotonic()-last_send))
                if waited>0: sleeper(waited)
                if monotonic()-last_send<MIN_REQUEST_SPACING_SECONDS: raise CollectorFailure("RATE_PACING")
            request_url,params=_url(plan,key); send_monotonic=monotonic(); last_send=send_monotonic
            sent=utc_clock(); response=transport(request_url,api_key,plan.cap_bytes); received=utc_clock(); http_ordinal+=1
            if type(response) is not HTTPResponse or response.final_url!=request_url or response.redirect_count!=0 or 300<=response.status<400: raise CollectorFailure("REDIRECT")
            content_type=response.content_type.split(";",1)[0].strip().lower()
            if content_type!="application/json" or len(response.body)>plan.cap_bytes: raise CollectorFailure("RESPONSE")
            parsed=parse_page(plan,page_number=page,status=response.status,body=response.body,received_at_ms=received)
            rel=f"responses/{plan.ordinal:02d}_{plan.query_id}_page_{page:04d}.json"; _atomic(responses,Path(rel).name,response.body); digest=sha256_bytes(response.body)
            entry={"bytes":len(response.body),"http_ordinal":http_ordinal,"page_number":page,"path":rel,"query_ordinal":plan.ordinal,"sha256":digest}; entries.append(entry)
            receipts.append({"body_bytes":len(response.body),"body_sha256":digest,"content_type":"application/json","http_ordinal":http_ordinal,"page_number":page,"path":rel,"query_id":plan.query_id,"query_ordinal":plan.ordinal,"request_parameters":params,"request_parameters_sha256":sha256_bytes(canonical_json_bytes(params)),"sent_at_ms":sent,"received_at_ms":received,"status":response.status,"redirect_count":0,"pacing_wait_ms":int(round(waited*1000))})
            pages.append(parsed); next_key=parsed.pagination_key
            if next_key is None: break
            if next_key in seen: raise CollectorFailure("PAGE_LOOP")
            seen.add(next_key); key=next_key; page+=1
    return pages,receipts,entries


def collect_and_publish(*,repo_root:Path,expected_freeze_sha256:str,api_key:str,transport:Transport=urllib_transport,utc_clock:Callable[[],int]=lambda:time.time_ns()//1_000_000,monotonic:Callable[[],float]=time.monotonic,sleeper:Callable[[float],None]=time.sleep,before_promotion:Callable[[],None]|None=None)->dict[str,object]:
    repo_root=Path(os.path.abspath(repo_root)); start_binding=verify_expected_freeze(repo_root,expected_freeze_sha256); validate_key(api_key)
    parent=repo_root/"data/raw/jquants_v2_v2/runs"; parent.mkdir(parents=True,exist_ok=True)
    current=repo_root
    for part in ("data","raw","jquants_v2_v2","runs"):
        current=current/part
        try: info=os.lstat(current)
        except OSError as exc: raise CollectorFailure("INFRASTRUCTURE") from exc
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or (getattr(info,"st_file_attributes",0)&getattr(stat,"FILE_ATTRIBUTE_REPARSE_POINT",0)): raise CollectorFailure("INFRASTRUCTURE")
    final=parent/RUN_ID; staging=parent/f".{RUN_ID}.staging"; control=parent/f".{RUN_ID}.control"
    if final.exists() or staging.exists() or control.exists(): raise CollectorFailure("PREEXISTENCE")
    control.mkdir(); _atomic(control,"lease.json",json_file_bytes({"experiment_id":EXPERIMENT_ID,"expected_freeze_manifest_sha256":expected_freeze_sha256,"run_id":RUN_ID}))
    try:
        staging.mkdir(); pages,receipts,entries=collect_pages(api_key=api_key,transport=transport,utc_clock=utc_clock,monotonic=monotonic,sleeper=sleeper,staging=staging); loaded=merge_and_validate(pages)
        raw_entries=sorted(entries,key=lambda x:str(x["path"]).encode("utf-8")); raw_tree=sha256_bytes(canonical_json_bytes(raw_entries))
        _atomic(staging,"query_plan.json",json_file_bytes({"global_http_cap":GLOBAL_HTTP_CAP,"logical_query_count":5,"max_pages_per_query":MAX_PAGES,"plan_sha256":PLAN_SHA256,"queries":[x.projection() for x in QUERY_PLANS],"retry_count":0}))
        _atomic(staging,"receipts.jsonl",_jsonl(receipts))
        _atomic(staging,"acquisition_manifest.json",json_file_bytes({"experiment_id":EXPERIMENT_ID,"http_request_count":len(receipts),"logical_query_count":5,"plan_sha256":PLAN_SHA256,"raw_files":raw_entries,"raw_tree_sha256":raw_tree,"retry_count":0,"run_id":RUN_ID,"version":VERSION}))
        summary={"artifact_state":"JQUANTS_V2_FREE_SOURCE_PROBE_ACQUIRED","bar_rows":len(loaded.bars),"calendar_rows":len(loaded.calendar),"empirical_authorized":False,"experiment_id":EXPERIMENT_ID,"historical_eligibility_ready":False,"http_request_count":len(receipts),"logical_query_count":5,"master_rows":len(loaded.masters),"raw_tree_sha256":raw_tree,"retry_count":0,"run_id":RUN_ID,"strict_eligible_count":0,"terminal_status":"NEEDS_MORE_DATA"}
        _atomic(staging,"summary.json",json_file_bytes(summary))
        if before_promotion is not None: before_promotion()
        end_binding=verify_expected_freeze(repo_root,expected_freeze_sha256)
        if end_binding!=start_binding: raise CollectorFailure("SOURCE_BINDING_CHANGED")
        rebuilt=trusted_rebuild(staging)
        authorization={"experiment_id":EXPERIMENT_ID,"expected_freeze_manifest_sha256":expected_freeze_sha256,"final_path":str(final),"final_tree_entries":list(rebuilt.final_tree_entries),"final_tree_sha256":rebuilt.final_tree_sha256,"raw_tree_sha256":raw_tree,"run_id":RUN_ID}
        _atomic(control,"authorization.json",json_file_bytes(authorization))
        if final.exists(): raise CollectorFailure("FINAL_RACE")
        if os.path.splitdrive(str(staging))[0].casefold()!=os.path.splitdrive(str(final))[0].casefold(): raise CollectorFailure("PROMOTION_VOLUME")
        os.rename(staging,final); return summary
    except Exception as exc:
        code=exc.code if isinstance(exc,(CollectorFailure,ProbeError)) else "INTERNAL"
        if (control/"lease.json").exists() and not (control/"authorization.json").exists():
            try: _atomic(control,"failure.json",json_file_bytes({"experiment_id":EXPERIMENT_ID,"failure_code":code,"run_id":RUN_ID}))
            except Exception: pass
        raise CollectorFailure(code) from None


def dry_plan()->dict[str,object]: return {"execute":False,"global_http_cap":GLOBAL_HTTP_CAP,"logical_query_count":5,"minimum_request_spacing_seconds":MIN_REQUEST_SPACING_SECONDS,"network_request_count":0,"plan_sha256":PLAN_SHA256,"queries":[x.projection() for x in QUERY_PLANS],"retry_count":0}


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(); group=parser.add_mutually_exclusive_group(required=True); group.add_argument("--dry-plan",action="store_true"); group.add_argument("--execute",action="store_true"); parser.add_argument("--expected-freeze-manifest-sha256"); args=parser.parse_args(argv)
    if args.dry_plan:
        if args.expected_freeze_manifest_sha256 is not None: return 11
        sys.stdout.buffer.write(json_file_bytes(dry_plan())); return 0
    if args.expected_freeze_manifest_sha256 is None: return 11
    raw=os.environ.pop(API_KEY_ENV,None)
    if raw is None: return 11
    try: key=validate_key(raw)
    except ProbeError: return 11
    raw=None
    try: collect_and_publish(repo_root=Path(__file__).resolve().parents[5],expected_freeze_sha256=args.expected_freeze_manifest_sha256,api_key=key); return 0
    except CollectorFailure: return 20
    finally: key=""; os.environ.pop(API_KEY_ENV,None)


if __name__=="__main__": raise SystemExit(main())
