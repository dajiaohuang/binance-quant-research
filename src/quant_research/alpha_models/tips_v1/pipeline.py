from __future__ import annotations

from dataclasses import dataclass
import hashlib

import torch

from .contracts import PipelineState, TIPSConfig, TIPSInferenceBatch, TIPSSmokeOverride, TIPSTrainingBatch, TeacherKind, TEACHER_KINDS, validate_hex64
from .losses import distillation_loss, teacher_rank_loss
from .model import TIPSBackbone, state_dict_sha256
from .contracts import sha256_canonical
from .data import MarketCalendar


@dataclass(frozen=True)
class InferenceResult:
    symbols: tuple[str, ...]
    formation_time_ms: int
    logits: tuple[float, ...]
    student_state_sha256: str
    swa_update_count: int


@dataclass(frozen=True)
class StudentStepReceipt:
    step_index: int
    student_state_sha256: str

    def __post_init__(self) -> None:
        if type(self.step_index) is not int or self.step_index < 1:
            raise ValueError("student_step_index")
        validate_hex64(self.student_state_sha256, "student_step_state_sha256")

    @property
    def receipt_id(self) -> str:
        return sha256_canonical({"step_index": self.step_index, "student_state_sha256": self.student_state_sha256})


def _snapshot_state_sha256(tensors: tuple[tuple[str, torch.Tensor], ...]) -> str:
    digest = hashlib.sha256()
    for name, tensor in tensors:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(str(tensor.dtype).encode("ascii") + b"\0")
        digest.update(str(tuple(tensor.shape)).encode("ascii") + b"\0")
        digest.update(tensor.numpy().tobytes(order="C"))
    return digest.hexdigest()


@dataclass(frozen=True)
class SWASnapshot:
    update_id: str
    step_receipt: StudentStepReceipt
    tensors: tuple[tuple[str, torch.Tensor], ...]

    def __post_init__(self) -> None:
        validate_swa_snapshot(self)

    @property
    def state_sha256(self) -> str:
        return _snapshot_state_sha256(self.tensors)

    def as_dict(self) -> dict[str, torch.Tensor]:
        return {name: tensor for name, tensor in self.tensors}


def validate_swa_snapshot(
    snapshot: object,
    *,
    final_state: dict[str, torch.Tensor] | None = None,
    previous_step_index: int | None = None,
) -> None:
    """Strict validation only; this function never clones, sorts, or normalizes."""
    if type(snapshot) is not SWASnapshot:
        raise ValueError("swa_snapshot_type")
    if type(snapshot.update_id) is not str or not snapshot.update_id:
        raise ValueError("swa_update_id")
    if type(snapshot.step_receipt) is not StudentStepReceipt:
        raise ValueError("swa_step_receipt")
    snapshot.step_receipt.__post_init__()
    if snapshot.update_id != snapshot.step_receipt.receipt_id:
        raise ValueError("swa_update_receipt_binding")
    if previous_step_index is not None and (type(previous_step_index) is not int or snapshot.step_receipt.step_index <= previous_step_index):
        raise ValueError("swa_step_receipt_order")
    if type(snapshot.tensors) is not tuple or not snapshot.tensors:
        raise ValueError("swa_snapshot_tensors")
    names: list[str] = []
    for item in snapshot.tensors:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("swa_snapshot_item")
        name, tensor = item
        if type(name) is not str or not name:
            raise ValueError("swa_snapshot_key")
        if (
            type(tensor) is not torch.Tensor
            or tensor.device.type != "cpu"
            or not tensor.is_contiguous()
            or not tensor.dtype.is_floating_point
            or not torch.isfinite(tensor).all()
        ):
            raise ValueError("swa_snapshot_tensor")
        names.append(name)
    if tuple(names) != tuple(sorted(names)) or len(set(names)) != len(names):
        raise ValueError("swa_snapshot_keys")
    if _snapshot_state_sha256(snapshot.tensors) != snapshot.step_receipt.student_state_sha256:
        raise ValueError("swa_snapshot_receipt_state")
    if final_state is not None:
        expected_keys = tuple(sorted(final_state))
        if tuple(names) != expected_keys:
            raise ValueError("swa_snapshot_keys")
        values = snapshot.as_dict()
        for name in expected_keys:
            final_tensor = final_state[name]
            if type(final_tensor) is not torch.Tensor or values[name].shape != final_tensor.shape or values[name].dtype != final_tensor.dtype:
                raise ValueError("swa_snapshot_shape_dtype")


def _validate_swa_policy(*, required_swa_updates: int, synthetic_only: bool, policy_id: str) -> None:
    if type(required_swa_updates) is not int or type(synthetic_only) is not bool or type(policy_id) is not str:
        raise ValueError("swa_policy")
    expected = (
        (2, True, "SYNTHETIC_SMOKE_OVERRIDE_V1")
        if synthetic_only
        else (10, False, "PAPER_FINAL_10_EPOCHS_LOCAL_MAPPING_V1")
    )
    if (required_swa_updates, synthetic_only, policy_id) != expected:
        raise ValueError("swa_policy")


def _average_snapshots(snapshots: tuple[SWASnapshot, ...], final: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    if type(snapshots) is not tuple or not snapshots:
        raise ValueError("swa_zero_updates")
    expected_keys = tuple(sorted(final))
    sums: dict[str, torch.Tensor] = {}
    previous_step_index: int | None = None
    for snapshot in snapshots:
        validate_swa_snapshot(snapshot, final_state=final, previous_step_index=previous_step_index)
        previous_step_index = snapshot.step_receipt.step_index
        values = snapshot.as_dict()
        for name in expected_keys:
            tensor = values[name]
            if name not in sums:
                sums[name] = tensor.to(torch.float64).clone()
            else:
                sums[name].add_(tensor.to(torch.float64))
    return {name: (sums[name] / len(snapshots)).to(dtype=final[name].dtype) for name in expected_keys}


@dataclass(frozen=True)
class FrozenSWAStudent:
    model: TIPSBackbone
    teacher_state_hashes: dict[str, str]
    snapshots: tuple[SWASnapshot, ...]
    required_swa_updates: int
    synthetic_only: bool
    swa_policy_id: str

    def __post_init__(self) -> None:
        if type(self.model) is not TIPSBackbone or self.model.role != "STUDENT":
            raise ValueError("frozen_swa_model")
        self.model.assert_frozen()
        _validate_swa_policy(
            required_swa_updates=self.required_swa_updates,
            synthetic_only=self.synthetic_only,
            policy_id=self.swa_policy_id,
        )
        if type(self.snapshots) is not tuple or len(self.snapshots) != self.required_swa_updates or any(type(item) is not SWASnapshot for item in self.snapshots):
            raise ValueError("swa_trajectory")
        if len(set(self.swa_update_ids)) != len(self.snapshots) or len(set(self.swa_state_sha256s)) != len(self.snapshots):
            raise ValueError("duplicate_swa_trajectory")
        if type(self.teacher_state_hashes) is not dict or set(self.teacher_state_hashes) != {kind.value for kind in TEACHER_KINDS}:
            raise ValueError("teacher_state_hashes")
        for value in self.teacher_state_hashes.values():
            validate_hex64(value, "teacher_state_sha256")
        final = self.model.state_dict()
        previous_step_index: int | None = None
        for snapshot in self.snapshots:
            validate_swa_snapshot(snapshot, final_state=final, previous_step_index=previous_step_index)
            previous_step_index = snapshot.step_receipt.step_index
        averaged = _average_snapshots(self.snapshots, final)
        if any(not torch.equal(averaged[name], final[name].detach().cpu()) for name in sorted(final)):
            raise ValueError("swa_final_average_mismatch")

    @property
    def final_state_sha256(self) -> str:
        return self.model.state_sha256

    @property
    def swa_update_ids(self) -> tuple[str, ...]:
        return tuple(item.update_id for item in self.snapshots)

    @property
    def swa_state_sha256s(self) -> tuple[str, ...]:
        return tuple(item.state_sha256 for item in self.snapshots)

    @property
    def swa_trajectory_proof_sha256(self) -> str:
        return sha256_canonical({"state_sha256s": list(self.swa_state_sha256s), "update_ids": list(self.swa_update_ids)})

    @property
    def swa_update_count(self) -> int:
        return len(self.swa_update_ids)

    @property
    def swa_step_receipts(self) -> tuple[StudentStepReceipt, ...]:
        return tuple(item.step_receipt for item in self.snapshots)


class SWAAccumulator:
    def __init__(self) -> None:
        self._snapshots: list[SWASnapshot] = []

    @property
    def count(self) -> int:
        return len(self._snapshots)

    def update(self, model: TIPSBackbone, *, step_receipt: StudentStepReceipt) -> None:
        if type(model) is not TIPSBackbone or model.role != "STUDENT" or model.kind is not TeacherKind.VANILLA:
            raise ValueError("swa_student_only")
        if type(step_receipt) is not StudentStepReceipt:
            raise ValueError("swa_step_receipt")
        tensors = tuple((name, tensor.detach().cpu().contiguous().clone()) for name, tensor in sorted(model.state_dict().items()))
        snapshot = SWASnapshot(step_receipt.receipt_id, step_receipt, tensors)
        if snapshot.update_id in self.update_ids:
            raise ValueError("swa_update_id")
        if snapshot.state_sha256 in self.state_sha256s:
            raise ValueError("duplicate_swa_state")
        if self._snapshots:
            validate_swa_snapshot(snapshot, final_state=model.state_dict(), previous_step_index=self._snapshots[-1].step_receipt.step_index)
        self._snapshots.append(snapshot)

    @property
    def update_ids(self) -> tuple[str, ...]:
        return tuple(item.update_id for item in self._snapshots)

    @property
    def state_sha256s(self) -> tuple[str, ...]:
        return tuple(item.state_sha256 for item in self._snapshots)

    @property
    def snapshots(self) -> tuple[SWASnapshot, ...]:
        return tuple(self._snapshots)

    @property
    def trajectory_proof_sha256(self) -> str:
        if not self._snapshots:
            raise ValueError("swa_zero_updates")
        return sha256_canonical({"state_sha256s": list(self.state_sha256s), "update_ids": list(self.update_ids)})

    def validate(
        self,
        model: TIPSBackbone,
        *,
        required_swa_updates: int,
        student_step_receipts: tuple[StudentStepReceipt, ...],
    ) -> None:
        if type(model) is not TIPSBackbone or model.role != "STUDENT":
            raise ValueError("swa_student_only")
        if type(required_swa_updates) is not int or self.count != required_swa_updates:
            raise ValueError("swa_update_count")
        if type(student_step_receipts) is not tuple or any(type(item) is not StudentStepReceipt for item in student_step_receipts):
            raise ValueError("student_step_receipts")
        recorded = {item.receipt_id: item for item in student_step_receipts}
        previous_step_index: int | None = None
        for snapshot in self.snapshots:
            validate_swa_snapshot(snapshot, final_state=model.state_dict(), previous_step_index=previous_step_index)
            if recorded.get(snapshot.update_id) != snapshot.step_receipt:
                raise ValueError("swa_receipt_not_pipeline_issued")
            previous_step_index = snapshot.step_receipt.step_index

    def apply(
        self,
        model: TIPSBackbone,
        *,
        required_swa_updates: int,
        student_step_receipts: tuple[StudentStepReceipt, ...],
    ) -> None:
        self.validate(model, required_swa_updates=required_swa_updates, student_step_receipts=student_step_receipts)
        current = model.state_dict()
        averaged = _average_snapshots(self.snapshots, current)
        model.load_state_dict(averaged, strict=True)


class TIPSPipeline:
    def __init__(
        self,
        config: TIPSConfig,
        *,
        device: torch.device,
        smoke_override: TIPSSmokeOverride | None = None,
    ) -> None:
        if type(config) is not TIPSConfig or type(device) is not torch.device:
            raise ValueError("pipeline_config")
        if smoke_override is not None and type(smoke_override) is not TIPSSmokeOverride:
            raise ValueError("smoke_override")
        self.config = config
        self.device = device
        self.smoke_override = smoke_override
        self.required_swa_updates = smoke_override.required_swa_updates if smoke_override is not None else config.required_swa_updates
        self.synthetic_only = smoke_override is not None
        self.swa_policy_id = "SYNTHETIC_SMOKE_OVERRIDE_V1" if self.synthetic_only else "PAPER_FINAL_10_EPOCHS_LOCAL_MAPPING_V1"
        _validate_swa_policy(
            required_swa_updates=self.required_swa_updates,
            synthetic_only=self.synthetic_only,
            policy_id=self.swa_policy_id,
        )
        self.state = PipelineState.INIT
        self.teachers: dict[TeacherKind, TIPSBackbone] = {}
        self.student: TIPSBackbone | None = None
        self.swa = SWAAccumulator()
        self._teacher_frozen_hashes: dict[TeacherKind, str] = {}
        self._student_step_receipts: list[StudentStepReceipt] = []

    @property
    def student_step_receipts(self) -> tuple[StudentStepReceipt, ...]:
        return tuple(self._student_step_receipts)

    def _require(self, expected: PipelineState) -> None:
        if self.state is not expected:
            raise RuntimeError(f"state:{self.state.value}:{expected.value}")

    def begin_teachers(self, *, seed: int) -> None:
        self._require(PipelineState.INIT)
        if type(seed) is not int:
            raise ValueError("seed")
        torch.manual_seed(seed)
        self.teachers = {kind: TIPSBackbone(self.config, kind, role="TEACHER").to(self.device) for kind in TEACHER_KINDS}
        if tuple(self.teachers) != TEACHER_KINDS:
            raise RuntimeError("teacher_registry")
        self.state = PipelineState.TEACHERS_TRAINING

    def train_teacher_step(self, kind: TeacherKind, batch: TIPSTrainingBatch, optimizer: torch.optim.Optimizer, *, calendar: MarketCalendar) -> float:
        self._require(PipelineState.TEACHERS_TRAINING)
        if type(kind) is not TeacherKind or kind not in self.teachers or type(batch) is not TIPSTrainingBatch:
            raise ValueError("teacher_step")
        if type(calendar) is not MarketCalendar:
            raise ValueError("calendar")
        calendar.validate_training_batch(batch)
        model = self.teachers[kind]
        model.train()
        optimizer.zero_grad(set_to_none=True)
        logits = model(batch.as_inference_batch())
        labels = torch.as_tensor(batch.labels.copy(), dtype=logits.dtype, device=logits.device)
        loss = teacher_rank_loss(logits, labels)
        loss.backward()
        optimizer.step()
        return float(loss.detach().cpu())

    def freeze_teachers(self) -> dict[str, str]:
        self._require(PipelineState.TEACHERS_TRAINING)
        if tuple(self.teachers) != TEACHER_KINDS:
            raise RuntimeError("seven_teachers_required")
        for kind, teacher in self.teachers.items():
            teacher.freeze()
            teacher.assert_frozen()
            self._teacher_frozen_hashes[kind] = teacher.state_sha256
        self.state = PipelineState.SEVEN_TEACHERS_FROZEN
        return {kind.value: self._teacher_frozen_hashes[kind] for kind in TEACHER_KINDS}

    def begin_student(self, *, seed: int) -> None:
        self._require(PipelineState.SEVEN_TEACHERS_FROZEN)
        torch.manual_seed(seed)
        self.student = TIPSBackbone(self.config, TeacherKind.VANILLA, role="STUDENT").to(self.device)
        self.state = PipelineState.STUDENT_DISTILLING

    def student_step(self, batch: TIPSInferenceBatch, optimizer: torch.optim.Optimizer) -> float:
        if self.state not in (PipelineState.STUDENT_DISTILLING, PipelineState.SWA_ACTIVE):
            raise RuntimeError("student_training_state")
        if type(batch) is not TIPSInferenceBatch or self.student is None:
            raise ValueError("student_label_free_batch")
        before = {kind: state_dict_sha256(model) for kind, model in self.teachers.items()}
        for model in self.teachers.values():
            model.assert_frozen()
        with torch.inference_mode():
            teacher_logits = tuple(self.teachers[kind](batch) for kind in TEACHER_KINDS)
        self.student.train()
        optimizer.zero_grad(set_to_none=True)
        loss = distillation_loss(self.student(batch), teacher_logits)
        loss.backward()
        optimizer.step()
        after = {kind: state_dict_sha256(model) for kind, model in self.teachers.items()}
        if before != after or before != self._teacher_frozen_hashes:
            raise RuntimeError("teacher_state_mutated")
        receipt = StudentStepReceipt(len(self._student_step_receipts) + 1, self.student.state_sha256)
        if self._student_step_receipts and receipt.step_index != self._student_step_receipts[-1].step_index + 1:
            raise RuntimeError("student_step_receipt_order")
        self._student_step_receipts.append(receipt)
        return float(loss.detach().cpu())

    def begin_swa(self) -> None:
        self._require(PipelineState.STUDENT_DISTILLING)
        self.state = PipelineState.SWA_ACTIVE

    def swa_update(self) -> None:
        self._require(PipelineState.SWA_ACTIVE)
        if self.student is None or not self._student_step_receipts:
            raise RuntimeError("student_missing")
        self.swa.update(self.student, step_receipt=self._student_step_receipts[-1])

    def freeze_student(self) -> None:
        self._require(PipelineState.SWA_ACTIVE)
        if self.student is None:
            raise RuntimeError("student_missing")
        self.swa.apply(
            self.student,
            required_swa_updates=self.required_swa_updates,
            student_step_receipts=self.student_step_receipts,
        )
        self.student.freeze()
        self.student.assert_frozen()
        self.state = PipelineState.STUDENT_FROZEN
        self.frozen_swa_student()

    def frozen_swa_student(self) -> FrozenSWAStudent:
        self._require(PipelineState.STUDENT_FROZEN)
        if self.student is None:
            raise RuntimeError("student_missing")
        self.swa.validate(
            self.student,
            required_swa_updates=self.required_swa_updates,
            student_step_receipts=self.student_step_receipts,
        )
        return FrozenSWAStudent(
            model=self.student,
            teacher_state_hashes={kind.value: self._teacher_frozen_hashes[kind] for kind in TEACHER_KINDS},
            snapshots=self.swa.snapshots,
            required_swa_updates=self.required_swa_updates,
            synthetic_only=self.synthetic_only,
            swa_policy_id=self.swa_policy_id,
        )

    def begin_inference(self) -> None:
        self._require(PipelineState.STUDENT_FROZEN)
        self.teachers.clear()
        self.state = PipelineState.INFERENCE

    def infer(self, batch: TIPSInferenceBatch) -> InferenceResult:
        self._require(PipelineState.INFERENCE)
        if type(batch) is not TIPSInferenceBatch or self.student is None or self.teachers:
            raise ValueError("single_student_label_free_inference")
        self.student.assert_frozen()
        with torch.inference_mode():
            first = self.student(batch)
            second = self.student(batch)
        if not torch.equal(first, second) or not torch.isfinite(first).all():
            raise RuntimeError("nondeterministic_inference")
        return InferenceResult(
            symbols=batch.symbols,
            formation_time_ms=batch.formation_time_ms,
            logits=tuple(float(item) for item in first.detach().cpu()),
            student_state_sha256=self.student.state_sha256,
            swa_update_count=self.swa.count,
        )
