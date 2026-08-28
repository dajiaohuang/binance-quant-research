from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path

import torch

from quant_research.alpha_models.adapter import predictions_to_expert_outputs
from quant_research.alpha_models.contracts import (
    DDGLConfig,
    DDGLContractError,
    DDGLInputBatch,
    load_ddgl_config,
    make_synthetic_examples,
    parse_ddgl_config_bytes,
    validate_input,
)
from quant_research.alpha_models.ddgl_net import DDGLNet
from quant_research.alpha_models.smoke import run
from quant_research.alpha_models.training import (
    fit_synthetic,
    infer,
    load_checkpoint,
    save_checkpoint,
    set_deterministic_seed,
)


ROOT = Path(__file__).resolve().parents[1]
CPU_CONFIG = ROOT / "config/alpha_models/ddgl_tiny_cpu.json"


def tensors(example):
    inputs = example.inputs
    return (
        torch.tensor([inputs.coarse_values], dtype=torch.float32),
        torch.tensor([inputs.fine_values], dtype=torch.float32),
        torch.tensor([inputs.global_market_values], dtype=torch.float32),
    )


class DDGLSyntheticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_ddgl_config(CPU_CONFIG)
        cls.examples = make_synthetic_examples(cls.config)

    def test_config_rejects_unknown_fields_and_empirical_authorization(self):
        payload = json.loads(CPU_CONFIG.read_text(encoding="utf-8"))
        payload["unknown"] = 1
        with self.assertRaises(DDGLContractError):
            parse_ddgl_config_bytes(json.dumps(payload).encode("utf-8"))
        payload.pop("unknown")
        payload["empirical_authorized"] = True
        with self.assertRaises(DDGLContractError):
            parse_ddgl_config_bytes(json.dumps(payload).encode("utf-8"))

    def test_input_shape_clock_and_provenance_are_fail_closed(self):
        inputs = self.examples[0].inputs
        self.assertIs(validate_input(inputs, self.config), inputs)
        bad_shape = replace(inputs, coarse_values=inputs.coarse_values[:-1])
        with self.assertRaises(DDGLContractError):
            validate_input(bad_shape, self.config)
        future = replace(
            inputs,
            fine_known_at_ms=inputs.fine_known_at_ms[:-1]
            + (inputs.formation_time_ms + 1,),
        )
        with self.assertRaises(DDGLContractError):
            validate_input(future, self.config)
        with self.assertRaises(DDGLContractError):
            validate_input(replace(inputs, provenance_sha256="0" * 64), self.config)

    def test_forward_is_finite_and_has_asset_shape(self):
        set_deterministic_seed(self.config.seed)
        model = DDGLNet(self.config).eval()
        values = model(*tensors(self.examples[0]))
        self.assertEqual(values.shape, (1, self.config.synthetic.num_assets))
        self.assertTrue(torch.isfinite(values).all().item())

    def test_backward_updates_parameters(self):
        result = fit_synthetic(self.config, self.examples)
        self.assertTrue(result.parameters_updated)
        self.assertTrue(torch.isfinite(torch.tensor(result.final_loss)).item())
        self.assertLess(result.final_loss, result.initial_loss)
        self.assertEqual(result.device_used, "cpu")

    def test_gpu_plan_is_inside_frozen_16gb_bounds(self):
        config = load_ddgl_config(
            ROOT / "config/alpha_models/ddgl_16gb_gpu.json"
        )
        self.assertLessEqual(config.synthetic.num_assets, 32)
        self.assertLessEqual(config.synthetic.coarse_steps, 8)
        self.assertLessEqual(config.synthetic.fine_steps, 30)
        self.assertLessEqual(config.architecture.hidden_dim, 96)
        self.assertEqual(config.optimization.batch_size, 1)
        self.assertLessEqual(config.runtime.max_vram_mib, 8192)

    def test_checkpoint_weights_only_roundtrip(self):
        result = fit_synthetic(self.config, self.examples[:2])
        inputs = tuple(item.inputs for item in self.examples[:2])
        before = infer(result.model, inputs, self.config)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model.pt"
            digest = save_checkpoint(result.model, self.config, path)
            self.assertEqual(len(digest), 64)
            restored = load_checkpoint(self.config, path)
            after = infer(restored, inputs, self.config)
        self.assertEqual(before, after)

    def test_asset_permutation_equivariance(self):
        set_deterministic_seed(self.config.seed)
        model = DDGLNet(self.config).eval()
        coarse, fine, market = tensors(self.examples[0])
        permutation = torch.tensor([2, 0, 3, 1])
        with torch.no_grad():
            original = model(coarse, fine, market)
            permuted = model(
                coarse.index_select(2, permutation),
                fine.index_select(2, permutation),
                market,
            )
        self.assertTrue(
            torch.allclose(
                permuted, original.index_select(1, permutation), atol=1e-6, rtol=1e-5
            )
        )

    def test_global_market_encoding_is_broadcast_across_assets(self):
        set_deterministic_seed(self.config.seed)
        model = DDGLNet(self.config).eval()
        _, _, market = tensors(self.examples[0])
        broadcast = model.broadcast_global_market(
            market, self.config.synthetic.num_assets
        )
        for asset in range(1, self.config.synthetic.num_assets):
            self.assertTrue(torch.equal(broadcast[:, 0], broadcast[:, asset]))

    def test_inference_interface_cannot_accept_labels(self):
        set_deterministic_seed(self.config.seed)
        model = DDGLNet(self.config)
        with self.assertRaises(DDGLContractError):
            infer(model, self.examples, self.config)

    def test_empirical_gate_and_synthetic_adapter(self):
        result = fit_synthetic(self.config, self.examples[:1])
        predictions = infer(
            result.model, (self.examples[0].inputs,), self.config
        )
        outputs = predictions_to_expert_outputs(predictions, self.config)
        self.assertEqual(len(outputs), self.config.synthetic.num_assets)
        self.assertTrue(all(output.key.horizon_hours == 24 for output in outputs))
        forged = replace(self.config, empirical_authorized=True)
        with self.assertRaises(DDGLContractError):
            predictions_to_expert_outputs(predictions, forged)
        with self.assertRaises(DDGLContractError):
            DDGLNet(forged)

    def test_input_and_label_contracts_are_separate_types(self):
        input_fields = {field.name for field in fields(DDGLInputBatch)}
        self.assertNotIn("labels", input_fields)
        self.assertNotIn("values", input_fields)
        self.assertIsInstance(self.examples[0].inputs, DDGLInputBatch)

    def test_cpu_smoke_writes_bounded_metrics_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "metrics.json"
            checkpoint = Path(temporary) / "checkpoint.pt"
            metrics = run(CPU_CONFIG, output, checkpoint)
            persisted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(metrics, persisted)
            self.assertTrue(checkpoint.is_file())
        self.assertEqual(metrics["artifact_state"], "SYNTHETIC_CONTRACT_VERIFIED")
        self.assertEqual(metrics["terminal_status"], "NEEDS_MORE_DATA")
        self.assertFalse(metrics["real_data_accessed"])
        self.assertFalse(metrics["ic_evaluated"])
        self.assertFalse(metrics["pnl_evaluated"])


if __name__ == "__main__":
    unittest.main()
