"""Ordinary one-shot publisher for exp_20260826_006 LEAN_R2_2_2.

No network imports or account functionality exist here.  Phase 1/2 tests may
import helpers, but the frozen command must not run before independent GO.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
from typing import Any, Mapping, Sequence

import binance_spot_announcement_claims_lean_v1 as extractor
import binance_spot_announcement_claims_lean_v1_loader as loader


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FINAL_ROOT = REPO_ROOT / (
    "data/processed/binance_spot_announcement_claims_v1/runs/"
    "exp_20260826_006_formal_001"
)
STAGING_ROOT = FINAL_ROOT.parent / ".exp_20260826_006_formal_001.staging"
CONTROL_ROOT = FINAL_ROOT.parent / ".exp_20260826_006_formal_001.control"

BINDING_SPECS: tuple[tuple[str, str, str], ...] = (
    (
        "--expected-runner-sha256", "runner",
        "src/quant_research/binance_spot_announcement_claims_lean_v1_runner.py",
    ),
    (
        "--expected-extractor-sha256", "extractor",
        "src/quant_research/binance_spot_announcement_claims_lean_v1.py",
    ),
    (
        "--expected-loader-sha256", "loader",
        "src/quant_research/binance_spot_announcement_claims_lean_v1_loader.py",
    ),
    (
        "--expected-source-contract-sha256", "source_contract",
        "experiments/exp_20260826_006/artifacts/source_contract.json",
    ),
    (
        "--expected-schema-sha256", "schema",
        "experiments/exp_20260826_006/artifacts/schema.json",
    ),
    (
        "--expected-parameters-sha256", "parameters",
        "experiments/exp_20260826_006/parameters.json",
    ),
)

EXIT_PREEXISTENCE = 10
EXIT_SOURCE_BINDING = 20
EXIT_INPUT = 21
EXIT_OUTPUT = 22
EXIT_PROMOTION = 23
EXIT_INTERNAL = 70


class RunnerError(RuntimeError):
    def __init__(self, failure_code: str, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code
        self.exit_code = exit_code


def _hex64(value: str) -> str:
    if (
        len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise argparse.ArgumentTypeError("expected lowercase SHA-256")
    return value


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    expected_flags = [spec[0] for spec in BINDING_SPECS]
    if len(argv) != len(expected_flags) * 2:
        raise RunnerError(
            "SOURCE_BINDING", EXIT_SOURCE_BINDING,
            "exactly six expected SHA flags are required",
        )
    if list(argv[0::2]) != expected_flags:
        raise RunnerError(
            "SOURCE_BINDING", EXIT_SOURCE_BINDING,
            "expected SHA flags are missing, duplicated, or out of order",
        )
    parser = argparse.ArgumentParser(allow_abbrev=False)
    for flag in expected_flags:
        parser.add_argument(flag, required=True, type=_hex64)
    try:
        return parser.parse_args(list(argv))
    except SystemExit as exc:
        raise RunnerError(
            "SOURCE_BINDING", EXIT_SOURCE_BINDING,
            "invalid expected SHA argument",
        ) from exc


def expected_bindings(args: argparse.Namespace) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for flag, name, path in BINDING_SPECS:
        attribute = flag.removeprefix("--").replace("-", "_")
        result[name] = {"path": path, "sha256": getattr(args, attribute)}
    return result


def verify_bindings(
    repo_root: pathlib.Path, bindings: Mapping[str, Mapping[str, str]],
) -> None:
    if set(bindings) != {spec[1] for spec in BINDING_SPECS}:
        raise RunnerError(
            "SOURCE_BINDING", EXIT_SOURCE_BINDING,
            "binding name set mismatch",
        )
    for _flag, name, relative in BINDING_SPECS:
        if bindings[name] != {
            "path": relative, "sha256": bindings[name]["sha256"],
        }:
            raise RunnerError(
                "SOURCE_BINDING", EXIT_SOURCE_BINDING,
                f"binding path mismatch: {name}",
            )
        path = repo_root.joinpath(*pathlib.PurePosixPath(relative).parts)
        if (
            not path.is_file()
            or extractor.sha256_file(path) != bindings[name]["sha256"]
        ):
            raise RunnerError(
                "SOURCE_BINDING", EXIT_SOURCE_BINDING,
                f"binding hash mismatch: {name}",
            )


def formal_command(bindings: Mapping[str, Mapping[str, str]]) -> str:
    parts = [
        r".venv\Scripts\python.exe", "-B",
        r"src\quant_research\binance_spot_announcement_claims_lean_v1_runner.py",
    ]
    for flag, name, _path in BINDING_SPECS:
        parts.extend((flag, bindings[name]["sha256"]))
    return " ".join(parts)


def _write_once(path: pathlib.Path, value: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _failure(code: str, exit_code: int) -> None:
    failure = {
        "experiment_id": extractor.EXPERIMENT_ID,
        "run_id": extractor.RUN_ID,
        "failure_code": code,
        "exit_code": exit_code,
    }
    _write_once(
        CONTROL_ROOT / "failure.json",
        extractor.canonical_pretty(failure),
    )


def _run(args: argparse.Namespace) -> int:
    bindings = expected_bindings(args)
    if any(path.exists() for path in (FINAL_ROOT, STAGING_ROOT, CONTROL_ROOT)):
        return EXIT_PREEXISTENCE
    verify_bindings(REPO_ROOT, bindings)
    CONTROL_ROOT.mkdir(parents=False, exist_ok=False)
    formal_sha = hashlib.sha256(
        formal_command(bindings).encode("utf-8")
    ).hexdigest()
    expected_sha = hashlib.sha256(
        extractor.canonical_compact(bindings)
    ).hexdigest()
    lease = {
        "experiment_id": extractor.EXPERIMENT_ID,
        "run_id": extractor.RUN_ID,
        "formal_command_sha256": formal_sha,
        "expected_bindings_sha256": expected_sha,
    }
    _write_once(
        CONTROL_ROOT / "lease.json", extractor.canonical_pretty(lease),
    )
    try:
        STAGING_ROOT.mkdir(parents=False, exist_ok=False)
        try:
            payload = extractor.extract(REPO_ROOT, bindings)
        except extractor.ClaimsError as exc:
            if exc.code == "INPUT_BINDING":
                code = "INPUT_BINDING"
                exit_code = EXIT_INPUT
            elif exc.code == "INPUT_BIJECTION":
                code = "INPUT_BIJECTION"
                exit_code = EXIT_INPUT
            elif exc.code in {"OUTPUT_SCHEMA", "OUTPUT_INTEGRITY"}:
                code = exc.code
                exit_code = EXIT_OUTPUT
            else:
                code = "INPUT_SCHEMA"
                exit_code = EXIT_INPUT
            raise RunnerError(code, exit_code, str(exc)) from exc
        for name, raw in payload["payload_bytes"].items():
            _write_once(STAGING_ROOT / name, raw)
        _write_once(
            STAGING_ROOT / "summary.json", payload["summary_bytes"],
        )
        details = payload["accepted_details"]
        try:
            loaded = loader.validate_directory(
                STAGING_ROOT,
                expected_article_codes=[
                    detail.article_code for detail in details
                ],
                expected_detail_bindings={
                    detail.article_code: (
                        detail.response_sha256, detail.body_sha256,
                    )
                    for detail in details
                },
                expected_input_bindings=extractor.INPUT_BINDINGS,
                expected_code_bindings=bindings,
            )
        except loader.ClaimsLoadError as exc:
            raise RunnerError(
                "OUTPUT_INTEGRITY", EXIT_OUTPUT, str(exc),
            ) from exc
        if (
            loaded.terminal_status != "NEEDS_MORE_DATA"
            or loaded.summary["strict_eligible_count"] != 0
        ):
            raise RunnerError(
                "OUTPUT_SCHEMA", EXIT_OUTPUT, "semantic ceiling mismatch",
            )
        verify_bindings(REPO_ROOT, bindings)
        final_tree = loader.final_tree_sha256(STAGING_ROOT)
        authorization = {
            "experiment_id": extractor.EXPERIMENT_ID,
            "run_id": extractor.RUN_ID,
            "summary_sha256": extractor.sha256_file(
                STAGING_ROOT / "summary.json",
            ),
            "payload_tree_sha256": loaded.summary[
                "payload_tree_sha256"
            ],
            "final_tree_sha256": final_tree,
        }
        _write_once(
            CONTROL_ROOT / "authorization.json",
            extractor.canonical_pretty(authorization),
        )
        try:
            os.rename(STAGING_ROOT, FINAL_ROOT)
        except OSError as exc:
            (CONTROL_ROOT / "authorization.json").unlink()
            raise RunnerError(
                "PROMOTION", EXIT_PROMOTION, "atomic rename failed",
            ) from exc
        return 0
    except RunnerError:
        raise
    except Exception as exc:
        raise RunnerError("INTERNAL", EXIT_INTERNAL, "internal failure") from exc


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        return _run(args)
    except RunnerError as exc:
        if CONTROL_ROOT.exists():
            if (
                (CONTROL_ROOT / "lease.json").is_file()
                and not (CONTROL_ROOT / "failure.json").exists()
                and not (CONTROL_ROOT / "authorization.json").exists()
                and not FINAL_ROOT.exists()
            ):
                _failure(exc.failure_code, exc.exit_code)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
