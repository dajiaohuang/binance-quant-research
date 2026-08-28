from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..jquants_v2_bars_monthly_v1 import loader as v1_loader
from .contracts import (
    API_HOST,
    BOOTSTRAP_GLOBAL_HTTP_CAP,
    BOOTSTRAP_PLAN_SHA256,
    BOOTSTRAP_QUERY_PLANS,
    BOOTSTRAP_RUN_ID,
    EXP005_Q04_RAW_BYTES,
    EXP005_Q04_RAW_RELATIVE,
    EXP005_Q04_RAW_SHA256,
    EXP005_Q04_RECEIPT_BYTES,
    EXP005_Q04_RECEIPT_RELATIVE,
    EXP005_Q04_RECEIPT_SHA256,
    EXP006_CLOSURE_RELATIVE,
    EXP006_CLOSURE_SHA256,
    FIRST_BAR_DATE,
    LAST_BAR_DATE,
    PAGE_KEY,
    RECEIPT_SCHEMA_VERSION,
    REQUIRED_REUSE_DATES,
    REUSE_REGISTRY_SCHEMA_VERSION,
    ContractError,
    MonthPlan,
    canonical_json_bytes,
    date_text,
    exact_int,
    sha256_bytes,
    strict_json,
    text,
)


_TRUSTED_MINT = object()


@dataclass(frozen=True)
class VerifiedReuseEntry:
    session_date: str
    source_kind: str
    raw_relative_path: str
    raw_bytes: int
    raw_sha256: str
    receipt_relative_path: str
    receipt_bytes: int
    receipt_sha256: str

    def __post_init__(self) -> None:
        date_text(self.session_date)
        if self.source_kind not in ("BOOTSTRAP_BOUNDARY_FIRST", "EXP005_Q04_REUSE", "BOOTSTRAP_BOUNDARY_LAST"):
            raise ContractError("REUSE_SOURCE_KIND")
        for relative in (self.raw_relative_path, self.receipt_relative_path):
            candidate = Path(relative)
            if not relative or "\\" in relative or candidate.is_absolute() or ".." in candidate.parts or candidate.as_posix() != relative:
                raise ContractError("REUSE_SOURCE_PATH")
        exact_int(self.raw_bytes, "REUSE_RAW_BYTES", 1)
        exact_int(self.receipt_bytes, "REUSE_RECEIPT_BYTES", 1)
        for value in (self.raw_sha256, self.receipt_sha256):
            if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ContractError("REUSE_SOURCE_HASH")

    def projection(self) -> dict[str, object]:
        return {
            "raw_bytes": self.raw_bytes,
            "raw_relative_path": self.raw_relative_path,
            "raw_sha256": self.raw_sha256,
            "receipt_bytes": self.receipt_bytes,
            "receipt_relative_path": self.receipt_relative_path,
            "receipt_sha256": self.receipt_sha256,
            "session_date": self.session_date,
            "source_kind": self.source_kind,
        }


class VerifiedReuseRegistry:
    __slots__ = ("_token", "bootstrap_plan_sha256", "entries", "registry_sha256", "schema_version")

    def __init__(self, *_: object, **__: object) -> None:
        raise ContractError("TRUSTED_LOADER_ONLY")

    def __setattr__(self, _: str, __: object) -> None:
        raise AttributeError("IMMUTABLE_VERIFIED_REUSE_REGISTRY")

    @classmethod
    def _mint(cls, entries: tuple[VerifiedReuseEntry, ...]) -> "VerifiedReuseRegistry":
        if not entries or len({item.session_date for item in entries}) != len(entries):
            raise ContractError("DUPLICATE_REUSE")
        value = object.__new__(cls)
        object.__setattr__(value, "_token", _TRUSTED_MINT)
        object.__setattr__(value, "bootstrap_plan_sha256", BOOTSTRAP_PLAN_SHA256)
        object.__setattr__(value, "entries", entries)
        object.__setattr__(value, "schema_version", REUSE_REGISTRY_SCHEMA_VERSION)
        core = {
            "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
            "entries": [item.projection() for item in entries],
            "schema_version": REUSE_REGISTRY_SCHEMA_VERSION,
        }
        object.__setattr__(value, "registry_sha256", sha256_bytes(canonical_json_bytes(core)))
        return value

    def require_trusted(self) -> None:
        if getattr(self, "_token", None) is not _TRUSTED_MINT:
            raise ContractError("UNVERIFIED_REUSE_REGISTRY")

    def projection(self) -> dict[str, object]:
        self.require_trusted()
        return {
            "bootstrap_plan_sha256": self.bootstrap_plan_sha256,
            "entries": [item.projection() for item in self.entries],
            "registry_sha256": self.registry_sha256,
            "schema_version": self.schema_version,
        }


def _strict_source_file(repo_root: Path, relative: str, expected_bytes: int | None, expected_sha: str) -> bytes:
    path = v1_loader._safe_file(repo_root, relative)
    body = path.read_bytes()
    if (expected_bytes is not None and len(body) != expected_bytes) or sha256_bytes(body) != expected_sha:
        raise ContractError("REUSE_SOURCE_DRIFT")
    return body


def verify_exp005_reuse(repo_root: Path) -> VerifiedReuseRegistry:
    raw = _strict_source_file(repo_root, EXP005_Q04_RAW_RELATIVE, EXP005_Q04_RAW_BYTES, EXP005_Q04_RAW_SHA256)
    receipt = _strict_source_file(repo_root, EXP005_Q04_RECEIPT_RELATIVE, EXP005_Q04_RECEIPT_BYTES, EXP005_Q04_RECEIPT_SHA256)
    _strict_source_file(repo_root, EXP006_CLOSURE_RELATIVE, None, EXP006_CLOSURE_SHA256)
    source_receipt = strict_json(receipt)
    if source_receipt.get("body_sha256") != EXP005_Q04_RAW_SHA256 or source_receipt.get("body_bytes") != EXP005_Q04_RAW_BYTES:
        raise ContractError("REUSE_SIDECAR_BINDING")
    plan = BOOTSTRAP_QUERY_PLANS[1].__class__(
        1,
        "EXP005_Q04_REUSE",
        "/v2/equities/bars/daily",
        {"date": "2025-03-28"},
        "SOURCE_BOUND_REUSE",
        67_108_864,
    )
    parsed = v1_loader.parse_page(plan, 1, exact_int(source_receipt.get("received_at_ms"), "RECEIVED"), raw)
    if not parsed.bars:
        raise ContractError("REUSE_EMPTY")
    entry = VerifiedReuseEntry(
        "2025-03-28",
        "EXP005_Q04_REUSE",
        EXP005_Q04_RAW_RELATIVE,
        EXP005_Q04_RAW_BYTES,
        EXP005_Q04_RAW_SHA256,
        EXP005_Q04_RECEIPT_RELATIVE,
        EXP005_Q04_RECEIPT_BYTES,
        EXP005_Q04_RECEIPT_SHA256,
    )
    return VerifiedReuseRegistry._mint((entry,))


def _boundary_entry(staging: Path, receipt: Mapping[str, Any], session_date: str, kind: str) -> VerifiedReuseEntry:
    raw_relative = text(receipt.get("raw_relative_path"), "RAW_PATH")
    receipt_relative = text(receipt.get("receipt_relative_path"), "RECEIPT_PATH")
    raw = v1_loader._safe_file(staging, raw_relative).read_bytes()
    sidecar = v1_loader._safe_file(staging, receipt_relative).read_bytes()
    if len(raw) != receipt["body_bytes"] or sha256_bytes(raw) != receipt["body_sha256"]:
        raise ContractError("BOUNDARY_RAW_BINDING")
    return VerifiedReuseEntry(
        session_date,
        kind,
        raw_relative,
        len(raw),
        sha256_bytes(raw),
        receipt_relative,
        len(sidecar),
        sha256_bytes(sidecar),
    )


def mint_full_registry(repo_root: Path, staging: Path, bundle: v1_loader.BootstrapBundle) -> VerifiedReuseRegistry:
    exp005 = verify_exp005_reuse(repo_root).entries[0]
    sessions = set(bundle.session_dates)
    if any(day not in sessions for day in REQUIRED_REUSE_DATES):
        raise ContractError("REUSE_DATE_NOT_OFFICIAL_SESSION")
    by_query = {
        query_id: tuple(item for item in bundle.receipts if item.get("query_id") == query_id)
        for query_id in ("Q01_CALENDAR", "Q02_BARS_FIRST", "Q03_BARS_LAST")
    }
    if not by_query["Q01_CALENDAR"] or len(by_query["Q02_BARS_FIRST"]) != 1 or len(by_query["Q03_BARS_LAST"]) != 1:
        raise ContractError("BOUNDARY_RECEIPT_COVERAGE")
    first = _boundary_entry(staging, by_query["Q02_BARS_FIRST"][0], FIRST_BAR_DATE, "BOOTSTRAP_BOUNDARY_FIRST")
    last = _boundary_entry(staging, by_query["Q03_BARS_LAST"][0], LAST_BAR_DATE, "BOOTSTRAP_BOUNDARY_LAST")
    entries = tuple(sorted((first, exp005, last), key=lambda item: item.session_date))
    if tuple(item.session_date for item in entries) != REQUIRED_REUSE_DATES:
        raise ContractError("REUSE_REGISTRY_DATES")
    return VerifiedReuseRegistry._mint(entries)


def load_bootstrap_component(root: Path) -> v1_loader.BootstrapBundle:
    if not root.is_dir() or root.is_symlink():
        raise ContractError("BOOTSTRAP_ROOT")
    query_doc = strict_json(v1_loader._safe_file(root, "query_plan.json").read_bytes())
    if query_doc != {"plan_sha256": BOOTSTRAP_PLAN_SHA256, "queries": [item.projection() for item in BOOTSTRAP_QUERY_PLANS]}:
        raise ContractError("QUERY_PLAN_MISMATCH")
    receipt_dir = root / "response_receipts"
    if not receipt_dir.is_dir() or receipt_dir.is_symlink():
        raise ContractError("RECEIPT_DIRECTORY")
    paths = sorted(receipt_dir.glob("*.receipt.json"))
    if not paths or len(paths) > BOOTSTRAP_GLOBAL_HTTP_CAP:
        raise ContractError("RECEIPT_COUNT")
    pages = []
    receipts: list[dict[str, Any]] = []
    plans = {item.query_id: item for item in BOOTSTRAP_QUERY_PLANS}
    page_counts = {item.query_id: 0 for item in BOOTSTRAP_QUERY_PLANS}
    next_keys: dict[str, str | None] = {item.query_id: None for item in BOOTSTRAP_QUERY_PLANS}
    for ordinal, path in enumerate(paths, 1):
        relative_receipt = path.relative_to(root).as_posix()
        receipt = strict_json(v1_loader._safe_file(root, relative_receipt).read_bytes())
        v1_loader._exact_keys(receipt, v1_loader.RECEIPT_REQUIRED, "RECEIPT_SCHEMA")
        if receipt["schema_version"] != RECEIPT_SCHEMA_VERSION or receipt["api_host"] != API_HOST or receipt["run_id"] != BOOTSTRAP_RUN_ID or receipt["request_ordinal"] != ordinal:
            raise ContractError("RECEIPT_AUTHORITY")
        query_id = receipt["query_id"]
        if query_id not in plans:
            raise ContractError("QUERY_ID")
        plan = plans[query_id]
        page_counts[query_id] += 1
        page = page_counts[query_id]
        expected = dict(plan.parameters)
        if page > 1:
            prior = next_keys[query_id]
            if prior is None:
                raise ContractError("PAGINATION_WITHOUT_PRIOR")
            expected[PAGE_KEY] = prior
        expected_raw = f"responses/{plan.ordinal:02d}_{query_id}_page_{page:04d}.json"
        expected_receipt = f"response_receipts/{ordinal:04d}_{plan.ordinal:02d}_{query_id}_page_{page:04d}.receipt.json"
        if receipt["parameters"] != expected or receipt["cap_bytes"] != plan.cap_bytes or receipt["page_number"] != page or receipt["query_ordinal"] != plan.ordinal or receipt["path"] != plan.path or receipt["raw_relative_path"] != expected_raw or receipt["receipt_relative_path"] != expected_receipt or relative_receipt != expected_receipt:
            raise ContractError("RECEIPT_BINDING")
        raw = v1_loader._safe_file(root, expected_raw).read_bytes()
        if len(raw) != receipt["body_bytes"] or sha256_bytes(raw) != receipt["body_sha256"] or receipt["redirected"] is not False or receipt["status"] != 200 or receipt["content_type"] not in ("application/json", "application/problem+json"):
            raise ContractError("RAW_HTTP_BINDING")
        parsed = v1_loader.parse_page(plan, page, receipt["received_at_ms"], raw)
        next_keys[query_id] = parsed.next_key
        pages.append(parsed); receipts.append(receipt)
    if any(next_keys.values()):
        raise ContractError("PAGINATION_INCOMPLETE")
    return v1_loader.merge_bootstrap(pages, receipts)


def load_bootstrap_tree(repo_root: Path, root: Path) -> tuple[v1_loader.BootstrapBundle, VerifiedReuseRegistry, tuple[Any, ...]]:
    bundle = load_bootstrap_component(root)
    registry = mint_full_registry(repo_root, root, bundle)
    registry_doc = strict_json(v1_loader._safe_file(root, "reuse_registry.json").read_bytes())
    if registry_doc != registry.projection():
        raise ContractError("REUSE_REGISTRY_FILE_MISMATCH")
    from .planner import build_verified_month_plans
    plans = build_verified_month_plans(bundle.month_plans, registry)
    for plan in plans:
        document = strict_json(v1_loader._safe_file(root, f"monthly_plans/{plan.month}.json").read_bytes())
        if document != plan.projection():
            raise ContractError("MONTH_PLAN_FILE_MISMATCH")
    verify_acquisition_manifest(root, bundle)
    return bundle, registry, plans


def verify_acquisition_manifest(root: Path, bundle: v1_loader.BootstrapBundle) -> None:
    manifest = strict_json(v1_loader._safe_file(root, "acquisition_manifest.json").read_bytes())
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
        row = v1_loader._exact_keys(entry, frozenset(("bytes", "relative_path", "sha256")), "ACQUISITION_FILE_SCHEMA")
        relative = row["relative_path"]
        if relative in seen or relative == "acquisition_manifest.json":
            raise ContractError("ACQUISITION_FILE_DUPLICATE")
        seen.add(relative)
        body = v1_loader._safe_file(root, relative).read_bytes()
        if len(body) != row["bytes"] or sha256_bytes(body) != row["sha256"]:
            raise ContractError("ACQUISITION_FILE_HASH")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "acquisition_manifest.json"
    }
    if actual != seen:
        raise ContractError("ACQUISITION_FILE_SET")
