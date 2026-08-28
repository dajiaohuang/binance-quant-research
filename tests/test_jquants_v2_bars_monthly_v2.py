from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.catalog import ReuseLeaf
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.contracts import ContractError, json_file_bytes
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.collector import write_once
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v2 import loader as loader_v2
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v2.collector import (
    collect_bootstrap,
    dry_plan,
    reserve_attempt,
)
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v2.contracts import (
    BOOTSTRAP_PLAN_SHA256,
    BOOTSTRAP_RUN_ID,
    EXP005_Q04_RAW_SHA256,
    EXP005_Q04_RECEIPT_SHA256,
    EXP006_CLOSURE_SHA256,
    REQUIRED_REUSE_DATES,
)
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v2.loader import (
    VerifiedReuseRegistry,
    load_bootstrap_tree,
    verify_exp005_reuse,
)
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v2.planner import build_verified_month_plans
from tests.test_jquants_v2_bars_monthly_v1 import (
    FakeClock,
    FakeTransport,
    calendar_rows,
    json_bytes,
    valid_responses,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/quant_research/alpha_models/data/jquants_v2_bars_monthly_v2"


def prepare_staging(root: Path) -> Path:
    staging = root / f".{BOOTSTRAP_RUN_ID}.staging"
    staging.mkdir(parents=True)
    registry = verify_exp005_reuse(ROOT)
    write_once(staging / "preflight_reuse_registry.json", json_file_bytes(registry.projection()))
    return staging


def collect_fixture(root: Path, responses: object | None = None) -> tuple[Path, FakeTransport]:
    staging = prepare_staging(root)
    clock = FakeClock()
    transport = FakeTransport(valid_responses() if responses is None else responses)
    collect_bootstrap(
        ROOT,
        staging,
        "test-key",
        transport=transport,
        monotonic_ns=clock.monotonic_ns,
        sleep_seconds=clock.sleep,
        utc_ns=clock.utc_ns,
    )
    return staging, transport


class SourcePreflightTests(unittest.TestCase):
    def test_exact_exp005_and_exp006_sources_mint_safe_entry(self) -> None:
        registry = verify_exp005_reuse(ROOT)
        self.assertEqual(("2025-03-28",), tuple(item.session_date for item in registry.entries))
        self.assertEqual("EXP005_Q04_REUSE", registry.entries[0].source_kind)
        self.assertEqual(EXP005_Q04_RAW_SHA256, registry.entries[0].raw_sha256)
        self.assertEqual(EXP005_Q04_RECEIPT_SHA256, registry.entries[0].receipt_sha256)
        projection = json.dumps(registry.projection(), sort_keys=True)
        self.assertNotIn('"O"', projection)
        self.assertNotIn("JQUANTS_API_KEY", projection)

    def test_exp005_raw_drift_fails(self) -> None:
        with mock.patch.object(loader_v2, "EXP005_Q04_RAW_SHA256", "0" * 64):
            with self.assertRaisesRegex(ContractError, "REUSE_SOURCE_DRIFT"):
                verify_exp005_reuse(ROOT)

    def test_exp005_sidecar_drift_fails(self) -> None:
        with mock.patch.object(loader_v2, "EXP005_Q04_RECEIPT_SHA256", "0" * 64):
            with self.assertRaisesRegex(ContractError, "REUSE_SOURCE_DRIFT"):
                verify_exp005_reuse(ROOT)

    def test_exp006_closure_drift_fails(self) -> None:
        with mock.patch.object(loader_v2, "EXP006_CLOSURE_SHA256", "0" * 64):
            with self.assertRaisesRegex(ContractError, "REUSE_SOURCE_DRIFT"):
                verify_exp005_reuse(ROOT)

    def test_dry_plan_emits_no_registry(self) -> None:
        result = dry_plan(ROOT)
        self.assertEqual("PASS_READ_ONLY_NO_REGISTRY_EMITTED", result["preflight_reuse_verdict"])
        self.assertNotIn("preflight_reuse_registry", result)
        self.assertEqual(0, result["network_requests"])
        self.assertFalse(result["monthly_network_authorized"])

    def test_source_drift_stops_collector_before_key_or_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = Path(temp)
            transport = FakeTransport([])
            with mock.patch.object(loader_v2, "EXP005_Q04_RAW_SHA256", "0" * 64):
                with self.assertRaisesRegex(ContractError, "REUSE_SOURCE_DRIFT"):
                    collect_bootstrap(ROOT, staging, object(), transport=transport)
            self.assertEqual([], transport.calls)


class TrustedRegistryPlanningTests(unittest.TestCase):
    def test_unverified_caller_registry_is_rejected(self) -> None:
        with self.assertRaisesRegex(ContractError, "TRUSTED_LOADER_ONLY"):
            VerifiedReuseRegistry(object())

    def test_old_v1_caller_leaf_cannot_enter_builder(self) -> None:
        leaf = ReuseLeaf("2025-03-28", "EXP006_SOURCE_BOUND", "data/raw/source.json", EXP005_Q04_RAW_SHA256)
        with self.assertRaisesRegex(ContractError, "UNVERIFIED_REUSE_REGISTRY"):
            build_verified_month_plans((), leaf)  # type: ignore[arg-type]

    def test_missing_registry_dates_are_rejected(self) -> None:
        registry = verify_exp005_reuse(ROOT)
        with self.assertRaisesRegex(ContractError, "REGISTRY_REQUIRED_SOURCES"):
            build_verified_month_plans((), registry)

    def test_positive_formal_fixture_excludes_exactly_three_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging, _ = collect_fixture(Path(temp))
            bundle, registry, plans = load_bootstrap_tree(ROOT, staging)
            self.assertEqual(REQUIRED_REUSE_DATES, tuple(item.session_date for item in registry.entries))
            excluded = tuple(day for plan in plans for day in plan.session_dates if day not in plan.network_dates)
            self.assertEqual(REQUIRED_REUSE_DATES, excluded)
            reused_months = tuple(plan.month for plan in plans if plan.reuse_entries)
            self.assertEqual(("2024-07", "2025-03", "2026-05"), reused_months)
            self.assertEqual(20, sum(plan.network_dates == plan.session_dates for plan in plans))
            self.assertEqual(3, len(json.loads((staging / "reuse_registry.json").read_text(encoding="utf-8"))["entries"]))

    def test_boundary_non_session_fails_after_parse(self) -> None:
        rows = calendar_rows()
        for row in rows:
            if row["Date"] == "2024-07-01":
                row["HolDiv"] = "0"
        responses = valid_responses()
        responses[0].body = json_bytes({"data": rows})
        with tempfile.TemporaryDirectory() as temp:
            staging = prepare_staging(Path(temp)); clock = FakeClock(); transport = FakeTransport(responses)
            with self.assertRaisesRegex(ContractError, "REUSE_DATE_NOT_OFFICIAL_SESSION"):
                collect_bootstrap(ROOT, staging, "test-key", transport=transport, monotonic_ns=clock.monotonic_ns, sleep_seconds=clock.sleep, utc_ns=clock.utc_ns)
            self.assertEqual(3, len(transport.calls))

    def _tampered_tree(self, mutation: object) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging, _ = collect_fixture(Path(temp))
            registry_path = staging / "reuse_registry.json"
            value = json.loads(registry_path.read_text(encoding="utf-8"))
            mutation(value)
            registry_path.write_bytes(json_file_bytes(value))
            with self.assertRaises(ContractError):
                load_bootstrap_tree(ROOT, staging)

    def test_missing_registry_date_file_fails(self) -> None:
        self._tampered_tree(lambda value: value["entries"].pop(1))

    def test_wrong_registry_month_fails(self) -> None:
        self._tampered_tree(lambda value: value["entries"][1].__setitem__("session_date", "2025-04-01"))

    def test_wrong_source_kind_file_fails(self) -> None:
        self._tampered_tree(lambda value: value["entries"][1].__setitem__("source_kind", "BOOTSTRAP_BOUNDARY_FIRST"))

    def test_wrong_source_path_file_fails(self) -> None:
        self._tampered_tree(lambda value: value["entries"][1].__setitem__("raw_relative_path", "data/raw/wrong.json"))

    def test_wrong_source_hash_file_fails(self) -> None:
        self._tampered_tree(lambda value: value["entries"][1].__setitem__("raw_sha256", "0" * 64))

    def test_duplicate_registry_date_file_fails(self) -> None:
        self._tampered_tree(lambda value: value["entries"].append(dict(value["entries"][1])))

    def test_registry_plan_hash_file_fails(self) -> None:
        self._tampered_tree(lambda value: value.__setitem__("bootstrap_plan_sha256", "0" * 64))

    def test_wrong_month_and_registry_month_plan_mismatch_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging, _ = collect_fixture(Path(temp))
            path = staging / "monthly_plans/2025-03.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["month"] = "2025-04"
            value["network_dates"].append("2025-03-28")
            path.write_bytes(json_file_bytes(value))
            with self.assertRaisesRegex(ContractError, "MONTH_PLAN_FILE_MISMATCH"):
                load_bootstrap_tree(ROOT, staging)


class ReservationAndLauncherTests(unittest.TestCase):
    def test_preexisting_attempt_blocks_reservation_before_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            raw_root = Path(temp); staging, _ = reserve_attempt(raw_root)
            self.assertFalse((staging / "preflight_reuse_registry.json").exists())
            with self.assertRaisesRegex(ContractError, "ATTEMPT_EXISTS"):
                reserve_attempt(raw_root)

    def test_attempt_owned_registry_is_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging, _ = reserve_attempt(Path(temp))
            registry = verify_exp005_reuse(ROOT)
            target = staging / "preflight_reuse_registry.json"
            write_once(target, json_file_bytes(registry.projection()))
            with self.assertRaises(FileExistsError):
                write_once(target, json_file_bytes(registry.projection()))

    def test_mismatched_attempt_registry_stops_before_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = Path(temp)
            write_once(staging / "preflight_reuse_registry.json", b'{"wrong":true}\n')
            transport = FakeTransport([])
            with self.assertRaisesRegex(ContractError, "ATTEMPT_OWNED_REUSE_REGISTRY_MISMATCH"):
                collect_bootstrap(ROOT, staging, object(), transport=transport)
            self.assertEqual([], transport.calls)

    def test_launcher_orders_read_only_check_reservation_emit_env(self) -> None:
        source = (PACKAGE / "launcher.ps1").read_text(encoding="utf-8")
        readonly = source.index("--reuse-preflight-check")
        reservation = source.index("FileMode]::CreateNew")
        emit = source.index("--reserve-and-emit-reuse")
        env_read = source.index(".env.jquants.local")
        self.assertLess(readonly, reservation)
        self.assertLess(reservation, emit)
        self.assertLess(emit, env_read)
        self.assertLess(env_read, source.index("--formal-bootstrap-pre-reserved"))

    def test_monthly_cli_remains_absent(self) -> None:
        source = (PACKAGE / "__main__.py").read_text(encoding="utf-8")
        self.assertNotIn("--monthly", source)


if __name__ == "__main__":
    unittest.main()
