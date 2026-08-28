"""Narrow synthetic prediction adapter for the hierarchical-alpha type."""

from __future__ import annotations

import hashlib
import json

from quant_research.hierarchical_alpha import ExpertKey, ExpertOutput

from .contracts import DDGLConfig, DDGLContractError
from .training import DDGLPrediction


def _sha256(parts: object) -> str:
    return hashlib.sha256(
        json.dumps(
            parts,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def predictions_to_expert_outputs(
    predictions: tuple[DDGLPrediction, ...], config: DDGLConfig
) -> tuple[ExpertOutput, ...]:
    if type(config) is not DDGLConfig or config.empirical_authorized:
        raise DDGLContractError("adapter is synthetic-only")
    if type(predictions) is not tuple or not predictions:
        raise DDGLContractError("adapter requires exact non-empty prediction tuple")
    key = ExpertKey("SyntheticGraph", "ddgl_clean_room", config.horizon_hours, "v1")
    outputs: list[ExpertOutput] = []
    for prediction in predictions:
        if type(prediction) is not DDGLPrediction:
            raise DDGLContractError("adapter accepts only DDGLPrediction")
        for symbol, value in zip(prediction.symbols, prediction.values, strict=True):
            outputs.append(
                ExpertOutput(
                    key=key,
                    symbol=symbol,
                    formation_time_ms=prediction.formation_time_ms,
                    known_at_ms=prediction.known_at_ms,
                    value=float(value),
                    provenance_sha256=_sha256(
                        (
                            "ddgl-synthetic-expert-output-v1",
                            config.config_sha256,
                            prediction.input_provenance_sha256,
                            symbol,
                            float(value),
                        )
                    ),
                )
            )
    return tuple(outputs)


__all__ = ["predictions_to_expert_outputs"]
