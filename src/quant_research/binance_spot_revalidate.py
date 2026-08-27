"""Offline revalidation of frozen Binance Spot monthly Kline payloads.

This successor consumes only immutable local evidence produced by exp004.  It
contains no transport client and cannot fetch, authenticate, or access account
state.  Its output proves archive-row availability only, never historical market
status, eligibility, or executability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from quant_research.binance_spot_payload import (
    CLOSE_TIME_POLICY_WITHIN_OPEN_INTERVAL,
    EXPECTED_CANDIDATE_SYMBOLS,
    EXPECTED_OBJECTS,
    EXPECTED_PAIRS,
    EXPECTED_SYMBOL_LIST_SHA256,
    PANEL_END_MS,
    PANEL_START_MS,
    ChecksumError,
    ContractError,
    FrozenInputs,
    PayloadError,
    PayloadPair,
    build_derived_outputs,
    load_frozen_inputs,
    parse_checksum,
    raw_object_paths,
    validate_kline_zip_bytes,
    _validation_without_rows,
    _write_json,
    _write_jsonl,
)


NON_NOMINAL_CLOSE_REASON = "NON_NOMINAL_CLOSE_TIME_WITHIN_INTERVAL"
EXPECTED_NON_NOMINAL_EVENT_ROWS = 354
EXPECTED_AFFECTED_OBJECT_MONTHS = 353
EXPECTED_NORMALIZED_ROWS = 6_687_797
EXPECTED_STATE_COUNTS = {
    "A": 6_687_797,
    "M": 72_379,
    "N": 6_462_048,
    "U": 0,
}


class OfflineRawError(PayloadError):
    code = "OFFLINE_RAW_ERROR"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_artifact(path: Path, expected_sha256: str, label: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise ContractError(f"invalid {label} SHA-256 {expected_sha256!r}")
    if not path.is_file():
        raise ContractError(f"missing frozen {label}: {path}")
    actual = _sha256_file(path)
    if actual != expected_sha256:
        raise ContractError(
            f"frozen {label} SHA-256 mismatch: expected {expected_sha256}, "
            f"received {actual}"
        )
    return actual


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid frozen {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"frozen {label} must be a JSON object")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ContractError(
                        f"{label} line {line_number} must be a JSON object"
                    )
                rows.append(value)
    except ContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid frozen {label}: {exc}") from exc
    return rows


def _normalized_path_text(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _validate_prior_evidence(
    *,
    row: Mapping[str, Any],
    pair: PayloadPair,
    raw_root: Path,
) -> None:
    if row.get("symbol") != pair.symbol or row.get("month") != pair.month:
        raise ContractError(f"exp004 quality identity mismatch for {pair.symbol} {pair.month}")
    if row.get("zip_key") != pair.zip_object.key:
        raise ContractError(f"exp004 ZIP key mismatch for {pair.symbol} {pair.month}")
    if row.get("checksum_key") != pair.checksum_object.key:
        raise ContractError(f"exp004 CHECKSUM key mismatch for {pair.symbol} {pair.month}")
    zip_path, checksum_path = raw_object_paths(raw_root, pair)
    specifications = (
        ("zip_evidence", pair.zip_object, zip_path),
        ("checksum_evidence", pair.checksum_object, checksum_path),
    )
    for field, inventory_object, expected_path in specifications:
        evidence = row.get(field)
        if not isinstance(evidence, dict):
            raise ContractError(
                f"exp004 quality lacks {field} for {pair.symbol} {pair.month}"
            )
        expected_values = {
            "key": inventory_object.key,
            "frozen_size": inventory_object.size,
            "frozen_etag": inventory_object.etag,
            "downloaded_bytes": inventory_object.size,
            "response_content_length": inventory_object.size,
        }
        for name, expected in expected_values.items():
            if evidence.get(name) != expected:
                raise ContractError(
                    f"exp004 {field}.{name} mismatch for {pair.symbol} {pair.month}: "
                    f"expected {expected!r}, received {evidence.get(name)!r}"
                )
        if _normalized_path_text(evidence.get("local_path", "")) != _normalized_path_text(
            expected_path
        ):
            raise ContractError(
                f"exp004 {field}.local_path mismatch for {pair.symbol} {pair.month}"
            )
        local_sha = evidence.get("local_sha256")
        if not isinstance(local_sha, str) or re.fullmatch(r"[0-9a-f]{64}", local_sha) is None:
            raise ContractError(
                f"exp004 {field}.local_sha256 is invalid for {pair.symbol} {pair.month}"
            )


def load_exp004_provenance(
    *,
    frozen: FrozenInputs,
    object_quality_path: Path,
    object_quality_sha256: str,
    payload_summary_path: Path,
    payload_summary_sha256: str,
    raw_run_contract_path: Path,
    raw_run_contract_sha256: str,
    raw_root: Path,
    expected_pairs: int = EXPECTED_PAIRS,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Bind local bytes to all 9,240 exp004 pair records and 18,480 evidences."""

    _verify_artifact(object_quality_path, object_quality_sha256, "exp004 object quality")
    _verify_artifact(payload_summary_path, payload_summary_sha256, "exp004 payload summary")
    _verify_artifact(raw_run_contract_path, raw_run_contract_sha256, "raw run contract")

    run_contract = _read_json(raw_run_contract_path, "raw run contract")
    required_contract = {
        "inventory_sha256": frozen.inventory_sha256,
        "symbol_index_sha256": frozen.symbol_index_sha256,
        "summary_sha256": frozen.summary_sha256,
        "symbol_list_sha256": frozen.symbol_list_sha256,
        "pair_count": expected_pairs,
        "candidate_symbol_count": len(frozen.candidate_symbols),
    }
    for field, expected in required_contract.items():
        if run_contract.get(field) != expected:
            raise ContractError(
                f"raw run contract {field} mismatch: expected {expected!r}, "
                f"received {run_contract.get(field)!r}"
            )

    payload_summary = _read_json(payload_summary_path, "exp004 payload summary")
    if payload_summary.get("pairs_expected") != expected_pairs:
        raise ContractError("exp004 payload-summary pair count mismatch")
    if payload_summary.get("object_quality_sha256") != object_quality_sha256:
        raise ContractError("exp004 payload summary references different object quality")
    prior_frozen = payload_summary.get("frozen_inputs")
    if not isinstance(prior_frozen, dict):
        raise ContractError("exp004 payload summary lacks frozen input provenance")
    for field, expected in {
        "inventory_sha256": frozen.inventory_sha256,
        "symbol_index_sha256": frozen.symbol_index_sha256,
        "summary_sha256": frozen.summary_sha256,
        "symbol_list_sha256": frozen.symbol_list_sha256,
    }.items():
        if prior_frozen.get(field) != expected:
            raise ContractError(f"exp004 payload summary {field} mismatch")

    rows = _read_jsonl(object_quality_path, "exp004 object quality")
    if len(rows) != expected_pairs:
        raise ContractError(
            f"exp004 object-quality record count mismatch: expected {expected_pairs}, "
            f"received {len(rows)}"
        )
    expected_identities = {(pair.symbol, pair.month): pair for pair in frozen.pairs}
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    evidence_count = 0
    for row in rows:
        identity = (row.get("symbol"), row.get("month"))
        if identity not in expected_identities or identity in indexed:
            raise ContractError(f"unexpected or duplicate exp004 quality identity {identity!r}")
        pair = expected_identities[identity]
        _validate_prior_evidence(row=row, pair=pair, raw_root=raw_root)
        indexed[identity] = row
        evidence_count += 2
    if set(indexed) != set(expected_identities):
        raise ContractError("exp004 object quality does not cover every frozen pair")
    if evidence_count != expected_pairs * 2:
        raise ContractError("exp004 acquisition-evidence count mismatch")
    return indexed


def _revalidate_pair(
    pair: PayloadPair,
    *,
    prior: Mapping[str, Any],
    raw_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    zip_path, checksum_path = raw_object_paths(raw_root, pair)
    quality: dict[str, Any] = {
        "symbol": pair.symbol,
        "month": pair.month,
        "zip_key": pair.zip_object.key,
        "checksum_key": pair.checksum_object.key,
        "status": "U",
        "failure_code": None,
        "failure_reason": None,
        "exp004_status": prior.get("status"),
        "close_time_policy": CLOSE_TIME_POLICY_WITHIN_OPEN_INTERVAL,
    }
    events: list[dict[str, Any]] = []
    try:
        immutable_payloads: dict[str, bytes] = {}
        for label, path, expected_size, prior_field in (
            ("ZIP", zip_path, pair.zip_object.size, "zip_evidence"),
            ("CHECKSUM", checksum_path, pair.checksum_object.size, "checksum_evidence"),
        ):
            if not path.is_file():
                raise OfflineRawError(f"missing local {label} payload: {path}")
            payload = path.read_bytes()
            actual_size = len(payload)
            if actual_size != expected_size:
                raise OfflineRawError(
                    f"local {label} size mismatch for {pair.symbol} {pair.month}: "
                    f"expected {expected_size}, received {actual_size}"
                )
            actual_hash = hashlib.sha256(payload).hexdigest()
            if actual_hash != prior[prior_field]["local_sha256"]:
                raise OfflineRawError(
                    f"local {label} SHA-256 no longer matches exp004 evidence for "
                    f"{pair.symbol} {pair.month}"
                )
            immutable_payloads[label] = payload

        checksum_payload = immutable_payloads["CHECKSUM"]
        expected_zip_sha = parse_checksum(
            checksum_payload, expected_basename=Path(pair.zip_object.key).name
        )
        zip_payload = immutable_payloads["ZIP"]
        actual_zip_sha = hashlib.sha256(zip_payload).hexdigest()
        if actual_zip_sha != expected_zip_sha:
            raise ChecksumError(
                f"official CHECKSUM mismatch for {pair.zip_object.key}: "
                f"expected {expected_zip_sha}, received {actual_zip_sha}"
            )
        validation = validate_kline_zip_bytes(
            zip_payload,
            symbol=pair.symbol,
            month=pair.month,
            close_time_policy=CLOSE_TIME_POLICY_WITHIN_OPEN_INTERVAL,
        )
        quality.update(
            {
                "status": "VALID",
                "zip_local_path": str(zip_path),
                "checksum_local_path": str(checksum_path),
                "zip_sha256": actual_zip_sha,
                "checksum_payload_sha256": hashlib.sha256(checksum_payload).hexdigest(),
                "validation": _validation_without_rows(validation),
                "non_nominal_close_event_count": len(
                    validation.non_nominal_close_events
                ),
                "non_nominal_close_reason": NON_NOMINAL_CLOSE_REASON,
            }
        )
        for event in validation.non_nominal_close_events:
            events.append(
                {
                    "symbol": pair.symbol,
                    "month": pair.month,
                    "open_time_ms": event.open_time_ms,
                    "actual_close_time_ms": event.actual_close_time_ms,
                    "nominal_close_time_ms": event.nominal_close_time_ms,
                    "shortfall_ms": event.shortfall_ms,
                    "source_zip_sha256": actual_zip_sha,
                    "anomaly_code": NON_NOMINAL_CLOSE_REASON,
                    "zip_key": pair.zip_object.key,
                }
            )
    except PayloadError as exc:
        quality["failure_code"] = exc.code
        quality["failure_reason"] = str(exc)
    except (OSError, ValueError) as exc:
        quality["failure_code"] = "OFFLINE_RAW_ERROR"
        quality["failure_reason"] = f"{type(exc).__name__}: {exc}"
    return quality, events


def run_offline_revalidation(
    *,
    inventory_path: Path,
    inventory_sha256: str,
    symbol_index_path: Path,
    symbol_index_sha256: str,
    inventory_summary_path: Path,
    inventory_summary_sha256: str,
    exp004_object_quality_path: Path,
    exp004_object_quality_sha256: str,
    exp004_payload_summary_path: Path,
    exp004_payload_summary_sha256: str,
    raw_run_contract_path: Path,
    raw_run_contract_sha256: str,
    raw_root: Path,
    processed_root: Path,
    summary_output: Path,
    object_quality_output: Path,
    non_nominal_close_output: Path,
    coverage_schema_output: Path,
    derived_index_output: Path,
    expected_objects: int = EXPECTED_OBJECTS,
    expected_pairs: int = EXPECTED_PAIRS,
    expected_candidate_symbols: int = EXPECTED_CANDIDATE_SYMBOLS,
    expected_symbol_list_sha256: str = EXPECTED_SYMBOL_LIST_SHA256,
    panel_start_ms: int = PANEL_START_MS,
    panel_end_ms: int = PANEL_END_MS,
    expected_non_nominal_event_rows: int = EXPECTED_NON_NOMINAL_EVENT_ROWS,
    expected_affected_object_months: int = EXPECTED_AFFECTED_OBJECT_MONTHS,
    expected_normalized_rows: int = EXPECTED_NORMALIZED_ROWS,
    expected_state_counts: Mapping[str, int] | None = None,
) -> tuple[dict[str, Any], int]:
    """Revalidate every frozen pair from local bytes and write a new data version."""

    input_paths = {
        inventory_path.resolve(),
        symbol_index_path.resolve(),
        inventory_summary_path.resolve(),
        exp004_object_quality_path.resolve(),
        exp004_payload_summary_path.resolve(),
        raw_run_contract_path.resolve(),
    }
    output_paths = {
        summary_output.resolve(),
        object_quality_output.resolve(),
        non_nominal_close_output.resolve(),
        coverage_schema_output.resolve(),
        derived_index_output.resolve(),
    }
    if input_paths & output_paths:
        raise ContractError("offline outputs must not overwrite frozen input artifacts")
    if processed_root.resolve() == raw_root.resolve():
        raise ContractError("processed output root must differ from the frozen raw root")

    frozen = load_frozen_inputs(
        inventory_path=inventory_path,
        inventory_sha256=inventory_sha256,
        symbol_index_path=symbol_index_path,
        symbol_index_sha256=symbol_index_sha256,
        summary_path=inventory_summary_path,
        summary_sha256=inventory_summary_sha256,
        expected_objects=expected_objects,
        expected_pairs=expected_pairs,
        expected_candidate_symbols=expected_candidate_symbols,
        expected_symbol_list_sha256=expected_symbol_list_sha256,
    )
    prior = load_exp004_provenance(
        frozen=frozen,
        object_quality_path=exp004_object_quality_path,
        object_quality_sha256=exp004_object_quality_sha256,
        payload_summary_path=exp004_payload_summary_path,
        payload_summary_sha256=exp004_payload_summary_sha256,
        raw_run_contract_path=raw_run_contract_path,
        raw_run_contract_sha256=raw_run_contract_sha256,
        raw_root=raw_root,
        expected_pairs=expected_pairs,
    )

    quality_records: list[dict[str, Any]] = []
    non_nominal_events: list[dict[str, Any]] = []
    for pair in frozen.pairs:
        quality, events = _revalidate_pair(
            pair,
            prior=prior[(pair.symbol, pair.month)],
            raw_root=raw_root,
        )
        quality_records.append(quality)
        non_nominal_events.extend(events)
    quality_records.sort(key=lambda row: (row["symbol"], row["month"]))

    derived = build_derived_outputs(
        frozen=frozen,
        quality_records=quality_records,
        processed_root=processed_root,
        coverage_schema_output=coverage_schema_output,
        derived_index_output=derived_index_output,
        panel_start_ms=panel_start_ms,
        panel_end_ms=panel_end_ms,
        close_time_policy=CLOSE_TIME_POLICY_WITHIN_OPEN_INTERVAL,
    )
    valid_identities = {
        (row["symbol"], row["month"])
        for row in quality_records
        if row["status"] == "VALID"
    }
    non_nominal_events = [
        row
        for row in non_nominal_events
        if (row["symbol"], row["month"]) in valid_identities
    ]
    non_nominal_events.sort(
        key=lambda row: (row["symbol"], row["month"], row["open_time_ms"])
    )
    affected_object_months = len(
        {(row["symbol"], row["month"]) for row in non_nominal_events}
    )
    quality_sha = _write_jsonl(object_quality_output, quality_records)
    event_sha = _write_jsonl(non_nominal_close_output, non_nominal_events)

    valid = len(valid_identities)
    invalid = len(quality_records) - valid
    failure_counts: dict[str, int] = {}
    for row in quality_records:
        if row["failure_code"]:
            code = str(row["failure_code"])
            failure_counts[code] = failure_counts.get(code, 0) + 1
    frozen_state_counts = dict(
        EXPECTED_STATE_COUNTS if expected_state_counts is None else expected_state_counts
    )
    if set(frozen_state_counts) != {"A", "M", "N", "U"}:
        raise ContractError("expected_state_counts must contain exactly A, M, N, and U")
    contract_failures: list[str] = []
    invariants = (
        (
            "non_nominal_close_event_count",
            len(non_nominal_events),
            expected_non_nominal_event_rows,
        ),
        (
            "affected_object_month_count",
            affected_object_months,
            expected_affected_object_months,
        ),
        ("normalized_rows", derived["normalized_rows"], expected_normalized_rows),
    )
    for label, actual, expected in invariants:
        if actual != expected:
            contract_failures.append(
                f"{label}: expected {expected}, received {actual}"
            )
    for state in ("A", "M", "N", "U"):
        actual = derived["state_counts"].get(state)
        expected = frozen_state_counts[state]
        if actual != expected:
            contract_failures.append(
                f"state_counts.{state}: expected {expected}, received {actual}"
            )
    if invalid:
        contract_failures.append(
            f"pairs_invalid: expected 0, received {invalid}"
        )
    complete_success = invalid == 0 and not contract_failures
    summary = {
        "decision": "NEEDS_MORE_DATA" if complete_success else "INCONCLUSIVE",
        "archive_semantics": (
            "ARCHIVE_KLINE_AVAILABLE only; no historical market-status or "
            "eligibility inference"
        ),
        "network_scope": "none",
        "close_time_policy": CLOSE_TIME_POLICY_WITHIN_OPEN_INTERVAL,
        "non_nominal_close_reason": NON_NOMINAL_CLOSE_REASON,
        "pairs_expected": len(frozen.pairs),
        "pairs_valid": valid,
        "pairs_invalid": invalid,
        "exp004_evidence_records": len(prior) * 2,
        "failure_counts": dict(sorted(failure_counts.items())),
        "contract_failures": contract_failures,
        "non_nominal_close_event_count": len(non_nominal_events),
        "affected_object_month_count": affected_object_months,
        "success_contract": {
            "non_nominal_close_event_count": expected_non_nominal_event_rows,
            "affected_object_month_count": expected_affected_object_months,
            "normalized_rows": expected_normalized_rows,
            "state_counts": frozen_state_counts,
        },
        "object_quality_sha256": quality_sha,
        "non_nominal_close_rows_sha256": event_sha,
        "frozen_input_sha256": {
            "inventory": frozen.inventory_sha256,
            "symbol_index": frozen.symbol_index_sha256,
            "inventory_summary": frozen.summary_sha256,
            "exp004_object_quality": exp004_object_quality_sha256,
            "exp004_payload_summary": exp004_payload_summary_sha256,
            "raw_run_contract": raw_run_contract_sha256,
        },
        "derived": derived,
    }
    summary_sha = _write_json(summary_output, summary, pretty=True)
    summary["summary_sha256"] = summary_sha
    return summary, 0 if complete_success else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Revalidate frozen Binance Spot Kline payloads entirely offline."
    )
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--symbol-index", required=True, type=Path)
    parser.add_argument("--symbol-index-sha256", required=True)
    parser.add_argument("--inventory-summary", required=True, type=Path)
    parser.add_argument("--inventory-summary-sha256", required=True)
    parser.add_argument("--exp004-object-quality", required=True, type=Path)
    parser.add_argument("--exp004-object-quality-sha256", required=True)
    parser.add_argument("--exp004-payload-summary", required=True, type=Path)
    parser.add_argument("--exp004-payload-summary-sha256", required=True)
    parser.add_argument("--raw-run-contract", required=True, type=Path)
    parser.add_argument("--raw-run-contract-sha256", required=True)
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--processed-root", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--object-quality-output", required=True, type=Path)
    parser.add_argument("--non-nominal-close-output", required=True, type=Path)
    parser.add_argument("--coverage-schema-output", required=True, type=Path)
    parser.add_argument("--derived-index-output", required=True, type=Path)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(arguments)
    try:
        summary, exit_code = run_offline_revalidation(
            inventory_path=options.inventory,
            inventory_sha256=options.inventory_sha256,
            symbol_index_path=options.symbol_index,
            symbol_index_sha256=options.symbol_index_sha256,
            inventory_summary_path=options.inventory_summary,
            inventory_summary_sha256=options.inventory_summary_sha256,
            exp004_object_quality_path=options.exp004_object_quality,
            exp004_object_quality_sha256=options.exp004_object_quality_sha256,
            exp004_payload_summary_path=options.exp004_payload_summary,
            exp004_payload_summary_sha256=options.exp004_payload_summary_sha256,
            raw_run_contract_path=options.raw_run_contract,
            raw_run_contract_sha256=options.raw_run_contract_sha256,
            raw_root=options.raw_root,
            processed_root=options.processed_root,
            summary_output=options.summary_output,
            object_quality_output=options.object_quality_output,
            non_nominal_close_output=options.non_nominal_close_output,
            coverage_schema_output=options.coverage_schema_output,
            derived_index_output=options.derived_index_output,
        )
    except PayloadError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
