"""Trusted loader for LEAN_R2_2_2 announcement schedule-claim artifacts."""

from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import binance_spot_announcement_claims_lean_v2 as extractor


FINAL_BASENAMES = {
    "claims.jsonl", "ambiguity.jsonl", "coverage.jsonl", "summary.json",
}
PAYLOAD_BASENAMES = {
    "claims.jsonl", "ambiguity.jsonl", "coverage.jsonl",
}
CLAIM_KEYS = {
    "article_code", "claim_id", "claim_type",
    "syntactic_pair_token_claim", "claimed_schedule_ms",
    "accepted_response_sha256", "detail_body_sha256",
    "action_source_span", "time_source_span", "pair_source_span",
}
AMBIGUITY_KEYS = {
    "article_code", "accepted_response_sha256", "detail_body_sha256",
    "primary_reason", "reasons", "evidence_spans",
}
COVERAGE_KEYS = {
    "article_code", "accepted_response_sha256", "detail_body_sha256",
    "status", "claim_count", "ambiguity_count", "primary_reason",
}
SUMMARY_KEYS = {
    "experiment_id", "run_id", "version", "semantics", "artifact_state",
    "terminal_status", "input_detail_count", "coverage_count",
    "ambiguity_count", "claim_count", "coverage_counts",
    "claim_type_counts", "input_bindings", "code_bindings",
    "output_artifacts", "payload_tree_sha256",
    "historical_eligibility_ready", "eligibility_evaluated",
    "strict_eligible_count",
}
SPAN_KEYS = {
    "matching_text_sha256", "matching_start_cp", "matching_end_cp",
    "fragment", "atoms", "span_sha256",
}
TEXT_ATOM_KEYS = {
    "kind", "pointer", "raw_start_cp", "raw_end_cp", "raw_fragment",
    "matching_start_cp", "matching_end_cp",
}
EVIDENCE_KEYS = {"kind", "reason", "span"}
ARTIFACT_KEYS = {"path", "rows", "bytes", "sha256"}
BINDING_KEYS = {"path", "sha256"}
LEASE_KEYS = {
    "experiment_id", "run_id", "formal_command_sha256",
    "expected_bindings_sha256",
}
AUTHORIZATION_KEYS = {
    "experiment_id", "run_id", "summary_sha256",
    "payload_tree_sha256", "final_tree_sha256",
}
CODE_BINDING_PATHS = {
    "runner": "src/quant_research/binance_spot_announcement_claims_lean_v2_runner.py",
    "extractor": "src/quant_research/binance_spot_announcement_claims_lean_v2.py",
    "loader": "src/quant_research/binance_spot_announcement_claims_lean_v2_loader.py",
    "source_contract": "experiments/exp_20260826_007/artifacts/source_contract.json",
    "schema": "experiments/exp_20260826_007/artifacts/schema.json",
    "parameters": "experiments/exp_20260826_007/parameters.json",
}
FLAG_ORDER = (
    ("--expected-runner-sha256", "runner"),
    ("--expected-extractor-sha256", "extractor"),
    ("--expected-loader-sha256", "loader"),
    ("--expected-source-contract-sha256", "source_contract"),
    ("--expected-schema-sha256", "schema"),
    ("--expected-parameters-sha256", "parameters"),
)
HEX = set("0123456789abcdef")


class ClaimsLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedClaims:
    terminal_status: str
    artifact_state: str
    claim_count: int
    ambiguity_count: int
    summary: Mapping[str, Any]


def _fail(message: str) -> None:
    raise ClaimsLoadError(message)


def _exact_keys(value: Any, expected: set[str], name: str) -> None:
    if type(value) is not dict or set(value) != expected:
        _fail(f"{name} keyset mismatch")


def _hex64(value: Any) -> bool:
    return (
        type(value) is str and len(value) == 64
        and all(character in HEX for character in value)
    )


def _utf8_key(value: str) -> bytes:
    return value.encode("utf-8")


def _load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    rows = extractor.strict_jsonl(raw)
    if b"".join(
        extractor.canonical_compact(row, newline=True) for row in rows
    ) != raw:
        _fail(f"noncanonical JSONL: {path.name}")
    return rows


def _validate_atom(atom: Any, start: int, end: int) -> None:
    if type(atom) is not dict:
        _fail("span atom is not object")
    if atom.get("kind") in {"TEXT", "ENTITY_NBSP"}:
        _exact_keys(atom, TEXT_ATOM_KEYS, "TEXT atom")
        if (
            type(atom["pointer"]) is not str
            or type(atom["raw_start_cp"]) is not int
            or type(atom["raw_end_cp"]) is not int
            or type(atom["raw_fragment"]) is not str
            or atom["raw_start_cp"] < 0
            or atom["raw_end_cp"] <= atom["raw_start_cp"]
        ):
            _fail("invalid TEXT atom")
        if len(atom["raw_fragment"]) != (
            atom["raw_end_cp"] - atom["raw_start_cp"]
        ):
            _fail("TEXT atom raw range mismatch")
        if atom["kind"] == "ENTITY_NBSP" and atom["raw_fragment"].lower() != "&nbsp;":
            _fail("invalid ENTITY_NBSP atom")
    else:
        _fail("unknown span atom kind")
    if (
        type(atom["matching_start_cp"]) is not int
        or type(atom["matching_end_cp"]) is not int
        or not (
            start <= atom["matching_start_cp"]
            < atom["matching_end_cp"] <= end
        )
    ):
        _fail("atom matching range outside span")


def validate_span(value: Any) -> None:
    _exact_keys(value, SPAN_KEYS, "span")
    start = value["matching_start_cp"]
    end = value["matching_end_cp"]
    if (
        type(start) is not int or type(end) is not int
        or start < 0 or end <= start
        or type(value["fragment"]) is not str
        or len(value["fragment"]) != end - start
        or not _hex64(value["matching_text_sha256"])
        or not _hex64(value["span_sha256"])
        or type(value["atoms"]) is not list or not value["atoms"]
    ):
        _fail("invalid span scalar fields")
    for atom in value["atoms"]:
        _validate_atom(atom, start, end)
    covered: set[int] = set()
    for atom in value["atoms"]:
        covered.update(
            range(atom["matching_start_cp"], atom["matching_end_cp"])
        )
    if covered != set(range(start, end)):
        _fail("span atoms do not cover matching range")
    core = {key: value[key] for key in SPAN_KEYS if key != "span_sha256"}
    if extractor.sha256_bytes(
        extractor.canonical_compact(core)
    ) != value["span_sha256"]:
        _fail("span SHA mismatch")


def _validate_claim(row: Any) -> None:
    _exact_keys(row, CLAIM_KEYS, "claim")
    if (
        type(row["article_code"]) is not str
        or row["claim_type"] not in {
            "OPEN_SCHEDULE_CLAIM", "REMOVAL_SCHEDULE_CLAIM",
        }
        or type(row["syntactic_pair_token_claim"]) is not str
        or extractor.PAIR.fullmatch(
            row["syntactic_pair_token_claim"],
        ) is None
        or type(row["claimed_schedule_ms"]) is not int
        or not _hex64(row["accepted_response_sha256"])
        or not _hex64(row["detail_body_sha256"])
        or not _hex64(row["claim_id"])
    ):
        _fail("invalid claim scalar fields")
    for name in (
        "action_source_span", "time_source_span", "pair_source_span",
    ):
        validate_span(row[name])
    core = {
        "action_span_sha256": row["action_source_span"]["span_sha256"],
        "article_code": row["article_code"],
        "claim_type": row["claim_type"],
        "claimed_schedule_ms": row["claimed_schedule_ms"],
        "pair_span_sha256": row["pair_source_span"]["span_sha256"],
        "syntactic_pair_token_claim": row["syntactic_pair_token_claim"],
        "time_span_sha256": row["time_source_span"]["span_sha256"],
    }
    if extractor.sha256_bytes(
        extractor.canonical_compact(core)
    ) != row["claim_id"]:
        _fail("claim ID mismatch")
    if row["pair_source_span"]["fragment"] != (
        row["syntactic_pair_token_claim"]
    ):
        _fail("pair span/token mismatch")


def _claim_sort(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _utf8_key(row["article_code"]),
        0 if row["claim_type"] == "OPEN_SCHEDULE_CLAIM" else 1,
        row["claimed_schedule_ms"],
        _utf8_key(row["syntactic_pair_token_claim"]),
        row["claim_id"],
    )


def _validate_ambiguity(row: Any) -> None:
    _exact_keys(row, AMBIGUITY_KEYS, "ambiguity")
    if (
        type(row["article_code"]) is not str
        or not _hex64(row["accepted_response_sha256"])
        or not _hex64(row["detail_body_sha256"])
        or row["primary_reason"] not in extractor.REASON_RANK
        or type(row["reasons"]) is not list
        or not row["reasons"]
        or any(reason not in extractor.REASON_RANK for reason in row["reasons"])
        or row["reasons"] != sorted(
            set(row["reasons"]), key=extractor.REASON_RANK.__getitem__,
        )
        or row["primary_reason"] != row["reasons"][0]
        or type(row["evidence_spans"]) is not list
    ):
        _fail("invalid ambiguity fields")
    previous: tuple[Any, ...] | None = None
    for evidence in row["evidence_spans"]:
        _exact_keys(evidence, EVIDENCE_KEYS, "evidence")
        if (
            type(evidence["kind"]) is not str
            or evidence["reason"] not in row["reasons"]
        ):
            _fail("invalid ambiguity evidence")
        validate_span(evidence["span"])
        key = (
            _utf8_key(evidence["kind"]),
            extractor.REASON_RANK[evidence["reason"]],
            evidence["span"]["matching_start_cp"],
            evidence["span"]["matching_end_cp"],
            evidence["span"]["span_sha256"],
        )
        if previous is not None and key < previous:
            _fail("ambiguity evidence order mismatch")
        previous = key


def _validate_coverage(row: Any) -> None:
    _exact_keys(row, COVERAGE_KEYS, "coverage")
    if (
        type(row["article_code"]) is not str
        or not _hex64(row["accepted_response_sha256"])
        or not _hex64(row["detail_body_sha256"])
        or row["status"] not in {"CLAIMED", "AMBIGUOUS", "NO_MATCH"}
        or type(row["claim_count"]) is not int or row["claim_count"] < 0
        or row["ambiguity_count"] not in {0, 1}
        or (
            row["primary_reason"] is not None
            and row["primary_reason"] not in extractor.REASON_RANK
        )
    ):
        _fail("invalid coverage fields")


def _validate_binding_map(value: Any, name: str) -> None:
    if type(value) is not dict:
        _fail(f"{name} is not object")
    for key, item in value.items():
        if type(key) is not str:
            _fail(f"{name} key is not string")
        _exact_keys(item, BINDING_KEYS, f"{name}.{key}")
        if type(item["path"]) is not str or not _hex64(item["sha256"]):
            _fail(f"invalid {name}.{key}")


def validate_directory(
    root: pathlib.Path,
    *,
    expected_article_codes: Sequence[str] | None = None,
    expected_detail_bindings: Mapping[
        str, tuple[str, str]
    ] | None = None,
    expected_input_bindings: Mapping[str, Mapping[str, str]] | None = None,
    expected_code_bindings: Mapping[str, Mapping[str, str]] | None = None,
) -> LoadedClaims:
    if not root.is_dir() or root.is_symlink():
        _fail("output root is not a regular directory")
    entries = list(root.iterdir())
    if (
        {entry.name for entry in entries} != FINAL_BASENAMES
        or any(not entry.is_file() or entry.is_symlink() for entry in entries)
    ):
        _fail("output basename allowlist mismatch")
    claims = _load_jsonl(root / "claims.jsonl")
    ambiguity = _load_jsonl(root / "ambiguity.jsonl")
    coverage = _load_jsonl(root / "coverage.jsonl")
    for row in claims:
        _validate_claim(row)
    for row in ambiguity:
        _validate_ambiguity(row)
    for row in coverage:
        _validate_coverage(row)
    if claims != sorted(claims, key=_claim_sort):
        _fail("claims order mismatch")
    for rows, name in ((ambiguity, "ambiguity"), (coverage, "coverage")):
        codes = [row["article_code"] for row in rows]
        if codes != sorted(codes, key=_utf8_key) or len(codes) != len(set(codes)):
            _fail(f"{name} order/uniqueness mismatch")
    claim_by_code: dict[str, int] = {}
    for row in claims:
        claim_by_code[row["article_code"]] = (
            claim_by_code.get(row["article_code"], 0) + 1
        )
    ambiguity_by_code = {row["article_code"]: row for row in ambiguity}
    coverage_by_code = {row["article_code"]: row for row in coverage}
    if expected_article_codes is not None:
        expected = set(expected_article_codes)
        if (
            len(expected) != len(expected_article_codes)
            or set(coverage_by_code) != expected
        ):
            _fail("coverage/input article closure mismatch")
    for code, row in coverage_by_code.items():
        count = claim_by_code.get(code, 0)
        ambiguous = int(code in ambiguity_by_code)
        if row["claim_count"] != count or row["ambiguity_count"] != ambiguous:
            _fail("coverage row counts mismatch")
        if row["status"] == "CLAIMED":
            valid = count > 0 and ambiguous == 0 and row["primary_reason"] is None
        elif row["status"] == "AMBIGUOUS":
            valid = count == 0 and ambiguous == 1 and (
                row["primary_reason"]
                == ambiguity_by_code[code]["primary_reason"]
            )
        else:
            valid = count == 0 and ambiguous == 0 and row["primary_reason"] is None
        if not valid:
            _fail("coverage partition mismatch")
        if expected_detail_bindings is not None:
            if code not in expected_detail_bindings or (
                row["accepted_response_sha256"],
                row["detail_body_sha256"],
            ) != expected_detail_bindings[code]:
                _fail("coverage/input provenance mismatch")
    if set(claim_by_code) - set(coverage_by_code):
        _fail("claim without coverage")
    if set(ambiguity_by_code) - set(coverage_by_code):
        _fail("ambiguity without coverage")
    if (
        expected_detail_bindings is not None
        and set(expected_detail_bindings) != set(coverage_by_code)
    ):
        _fail("detail provenance keyset mismatch")
    for row in claims:
        coverage_row = coverage_by_code[row["article_code"]]
        if (
            row["accepted_response_sha256"]
            != coverage_row["accepted_response_sha256"]
            or row["detail_body_sha256"]
            != coverage_row["detail_body_sha256"]
        ):
            _fail("claim/coverage provenance mismatch")
    for row in ambiguity:
        coverage_row = coverage_by_code[row["article_code"]]
        if (
            row["accepted_response_sha256"]
            != coverage_row["accepted_response_sha256"]
            or row["detail_body_sha256"]
            != coverage_row["detail_body_sha256"]
        ):
            _fail("ambiguity/coverage provenance mismatch")

    summary_raw = (root / "summary.json").read_bytes()
    summary = extractor.strict_json(summary_raw)
    _exact_keys(summary, SUMMARY_KEYS, "summary")
    if extractor.canonical_pretty(summary) != summary_raw:
        _fail("summary is noncanonical")
    if (
        summary["experiment_id"] != extractor.EXPERIMENT_ID
        or summary["run_id"] != extractor.RUN_ID
        or summary["version"] != extractor.VERSION
        or summary["semantics"] != extractor.SEMANTICS
        or summary["terminal_status"] != "NEEDS_MORE_DATA"
        or summary["artifact_state"]
        != "ANNOUNCEMENT_SCHEDULE_CLAIM_SCAN_COMPLETE"
        or summary["historical_eligibility_ready"] is not False
        or summary["eligibility_evaluated"] is not False
        or summary["strict_eligible_count"] != 0
    ):
        _fail("summary fixed fields mismatch")
    _validate_binding_map(summary["input_bindings"], "input_bindings")
    _validate_binding_map(summary["code_bindings"], "code_bindings")
    if (
        expected_input_bindings is not None
        and summary["input_bindings"] != expected_input_bindings
    ):
        _fail("summary input bindings mismatch")
    if (
        expected_code_bindings is not None
        and summary["code_bindings"] != expected_code_bindings
    ):
        _fail("summary code bindings mismatch")
    if (
        summary["input_detail_count"] != len(coverage)
        or summary["coverage_count"] != len(coverage)
        or summary["ambiguity_count"] != len(ambiguity)
        or summary["claim_count"] != len(claims)
        or summary["coverage_counts"] != {
            key: sum(row["status"] == key for row in coverage)
            for key in ("CLAIMED", "AMBIGUOUS", "NO_MATCH")
        }
        or summary["claim_type_counts"] != {
            key: sum(row["claim_type"] == key for row in claims)
            for key in ("OPEN_SCHEDULE_CLAIM", "REMOVAL_SCHEDULE_CLAIM")
        }
        or sum(row["claim_count"] for row in coverage) != len(claims)
    ):
        _fail("summary count closure mismatch")
    artifacts: list[dict[str, Any]] = []
    for name in sorted(PAYLOAD_BASENAMES, key=_utf8_key):
        raw = (root / name).read_bytes()
        row_count = {
            "claims.jsonl": len(claims),
            "ambiguity.jsonl": len(ambiguity),
            "coverage.jsonl": len(coverage),
        }[name]
        artifacts.append(
            {
                "path": name, "rows": row_count, "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    for item in summary["output_artifacts"]:
        _exact_keys(item, ARTIFACT_KEYS, "output artifact")
    if summary["output_artifacts"] != artifacts:
        _fail("output artifact bindings mismatch")
    material = b"".join(
        (
            f"{item['path']}\0{item['rows']}\0{item['bytes']}\0"
            f"{item['sha256']}\n"
        ).encode("utf-8")
        for item in artifacts
    )
    if hashlib.sha256(material).hexdigest() != summary["payload_tree_sha256"]:
        _fail("payload tree mismatch")
    return LoadedClaims(
        summary["terminal_status"], summary["artifact_state"],
        len(claims), len(ambiguity), summary,
    )


def final_tree_sha256(root: pathlib.Path) -> str:
    entries = []
    for name in sorted(FINAL_BASENAMES, key=_utf8_key):
        raw = (root / name).read_bytes()
        entries.append(
            {
                "path": name, "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return hashlib.sha256(
        extractor.canonical_compact(entries)
    ).hexdigest()


def _canonical_object(path: pathlib.Path, keys: set[str]) -> dict[str, Any]:
    raw = path.read_bytes()
    value = extractor.strict_json(raw)
    _exact_keys(value, keys, path.name)
    if extractor.canonical_pretty(value) != raw:
        _fail(f"noncanonical authority: {path.name}")
    return value


def _formal_command_sha256(
    code_bindings: Mapping[str, Mapping[str, str]],
) -> str:
    parts = [
        r".venv\Scripts\python.exe", "-B",
        r"src\quant_research\binance_spot_announcement_claims_lean_v2_runner.py",
    ]
    for flag, name in FLAG_ORDER:
        parts.extend((flag, code_bindings[name]["sha256"]))
    return hashlib.sha256(" ".join(parts).encode("utf-8")).hexdigest()


def load_committed(
    repo_root: pathlib.Path,
    *,
    final_root: pathlib.Path | None = None,
    staging_root: pathlib.Path | None = None,
    control_root: pathlib.Path | None = None,
) -> LoadedClaims:
    repo_root = repo_root.resolve()
    run_parent = repo_root / (
        "data/processed/binance_spot_announcement_claims_v2/runs"
    )
    final_root = final_root or run_parent / extractor.RUN_ID
    staging_root = staging_root or run_parent / (
        "." + extractor.RUN_ID + ".staging"
    )
    control_root = control_root or run_parent / (
        "." + extractor.RUN_ID + ".control"
    )
    if (
        not final_root.is_dir() or staging_root.exists()
        or not control_root.is_dir()
        or (control_root / "failure.json").exists()
    ):
        _fail("committed lifecycle state mismatch")
    if {item.name for item in control_root.iterdir()} != {
        "lease.json", "authorization.json",
    }:
        _fail("control basename allowlist mismatch")
    lease = _canonical_object(control_root / "lease.json", LEASE_KEYS)
    authorization = _canonical_object(
        control_root / "authorization.json", AUTHORIZATION_KEYS,
    )
    if (
        lease["experiment_id"] != extractor.EXPERIMENT_ID
        or lease["run_id"] != extractor.RUN_ID
        or authorization["experiment_id"] != extractor.EXPERIMENT_ID
        or authorization["run_id"] != extractor.RUN_ID
    ):
        _fail("authority identity mismatch")
    summary = extractor.strict_json((final_root / "summary.json").read_bytes())
    code_bindings = summary.get("code_bindings") if type(summary) is dict else None
    _validate_binding_map(code_bindings, "code_bindings")
    if set(code_bindings) != set(CODE_BINDING_PATHS):
        _fail("code binding name set mismatch")
    for name, relative in CODE_BINDING_PATHS.items():
        if code_bindings[name]["path"] != relative:
            _fail("code binding path mismatch")
        path = repo_root.joinpath(*pathlib.PurePosixPath(relative).parts)
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest()
            != code_bindings[name]["sha256"]
        ):
            _fail("live code binding mismatch")
    if (
        hashlib.sha256(
            extractor.canonical_compact(code_bindings)
        ).hexdigest() != lease["expected_bindings_sha256"]
        or _formal_command_sha256(code_bindings)
        != lease["formal_command_sha256"]
    ):
        _fail("lease binding mismatch")
    details = extractor.load_accepted_details(repo_root)
    loaded = validate_directory(
        final_root,
        expected_article_codes=[item.article_code for item in details],
        expected_detail_bindings={
            item.article_code: (
                item.response_sha256, item.body_sha256,
            )
            for item in details
        },
        expected_input_bindings=extractor.INPUT_BINDINGS,
        expected_code_bindings=code_bindings,
    )
    if (
        hashlib.sha256(
            (final_root / "summary.json").read_bytes()
        ).hexdigest() != authorization["summary_sha256"]
        or loaded.summary["payload_tree_sha256"]
        != authorization["payload_tree_sha256"]
        or final_tree_sha256(final_root)
        != authorization["final_tree_sha256"]
    ):
        _fail("authorization/final tree mismatch")
    return loaded

