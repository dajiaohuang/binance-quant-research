from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.contracts import ContractError
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v5 import recovery as v5_recovery
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v6 import recovery
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v6.contracts import (
    BATCH_ID,
    EXPECTED_ADOPTED_COUNT,
    EXPECTED_FIRST_NETWORK_DATE,
    EXPECTED_REMAINING_NETWORK_COUNT,
)


REPO = Path(__file__).resolve().parents[1]
RAW_ROOT = REPO / "data/raw/jquants_v2_bars_monthly_v4"
AUG_FAILED = RAW_ROOT / "months/2024-08/.jquants-bars-202408-attempt002.staging"


def tree_hash(root: Path) -> str:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        body = path.read_bytes()
        rows.append([path.relative_to(root).as_posix(), len(body), hashlib.sha256(body).hexdigest()])
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


class _Response:
    pass


class _Connection:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.requests = 0
        self.closed = 0

    def request(self, method: str, path: str, headers: dict[str, str]) -> None:
        self.requests += 1
        if self.fail:
            raise OSError("synthetic send failure")

    def getresponse(self) -> _Response:
        return _Response()

    def close(self) -> None:
        self.closed += 1


class OperationalRecoveryTests(unittest.TestCase):
    def test_01_generic_adoption_and_exact_dry_boundary(self) -> None:
        before_july = tree_hash(RAW_ROOT / "months/2024-07")
        before_aug = tree_hash(AUG_FAILED)
        state = recovery._prior_state(REPO)
        adopted = tuple(item["session_date"] for item in state["document"]["adopted_entries"])
        self.assertEqual(len(adopted), EXPECTED_ADOPTED_COUNT)
        self.assertEqual(len(set(adopted)), EXPECTED_ADOPTED_COUNT)
        self.assertEqual(adopted[-1], "2024-08-09")
        result = recovery.dry_recovery_plan(REPO)
        self.assertEqual(result["batch_id"], BATCH_ID)
        self.assertEqual(result["adopted_network_date_count"], EXPECTED_ADOPTED_COUNT)
        self.assertEqual(result["remaining_network_date_count"], EXPECTED_REMAINING_NETWORK_COUNT)
        self.assertEqual(result["first_network_date"], EXPECTED_FIRST_NETWORK_DATE)
        self.assertTrue(result["no_overlap"])
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(tree_hash(RAW_ROOT / "months/2024-07"), before_july)
        self.assertEqual(tree_hash(AUG_FAILED), before_aug)

    def test_02_duplicate_or_conflicting_leaf_is_rejected(self) -> None:
        state = recovery._prior_state(REPO)
        entries = copy.deepcopy(state["document"]["adopted_entries"])
        entries[-1] = copy.deepcopy(entries[0])
        entries[-1]["row_count"] = int(entries[-1]["row_count"]) + 1
        with self.assertRaisesRegex(ContractError, "GENERIC_DUPLICATE_OR_AMBIGUOUS_DATE"):
            recovery._registry_from_entries(state["snapshot"], entries)

    def test_03_partial_failed_leaf_is_rejected(self) -> None:
        state = recovery._prior_state(REPO)
        aug_plan = next(item for item in state["v5_plans"] if item.month == "2024-08")
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            copied = base / AUG_FAILED.name
            shutil.copytree(AUG_FAILED, copied)
            sorted((copied / "date_manifests").glob("*.json"))[-1].unlink()
            reservation = json.loads((copied / "attempt.reservation.json").read_text("utf-8"))
            with self.assertRaisesRegex(ContractError, "FILE_MISSING|GENERIC_DATE_MANIFEST_BINDING"):
                recovery._extract_day_entries(
                    base,
                    copied,
                    state["snapshot"],
                    aug_plan,
                    reservation["attempt_id"],
                    reservation["batch_id"],
                )

    def test_04_one_connection_is_reused_and_closed(self) -> None:
        connection = _Connection()
        factory_calls = 0

        def factory() -> _Connection:
            nonlocal factory_calls
            factory_calls += 1
            return connection

        transport = recovery.PersistentHttpsTransport(factory)
        for day in ("2024-08-13", "2024-08-14"):
            response = transport.request(
                "api.jquants.com", f"/v2/equities/bars/daily?date={day}", {"x-api-key": "fixture"}
            )
            self.assertIsInstance(response, _Response)
        transport.close()
        self.assertEqual(factory_calls, 1)
        self.assertEqual(connection.requests, 2)
        self.assertEqual(connection.closed, 1)

    def test_05_send_failure_closes_without_retry(self) -> None:
        connection = _Connection(fail=True)
        factory_calls = 0

        def factory() -> _Connection:
            nonlocal factory_calls
            factory_calls += 1
            return connection

        transport = recovery.PersistentHttpsTransport(factory)
        with self.assertRaisesRegex(OSError, "synthetic send failure"):
            transport.request(
                "api.jquants.com", "/v2/equities/bars/daily?date=2024-08-13", {"x-api-key": "fixture"}
            )
        self.assertEqual(factory_calls, 1)
        self.assertEqual(connection.requests, 1)
        self.assertEqual(connection.closed, 1)

    def test_06_launcher_order_and_verified_https_configuration(self) -> None:
        launcher = (
            REPO / "src/quant_research/alpha_models/data/jquants_v2_bars_monthly_v6/launcher.ps1"
        ).read_text("utf-8")
        source = Path(recovery.__file__).read_text("utf-8")
        self.assertLess(launcher.index("--recovery-preflight-check"), launcher.index("CreateDirectory($controlRoot)"))
        self.assertLess(launcher.index("--reserve-recovery-batch"), launcher.index(".env.jquants.local"))
        self.assertIn("Remove-Item Env:JQUANTS_API_KEY", launcher)
        self.assertNotIn("Invoke-WebRequest", launcher)
        self.assertNotIn("Invoke-RestMethod", launcher)
        self.assertIn("http.client.HTTPSConnection(", source)
        self.assertIn("API_HOST, timeout=60, context=ssl.create_default_context()", source)
        self.assertIn("finally:\n                transport.close()", source)


if __name__ == "__main__":
    unittest.main()
