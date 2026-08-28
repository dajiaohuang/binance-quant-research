from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import (
    BOOTSTRAP_PLAN_SHA256,
    ContractError,
    MonthPlan,
    canonical_json_bytes,
    date_text,
    json_file_bytes,
    month_text,
    sha256_bytes,
    validate_attempt_id,
)


@dataclass(frozen=True)
class ReuseLeaf:
    session_date: str
    source_kind: str
    source_relative_path: str
    source_sha256: str

    def __post_init__(self) -> None:
        date_text(self.session_date)
        if self.source_kind not in ("EXP006_SOURCE_BOUND", "BOOTSTRAP_BOUNDARY"):
            raise ContractError("REUSE_KIND")
        if not self.source_relative_path or Path(self.source_relative_path).is_absolute() or ".." in Path(self.source_relative_path).parts:
            raise ContractError("REUSE_PATH")
        if len(self.source_sha256) != 64 or any(item not in "0123456789abcdef" for item in self.source_sha256):
            raise ContractError("REUSE_SHA")

    def projection(self) -> dict[str, str]:
        return {
            "session_date": self.session_date,
            "source_kind": self.source_kind,
            "source_relative_path": self.source_relative_path,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class MonthlyAttemptPlan:
    month: str
    attempt_id: str
    session_dates: tuple[str, ...]
    reuse_leaves: tuple[ReuseLeaf, ...]
    network_dates: tuple[str, ...]
    bootstrap_plan_sha256: str
    plan_sha256: str

    def projection(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "bootstrap_plan_sha256": self.bootstrap_plan_sha256,
            "month": self.month,
            "network_dates": list(self.network_dates),
            "plan_sha256": self.plan_sha256,
            "reuse_leaves": [item.projection() for item in self.reuse_leaves],
            "session_dates": list(self.session_dates),
        }


def build_monthly_attempt(month_plan: MonthPlan, attempt_id: str, leaves: Iterable[ReuseLeaf]) -> MonthlyAttemptPlan:
    month = month_text(month_plan.month)
    validate_attempt_id(attempt_id, month)
    reuse = tuple(sorted(leaves, key=lambda item: item.session_date))
    if len({item.session_date for item in reuse}) != len(reuse):
        raise ContractError("REUSE_DUPLICATE")
    sessions = set(month_plan.session_dates)
    if any(item.session_date not in sessions for item in reuse):
        raise ContractError("REUSE_OUTSIDE_MONTH")
    network = tuple(item for item in month_plan.session_dates if item not in {leaf.session_date for leaf in reuse})
    core = {
        "attempt_id": attempt_id,
        "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
        "month": month,
        "network_dates": list(network),
        "reuse_leaves": [item.projection() for item in reuse],
        "session_dates": list(month_plan.session_dates),
    }
    return MonthlyAttemptPlan(month, attempt_id, month_plan.session_dates, reuse, network, BOOTSTRAP_PLAN_SHA256, sha256_bytes(canonical_json_bytes(core)))


def oldest_missing_month(month_plans: Iterable[MonthPlan], completed_months: Iterable[str]) -> MonthPlan | None:
    completed = {month_text(item) for item in completed_months}
    pending = [item for item in month_plans if item.month not in completed]
    return min(pending, key=lambda item: item.month) if pending else None


def validate_repair_attempt(previous_attempt_id: str, new_attempt_id: str, month: str) -> None:
    validate_attempt_id(previous_attempt_id, month)
    validate_attempt_id(new_attempt_id, month)
    previous_number = int(previous_attempt_id[-3:])
    new_number = int(new_attempt_id[-3:])
    if new_number <= previous_number:
        raise ContractError("REPAIR_REQUIRES_NEW_ATTEMPT_ID")


def reserve_month_attempt(raw_root: Path, plan: MonthlyAttemptPlan) -> Path:
    month_root = raw_root / plan.month
    month_root.mkdir(parents=True, exist_ok=True)
    staging = month_root / f".{plan.attempt_id}.staging"
    final = month_root / plan.attempt_id
    if staging.exists() or final.exists():
        raise ContractError("MONTH_ATTEMPT_EXISTS")
    staging.mkdir(exist_ok=False)
    try:
        _write_once(staging / "attempt.reservation", json_file_bytes({
            "attempt_id": plan.attempt_id,
            "month": plan.month,
            "plan_sha256": plan.plan_sha256,
        }))
        _write_once(staging / "monthly_attempt_plan.json", json_file_bytes(plan.projection()))
    except BaseException:
        raise
    return staging


def publish_month_attempt(staging: Path, final: Path) -> None:
    if not (staging / "monthly_manifest.json").is_file():
        raise ContractError("MONTH_PUBLISH_INCOMPLETE")
    if final.exists():
        raise ContractError("MONTH_PUBLISH_NO_CLOBBER")
    os.rename(staging, final)


def require_monthly_network_authorization(bootstrap_final: Path, authorization: bool) -> None:
    required = ("summary.json", "calendar_sessions.json", "acquisition_manifest.json")
    if not authorization or any(not (bootstrap_final / item).is_file() for item in required):
        raise ContractError("MONTHLY_NETWORK_NOT_AUTHORIZED")


def _write_once(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def publish_catalog_entry(catalog_root: Path, entry: Mapping[str, Any]) -> Path:
    if type(entry) is not dict or set(entry) != {"attempt_id", "manifest_sha256", "month", "status"}:
        raise ContractError("CATALOG_SCHEMA")
    month = month_text(entry["month"])
    attempt = validate_attempt_id(entry["attempt_id"], month)
    if entry["status"] != "COMPLETE":
        raise ContractError("CATALOG_STATUS")
    sha = entry["manifest_sha256"]
    if type(sha) is not str or len(sha) != 64 or any(item not in "0123456789abcdef" for item in sha):
        raise ContractError("CATALOG_SHA")
    target = catalog_root / "entries" / month / f"{attempt}.json"
    _write_once(target, json_file_bytes(dict(entry)))
    return target


def read_catalog(catalog_root: Path) -> tuple[dict[str, Any], ...]:
    entries: list[dict[str, Any]] = []
    base = catalog_root / "entries"
    if not base.exists():
        return ()
    for path in sorted(base.glob("*/*.json")):
        import json
        value = json.loads(path.read_text(encoding="utf-8"))
        month = month_text(value.get("month"))
        validate_attempt_id(value.get("attempt_id"), month)
        entries.append(value)
    months = [item["month"] for item in entries]
    if len(months) != len(set(months)):
        raise ContractError("CATALOG_MULTIPLE_COMPLETE_MONTH")
    return tuple(entries)
