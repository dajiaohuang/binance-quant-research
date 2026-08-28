from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..jquants_v2_bars_monthly_v1 import loader as v1_loader
from ..jquants_v2_bars_monthly_v3.loader import load_bootstrap_tree
from .contracts import (
    BOOTSTRAP_ROOT_RELATIVE,
    EXP009_ACQUISITION_MANIFEST_SHA256,
    EXP009_POSTFLIGHT_RELATIVE,
    EXP009_POSTFLIGHT_SHA256,
    EXP009_RAW_TREE_SHA256,
    EXP009_REGISTRY_SHA256,
    EXP009_SESSION_ARTIFACT_SHA256,
    EXP009_SESSION_LIST_SHA256,
    MONTH_COUNT,
    NETWORK_DATE_COUNT,
    REUSE_DATES,
    SESSION_DATE_COUNT,
    ContractError,
    canonical_json_bytes,
    sha256_bytes,
    strict_json,
)


@dataclass(frozen=True)
class _SourceSnapshot:
    bootstrap_root: Path
    plans: tuple[Any, ...]
    binding_document: dict[str, object]
    binding_sha256: str


def _file_hash(root: Path, relative: str) -> tuple[Path, bytes, str]:
    path = v1_loader._safe_file(root, relative)
    raw = path.read_bytes()
    return path, raw, sha256_bytes(raw)


def _source_snapshot(repo_root: Path) -> _SourceSnapshot:
    root = repo_root.resolve(strict=True)
    _, postflight_raw, postflight_sha = _file_hash(root, EXP009_POSTFLIGHT_RELATIVE)
    if postflight_sha != EXP009_POSTFLIGHT_SHA256:
        raise ContractError("EXP009_POSTFLIGHT_DRIFT")
    postflight = strict_json(postflight_raw)
    if (
        postflight.get("verdict") != "PASS"
        or postflight.get("final", {}).get("acquisition_manifest_sha256")
        != EXP009_ACQUISITION_MANIFEST_SHA256
        or postflight.get("final", {}).get("raw_tree_sha256") != EXP009_RAW_TREE_SHA256
        or postflight.get("reuse", {}).get("registry_artifact_sha256")
        != EXP009_REGISTRY_SHA256
        or postflight.get("calendar", {}).get("session_list_sha256")
        != EXP009_SESSION_LIST_SHA256
    ):
        raise ContractError("EXP009_POSTFLIGHT_BINDING")

    bootstrap_root = (root / BOOTSTRAP_ROOT_RELATIVE).resolve(strict=True)
    expected_bootstrap = root / BOOTSTRAP_ROOT_RELATIVE
    if bootstrap_root != expected_bootstrap.resolve(strict=True):
        raise ContractError("BOOTSTRAP_ROOT")
    bundle, plans = load_bootstrap_tree(root, bootstrap_root)
    if bundle.raw_tree_sha256 != EXP009_RAW_TREE_SHA256:
        raise ContractError("EXP009_RAW_TREE_DRIFT")
    _, acquisition_raw, acquisition_sha = _file_hash(
        bootstrap_root, "acquisition_manifest.json"
    )
    if acquisition_sha != EXP009_ACQUISITION_MANIFEST_SHA256:
        raise ContractError("EXP009_ACQUISITION_MANIFEST_DRIFT")
    acquisition = strict_json(acquisition_raw)
    if acquisition.get("raw_tree_sha256") != EXP009_RAW_TREE_SHA256:
        raise ContractError("EXP009_ACQUISITION_RAW_TREE_BINDING")
    _, registry_raw, registry_sha = _file_hash(bootstrap_root, "reuse_registry.json")
    if registry_sha != EXP009_REGISTRY_SHA256:
        raise ContractError("EXP009_REGISTRY_DRIFT")
    registry = strict_json(registry_raw)
    _, sessions_raw, sessions_sha = _file_hash(bootstrap_root, "calendar_sessions.json")
    if sessions_sha != EXP009_SESSION_ARTIFACT_SHA256:
        raise ContractError("EXP009_SESSION_ARTIFACT_DRIFT")
    sessions = strict_json(sessions_raw)
    session_dates = tuple(sessions.get("session_dates", ()))
    if (
        len(session_dates) != SESSION_DATE_COUNT
        or session_dates != tuple(sorted(set(session_dates)))
        or sha256_bytes(canonical_json_bytes(list(session_dates)))
        != EXP009_SESSION_LIST_SHA256
        or sessions.get("session_list_sha256") != EXP009_SESSION_LIST_SHA256
    ):
        raise ContractError("EXP009_SESSION_LIST_DRIFT")

    ordered = tuple(sorted(plans, key=lambda item: item.month))
    if len(ordered) != MONTH_COUNT or tuple(plans) != ordered:
        raise ContractError("MONTH_ORDER_OR_COUNT")
    plan_sessions = tuple(day for plan in ordered for day in plan.session_dates)
    network_dates = tuple(day for plan in ordered for day in plan.network_dates)
    exclusions = tuple(day for day in plan_sessions if day not in set(network_dates))
    if (
        plan_sessions != session_dates
        or len(network_dates) != NETWORK_DATE_COUNT
        or network_dates != tuple(sorted(set(network_dates)))
        or exclusions != REUSE_DATES
    ):
        raise ContractError("MONTH_PLAN_COVERAGE")
    entries = registry.get("entries")
    if type(entries) is not list or tuple(item.get("session_date") for item in entries) != REUSE_DATES:
        raise ContractError("REUSE_REGISTRY_DATES")

    core: dict[str, object] = {
        "acquisition_manifest_sha256": acquisition_sha,
        "bootstrap_root_relative": BOOTSTRAP_ROOT_RELATIVE,
        "month_count": len(ordered),
        "network_date_count": len(network_dates),
        "postflight_closure_sha256": postflight_sha,
        "raw_tree_sha256": bundle.raw_tree_sha256,
        "registry_artifact_sha256": registry_sha,
        "reuse_dates": list(REUSE_DATES),
        "session_artifact_sha256": sessions_sha,
        "session_date_count": len(session_dates),
        "session_list_sha256": EXP009_SESSION_LIST_SHA256,
    }
    binding = sha256_bytes(
        b"JQUANTS_V2_BARS_MONTHLY_V4_SOURCE\x00" + canonical_json_bytes(core)
    )
    return _SourceSnapshot(bootstrap_root, ordered, core, binding)


def verify_source_preflight(repo_root: Path) -> dict[str, object]:
    snapshot = _source_snapshot(repo_root)
    return {
        **snapshot.binding_document,
        "source_binding_sha256": snapshot.binding_sha256,
        "verdict": "PASS_EXP009_EXP005_EXP006_SOURCE_REVALIDATED",
    }


__all__ = ["verify_source_preflight"]
