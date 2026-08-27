"""LEAN_R2_2_2 offline Binance announcement schedule-claim extractor.

This module has no network capability. It treats a slash pair as an opaque
syntactic token and emits current-visible announcement text claims only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


EXPERIMENT_ID = "exp_20260826_007"
RUN_ID = "exp_20260826_007_formal_001"
VERSION = "binance_spot_announcement_claims_lean_v2"
SEMANTICS = (
    "CURRENT_VISIBLE_ANNOUNCEMENT_SCHEDULE_CLAIMS_ONLY;"
    "NOT_TRADING_STATUS_PERMISSION_LISTING_INTERVAL_EFFECTIVE_AT_OR_KNOWN_AT;"
    "NOT_HISTORICAL_ELIGIBILITY"
)

RAW_ROOT = pathlib.PurePosixPath(
    "data/raw/binance_spot_announcement_v4/runs/exp_20260826_005_formal_001"
)
INPUT_BINDINGS: dict[str, dict[str, str]] = {
    "exp005_source_contract": {
        "path": "experiments/exp_20260826_005/artifacts/source_contract.json",
        "sha256": "9a99ebc97803228dbb1bb20f4eb8cfe03b9a381a076959355353c3f9a7c4d852",
    },
    "exp005_schema": {
        "path": "experiments/exp_20260826_005/artifacts/schema.json",
        "sha256": "041c867ebbe82ffea9d22e14204e9ae6ca4586287dd448d3fd333ba74b3b1d0c",
    },
    "exp005_corpus_summary": {
        "path": "experiments/exp_20260826_005/artifacts/corpus_summary.json",
        "sha256": "03900ad203d15483f7dd968b77390abdb675ef77519d60f35e3071d6ccfe8a19",
    },
    "exp005_raw_summary": {
        "path": str(RAW_ROOT / "summary.json"),
        "sha256": "912282eea1a4f9e691d34e0bffeb28053cae61cc1ec6581f16a7c298c987ea48",
    },
    "exp005_request_ledger": {
        "path": str(RAW_ROOT / "request_ledger.jsonl"),
        "sha256": "455afba5577a2c56d1a1c16604c9becacc0308806a9a28bbdee7137746be7928",
    },
    "exp005_detail_index": {
        "path": (
            "data/processed/binance_spot_announcement_v4/runs/"
            "exp_20260826_005_formal_001/detail_index.jsonl"
        ),
        "sha256": "aa9608faa48ec15a2beae209085c2139dec70672c9e9346103d485ad694a6d96",
    },
}

EXPECTED_DETAIL_COUNT = 756
EXPECTED_LOGICAL_COUNT = 866
EXPECTED_WIRE_COUNT = 884
EXPECTED_RAW_FILE_COUNT = 2655
EXPECTED_RAW_BYTES = 44_020_359
EXPECTED_RAW_TREE = "5969f39532b39d322f6e4b8e44e06a27b5962071c4454154ec87628937d9e2fd"
EXPECTED_RECEIPT_TREE = "49d332dda5bedf9a6b3fd0c598937f0d80540e8b688447f1dc1d8def0ae04360"
EXPECTED_DETAIL_KEYSET = "9acbd3b26999fba4e00aabf4147965975ef51b02da192f1eca89cda8e907da0c"
EXPECTED_ATTEMPT_KEYSET = "3c1e379a3b50d7decd9c710eb64b51e2bddefcdf51d9c658c6cff891b4969d47"

ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("O1", "OPEN", "open trading for the following spot trading pairs"),
    ("O2", "OPEN", "open trading for these spot trading pairs"),
    ("O3", "OPEN", "will open trading for the following trading pairs"),
    ("O4", "OPEN", "add the following spot trading pairs"),
    ("O5", "OPEN", "add new spot trading pairs"),
    ("O6", "OPEN", "launch trading for the following spot trading pairs"),
    ("R1", "REMOVAL", "remove and cease trading on the following spot trading pairs"),
    ("R2", "REMOVAL", "remove and cease trading on these spot trading pairs"),
    ("R3", "REMOVAL", "remove the following spot trading pairs and cease trading"),
    ("R4", "REMOVAL", "cease trading on the following spot trading pairs"),
    ("R5", "REMOVAL", "delist and cease trading on all spot trading pairs"),
)
REASON_PRIORITY = (
    "MULTIPLE_ACTION_FAMILIES", "MULTIPLE_ACTION_SPANS", "SPOT_SCOPE_MISSING",
    "HEADER_SUFFIX_INVALID", "CARRIER_MISSING", "PAIR_WRAPPER_MISMATCH",
    "MULTIPLE_PAIR_WRAPPERS", "NONSELECTED_CARRIER_COLLISION", "UTC_INVALID",
    "PAIR_TOKEN_REJECTED", "DUPLICATE_PAIR", "PAIR_BOUND_TO_MULTIPLE_TIMES",
)
REASON_RANK = {value: index for index, value in enumerate(REASON_PRIORITY)}

T00 = re.compile(r"(?<![0-9])([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}) \(UTC\)(?![0-9])")
PAIR_TEXT = r"[A-Z0-9][A-Z0-9_-]{0,63}/[A-Z0-9][A-Z0-9_-]{0,63}"
PAIR = re.compile(PAIR_TEXT)
PAIR_LIST_TEXT = rf"{PAIR_TEXT}(?:, {PAIR_TEXT})*(?:,? and {PAIR_TEXT})?"
OPEN_WRAPPER = re.compile(rf"New (?:Spot|spot) Trading Pairs: (?P<pairs>{PAIR_LIST_TEXT})\.")
REMOVAL_WRAPPER = re.compile(rf"At (?P<time>{T00.pattern}): (?P<pairs>{PAIR_LIST_TEXT})\.?")
VISIBLE_URL = re.compile(r"(?:https?://|bnc://|www\.)\S+", re.IGNORECASE)
FORBIDDEN_PAIRS = {"AND/OR", "N/A", "AM/PM", "A/B", "INPUT/OUTPUT"}


class ClaimsError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_compact(value: Any, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return raw + (b"\n" if newline else b"")


def canonical_pretty(value: Any) -> bytes:
    text = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ClaimsError("INPUT_SCHEMA", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ClaimsError("INPUT_SCHEMA", f"nonfinite JSON number: {value}")


def strict_json(raw: bytes | str) -> Any:
    if isinstance(raw, bytes):
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ClaimsError("INPUT_SCHEMA", "UTF-8 BOM forbidden")
        raw = raw.decode("utf-8", errors="strict")
    try:
        return json.loads(
            raw, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant,
        )
    except ClaimsError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ClaimsError("INPUT_SCHEMA", "invalid JSON") from exc


def strict_jsonl(raw: bytes) -> list[dict[str, Any]]:
    if raw == b"":
        return []
    if raw.startswith(b"\xef\xbb\xbf") or not raw.endswith(b"\n"):
        raise ClaimsError("INPUT_SCHEMA", "JSONL must be UTF-8 LF terminated")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        value = strict_json(line)
        if type(value) is not dict:
            raise ClaimsError("INPUT_SCHEMA", "JSONL row must be object")
        rows.append(value)
    return rows


def keyset_sha256(values: Iterable[str]) -> str:
    ordered = sorted(values, key=lambda value: value.encode("utf-8"))
    return sha256_bytes(canonical_compact(ordered, newline=True))


def _bound_path(
    repo_root: pathlib.Path, relative: str, *, raw_only: bool = False,
) -> pathlib.Path:
    posix = pathlib.PurePosixPath(relative)
    if posix.is_absolute() or any(part in ("", ".", "..") for part in posix.parts):
        raise ClaimsError("INPUT_BINDING", f"unsafe relative path: {relative}")
    if raw_only and posix != RAW_ROOT and RAW_ROOT not in posix.parents:
        raise ClaimsError("INPUT_BINDING", f"path outside raw root: {relative}")
    root = repo_root.resolve()
    path = root.joinpath(*posix.parts).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ClaimsError(
            "INPUT_BINDING", f"path escapes repository: {relative}",
        ) from exc
    return path


@dataclass(frozen=True)
class AcceptedDetail:
    article_code: str
    response_sha256: str
    body_sha256: str
    body: str
    ast: dict[str, Any]


def load_accepted_details(repo_root: pathlib.Path) -> list[AcceptedDetail]:
    parsed: dict[str, Any] = {}
    for name, binding in INPUT_BINDINGS.items():
        path = _bound_path(repo_root, binding["path"])
        raw = path.read_bytes()
        if sha256_bytes(raw) != binding["sha256"]:
            raise ClaimsError("INPUT_BINDING", f"input hash mismatch: {name}")
        parsed[name] = (
            strict_jsonl(raw) if binding["path"].endswith(".jsonl")
            else strict_json(raw)
        )

    summary = parsed["exp005_raw_summary"]
    required_summary = {
        "logical_request_count": EXPECTED_LOGICAL_COUNT,
        "wire_attempt_count": EXPECTED_WIRE_COUNT,
        "raw_artifact_tree_sha256": EXPECTED_RAW_TREE,
        "receipt_tree_sha256": EXPECTED_RECEIPT_TREE,
        "selected_detail_keyset_sha256": EXPECTED_DETAIL_KEYSET,
        "accepted_detail_keyset_sha256": EXPECTED_DETAIL_KEYSET,
        "attempt_keyset_sha256": EXPECTED_ATTEMPT_KEYSET,
        "body_keyset_sha256": EXPECTED_ATTEMPT_KEYSET,
        "sidecar_keyset_sha256": EXPECTED_ATTEMPT_KEYSET,
        "receipt_keyset_sha256": EXPECTED_ATTEMPT_KEYSET,
    }
    for key, expected in required_summary.items():
        if summary.get(key) != expected:
            raise ClaimsError("INPUT_BINDING", f"raw summary mismatch: {key}")

    detail_rows = parsed["exp005_detail_index"]
    ledger = parsed["exp005_request_ledger"]
    if (
        len(detail_rows) != EXPECTED_DETAIL_COUNT
        or len(ledger) != EXPECTED_LOGICAL_COUNT
    ):
        raise ClaimsError("INPUT_BIJECTION", "frozen row counts differ")
    detail_by_code: dict[str, dict[str, Any]] = {}
    for row in detail_rows:
        code = row.get("article_code")
        if type(code) is not str or code in detail_by_code:
            raise ClaimsError(
                "INPUT_BIJECTION", "invalid or duplicate article_code",
            )
        detail_by_code[code] = row
    detail_ledger: dict[str, dict[str, Any]] = {}
    for row in ledger:
        if row.get("kind") != "detail":
            continue
        logical = row.get("logical_key")
        if type(logical) is not str or not logical.startswith("details/"):
            raise ClaimsError("INPUT_BIJECTION", "invalid detail logical key")
        code = logical.removeprefix("details/")
        if (
            code in detail_ledger
            or row.get("canonical_parameters") != {"articleCode": code}
        ):
            raise ClaimsError(
                "INPUT_BIJECTION", "duplicate or inconsistent detail ledger",
            )
        detail_ledger[code] = row
    if (
        set(detail_by_code) != set(detail_ledger)
        or keyset_sha256(detail_by_code) != EXPECTED_DETAIL_KEYSET
    ):
        raise ClaimsError("INPUT_BIJECTION", "detail keysets differ")

    accepted: list[AcceptedDetail] = []
    for code in sorted(detail_by_code, key=lambda value: value.encode("utf-8")):
        index = detail_by_code[code]
        row = detail_ledger[code]
        attempts = row.get("attempts")
        if type(attempts) is not list or not attempts:
            raise ClaimsError("INPUT_SCHEMA", "missing detail attempts")
        accepted_attempts: list[
            tuple[dict[str, Any], dict[str, Any]]
        ] = []
        for attempt in attempts:
            if type(attempt) is not dict:
                raise ClaimsError("INPUT_SCHEMA", "attempt is not object")
            receipt_path = _bound_path(
                repo_root, attempt.get("receipt", ""), raw_only=True,
            )
            receipt_raw = receipt_path.read_bytes()
            if sha256_bytes(receipt_raw) != attempt.get("receipt_sha256"):
                raise ClaimsError("INPUT_BINDING", "receipt hash mismatch")
            receipt = strict_json(receipt_raw)
            identity = (
                receipt.get("logical_key") == row["logical_key"]
                and receipt.get("attempt_key") == attempt.get("attempt_key")
                and receipt.get("attempt_no") == attempt.get("attempt_no")
                and receipt.get("body_path") == attempt.get("body")
                and receipt.get("body_sha256") == attempt.get("body_sha256")
                and receipt.get("sidecar_path") == attempt.get("sidecar")
                and receipt.get("sidecar_sha256")
                == attempt.get("sidecar_sha256")
            )
            if not identity:
                raise ClaimsError(
                    "INPUT_BIJECTION", "ledger/receipt identity mismatch",
                )
            if type(receipt.get("accepted")) is not bool:
                raise ClaimsError(
                    "INPUT_SCHEMA", "receipt accepted is not bool",
                )
            if receipt.get("outcome") != attempt.get("outcome"):
                raise ClaimsError(
                    "INPUT_BIJECTION", "attempt/receipt outcome mismatch",
                )
            if receipt["accepted"] is True:
                if (
                    receipt.get("outcome") != "OK"
                    or receipt.get("http_status") != 200
                ):
                    raise ClaimsError(
                        "INPUT_BIJECTION",
                        "accepted receipt is not OK/200",
                    )
                accepted_attempts.append((attempt, receipt))
        if len(accepted_attempts) != 1:
            raise ClaimsError(
                "INPUT_BIJECTION", "accepted attempt is not unique",
            )
        attempt, _receipt = accepted_attempts[0]
        if attempt is not attempts[-1]:
            raise ClaimsError(
                "INPUT_BIJECTION", "accepted attempt is not final",
            )
        response_path = _bound_path(
            repo_root, attempt["body"], raw_only=True,
        )
        sidecar_path = _bound_path(
            repo_root, attempt["sidecar"], raw_only=True,
        )
        response_raw = response_path.read_bytes()
        sidecar_raw = sidecar_path.read_bytes()
        if (
            sha256_bytes(response_raw) != attempt["body_sha256"]
            or sha256_bytes(sidecar_raw) != attempt["sidecar_sha256"]
        ):
            raise ClaimsError(
                "INPUT_BINDING", "accepted response/sidecar hash mismatch",
            )
        if attempt["body_sha256"] != index.get("detail_raw_sha256"):
            raise ClaimsError("INPUT_BIJECTION", "detail raw SHA mismatch")
        outer = strict_json(response_raw)
        data = outer.get("data") if type(outer) is dict else None
        body = data.get("body") if type(data) is dict else None
        if type(body) is not str:
            raise ClaimsError("INPUT_SCHEMA", "$.data.body is not a string")
        body_raw = body.encode("utf-8")
        if (
            sha256_bytes(body_raw) != index.get("detail_body_sha256")
            or len(body_raw) != index.get("detail_body_utf8_bytes")
        ):
            raise ClaimsError(
                "INPUT_BIJECTION", "detail body binding mismatch",
            )
        ast = strict_json(body)
        if type(ast) is not dict:
            raise ClaimsError(
                "INPUT_SCHEMA", "body AST root is not an object",
            )
        accepted.append(
            AcceptedDetail(
                code, attempt["body_sha256"],
                index["detail_body_sha256"], body, ast,
            )
        )
    return accepted


@dataclass(frozen=True)
class SourceUnit:
    character: str
    atom: Mapping[str, Any]


@dataclass(frozen=True)
class View:
    text: str
    mapping: tuple[tuple[Mapping[str, Any], ...], ...]
    node_pointer: str


def _raw_units(
    node: Any, pointer: str,
) -> tuple[list[SourceUnit], str | None, str | None]:
    if type(node) is not dict:
        raise ClaimsError("INPUT_SCHEMA", "AST node must be object")
    if node.get("node") == "text":
        text = node.get("text")
        if type(text) is not str:
            raise ClaimsError("INPUT_SCHEMA", "text node missing string")
        units: list[SourceUnit] = []
        index = 0
        while index < len(text):
            if text[index:index + 6].lower() == "&nbsp;":
                units.append(
                    SourceUnit(
                        " ",
                        {
                            "kind": "ENTITY_NBSP",
                            "pointer": pointer + "/text",
                            "raw_start_cp": index,
                            "raw_end_cp": index + 6,
                            "raw_fragment": text[index:index + 6],
                        },
                    )
                )
                index += 6
            else:
                units.append(
                    SourceUnit(
                        text[index],
                        {
                            "kind": "TEXT",
                            "pointer": pointer + "/text",
                            "raw_start_cp": index,
                            "raw_end_cp": index + 1,
                            "raw_fragment": text[index],
                        },
                    )
                )
                index += 1
        return units, pointer, pointer
    children = node.get("child", [])
    if type(children) is not list:
        raise ClaimsError("INPUT_SCHEMA", "AST child must be list")
    emitted: list[
        tuple[list[SourceUnit], str | None, str | None]
    ] = []
    for index, child in enumerate(children):
        child_pointer = (
            f"{pointer}/child/{index}" if pointer else f"/child/{index}"
        )
        result = _raw_units(child, child_pointer)
        if result[0]:
            emitted.append(result)
    output: list[SourceUnit] = []
    first: str | None = None
    for units, child_first, child_last in emitted:
        output.extend(units)
        first = first or child_first
    return output, first, emitted[-1][2] if emitted else None


def build_view(node: dict[str, Any], pointer: str) -> View:
    raw, _, _ = _raw_units(node, pointer)
    characters: list[str] = []
    mapping: list[tuple[Mapping[str, Any], ...]] = []
    index = 0
    while index < len(raw):
        if raw[index].character.isspace():
            atoms: list[Mapping[str, Any]] = []
            while index < len(raw) and raw[index].character.isspace():
                atoms.append(raw[index].atom)
                index += 1
            characters.append(" ")
            mapping.append(tuple(atoms))
        else:
            characters.append(raw[index].character)
            mapping.append((raw[index].atom,))
            index += 1
    start = 0
    end = len(characters)
    while start < end and characters[start] == " ":
        start += 1
    while end > start and characters[end - 1] == " ":
        end -= 1
    return View(
        "".join(characters[start:end]), tuple(mapping[start:end]), pointer,
    )


def root_segments(
    ast: dict[str, Any],
) -> list[tuple[int, dict[str, Any], View]]:
    children = ast.get("child")
    if type(children) is not list:
        raise ClaimsError("INPUT_SCHEMA", "AST root missing child list")
    result: list[tuple[int, dict[str, Any], View]] = []
    for index, child in enumerate(children):
        if type(child) is not dict:
            raise ClaimsError("INPUT_SCHEMA", "root child is not object")
        pointer = f"/child/{index}"
        result.append((index, child, build_view(child, pointer)))
    return result


def _merge_text_atoms(
    atoms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for atom in atoms:
        if (
            result and atom["kind"] == "TEXT"
            and result[-1]["kind"] == "TEXT"
            and atom["pointer"] == result[-1]["pointer"]
            and atom["raw_start_cp"] == result[-1]["raw_end_cp"]
            and atom["matching_start_cp"] == result[-1]["matching_end_cp"]
        ):
            result[-1]["raw_end_cp"] = atom["raw_end_cp"]
            result[-1]["raw_fragment"] += atom["raw_fragment"]
            result[-1]["matching_end_cp"] = atom["matching_end_cp"]
        else:
            result.append(atom)
    return result


def make_span(view: View, start: int, end: int) -> dict[str, Any]:
    if not (0 <= start < end <= len(view.text)):
        raise ClaimsError("OUTPUT_SCHEMA", "invalid span bounds")
    atoms: list[dict[str, Any]] = []
    for position in range(start, end):
        for source in view.mapping[position]:
            if source["kind"] in {"TEXT", "ENTITY_NBSP"}:
                atoms.append(
                    {
                        "kind": source["kind"], "pointer": source["pointer"],
                        "raw_start_cp": source["raw_start_cp"],
                        "raw_end_cp": source["raw_end_cp"],
                        "raw_fragment": source["raw_fragment"],
                        "matching_start_cp": position,
                        "matching_end_cp": position + 1,
                    }
                )
    core = {
        "matching_text_sha256": sha256_bytes(view.text.encode("utf-8")),
        "matching_start_cp": start,
        "matching_end_cp": end,
        "fragment": view.text[start:end],
        "atoms": _merge_text_atoms(atoms),
    }
    return {
        **core, "span_sha256": sha256_bytes(canonical_compact(core)),
    }


def _ascii_ci_matches(view: View) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for action_id, family, phrase in ACTIONS:
        regex = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])",
            re.IGNORECASE | re.ASCII,
        )
        for match in regex.finditer(view.text):
            if not _token_boundary_safe(view, match.start(), match.end()):
                continue
            matches.append(
                {
                    "id": action_id, "family": family,
                    "start": match.start(), "end": match.end(),
                    "view": view,
                }
            )
    return matches


def _token_boundary_safe(view: View, start: int, end: int) -> bool:
    """Reject lexical tokens glued across text nodes without raw spacing."""
    for position in range(start + 1, end):
        left = view.mapping[position - 1]
        right = view.mapping[position]
        left_pointers = {atom.get("pointer") for atom in left}
        right_pointers = {atom.get("pointer") for atom in right}
        if left_pointers == right_pointers:
            continue
        if view.text[position - 1].isspace() or view.text[position].isspace():
            continue
        if any(atom["kind"] == "ENTITY_NBSP" for atom in (*left, *right)):
            continue
        return False
    return True


def reduce_actions(
    matches: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    survivors: list[dict[str, Any]] = []
    for candidate in sorted(
        matches, key=lambda item: (item["id"], item["start"], item["end"]),
    ):
        contained = False
        for other in matches:
            same_family = (
                candidate["family"] == other["family"]
                and candidate["view"].node_pointer
                == other["view"].node_pointer
            )
            strictly_longer = (
                other["end"] - other["start"]
                > candidate["end"] - candidate["start"]
            )
            contains = (
                other["start"] <= candidate["start"]
                and other["end"] >= candidate["end"]
            )
            identical_better = (
                other["start"] == candidate["start"]
                and other["end"] == candidate["end"]
                and other["id"] < candidate["id"]
            )
            if same_family and (
                (strictly_longer and contains) or identical_better
            ):
                contained = True
                break
        if not contained:
            survivors.append(candidate)
    return sorted(
        survivors,
        key=lambda item: (
            item["view"].node_pointer, item["start"], item["end"], item["id"],
        ),
    )


def _parse_time(fragment: str) -> int | None:
    match = T00.fullmatch(fragment)
    if match is None:
        return None
    try:
        value = dt.datetime(
            *(int(part) for part in match.groups()), tzinfo=dt.timezone.utc,
        )
    except ValueError:
        return None
    return int(value.timestamp() * 1000)


def _pair_matches(
    view: View, start: int, end: int,
) -> list[re.Match[str]]:
    result = [
        match for match in PAIR.finditer(view.text, start, end)
        if _token_boundary_safe(view, match.start(), match.end())
    ]
    if any(match.group(0) in FORBIDDEN_PAIRS for match in result):
        return []
    return result


def _li_views(
    carrier: dict[str, Any], carrier_pointer: str,
) -> list[View] | None:
    if carrier.get("node") != "element" or carrier.get("tag") != "ul":
        return None
    children = carrier.get("child")
    if type(children) is not list or not children or len(children) > 64:
        return None
    result: list[View] = []
    for index, child in enumerate(children):
        if (
            type(child) is not dict
            or child.get("node") != "element"
            or child.get("tag") != "li"
        ):
            return None
        result.append(
            build_view(child, f"{carrier_pointer}/child/{index}"),
        )
    return result


def _collision(view: View) -> bool:
    return bool(
        PAIR.search(view.text)
        or _ascii_ci_matches(view)
        or OPEN_WRAPPER.fullmatch(view.text)
        or REMOVAL_WRAPPER.fullmatch(view.text)
        or VISIBLE_URL.search(view.text)
    )


def _evidence(
    reason: str, kind: str, span: dict[str, Any],
) -> dict[str, Any]:
    return {"kind": kind, "reason": reason, "span": span}


def analyze_article(
    detail: AcceptedDetail,
) -> tuple[
    list[dict[str, Any]], dict[str, Any] | None, dict[str, Any]
]:
    segments = root_segments(detail.ast)
    all_actions: list[dict[str, Any]] = []
    for _, _, view in segments:
        all_actions.extend(_ascii_ci_matches(view))
    actions = reduce_actions(all_actions)
    reasons: list[str] = []
    evidence: list[dict[str, Any]] = []
    families = {action["family"] for action in actions}
    if len(families) > 1:
        reasons.append("MULTIPLE_ACTION_FAMILIES")
    if len(actions) > 1:
        reasons.append("MULTIPLE_ACTION_SPANS")
    if reasons:
        for action in actions:
            evidence.append(
                _evidence(
                    reasons[0], "ACTION",
                    make_span(
                        action["view"], action["start"], action["end"],
                    ),
                )
            )
    if not actions:
        coverage = _coverage(detail, "NO_MATCH", 0, 0, None)
        return [], None, coverage
    if len(actions) != 1 or reasons:
        ambiguity = _ambiguity(detail, reasons, evidence)
        return (
            [], ambiguity,
            _coverage(
                detail, "AMBIGUOUS", 0, 1,
                ambiguity["primary_reason"],
            ),
        )

    action = actions[0]
    view = action["view"]
    if not re.search(
        r"(?<![A-Za-z0-9])spot trading pairs?(?![A-Za-z0-9])",
        view.text, re.IGNORECASE | re.ASCII,
    ):
        reasons.append("SPOT_SCOPE_MISSING")
    action_span = make_span(view, action["start"], action["end"])
    segment_by_pointer = {
        segment_view.node_pointer: (index, node, segment_view)
        for index, node, segment_view in segments
    }
    root_index, _, _ = segment_by_pointer[view.node_pointer]
    suffix_start = action["end"]
    suffix = view.text[suffix_start:]
    header_time: tuple[int, re.Match[str]] | None = None
    if action["family"] == "OPEN":
        suffix_match = re.fullmatch(rf" at ({T00.pattern})\.", suffix)
        if suffix_match:
            time_match = T00.search(
                suffix, suffix_match.start(1), suffix_match.end(1),
            )
            if time_match is not None and not _token_boundary_safe(
                view, suffix_start + time_match.start(),
                suffix_start + time_match.end(),
            ):
                time_match = None
            epoch = _parse_time(time_match.group(0)) if time_match else None
            if epoch is None:
                reasons.append("UTC_INVALID")
            else:
                header_time = (epoch, time_match)
        else:
            reasons.append("HEADER_SUFFIX_INVALID")
            time_candidates = list(T00.finditer(suffix))
            if (
                len(time_candidates) != 1
                or _parse_time(time_candidates[0].group(0)) is None
                or not _token_boundary_safe(
                    view,
                    suffix_start + time_candidates[0].start(),
                    suffix_start + time_candidates[0].end(),
                )
            ):
                reasons.append("UTC_INVALID")
    elif suffix != ":":
        reasons.append("HEADER_SUFFIX_INVALID")

    carrier_tuple = next(
        (item for item in segments if item[0] == root_index + 1), None,
    )
    if carrier_tuple is None:
        reasons.append("CARRIER_MISSING")
        leaf_views = None
    else:
        carrier_index, carrier, _ = carrier_tuple
        leaf_views = _li_views(carrier, f"/child/{carrier_index}")
        if leaf_views is None:
            reasons.append("CARRIER_MISSING")
    selected: list[
        tuple[
            View, re.Match[str], list[re.Match[str]], int, re.Match[str],
        ]
    ] = []
    selected_pointers: set[str] = set()
    if leaf_views is not None:
        if action["family"] == "OPEN":
            wrapper_matches = [
                (leaf, OPEN_WRAPPER.fullmatch(leaf.text))
                for leaf in leaf_views
            ]
            wrapper_matches = [
                (leaf, match) for leaf, match in wrapper_matches
                if match is not None
            ]
            if not wrapper_matches:
                reasons.append("PAIR_WRAPPER_MISMATCH")
                if any(
                    leaf.text.lower().startswith(
                        "new spot trading pairs:"
                    )
                    for leaf in leaf_views
                ):
                    reasons.append("PAIR_TOKEN_REJECTED")
            elif len(wrapper_matches) > 1:
                reasons.append("MULTIPLE_PAIR_WRAPPERS")
            elif header_time is not None:
                leaf, wrapper = wrapper_matches[0]
                pair_matches = _pair_matches(
                    leaf, wrapper.start("pairs"), wrapper.end("pairs"),
                )
                lexical_pairs = list(
                    PAIR.finditer(
                        leaf.text,
                        wrapper.start("pairs"), wrapper.end("pairs"),
                    )
                )
                if (
                    not pair_matches
                    or len(pair_matches) != len(lexical_pairs)
                ):
                    reasons.append("PAIR_TOKEN_REJECTED")
                else:
                    selected.append(
                        (
                            leaf, wrapper, pair_matches,
                            header_time[0], header_time[1],
                        )
                    )
                    selected_pointers.add(leaf.node_pointer)
        else:
            wrapper_matches = [
                (leaf, REMOVAL_WRAPPER.fullmatch(leaf.text))
                for leaf in leaf_views
            ]
            wrapper_matches = [
                (leaf, match) for leaf, match in wrapper_matches
                if match is not None
            ]
            if not wrapper_matches:
                reasons.append("PAIR_WRAPPER_MISMATCH")
                if any(
                    leaf.text.lower().startswith("at ")
                    and "/" in leaf.text
                    for leaf in leaf_views
                ):
                    reasons.append("PAIR_TOKEN_REJECTED")
                for leaf in leaf_views:
                    if not (
                        leaf.text.lower().startswith("at ")
                        and "/" in leaf.text
                    ):
                        continue
                    time_candidates = list(T00.finditer(leaf.text))
                    if (
                        len(time_candidates) != 1
                        or _parse_time(time_candidates[0].group(0)) is None
                        or not _token_boundary_safe(
                            leaf, time_candidates[0].start(),
                            time_candidates[0].end(),
                        )
                    ):
                        reasons.append("UTC_INVALID")
            for leaf, wrapper in wrapper_matches:
                raw_time = wrapper.group("time")
                local_time = T00.search(
                    leaf.text,
                    wrapper.start("time"), wrapper.end("time"),
                )
                epoch = (
                    _parse_time(raw_time)
                    if local_time is not None
                    and _token_boundary_safe(
                        leaf, local_time.start(), local_time.end(),
                    )
                    else None
                )
                pair_matches = _pair_matches(
                    leaf, wrapper.start("pairs"), wrapper.end("pairs"),
                )
                lexical_pairs = list(
                    PAIR.finditer(
                        leaf.text,
                        wrapper.start("pairs"), wrapper.end("pairs"),
                    )
                )
                if epoch is None:
                    reasons.append("UTC_INVALID")
                elif (
                    not pair_matches
                    or len(pair_matches) != len(lexical_pairs)
                ):
                    reasons.append("PAIR_TOKEN_REJECTED")
                else:
                    selected.append(
                        (leaf, wrapper, pair_matches, epoch, local_time),
                    )
                    selected_pointers.add(leaf.node_pointer)
        for leaf in leaf_views:
            if (
                leaf.node_pointer not in selected_pointers
                and _collision(leaf)
            ):
                reasons.append("NONSELECTED_CARRIER_COLLISION")
                if leaf.text:
                    evidence.append(
                        _evidence(
                            "NONSELECTED_CARRIER_COLLISION", "COLLISION",
                            make_span(leaf, 0, len(leaf.text)),
                        )
                    )

    pair_bindings: dict[str, set[int]] = {}
    pair_occurrences: dict[str, int] = {}
    for leaf, _wrapper, pair_matches, epoch, _time_match in selected:
        for pair_match in pair_matches:
            token = pair_match.group(0)
            pair_occurrences[token] = pair_occurrences.get(token, 0) + 1
            pair_bindings.setdefault(token, set()).add(epoch)
    if any(count > 1 for count in pair_occurrences.values()):
        reasons.append("DUPLICATE_PAIR")
    if any(len(values) > 1 for values in pair_bindings.values()):
        reasons.append("PAIR_BOUND_TO_MULTIPLE_TIMES")
    reasons = sorted(set(reasons), key=REASON_RANK.__getitem__)
    if reasons:
        evidence.insert(0, _evidence(reasons[0], "ACTION", action_span))
        ambiguity = _ambiguity(detail, reasons, evidence)
        return (
            [], ambiguity,
            _coverage(
                detail, "AMBIGUOUS", 0, 1,
                ambiguity["primary_reason"],
            ),
        )

    claims: list[dict[str, Any]] = []
    for leaf, _wrapper, pair_matches, epoch, time_match in selected:
        if action["family"] == "OPEN":
            header_t = T00.search(view.text, suffix_start)
            if header_t is None:
                raise ClaimsError(
                    "OUTPUT_INTEGRITY", "validated OPEN time disappeared",
                )
            time_span = make_span(view, header_t.start(), header_t.end())
        else:
            time_span = make_span(
                leaf, time_match.start(), time_match.end(),
            )
        for pair_match in pair_matches:
            pair_span = make_span(
                leaf, pair_match.start(), pair_match.end(),
            )
            core = {
                "action_span_sha256": action_span["span_sha256"],
                "article_code": detail.article_code,
                "claim_type": (
                    "OPEN_SCHEDULE_CLAIM"
                    if action["family"] == "OPEN"
                    else "REMOVAL_SCHEDULE_CLAIM"
                ),
                "claimed_schedule_ms": epoch,
                "pair_span_sha256": pair_span["span_sha256"],
                "syntactic_pair_token_claim": pair_match.group(0),
                "time_span_sha256": time_span["span_sha256"],
            }
            claims.append(
                {
                    "article_code": detail.article_code,
                    "claim_id": sha256_bytes(canonical_compact(core)),
                    "claim_type": core["claim_type"],
                    "syntactic_pair_token_claim":
                        core["syntactic_pair_token_claim"],
                    "claimed_schedule_ms": epoch,
                    "accepted_response_sha256": detail.response_sha256,
                    "detail_body_sha256": detail.body_sha256,
                    "action_source_span": action_span,
                    "time_source_span": time_span,
                    "pair_source_span": pair_span,
                }
            )
    coverage = _coverage(
        detail, "CLAIMED", len(claims), 0, None,
    )
    return claims, None, coverage


def _ambiguity(
    detail: AcceptedDetail, reasons: Sequence[str],
    evidence: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    ordered_reasons = sorted(set(reasons), key=REASON_RANK.__getitem__)
    ordered_evidence = sorted(
        evidence,
        key=lambda item: (
            item["kind"].encode("utf-8"),
            REASON_RANK.get(item["reason"], len(REASON_RANK)),
            item["span"]["matching_start_cp"],
            item["span"]["matching_end_cp"],
            item["span"]["span_sha256"],
        ),
    )
    return {
        "article_code": detail.article_code,
        "accepted_response_sha256": detail.response_sha256,
        "detail_body_sha256": detail.body_sha256,
        "primary_reason": ordered_reasons[0],
        "reasons": ordered_reasons,
        "evidence_spans": ordered_evidence,
    }


def _coverage(
    detail: AcceptedDetail, status: str, claim_count: int,
    ambiguity_count: int, primary_reason: str | None,
) -> dict[str, Any]:
    return {
        "article_code": detail.article_code,
        "accepted_response_sha256": detail.response_sha256,
        "detail_body_sha256": detail.body_sha256,
        "status": status,
        "claim_count": claim_count,
        "ambiguity_count": ambiguity_count,
        "primary_reason": primary_reason,
    }


def _claim_sort(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row["article_code"].encode("utf-8"),
        0 if row["claim_type"] == "OPEN_SCHEDULE_CLAIM" else 1,
        row["claimed_schedule_ms"],
        row["syntactic_pair_token_claim"].encode("utf-8"),
        row["claim_id"],
    )


def build_payload(
    details: Sequence[AcceptedDetail],
    code_bindings: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    ambiguity: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    for detail in sorted(
        details, key=lambda item: item.article_code.encode("utf-8"),
    ):
        article_claims, article_ambiguity, article_coverage = (
            analyze_article(detail)
        )
        claims.extend(article_claims)
        if article_ambiguity is not None:
            ambiguity.append(article_ambiguity)
        coverage.append(article_coverage)
    claims.sort(key=_claim_sort)
    ambiguity.sort(key=lambda row: row["article_code"].encode("utf-8"))
    coverage.sort(key=lambda row: row["article_code"].encode("utf-8"))
    rows = {
        "claims.jsonl": claims,
        "ambiguity.jsonl": ambiguity,
        "coverage.jsonl": coverage,
    }
    payload_bytes = {
        name: b"".join(
            canonical_compact(row, newline=True) for row in values
        )
        for name, values in rows.items()
    }
    artifacts = [
        {
            "path": name, "rows": len(rows[name]),
            "bytes": len(payload_bytes[name]),
            "sha256": sha256_bytes(payload_bytes[name]),
        }
        for name in sorted(rows, key=lambda value: value.encode("utf-8"))
    ]
    tree_material = b"".join(
        (
            f"{item['path']}\0{item['rows']}\0{item['bytes']}\0"
            f"{item['sha256']}\n"
        ).encode("utf-8")
        for item in artifacts
    )
    coverage_counts = {
        key: sum(row["status"] == key for row in coverage)
        for key in ("CLAIMED", "AMBIGUOUS", "NO_MATCH")
    }
    claim_type_counts = {
        key: sum(row["claim_type"] == key for row in claims)
        for key in ("OPEN_SCHEDULE_CLAIM", "REMOVAL_SCHEDULE_CLAIM")
    }
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "run_id": RUN_ID,
        "version": VERSION,
        "semantics": SEMANTICS,
        "artifact_state": "ANNOUNCEMENT_SCHEDULE_CLAIM_SCAN_COMPLETE",
        "terminal_status": "NEEDS_MORE_DATA",
        "input_detail_count": len(details),
        "coverage_count": len(coverage),
        "ambiguity_count": len(ambiguity),
        "claim_count": len(claims),
        "coverage_counts": coverage_counts,
        "claim_type_counts": claim_type_counts,
        "input_bindings": INPUT_BINDINGS,
        "code_bindings": dict(code_bindings),
        "output_artifacts": artifacts,
        "payload_tree_sha256": sha256_bytes(tree_material),
        "historical_eligibility_ready": False,
        "eligibility_evaluated": False,
        "strict_eligible_count": 0,
    }
    return {
        "rows": rows,
        "payload_bytes": payload_bytes,
        "summary": summary,
        "summary_bytes": canonical_pretty(summary),
    }


def extract(
    repo_root: pathlib.Path,
    code_bindings: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    details = load_accepted_details(repo_root)
    payload = build_payload(details, code_bindings)
    payload["accepted_details"] = details
    return payload

