"""Strict, torch-free contracts for the DDGL synthetic experiment.

The structures here intentionally use immutable Python tuples.  Tensor
materialization lives in :mod:`training`, and inference accepts no label type.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXPERIMENT_ID = "exp_20260827_006"
STAGE = "SYNTHETIC_CONTRACT_ONLY"
SOURCE_STATUS = "UNVERIFIED_THIRD_PARTY"
SCHEMA_VERSION = "ddgl_synthetic_config_v1"
MODEL_NAME = "DDGL_CLEAN_ROOM_SYNTHETIC_V1"
HOUR_MILLISECONDS = 3_600_000
ALPHA_HORIZONS_HOURS = (1, 24, 120, 480)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DDGLContractError(ValueError):
    """Any fail-closed config, input, label or provenance violation."""


def _reject_constant(value: str) -> None:
    raise DDGLContractError(f"non-finite JSON constant {value!r} is forbidden")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DDGLContractError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _provenance(*parts: object) -> str:
    return _sha256(_canonical_bytes(parts))


def _exact_keys(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        actual = set(value) if type(value) is dict else type(value).__name__
        raise DDGLContractError(
            f"{label} must have exact keys {sorted(expected)!r}; got {actual!r}"
        )
    return value


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise DDGLContractError(f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise DDGLContractError(f"{label} must be an exact integer >= {minimum}")
    return value


def _number(value: object, label: str, *, minimum: float | None = None) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise DDGLContractError(f"{label} must be finite")
    result = float(value)
    if minimum is not None and result < minimum:
        raise DDGLContractError(f"{label} must be >= {minimum}")
    return result


def _hash(value: object, label: str) -> str:
    if type(value) is not str or SHA256_PATTERN.fullmatch(value) is None:
        raise DDGLContractError(f"{label} must be a lowercase SHA-256")
    return value


@dataclass(frozen=True)
class SourceContract:
    paper_doi: str
    paper_metadata_status: str
    paper_license_claim: str
    community_repository: str
    community_commit: str
    community_license_claim: str
    source_status: str
    implementation_claim: str


@dataclass(frozen=True)
class SyntheticSpec:
    num_samples: int
    num_assets: int
    coarse_steps: int
    fine_steps: int
    global_steps: int
    coarse_features: int
    fine_features: int
    global_features: int
    formation_start_ms: int


@dataclass(frozen=True)
class ArchitectureSpec:
    hidden_dim: int
    graph_top_k: int
    moe_experts: int
    moe_hidden_dim: int
    dropout: float


@dataclass(frozen=True)
class OptimizationSpec:
    epochs: int
    batch_size: int
    learning_rate: float
    weight_decay: float
    grad_clip_norm: float
    precision: str
    allow_precision_fallback: bool


@dataclass(frozen=True)
class RuntimeSpec:
    device: str
    max_vram_mib: int
    min_free_vram_mib: int


@dataclass(frozen=True)
class DDGLConfig:
    schema_version: str
    experiment_id: str
    stage: str
    model_name: str
    source: SourceContract
    empirical_authorized: bool
    horizon_hours: int
    seed: int
    synthetic: SyntheticSpec
    architecture: ArchitectureSpec
    optimization: OptimizationSpec
    runtime: RuntimeSpec
    config_sha256: str


CONFIG_KEYS = {
    "schema_version",
    "experiment_id",
    "stage",
    "model_name",
    "source",
    "empirical_authorized",
    "horizon_hours",
    "seed",
    "synthetic",
    "architecture",
    "optimization",
    "runtime",
}
SOURCE_KEYS = {
    "paper_doi",
    "paper_metadata_status",
    "paper_license_claim",
    "community_repository",
    "community_commit",
    "community_license_claim",
    "source_status",
    "implementation_claim",
}
SYNTHETIC_KEYS = {
    "num_samples",
    "num_assets",
    "coarse_steps",
    "fine_steps",
    "global_steps",
    "coarse_features",
    "fine_features",
    "global_features",
    "formation_start_ms",
}
ARCHITECTURE_KEYS = {
    "hidden_dim",
    "graph_top_k",
    "moe_experts",
    "moe_hidden_dim",
    "dropout",
}
OPTIMIZATION_KEYS = {
    "epochs",
    "batch_size",
    "learning_rate",
    "weight_decay",
    "grad_clip_norm",
    "precision",
    "allow_precision_fallback",
}
RUNTIME_KEYS = {"device", "max_vram_mib", "min_free_vram_mib"}


def _parse_config(raw: bytes) -> DDGLConfig:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise DDGLContractError("config must not contain a UTF-8 BOM")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise DDGLContractError("config must be strict UTF-8") from error
    if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise DDGLContractError("config must not contain surrogate code points")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, TypeError) as error:
        raise DDGLContractError("config is not strict JSON") from error
    config = _exact_keys(value, CONFIG_KEYS, "config")
    source_value = _exact_keys(config["source"], SOURCE_KEYS, "source")
    synthetic_value = _exact_keys(
        config["synthetic"], SYNTHETIC_KEYS, "synthetic"
    )
    architecture_value = _exact_keys(
        config["architecture"], ARCHITECTURE_KEYS, "architecture"
    )
    optimization_value = _exact_keys(
        config["optimization"], OPTIMIZATION_KEYS, "optimization"
    )
    runtime_value = _exact_keys(config["runtime"], RUNTIME_KEYS, "runtime")

    source = SourceContract(
        *(_text(source_value[key], f"source.{key}") for key in SOURCE_KEYS_IN_ORDER)
    )
    if source.paper_doi != "10.1145/3770855.3817765":
        raise DDGLContractError("unexpected paper DOI")
    if source.community_commit != "9c1152d8572550d0a869d898f65f208c52706747":
        raise DDGLContractError("unexpected community commit")
    if source.source_status != SOURCE_STATUS:
        raise DDGLContractError("source status must remain UNVERIFIED_THIRD_PARTY")
    if source.implementation_claim != "CLEAN_ROOM_NOT_OFFICIAL_NOT_PAPER_FAITHFUL":
        raise DDGLContractError("implementation claim is not conservative")

    synthetic = SyntheticSpec(
        *(
            _integer(synthetic_value[key], f"synthetic.{key}", minimum=1)
            for key in SYNTHETIC_KEYS_IN_ORDER[:-1]
        ),
        _integer(synthetic_value["formation_start_ms"], "synthetic.formation_start_ms"),
    )
    if synthetic.num_assets < 2:
        raise DDGLContractError("synthetic graph requires at least two assets")

    architecture = ArchitectureSpec(
        hidden_dim=_integer(architecture_value["hidden_dim"], "hidden_dim", minimum=2),
        graph_top_k=_integer(architecture_value["graph_top_k"], "graph_top_k", minimum=1),
        moe_experts=_integer(architecture_value["moe_experts"], "moe_experts", minimum=1),
        moe_hidden_dim=_integer(
            architecture_value["moe_hidden_dim"], "moe_hidden_dim", minimum=2
        ),
        dropout=_number(architecture_value["dropout"], "dropout", minimum=0),
    )
    if architecture.graph_top_k > synthetic.num_assets:
        raise DDGLContractError("graph_top_k exceeds num_assets")
    if architecture.dropout >= 1:
        raise DDGLContractError("dropout must be < 1")

    precision = _text(optimization_value["precision"], "precision")
    if precision not in {"FP32", "BF16"}:
        raise DDGLContractError("precision must be FP32 or BF16")
    if type(optimization_value["allow_precision_fallback"]) is not bool:
        raise DDGLContractError("allow_precision_fallback must be bool")
    optimization = OptimizationSpec(
        epochs=_integer(optimization_value["epochs"], "epochs", minimum=1),
        batch_size=_integer(optimization_value["batch_size"], "batch_size", minimum=1),
        learning_rate=_number(
            optimization_value["learning_rate"], "learning_rate", minimum=0
        ),
        weight_decay=_number(
            optimization_value["weight_decay"], "weight_decay", minimum=0
        ),
        grad_clip_norm=_number(
            optimization_value["grad_clip_norm"], "grad_clip_norm", minimum=0
        ),
        precision=precision,
        allow_precision_fallback=optimization_value["allow_precision_fallback"],
    )
    if optimization.learning_rate <= 0 or optimization.grad_clip_norm <= 0:
        raise DDGLContractError("learning rate and grad clip must be positive")

    device = _text(runtime_value["device"], "runtime.device")
    if device not in {"cpu", "cuda"}:
        raise DDGLContractError("runtime.device must be cpu or cuda")
    runtime = RuntimeSpec(
        device=device,
        max_vram_mib=_integer(runtime_value["max_vram_mib"], "max_vram_mib"),
        min_free_vram_mib=_integer(
            runtime_value["min_free_vram_mib"], "min_free_vram_mib"
        ),
    )
    if device == "cuda" and (runtime.max_vram_mib <= 0 or runtime.min_free_vram_mib <= 0):
        raise DDGLContractError("CUDA config requires positive VRAM gates")
    if device == "cpu" and (runtime.max_vram_mib or runtime.min_free_vram_mib):
        raise DDGLContractError("CPU config must use zero VRAM gates")
    if device == "cuda" and (
        synthetic.num_assets > 32
        or synthetic.coarse_steps > 8
        or synthetic.fine_steps > 30
        or architecture.hidden_dim > 96
        or optimization.batch_size != 1
        or runtime.max_vram_mib > 8192
    ):
        raise DDGLContractError("CUDA plan exceeds the frozen 16GB shared-device cap")

    if config["schema_version"] != SCHEMA_VERSION:
        raise DDGLContractError("unsupported config schema")
    if config["experiment_id"] != EXPERIMENT_ID or config["stage"] != STAGE:
        raise DDGLContractError("config identity or stage mismatch")
    if config["model_name"] != MODEL_NAME:
        raise DDGLContractError("unexpected model name")
    if type(config["empirical_authorized"]) is not bool or config["empirical_authorized"]:
        raise DDGLContractError("empirical authorization must be exact false")
    horizon = _integer(config["horizon_hours"], "horizon_hours", minimum=1)
    if horizon not in ALPHA_HORIZONS_HOURS:
        raise DDGLContractError("unsupported exact horizon")
    seed = _integer(config["seed"], "seed")

    return DDGLConfig(
        schema_version=SCHEMA_VERSION,
        experiment_id=EXPERIMENT_ID,
        stage=STAGE,
        model_name=MODEL_NAME,
        source=source,
        empirical_authorized=False,
        horizon_hours=horizon,
        seed=seed,
        synthetic=synthetic,
        architecture=architecture,
        optimization=optimization,
        runtime=runtime,
        config_sha256=_sha256(raw),
    )


SOURCE_KEYS_IN_ORDER = (
    "paper_doi",
    "paper_metadata_status",
    "paper_license_claim",
    "community_repository",
    "community_commit",
    "community_license_claim",
    "source_status",
    "implementation_claim",
)
SYNTHETIC_KEYS_IN_ORDER = (
    "num_samples",
    "num_assets",
    "coarse_steps",
    "fine_steps",
    "global_steps",
    "coarse_features",
    "fine_features",
    "global_features",
    "formation_start_ms",
)


def load_ddgl_config(path: str | Path) -> DDGLConfig:
    return _parse_config(Path(path).read_bytes())


def parse_ddgl_config_bytes(raw: bytes) -> DDGLConfig:
    if type(raw) is not bytes:
        raise DDGLContractError("config payload must be exact bytes")
    return _parse_config(raw)


Tensor3 = tuple[tuple[tuple[float, ...], ...], ...]
Tensor2 = tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class DDGLInputBatch:
    symbols: tuple[str, ...]
    formation_time_ms: int
    horizon_hours: int
    coarse_values: Tensor3
    fine_values: Tensor3
    global_market_values: Tensor2
    coarse_known_at_ms: tuple[int, ...]
    fine_known_at_ms: tuple[int, ...]
    global_known_at_ms: tuple[int, ...]
    provenance_sha256: str


@dataclass(frozen=True)
class DDGLLabelBatch:
    values: tuple[float, ...]
    horizon_hours: int
    known_at_ms: int
    provenance_sha256: str


@dataclass(frozen=True)
class DDGLTrainingExample:
    inputs: DDGLInputBatch
    labels: DDGLLabelBatch


def _validate_tensor3(
    value: object,
    *,
    steps: int,
    assets: int,
    features: int,
    label: str,
) -> Tensor3:
    if type(value) is not tuple or len(value) != steps:
        raise DDGLContractError(f"{label} has invalid time dimension")
    for time_row in value:
        if type(time_row) is not tuple or len(time_row) != assets:
            raise DDGLContractError(f"{label} has invalid asset dimension")
        for asset_row in time_row:
            if type(asset_row) is not tuple or len(asset_row) != features:
                raise DDGLContractError(f"{label} has invalid feature dimension")
            for item in asset_row:
                _number(item, label)
    return value


def _validate_tensor2(
    value: object, *, steps: int, features: int, label: str
) -> Tensor2:
    if type(value) is not tuple or len(value) != steps:
        raise DDGLContractError(f"{label} has invalid time dimension")
    for row in value:
        if type(row) is not tuple or len(row) != features:
            raise DDGLContractError(f"{label} has invalid feature dimension")
        for item in row:
            _number(item, label)
    return value


def _validate_clock_vector(
    value: object, *, steps: int, formation_time_ms: int, label: str
) -> tuple[int, ...]:
    if type(value) is not tuple or len(value) != steps:
        raise DDGLContractError(f"{label} has invalid length")
    prior = -1
    for clock in value:
        _integer(clock, label)
        if clock <= prior or clock > formation_time_ms:
            raise DDGLContractError(f"{label} must strictly increase and not be future")
        prior = clock
    return value


def input_projection(inputs: DDGLInputBatch) -> dict[str, object]:
    return {
        "coarse_known_at_ms": inputs.coarse_known_at_ms,
        "coarse_values": inputs.coarse_values,
        "fine_known_at_ms": inputs.fine_known_at_ms,
        "fine_values": inputs.fine_values,
        "formation_time_ms": inputs.formation_time_ms,
        "global_known_at_ms": inputs.global_known_at_ms,
        "global_market_values": inputs.global_market_values,
        "horizon_hours": inputs.horizon_hours,
        "symbols": inputs.symbols,
    }


def validate_input(inputs: object, config: DDGLConfig) -> DDGLInputBatch:
    if type(inputs) is not DDGLInputBatch:
        raise DDGLContractError("inference accepts only DDGLInputBatch")
    if type(inputs.symbols) is not tuple or len(inputs.symbols) != config.synthetic.num_assets:
        raise DDGLContractError("input symbols do not match configured assets")
    if any(type(symbol) is not str or not symbol for symbol in inputs.symbols):
        raise DDGLContractError("input symbols must be non-empty strings")
    if len(set(inputs.symbols)) != len(inputs.symbols):
        raise DDGLContractError("input symbols must be unique")
    _integer(inputs.formation_time_ms, "formation_time_ms")
    if type(inputs.horizon_hours) is not int or inputs.horizon_hours != config.horizon_hours:
        raise DDGLContractError("input horizon must exactly match config")
    spec = config.synthetic
    _validate_tensor3(
        inputs.coarse_values,
        steps=spec.coarse_steps,
        assets=spec.num_assets,
        features=spec.coarse_features,
        label="coarse_values",
    )
    _validate_tensor3(
        inputs.fine_values,
        steps=spec.fine_steps,
        assets=spec.num_assets,
        features=spec.fine_features,
        label="fine_values",
    )
    _validate_tensor2(
        inputs.global_market_values,
        steps=spec.global_steps,
        features=spec.global_features,
        label="global_market_values",
    )
    _validate_clock_vector(
        inputs.coarse_known_at_ms,
        steps=spec.coarse_steps,
        formation_time_ms=inputs.formation_time_ms,
        label="coarse_known_at_ms",
    )
    _validate_clock_vector(
        inputs.fine_known_at_ms,
        steps=spec.fine_steps,
        formation_time_ms=inputs.formation_time_ms,
        label="fine_known_at_ms",
    )
    _validate_clock_vector(
        inputs.global_known_at_ms,
        steps=spec.global_steps,
        formation_time_ms=inputs.formation_time_ms,
        label="global_known_at_ms",
    )
    _hash(inputs.provenance_sha256, "input provenance")
    expected = _provenance("ddgl-input-v1", config.config_sha256, input_projection(inputs))
    if inputs.provenance_sha256 != expected:
        raise DDGLContractError("input provenance mismatch")
    return inputs


def label_projection(labels: DDGLLabelBatch) -> dict[str, object]:
    return {
        "horizon_hours": labels.horizon_hours,
        "known_at_ms": labels.known_at_ms,
        "values": labels.values,
    }


def validate_training_example(
    example: object, config: DDGLConfig
) -> DDGLTrainingExample:
    if type(example) is not DDGLTrainingExample:
        raise DDGLContractError("trainer accepts only DDGLTrainingExample")
    inputs = validate_input(example.inputs, config)
    labels = example.labels
    if type(labels) is not DDGLLabelBatch:
        raise DDGLContractError("training label type is invalid")
    if type(labels.values) is not tuple or len(labels.values) != len(inputs.symbols):
        raise DDGLContractError("label asset dimension mismatch")
    for item in labels.values:
        _number(item, "label")
    if type(labels.horizon_hours) is not int or labels.horizon_hours != config.horizon_hours:
        raise DDGLContractError("label horizon mismatch")
    expected_known = inputs.formation_time_ms + labels.horizon_hours * HOUR_MILLISECONDS
    if type(labels.known_at_ms) is not int or labels.known_at_ms != expected_known:
        raise DDGLContractError("label clock must equal formation plus horizon")
    _hash(labels.provenance_sha256, "label provenance")
    expected = _provenance(
        "ddgl-label-v1",
        config.config_sha256,
        inputs.provenance_sha256,
        label_projection(labels),
    )
    if labels.provenance_sha256 != expected:
        raise DDGLContractError("label provenance mismatch")
    return example


def make_synthetic_examples(config: DDGLConfig) -> tuple[DDGLTrainingExample, ...]:
    """Create deterministic, causally clocked synthetic examples only."""

    if type(config) is not DDGLConfig or config.empirical_authorized:
        raise DDGLContractError("synthetic generator requires a non-empirical config")
    spec = config.synthetic
    rng = random.Random(config.seed)
    symbols = tuple(f"SYNTH_{index:03d}" for index in range(spec.num_assets))
    examples: list[DDGLTrainingExample] = []
    for sample in range(spec.num_samples):
        formation = spec.formation_start_ms + sample * HOUR_MILLISECONDS
        coarse = tuple(
            tuple(
                tuple(
                    math.sin((sample + 1) * 0.13 + time * 0.07 + asset * 0.11 + feature)
                    + rng.uniform(-0.01, 0.01)
                    for feature in range(spec.coarse_features)
                )
                for asset in range(spec.num_assets)
            )
            for time in range(spec.coarse_steps)
        )
        fine = tuple(
            tuple(
                tuple(
                    math.cos((sample + 1) * 0.17 + time * 0.05 + asset * 0.09 + feature)
                    + rng.uniform(-0.01, 0.01)
                    for feature in range(spec.fine_features)
                )
                for asset in range(spec.num_assets)
            )
            for time in range(spec.fine_steps)
        )
        global_values = tuple(
            tuple(
                math.sin((sample + 1) * 0.19 + time * 0.03 + feature * 0.23)
                for feature in range(spec.global_features)
            )
            for time in range(spec.global_steps)
        )
        coarse_known = tuple(
            formation - (spec.coarse_steps - 1 - index) * 4 * HOUR_MILLISECONDS
            for index in range(spec.coarse_steps)
        )
        fine_known = tuple(
            formation - (spec.fine_steps - 1 - index) * HOUR_MILLISECONDS
            for index in range(spec.fine_steps)
        )
        global_known = tuple(
            formation - (spec.global_steps - 1 - index) * HOUR_MILLISECONDS
            for index in range(spec.global_steps)
        )
        temporary = DDGLInputBatch(
            symbols=symbols,
            formation_time_ms=formation,
            horizon_hours=config.horizon_hours,
            coarse_values=coarse,
            fine_values=fine,
            global_market_values=global_values,
            coarse_known_at_ms=coarse_known,
            fine_known_at_ms=fine_known,
            global_known_at_ms=global_known,
            provenance_sha256="0" * 64,
        )
        inputs = DDGLInputBatch(
            symbols=temporary.symbols,
            formation_time_ms=temporary.formation_time_ms,
            horizon_hours=temporary.horizon_hours,
            coarse_values=temporary.coarse_values,
            fine_values=temporary.fine_values,
            global_market_values=temporary.global_market_values,
            coarse_known_at_ms=temporary.coarse_known_at_ms,
            fine_known_at_ms=temporary.fine_known_at_ms,
            global_known_at_ms=temporary.global_known_at_ms,
            provenance_sha256=_provenance(
                "ddgl-input-v1", config.config_sha256, input_projection(temporary)
            ),
        )
        cross_mean = sum(row[0] for row in fine[-1]) / spec.num_assets
        targets = tuple(
            0.55 * fine[-1][asset][0]
            + 0.25 * coarse[-1][asset][0]
            + 0.15 * global_values[-1][0]
            + 0.05 * cross_mean
            for asset in range(spec.num_assets)
        )
        temporary_label = DDGLLabelBatch(
            values=targets,
            horizon_hours=config.horizon_hours,
            known_at_ms=formation + config.horizon_hours * HOUR_MILLISECONDS,
            provenance_sha256="0" * 64,
        )
        labels = DDGLLabelBatch(
            values=temporary_label.values,
            horizon_hours=temporary_label.horizon_hours,
            known_at_ms=temporary_label.known_at_ms,
            provenance_sha256=_provenance(
                "ddgl-label-v1",
                config.config_sha256,
                inputs.provenance_sha256,
                label_projection(temporary_label),
            ),
        )
        example = DDGLTrainingExample(inputs, labels)
        validate_training_example(example, config)
        examples.append(example)
    return tuple(examples)


__all__ = [
    "ALPHA_HORIZONS_HOURS",
    "DDGLConfig",
    "DDGLContractError",
    "DDGLInputBatch",
    "DDGLLabelBatch",
    "DDGLTrainingExample",
    "EXPERIMENT_ID",
    "HOUR_MILLISECONDS",
    "MODEL_NAME",
    "SOURCE_STATUS",
    "STAGE",
    "input_projection",
    "load_ddgl_config",
    "make_synthetic_examples",
    "parse_ddgl_config_bytes",
    "validate_input",
    "validate_training_example",
]
