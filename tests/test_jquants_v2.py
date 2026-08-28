from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import numpy as np

from quant_research.alpha_models.data.jquants_v2 import adapters
from quant_research.alpha_models.data.jquants_v2.collector import (
    CollectorError,
    HTTPPage,
    _build_url,
    _validate_http_page,
    _validate_url,
    collect_and_publish,
    collect_pages,
    dry_plan_projection,
    main,
)
from quant_research.alpha_models.data.jquants_v2.contracts import (
    GLOBAL_HTTP_CAP,
    QUERY_PLANS,
    CalendarDay,
    ContractError,
    DailyBar,
    MasterRow,
    canonical_json_bytes,
    jst_known_at_ms,
)
from quant_research.alpha_models.data.jquants_v2.loader import (
    causal_adjust_bars,
    derive_listing_spells,
    load_acquired_run,
    master_at_formation,
    parse_page,
    validate_pre_service_floor_fixture,
)
from quant_research.alpha_models.sspt_v2.contracts import StableLabelRegistry
from quant_research.alpha_models.sspt_v2.data import TrainOnlyMinMax, TrainingFeaturePartition


RAW_SHA = "a" * 64
RECEIVED = 1_800_000_000_000


def _json(value: object) -> bytes:
    return canonical_json_bytes(value)


def _master(date_text: str, code: str, *, sector: str = "10", company: str | None = None, market: str = "0111") -> dict[str, object]:
    return {
        "Date": date_text,
        "Code": code,
        "CoName": company or f"Company {code}",
        "CoNameEn": company or f"Company {code}",
        "S17": sector,
        "S17Nm": f"Sector {sector}",
        "S33": f"{sector}0",
        "S33Nm": f"Sector33 {sector}",
        "ScaleCat": "TOPIX Core30",
        "Mkt": market,
        "MktNm": f"Market {market}",
        "Mrgn": "1",
        "MrgnNm": "Margin",
        "SecType": "1",
        "SecTypeNm": "Equity",
    }


def _bar(
    date_text: str,
    code: str,
    *,
    close: float | None = 100.0,
    factor: float = 1.0,
    volume: float | None = 1000.0,
) -> dict[str, object]:
    if close is None:
        return {
            "Date": date_text, "Code": code,
            "O": None, "H": None, "L": None, "C": None,
            "Vo": 0, "Va": 0, "AdjFactor": factor,
            "AdjO": None, "AdjH": None, "AdjL": None, "AdjC": None, "AdjVo": 0,
        }
    open_ = close - 1.0
    high = close + 1.0
    low = close - 2.0
    return {
        "Date": date_text, "Code": code,
        "O": open_, "H": high, "L": low, "C": close,
        "Vo": volume, "Va": 100000.0, "AdjFactor": factor,
        "AdjO": open_, "AdjH": high, "AdjL": low, "AdjC": close, "AdjVo": volume,
    }


class SyntheticTransport:
    def __init__(self, *, paginate_calendar: bool = False, secret: str = "SENTINEL_SECRET") -> None:
        self.paginate_calendar = paginate_calendar
        self.secret = secret
        self.calls: list[str] = []

    def __call__(self, url: str, api_key: str, cap_bytes: int) -> HTTPPage:
        self.assertions(api_key, cap_bytes)
        self.calls.append(url)
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, strict_parsing=True)
        if parsed.path == "/v2/markets/calendar":
            if self.paginate_calendar and "pagination_key" not in query:
                body = _json({"data": [{"Date": "2025-06-01", "HolDiv": "1"}], "pagination_key": "PAGE_TWO"})
            else:
                body = _json({"data": [{"Date": "2025-06-02", "HolDiv": "0"}]})
            return HTTPPage(200, "application/json; charset=utf-8", body, url)
        if parsed.path == "/v2/equities/master" and query.get("date") == ["2025-06-01"]:
            return HTTPPage(400, "application/json", _json({"message": "non-session rejected"}), url)
        if parsed.path == "/v2/equities/master":
            return HTTPPage(200, "application/json", _json({"data": [_master("2025-06-02", "65010"), _master("2025-06-02", "72030", sector="20")]}), url)
        if query.get("date") == ["2025-06-02"]:
            return HTTPPage(200, "application/json", _json({"data": [_bar("2025-06-02", "72030")]}), url)
        return HTTPPage(200, "application/json", _json({"data": [_bar("2024-07-01", "65010")]}), url)

    def assertions(self, api_key: str, cap_bytes: int) -> None:
        if api_key != self.secret or type(cap_bytes) is not int or cap_bytes <= 0:
            raise AssertionError("transport contract")


def _clock() -> int:
    _clock.value += 1
    return _clock.value


_clock.value = RECEIVED


def _calendar_days(count: int = 52) -> tuple[CalendarDay, ...]:
    current = date(2025, 3, 3)
    rows: list[CalendarDay] = []
    while len(rows) < count:
        if current.weekday() < 5:
            rows.append(CalendarDay(current.isoformat(), "0", RECEIVED, RAW_SHA))
        current += timedelta(days=1)
    return tuple(rows)


def _master_object(date_text: str, code: str, *, sector: str = "10", company: str | None = None, market: str = "0111") -> MasterRow:
    plan = QUERY_PLANS[1]
    local = replace(plan, parameters={"date": date_text})
    page = parse_page(local, page_number=1, status=200, body=_json({"data": [_master(date_text, code, sector=sector, company=company, market=market)]}), received_at_ms=RECEIVED)
    return page.records[0]  # type: ignore[return-value]


def _bar_object(date_text: str, code: str, *, close: float = 100.0, factor: float = 1.0) -> DailyBar:
    plan = replace(QUERY_PLANS[3], parameters={"date": date_text})
    page = parse_page(plan, page_number=1, status=200, body=_json({"data": [_bar(date_text, code, close=close, factor=factor)]}), received_at_ms=RECEIVED)
    return page.records[0]  # type: ignore[return-value]


class JQuantsContractsTests(unittest.TestCase):
    def test_query_plan_is_exact_five_and_dry_plan_has_zero_network(self) -> None:
        self.assertEqual(len(QUERY_PLANS), 5)
        self.assertEqual([plan.ordinal for plan in QUERY_PLANS], [1, 2, 3, 4, 5])
        self.assertEqual(dry_plan_projection()["network_request_count"], 0)
        self.assertEqual(dry_plan_projection()["global_http_cap"], 60)
        self.assertTrue(all(plan.max_pages == 25 for plan in QUERY_PLANS))

    def test_url_host_path_query_and_redirect_are_fail_closed(self) -> None:
        plan = QUERY_PLANS[0]
        valid = _build_url(plan)
        _validate_url(plan, valid, None)
        for bad in (
            valid.replace("https://", "http://"),
            valid.replace("api.jquants.com", "evil.example"),
            valid.replace(plan.path, "/v2/other"),
            valid + "&extra=1",
        ):
            with self.assertRaises(CollectorError):
                _validate_url(plan, bad, None)
        with self.assertRaisesRegex(CollectorError, "REDIRECT"):
            _validate_http_page(plan, valid, HTTPPage(302, "application/json", b"{}", "https://evil.example", 1))

    def test_pagination_is_prior_key_only_and_loop_or_caps_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)
            transport = SyntheticTransport(paginate_calendar=True)
            pages, receipts, _ = collect_pages(api_key=transport.secret, transport=transport, clock=_clock, staging=stage)
            self.assertEqual(len(pages), 6)
            self.assertEqual(len(receipts), 6)
            self.assertIn("pagination_key=PAGE_TWO", transport.calls[1])
        class LoopTransport(SyntheticTransport):
            def __call__(self, url: str, api_key: str, cap_bytes: int) -> HTTPPage:
                parsed = urlsplit(url)
                if parsed.path == "/v2/markets/calendar":
                    return HTTPPage(200, "application/json", _json({"data": [], "pagination_key": "LOOP"}), url)
                return super().__call__(url, api_key, cap_bytes)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(CollectorError, "PAGINATION_LOOP"):
                collect_pages(api_key="SENTINEL_SECRET", transport=LoopTransport(), clock=_clock, staging=Path(temp))
        with tempfile.TemporaryDirectory() as temp, patch("quant_research.alpha_models.data.jquants_v2.collector.GLOBAL_HTTP_CAP", 5):
            with self.assertRaisesRegex(CollectorError, "HTTP_REQUEST_CAP"):
                collect_pages(api_key="SENTINEL_SECRET", transport=SyntheticTransport(paginate_calendar=True), clock=_clock, staging=Path(temp))

    def test_strict_json_duplicate_nonfinite_unknown_and_dates(self) -> None:
        plan = QUERY_PLANS[0]
        for body in (
            b'{"data":[],"data":[]}',
            b'{"data":[{"Date":"2025-06-02","HolDiv":NaN}]}',
            _json({"data": [{"Date": "2025-06-02", "HolDiv": "0", "Extra": 1}]}),
            _json({"data": [{"Date": "2025-02-30", "HolDiv": "0"}]}),
        ):
            with self.assertRaises(ContractError):
                parse_page(plan, page_number=1, status=200, body=body, received_at_ms=RECEIVED)

    def test_duplicate_rows_fail_when_pages_are_merged(self) -> None:
        plan = replace(QUERY_PLANS[3], parameters={"date": "2025-06-02"})
        page = parse_page(plan, page_number=1, status=200, body=_json({"data": [_bar("2025-06-02", "65010")]}), received_at_ms=RECEIVED)
        from quant_research.alpha_models.data.jquants_v2.loader import merge_pages
        with self.assertRaisesRegex(ContractError, "DUPLICATE_BAR"):
            merge_pages((page, replace(page, page_number=2)))

    def test_rest_day_and_pre_floor_expected_rejections(self) -> None:
        weekend = parse_page(
            QUERY_PLANS[2], page_number=1, status=400,
            body=_json({"message": "weekend"}), received_at_ms=RECEIVED,
        )
        self.assertTrue(weekend.expected_rejection)
        validate_pre_service_floor_fixture(requested_date="2024-06-30", status=400, body=_json({"message": "outside service floor"}))
        with self.assertRaises(ContractError):
            validate_pre_service_floor_fixture(requested_date="2024-07-01", status=400, body=_json({"message": "bad fixture"}))

    def test_null_zero_and_adjusted_field_relationships(self) -> None:
        plan = replace(QUERY_PLANS[3], parameters={"date": "2025-06-02"})
        parsed = parse_page(plan, page_number=1, status=200, body=_json({"data": [_bar("2025-06-02", "65010", close=None)]}), received_at_ms=RECEIVED)
        self.assertFalse(parsed.records[0].traded)  # type: ignore[union-attr]
        bad = _bar("2025-06-02", "65010")
        bad["AdjC"] = 100.5
        with self.assertRaisesRegex(ContractError, "ADJUSTED_PRICE_RATIO"):
            parse_page(plan, page_number=1, status=200, body=_json({"data": [bad]}), received_at_ms=RECEIVED)
        zero_price = _bar("2025-06-02", "65010")
        zero_price["C"] = 0
        with self.assertRaises(ContractError):
            parse_page(plan, page_number=1, status=200, body=_json({"data": [zero_price]}), received_at_ms=RECEIVED)

    def test_causal_split_factor_does_not_backfill_before_known_at(self) -> None:
        first = _bar_object("2025-06-02", "65010", close=100.0)
        second = _bar_object("2025-06-03", "65010", close=50.0, factor=0.5)
        before = causal_adjust_bars((first, second), formation_time_ms=first.known_at_ms)
        after = causal_adjust_bars((first, second), formation_time_ms=second.known_at_ms)
        self.assertEqual(before[0].adjusted_close, 100.0)
        self.assertEqual(after[0].adjusted_close, 50.0)
        self.assertEqual(after[0].adjusted_volume, 2000.0)

    def test_listing_spells_ticker_reuse_and_metadata_change(self) -> None:
        rows = (
            _master_object("2025-06-02", "65010", sector="10", market="0111"),
            _master_object("2025-06-02", "72030", sector="20"),
            _master_object("2025-06-03", "72030", sector="30", market="0112"),
            _master_object("2025-06-04", "65010", sector="10", company="Reused 6501"),
            _master_object("2025-06-04", "72030", sector="30", market="0112"),
        )
        spells = derive_listing_spells(rows)
        self.assertEqual(len([item for item in spells if item.symbol == "6501"]), 2)
        self.assertEqual(len([item for item in spells if item.symbol == "7203"]), 1)
        formation = jst_known_at_ms("2025-06-03", 16, 30)
        changed = master_at_formation(rows, symbol="7203", formation_date="2025-06-03", formation_time_ms=formation)
        self.assertEqual((changed.sector17_code, changed.market_code), ("30", "0112"))


class JQuantsCollectorTests(unittest.TestCase):
    def test_dry_plan_does_not_read_or_remove_key_environment(self) -> None:
        sentinel = "DO_NOT_READ_ME"
        with patch.dict(os.environ, {"JQUANTS_API_KEY": sentinel}, clear=False), patch("sys.stdout"):
            self.assertEqual(main(["--dry-plan"]), 0)
            self.assertEqual(os.environ.get("JQUANTS_API_KEY"), sentinel)

    def test_secret_redaction_atomic_publish_and_loader_tamper(self) -> None:
        secret = "SENTINEL_SECRET_123"
        with tempfile.TemporaryDirectory() as temp:
            final = Path(temp) / "run"
            summary = collect_and_publish(run_root=final, api_key=secret, transport=SyntheticTransport(secret=secret), clock=_clock)
            self.assertEqual(summary["http_request_count"], 5)
            loaded = load_acquired_run(final)
            self.assertEqual(loaded.page_count, 5)
            for file in Path(temp).rglob("*"):
                if file.is_file():
                    self.assertNotIn(secret.encode("utf-8"), file.read_bytes())
            response = next((final / "responses").iterdir())
            response.write_bytes(response.read_bytes() + b" ")
            with self.assertRaisesRegex(ContractError, "RAW_TAMPER"):
                load_acquired_run(final)

    def test_raw_tree_extra_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            final = Path(temp) / "run"
            collect_and_publish(run_root=final, api_key="SENTINEL_SECRET", transport=SyntheticTransport(), clock=_clock)
            (final / "responses" / "extra.json").write_bytes(b"{}")
            with self.assertRaisesRegex(ContractError, "RAW_BIJECTION"):
                load_acquired_run(final)

    def test_transport_exception_secret_is_reduced_to_code_and_not_persisted(self) -> None:
        secret = "SENTINEL_EXCEPTION_SECRET"
        def bad_transport(url: str, api_key: str, cap_bytes: int) -> HTTPPage:
            raise RuntimeError(f"transport failed with {api_key}")
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(CollectorError, "INTERNAL"):
                collect_and_publish(run_root=Path(temp) / "run", api_key=secret, transport=bad_transport, clock=_clock)
            for file in Path(temp).rglob("*"):
                if file.is_file():
                    self.assertNotIn(secret.encode("utf-8"), file.read_bytes())

    def test_preexistence_is_zero_new_run_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            final = Path(temp) / "run"
            final.mkdir()
            before = {path.name for path in Path(temp).iterdir()}
            with self.assertRaisesRegex(CollectorError, "PREEXISTENCE"):
                collect_and_publish(run_root=final, api_key="SENTINEL_SECRET", transport=SyntheticTransport(), clock=_clock)
            self.assertEqual({path.name for path in Path(temp).iterdir()}, before)

    def test_content_type_size_and_transport_exception_are_sanitized(self) -> None:
        plan = QUERY_PLANS[0]
        url = _build_url(plan)
        with self.assertRaisesRegex(CollectorError, "CONTENT_TYPE"):
            _validate_http_page(plan, url, HTTPPage(200, "text/html", b"{}", url))
        with self.assertRaisesRegex(CollectorError, "RESPONSE_TOO_LARGE"):
            _validate_http_page(plan, url, HTTPPage(200, "application/json", b"x" * (plan.response_cap_bytes + 1), url))


class JQuantsAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.calendar = _calendar_days()
        cls.session_dates = tuple(item.session_date for item in cls.calendar)
        cls.formation_date = cls.session_dates[44]
        cls.symbols = ("6501", "7203")
        cls.masters = (
            _master_object(cls.formation_date, "65010", sector="10"),
            _master_object(cls.formation_date, "72030", sector="20"),
        )
        bars: list[DailyBar] = []
        for row_index, day in enumerate(cls.session_dates):
            bars.append(_bar_object(day, "65010", close=100.0 + row_index))
            bars.append(_bar_object(day, "72030", close=200.0 + row_index))
        cls.bars = tuple(bars)

    def _scaler(self) -> TrainOnlyMinMax:
        formation_ms = jst_known_at_ms(self.formation_date, 16, 30)
        next_ms = jst_known_at_ms(self.session_dates[45], 16, 30)
        partition = TrainingFeaturePartition(
            kind="TRAIN",
            features=np.zeros((1, 2, 16, 25), dtype=np.float64),
            formation_times_ms=(formation_ms,),
            label_end_times_ms=(next_ms,),
            train_end_exclusive_ms=next_ms + 1,
            data_provenance_sha256="c" * 64,
        )
        return TrainOnlyMinMax().fit(partition)

    def _registries(self) -> tuple[StableLabelRegistry, StableLabelRegistry]:
        known = jst_known_at_ms(self.formation_date, 8, 0)
        scc = StableLabelRegistry.from_labels("SCC", self.symbols, authority_id="JQUANTS_SYNTHETIC", training_partition_id="TRAIN", known_at_ms=known)
        ssc = StableLabelRegistry.from_labels("SSC", ("10", "20"), authority_id="JQUANTS_SYNTHETIC", training_partition_id="TRAIN", known_at_ms=known)
        return scc, ssc

    def test_sspt_exact_master_sector_calendar_and_no_same_close(self) -> None:
        scc, ssc = self._registries()
        result = adapters.build_sspt_training_adapter(
            bars=self.bars,
            master_rows=self.masters,
            calendar_days=self.calendar,
            symbols=self.symbols,
            formation_date=self.formation_date,
            lookback=16,
            scaler=self._scaler(),
            scc_registry=scc,
            ssc_registry=ssc,
        )
        self.assertEqual(result.batch.symbols, self.symbols)
        self.assertEqual(tuple(result.batch.ssc_targets), (0, 1))
        self.assertFalse(result.same_close_execution_allowed)
        self.assertEqual(result.earliest_execution_session_id, self.session_dates[45])

    def test_tips_q5_calendar_path_and_purge(self) -> None:
        result = adapters.build_tips_training_adapter(
            bars=self.bars,
            master_rows=self.masters,
            calendar_days=self.calendar,
            symbols=self.symbols,
            formation_date=self.formation_date,
            partition_id="TRAIN",
            partition_session_ids=self.session_dates,
        )
        self.assertEqual(result.batch.label_path_session_ids, self.session_dates[44:49])
        self.assertFalse(result.same_close_execution_allowed)
        with self.assertRaises(ValueError):
            adapters.build_tips_training_adapter(
                bars=self.bars,
                master_rows=self.masters,
                calendar_days=self.calendar,
                symbols=self.symbols,
                formation_date=self.formation_date,
                partition_id="TRAIN",
                partition_session_ids=self.session_dates[:-4],
            )

    def test_official_calendar_gap_null_bar_and_rest_day_fail_closed(self) -> None:
        missing = tuple(row for row in self.bars if not (row.symbol == "6501" and row.session_date == self.session_dates[20]))
        with self.assertRaisesRegex(ContractError, "MISSING_OR_NULL_BAR"):
            adapters.build_tips_inference_adapter(
                bars=missing, master_rows=self.masters, calendar_days=self.calendar,
                symbols=self.symbols, formation_date=self.formation_date, partition_id="TRAIN",
            )
        rest_calendar = tuple(replace(row, holiday_division="1") if row.session_date == self.formation_date else row for row in self.calendar)
        with self.assertRaises(ContractError):
            adapters.build_tips_inference_adapter(
                bars=self.bars, master_rows=self.masters, calendar_days=rest_calendar,
                symbols=self.symbols, formation_date=self.formation_date, partition_id="TRAIN",
            )

    def test_future_split_on_label_path_is_not_leaked(self) -> None:
        changed = tuple(
            replace(row, adjustment_factor=0.5)
            if row.symbol == "6501" and row.session_date == self.session_dates[46]
            else row
            for row in self.bars
        )
        with self.assertRaisesRegex(ContractError, "CORPORATE_ACTION_IN_LABEL_PATH"):
            adapters.build_tips_training_adapter(
                bars=changed, master_rows=self.masters, calendar_days=self.calendar,
                symbols=self.symbols, formation_date=self.formation_date,
                partition_id="TRAIN", partition_session_ids=self.session_dates,
            )


if __name__ == "__main__":
    unittest.main()
