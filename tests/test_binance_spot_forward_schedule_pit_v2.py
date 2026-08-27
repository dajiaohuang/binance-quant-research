from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src/quant_research"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import binance_spot_forward_schedule_pit_v2 as collector
import binance_spot_forward_schedule_pit_v2_loader as loader
import binance_spot_forward_schedule_pit_v1 as exp008_collector


SECRET = "SENTINEL_EXP009_READ_ONLY_KEY_9f6c"

LEDGER_STAGES = {
    "SELF_HASH", "CLIPBOARD_READ", "VALIDATE", "PRECLEAR", "COLLECTOR",
    "FINAL_CLEANUP",
}
LEDGER_EVENTS = {"START", "PASS", "FAIL", "EXIT"}
LEDGER_FAIL_CODES = {
    "CLIPBOARD_READ": 42,
    "VALIDATE": 43,
    "PRECLEAR": 44,
    "COLLECTOR": 45,
    "FINAL_CLEANUP": 46,
}
ALLOWED_CHILD_CODES = {0, 10, 11, 20, 24, 30, 31, 32, 33, 34, 35}


def validate_complete_stage_ledger(raw: bytes) -> list[dict[str, object]]:
    if not raw or raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw or not raw.endswith(b"\n"):
        raise ValueError("ledger framing")
    rows: list[dict[str, object]] = []
    for sequence, line in enumerate(raw.splitlines(), 1):
        if not line:
            raise ValueError("empty ledger line")
        row = collector.strict_json(line)
        if type(row) is not dict or set(row) != {"seq", "stage", "event", "exit_code"}:
            raise ValueError("ledger keys")
        if type(row["seq"]) is not int or row["seq"] != sequence:
            raise ValueError("ledger sequence")
        stage = row["stage"]
        event = row["event"]
        exit_code = row["exit_code"]
        if stage not in LEDGER_STAGES or event not in LEDGER_EVENTS:
            raise ValueError("ledger enum")
        if collector.canonical_compact(row, newline=True) != line + b"\n":
            raise ValueError("ledger noncanonical")
        if event in {"START", "PASS"}:
            if exit_code is not None or (stage == "SELF_HASH" and event != "PASS"):
                raise ValueError("ledger null matrix")
        elif event == "FAIL":
            if type(exit_code) is not int or LEDGER_FAIL_CODES.get(stage) != exit_code:
                raise ValueError("ledger fail matrix")
        elif (
            stage != "COLLECTOR" or type(exit_code) is not int
            or exit_code not in ALLOWED_CHILD_CODES
        ):
            raise ValueError("ledger exit matrix")
        rows.append(row)

    position = 0
    def take(stage: str, event: str) -> dict[str, object]:
        nonlocal position
        if position >= len(rows):
            raise ValueError("ledger incomplete")
        row = rows[position]
        position += 1
        if (row["stage"], row["event"]) != (stage, event):
            raise ValueError("ledger transition")
        return row

    take("SELF_HASH", "PASS")
    take("CLIPBOARD_READ", "START")
    read = rows[position] if position < len(rows) else None
    if read is None:
        raise ValueError("ledger incomplete")
    if read["event"] == "FAIL":
        take("CLIPBOARD_READ", "FAIL")
    else:
        take("CLIPBOARD_READ", "PASS")
        validation = rows[position] if position < len(rows) else None
        if validation is None:
            raise ValueError("ledger incomplete")
        if validation["event"] == "FAIL":
            take("VALIDATE", "FAIL")
        else:
            take("VALIDATE", "PASS")
            preclear = rows[position] if position < len(rows) else None
            if preclear is None:
                raise ValueError("ledger incomplete")
            if preclear["event"] == "FAIL":
                take("PRECLEAR", "FAIL")
            else:
                take("PRECLEAR", "PASS")
                take("COLLECTOR", "START")
                terminal = rows[position] if position < len(rows) else None
                if terminal is None or terminal["event"] not in {"EXIT", "FAIL"}:
                    raise ValueError("ledger collector terminal")
                take("COLLECTOR", str(terminal["event"]))
    cleanup = rows[position] if position < len(rows) else None
    if cleanup is None or cleanup["stage"] != "FINAL_CLEANUP" or cleanup["event"] not in {"PASS", "FAIL"}:
        raise ValueError("ledger cleanup terminal")
    take("FINAL_CLEANUP", str(cleanup["event"]))
    if position != len(rows):
        raise ValueError("ledger extra rows")
    return rows


class WrapperHarness:
    def __init__(self, case: unittest.TestCase, *, child_exit: int = 0, include_python: bool = True) -> None:
        self.case = case
        self.temp = tempfile.TemporaryDirectory()
        case.addCleanup(self.temp.cleanup)
        self.repo = pathlib.Path(self.temp.name) / "repo"
        self.wrapper = self.repo / "src/quant_research/binance_spot_forward_schedule_pit_v2_wrapper.ps1"
        self.wrapper.parent.mkdir(parents=True)
        self.wrapper.write_bytes((ROOT / collector.BINDING_SPECS[0][2]).read_bytes())
        self.control = self.repo / "experiments/exp_20260826_009/formal_control"
        self.control.mkdir(parents=True)
        self.reservation = self.control / f"{collector.RUN_ID}.reservation.lock"
        self.ledger = self.control / f"{collector.RUN_ID}.stage_ledger.jsonl"
        self.get_marker = self.repo / "get_clipboard.marker"
        self.set_marker = self.repo / "set_clipboard.marker"
        self.child_marker = self.repo / "collector.marker"
        if include_python:
            python_path = self.repo / ".venv/Scripts/python.exe"
            python_path.parent.mkdir(parents=True)
            shutil.copy2(sys.executable, python_path)
            fake = self.repo / "src/quant_research/binance_spot_forward_schedule_pit_v2.py"
            fake.write_text(
                "import pathlib\n"
                f"pathlib.Path({str(self.child_marker)!r}).write_text('ran', encoding='utf-8')\n"
                f"raise SystemExit({child_exit})\n",
                encoding="utf-8",
            )
        self.wrapper_sha = hashlib.sha256(self.wrapper.read_bytes()).hexdigest()
        self.zero = "0" * 64

    def flags(self, *, wrapper_sha: str | None = None) -> str:
        return " ".join(
            f"-{parameter.removeprefix('-')} "
            f"{wrapper_sha if parameter == '-ExpectedWrapperSha256' else self.zero}"
            for _name, parameter in loader.FLAG_ORDER
        )

    def write_host(self, get_body: str, set_body: str, *, name: str = "host.ps1", wrapper_sha: str | None = None) -> pathlib.Path:
        host = self.repo / name
        host.write_text(
            "$ErrorActionPreference='Stop'\n"
            "$global:setCount=0\n"
            f"function global:Get-Clipboard {{ param([switch]$Raw) {get_body} }}\n"
            f"function global:Set-Clipboard {{ param([string]$Value) {set_body} }}\n"
            f"& '{self.wrapper.as_posix()}' {self.flags(wrapper_sha=wrapper_sha or self.wrapper_sha)}\n"
            "$wrapperExitCode=$LASTEXITCODE\n"
            "exit $wrapperExitCode\n",
            encoding="utf-8",
        )
        return host

    def run(self, get_body: str, set_body: str, *, wrapper_sha: str | None = None) -> subprocess.CompletedProcess[str]:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.case.skipTest("PowerShell unavailable")
        host = self.write_host(get_body, set_body, wrapper_sha=wrapper_sha)
        return subprocess.run(
            [
                powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(host),
            ],
            cwd=self.repo, capture_output=True, text=True, timeout=30, check=False,
        )


class Clock:
    def __init__(self) -> None:
        self.mono_value = 0.0
        self.utc_value = 1_000_000

    def monotonic(self) -> float:
        self.mono_value += 0.01
        return self.mono_value

    def utc_ms(self) -> int:
        self.utc_value += 1
        return self.utc_value


class FakeTransport:
    def __init__(self, bodies: list[bytes], statuses: list[int] | None = None) -> None:
        self.bodies = bodies
        self.statuses = statuses or [200] * len(bodies)
        self.calls: list[tuple[str, dict[str, str], float, int]] = []

    def __call__(
        self, url: str, headers: dict[str, str], timeout: float, cap: int,
    ) -> collector.TransportResponse:
        index = len(self.calls)
        self.calls.append((url, dict(headers), timeout, cap))
        return collector.TransportResponse(
            self.statuses[index], self.bodies[index], url,
        )


def response_bodies(
    *, open_rows: object | None = None, delist_rows: object | None = None,
    symbols: object | None = None, before: int = 1_000, after: int = 1_100,
) -> list[bytes]:
    if open_rows is None:
        open_rows = [{"openTime": 2_000, "symbols": ["AAAUSDT", "MISSUSDT"]}]
    if delist_rows is None:
        delist_rows = [{"delistTime": 3_000, "symbols": ["BBBUSDT"]}]
    if symbols is None:
        symbols = [
            {
                "symbol": "AAAUSDT", "status": "TRADING",
                "baseAsset": "AAA", "quoteAsset": "USDT",
                "permissionSets": [["SPOT"]], "unknown": "raw-only",
            },
            {
                "symbol": "BBBUSDT", "status": "BREAK",
                "baseAsset": "BBB", "quoteAsset": "USDT",
                "permissionSets": [["SPOT", "MARGIN"]],
            },
        ]
    return [
        json.dumps({"serverTime": before}).encode(),
        json.dumps(open_rows).encode(),
        json.dumps(delist_rows).encode(),
        json.dumps({"timezone": "UTC", "symbols": symbols}).encode(),
        json.dumps({"serverTime": after}).encode(),
    ]


class Layout:
    def __init__(self, case: unittest.TestCase) -> None:
        self.case = case
        self.temp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.temp.name) / "repo"
        (self.repo / "data/raw").mkdir(parents=True)
        self.patches: list[mock._patch] = []
        for _flag, _name, relative in collector.BINDING_SPECS:
            source = ROOT.joinpath(*pathlib.PurePosixPath(relative).parts)
            target = self.repo.joinpath(*pathlib.PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        version = self.repo / f"data/raw/{collector.VERSION}"
        runs = version / "runs"
        final = runs / collector.RUN_ID
        staging = runs / f".{collector.RUN_ID}.staging"
        control = runs / f".{collector.RUN_ID}.control"
        values = {
            "REPO_ROOT": self.repo,
            "RAW_ROOT": self.repo / "data/raw",
            "VERSION_ROOT": version,
            "RUNS_ROOT": runs,
            "FINAL_ROOT": final,
            "STAGING_ROOT": staging,
            "CONTROL_ROOT": control,
        }
        for name, value in values.items():
            patcher = mock.patch.object(collector, name, value)
            patcher.start()
            self.patches.append(patcher)
        self.final = final
        self.staging = staging
        self.control = control
        self.version = version
        self.runs = runs

    def close(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def argv(self) -> list[str]:
        result: list[str] = []
        for flag, _name, relative in collector.BINDING_SPECS:
            path = self.repo.joinpath(*pathlib.PurePosixPath(relative).parts)
            result.extend((flag, hashlib.sha256(path.read_bytes()).hexdigest()))
        return result

    def bindings(self) -> dict[str, dict[str, str]]:
        return collector.expected_bindings(collector.parse_args(self.argv()))

    def run(
        self, transport: object, *, environ: dict[str, str] | None = None,
        clock: Clock | None = None,
    ) -> int:
        clock = clock or Clock()
        return collector.main(
            self.argv(), transport=transport, monotonic=clock.monotonic,
            utc_ms=clock.utc_ms,
            environ=environ if environ is not None else {collector.API_KEY_ENV: SECRET},
        )


class ForwardSchedulePitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = Layout(self)

    def tearDown(self) -> None:
        self.layout.close()

    def test_success_nonempty_matched_and_missing(self) -> None:
        transport = FakeTransport(response_bodies())
        self.assertEqual(self.layout.run(transport), 0)
        loaded = loader.validate_directory(
            self.layout.final,
            expected_code_bindings=self.layout.bindings(),
        )
        self.assertEqual(loaded.terminal_status, "NEEDS_MORE_DATA")
        self.assertEqual((loaded.plan_count, loaded.current_symbol_count, loaded.join_count), (3, 2, 3))
        joins = collector.strict_jsonl((self.layout.final / "joins.jsonl").read_bytes())
        self.assertEqual({row["join_status"] for row in joins}, {"MATCHED", "MISSING"})
        self.assertFalse(loaded.summary["historical_eligibility_ready"])
        self.assertFalse(loaded.summary["eligibility_evaluated"])
        self.assertEqual(loaded.strict_eligible_count, 0)

    def test_both_schedule_lists_empty(self) -> None:
        transport = FakeTransport(response_bodies(open_rows=[], delist_rows=[]))
        self.assertEqual(self.layout.run(transport), 0)
        loaded = loader.validate_directory(self.layout.final, expected_code_bindings=self.layout.bindings())
        self.assertEqual((loaded.plan_count, loaded.join_count), (0, 0))
        self.assertEqual(loaded.summary["join_status_counts"], {"MATCHED": 0, "MISSING": 0})

    def test_exact_five_order_urls_headers_and_caps(self) -> None:
        transport = FakeTransport(response_bodies())
        self.assertEqual(self.layout.run(transport), 0)
        self.assertEqual(len(transport.calls), 5)
        for call, endpoint in zip(transport.calls, collector.ENDPOINTS, strict=True):
            url, headers, timeout, cap = call
            self.assertEqual(url, endpoint.url)
            self.assertEqual(headers, {"X-MBX-APIKEY": SECRET} if endpoint.api_key_header else {})
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 30)
            self.assertEqual(cap, endpoint.body_cap)
        self.assertEqual(transport.calls[3][0].count("?"), 1)
        self.assertTrue(transport.calls[3][0].endswith("?showPermissionSets=true"))
        for path in self.layout.final.rglob("*"):
            if path.is_file() and path.suffix != ".response":
                self.assertNotIn("X-MBX-APIKEY", path.read_text(encoding="utf-8"))

    def test_receipts_are_strictly_sequential_and_safe(self) -> None:
        self.assertEqual(self.layout.run(FakeTransport(response_bodies())), 0)
        previous = None
        for endpoint in collector.ENDPOINTS:
            path = self.layout.final.joinpath(*pathlib.PurePosixPath(collector.RECEIPT_NAMES[endpoint.endpoint_id]).parts)
            receipt = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(receipt), loader.RECEIPT_KEYS)
            self.assertLess(receipt["client_send_utc_ms"], receipt["client_recv_utc_ms"])
            if previous is not None:
                self.assertLess(previous, receipt["client_send_utc_ms"])
            previous = receipt["client_recv_utc_ms"]
            self.assertNotIn("headers", receipt)

    def test_no_redirect_handler(self) -> None:
        handler = collector._NoRedirectHandler()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://evil.invalid/"))

    def test_default_transport_uses_no_proxy_no_redirect_and_bounded_read(self) -> None:
        class Response:
            status = 200
            def __enter__(self) -> "Response":
                return self
            def __exit__(self, *args: object) -> None:
                return None
            def read(self, amount: int) -> bytes:
                self.amount = amount
                return b"{}"
            def geturl(self) -> str:
                return collector.ENDPOINTS[0].url
        class Opener:
            def __init__(self) -> None:
                self.response = Response()
            def open(self, request: object, timeout: float) -> Response:
                self.request = request
                self.timeout = timeout
                return self.response
        opener = Opener()
        captured: list[object] = []
        def build(*handlers: object) -> Opener:
            captured.extend(handlers)
            return opener
        with mock.patch.object(collector.urllib.request, "build_opener", side_effect=build):
            response = collector._default_transport(
                collector.ENDPOINTS[0].url, {}, 30.0, 10,
            )
        self.assertEqual(response.body, b"{}")
        self.assertEqual(opener.response.amount, 11)
        proxy = next(item for item in captured if isinstance(item, collector.urllib.request.ProxyHandler))
        self.assertEqual(proxy.proxies, {})
        self.assertTrue(any(isinstance(item, collector._NoRedirectHandler) for item in captured))

    def test_http_401_and_429_stop_without_retry(self) -> None:
        for status, failing_ordinal in ((401, 2), (429, 3)):
            with self.subTest(status=status):
                self.layout.close()
                self.layout = Layout(self)
                statuses = [200] * 5
                statuses[failing_ordinal - 1] = status
                transport = FakeTransport(response_bodies(), statuses)
                self.assertEqual(self.layout.run(transport), collector.EXIT_HTTP_STATUS)
                self.assertEqual(len(transport.calls), failing_ordinal)
                self.assertTrue((self.layout.control / "failure.json").is_file())
                self.assertFalse(self.layout.final.exists())

    def test_redirect_response_stops_once(self) -> None:
        statuses = [302, 200, 200, 200, 200]
        transport = FakeTransport(response_bodies(), statuses)
        self.assertEqual(self.layout.run(transport), collector.EXIT_HTTP_STATUS)
        self.assertEqual(len(transport.calls), 1)

    def test_timeout_exception_is_sanitized_and_stops(self) -> None:
        calls = []
        def failing(url: str, headers: dict[str, str], timeout: float, cap: int) -> collector.TransportResponse:
            calls.append(url)
            raise RuntimeError(SECRET)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.layout.run(failing)
        self.assertEqual(code, collector.EXIT_TRANSPORT)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(SECRET, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn(SECRET, (self.layout.control / "failure.json").read_text(encoding="utf-8"))

    def test_transport_exception_chain_contains_no_secret(self) -> None:
        clock = Clock()
        def failing(url: str, headers: dict[str, str], timeout: float, cap: int) -> collector.TransportResponse:
            raise RuntimeError(SECRET)
        with self.assertRaises(collector.CollectorError) as captured:
            collector._safe_request(
                collector.ENDPOINTS[0], api_key=SECRET, transport=failing,
                monotonic=clock.monotonic, utc_ms=clock.utc_ms,
                deadline=100.0, previous_recv_ms=None,
            )
        values = []
        current: BaseException | None = captured.exception
        while current is not None:
            values.extend((str(current), repr(current)))
            current = current.__cause__ or current.__context__
        self.assertNotIn(SECRET, "".join(values))

    def test_total_wall_exhaustion_stops_after_current_wire(self) -> None:
        class WallClock:
            def __init__(self) -> None:
                self.values = iter((0.0, 0.0, 181.0))
                self.utc = iter((1_000, 1_001))
            def monotonic(self) -> float:
                return next(self.values)
            def utc_ms(self) -> int:
                return next(self.utc)
        transport = FakeTransport(response_bodies())
        clock = WallClock()
        self.assertEqual(
            self.layout.run(transport, clock=clock), collector.EXIT_TRANSPORT,
        )
        self.assertEqual(len(transport.calls), 1)

    def test_oversize_stops_without_persisting_offending_body(self) -> None:
        bodies = response_bodies()
        bodies[1] = b"x" * (collector.ENDPOINTS[1].body_cap + 1)
        transport = FakeTransport(bodies)
        self.assertEqual(self.layout.run(transport), collector.EXIT_TRANSPORT)
        self.assertEqual(len(transport.calls), 2)
        offending = self.layout.staging.joinpath(*pathlib.PurePosixPath(collector.RAW_NAMES["open_symbol_list"]).parts)
        self.assertFalse(offending.exists())

    def test_bad_json_duplicate_symbol_and_bad_permission_sets(self) -> None:
        cases = []
        bad_json = response_bodies()
        bad_json[1] = b"["
        cases.append(bad_json)
        duplicate = response_bodies(symbols=[
            {"symbol": "A", "status": "TRADING", "baseAsset": "A", "quoteAsset": "B", "permissionSets": [[]]},
            {"symbol": "A", "status": "BREAK", "baseAsset": "A", "quoteAsset": "B", "permissionSets": [[]]},
        ])
        cases.append(duplicate)
        bad_permissions = response_bodies(symbols=[
            {"symbol": "A", "status": "TRADING", "baseAsset": "A", "quoteAsset": "B", "permissionSets": ["SPOT"]},
        ])
        cases.append(bad_permissions)
        for bodies in cases:
            with self.subTest(case=len(cases)):
                self.layout.close()
                self.layout = Layout(self)
                self.assertEqual(self.layout.run(FakeTransport(bodies)), collector.EXIT_JSON_SCHEMA)
                self.assertFalse(self.layout.final.exists())

    def test_duplicate_schedule_key_rejected(self) -> None:
        rows = [
            {"openTime": 2_000, "symbols": ["AAA"]},
            {"openTime": 2_000, "symbols": ["AAA"]},
        ]
        self.assertEqual(self.layout.run(FakeTransport(response_bodies(open_rows=rows))), collector.EXIT_JSON_SCHEMA)

    def test_server_time_reverse_and_too_wide(self) -> None:
        for before, after in ((2_000, 1_999), (1_000, 181_001)):
            with self.subTest(before=before, after=after):
                self.layout.close()
                self.layout = Layout(self)
                self.assertEqual(
                    self.layout.run(FakeTransport(response_bodies(before=before, after=after))),
                    collector.EXIT_TIME_BRACKET,
                )

    def test_loader_rebuild_and_tamper_closure(self) -> None:
        self.assertEqual(self.layout.run(FakeTransport(response_bodies())), 0)
        target = self.layout.final / "plans.jsonl"
        raw = bytearray(target.read_bytes())
        raw[0] ^= 1
        target.write_bytes(bytes(raw))
        with self.assertRaises(loader.SnapshotLoadError):
            loader.validate_directory(self.layout.final, expected_code_bindings=self.layout.bindings())

    def test_raw_or_receipt_tamper_rejected(self) -> None:
        for relative in (
            collector.RAW_NAMES["time_before"],
            collector.RECEIPT_NAMES["exchange_info"],
        ):
            with self.subTest(relative=relative):
                self.layout.close()
                self.layout = Layout(self)
                self.assertEqual(self.layout.run(FakeTransport(response_bodies())), 0)
                path = self.layout.final.joinpath(*pathlib.PurePosixPath(relative).parts)
                path.write_bytes(path.read_bytes() + b"x")
                with self.assertRaises(loader.SnapshotLoadError):
                    loader.validate_directory(self.layout.final, expected_code_bindings=self.layout.bindings())

    def test_precondition_deletes_child_env_before_any_write(self) -> None:
        for value in (None, "", "  ", "A\nB", "A\rB", "A\x00B", " A "):
            with self.subTest(value=value):
                self.layout.close()
                self.layout = Layout(self)
                env = {} if value is None else {collector.API_KEY_ENV: value}
                transport = FakeTransport(response_bodies())
                self.assertEqual(self.layout.run(transport, environ=env), collector.EXIT_PRECONDITION)
                self.assertNotIn(collector.API_KEY_ENV, env)
                self.assertEqual(transport.calls, [])
                self.assertFalse(self.layout.version.exists())

    def test_secret_echo_in_response_is_rejected_before_raw_write(self) -> None:
        bodies = response_bodies()
        bodies[0] = (b'{"serverTime":1000,"echo":"' + SECRET.encode() + b'"}')
        self.assertEqual(self.layout.run(FakeTransport(bodies)), collector.EXIT_TRANSPORT)
        for path in self.layout.version.rglob("*"):
            if path.is_file():
                self.assertNotIn(SECRET.encode(), path.read_bytes())

    def test_secret_absent_from_all_success_outputs(self) -> None:
        env = {collector.API_KEY_ENV: SECRET}
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(self.layout.run(FakeTransport(response_bodies()), environ=env), 0)
        self.assertNotIn(collector.API_KEY_ENV, env)
        material = stdout.getvalue().encode() + stderr.getvalue().encode()
        for path in self.layout.version.rglob("*"):
            if path.is_file():
                material += path.read_bytes()
        self.assertNotIn(SECRET.encode(), material)

    def test_parent_bootstrap_and_atomic_authority(self) -> None:
        self.assertFalse(self.layout.version.exists())
        self.assertEqual(self.layout.run(FakeTransport(response_bodies())), 0)
        self.assertTrue(self.layout.runs.is_dir())
        self.assertTrue(self.layout.final.is_dir())
        self.assertFalse(self.layout.staging.exists())
        self.assertTrue((self.layout.control / "lease.json").is_file())
        self.assertTrue((self.layout.control / "authorization.json").is_file())
        self.assertFalse((self.layout.control / "failure.json").exists())

    def test_preexisting_control_is_zero_network(self) -> None:
        self.layout.control.mkdir(parents=True)
        transport = FakeTransport(response_bodies())
        self.assertEqual(self.layout.run(transport), collector.EXIT_PREEXISTENCE)
        self.assertEqual(transport.calls, [])
        self.assertEqual(list(self.layout.control.iterdir()), [])

    def test_non_directory_shared_parent_fails_before_reservation(self) -> None:
        self.layout.version.parent.mkdir(parents=True, exist_ok=True)
        self.layout.version.write_text("not-dir", encoding="utf-8")
        transport = FakeTransport(response_bodies())
        self.assertEqual(self.layout.run(transport), collector.EXIT_INFRASTRUCTURE)
        self.assertEqual(transport.calls, [])
        self.assertFalse(self.layout.control.exists())

    def test_path_escape_and_cross_drive_seams_fail(self) -> None:
        with mock.patch.object(collector, "_same_path", return_value=False):
            self.assertEqual(self.layout.run(FakeTransport(response_bodies())), collector.EXIT_INFRASTRUCTURE)
        self.layout.close()
        self.layout = Layout(self)
        with mock.patch.object(collector, "_drive", side_effect=lambda path: "x" if path == collector.FINAL_ROOT else "y"):
            self.assertEqual(self.layout.run(FakeTransport(response_bodies())), collector.EXIT_INFRASTRUCTURE)

    def test_control_reservation_then_lease_failure_consumes_without_failure(self) -> None:
        original = collector._write_once
        def fail_lease(path: pathlib.Path, raw: bytes) -> None:
            if path.name == "lease.json":
                raise OSError("injected")
            original(path, raw)
        with mock.patch.object(collector, "_write_once", side_effect=fail_lease):
            self.assertEqual(self.layout.run(FakeTransport(response_bodies())), collector.EXIT_INFRASTRUCTURE)
        self.assertTrue(self.layout.control.is_dir())
        self.assertFalse((self.layout.control / "lease.json").exists())
        self.assertFalse((self.layout.control / "failure.json").exists())

    def test_rename_failure_records_promotion_failure(self) -> None:
        with mock.patch.object(collector.os, "rename", side_effect=OSError("injected")):
            self.assertEqual(self.layout.run(FakeTransport(response_bodies())), collector.EXIT_PROMOTION)
        self.assertFalse(self.layout.final.exists())
        self.assertTrue(self.layout.staging.exists())
        self.assertTrue((self.layout.control / "lease.json").exists())
        self.assertTrue((self.layout.control / "failure.json").exists())
        self.assertFalse((self.layout.control / "authorization.json").exists())

    def test_source_hash_mismatch_precedes_parent_creation(self) -> None:
        argv = self.layout.argv()
        argv[1] = "0" * 64
        env = {collector.API_KEY_ENV: SECRET}
        self.assertEqual(collector.main(argv, transport=FakeTransport(response_bodies()), environ=env), collector.EXIT_SOURCE_BINDING)
        self.assertFalse(self.layout.version.exists())
        self.assertNotIn(collector.API_KEY_ENV, env)

    def test_wrapper_static_cleanup_and_exit_order(self) -> None:
        text = (ROOT / collector.BINDING_SPECS[0][2]).read_text(encoding="utf-8")
        self.assertLess(text.index("ReadAllBytes($PSCommandPath)"), text.index("Get-Clipboard -Raw"))
        self.assertLess(text.index("SHA256]::Create()"), text.index("Get-Clipboard -Raw"))
        self.assertLess(text.index("FileMode]::CreateNew"), text.index("Get-Clipboard -Raw"))
        self.assertLess(text.index("CLIPBOARD_READ' -Event 'START"), text.index("Get-Clipboard -Raw"))
        self.assertLess(text.index("Get-Clipboard -Raw"), text.index("Set-Clipboard -Value ''"))
        self.assertLess(text.index("Set-Clipboard -Value ''"), text.index("$env:BINANCE_READ_ONLY_API_KEY"))
        self.assertIn("Remove-Item Env:BINANCE_READ_ONLY_API_KEY", text)
        self.assertIn("Set-Clipboard -Value ''", text)
        self.assertIn("Clear-Variable -Name rawClipboard", text)
        self.assertIn("Clear-Variable -Name trimmedKey", text)
        self.assertIn("exit 46", text)
        self.assertIn("exit 40", text)
        self.assertIn("exit 47", text)
        self.assertNotIn(SECRET, text)

    def test_powershell_wrapper_parses_without_execution(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell unavailable")
        wrapper = ROOT / collector.BINDING_SPECS[0][2]
        command = (
            "$ErrorActionPreference='Stop';"
            f"[void][scriptblock]::Create([IO.File]::ReadAllText('{wrapper.as_posix()}'));"
            "exit 0"
        )
        result = subprocess.run(
            [
                powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", command,
            ],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_wrapper_valid_clipboard_source_failure_clears_parent_state(self) -> None:
        harness = WrapperHarness(self, child_exit=0)
        result = harness.run(f"[IO.File]::WriteAllText('{harness.get_marker.as_posix()}','1'); '{SECRET}'", "")
        self.assertEqual(result.returncode, 0)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        rows = validate_complete_stage_ledger(harness.ledger.read_bytes())
        self.assertEqual(
            [(row["stage"], row["event"], row["exit_code"]) for row in rows],
            [
                ("SELF_HASH", "PASS", None),
                ("CLIPBOARD_READ", "START", None),
                ("CLIPBOARD_READ", "PASS", None),
                ("VALIDATE", "PASS", None),
                ("PRECLEAR", "PASS", None),
                ("COLLECTOR", "START", None),
                ("COLLECTOR", "EXIT", 0),
                ("FINAL_CLEANUP", "PASS", None),
            ],
        )
        self.assertEqual(
            harness.reservation.read_bytes(),
            b'{"experiment_id":"exp_20260826_009","run_id":"exp_20260826_009_formal_001"}\n',
        )
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertNotIn(SECRET.encode(), harness.reservation.read_bytes() + harness.ledger.read_bytes())

    def test_wrapper_hash_drift_stops_before_clipboard(self) -> None:
        harness = WrapperHarness(self)
        result = harness.run(
            f"[IO.File]::WriteAllText('{harness.get_marker.as_posix()}','called'); '{SECRET}'",
            "throw 'must-not-write-clipboard'",
            wrapper_sha="0" * 64,
        )
        self.assertEqual(result.returncode, 41)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertFalse(harness.get_marker.exists())
        self.assertFalse(harness.reservation.exists())
        self.assertFalse(harness.ledger.exists())

    def test_wrapper_prelaunch_clipboard_clear_failure_stops_collector(self) -> None:
        harness = WrapperHarness(self)
        result = harness.run(f"'{SECRET}'", "$global:setCount += 1; if ($global:setCount -eq 1) { throw 'preclear' }")
        self.assertEqual(result.returncode, 44)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        rows = validate_complete_stage_ledger(harness.ledger.read_bytes())
        self.assertEqual((rows[-2]["stage"], rows[-2]["event"], rows[-2]["exit_code"]), ("PRECLEAR", "FAIL", 44))
        self.assertFalse(harness.child_marker.exists())

    def test_wrapper_final_clipboard_clear_failure_cannot_be_exit_zero(self) -> None:
        harness = WrapperHarness(self, child_exit=0)
        result = harness.run(f"'{SECRET}'", "$global:setCount += 1; if ($global:setCount -ge 2) { throw 'cleanup' }")
        self.assertEqual(result.returncode, 46)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        rows = validate_complete_stage_ledger(harness.ledger.read_bytes())
        self.assertEqual((rows[-1]["stage"], rows[-1]["event"], rows[-1]["exit_code"]), ("FINAL_CLEANUP", "FAIL", 46))
        self.assertTrue(harness.child_marker.exists())

    def test_wrapper_exit_priority_cleanup_over_control_io_over_child(self) -> None:
        control_io = WrapperHarness(self, child_exit=35)
        close_ledger = (
            "$stream=(Get-Variable -Name ledgerStream -Scope 1).Value; "
            f"$stream.Dispose(); '{SECRET}'"
        )
        result = control_io.run(close_ledger, "")
        self.assertEqual(result.returncode, 40)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertFalse(control_io.child_marker.exists())
        self.assertNotIn(SECRET.encode(), control_io.reservation.read_bytes() + control_io.ledger.read_bytes())

        cleanup = WrapperHarness(self, child_exit=35)
        result = cleanup.run(close_ledger, "throw 'cleanup'")
        self.assertEqual(result.returncode, 46)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertFalse(cleanup.child_marker.exists())
        self.assertNotIn(SECRET.encode(), cleanup.reservation.read_bytes() + cleanup.ledger.read_bytes())

    def test_wrapper_read_and_validate_failures_are_exact_and_cleanup_runs(self) -> None:
        read = WrapperHarness(self)
        result = read.run("throw 'read'", f"[IO.File]::AppendAllText('{read.set_marker.as_posix()}','S')")
        self.assertEqual(result.returncode, 42)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        rows = validate_complete_stage_ledger(read.ledger.read_bytes())
        self.assertEqual((rows[-2]["stage"], rows[-2]["event"]), ("CLIPBOARD_READ", "FAIL"))
        self.assertEqual(read.set_marker.read_text(encoding="utf-8"), "S")

        validate = WrapperHarness(self)
        result = validate.run("''", f"[IO.File]::AppendAllText('{validate.set_marker.as_posix()}','S')")
        self.assertEqual(result.returncode, 43)
        rows = validate_complete_stage_ledger(validate.ledger.read_bytes())
        self.assertEqual((rows[-2]["stage"], rows[-2]["event"]), ("VALIDATE", "FAIL"))
        self.assertEqual(validate.set_marker.read_text(encoding="utf-8"), "S")

    def test_wrapper_allowed_child_codes_unknown_and_launch_failure(self) -> None:
        for code in sorted(ALLOWED_CHILD_CODES):
            with self.subTest(code=code):
                harness = WrapperHarness(self, child_exit=code)
                result = harness.run(f"'{SECRET}'", "")
                self.assertEqual(result.returncode, code)
                rows = validate_complete_stage_ledger(harness.ledger.read_bytes())
                self.assertEqual((rows[-2]["event"], rows[-2]["exit_code"]), ("EXIT", code))
        unknown = WrapperHarness(self, child_exit=99)
        result = unknown.run(f"'{SECRET}'", "")
        self.assertEqual(result.returncode, 45)
        rows = validate_complete_stage_ledger(unknown.ledger.read_bytes())
        self.assertEqual((rows[-2]["event"], rows[-2]["exit_code"]), ("FAIL", 45))

        launch = WrapperHarness(self, include_python=False)
        result = launch.run(f"'{SECRET}'", "")
        self.assertEqual(result.returncode, 45)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        validate_complete_stage_ledger(launch.ledger.read_bytes())

    def test_reservation_zero_partial_and_preexisting_are_exit47_before_clipboard(self) -> None:
        for raw in (b"", b"partial"):
            with self.subTest(raw=raw):
                harness = WrapperHarness(self)
                harness.reservation.write_bytes(raw)
                result = harness.run(
                    f"[IO.File]::WriteAllText('{harness.get_marker.as_posix()}','called'); '{SECRET}'",
                    "",
                )
                self.assertEqual(result.returncode, 47)
                self.assertFalse(harness.get_marker.exists())
                self.assertFalse(harness.ledger.exists())
                self.assertEqual(harness.reservation.read_bytes(), raw)

    def test_reservation_parent_failure_is_exit40_before_clipboard(self) -> None:
        harness = WrapperHarness(self)
        harness.control.rmdir()
        result = harness.run(
            f"[IO.File]::WriteAllText('{harness.get_marker.as_posix()}','called'); '{SECRET}'",
            "",
        )
        self.assertEqual(result.returncode, 40)
        self.assertFalse(harness.get_marker.exists())
        self.assertFalse(harness.ledger.exists())

    def test_create_new_two_process_competition_has_one_winner_and_one_47(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell unavailable")
        harness = WrapperHarness(self, child_exit=0)
        first_marker = harness.repo / "get_first.marker"
        second_marker = harness.repo / "get_second.marker"
        first = harness.write_host(
            f"[IO.File]::WriteAllText('{first_marker.as_posix()}','1'); Start-Sleep -Milliseconds 200; '{SECRET}'",
            "", name="first.ps1",
        )
        second = harness.write_host(
            f"[IO.File]::WriteAllText('{second_marker.as_posix()}','1'); '{SECRET}'",
            "", name="second.ps1",
        )
        processes = [
            subprocess.Popen(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(host)],
                cwd=harness.repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for host in (first, second)
        ]
        results = [(process.wait(timeout=30), *process.communicate()) for process in processes]
        self.assertEqual({item[0] for item in results}, {0, 47})
        self.assertTrue(all(item[1:] == ("", "") for item in results))
        self.assertEqual(sum(path.exists() for path in (first_marker, second_marker)), 1)
        validate_complete_stage_ledger(harness.ledger.read_bytes())

    def test_stage_ledger_rejects_partial_duplicate_noncanonical_and_bad_matrix(self) -> None:
        harness = WrapperHarness(self, child_exit=0)
        self.assertEqual(harness.run(f"'{SECRET}'", "").returncode, 0)
        raw = harness.ledger.read_bytes()
        validate_complete_stage_ledger(raw)
        bad_values = (
            raw[:-1],
            raw.replace(b'{"event"', b'{ "event"', 1),
            raw.replace(b'"seq":1', b'"seq":2', 1),
            raw + raw.splitlines(keepends=True)[-1],
            raw.replace(b'"stage":"SELF_HASH"', b'"stage":"UNKNOWN"', 1),
            raw.replace(b'"event":"PASS","exit_code":null', b'"event":"FAIL","exit_code":41', 1),
        )
        for candidate in bad_values:
            with self.subTest(candidate=candidate[:40]):
                with self.assertRaises((ValueError, collector.CollectorError)):
                    validate_complete_stage_ledger(candidate)

    def test_native_exit_folds_without_propagation_and_is_exact_with_propagation(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell unavailable")
        child = "powershell.exe -NoLogo -NoProfile -NonInteractive -Command \"exit 35\""
        plain = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", child],
            capture_output=True, text=True, timeout=30, check=False,
        )
        propagated = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", child + "; $nativeExitCode=$LASTEXITCODE; exit $nativeExitCode"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual((plain.returncode, propagated.returncode), (1, 35))
        self.assertEqual((plain.stdout, plain.stderr, propagated.stdout, propagated.stderr), ("", "", "", ""))

    def test_exp008_to_exp009_data_semantics_are_value_equal(self) -> None:
        self.assertEqual(
            [(x.ordinal, x.endpoint_id, x.url, x.api_key_header, x.body_cap) for x in collector.ENDPOINTS],
            [(x.ordinal, x.endpoint_id, x.url, x.api_key_header, x.body_cap) for x in exp008_collector.ENDPOINTS],
        )
        self.assertEqual(
            (collector.MAX_TOTAL_BODY_BYTES, collector.REQUEST_TIMEOUT_SECONDS, collector.TOTAL_WALL_SECONDS),
            (exp008_collector.MAX_TOTAL_BODY_BYTES, exp008_collector.REQUEST_TIMEOUT_SECONDS, exp008_collector.TOTAL_WALL_SECONDS),
        )
        bodies = dict(zip((item.endpoint_id for item in collector.ENDPOINTS), response_bodies(), strict=True))
        self.assertEqual(collector.derive_rows(bodies), exp008_collector.derive_rows(bodies))

    def test_exp008_to_exp009_sources_differ_only_by_identity_and_outer_exit(self) -> None:
        old_collector = (SRC / "binance_spot_forward_schedule_pit_v1.py").read_text(encoding="utf-8")
        new_collector = (SRC / "binance_spot_forward_schedule_pit_v2.py").read_text(encoding="utf-8")
        normalized_collector = (
            new_collector
            .replace("exp_20260826_009", "exp_20260826_008")
            .replace("binance_spot_forward_schedule_pit_v2", "binance_spot_forward_schedule_pit_v1")
            .replace(
                'return " ".join(parts) + "; $nativeExitCode=$LASTEXITCODE; exit $nativeExitCode"',
                'return " ".join(parts)',
            )
        )
        self.assertEqual(normalized_collector, old_collector)

        old_loader = (SRC / "binance_spot_forward_schedule_pit_v1_loader.py").read_text(encoding="utf-8")
        new_loader = (SRC / "binance_spot_forward_schedule_pit_v2_loader.py").read_text(encoding="utf-8")
        normalized_loader = (
            new_loader
            .replace("exp_20260826_009", "exp_20260826_008")
            .replace("binance_spot_forward_schedule_pit_v2", "binance_spot_forward_schedule_pit_v1")
            .replace(
                'command = " ".join(parts) + "; $nativeExitCode=$LASTEXITCODE; exit $nativeExitCode"\n'
                '    return _sha(command.encode("utf-8"))',
                'return _sha(" ".join(parts).encode("utf-8"))',
            )
        )
        self.assertEqual(normalized_loader, old_loader)

    def test_formal_command_contains_no_key_and_matches_seven_bindings(self) -> None:
        command = collector.formal_command(self.layout.bindings())
        self.assertNotIn(SECRET, command)
        self.assertEqual(command.count("-Expected"), 7)
        self.assertTrue(command.startswith("powershell.exe -NoLogo -NoProfile -NonInteractive"))
        self.assertTrue(command.endswith("; $nativeExitCode=$LASTEXITCODE; exit $nativeExitCode"))

    def test_real_workspace_formal_paths_absent_and_network_zero(self) -> None:
        root = ROOT / f"data/raw/{collector.VERSION}/runs"
        self.assertFalse((root / collector.RUN_ID).exists())
        self.assertFalse((root / f".{collector.RUN_ID}.staging").exists())
        self.assertFalse((root / f".{collector.RUN_ID}.control").exists())
        wrapper_control = ROOT / "experiments/exp_20260826_009/formal_control"
        self.assertTrue(wrapper_control.is_dir())
        self.assertFalse((wrapper_control / f"{collector.RUN_ID}.reservation.lock").exists())
        self.assertFalse((wrapper_control / f"{collector.RUN_ID}.stage_ledger.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
