from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock
from urllib.request import Request

from quant_research.binance_spot_announcement_corpus import (
    DETAIL_BASE,
    LIST_BASE,
    REQUEST_HEADERS,
    CorpusContractError,
    CorpusExistingError,
    CorpusHttpError,
    CorpusIntegrityError,
    CorpusSchemaError,
    TransportResponse,
    _CorpusNoRedirectHandler,
    _bounded_default_fetcher,
    _dependency_sha,
    _detail_url,
    _list_url,
    _module_sha,
    _request,
    _validate_canonical_url,
    load_corpus,
    run_corpus,
)
from quant_research.binance_spot_pit import TIME_URL


BASE_MS = 1_777_420_800_000
BASE_UTC = "2026-04-29T00:00:00Z"
START_MS = 1_669_852_800_000
END_MS = 1_735_689_600_000
CODE_A = "a" * 32
CODE_B = "b" * 32
CODE_C = "c" * 32


def body(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def article(article_id: int, code: str, title: str, release: int) -> dict[str, object]:
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


class AnnouncementCorpusTests(unittest.TestCase):
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

    def execute(
        self,
        fetcher: CmsFetcher,
        *,
        expected_totals=None,
        expected_counts=None,
        run_id="unit",
        expected_extractor_sha256=None,
        max_response_bytes=2_000_000,
        max_wire_attempts=2_598,
        max_articles=10,
    ):
        return run_corpus(
            run_id=run_id,
            expected_extractor_sha256=expected_extractor_sha256 or _module_sha(),
            raw_root=self.raw_root,
            processed_root=self.processed_root,
            summary_output=self.summary,
            schema_output=self.schema,
            source_contract_output=self.source,
            claimed_release_start_ms=START_MS,
            claimed_release_end_ms_exclusive=END_MS,
            expected_totals=expected_totals or {48: 2, 161: 1},
            expected_interval_counts=expected_counts or {48: 1, 161: 1},
            max_pages_per_catalog=50,
            max_articles=max_articles,
            max_response_bytes=max_response_bytes,
            max_total_response_bytes=10_000_000,
            timeout_seconds=1,
            max_attempts=3,
            max_wire_attempts=max_wire_attempts,
            pacing_seconds=0,
            max_clock_skew_ms=300_000,
            fetcher=fetcher,
            sleeper=lambda _delay: None,
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

    def test_complete_corpus_rebuilds_and_is_corpus_only(self) -> None:
        fetcher = CmsFetcher(self.catalogs)
        summary = self.execute(fetcher)
        self.assertEqual(summary["terminal_status"], "NEEDS_MORE_DATA")
        self.assertEqual(summary["artifact_state"], "ANNOUNCEMENT_CORPUS_AVAILABLE")
        self.assertEqual(summary["inventory_count"], 3)
        self.assertEqual(summary["detail_count"], 2)
        self.assertEqual(summary["request_count"], 8)
        self.assertEqual(summary["wire_attempt_count"], 8)
        self.assertEqual(summary["max_wire_attempts"], 2_598)
        self.assertEqual(
            summary["acquisition_bounds"]["max_wire_attempts"], 2_598
        )
        loaded = load_corpus(summary_output=self.summary, schema_output=self.schema, source_contract_output=self.source)
        self.assertEqual(len(loaded.inventory), 3)
        self.assertEqual(len(loaded.details), 2)
        self.assertTrue(all(row["detail_version_known_at_ms"] > END_MS for row in loaded.details))
        self.assertTrue(all("body" not in row for row in loaded.details))
        self.assertTrue(all("contentJson" not in row for row in loaded.details))
        self.assertTrue(all(row["detail_body_utf8_bytes"] > 0 for row in loaded.details))
        self.assertTrue(all(row["detail_content_json_present"] for row in loaded.details))
        for forbidden in ("events.jsonl", "listing_intervals.jsonl", "alpha.json"):
            self.assertFalse((self.processed_root / "unit" / forbidden).exists())
        source = json.loads(self.source.read_text(encoding="utf-8"))
        self.assertEqual(source["fixed_request_headers"], REQUEST_HEADERS)
        self.assertIn("ETag", source["selected_response_version_headers"])
        self.assertIn("no attempt above the cap", source["wire_attempt_rule"])
        with mock.patch(
            "quant_research.binance_spot_announcement_corpus._dependency_sha",
            return_value="0" * 64,
        ):
            with self.assertRaises(CorpusIntegrityError):
                load_corpus(
                    summary_output=self.summary,
                    schema_output=self.schema,
                    source_contract_output=self.source,
                )
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
        self.assertIn("CATALOG_48_INTERVAL_COUNT_MISMATCH", mismatch["contract_failures"])

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
        with self.assertRaisesRegex(
            CorpusContractError, "global wire-attempt cap exhausted"
        ):
            self.execute(
                fetcher,
                expected_totals={48: 2_500, 161: 0},
                expected_counts={48: 800, 161: 0},
                max_articles=800,
                max_wire_attempts=52,
            )
        self.assertEqual(len(fetcher.requests), 52)
        self.assertIn(_list_url(48, 50, 50), [r.full_url for r in fetcher.requests])
        self.assertFalse(
            any(r.full_url.startswith(DETAIL_BASE) for r in fetcher.requests)
        )

        root = self.root / "zero_wire_cap"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
        zero_fetcher = CmsFetcher(self.catalogs)
        with self.assertRaisesRegex(CorpusContractError, "invalid acquisition bounds"):
            self.execute(zero_fetcher, max_wire_attempts=0)
        self.assertEqual(zero_fetcher.requests, [])
        self.assertFalse((self.raw_root / "unit").exists())

    def test_detail_code_category_publish_and_content_contracts(self) -> None:
        mutators = [
            lambda item: {**item, "code": "d" * 32},
            lambda item: {**item, "firstCatalogId": 999},
            lambda item: {**item, "publishDate": item["publishDate"] + 1},
            lambda item: {key: value for key, value in item.items() if key != "body"},
        ]
        for index, mutator in enumerate(mutators):
            with self.subTest(index=index):
                root = self.root / f"detail_{index}"
                self.raw_root, self.processed_root = root / "raw", root / "processed"
                self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
                with self.assertRaises(CorpusSchemaError):
                    self.execute(CmsFetcher(self.catalogs, detail_mutator=mutator))

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
            "quant_research.binance_spot_announcement_corpus.urllib.request.build_opener",
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
            "quant_research.binance_spot_announcement_corpus._module_sha",
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
        with mock.patch(
            "quant_research.binance_spot_announcement_corpus._module_sha",
            side_effect=[expected, expected, expected, "0" * 64],
        ):
            with self.assertRaises(CorpusIntegrityError):
                self.execute(fetcher, expected_extractor_sha256=expected)
        self.assertEqual(len(fetcher.requests), 1)
        sidecars = list((self.raw_root / "unit" / "requests").rglob("*.request.json"))
        self.assertEqual(len(sidecars), 1)
        self.assertEqual(
            json.loads(sidecars[0].read_text(encoding="utf-8"))["outcome"],
            "HTTP_429",
        )

    def test_source_or_dependency_drift_during_final_fetch_is_rejected(self) -> None:
        expected = _module_sha()
        module_calls = [0]

        def drifting_module_sha():
            module_calls[0] += 1
            return "0" * 64 if module_calls[0] == 17 else expected

        module_fetcher = CmsFetcher(self.catalogs)
        with mock.patch(
            "quant_research.binance_spot_announcement_corpus._module_sha",
            side_effect=drifting_module_sha,
        ):
            with self.assertRaises(CorpusIntegrityError):
                self.execute(
                    module_fetcher, expected_extractor_sha256=expected
                )
        self.assertEqual(len(module_fetcher.requests), 8)
        self.assertFalse(self.summary.exists())

        root = self.root / "dependency_final_fetch"
        self.raw_root, self.processed_root = root / "raw", root / "processed"
        self.summary, self.schema, self.source = root / "summary.json", root / "schema.json", root / "source.json"
        dependency_expected = _dependency_sha()
        dependency_calls = [0]

        def drifting_dependency_sha():
            dependency_calls[0] += 1
            return "0" * 64 if dependency_calls[0] == 17 else dependency_expected

        dependency_fetcher = CmsFetcher(self.catalogs)
        with mock.patch(
            "quant_research.binance_spot_announcement_corpus._dependency_sha",
            side_effect=drifting_dependency_sha,
        ):
            with self.assertRaises(CorpusIntegrityError):
                self.execute(
                    dependency_fetcher, expected_extractor_sha256=expected
                )
        self.assertEqual(len(dependency_fetcher.requests), 8)
        self.assertFalse(self.summary.exists())

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

    def test_atomic_run_lease_and_output_no_overwrite(self) -> None:
        self.execute(CmsFetcher(self.catalogs))
        second = CmsFetcher(self.catalogs)
        with self.assertRaises(CorpusExistingError):
            self.execute(second)
        self.assertEqual(second.requests, [])


if __name__ == "__main__":
    unittest.main()
