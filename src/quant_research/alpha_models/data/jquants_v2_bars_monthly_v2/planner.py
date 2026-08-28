from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .contracts import (
    BOOTSTRAP_MONTH_COUNT,
    BOOTSTRAP_PLAN_SHA256,
    MONTH_PLAN_SCHEMA_VERSION,
    REQUIRED_REUSE_DATES,
    REUSE_SOURCE_KINDS,
    ContractError,
    MonthPlan,
    canonical_json_bytes,
    sha256_bytes,
)
from .loader import VerifiedReuseEntry, VerifiedReuseRegistry


@dataclass(frozen=True)
class VerifiedMonthPlan:
    month: str
    session_dates: tuple[str, ...]
    reuse_entries: tuple[VerifiedReuseEntry, ...]
    network_dates: tuple[str, ...]
    registry_sha256: str
    plan_sha256: str

    def projection(self) -> dict[str, Any]:
        return {
            "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
            "month": self.month,
            "network_dates": list(self.network_dates),
            "plan_sha256": self.plan_sha256,
            "registry_sha256": self.registry_sha256,
            "reuse_entries": [item.projection() for item in self.reuse_entries],
            "schema_version": MONTH_PLAN_SCHEMA_VERSION,
            "session_dates": list(self.session_dates),
        }


def _mint_month_plan(month_plan: MonthPlan, entries: tuple[VerifiedReuseEntry, ...], registry: VerifiedReuseRegistry) -> VerifiedMonthPlan:
    reuse_dates = {item.session_date for item in entries}
    if len(reuse_dates) != len(entries) or any(not item.session_date.startswith(month_plan.month) for item in entries):
        raise ContractError("DUPLICATE_OR_WRONG_MONTH_REUSE")
    if any(item.session_date not in month_plan.session_dates for item in entries):
        raise ContractError("REGISTRY_MONTH_PLAN_MISMATCH")
    network = tuple(day for day in month_plan.session_dates if day not in reuse_dates)
    core = {
        "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
        "month": month_plan.month,
        "network_dates": list(network),
        "registry_sha256": registry.registry_sha256,
        "reuse_entries": [item.projection() for item in entries],
        "schema_version": MONTH_PLAN_SCHEMA_VERSION,
        "session_dates": list(month_plan.session_dates),
    }
    return VerifiedMonthPlan(month_plan.month, month_plan.session_dates, entries, network, registry.registry_sha256, sha256_bytes(canonical_json_bytes(core)))


def build_verified_month_plans(month_plans: Iterable[MonthPlan], registry: VerifiedReuseRegistry) -> tuple[VerifiedMonthPlan, ...]:
    if type(registry) is not VerifiedReuseRegistry:
        raise ContractError("UNVERIFIED_REUSE_REGISTRY")
    registry.require_trusted()
    if registry.bootstrap_plan_sha256 != BOOTSTRAP_PLAN_SHA256:
        raise ContractError("REGISTRY_PLAN_MISMATCH")
    entries = registry.entries
    if tuple(item.session_date for item in entries) != REQUIRED_REUSE_DATES or tuple(item.source_kind for item in entries) != REUSE_SOURCE_KINDS:
        raise ContractError("REGISTRY_REQUIRED_SOURCES")
    plans = tuple(sorted(month_plans, key=lambda item: item.month))
    if len(plans) != BOOTSTRAP_MONTH_COUNT or len({item.month for item in plans}) != len(plans):
        raise ContractError("MONTH_PLAN_COVERAGE")
    result = tuple(
        _mint_month_plan(plan, tuple(item for item in entries if item.session_date.startswith(plan.month)), registry)
        for plan in plans
    )
    excluded = tuple(day for plan in result for day in plan.session_dates if day not in plan.network_dates)
    if excluded != REQUIRED_REUSE_DATES:
        raise ContractError("REUSE_EXCLUSION_MISMATCH")
    if sum(bool(plan.reuse_entries) for plan in result) != 3:
        raise ContractError("REUSE_MONTH_COUNT")
    return result
