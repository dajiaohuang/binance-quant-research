from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quant_research.binance_spot_archive import symbol_evidence_directory_name
from quant_research.binance_spot_payload import (
    CLOSE_TIME_POLICY_WITHIN_OPEN_INTERVAL,
    ContractError,
    CsvValidationError,
    INTERVAL_MILLISECONDS,
    load_frozen_inputs,
    validate_kline_zip_bytes,
)
from quant_research.binance_spot_revalidate import run_offline_revalidation


SYMBOL = "AAAUSDT"
MONTH = "2023-01"
OPEN_MS = 1_672_531_200_000
ETAG = "a" * 32
LAST_MODIFIED = "2025-01-07T23:35:01.000Z"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_json(value: object, *, pretty: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def kline_row(close_time_ms: int) -> str:
    return (
        f"{OPEN_MS},100,110,90,105,2,{close_time_ms},210,3,1,100,0\n"
    )


def zip_payload(close_time_ms: int) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"{SYMBOL}-1h-{MONTH}.csv",
            kline_row(close_time_ms),
        )
    return output.getvalue()


class CloseTimePolicyTests(unittest.TestCase):
    def test_exact_remains_default_and_reports_no_non_nominal_event(self) -> None:
        nominal = OPEN_MS + INTERVAL_MILLISECONDS - 1
        result = validate_kline_zip_bytes(
            zip_payload(nominal), symbol=SYMBOL, month=MONTH
        )
        self.assertEqual(result.rows[0][6], str(nominal))
        self.assertEqual(result.non_nominal_close_events, ())

    def test_within_interval_preserves_and_reports_non_nominal_close(self) -> None:
        actual = OPEN_MS + 1_234
        with self.assertRaisesRegex(CsvValidationError, "does not equal"):
            validate_kline_zip_bytes(
                zip_payload(actual), symbol=SYMBOL, month=MONTH
            )
        result = validate_kline_zip_bytes(
            zip_payload(actual),
            symbol=SYMBOL,
            month=MONTH,
            close_time_policy=CLOSE_TIME_POLICY_WITHIN_OPEN_INTERVAL,
        )
        self.assertEqual(result.rows[0][6], str(actual))
        self.assertEqual(len(result.non_nominal_close_events), 1)
        event = result.non_nominal_close_events[0]
        self.assertEqual(event.actual_close_time_ms, actual)
        self.assertEqual(
            event.shortfall_ms,
            OPEN_MS + INTERVAL_MILLISECONDS - 1 - actual,
        )

    def test_within_interval_rejects_before_open_and_after_nominal(self) -> None:
        for invalid in (OPEN_MS - 1, OPEN_MS + INTERVAL_MILLISECONDS):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(CsvValidationError, "outside"):
                    validate_kline_zip_bytes(
                        zip_payload(invalid),
                        symbol=SYMBOL,
                        month=MONTH,
                        close_time_policy=CLOSE_TIME_POLICY_WITHIN_OPEN_INTERVAL,
                    )


class OfflineRevalidationTests(unittest.TestCase):
    def _fixture(self, root: Path) -> dict[str, object]:
        actual_close = OPEN_MS + 1_234
        zip_bytes = zip_payload(actual_close)
        zip_sha = sha256(zip_bytes)
        zip_name = f"{SYMBOL}-1h-{MONTH}.zip"
        checksum_bytes = f"{zip_sha}  {zip_name}\n".encode("ascii")

        raw_root = root / "raw"
        raw_directory = raw_root / symbol_evidence_directory_name(SYMBOL) / MONTH
        raw_directory.mkdir(parents=True)
        zip_path = raw_directory / "payload.zip"
        checksum_path = raw_directory / "payload.zip.CHECKSUM"
        zip_path.write_bytes(zip_bytes)
        checksum_path.write_bytes(checksum_bytes)

        zip_key = f"data/spot/monthly/klines/{SYMBOL}/1h/{zip_name}"
        records = [
            {
                "key": zip_key,
                "symbol": SYMBOL,
                "month": MONTH,
                "object_type": "zip",
                "size": len(zip_bytes),
                "etag": ETAG,
                "last_modified": LAST_MODIFIED,
            },
            {
                "key": zip_key + ".CHECKSUM",
                "symbol": SYMBOL,
                "month": MONTH,
                "object_type": "checksum",
                "size": len(checksum_bytes),
                "etag": ETAG,
                "last_modified": LAST_MODIFIED,
            },
        ]
        inventory_bytes = b"".join(stable_json(row) for row in records)
        inventory_path = root / "inventory.jsonl"
        inventory_path.write_bytes(inventory_bytes)
        inventory_sha = sha256(inventory_bytes)

        symbol_bytes = stable_json({"symbol": SYMBOL, "suffix_candidate": True})
        symbol_index_path = root / "symbols.jsonl"
        symbol_index_path.write_bytes(symbol_bytes)
        symbol_index_sha = sha256(symbol_bytes)
        symbol_list_sha = sha256(f"{SYMBOL}\n".encode())

        inventory_summary = {
            "archive": {
                "candidate_symbol_count": 1,
                "symbol_index_sha256": symbol_index_sha,
            },
            "inventory": {
                "zip_count": 1,
                "checksum_count": 1,
                "inventory_record_count": 2,
                "inventory_jsonl_sha256": inventory_sha,
            },
        }
        inventory_summary_bytes = stable_json(inventory_summary)
        inventory_summary_path = root / "inventory_summary.json"
        inventory_summary_path.write_bytes(inventory_summary_bytes)
        inventory_summary_sha = sha256(inventory_summary_bytes)

        def evidence(key: str, path: Path, payload: bytes) -> dict[str, object]:
            return {
                "key": key,
                "frozen_size": len(payload),
                "frozen_etag": ETAG,
                "downloaded_bytes": len(payload),
                "response_content_length": len(payload),
                "local_path": str(path),
                "local_sha256": sha256(payload),
            }

        prior_quality = {
            "symbol": SYMBOL,
            "month": MONTH,
            "zip_key": zip_key,
            "checksum_key": zip_key + ".CHECKSUM",
            "status": "U",
            "failure_code": "CSV_VALIDATION_ERROR",
            "zip_evidence": evidence(zip_key, zip_path, zip_bytes),
            "checksum_evidence": evidence(
                zip_key + ".CHECKSUM", checksum_path, checksum_bytes
            ),
        }
        prior_quality_bytes = stable_json(prior_quality)
        prior_quality_path = root / "exp004_quality.jsonl"
        prior_quality_path.write_bytes(prior_quality_bytes)
        prior_quality_sha = sha256(prior_quality_bytes)

        prior_summary = {
            "pairs_expected": 1,
            "object_quality_sha256": prior_quality_sha,
            "frozen_inputs": {
                "inventory_sha256": inventory_sha,
                "symbol_index_sha256": symbol_index_sha,
                "summary_sha256": inventory_summary_sha,
                "symbol_list_sha256": symbol_list_sha,
            },
        }
        prior_summary_bytes = stable_json(prior_summary)
        prior_summary_path = root / "exp004_summary.json"
        prior_summary_path.write_bytes(prior_summary_bytes)

        run_contract = {
            "inventory_sha256": inventory_sha,
            "symbol_index_sha256": symbol_index_sha,
            "summary_sha256": inventory_summary_sha,
            "symbol_list_sha256": symbol_list_sha,
            "pair_count": 1,
            "candidate_symbol_count": 1,
        }
        run_contract_bytes = stable_json(run_contract, pretty=True)
        run_contract_path = root / "run_contract.json"
        run_contract_path.write_bytes(run_contract_bytes)

        return {
            "inventory_path": inventory_path,
            "inventory_sha256": inventory_sha,
            "symbol_index_path": symbol_index_path,
            "symbol_index_sha256": symbol_index_sha,
            "inventory_summary_path": inventory_summary_path,
            "inventory_summary_sha256": inventory_summary_sha,
            "exp004_object_quality_path": prior_quality_path,
            "exp004_object_quality_sha256": prior_quality_sha,
            "exp004_payload_summary_path": prior_summary_path,
            "exp004_payload_summary_sha256": sha256(prior_summary_bytes),
            "raw_run_contract_path": run_contract_path,
            "raw_run_contract_sha256": sha256(run_contract_bytes),
            "raw_root": raw_root,
            "processed_root": root / "processed_v4",
            "summary_output": root / "out" / "summary.json",
            "object_quality_output": root / "out" / "quality.jsonl",
            "non_nominal_close_output": root / "out" / "non_nominal.jsonl",
            "coverage_schema_output": root / "out" / "schema.json",
            "derived_index_output": root / "out" / "index.jsonl",
            "expected_objects": 2,
            "expected_pairs": 1,
            "expected_candidate_symbols": 1,
            "expected_symbol_list_sha256": symbol_list_sha,
            "panel_start_ms": OPEN_MS,
            "panel_end_ms": 1_675_209_600_000,
            "expected_non_nominal_event_rows": 1,
            "expected_affected_object_months": 1,
            "expected_normalized_rows": 1,
            "expected_state_counts": {"A": 1, "M": 743, "N": 0, "U": 0},
        }

    def test_frozen_exp004_provenance_failure_prevents_outputs(self) -> None:
        with TemporaryDirectory() as temporary:
            kwargs = self._fixture(Path(temporary))
            Path(kwargs["exp004_object_quality_path"]).write_bytes(b"changed\n")
            with self.assertRaisesRegex(ContractError, "SHA-256 mismatch"):
                run_offline_revalidation(**kwargs)
            self.assertFalse(Path(kwargs["summary_output"]).exists())

    def test_minimal_offline_end_to_end_preserves_non_nominal_close(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            kwargs = self._fixture(root)
            summary, exit_code = run_offline_revalidation(**kwargs)
            event_rows = [
                json.loads(line)
                for line in Path(kwargs["non_nominal_close_output"])
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            derived = json.loads(
                Path(kwargs["derived_index_output"])
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            with gzip.open(
                derived["normalized_path"], "rt", encoding="utf-8", newline=""
            ) as stream:
                normalized = list(csv.reader(stream))

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["pairs_valid"], 1)
        self.assertEqual(summary["exp004_evidence_records"], 2)
        self.assertEqual(summary["non_nominal_close_event_count"], 1)
        self.assertEqual(summary["affected_object_month_count"], 1)
        self.assertEqual(summary["contract_failures"], [])
        self.assertEqual(summary["derived"]["state_counts"]["U"], 0)
        self.assertEqual(normalized[1][6], str(OPEN_MS + 1_234))
        self.assertEqual(
            set(event_rows[0]),
            {
                "symbol",
                "month",
                "open_time_ms",
                "actual_close_time_ms",
                "nominal_close_time_ms",
                "shortfall_ms",
                "source_zip_sha256",
                "anomaly_code",
                "zip_key",
            },
        )
        self.assertEqual(
            event_rows[0]["anomaly_code"],
            "NON_NOMINAL_CLOSE_TIME_WITHIN_INTERVAL",
        )

    def test_success_invariant_deviation_is_inconclusive_and_exit_two(self) -> None:
        with TemporaryDirectory() as temporary:
            kwargs = self._fixture(Path(temporary))
            kwargs["expected_non_nominal_event_rows"] = 2
            summary, exit_code = run_offline_revalidation(**kwargs)

        self.assertEqual(exit_code, 2)
        self.assertEqual(summary["decision"], "INCONCLUSIVE")
        self.assertTrue(
            any(
                failure.startswith("non_nominal_close_event_count:")
                for failure in summary["contract_failures"]
            )
        )

    def test_pair_validation_uses_one_immutable_read_per_raw_object(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "quant_research"
            / "binance_spot_revalidate.py"
        ).read_text(encoding="utf-8")
        pair_body = source.split("def _revalidate_pair(", 1)[1].split(
            "def run_offline_revalidation(", 1
        )[0]
        self.assertNotIn("_sha256_file(path)", pair_body)
        self.assertEqual(pair_body.count("path.read_bytes()"), 1)
        self.assertIn('immutable_payloads["CHECKSUM"]', pair_body)
        self.assertIn('immutable_payloads["ZIP"]', pair_body)

    def test_offline_module_has_no_transport_import_or_call(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "quant_research"
            / "binance_spot_revalidate.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("url" + "lib", source)
        self.assertNotIn("url" + "open", source)


if __name__ == "__main__":
    unittest.main()
