from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock
from urllib.request import Request

from quant_research.binance_spot_pit import (
    EXCHANGE_INFO_URL,
    TIME_URL,
    PITClockError,
    PITContractError,
    PITExistingEvidenceError,
    PITHttpError,
    PITIntegrityError,
    PITSchemaError,
    TransportResponse,
    _NoRedirectHandler,
    _default_fetcher,
    load_snapshot,
    run_snapshot,
    strict_gate_result,
    to_alpha_snapshot,
)
from quant_research.hierarchical_alpha import require_pit_eligibility


BASE_MS = 1_777_420_800_000
BASE_UTC = "2026-04-29T00:00:00Z"


def stable_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def symbol(
    name: str,
    *,
    status: str = "TRADING",
    quote: str = "USDT",
    spot: bool = True,
) -> dict[str, object]:
    return {
        "symbol": name,
        "status": status,
        "baseAsset": name.removesuffix(quote) or "BASE",
        "quoteAsset": quote,
        "isSpotTradingAllowed": spot,
        "permissions": ["SPOT"] if spot else [],
        "permissionSets": [["SPOT"]] if spot else [],
        "filters": [],
    }


def response(
    url: str,
    payload: object | bytes,
    *,
    status: int = 200,
    started: str | None = None,
    completed: str = BASE_UTC,
    headers: dict[str, str] | None = None,
) -> TransportResponse:
    body = payload if isinstance(payload, bytes) else stable_bytes(payload)
    response_headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
    if headers:
        response_headers.update(headers)
    return TransportResponse(
        status=status,
        headers=response_headers,
        body=body,
        final_url=url,
        request_started_at_utc=started or completed,
        response_completed_at_utc=completed,
    )


class FakeFetcher:
    def __init__(self, responses: list[TransportResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float) -> TransportResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class FailingFetcher:
    def __init__(self) -> None:
        self.requests: list[Request] = []

    def __call__(self, request: Request, timeout: float) -> TransportResponse:
        self.requests.append(request)
        raise OSError(
            "proxy http://proxy-user:proxy-secret@127.0.0.1:8080 failed"
        )


class BinanceSpotPITTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw_root = self.root / "raw"
        self.processed_root = self.root / "processed"
        self.summary = self.root / "artifacts" / "summary.json"
        self.schema = self.root / "artifacts" / "schema.json"
        self.gate = self.root / "artifacts" / "gate.json"
        self.symbol_index = self.root / "symbol_index.jsonl"
        rows = [
            {"symbol": "ARCHIVEONLYUSDT", "suffix_candidate": True},
            {"symbol": "BTCUSDT", "suffix_candidate": True},
            {"symbol": "IGNORED", "suffix_candidate": False},
        ]
        body = b"".join(stable_bytes(row) for row in rows)
        self.symbol_index.write_bytes(body)
        self.symbol_index_sha = hashlib.sha256(body).hexdigest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def good_fetcher(self, records: list[dict[str, object]] | None = None) -> FakeFetcher:
        if records is None:
            records = [symbol("BTCUSDT"), symbol("币USDT", status="HALT")]
        return FakeFetcher(
            [
                response(TIME_URL, {"serverTime": BASE_MS}),
                response(
                    EXCHANGE_INFO_URL,
                    {"serverTime": BASE_MS + 100, "timezone": "UTC", "symbols": records},
                    completed="2026-04-29T00:00:00.100000Z",
                ),
                response(
                    TIME_URL,
                    {"serverTime": BASE_MS + 200},
                    completed="2026-04-29T00:00:00.200000Z",
                ),
            ]
        )

    def run_good(self, *, snapshot_id: str = "unit", fetcher: FakeFetcher | None = None):
        selected = fetcher or self.good_fetcher()
        summary = run_snapshot(
            snapshot_id=snapshot_id,
            raw_root=self.raw_root,
            processed_root=self.processed_root,
            symbol_index_path=self.symbol_index,
            symbol_index_sha256=self.symbol_index_sha,
            summary_output=self.summary,
            schema_output=self.schema,
            gate_output=self.gate,
            fetcher=selected,
            sleeper=lambda _delay: None,
        )
        return summary, selected

    def test_complete_unfiltered_unauthenticated_bracket_and_raw_sidecars(self) -> None:
        summary, fetcher = self.run_good()
        self.assertEqual(
            [request.full_url for request in fetcher.requests],
            [TIME_URL, EXCHANGE_INFO_URL, TIME_URL],
        )
        self.assertEqual(
            fetcher.requests[1].full_url,
            "https://data-api.binance.vision/api/v3/exchangeInfo?showPermissionSets=true",
        )
        for request in fetcher.requests:
            self.assertEqual(request.get_method(), "GET")
            names = {name.lower() for name, _value in request.header_items()}
            self.assertTrue(
                names.isdisjoint(
                    {"authorization", "cookie", "proxy-authorization", "x-mbx-apikey"}
                )
            )
        self.assertEqual(
            summary["server_time_bracket_ms"],
            {"before": BASE_MS, "exchange_info": BASE_MS + 100, "after": BASE_MS + 200},
        )
        for label in ("time_before", "exchange_info", "time_after"):
            reference = summary["raw_requests"][label]
            self.assertTrue(Path(reference["body"]).is_file())
            self.assertTrue(Path(reference["sidecar"]).is_file())
        sidecar = json.loads(Path(summary["raw_requests"]["exchange_info"]["sidecar"]).read_text())
        self.assertEqual(sidecar["request"]["canonical_parameters"], {"showPermissionSets": "true"})
        self.assertEqual(sidecar["request"]["authentication"], "NONE")
        self.assertEqual(sidecar["response"]["response_completed_at_utc"], summary["known_at_utc"])

    def test_unicode_stable_order_archive_only_unknown_and_listing_never_fabricated(self) -> None:
        self.run_good()
        loaded = load_snapshot(
            summary_output=self.summary,
            symbol_index_path=self.symbol_index,
            symbol_index_sha256=self.symbol_index_sha,
        )
        rows = list(loaded.memberships)
        self.assertEqual([row["symbol"] for row in rows], sorted(row["symbol"] for row in rows))
        archive_only = next(row for row in rows if row["symbol"] == "ARCHIVEONLYUSDT")
        self.assertFalse(archive_only["current_response_observed"])
        self.assertIsNone(archive_only["raw_status"])
        self.assertIsNone(archive_only["raw_quote_asset"])
        self.assertIsNone(archive_only["raw_is_spot_trading_allowed"])
        for row in rows:
            self.assertIsNone(row["listing_from_ms"])
            self.assertIsNone(row["listing_to_ms_exclusive"])
            self.assertFalse(row["strict_eligible"])
            self.assertIn("UNKNOWN_LISTING_WINDOW", row["strict_reasons"])

    def test_current_evidence_cannot_be_used_for_earlier_formation(self) -> None:
        self.run_good()
        loaded = load_snapshot(
            summary_output=self.summary,
            symbol_index_path=self.symbol_index,
            symbol_index_sha256=self.symbol_index_sha,
        )
        with self.assertRaises(PITClockError):
            to_alpha_snapshot(loaded, formation_time_ms=loaded.known_at_ms - 1)

    def test_strict_gate_integration_keeps_every_symbol_ineligible(self) -> None:
        self.run_good()
        loaded = load_snapshot(
            summary_output=self.summary,
            symbol_index_path=self.symbol_index,
            symbol_index_sha256=self.symbol_index_sha,
        )
        gate = strict_gate_result(loaded, formation_time_ms=loaded.known_at_ms)
        self.assertEqual(gate["eligible_count"], 0)
        self.assertTrue(gate["all_reasons_include_unknown_listing_window"])
        snapshot = to_alpha_snapshot(loaded, formation_time_ms=loaded.known_at_ms)
        decisions = require_pit_eligibility(
            snapshot,
            formation_time_ms=loaded.known_at_ms,
            symbols=[row["symbol"] for row in loaded.memberships],
        )
        self.assertTrue(all(not decision.eligible for decision in decisions.values()))

    def test_filtered_or_noncanonical_exchange_url_is_rejected_before_fetch(self) -> None:
        fetcher = self.good_fetcher()
        with self.assertRaises(PITContractError):
            run_snapshot(
                snapshot_id="filtered",
                raw_root=self.raw_root,
                processed_root=self.processed_root,
                symbol_index_path=self.symbol_index,
                symbol_index_sha256=self.symbol_index_sha,
                summary_output=self.summary,
                schema_output=self.schema,
                gate_output=self.gate,
                exchange_info_url=EXCHANGE_INFO_URL + "&symbolStatus=TRADING",
                fetcher=fetcher,
            )
        self.assertEqual(fetcher.requests, [])

    def test_non_200_preserves_body_and_sidecar(self) -> None:
        fetcher = FakeFetcher([response(TIME_URL, {"code": -1}, status=418)])
        with self.assertRaises(PITHttpError):
            self.run_good(snapshot_id="http_failure", fetcher=fetcher)
        attempt_dir = self.raw_root / "http_failure" / "requests" / "time_before"
        self.assertTrue((attempt_dir / "attempt_0001.response").is_file())
        sidecar = json.loads((attempt_dir / "attempt_0001.request.json").read_text())
        self.assertEqual(sidecar["response"]["status"], 418)
        self.assertEqual(sidecar["outcome"], "HTTP_418")

    def test_default_fetcher_refuses_redirect_without_second_wire_request(self) -> None:
        original_url = TIME_URL
        cross_domain_location = "https://evil.example/steal"
        redirect_body = b'{"redirect":"preserved"}'
        redirect_headers = {
            "Content-Length": str(len(redirect_body)),
            "Content-Type": "application/json",
            "Location": cross_domain_location,
        }
        http_error = urllib.error.HTTPError(
            original_url,
            302,
            "Found",
            redirect_headers,
            io.BytesIO(redirect_body),
        )
        fake_opener = mock.Mock()
        fake_opener.open.side_effect = http_error
        with mock.patch(
            "quant_research.binance_spot_pit.urllib.request.build_opener",
            return_value=fake_opener,
        ) as build_opener:
            fetched = _default_fetcher(Request(original_url, method="GET"), 1.0)

        self.assertEqual(fake_opener.open.call_count, 1)
        self.assertEqual(fake_opener.open.call_args.args[0].full_url, original_url)
        self.assertNotIn(cross_domain_location, repr(fake_opener.open.call_args_list))
        self.assertEqual(fetched.status, 302)
        self.assertEqual(fetched.final_url, original_url)
        self.assertEqual(fetched.body, redirect_body)
        self.assertEqual(fetched.headers["Location"], cross_domain_location)
        handlers = build_opener.call_args.args
        self.assertEqual(len(handlers), 2)
        self.assertIsInstance(handlers[0], urllib.request.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], _NoRedirectHandler)
        self.assertIsNone(
            handlers[1].redirect_request(
                Request(original_url),
                io.BytesIO(redirect_body),
                302,
                "Found",
                redirect_headers,
                cross_domain_location,
            )
        )

    def test_redirect_attempt_is_preserved_with_location_and_never_retried(self) -> None:
        cross_domain_location = "https://evil.example/steal"
        redirect = response(
            TIME_URL,
            {"redirect": "preserved"},
            status=302,
            headers={"Location": cross_domain_location},
        )
        fetcher = FakeFetcher([redirect])
        with self.assertRaises(PITHttpError):
            self.run_good(snapshot_id="redirect", fetcher=fetcher)
        self.assertEqual(len(fetcher.requests), 1)
        self.assertEqual(fetcher.requests[0].full_url, TIME_URL)
        attempt_dir = self.raw_root / "redirect" / "requests" / "time_before"
        sidecar = json.loads(
            (attempt_dir / "attempt_0001.request.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sidecar["outcome"], "REDIRECT_REJECTED")
        self.assertEqual(sidecar["response"]["status"], 302)
        self.assertEqual(sidecar["response"]["final_url"], TIME_URL)
        self.assertEqual(
            sidecar["response"]["selected_headers"]["Location"],
            cross_domain_location,
        )
        self.assertEqual(
            (attempt_dir / "attempt_0001.response").read_bytes(), redirect.body
        )

    def test_transport_failure_preserves_request_receipt_without_raw_body(self) -> None:
        fetcher = FailingFetcher()
        with self.assertRaises(PITHttpError):
            self.run_good(snapshot_id="transport_failure", fetcher=fetcher)  # type: ignore[arg-type]
        attempt_dir = self.raw_root / "transport_failure" / "requests" / "time_before"
        self.assertFalse((attempt_dir / "attempt_0001.response").exists())
        sidecar = json.loads((attempt_dir / "attempt_0001.request.json").read_text())
        self.assertEqual(sidecar["outcome"], "TRANSPORT_ERROR")
        self.assertEqual(sidecar["request"]["authentication"], "NONE")
        self.assertIsNone(sidecar["response"])
        self.assertEqual(sidecar["error_category"], "OS_ERROR")
        self.assertEqual(sidecar["safe_error_message"], "public endpoint transport failed")
        receipt = (attempt_dir / "attempt_0001.request.json").read_text(encoding="utf-8")
        self.assertNotIn("proxy-user", receipt)
        self.assertNotIn("proxy-secret", receipt)

    def test_200_without_content_length_is_preserved_and_fails_closed(self) -> None:
        missing = response(TIME_URL, {"serverTime": BASE_MS})
        fetcher = FakeFetcher(
            [
                TransportResponse(
                    status=missing.status,
                    headers={"Content-Type": "application/json"},
                    body=missing.body,
                    final_url=missing.final_url,
                    request_started_at_utc=missing.request_started_at_utc,
                    response_completed_at_utc=missing.response_completed_at_utc,
                )
            ]
        )
        with self.assertRaises(PITHttpError):
            self.run_good(snapshot_id="missing_length", fetcher=fetcher)
        sidecar = json.loads(
            (
                self.raw_root
                / "missing_length"
                / "requests"
                / "time_before"
                / "attempt_0001.request.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(sidecar["outcome"], "MISSING_CONTENT_LENGTH")

    def test_429_is_preserved_before_bounded_retry(self) -> None:
        good = self.good_fetcher().responses
        fetcher = FakeFetcher(
            [response(TIME_URL, {"code": -1003}, status=429), *good]
        )
        summary, _selected = self.run_good(snapshot_id="retry", fetcher=fetcher)
        attempt_dir = self.raw_root / "retry" / "requests" / "time_before"
        self.assertEqual(len(list(attempt_dir.glob("*.response"))), 2)
        first = json.loads((attempt_dir / "attempt_0001.request.json").read_text())
        self.assertEqual(first["outcome"], "HTTP_429")
        ledger = summary["raw_requests"]["time_before"]["attempts"]
        self.assertEqual([item["attempt"] for item in ledger], [1, 2])
        self.assertEqual([item["outcome"] for item in ledger], ["HTTP_429", "OK"])
        Path(ledger[0]["body"]).write_bytes(b"tampered")
        with self.assertRaises(PITIntegrityError):
            load_snapshot(
                summary_output=self.summary,
                symbol_index_path=self.symbol_index,
                symbol_index_sha256=self.symbol_index_sha,
            )

    def test_invalid_json_preserves_all_acquired_evidence_and_fails(self) -> None:
        fetcher = FakeFetcher(
            [
                response(TIME_URL, {"serverTime": BASE_MS}),
                response(EXCHANGE_INFO_URL, b"{not-json", completed="2026-04-29T00:00:00.1Z"),
                response(TIME_URL, {"serverTime": BASE_MS + 200}, completed="2026-04-29T00:00:00.2Z"),
            ]
        )
        with self.assertRaises(PITSchemaError):
            self.run_good(snapshot_id="bad_json", fetcher=fetcher)
        raw = self.raw_root / "bad_json" / "requests" / "exchange_info" / "attempt_0001.response"
        self.assertEqual(raw.read_bytes(), b"{not-json")

    def test_duplicate_unknown_and_missing_fields_fail_closed(self) -> None:
        invalid_sets = [
            [symbol("BTCUSDT"), symbol("BTCUSDT")],
            [symbol("BTCUSDT", status="PENDING")],
            [{key: value for key, value in symbol("BTCUSDT").items() if key != "quoteAsset"}],
            [{key: value for key, value in symbol("BTCUSDT").items() if key != "permissions"}],
            [{key: value for key, value in symbol("BTCUSDT").items() if key != "permissionSets"}],
        ]
        for index, records in enumerate(invalid_sets):
            with self.subTest(index=index):
                case_root = self.root / f"case_{index}"
                with self.assertRaises(PITSchemaError):
                    run_snapshot(
                        snapshot_id=f"schema_{index}",
                        raw_root=case_root / "raw",
                        processed_root=case_root / "processed",
                        symbol_index_path=self.symbol_index,
                        symbol_index_sha256=self.symbol_index_sha,
                        summary_output=case_root / "summary.json",
                        schema_output=case_root / "schema.json",
                        gate_output=case_root / "gate.json",
                        fetcher=self.good_fetcher(records),
                        sleeper=lambda _delay: None,
                    )

    def test_clock_bracket_and_local_skew_fail_closed(self) -> None:
        fetcher = FakeFetcher(
            [
                response(TIME_URL, {"serverTime": BASE_MS + 300}),
                response(
                    EXCHANGE_INFO_URL,
                    {"serverTime": BASE_MS + 100, "symbols": [symbol("BTCUSDT")]},
                    completed="2026-04-29T00:00:00.1Z",
                ),
                response(TIME_URL, {"serverTime": BASE_MS + 200}, completed="2026-04-29T00:00:00.2Z"),
            ]
        )
        with self.assertRaises(PITClockError):
            self.run_good(snapshot_id="bad_clock", fetcher=fetcher)

    def test_local_request_response_sequence_fails_collector_and_loader(self) -> None:
        records = [symbol("BTCUSDT")]
        collector_fetcher = FakeFetcher(
            [
                response(TIME_URL, {"serverTime": BASE_MS}),
                response(
                    EXCHANGE_INFO_URL,
                    {"serverTime": BASE_MS + 100, "symbols": records},
                    started="2026-04-28T23:59:59Z",
                    completed="2026-04-29T00:00:00.100000Z",
                ),
                response(TIME_URL, {"serverTime": BASE_MS + 200}, completed="2026-04-29T00:00:00.2Z"),
            ]
        )
        with self.assertRaises(PITClockError):
            self.run_good(snapshot_id="bad_local_sequence", fetcher=collector_fetcher)

        summary, _fetcher = self.run_good()
        before_sidecar = Path(summary["raw_requests"]["time_before"]["sidecar"])
        sidecar = json.loads(before_sidecar.read_text(encoding="utf-8"))
        sidecar["response"]["response_completed_at_utc"] = "2026-04-29T00:00:00.150000Z"
        before_sidecar.write_bytes(
            (json.dumps(sidecar, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        summary_doc = json.loads(self.summary.read_text(encoding="utf-8"))
        summary_doc["raw_requests"]["time_before"]["attempts"][-1][
            "sidecar_sha256"
        ] = hashlib.sha256(before_sidecar.read_bytes()).hexdigest()
        self.summary.write_bytes(
            (json.dumps(summary_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        with self.assertRaises(PITClockError):
            load_snapshot(
                summary_output=self.summary,
                symbol_index_path=self.symbol_index,
                symbol_index_sha256=self.symbol_index_sha,
            )

    def test_loader_requires_content_length_on_every_success(self) -> None:
        summary, _fetcher = self.run_good()
        before_sidecar = Path(summary["raw_requests"]["time_before"]["sidecar"])
        sidecar = json.loads(before_sidecar.read_text(encoding="utf-8"))
        sidecar["response"]["selected_headers"].pop("Content-Length")
        before_sidecar.write_bytes(
            (json.dumps(sidecar, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        summary_doc = json.loads(self.summary.read_text(encoding="utf-8"))
        summary_doc["raw_requests"]["time_before"]["attempts"][-1][
            "sidecar_sha256"
        ] = hashlib.sha256(before_sidecar.read_bytes()).hexdigest()
        self.summary.write_bytes(
            (json.dumps(summary_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        with self.assertRaises(PITIntegrityError):
            load_snapshot(
                summary_output=self.summary,
                symbol_index_path=self.symbol_index,
                symbol_index_sha256=self.symbol_index_sha,
            )

    def test_symbol_index_hash_mismatch_stops_before_network(self) -> None:
        fetcher = self.good_fetcher()
        with self.assertRaises(PITIntegrityError):
            run_snapshot(
                snapshot_id="bad_index",
                raw_root=self.raw_root,
                processed_root=self.processed_root,
                symbol_index_path=self.symbol_index,
                symbol_index_sha256="0" * 64,
                summary_output=self.summary,
                schema_output=self.schema,
                gate_output=self.gate,
                fetcher=fetcher,
            )
        self.assertEqual(fetcher.requests, [])

    def test_loader_recomputes_raw_hash_and_rejects_tamper(self) -> None:
        summary, _fetcher = self.run_good()
        raw_path = Path(summary["raw_requests"]["exchange_info"]["body"])
        raw_path.write_bytes(raw_path.read_bytes() + b" ")
        with self.assertRaises(PITIntegrityError):
            load_snapshot(
                summary_output=self.summary,
                symbol_index_path=self.symbol_index,
                symbol_index_sha256=self.symbol_index_sha,
            )

    def test_loader_recomputes_artifact_hash_and_rejects_tamper(self) -> None:
        summary, _fetcher = self.run_good()
        membership_path = Path(summary["memberships_path"])
        membership_path.write_bytes(membership_path.read_bytes() + b" ")
        with self.assertRaises(PITIntegrityError):
            load_snapshot(
                summary_output=self.summary,
                symbol_index_path=self.symbol_index,
                symbol_index_sha256=self.symbol_index_sha,
            )

    def test_loader_rederives_row_hash_values_and_provenance(self) -> None:
        summary, _fetcher = self.run_good()
        membership_path = Path(summary["memberships_path"])
        rows = [
            json.loads(line)
            for line in membership_path.read_text(encoding="utf-8").splitlines()
        ]
        current = next(row for row in rows if row["current_response_observed"])
        current["raw_status"] = "BREAK"
        new_body = b"".join(stable_bytes(row) for row in rows)
        membership_path.write_bytes(new_body)
        summary_doc = json.loads(self.summary.read_text())
        summary_doc["memberships_sha256"] = hashlib.sha256(new_body).hexdigest()
        self.summary.write_bytes(
            (json.dumps(summary_doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
        )
        with self.assertRaises(PITIntegrityError):
            load_snapshot(
                summary_output=self.summary,
                symbol_index_path=self.symbol_index,
                symbol_index_sha256=self.symbol_index_sha,
            )

    def test_atomic_no_overwrite_stops_before_new_requests(self) -> None:
        self.run_good()
        second = self.good_fetcher()
        with self.assertRaises(PITExistingEvidenceError):
            run_snapshot(
                snapshot_id="unit",
                raw_root=self.raw_root,
                processed_root=self.processed_root,
                symbol_index_path=self.symbol_index,
                symbol_index_sha256=self.symbol_index_sha,
                summary_output=self.summary,
                schema_output=self.schema,
                gate_output=self.gate,
                fetcher=second,
            )
        self.assertEqual(second.requests, [])

    def test_exclusive_snapshot_lease_rejects_same_id_while_first_is_running(self) -> None:
        outer = self.good_fetcher()
        second = self.good_fetcher()
        probed = False

        def probing_fetcher(request: Request, timeout: float) -> TransportResponse:
            nonlocal probed
            if not probed:
                probed = True
                with self.assertRaises(PITExistingEvidenceError):
                    run_snapshot(
                        snapshot_id="leased",
                        raw_root=self.raw_root,
                        processed_root=self.processed_root,
                        symbol_index_path=self.symbol_index,
                        symbol_index_sha256=self.symbol_index_sha,
                        summary_output=self.summary,
                        schema_output=self.schema,
                        gate_output=self.gate,
                        fetcher=second,
                    )
            return outer(request, timeout)

        run_snapshot(
            snapshot_id="leased",
            raw_root=self.raw_root,
            processed_root=self.processed_root,
            symbol_index_path=self.symbol_index,
            symbol_index_sha256=self.symbol_index_sha,
            summary_output=self.summary,
            schema_output=self.schema,
            gate_output=self.gate,
            fetcher=probing_fetcher,
            sleeper=lambda _delay: None,
        )
        self.assertTrue(probed)
        self.assertEqual(second.requests, [])


if __name__ == "__main__":
    unittest.main()
