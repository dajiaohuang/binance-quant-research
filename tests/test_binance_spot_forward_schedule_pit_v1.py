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

import binance_spot_forward_schedule_pit_v1 as collector
import binance_spot_forward_schedule_pit_v1_loader as loader


SECRET = "SENTINEL_EXP008_READ_ONLY_KEY_9f6c"


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
        self.assertLess(text.index("$PSCommandPath"), text.index("Get-Clipboard -Raw"))
        self.assertLess(text.index("$savedExitCode = 11"), text.index("Get-Clipboard -Raw"))
        self.assertLess(text.index("Get-Clipboard -Raw"), text.index("Set-Clipboard -Value ''"))
        self.assertLess(text.index("Set-Clipboard -Value ''"), text.index("$env:BINANCE_READ_ONLY_API_KEY"))
        self.assertLess(text.index("$savedExitCode = $LASTEXITCODE"), text.rindex("finally"))
        self.assertIn("Remove-Item Env:BINANCE_READ_ONLY_API_KEY", text)
        self.assertIn("Set-Clipboard -Value ''", text)
        self.assertIn("Clear-Variable -Name rawClipboard", text)
        self.assertIn("$cleanupFailed -and $savedExitCode -eq 0", text)
        self.assertIn("$savedExitCode = 12", text)
        self.assertTrue(text.rstrip().endswith("exit $savedExitCode"))
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
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell unavailable")
        wrapper = ROOT / collector.BINDING_SPECS[0][2]
        zero = "0" * 64
        wrapper_sha = hashlib.sha256(wrapper.read_bytes()).hexdigest()
        clear_marker = pathlib.Path(self.layout.temp.name) / "clipboard_clear_calls.txt"
        command = (
            "function global:Get-Clipboard { param([switch]$Raw) '"
            + SECRET
            + "' };"
            "function global:Set-Clipboard { param([string]$Value) "
            "if (($Value -eq '') -and (-not (Test-Path Env:BINANCE_READ_ONLY_API_KEY))) "
            f"{{ [IO.File]::AppendAllText('{clear_marker.as_posix()}', \"CLEAR`n\") }} }};"
            f"& '{wrapper.as_posix()}' "
            + " ".join(
                f"-{parameter.removeprefix('-')} "
                f"{wrapper_sha if parameter == '-ExpectedWrapperSha256' else zero}"
                for _name, parameter in loader.FLAG_ORDER
            )
        )
        result = subprocess.run(
            [
                powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", command,
            ],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
        # The wrapper is invoked as a nested script inside -Command so the
        # outer Windows PowerShell host normalizes its nested exit to nonzero;
        # exact propagation is covered structurally for the formal -File form.
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertEqual(clear_marker.read_text(encoding="utf-8").splitlines(), ["CLEAR", "CLEAR"])
        self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_wrapper_hash_drift_stops_before_clipboard(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell unavailable")
        wrapper = ROOT / collector.BINDING_SPECS[0][2]
        zero = "0" * 64
        command = (
            "function global:Get-Clipboard { throw 'must-not-read-clipboard' };"
            "function global:Set-Clipboard { throw 'must-not-write-clipboard' };"
            f"& '{wrapper.as_posix()}' "
            + " ".join(
                f"-{parameter.removeprefix('-')} {zero}"
                for _name, parameter in loader.FLAG_ORDER
            )
        )
        result = subprocess.run(
            [
                powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", command,
            ],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_wrapper_prelaunch_clipboard_clear_failure_stops_collector(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell unavailable")
        wrapper = ROOT / collector.BINDING_SPECS[0][2]
        zero = "0" * 64
        wrapper_sha = hashlib.sha256(wrapper.read_bytes()).hexdigest()
        command = (
            "function global:Get-Clipboard { param([switch]$Raw) '"
            + SECRET
            + "' };"
            "function global:Set-Clipboard { param([string]$Value) throw 'synthetic-clear-failure' };"
            f"& '{wrapper.as_posix()}' "
            + " ".join(
                f"-{parameter.removeprefix('-')} "
                f"{wrapper_sha if parameter == '-ExpectedWrapperSha256' else zero}"
                for _name, parameter in loader.FLAG_ORDER
            )
        )
        result = subprocess.run(
            [
                powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", command,
            ],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertFalse((ROOT / f"data/raw/{collector.VERSION}").exists())

    def test_wrapper_final_clipboard_clear_failure_cannot_be_exit_zero(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("PowerShell unavailable")
        wrapper = ROOT / collector.BINDING_SPECS[0][2]
        harness = pathlib.Path(self.layout.temp.name) / "wrapper_collector_zero_harness"
        python_path = harness / ".venv/Scripts/python.exe"
        fake_collector = harness / "src/quant_research/binance_spot_forward_schedule_pit_v1.py"
        python_path.parent.mkdir(parents=True)
        fake_collector.parent.mkdir(parents=True)
        shutil.copy2(sys.executable, python_path)
        fake_collector.write_text(
            "import pathlib\n"
            "pathlib.Path('collector_ran.marker').write_text('ran', encoding='utf-8')\n"
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        zero = "0" * 64
        wrapper_sha = hashlib.sha256(wrapper.read_bytes()).hexdigest()
        command = (
            "$global:clipboardSetCount=0;"
            "function global:Get-Clipboard { param([switch]$Raw) '"
            + SECRET
            + "' };"
            "function global:Set-Clipboard { param([string]$Value) "
            "$global:clipboardSetCount += 1; "
            "if ($global:clipboardSetCount -ge 2) { throw 'synthetic-final-clear-failure' } };"
            f"& '{wrapper.as_posix()}' "
            + " ".join(
                f"-{parameter.removeprefix('-')} "
                f"{wrapper_sha if parameter == '-ExpectedWrapperSha256' else zero}"
                for _name, parameter in loader.FLAG_ORDER
            )
        )
        result = subprocess.run(
            [
                powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", command,
            ],
            cwd=harness, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertEqual((harness / "collector_ran.marker").read_text(encoding="utf-8"), "ran")

    def test_formal_command_contains_no_key_and_matches_seven_bindings(self) -> None:
        command = collector.formal_command(self.layout.bindings())
        self.assertNotIn(SECRET, command)
        self.assertEqual(command.count("-Expected"), 7)
        self.assertTrue(command.startswith("powershell.exe -NoLogo -NoProfile -NonInteractive"))

    def test_real_workspace_formal_paths_absent_and_network_zero(self) -> None:
        root = ROOT / f"data/raw/{collector.VERSION}/runs"
        self.assertFalse((root / collector.RUN_ID).exists())
        self.assertFalse((root / f".{collector.RUN_ID}.staging").exists())
        self.assertFalse((root / f".{collector.RUN_ID}.control").exists())


if __name__ == "__main__":
    unittest.main()
