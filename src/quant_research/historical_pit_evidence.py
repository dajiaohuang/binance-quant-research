"""Strict offline adapter for hash-bound historical PIT eligibility claims.

The adapter is deliberately data-agnostic.  Its only registered V1 policy is a
synthetic fixture policy and is not authorized for empirical research.  Raw
bytes are verified before parsing; malformed evidence fails the whole call.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any

from quant_research.hierarchical_alpha import (
    EvidenceKind,
    EvidenceReference,
    EligibilityDecision,
    ListingWindow,
    MarketType,
    PITEligibilityEvidence,
    PITEligibilitySnapshot,
    PermissionState,
    TradingStatus,
    Venue,
    require_pit_eligibility,
)


SCHEMA_VERSION = "BINANCE_HISTORICAL_PIT_EVIDENCE_V1"
SYNTHETIC_POLICY_ID = "SYNTHETIC_BINANCE_HISTORICAL_PIT_FIXTURE_V1"
ALLOWED_AUTHORITY_ID = "SYNTHETIC_BINANCE_OFFICIAL_FIXTURE"
ALLOWED_SOURCE_CONTRACT_ID = "SYNTHETIC_BINANCE_HISTORICAL_PIT_SOURCE_CONTRACT_V1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

COMPONENT_STATUS = "TRADING_STATUS"
COMPONENT_PERMISSION = "SPOT_PERMISSION"
COMPONENT_QUOTE = "QUOTE_ASSET"
COMPONENT_LISTING = "LISTING_WINDOW"
COMPONENTS = (
    COMPONENT_STATUS,
    COMPONENT_PERMISSION,
    COMPONENT_QUOTE,
    COMPONENT_LISTING,
)

SEMANTICS_HISTORICAL = "HISTORICAL_EFFECTIVE_FACT"
ASSERTION_SEMANTICS = (
    SEMANTICS_HISTORICAL,
    "PLANNED_SCHEDULE_CLAIM",
    "CURRENT_OBSERVATION",
    "ARCHIVE_AVAILABILITY",
    "RESPONSE_ABSENCE",
)
REVISION_ACTIONS = ("ASSERT", "REPLACE", "CANCEL")

TOP_KEYS = frozenset(
    {"schema_version", "authority_id", "venue", "market_type", "records"}
)
RECORD_KEYS = frozenset(
    {
        "source_id",
        "symbol",
        "component",
        "value",
        "effective_from_ms",
        "effective_to_ms_exclusive",
        "published_at_ms",
        "assertion_semantics",
        "revision_action",
        "revision_target_source_id",
    }
)


class HistoricalPITEvidenceError(ValueError):
    """Any integrity, schema, or revision failure."""


class EmpiricalPITAuthorizationError(HistoricalPITEvidenceError):
    """Raised when an unapproved policy reaches the empirical entry point."""


@dataclass(frozen=True)
class RawPayloadBinding:
    raw_bytes: bytes
    expected_sha256: str
    source_contract_id: str
    payload_id: str
    policy_id: str = SYNTHETIC_POLICY_ID


@dataclass(frozen=True)
class HistoricalPITPolicyInfo:
    policy_id: str
    empirical_authorized: bool
    qualifying_assertion_semantics: str
    schema_version: str
    allowed_authority_id: str
    allowed_source_contract_id: str
    allowed_venue: str
    allowed_market_type: str


@dataclass(frozen=True)
class BoundRawPayload:
    source_contract_id: str
    payload_id: str
    policy_id: str
    exact_raw_sha256: str
    canonical_payload_sha256: str
    authority_id: str
    venue: str
    market_type: str
    record_count: int


@dataclass(frozen=True)
class RawRecordLocator:
    source_contract_id: str
    payload_id: str
    raw_payload_sha256: str
    json_pointer: str


@dataclass(frozen=True)
class HistoricalPITClaim:
    policy_id: str
    source_contract_id: str
    payload_id: str
    raw_payload_sha256: str
    authority_id: str
    venue: str
    market_type: str
    source_id: str
    symbol: str
    component: str
    value: str | None
    effective_from_ms: int | None
    effective_to_ms_exclusive: int | None
    published_at_ms: int
    assertion_semantics: str
    revision_action: str
    revision_target_source_id: str | None
    raw_locator: RawRecordLocator
    record_fragment_sha256: str
    claim_id: str


@dataclass(frozen=True)
class RevisionLedgerEntry:
    source_id: str
    claim_id: str
    revision_action: str
    revision_target_source_id: str | None
    superseded_by_source_id: str | None


@dataclass(frozen=True)
class ComponentResolution:
    symbol: str
    component: str
    state: str
    reason: str
    normalized_value: str | tuple[str, int, int | None] | None
    considered_claim_ids: tuple[str, ...]
    active_claim_ids: tuple[str, ...]
    evidence_reference: EvidenceReference | None


@dataclass(frozen=True)
class BoundHistoricalPITResult:
    snapshot: PITEligibilitySnapshot
    resolution_ledger: tuple[ComponentResolution, ...]
    revision_ledger: tuple[RevisionLedgerEntry, ...]
    raw_payloads: tuple[BoundRawPayload, ...]
    claims: tuple[HistoricalPITClaim, ...]
    policy_info: HistoricalPITPolicyInfo
    semantic_resolution_sha256: str


POLICY_REGISTRY = MappingProxyType(
    {
        SYNTHETIC_POLICY_ID: HistoricalPITPolicyInfo(
            policy_id=SYNTHETIC_POLICY_ID,
            empirical_authorized=False,
            qualifying_assertion_semantics=SEMANTICS_HISTORICAL,
            schema_version=SCHEMA_VERSION,
            allowed_authority_id=ALLOWED_AUTHORITY_ID,
            allowed_source_contract_id=ALLOWED_SOURCE_CONTRACT_ID,
            allowed_venue=Venue.BINANCE.value,
            allowed_market_type=MarketType.SPOT.value,
        )
    }
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha(value: object) -> str:
    return _sha(_canonical_bytes(value))


def _reject_constant(value: str) -> None:
    raise HistoricalPITEvidenceError(f"non-finite JSON number {value!r}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HistoricalPITEvidenceError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_surrogates(value: object, path: str = "$") -> None:
    if type(value) is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise HistoricalPITEvidenceError(f"surrogate code point at {path}")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _reject_surrogates(item, f"{path}/{index}")
        return
    if type(value) is dict:
        for key, item in value.items():
            _reject_surrogates(key, f"{path}/<key>")
            _reject_surrogates(item, f"{path}/{key}")
        return
    if value is not None and type(value) not in (int, float, bool):
        raise HistoricalPITEvidenceError(f"unsupported JSON value at {path}")
    if type(value) is float and not math.isfinite(value):
        raise HistoricalPITEvidenceError(f"non-finite JSON number at {path}")


def _strict_json(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise HistoricalPITEvidenceError("raw_bytes must be exact bytes")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise HistoricalPITEvidenceError("UTF-8 BOM is forbidden")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HistoricalPITEvidenceError("payload is not strict UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except HistoricalPITEvidenceError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise HistoricalPITEvidenceError("payload is not one strict JSON value") from exc
    _reject_surrogates(value)
    if type(value) is not dict:
        raise HistoricalPITEvidenceError("payload top level must be an object")
    return value


def _require_exact_keys(value: dict[str, Any], keys: frozenset[str], path: str) -> None:
    actual = frozenset(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        raise HistoricalPITEvidenceError(
            f"{path} keyset mismatch; missing={missing!r}, extra={extra!r}"
        )


def _nonempty_text(value: object, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise HistoricalPITEvidenceError(f"{label} must be a canonical non-empty string")
    if any(character.isspace() or ord(character) < 0x20 for character in value):
        raise HistoricalPITEvidenceError(f"{label} contains whitespace/control")
    _reject_surrogates(value, label)
    return value


def _milliseconds(value: object, label: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or value < 0:
        raise HistoricalPITEvidenceError(f"{label} must be a nonnegative integer")
    return value


def _require_sha(value: object, label: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None:
        raise HistoricalPITEvidenceError(f"{label} must be a lowercase SHA-256")
    return value


def _enum_argument(value: object, enum_type: type[Any], label: str) -> Any:
    if type(value) is not enum_type:
        raise HistoricalPITEvidenceError(f"{label} must be {enum_type.__name__}")
    return value


def _validate_requested_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    if isinstance(symbols, (str, bytes)):
        raise HistoricalPITEvidenceError("requested_symbols must be an explicit iterable")
    try:
        requested = tuple(symbols)
    except TypeError as exc:
        raise HistoricalPITEvidenceError("requested_symbols must be iterable") from exc
    if not requested:
        raise HistoricalPITEvidenceError("requested_symbols must be non-empty")
    canonical = tuple(_nonempty_text(value, "requested symbol") for value in requested)
    if len(set(canonical)) != len(canonical):
        raise HistoricalPITEvidenceError("requested_symbols must be unique")
    return tuple(sorted(canonical, key=lambda value: value.encode("utf-8")))


def _normalized_record(record: dict[str, Any], component: str) -> dict[str, Any]:
    value = record["value"]
    action = record["revision_action"]
    target = record["revision_target_source_id"]
    start = record["effective_from_ms"]
    end = record["effective_to_ms_exclusive"]

    if component not in COMPONENTS:
        raise HistoricalPITEvidenceError(f"unsupported component {component!r}")
    if action not in REVISION_ACTIONS:
        raise HistoricalPITEvidenceError(f"unsupported revision action {action!r}")
    if record["assertion_semantics"] not in ASSERTION_SEMANTICS:
        raise HistoricalPITEvidenceError("unsupported assertion_semantics")
    _milliseconds(record["published_at_ms"], "published_at_ms")

    if action == "ASSERT":
        if target is not None:
            raise HistoricalPITEvidenceError("ASSERT revision target must be null")
    else:
        _nonempty_text(target, "revision_target_source_id")

    if action == "CANCEL":
        if value is not None or start is not None or end is not None:
            raise HistoricalPITEvidenceError("CANCEL assertion fields must be null")
        return record

    start_ms = _milliseconds(start, "effective_from_ms")
    end_ms = _milliseconds(end, "effective_to_ms_exclusive", nullable=True)
    if end_ms is not None and end_ms <= start_ms:
        raise HistoricalPITEvidenceError("effective interval must be half-open and non-empty")

    if component == COMPONENT_STATUS:
        if value not in ("TRADING", "NOT_TRADING"):
            raise HistoricalPITEvidenceError("invalid TRADING_STATUS value")
    elif component == COMPONENT_PERMISSION:
        if value not in ("ENABLED", "DISABLED"):
            raise HistoricalPITEvidenceError("invalid SPOT_PERMISSION value")
    elif component == COMPONENT_QUOTE:
        _nonempty_text(value, "QUOTE_ASSET value")
    elif component == COMPONENT_LISTING:
        if value != "LISTED":
            raise HistoricalPITEvidenceError("LISTING_WINDOW value must be LISTED")
    return record


def _parse_binding(
    binding: RawPayloadBinding,
    *,
    expected_venue: Venue,
    expected_market_type: MarketType,
) -> tuple[BoundRawPayload, list[HistoricalPITClaim]]:
    if type(binding) is not RawPayloadBinding:
        raise HistoricalPITEvidenceError("raw_payload_bindings must contain RawPayloadBinding")
    expected_sha = _require_sha(binding.expected_sha256, "expected_sha256")
    source_contract_id = _nonempty_text(binding.source_contract_id, "source_contract_id")
    payload_id = _nonempty_text(binding.payload_id, "payload_id")
    policy_id = _nonempty_text(binding.policy_id, "policy_id")
    if policy_id not in POLICY_REGISTRY:
        raise HistoricalPITEvidenceError(f"unregistered policy {policy_id!r}")
    policy = POLICY_REGISTRY[policy_id]
    if source_contract_id != policy.allowed_source_contract_id:
        raise HistoricalPITEvidenceError("source_contract_id is not allowed by policy")
    if _sha(binding.raw_bytes) != expected_sha:
        raise HistoricalPITEvidenceError("raw payload SHA-256 mismatch")

    payload = _strict_json(binding.raw_bytes)
    _require_exact_keys(payload, TOP_KEYS, "payload")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise HistoricalPITEvidenceError("schema_version mismatch")
    authority_id = _nonempty_text(payload["authority_id"], "authority_id")
    if authority_id != policy.allowed_authority_id:
        raise HistoricalPITEvidenceError("authority_id is not allowed by policy")
    if payload["venue"] != policy.allowed_venue or payload["venue"] != expected_venue.value:
        raise HistoricalPITEvidenceError("payload venue mismatch")
    if (
        payload["market_type"] != policy.allowed_market_type
        or payload["market_type"] != expected_market_type.value
    ):
        raise HistoricalPITEvidenceError("payload market_type mismatch")
    records = payload["records"]
    if type(records) is not list:
        raise HistoricalPITEvidenceError("records must be a list")

    claims: list[HistoricalPITClaim] = []
    canonical_records: list[dict[str, Any]] = []
    for index, candidate in enumerate(records):
        if type(candidate) is not dict:
            raise HistoricalPITEvidenceError(f"/records/{index} must be an object")
        _require_exact_keys(candidate, RECORD_KEYS, f"/records/{index}")
        record = dict(candidate)
        source_id = _nonempty_text(record["source_id"], "source_id")
        symbol = _nonempty_text(record["symbol"], "symbol")
        component = _nonempty_text(record["component"], "component")
        _normalized_record(record, component)
        fragment_sha = _canonical_sha(record)
        logical_claim = {
            "assertion_semantics": record["assertion_semantics"],
            "authority_id": authority_id,
            "component": component,
            "effective_from_ms": record["effective_from_ms"],
            "effective_to_ms_exclusive": record["effective_to_ms_exclusive"],
            "market_type": expected_market_type.value,
            "payload_id": payload_id,
            "policy_id": policy_id,
            "published_at_ms": record["published_at_ms"],
            "raw_locator": {
                "json_pointer": f"/records/{index}",
                "payload_id": payload_id,
                "raw_payload_sha256": expected_sha,
                "source_contract_id": source_contract_id,
            },
            "raw_payload_sha256": expected_sha,
            "record_fragment_sha256": fragment_sha,
            "revision_action": record["revision_action"],
            "revision_target_source_id": record["revision_target_source_id"],
            "source_contract_id": source_contract_id,
            "source_id": source_id,
            "symbol": symbol,
            "value": record["value"],
            "venue": expected_venue.value,
        }
        claim_id = _canonical_sha(logical_claim)
        claims.append(
            HistoricalPITClaim(
                policy_id=policy_id,
                source_contract_id=source_contract_id,
                payload_id=payload_id,
                raw_payload_sha256=expected_sha,
                authority_id=authority_id,
                venue=expected_venue.value,
                market_type=expected_market_type.value,
                source_id=source_id,
                symbol=symbol,
                component=component,
                value=record["value"],
                effective_from_ms=record["effective_from_ms"],
                effective_to_ms_exclusive=record["effective_to_ms_exclusive"],
                published_at_ms=record["published_at_ms"],
                assertion_semantics=record["assertion_semantics"],
                revision_action=record["revision_action"],
                revision_target_source_id=record["revision_target_source_id"],
                raw_locator=RawRecordLocator(
                    source_contract_id=source_contract_id,
                    payload_id=payload_id,
                    raw_payload_sha256=expected_sha,
                    json_pointer=f"/records/{index}",
                ),
                record_fragment_sha256=fragment_sha,
                claim_id=claim_id,
            )
        )
        canonical_records.append(record)

    canonical_records.sort(key=lambda item: _canonical_bytes(item))
    canonical_payload = {
        "authority_id": authority_id,
        "market_type": expected_market_type.value,
        "records": canonical_records,
        "schema_version": SCHEMA_VERSION,
        "venue": expected_venue.value,
    }
    bound = BoundRawPayload(
        source_contract_id=source_contract_id,
        payload_id=payload_id,
        policy_id=policy_id,
        exact_raw_sha256=expected_sha,
        canonical_payload_sha256=_canonical_sha(canonical_payload),
        authority_id=authority_id,
        venue=expected_venue.value,
        market_type=expected_market_type.value,
        record_count=len(records),
    )
    return bound, claims


def replay_bound_record_fragment(
    raw_payload_bindings: Sequence[RawPayloadBinding],
    locator: RawRecordLocator,
) -> bytes:
    """Replay one canonical record fragment from an exact composite locator."""

    if type(locator) is not RawRecordLocator:
        raise HistoricalPITEvidenceError("locator must be RawRecordLocator")
    if type(raw_payload_bindings) not in (tuple, list):
        raise HistoricalPITEvidenceError("raw_payload_bindings must be a sequence")
    matches = [
        binding
        for binding in raw_payload_bindings
        if type(binding) is RawPayloadBinding
        and binding.source_contract_id == locator.source_contract_id
        and binding.payload_id == locator.payload_id
        and binding.expected_sha256 == locator.raw_payload_sha256
    ]
    if len(matches) != 1:
        raise HistoricalPITEvidenceError("composite locator does not identify one payload")
    selected = matches[0]
    if _sha(selected.raw_bytes) != locator.raw_payload_sha256:
        raise HistoricalPITEvidenceError("locator raw payload SHA-256 mismatch")
    pointer_match = re.fullmatch(r"/records/(0|[1-9][0-9]*)", locator.json_pointer)
    if pointer_match is None:
        raise HistoricalPITEvidenceError("locator JSON pointer is invalid")
    parsed = _strict_json(selected.raw_bytes)
    _require_exact_keys(parsed, TOP_KEYS, "payload")
    records = parsed["records"]
    index = int(pointer_match.group(1))
    if type(records) is not list or index >= len(records) or type(records[index]) is not dict:
        raise HistoricalPITEvidenceError("locator JSON pointer is out of range")
    _require_exact_keys(records[index], RECORD_KEYS, locator.json_pointer)
    return _canonical_bytes(records[index])


def _validate_revisions(
    claims: Sequence[HistoricalPITClaim],
) -> tuple[tuple[RevisionLedgerEntry, ...], dict[str, str]]:
    by_source: dict[str, HistoricalPITClaim] = {}
    for claim in claims:
        if claim.source_id in by_source:
            raise HistoricalPITEvidenceError(f"duplicate source_id {claim.source_id!r}")
        by_source[claim.source_id] = claim

    child_by_target: dict[str, str] = {}
    lineage_times: set[tuple[str, str, str, str, str, str, str, int]] = set()
    for claim in claims:
        tie_key = (
            claim.policy_id,
            claim.source_contract_id,
            claim.authority_id,
            claim.venue,
            claim.market_type,
            claim.symbol,
            claim.component,
            claim.published_at_ms,
        )
        if tie_key in lineage_times:
            raise HistoricalPITEvidenceError("same-lineage known-at tie")
        lineage_times.add(tie_key)
        target_id = claim.revision_target_source_id
        if target_id is None:
            continue
        target = by_source.get(target_id)
        if target is None:
            raise HistoricalPITEvidenceError(f"dangling revision target {target_id!r}")
        lineage = (
            "policy_id",
            "source_contract_id",
            "authority_id",
            "venue",
            "market_type",
            "symbol",
            "component",
        )
        if any(getattr(claim, field) != getattr(target, field) for field in lineage):
            raise HistoricalPITEvidenceError("revision crosses evidence lineage")
        if target.published_at_ms >= claim.published_at_ms:
            raise HistoricalPITEvidenceError("revision target must be known strictly earlier")
        if target_id in child_by_target:
            raise HistoricalPITEvidenceError("revision lineage branches")
        child_by_target[target_id] = claim.source_id

    for source_id in by_source:
        seen: set[str] = set()
        cursor: str | None = source_id
        while cursor is not None:
            if cursor in seen:
                raise HistoricalPITEvidenceError("revision lineage cycles")
            seen.add(cursor)
            cursor = by_source[cursor].revision_target_source_id

    ledger = tuple(
        RevisionLedgerEntry(
            source_id=claim.source_id,
            claim_id=claim.claim_id,
            revision_action=claim.revision_action,
            revision_target_source_id=claim.revision_target_source_id,
            superseded_by_source_id=child_by_target.get(claim.source_id),
        )
        for claim in sorted(claims, key=lambda item: item.source_id.encode("utf-8"))
    )
    return ledger, child_by_target


def _active_claims(
    claims: Sequence[HistoricalPITClaim],
    child_by_target: dict[str, str],
    formation_time_ms: int,
) -> tuple[HistoricalPITClaim, ...]:
    by_source = {claim.source_id: claim for claim in claims}
    targeted = set(child_by_target)
    roots = [claim for claim in claims if claim.revision_target_source_id is None]
    active: list[HistoricalPITClaim] = []
    for root in roots:
        if root.published_at_ms > formation_time_ms:
            continue
        current = root
        while current.source_id in targeted:
            child = by_source[child_by_target[current.source_id]]
            if child.published_at_ms > formation_time_ms:
                break
            current = child
        if current.revision_action == "CANCEL":
            continue
        if current.assertion_semantics != SEMANTICS_HISTORICAL:
            continue
        assert current.effective_from_ms is not None
        if formation_time_ms < current.effective_from_ms:
            continue
        if (
            current.effective_to_ms_exclusive is not None
            and formation_time_ms >= current.effective_to_ms_exclusive
        ):
            continue
        active.append(current)
    return tuple(sorted(active, key=lambda item: item.claim_id))


def _normalize_claim_value(
    claim: HistoricalPITClaim,
) -> str | tuple[str, int, int | None]:
    if claim.component == COMPONENT_LISTING:
        assert claim.effective_from_ms is not None
        return ("LISTED", claim.effective_from_ms, claim.effective_to_ms_exclusive)
    assert claim.value is not None
    return claim.value


def _reference_kind(component: str) -> EvidenceKind:
    return {
        COMPONENT_STATUS: EvidenceKind.VENUE_MARKET_STATUS,
        COMPONENT_PERMISSION: EvidenceKind.SPOT_PERMISSION,
        COMPONENT_QUOTE: EvidenceKind.QUOTE_ASSET_RULE,
        COMPONENT_LISTING: EvidenceKind.LISTING_WINDOW,
    }[component]


def _component_resolution(
    *,
    symbol: str,
    component: str,
    component_claims: Sequence[HistoricalPITClaim],
    active: Sequence[HistoricalPITClaim],
    formation_time_ms: int,
    policy: HistoricalPITPolicyInfo,
) -> ComponentResolution:
    considered_ids = tuple(sorted(claim.claim_id for claim in component_claims))
    active_ids = tuple(sorted(claim.claim_id for claim in active))
    if not active:
        reason = "NO_ACTIVE_HISTORICAL_EFFECTIVE_FACT"
        return ComponentResolution(
            symbol=symbol,
            component=component,
            state="UNKNOWN",
            reason=reason,
            normalized_value=None,
            considered_claim_ids=considered_ids,
            active_claim_ids=(),
            evidence_reference=None,
        )
    normalized = {_canonical_bytes(_normalize_claim_value(claim)) for claim in active}
    if len(normalized) != 1:
        return ComponentResolution(
            symbol=symbol,
            component=component,
            state="UNKNOWN",
            reason="CONFLICTING_ACTIVE_HISTORICAL_EFFECTIVE_FACTS",
            normalized_value=None,
            considered_claim_ids=considered_ids,
            active_claim_ids=active_ids,
            evidence_reference=None,
        )
    value = _normalize_claim_value(active[0])
    evidence_sha = _canonical_sha(
        {
            "active_contributors": [
                {
                    "exact_raw_sha256": claim.raw_payload_sha256,
                    "json_pointer": claim.raw_locator.json_pointer,
                    "payload_id": claim.payload_id,
                    "physical_claim_id": claim.claim_id,
                    "record_fragment_sha256": claim.record_fragment_sha256,
                    "source_contract_id": claim.source_contract_id,
                }
                for claim in sorted(active, key=lambda item: item.claim_id)
            ],
            "component": component,
            "formation_time_ms": formation_time_ms,
            "normalized_value": value,
            "policy_id": policy.policy_id,
            "symbol": symbol,
        }
    )
    return ComponentResolution(
        symbol=symbol,
        component=component,
        state="RESOLVED",
        reason="ACTIVE_HISTORICAL_EFFECTIVE_FACT",
        normalized_value=value,
        considered_claim_ids=considered_ids,
        active_claim_ids=active_ids,
        evidence_reference=EvidenceReference(
            kind=_reference_kind(component),
            known_at_ms=max(claim.published_at_ms for claim in active),
            sha256=evidence_sha,
        ),
    )


def _artifact_value(value: object) -> object:
    if isinstance(value, tuple):
        return [_artifact_value(item) for item in value]
    if isinstance(value, (EvidenceKind, Venue, MarketType, TradingStatus, PermissionState)):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return _artifact_value(asdict(value))
    if type(value) is dict:
        return {key: _artifact_value(item) for key, item in value.items()}
    if type(value) is list:
        return [_artifact_value(item) for item in value]
    return value


def _semantic_claim_projection(claim: HistoricalPITClaim) -> dict[str, object]:
    return {
        "assertion_semantics": claim.assertion_semantics,
        "component": claim.component,
        "effective_from_ms": claim.effective_from_ms,
        "effective_to_ms_exclusive": claim.effective_to_ms_exclusive,
        "published_at_ms": claim.published_at_ms,
        "revision_action": claim.revision_action,
        "revision_target_source_id": claim.revision_target_source_id,
        "source_id": claim.source_id,
        "symbol": claim.symbol,
        "value": claim.value,
    }


def _semantic_resolution_projection(
    resolutions: Sequence[ComponentResolution],
    claims: Sequence[HistoricalPITClaim],
) -> list[dict[str, object]]:
    source_by_claim_id = {claim.claim_id: claim.source_id for claim in claims}
    return [
        {
            "active_source_ids": sorted(
                (source_by_claim_id[claim_id] for claim_id in resolution.active_claim_ids),
                key=lambda value: value.encode("utf-8"),
            ),
            "component": resolution.component,
            "considered_source_ids": sorted(
                (
                    source_by_claim_id[claim_id]
                    for claim_id in resolution.considered_claim_ids
                ),
                key=lambda value: value.encode("utf-8"),
            ),
            "normalized_value": _artifact_value(resolution.normalized_value),
            "reason": resolution.reason,
            "state": resolution.state,
            "symbol": resolution.symbol,
        }
        for resolution in resolutions
    ]


def _semantic_revision_projection(
    revision_ledger: Sequence[RevisionLedgerEntry],
) -> list[dict[str, object]]:
    return [
        {
            "revision_action": item.revision_action,
            "revision_target_source_id": item.revision_target_source_id,
            "source_id": item.source_id,
            "superseded_by_source_id": item.superseded_by_source_id,
        }
        for item in revision_ledger
    ]


def build_bound_historical_pit_snapshot(
    raw_payload_bindings: Sequence[RawPayloadBinding],
    formation_time_ms: int,
    requested_symbols: Iterable[str],
    *,
    expected_venue: Venue = Venue.BINANCE,
    expected_market_type: MarketType = MarketType.SPOT,
    expected_quote_asset: str,
) -> BoundHistoricalPITResult:
    """Build a deterministic synthetic PIT snapshot from exact bound bytes."""

    _enum_argument(expected_venue, Venue, "expected_venue")
    _enum_argument(expected_market_type, MarketType, "expected_market_type")
    if expected_venue is not Venue.BINANCE or expected_market_type is not MarketType.SPOT:
        raise HistoricalPITEvidenceError("V1 supports BINANCE SPOT only")
    formation = _milliseconds(formation_time_ms, "formation_time_ms")
    assert formation is not None
    quote_asset = _nonempty_text(expected_quote_asset, "expected_quote_asset")
    requested = _validate_requested_symbols(requested_symbols)
    if isinstance(raw_payload_bindings, (bytes, bytearray, str)):
        raise HistoricalPITEvidenceError("raw_payload_bindings must be a sequence")
    if type(raw_payload_bindings) not in (tuple, list) or not raw_payload_bindings:
        raise HistoricalPITEvidenceError("raw_payload_bindings must be non-empty")

    bound_payloads: list[BoundRawPayload] = []
    claims: list[HistoricalPITClaim] = []
    for binding in raw_payload_bindings:
        bound, parsed = _parse_binding(
            binding,
            expected_venue=expected_venue,
            expected_market_type=expected_market_type,
        )
        bound_payloads.append(bound)
        claims.extend(parsed)
    policy_ids = {bound.policy_id for bound in bound_payloads}
    if len(policy_ids) != 1:
        raise HistoricalPITEvidenceError("all payloads must use one policy")
    policy = POLICY_REGISTRY[next(iter(policy_ids))]
    payload_ids = [bound.payload_id for bound in bound_payloads]
    if len(set(payload_ids)) != len(payload_ids):
        raise HistoricalPITEvidenceError("duplicate payload_id")

    revision_ledger, child_by_target = _validate_revisions(claims)
    raw_manifest = tuple(
        {
            "authority_id": bound.authority_id,
            "exact_raw_sha256": bound.exact_raw_sha256,
            "payload_id": bound.payload_id,
            "policy_id": bound.policy_id,
            "source_contract_id": bound.source_contract_id,
        }
        for bound in sorted(
            bound_payloads, key=lambda item: item.payload_id.encode("utf-8")
        )
    )

    resolutions: list[ComponentResolution] = []
    memberships: list[PITEligibilityEvidence] = []
    for symbol in requested:
        resolved: dict[str, ComponentResolution] = {}
        for component in COMPONENTS:
            component_claims = tuple(
                claim
                for claim in claims
                if claim.symbol == symbol and claim.component == component
            )
            active = _active_claims(component_claims, child_by_target, formation)
            resolution = _component_resolution(
                symbol=symbol,
                component=component,
                component_claims=component_claims,
                active=active,
                formation_time_ms=formation,
                policy=policy,
            )
            resolutions.append(resolution)
            resolved[component] = resolution

        status_value = resolved[COMPONENT_STATUS].normalized_value
        permission_value = resolved[COMPONENT_PERMISSION].normalized_value
        quote_value = resolved[COMPONENT_QUOTE].normalized_value
        listing_value = resolved[COMPONENT_LISTING].normalized_value
        references = tuple(
            resolution.evidence_reference
            for resolution in (resolved[component] for component in COMPONENTS)
            if resolution.evidence_reference is not None
        )
        memberships.append(
            PITEligibilityEvidence(
                symbol=symbol,
                formation_time_ms=formation,
                venue=expected_venue,
                market_type=expected_market_type,
                trading_status=(
                    TradingStatus(status_value)
                    if type(status_value) is str
                    else TradingStatus.UNKNOWN
                ),
                spot_permission=(
                    PermissionState(permission_value)
                    if type(permission_value) is str
                    else PermissionState.UNKNOWN
                ),
                quote_asset=quote_value if type(quote_value) is str else None,
                listing=(
                    ListingWindow(
                        effective_from_ms=listing_value[1],
                        effective_to_ms_exclusive=listing_value[2],
                    )
                    if type(listing_value) is tuple
                    else None
                ),
                evidence=references,
            )
        )

    semantic_claims = [
        _semantic_claim_projection(claim)
        for claim in sorted(
            claims,
            key=lambda item: (
                item.source_id.encode("utf-8"),
                item.symbol.encode("utf-8"),
                item.component.encode("utf-8"),
            ),
        )
    ]
    semantic_projection = {
        "claims": semantic_claims,
        "expected_market_type": expected_market_type.value,
        "expected_quote_asset": quote_asset,
        "expected_venue": expected_venue.value,
        "formation_time_ms": formation,
        "requested_symbols": requested,
        "resolutions": _semantic_resolution_projection(resolutions, claims),
        "revisions": _semantic_revision_projection(revision_ledger),
    }
    semantic_resolution_sha = _canonical_sha(semantic_projection)
    artifact_sha = _canonical_sha(
        {
            "policy": _artifact_value(policy),
            "raw_manifest": raw_manifest,
            "semantic_resolution_sha256": semantic_resolution_sha,
            "scope": {
                "formation_time_ms": formation,
                "expected_market_type": expected_market_type.value,
                "expected_quote_asset": quote_asset,
                "expected_venue": expected_venue.value,
                "requested_symbols": requested,
            },
        }
    )
    snapshot = PITEligibilitySnapshot(
        formation_time_ms=formation,
        expected_venue=expected_venue,
        expected_market_type=expected_market_type,
        expected_quote_asset=quote_asset,
        memberships=tuple(memberships),
        artifact_sha256=artifact_sha,
    )
    return BoundHistoricalPITResult(
        snapshot=snapshot,
        resolution_ledger=tuple(resolutions),
        revision_ledger=revision_ledger,
        raw_payloads=tuple(
            sorted(
                bound_payloads,
                key=lambda item: item.payload_id.encode("utf-8"),
            )
        ),
        claims=tuple(sorted(claims, key=lambda item: item.claim_id)),
        policy_info=policy,
        semantic_resolution_sha256=semantic_resolution_sha,
    )


def require_trusted_empirical_pit_eligibility(
    raw_payload_bindings: Sequence[RawPayloadBinding],
    *,
    formation_time_ms: int,
    requested_symbols: Iterable[str],
    expected_venue: Venue = Venue.BINANCE,
    expected_market_type: MarketType = MarketType.SPOT,
    expected_quote_asset: str = "USDT",
) -> dict[str, EligibilityDecision]:
    """Empirical boundary; V1 rejects every registered synthetic policy."""

    if type(raw_payload_bindings) is PITEligibilitySnapshot:
        raise EmpiricalPITAuthorizationError("empirical API does not accept a snapshot")
    result = build_bound_historical_pit_snapshot(
        raw_payload_bindings,
        formation_time_ms,
        requested_symbols,
        expected_venue=expected_venue,
        expected_market_type=expected_market_type,
        expected_quote_asset=expected_quote_asset,
    )
    decisions = require_pit_eligibility(
        result.snapshot,
        formation_time_ms=formation_time_ms,
        symbols=tuple(membership.symbol for membership in result.snapshot.memberships),
    )
    if not result.policy_info.empirical_authorized:
        raise EmpiricalPITAuthorizationError(
            f"policy {result.policy_info.policy_id!r} is not empirically authorized"
        )
    return decisions


__all__ = [
    "ALLOWED_AUTHORITY_ID",
    "ALLOWED_SOURCE_CONTRACT_ID",
    "ASSERTION_SEMANTICS",
    "BoundHistoricalPITResult",
    "BoundRawPayload",
    "COMPONENTS",
    "ComponentResolution",
    "EmpiricalPITAuthorizationError",
    "HistoricalPITClaim",
    "HistoricalPITEvidenceError",
    "HistoricalPITPolicyInfo",
    "POLICY_REGISTRY",
    "RawPayloadBinding",
    "RawRecordLocator",
    "RevisionLedgerEntry",
    "SCHEMA_VERSION",
    "SYNTHETIC_POLICY_ID",
    "build_bound_historical_pit_snapshot",
    "replay_bound_record_fragment",
    "require_trusted_empirical_pit_eligibility",
]
