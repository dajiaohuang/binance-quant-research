from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.contracts import (
    ContractError,
    json_file_bytes,
)
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v5 import recovery
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v5.contracts import (
    BATCH_ID,
    EXPECTED_ADOPTED_DATES,
    EXPECTED_FIRST_NETWORK_DATE,
    FAILED_STAGING_RELATIVE,
    RECOVERY_ATTEMPT_ID,
)


REPO = Path(__file__).resolve().parents[1]
FAILED = REPO / FAILED_STAGING_RELATIVE


def tree_hash(root: Path) -> str:
    entries = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        body = path.read_bytes()
        entries.append([path.relative_to(root).as_posix(), len(body), hashlib.sha256(body).hexdigest()])
    return hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class RecoveryTests(unittest.TestCase):
    def copied_failed(self, base: Path) -> Path:
        target = base / "failed"
        shutil.copytree(FAILED, target)
        return target

    def test_01_positive_pointer_adoption_and_dry_plan_start_july_08(self) -> None:
        before = tree_hash(FAILED)
        result = recovery._validate_failed_attempt(REPO)
        document = result["document"]
        self.assertEqual(
            tuple(item["session_date"] for item in document["adopted_entries"]),
            EXPECTED_ADOPTED_DATES,
        )
        self.assertEqual(sum(item["page_count"] for item in document["adopted_entries"]), 4)
        body = json_file_bytes(document)
        self.assertNotIn(b'"AdjFactor"', body)
        self.assertNotIn(b'"data"', body)
        plan = recovery.dry_recovery_plan(REPO)
        self.assertEqual(plan["batch_id"], BATCH_ID)
        self.assertEqual(plan["recovery_attempt_id"], RECOVERY_ATTEMPT_ID)
        self.assertEqual(plan["first_network_date"], EXPECTED_FIRST_NETWORK_DATE)
        self.assertEqual(plan["network_date_count"], 458)
        self.assertTrue(plan["no_duplicate_adopted_dates"])
        self.assertEqual(plan["network_requests"], 0)
        self.assertEqual(tree_hash(FAILED), before)

    def test_02_raw_hash_or_size_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            failed = self.copied_failed(Path(folder))
            raw = sorted((failed / "responses").glob("*.json"))[0]
            raw.write_bytes(raw.read_bytes() + b" ")
            with self.assertRaisesRegex(ContractError, "RECOVERY_RAW_BINDING"):
                recovery._validate_failed_attempt(
                    REPO, failed, require_frozen_partial_hash=False
                )

    def test_03_missing_or_partial_leaf_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            failed = self.copied_failed(Path(folder))
            sorted((failed / "response_receipts").glob("*.json"))[-1].unlink()
            with self.assertRaisesRegex(ContractError, "RECOVERY_ADOPTED_DATE_SET"):
                recovery._validate_failed_attempt(
                    REPO, failed, require_frozen_partial_hash=False
                )

    def test_04_free18_schema_drift_is_rejected_after_hash_rebinding(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            failed = self.copied_failed(Path(folder))
            receipt_path = sorted((failed / "response_receipts").glob("*.json"))[0]
            receipt = json.loads(receipt_path.read_text("utf-8"))
            raw_path = failed / receipt["raw_relative_path"]
            payload = json.loads(raw_path.read_text("utf-8"))
            payload["data"][0].pop("MktCap")
            body = json_file_bytes(payload)
            raw_path.write_bytes(body)
            receipt["body_bytes"] = len(body)
            receipt["body_sha256"] = hashlib.sha256(body).hexdigest()
            receipt_path.write_bytes(json_file_bytes(receipt))
            with self.assertRaisesRegex(ContractError, "FREE18_SCHEMA"):
                recovery._validate_failed_attempt(
                    REPO, failed, require_frozen_partial_hash=False
                )

    def test_05_page_chain_or_receipt_binding_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            failed = self.copied_failed(Path(folder))
            receipt_path = sorted((failed / "response_receipts").glob("*.json"))[0]
            receipt = json.loads(receipt_path.read_text("utf-8"))
            receipt["page_number"] = 2
            receipt_path.write_bytes(json_file_bytes(receipt))
            with self.assertRaisesRegex(ContractError, "RECOVERY_RECEIPT_BINDING"):
                recovery._validate_failed_attempt(
                    REPO, failed, require_frozen_partial_hash=False
                )

    def test_06_dry_plan_creates_no_recovery_attempt_or_key_access(self) -> None:
        control = REPO / "experiments/exp_20260828_011/formal_control"
        batch = REPO / "data/raw/jquants_v2_bars_monthly_v4/batches/exp_20260828_010_monthly_formal_002.reservation.lock"
        before = (control.exists(), batch.exists(), "JQUANTS_API_KEY" in os.environ)
        result = recovery.dry_recovery_plan(REPO)
        after = (control.exists(), batch.exists(), "JQUANTS_API_KEY" in os.environ)
        self.assertEqual(before, after)
        self.assertEqual(result["key_reads"], 0)
        self.assertEqual(result["network_requests"], 0)

    def test_07_launcher_orders_recovery_preflight_registry_env(self) -> None:
        script = (
            REPO
            / "src/quant_research/alpha_models/data/jquants_v2_bars_monthly_v5/launcher.ps1"
        ).read_text("utf-8")
        self.assertLess(
            script.index("--recovery-preflight-check"),
            script.index("CreateDirectory($controlRoot)"),
        )
        self.assertLess(
            script.index("--reserve-recovery-batch"),
            script.index(".env.jquants.local"),
        )
        self.assertIn("Remove-Item Env:JQUANTS_API_KEY", script)
        self.assertNotIn("Invoke-WebRequest", script)
        self.assertNotIn("Invoke-RestMethod", script)


if __name__ == "__main__":
    unittest.main()
