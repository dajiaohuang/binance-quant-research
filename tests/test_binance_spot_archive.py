from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import urllib.parse

from quant_research.binance_spot_archive import (
    DEFAULT_BUCKET,
    ExchangeInfoSnapshot,
    HttpResponse,
    InventoryError,
    OBSERVED_SEMANTICS,
    ROOT_KLINE_PREFIX,
    S3InventoryClient,
    S3Object,
    YearMonth,
    build_inventory_summary,
    discover_archive_symbols,
    filter_archive_objects,
    run_inventory,
    serialize_inventory,
    serialize_symbol_index,
    sha256_bytes,
    symbol_evidence_directory_name,
)


FIXED_FETCHED_AT = "2026-08-25T01:00:00+00:00"
S3_NAMESPACE = "http://s3.amazonaws.com/doc/2006-03-01/"


def s3_xml(
    *,
    prefix: str,
    common_prefixes: tuple[str, ...] = (),
    objects: tuple[tuple[str, int], ...] = (),
    truncated: bool = False,
    request_token: str | None = None,
    next_token: str | None = None,
) -> bytes:
    token_xml = (
        f"<ContinuationToken>{request_token}</ContinuationToken>"
        if request_token is not None
        else ""
    )
    next_xml = (
        f"<NextContinuationToken>{next_token}</NextContinuationToken>"
        if next_token is not None
        else ""
    )
    prefixes_xml = "".join(
        f"<CommonPrefixes><Prefix>{value}</Prefix></CommonPrefixes>"
        for value in common_prefixes
    )
    contents_xml = "".join(
        "<Contents>"
        f"<Key>{key}</Key>"
        "<LastModified>2026-08-24T00:00:00.000Z</LastModified>"
        f"<ETag>&quot;etag-{size}&quot;</ETag>"
        f"<Size>{size}</Size>"
        "</Contents>"
        for key, size in objects
    )
    key_count = len(common_prefixes) + len(objects)
    return (
        f'<ListBucketResult xmlns="{S3_NAMESPACE}">'
        f"<Prefix>{prefix}</Prefix>"
        f"{token_xml}{prefixes_xml}{contents_xml}"
        f"<KeyCount>{key_count}</KeyCount>"
        f"<IsTruncated>{str(truncated).lower()}</IsTruncated>{next_xml}"
        "</ListBucketResult>"
    ).encode("utf-8")


def response(body: bytes, *, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"Content-Type": "application/xml", "X-Amz-Request-Id": "request"},
        body=body,
        fetched_at_utc=FIXED_FETCHED_AT,
    )


def object_for(key: str, size: int = 1) -> S3Object:
    return S3Object(
        key=key,
        size=size,
        etag="etag",
        last_modified="2026-08-24T00:00:00.000Z",
        observed_at_utc=FIXED_FETCHED_AT,
    )


class S3PaginationTests(unittest.TestCase):
    def test_multi_page_token_chain_preserves_raw_pages(self) -> None:
        prefix = ROOT_KLINE_PREFIX
        first = s3_xml(
            prefix=prefix,
            common_prefixes=(f"{prefix}AAAUSDT/",),
            truncated=True,
            next_token="token-2",
        )
        second = s3_xml(
            prefix=prefix,
            common_prefixes=(f"{prefix}OLDUSDT/",),
            request_token="token-2",
        )
        calls: list[str] = []

        def fetch(url: str) -> HttpResponse:
            calls.append(url)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            return response(second if "continuation-token" in query else first)

        with TemporaryDirectory() as temporary:
            raw = Path(temporary) / "raw"
            listing = S3InventoryClient(fetcher=fetch).list_objects(
                prefix=prefix,
                delimiter="/",
                raw_directory=raw,
            )
            self.assertEqual(
                listing.common_prefixes,
                (f"{prefix}AAAUSDT/", f"{prefix}OLDUSDT/"),
            )
            self.assertEqual(listing.page_count, 2)
            self.assertEqual(len(calls), 2)
            self.assertEqual((raw / "page_0001.xml").read_bytes(), first)
            sidecar = json.loads((raw / "page_0002.request.json").read_text())
            self.assertEqual(
                sidecar["request"]["parameters"]["continuation-token"], "token-2"
            )
            self.assertEqual(sidecar["response"]["body_sha256"], sha256_bytes(second))

    def test_token_loop_fails(self) -> None:
        prefix = ROOT_KLINE_PREFIX
        first = s3_xml(prefix=prefix, truncated=True, next_token="loop")
        second = s3_xml(
            prefix=prefix,
            request_token="loop",
            truncated=True,
            next_token="loop",
        )
        call_count = 0

        def fetch(url: str) -> HttpResponse:
            nonlocal call_count
            call_count += 1
            return response(first if call_count == 1 else second)

        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(InventoryError, "duplicate continuation token"):
                S3InventoryClient(fetcher=fetch).list_objects(
                    prefix=prefix,
                    delimiter="/",
                    raw_directory=Path(temporary),
                )

    def test_duplicate_prefix_across_pages_fails(self) -> None:
        prefix = ROOT_KLINE_PREFIX
        duplicate = f"{prefix}AAAUSDT/"
        pages = iter(
            (
                s3_xml(
                    prefix=prefix,
                    common_prefixes=(duplicate,),
                    truncated=True,
                    next_token="next",
                ),
                s3_xml(
                    prefix=prefix,
                    common_prefixes=(duplicate,),
                    request_token="next",
                ),
            )
        )
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(InventoryError, "duplicate CommonPrefix"):
                S3InventoryClient(fetcher=lambda _: response(next(pages))).list_objects(
                    prefix=prefix,
                    delimiter="/",
                    raw_directory=Path(temporary),
                )

    def test_duplicate_object_key_across_pages_fails(self) -> None:
        prefix = f"{ROOT_KLINE_PREFIX}AAAUSDT/1h/"
        duplicate = f"{prefix}AAAUSDT-1h-2023-01.zip"
        pages = iter(
            (
                s3_xml(
                    prefix=prefix,
                    objects=((duplicate, 10),),
                    truncated=True,
                    next_token="next",
                ),
                s3_xml(
                    prefix=prefix,
                    objects=((duplicate, 10),),
                    request_token="next",
                ),
            )
        )
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(InventoryError, "duplicate object key"):
                S3InventoryClient(fetcher=lambda _: response(next(pages))).list_objects(
                    prefix=prefix,
                    raw_directory=Path(temporary),
                )

    def test_http_200_invalid_xml_is_preserved_and_fails(self) -> None:
        invalid = b"not xml"
        with TemporaryDirectory() as temporary:
            raw = Path(temporary)
            with self.assertRaisesRegex(InventoryError, "invalid XML"):
                S3InventoryClient(fetcher=lambda _: response(invalid)).list_objects(
                    prefix=ROOT_KLINE_PREFIX,
                    delimiter="/",
                    raw_directory=raw,
                )
            self.assertEqual((raw / "page_0001.xml").read_bytes(), invalid)


class ArchiveFilteringTests(unittest.TestCase):
    def test_historical_symbol_discovery_does_not_use_current_symbols(self) -> None:
        prefixes = (
            f"{ROOT_KLINE_PREFIX}AAAUSDT/",
            f"{ROOT_KLINE_PREFIX}DELISTEDUSDT/",
            f"{ROOT_KLINE_PREFIX}ETHBTC/",
        )
        discovered = discover_archive_symbols(prefixes)
        current_symbols = {"AAAUSDT"}
        candidates = tuple(symbol for symbol in discovered if symbol.endswith("USDT"))
        self.assertEqual(candidates, ("AAAUSDT", "DELISTEDUSDT"))
        self.assertEqual(set(candidates) - current_symbols, {"DELISTEDUSDT"})

    def test_unicode_archive_symbol_is_preserved_with_safe_evidence_path(self) -> None:
        unicode_symbol = "币安人生USDT"
        discovered = discover_archive_symbols(
            (f"{ROOT_KLINE_PREFIX}{unicode_symbol}/",)
        )
        self.assertEqual(discovered, (unicode_symbol,))
        directory = symbol_evidence_directory_name(unicode_symbol)
        self.assertRegex(directory, r"^sha256_[0-9a-f]{64}$")
        self.assertNotIn(unicode_symbol, directory)
        self.assertNotEqual(
            symbol_evidence_directory_name("éUSDT"),
            symbol_evidence_directory_name("e\u0301USDT"),
        )

        index_rows = [
            json.loads(line)
            for line in serialize_symbol_index(
                (unicode_symbol, "ETHBTC"), quote_suffix="USDT"
            ).splitlines()
        ]
        unicode_row = next(row for row in index_rows if row["symbol"] == unicode_symbol)
        self.assertTrue(unicode_row["suffix_candidate"])
        self.assertEqual(
            bytes.fromhex(unicode_row["symbol_utf8_hex"]).decode("utf-8"),
            unicode_symbol,
        )

    def test_unicode_prefix_request_round_trips_exactly(self) -> None:
        symbol = "币安人生USDT"
        prefix = f"{ROOT_KLINE_PREFIX}{symbol}/1h/"

        def fetch(url: str) -> HttpResponse:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            self.assertEqual(query["prefix"], [prefix])
            return response(s3_xml(prefix=prefix))

        with TemporaryDirectory() as temporary:
            listing = S3InventoryClient(fetcher=fetch).list_objects(
                prefix=prefix,
                raw_directory=Path(temporary),
            )
        self.assertEqual(listing.objects, ())

    def test_unicode_symbol_object_key_matches_exactly(self) -> None:
        symbol = "币安人生USDT"
        key = (
            f"{ROOT_KLINE_PREFIX}{symbol}/1h/{symbol}-1h-2023-01.zip"
        )
        filtered = filter_archive_objects(
            (object_for(key, 10),),
            bucket=DEFAULT_BUCKET,
            symbol=symbol,
            interval="1h",
            start_month=YearMonth.parse("2023-01"),
            end_month=YearMonth.parse("2023-01"),
        )
        self.assertEqual([record["key"] for record in filtered.records], [key])

    def test_strict_object_regex_accepts_only_exact_monthly_keys(self) -> None:
        symbol = "AAAUSDT"
        base = f"{ROOT_KLINE_PREFIX}{symbol}/1h/"
        valid_zip = f"{base}{symbol}-1h-2023-01.zip"
        valid_checksum = f"{valid_zip}.CHECKSUM"
        filtered = filter_archive_objects(
            (
                object_for(valid_checksum),
                object_for(valid_zip, 10),
                object_for(f"{base}{symbol}-1h-2023-01-01.zip"),
                object_for(f"{base}{symbol}-5m-2023-01.zip"),
                object_for(f"{base}WRONGUSDT-1h-2023-01.zip"),
                object_for(f"{valid_zip}.sha256"),
                object_for(f"{base}{symbol}-1h-2025-01.zip"),
            ),
            bucket=DEFAULT_BUCKET,
            symbol=symbol,
            interval="1h",
            start_month=YearMonth.parse("2022-12"),
            end_month=YearMonth.parse("2024-12"),
        )
        self.assertEqual(
            [record["key"] for record in filtered.records],
            [valid_zip, valid_checksum],
        )
        self.assertEqual(len(filtered.rejected_keys), 4)
        self.assertEqual(
            filtered.out_of_range_keys,
            (f"{base}{symbol}-1h-2025-01.zip",),
        )
        self.assertTrue(
            all(record["observed_semantics"] == OBSERVED_SEMANTICS for record in filtered.records)
        )


class SummaryTests(unittest.TestCase):
    def _record(
        self,
        symbol: str,
        month: str,
        object_type: str,
        size: int = 1,
    ) -> dict[str, object]:
        suffix = ".zip.CHECKSUM" if object_type == "checksum" else ".zip"
        return {
            "bucket": DEFAULT_BUCKET,
            "etag": "etag",
            "interval": "1h",
            "key": (
                f"{ROOT_KLINE_PREFIX}{symbol}/1h/{symbol}-1h-{month}{suffix}"
            ),
            "last_modified": "2026-08-24T00:00:00.000Z",
            "month": month,
            "object_type": object_type,
            "observed_at_utc": FIXED_FETCHED_AT,
            "observed_semantics": OBSERVED_SEMANTICS,
            "size": size,
            "symbol": symbol,
        }

    def test_missing_checksum_and_gap_classes_are_distinct(self) -> None:
        records = [
            self._record("AAAUSDT", "2023-01", "zip", 10),
            self._record("AAAUSDT", "2023-01", "checksum"),
            self._record("AAAUSDT", "2023-03", "zip", 20),
        ]
        inventory = serialize_inventory(reversed(records))
        summary = build_inventory_summary(
            records=records,
            all_archive_symbols=("AAAUSDT",),
            candidate_symbols=("AAAUSDT",),
            rejected_keys=(),
            out_of_range_keys=(),
            inventory_bytes=inventory,
            symbol_index_bytes=serialize_symbol_index(
                ("AAAUSDT",), quote_suffix="USDT"
            ),
            exchange_info=ExchangeInfoSnapshot(
                sha256="hash",
                fetched_at_utc=FIXED_FETCHED_AT,
                symbols=("AAAUSDT",),
                quote_symbols=("AAAUSDT",),
            ),
            bucket=DEFAULT_BUCKET,
            interval="1h",
            quote_suffix="USDT",
            start_month=YearMonth.parse("2022-12"),
            end_month=YearMonth.parse("2023-04"),
        )
        symbol = summary["symbols"][0]
        self.assertEqual(symbol["leading_missing_months"], ["2022-12"])
        self.assertEqual(symbol["internal_missing_months"], ["2023-02"])
        self.assertEqual(symbol["trailing_missing_months"], ["2023-04"])
        self.assertEqual(symbol["missing_checksum_months"], ["2023-03"])
        self.assertEqual(symbol["zip_total_bytes"], 30)
        self.assertEqual(symbol["checksum_total_bytes"], 1)
        self.assertEqual(summary["inventory"]["missing_checksum_count"], 1)
        self.assertEqual(summary["inventory"]["checksum_total_bytes"], 1)

    def test_inventory_and_summary_are_deterministic(self) -> None:
        records = [
            self._record("AAAUSDT", "2023-01", "checksum"),
            self._record("AAAUSDT", "2023-01", "zip", 10),
        ]
        first_inventory = serialize_inventory(records)
        second_inventory = serialize_inventory(reversed(records))
        self.assertEqual(first_inventory, second_inventory)

        kwargs = {
            "all_archive_symbols": ("AAAUSDT",),
            "candidate_symbols": ("AAAUSDT",),
            "rejected_keys": (),
            "out_of_range_keys": (),
            "inventory_bytes": first_inventory,
            "symbol_index_bytes": serialize_symbol_index(
                ("AAAUSDT",), quote_suffix="USDT"
            ),
            "exchange_info": ExchangeInfoSnapshot(
                sha256="hash",
                fetched_at_utc=FIXED_FETCHED_AT,
                symbols=(),
                quote_symbols=(),
            ),
            "bucket": DEFAULT_BUCKET,
            "interval": "1h",
            "quote_suffix": "USDT",
            "start_month": YearMonth.parse("2023-01"),
            "end_month": YearMonth.parse("2023-02"),
        }
        first_summary = build_inventory_summary(records=records, **kwargs)
        second_summary = build_inventory_summary(records=reversed(records), **kwargs)
        self.assertEqual(first_summary, second_summary)


class OfflineWorkflowTests(unittest.TestCase):
    def test_full_workflow_keeps_archive_only_symbol_and_writes_outputs(self) -> None:
        root_prefix = ROOT_KLINE_PREFIX
        aaa_prefix = f"{root_prefix}AAAUSDT/1h/"
        empty_prefix = f"{root_prefix}EMPTYUSDT/1h/"
        old_prefix = f"{root_prefix}OLDUSDT/1h/"
        responses = {
            root_prefix: s3_xml(
                prefix=root_prefix,
                common_prefixes=(
                    f"{root_prefix}AAAUSDT/",
                    f"{root_prefix}EMPTYUSDT/",
                    f"{root_prefix}OLDUSDT/",
                    f"{root_prefix}ETHBTC/",
                ),
            ),
            aaa_prefix: s3_xml(
                prefix=aaa_prefix,
                objects=(
                    (f"{aaa_prefix}AAAUSDT-1h-2023-01.zip", 100),
                    (f"{aaa_prefix}AAAUSDT-1h-2023-01.zip.CHECKSUM", 80),
                ),
            ),
            empty_prefix: s3_xml(prefix=empty_prefix),
            old_prefix: s3_xml(
                prefix=old_prefix,
                objects=((f"{old_prefix}OLDUSDT-1h-2022-12.zip", 50),),
            ),
        }
        exchange_body = json.dumps(
            {
                "symbols": [
                    {
                        "symbol": "AAAUSDT",
                        "quoteAsset": "USDT",
                        "status": "TRADING",
                    }
                ]
            },
            separators=(",", ":"),
        ).encode()

        def fetch(url: str) -> HttpResponse:
            parsed = urllib.parse.urlparse(url)
            if parsed.path.endswith("exchangeInfo"):
                return HttpResponse(
                    status=200,
                    headers={"Content-Type": "application/json"},
                    body=exchange_body,
                    fetched_at_utc=FIXED_FETCHED_AT,
                )
            query = urllib.parse.parse_qs(parsed.query)
            return response(responses[query["prefix"][0]])

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            inventory_path = root / "processed" / "inventory.jsonl"
            summary_path = root / "experiment" / "summary.json"
            summary = run_inventory(
                raw_root=root / "raw",
                inventory_output=inventory_path,
                summary_output=summary_path,
                interval="1h",
                quote_suffix="USDT",
                start_month=YearMonth.parse("2022-12"),
                end_month=YearMonth.parse("2024-12"),
                max_workers=2,
                fetcher=fetch,
            )
            self.assertTrue(inventory_path.is_file())
            self.assertTrue(summary_path.is_file())
            self.assertEqual(summary["archive"]["candidate_symbol_count"], 3)
            self.assertEqual(
                summary["exchange_info_snapshot"]["archive_candidates_not_current"],
                ["EMPTYUSDT", "OLDUSDT"],
            )
            self.assertEqual(summary["inventory"]["zip_total_bytes"], 150)
            old = next(item for item in summary["symbols"] if item["symbol"] == "OLDUSDT")
            self.assertEqual(old["missing_checksum_months"], ["2022-12"])
            empty = next(
                item for item in summary["symbols"] if item["symbol"] == "EMPTYUSDT"
            )
            self.assertEqual(len(empty["all_missing_months"]), 25)
            self.assertEqual(
                sha256_bytes((root / "raw/exchange_info/exchange_info.json").read_bytes()),
                summary["exchange_info_snapshot"]["sha256"],
            )
            symbol_index = root / "raw/symbol_index.jsonl"
            self.assertTrue(symbol_index.is_file())
            self.assertEqual(
                sha256_bytes(symbol_index.read_bytes()),
                summary["archive"]["symbol_index_sha256"],
            )

    def test_workflow_refuses_to_overwrite_existing_evidence(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root = root / "raw"
            raw_root.mkdir()
            with self.assertRaisesRegex(InventoryError, "refusing to overwrite"):
                run_inventory(
                    raw_root=raw_root,
                    inventory_output=root / "inventory.jsonl",
                    summary_output=root / "summary.json",
                    interval="1h",
                    quote_suffix="USDT",
                    start_month=YearMonth.parse("2022-12"),
                    end_month=YearMonth.parse("2024-12"),
                    max_workers=1,
                    fetcher=lambda _: response(b"unused"),
                )


if __name__ == "__main__":
    unittest.main()
