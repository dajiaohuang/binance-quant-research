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

import binance_spot_forward_schedule_pit_v6 as collector
import binance_spot_forward_schedule_pit_v6_loader as loader
import binance_spot_forward_schedule_pit_v5 as parent_collector


SECRET = "SENTINELEXP003_READ-ONLY_KEY9F6C"

LEDGER_STAGES = {
    "SELF_HASH", "ENV_FILE_READ", "VALIDATE", "HANDOFF", "COLLECTOR",
    "FINAL_CLEANUP",
}
LEDGER_EVENTS = {"START", "PASS", "FAIL", "EXIT"}
LEDGER_FAIL_CODES = {
    "ENV_FILE_READ": 42,
    "VALIDATE": 43,
    "HANDOFF": 44,
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
    take("ENV_FILE_READ", "START")
    read = rows[position] if position < len(rows) else None
    if read is None:
        raise ValueError("ledger incomplete")
    if read["event"] == "FAIL":
        take("ENV_FILE_READ", "FAIL")
    else:
        take("ENV_FILE_READ", "PASS")
        validation = rows[position] if position < len(rows) else None
        if validation is None:
            raise ValueError("ledger incomplete")
        if validation["event"] == "FAIL":
            take("VALIDATE", "FAIL")
        else:
            take("VALIDATE", "PASS")
            handoff = rows[position] if position < len(rows) else None
            if handoff is None:
                raise ValueError("ledger incomplete")
            if handoff["event"] == "FAIL":
                take("HANDOFF", "FAIL")
            else:
                take("HANDOFF", "PASS")
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
        self.wrapper = self.repo / "src/quant_research/binance_spot_forward_schedule_pit_v6_wrapper.ps1"
        self.wrapper.parent.mkdir(parents=True)
        self.wrapper.write_bytes((ROOT / collector.BINDING_SPECS[0][2]).read_bytes())
        self.control = self.repo / "experiments/exp_20260827_003/formal_control"
        self.control.mkdir(parents=True)
        self.reservation = self.control / f"{collector.RUN_ID}.reservation.lock"
        self.ledger = self.control / f"{collector.RUN_ID}.stage_ledger.jsonl"
        self.env_file = self.repo / ".env.binance.local"
        self.env_file.write_bytes(
            b"# Binance read-only API key; local file, never commit\n"
            + b"BINANCE_READ_ONLY_API_KEY=" + SECRET.encode("ascii") + b"\n"
        )
        self.child_marker = self.repo / "collector.marker"
        if include_python:
            python_path = self.repo / ".venv/Scripts/python.exe"
            python_path.parent.mkdir(parents=True)
            shutil.copy2(sys.executable, python_path)
            fake = self.repo / "src/quant_research/binance_spot_forward_schedule_pit_v6.py"
            fake.write_text(
                "import os, pathlib\n"
                f"pathlib.Path({str(self.child_marker)!r}).write_text(" 
                "'present' if os.environ.get('BINANCE_READ_ONLY_API_KEY') == "
                f"{SECRET!r} else 'missing', encoding='utf-8')\n"
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

    def write_host(
        self, *, name: str = "host.ps1", wrapper_sha: str | None = None,
        prefix: str = "", suffix: str = "",
    ) -> pathlib.Path:
        host = self.repo / name
        host.write_text(
            "$ErrorActionPreference='Stop'\n"
            f"{prefix}\n"
            f"& '{self.wrapper.as_posix()}' {self.flags(wrapper_sha=wrapper_sha or self.wrapper_sha)}\n"
            "$wrapperExitCode=$LASTEXITCODE\n"
            f"{suffix}\n"
            "exit $wrapperExitCode\n",
            encoding="utf-8",
        )
        return host

    def run(
        self, *, wrapper_sha: str | None = None, prefix: str = "",
        suffix: str = "", environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.case.skipTest("PowerShell unavailable")
        host = self.write_host(
            wrapper_sha=wrapper_sha, prefix=prefix, suffix=suffix,
        )
        return subprocess.run(
            [
                powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(host),
            ],
            cwd=self.repo, capture_output=True, text=True, timeout=30,
            check=False, env=environment,
        )


class LauncherHarness:
    def __init__(self, case: unittest.TestCase, *, child_exit: int = 0) -> None:
        self.case = case
        self.temp = tempfile.TemporaryDirectory()
        case.addCleanup(self.temp.cleanup)
        self.repo = pathlib.Path(self.temp.name) / "repo"
        self.launcher = self.repo / "src/quant_research/binance_spot_forward_schedule_pit_v6_launcher.ps1"
        self.wrapper = self.repo / "src/quant_research/binance_spot_forward_schedule_pit_v6_wrapper.ps1"
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_bytes(
            (SRC / "binance_spot_forward_schedule_pit_v6_launcher.ps1").read_bytes()
        )
        self.marker = self.repo / "launcher-call.marker"
        self.argv = self.repo / "launcher-argv.txt"
        self.write_fake_wrapper(child_exit)

    def write_fake_wrapper(self, child_exit: int) -> None:
        self.wrapper.write_text(
            "param(\n"
            "[string]$ExpectedWrapperSha256,\n"
            "[string]$ExpectedCollectorSha256,\n"
            "[string]$ExpectedLoaderSha256,\n"
            "[string]$ExpectedSourceContractSha256,\n"
            "[string]$ExpectedSchemaSha256,\n"
            "[string]$ExpectedParametersSha256,\n"
            "[string]$ExpectedTestsSha256\n"
            ")\n"
            "$utf8NoBom=New-Object System.Text.UTF8Encoding($false)\n"
            f'[IO.File]::AppendAllText({str(self.marker)!r},"1`n",$utf8NoBom)\n'
            "$captured=@($PSCommandPath,$ExpectedWrapperSha256,"
            "$ExpectedCollectorSha256,$ExpectedLoaderSha256,"
            "$ExpectedSourceContractSha256,$ExpectedSchemaSha256,"
            "$ExpectedParametersSha256,$ExpectedTestsSha256)\n"
            f"[IO.File]::WriteAllLines({str(self.argv)!r},$captured,$utf8NoBom)\n"
            f"exit {child_exit}\n",
            encoding="utf-8",
        )

    def launcher_sha(self) -> str:
        return hashlib.sha256(self.launcher.read_bytes()).hexdigest()

    def run(self, *, expected_sha: str | None = None) -> subprocess.CompletedProcess[str]:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.case.skipTest("PowerShell unavailable")
        return subprocess.run(
            [
                powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-File", str(self.launcher),
                "-ExpectedLauncherSha256", expected_sha or self.launcher_sha(),
            ],
            cwd=self.repo, capture_output=True, text=True, timeout=30,
            check=False,
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

    def test_wrapper_valid_env_grammar_handoff_and_secret_nonpersistence(self) -> None:
        variants = (
            b"BINANCE_READ_ONLY_API_KEY=" + SECRET.encode("ascii"),
            b"BINANCE_READ_ONLY_API_KEY=" + SECRET.encode("ascii") + b"\n",
            b"\xef\xbb\xbfBINANCE_READ_ONLY_API_KEY=" + SECRET.encode("ascii") + b"\n",
            b"\t# printable comment\tABC_-=\n\n"
            b"BINANCE_READ_ONLY_API_KEY \t=\t " + SECRET.encode("ascii") + b" \t\n",
            b"# first\r\n\t\r\n"
            b"BINANCE_READ_ONLY_API_KEY\t=\t" + SECRET.encode("ascii") + b"\r\n"
            b"  # last\r\n",
            b"# LF first\nBINANCE_READ_ONLY_API_KEY=" + SECRET.encode("ascii") + b"\r\n",
            b"# CRLF first\r\nBINANCE_READ_ONLY_API_KEY=" + SECRET.encode("ascii") + b"\n",
            b"\xef\xbb\xbf# comment\r\n"
            b"BINANCE_READ_ONLY_API_KEY=" + SECRET.encode("ascii"),
        )
        expected = [
            ("SELF_HASH", "PASS", None),
            ("ENV_FILE_READ", "START", None),
            ("ENV_FILE_READ", "PASS", None),
            ("VALIDATE", "PASS", None),
            ("HANDOFF", "PASS", None),
            ("COLLECTOR", "START", None),
            ("COLLECTOR", "EXIT", 0),
            ("FINAL_CLEANUP", "PASS", None),
        ]
        for raw in variants:
            with self.subTest(raw=raw[:20]):
                harness = WrapperHarness(self)
                harness.env_file.write_bytes(raw)
                result = harness.run()
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
                self.assertEqual(harness.child_marker.read_text(encoding="utf-8"), "present")
                rows = validate_complete_stage_ledger(harness.ledger.read_bytes())
                self.assertEqual(
                    [(row["stage"], row["event"], row["exit_code"]) for row in rows],
                    expected,
                )
                evidence = harness.reservation.read_bytes() + harness.ledger.read_bytes()
                self.assertNotIn(SECRET.encode(), evidence)
                self.assertNotIn(b"API_KEY", evidence)
                self.assertEqual(harness.env_file.read_bytes(), raw)

    def test_wrapper_invalid_env_byte_grammar_is_43_and_zero_child(self) -> None:
        invalid = (
            b"", b"\xef\xbb\xbf", b"\xef\xbb\xbf\xef\xbb\xbfBINANCE_READ_ONLY_API_KEY=ABC",
            b"# no assignment\n", b"BINANCE_READ_ONLY_API_KEY=ABC\r",
            b"BINANCE_READ_ONLY_API_KEY=ABC\x00", b"# invalid\xff\nBINANCE_READ_ONLY_API_KEY=ABC",
            b"# caf\xc3\xa9\nBINANCE_READ_ONLY_API_KEY=ABC",
            b"BINANCE_READ_ONLY_API_KEY=\xc3\x89",
            b"BINANCE_READ_ONLY_API_KEY=ABC DEF",
            b"BINANCE_READ_ONLY_API_KEY='ABC'",
            b'BINANCE_READ_ONLY_API_KEY="ABC"',
            b"BINANCE_READ_ONLY_API_KEY=${ABC}",
            b" BINANCE_READ_ONLY_API_KEY=ABC",
            b"binance_read_only_api_key=ABC",
            b"BINANCE_READ_ONLY_API_KEY=ABC#inline",
            b"BINANCE_READ_ONLY_API_KEY=ABC=DEF",
            b"BINANCE_READ_ONLY_API_KEY=ABC/DEF",
            b"EXTRA=1\nBINANCE_READ_ONLY_API_KEY=ABC",
            b"BINANCE_READ_ONLY_API_KEY=ABC\nEXTRA=1\n",
            b"BINANCE_READ_ONLY_API_KEY=ABC\nBINANCE_READ_ONLY_API_KEY=DEF\n",
            b"# bad\x0bcomment\nBINANCE_READ_ONLY_API_KEY=ABC\n",
            b"X\xef\xbb\xbf\nBINANCE_READ_ONLY_API_KEY=ABC\n",
        )
        for raw in invalid:
            with self.subTest(raw=raw[:30]):
                harness = WrapperHarness(self)
                harness.env_file.write_bytes(raw)
                result = harness.run()
                self.assertEqual((result.returncode, result.stdout, result.stderr), (43, "", ""))
                self.assertFalse(harness.child_marker.exists())
                rows = validate_complete_stage_ledger(harness.ledger.read_bytes())
                self.assertEqual(
                    (rows[-2]["stage"], rows[-2]["event"], rows[-2]["exit_code"]),
                    ("VALIDATE", "FAIL", 43),
                )

    def test_wrapper_env_file_read_contract_missing_directory_cap_and_reparse(self) -> None:
        cases: list[tuple[str, object]] = []
        missing = WrapperHarness(self)
        missing.env_file.unlink()
        cases.append(("missing", missing))
        directory = WrapperHarness(self)
        directory.env_file.unlink()
        directory.env_file.mkdir()
        cases.append(("directory", directory))
        oversize = WrapperHarness(self)
        oversize.env_file.write_bytes(SECRET.encode("ascii") + b"A" * (4097 - len(SECRET)))
        cases.append(("oversize", oversize))
        for name, raw_harness in cases:
            harness = raw_harness
            with self.subTest(name=name):
                result = harness.run()
                self.assertEqual((result.returncode, result.stdout, result.stderr), (42, "", ""))
                self.assertFalse(harness.child_marker.exists())
                rows = validate_complete_stage_ledger(harness.ledger.read_bytes())
                self.assertEqual((rows[-2]["stage"], rows[-2]["event"]), ("ENV_FILE_READ", "FAIL"))
                evidence = harness.reservation.read_bytes() + harness.ledger.read_bytes()
                self.assertNotIn(SECRET.encode(), evidence)
                self.assertNotIn(SECRET, result.stdout + result.stderr)

        linked = WrapperHarness(self)
        target = linked.repo / "synthetic-secret-file"
        target.write_bytes(b"BINANCE_READ_ONLY_API_KEY=ABC")
        linked.env_file.unlink()
        try:
            linked.env_file.symlink_to(target)
        except OSError:
            wrapper_text = (ROOT / collector.BINDING_SPECS[0][2]).read_text(encoding="utf-8")
            self.assertIn("FileAttributes]::ReparsePoint", wrapper_text)
            return
        result = linked.run()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (42, "", ""))
        self.assertFalse(linked.child_marker.exists())

    def test_wrapper_partial_read_exception_is_42_and_buffer_cleanup_is_frozen(self) -> None:
        harness = WrapperHarness(self)
        source = harness.wrapper.read_text(encoding="utf-8")
        needle = "$readNow = $envStream.Read($buffer, $totalRead, 4097 - $totalRead)"
        replacement = (
            "if ($totalRead -gt 0) { "
            "throw [System.IO.IOException]::new('synthetic partial read') }\n"
            "            " + needle
        )
        self.assertEqual(source.count(needle), 1)
        harness.wrapper.write_text(source.replace(needle, replacement), encoding="utf-8")
        harness.wrapper_sha = hashlib.sha256(harness.wrapper.read_bytes()).hexdigest()
        result = harness.run()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (42, "", ""))
        self.assertFalse(harness.child_marker.exists())
        rows = validate_complete_stage_ledger(harness.ledger.read_bytes())
        self.assertEqual((rows[-2]["stage"], rows[-2]["event"]), ("ENV_FILE_READ", "FAIL"))
        evidence = harness.reservation.read_bytes() + harness.ledger.read_bytes()
        self.assertNotIn(SECRET.encode(), evidence)

        frozen = (ROOT / collector.BINDING_SPECS[0][2]).read_text(encoding="utf-8")
        self.assertIn("$buffer = $null", frozen)
        self.assertIn("$lineValue = $null", frozen)
        self.assertIn("'keyMatch', 'lineValue', 'buffer'", frozen)

    def test_wrapper_validation_faults_clear_every_secret_bearing_representation(self) -> None:
        seams = (
            ("before_decode", "$textValue = $utf8Strict.GetString($rawBytes)", False),
            ("after_decode", "$textValue = $utf8Strict.GetString($rawBytes)", True),
            ("before_line_classify", "$lineValue = $textValue.Substring($lineStart, $cursor - $lineStart)", False),
            ("after_line_classify", "$lineValue = $textValue.Substring($lineStart, $cursor - $lineStart)", True),
            ("before_capture", "$keyValue = $keyMatch.Groups[1].Value", False),
            ("after_capture", "$keyValue = $keyMatch.Groups[1].Value", True),
        )
        for name, needle, after in seams:
            with self.subTest(name=name):
                harness = WrapperHarness(self)
                source = harness.wrapper.read_text(encoding="utf-8")
                self.assertEqual(source.count(needle), 1)
                fault = "throw [System.IO.InvalidDataException]::new('synthetic validation fault')"
                replacement = (needle + "\n            " + fault) if after else (fault + "\n            " + needle)
                harness.wrapper.write_text(source.replace(needle, replacement), encoding="utf-8")
                harness.wrapper_sha = hashlib.sha256(harness.wrapper.read_bytes()).hexdigest()
                result = harness.run()
                self.assertEqual((result.returncode, result.stdout, result.stderr), (43, "", ""))
                self.assertFalse(harness.child_marker.exists())
                rows = validate_complete_stage_ledger(harness.ledger.read_bytes())
                self.assertEqual((rows[-2]["stage"], rows[-2]["event"]), ("VALIDATE", "FAIL"))
                evidence = harness.reservation.read_bytes() + harness.ledger.read_bytes()
                self.assertNotIn(SECRET.encode(), evidence)

    def test_wrapper_preexisting_environment_is_presence_only_handoff_44(self) -> None:
        harness = WrapperHarness(self)
        environment = os.environ.copy()
        environment[collector.API_KEY_ENV] = "PREEXISTINGMUSTNOTBEREAD"
        result = harness.run(environment=environment)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (44, "", ""))
        self.assertFalse(harness.child_marker.exists())
        rows = validate_complete_stage_ledger(harness.ledger.read_bytes())
        self.assertEqual((rows[-2]["stage"], rows[-2]["event"]), ("HANDOFF", "FAIL"))
        self.assertNotIn(b"PREEXISTINGMUSTNOTBEREAD", harness.ledger.read_bytes())

    def test_wrapper_hash_reservation_and_parent_fail_before_env_read(self) -> None:
        drift = WrapperHarness(self)
        drift.env_file.unlink()
        result = drift.run(wrapper_sha="0" * 64)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (41, "", ""))
        self.assertFalse(drift.reservation.exists())
        self.assertFalse(drift.ledger.exists())

        for raw in (b"", b"partial"):
            with self.subTest(reservation=raw):
                existing = WrapperHarness(self)
                existing.env_file.unlink()
                existing.reservation.write_bytes(raw)
                result = existing.run()
                self.assertEqual((result.returncode, result.stdout, result.stderr), (47, "", ""))
                self.assertEqual(existing.reservation.read_bytes(), raw)
                self.assertFalse(existing.ledger.exists())

        missing_parent = WrapperHarness(self)
        missing_parent.env_file.unlink()
        missing_parent.control.rmdir()
        result = missing_parent.run()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (40, "", ""))
        self.assertFalse(missing_parent.reservation.exists())

    def test_wrapper_allowed_unknown_launch_and_cleanup_exit_codes(self) -> None:
        for code in sorted(ALLOWED_CHILD_CODES):
            with self.subTest(code=code):
                harness = WrapperHarness(self, child_exit=code)
                result = harness.run()
                self.assertEqual((result.returncode, result.stdout, result.stderr), (code, "", ""))
                rows = validate_complete_stage_ledger(harness.ledger.read_bytes())
                self.assertEqual((rows[-2]["event"], rows[-2]["exit_code"]), ("EXIT", code))

        unknown = WrapperHarness(self, child_exit=99)
        result = unknown.run()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (45, "", ""))
        self.assertEqual(validate_complete_stage_ledger(unknown.ledger.read_bytes())[-2]["event"], "FAIL")

        launch = WrapperHarness(self, include_python=False)
        result = launch.run()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (45, "", ""))
        validate_complete_stage_ledger(launch.ledger.read_bytes())

        cleanup = WrapperHarness(self, child_exit=0)
        prefix = (
            "function global:Remove-Item { [CmdletBinding()] param(" 
            "[Parameter(Position=0)][string]$Path) throw 'synthetic cleanup' }"
        )
        result = cleanup.run(prefix=prefix)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (46, "", ""))
        rows = validate_complete_stage_ledger(cleanup.ledger.read_bytes())
        self.assertEqual((rows[-1]["stage"], rows[-1]["event"]), ("FINAL_CLEANUP", "FAIL"))

    def test_wrapper_static_order_single_read_and_no_secret_derived_evidence(self) -> None:
        text = (ROOT / collector.BINDING_SPECS[0][2]).read_text(encoding="utf-8")
        self.assertLess(text.index("ReadAllBytes($PSCommandPath)"), text.index("FileMode]::CreateNew"))
        self.assertLess(text.index("FileMode]::CreateNew"), text.index("ENV_FILE_READ' -Event 'START"))
        self.assertLess(text.index("ENV_FILE_READ' -Event 'START"), text.index("FileMode]::Open"))
        self.assertLess(text.index("FileMode]::Open"), text.index("$env:BINANCE_READ_ONLY_API_KEY = $keyValue"))
        self.assertIn("New-Object byte[] 4097", text)
        self.assertIn("FileShare]::Read", text)
        self.assertIn("FileAttributes]::ReparsePoint", text)
        self.assertIn("if ($envOwned)", text)
        self.assertNotIn("Get-Clipboard", text)
        self.assertNotIn("Set-Clipboard", text)
        self.assertNotIn("Regex]::Matches", text)
        self.assertNotIn("MatchCollection", text)
        self.assertNotIn(" -split ", text)
        self.assertIn("$rawBytes = $null", text)
        self.assertIn("$textValue = $null", text)
        self.assertIn("$keyValue = $null", text)
        self.assertIn("$keyMatch = $null", text)
        self.assertIn("$lineValue = $null", text)
        self.assertIn("'rawBytes', 'textValue', 'keyValue', 'keyMatch', 'lineValue', 'buffer'", text)
        self.assertNotIn(SECRET, text)
        self.assertNotRegex(text, r"key(Value)?.*(Length|SHA|Hash)")

    def test_stage_ledger_strict_canonical_matrix(self) -> None:
        harness = WrapperHarness(self)
        self.assertEqual(harness.run().returncode, 0)
        raw = harness.ledger.read_bytes()
        validate_complete_stage_ledger(raw)
        bad_values = (
            raw[:-1], raw.replace(b'{"event"', b'{ "event"', 1),
            raw.replace(b'"seq":1', b'"seq":2', 1),
            raw + raw.splitlines(keepends=True)[-1],
            raw.replace(b'"stage":"SELF_HASH"', b'"stage":"UNKNOWN"', 1),
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

    def test_exp002_to_exp003_data_semantics_are_value_equal(self) -> None:
        self.assertEqual(
            [(x.ordinal, x.endpoint_id, x.url, x.api_key_header, x.body_cap) for x in collector.ENDPOINTS],
            [(x.ordinal, x.endpoint_id, x.url, x.api_key_header, x.body_cap) for x in parent_collector.ENDPOINTS],
        )
        self.assertEqual(
            (collector.MAX_TOTAL_BODY_BYTES, collector.REQUEST_TIMEOUT_SECONDS, collector.TOTAL_WALL_SECONDS),
            (parent_collector.MAX_TOTAL_BODY_BYTES, parent_collector.REQUEST_TIMEOUT_SECONDS, parent_collector.TOTAL_WALL_SECONDS),
        )
        bodies = dict(zip((item.endpoint_id for item in collector.ENDPOINTS), response_bodies(), strict=True))
        self.assertEqual(collector.derive_rows(bodies), parent_collector.derive_rows(bodies))

    def test_exp002_to_exp003_sources_differ_only_by_identity(self) -> None:
        old_collector = (SRC / "binance_spot_forward_schedule_pit_v5.py").read_text(encoding="utf-8")
        new_collector = (SRC / "binance_spot_forward_schedule_pit_v6.py").read_text(encoding="utf-8")
        normalized_collector = (
            new_collector
            .replace("exp_20260827_003", "exp_20260827_002")
            .replace("binance_spot_forward_schedule_pit_v6", "binance_spot_forward_schedule_pit_v5")
        )
        self.assertEqual(normalized_collector, old_collector)

        old_loader = (SRC / "binance_spot_forward_schedule_pit_v5_loader.py").read_text(encoding="utf-8")
        new_loader = (SRC / "binance_spot_forward_schedule_pit_v6_loader.py").read_text(encoding="utf-8")
        normalized_loader = (
            new_loader
            .replace("exp_20260827_003", "exp_20260827_002")
            .replace("binance_spot_forward_schedule_pit_v6", "binance_spot_forward_schedule_pit_v5")
        )
        self.assertEqual(normalized_loader, old_loader)

        old_wrapper = (SRC / "binance_spot_forward_schedule_pit_v5_wrapper.ps1").read_text(encoding="utf-8")
        new_wrapper = (SRC / "binance_spot_forward_schedule_pit_v6_wrapper.ps1").read_text(encoding="utf-8")
        normalized_wrapper = (
            new_wrapper
            .replace("exp_20260827_003", "exp_20260827_002")
            .replace("binance_spot_forward_schedule_pit_v6", "binance_spot_forward_schedule_pit_v5")
        )
        self.assertEqual(normalized_wrapper, old_wrapper)

    def test_launcher_static_contract_has_one_literal_native_call(self) -> None:
        text = (SRC / "binance_spot_forward_schedule_pit_v6_launcher.ps1").read_text(
            encoding="utf-8"
        )
        self.assertEqual(text.count("& $nativePowerShell"), 1)
        self.assertIn("Join-Path $PSHOME 'powershell.exe'", text)
        self.assertIn("binance_spot_forward_schedule_pit_v6_wrapper.ps1", text)
        self.assertNotIn("commands.txt", text.lower())
        self.assertNotIn("Get-Content", text)
        self.assertNotIn("ReadAllLines", text)
        self.assertNotIn("Invoke-Expression", text)
        self.assertNotIn("ScriptBlock", text)
        self.assertNotIn("$env:", text)
        self.assertNotIn("|", text)
        self.assertNotRegex(text, r"\$[A-Za-z_][A-Za-z0-9_]*\[[0-9]+\]")
        self.assertNotIn(". ", text)

    def test_launcher_exact_single_call_and_argv_tokens(self) -> None:
        harness = LauncherHarness(self, child_exit=0)
        result = harness.run()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual(harness.marker.read_text(encoding="utf-8"), "1\n")
        actual_bindings = [
            hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for _flag, _name, path in collector.BINDING_SPECS
        ]
        captured = harness.argv.read_text(encoding="utf-8").splitlines()
        self.assertEqual(captured, [str(harness.wrapper.resolve()), *actual_bindings])

    def test_launcher_passes_every_allowed_child_code_exactly(self) -> None:
        for child_exit in (0, 10, 11, 20, 24, *range(30, 36), *range(40, 48)):
            with self.subTest(child_exit=child_exit):
                harness = LauncherHarness(self, child_exit=child_exit)
                result = harness.run()
                self.assertEqual(
                    (result.returncode, result.stdout, result.stderr),
                    (child_exit, "", ""),
                )
                self.assertEqual(harness.marker.read_text(encoding="utf-8"), "1\n")

    def test_launcher_self_hash_mismatch_is_48_before_wrapper(self) -> None:
        harness = LauncherHarness(self, child_exit=0)
        result = harness.run(expected_sha="0" * 64)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (48, "", ""))
        self.assertFalse(harness.marker.exists())

    def test_launcher_fixed_path_and_reparse_failures_are_49(self) -> None:
        missing = LauncherHarness(self, child_exit=0)
        missing.wrapper.unlink()
        result = missing.run()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (49, "", ""))
        self.assertFalse(missing.marker.exists())

        linked = LauncherHarness(self, child_exit=0)
        target = linked.repo / "synthetic-wrapper-target.ps1"
        target.write_text("exit 0\n", encoding="utf-8")
        linked.wrapper.unlink()
        try:
            linked.wrapper.symlink_to(target)
        except OSError:
            source = linked.launcher.read_text(encoding="utf-8")
            self.assertIn("FileAttributes]::ReparsePoint", source)
        else:
            result = linked.run()
            self.assertEqual((result.returncode, result.stdout, result.stderr), (49, "", ""))
            self.assertFalse(linked.marker.exists())

    def test_launcher_unknown_and_synthetic_null_child_are_50(self) -> None:
        unknown = LauncherHarness(self, child_exit=99)
        result = unknown.run()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (50, "", ""))
        self.assertEqual(unknown.marker.read_text(encoding="utf-8"), "1\n")

        null_child = LauncherHarness(self, child_exit=0)
        source = null_child.launcher.read_text(encoding="utf-8")
        needle = "$childExitCode = $LASTEXITCODE"
        self.assertEqual(source.count(needle), 1)
        null_child.launcher.write_text(
            source.replace(needle, "$childExitCode = $null"), encoding="utf-8"
        )
        result = null_child.run()
        self.assertEqual((result.returncode, result.stdout, result.stderr), (50, "", ""))
        self.assertEqual(null_child.marker.read_text(encoding="utf-8"), "1\n")

    def test_formal_command_is_direct_launcher_only_and_manifest_has_eight_bindings(self) -> None:
        launcher_path = SRC / "binance_spot_forward_schedule_pit_v6_launcher.ps1"
        launcher_sha = hashlib.sha256(launcher_path.read_bytes()).hexdigest()
        expected = (
            "powershell.exe -NoLogo -NoProfile -NonInteractive "
            "-ExecutionPolicy Bypass -File "
            "src\\quant_research\\binance_spot_forward_schedule_pit_v6_launcher.ps1 "
            f"-ExpectedLauncherSha256 {launcher_sha}; "
            "$nativeExitCode=$LASTEXITCODE; exit $nativeExitCode"
        )
        lines = (ROOT / "experiments/exp_20260827_003/commands.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        formal = [line for line in lines if line.startswith("powershell.exe ") and " -File src\\quant_research\\binance_spot_forward_schedule_pit_v6_launcher.ps1 " in line]
        self.assertEqual(formal, [expected])
        self.assertEqual(expected.count("-Expected"), 1)
        self.assertNotIn(SECRET, expected)

        manifest = collector.strict_json(
            (ROOT / "experiments/exp_20260827_003/manifest.json").read_bytes()
        )
        self.assertEqual(set(manifest["code_bindings"]), {
            "launcher", "wrapper", "collector", "loader", "source_contract",
            "schema", "parameters", "tests",
        })
        self.assertEqual(manifest["code_bindings"]["launcher"]["sha256"], launcher_sha)
        self.assertEqual(len(collector.BINDING_SPECS), 7)

    def test_real_workspace_formal_paths_absent_and_network_zero(self) -> None:
        root = ROOT / f"data/raw/{collector.VERSION}/runs"
        self.assertFalse((root / collector.RUN_ID).exists())
        self.assertFalse((root / f".{collector.RUN_ID}.staging").exists())
        self.assertFalse((root / f".{collector.RUN_ID}.control").exists())
        wrapper_control = ROOT / "experiments/exp_20260827_003/formal_control"
        self.assertTrue(wrapper_control.is_dir())
        self.assertFalse((wrapper_control / f"{collector.RUN_ID}.reservation.lock").exists())
        self.assertFalse((wrapper_control / f"{collector.RUN_ID}.stage_ledger.jsonl").exists())
        env_file = ROOT / ".env.binance.local"
        self.assertTrue(env_file.exists())
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--", ".env.binance.local"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", ".env.binance.local"],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(ignored.returncode, 0)
        self.assertNotEqual(tracked.returncode, 0)
        self.assertNotIn(collector.API_KEY_ENV, os.environ)


if __name__ == "__main__":
    unittest.main()
