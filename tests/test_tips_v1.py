from __future__ import annotations

from dataclasses import fields, replace
from enum import IntEnum
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np
import torch
from safetensors.torch import load_file, save_file

from quant_research.alpha_models.tips_v1.biases import ALIBI_SLOPES, PERIODS, learned_rpb_indices, temporal_bias
from quant_research.alpha_models.tips_v1.checkpoint import CheckpointBindings, load_checkpoint, save_checkpoint
from quant_research.alpha_models.tips_v1.contracts import (
    BIAS_REGISTRY_SHA256,
    BIAS_REGISTRY,
    FEATURE_ORDER,
    FEATURE_SCHEMA_SHA256,
    PipelineState,
    TIPSConfig,
    TIPSInferenceBatch,
    TIPSSmokeOverride,
    TIPSTrainingBatch,
    TEACHER_KINDS,
    TeacherKind,
    canonical_json_bytes,
    sha256_canonical,
)
from quant_research.alpha_models.tips_v1.data import MarketCalendar, SymbolOHLCV, build_inference_batch, build_training_batch, feature_window
from quant_research.alpha_models.tips_v1.losses import distillation_loss, distillation_target, pairwise_soft_rank, teacher_rank_loss
from quant_research.alpha_models.tips_v1.model import TIPSBackbone, state_dict_sha256
from quant_research.alpha_models.tips_v1.pipeline import (
    FrozenSWAStudent,
    StudentStepReceipt,
    SWAAccumulator,
    SWASnapshot,
    TIPSPipeline,
    validate_swa_snapshot,
)
from quant_research.alpha_models.tips_v1.provenance import IMPLEMENTATION_FILES, implementation_tree, verify_implementation_tree
from quant_research.alpha_models.tips_v1.smoke import synthetic_batch


REPO_ROOT = Path(__file__).resolve().parents[1]
AVAILABILITY = REPO_ROOT / "experiments/exp_20260827_012/artifacts/data_availability.json"


class FakeInt(IntEnum):
    TWENTY = 20


def fixture() -> tuple[MarketCalendar, tuple[SymbolOHLCV, ...], TIPSTrainingBatch]:
    return synthetic_batch()


class TIPSContractsTests(unittest.TestCase):
    def test_config_exact_and_unknown_rejected(self) -> None:
        config = TIPSConfig()
        self.assertEqual(config.to_dict()["d_model"], 64)
        with self.assertRaises(ValueError):
            TIPSConfig.from_dict({**config.to_dict(), "unknown": 1})
        for bad in (True, FakeInt.TWENTY, 20.0, "20"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                TIPSConfig.from_dict({**config.to_dict(), "lookback": bad})
        self.assertTrue(TIPSSmokeOverride().synthetic_only)
        self.assertEqual(config.required_swa_updates, 10)
        self.assertEqual(TIPSSmokeOverride().required_swa_updates, 2)

    def test_registry_exact_order_identity_and_unknown_rejected(self) -> None:
        self.assertEqual(tuple(item.value for item in TEACHER_KINDS), (
            "PAST_CAUSAL", "FUTURE_REVERSE_SELF_SAFE", "PATCH_LEN2_STRIDE1", "ALIBI",
            "FIXED_PERIODIC_LOCAL_SINUSOIDAL", "LEARNED_RPB", "VANILLA",
        ))
        self.assertEqual(BIAS_REGISTRY_SHA256, sha256_canonical(BIAS_REGISTRY))
        self.assertEqual(tuple(row["id"] for row in BIAS_REGISTRY), tuple(item.value for item in TEACHER_KINDS))
        self.assertEqual(BIAS_REGISTRY[1]["status"], "LOCAL_DISCLOSED_CHOICE")
        self.assertEqual(BIAS_REGISTRY[4]["status"], "LOCAL_DISCLOSED_CHOICE")
        with self.assertRaises(ValueError):
            TIPSBackbone(TIPSConfig(), "VANILLA", role="TEACHER")  # type: ignore[arg-type]

    def test_feature_known_vector_order_label_and_no_leakage(self) -> None:
        calendar, series, batch = fixture()
        self.assertEqual(FEATURE_ORDER[-3:], ("ma5_over_close_minus_1", "ma10_over_close_minus_1", "ma20_over_close_minus_1"))
        formation = calendar.index("S042")
        actual = feature_window(series[0], formation)
        raw = series[0].values
        endpoint = formation
        expected_open = (raw[endpoint, 0] - raw[endpoint - 19 : endpoint + 1, 0].mean()) / raw[endpoint - 19 : endpoint + 1, 0].std(ddof=0)
        expected_ma5 = raw[endpoint - 4 : endpoint + 1, 3].mean() / raw[endpoint, 3] - 1.0
        self.assertAlmostEqual(actual[-1, 0], expected_open, places=12)
        self.assertAlmostEqual(actual[-1, 5], expected_ma5, places=12)
        self.assertTrue(np.array_equal(batch.labels, batch.close_t_plus_4_raw / batch.close_t_raw - 1.0))
        changed = np.array(series[0].values, copy=True)
        changed[formation + 1 :, :] *= 100.0
        modified = SymbolOHLCV(series[0].symbol, calendar.calendar_id, changed, series[0].known_at_ms)
        self.assertTrue(np.array_equal(actual, feature_window(modified, formation)))

    def test_warmup_calendar_purge_and_typed_join_fail_closed(self) -> None:
        calendar, series, _ = fixture()
        with self.assertRaises(ValueError):
            feature_window(series[0], 37)
        with self.assertRaises(ValueError):
            build_training_batch(calendar, series, "S042", partition_id="TRAIN", partition_session_ids=tuple(item for item in calendar.session_ids if item != "S046"))
        bad_known = np.array(series[0].known_at_ms, copy=True)
        bad_known[42] += 1
        bad_series = SymbolOHLCV("AAA", calendar.calendar_id, series[0].values, bad_known)
        with self.assertRaises(ValueError):
            build_inference_batch(calendar, (bad_series, series[1]), "S042", partition_id="TRAIN")
        with self.assertRaises(ValueError):
            build_inference_batch(calendar, (series[0], series[0]), "S042", partition_id="TRAIN")

    def test_training_labels_are_not_an_inference_input(self) -> None:
        _, _, batch = fixture()
        inference = batch.as_inference_batch()
        self.assertIs(type(inference), TIPSInferenceBatch)
        self.assertNotIn("labels", {field.name for field in fields(TIPSInferenceBatch)})

    def test_row_identity_rejects_positional_feature_or_label_reorder(self) -> None:
        _, _, batch = fixture()
        permutation = np.array([2, 0, 3, 1])
        with self.assertRaises(ValueError):
            replace(batch.as_inference_batch(), features=batch.features[permutation], feature_known_at_ms=batch.feature_known_at_ms[permutation])
        with self.assertRaises(ValueError):
            replace(batch, label_row_symbols=("CCC", "AAA", "DDD", "BBB"))

    def test_builder_input_order_is_semantically_invariant(self) -> None:
        calendar, series, first = fixture()
        second = build_training_batch(
            calendar, tuple(reversed(series)), "S042", partition_id="TRAIN_SYNTHETIC",
            partition_session_ids=calendar.session_ids,
        )
        self.assertEqual(first.symbols, second.symbols)
        self.assertTrue(np.array_equal(first.features, second.features))
        self.assertTrue(np.array_equal(first.labels, second.labels))

    def test_strict_clock_arrays_and_explicit_q5_path(self) -> None:
        _, _, batch = fixture()
        self.assertEqual(batch.label_path_session_ids, ("S042", "S043", "S044", "S045", "S046"))
        for bad in (True, -1, 1.5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                replace(batch.as_inference_batch(), formation_time_ms=bad)
        with self.assertRaises(ValueError):
            replace(batch.as_inference_batch(), feature_known_at_ms=np.zeros((4, 20), dtype=np.bool_))
        with self.assertRaises(ValueError):
            replace(batch, label_path_session_ids=("S042", "S043", "S044", "S045", "OTHER"))

    def test_paper_and_smoke_configs_are_separate(self) -> None:
        paper = TIPSConfig()
        smoke = TIPSSmokeOverride()
        self.assertEqual((paper.paper_teacher_epochs, paper.paper_student_epochs, paper.effective_batch_size), (100, 20, 256))
        self.assertEqual((smoke.teacher_steps_each, smoke.student_steps, smoke.required_swa_updates), (1, 2, 2))
        self.assertNotIn("teacher_steps_each", paper.to_dict())


class TIPSBiasAndLossTests(unittest.TestCase):
    def test_past_and_future_exact_masks_self_safe(self) -> None:
        past = temporal_bias(TeacherKind.PAST_CAUSAL, length=20, device=torch.device("cpu"), dtype=torch.float32)[0]
        future = temporal_bias(TeacherKind.FUTURE_REVERSE_SELF_SAFE, length=20, device=torch.device("cpu"), dtype=torch.float32)[0]
        self.assertEqual(float(past[1, 0]), 0.0)
        self.assertTrue(torch.isneginf(past[0, 1]))
        self.assertEqual(float(future[19, 19]), 0.0)
        self.assertTrue(torch.isneginf(future[1, 0]))
        self.assertEqual(float(future[0, 19]), 0.0)

    def test_alibi_periodic_rpb_and_patch_matrices(self) -> None:
        alibi = temporal_bias(TeacherKind.ALIBI, length=20, device=torch.device("cpu"), dtype=torch.float64)
        for head, slope in enumerate(ALIBI_SLOPES):
            self.assertAlmostEqual(float(alibi[head, 0, 3]), -3.0 * slope)
        periodic = temporal_bias(TeacherKind.FIXED_PERIODIC_LOCAL_SINUSOIDAL, length=20, device=torch.device("cpu"), dtype=torch.float64)
        for head, period in enumerate(PERIODS):
            offset = period if period < 20 else 0
            self.assertAlmostEqual(float(periodic[head, 0, offset]), 1.0, places=12)
        indices = learned_rpb_indices(20, device=torch.device("cpu"))
        self.assertEqual((int(indices.min()), int(indices.max())), (0, 38))
        _, _, batch = fixture()
        patch = TIPSBackbone(TIPSConfig(), TeacherKind.PATCH_LEN2_STRIDE1, role="TEACHER")
        self.assertEqual(patch._tokens(torch.as_tensor(np.array(batch.features, copy=True), dtype=torch.float32)).shape, (4, 19, 64))

    def test_soft_rank_perfect_reverse_ties_extreme_and_gradient(self) -> None:
        labels = torch.tensor([-0.2, 0.0, 0.3], dtype=torch.float64)
        perfect = labels.clone().requires_grad_(True)
        loss = teacher_rank_loss(perfect, labels)
        self.assertAlmostEqual(float(loss.detach()), -1.0, places=12)
        loss.backward()
        self.assertTrue(torch.isfinite(perfect.grad).all())
        self.assertAlmostEqual(float(teacher_rank_loss(-labels, labels)), 1.0, places=12)
        constant = torch.ones(3, requires_grad=True)
        constant_loss = teacher_rank_loss(constant, labels)
        self.assertEqual(float(constant_loss.detach()), 0.0)
        constant_loss.backward()
        self.assertTrue(torch.isfinite(constant.grad).all())
        self.assertTrue(torch.isfinite(pairwise_soft_rank(torch.tensor([-1e30, 0.0, 1e30]))).all())

    def test_soft_rank_permutation_and_invalid_masks(self) -> None:
        values = torch.tensor([0.1, -0.2, 0.4])
        permutation = torch.tensor([2, 0, 1])
        self.assertTrue(torch.equal(pairwise_soft_rank(values)[permutation], pairwise_soft_rank(values[permutation])))
        with self.assertRaises(ValueError):
            pairwise_soft_rank(torch.tensor([1.0]))
        with self.assertRaises(ValueError):
            pairwise_soft_rank(values, valid_mask=torch.tensor([True, False, False]))
        with self.assertRaises(ValueError):
            pairwise_soft_rank(values, valid_mask=torch.tensor([False, False, False]))
        with self.assertRaises(ValueError):
            pairwise_soft_rank(torch.tensor([1.0, float("nan")]))

    def test_distillation_known_vector_detached_and_label_free(self) -> None:
        teachers = tuple(torch.tensor([0.0, 0.01], requires_grad=True) for _ in range(7))
        target = distillation_target(teachers)
        base = torch.softmax(torch.tensor([0.0, 1.0]), dim=0)
        self.assertTrue(torch.allclose(target, 0.1 * base + 0.45))
        student = torch.tensor([0.2, -0.1], requires_grad=True)
        loss = distillation_loss(student, teachers)
        loss.backward()
        self.assertIsNotNone(student.grad)
        self.assertTrue(all(item.grad is None for item in teachers))


class TIPSModelPipelineTests(unittest.TestCase):
    def test_all_teachers_forward_finite_and_symbol_permutation(self) -> None:
        _, _, batch = fixture()
        inference = batch.as_inference_batch()
        permutation = np.array([2, 0, 3, 1])
        with self.assertRaises(ValueError):
            replace(
                inference,
                symbols=tuple(inference.symbols[index] for index in permutation),
                features=inference.features[permutation],
                feature_known_at_ms=inference.feature_known_at_ms[permutation],
            )
        for kind in TEACHER_KINDS:
            model = TIPSBackbone(TIPSConfig(), kind, role="TEACHER")
            output = model(inference)
            self.assertEqual(output.shape, (4,))
            self.assertTrue(torch.isfinite(output).all())
            with self.assertRaises(ValueError):
                replace(inference, features=inference.features[permutation], feature_known_at_ms=inference.feature_known_at_ms[permutation])

    def test_state_machine_missing_teacher_and_invalid_transition(self) -> None:
        pipeline = TIPSPipeline(TIPSConfig(), device=torch.device("cpu"), smoke_override=TIPSSmokeOverride())
        with self.assertRaises(RuntimeError):
            pipeline.begin_student(seed=1)
        pipeline.begin_teachers(seed=1)
        pipeline.teachers.pop(TeacherKind.VANILLA)
        with self.assertRaises(RuntimeError):
            pipeline.freeze_teachers()

    def test_teacher_freeze_student_zero_teacher_grad_and_swa(self) -> None:
        calendar, _, batch = fixture()
        pipeline = TIPSPipeline(TIPSConfig(), device=torch.device("cpu"), smoke_override=TIPSSmokeOverride())
        pipeline.begin_teachers(seed=1)
        for kind in TEACHER_KINDS:
            optimizer = torch.optim.Adam(pipeline.teachers[kind].parameters(), lr=1e-4)
            pipeline.train_teacher_step(kind, batch, optimizer, calendar=calendar)
        frozen = pipeline.freeze_teachers()
        pipeline.begin_student(seed=2)
        assert pipeline.student is not None
        optimizer = torch.optim.Adam(pipeline.student.parameters(), lr=1e-4)
        pipeline.student_step(batch.as_inference_batch(), optimizer)
        self.assertEqual(frozen, {kind.value: pipeline._teacher_frozen_hashes[kind] for kind in TEACHER_KINDS})
        self.assertTrue(all(parameter.grad is None for teacher in pipeline.teachers.values() for parameter in teacher.parameters()))
        pipeline.begin_swa()
        pipeline.swa_update()
        with self.assertRaises(ValueError):
            pipeline.swa_update()
        pipeline.student_step(batch.as_inference_batch(), optimizer)
        pipeline.swa_update()
        pipeline.freeze_student()
        pipeline.begin_inference()
        result = pipeline.infer(batch.as_inference_batch())
        self.assertEqual(result.swa_update_count, 2)
        self.assertEqual(pipeline.teachers, {})

    def test_teacher_step_revalidates_calendar_path_time_and_partition(self) -> None:
        calendar, _, batch = fixture()
        pipeline = TIPSPipeline(TIPSConfig(), device=torch.device("cpu"), smoke_override=TIPSSmokeOverride())
        pipeline.begin_teachers(seed=3)
        optimizer = torch.optim.Adam(pipeline.teachers[TeacherKind.VANILLA].parameters(), lr=1e-4)
        forged_path = replace(batch)
        object.__setattr__(forged_path, "label_path_session_ids", ("S042", "S043", "S044", "S045", "S047"))
        object.__setattr__(forged_path, "label_session_id", "S047")
        forged_time = replace(batch)
        object.__setattr__(forged_time, "label_path_times_ms", batch.label_path_times_ms[:-1] + (batch.label_path_times_ms[-1] + 1,))
        object.__setattr__(forged_time, "label_time_ms", batch.label_time_ms + 1)
        forged_partition = replace(batch)
        object.__setattr__(forged_partition, "partition_session_ids", tuple(item for item in calendar.session_ids if item != "S044"))
        for forged in (forged_path, forged_time, forged_partition):
            with self.subTest(path=forged.label_path_session_ids), self.assertRaises(ValueError):
                pipeline.train_teacher_step(TeacherKind.VANILLA, forged, optimizer, calendar=calendar)

    def test_swa_active_training_distinct_trajectory_and_exact_mean(self) -> None:
        calendar, _, batch = fixture()
        pipeline = TIPSPipeline(TIPSConfig(), device=torch.device("cpu"), smoke_override=TIPSSmokeOverride())
        pipeline.begin_teachers(seed=8)
        pipeline.freeze_teachers()
        pipeline.begin_student(seed=9)
        assert pipeline.student is not None
        optimizer = torch.optim.Adam(pipeline.student.parameters(), lr=1e-4)
        pipeline.student_step(batch.as_inference_batch(), optimizer)
        pipeline.begin_swa()
        first = {key: value.detach().cpu().clone() for key, value in pipeline.student.state_dict().items()}
        pipeline.swa_update()
        with self.assertRaises(ValueError):
            pipeline.swa_update()
        pipeline.student_step(batch.as_inference_batch(), optimizer)
        second = {key: value.detach().cpu().clone() for key, value in pipeline.student.state_dict().items()}
        pipeline.swa_update()
        pipeline.freeze_student()
        for key, value in pipeline.student.state_dict().items():
            expected = ((first[key].double() + second[key].double()) / 2).to(value.dtype)
            self.assertTrue(torch.equal(value.detach().cpu(), expected))
        frozen = pipeline.frozen_swa_student()
        self.assertEqual(frozen.swa_update_ids, tuple(receipt.receipt_id for receipt in pipeline.student_step_receipts))
        self.assertEqual(frozen.swa_update_count, 2)
        self.assertEqual(tuple(receipt.step_index for receipt in frozen.swa_step_receipts), (1, 2))

    def test_swa_zero_and_teacher_rejected_known_average(self) -> None:
        config = TIPSConfig()
        student = TIPSBackbone(config, TeacherKind.VANILLA, role="STUDENT")
        accumulator = SWAAccumulator()
        with self.assertRaises(ValueError):
            accumulator.apply(student, required_swa_updates=2, student_step_receipts=())
        first_receipt = StudentStepReceipt(1, student.state_sha256)
        accumulator.update(student, step_receipt=first_receipt)
        first = {key: value.clone() for key, value in student.state_dict().items()}
        with torch.no_grad():
            for parameter in student.parameters():
                parameter.add_(2.0)
        second = {key: value.clone() for key, value in student.state_dict().items()}
        second_receipt = StudentStepReceipt(2, student.state_sha256)
        accumulator.update(student, step_receipt=second_receipt)
        accumulator.apply(student, required_swa_updates=2, student_step_receipts=(first_receipt, second_receipt))
        for key, value in student.state_dict().items():
            self.assertTrue(torch.equal(value, ((first[key].double() + second[key].double()) / 2).to(value.dtype)))
        teacher = TIPSBackbone(config, TeacherKind.VANILLA, role="TEACHER")
        with self.assertRaises(ValueError):
            SWAAccumulator().update(teacher, step_receipt=first_receipt)

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA unavailable")
    def test_cuda_on_device_deterministic_and_under_2gib(self) -> None:
        _, _, batch = fixture()
        device = torch.device("cuda:0")
        model = TIPSBackbone(TIPSConfig(), TeacherKind.VANILLA, role="STUDENT").to(device)
        torch.cuda.reset_peak_memory_stats()
        model.freeze()
        with torch.inference_mode():
            first = model(batch.as_inference_batch())
            second = model(batch.as_inference_batch())
        self.assertTrue(torch.equal(first, second))
        self.assertLess(torch.cuda.max_memory_allocated(), 2 * 1024**3)


class TIPSCheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entries, cls.tree_sha = implementation_tree(REPO_ROOT, IMPLEMENTATION_FILES)
        cls.availability_sha = hashlib.sha256(AVAILABILITY.read_bytes()).hexdigest()
        cls.bindings = CheckpointBindings(cls.tree_sha, cls.availability_sha, TIPSConfig().sha256)
        cls.teacher_hashes = {kind.value: hashlib.sha256(kind.value.encode()).hexdigest() for kind in TEACHER_KINDS}

    def _pipeline(self) -> TIPSPipeline:
        _, _, batch = fixture()
        pipeline = TIPSPipeline(TIPSConfig(), device=torch.device("cpu"), smoke_override=TIPSSmokeOverride())
        pipeline.begin_teachers(seed=10)
        pipeline.freeze_teachers()
        pipeline.begin_student(seed=11)
        assert pipeline.student is not None
        optimizer = torch.optim.Adam(pipeline.student.parameters(), lr=1e-4)
        pipeline.student_step(batch.as_inference_batch(), optimizer)
        pipeline.begin_swa()
        pipeline.swa_update()
        pipeline.student_step(batch.as_inference_batch(), optimizer)
        pipeline.swa_update()
        pipeline.freeze_student()
        return pipeline

    def _frozen(self) -> FrozenSWAStudent:
        return self._pipeline().frozen_swa_student()

    def _save(self, root: Path) -> tuple[Path, str, str, TIPSPipeline]:
        pipeline = self._pipeline()
        directory = root / "checkpoint"
        manifest_id, weights_sha = save_checkpoint(
            directory, pipeline, repo_root=REPO_ROOT, bindings=self.bindings,
        )
        return directory, manifest_id, weights_sha, pipeline

    @staticmethod
    def _snapshot_sha(tensors: tuple[tuple[str, torch.Tensor], ...]) -> str:
        digest = hashlib.sha256()
        for name, tensor in tensors:
            digest.update(name.encode("utf-8") + b"\0")
            digest.update(str(tensor.dtype).encode("ascii") + b"\0")
            digest.update(str(tuple(tensor.shape)).encode("ascii") + b"\0")
            digest.update(tensor.numpy().tobytes(order="C"))
        return digest.hexdigest()

    def _snapshot(self, tensors: tuple[tuple[str, torch.Tensor], ...], step: int) -> SWASnapshot:
        receipt = StudentStepReceipt(step, self._snapshot_sha(tensors))
        return SWASnapshot(receipt.receipt_id, receipt, tensors)

    @staticmethod
    def _forged_snapshot(update_id: object, receipt: object, tensors: object) -> SWASnapshot:
        value = object.__new__(SWASnapshot)
        object.__setattr__(value, "update_id", update_id)
        object.__setattr__(value, "step_receipt", receipt)
        object.__setattr__(value, "tensors", tensors)
        return value

    def test_implementation_tree_known_vector_and_drift(self) -> None:
        self.assertEqual(self.tree_sha, sha256_canonical(self.entries))
        self.assertEqual([entry["path"] for entry in self.entries], list(IMPLEMENTATION_FILES))
        with self.assertRaises(ValueError):
            verify_implementation_tree(REPO_ROOT, IMPLEMENTATION_FILES, "0" * 64)

    def test_checkpoint_roundtrip_external_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory, manifest_id, weights_sha, _ = self._save(Path(raw))
            loaded = load_checkpoint(directory, repo_root=REPO_ROOT, bindings=self.bindings, expected_manifest_id=manifest_id, expected_weights_sha256=weights_sha)
            loaded.model.assert_frozen()
            self.assertEqual(loaded.swa_update_count, 2)
            self.assertEqual(tuple(item.step_index for item in loaded.swa_step_receipts), (1, 2))
            with self.assertRaises(ValueError):
                load_checkpoint(directory, repo_root=REPO_ROOT, bindings=self.bindings, expected_manifest_id="0" * 64, expected_weights_sha256=weights_sha)

    def test_checkpoint_tamper_and_synchronized_resign_attack_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory, external_manifest_id, external_weights_sha, _ = self._save(Path(raw))
            tensors = {key: value.clone() for key, value in load_file(directory / "model.safetensors", device="cpu").items()}
            key = sorted(tensors)[0]
            tensors[key] = tensors[key].clone()
            tensors[key].view(-1)[0] += 1.0
            replacement = directory / "replacement.safetensors"
            save_file(tensors, replacement)
            (directory / "model.safetensors").unlink()
            replacement.rename(directory / "model.safetensors")
            manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
            manifest["weights_bytes"] = (directory / "model.safetensors").stat().st_size
            manifest["weights_sha256"] = hashlib.sha256((directory / "model.safetensors").read_bytes()).hexdigest()
            old_id = manifest.pop("manifest_id")
            manifest["manifest_id"] = sha256_canonical(manifest)
            (directory / "manifest.json").write_bytes(canonical_json_bytes(manifest))
            self.assertNotEqual(old_id, manifest["manifest_id"])
            with self.assertRaises(ValueError):
                load_checkpoint(directory, repo_root=REPO_ROOT, bindings=self.bindings, expected_manifest_id=external_manifest_id, expected_weights_sha256=external_weights_sha)

    def test_checkpoint_rejects_extra_pickle_symlink_and_nonfinite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory, manifest_id, weights_sha, _ = self._save(Path(raw))
            (directory / "evil.pkl").write_bytes(b"not-read")
            with self.assertRaises(ValueError):
                load_checkpoint(directory, repo_root=REPO_ROOT, bindings=self.bindings, expected_manifest_id=manifest_id, expected_weights_sha256=weights_sha)

    def test_checkpoint_nonfinite_student_rejected_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pipeline = self._pipeline()
            with torch.no_grad():
                pipeline.swa.snapshots[0].tensors[0][1].view(-1)[0] = float("nan")
            with self.assertRaises(ValueError):
                save_checkpoint(
                    Path(raw) / "checkpoint", pipeline, repo_root=REPO_ROOT, bindings=self.bindings,
                )
            self.assertFalse((Path(raw) / "checkpoint").exists())

    def test_checkpoint_save_requires_exact_frozen_pipeline_before_mkdir(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            model = TIPSBackbone(TIPSConfig(), TeacherKind.VANILLA, role="STUDENT")
            model.freeze()
            with self.assertRaises(ValueError):
                save_checkpoint(Path(raw) / "raw", model, repo_root=REPO_ROOT, bindings=self.bindings)  # type: ignore[arg-type]
            frozen = self._frozen()
            with self.assertRaises(ValueError):
                save_checkpoint(Path(raw) / "forged", frozen, repo_root=REPO_ROOT, bindings=self.bindings)
            class PipelineLike:
                state = PipelineState.STUDENT_FROZEN
            with self.assertRaises(ValueError):
                save_checkpoint(Path(raw) / "like", PipelineLike(), repo_root=REPO_ROOT, bindings=self.bindings)  # type: ignore[arg-type]
            unfinished = TIPSPipeline(TIPSConfig(), device=torch.device("cpu"), smoke_override=TIPSSmokeOverride())
            with self.assertRaises(ValueError):
                save_checkpoint(Path(raw) / "unfinished", unfinished, repo_root=REPO_ROOT, bindings=self.bindings)
            self.assertFalse((Path(raw) / "raw").exists())
            self.assertFalse((Path(raw) / "forged").exists())
            self.assertFalse((Path(raw) / "like").exists())
            self.assertFalse((Path(raw) / "unfinished").exists())

    def test_strict_snapshot_structure_rejects_without_normalization(self) -> None:
        snapshot = self._frozen().snapshots[0]
        malformed = (
            self._forged_snapshot("", snapshot.step_receipt, snapshot.tensors),
            self._forged_snapshot(1, snapshot.step_receipt, snapshot.tensors),
            self._forged_snapshot(snapshot.update_id, snapshot.step_receipt, list(snapshot.tensors)),
            self._forged_snapshot(snapshot.update_id, snapshot.step_receipt, (("only",),)),
            self._forged_snapshot(snapshot.update_id, snapshot.step_receipt, (("", torch.zeros(1)),)),
            self._forged_snapshot(snapshot.update_id, snapshot.step_receipt, tuple(reversed(snapshot.tensors))),
            self._forged_snapshot(snapshot.update_id, snapshot.step_receipt, (("x", torch.ones(2, 2).T),)),
            self._forged_snapshot(snapshot.update_id, snapshot.step_receipt, (("x", torch.tensor([float("nan")])),)),
        )
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_swa_snapshot(value)

    def test_snapshot_final_key_shape_dtype_and_replacement_rejected(self) -> None:
        frozen = self._frozen()
        final = frozen.model.state_dict()
        original = frozen.snapshots[0].tensors
        candidates = [
            original[:-1],
            original + (("zz_extra", torch.zeros(1)),),
            ((original[0][0], original[0][1].reshape((*original[0][1].shape, 1))),) + original[1:],
            ((original[0][0], original[0][1].double()),) + original[1:],
        ]
        for index, tensors in enumerate(candidates, start=20):
            snapshot = self._snapshot(tensors, index)
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_swa_snapshot(snapshot, final_state=final)

    def test_frozen_rejects_wrong_average_duplicate_state_and_update(self) -> None:
        frozen = self._frozen()
        final_tensors = tuple((name, value.detach().cpu().contiguous().clone()) for name, value in sorted(frozen.model.state_dict().items()))
        plus_one = tuple((name, value + 1.0) for name, value in final_tensors)
        plus_two = tuple((name, value + 2.0) for name, value in final_tensors)
        wrong = (self._snapshot(plus_one, 10), self._snapshot(plus_two, 11))
        with self.assertRaises(ValueError):
            FrozenSWAStudent(frozen.model, frozen.teacher_state_hashes, wrong, 2, True, "SYNTHETIC_SMOKE_OVERRIDE_V1")
        same_state = (self._snapshot(final_tensors, 20), self._snapshot(final_tensors, 21))
        with self.assertRaises(ValueError):
            FrozenSWAStudent(frozen.model, frozen.teacher_state_hashes, same_state, 2, True, "SYNTHETIC_SMOKE_OVERRIDE_V1")
        duplicate_update = (frozen.snapshots[0], frozen.snapshots[0])
        with self.assertRaises(ValueError):
            FrozenSWAStudent(frozen.model, frozen.teacher_state_hashes, duplicate_update, 2, True, "SYNTHETIC_SMOKE_OVERRIDE_V1")

    def test_single_and_symmetric_forged_artifacts_cannot_publish(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            frozen = self._frozen()
            with self.assertRaises(ValueError):
                FrozenSWAStudent(frozen.model, frozen.teacher_state_hashes, frozen.snapshots[:1], 2, True, "SYNTHETIC_SMOKE_OVERRIDE_V1")
            symmetric = FrozenSWAStudent(
                frozen.model,
                frozen.teacher_state_hashes,
                (
                    self._snapshot(frozen.snapshots[0].tensors, 100),
                    self._snapshot(frozen.snapshots[1].tensors, 101),
                ),
                2,
                True,
                "SYNTHETIC_SMOKE_OVERRIDE_V1",
            )
            with self.assertRaises(ValueError):
                save_checkpoint(Path(raw) / "symmetric", symmetric, repo_root=REPO_ROOT, bindings=self.bindings)
            self.assertFalse(any(Path(raw).iterdir()))

    def test_pipeline_receipt_binding_and_required_update_count(self) -> None:
        pipeline = self._pipeline()
        self.assertEqual(tuple(item.step_index for item in pipeline.student_step_receipts), (1, 2))
        self.assertEqual(pipeline.swa.update_ids, tuple(item.receipt_id for item in pipeline.student_step_receipts))
        forged = self._snapshot(pipeline.swa.snapshots[0].tensors, 99)
        pipeline.swa._snapshots[0] = forged
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(ValueError):
                save_checkpoint(Path(raw) / "forged", pipeline, repo_root=REPO_ROOT, bindings=self.bindings)
            self.assertFalse(any(Path(raw).iterdir()))

    def test_paper_policy_requires_exact_ten_updates(self) -> None:
        _, _, batch = fixture()
        pipeline = TIPSPipeline(TIPSConfig(), device=torch.device("cpu"))
        pipeline.begin_teachers(seed=10)
        pipeline.freeze_teachers()
        pipeline.begin_student(seed=11)
        assert pipeline.student is not None
        optimizer = torch.optim.Adam(pipeline.student.parameters(), lr=1e-4)
        pipeline.student_step(batch.as_inference_batch(), optimizer)
        pipeline.begin_swa()
        pipeline.swa_update()
        pipeline.student_step(batch.as_inference_batch(), optimizer)
        pipeline.swa_update()
        with self.assertRaises(ValueError):
            pipeline.freeze_student()
        self.assertEqual(pipeline.required_swa_updates, 10)

    def test_legacy_pipeline_token_is_not_importable(self) -> None:
        import quant_research.alpha_models.tips_v1.pipeline as pipeline_module
        self.assertFalse(hasattr(pipeline_module, "_PIPELINE_TOKEN"))

    def test_static_no_network_upstream_or_unsafe_load_entrypoint(self) -> None:
        forbidden = ("requests.", "urllib.", "socket.", "huggingface", "torch.load(", "pickle.load", "joblib.load")
        for relative in IMPLEMENTATION_FILES:
            text = (REPO_ROOT / relative).read_text("utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{relative}:{token}")


if __name__ == "__main__":
    unittest.main()
