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

from quant_research.binance_spot_payload import (
    ChecksumError,
    ContractError,
    CsvValidationError,
    FrozenInputs,
    InventoryObject,
    PayloadPair,
    SourceChangedError,
    ZipValidationError,
    acquire_object,
    build_derived_outputs,
    load_frozen_inputs,
    parse_checksum,
    process_pair,
    validate_kline_zip,
)


ETAG = "a" * 32
LAST_MODIFIED_ISO = "2025-01-07T23:35:01.000Z"
LAST_MODIFIED_HTTP = "Tue, 07 Jan 2025 23:35:01 GMT"
OPEN_MS = 1_672_531_200_000


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def kline_row(open_ms: int = OPEN_MS, *, taker_base: str = "1") -> str:
    return (
        f"{open_ms},100,110,90,105,2,{open_ms + 3_599_999},210,3,"
        f"{taker_base},100,0\n"
    )


def zip_payload(
    *,
    symbol: str = "AAAUSDT",
    month: str = "2023-01",
    rows: str | None = None,
    member_name: str | None = None,
    extra_member: bool = False,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            member_name or f"{symbol}-1h-{month}.csv",
            rows if rows is not None else kline_row(),
        )
        if extra_member:
            archive.writestr("extra.csv", kline_row())
    return output.getvalue()


def inventory_object(
    *, symbol: str = "AAAUSDT", month: str = "2023-01", object_type: str = "zip",
    size: int = 1,
) -> InventoryObject:
    suffix = ".zip.CHECKSUM" if object_type == "checksum" else ".zip"
    return InventoryObject(
        key=f"data/spot/monthly/klines/{symbol}/1h/{symbol}-1h-{month}{suffix}",
        symbol=symbol,
        month=month,
        object_type=object_type,
        size=size,
        etag=ETAG,
        last_modified=LAST_MODIFIED_ISO,
    )


class FakeResponse:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        *,
        etag: str = ETAG,
        size: int | None = None,
        last_modified: str = LAST_MODIFIED_HTTP,
        response_url: str | None = None,
    ) -> None:
        self.status = status
        self.headers = {
            "ETag": f'"{etag}"',
            "Content-Length": str(len(body) if size is None else size),
            "Last-Modified": last_modified,
        }
        self._body = io.BytesIO(body)
        self._response_url = response_url

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self._response_url or (
            "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/"
            "data/spot/monthly/klines/AAAUSDT/1h/AAAUSDT-1h-2023-01.zip"
        )

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> bool:
        return False


class FrozenInputTests(unittest.TestCase):
    def _fixture(self, root: Path) -> dict[str, object]:
        symbols = (
            {"symbol": "AAAUSDT", "suffix_candidate": True},
            {"symbol": "币安人生USDT", "suffix_candidate": True},
        )
        symbol_bytes = b"".join(
            (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            for row in symbols
        )
        symbol_path = root / "symbols.jsonl"
        symbol_path.write_bytes(symbol_bytes)

        zip_record = {
            "key": "data/spot/monthly/klines/AAAUSDT/1h/AAAUSDT-1h-2023-01.zip",
            "symbol": "AAAUSDT",
            "month": "2023-01",
            "object_type": "zip",
            "size": 100,
            "etag": ETAG,
            "last_modified": LAST_MODIFIED_ISO,
        }
        checksum_record = dict(zip_record)
        checksum_record.update(
            {
                "key": zip_record["key"] + ".CHECKSUM",
                "object_type": "checksum",
                "size": 90,
            }
        )
        inventory_bytes = b"".join(
            (json.dumps(row, sort_keys=True) + "\n").encode()
            for row in (zip_record, checksum_record)
        )
        inventory_path = root / "inventory.jsonl"
        inventory_path.write_bytes(inventory_bytes)
        inventory_hash = sha256(inventory_bytes)
        symbol_hash = sha256(symbol_bytes)
        summary = {
            "archive": {
                "candidate_symbol_count": 2,
                "symbol_index_sha256": symbol_hash,
            },
            "inventory": {
                "zip_count": 1,
                "checksum_count": 1,
                "inventory_record_count": 2,
                "inventory_jsonl_sha256": inventory_hash,
            },
        }
        summary_bytes = (json.dumps(summary, sort_keys=True) + "\n").encode()
        summary_path = root / "summary.json"
        summary_path.write_bytes(summary_bytes)
        return {
            "inventory_path": inventory_path,
            "inventory_sha256": inventory_hash,
            "symbol_index_path": symbol_path,
            "symbol_index_sha256": symbol_hash,
            "summary_path": summary_path,
            "summary_sha256": sha256(summary_bytes),
            "expected_objects": 2,
            "expected_pairs": 1,
            "expected_candidate_symbols": 2,
            "expected_symbol_list_sha256": sha256("AAAUSDT\n币安人生USDT\n".encode()),
        }

    def test_exact_hash_count_pair_and_unicode_contract(self) -> None:
        with TemporaryDirectory() as temporary:
            kwargs = self._fixture(Path(temporary))
            frozen = load_frozen_inputs(**kwargs)
        self.assertEqual(len(frozen.pairs), 1)
        self.assertEqual(frozen.candidate_symbols, ("AAAUSDT", "币安人生USDT"))

    def test_changed_frozen_input_fails_before_use(self) -> None:
        with TemporaryDirectory() as temporary:
            kwargs = self._fixture(Path(temporary))
            Path(kwargs["inventory_path"]).write_bytes(b"changed\n")
            with self.assertRaisesRegex(ContractError, "SHA-256 mismatch"):
                load_frozen_inputs(**kwargs)


class ChecksumAndZipTests(unittest.TestCase):
    def test_checksum_requires_one_record_and_exact_basename(self) -> None:
        digest = "b" * 64
        name = "AAAUSDT-1h-2023-01.zip"
        self.assertEqual(parse_checksum(f"{digest}  {name}".encode(), expected_basename=name), digest)
        with self.assertRaises(ChecksumError):
            parse_checksum(f"{digest}  other.zip".encode(), expected_basename=name)
        with self.assertRaises(ChecksumError):
            parse_checksum(f"{digest}  {name}\nextra".encode(), expected_basename=name)

    def _validate(self, payload: bytes):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "payload.zip"
            path.write_bytes(payload)
            return validate_kline_zip(path, symbol="AAAUSDT", month="2023-01")

    def test_valid_zip_and_row_are_preserved(self) -> None:
        result = self._validate(zip_payload())
        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.rows[0][1:5], ("100", "110", "90", "105"))
        self.assertEqual(result.leading_missing_hours, 0)

    def test_multiple_or_unsafe_member_is_rejected(self) -> None:
        with self.assertRaises(ZipValidationError):
            self._validate(zip_payload(extra_member=True))
        with self.assertRaises(ZipValidationError):
            self._validate(zip_payload(member_name="../AAAUSDT-1h-2023-01.csv"))

    def test_uncompressed_size_gate_is_enforced(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "payload.zip"
            path.write_bytes(zip_payload())
            with self.assertRaises(ZipValidationError):
                validate_kline_zip(
                    path,
                    symbol="AAAUSDT",
                    month="2023-01",
                    max_uncompressed_bytes=10,
                )

    def test_duplicate_grid_and_invalid_taker_subset_are_rejected(self) -> None:
        with self.assertRaises(CsvValidationError):
            self._validate(zip_payload(rows=kline_row() + kline_row()))
        with self.assertRaises(CsvValidationError):
            self._validate(zip_payload(rows=kline_row(taker_base="3")))
        with self.assertRaises(CsvValidationError):
            self._validate(zip_payload(rows=kline_row(OPEN_MS + 1)))


class AcquisitionTests(unittest.TestCase):
    def test_conditional_download_retries_and_commits_atomically(self) -> None:
        body = b"payload"
        record = inventory_object(size=len(body))
        calls = []
        sleeps = []

        def opener(request, timeout):
            calls.append((request, timeout))
            if len(calls) == 1:
                return FakeResponse(503)
            return FakeResponse(200, body)

        with TemporaryDirectory() as temporary:
            destination = Path(temporary) / "payload.zip"
            result = acquire_object(
                record,
                destination=destination,
                max_attempts=3,
                opener=opener,
                sleeper=sleeps.append,
            )
            self.assertEqual(destination.read_bytes(), body)
            self.assertFalse(result.reused_local)
            self.assertEqual(result.attempts, 2)
            self.assertEqual(calls[1][0].headers["If-match"], f'"{ETAG}"')
            self.assertEqual(list(Path(temporary).glob("*.partial")), [])

    def test_source_change_is_not_retried(self) -> None:
        record = inventory_object(size=1)
        calls = []

        def opener(request, timeout):
            calls.append(request)
            return FakeResponse(412)

        with TemporaryDirectory() as temporary:
            with self.assertRaises(SourceChangedError) as raised:
                acquire_object(
                    record,
                    destination=Path(temporary) / "payload.zip",
                    opener=opener,
                    sleeper=lambda _: None,
                )
        self.assertEqual(len(calls), 1)
        self.assertEqual(raised.exception.evidence["attempt_log"][0]["http_status"], 412)

    def test_truncated_success_response_is_retried(self) -> None:
        body = b"payload"
        record = inventory_object(size=len(body))
        calls = []

        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                return FakeResponse(200, b"pay", size=len(body))
            return FakeResponse(200, body)

        with TemporaryDirectory() as temporary:
            result = acquire_object(
                record,
                destination=Path(temporary) / "payload.zip",
                max_attempts=2,
                opener=opener,
                sleeper=lambda _: None,
            )
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.attempt_log[0]["outcome"], "TRUNCATED_RESPONSE")

    def test_redirect_is_rejected_and_preserved_as_failure_receipt(self) -> None:
        zip_object = inventory_object(size=1)
        checksum_object = inventory_object(object_type="checksum", size=1)
        pair = PayloadPair("AAAUSDT", "2023-01", zip_object, checksum_object)

        def opener(request, timeout):
            return FakeResponse(200, b"x", response_url="https://example.invalid/object")

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = process_pair(
                pair,
                raw_root=root,
                source_base_url="https://s3-ap-northeast-1.amazonaws.com/data.binance.vision",
                timeout_seconds=30,
                max_attempts=2,
                opener=opener,
                sleeper=lambda _: None,
            )
            receipt = Path(result["receipt_path"])
            self.assertTrue(receipt.is_file())
            receipt_record = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "U")
        self.assertEqual(result["failure_code"], "SOURCE_CHANGED")
        self.assertEqual(
            result["failure_evidence"]["attempt_log"][0]["response_url"],
            "https://example.invalid/object",
        )
        self.assertEqual(receipt_record["failure_code"], "SOURCE_CHANGED")

    def test_existing_final_is_conditionally_refetched_and_byte_compared(self) -> None:
        body = b"payload"
        record = inventory_object(size=len(body))
        with TemporaryDirectory() as temporary:
            destination = Path(temporary) / "payload.zip"
            destination.write_bytes(body)
            result = acquire_object(
                record,
                destination=destination,
                opener=lambda *_args, **_kwargs: FakeResponse(200, body),
            )
        self.assertTrue(result.reused_local)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.local_sha256, sha256(body))


class DerivedOutputTests(unittest.TestCase):
    def _frozen(self, zip_size: int = 1) -> FrozenInputs:
        zip_object = inventory_object(size=zip_size)
        checksum_object = inventory_object(object_type="checksum", size=90)
        pair = PayloadPair("AAAUSDT", "2023-01", zip_object, checksum_object)
        symbols = ("AAAUSDT", "币安人生USDT")
        return FrozenInputs(
            pairs=(pair,),
            candidate_symbols=symbols,
            symbol_list_sha256=sha256("AAAUSDT\n币安人生USDT\n".encode()),
            inventory_sha256="1" * 64,
            symbol_index_sha256="2" * 64,
            summary_sha256="3" * 64,
        )

    def test_invalid_object_is_whole_month_u_and_unicode_candidate_remains_n(self) -> None:
        frozen = self._frozen()
        quality = [{
            "symbol": "AAAUSDT",
            "month": "2023-01",
            "status": "U",
            "failure_code": "CHECKSUM_ERROR",
            "failure_reason": "test",
        }]
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = build_derived_outputs(
                frozen=frozen,
                quality_records=[dict(quality[0])],
                processed_root=root / "processed_a",
                coverage_schema_output=root / "schema_a.json",
                derived_index_output=root / "index_a.jsonl",
                panel_start_ms=OPEN_MS,
                panel_end_ms=1_675_209_600_000,
            )
            second = build_derived_outputs(
                frozen=frozen,
                quality_records=[dict(quality[0])],
                processed_root=root / "processed_b",
                coverage_schema_output=root / "schema_b.json",
                derived_index_output=root / "index_b.jsonl",
                panel_start_ms=OPEN_MS,
                panel_end_ms=1_675_209_600_000,
            )
            self.assertEqual(first["panel_sha256"], second["panel_sha256"])
            with gzip.open(first["panel_path"], "rt", encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))
        self.assertEqual(rows[0], ["open_time_utc", "AAAUSDT", "币安人生USDT"])
        self.assertEqual(len(rows), 745)
        self.assertTrue(all(row[1:] == ["U", "N"] for row in rows[1:]))
        self.assertEqual(first["state_counts"], {"A": 0, "N": 744, "M": 0, "U": 744})

    def test_valid_object_emits_normalized_rows_and_a_m_states(self) -> None:
        payload = zip_payload()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            zip_path = root / "payload.zip"
            zip_path.write_bytes(payload)
            quality = [{
                "symbol": "AAAUSDT",
                "month": "2023-01",
                "status": "VALID",
                "failure_code": None,
                "failure_reason": None,
                "zip_local_path": str(zip_path),
                "zip_sha256": sha256(payload),
            }]
            result = build_derived_outputs(
                frozen=self._frozen(len(payload)),
                quality_records=quality,
                processed_root=root / "processed",
                coverage_schema_output=root / "schema.json",
                derived_index_output=root / "index.jsonl",
                panel_start_ms=OPEN_MS,
                panel_end_ms=1_675_209_600_000,
            )
            index = json.loads((root / "index.jsonl").read_text(encoding="utf-8").splitlines()[0])
            with gzip.open(index["normalized_path"], "rt", encoding="utf-8", newline="") as stream:
                normalized = list(csv.reader(stream))
        self.assertEqual(result["normalized_rows"], 1)
        self.assertEqual(normalized[0][-3:], ["symbol", "source_key", "source_zip_sha256"])
        self.assertEqual(normalized[1][0], str(OPEN_MS))
        self.assertEqual(result["state_counts"], {"A": 1, "N": 744, "M": 743, "U": 0})

    def test_structurally_valid_zip_replacement_fails_frozen_sha_and_becomes_u(self) -> None:
        original = zip_payload()
        replacement = zip_payload(rows=kline_row() + kline_row(OPEN_MS + 3_600_000))
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            zip_path = root / "payload.zip"
            zip_path.write_bytes(replacement)
            quality = [{
                "symbol": "AAAUSDT",
                "month": "2023-01",
                "status": "VALID",
                "failure_code": None,
                "failure_reason": None,
                "zip_local_path": str(zip_path),
                "zip_sha256": sha256(original),
            }]
            result = build_derived_outputs(
                frozen=self._frozen(len(replacement)),
                quality_records=quality,
                processed_root=root / "processed",
                coverage_schema_output=root / "schema.json",
                derived_index_output=root / "index.jsonl",
                panel_start_ms=OPEN_MS,
                panel_end_ms=1_675_209_600_000,
            )
        self.assertEqual(result["normalized_rows"], 0)
        self.assertEqual(result["state_counts"], {"A": 0, "N": 744, "M": 0, "U": 744})
        self.assertEqual(quality[0]["failure_code"], "DERIVATION_REVALIDATION_FAILED")

    def test_missing_zip_during_derivation_becomes_u_instead_of_escaping(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            quality = [{
                "symbol": "AAAUSDT",
                "month": "2023-01",
                "status": "VALID",
                "failure_code": None,
                "failure_reason": None,
                "zip_local_path": str(root / "missing.zip"),
                "zip_sha256": "0" * 64,
            }]
            result = build_derived_outputs(
                frozen=self._frozen(),
                quality_records=quality,
                processed_root=root / "processed",
                coverage_schema_output=root / "schema.json",
                derived_index_output=root / "index.jsonl",
                panel_start_ms=OPEN_MS,
                panel_end_ms=1_675_209_600_000,
            )
        self.assertEqual(result["normalized_rows"], 0)
        self.assertEqual(result["state_counts"], {"A": 0, "N": 744, "M": 0, "U": 744})
        self.assertEqual(quality[0]["failure_code"], "DERIVATION_REVALIDATION_FAILED")
        self.assertIn("FileNotFoundError", quality[0]["failure_reason"])


if __name__ == "__main__":
    unittest.main()
