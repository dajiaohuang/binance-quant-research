from __future__ import annotations

import builtins
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest import mock

import quant_research.alpha_models.data.jquants_v2_bars_monthly_v3 as public_v3
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.collector import write_once
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.contracts import ContractError, json_file_bytes
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v3 import loader as loader_v3
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v3.collector import collect_bootstrap, dry_plan, reserve_attempt
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v3.contracts import BOOTSTRAP_RUN_ID, REQUIRED_REUSE_DATES
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v3.loader import load_bootstrap_tree, read_only_reuse_preflight
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v3.planner import build_trusted_month_plans
from tests.test_jquants_v2_bars_monthly_v1 import FakeClock, FakeTransport, valid_responses


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/quant_research/alpha_models/data/jquants_v2_bars_monthly_v3"


def snapshot(paths: tuple[Path, ...]) -> tuple[tuple[str, str, int, str], ...]:
    rows: list[tuple[str, str, int, str]] = []
    for base in paths:
        if not base.exists():
            rows.append((str(base), "MISSING", 0, ""))
            continue
        for path in sorted((base, *base.rglob("*"))):
            if path.is_dir():
                rows.append((str(path), "DIRECTORY", 0, ""))
            elif path.is_file():
                body = path.read_bytes()
                rows.append((str(path), "FILE", len(body), hashlib.sha256(body).hexdigest()))
            else:
                rows.append((str(path), "OTHER", 0, ""))
    return tuple(rows)


def prepare_staging(raw_root: Path) -> Path:
    staging, _ = reserve_attempt(raw_root)
    binding = loader_v3._preflight_binding_document(ROOT)
    write_once(staging / "preflight_source_binding.json", json_file_bytes(binding))
    return staging


def collect_fixture(raw_root: Path) -> Path:
    staging = prepare_staging(raw_root)
    clock = FakeClock()
    collect_bootstrap(
        ROOT,
        staging,
        "test-key",
        transport=FakeTransport(valid_responses()),
        monotonic_ns=clock.monotonic_ns,
        sleep_seconds=clock.sleep,
        utc_ns=clock.utc_ns,
    )
    return staging


def registry_sha(staging: Path) -> str:
    return hashlib.sha256((staging / "reuse_registry.json").read_bytes()).hexdigest()


class ZeroWritePreflightTests(unittest.TestCase):
    def test_success_preflight_intercepts_all_mutation_primitives(self) -> None:
        real_open = builtins.open

        def read_only_open(file: object, mode: str = "r", *args: object, **kwargs: object) -> object:
            if any(flag in mode for flag in "wax+"):
                raise AssertionError("write open before preflight pass")
            return real_open(file, mode, *args, **kwargs)

        forbidden = AssertionError("mutation before preflight pass")
        with (
            mock.patch("builtins.open", side_effect=read_only_open),
            mock.patch.object(Path, "mkdir", side_effect=forbidden),
            mock.patch.object(Path, "write_bytes", side_effect=forbidden),
            mock.patch.object(Path, "write_text", side_effect=forbidden),
            mock.patch.object(Path, "touch", side_effect=forbidden),
            mock.patch.object(Path, "rename", side_effect=forbidden),
            mock.patch("os.open", side_effect=forbidden),
            mock.patch("os.mkdir", side_effect=forbidden),
            mock.patch("os.makedirs", side_effect=forbidden),
        ):
            result = read_only_reuse_preflight(ROOT)
        self.assertEqual("PASS_READ_ONLY_ZERO_WRITES", result["verdict"])

    def test_drift_failure_leaves_control_and_raw_trees_identical(self) -> None:
        watched = (
            ROOT / "experiments/exp_20260828_009/formal_control",
            ROOT / "data/raw/jquants_v2_bars_monthly_v3",
        )
        before = snapshot(watched)
        with mock.patch.object(loader_v3, "EXP005_Q04_RAW_SHA256", "0" * 64):
            with self.assertRaisesRegex(ContractError, "REUSE_SOURCE_DRIFT"):
                read_only_reuse_preflight(ROOT)
        self.assertEqual(before, snapshot(watched))

    def test_launcher_has_no_mutating_primitive_before_preflight_pass(self) -> None:
        source = (PACKAGE / "launcher.ps1").read_text(encoding="utf-8")
        boundary = source.index("$reusePreflightPassed = $true")
        prefix = source[:boundary]
        for forbidden in ("CreateDirectory", "New-Item", "FileMode]::CreateNew", ".Write(", "formal_control", "preflight_source_binding.json"):
            self.assertNotIn(forbidden, prefix)
        self.assertIn("--reuse-preflight-check", prefix)
        self.assertIn("-B", prefix)

    def test_dry_plan_is_read_only_and_monthly_disabled(self) -> None:
        result = dry_plan(ROOT)
        self.assertEqual("PASS_READ_ONLY_ZERO_WRITES", result["preflight_reuse_verdict"])
        self.assertEqual(0, result["network_requests"])
        self.assertFalse(result["monthly_cli_available"])
        self.assertFalse(result["monthly_network_authorized"])


class ArtifactAuthorityTests(unittest.TestCase):
    def test_public_api_exports_no_registry_authority_type(self) -> None:
        self.assertEqual({"build_trusted_month_plans", "load_bootstrap_tree", "read_only_reuse_preflight"}, set(public_v3.__all__))
        public_classes = [name for name, value in inspect.getmembers(public_v3, inspect.isclass) if not name.startswith("_")]
        self.assertEqual([], public_classes)
        self.assertFalse(hasattr(public_v3, "VerifiedReuseRegistry"))
        self.assertFalse(hasattr(loader_v3, "VerifiedReuseRegistry"))
        self.assertFalse(hasattr(loader_v3, "_mint"))

    def test_manual_copy_pickle_and_mapping_objects_carry_no_authority(self) -> None:
        fabricated = {"entries": []}
        candidates = (fabricated, copy.copy(fabricated), pickle.loads(pickle.dumps(fabricated)), object())
        for candidate in candidates:
            with self.subTest(candidate=type(candidate).__name__):
                with self.assertRaisesRegex(ContractError, "REGISTRY_ARTIFACT_PATH_TYPE"):
                    build_trusted_month_plans(ROOT, ROOT, candidate, "0" * 64)  # type: ignore[arg-type]

    def test_subclass_like_path_wrapper_carries_no_authority(self) -> None:
        class PathWrapper:
            def resolve(self, *_: object, **__: object) -> Path:
                return ROOT / "reuse_registry.json"
        with self.assertRaisesRegex(ContractError, "REGISTRY_ARTIFACT_PATH_TYPE"):
            build_trusted_month_plans(ROOT, ROOT, PathWrapper(), "0" * 64)  # type: ignore[arg-type]

    def test_fabricated_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = collect_fixture(Path(temp))
            other = staging / "fabricated.json"
            other.write_bytes((staging / "reuse_registry.json").read_bytes())
            with self.assertRaisesRegex(ContractError, "REGISTRY_ARTIFACT_PATH"):
                build_trusted_month_plans(ROOT, staging, other, registry_sha(staging))

    def test_wrong_expected_registry_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = collect_fixture(Path(temp))
            with self.assertRaisesRegex(ContractError, "REGISTRY_ARTIFACT_HASH_MISMATCH"):
                build_trusted_month_plans(ROOT, staging, staging / "reuse_registry.json", "0" * 64)

    def test_registry_artifact_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = collect_fixture(Path(temp))
            path = staging / "reuse_registry.json"
            expected_sha = registry_sha(staging)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["entries"][1]["raw_sha256"] = "0" * 64
            path.write_bytes(json_file_bytes(value))
            with self.assertRaisesRegex(ContractError, "REGISTRY_ARTIFACT_HASH_MISMATCH"):
                build_trusted_month_plans(ROOT, staging, path, expected_sha)

    def test_fabricated_hash_over_tampered_artifact_still_fails_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = collect_fixture(Path(temp))
            path = staging / "reuse_registry.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["entries"][1]["raw_relative_path"] = "data/raw/fabricated.json"
            path.write_bytes(json_file_bytes(value))
            with self.assertRaisesRegex(ContractError, "REGISTRY_ARTIFACT_BINDING_MISMATCH"):
                build_trusted_month_plans(ROOT, staging, path, registry_sha(staging))

    def test_source_bindings_are_revalidated_at_every_planner_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = collect_fixture(Path(temp)); path = staging / "reuse_registry.json"; expected = registry_sha(staging)
            self.assertEqual(23, len(build_trusted_month_plans(ROOT, staging, path, expected)))
            with mock.patch.object(loader_v3, "EXP006_CLOSURE_SHA256", "0" * 64):
                with self.assertRaisesRegex(ContractError, "REUSE_SOURCE_DRIFT"):
                    build_trusted_month_plans(ROOT, staging, path, expected)

    def test_positive_artifact_authoritative_plans_exclude_only_three(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = collect_fixture(Path(temp))
            bundle, plans = load_bootstrap_tree(ROOT, staging)
            excluded = tuple(day for plan in plans for day in plan.session_dates if day not in plan.network_dates)
            self.assertEqual(REQUIRED_REUSE_DATES, excluded)
            self.assertEqual(("2024-07", "2025-03", "2026-05"), tuple(plan.month for plan in plans if plan.reuse_entries))
            self.assertEqual(20, sum(plan.network_dates == plan.session_dates for plan in plans))
            self.assertEqual(23, len(bundle.month_plans))

    def test_wrong_month_path_hash_duplicate_and_missing_registry_entries_fail(self) -> None:
        mutations = (
            lambda value: value["entries"][1].__setitem__("session_date", "2025-04-01"),
            lambda value: value["entries"][1].__setitem__("source_kind", "BOOTSTRAP_BOUNDARY_FIRST"),
            lambda value: value["entries"][1].__setitem__("receipt_relative_path", "data/raw/wrong.json"),
            lambda value: value["entries"][1].__setitem__("receipt_sha256", "0" * 64),
            lambda value: value["entries"].append(dict(value["entries"][1])),
            lambda value: value["entries"].pop(1),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temp:
                    staging = collect_fixture(Path(temp)); path = staging / "reuse_registry.json"
                    value = json.loads(path.read_text(encoding="utf-8")); mutation(value); path.write_bytes(json_file_bytes(value))
                    with self.assertRaisesRegex(ContractError, "REGISTRY_ARTIFACT_BINDING_MISMATCH"):
                        build_trusted_month_plans(ROOT, staging, path, registry_sha(staging))


class PreservationTests(unittest.TestCase):
    def test_exp008_frozen_candidate_is_unchanged(self) -> None:
        path = ROOT / "experiments/exp_20260828_008/artifacts/candidate_freeze_manifest.json"
        raw = path.read_bytes()
        self.assertEqual("758be2c679ba655723c8df91aba30aa48b1937beb42870e15af7c35b7bde9598", hashlib.sha256(raw).hexdigest())
        value = json.loads(raw)
        for row in value["files"]:
            body = (ROOT / row["path"]).read_bytes()
            self.assertEqual(row["bytes"], len(body))
            self.assertEqual(row["sha256"], hashlib.sha256(body).hexdigest())


if __name__ == "__main__":
    unittest.main()
