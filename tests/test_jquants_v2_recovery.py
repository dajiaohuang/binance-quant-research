from __future__ import annotations

import json
from pathlib import Path
import unittest

from quant_research.alpha_models.data.jquants_v2_recovery import (
    EXPECTED_RAW_TREE_SHA256,
    PACING_STATUS,
    validate_source_probe,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_STAGING = (
    REPO_ROOT
    / "data/raw/jquants_v2_v4/runs/.exp_20260828_005_formal_001.staging"
)


@unittest.skipUnless(SOURCE_STAGING.is_dir(), "licensed local source staging is absent")
class JQuantsV2OfflineRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = validate_source_probe(REPO_ROOT)
        cls.manifest = cls.result.recovery_manifest
        cls.summary = cls.result.source_summary
        cls.pointer = cls.result.adapter_pointer

    def test_exact_source_binding(self) -> None:
        binding = self.manifest["source_binding"]
        self.assertEqual(binding["source_experiment_id"], "exp_20260828_005")
        self.assertEqual(binding["source_run_id"], "exp_20260828_005_formal_001")
        self.assertEqual(binding["raw_tree_sha256"], EXPECTED_RAW_TREE_SHA256)

    def test_all_five_raw_and_receipt_files_are_bijective(self) -> None:
        validation = self.manifest["validation"]
        self.assertEqual(validation["http_request_count"], 5)
        self.assertEqual(validation["receipt_count"], 5)
        self.assertTrue(validation["raw_receipt_bijection"])
        self.assertEqual(len(self.manifest["source_files"]), 5)

    def test_transport_receipts_are_direct_successes(self) -> None:
        validation = self.manifest["validation"]
        self.assertTrue(validation["direct_http_200_json_no_redirect"])
        self.assertEqual(validation["retry_count"], 0)
        self.assertEqual(validation["pagination_key_count"], 0)

    def test_raw_hashes_and_schema_validate(self) -> None:
        validation = self.manifest["validation"]
        self.assertTrue(validation["all_raw_hashes_match"])
        self.assertTrue(validation["schema_valid"])
        self.assertTrue(validation["all_five_queries_semantically_valid"])

    def test_source_counts_are_stable(self) -> None:
        self.assertEqual(self.summary["calendar"]["civil_date_rows"], 4)
        self.assertEqual(self.summary["master"]["unique_merged_rows"], 1)
        self.assertEqual(self.summary["daily_bars"]["q04_rows"], 4410)
        self.assertEqual(self.summary["daily_bars"]["q05_rows"], 5)
        self.assertEqual(self.summary["daily_bars"]["merged_distinct_rows"], 4414)

    def test_nontrading_master_mapping_is_explicit(self) -> None:
        master = self.summary["master"]
        self.assertEqual(master["normal_query_rows"], 1)
        self.assertEqual(master["nontrading_query_rows"], 1)
        self.assertTrue(master["nontrading_requested_date_mapped_to_next_business_date"])
        self.assertTrue(master["sector17_nonempty"])
        self.assertTrue(master["sector33_nonempty"])

    def test_split_probe_expectation_is_preserved(self) -> None:
        bars = self.summary["daily_bars"]
        self.assertTrue(bars["q05_expected_session_coverage_matches"])
        self.assertTrue(bars["split_factor_and_ex_right_expectation_matches"])

    def test_pacing_is_not_promoted_to_pass(self) -> None:
        pacing = self.manifest["pacing"]
        self.assertEqual(pacing["status"], PACING_STATUS)
        self.assertNotEqual(pacing["status"], "PASS")
        self.assertEqual(pacing["wall_clock_send_gap_ms"], [13008, 13001, 12994, 12999])

    def test_failed_promotion_state_is_preserved(self) -> None:
        control = self.manifest["control_state"]
        self.assertTrue(control["staging_present"])
        self.assertTrue(control["control_present"])
        self.assertFalse(control["final_present"])
        self.assertFalse(control["authorization_present"])
        self.assertEqual(control["failure_code"], "RATE_PACING")

    def test_recovery_does_not_authorize_research(self) -> None:
        authorization = self.manifest["authorization"]
        self.assertFalse(authorization["empirical_authorized"])
        self.assertFalse(authorization["training_authorized"])
        self.assertFalse(authorization["ic_authorized"])
        self.assertFalse(authorization["pnl_authorized"])
        self.assertFalse(authorization["backtest_authorized"])
        self.assertEqual(authorization["listing_presence"], "UNKNOWN")

    def test_git_safe_bundle_contains_no_raw_rows(self) -> None:
        encoded = json.dumps(self.result.bundle(), ensure_ascii=True, sort_keys=True)
        for forbidden in ('"CoName"', '"CoNameEn"', '"O"', '"H"', '"L"', '"C"'):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(self.pointer["licensed_raw_policy"], "LOCAL_GITIGNORED_SOURCE_PATH_ONLY")

    def test_persisted_git_safe_artifacts_match_validator(self) -> None:
        artifact_root = REPO_ROOT / "experiments/exp_20260828_006/artifacts"
        expected = {
            "recovery_manifest.json": self.manifest,
            "source_summary.json": self.summary,
            "adapter_pointer.json": self.pointer,
        }
        for name, value in expected.items():
            persisted = json.loads((artifact_root / name).read_text(encoding="utf-8"))
            self.assertEqual(persisted, value)


if __name__ == "__main__":
    unittest.main()
