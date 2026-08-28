from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from quant_research.alpha_models.external import kronos_offline_reverification as verifier


class KronosOfflineReverificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected, cls.acquisition = verifier._load_bound_manifests(
            verifier.EXPECTED_MANIFEST_SHA256,
            verifier.ACQUISITION_MANIFEST_SHA256,
        )

    def test_manifest_hashes_are_external_and_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "cli_manifest_binding"):
            verifier._load_bound_manifests("0" * 64, verifier.ACQUISITION_MANIFEST_SHA256)
        with tempfile.TemporaryDirectory() as directory:
            tampered = Path(directory) / "acquisition.json"
            tampered.write_bytes(verifier.ACQUISITION_MANIFEST.read_bytes() + b" ")
            with patch.object(verifier, "ACQUISITION_MANIFEST", tampered):
                with self.assertRaisesRegex(ValueError, "acquisition_manifest_hash"):
                    verifier._load_bound_manifests(verifier.EXPECTED_MANIFEST_SHA256, verifier.ACQUISITION_MANIFEST_SHA256)

    def test_exp009_source_bindings_are_exact(self):
        verifier._verify_exp009_sources()
        first = next(iter(verifier.FROZEN_EXP009_INPUTS))
        with patch.object(verifier, "_sha256", side_effect=lambda path: "0" * 64 if path == first else verifier.FROZEN_EXP009_INPUTS[path]):
            with self.assertRaisesRegex(ValueError, "exp009_input_drift"):
                verifier._verify_exp009_sources()

    def test_model_sys_modules_hijack_is_ignored(self):
        attacker = types.ModuleType("model")
        attacker.Kronos = object()
        prior = sys.modules.get("model")
        sys.modules["model"] = attacker
        try:
            with verifier._verified_vendor_modules(self.expected) as kronos:
                self.assertIsNot(kronos, attacker)
                self.assertEqual(kronos.__name__, f"{verifier.SYNTHETIC_PACKAGE}.kronos")
                self.assertIsNot(sys.modules["model"], attacker)
        finally:
            if prior is None:
                sys.modules.pop("model", None)
            else:
                sys.modules["model"] = prior
        self.assertNotIn(verifier.SYNTHETIC_PACKAGE, sys.modules)
        self.assertIs(sys.modules.get("model"), prior)

    def test_synthetic_namespace_collision_fails_closed(self):
        sys.modules[verifier.SYNTHETIC_PACKAGE] = types.ModuleType(verifier.SYNTHETIC_PACKAGE)
        try:
            with self.assertRaisesRegex(ValueError, "synthetic_module_collision"):
                with verifier._verified_vendor_modules(self.expected):
                    pass
        finally:
            sys.modules.pop(verifier.SYNTHETIC_PACKAGE, None)

    def test_source_bytes_not_unbound_pyc_are_executed(self):
        source_rows = verifier._source_rows(self.expected)
        with tempfile.TemporaryDirectory() as directory:
            vendor = Path(directory)
            for relative in source_rows:
                target = vendor / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(verifier.VENDOR_ROOT / relative, target)
            pycache = vendor / "model/__pycache__"
            pycache.mkdir()
            (pycache / "kronos.cpython-312.pyc").write_bytes(b"malicious-unbound-bytecode")
            with patch.object(verifier, "VENDOR_ROOT", vendor):
                with verifier._verified_vendor_modules(self.expected) as kronos:
                    self.assertIsNone(kronos.__cached__)
                    self.assertEqual(Path(kronos.__file__), (vendor / "model/kronos.py").resolve())

    def test_source_tamper_rejected_before_exec(self):
        row = verifier._source_rows(self.expected)["model/module.py"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "module.py"
            path.write_bytes((verifier.VENDOR_ROOT / "model/module.py").read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "vendor_source_binding"):
                verifier._read_verified_source(path, row)

    def test_checkpoint_tamper_rejected_before_tensor_load(self):
        with verifier._verified_vendor_modules(self.expected) as kronos:
            revision = verifier.PAIRINGS["Kronos-mini"][0]
            checkpoint = verifier.RAW_ROOT / "Kronos-mini" / revision
            with patch.object(verifier, "_sha256", return_value="0" * 64):
                with self.assertRaisesRegex(ValueError, "config_binding"):
                    verifier._load_checkpoint(kronos.Kronos, checkpoint, self.expected)

    def test_application_network_blocker_covers_socket_dns_urllib_and_hub(self):
        import huggingface_hub
        with verifier._network_blocked():
            for operation in (
                lambda: socket.socket(),
                lambda: socket.getaddrinfo("example.com", 443),
                lambda: verifier.urllib.request.urlopen("https://example.com"),
                lambda: huggingface_hub.hf_hub_download("repo", "file"),
            ):
                with self.assertRaisesRegex(RuntimeError, "network_forbidden"):
                    operation()

    def test_readonly_full_reverification_and_official_interface(self):
        result = verifier.verify_offline(verifier.EXPECTED_MANIFEST_SHA256, verifier.ACQUISITION_MANIFEST_SHA256)
        self.assertEqual(
            set(result),
            {"acquisition_manifest_sha256", "expected_manifest_sha256", "mini_inference", "network_request_count", "raw_file_count", "raw_total_bytes", "raw_verification_passes", "schema_version", "small_golden", "source_files", "terminal_status"},
        )
        self.assertEqual(result["network_request_count"], 0)
        self.assertEqual(result["raw_file_count"], 19)
        self.assertEqual(result["raw_total_bytes"], 562_430_552)
        self.assertEqual(result["raw_verification_passes"], 2)
        self.assertEqual(result["mini_inference"]["rows"], 2)
        self.assertEqual(result["small_golden"]["rows"], 8)
        self.assertEqual(result["terminal_status"], "NEEDS_MORE_DATA")

    def test_end_raw_reverification_must_equal_start(self):
        expected = {"files": []}
        acquisition = {}
        with (
            patch.object(verifier, "_verify_exp009_sources"),
            patch.object(verifier, "_load_bound_manifests", return_value=(expected, acquisition)),
            patch.object(verifier, "_source_rows", return_value={}),
            patch.object(verifier, "_verify_raw", side_effect=([{"bytes": 1, "path": "a", "sha256": "1"}], [{"bytes": 1, "path": "a", "sha256": "2"}])),
            patch.object(verifier, "_run_model_checks", return_value=({}, {})),
        ):
            with self.assertRaisesRegex(ValueError, "raw_changed_during_verification"):
                verifier.verify_offline(verifier.EXPECTED_MANIFEST_SHA256, verifier.ACQUISITION_MANIFEST_SHA256)

    def test_no_redistribution_or_empirical_claim_in_contract(self):
        contract = json.loads((verifier.ROOT / "experiments/exp_20260827_010/artifacts/source_contract.json").read_text(encoding="utf-8"))
        self.assertIn("do not redistribute", contract["redistribution_notice"])
        self.assertIn("NO_EMPIRICAL_ALPHA", contract["research_ceiling"])


if __name__ == "__main__":
    unittest.main()
