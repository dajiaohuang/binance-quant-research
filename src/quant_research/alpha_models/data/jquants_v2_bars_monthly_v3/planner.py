from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..jquants_v2_bars_monthly_v1 import loader as v1_loader
from .contracts import (
    BOOTSTRAP_MONTH_COUNT,
    BOOTSTRAP_PLAN_SHA256,
    MONTH_PLAN_SCHEMA_VERSION,
    REQUIRED_REUSE_DATES,
    REUSE_SOURCE_KINDS,
    ContractError,
    canonical_json_bytes,
    sha256_bytes,
    strict_json,
)
from .loader import _expected_registry_document, load_bootstrap_component


@dataclass(frozen=True)
class _TrustedMonthPlan:
    month: str
    session_dates: tuple[str, ...]
    reuse_entries: tuple[dict[str, object], ...]
    network_dates: tuple[str, ...]
    registry_artifact_sha256: str
    plan_sha256: str

    def projection(self) -> dict[str, Any]:
        return {
            "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
            "month": self.month,
            "network_dates": list(self.network_dates),
            "plan_sha256": self.plan_sha256,
            "registry_artifact_sha256": self.registry_artifact_sha256,
            "reuse_entries": list(self.reuse_entries),
            "schema_version": MONTH_PLAN_SCHEMA_VERSION,
            "session_dates": list(self.session_dates),
        }


def _registry_path(root: Path, candidate: object) -> Path:
    if type(candidate) is not type(Path()):
        raise ContractError("REGISTRY_ARTIFACT_PATH_TYPE")
    expected = root / "reuse_registry.json"
    try:
        actual = candidate.resolve(strict=True)
        expected_resolved = expected.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ContractError("REGISTRY_ARTIFACT_PATH") from exc
    if actual != expected_resolved:
        raise ContractError("REGISTRY_ARTIFACT_PATH")
    return v1_loader._safe_file(root, "reuse_registry.json")


def build_trusted_month_plans(
    repo_root: Path,
    bootstrap_root: Path,
    registry_artifact_path: Path,
    expected_registry_sha256: str,
) -> tuple[_TrustedMonthPlan, ...]:
    if type(expected_registry_sha256) is not str or len(expected_registry_sha256) != 64 or any(char not in "0123456789abcdef" for char in expected_registry_sha256):
        raise ContractError("REGISTRY_ARTIFACT_SHA")
    artifact = _registry_path(bootstrap_root, registry_artifact_path)
    raw = artifact.read_bytes()
    if sha256_bytes(raw) != expected_registry_sha256:
        raise ContractError("REGISTRY_ARTIFACT_HASH_MISMATCH")
    bundle = load_bootstrap_component(bootstrap_root)
    expected_document = _expected_registry_document(repo_root, bootstrap_root, bundle)
    actual_document = strict_json(raw)
    if actual_document != expected_document:
        raise ContractError("REGISTRY_ARTIFACT_BINDING_MISMATCH")
    entries_raw = expected_document["entries"]
    if type(entries_raw) is not list:
        raise ContractError("REGISTRY_ENTRIES")
    entries = tuple(entries_raw)
    if tuple(item["session_date"] for item in entries) != REQUIRED_REUSE_DATES or tuple(item["source_kind"] for item in entries) != REUSE_SOURCE_KINDS:
        raise ContractError("REGISTRY_REQUIRED_SOURCES")
    months = tuple(sorted(bundle.month_plans, key=lambda item: item.month))
    if len(months) != BOOTSTRAP_MONTH_COUNT:
        raise ContractError("MONTH_PLAN_COVERAGE")
    output: list[_TrustedMonthPlan] = []
    for month in months:
        reused = tuple(item for item in entries if str(item["session_date"]).startswith(month.month))
        reuse_dates = {str(item["session_date"]) for item in reused}
        if any(day not in month.session_dates for day in reuse_dates):
            raise ContractError("REGISTRY_MONTH_PLAN_MISMATCH")
        network = tuple(day for day in month.session_dates if day not in reuse_dates)
        core = {
            "bootstrap_plan_sha256": BOOTSTRAP_PLAN_SHA256,
            "month": month.month,
            "network_dates": list(network),
            "registry_artifact_sha256": expected_registry_sha256,
            "reuse_entries": list(reused),
            "schema_version": MONTH_PLAN_SCHEMA_VERSION,
            "session_dates": list(month.session_dates),
        }
        output.append(_TrustedMonthPlan(month.month, month.session_dates, reused, network, expected_registry_sha256, sha256_bytes(canonical_json_bytes(core))))
    result = tuple(output)
    excluded = tuple(day for plan in result for day in plan.session_dates if day not in plan.network_dates)
    if excluded != REQUIRED_REUSE_DATES or sum(bool(plan.reuse_entries) for plan in result) != 3:
        raise ContractError("REUSE_EXCLUSION_MISMATCH")
    return result
