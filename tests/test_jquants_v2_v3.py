from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import numpy as np

from quant_research.alpha_models.data.jquants_v2_v3 import adapters

from quant_research.alpha_models.data.jquants_v2_v3.collector import (
    CollectorFailure,
    HTTPResponse,
    MIN_REQUEST_SPACING_SECONDS,
    collect_and_publish,
    collect_pages,
    dry_plan,
    verify_expected_freeze,
)
from quant_research.alpha_models.data.jquants_v2_v3.contracts import (
    QUERY_PLANS,
    DailyBar,
    CalendarDay,
    MasterRow,
    ProbeError,
    canonical_json_bytes,
    json_file_bytes,
    policy_time_ms,
    sha256_bytes,
)
from quant_research.alpha_models.data.jquants_v2_v3.loader import (
    EXPECTED_9433_DATES,
    causal_prices,
    listing_presence_for_formal_probe,
    merge_and_validate,
    parse_page,
    trusted_rebuild,
)
from quant_research.alpha_models.sspt_v2.contracts import StableLabelRegistry
from quant_research.alpha_models.sspt_v2.data import TrainOnlyMinMax, TrainingFeaturePartition


RECEIVED = 1_800_000_000_000
REPO_ROOT = Path(__file__).resolve().parents[1]
FREEZE_FILES = (
    "src/quant_research/alpha_models/data/jquants_v2_v3/__init__.py",
    "src/quant_research/alpha_models/data/jquants_v2_v3/contracts.py",
    "src/quant_research/alpha_models/data/jquants_v2_v3/collector.py",
    "src/quant_research/alpha_models/data/jquants_v2_v3/loader.py",
    "src/quant_research/alpha_models/data/jquants_v2_v3/adapters.py",
    "src/quant_research/alpha_models/data/jquants_v2_v3/launcher.ps1",
    "tests/test_jquants_v2_v3.py",
    "experiments/exp_20260828_004/artifacts/source_contract.json",
    "experiments/exp_20260828_004/artifacts/schema.json",
    "experiments/exp_20260828_004/parameters.json",
)


def _body(value: object) -> bytes:
    return canonical_json_bytes(value)


def _master(day: str = "2025-03-31") -> dict[str, object]:
    return {
        "Date": day,
        "Code": "94330",
        "CoName": "KDDI",
        "CoNameEn": "KDDI CORPORATION",
        "S17": "10",
        "S17Nm": "Information",
        "S33": "5250",
        "S33Nm": "Information and Communication",
        "ScaleCat": "TOPIX Core30",
        "Mkt": "0111",
        "MktNm": "Prime",
        "Mrgn": "1",
        "MrgnNm": "Margin",
        "SecType": "1",
        "SecTypeNm": "Equity",
    }


def _bar(day: str, code: str = "94330", *, factor: float = 1.0, ex_right: str = "0", close: float = 100.0) -> dict[str, object]:
    return {
        "Date": day,
        "Code": code,
        "O": close - 1.0,
        "H": close + 1.0,
        "L": close - 2.0,
        "C": close,
        "Vo": 1000.0,
        "Va": 100000.0,
        "AdjFactor": factor,
        "ExRT": ex_right,
        "AdjO": close - 1.0,
        "AdjH": close + 1.0,
        "AdjL": close - 2.0,
        "AdjC": close,
        "AdjVo": 1000.0,
    }


def _calendar_object(day: str) -> CalendarDay:
    policy = policy_time_ms(day, 0, 0)
    return CalendarDay(day, "0", policy, policy - 1, policy, "a" * 64)


def _master_object(day: str, code: str, sector: str) -> MasterRow:
    policy = policy_time_ms(day, 8, 0)
    raw_code = code + "0"
    return MasterRow(day, raw_code, code, f"Company {code}", f"Company {code}", sector, f"Sector {sector}", sector + "0", f"Sector33 {sector}", "TOPIX", "0111", "Prime", "1", "Margin", "1", "Equity", policy, policy - 1, policy, "b" * 64)


def _bar_object(day: str, code: str, close: float) -> DailyBar:
    policy = policy_time_ms(day, 16, 30)
    return DailyBar(day, code + "0", code, close - 1, close + 1, close - 2, close, 1000.0, 100000.0, 1.0, "0", close - 1, close + 1, close - 2, close, 1000.0, policy, policy - 1, policy, "c" * 64)


def _five_pages(*, q4_overlap: dict[str, object] | None = None) -> tuple[object, ...]:
    calendar = [{"Date": day, "HolDiv": "0" if day in ("2025-03-28", "2025-03-31") else "1"} for day in ("2025-03-28", "2025-03-29", "2025-03-30", "2025-03-31")]
    q4 = q4_overlap or _bar("2025-03-28", factor=0.5, ex_right="1")
    q5 = [_bar(day, factor=0.5 if day == "2025-03-28" else 1.0, ex_right="1" if day == "2025-03-28" else "0") for day in EXPECTED_9433_DATES]
    payloads = (
        {"data": calendar},
        {"data": [_master()]},
        {"data": [_master()]},
        {"data": [q4, _bar("2025-03-28", "72030", close=200.0)]},
        {"data": q5},
    )
    return tuple(parse_page(plan, page_number=1, status=200, body=_body(payload), received_at_ms=RECEIVED + index) for index, (plan, payload) in enumerate(zip(QUERY_PLANS, payloads)))


class VirtualClock:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.utc_ms = RECEIVED
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.seconds

    def utc(self) -> int:
        result = self.utc_ms
        self.utc_ms += 1
        return result

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.seconds += seconds
        self.utc_ms += int(round(seconds * 1000))


class FiveQueryTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, url: str, api_key: str, cap_bytes: int) -> HTTPResponse:
        if api_key != "SYNTHETIC_KEY" or cap_bytes <= 0:
            raise AssertionError("transport contract")
        self.calls.append(url)
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, strict_parsing=True)
        if parsed.path.endswith("calendar"):
            payload = {"data": [{"Date": day, "HolDiv": "0" if day in ("2025-03-28", "2025-03-31") else "1"} for day in ("2025-03-28", "2025-03-29", "2025-03-30", "2025-03-31")]}
        elif parsed.path.endswith("master"):
            self.assertEqual(query.get("code"), ["9433"])
            payload = {"data": [_master()]}
        elif query.get("date") == ["2025-03-28"]:
            payload = {"data": [_bar("2025-03-28", factor=0.5, ex_right="1"), _bar("2025-03-28", "72030", close=200.0)]}
        else:
            payload = {"data": [_bar(day, factor=0.5 if day == "2025-03-28" else 1.0, ex_right="1" if day == "2025-03-28" else "0") for day in EXPECTED_9433_DATES]}
        return HTTPResponse(200, "application/json; charset=utf-8", _body(payload), url)

    def assertEqual(self, left: object, right: object) -> None:
        if left != right:
            raise AssertionError((left, right))


class PaginatedCalendarTransport(FiveQueryTransport):
    def __call__(self, url: str, api_key: str, cap_bytes: int) -> HTTPResponse:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, strict_parsing=True)
        if parsed.path.endswith("calendar"):
            self.calls.append(url)
            if "pagination_key" not in query:
                return HTTPResponse(200, "application/json", _body({"data": [{"Date": "2025-03-28", "HolDiv": "0"}, {"Date": "2025-03-29", "HolDiv": "1"}], "pagination_key": "NEXT_PAGE"}), url)
            if query["pagination_key"] != ["NEXT_PAGE"]:
                raise AssertionError("pagination mutation")
            return HTTPResponse(200, "application/json", _body({"data": [{"Date": "2025-03-30", "HolDiv": "1"}, {"Date": "2025-03-31", "HolDiv": "0"}]}), url)
        return super().__call__(url, api_key, cap_bytes)


def _prepare_bound_repo(root: Path, overrides: dict[str, bytes] | None = None) -> str:
    overrides = {} if overrides is None else overrides
    entries = []
    for relative in FREEZE_FILES:
        source = REPO_ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative in overrides: target.write_bytes(overrides[relative])
        else: shutil.copyfile(source, target)
        raw = target.read_bytes()
        entries.append({"bytes": len(raw), "path": relative, "sha256": sha256_bytes(raw)})
    manifest = {"files": sorted(entries, key=lambda row: row["path"].encode("utf-8")), "schema_version": "JQUANTS_V2_V3_EXTERNAL_FREEZE_V1"}
    target = root / "experiments/exp_20260828_004/artifacts/expected_freeze_manifest.json"
    target.write_bytes(json_file_bytes(manifest))
    return sha256_bytes(target.read_bytes())


def _successful_temp_run(root: Path, transport: FiveQueryTransport | None = None) -> tuple[Path, Path, str]:
    expected = _prepare_bound_repo(root)
    clock = VirtualClock()
    collect_and_publish(
        repo_root=root,
        expected_freeze_sha256=expected,
        api_key="SYNTHETIC_KEY",
        transport=transport or FiveQueryTransport(),
        utc_clock=clock.utc,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )
    parent = root / "data/raw/jquants_v2_v3/runs"
    return parent / "exp_20260828_004_formal_001", parent / ".exp_20260828_004_formal_001.control", expected


def _prepare_launcher_sandbox(root: Path, env_bytes: bytes | None, *, launcher_transform=None) -> tuple[Path, str, str]:  # type: ignore[no-untyped-def]
    fake_collector = (
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "def main(argv=None):\n"
        " Path('child_marker.json').write_text(json.dumps({'argv':argv,'key_present':bool(os.environ.get('JQUANTS_API_KEY'))}),encoding='utf-8')\n"
        " return int(os.environ.get('FAKE_CHILD_EXIT','0'))\n"
    ).encode("utf-8")
    overrides = {"src/quant_research/alpha_models/data/jquants_v2_v3/collector.py": fake_collector}
    if launcher_transform is not None:
        launcher_source = (REPO_ROOT / FREEZE_FILES[5]).read_bytes()
        overrides[FREEZE_FILES[5]] = launcher_transform(launcher_source)
    freeze_sha = _prepare_bound_repo(root, overrides)
    launcher = root / "src/quant_research/alpha_models/data/jquants_v2_v3/launcher.ps1"
    freeze = root / "experiments/exp_20260828_004/artifacts/expected_freeze_manifest.json"
    control = root / "experiments/exp_20260828_004/formal_control"
    control.mkdir(parents=True, exist_ok=True)
    if env_bytes is not None:
        (root / ".env.jquants.local").write_bytes(env_bytes)
    scripts = root / ".venv/Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / ".venv/Scripts/python.exe", scripts / "python.exe")
    shutil.copyfile(REPO_ROOT / ".venv/pyvenv.cfg", root / ".venv/pyvenv.cfg")
    return launcher, sha256_bytes(launcher.read_bytes()), freeze_sha


def _run_launcher(root: Path, launcher: Path, launcher_sha: str, freeze_sha: str, *, child_exit: int = 0, parent_key: str | None = None) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("JQUANTS_API_KEY", None)
    environment["FAKE_CHILD_EXIT"] = str(child_exit)
    if parent_key is not None:
        environment["JQUANTS_API_KEY"] = parent_key
    return subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(launcher), launcher_sha, freeze_sha],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _attempt_paths(root: Path) -> tuple[Path, Path]:
    control = root / "experiments/exp_20260828_004/formal_control"
    return control / "exp_20260828_004_formal_001.reservation.lock", control / "exp_20260828_004_formal_001.stage_ledger.jsonl"


def _ledger_rows(root: Path) -> list[dict[str, object]]:
    _, ledger = _attempt_paths(root)
    raw = ledger.read_bytes()
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise AssertionError("ledger encoding")
    rows = [json.loads(line) for line in raw.splitlines()]
    if raw != b"".join(canonical_json_bytes(row) + b"\n" for row in rows):
        raise AssertionError("ledger canonical")
    return rows


class JQuantsV2V3QueryContractTests(unittest.TestCase):
    def test_exact_five_queries_and_researcher_dates(self) -> None:
        self.assertEqual([plan.query_id for plan in QUERY_PLANS], [
            "Q01_CALENDAR", "Q02_MASTER_NORMAL", "Q03_MASTER_NONTRADING_MAPPING",
            "Q04_ALL_BARS_NORMAL", "Q05_9433_SPLIT_RANGE",
        ])
        self.assertEqual(dict(QUERY_PLANS[0].parameters), {"from": "2025-03-28", "to": "2025-03-31"})
        self.assertEqual(dict(QUERY_PLANS[2].parameters), {"code": "9433", "date": "2025-03-30"})
        self.assertEqual(dict(QUERY_PLANS[4].parameters), {"code": "9433", "from": "2025-03-27", "to": "2025-04-02"})
        self.assertEqual(dry_plan()["minimum_request_spacing_seconds"], 13.0)

    def test_weekend_query_is_http_200_next_business_day_mapping(self) -> None:
        page = parse_page(QUERY_PLANS[2], page_number=1, status=200, body=_body({"data": [_master("2025-03-31")]}), received_at_ms=RECEIVED)
        self.assertEqual(page.records[0].snapshot_date, "2025-03-31")  # type: ignore[union-attr]
        for status, day in ((400, "2025-03-31"), (200, "2025-03-30")):
            with self.assertRaises(ProbeError):
                parse_page(QUERY_PLANS[2], page_number=1, status=status, body=_body({"data": [_master(day)]}), received_at_ms=RECEIVED)

    def test_exrt_is_required_and_split_expectation_is_exact(self) -> None:
        missing = _bar("2025-03-28", factor=0.5, ex_right="1")
        del missing["ExRT"]
        with self.assertRaisesRegex(ProbeError, "BAR_FIELDS"):
            parse_page(QUERY_PLANS[4], page_number=1, status=200, body=_body({"data": [missing]}), received_at_ms=RECEIVED)
        rows = merge_and_validate(_five_pages())
        split = next(row for row in rows.bars if row.symbol == "9433" and row.session_date == "2025-03-28")
        self.assertEqual((split.adjustment_factor, split.ex_right), (0.5, "1"))
        bad = list(_five_pages())
        q5 = [_bar(day, factor=1.0, ex_right="0") for day in EXPECTED_9433_DATES]
        bad[3] = parse_page(QUERY_PLANS[3], page_number=1, status=200, body=_body({"data": [_bar("2025-03-28"), _bar("2025-03-28", "72030", close=200.0)]}), received_at_ms=RECEIVED)
        bad[4] = parse_page(QUERY_PLANS[4], page_number=1, status=200, body=_body({"data": q5}), received_at_ms=RECEIVED)
        with self.assertRaisesRegex(ProbeError, "SPLIT_EXPECTATION"):
            merge_and_validate(bad)

    def test_q04_q05_overlap_must_match_then_is_deduplicated(self) -> None:
        loaded = merge_and_validate(_five_pages())
        self.assertEqual(len([row for row in loaded.bars if row.symbol == "9433" and row.session_date == "2025-03-28"]), 1)
        with self.assertRaisesRegex(ProbeError, "CROSS_QUERY_BAR_MISMATCH"):
            merge_and_validate(_five_pages(q4_overlap=_bar("2025-03-28", close=101.0)))

    def test_every_query_is_nonempty_and_exact_coverage(self) -> None:
        pages = list(_five_pages())
        pages[1] = replace(pages[1], records=())
        with self.assertRaisesRegex(ProbeError, "QUERY_EMPTY"):
            merge_and_validate(pages)
        pages = list(_five_pages())
        pages[0] = replace(pages[0], records=pages[0].records[:-1])
        with self.assertRaisesRegex(ProbeError, "CALENDAR_COVERAGE"):
            merge_and_validate(pages)

    def test_injected_clock_paces_every_request_without_real_sleep(self) -> None:
        clock = VirtualClock()
        transport = FiveQueryTransport()
        with tempfile.TemporaryDirectory() as temp:
            pages, receipts, _ = collect_pages(api_key="SYNTHETIC_KEY", transport=transport, utc_clock=clock.utc, monotonic=clock.monotonic, sleeper=clock.sleep, staging=Path(temp))
        self.assertEqual((len(pages), len(receipts), len(transport.calls)), (5, 5, 5))
        self.assertEqual(clock.sleeps, [MIN_REQUEST_SPACING_SECONDS] * 4)
        self.assertTrue(all(receipts[index]["sent_at_ms"] - receipts[index - 1]["sent_at_ms"] >= 13_000 for index in range(1, len(receipts))))
        self.assertTrue(all(receipt["pacing_wait_ms"] == 13_000 for receipt in receipts[1:]))

    def test_pagination_is_prior_page_only_complete_and_not_a_retry(self) -> None:
        clock = VirtualClock()
        transport = PaginatedCalendarTransport()
        with tempfile.TemporaryDirectory() as temp:
            pages, receipts, _ = collect_pages(api_key="SYNTHETIC_KEY", transport=transport, utc_clock=clock.utc, monotonic=clock.monotonic, sleeper=clock.sleep, staging=Path(temp))
        self.assertEqual((len(pages), len(receipts), len(transport.calls)), (6, 6, 6))
        self.assertNotIn("pagination_key", receipts[0]["request_parameters"])
        self.assertEqual(receipts[1]["request_parameters"]["pagination_key"], "NEXT_PAGE")
        self.assertEqual([row.query_id for row in pages[:2]], ["Q01_CALENDAR", "Q01_CALENDAR"])
        self.assertEqual(merge_and_validate(pages).http_count, 6)

    def test_pagination_loop_page_cap_and_global_cap_fail_closed(self) -> None:
        class LoopTransport(FiveQueryTransport):
            def __call__(self, url: str, api_key: str, cap_bytes: int) -> HTTPResponse:
                if urlsplit(url).path.endswith("calendar"):
                    self.calls.append(url)
                    return HTTPResponse(200, "application/json", _body({"data": [], "pagination_key": "LOOP"}), url)
                return super().__call__(url, api_key, cap_bytes)
        with tempfile.TemporaryDirectory() as temp:
            clock = VirtualClock()
            with self.assertRaisesRegex(CollectorFailure, "PAGE_LOOP"):
                collect_pages(api_key="SYNTHETIC_KEY", transport=LoopTransport(), utc_clock=clock.utc, monotonic=clock.monotonic, sleeper=clock.sleep, staging=Path(temp))
        with tempfile.TemporaryDirectory() as temp, patch("quant_research.alpha_models.data.jquants_v2_v3.collector.MAX_PAGES", 1):
            clock = VirtualClock()
            with self.assertRaisesRegex(CollectorFailure, "PAGE_CAP"):
                collect_pages(api_key="SYNTHETIC_KEY", transport=PaginatedCalendarTransport(), utc_clock=clock.utc, monotonic=clock.monotonic, sleeper=clock.sleep, staging=Path(temp))
        with tempfile.TemporaryDirectory() as temp, patch("quant_research.alpha_models.data.jquants_v2_v3.collector.GLOBAL_HTTP_CAP", 5):
            clock = VirtualClock()
            with self.assertRaisesRegex(CollectorFailure, "HTTP_CAP"):
                collect_pages(api_key="SYNTHETIC_KEY", transport=PaginatedCalendarTransport(), utc_clock=clock.utc, monotonic=clock.monotonic, sleeper=clock.sleep, staging=Path(temp))

    def test_redirect_bad_content_and_secret_exception_stop_before_next_query(self) -> None:
        class BadTransport:
            def __init__(self, mode: str) -> None:
                self.mode = mode
                self.calls = 0
            def __call__(self, url: str, api_key: str, cap_bytes: int) -> HTTPResponse:
                self.calls += 1
                if self.mode == "redirect": return HTTPResponse(302, "application/json", b"{}", "https://evil.example/path", 1)
                if self.mode == "content": return HTTPResponse(200, "text/html", b"{}", url)
                raise RuntimeError(f"must not disclose {api_key}")
        for mode in ("redirect", "content", "exception"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                clock = VirtualClock(); transport = BadTransport(mode)
                with self.assertRaises(Exception) as caught:
                    collect_pages(api_key="SYNTHETIC_KEY", transport=transport, utc_clock=clock.utc, monotonic=clock.monotonic, sleeper=clock.sleep, staging=Path(temp))
                self.assertEqual(transport.calls, 1)
                if mode != "exception": self.assertNotIn("SYNTHETIC_KEY", str(caught.exception))

    def test_strict_schema_duplicate_nonfinite_null_and_invalid_exrt(self) -> None:
        with self.assertRaisesRegex(ProbeError, "JSON_DUPLICATE_KEY"):
            parse_page(QUERY_PLANS[0], page_number=1, status=200, body=b'{"data":[],"data":[]}', received_at_ms=RECEIVED)
        with self.assertRaisesRegex(ProbeError, "JSON_NONFINITE"):
            parse_page(QUERY_PLANS[0], page_number=1, status=200, body=b'{"data":[{"Date":"2025-03-28","HolDiv":NaN}]}', received_at_ms=RECEIVED)
        bad = _bar("2025-03-28", factor=0.5, ex_right="2")
        with self.assertRaisesRegex(ProbeError, "EXRT"):
            parse_page(QUERY_PLANS[4], page_number=1, status=200, body=_body({"data": [bad]}), received_at_ms=RECEIVED)
        null_bar = _bar("2025-03-28", factor=0.5, ex_right="1")
        for key in ("O", "H", "L", "C", "AdjO", "AdjH", "AdjL", "AdjC"):
            null_bar[key] = None
        null_bar.update({"Vo": 0, "Va": 0, "AdjVo": 0})
        page = parse_page(QUERY_PLANS[4], page_number=1, status=200, body=_body({"data": [null_bar]}), received_at_ms=RECEIVED)
        self.assertFalse(page.records[0].traded)  # type: ignore[union-attr]


class JQuantsV2V3AuthorityAndLoaderTests(unittest.TestCase):
    def test_external_freeze_requires_canonical_exact_files_and_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = _prepare_bound_repo(root)
            self.assertEqual(verify_expected_freeze(root, expected)["files"], len(FREEZE_FILES))
            target = root / FREEZE_FILES[1]
            target.write_bytes(target.read_bytes() + b"\n")
            with self.assertRaisesRegex(CollectorFailure, "SOURCE_DRIFT"):
                verify_expected_freeze(root, expected)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = _prepare_bound_repo(root)
            path = root / "experiments/exp_20260828_004/artifacts/expected_freeze_manifest.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            path.write_bytes(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))
            with self.assertRaises(CollectorFailure):
                verify_expected_freeze(root, sha256_bytes(path.read_bytes()))

    def test_success_rebuilds_entire_tree_authorizes_and_never_persists_secret(self) -> None:
        secret = b"SYNTHETIC_KEY"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            final, control, expected = _successful_temp_run(root)
            self.assertTrue(final.is_dir())
            self.assertFalse((final.parent / f".{final.name}.staging").exists())
            self.assertTrue((control / "lease.json").is_file())
            self.assertTrue((control / "authorization.json").is_file())
            self.assertFalse((control / "failure.json").exists())
            rebuilt = trusted_rebuild(final)
            authorization = json.loads((control / "authorization.json").read_text(encoding="utf-8"))
            self.assertEqual(authorization["expected_freeze_manifest_sha256"], expected)
            self.assertEqual(authorization["final_tree_entries"], list(rebuilt.final_tree_entries))
            self.assertEqual(authorization["final_tree_sha256"], rebuilt.final_tree_sha256)
            for scope in (final, control):
                for path in scope.rglob("*"):
                    if not path.is_file():
                        continue
                    self.assertNotIn(secret, path.read_bytes())

    def test_source_drift_before_promotion_fails_without_final(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = _prepare_bound_repo(root)
            clock = VirtualClock()
            def drift() -> None:
                target = root / FREEZE_FILES[0]
                target.write_bytes(target.read_bytes() + b"\n")
            with self.assertRaisesRegex(CollectorFailure, "SOURCE_DRIFT"):
                collect_and_publish(repo_root=root, expected_freeze_sha256=expected, api_key="SYNTHETIC_KEY", transport=FiveQueryTransport(), utc_clock=clock.utc, monotonic=clock.monotonic, sleeper=clock.sleep, before_promotion=drift)
            parent = root / "data/raw/jquants_v2_v3/runs"
            self.assertFalse((parent / "exp_20260828_004_formal_001").exists())
            failure = json.loads((parent / ".exp_20260828_004_formal_001.control/failure.json").read_text(encoding="utf-8"))
            self.assertEqual(failure["failure_code"], "SOURCE_DRIFT")

    def test_no_clobber_race_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = _prepare_bound_repo(root)
            clock = VirtualClock()
            final = root / "data/raw/jquants_v2_v3/runs/exp_20260828_004_formal_001"
            def race() -> None:
                final.mkdir()
            with self.assertRaisesRegex(CollectorFailure, "FINAL_RACE"):
                collect_and_publish(repo_root=root, expected_freeze_sha256=expected, api_key="SYNTHETIC_KEY", transport=FiveQueryTransport(), utc_clock=clock.utc, monotonic=clock.monotonic, sleeper=clock.sleep, before_promotion=race)
            self.assertEqual(tuple(final.iterdir()), ())

    def _tamper_receipts(self, final: Path, mutation) -> None:  # type: ignore[no-untyped-def]
        path = final / "receipts.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        mutation(rows)
        path.write_bytes(b"".join(canonical_json_bytes(row) + b"\n" for row in rows))

    def test_loader_rejects_incomplete_query_chain_parameter_pacing_and_ordinals(self) -> None:
        mutations = (
            lambda rows: rows.__setitem__(1, {**rows[1], "query_ordinal": 3, "query_id": "Q03_MASTER_NONTRADING_MAPPING"}),
            lambda rows: rows[1].__setitem__("request_parameters_sha256", "0" * 64),
            lambda rows: rows[1].__setitem__("sent_at_ms", rows[0]["sent_at_ms"] + 12_999),
            lambda rows: rows[1].__setitem__("http_ordinal", 99),
        )
        for mutation in mutations:
            with self.subTest(mutation=repr(mutation)), tempfile.TemporaryDirectory() as temp:
                final, _, _ = _successful_temp_run(Path(temp))
                self._tamper_receipts(final, mutation)
                with self.assertRaises(ProbeError):
                    trusted_rebuild(final)

    def test_loader_rebuilds_valid_pagination_and_rejects_wrong_prior_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            final, _, _ = _successful_temp_run(Path(temp), PaginatedCalendarTransport())
            self.assertEqual(trusted_rebuild(final).loaded.http_count, 6)
            def wrong(rows) -> None:  # type: ignore[no-untyped-def]
                rows[1]["request_parameters"]["pagination_key"] = "WRONG"
                rows[1]["request_parameters_sha256"] = sha256_bytes(canonical_json_bytes(rows[1]["request_parameters"]))
            self._tamper_receipts(final, wrong)
            with self.assertRaisesRegex(ProbeError, "PAGINATION_CHAIN"):
                trusted_rebuild(final)

    def test_loader_rejects_raw_extra_missing_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            final, _, _ = _successful_temp_run(Path(temp))
            (final / "responses/extra.json").write_bytes(b"{}")
            with self.assertRaisesRegex(ProbeError, "RAW_BIJECTION"):
                trusted_rebuild(final)
        with tempfile.TemporaryDirectory() as temp:
            final, _, _ = _successful_temp_run(Path(temp))
            manifest_path = final / "acquisition_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["raw_files"][0]["path"] = "../escape.json"
            manifest_path.write_bytes(json_file_bytes(manifest))
            with self.assertRaises(ProbeError):
                trusted_rebuild(final)

    def test_preexistence_and_transport_secret_failure_do_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = _prepare_bound_repo(root)
            final = root / "data/raw/jquants_v2_v3/runs/exp_20260828_004_formal_001"
            final.mkdir(parents=True)
            before = tuple(final.parent.iterdir())
            with self.assertRaisesRegex(CollectorFailure, "PREEXISTENCE"):
                collect_and_publish(repo_root=root, expected_freeze_sha256=expected, api_key="SYNTHETIC_KEY", transport=FiveQueryTransport())
            self.assertEqual(tuple(final.parent.iterdir()), before)
        secret = "EXCEPTION_SENTINEL_SECRET"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = _prepare_bound_repo(root)
            def bad_transport(url: str, api_key: str, cap_bytes: int) -> HTTPResponse:
                raise RuntimeError(f"transport failed with {api_key}")
            clock = VirtualClock()
            with self.assertRaisesRegex(CollectorFailure, "INTERNAL"):
                collect_and_publish(repo_root=root, expected_freeze_sha256=expected, api_key=secret, transport=bad_transport, utc_clock=clock.utc, monotonic=clock.monotonic, sleeper=clock.sleep)
            data_root = root / "data/raw/jquants_v2_v3"
            for path in data_root.rglob("*"):
                if path.is_file(): self.assertNotIn(secret.encode("utf-8"), path.read_bytes())
            self.assertFalse((data_root / "runs/exp_20260828_004_formal_001").exists())

    def test_formal_code_filtered_master_presence_is_unconditionally_unknown(self) -> None:
        resolution = listing_presence_for_formal_probe()
        self.assertEqual((resolution.status, resolution.intervals, resolution.reason), ("UNKNOWN", (), "CODE_FILTERED_MASTER_CANNOT_PROVE_LISTING_PRESENCE"))
        loader_source = (REPO_ROOT / "src/quant_research/alpha_models/data/jquants_v2_v3/loader.py").read_text(encoding="utf-8")
        self.assertNotIn("derive_adjacent_presence", loader_source)
        self.assertNotIn("DERIVED_PRESENCE", loader_source)

    def test_policy_timestamp_is_not_backdated_known_at(self) -> None:
        page = parse_page(QUERY_PLANS[4], page_number=1, status=200, body=_body({"data": [_bar("2025-03-28", factor=0.5, ex_right="1")]}), received_at_ms=RECEIVED)
        row = page.records[0]
        assert isinstance(row, DailyBar)
        self.assertEqual(row.available_at_ms, RECEIVED)
        self.assertEqual(causal_prices((row,), symbol="9433", formation_time_ms=row.policy_observation_ms), {})
        self.assertIn("2025-03-28", causal_prices((row,), symbol="9433", formation_time_ms=RECEIVED))


class JQuantsV2V3AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        current = date(2025, 1, 6)
        days = []
        while len(days) < 52:
            if current.weekday() < 5:
                days.append(current.isoformat())
            current += timedelta(days=1)
        cls.days = tuple(days)
        cls.calendar = tuple(_calendar_object(day) for day in cls.days)
        cls.formation_index = 44
        cls.formation_date = cls.days[cls.formation_index]
        cls.symbols = ("6501", "7203")
        cls.masters = (_master_object(cls.formation_date, "6501", "10"), _master_object(cls.formation_date, "7203", "20"))
        cls.bars = tuple(_bar_object(day, symbol, (100.0 if symbol == "6501" else 200.0) + index) for index, day in enumerate(cls.days) for symbol in cls.symbols)

    def _scaler(self) -> TrainOnlyMinMax:
        formation = policy_time_ms(self.formation_date, 16, 30)
        label_end = policy_time_ms(self.days[self.formation_index + 1], 16, 30)
        partition = TrainingFeaturePartition(
            kind="TRAIN",
            features=np.zeros((1, 2, 16, 25), dtype=np.float64),
            formation_times_ms=(formation,),
            label_end_times_ms=(label_end,),
            train_end_exclusive_ms=label_end + 1,
            data_provenance_sha256="d" * 64,
        )
        return TrainOnlyMinMax().fit(partition)

    def _registries(self) -> tuple[StableLabelRegistry, StableLabelRegistry]:
        known = policy_time_ms(self.formation_date, 8, 0)
        return (
            StableLabelRegistry.from_labels("SCC", self.symbols, authority_id="JQUANTS_V2_V2_SYNTHETIC", training_partition_id="TRAIN", known_at_ms=known),
            StableLabelRegistry.from_labels("SSC", ("10", "20"), authority_id="JQUANTS_V2_V2_SYNTHETIC", training_partition_id="TRAIN", known_at_ms=known),
        )

    def test_sspt_and_tips_adapters_use_official_sessions_and_forbid_same_close(self) -> None:
        scc, ssc = self._registries()
        sspt = adapters.build_sspt_training(calendar=self.calendar, bars=self.bars, masters=self.masters, symbols=self.symbols, formation_date=self.formation_date, lookback=16, scaler=self._scaler(), scc_registry=scc, ssc_registry=ssc)
        self.assertEqual(sspt.batch.symbols, self.symbols)
        self.assertFalse(sspt.same_close_execution_allowed)
        tips = adapters.build_tips_training(calendar=self.calendar, bars=self.bars, masters=self.masters, symbols=self.symbols, formation_date=self.formation_date, partition_id="TRAIN", partition_session_ids=self.days)
        self.assertEqual(tips.batch.label_path_session_ids, self.days[self.formation_index:self.formation_index + 5])
        self.assertFalse(tips.same_close_execution_allowed)

    def test_late_received_historical_rows_fail_instead_of_backdating(self) -> None:
        late = tuple(replace(row, received_at_ms=RECEIVED, available_at_ms=RECEIVED) for row in self.calendar)
        late_bars = tuple(replace(row, received_at_ms=RECEIVED, available_at_ms=RECEIVED) for row in self.bars)
        late_masters = tuple(replace(row, received_at_ms=RECEIVED, available_at_ms=RECEIVED) for row in self.masters)
        with self.assertRaisesRegex(ProbeError, "NONINCREASING_OBSERVATION_CLOCK"):
            adapters.build_tips_training(calendar=late, bars=late_bars, masters=late_masters, symbols=self.symbols, formation_date=self.formation_date, partition_id="TRAIN", partition_session_ids=self.days)

    def test_missing_calendar_bar_and_label_path_action_fail_closed(self) -> None:
        missing = tuple(row for row in self.bars if not (row.symbol == "6501" and row.session_date == self.days[20]))
        with self.assertRaises(ProbeError):
            adapters.build_tips_training(calendar=self.calendar, bars=missing, masters=self.masters, symbols=self.symbols, formation_date=self.formation_date, partition_id="TRAIN", partition_session_ids=self.days)
        changed = tuple(replace(row, adjustment_factor=0.5) if row.symbol == "6501" and row.session_date == self.days[self.formation_index + 2] else row for row in self.bars)
        with self.assertRaisesRegex(ProbeError, "ACTION_IN_LABEL_PATH"):
            adapters.build_tips_training(calendar=self.calendar, bars=changed, masters=self.masters, symbols=self.symbols, formation_date=self.formation_date, partition_id="TRAIN", partition_session_ids=self.days)


class JQuantsV2V3LauncherTests(unittest.TestCase):
    def test_launcher_handoff_is_env_only_and_exact_child_exit_is_propagated(self) -> None:
        sentinel = "LAUNCHER_SENTINEL_KEY"
        for child_exit in (0, 10, 11, 20):
            with self.subTest(child_exit=child_exit), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                launcher, launcher_sha, freeze_sha = _prepare_launcher_sandbox(root, f"JQUANTS_API_KEY={sentinel}\n".encode("ascii"))
                result = _run_launcher(root, launcher, launcher_sha, freeze_sha, child_exit=child_exit)
                self.assertEqual(result.returncode, child_exit)
                self.assertEqual((result.stdout, result.stderr), ("", ""))
                marker = json.loads((root / "child_marker.json").read_text(encoding="utf-8"))
                self.assertTrue(marker["key_present"])
                self.assertNotIn(sentinel, json.dumps(marker))
                self.assertNotIn(sentinel, result.stdout + result.stderr)
                self.assertEqual(marker["argv"], ["--execute", "--expected-freeze-manifest-sha256", freeze_sha])
                reservation, ledger = _attempt_paths(root)
                self.assertTrue(reservation.is_file())
                self.assertTrue(ledger.is_file())
                rows = _ledger_rows(root)
                self.assertEqual([row["seq"] for row in rows], list(range(1, len(rows) + 1)))
                self.assertEqual([(row["stage"], row["event"]) for row in rows], [("SELF_HASH","PASS"),("FREEZE_PREFLIGHT","START"),("FREEZE_PREFLIGHT","PASS"),("ENV_FILE_READ","START"),("ENV_FILE_READ","PASS"),("VALIDATE","PASS"),("COLLECTOR","START"),("COLLECTOR","EXIT"),("FINAL_CLEANUP","PASS")])

    def test_launcher_selfhash_is_before_env_read_and_unknown_child_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launcher, _, freeze_sha = _prepare_launcher_sandbox(root, None)
            result = _run_launcher(root, launcher, "0" * 64, freeze_sha)
            self.assertEqual(result.returncode, 41)
            self.assertFalse((root / "child_marker.json").exists())
            self.assertEqual((result.stdout, result.stderr), ("", ""))
            reservation, ledger = _attempt_paths(root)
            self.assertFalse(reservation.exists())
            self.assertFalse(ledger.exists())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launcher, launcher_sha, freeze_sha = _prepare_launcher_sandbox(root, b"JQUANTS_API_KEY=SYNTHETIC\n")
            result = _run_launcher(root, launcher, launcher_sha, freeze_sha, child_exit=99)
            self.assertEqual(result.returncode, 45)
            self.assertTrue((root / "child_marker.json").exists())

    def test_launcher_rejects_preexisting_env_bad_grammar_cap_and_directory(self) -> None:
        cases = (
            (b"JQUANTS_API_KEY=A\nEXTRA=1\n", None, 44),
            (b"JQUANTS_API_KEY=" + b"A" * 4097, None, 43),
            (b"JQUANTS_API_KEY=FILE_VALUE\n", "PARENT_VALUE", 44),
        )
        for payload, parent_key, expected_exit in cases:
            with self.subTest(expected_exit=expected_exit), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                launcher, launcher_sha, freeze_sha = _prepare_launcher_sandbox(root, payload)
                result = _run_launcher(root, launcher, launcher_sha, freeze_sha, parent_key=parent_key)
                self.assertEqual(result.returncode, expected_exit)
                self.assertFalse((root / "child_marker.json").exists())
                self.assertEqual((result.stdout, result.stderr), ("", ""))
                self.assertTrue(_attempt_paths(root)[0].is_file())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launcher, launcher_sha, freeze_sha = _prepare_launcher_sandbox(root, None)
            (root / ".env.jquants.local").mkdir()
            result = _run_launcher(root, launcher, launcher_sha, freeze_sha)
            self.assertEqual(result.returncode, 43)
            self.assertFalse((root / "child_marker.json").exists())

    def test_launcher_source_has_no_argv_key_or_runtime_readback(self) -> None:
        source = (REPO_ROOT / FREEZE_FILES[5]).read_text(encoding="utf-8")
        self.assertIn("$env:JQUANTS_API_KEY = $key", source)
        self.assertIn("Remove-Item Env:JQUANTS_API_KEY", source)
        self.assertIn("ExpectedFreezeManifestSha256", source)
        self.assertNotIn("--api-key", source.lower())
        self.assertNotIn("Write-Host", source)
        self.assertNotIn("Write-Output", source)

    def test_exact_once_rerun_refuses_without_touching_ledger_or_child(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launcher, launcher_sha, freeze_sha = _prepare_launcher_sandbox(root, b"JQUANTS_API_KEY=SYNTHETIC\n")
            first = _run_launcher(root, launcher, launcher_sha, freeze_sha)
            self.assertEqual(first.returncode, 0)
            _, ledger = _attempt_paths(root)
            before = ledger.read_bytes()
            marker_before = (root / "child_marker.json").read_bytes()
            second = _run_launcher(root, launcher, launcher_sha, freeze_sha)
            self.assertEqual(second.returncode, 47)
            self.assertEqual(ledger.read_bytes(), before)
            self.assertEqual((root / "child_marker.json").read_bytes(), marker_before)

    def test_drifted_import_side_effect_never_executes_before_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launcher, launcher_sha, freeze_sha = _prepare_launcher_sandbox(root, b"JQUANTS_API_KEY=SYNTHETIC\n")
            collector = root / "src/quant_research/alpha_models/data/jquants_v2_v3/collector.py"
            collector.write_text("from pathlib import Path\nPath('IMPORT_SIDE_EFFECT_SENTINEL').write_text('BAD')\n", encoding="utf-8")
            result = _run_launcher(root, launcher, launcher_sha, freeze_sha)
            self.assertEqual(result.returncode, 42)
            self.assertFalse((root / "IMPORT_SIDE_EFFECT_SENTINEL").exists())
            self.assertFalse((root / "child_marker.json").exists())
            self.assertEqual([(row["stage"],row["event"]) for row in _ledger_rows(root)], [("SELF_HASH","PASS"),("FREEZE_PREFLIGHT","START"),("FREEZE_PREFLIGHT","FAIL"),("FINAL_CLEANUP","PASS")])

    def test_manifest_noncanonical_traversal_and_reparse_are_rejected_before_env(self) -> None:
        for mode in ("noncanonical", "traversal", "reparse"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                launcher, launcher_sha, freeze_sha = _prepare_launcher_sandbox(root, None)
                manifest_path = root / "experiments/exp_20260828_004/artifacts/expected_freeze_manifest.json"
                if mode == "noncanonical":
                    value = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest_path.write_bytes(json.dumps(value,sort_keys=True,separators=(",",":")).encode("utf-8"))
                    freeze_sha = sha256_bytes(manifest_path.read_bytes())
                elif mode == "traversal":
                    value = json.loads(manifest_path.read_text(encoding="utf-8"))
                    value["files"][0]["path"] = "../escape"
                    manifest_path.write_bytes(json_file_bytes(value)); freeze_sha=sha256_bytes(manifest_path.read_bytes())
                else:
                    target = root / FREEZE_FILES[0]
                    outside = root / "outside_directory"; outside.mkdir(); target.unlink()
                    created = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(target), str(outside)], text=True, capture_output=True, check=False)
                    self.assertEqual(created.returncode, 0)
                result = _run_launcher(root, launcher, launcher_sha, freeze_sha)
                self.assertEqual(result.returncode, 42)
                self.assertTrue(_attempt_paths(root)[0].is_file())
                self.assertFalse((root / "child_marker.json").exists())

    def test_control_io_and_cleanup_failures_are_consuming_and_prioritized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launcher, launcher_sha, freeze_sha = _prepare_launcher_sandbox(root, b"JQUANTS_API_KEY=SYNTHETIC\n")
            _, ledger = _attempt_paths(root); ledger.write_bytes(b"PREEXISTING")
            result = _run_launcher(root, launcher, launcher_sha, freeze_sha)
            self.assertEqual(result.returncode, 40)
            self.assertTrue(_attempt_paths(root)[0].is_file())
            self.assertEqual(ledger.read_bytes(), b"PREEXISTING")
            self.assertFalse((root / "child_marker.json").exists())
        def break_cleanup(raw: bytes) -> bytes:
            old=b"try { if ($envOwned) { Remove-Item Env:JQUANTS_API_KEY -ErrorAction Stop } } catch { $cleanupFailed=$true }"
            new=b"try { if ($envOwned) { throw 'SYNTHETIC_CLEANUP_FAILURE' } } catch { $cleanupFailed=$true }"
            if old not in raw: raise AssertionError("cleanup seam")
            return raw.replace(old,new)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            launcher, launcher_sha, freeze_sha = _prepare_launcher_sandbox(root, b"JQUANTS_API_KEY=SYNTHETIC\n", launcher_transform=break_cleanup)
            result = _run_launcher(root, launcher, launcher_sha, freeze_sha, child_exit=20)
            self.assertEqual(result.returncode, 46)
            self.assertEqual(_ledger_rows(root)[-1], {"event":"FAIL","exit_code":46,"seq":9,"stage":"FINAL_CLEANUP"})


if __name__ == "__main__":
    unittest.main()
