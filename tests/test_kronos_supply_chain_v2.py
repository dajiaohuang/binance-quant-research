from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from safetensors.torch import save_file
import torch

from quant_research.alpha_models.external import kronos_supply_chain_v2 as supply
from quant_research.alpha_models.external import kronos_adapter_v2 as adapter
from quant_research.alpha_models.external.kronos_adapter_v2 import (
    KronosRequest,
    PAIRINGS,
    infer,
    load_offline,
)


class _Response(io.BytesIO):
    def __init__(self, body: bytes, url: str = "https://raw.githubusercontent.com/example/path", status: int = 200):
        super().__init__(body)
        self._url = url
        self.status = status

    def geturl(self) -> str:
        return self._url


def _git_oid(body: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(body)).encode("ascii") + b"\0" + body).hexdigest()


def _row(body: bytes, target: Path, *, oid: str | None = None, sha: str | None = None) -> dict[str, object]:
    return {
        "authority_oid": oid or _git_oid(body),
        "authority_oid_kind": "GIT_SHA1",
        "expected_bytes": len(body),
        "expected_sha256": sha or hashlib.sha256(body).hexdigest(),
        "immutable_url": "https://raw.githubusercontent.com/example/path",
        "ordinal": 1,
        "path": target.as_posix(),
        "revision": "0" * 40,
        "source_kind": "GITHUB_BLOB",
    }


class KronosSupplyChainV2Tests(unittest.TestCase):
    def test_expected_manifest_is_external_complete_and_exact_total(self):
        document = supply.load_expected_manifest()
        self.assertEqual(len(document["files"]), 19)
        self.assertEqual(sum(row["expected_bytes"] for row in document["files"]), 562_430_552)
        self.assertEqual([row["ordinal"] for row in document["files"]], list(range(1, 20)))
        self.assertNotIn("blocker", document)
        for row in document["files"]:
            self.assertNotIn("kronos_official_v1", row["path"])
            if row["source_kind"] == "HF_LFS":
                self.assertEqual(row["authority_oid"], row["expected_sha256"])

    def test_stream_publish_checks_sha_bytes_and_git_blob_oid_before_publish(self):
        body = b"independent authority\n"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "object"
            result = supply._stream_and_publish(_row(body, target), _Response(body), 0, target)
            self.assertEqual(target.read_bytes(), body)
            self.assertEqual(result["sha256"], hashlib.sha256(body).hexdigest())

    def test_short_oversize_hash_and_git_oid_fail_before_publish(self):
        body = b"authority"
        cases = (
            (_row(body + b"x", Path("short")), body, "body_short"),
            (_row(body[:-1], Path("oversize")), body, "body_oversize"),
            (_row(body, Path("hash"), sha="0" * 64), body, "body_hash"),
            (_row(body, Path("oid"), oid="0" * 40), body, "git_blob_oid"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (row, response_body, reason) in enumerate(cases):
                target = Path(directory) / str(index)
                with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                    supply._stream_and_publish(row, _Response(response_body), 0, target)
                self.assertFalse(target.exists())

    def test_bad_redirect_host_and_second_redirect_rejected(self):
        handler = supply._BoundedRedirectHandler("huggingface.co")
        with self.assertRaisesRegex(ValueError, "url_scope"):
            handler.redirect_request(None, None, 302, "", {}, "https://evil.example/object")
        handler.redirect_count = 1
        with self.assertRaisesRegex(ValueError, "redirect_scope"):
            handler.redirect_request(None, None, 302, "", {}, "https://huggingface.co/object")
        github = supply._BoundedRedirectHandler("raw.githubusercontent.com")
        with self.assertRaisesRegex(ValueError, "redirect_scope"):
            github.redirect_request(None, None, 302, "", {}, "https://huggingface.co/object")

    def test_signed_transport_query_is_not_persisted(self):
        evidence = supply._sanitized_transport(
            "https://us.aws.cdn.hf.co/xet/object?Expires=SECRET&Signature=SECRET",
            200,
            1,
        )
        serialized = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("Expires", serialized)
        self.assertNotIn("Signature", serialized)
        self.assertEqual(evidence["final_path"], "/xet/object")

    def test_extra_or_missing_raw_path_fails_bijection(self):
        manifest = supply.load_expected_manifest()
        expected = {row["path"] for row in manifest["files"]}
        with patch.object(supply, "_tree_paths", return_value=expected - {next(iter(expected))}):
            with self.assertRaisesRegex(ValueError, "raw_path_bijection"):
                supply.verify_raw_tree(manifest)
        with patch.object(supply, "_tree_paths", return_value=expected | {"data/raw/kronos_official_v2/extra"}):
            with self.assertRaisesRegex(ValueError, "raw_path_bijection"):
                supply.verify_raw_tree(manifest)

    def test_v2_modules_do_not_reference_v1_raw_or_exp007_manifest(self):
        for module_path in (
            supply.ROOT / "src/quant_research/alpha_models/external/kronos_supply_chain_v2.py",
            supply.ROOT / "src/quant_research/alpha_models/external/kronos_adapter_v2.py",
        ):
            source = module_path.read_text(encoding="utf-8")
            self.assertNotIn("kronos_official_v1", source)
            self.assertNotIn("exp_20260827_007", source)

    def test_pairing_registry_and_explicit_v2_root(self):
        self.assertEqual(set(PAIRINGS), {"Kronos-mini", "Kronos-small", "Kronos-base"})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "checkpoint_root_not_frozen_v2"):
                load_offline(Path(directory), "Kronos-mini")

    def test_direct_safetensors_load_is_strict_for_config_hash_and_shape(self):
        class Dummy(torch.nn.Module):
            def __init__(self, width):
                super().__init__()
                self.weight = torch.nn.Parameter(torch.zeros(width))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "checkpoint"
            checkpoint.mkdir()
            config_path = checkpoint / "config.json"
            weights_path = checkpoint / "model.safetensors"
            config_path.write_text('{"width":2}', encoding="utf-8")
            save_file({"weight": torch.ones(2)}, weights_path)
            expected = {
                "files": [
                    {"path": "checkpoint/config.json", "expected_bytes": config_path.stat().st_size, "expected_sha256": supply._sha256_path(config_path)},
                    {"path": "checkpoint/model.safetensors", "expected_bytes": weights_path.stat().st_size, "expected_sha256": supply._sha256_path(weights_path)},
                ]
            }
            with patch.object(adapter, "ROOT", root):
                model, evidence = adapter._load_direct(Dummy, checkpoint, expected)
                self.assertTrue(torch.equal(model.weight, torch.ones(2)))
                self.assertEqual(evidence["tensor_count"], 1)
                config_path.write_text('{"unknown":2}', encoding="utf-8")
                expected["files"][0]["expected_bytes"] = config_path.stat().st_size
                expected["files"][0]["expected_sha256"] = supply._sha256_path(config_path)
                with self.assertRaisesRegex(ValueError, "checkpoint_config_keys"):
                    adapter._load_direct(Dummy, checkpoint, expected)
                config_path.write_text('{"width":2}', encoding="utf-8")
                expected["files"][0]["expected_bytes"] = config_path.stat().st_size
                expected["files"][0]["expected_sha256"] = supply._sha256_path(config_path)
                save_file({"weight": torch.ones(3)}, weights_path)
                expected["files"][1]["expected_bytes"] = weights_path.stat().st_size
                expected["files"][1]["expected_sha256"] = supply._sha256_path(weights_path)
                with self.assertRaises(RuntimeError):
                    adapter._load_direct(Dummy, checkpoint, expected)

    @unittest.skipUnless(supply.ACQUISITION_MANIFEST.exists(), "v2 formal acquisition not present")
    def test_hermetic_mini_offline_load_and_inference(self):
        import huggingface_hub
        frame = pd.read_csv(supply.RAW_ROOT / "samples/tests/data/regression_input.csv", parse_dates=["timestamps"]).iloc[:32]
        timestamps = tuple(int(value.timestamp() * 1000) for value in pd.to_datetime(frame.timestamps, utc=True))
        formation = timestamps[-1]
        step = timestamps[-1] - timestamps[-2]
        values = tuple(tuple(float(row[column]) for column in ("open", "high", "low", "close", "volume", "amount")) for _, row in frame.iterrows())
        request = KronosRequest(values, timestamps, (formation + step, formation + 2 * step), formation, formation)
        with (
            patch.object(socket, "socket", side_effect=AssertionError("socket forbidden")),
            patch.object(socket, "getaddrinfo", side_effect=AssertionError("dns forbidden")),
            patch("urllib.request.urlopen", side_effect=AssertionError("urllib forbidden")),
            patch.object(huggingface_hub, "hf_hub_download", side_effect=AssertionError("hub forbidden")),
        ):
            predictor, evidence = load_offline(supply.RAW_ROOT, "Kronos-mini", "cpu")
            with torch.inference_mode():
                output, provenance = infer(predictor, request)
        self.assertEqual(output.shape, (2, 6))
        self.assertTrue(np.isfinite(output.to_numpy()).all())
        self.assertEqual(evidence["logical_request_count"], 19)
        self.assertRegex(provenance, r"^[0-9a-f]{64}$")

    @unittest.skipUnless(supply.ACQUISITION_MANIFEST.exists(), "v2 formal acquisition not present")
    def test_official_small_256_golden_from_v2(self):
        predictor, _ = load_offline(supply.RAW_ROOT, "Kronos-small", "cpu", "Kronos-Tokenizer-base")
        sample_root = supply.RAW_ROOT / "samples/tests/data"
        frame = pd.read_csv(sample_root / "regression_input.csv", parse_dates=["timestamps"])
        expected = pd.read_csv(sample_root / "regression_output_256.csv")
        np.random.seed(123)
        torch.manual_seed(123)
        columns = ["open", "high", "low", "close", "volume", "amount"]
        with torch.inference_mode():
            actual = predictor.predict(
                frame.iloc[:256][columns],
                frame.timestamps.iloc[:256],
                frame.timestamps.iloc[256:264].reset_index(drop=True),
                8,
                T=1.0,
                top_k=1,
                top_p=1.0,
                sample_count=1,
                verbose=False,
            )
        np.testing.assert_allclose(actual[columns].to_numpy(), expected[columns].to_numpy(), rtol=2e-5, atol=2e-5)


if __name__ == "__main__":
    unittest.main()
