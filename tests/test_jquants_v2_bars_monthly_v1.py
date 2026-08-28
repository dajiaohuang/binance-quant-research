from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.catalog import (
    ReuseLeaf,
    build_monthly_attempt,
    oldest_missing_month,
    publish_catalog_entry,
    read_catalog,
    require_monthly_network_authorization,
    reserve_month_attempt,
    validate_repair_attempt,
)
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.collector import (
    MonotonicPacer,
    collect_bootstrap,
    dry_plan,
    publish_bootstrap,
    reserve_attempt,
)
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.contracts import (
    BAR_FIELDS,
    BOOTSTRAP_CIVIL_DATE_COUNT,
    BOOTSTRAP_FROM,
    BOOTSTRAP_GLOBAL_HTTP_CAP,
    BOOTSTRAP_MONTH_COUNT,
    BOOTSTRAP_PLAN_SHA256,
    BOOTSTRAP_QUERY_PLANS,
    BOOTSTRAP_RUN_ID,
    BOOTSTRAP_TO,
    EXP005_Q04_RAW_SHA256,
    MIN_SEND_SPACING_NS,
    ContractError,
    MonthPlan,
    QueryPlan,
    canonical_json_bytes,
    inclusive_dates,
    sha256_bytes,
)
from quant_research.alpha_models.data.jquants_v2_bars_monthly_v1.loader import (
    build_month_plans,
    load_bootstrap_tree,
    merge_bootstrap,
    parse_page,
    replay_monotonic_receipts,
    validate_rolling_five_per_minute,
    verify_reuse_source,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/quant_research/alpha_models/data/jquants_v2_bars_monthly_v1"


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def calendar_rows(session_count: int = 460) -> list[dict[str, str]]:
    dates = list(inclusive_dates(BOOTSTRAP_FROM, BOOTSTRAP_TO))
    mandatory = {dates[0], dates[-1]}
    mandatory.update({item for index, item in enumerate(dates) if index == 0 or item[:7] != dates[index - 1][:7]})
    selected = set(mandatory)
    for item in dates:
        if len(selected) == session_count:
            break
        selected.add(item)
    return [{"Date": item, "HolDiv": "1" if item in selected else "0"} for item in dates]


def bar(code: str = "13010", day: str = BOOTSTRAP_FROM, *, null: bool = False) -> dict[str, object]:
    if null:
        prices = {"O": None, "H": None, "L": None, "C": None, "AdjO": None, "AdjH": None, "AdjL": None, "AdjC": None}
        flows = {"Vo": None, "Va": None, "AdjVo": None, "MktCap": None}
    else:
        prices = {"O": 100.0, "H": 110.0, "L": 90.0, "C": 105.0, "AdjO": 100.0, "AdjH": 110.0, "AdjL": 90.0, "AdjC": 105.0}
        flows = {"Vo": 1000.0, "Va": 100000.0, "AdjVo": 1000.0, "MktCap": 500000.0}
    return {
        "Date": day,
        "Code": code,
        **prices,
        "UL": "0",
        "LL": "0",
        **flows,
        "AdjFactor": 1.0,
        "ExRT": None,
    }


class FakeResponse:
    def __init__(self, status: int, body: bytes, content_type: str = "application/json", location: str | None = None) -> None:
        self.status = status
        self.body = body
        self.offset = 0
        self.headers = {"Content-Type": content_type}
        if location is not None:
            self.headers["Location"] = location

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)

    def read(self, amount: int | None = None) -> bytes:
        if self.offset >= len(self.body):
            return b""
        end = len(self.body) if amount is None else min(len(self.body), self.offset + amount)
        value = self.body[self.offset:end]
        self.offset = end
        return value


class FakeTransport:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def request(self, host: str, path_and_query: str, headers: object) -> FakeResponse:
        self.calls.append((host, path_and_query, dict(headers)))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0
        self.sleeps: list[float] = []
        self.wall = 2_000_000_000_000_000_000

    def monotonic_ns(self) -> int:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += round(seconds * 1_000_000_000)

    def utc_ns(self) -> int:
        self.wall += 1_000_000
        return self.wall


def valid_responses() -> list[FakeResponse]:
    return [
        FakeResponse(200, json_bytes({"data": calendar_rows()})),
        FakeResponse(200, json_bytes({"data": [bar("13010"), bar("13320")]})),
        FakeResponse(200, json_bytes({"data": [bar("13010", BOOTSTRAP_TO), bar("13320", BOOTSTRAP_TO)]})),
    ]


def collected_tree(root: Path) -> Path:
    staging = root / f".{BOOTSTRAP_RUN_ID}.staging"
    staging.mkdir(parents=True)
    clock = FakeClock()
    collect_bootstrap(staging, "test-key", transport=FakeTransport(valid_responses()), monotonic_ns=clock.monotonic_ns, sleep_seconds=clock.sleep, utc_ns=clock.utc_ns)
    return staging


class CalendarAndSchemaTests(unittest.TestCase):
    def test_civil_coverage_is_exact_698(self) -> None:
        self.assertEqual(BOOTSTRAP_CIVIL_DATE_COUNT, len(inclusive_dates(BOOTSTRAP_FROM, BOOTSTRAP_TO)))

    def test_calendar_holiday_enum_is_strict(self) -> None:
        rows = calendar_rows()
        rows[10]["HolDiv"] = "4"
        with self.assertRaisesRegex(ContractError, "HOLDIV"):
            parse_page(BOOTSTRAP_QUERY_PLANS[0], 1, 0, json_bytes({"data": rows}))

    def test_session_count_band_is_enforced(self) -> None:
        parsed = parse_page(BOOTSTRAP_QUERY_PLANS[0], 1, 0, json_bytes({"data": calendar_rows(100)}))
        first = parse_page(BOOTSTRAP_QUERY_PLANS[1], 1, 0, json_bytes({"data": [bar()]}))
        last = parse_page(BOOTSTRAP_QUERY_PLANS[2], 1, 0, json_bytes({"data": [bar(day=BOOTSTRAP_TO)]}))
        with self.assertRaisesRegex(ContractError, "SESSION_COUNT"):
            merge_bootstrap((parsed, first, last), _synthetic_receipts())

    def test_month_split_is_immutable_23(self) -> None:
        sessions = tuple(row["Date"] for row in calendar_rows() if row["HolDiv"] == "1")
        plans = build_month_plans(sessions)
        self.assertEqual(BOOTSTRAP_MONTH_COUNT, len(plans))
        self.assertEqual(sessions, tuple(day for plan in plans for day in plan.session_dates))
        self.assertTrue(all(plan.bootstrap_plan_sha256 == BOOTSTRAP_PLAN_SHA256 for plan in plans))

    def test_exact_free18_schema_accepts(self) -> None:
        self.assertEqual(18, len(BAR_FIELDS))
        parsed = parse_page(BOOTSTRAP_QUERY_PLANS[1], 1, 0, json_bytes({"data": [bar()]}))
        self.assertEqual(1, len(parsed.bars))

    def test_extra_premium_field_rejected(self) -> None:
        row = bar()
        row["MO"] = 1.0
        with self.assertRaisesRegex(ContractError, "FREE18_SCHEMA"):
            parse_page(BOOTSTRAP_QUERY_PLANS[1], 1, 0, json_bytes({"data": [row]}))

    def test_coherent_null_bar_accepts(self) -> None:
        parsed = parse_page(BOOTSTRAP_QUERY_PLANS[1], 1, 0, json_bytes({"data": [bar(null=True)]}))
        self.assertFalse(parsed.bars[0].traded)

    def test_incoherent_null_bar_rejected(self) -> None:
        row = bar(null=True)
        row["Vo"] = 1.0
        with self.assertRaisesRegex(ContractError, "NULL_BAR"):
            parse_page(BOOTSTRAP_QUERY_PLANS[1], 1, 0, json_bytes({"data": [row]}))

    def test_boundary_date_is_exact(self) -> None:
        with self.assertRaisesRegex(ContractError, "BOUNDARY_DATE"):
            parse_page(BOOTSTRAP_QUERY_PLANS[1], 1, 0, json_bytes({"data": [bar(day="2024-07-02")]}))

    def test_bars_plan_is_date_only(self) -> None:
        with self.assertRaisesRegex(ContractError, "ALL_MARKET_DATE_ONLY"):
            QueryPlan(4, "BAD", "/v2/equities/bars/daily", {"date": BOOTSTRAP_FROM, "code": "1301"}, "BAD", 10)


def _synthetic_receipts(count: int = 3) -> list[dict[str, object]]:
    output = []
    previous = None
    for index in range(count):
        plan = BOOTSTRAP_QUERY_PLANS[index]
        sent = (index + 1) * MIN_SEND_SPACING_NS
        output.append({
            "api_host": "api.jquants.com", "body_bytes": 1, "body_sha256": "0" * 64,
            "cap_bytes": plan.cap_bytes, "clock_domain_id": "python-monotonic-test0001", "content_type": "application/json",
            "deadline_monotonic_ns": sent, "guard_base_monotonic_ns": 0, "page_number": 1,
            "parameters": dict(plan.parameters), "path": plan.path, "post_wait_monotonic_ns": sent,
            "pre_wait_monotonic_ns": 0 if index == 0 else previous, "previous_send_monotonic_ns": previous,
            "query_id": plan.query_id, "query_ordinal": plan.ordinal,
            "raw_relative_path": f"responses/{plan.ordinal:02d}_{plan.query_id}_page_0001.json",
            "receipt_relative_path": f"response_receipts/{index + 1:04d}_{plan.ordinal:02d}_{plan.query_id}_page_0001.receipt.json",
            "received_at_ms": 0, "received_at_utc": "1970-01-01T00:00:00Z", "redirected": False,
            "request_ordinal": index + 1, "requested_wait_ns": MIN_SEND_SPACING_NS,
            "run_id": BOOTSTRAP_RUN_ID, "schema_version": "JQUANTS_V2_BARS_MONTHLY_RECEIPT_V1",
            "send_monotonic_ns": sent, "sent_at_utc": "1970-01-01T00:00:00Z",
            "spacing_ns": MIN_SEND_SPACING_NS, "status": 200,
        })
        previous = sent
    return output


class PacingTests(unittest.TestCase):
    def test_first_request_waits_full_15_seconds(self) -> None:
        clock = FakeClock()
        evidence = MonotonicPacer(BOOTSTRAP_RUN_ID, 0, "python-monotonic-test0001", clock.monotonic_ns, clock.sleep).wait_to_send()
        self.assertEqual(MIN_SEND_SPACING_NS, evidence["requested_wait_ns"])
        self.assertEqual([15.0], clock.sleeps)

    def test_one_nanosecond_short_is_rejected(self) -> None:
        receipts = _synthetic_receipts(3)
        receipts[1]["post_wait_monotonic_ns"] = 2 * MIN_SEND_SPACING_NS - 1
        receipts[1]["send_monotonic_ns"] = 2 * MIN_SEND_SPACING_NS - 1
        with self.assertRaisesRegex(ContractError, "SPACING_SHORT"):
            replay_monotonic_receipts(receipts)

    def test_replay_uses_integer_monotonic_evidence(self) -> None:
        result = replay_monotonic_receipts(_synthetic_receipts(3))
        self.assertEqual("PASS", result["verdict"])
        self.assertTrue(result["first_request_full_cooldown"])

    def test_rolling_five_per_minute_allows_five(self) -> None:
        validate_rolling_five_per_minute((0, 10_000_000_000, 20_000_000_000, 30_000_000_000, 40_000_000_000))

    def test_rolling_five_per_minute_rejects_six(self) -> None:
        with self.assertRaisesRegex(ContractError, "ROLLING_FIVE_PER_MINUTE"):
            validate_rolling_five_per_minute((0, 10_000_000_000, 20_000_000_000, 30_000_000_000, 40_000_000_000, 50_000_000_000))


class AcquisitionTests(unittest.TestCase):
    def test_success_raw_first_and_reparse(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            staging = collected_tree(root)
            bundle = load_bootstrap_tree(staging)
            self.assertEqual(460, len(bundle.session_dates))
            self.assertEqual(23, len(bundle.month_plans))
            self.assertEqual(3, len(bundle.receipts))
            self.assertTrue((staging / "edge_manifests/first.json").is_file())
            self.assertTrue((staging / "edge_manifests/last.json").is_file())
            self.assertEqual(23, len(list((staging / "monthly_plans").glob("*.json"))))

    def test_http_failure_is_persisted_and_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = Path(temp)
            transport = FakeTransport([FakeResponse(500, b'{"error":"limited"}')])
            clock = FakeClock()
            with self.assertRaisesRegex(ContractError, "HTTP_500"):
                collect_bootstrap(staging, "test-key", transport=transport, monotonic_ns=clock.monotonic_ns, sleep_seconds=clock.sleep, utc_ns=clock.utc_ns)
            self.assertEqual(1, len(transport.calls))
            self.assertEqual(b'{"error":"limited"}', next((staging / "responses").glob("*.json")).read_bytes())
            self.assertEqual(1, len(list((staging / "response_receipts").glob("*.json"))))
            self.assertNotIn("test-key", next((staging / "response_receipts").glob("*.json")).read_text(encoding="utf-8"))

    def test_invalid_json_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = Path(temp)
            transport = FakeTransport([FakeResponse(200, b"not-json")])
            clock = FakeClock()
            with self.assertRaisesRegex(ContractError, "JSON_INVALID"):
                collect_bootstrap(staging, "test-key", transport=transport, monotonic_ns=clock.monotonic_ns, sleep_seconds=clock.sleep, utc_ns=clock.utc_ns)
            self.assertEqual(b"not-json", next((staging / "responses").glob("*.json")).read_bytes())

    def test_redirect_is_rejected_without_follow(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            transport = FakeTransport([FakeResponse(302, b"{}", location="https://other.invalid/")])
            clock = FakeClock()
            with self.assertRaisesRegex(ContractError, "REDIRECT"):
                collect_bootstrap(Path(temp), "test-key", transport=transport, monotonic_ns=clock.monotonic_ns, sleep_seconds=clock.sleep, utc_ns=clock.utc_ns)
            self.assertEqual(1, len(transport.calls))

    def test_pagination_uses_only_prior_key(self) -> None:
        rows = calendar_rows()
        responses = [
            FakeResponse(200, json_bytes({"data": rows[:350], "pagination_key": "next_1"})),
            FakeResponse(200, json_bytes({"data": rows[350:]})),
            *valid_responses()[1:],
        ]
        with tempfile.TemporaryDirectory() as temp:
            clock = FakeClock(); transport = FakeTransport(responses)
            collect_bootstrap(Path(temp), "test-key", transport=transport, monotonic_ns=clock.monotonic_ns, sleep_seconds=clock.sleep, utc_ns=clock.utc_ns)
            self.assertIn("pagination_key=next_1", transport.calls[1][1])
            self.assertNotIn("pagination_key", transport.calls[0][1])

    def test_pagination_page_cap_stops_without_retry(self) -> None:
        responses = [FakeResponse(200, json_bytes({"data": [], "pagination_key": f"next_{index}"})) for index in range(8)]
        with tempfile.TemporaryDirectory() as temp:
            clock = FakeClock(); transport = FakeTransport(responses)
            with self.assertRaisesRegex(ContractError, "PAGE_CAP"):
                collect_bootstrap(Path(temp), "test-key", transport=transport, monotonic_ns=clock.monotonic_ns, sleep_seconds=clock.sleep, utc_ns=clock.utc_ns)
            self.assertEqual(8, len(transport.calls))
            self.assertLessEqual(len(transport.calls), BOOTSTRAP_GLOBAL_HTTP_CAP)

    def test_tampered_raw_fails_reparse(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = collected_tree(Path(temp))
            raw = next((staging / "responses").glob("*.json"))
            raw.write_bytes(raw.read_bytes() + b" ")
            with self.assertRaisesRegex(ContractError, "RAW_HASH|ACQUISITION"):
                load_bootstrap_tree(staging)

    def test_receipt_path_traversal_fails_reparse(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = collected_tree(Path(temp))
            receipt = next((staging / "response_receipts").glob("*.json"))
            value = json.loads(receipt.read_text(encoding="utf-8"))
            value["raw_relative_path"] = "../escape.json"
            receipt.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "RECEIPT_PATH_BINDING"):
                load_bootstrap_tree(staging)

    def test_reparse_rejects_symlink_receipt_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = collected_tree(Path(temp))
            receipt = next((staging / "response_receipts").glob("*.json"))
            target = staging / "copy.json"
            target.write_bytes(receipt.read_bytes())
            receipt.unlink()
            try:
                receipt.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaises(ContractError):
                load_bootstrap_tree(staging)

    def test_exact_once_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            reserve_attempt(Path(temp), BOOTSTRAP_RUN_ID)
            with self.assertRaisesRegex(ContractError, "ATTEMPT_EXISTS"):
                reserve_attempt(Path(temp), BOOTSTRAP_RUN_ID)

    def test_publish_is_no_clobber(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); staging = collected_tree(root); final = root / BOOTSTRAP_RUN_ID
            final.mkdir()
            with self.assertRaisesRegex(ContractError, "PUBLISH_NO_CLOBBER"):
                publish_bootstrap(staging, final)


class ResumeCatalogAndSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        sessions = ("2025-03-03", "2025-03-28", "2025-03-31")
        session_sha = sha256_bytes(canonical_json_bytes(list(sessions)))
        self.month_plan = MonthPlan("2025-03", sessions, BOOTSTRAP_PLAN_SHA256, session_sha)

    def test_reuse_leaf_removes_network_date(self) -> None:
        leaf = ReuseLeaf("2025-03-28", "EXP006_SOURCE_BOUND", "data/raw/source.json", EXP005_Q04_RAW_SHA256)
        plan = build_monthly_attempt(self.month_plan, "jquants-bars-202503-attempt001", (leaf,))
        self.assertNotIn("2025-03-28", plan.network_dates)
        self.assertEqual("SOURCE_BOUND_POINTER_NO_COPY", "SOURCE_BOUND_POINTER_NO_COPY")

    def test_reuse_hash_constant_matches_frozen_source(self) -> None:
        result = verify_reuse_source(ROOT)
        self.assertEqual(EXP005_Q04_RAW_SHA256, result["raw_sha256"])
        self.assertEqual("SOURCE_BOUND_POINTER_NO_COPY", result["mode"])

    def test_oldest_month_first(self) -> None:
        later = MonthPlan("2025-04", ("2025-04-01",), BOOTSTRAP_PLAN_SHA256, "1" * 64)
        self.assertEqual("2025-03", oldest_missing_month((later, self.month_plan), ()).month)

    def test_monthly_network_is_gated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ContractError, "MONTHLY_NETWORK_NOT_AUTHORIZED"):
                require_monthly_network_authorization(Path(temp), False)

    def test_month_reservation_is_one_attempt_and_no_clobber(self) -> None:
        plan = build_monthly_attempt(self.month_plan, "jquants-bars-202503-attempt001", ())
        with tempfile.TemporaryDirectory() as temp:
            reserve_month_attempt(Path(temp), plan)
            with self.assertRaisesRegex(ContractError, "MONTH_ATTEMPT_EXISTS"):
                reserve_month_attempt(Path(temp), plan)

    def test_repair_requires_new_attempt_id(self) -> None:
        validate_repair_attempt("jquants-bars-202503-attempt001", "jquants-bars-202503-attempt002", "2025-03")
        with self.assertRaisesRegex(ContractError, "REPAIR_REQUIRES_NEW_ATTEMPT_ID"):
            validate_repair_attempt("jquants-bars-202503-attempt001", "jquants-bars-202503-attempt001", "2025-03")

    def test_catalog_publish_and_read(self) -> None:
        entry = {"attempt_id": "jquants-bars-202503-attempt001", "manifest_sha256": "2" * 64, "month": "2025-03", "status": "COMPLETE"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); publish_catalog_entry(root, entry)
            self.assertEqual((entry,), read_catalog(root))

    def test_catalog_no_clobber(self) -> None:
        entry = {"attempt_id": "jquants-bars-202503-attempt001", "manifest_sha256": "2" * 64, "month": "2025-03", "status": "COMPLETE"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); publish_catalog_entry(root, entry)
            with self.assertRaises(FileExistsError):
                publish_catalog_entry(root, entry)

    def test_dry_plan_has_no_network_or_monthly_authority(self) -> None:
        result = dry_plan(ROOT)
        self.assertEqual(0, result["network_requests"])
        self.assertFalse(result["monthly_network_authorized"])
        self.assertIn("environment:JQUANTS_API_KEY", result["key_source"])

    def test_raw_root_is_gitignored_by_policy(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertTrue("data/raw" in ignore or "data/" in ignore)

    def test_tracked_fact_snapshot_is_license_safe(self) -> None:
        fact = json.loads((ROOT / "experiments/exp_20260828_007/artifacts/official_plan_fact_snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual("CANONICAL_FACT_SNAPSHOT_NOT_RAW_BYTE_WEBPAGE_ARCHIVE", fact["semantics"])
        self.assertNotIn("JQUANTS_API_KEY", json.dumps(fact))

    def test_launcher_orders_freeze_reservation_before_key(self) -> None:
        source = (PACKAGE / "launcher.ps1").read_text(encoding="utf-8")
        self.assertLess(source.index("FREEZE_MANIFEST_MISMATCH"), source.index("FileMode]::CreateNew"))
        self.assertLess(source.index("FileMode]::CreateNew"), source.index(".env.jquants.local"))
        self.assertNotIn("Write-Output $apiKey", source)


if __name__ == "__main__":
    unittest.main()
