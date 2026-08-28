from __future__ import annotations

from dataclasses import fields, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np
import torch
from safetensors.torch import load as load_safetensors
from safetensors.torch import save as save_safetensors

from quant_research.alpha_models.sspt_v2.checkpoint import (
    CheckpointBindings,
    implementation_tree_sha256,
    load_checkpoint,
    save_checkpoint,
    verify_frozen_implementation_tree,
)
from quant_research.alpha_models.sspt_v2.contracts import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_SHA256,
    CrossSectionInferenceBatch,
    CrossSectionTrainingBatch,
    SSPTConfigV2,
    StableLabelRegistry,
    SymbolDailySeries,
    canonical_json_bytes,
    sha256_canonical,
)
from quant_research.alpha_models.sspt_v2.data import (
    TrainingFeaturePartition,
    TrainOnlyMinMax,
    build_cross_section_batch,
    build_feature_matrix,
    purged_time_split,
)
from quant_research.alpha_models.sspt_v2.losses import fine_tune_loss, pretrain_loss
from quant_research.alpha_models.sspt_v2.model import (
    FreezeMode,
    MAPView,
    SSPTInferenceRequestV2,
    SSPTModelV2,
    deterministic_map_view,
)
from quant_research.alpha_models.sspt_v2.smoke import run_smoke


ROOT = Path(__file__).resolve().parents[1]


def _bindings(fill: str = "1") -> CheckpointBindings:
    return CheckpointBindings(fill * 64, "2" * 64, "3" * 64, "4" * 64)


def _fixture(symbol_count: int = 4, *, dropout: float = 0.0):
    count = 48
    calendar = tuple((f"S{index:03d}", 1_700_000_000_000 + index * 86_400_000) for index in range(count))
    all_symbols = ("AAA", "BBB", "CCC", "DDD")[:symbol_count]
    all_sectors = ("ENERGY", "FINANCE", "TECH", "TECH")[:symbol_count]
    series = []
    raw_features = []
    for asset_index, (symbol, sector) in enumerate(zip(all_symbols, all_sectors, strict=True)):
        base = 100.0 + asset_index * 20.0 + np.arange(count, dtype=np.float64) * (0.3 + asset_index * 0.05)
        ohlcv = np.column_stack((base, base + 1.0, base - 1.0, base + 0.25, 1_000.0 + np.arange(count)))
        item = SymbolDailySeries(
            market_id="SYNTHETIC_SPOT",
            symbol=symbol,
            session_ids=tuple(identifier for identifier, _ in calendar),
            session_times_ms=tuple(timestamp for _, timestamp in calendar),
            feature_known_at_ms=tuple(timestamp for _, timestamp in calendar),
            ohlcv=ohlcv,
            sector_label=sector,
            sector_known_at_ms=calendar[0][1],
        )
        series.append(item)
        raw_features.append(build_feature_matrix(ohlcv))
    partition = TrainingFeaturePartition(
        kind="TRAIN",
        features=np.stack([np.stack([value[:16] for value in raw_features])]),
        formation_times_ms=(calendar[44][1],),
        label_end_times_ms=(calendar[45][1],),
        train_end_exclusive_ms=calendar[46][1],
        data_provenance_sha256=hashlib.sha256(b"synthetic-training").hexdigest(),
    )
    scaler = TrainOnlyMinMax().fit(partition)
    scc = StableLabelRegistry.from_labels(
        "SCC_SYMBOL_IDENTITY_V1",
        all_symbols,
        authority_id="SYNTHETIC_AUTHORITY",
        training_partition_id="TRAIN_V1",
        known_at_ms=calendar[0][1],
    )
    unique_sectors = tuple(sorted(set(all_sectors), key=lambda value: value.encode("utf-8")))
    ssc = StableLabelRegistry.from_labels(
        "SSC_SECTOR_V1",
        unique_sectors,
        authority_id="SYNTHETIC_AUTHORITY",
        training_partition_id="TRAIN_V1",
        known_at_ms=calendar[0][1],
    )
    config = SSPTConfigV2(
        dropout=dropout,
        scc_classes=len(scc.labels),
        ssc_classes=len(ssc.labels),
    )
    batch = build_cross_section_batch(
        reversed(series),
        expected_calendar=calendar,
        market_id="SYNTHETIC_SPOT",
        formation_time_ms=calendar[44][1],
        lookback=config.lookback,
        scaler=scaler,
        scc_registry=scc,
        ssc_registry=ssc,
    )
    return config, scc, ssc, scaler, batch, tuple(series), calendar, partition


def _model(config, scc, ssc, scaler):
    torch.manual_seed(7)
    return SSPTModelV2(config, scc_registry=scc, ssc_registry=ssc, scaler_sha256=scaler.sha256)


class SSPTV2DataTests(unittest.TestCase):
    def test_config_is_strict_and_registry_sizes_drive_heads(self):
        config, scc, ssc, scaler, _, _, _, _ = _fixture()
        with self.assertRaises(ValueError):
            SSPTConfigV2.from_dict({"lookback": 16})
        with self.assertRaises(ValueError):
            SSPTConfigV2(d_model=True)
        model = _model(config, scc, ssc, scaler)
        self.assertEqual(model.scc_head.out_features, len(scc.labels))
        self.assertEqual(model.ssc_head.out_features, len(ssc.labels))
        with self.assertRaises(ValueError):
            SSPTModelV2(replace(config, scc_classes=2), scc_registry=scc, ssc_registry=ssc, scaler_sha256=scaler.sha256)

    def test_exact_25_feature_order_and_full_ma30_warmup(self):
        self.assertEqual(len(FEATURE_NAMES), 25)
        base = 100.0 + np.arange(30, dtype=np.float64)
        ohlcv = np.column_stack((base, base + 2, base - 2, base + 0.5, 1_000 + base))
        features = build_feature_matrix(ohlcv)
        self.assertEqual(features.shape, (1, 25))
        self.assertEqual(FEATURE_NAMES[:5], ("open_ma5", "open_ma10", "open_ma20", "open_ma30", "open_raw"))
        self.assertAlmostEqual(features[0, 0], np.mean(base[-5:]))
        self.assertAlmostEqual(features[0, 3], np.mean(base))
        self.assertEqual(features[0, 4], base[-1])
        with self.assertRaises(ValueError):
            build_feature_matrix(ohlcv[:-1])

    def test_calendar_alignment_partial_warmup_and_missing_session_fail(self):
        config, scc, ssc, scaler, _, series, calendar, _ = _fixture()
        with self.assertRaisesRegex(ValueError, "partial_warmup"):
            build_cross_section_batch(series, expected_calendar=calendar, market_id="SYNTHETIC_SPOT", formation_time_ms=calendar[43][1], lookback=16, scaler=scaler, scc_registry=scc, ssc_registry=ssc)
        shortened = series[0]
        bad = SymbolDailySeries(
            market_id=shortened.market_id,
            symbol=shortened.symbol,
            session_ids=shortened.session_ids[:-1],
            session_times_ms=shortened.session_times_ms[:-1],
            feature_known_at_ms=shortened.feature_known_at_ms[:-1],
            ohlcv=shortened.ohlcv[:-1],
            sector_label=shortened.sector_label,
            sector_known_at_ms=shortened.sector_known_at_ms,
        )
        with self.assertRaisesRegex(ValueError, "missing_or_extra_session"):
            build_cross_section_batch((bad,) + series[1:], expected_calendar=calendar, market_id="SYNTHETIC_SPOT", formation_time_ms=calendar[44][1], lookback=config.lookback, scaler=scaler, scc_registry=scc, ssc_registry=ssc)

    def test_typed_cross_section_clock_tokens_and_return_identity(self):
        _, _, _, _, batch, _, calendar, _ = _fixture()
        self.assertEqual(batch.size, 4)
        self.assertEqual(batch.clock.formation_session_id, calendar[44][0])
        self.assertEqual(batch.clock.next_session_id, calendar[45][0])
        self.assertEqual(batch.feature_times_ms.shape, (4, 16))
        self.assertTrue(np.array_equal(batch.feature_times_ms[0], batch.feature_times_ms[-1]))
        self.assertTrue(np.all(batch.feature_known_at_ms <= batch.feature_times_ms))
        np.testing.assert_array_equal(batch.raw_close_window[:, -1], batch.close_t_raw)
        np.testing.assert_array_equal(batch.next_session_returns, batch.close_next_raw / batch.close_t_raw - 1.0)
        with self.assertRaisesRegex(ValueError, "calendar_adjacency"):
            replace(batch.clock, next_session_id=calendar[46][0], next_session_time_ms=calendar[46][1])
        with self.assertRaisesRegex(ValueError, "calendar_sha256"):
            replace(batch.clock, calendar_sha256="f" * 64)
        forged_ids = tuple((row[:-1] + ("FORGED",)) for row in batch.feature_session_ids)
        with self.assertRaisesRegex(ValueError, "feature_calendar_window"):
            replace(batch.as_inference_batch(), feature_session_ids=forged_ids)
        with self.assertRaises(ValueError):
            replace(batch, next_session_returns=batch.next_session_returns + 1e-12)

    def test_symbol_sector_and_registry_known_at_are_fail_closed(self):
        config, scc, ssc, scaler, _, series, calendar, _ = _fixture()
        with self.assertRaises(ValueError):
            replace(series[0], symbol="aaa")
        future_sector = replace(series[0], sector_known_at_ms=calendar[45][1])
        with self.assertRaisesRegex(ValueError, "future_sector"):
            build_cross_section_batch((future_sector,) + series[1:], expected_calendar=calendar, market_id="SYNTHETIC_SPOT", formation_time_ms=calendar[44][1], lookback=config.lookback, scaler=scaler, scc_registry=scc, ssc_registry=ssc)
        future_registry = replace(ssc, known_at_ms=calendar[45][1])
        with self.assertRaisesRegex(ValueError, "future_registry"):
            build_cross_section_batch(series, expected_calendar=calendar, market_id="SYNTHETIC_SPOT", formation_time_ms=calendar[44][1], lookback=config.lookback, scaler=scaler, scc_registry=scc, ssc_registry=future_registry)

    def test_scc_is_exact_symbol_identity_and_ssc_registry_is_utf8_stable(self):
        _, scc, ssc, _, batch, _, _, _ = _fixture()
        self.assertEqual(scc.labels, batch.symbols)
        self.assertEqual(tuple(batch.scc_targets), tuple(range(batch.size)))
        unicode_registry = StableLabelRegistry.from_labels("U", ("科技", "Energy"), authority_id="A", training_partition_id="T", known_at_ms=0)
        self.assertEqual(unicode_registry.labels, tuple(sorted(("科技", "Energy"), key=lambda value: value.encode("utf-8"))))
        self.assertEqual(batch.ssc_registry_sha256, ssc.sha256)

    def test_train_only_scaler_requires_typed_purged_partition_and_is_fit_once(self):
        _, _, _, scaler, _, _, calendar, partition = _fixture()
        baseline = scaler.state()
        future = np.full_like(partition.features, 1e12)
        scaler.transform(future)
        self.assertEqual(baseline, scaler.state())
        with self.assertRaises((TypeError, ValueError)):
            TrainOnlyMinMax().fit(np.zeros((1, 4, 16, 25)))
        with self.assertRaises(ValueError):
            scaler.fit(partition)
        with self.assertRaisesRegex(ValueError, "partition_purge"):
            TrainingFeaturePartition("TRAIN", partition.features, (calendar[44][1],), (calendar[46][1],), calendar[46][1], "a" * 64)
        with self.assertRaisesRegex(ValueError, "partition_purge"):
            TrainingFeaturePartition("TRAIN", np.concatenate((partition.features, partition.features)), (calendar[44][1], calendar[44][1]), (calendar[45][1], calendar[45][1]), calendar[46][1], "a" * 64)

    def test_constant_training_columns_map_all_future_values_to_zero(self):
        features = np.ones((1, 2, 16, 25), dtype=np.float64)
        partition = TrainingFeaturePartition("TRAIN", features, (10,), (20,), 30, "a" * 64)
        scaler = TrainOnlyMinMax().fit(partition)
        future = np.full((2, 16, 25), 999.0, dtype=np.float64)
        np.testing.assert_array_equal(scaler.transform(future), np.zeros_like(future))
        self.assertEqual(scaler.state()["constant_column_policy"], "MAP_TO_ZERO")

    def test_public_integer_arrays_reject_fraction_bool_and_negative_before_cast(self):
        _, _, _, _, batch, _, _, _ = _fixture()
        cases = (
            {"feature_times_ms": batch.feature_times_ms.astype(np.float64) + 0.5},
            {"feature_known_at_ms": batch.feature_known_at_ms.astype(bool)},
            {"scc_targets": batch.scc_targets.astype(np.float64) + 0.25},
            {"ssc_targets": batch.ssc_targets.astype(bool)},
            {"feature_times_ms": np.full_like(batch.feature_times_ms, -1)},
            {"scc_targets": np.full_like(batch.scc_targets, -1)},
        )
        for mutation in cases:
            with self.subTest(field=tuple(mutation)):
                with self.assertRaises(ValueError):
                    replace(batch, **mutation)

    def test_right_boundary_purge_keeps_label_endpoint_inside_split(self):
        split = purged_time_split((10, 20, 30, 40, 50), (20, 30, 40, 50, 60), train_end_exclusive_ms=30, validation_end_exclusive_ms=50)
        self.assertEqual(split.train_indices, (0,))
        self.assertEqual(split.validation_indices, (2,))
        self.assertEqual(split.test_indices, (4,))
        self.assertEqual(split.purged_indices, (1, 3))


class SSPTV2ImplementationBindingTests(unittest.TestCase):
    def _copied_binding(self, directory: str) -> tuple[Path, Path, dict[str, object]]:
        frozen = json.loads((ROOT / "experiments/exp_20260827_011/artifacts/frozen_hashes.json").read_text(encoding="utf-8"))
        projection = {
            "implementation_files": frozen["implementation_files"],
            "implementation_tree_sha256": frozen["implementation_tree_sha256"],
        }
        repo = Path(directory) / "repo"
        repo.mkdir()
        for row in projection["implementation_files"]:
            source = ROOT / row["path"]
            target = repo / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        manifest = Path(directory) / "frozen.json"
        manifest.write_bytes(canonical_json_bytes(projection) + b"\n")
        return repo, manifest, projection

    def test_canonical_tree_known_vector_and_live_frozen_binding(self):
        vector = [
            {"path": "a.py", "bytes": 1, "sha256": "0" * 64},
            {"path": "b.py", "bytes": 2, "sha256": "f" * 64},
        ]
        self.assertEqual(implementation_tree_sha256(vector), "7bea55207b95110505d6d68e25ca0122d05a12dc2d43786311576d9f3ed06bd8")
        frozen_path = ROOT / "experiments/exp_20260827_011/artifacts/frozen_hashes.json"
        expected = json.loads(frozen_path.read_text(encoding="utf-8"))["implementation_tree_sha256"]
        self.assertEqual(verify_frozen_implementation_tree(ROOT, frozen_path), expected)

    def test_path_bytes_sha_and_file_drift_all_fail_closed(self):
        for drift in ("path", "bytes", "sha256", "file"):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as directory:
                repo, manifest, projection = self._copied_binding(directory)
                if drift == "path":
                    projection["implementation_files"][0]["path"] += ".moved"
                elif drift == "bytes":
                    projection["implementation_files"][0]["bytes"] += 1
                elif drift == "sha256":
                    projection["implementation_files"][0]["sha256"] = "0" * 64
                else:
                    target = repo / projection["implementation_files"][0]["path"]
                    payload = target.read_bytes()
                    target.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
                if drift != "file":
                    manifest.write_bytes(canonical_json_bytes(projection) + b"\n")
                with self.assertRaises(ValueError):
                    verify_frozen_implementation_tree(repo, manifest)


class SSPTV2MethodTests(unittest.TestCase):
    def test_map_mask_is_exact_topk_and_target_is_full_pre_mask_close_mean(self):
        config, _, _, _, batch, _, _, _ = _fixture()
        first = deterministic_map_view(batch, seed=9, mask_rate=config.map_mask_rate)
        second = deterministic_map_view(batch, seed=10, mask_rate=config.map_mask_rate)
        expected_count = int(config.map_mask_rate * config.lookback)
        self.assertTrue(np.all(first.mask.sum(axis=1) == expected_count))
        np.testing.assert_array_equal(first.target_raw_close_mean, batch.raw_close_window.mean(axis=1))
        np.testing.assert_array_equal(first.target_raw_close_mean, second.target_raw_close_mean)
        self.assertFalse(np.array_equal(first.mask, second.mask))
        self.assertEqual(first.prf_sha256, deterministic_map_view(batch, seed=9, mask_rate=config.map_mask_rate).prf_sha256)

    def test_pretrain_rejects_forged_map_target_and_all_zero_weights(self):
        config, scc, ssc, scaler, batch, _, _, _ = _fixture()
        model = _model(config, scc, ssc, scaler)
        view = deterministic_map_view(batch, seed=9, mask_rate=config.map_mask_rate)
        outputs = model.pretrain(batch, view)
        self.assertTrue(torch.isfinite(pretrain_loss(outputs, batch, view, alpha=1, beta=1, gamma=1)))
        forged = MAPView(view.mask, view.target_raw_close_mean + 1.0, view.prf_sha256, view.seed, view.mask_rate)
        with self.assertRaisesRegex(ValueError, "map_view_binding"):
            pretrain_loss(outputs, batch, forged, alpha=0, beta=0, gamma=1)
        with self.assertRaisesRegex(ValueError, "all_zero_weights"):
            pretrain_loss(outputs, batch, view, alpha=0, beta=0, gamma=0)

    def test_three_heads_have_registry_shapes_and_gamma_zero_blocks_map_gradient(self):
        config, scc, ssc, scaler, batch, _, _, _ = _fixture()
        model = _model(config, scc, ssc, scaler)
        view = deterministic_map_view(batch, seed=4, mask_rate=config.map_mask_rate)
        outputs = model.pretrain(batch, view)
        self.assertEqual(outputs[0].shape, (4, 4))
        self.assertEqual(outputs[1].shape, (4, 3))
        pretrain_loss(outputs, batch, view, alpha=1, beta=1, gamma=0).backward()
        gradient = model.map_head.weight.grad
        self.assertTrue(gradient is None or torch.count_nonzero(gradient) == 0)

    def test_exact_equation5_full_same_day_cross_section_and_input_order_invariance(self):
        _, _, _, _, batch, _, _, _ = _fixture(symbol_count=2)
        close_t = np.array([1.0, 1.0])
        close_next = np.array([1.2, 1.1])
        target_batch = replace(
            batch,
            raw_close_window=np.column_stack((np.ones(16), np.ones(16))).T,
            close_t_raw=close_t,
            close_next_raw=close_next,
            next_session_returns=close_next / close_t - 1.0,
        )
        value = fine_tune_loss(torch.tensor([0.1, 0.2]), target_batch, epsilon=5.0)
        self.assertAlmostEqual(float(value), 0.12, places=6)
        with self.assertRaises(TypeError):
            fine_tune_loss(torch.tensor([0.1, 0.2]), target_batch, torch.tensor([True, True]))
        config, scc, ssc, scaler, canonical, series, calendar, _ = _fixture()
        reversed_batch = build_cross_section_batch(series, expected_calendar=calendar, market_id="SYNTHETIC_SPOT", formation_time_ms=calendar[44][1], lookback=config.lookback, scaler=scaler, scc_registry=scc, ssc_registry=ssc)
        np.testing.assert_array_equal(canonical.features, reversed_batch.features)

    def test_training_and_inference_reject_binding_mismatch_and_labels(self):
        config, scc, ssc, scaler, batch, _, _, _ = _fixture()
        model = _model(config, scc, ssc, scaler)
        request = SSPTInferenceRequestV2(batch.as_inference_batch())
        prediction = model.predict(request)
        self.assertEqual(prediction.shape, (4,))
        with self.assertRaises(TypeError):
            SSPTInferenceRequestV2(batch.as_inference_batch(), labels=np.zeros(4))
        bad = replace(batch.as_inference_batch(), scaler_sha256="f" * 64)
        with self.assertRaisesRegex(ValueError, "batch_binding"):
            model.predict(SSPTInferenceRequestV2(bad))
        self.assertNotIn("next_session_returns", {field.name for field in fields(CrossSectionInferenceBatch)})

    def test_eval_inference_mode_is_bitwise_deterministic_and_restores_mode(self):
        config, scc, ssc, scaler, batch, _, _, _ = _fixture(dropout=0.4)
        model = _model(config, scc, ssc, scaler)
        model.train()
        request = SSPTInferenceRequestV2(batch.as_inference_batch())
        first = model.predict(request)
        second = model.predict(request)
        self.assertTrue(model.training)
        self.assertTrue(torch.equal(first, second))
        self.assertFalse(first.requires_grad)

    def test_all_four_freeze_modes_preserve_frozen_parameters_bitwise(self):
        config, scc, ssc, scaler, batch, _, _, _ = _fixture()
        for mode in FreezeMode:
            with self.subTest(mode=mode.value):
                model = _model(config, scc, ssc, scaler)
                state = model.set_freeze_mode(mode)
                before = {name: parameter.detach().clone() for name, parameter in model.named_parameters()}
                optimizer = torch.optim.SGD((parameter for parameter in model.parameters() if parameter.requires_grad), lr=0.01)
                optimizer.zero_grad(set_to_none=True)
                model.fine_tune_scores(batch).sum().backward()
                optimizer.step()
                after = dict(model.named_parameters())
                self.assertTrue(all(torch.equal(before[name], after[name].detach()) for name in state.frozen_parameter_names))
                self.assertTrue(any(not torch.equal(before[name], after[name].detach()) for name in state.trainable_parameter_names))


class SSPTV2CheckpointTests(unittest.TestCase):
    def _saved(self, root: Path, mode: FreezeMode = FreezeMode.BACKBONE):
        config, scc, ssc, scaler, batch, _, _, _ = _fixture()
        model = _model(config, scc, ssc, scaler)
        target = root / "checkpoint"
        manifest = save_checkpoint(model, target, scaler=scaler, freeze_mode=mode, bindings=_bindings())
        identity = (manifest["manifest_id"], manifest["weights"]["sha256"])
        return target, config, scc, ssc, scaler, batch, identity

    def _load(self, target, config, scc, ssc, scaler, identity, mode=FreezeMode.BACKBONE, bindings=None):
        return load_checkpoint(
            target,
            expected_config=config,
            expected_scc_registry=scc,
            expected_ssc_registry=ssc,
            expected_scaler=scaler,
            expected_freeze_mode=mode,
            expected_bindings=bindings or _bindings(),
            expected_manifest_id=identity[0],
            expected_weights_sha256=identity[1],
        )

    def test_dual_file_roundtrip_binds_config_registry_scaler_freeze_and_code(self):
        with tempfile.TemporaryDirectory() as directory:
            target, config, scc, ssc, scaler, batch, identity = self._saved(Path(directory))
            self.assertEqual({path.name for path in target.iterdir()}, {"manifest.json", "model.safetensors"})
            bundle = self._load(target, config, scc, ssc, scaler, identity)
            request = SSPTInferenceRequestV2(batch.as_inference_batch())
            self.assertTrue(torch.equal(bundle.predict(request), bundle.predict(request)))
            self.assertEqual(bundle.bindings, _bindings())

    def test_wrong_config_registry_scaler_freeze_or_code_binding_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target, config, scc, ssc, scaler, _, identity = self._saved(Path(directory))
            cases = [
                {"expected_config": replace(config, lookback=32)},
                {"expected_scc_registry": replace(scc, authority_id="OTHER")},
                {"expected_scaler": TrainOnlyMinMax.from_state({**scaler.state(), "training_partition_sha256": "a" * 64})},
                {"expected_freeze_mode": FreezeMode.EMBEDDING},
                {"expected_bindings": _bindings("9")},
            ]
            for override in cases:
                arguments = dict(expected_config=config, expected_scc_registry=scc, expected_ssc_registry=ssc, expected_scaler=scaler, expected_freeze_mode=FreezeMode.BACKBONE, expected_bindings=_bindings(), expected_manifest_id=identity[0], expected_weights_sha256=identity[1])
                arguments.update(override)
                with self.subTest(override=tuple(override)):
                    with self.assertRaises(ValueError):
                        load_checkpoint(target, **arguments)

    def test_self_consistent_weight_and_manifest_rewrite_rejected_by_external_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            target, config, scc, ssc, scaler, _, original_identity = self._saved(Path(directory))
            weights_path = target / "model.safetensors"
            tensors = load_safetensors(weights_path.read_bytes())
            tensor_name = sorted(tensors, key=lambda value: value.encode("utf-8"))[0]
            changed = tensors[tensor_name].clone()
            changed.reshape(-1)[0] += 1.0
            tensors[tensor_name] = changed
            rewritten_weights = save_safetensors(tensors)
            weights_path.write_bytes(rewritten_weights)
            manifest_path = target / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["weights"]["bytes"] = len(rewritten_weights)
            manifest["weights"]["sha256"] = hashlib.sha256(rewritten_weights).hexdigest()
            without_id = dict(manifest)
            without_id.pop("manifest_id")
            manifest["manifest_id"] = sha256_canonical(without_id)
            manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
            self.assertNotEqual((manifest["manifest_id"], manifest["weights"]["sha256"]), original_identity)
            with self.assertRaisesRegex(ValueError, "external_manifest_identity"):
                self._load(target, config, scc, ssc, scaler, original_identity)

    def test_weights_tamper_and_nonfinite_tensor_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, config, scc, ssc, scaler, _, identity = self._saved(root)
            weights = target / "model.safetensors"
            weights.write_bytes(weights.read_bytes() + b"x")
            with self.assertRaises(ValueError):
                self._load(target, config, scc, ssc, scaler, identity)
        with tempfile.TemporaryDirectory() as directory:
            config, scc, ssc, scaler, _, _, _, _ = _fixture()
            model = _model(config, scc, ssc, scaler)
            next(model.parameters()).data.fill_(float("inf"))
            target = Path(directory) / "bad"
            with self.assertRaisesRegex(ValueError, "nonfinite"):
                save_checkpoint(model, target, scaler=scaler, freeze_mode=FreezeMode.NONE, bindings=_bindings())
            self.assertFalse(target.exists())

    def test_manifest_unknown_duplicate_noncanonical_and_nested_tamper_rejected(self):
        mutations = ("unknown", "duplicate", "noncanonical", "nested")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                target, config, scc, ssc, scaler, _, identity = self._saved(Path(directory))
                path = target / "manifest.json"
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if mutation == "unknown":
                    manifest["extra"] = True
                    path.write_bytes(canonical_json_bytes(manifest) + b"\n")
                elif mutation == "duplicate":
                    path.write_text('{"schema_version":"x","schema_version":"y"}\n', encoding="utf-8")
                elif mutation == "noncanonical":
                    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                else:
                    manifest["config"]["lookback"] = 32
                    path.write_bytes(canonical_json_bytes(manifest) + b"\n")
                with self.assertRaises(ValueError):
                    self._load(target, config, scc, ssc, scaler, identity)

    def test_extra_pickle_preexisting_target_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, config, scc, ssc, scaler, _, identity = self._saved(root)
            (target / "unsafe.pkl").write_bytes(b"pickle")
            with self.assertRaisesRegex(ValueError, "checkpoint_files"):
                self._load(target, config, scc, ssc, scaler, identity)
            with self.assertRaises(FileExistsError):
                save_checkpoint(_model(config, scc, ssc, scaler), target, scaler=scaler, freeze_mode=FreezeMode.NONE, bindings=_bindings())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, config, scc, ssc, scaler, _, identity = self._saved(root)
            manifest = target / "manifest.json"
            backup = root / "manifest-copy.json"
            backup.write_bytes(manifest.read_bytes())
            manifest.unlink()
            try:
                os.symlink(backup, manifest)
            except OSError:
                manifest.write_bytes(backup.read_bytes())
                with mock.patch("quant_research.alpha_models.sspt_v2.checkpoint._is_reparse", return_value=True):
                    with self.assertRaises(ValueError):
                        self._load(target, config, scc, ssc, scaler, identity)
            else:
                with self.assertRaises(ValueError):
                    self._load(target, config, scc, ssc, scaler, identity)


class SSPTV2SmokeAndScopeTests(unittest.TestCase):
    def test_cpu_minimal_train_infer_checkpoint_smoke(self):
        metrics = run_smoke("cpu")
        self.assertTrue(metrics["finite"])
        self.assertTrue(metrics["frozen_unchanged_bitwise"])
        self.assertTrue(metrics["trainable_updated"])
        self.assertEqual(metrics["network_request_count"], 0)

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA unavailable")
    def test_gpu_minimal_train_infer_smoke_below_2gib(self):
        metrics = run_smoke("cuda:0")
        self.assertLess(metrics["peak_cuda_bytes"], 2 * 1024**3)
        self.assertTrue(metrics["finite"])
        self.assertTrue(metrics["device_inference_deterministic"])

    def test_clean_room_scope_and_data_availability_are_fail_closed(self):
        contract = json.loads((ROOT / "experiments/exp_20260827_011/artifacts/source_contract.json").read_text(encoding="utf-8"))
        availability = json.loads((ROOT / "experiments/exp_20260827_011/artifacts/data_availability.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["paper"]["title"], "Pre-training Time Series Models with Stock Data Customization")
        self.assertEqual(contract["official_repository"]["license_status"], "NO_LICENSE_FOUND")
        self.assertFalse(contract["clean_room_rules"]["pickle_allowed"])
        self.assertEqual(len(availability["markets"]), 5)
        self.assertEqual({row["status"] for row in availability["markets"]}, {"NO_GO_NO_LICENSE_OR_PROVENANCE"})
        package_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src/quant_research/alpha_models/sspt_v2").glob("*.py"))
        self.assertNotIn("import pickle", package_text)
        self.assertNotIn("torch.load", package_text)


if __name__ == "__main__":
    unittest.main()
