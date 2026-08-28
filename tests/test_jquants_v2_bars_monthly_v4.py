from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.contracts import (
    ContractError,
    json_file_bytes,
    strict_json,
)
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v4 import monthly, source
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v4.contracts import (
    EXP009_RAW_TREE_SHA256,
    EXP009_REGISTRY_SHA256,
    EXP009_SESSION_LIST_SHA256,
    MIN_SEND_SPACING_NS,
    NETWORK_DATE_COUNT,
    REUSE_DATES,
    SESSION_DATE_COUNT,
    attempt_id_for,
)


REPO = Path(__file__).resolve().parents[1]
BATCH = "exp_20260828_010_monthly_formal_001"


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str = "application/json",
        location: str | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.offset = 0
        self.headers = {"Content-Type": content_type}
        if location is not None:
            self.headers["Location"] = location

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)

    def read(self, amount: int | None = None) -> bytes:
        if self.offset >= len(self.body):
            return b""
        end = len(self.body) if amount is None else min(len(self.body), self.offset + amount)
        result = self.body[self.offset:end]
        self.offset = end
        return result


class FakeTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def request(self, host: str, path_and_query: str, headers: dict[str, str]) -> FakeResponse:
        self.calls.append((host, path_and_query, dict(headers)))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class FakeClock:
    def __init__(self) -> None:
        self.value = 10_000

    def monotonic_ns(self) -> int:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += int(seconds * 1_000_000_000)

    def utc_ns(self) -> int:
        return 1_800_000_000_000_000_000 + self.value


def bar(day: str, code: str = "13010") -> dict[str, object]:
    return {
        "AdjC": 101.0,
        "AdjFactor": 1.0,
        "AdjH": 102.0,
        "AdjL": 99.0,
        "AdjO": 100.0,
        "AdjVo": 1000.0,
        "C": 101.0,
        "Code": code,
        "Date": day,
        "ExRT": None,
        "H": 102.0,
        "L": 99.0,
        "LL": "0",
        "MktCap": 1000000.0,
        "O": 100.0,
        "UL": "0",
        "Va": 100000.0,
        "Vo": 1000.0,
    }


def envelope(day: str, *, code: str = "13010", next_key: str | None = None) -> bytes:
    value: dict[str, object] = {"data": [bar(day, code)]}
    if next_key is not None:
        value["pagination_key"] = next_key
    return json_file_bytes(value)


def staging_root(base: Path, attempt_id: str, month: str) -> Path:
    root = base / "staging"
    root.mkdir()
    (root / "attempt.reservation.json").write_bytes(
        json_file_bytes({"attempt_id": attempt_id, "batch_id": BATCH, "month": month})
    )
    (root / "attempt_ledger.jsonl").write_bytes(
        json_file_bytes({"attempt_id": attempt_id, "batch_id": BATCH, "event": "RESERVED"})
    )
    return root


class JQuantsV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.snapshot = source._source_snapshot(REPO)
        cls.first = cls.snapshot.plans[0]

    def one_day_plan(self):
        return replace(self.first, network_dates=(self.first.network_dates[0],))

    def collect(
        self,
        base: Path,
        plan,
        responses: list[FakeResponse],
    ):
        attempt = attempt_id_for(BATCH, plan.month)
        staging = staging_root(base, attempt, plan.month)
        clock = FakeClock()
        transport = FakeTransport(responses)
        summary = monthly._collect_month(
            REPO,
            self.snapshot,
            plan,
            staging,
            BATCH,
            attempt,
            "unit_test_key",
            transport=transport,
            monotonic_ns=clock.monotonic_ns,
            sleep_seconds=clock.sleep,
            utc_ns=clock.utc_ns,
        )
        return staging, transport, summary

    def test_01_exact_source_and_plan_counts(self) -> None:
        result = source.verify_source_preflight(REPO)
        self.assertEqual(result["raw_tree_sha256"], EXP009_RAW_TREE_SHA256)
        self.assertEqual(result["registry_artifact_sha256"], EXP009_REGISTRY_SHA256)
        self.assertEqual(result["session_list_sha256"], EXP009_SESSION_LIST_SHA256)
        self.assertEqual(result["session_date_count"], SESSION_DATE_COUNT)
        self.assertEqual(result["network_date_count"], NETWORK_DATE_COUNT)
        self.assertEqual(tuple(result["reuse_dates"]), REUSE_DATES)
        self.assertEqual([item.month for item in self.snapshot.plans], sorted(item.month for item in self.snapshot.plans))

    def test_02_postflight_hash_drift_fails(self) -> None:
        with mock.patch.object(source, "EXP009_POSTFLIGHT_SHA256", "0" * 64):
            with self.assertRaisesRegex(ContractError, "EXP009_POSTFLIGHT_DRIFT"):
                source.verify_source_preflight(REPO)

    def test_03_dry_plan_is_read_only_and_exact(self) -> None:
        raw = REPO / "data/raw/jquants_v2_bars_monthly_v4"
        before = raw.exists()
        result = monthly.dry_plan(REPO)
        self.assertEqual(raw.exists(), before)
        self.assertEqual(result["month_count"], 23)
        self.assertEqual(result["session_date_count"], 465)
        self.assertEqual(result["network_date_count"], 462)
        self.assertEqual(result["network_requests"], 0)

    def test_04_batch_reservation_is_ocl_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.object(monthly, "_source_snapshot", return_value=self.snapshot):
                first = monthly.reserve_batch_and_emit_source_binding(root, BATCH)
                self.assertEqual(first["source_binding_sha256"], self.snapshot.binding_sha256)
                with self.assertRaises(FileExistsError):
                    monthly.reserve_batch_and_emit_source_binding(root, BATCH)

    def test_05_month_attempt_is_unique_and_new_batch_id_changes_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            staging, _, attempt = monthly._reserve_month(root, BATCH, self.first.month)
            self.assertTrue(staging.is_dir())
            with self.assertRaises(ContractError):
                monthly._reserve_month(root, BATCH, self.first.month)
            other = "exp_20260828_010_monthly_formal_002"
            other_staging, _, other_attempt = monthly._reserve_month(root, other, self.first.month)
            self.assertNotEqual(attempt, other_attempt)
            self.assertTrue(other_staging.is_dir())

    def test_06_successful_month_is_raw_first_valid_and_no_clobber(self) -> None:
        plan = self.one_day_plan()
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            staging, transport, summary = self.collect(
                base, plan, [FakeResponse(envelope(plan.network_dates[0]))]
            )
            validated = monthly.validate_month_shard(REPO, staging, self.snapshot, plan)
            self.assertEqual(summary["request_count"], 1)
            self.assertEqual(validated["network_date_count"], 1)
            self.assertEqual(len(transport.calls), 1)
            final = base / "final"
            monthly._publish(staging, final)
            self.assertTrue(final.is_dir())
            with self.assertRaises(ContractError):
                monthly._publish(final, final)

    def test_07_pagination_uses_only_prior_key(self) -> None:
        plan = self.one_day_plan()
        day = plan.network_dates[0]
        with tempfile.TemporaryDirectory() as folder:
            staging, transport, summary = self.collect(
                Path(folder),
                plan,
                [FakeResponse(envelope(day, next_key="prior1")), FakeResponse(envelope(day, code="13020"))],
            )
            monthly.validate_month_shard(REPO, staging, self.snapshot, plan)
            self.assertEqual(summary["request_count"], 2)
            self.assertNotIn("pagination_key", transport.calls[0][1])
            self.assertIn("pagination_key=prior1", transport.calls[1][1])

    def test_08_page_cap_stops_without_ninth_request(self) -> None:
        plan = self.one_day_plan()
        day = plan.network_dates[0]
        with tempfile.TemporaryDirectory() as folder:
            responses = [FakeResponse(envelope(day, code=f"{1301 + index}0", next_key=f"k{index}")) for index in range(8)]
            attempt = attempt_id_for(BATCH, plan.month)
            staging = staging_root(Path(folder), attempt, plan.month)
            clock = FakeClock(); transport = FakeTransport(responses)
            with self.assertRaisesRegex(ContractError, "PAGE_CAP"):
                monthly._collect_month(
                    REPO, self.snapshot, plan, staging, BATCH, attempt,
                    "unit_test_key", transport=transport,
                    monotonic_ns=clock.monotonic_ns, sleep_seconds=clock.sleep,
                    utc_ns=clock.utc_ns,
                )
            self.assertEqual(len(transport.calls), 8)

    def test_09_http_failure_preserves_raw_and_receipt_before_stop(self) -> None:
        plan = self.one_day_plan()
        with tempfile.TemporaryDirectory() as folder:
            attempt = attempt_id_for(BATCH, plan.month)
            staging = staging_root(Path(folder), attempt, plan.month)
            clock = FakeClock(); transport = FakeTransport([FakeResponse(b'{"error":"x"}\n', status=429)])
            with self.assertRaisesRegex(ContractError, "HTTP_429"):
                monthly._collect_month(
                    REPO, self.snapshot, plan, staging, BATCH, attempt,
                    "unit_test_key", transport=transport,
                    monotonic_ns=clock.monotonic_ns, sleep_seconds=clock.sleep,
                    utc_ns=clock.utc_ns,
                )
            self.assertEqual(len(list((staging / "responses").glob("*.json"))), 1)
            self.assertEqual(len(list((staging / "response_receipts").glob("*.json"))), 1)
            self.assertIn("STOPPED_FIRST_FAILURE", (staging / "attempt_ledger.jsonl").read_text("utf-8"))

    def test_10_redirect_is_not_followed_and_is_preserved(self) -> None:
        plan = self.one_day_plan()
        with tempfile.TemporaryDirectory() as folder:
            attempt = attempt_id_for(BATCH, plan.month)
            staging = staging_root(Path(folder), attempt, plan.month)
            clock = FakeClock(); transport = FakeTransport([FakeResponse(b"{}\n", status=302, location="https://evil.invalid")])
            with self.assertRaisesRegex(ContractError, "REDIRECT"):
                monthly._collect_month(
                    REPO, self.snapshot, plan, staging, BATCH, attempt,
                    "unit_test_key", transport=transport,
                    monotonic_ns=clock.monotonic_ns, sleep_seconds=clock.sleep,
                    utc_ns=clock.utc_ns,
                )
            receipt = strict_json(next((staging / "response_receipts").glob("*.json")).read_bytes())
            self.assertTrue(receipt["redirected"])
            self.assertEqual(len(transport.calls), 1)

    def test_11_free18_and_exact_date_fail_closed_after_raw_receipt(self) -> None:
        plan = self.one_day_plan(); day = plan.network_dates[0]
        bad = bar(day); bad.pop("MktCap")
        for body in (json_file_bytes({"data": [bad]}), envelope("2024-07-31")):
            with self.subTest(body=body[:30]), tempfile.TemporaryDirectory() as folder:
                attempt = attempt_id_for(BATCH, plan.month)
                staging = staging_root(Path(folder), attempt, plan.month)
                clock = FakeClock(); transport = FakeTransport([FakeResponse(body)])
                with self.assertRaises(ContractError):
                    monthly._collect_month(
                        REPO, self.snapshot, plan, staging, BATCH, attempt,
                        "unit_test_key", transport=transport,
                        monotonic_ns=clock.monotonic_ns, sleep_seconds=clock.sleep,
                        utc_ns=clock.utc_ns,
                    )
                self.assertEqual(len(list((staging / "responses").glob("*.json"))), 1)
                self.assertEqual(len(list((staging / "response_receipts").glob("*.json"))), 1)

    def test_12_duplicate_date_code_across_pages_fails(self) -> None:
        plan = self.one_day_plan(); day = plan.network_dates[0]
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ContractError, "BAR_ORDER_OR_DUPLICATE"):
                self.collect(
                    Path(folder), plan,
                    [FakeResponse(envelope(day, next_key="p")), FakeResponse(envelope(day))],
                )

    def test_13_one_nanosecond_short_receipt_fails_replay(self) -> None:
        plan = self.one_day_plan(); day = plan.network_dates[0]
        with tempfile.TemporaryDirectory() as folder:
            staging, _, _ = self.collect(Path(folder), plan, [FakeResponse(envelope(day))])
            receipt_path = next((staging / "response_receipts").glob("*.json"))
            receipt = strict_json(receipt_path.read_bytes())
            receipt["send_monotonic_ns"] -= 1
            with self.assertRaisesRegex(ContractError, "SPACING_SHORT"):
                monthly._receipt_timing([receipt], attempt_id_for(BATCH, plan.month))

    def test_14_nonchronological_existing_shards_fail(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            later = monthly._raw_root(root) / "months" / self.snapshot.plans[1].month / "final"
            later.mkdir(parents=True)
            with self.assertRaisesRegex(ContractError, "NON_CHRONOLOGICAL_SHARDS"):
                monthly._completed_prefix(root, self.snapshot)

    def test_15_formal_revalidates_startup_and_before_month(self) -> None:
        plan = self.one_day_plan()
        snap = replace(self.snapshot, plans=(plan,))
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(monthly, "_source_snapshot", side_effect=[snap, snap]), \
             mock.patch.object(monthly, "_adopt_batch", return_value=Path(folder) / "ledger"), \
             mock.patch.object(monthly, "_completed_prefix", return_value=0), \
             mock.patch.object(monthly, "_reserve_month", return_value=(Path(folder) / "staging", Path(folder) / "final", attempt_id_for(BATCH, plan.month))), \
             mock.patch.object(monthly, "_collect_month"), \
             mock.patch.object(monthly, "validate_month_shard"), \
             mock.patch.object(monthly, "_publish"), \
             mock.patch.object(monthly, "build_global_catalog", return_value={"catalog_sha256": "a" * 64}), \
             mock.patch.object(monthly, "_append"), \
             mock.patch.dict(os.environ, {"JQUANTS_API_KEY": "unit_test_key"}, clear=False):
            result = monthly.launch_formal(Path(folder), BATCH, transport=FakeTransport([]), pre_reserved=True)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertNotIn("JQUANTS_API_KEY", os.environ)

    def test_16_stop_first_month_failure(self) -> None:
        plans = tuple(replace(item, network_dates=(item.network_dates[0],)) for item in self.snapshot.plans[:2])
        snap = replace(self.snapshot, plans=plans)
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(monthly, "_source_snapshot", side_effect=[snap, snap]), \
             mock.patch.object(monthly, "_adopt_batch", return_value=Path(folder) / "ledger"), \
             mock.patch.object(monthly, "_completed_prefix", return_value=0), \
             mock.patch.object(monthly, "_reserve_month", return_value=(Path(folder) / "staging", Path(folder) / "final", attempt_id_for(BATCH, plans[0].month))) as reserve, \
             mock.patch.object(monthly, "_collect_month", side_effect=ContractError("FIRST_FAIL")), \
             mock.patch.object(monthly, "_append"), \
             mock.patch.dict(os.environ, {"JQUANTS_API_KEY": "unit_test_key"}, clear=False):
            with self.assertRaisesRegex(ContractError, "FIRST_FAIL"):
                monthly.launch_formal(Path(folder), BATCH, transport=FakeTransport([]), pre_reserved=True)
            self.assertEqual(reserve.call_count, 1)
            self.assertNotIn("JQUANTS_API_KEY", os.environ)

    def test_17_global_catalog_is_deterministic_and_not_overwritten(self) -> None:
        def validation(_repo, _root, _snapshot, plan):
            return {
                "manifest_sha256": "1" * 64,
                "network_date_count": len(plan.network_dates),
                "raw_tree_sha256": "2" * 64,
                "request_count": len(plan.network_dates),
                "row_count": len(plan.network_dates),
            }
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(monthly, "_source_snapshot", return_value=self.snapshot), \
             mock.patch.object(monthly, "validate_month_shard", side_effect=validation):
            root = Path(folder)
            first = monthly.build_global_catalog(root)
            path = root / first["catalog_relative_path"]
            before = path.read_bytes()
            second = monthly.build_global_catalog(root)
            self.assertEqual(first, second)
            self.assertEqual(path.read_bytes(), before)

    def test_18_launcher_orders_preflight_binding_env_and_cleanup(self) -> None:
        script = (REPO / "src/quant_research/alpha_models/data/jquants_v2_bars_monthly_v4/launcher.ps1").read_text("utf-8")
        self.assertLess(script.index("--source-preflight-check"), script.index("CreateDirectory($controlRoot)"))
        self.assertLess(script.index("--reserve-batch-and-emit-source-binding"), script.index(".env.jquants.local"))
        self.assertIn("Remove-Item Env:JQUANTS_API_KEY", script)
        self.assertNotIn("Invoke-WebRequest", script)
        self.assertNotIn("Invoke-RestMethod", script)

    def test_19_tracked_v4_artifacts_contain_no_raw_rows_or_secret(self) -> None:
        paths = list((REPO / "src/quant_research/alpha_models/data/jquants_v2_bars_monthly_v4").glob("*"))
        paths += list((REPO / "experiments/exp_20260828_010").glob("*"))
        for path in paths:
            if not path.is_file():
                continue
            body = path.read_bytes()
            self.assertNotIn(b'"AdjFactor":', body)
            self.assertNotIn(b"x-api-key=", body.lower())
            self.assertIsNone(re.search(rb"JQUANTS_API_KEY=[A-Za-z0-9_-]{20,}", body))

    def test_20_raw_tamper_fails_reparse(self) -> None:
        plan = self.one_day_plan(); day = plan.network_dates[0]
        with tempfile.TemporaryDirectory() as folder:
            staging, _, _ = self.collect(Path(folder), plan, [FakeResponse(envelope(day))])
            raw = next((staging / "responses").glob("*.json"))
            raw.write_bytes(raw.read_bytes() + b" ")
            with self.assertRaisesRegex(ContractError, "MONTH_FILE_HASH"):
                monthly.validate_month_shard(REPO, staging, self.snapshot, plan)

    def test_21_manifest_path_traversal_fails(self) -> None:
        plan = self.one_day_plan(); day = plan.network_dates[0]
        with tempfile.TemporaryDirectory() as folder:
            staging, _, _ = self.collect(Path(folder), plan, [FakeResponse(envelope(day))])
            path = staging / "month_manifest.json"
            manifest = strict_json(path.read_bytes())
            manifest["files"][0]["relative_path"] = "../escape"
            path.write_bytes(json_file_bytes(manifest))
            with self.assertRaisesRegex(ContractError, "RELATIVE_PATH"):
                monthly.validate_month_shard(REPO, staging, self.snapshot, plan)

    def test_22_symlinked_receipt_directory_fails_when_supported(self) -> None:
        plan = self.one_day_plan(); day = plan.network_dates[0]
        with tempfile.TemporaryDirectory() as folder:
            staging, _, _ = self.collect(Path(folder), plan, [FakeResponse(envelope(day))])
            receipt_dir = staging / "response_receipts"
            actual = staging / "receipt_actual"
            receipt_dir.rename(actual)
            try:
                os.symlink(actual, receipt_dir, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(ContractError, "UNTRUSTED_DIRECTORY"):
                monthly.validate_month_shard(REPO, staging, self.snapshot, plan)


if __name__ == "__main__":
    unittest.main()
