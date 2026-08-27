from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock
from urllib.request import Request

from quant_research import binance_spot_announcement_corpus_v4 as corpus_v4
from quant_research.binance_spot_announcement_corpus_v4 import (
    BASE_LOGICAL_DELAY_NS,
    DETAIL_BASE,
    LIST_BASE,
    REQUEST_HEADERS,
    TIME_URL,
    WALL_BUDGET_NS,
    CorpusContractError,
    CorpusExistingError,
    CorpusHttpError,
    CorpusIntegrityError,
    CorpusSchemaError,
    TransportResponse,
    _CorpusNoRedirectHandler,
    _article_id,
    _bounded_default_fetcher,
    _detail_url,
    _list_url,
    _module_sha,
    _parse_detail,
    _parse_list,
    _parser,
    _request,
    _timeout_seconds_without_up_round,
    _validate_canonical_url,
    _verified_response,
    load_corpus,
    run_corpus,
)


BASE_MS = 1_777_420_800_000
BASE_UTC = "2026-04-29T00:00:00Z"
START_MS = 1_669_852_800_000
END_MS = 1_735_689_600_000
CODE_A = "a" * 32
CODE_B = "b" * 32
CODE_C = "c" * 32


def body(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def article(article_id: int | str, code: str, title: str, release: int) -> dict[str, object]:
    return {"id": article_id, "code": code, "title": title, "releaseDate": release}


def envelope(data: object) -> dict[str, object]:
    return {"code": "000000", "success": True, "data": data}


class CmsFetcher:
    def __init__(
        self,
        catalogs: dict[int, list[dict[str, object]]],
        *,
        detail_mutator=None,
        anchor_mutator=None,
        total_mutator=None,
        catalogs_mutator=None,
        raw_mutator=None,
        local_clock_mutator=None,
        header_mutator=None,
        injected_host: str | None = "canonical",
        omit_content_length: bool = False,
        first_status: int | None = None,
        reversed_time: bool = False,
    ) -> None:
        self.catalogs = catalogs
        self.detail_mutator = detail_mutator
        self.anchor_mutator = anchor_mutator
        self.total_mutator = total_mutator
        self.catalogs_mutator = catalogs_mutator
        self.raw_mutator = raw_mutator
        self.local_clock_mutator = local_clock_mutator
        self.header_mutator = header_mutator
        self.injected_host = injected_host
        self.omit_content_length = omit_content_length
        self.first_status = first_status
        self.reversed_time = reversed_time
        self.requests: list[Request] = []
        self.url_counts: dict[str, int] = {}
        self.clock = 0

    def __call__(self, request: Request, timeout: float) -> TransportResponse:
        if self.injected_host is not None:
            host = (
                urllib.parse.urlsplit(request.full_url).hostname
                if self.injected_host == "canonical"
                else self.injected_host
            )
            request.add_unredirected_header("Host", host)
        self.requests.append(request)
        self.clock += 1
        url = request.full_url
        self.url_counts[url] = self.url_counts.get(url, 0) + 1
        if self.first_status is not None and len(self.requests) == 1:
            status = self.first_status
            raw = body({"error": status})
            self.first_status = None
        elif url == TIME_URL:
            status = 200
            time_value = BASE_MS + (10_000 if self.reversed_time and self.url_counts[url] == 1 else self.clock * 100)
            raw = body({"serverTime": time_value})
        elif url.startswith(LIST_BASE):
            status = 200
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
            catalog_id, page = int(query["catalogId"]), int(query["pageNo"])
            rows = list(self.catalogs[catalog_id])
            page_rows = rows[(page - 1) * 50 : page * 50]
            if self.url_counts[url] > 1 and self.anchor_mutator is not None:
                page_rows = self.anchor_mutator(catalog_id, page_rows)
            total = len(rows)
            if self.total_mutator is not None:
                total = self.total_mutator(catalog_id, page, total)
            catalog_objects = [
                {
                    "catalogId": catalog_id,
                    "total": total,
                    "articles": page_rows,
                }
            ]
            if self.catalogs_mutator is not None:
                catalog_objects = self.catalogs_mutator(
                    catalog_id, page, catalog_objects
                )
            raw = body(envelope({"catalogs": catalog_objects}))
        elif url.startswith(DETAIL_BASE):
            status = 200
            code = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["articleCode"]
            source = next(item for rows in self.catalogs.values() for item in rows if item["code"] == code)
            catalog_id = next(key for key, rows in self.catalogs.items() if source in rows)
            detail = {
                "id": source["id"],
                "code": code,
                "title": source["title"],
                "body": f"<p>正文 {source['title']}</p>",
                "contentJson": f'{{"title":"{source["title"]}"}}',
                "publishDate": source["releaseDate"],
                "firstCatalogId": catalog_id,
                "lastUpdateTime": None,
            }
            if self.detail_mutator is not None:
                detail = self.detail_mutator(dict(detail))
            raw = body(envelope(detail))
        else:
            raise AssertionError(f"unexpected URL {url}")
        if self.raw_mutator is not None:
            raw = self.raw_mutator(url, raw)
        completed_ms = self.clock * 100
        completed = f"2026-04-29T00:00:00.{completed_ms:06d}Z"
        if self.local_clock_mutator is not None:
            completed = self.local_clock_mutator(self.clock, completed)
        headers = {
            "Content-Type": "application/json",
            "ETag": f'"etag-{self.clock}"',
            "Last-Modified": "Wed, 29 Apr 2026 00:00:00 GMT",
            "Age": "1",
            "Cache-Control": "max-age=1",
            "Date": "Wed, 29 Apr 2026 00:00:00 GMT",
        }
        if not self.omit_content_length:
            headers["Content-Length"] = str(len(raw))
        if self.header_mutator is not None:
            headers = self.header_mutator(url, status, raw, dict(headers))
        return TransportResponse(
            status=status,
            headers=headers,
            body=raw,
            final_url=url,
            request_started_at_utc=completed,
            response_completed_at_utc=completed,
        )


class FakeMonotonic:
    def __init__(self, start_ns: int = 1_000_000_000) -> None:
        self.value = start_ns
        self.sleeps: list[float] = []

    def __call__(self) -> int:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += int(seconds * 1_000_000_000)


class StatusSequenceFetcher:
    def __init__(
        self,
        base: CmsFetcher,
        statuses: list[int],
        *,
        retry_after: object = None,
    ) -> None:
        self.base = base
        self.statuses = list(statuses)
        self.retry_after = retry_after
        self.timeouts: list[float] = []

    @property
    def requests(self) -> list[Request]:
        return self.base.requests

    def __call__(self, request: Request, timeout: float) -> TransportResponse:
        self.timeouts.append(timeout)
        normal = self.base(request, timeout)
        if not self.statuses:
            return normal
        status = self.statuses.pop(0)
        if status == 200:
            return normal
        raw = body({"error": status})
        headers = dict(normal.headers)
        headers["Content-Length"] = str(len(raw))
        if self.retry_after is not None:
            key, value = self.retry_after
            headers[key] = value
        return TransportResponse(
            status=status,
            headers=headers,
            body=raw,
            final_url=normal.final_url,
            request_started_at_utc=normal.request_started_at_utc,
            response_completed_at_utc=normal.response_completed_at_utc,
        )


class AnnouncementCorpusV4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw_root = self.root / "raw"
        self.processed_root = self.root / "processed"
        self.summary = self.root / "artifacts" / "summary.json"
        self.schema = self.root / "artifacts" / "schema.json"
        self.source = self.root / "artifacts" / "source.json"
        self.catalogs = {
            48: [
                article(1, CODE_A, "New Token 上线", START_MS + 1),
                article(2, CODE_B, "Old notice", START_MS - 1),
            ],
            161: [article(3, CODE_C, "Delisting 通知", END_MS - 1)],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def reset_paths(self, name: str) -> None:
        root = self.root / name
        self.raw_root = root / "raw"
        self.processed_root = root / "processed"
        self.summary = root / "artifacts" / "summary.json"
        self.schema = root / "artifacts" / "schema.json"
        self.source = root / "artifacts" / "source.json"

    def execute(
        self,
        fetcher: CmsFetcher,
        *,
        expected_totals=None,
        expected_counts=None,
        run_id="unit",
        expected_extractor_sha256=None,
        max_response_bytes=2_000_000,
        max_articles=10,
        clock: FakeMonotonic | None = None,
    ):
        clock = clock or FakeMonotonic()
        return run_corpus(
            run_id=run_id,
            expected_extractor_sha256=expected_extractor_sha256 or _module_sha(),
            raw_root=self.raw_root,
            processed_root=self.processed_root,
            summary_output=self.summary,
            schema_output=self.schema,
            source_contract_output=self.source,
            list_release_date_claim_start_ms=START_MS,
            list_release_date_claim_end_ms_exclusive=END_MS,
            expected_totals=expected_totals or {48: 2, 161: 1},
            expected_list_release_date_claim_interval_counts=expected_counts or {48: 1, 161: 1},
            max_pages_per_catalog=50,
            max_articles=max_articles,
            max_response_bytes=max_response_bytes,
            max_total_response_bytes=10_000_000,
            max_clock_skew_ms=300_000,
            http_429_backoff_seconds=(30, 60, 120),
            other_retryable_backoff_seconds=(1, 2),
            fetcher=fetcher,
            sleeper=clock.sleep,
            monotonic_ns=clock,
        )

    def test_url_allowlist_and_fixed_english_unauthenticated_headers(self) -> None:
        list_url = _list_url(48, 7, 50)
        self.assertEqual(
            list_url,
            LIST_BASE + "?type=1&catalogId=48&pageNo=7&pageSize=50",
        )
        request, parameters = _request(list_url, "list")
        self.assertEqual(parameters, {"type": "1", "catalogId": "48", "pageNo": "7", "pageSize": "50"})
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(headers["accept-encoding"], "identity")
        self.assertEqual(headers["accept-language"], "en-US,en;q=0.9")
        self.assertEqual(headers["clienttype"], "web")
        self.assertEqual(headers["lang"], "en")
        self.assertTrue(set(headers).isdisjoint({"authorization", "cookie", "proxy-authorization"}))
        self.assertEqual(_detail_url(CODE_A), DETAIL_BASE + f"?articleCode={CODE_A}")

    def test_production_transport_injected_host_is_canonical_and_audited(self) -> None:
        summary = self.execute(CmsFetcher(self.catalogs))
        ledger_rows = [
            json.loads(line)
            for line in Path(summary["request_ledger"]["path"])
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        for row in ledger_rows:
            sidecar = json.loads(
                Path(row["attempts"][-1]["sidecar"]).read_text(encoding="utf-8")
            )
            observed = {
                key.lower(): value
                for key, value in sidecar["request"]["headers"].items()
            }
            self.assertEqual(
                observed["host"],
                urllib.parse.urlsplit(row["canonical_url"]).hostname,
            )

        root = self.root / "bad_host"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
        with self.assertRaises(CorpusHttpError):
            self.execute(CmsFetcher(self.catalogs, injected_host="evil.example"))

    def test_url_contract_rejects_catalog_page_size_code_and_extra_query(self) -> None:
        bad = [
            (LIST_BASE + "?type=1&catalogId=49&pageNo=1&pageSize=50", "list"),
            (LIST_BASE + "?type=1&catalogId=48&pageNo=0&pageSize=50", "list"),
            (LIST_BASE + "?type=1&catalogId=48&pageNo=1&pageSize=20", "list"),
            (_list_url(48, 1, 50) + "&x=1", "list"),
            (DETAIL_BASE + "?articleCode=ABC", "detail"),
        ]
        for url, kind in bad:
            with self.subTest(url=url), self.assertRaises(CorpusContractError):
                _validate_canonical_url(url, kind)

    def test_tracked_production_page38_fixture_is_exact_and_opaque(self) -> None:
        fixture = (
            Path(__file__).parent
            / "fixtures"
            / "binance_cms_catalog_48_page_0038_legacy_codes.response"
        )
        raw = fixture.read_bytes()
        self.assertEqual(len(raw), 7_607)
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            "ab4676534a3219461555e4127326a1da9a43e9e29afce0df3e8ace3ad0c31876",
        )
        total, rows = _parse_list(
            raw,
            48,
            38,
            hashlib.sha256(raw).hexdigest(),
            BASE_UTC,
        )
        self.assertEqual(total, 2_234)
        self.assertEqual(len(rows), 50)
        self.assertEqual(
            sum(row["article_code"].isdecimal() for row in rows),
            38,
        )
        self.assertTrue(all(row["article_id_type"] == "int" for row in rows))
        self.assertTrue(all("code_kind" not in row for row in rows))

    def test_exact_paired_fixtures_preserve_independent_claims_and_delta(self) -> None:
        fixture_root = Path(__file__).parent / "fixtures"
        list_path = (
            fixture_root
            / "binance_cms_catalog_48_page_0014_time_claims.response"
        )
        detail_path = (
            fixture_root
            / "binance_cms_article_209355888f0042f788899dd1a04a0052_time_claims.response"
        )
        list_raw = list_path.read_bytes()
        detail_raw = detail_path.read_bytes()
        self.assertEqual(len(list_raw), 9_949)
        self.assertEqual(len(detail_raw), 78_567)
        self.assertEqual(
            hashlib.sha256(list_raw).hexdigest(),
            "adbcfcf758ce0c55b772b77de49169702b44759b3e99525e92bb16591d13ce5d",
        )
        self.assertEqual(
            hashlib.sha256(detail_raw).hexdigest(),
            "cb27c5578fa9303516b238b0e4845640b21f397d09e6f5a90fd4446d21a2a8d5",
        )
        total, rows = _parse_list(
            list_raw,
            48,
            14,
            hashlib.sha256(list_raw).hexdigest(),
            BASE_UTC,
        )
        self.assertEqual(total, 2_234)
        self.assertEqual(len(rows), 50)
        selected = rows[1]
        self.assertEqual(selected["article_id_type"], "int")
        self.assertEqual(selected["article_id"], 216_744)
        self.assertEqual(
            selected["article_code"], "209355888f0042f788899dd1a04a0052"
        )
        self.assertEqual(selected["list_release_date_claim_ms"], 1_730_975_333_236)
        detail = _parse_detail(
            detail_raw,
            selected,
            hashlib.sha256(detail_raw).hexdigest(),
            BASE_UTC,
        )
        self.assertEqual(detail["list_release_date_claim_ms"], 1_730_975_333_236)
        self.assertEqual(detail["detail_publish_date_claim_ms"], 1_730_975_320_602)
        self.assertEqual(
            detail["detail_publish_minus_list_release_claim_ms"], -12_634
        )
        self.assertNotIn("claimed_published_at_ms", detail)
        self.assertNotIn("detail_publish_date_ms", detail)

    def test_list_release_date_claim_requires_positive_nonbool_int(self) -> None:
        invalid_values = (True, False, 0, -1, "1", 1.0, None)
        for index, value in enumerate(invalid_values):
            payload = body(
                envelope(
                    {
                        "catalogs": [
                            {
                                "catalogId": 48,
                                "total": 1,
                                "articles": [
                                    {
                                        "id": index + 1,
                                        "code": CODE_A,
                                        "title": "claim",
                                        "releaseDate": value,
                                    }
                                ],
                            }
                        ]
                    }
                )
            )
            with self.subTest(value=value), self.assertRaises(CorpusSchemaError):
                _parse_list(
                    payload,
                    48,
                    1,
                    hashlib.sha256(payload).hexdigest(),
                    BASE_UTC,
                )

    def test_opaque_code_grammar_and_exact_canonical_rebuild(self) -> None:
        accepted = (
            "a" * 32,
            "0",
            "0" * 32,
            "9" * 64,
        )
        for code in accepted:
            with self.subTest(code=code):
                url = _detail_url(code)
                self.assertEqual(
                    url,
                    DETAIL_BASE + "?articleCode=" + urllib.parse.quote(code, safe=""),
                )
                self.assertEqual(_validate_canonical_url(url, "detail"), {"articleCode": code})
        rejected = ("", "A" * 32, "g" * 32, "9" * 65, 123)
        for code in rejected:
            with self.subTest(code=code), self.assertRaises(CorpusContractError):
                _detail_url(code)  # type: ignore[arg-type]
        encoded = DETAIL_BASE + "?articleCode=%31"
        with self.assertRaises(CorpusContractError):
            _validate_canonical_url(encoded, "detail")

    def test_type_preserving_article_id_contract_and_detail_equality(self) -> None:
        accepted = (
            (1, ("int", 1)),
            (10**63, ("int", 10**63)),
            ("0", ("str", "0")),
            ("001", ("str", "001")),
            ("9" * 64, ("str", "9" * 64)),
        )
        for value, expected in accepted:
            with self.subTest(value=value):
                self.assertEqual(_article_id(value), expected)
        rejected = (True, False, 0, -1, 10**64, "", "-1", "x", "9" * 65, 1.0)
        for value in rejected:
            with self.subTest(value=value), self.assertRaises(CorpusSchemaError):
                _article_id(value)

        list_body = body(
            envelope(
                {
                    "catalogs": [
                        {
                            "catalogId": 48,
                            "total": 1,
                            "articles": [article("001", CODE_A, "opaque", START_MS + 1)],
                        }
                    ]
                }
            )
        )
        _, rows = _parse_list(list_body, 48, 1, hashlib.sha256(list_body).hexdigest(), BASE_UTC)
        expected_row = rows[0]
        self.assertEqual(expected_row["article_id_type"], "str")
        self.assertEqual(expected_row["article_id"], "001")
        matching = body(
            envelope(
                {
                    "id": "001",
                    "code": CODE_A,
                    "title": "opaque",
                    "body": "text",
                    "publishDate": START_MS + 1,
                    "firstCatalogId": 48,
                }
            )
        )
        parsed = _parse_detail(matching, expected_row, hashlib.sha256(matching).hexdigest(), BASE_UTC)
        self.assertEqual(parsed["article_id"], "001")
        mismatch = body(
            envelope(
                {
                    "id": 1,
                    "code": CODE_A,
                    "title": "opaque",
                    "body": "text",
                    "publishDate": START_MS + 1,
                    "firstCatalogId": 48,
                }
            )
        )
        with self.assertRaises(CorpusSchemaError):
            _parse_detail(mismatch, expected_row, hashlib.sha256(mismatch).hexdigest(), BASE_UTC)

    def test_article_id_uniqueness_is_type_aware_and_type_hashes_differ(self) -> None:
        distinct = {
            48: [
                article(1, CODE_A, "integer", START_MS + 1),
                article("1", CODE_B, "string", START_MS + 2),
            ],
            161: [],
        }
        summary = self.execute(
            CmsFetcher(distinct),
            expected_totals={48: 2, 161: 0},
            expected_counts={48: 2, 161: 0},
        )
        rows = [
            json.loads(line)
            for line in Path(summary["inventory"]["path"]).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual({(row["article_id_type"], row["article_id"]) for row in rows}, {("int", 1), ("str", "1")})
        self.assertNotEqual(rows[0]["raw_record_sha256"], rows[1]["raw_record_sha256"])

        root = self.root / "typed_duplicate"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
        duplicate = {
            48: [
                article("01", CODE_A, "one", START_MS + 1),
                article("01", CODE_B, "two", START_MS + 2),
            ],
            161: [],
        }
        with self.assertRaises(CorpusSchemaError):
            self.execute(
                CmsFetcher(duplicate),
                expected_totals={48: 2, 161: 0},
                expected_counts={48: 2, 161: 0},
            )

    def test_v4_extractor_is_self_contained_and_cli_has_only_explicit_claim_names(self) -> None:
        source = Path(_module_sha.__code__.co_filename).read_text(encoding="utf-8")
        self.assertNotIn("from quant_research.binance_spot_announcement_corpus import", source)
        self.assertNotIn("from quant_research.binance_spot_announcement_corpus_v2 import", source)
        self.assertNotIn("from quant_research.binance_spot_announcement_corpus_v3 import", source)
        self.assertNotIn("from quant_research.binance_spot_pit import", source)
        options = {
            option
            for action in _parser()._actions
            for option in action.option_strings
        }
        self.assertIn("--list-release-date-claim-start-ms", options)
        self.assertIn("--list-release-date-claim-end-ms-exclusive", options)
        self.assertIn(
            "--expected-list-release-date-claim-interval-count", options
        )
        self.assertIn("--http-429-backoff-seconds", options)
        self.assertIn("--other-retryable-backoff-seconds", options)
        self.assertTrue(
            options.isdisjoint(
                {
                    "--claimed-release-start-ms",
                    "--claimed-release-end-ms-exclusive",
                    "--expected-interval-count",
                    "--timeout-seconds",
                    "--max-attempts",
                    "--max-wire-attempts",
                    "--pacing-seconds",
                }
            )
        )

    def test_complete_corpus_rebuilds_and_is_corpus_only(self) -> None:
        fetcher = CmsFetcher(self.catalogs)
        summary = self.execute(fetcher)
        self.assertEqual(summary["terminal_status"], "NEEDS_MORE_DATA")
        self.assertEqual(summary["artifact_state"], "ANNOUNCEMENT_CORPUS_AVAILABLE")
        self.assertEqual(summary["inventory_count"], 3)
        self.assertEqual(summary["detail_count"], 2)
        self.assertEqual(summary["time_claim_discrepancy_count"], 0)
        self.assertEqual(summary["request_count"], 8)
        self.assertEqual(summary["wire_attempt_count"], 8)
        self.assertEqual(summary["max_wire_attempts"], 3_464)
        self.assertEqual(
            summary["acquisition_bounds"]["max_wire_attempts"], 3_464
        )
        loaded = load_corpus(summary_output=self.summary, schema_output=self.schema, source_contract_output=self.source)
        self.assertEqual(len(loaded.inventory), 3)
        self.assertEqual(len(loaded.details), 2)
        self.assertEqual(loaded.time_claim_discrepancies, ())
        discrepancy = Path(summary["time_claim_discrepancies"]["path"])
        self.assertEqual(discrepancy.name, "time_claim_discrepancies.jsonl")
        self.assertEqual(discrepancy.read_bytes(), b"")
        self.assertEqual(summary["time_claim_discrepancies"]["count"], 0)
        for row in loaded.details:
            self.assertEqual(
                row["detail_publish_date_claim_ms"],
                row["list_release_date_claim_ms"],
            )
            self.assertEqual(
                row["detail_publish_minus_list_release_claim_ms"], 0
            )
        self.assertTrue(all(row["detail_version_known_at_ms"] > END_MS for row in loaded.details))
        self.assertTrue(all("body" not in row for row in loaded.details))
        self.assertTrue(all("contentJson" not in row for row in loaded.details))
        self.assertTrue(all(row["detail_body_utf8_bytes"] > 0 for row in loaded.details))
        self.assertTrue(all(row["detail_content_json_present"] for row in loaded.details))
        aliases = {
            "claimed_published_at_ms",
            "claimed_published_at_source_field",
            "detail_publish_date_ms",
        }
        self.assertTrue(all(aliases.isdisjoint(row) for row in loaded.inventory))
        self.assertTrue(all(aliases.isdisjoint(row) for row in loaded.details))
        self.assertTrue(
            {
                "claimed_release_interval_ms",
                "interval_counts",
                "expected_interval_counts",
            }.isdisjoint(summary)
        )
        for forbidden in ("events.jsonl", "listing_intervals.jsonl", "alpha.json"):
            self.assertFalse((self.processed_root / "unit" / forbidden).exists())
        source = json.loads(self.source.read_text(encoding="utf-8"))
        self.assertEqual(source["fixed_request_headers"], REQUEST_HEADERS)
        self.assertIn("ETag", source["selected_response_version_headers"])
        self.assertIn("hardcoded cap 3464", source["wire_attempt_rule"])
        summary_doc = json.loads(self.summary.read_text(encoding="utf-8"))
        summary_doc["wire_attempt_count"] += 1
        self.summary.write_text(
            json.dumps(summary_doc, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

    def test_selected_headers_are_retained_only_as_transport_clues(self) -> None:
        summary = self.execute(CmsFetcher(self.catalogs))
        ledger = Path(summary["request_ledger"]["path"])
        first = json.loads(ledger.read_text(encoding="utf-8").splitlines()[1])
        sidecar = json.loads(Path(first["attempts"][0]["sidecar"]).read_text(encoding="utf-8"))
        selected = sidecar["response"]["selected_headers"]
        for name in ("ETag", "Last-Modified", "Age", "Cache-Control", "Date"):
            self.assertIn(name, selected)
        contract = json.loads(self.source.read_text(encoding="utf-8"))
        self.assertIn("never publish/effective/known_at", contract["response_version_header_semantics"])

    def test_anchor_drift_and_expected_count_mismatch_are_inconclusive(self) -> None:
        def mutate(catalog_id, rows):
            changed = [dict(row) for row in rows]
            if catalog_id == 48:
                changed[0]["title"] = "changed"
            return changed

        drift = self.execute(CmsFetcher(self.catalogs, anchor_mutator=mutate))
        self.assertEqual(drift["terminal_status"], "INCONCLUSIVE")
        self.assertIn("CATALOG_48_FULL_PASS_DRIFT", drift["contract_failures"])

        other_root = self.root / "other"
        self.raw_root, self.processed_root = other_root / "raw", other_root / "processed"
        self.summary, self.schema, self.source = other_root / "summary.json", other_root / "schema.json", other_root / "source.json"
        mismatch = self.execute(CmsFetcher(self.catalogs), expected_counts={48: 2, 161: 1})
        self.assertEqual(mismatch["terminal_status"], "INCONCLUSIVE")
        self.assertIn(
            "CATALOG_48_LIST_RELEASE_DATE_CLAIM_INTERVAL_COUNT_MISMATCH",
            mismatch["contract_failures"],
        )

    def test_duplicate_and_page_shape_fail_closed(self) -> None:
        duplicate = {48: [article(1, CODE_A, "one", START_MS), article(1, CODE_A, "two", START_MS)], 161: []}
        with self.assertRaises(CorpusSchemaError):
            self.execute(CmsFetcher(duplicate), expected_totals={48: 2, 161: 0}, expected_counts={48: 2, 161: 0})

    def test_list_requires_exactly_one_matching_production_catalog_object(self) -> None:
        mutators = [
            lambda _catalog, _page, _items: [],
            lambda _catalog, _page, items: items + [dict(items[0])],
            lambda _catalog, _page, items: [{**items[0], "catalogId": 999}],
            lambda _catalog, _page, items: [
                {key: value for key, value in items[0].items() if key != "catalogId"}
            ],
        ]
        for index, mutator in enumerate(mutators):
            with self.subTest(index=index):
                root = self.root / f"catalog_shape_{index}"
                self.raw_root, self.processed_root = root / "raw", root / "processed"
                self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
                with self.assertRaises(CorpusSchemaError):
                    self.execute(
                        CmsFetcher(self.catalogs, catalogs_mutator=mutator)
                    )

    def test_multi_page_pagination_is_complete_and_total_drift_fails(self) -> None:
        rows = [
            article(index, f"{index:032x}", f"公告 {index}", START_MS - index)
            for index in range(1, 52)
        ]
        fetcher = CmsFetcher({48: rows, 161: []})
        summary = self.execute(
            fetcher,
            expected_totals={48: 51, 161: 0},
            expected_counts={48: 0, 161: 0},
        )
        self.assertEqual(summary["page_counts"], {"48": 2, "161": 1})
        self.assertEqual(summary["inventory_count"], 51)
        list_urls = [
            request.full_url
            for request in fetcher.requests
            if request.full_url.startswith(LIST_BASE)
        ]
        self.assertIn(_list_url(48, 2, 50), list_urls)
        self.assertEqual(list_urls.count(_list_url(48, 2, 50)), 2)
        self.assertFalse(summary["list_pass_stability"]["pass_2_merged_into_inventory"])

        root = self.root / "total_drift"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"

        def drift(catalog_id, page, total):
            return total + 1 if catalog_id == 48 and page == 2 else total

        with self.assertRaises(CorpusSchemaError):
            self.execute(
                CmsFetcher({48: rows, 161: []}, total_mutator=drift),
                expected_totals={48: 51, 161: 0},
                expected_counts={48: 0, 161: 0},
            )

    def test_global_wire_attempt_cap_is_hard_and_positive(self) -> None:
        rows = [
            article(
                index,
                f"{index:032x}",
                f"公告 {index}",
                START_MS + index if index <= 800 else START_MS - index,
            )
            for index in range(1, 2_501)
        ]
        fetcher = CmsFetcher({48: rows, 161: []})
        with mock.patch.object(corpus_v4, "MAX_WIRE_ATTEMPTS", 52):
            with self.assertRaisesRegex(
                CorpusContractError, "global wire-attempt cap exhausted"
            ):
                self.execute(
                    fetcher,
                    expected_totals={48: 2_500, 161: 0},
                    expected_counts={48: 800, 161: 0},
                    max_articles=800,
                )
        self.assertEqual(len(fetcher.requests), 52)
        self.assertIn(_list_url(48, 50, 50), [r.full_url for r in fetcher.requests])
        self.assertFalse(
            any(r.full_url.startswith(DETAIL_BASE) for r in fetcher.requests)
        )

        self.assertEqual(corpus_v4.MAX_WIRE_ATTEMPTS, 3_464)

    def test_detail_code_category_claim_type_and_content_contracts(self) -> None:
        mutators = [
            lambda item: {**item, "code": "d" * 32},
            lambda item: {**item, "firstCatalogId": 999},
            lambda item: {key: value for key, value in item.items() if key != "body"},
            lambda item: {**item, "publishDate": True},
            lambda item: {**item, "publishDate": 0},
            lambda item: {**item, "publishDate": -1},
            lambda item: {**item, "publishDate": "1"},
            lambda item: {
                key: value for key, value in item.items() if key != "publishDate"
            },
        ]
        for index, mutator in enumerate(mutators):
            with self.subTest(index=index):
                root = self.root / f"detail_{index}"
                self.raw_root, self.processed_root = root / "raw", root / "processed"
                self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
                with self.assertRaises(CorpusSchemaError):
                    self.execute(CmsFetcher(self.catalogs, detail_mutator=mutator))

    def test_independent_claims_allow_any_signed_delta_and_build_exact_subset(self) -> None:
        def mutate(item):
            if item["code"] == CODE_A:
                item["publishDate"] += 10**30
            elif item["code"] == CODE_C:
                item["publishDate"] = 1
            return item

        summary = self.execute(
            CmsFetcher(self.catalogs, detail_mutator=mutate)
        )
        details = [
            json.loads(line)
            for line in Path(summary["detail_index"]["path"])
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        discrepancies = [
            json.loads(line)
            for line in Path(summary["time_claim_discrepancies"]["path"])
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(discrepancies, details)
        self.assertEqual(
            [(row["catalog_id"], row["article_code"]) for row in discrepancies],
            sorted(
                (row["catalog_id"], row["article_code"])
                for row in discrepancies
            ),
        )
        self.assertEqual(summary["time_claim_discrepancy_count"], 2)
        self.assertEqual(summary["time_claim_discrepancies"]["count"], 2)
        self.assertGreater(
            discrepancies[0]["detail_publish_minus_list_release_claim_ms"],
            10**29,
        )
        self.assertLess(
            discrepancies[1]["detail_publish_minus_list_release_claim_ms"],
            0,
        )
        for row in discrepancies:
            self.assertEqual(
                row["detail_publish_minus_list_release_claim_ms"],
                row["detail_publish_date_claim_ms"]
                - row["list_release_date_claim_ms"],
            )
        self.assertTrue(
            {
                "expected_time_claim_discrepancy_count",
                "expected_discrepancy_count",
            }.isdisjoint(summary)
        )
        loaded = load_corpus(
            summary_output=self.summary,
            schema_output=self.schema,
            source_contract_output=self.source,
        )
        self.assertEqual(list(loaded.time_claim_discrepancies), discrepancies)

    def test_discrepancy_artifact_and_generic_alias_tamper_fail_loader(self) -> None:
        def mismatch(item):
            item["publishDate"] += 1
            return item

        summary = self.execute(
            CmsFetcher(self.catalogs, detail_mutator=mismatch)
        )
        discrepancy = Path(summary["time_claim_discrepancies"]["path"])
        discrepancy.write_bytes(discrepancy.read_bytes() + b" ")
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

        root = self.root / "discrepancy_count_tamper"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = (
            root / "summary.json",
            root / "schema.json",
            root / "source.json",
        )
        self.execute(CmsFetcher(self.catalogs, detail_mutator=mismatch))
        summary_doc = json.loads(self.summary.read_text(encoding="utf-8"))
        summary_doc["time_claim_discrepancies"]["count"] += 1
        self.summary.write_text(
            json.dumps(summary_doc, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

        root = self.root / "discrepancy_path_tamper"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = (
            root / "summary.json",
            root / "schema.json",
            root / "source.json",
        )
        self.execute(CmsFetcher(self.catalogs, detail_mutator=mismatch))
        summary_doc = json.loads(self.summary.read_text(encoding="utf-8"))
        summary_doc["time_claim_discrepancies"]["path"] = summary_doc[
            "detail_index"
        ]["path"]
        self.summary.write_text(
            json.dumps(summary_doc, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

        root = self.root / "summary_alias_tamper"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = (
            root / "summary.json",
            root / "schema.json",
            root / "source.json",
        )
        self.execute(CmsFetcher(self.catalogs))
        summary_doc = json.loads(self.summary.read_text(encoding="utf-8"))
        summary_doc["interval_counts"] = {"48": 1, "161": 1}
        self.summary.write_text(
            json.dumps(summary_doc, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

    def test_detail_body_and_optional_content_json_are_hash_only(self) -> None:
        def without_content_json(item):
            item.pop("contentJson")
            return item

        summary = self.execute(
            CmsFetcher(self.catalogs, detail_mutator=without_content_json)
        )
        details = [
            json.loads(line)
            for line in Path(summary["detail_index"]["path"])
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertTrue(all(row["detail_body_sha256"] for row in details))
        self.assertTrue(
            all(row["detail_content_json_present"] is False for row in details)
        )
        self.assertTrue(
            all(row["detail_content_json_sha256"] is None for row in details)
        )
        self.assertTrue(all("body" not in row and "contentJson" not in row for row in details))

    def test_bounded_default_reader_uses_cap_plus_one_and_oversize_is_terminal(self) -> None:
        class WireResponse:
            status = 200
            headers = {"Content-Length": "9"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, amount):
                self.requested = amount
                return b"123456789"

            def geturl(self):
                return TIME_URL

        wire = WireResponse()
        opener = mock.Mock()

        def open_request(request, timeout):
            request.add_unredirected_header("Host", "data-api.binance.vision")
            return wire

        opener.open.side_effect = open_request
        request = Request(TIME_URL, headers={"Accept-Encoding": "identity"})
        with mock.patch(
            "quant_research.binance_spot_announcement_corpus_v4.urllib.request.build_opener",
            return_value=opener,
        ) as builder:
            response = _bounded_default_fetcher(request, 1, 8)
        self.assertEqual(wire.requested, 9)
        self.assertEqual(len(response.body), 9)
        self.assertEqual(opener.open.call_count, 1)
        handlers = builder.call_args.args
        self.assertEqual(len(handlers), 2)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], _CorpusNoRedirectHandler)
        self.assertIsNone(handlers[1].redirect_request(None, None, 302, "", {}, "https://evil.example"))

        def oversized_first(url, raw):
            return b"123456789" if url == TIME_URL else raw

        root = self.root / "oversized"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
        fetcher = CmsFetcher(self.catalogs, raw_mutator=oversized_first)
        with self.assertRaises(CorpusHttpError):
            self.execute(fetcher, max_response_bytes=8)
        self.assertEqual(len(fetcher.requests), 1)
        sidecar = json.loads(
            next((self.raw_root / "unit" / "requests").rglob("*.request.json"))
            .read_text(encoding="utf-8")
        )
        self.assertEqual(sidecar["outcome"], "OVERSIZED_RESPONSE")
        self.assertEqual(sidecar["response"]["body_bytes"], 9)

    def test_malformed_retryable_response_is_terminal_and_loader_recomputes_outcomes(self) -> None:
        def mismatched_length(_url, status, raw, headers):
            if status == 429:
                headers["Content-Length"] = str(len(raw) + 1)
            return headers

        malformed = CmsFetcher(
            self.catalogs, first_status=429, header_mutator=mismatched_length
        )
        with self.assertRaises(CorpusHttpError):
            self.execute(malformed)
        self.assertEqual(len(malformed.requests), 1)

        root = self.root / "missing_length_429"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
        missing_length = CmsFetcher(
            self.catalogs, first_status=429, omit_content_length=True
        )
        with self.assertRaises(CorpusHttpError):
            self.execute(missing_length)
        self.assertEqual(len(missing_length.requests), 1)

        root = self.root / "outcome_tamper"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
        summary = self.execute(CmsFetcher(self.catalogs, first_status=429))
        ledger_path = Path(summary["request_ledger"]["path"])
        ledger = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
        attempt = ledger[0]["attempts"][0]
        sidecar_path = Path(attempt["sidecar"])
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["outcome"] = "CONTENT_LENGTH_MISMATCH"
        sidecar_bytes = (
            json.dumps(sidecar, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        sidecar_path.write_bytes(sidecar_bytes)
        attempt["outcome"] = "CONTENT_LENGTH_MISMATCH"
        attempt["sidecar_sha256"] = hashlib.sha256(sidecar_bytes).hexdigest()
        ledger_bytes = b"".join(
            (
                json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                + "\n"
            ).encode("utf-8")
            for row in ledger
        )
        ledger_path.write_bytes(ledger_bytes)
        summary_doc = json.loads(self.summary.read_text(encoding="utf-8"))
        summary_doc["request_ledger"]["sha256"] = hashlib.sha256(ledger_bytes).hexdigest()
        self.summary.write_text(
            json.dumps(summary_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

    def test_expected_extractor_hash_mismatch_stops_before_lease_or_fetch(self) -> None:
        fetcher = CmsFetcher(self.catalogs)
        with self.assertRaises(CorpusIntegrityError):
            self.execute(fetcher, expected_extractor_sha256="0" * 64)
        self.assertEqual(fetcher.requests, [])
        self.assertFalse((self.raw_root / "unit").exists())

    def test_extractor_drift_after_lease_stops_before_first_get(self) -> None:
        expected = _module_sha()
        fetcher = CmsFetcher(self.catalogs)
        with mock.patch(
            "quant_research.binance_spot_announcement_corpus_v4._module_sha",
            side_effect=[expected, "0" * 64],
        ):
            with self.assertRaises(CorpusIntegrityError):
                self.execute(fetcher, expected_extractor_sha256=expected)
        self.assertEqual(fetcher.requests, [])
        self.assertTrue((self.raw_root / "unit").is_dir())
        self.assertFalse((self.raw_root / "unit" / "requests").exists())

    def test_extractor_drift_between_retry_attempts_stops_second_get(self) -> None:
        expected = _module_sha()
        fetcher = CmsFetcher(self.catalogs, first_status=429)
        clock = FakeMonotonic()

        def observed_sha():
            return "0" * 64 if clock.sleeps else expected

        with mock.patch(
            "quant_research.binance_spot_announcement_corpus_v4._module_sha",
            side_effect=observed_sha,
        ):
            with self.assertRaises(CorpusIntegrityError):
                self.execute(
                    fetcher,
                    expected_extractor_sha256=expected,
                    clock=clock,
                )
        self.assertEqual(len(fetcher.requests), 1)
        sidecars = list((self.raw_root / "unit" / "requests").rglob("*.request.json"))
        self.assertEqual(len(sidecars), 1)
        self.assertEqual(
            json.loads(sidecars[0].read_text(encoding="utf-8"))["outcome"],
            "HTTP_429",
        )

    def test_source_drift_during_final_fetch_is_rejected(self) -> None:
        expected = _module_sha()
        module_fetcher = CmsFetcher(self.catalogs)

        def drifting_module_sha():
            return "0" * 64 if len(module_fetcher.requests) == 8 else expected

        with mock.patch(
            "quant_research.binance_spot_announcement_corpus_v4._module_sha",
            side_effect=drifting_module_sha,
        ):
            with self.assertRaises(CorpusIntegrityError):
                self.execute(
                    module_fetcher, expected_extractor_sha256=expected
                )
        self.assertEqual(len(module_fetcher.requests), 8)
        self.assertFalse(self.summary.exists())
        self.assertTrue(
            (self.raw_root / "unit" / "terminal_schedule.json").exists()
        )
        self.assertFalse(
            (self.raw_root / "unit" / "requests" / "time_after").exists()
        )

    def test_source_drift_after_final_artifacts_marks_raw_run_terminal(self) -> None:
        expected = _module_sha()
        fetcher = CmsFetcher(self.catalogs)

        def drifting_module_sha():
            return (
                "0" * 64
                if self.summary.exists()
                and self.schema.exists()
                and self.source.exists()
                else expected
            )

        with mock.patch(
            "quant_research.binance_spot_announcement_corpus_v4._module_sha",
            side_effect=drifting_module_sha,
        ):
            with self.assertRaises(CorpusIntegrityError):
                self.execute(fetcher, expected_extractor_sha256=expected)
        terminal = self.raw_root / "unit" / "terminal_schedule.json"
        self.assertTrue(terminal.exists())
        self.assertEqual(
            json.loads(terminal.read_text(encoding="utf-8"))["reason"],
            "SOURCE_DRIFT_POST_FINAL_ARTIFACTS",
        )
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

    def test_missing_content_length_and_reversed_clock_fail_closed(self) -> None:
        with self.assertRaises(CorpusHttpError):
            self.execute(CmsFetcher(self.catalogs, omit_content_length=True))
        root = self.root / "clock"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
        with self.assertRaises(CorpusContractError):
            self.execute(CmsFetcher(self.catalogs, reversed_time=True))

    def test_invalid_json_and_nonmonotone_local_clock_fail_closed(self) -> None:
        def corrupt_first_list(url, raw):
            return b"not-json" if url == _list_url(48, 1, 50) else raw

        with self.assertRaises(CorpusSchemaError):
            self.execute(CmsFetcher(self.catalogs, raw_mutator=corrupt_first_list))

        root = self.root / "local_clock"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"

        def regress(clock, completed):
            return "2026-04-28T23:59:59Z" if clock == 3 else completed

        with self.assertRaises(CorpusContractError):
            self.execute(CmsFetcher(self.catalogs, local_clock_mutator=regress))

    def test_retry_attempt_chain_is_root_bound_and_tamper_fails_loader(self) -> None:
        summary = self.execute(CmsFetcher(self.catalogs, first_status=429))
        ledger = Path(summary["request_ledger"]["path"])
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([item["outcome"] for item in rows[0]["attempts"]], ["HTTP_429", "OK"])
        Path(rows[0]["attempts"][0]["body"]).write_bytes(b"tampered")
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(summary_output=self.summary, schema_output=self.schema, source_contract_output=self.source)

    def test_artifact_and_source_tamper_fail_loader(self) -> None:
        summary = self.execute(CmsFetcher(self.catalogs))
        inventory = Path(summary["inventory"]["path"])
        inventory.write_bytes(inventory.read_bytes() + b" ")
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(summary_output=self.summary, schema_output=self.schema, source_contract_output=self.source)

        root = self.root / "source_tamper"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
        self.execute(CmsFetcher(self.catalogs))
        source = json.loads(self.source.read_text(encoding="utf-8"))
        source["authentication"] = "KEY"
        self.source.write_text(json.dumps(source), encoding="utf-8")
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(summary_output=self.summary, schema_output=self.schema, source_contract_output=self.source)

    def test_root_summary_unknown_alpha_field_fails_closed(self) -> None:
        self.execute(CmsFetcher(self.catalogs))
        summary = json.loads(self.summary.read_text(encoding="utf-8"))
        summary["alpha"] = {"fabricated": True}
        self.summary.write_bytes(corpus_v4._stable_json_bytes(summary, pretty=True))
        with self.assertRaisesRegex(
            CorpusIntegrityError, "top-level field set mismatch"
        ):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

    def test_root_summary_noncanonical_bytes_fail_closed(self) -> None:
        self.execute(CmsFetcher(self.catalogs))
        summary = json.loads(self.summary.read_text(encoding="utf-8"))
        self.summary.write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(CorpusIntegrityError, "non-canonical"):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

    def test_root_summary_duplicate_top_level_key_fails_closed(self) -> None:
        self.execute(CmsFetcher(self.catalogs))
        original = self.summary.read_text(encoding="utf-8")
        self.assertTrue(original.endswith("}\n"))
        duplicated = original[:-2] + ',\n  "run_id": "unit"\n}\n'
        self.summary.write_text(duplicated, encoding="utf-8")
        with self.assertRaisesRegex(CorpusIntegrityError, "duplicate JSON"):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

    def test_atomic_run_lease_and_output_no_overwrite(self) -> None:
        self.execute(CmsFetcher(self.catalogs))
        second = CmsFetcher(self.catalogs)
        with self.assertRaises(CorpusExistingError):
            self.execute(second)
        self.assertEqual(second.requests, [])

    def test_absolute_retry_matrix_mixed_statuses_and_receipt_delays(self) -> None:
        cases = [
            ([429, 500, 429, 200], [30.0, 2.0, 120.0]),
            ([500, 500, 429, 200], [1.0, 2.0, 120.0]),
        ]
        for index, (statuses, expected_delays) in enumerate(cases):
            with self.subTest(statuses=statuses):
                self.reset_paths(f"mixed_{index}")
                clock = FakeMonotonic()
                fetcher = StatusSequenceFetcher(
                    CmsFetcher(self.catalogs), statuses
                )
                summary = self.execute(fetcher, clock=clock)
                self.assertEqual(clock.sleeps[:3], expected_delays)
                self.assertEqual(clock.sleeps[3:], [1.0] * 7)
                ledger = [
                    json.loads(line)
                    for line in Path(summary["request_ledger"]["path"])
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
                receipts = [
                    json.loads(Path(row["receipt"]).read_text(encoding="utf-8"))
                    for row in ledger[0]["attempts"]
                ]
                self.assertEqual(
                    [row["requested_delay_ns"] for row in receipts[:3]],
                    [int(value * 1_000_000_000) for value in expected_delays],
                )
                self.assertTrue(all(row["decision"] == "NEXT_WIRE" for row in receipts))
                load_corpus(
                    summary_output=self.summary,
                    schema_output=self.schema,
                    source_contract_output=self.source,
                )

    def test_other_retryable_attempt_three_is_terminal(self) -> None:
        clock = FakeMonotonic()
        fetcher = StatusSequenceFetcher(
            CmsFetcher(self.catalogs), [500, 429, 500]
        )
        with self.assertRaises(CorpusHttpError):
            self.execute(fetcher, clock=clock)
        self.assertEqual(clock.sleeps, [1.0, 60.0])
        receipts = sorted(
            (self.raw_root / "unit" / "requests").rglob(
                "*.receipt.json"
            )
        )
        self.assertEqual(len(receipts), 3)
        terminal = json.loads(receipts[-1].read_text(encoding="utf-8"))
        self.assertEqual(terminal["decision"], "NO_NEXT_WIRE")
        self.assertEqual(terminal["terminal_reason"], "RETRY_SCHEDULE_EXHAUSTED")
        self.assertFalse(self.summary.exists())

        self.reset_paths("http_429_attempt_four_terminal")
        clock_429 = FakeMonotonic()
        fetcher_429 = StatusSequenceFetcher(
            CmsFetcher(self.catalogs), [429, 429, 429, 429]
        )
        with self.assertRaises(CorpusHttpError):
            self.execute(fetcher_429, clock=clock_429)
        self.assertEqual(clock_429.sleeps, [30.0, 60.0, 120.0])
        receipts_429 = sorted(
            (self.raw_root / "unit" / "requests").rglob(
                "*.receipt.json"
            )
        )
        self.assertEqual(len(receipts_429), 4)
        final_429 = json.loads(receipts_429[-1].read_text(encoding="utf-8"))
        self.assertEqual(
            final_429["terminal_reason"], "RETRY_SCHEDULE_EXHAUSTED"
        )

    def test_every_retryable_status_and_retry_after_presence_matrix(self) -> None:
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.reset_paths(f"retryable_{status}")
                clock = FakeMonotonic()
                fetcher = StatusSequenceFetcher(
                    CmsFetcher(self.catalogs), [status, 200]
                )
                self.execute(fetcher, clock=clock)
                self.assertEqual(clock.sleeps[0], 30.0 if status == 429 else 1.0)

                self.reset_paths(f"retry_after_{status}")
                terminal_clock = FakeMonotonic()
                terminal_fetcher = StatusSequenceFetcher(
                    CmsFetcher(self.catalogs),
                    [status],
                    retry_after=("rEtRy-AfTeR", ""),
                )
                with self.assertRaises(CorpusHttpError):
                    self.execute(terminal_fetcher, clock=terminal_clock)
                self.assertEqual(terminal_clock.sleeps, [])
                receipt = next(
                    (self.raw_root / "unit" / "requests").rglob(
                        "*.receipt.json"
                    )
                )
                record = json.loads(receipt.read_text(encoding="utf-8"))
                self.assertTrue(record["retry_after_present"])
                self.assertEqual(record["terminal_reason"], "RETRY_AFTER_PRESENT")

    def test_monotonic_budget_pre_sleep_post_sleep_and_postfetch_fail_closed(self) -> None:
        start = 1_000_000_000

        class JumpingFetcher:
            def __init__(self, wrapped, clock, target):
                self.wrapped, self.clock, self.target = wrapped, clock, target
                self.requests = wrapped.requests

            def __call__(self, request, timeout):
                response = self.wrapped(request, timeout)
                self.clock.value = self.target
                return response

        pre_sleep_clock = FakeMonotonic(start)
        pre_sleep_base = StatusSequenceFetcher(
            CmsFetcher(self.catalogs), [429]
        )
        pre_sleep = JumpingFetcher(
            pre_sleep_base,
            pre_sleep_clock,
            start + WALL_BUDGET_NS - 20_000_000_000,
        )
        with self.assertRaisesRegex(CorpusContractError, "before sleep"):
            self.execute(pre_sleep, clock=pre_sleep_clock)
        self.assertTrue(
            (self.raw_root / "unit" / "terminal_schedule.json").exists()
        )
        self.assertFalse(
            any((self.raw_root / "unit" / "requests").rglob("*.receipt.json"))
        )

        self.reset_paths("sleep_under_run")

        class UnderRunClock(FakeMonotonic):
            def sleep(self, seconds: float) -> None:
                self.sleeps.append(seconds)

        under = UnderRunClock()
        with self.assertRaisesRegex(CorpusContractError, "monotonic"):
            self.execute(
                StatusSequenceFetcher(CmsFetcher(self.catalogs), [429]),
                clock=under,
            )
        self.assertTrue(
            (self.raw_root / "unit" / "terminal_schedule.json").exists()
        )

        self.reset_paths("postfetch_deadline")
        deadline_clock = FakeMonotonic(start)
        deadline = JumpingFetcher(
            CmsFetcher(self.catalogs),
            deadline_clock,
            start + WALL_BUDGET_NS,
        )
        with self.assertRaises(CorpusHttpError):
            self.execute(deadline, clock=deadline_clock)
        receipt = next(
            (self.raw_root / "unit" / "requests").rglob("*.receipt.json")
        )
        record = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertFalse(record["accepted"])
        self.assertEqual(record["terminal_reason"], "WALL_DEADLINE_POSTFETCH")
        self.assertFalse(
            (self.raw_root / "unit" / "terminal_schedule.json").exists()
        )

    def test_timeout_never_rounds_above_remaining_nanoseconds(self) -> None:
        for timeout_ns in (1, 2, 999_999_999, 30_000_000_000):
            value = _timeout_seconds_without_up_round(timeout_ns)
            self.assertGreater(value, 0)
            self.assertLessEqual(value * 1_000_000_000, timeout_ns)

    def test_parser_abort_gets_terminal_receipt_and_transport_gets_schedule(self) -> None:
        def corrupt_first_list(url, raw):
            return b"not-json" if url == _list_url(48, 1, 50) else raw

        with self.assertRaises(CorpusSchemaError):
            self.execute(
                CmsFetcher(self.catalogs, raw_mutator=corrupt_first_list)
            )
        receipts = sorted(
            (self.raw_root / "unit" / "requests").rglob(
                "*.receipt.json"
            )
        )
        self.assertEqual(len(receipts), 2)
        parser_receipt = next(
            record
            for record in (
                json.loads(path.read_text(encoding="utf-8"))
                for path in receipts
            )
            if record["terminal_reason"]
            == "COLLECTOR_ABORTED_BEFORE_NEXT_WIRE"
        )
        self.assertFalse(parser_receipt["accepted"])
        self.assertEqual(
            parser_receipt["terminal_reason"],
            "COLLECTOR_ABORTED_BEFORE_NEXT_WIRE",
        )

        self.reset_paths("transport")

        def transport_failure(_request, _timeout):
            raise urllib.error.URLError("proxy://user:secret@example.invalid")

        with self.assertRaises(CorpusHttpError) as captured:
            self.execute(transport_failure)
        self.assertNotIn("secret", str(captured.exception))
        schedule = json.loads(
            (self.raw_root / "unit" / "terminal_schedule.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schedule["reason"], "TRANSPORT_EXCEPTION")
        self.assertEqual(schedule["wire_attempt_count"], 1)
        self.assertFalse((self.raw_root / "unit" / "requests").exists())

    def test_source_drift_after_raw_before_receipt_preserves_orphan_and_schedule(self) -> None:
        expected = _module_sha()
        fetcher = CmsFetcher(self.catalogs, first_status=429)
        request_root = self.raw_root / "unit" / "requests"

        def observed_sha():
            return (
                "0" * 64
                if request_root.exists()
                and any(request_root.rglob("*.request.json"))
                else expected
            )

        with mock.patch(
            "quant_research.binance_spot_announcement_corpus_v4._module_sha",
            side_effect=observed_sha,
        ):
            with self.assertRaises(CorpusIntegrityError):
                self.execute(fetcher, expected_extractor_sha256=expected)
        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(len(list(request_root.rglob("*.response"))), 1)
        self.assertEqual(len(list(request_root.rglob("*.request.json"))), 1)
        self.assertEqual(len(list(request_root.rglob("*.receipt.json"))), 0)
        self.assertTrue(
            (self.raw_root / "unit" / "terminal_schedule.json").exists()
        )

    def test_success_raw_summary_receipt_chain_tree_and_bijections(self) -> None:
        clock = FakeMonotonic()
        summary = self.execute(CmsFetcher(self.catalogs), clock=clock)
        self.assertEqual(clock.sleeps, [1.0] * 7)
        raw_summary_path = Path(summary["raw_summary"]["path"])
        raw_summary = json.loads(raw_summary_path.read_text(encoding="utf-8"))
        self.assertEqual(raw_summary["logical_request_count"], 8)
        self.assertEqual(raw_summary["wire_attempt_count"], 8)
        self.assertEqual(len(raw_summary["receipt_tree"]), 8)
        self.assertEqual(len(raw_summary["raw_artifact_tree"]), 26)
        self.assertNotIn(
            "summary.json",
            {row["path"] for row in raw_summary["raw_artifact_tree"]},
        )
        self.assertEqual(
            raw_summary["selected_detail_keyset_sha256"],
            raw_summary["accepted_detail_keyset_sha256"],
        )
        self.assertEqual(
            {
                raw_summary["attempt_keyset_sha256"],
                raw_summary["body_keyset_sha256"],
                raw_summary["sidecar_keyset_sha256"],
                raw_summary["receipt_keyset_sha256"],
            },
            {raw_summary["attempt_keyset_sha256"]},
        )
        previous = None
        for item in raw_summary["receipt_tree"]:
            receipt = json.loads(Path(item["path"]).read_text(encoding="utf-8"))
            self.assertEqual(receipt["previous_receipt_sha256"], previous)
            previous = item["sha256"]
        self.assertEqual(previous, raw_summary["final_receipt_sha256"])
        load_corpus(
            summary_output=self.summary,
            schema_output=self.schema,
            source_contract_output=self.source,
        )

    def test_verified_response_recomputes_retry_delay_not_only_hashes(self) -> None:
        summary = self.execute(CmsFetcher(self.catalogs, first_status=429))
        ledger_path = Path(summary["request_ledger"]["path"])
        ledger = [
            json.loads(line)
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
        ]
        raw_summary = json.loads(
            Path(summary["raw_summary"]["path"]).read_text(encoding="utf-8")
        )
        runtime = json.loads(
            Path(raw_summary["runtime_contract"]["path"]).read_text(
                encoding="utf-8"
            )
        )
        first = ledger[0]["attempts"][0]
        receipt_path = Path(first["receipt"])
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["requested_delay_ns"] = 31_000_000_000
        mutated = (
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        receipt_path.write_bytes(mutated)
        first["receipt_sha256"] = hashlib.sha256(mutated).hexdigest()
        with self.assertRaisesRegex(
            CorpusIntegrityError, "retry receipt decision mismatch"
        ):
            _verified_response(
                ledger[0],
                "unit",
                runtime,
                [0],
                [None],
                [0],
                [None],
                [],
                {},
            )

    def test_receipt_raw_summary_extra_file_and_terminal_tamper_fail_loader(self) -> None:
        summary = self.execute(CmsFetcher(self.catalogs))
        raw_summary = json.loads(
            Path(summary["raw_summary"]["path"]).read_text(encoding="utf-8")
        )
        receipt = Path(raw_summary["receipt_tree"][0]["path"])
        receipt.write_bytes(receipt.read_bytes() + b" ")
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

        self.reset_paths("extra_raw")
        summary = self.execute(CmsFetcher(self.catalogs))
        raw_run = Path(summary["raw_summary"]["path"]).parent
        (raw_run / "unexpected.bin").write_bytes(b"x")
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )

        self.reset_paths("terminal_tamper")
        summary = self.execute(CmsFetcher(self.catalogs))
        raw_run = Path(summary["raw_summary"]["path"]).parent
        (raw_run / "terminal_schedule.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(CorpusIntegrityError):
            load_corpus(
                summary_output=self.summary,
                schema_output=self.schema,
                source_contract_output=self.source,
            )


if __name__ == "__main__":
    unittest.main()
