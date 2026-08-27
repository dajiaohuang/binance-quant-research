"""Fail-closed, data-agnostic primitives for hierarchical alpha research.

This module has no loader, network client, model trainer, optimizer, portfolio
simulator, backtester, or CLI.  It accepts only explicit synthetic/PIT contract
types; archive availability can never substitute for eligibility evidence.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

import numpy
from scipy.linalg import lstsq


HOUR_MILLISECONDS = 3_600_000
ALPHA_HORIZONS_HOURS = (1, 24, 120, 480)
SIMPLEX_TOLERANCE = 1e-12
ORTHOGONALITY_TOLERANCE = 1e-10
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PIT_SEMANTICS = "POINT_IN_TIME_BINANCE_SPOT_ELIGIBILITY"


class AlphaContractError(ValueError):
    """Base error for any fail-closed kernel contract violation."""


class PITGateError(AlphaContractError):
    pass


class ExpertRegistryError(AlphaContractError):
    pass


class CrossSectionError(AlphaContractError):
    pass


class LabelContractError(AlphaContractError):
    pass


class EnsembleContractError(AlphaContractError):
    pass


class ArchiveState(str, Enum):
    AVAILABLE = "A"
    NO_OBJECT = "N"
    VALIDATED_OBJECT_ROW_MISSING = "M"
    INVALID_OR_UNVERIFIED = "U"


class Venue(str, Enum):
    BINANCE = "BINANCE"


class MarketType(str, Enum):
    SPOT = "SPOT"
    MARGIN = "MARGIN"
    FUTURES = "FUTURES"


class TradingStatus(str, Enum):
    TRADING = "TRADING"
    NOT_TRADING = "NOT_TRADING"
    UNKNOWN = "UNKNOWN"


class PermissionState(str, Enum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class EvidenceKind(str, Enum):
    VENUE_MARKET_STATUS = "VENUE_MARKET_STATUS"
    SPOT_PERMISSION = "SPOT_PERMISSION"
    QUOTE_ASSET_RULE = "QUOTE_ASSET_RULE"
    LISTING_WINDOW = "LISTING_WINDOW"
    ARCHIVE_DERIVED = "ARCHIVE_DERIVED"


class ExpertReadiness(str, Enum):
    SYNTHETIC_READY = "SYNTHETIC_READY"
    PIT_BLOCKED = "PIT_BLOCKED"
    DATA_BLOCKED = "DATA_BLOCKED"


class SplitRole(str, Enum):
    TRAIN = "TRAIN"
    EVALUATION = "EVALUATION"


@dataclass(frozen=True)
class ArchiveAvailability:
    symbol: str
    formation_time_ms: int
    state: ArchiveState
    artifact_sha256: str


@dataclass(frozen=True)
class EvidenceReference:
    kind: EvidenceKind
    known_at_ms: int
    sha256: str


@dataclass(frozen=True)
class ListingWindow:
    effective_from_ms: int
    effective_to_ms_exclusive: int | None


@dataclass(frozen=True)
class PITEligibilityEvidence:
    symbol: str
    formation_time_ms: int
    venue: Venue
    market_type: MarketType
    trading_status: TradingStatus
    spot_permission: PermissionState
    quote_asset: str | None
    listing: ListingWindow | None
    evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class PITEligibilitySnapshot:
    formation_time_ms: int
    expected_venue: Venue
    expected_market_type: MarketType
    expected_quote_asset: str
    memberships: tuple[PITEligibilityEvidence, ...]
    artifact_sha256: str
    semantics: str = PIT_SEMANTICS


@dataclass(frozen=True)
class EligibilityDecision:
    symbol: str
    formation_time_ms: int
    eligible: bool
    reasons: tuple[str, ...]
    provenance_sha256: str


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or SHA256_PATTERN.fullmatch(value) is None:
        raise AlphaContractError(f"{label} must be a lower-case SHA-256")
    return value


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise AlphaContractError(f"{label} must be numeric, not bool")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AlphaContractError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise AlphaContractError(f"{label} must be finite")
    return result


def _provenance(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(repr(part).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def require_pit_eligibility(
    snapshot: object,
    *,
    formation_time_ms: int,
    symbols: Iterable[str],
) -> dict[str, EligibilityDecision]:
    """Derive eligibility from complete component evidence.

    Callers cannot provide an ELIGIBLE bit.  ArchiveAvailability, bool, dict,
    strings, missing membership, and archive-derived evidence fail closed.
    """

    if type(snapshot) is not PITEligibilitySnapshot:
        raise PITGateError(
            "PITEligibilitySnapshot is required; archive availability and raw "
            "containers are not eligibility evidence"
        )
    if snapshot.semantics != PIT_SEMANTICS:
        raise PITGateError(f"invalid PIT semantics {snapshot.semantics!r}")
    if type(snapshot.expected_venue) is not Venue or snapshot.expected_venue is not Venue.BINANCE:
        raise PITGateError("expected venue must be Venue.BINANCE")
    if (
        type(snapshot.expected_market_type) is not MarketType
        or snapshot.expected_market_type is not MarketType.SPOT
    ):
        raise PITGateError("expected market type must be MarketType.SPOT")
    if not snapshot.expected_quote_asset:
        raise PITGateError("expected quote asset must be explicit")
    if snapshot.formation_time_ms != formation_time_ms:
        raise PITGateError("PIT snapshot formation time mismatch")
    _require_sha256(snapshot.artifact_sha256, "PIT snapshot artifact_sha256")

    index: dict[str, PITEligibilityEvidence] = {}
    for membership in snapshot.memberships:
        if type(membership) is not PITEligibilityEvidence:
            raise PITGateError("snapshot contains a non-PITEligibilityEvidence value")
        if not membership.symbol or membership.symbol in index:
            raise PITGateError(
                f"invalid or duplicate PIT membership {membership.symbol!r}"
            )
        if membership.formation_time_ms != formation_time_ms:
            raise PITGateError(
                f"membership formation mismatch for {membership.symbol!r}"
            )
        index[membership.symbol] = membership

    requested = tuple(symbols)
    if len(set(requested)) != len(requested):
        raise PITGateError("requested symbols contain duplicates")
    missing = sorted(set(requested) - set(index))
    if missing:
        raise PITGateError(
            "missing explicit PIT membership for: " + ", ".join(missing)
        )

    required_kinds = {
        EvidenceKind.VENUE_MARKET_STATUS,
        EvidenceKind.SPOT_PERMISSION,
        EvidenceKind.QUOTE_ASSET_RULE,
        EvidenceKind.LISTING_WINDOW,
    }
    decisions: dict[str, EligibilityDecision] = {}
    for symbol in requested:
        item = index[symbol]
        reasons: list[str] = []
        kinds: set[EvidenceKind] = set()
        hashes: list[str] = []
        for reference in item.evidence:
            if type(reference) is not EvidenceReference or type(reference.kind) is not EvidenceKind:
                raise PITGateError(f"invalid evidence reference for {symbol!r}")
            if reference.kind is EvidenceKind.ARCHIVE_DERIVED:
                raise PITGateError(
                    f"archive-derived evidence is forbidden for {symbol!r}"
                )
            if reference.kind in kinds:
                raise PITGateError(
                    f"duplicate evidence kind {reference.kind.value!r} for {symbol!r}"
                )
            if reference.known_at_ms > formation_time_ms:
                reasons.append(f"{reference.kind.value}_KNOWN_AFTER_FORMATION")
            _require_sha256(reference.sha256, f"evidence SHA-256 for {symbol!r}")
            kinds.add(reference.kind)
            hashes.append(reference.sha256)
        for missing_kind in sorted(required_kinds - kinds, key=lambda value: value.value):
            reasons.append(f"MISSING_{missing_kind.value}")
        if type(item.venue) is not Venue or item.venue is not snapshot.expected_venue:
            reasons.append("WRONG_VENUE")
        if (
            type(item.market_type) is not MarketType
            or item.market_type is not snapshot.expected_market_type
        ):
            reasons.append("WRONG_MARKET_TYPE")
        if type(item.trading_status) is not TradingStatus:
            raise PITGateError(f"invalid trading status type for {symbol!r}")
        if item.trading_status is not TradingStatus.TRADING:
            reasons.append(
                "UNKNOWN_TRADING_STATUS"
                if item.trading_status is TradingStatus.UNKNOWN
                else "NOT_TRADING"
            )
        if type(item.spot_permission) is not PermissionState:
            raise PITGateError(f"invalid spot permission type for {symbol!r}")
        if item.spot_permission is not PermissionState.ENABLED:
            reasons.append(
                "UNKNOWN_SPOT_PERMISSION"
                if item.spot_permission is PermissionState.UNKNOWN
                else "SPOT_PERMISSION_DISABLED"
            )
        if item.quote_asset is None:
            reasons.append("UNKNOWN_QUOTE_ASSET")
        elif item.quote_asset != snapshot.expected_quote_asset:
            reasons.append("WRONG_QUOTE_ASSET")
        if item.listing is None:
            reasons.append("UNKNOWN_LISTING_WINDOW")
        elif type(item.listing) is not ListingWindow:
            raise PITGateError(f"invalid listing window for {symbol!r}")
        else:
            if item.listing.effective_to_ms_exclusive is not None and (
                item.listing.effective_to_ms_exclusive
                <= item.listing.effective_from_ms
            ):
                raise PITGateError(f"invalid listing interval for {symbol!r}")
            if formation_time_ms < item.listing.effective_from_ms:
                reasons.append("LISTING_NOT_YET_EFFECTIVE")
            if (
                item.listing.effective_to_ms_exclusive is not None
                and formation_time_ms >= item.listing.effective_to_ms_exclusive
            ):
                reasons.append("LISTING_NO_LONGER_EFFECTIVE")
        unique_reasons = tuple(sorted(set(reasons)))
        decisions[symbol] = EligibilityDecision(
            symbol=symbol,
            formation_time_ms=formation_time_ms,
            eligible=not unique_reasons,
            reasons=unique_reasons,
            provenance_sha256=_provenance(
                snapshot.artifact_sha256,
                symbol,
                unique_reasons,
                tuple(sorted(hashes)),
            ),
        )
    return decisions


@dataclass(frozen=True, order=True)
class ExpertKey:
    family: str
    name: str
    horizon_hours: int
    version: str


def _validate_expert_key(
    key: object,
    label: str,
    error_type: type[AlphaContractError],
) -> ExpertKey:
    if type(key) is not ExpertKey:
        raise error_type(f"{label} must be an exact ExpertKey")
    if not all(
        type(value) is str and bool(value)
        for value in (key.family, key.name, key.version)
    ):
        raise error_type(f"{label} string fields must be non-empty")
    if (
        type(key.horizon_hours) is not int
        or key.horizon_hours not in ALPHA_HORIZONS_HOURS
    ):
        raise error_type(f"{label} has unsupported horizon {key.horizon_hours!r}")
    return key


@dataclass(frozen=True)
class ExpertSpec:
    key: ExpertKey
    required_inputs: tuple[str, ...]
    clock_rule: str
    output_direction: str
    readiness: ExpertReadiness
    provenance_sha256: str


@dataclass(frozen=True)
class ExpertRegistry:
    specs: tuple[ExpertSpec, ...]

    def __post_init__(self) -> None:
        indexed: dict[ExpertKey, ExpertSpec] = {}
        for spec in self.specs:
            if type(spec) is not ExpertSpec:
                raise ExpertRegistryError("registry accepts only ExpertSpec values")
            key = _validate_expert_key(
                spec.key, "registry expert key", ExpertRegistryError
            )
            if key in indexed:
                raise ExpertRegistryError(f"duplicate expert key {key!r}")
            if (
                type(spec.required_inputs) is not tuple
                or not spec.required_inputs
                or any(
                    type(value) is not str or not value
                    for value in spec.required_inputs
                )
                or len(set(spec.required_inputs)) != len(spec.required_inputs)
            ):
                raise ExpertRegistryError(f"invalid required inputs for {key!r}")
            if type(spec.clock_rule) is not str or not spec.clock_rule:
                raise ExpertRegistryError(f"missing clock rule for {key!r}")
            if spec.output_direction != "HIGHER_IS_BETTER":
                raise ExpertRegistryError(
                    "experts must be standardized upstream to HIGHER_IS_BETTER"
                )
            if type(spec.readiness) is not ExpertReadiness:
                raise ExpertRegistryError(f"invalid readiness for {key!r}")
            _require_sha256(spec.provenance_sha256, f"expert spec provenance {key!r}")
            indexed[key] = spec
        if not indexed:
            raise ExpertRegistryError("registry must not be empty")
        object.__setattr__(
            self, "specs", tuple(indexed[key] for key in sorted(indexed))
        )

    @classmethod
    def build(cls, specs: Iterable[ExpertSpec]) -> "ExpertRegistry":
        return cls(tuple(specs))

    @property
    def keys(self) -> tuple[ExpertKey, ...]:
        return tuple(spec.key for spec in self.specs)

    def get(self, key: ExpertKey) -> ExpertSpec:
        for spec in self.specs:
            if spec.key == key:
                return spec
        raise ExpertRegistryError(f"unregistered expert {key!r}")


def modern_crypto_v1_readiness_catalog() -> ExpertRegistry:
    definitions = (
        ("Price", "residual_momentum", ("spot_close", "benchmark_close"), ExpertReadiness.PIT_BLOCKED),
        ("Liquidity", "quote_volume_capacity", ("quote_volume",), ExpertReadiness.PIT_BLOCKED),
        ("Flow", "taker_flow_imbalance", ("taker_buy_volume", "volume"), ExpertReadiness.PIT_BLOCKED),
        ("Risk", "realized_downside_risk", ("spot_close",), ExpertReadiness.PIT_BLOCKED),
        ("RelativeValueResidual", "cross_asset_residual", ("spot_close", "benchmark_close"), ExpertReadiness.PIT_BLOCKED),
        ("Structural", "supply_schedule", ("point_in_time_supply",), ExpertReadiness.DATA_BLOCKED),
        ("Derivatives", "funding_oi_basis", ("funding", "open_interest", "basis"), ExpertReadiness.DATA_BLOCKED),
        ("Microstructure", "orderbook_pressure", ("historical_orderbook",), ExpertReadiness.DATA_BLOCKED),
        ("EventNLP", "timestamped_event_signal", ("point_in_time_events",), ExpertReadiness.DATA_BLOCKED),
    )
    return ExpertRegistry.build(
        ExpertSpec(
            key=ExpertKey(family, name, 24, "v1"),
            required_inputs=inputs,
            clock_rule="known_at <= formation_time",
            output_direction="HIGHER_IS_BETTER",
            readiness=readiness,
            provenance_sha256=_provenance("modern_crypto_v1", family, name),
        )
        for family, name, inputs, readiness in definitions
    )


@dataclass(frozen=True)
class CrossSectionValue:
    symbol: str
    formation_time_ms: int
    known_at_ms: int
    value: float
    provenance_sha256: str


def _eligible_values(
    values: Sequence[CrossSectionValue],
    snapshot: object,
    *,
    min_breadth: int,
) -> tuple[CrossSectionValue, ...]:
    if min_breadth < 2 or not values:
        raise CrossSectionError("cross section requires min_breadth >= 2 and non-empty values")
    if any(type(value) is not CrossSectionValue for value in values):
        raise CrossSectionError("cross section accepts only CrossSectionValue")
    formations = {value.formation_time_ms for value in values}
    if len(formations) != 1:
        raise CrossSectionError("mixed formation times are forbidden")
    formation = next(iter(formations))
    symbols = [value.symbol for value in values]
    if len(set(symbols)) != len(symbols):
        raise CrossSectionError("duplicate symbol-time key")
    decisions = require_pit_eligibility(
        snapshot, formation_time_ms=formation, symbols=symbols
    )
    eligible: list[CrossSectionValue] = []
    for value in values:
        if value.known_at_ms > formation:
            raise CrossSectionError(f"value for {value.symbol!r} has a future clock")
        _require_sha256(value.provenance_sha256, f"value provenance {value.symbol!r}")
        if not decisions[value.symbol].eligible:
            continue
        _finite(value.value, f"value for {value.symbol!r}")
        eligible.append(value)
    if len(eligible) < min_breadth:
        raise CrossSectionError(
            f"eligible breadth {len(eligible)} is below minimum {min_breadth}"
        )
    return tuple(sorted(eligible, key=lambda value: value.symbol))


def _linear_quantile(values: Sequence[float], quantile: float) -> float:
    position = quantile * (len(values) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction


def winsorize_cross_section(
    values: Sequence[CrossSectionValue],
    snapshot: object,
    *,
    lower_quantile: float,
    upper_quantile: float,
    min_breadth: int = 3,
) -> tuple[CrossSectionValue, ...]:
    if not 0 <= lower_quantile < upper_quantile <= 1:
        raise CrossSectionError("winsor quantiles must satisfy 0 <= lower < upper <= 1")
    eligible = _eligible_values(values, snapshot, min_breadth=min_breadth)
    ordered = sorted(float(value.value) for value in eligible)
    lower = _linear_quantile(ordered, lower_quantile)
    upper = _linear_quantile(ordered, upper_quantile)
    known_at = max(value.known_at_ms for value in eligible)
    return tuple(
        CrossSectionValue(
            value.symbol,
            value.formation_time_ms,
            known_at,
            min(max(float(value.value), lower), upper),
            _provenance("winsor", value.provenance_sha256, lower, upper),
        )
        for value in eligible
    )


def _centered_midranks(values: Sequence[float], label: str) -> list[float]:
    if len(values) < 2:
        raise CrossSectionError(f"{label} requires at least two values")
    if len(set(values)) == 1:
        raise CrossSectionError(f"all {label} values are tied")
    ordered = sorted(enumerate(values), key=lambda pair: (pair[1], pair[0]))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        average_rank = ((cursor + 1) + end) / 2
        centered = 2 * (average_rank - (len(values) + 1) / 2) / (len(values) - 1)
        for offset in range(cursor, end):
            ranks[ordered[offset][0]] = centered
        cursor = end
    if abs(sum(ranks)) > 1e-12:
        raise CrossSectionError(f"{label} centered ranks do not sum to zero")
    return ranks


def rank_cross_section(
    values: Sequence[CrossSectionValue],
    snapshot: object,
    *,
    min_breadth: int = 3,
) -> tuple[CrossSectionValue, ...]:
    eligible = _eligible_values(values, snapshot, min_breadth=min_breadth)
    ranks = _centered_midranks(
        [float(value.value) for value in eligible], "cross-sectional"
    )
    known_at = max(value.known_at_ms for value in eligible)
    return tuple(
        CrossSectionValue(
            value.symbol,
            value.formation_time_ms,
            known_at,
            ranks[index],
            _provenance("rank", value.provenance_sha256, ranks[index]),
        )
        for index, value in enumerate(eligible)
    )


def residualize_cross_section(
    targets: Sequence[CrossSectionValue],
    exposures: Mapping[str, Sequence[CrossSectionValue]],
    snapshot: object,
    *,
    include_intercept: bool,
    min_breadth: int = 3,
    orthogonality_tolerance: float = ORTHOGONALITY_TOLERANCE,
) -> tuple[CrossSectionValue, ...]:
    if not exposures:
        raise CrossSectionError("at least one exposure is required")
    if orthogonality_tolerance <= 0 or not math.isfinite(orthogonality_tolerance):
        raise CrossSectionError("orthogonality tolerance must be finite and positive")
    target_slice = _eligible_values(targets, snapshot, min_breadth=min_breadth)
    symbols = tuple(value.symbol for value in target_slice)
    exposure_slices: dict[str, tuple[CrossSectionValue, ...]] = {}
    for name in sorted(exposures):
        values = _eligible_values(exposures[name], snapshot, min_breadth=min_breadth)
        if tuple(value.symbol for value in values) != symbols:
            raise CrossSectionError(f"exposure {name!r} has incomplete key alignment")
        exposure_slices[name] = values
    columns = len(exposure_slices) + int(include_intercept)
    if columns == 0 or len(target_slice) <= columns:
        raise CrossSectionError("insufficient breadth for residualization")
    matrix: list[list[float]] = []
    target_vector: list[float] = []
    for row, target in enumerate(target_slice):
        design = [1.0] if include_intercept else []
        design.extend(
            float(exposure_slices[name][row].value)
            for name in sorted(exposure_slices)
        )
        matrix.append(design)
        target_vector.append(float(target.value))
    design_array = numpy.asarray(matrix, dtype=float)
    target_array = numpy.asarray(target_vector, dtype=float)
    coefficients, _squares, rank, _singular = lstsq(design_array, target_array)
    if int(rank) != columns:
        raise CrossSectionError(
            f"residual design rank deficient: rank {rank}, columns {columns}"
        )
    residuals = [
        target_vector[row]
        - sum(matrix[row][column] * float(coefficients[column]) for column in range(columns))
        for row in range(len(target_vector))
    ]
    scale = max(1.0, sum(abs(value) for value in target_vector))
    for column in range(columns):
        dot = sum(matrix[row][column] * residuals[row] for row in range(len(residuals)))
        if abs(dot) > orthogonality_tolerance * scale:
            raise CrossSectionError(f"residual orthogonality failed for column {column}")
    known_at = max(
        [value.known_at_ms for value in target_slice]
        + [value.known_at_ms for values in exposure_slices.values() for value in values]
    )
    return tuple(
        CrossSectionValue(
            target.symbol,
            target.formation_time_ms,
            known_at,
            residuals[index],
            _provenance(
                "residual",
                target.provenance_sha256,
                tuple(exposure_slices[name][index].provenance_sha256 for name in sorted(exposure_slices)),
            ),
        )
        for index, target in enumerate(target_slice)
    )


@dataclass(frozen=True)
class FormationPoint:
    symbol: str
    feature_bar_index: int
    feature_bar_open_time_ms: int
    decision_time_ms: int
    known_at_ms: int
    provenance_sha256: str


@dataclass(frozen=True)
class OpenPrice:
    symbol: str
    bar_index: int
    open_time_ms: int
    known_at_ms: int
    value: float
    provenance_sha256: str


@dataclass(frozen=True)
class ForwardLabel:
    symbol: str
    feature_bar_index: int
    feature_bar_open_time_ms: int
    decision_time_ms: int
    horizon_hours: int
    entry_bar_index: int
    entry_time_ms: int
    exit_bar_index: int
    exit_time_ms: int
    known_at_ms: int
    value: float
    provenance_sha256: str


def _validate_horizons(horizons: Iterable[int]) -> tuple[int, ...]:
    values = tuple(horizons)
    if not values:
        raise LabelContractError("horizons must be non-empty and unique")
    if any(type(value) is not int for value in values):
        raise LabelContractError("horizons must contain exact integer hours")
    if len(set(values)) != len(values):
        raise LabelContractError("horizons must be non-empty and unique")
    if any(value not in ALPHA_HORIZONS_HOURS for value in values):
        raise LabelContractError(f"horizons must be drawn from {ALPHA_HORIZONS_HOURS}")
    return tuple(sorted(values))


def _validate_formation(formation: FormationPoint) -> None:
    if type(formation) is not FormationPoint:
        raise LabelContractError("FormationPoint is required")
    if formation.decision_time_ms != formation.feature_bar_open_time_ms + HOUR_MILLISECONDS:
        raise LabelContractError("decision time must equal feature bar open plus one hour")
    if formation.known_at_ms != formation.decision_time_ms:
        raise LabelContractError("closed-bar feature must be known exactly at decision time")
    _require_sha256(formation.provenance_sha256, "formation provenance")


def _validate_label(label: ForwardLabel) -> None:
    if type(label) is not ForwardLabel:
        raise LabelContractError("ForwardLabel is required")
    if (
        type(label.horizon_hours) is not int
        or label.horizon_hours not in ALPHA_HORIZONS_HOURS
    ):
        raise LabelContractError("invalid label horizon")
    if label.decision_time_ms != label.feature_bar_open_time_ms + HOUR_MILLISECONDS:
        raise LabelContractError("label decision clock is invalid")
    if label.entry_bar_index != label.feature_bar_index + 1:
        raise LabelContractError("label entry must be bar k+1")
    if label.entry_time_ms != label.decision_time_ms:
        raise LabelContractError("label entry open must equal decision time")
    if label.exit_bar_index != label.feature_bar_index + label.horizon_hours + 1:
        raise LabelContractError("label exit must be bar k+h+1")
    if label.exit_time_ms != label.decision_time_ms + label.horizon_hours * HOUR_MILLISECONDS:
        raise LabelContractError("label exit clock is invalid")
    if label.known_at_ms != label.exit_time_ms:
        raise LabelContractError("label must become known exactly at exit open")
    _finite(label.value, "forward label")
    _require_sha256(label.provenance_sha256, "label provenance")


def build_next_open_labels(
    formations: Sequence[FormationPoint],
    open_prices: Sequence[OpenPrice],
    *,
    horizons_hours: Iterable[int] = ALPHA_HORIZONS_HOURS,
) -> tuple[ForwardLabel, ...]:
    horizons = _validate_horizons(horizons_hours)
    prices: dict[tuple[str, int], OpenPrice] = {}
    for price in open_prices:
        if type(price) is not OpenPrice:
            raise LabelContractError("OpenPrice is required")
        identity = (price.symbol, price.bar_index)
        if identity in prices:
            raise LabelContractError(f"duplicate open price {identity!r}")
        if price.known_at_ms != price.open_time_ms:
            raise LabelContractError("synthetic open price must be known exactly at open time")
        if _finite(price.value, f"open price {identity!r}") <= 0:
            raise LabelContractError("open prices must be positive")
        _require_sha256(price.provenance_sha256, f"open price provenance {identity!r}")
        prices[identity] = price
    seen: set[tuple[str, int]] = set()
    labels: list[ForwardLabel] = []
    for formation in formations:
        _validate_formation(formation)
        identity = (formation.symbol, formation.feature_bar_index)
        if identity in seen:
            raise LabelContractError(f"duplicate formation {identity!r}")
        seen.add(identity)
        for horizon in horizons:
            entry_key = (formation.symbol, formation.feature_bar_index + 1)
            exit_key = (
                formation.symbol,
                formation.feature_bar_index + horizon + 1,
            )
            if entry_key not in prices or exit_key not in prices:
                raise LabelContractError(
                    f"missing k+1 or k+h+1 open for {identity!r}, horizon {horizon}"
                )
            entry, exit_price = prices[entry_key], prices[exit_key]
            if entry.open_time_ms != formation.decision_time_ms:
                raise LabelContractError("entry k+1 open must equal decision time")
            if exit_price.open_time_ms != formation.decision_time_ms + horizon * HOUR_MILLISECONDS:
                raise LabelContractError("exit k+h+1 open time is invalid")
            label = ForwardLabel(
                symbol=formation.symbol,
                feature_bar_index=formation.feature_bar_index,
                feature_bar_open_time_ms=formation.feature_bar_open_time_ms,
                decision_time_ms=formation.decision_time_ms,
                horizon_hours=horizon,
                entry_bar_index=entry.bar_index,
                entry_time_ms=entry.open_time_ms,
                exit_bar_index=exit_price.bar_index,
                exit_time_ms=exit_price.open_time_ms,
                known_at_ms=exit_price.open_time_ms,
                value=float(exit_price.value) / float(entry.value) - 1,
                provenance_sha256=_provenance(
                    "label",
                    formation.provenance_sha256,
                    entry.provenance_sha256,
                    exit_price.provenance_sha256,
                    horizon,
                ),
            )
            _validate_label(label)
            labels.append(label)
    return tuple(
        sorted(
            labels,
            key=lambda value: (
                value.decision_time_ms,
                value.symbol,
                value.horizon_hours,
            ),
        )
    )


@dataclass(frozen=True)
class LabelInterval:
    symbol: str
    horizon_hours: int
    start_utc_ms: int
    end_utc_ms_exclusive: int

    @classmethod
    def from_label(cls, label: ForwardLabel) -> "LabelInterval":
        _validate_label(label)
        return cls(
            label.symbol,
            label.horizon_hours,
            label.entry_time_ms,
            label.exit_time_ms + 1,
        )

    def overlaps(self, other: "LabelInterval") -> bool:
        return (
            self.start_utc_ms < other.end_utc_ms_exclusive
            and other.start_utc_ms < self.end_utc_ms_exclusive
        )


@dataclass(frozen=True)
class PurgeEmbargoSpec:
    horizons_hours: tuple[int, ...]
    purge_bars: int
    embargo_bars: int
    bar_milliseconds: int = HOUR_MILLISECONDS

    def __post_init__(self) -> None:
        horizons = _validate_horizons(self.horizons_hours)
        if type(self.bar_milliseconds) is not int or (
            self.bar_milliseconds != HOUR_MILLISECONDS
        ):
            raise LabelContractError(
                "bar_milliseconds must be the positive one-hour contract"
            )
        if type(self.purge_bars) is not int or type(self.embargo_bars) is not int:
            raise LabelContractError("purge and embargo bars must be integers")
        required = max(horizons) + 1
        if self.purge_bars < required or self.embargo_bars < required:
            raise LabelContractError(
                f"purge and embargo must each be at least {required} bars"
            )
        object.__setattr__(self, "horizons_hours", horizons)

    @classmethod
    def build(
        cls,
        horizons_hours: Iterable[int],
        *,
        purge_bars: int,
        embargo_bars: int,
    ) -> "PurgeEmbargoSpec":
        return cls(tuple(horizons_hours), purge_bars, embargo_bars)

    @property
    def required_shared_bars(self) -> int:
        return max(self.horizons_hours) + 1

    @property
    def purge_milliseconds(self) -> int:
        return self.purge_bars * self.bar_milliseconds

    @property
    def embargo_milliseconds(self) -> int:
        return self.embargo_bars * self.bar_milliseconds


@dataclass(frozen=True)
class SplitLabel:
    label: ForwardLabel
    role: SplitRole


def validate_purged_embargo_split(
    assignments: Sequence[SplitLabel],
    spec: PurgeEmbargoSpec,
    *,
    evaluation_start_ms: int,
    evaluation_end_ms_exclusive: int,
) -> None:
    """Validate actual UTC label intervals and both sides of a split."""

    if type(spec) is not PurgeEmbargoSpec:
        raise LabelContractError("PurgeEmbargoSpec is required")
    if evaluation_end_ms_exclusive <= evaluation_start_ms:
        raise LabelContractError("evaluation interval must be positive")
    if not assignments:
        raise LabelContractError("split assignments must not be empty")
    roles_by_formation: dict[int, SplitRole] = {}
    seen: set[tuple[SplitRole, str, int, int]] = set()
    train: list[ForwardLabel] = []
    evaluation: list[ForwardLabel] = []
    for assignment in assignments:
        if type(assignment) is not SplitLabel or type(assignment.role) is not SplitRole:
            raise LabelContractError("invalid split assignment")
        label = assignment.label
        _validate_label(label)
        if label.horizon_hours not in spec.horizons_hours:
            raise LabelContractError("label horizon is absent from purge specification")
        identity = (
            assignment.role,
            label.symbol,
            label.decision_time_ms,
            label.horizon_hours,
        )
        if identity in seen:
            raise LabelContractError(f"duplicate split label {identity!r}")
        seen.add(identity)
        prior_role = roles_by_formation.setdefault(label.decision_time_ms, assignment.role)
        if prior_role is not assignment.role:
            raise LabelContractError(
                "all symbols and horizons at one formation must share a fold"
            )
        if assignment.role is SplitRole.EVALUATION:
            if not evaluation_start_ms <= label.decision_time_ms < evaluation_end_ms_exclusive:
                raise LabelContractError("evaluation label formation lies outside evaluation interval")
            evaluation.append(label)
        else:
            if evaluation_start_ms <= label.decision_time_ms < evaluation_end_ms_exclusive:
                raise LabelContractError("training formation lies inside evaluation interval")
            train.append(label)
    if not train or not evaluation:
        raise LabelContractError("split must contain both train and evaluation labels")
    evaluation_intervals = [LabelInterval.from_label(label) for label in evaluation]
    for training_label in train:
        interval = LabelInterval.from_label(training_label)
        for evaluation_interval in evaluation_intervals:
            if interval.overlaps(evaluation_interval):
                raise LabelContractError("actual train and evaluation label intervals overlap")
        if training_label.decision_time_ms < evaluation_start_ms:
            latest_allowed_end = evaluation_start_ms - spec.purge_milliseconds
            if interval.end_utc_ms_exclusive > latest_allowed_end:
                raise LabelContractError("pre-evaluation label violates purge boundary")
        else:
            earliest_allowed_start = (
                evaluation_end_ms_exclusive + spec.embargo_milliseconds
            )
            if interval.start_utc_ms < earliest_allowed_start:
                raise LabelContractError("post-evaluation label violates embargo boundary")


@dataclass(frozen=True)
class ExpertOutput:
    key: ExpertKey
    symbol: str
    formation_time_ms: int
    known_at_ms: int
    value: float
    provenance_sha256: str


@dataclass(frozen=True)
class FamilyWeights:
    family: str
    expert_weights: tuple[tuple[ExpertKey, float], ...]


@dataclass(frozen=True)
class HierarchicalWeights:
    families: tuple[FamilyWeights, ...]
    family_weights: tuple[tuple[str, float], ...]
    simplex_tolerance: float = SIMPLEX_TOLERANCE


@dataclass(frozen=True)
class EnsembleScore:
    symbol: str
    formation_time_ms: int
    known_at_ms: int
    horizon_hours: int
    value: float
    provenance_sha256: str


def _validate_ensemble_score(score: object) -> EnsembleScore:
    if type(score) is not EnsembleScore:
        raise EnsembleContractError("EnsembleScore is required")
    if type(score.symbol) is not str or not score.symbol:
        raise EnsembleContractError("ensemble score symbol is required")
    if type(score.formation_time_ms) is not int or type(score.known_at_ms) is not int:
        raise EnsembleContractError("ensemble clocks must be exact integer milliseconds")
    if score.known_at_ms > score.formation_time_ms:
        raise EnsembleContractError("ensemble score has a future clock")
    if (
        type(score.horizon_hours) is not int
        or score.horizon_hours not in ALPHA_HORIZONS_HOURS
    ):
        raise EnsembleContractError("ensemble score has an invalid exact horizon")
    _finite(score.value, "ensemble score")
    _require_sha256(score.provenance_sha256, "ensemble provenance")
    return score


def _simplex(
    pairs: Sequence[tuple[object, float]], label: str, tolerance: float
) -> dict[object, float]:
    if not pairs:
        raise EnsembleContractError(f"{label} simplex must not be empty")
    result: dict[object, float] = {}
    for key, raw_weight in pairs:
        if key in result:
            raise EnsembleContractError(f"duplicate {label} weight key {key!r}")
        weight = _finite(raw_weight, f"{label} weight")
        if weight < 0:
            raise EnsembleContractError(f"{label} weights must be nonnegative")
        result[key] = weight
    if abs(sum(result.values()) - 1) > tolerance:
        raise EnsembleContractError(f"{label} weights must sum to one")
    return result


def combine_hierarchical(
    outputs: Sequence[ExpertOutput],
    registry: ExpertRegistry,
    weights: HierarchicalWeights,
) -> EnsembleScore:
    if type(registry) is not ExpertRegistry or type(weights) is not HierarchicalWeights:
        raise EnsembleContractError("registry and fixed HierarchicalWeights are required")
    if not outputs:
        raise EnsembleContractError("expert output map must not be empty")
    if any(spec.readiness is not ExpertReadiness.SYNTHETIC_READY for spec in registry.specs):
        raise EnsembleContractError("blocked experts cannot enter an exp006 ensemble")
    horizons = {spec.key.horizon_hours for spec in registry.specs}
    if len(horizons) != 1:
        raise EnsembleContractError("ensemble registry must contain exactly one horizon")
    horizon = next(iter(horizons))
    if weights.simplex_tolerance <= 0 or not math.isfinite(weights.simplex_tolerance):
        raise EnsembleContractError("invalid simplex tolerance")
    output_index: dict[ExpertKey, ExpertOutput] = {}
    identity: set[tuple[str, int]] = set()
    for output in outputs:
        if type(output) is not ExpertOutput:
            raise EnsembleContractError("ensemble accepts only ExpertOutput")
        output_key = _validate_expert_key(
            output.key, "expert output key", EnsembleContractError
        )
        if output_key in output_index:
            raise EnsembleContractError(f"duplicate expert output {output_key!r}")
        if output.known_at_ms > output.formation_time_ms:
            raise EnsembleContractError("expert output has a future clock")
        _finite(output.value, f"expert output {output.key!r}")
        _require_sha256(output.provenance_sha256, "expert output provenance")
        output_index[output_key] = output
        identity.add((output.symbol, output.formation_time_ms))
    if len(identity) != 1:
        raise EnsembleContractError("expert outputs must share symbol and formation")
    expected_keys = set(registry.keys)
    if set(output_index) != expected_keys:
        raise EnsembleContractError(
            f"full ExpertKey map required; missing={sorted(expected_keys-set(output_index))!r}, "
            f"extra={sorted(set(output_index)-expected_keys)!r}"
        )
    family_specs: dict[str, FamilyWeights] = {}
    validated_family_pairs: dict[str, tuple[tuple[ExpertKey, float], ...]] = {}
    for family in weights.families:
        if type(family) is not FamilyWeights or family.family in family_specs:
            raise EnsembleContractError("invalid or duplicate family weights")
        if type(family.family) is not str or not family.family:
            raise EnsembleContractError("family weight name must be a non-empty string")
        raw_pairs = family.expert_weights
        if type(raw_pairs) is not tuple:
            raise EnsembleContractError("expert weight pairs must be an exact tuple")
        validated_pairs: list[tuple[ExpertKey, float]] = []
        for pair in raw_pairs:
            if type(pair) is not tuple or len(pair) != 2:
                raise EnsembleContractError("each expert weight must be a key/weight tuple")
            key, raw_weight = pair
            validated_pairs.append(
                (
                    _validate_expert_key(
                        key, "expert weight key", EnsembleContractError
                    ),
                    raw_weight,
                )
            )
        family_specs[family.family] = family
        validated_family_pairs[family.family] = tuple(validated_pairs)
    registry_families = {key.family for key in expected_keys}
    across = _simplex(weights.family_weights, "across-family", weights.simplex_tolerance)
    if set(across) != registry_families or set(family_specs) != registry_families:
        raise EnsembleContractError("family maps must exactly cover registry families")
    family_values: dict[str, float] = {}
    for family in sorted(registry_families):
        within = _simplex(
            validated_family_pairs[family],
            f"within-family {family}",
            weights.simplex_tolerance,
        )
        family_keys = {key for key in expected_keys if key.family == family}
        if set(within) != family_keys:
            raise EnsembleContractError("within-family map must exactly cover experts")
        family_values[family] = sum(
            within[key] * float(output_index[key].value) for key in family_keys
        )
    value = sum(across[family] * family_values[family] for family in registry_families)
    symbol, formation = next(iter(identity))
    known_at = max(output.known_at_ms for output in outputs)
    provenance = _provenance(
        "horizon-ensemble-score-v1",
        horizon,
        tuple((key, output_index[key].provenance_sha256) for key in sorted(expected_keys)),
        weights,
    )
    return EnsembleScore(symbol, formation, known_at, horizon, value, provenance)


@dataclass(frozen=True)
class MultiHorizonEnsemble:
    symbol: str
    formation_time_ms: int
    known_at_ms: int
    scores: tuple[EnsembleScore, ...]
    provenance_sha256: str

    def __post_init__(self) -> None:
        if type(self.symbol) is not str or not self.symbol:
            raise EnsembleContractError("multi-horizon symbol is required")
        if type(self.formation_time_ms) is not int or type(self.known_at_ms) is not int:
            raise EnsembleContractError("multi-horizon clocks must be exact integers")
        if self.known_at_ms > self.formation_time_ms:
            raise EnsembleContractError("multi-horizon bundle has a future clock")
        if type(self.scores) is not tuple:
            raise EnsembleContractError("multi-horizon scores must be an exact tuple")
        if len(self.scores) != len(ALPHA_HORIZONS_HOURS):
            raise EnsembleContractError("multi-horizon bundle requires exactly four scores")
        validated = tuple(_validate_ensemble_score(score) for score in self.scores)
        horizons = tuple(score.horizon_hours for score in validated)
        if horizons != ALPHA_HORIZONS_HOURS:
            raise EnsembleContractError(
                "multi-horizon scores must be unique and canonically horizon-sorted"
            )
        if any(
            score.symbol != self.symbol
            or score.formation_time_ms != self.formation_time_ms
            for score in validated
        ):
            raise EnsembleContractError(
                "multi-horizon scores must share bundle symbol and formation"
            )
        expected_known_at = max(score.known_at_ms for score in validated)
        if self.known_at_ms != expected_known_at:
            raise EnsembleContractError(
                "multi-horizon known_at must equal the maximum score known_at"
            )
        _require_sha256(self.provenance_sha256, "multi-horizon provenance")
        expected_provenance = _multi_horizon_provenance(
            self.symbol,
            self.formation_time_ms,
            self.known_at_ms,
            validated,
        )
        if self.provenance_sha256 != expected_provenance:
            raise EnsembleContractError("multi-horizon provenance does not match content")


def _multi_horizon_provenance(
    symbol: str,
    formation_time_ms: int,
    known_at_ms: int,
    scores: tuple[EnsembleScore, ...],
) -> str:
    return _provenance(
        "multi-horizon-ensemble-v1",
        symbol,
        formation_time_ms,
        known_at_ms,
        tuple(
            (
                score.horizon_hours,
                float(score.value),
                score.known_at_ms,
                score.provenance_sha256,
            )
            for score in scores
        ),
    )


def compose_multi_horizon(
    scores: Sequence[EnsembleScore],
) -> MultiHorizonEnsemble:
    if not scores:
        raise EnsembleContractError("multi-horizon ensemble requires scores")
    validated = tuple(_validate_ensemble_score(score) for score in scores)
    symbols = {score.symbol for score in validated}
    formations = {score.formation_time_ms for score in validated}
    if len(symbols) != 1 or len(formations) != 1:
        raise EnsembleContractError(
            "multi-horizon scores must share symbol and formation"
        )
    by_horizon: dict[int, EnsembleScore] = {}
    for score in validated:
        if score.horizon_hours in by_horizon:
            raise EnsembleContractError("multi-horizon scores contain a duplicate horizon")
        by_horizon[score.horizon_hours] = score
    if tuple(sorted(by_horizon)) != ALPHA_HORIZONS_HOURS:
        raise EnsembleContractError(
            f"multi-horizon scores must cover exactly {ALPHA_HORIZONS_HOURS}"
        )
    sorted_scores = tuple(by_horizon[horizon] for horizon in ALPHA_HORIZONS_HOURS)
    symbol = next(iter(symbols))
    formation = next(iter(formations))
    known_at = max(score.known_at_ms for score in sorted_scores)
    provenance = _multi_horizon_provenance(
        symbol,
        formation,
        known_at,
        sorted_scores,
    )
    return MultiHorizonEnsemble(
        symbol, formation, known_at, sorted_scores, provenance
    )


@dataclass(frozen=True)
class RegimeMultiplier:
    formation_time_ms: int
    known_at_ms: int
    value: float
    evidence_sha256: str


@dataclass(frozen=True)
class RegimeAdjustedScore:
    symbol: str
    formation_time_ms: int
    known_at_ms: int
    horizon_hours: int
    ensemble_value: float
    multiplier: float
    adjusted_value: float
    provenance_sha256: str


def _validate_regime_adjusted_score(score: object) -> RegimeAdjustedScore:
    if type(score) is not RegimeAdjustedScore:
        raise AlphaContractError("RegimeAdjustedScore is required")
    if type(score.symbol) is not str or not score.symbol:
        raise AlphaContractError("regime-adjusted score symbol is required")
    if type(score.formation_time_ms) is not int or type(score.known_at_ms) is not int:
        raise AlphaContractError("regime-adjusted clocks must be integer milliseconds")
    if score.known_at_ms > score.formation_time_ms:
        raise AlphaContractError("regime-adjusted score has a future clock")
    if (
        type(score.horizon_hours) is not int
        or score.horizon_hours not in ALPHA_HORIZONS_HOURS
    ):
        raise AlphaContractError("regime-adjusted score has an invalid exact horizon")
    _require_sha256(score.provenance_sha256, "regime-adjusted provenance")
    ensemble = _finite(score.ensemble_value, "underlying ensemble score")
    multiplier = _finite(score.multiplier, "regime multiplier")
    adjusted = _finite(score.adjusted_value, "regime-adjusted expected gross alpha")
    if not 0.7 <= multiplier <= 1.3:
        raise AlphaContractError("regime multiplier must lie in [0.7, 1.3]")
    expected = ensemble * multiplier
    if not math.isclose(adjusted, expected, rel_tol=1e-12, abs_tol=1e-15):
        raise AlphaContractError("adjusted score does not equal ensemble times multiplier")
    if ensemble and math.copysign(1, ensemble) != math.copysign(1, adjusted):
        raise AlphaContractError("regime-adjusted score changed direction")
    if abs(adjusted) > abs(ensemble) * 1.3 + 1e-15:
        raise AlphaContractError("regime-adjusted score exceeded 1.3 amplification")
    return score


def apply_regime_multiplier(
    scores: Sequence[EnsembleScore], multiplier: object
) -> tuple[RegimeAdjustedScore, ...]:
    if type(multiplier) is not RegimeMultiplier:
        raise EnsembleContractError("a frozen RegimeMultiplier is required")
    if not scores or any(type(score) is not EnsembleScore for score in scores):
        raise EnsembleContractError("direct EnsembleScore values are required")
    formations = {score.formation_time_ms for score in scores}
    horizons = {score.horizon_hours for score in scores}
    symbols = [score.symbol for score in scores]
    if len(formations) != 1 or len(horizons) != 1 or len(set(symbols)) != len(symbols):
        raise EnsembleContractError(
            "one common multiplier requires one formation, one horizon, and unique symbols"
        )
    formation = next(iter(formations))
    horizon = next(iter(horizons))
    for score in scores:
        _validate_ensemble_score(score)
    if multiplier.formation_time_ms != formation or multiplier.known_at_ms > formation:
        raise EnsembleContractError("regime multiplier violates PIT clock")
    _require_sha256(multiplier.evidence_sha256, "regime evidence SHA-256")
    scalar = _finite(multiplier.value, "regime multiplier")
    if not 0.7 <= scalar <= 1.3:
        raise EnsembleContractError("regime multiplier must lie in [0.7, 1.3]")
    adjusted = []
    for score in scores:
        value = float(score.value)
        transformed = value * scalar
        if value and math.copysign(1, value) != math.copysign(1, transformed):
            raise EnsembleContractError("regime multiplier changed direction")
        if abs(transformed) > abs(value) * 1.3 + 1e-15:
            raise EnsembleContractError("regime multiplier exceeded 1.3 amplification")
        adjusted.append(
            RegimeAdjustedScore(
                score.symbol,
                formation,
                max(score.known_at_ms, multiplier.known_at_ms),
                horizon,
                value,
                scalar,
                transformed,
                _provenance(
                    "horizon-regime-adjusted-score-v1",
                    horizon,
                    score.provenance_sha256,
                    multiplier.evidence_sha256,
                    scalar,
                ),
            )
        )
    return tuple(sorted(adjusted, key=lambda value: value.symbol))


@dataclass(frozen=True)
class TradingCostPenalty:
    one_way_cost_rate: float
    turnover_per_leg: float
    legs: int

    @property
    def amount(self) -> float:
        rate = _finite(self.one_way_cost_rate, "one-way cost rate")
        turnover = _finite(self.turnover_per_leg, "turnover per leg")
        if rate < 0 or turnover < 0:
            raise AlphaContractError("cost rate and turnover must be nonnegative")
        if type(self.legs) is not int or self.legs <= 0:
            raise AlphaContractError(
                "legs must be explicit; one-way cost is never silently round trip"
            )
        return rate * turnover * self.legs


@dataclass(frozen=True)
class ExpectedNetAlpha:
    symbol: str
    formation_time_ms: int
    known_at_ms: int
    horizon_hours: int
    regime_adjusted_expected_gross_alpha: float
    trading_cost_penalty: float
    uncertainty_penalty: float
    crowding_penalty: float
    expected_net_alpha: float
    provenance_sha256: str


def _validate_expected_net_alpha(score: object) -> ExpectedNetAlpha:
    if type(score) is not ExpectedNetAlpha:
        raise AlphaContractError("ExpectedNetAlpha is required")
    if type(score.symbol) is not str or not score.symbol:
        raise AlphaContractError("expected-net-alpha symbol is required")
    if type(score.formation_time_ms) is not int or type(score.known_at_ms) is not int:
        raise AlphaContractError("expected-net-alpha clocks must be exact integers")
    if score.known_at_ms > score.formation_time_ms:
        raise AlphaContractError("expected-net-alpha has a future clock")
    if (
        type(score.horizon_hours) is not int
        or score.horizon_hours not in ALPHA_HORIZONS_HOURS
    ):
        raise AlphaContractError("expected-net-alpha has an invalid exact horizon")
    gross = _finite(
        score.regime_adjusted_expected_gross_alpha,
        "regime-adjusted expected gross alpha",
    )
    cost = _finite(score.trading_cost_penalty, "trading cost penalty")
    uncertainty = _finite(score.uncertainty_penalty, "uncertainty penalty")
    crowding = _finite(score.crowding_penalty, "crowding penalty")
    net = _finite(score.expected_net_alpha, "expected net alpha")
    if cost < 0 or uncertainty < 0 or crowding < 0:
        raise AlphaContractError("expected-net-alpha penalties must be nonnegative")
    expected = (((gross - cost) - uncertainty) - crowding)
    if net != expected:
        raise AlphaContractError("expected net alpha does not equal the frozen expression")
    _require_sha256(score.provenance_sha256, "expected-net-alpha provenance")
    return score


def compute_expected_net_alpha(
    score: RegimeAdjustedScore,
    *,
    trading_cost: TradingCostPenalty,
    uncertainty_penalty: float,
    crowding_penalty: float,
) -> ExpectedNetAlpha:
    score = _validate_regime_adjusted_score(score)
    gross = float(score.adjusted_value)
    if type(trading_cost) is not TradingCostPenalty:
        raise AlphaContractError("explicit TradingCostPenalty is required")
    cost = trading_cost.amount
    uncertainty = _finite(uncertainty_penalty, "uncertainty penalty")
    crowding = _finite(crowding_penalty, "crowding penalty")
    if uncertainty < 0 or crowding < 0:
        raise AlphaContractError("uncertainty and crowding penalties must be nonnegative")
    net = (((gross - cost) - uncertainty) - crowding)
    return ExpectedNetAlpha(
        score.symbol,
        score.formation_time_ms,
        score.known_at_ms,
        score.horizon_hours,
        gross,
        cost,
        uncertainty,
        crowding,
        net,
        _provenance(
            "horizon-expected-net-alpha-v1",
            score.horizon_hours,
            score.provenance_sha256,
            cost,
            uncertainty,
            crowding,
        ),
    )


@dataclass(frozen=True)
class RankICDiagnostic:
    formation_time_ms: int
    horizon_hours: int
    breadth: int
    rank_ic: float


@dataclass(frozen=True)
class DiagnosticScore:
    symbol: str
    formation_time_ms: int
    known_at_ms: int
    horizon_hours: int
    value: float
    provenance_sha256: str


def diagnostic_score_from_expected_net_alpha(
    score: object,
) -> DiagnosticScore:
    score = _validate_expected_net_alpha(score)
    return DiagnosticScore(
        symbol=score.symbol,
        formation_time_ms=score.formation_time_ms,
        known_at_ms=score.known_at_ms,
        horizon_hours=score.horizon_hours,
        value=float(score.expected_net_alpha),
        provenance_sha256=_provenance(
            "horizon-diagnostic-score-v1",
            score.horizon_hours,
            score.provenance_sha256,
            float(score.expected_net_alpha),
        ),
    )


def _eligible_diagnostic_scores(
    scores: Sequence[DiagnosticScore],
    snapshot: object,
    *,
    horizon_hours: int,
    min_breadth: int,
) -> tuple[DiagnosticScore, ...]:
    if type(horizon_hours) is not int or horizon_hours not in ALPHA_HORIZONS_HOURS:
        raise CrossSectionError("invalid IC horizon")
    if min_breadth < 2 or not scores:
        raise CrossSectionError("IC requires min_breadth >= 2 and non-empty scores")
    if any(type(score) is not DiagnosticScore for score in scores):
        raise CrossSectionError("IC accepts only horizon-bound DiagnosticScore values")
    formations = {score.formation_time_ms for score in scores}
    if len(formations) != 1:
        raise CrossSectionError("IC scores contain mixed formation times")
    formation = next(iter(formations))
    symbols = [score.symbol for score in scores]
    if len(set(symbols)) != len(symbols):
        raise CrossSectionError("duplicate IC score key")
    for score in scores:
        if type(score.symbol) is not str or not score.symbol:
            raise CrossSectionError("IC score symbol is required")
        if type(score.formation_time_ms) is not int or type(score.known_at_ms) is not int:
            raise CrossSectionError("IC score clocks must be integer milliseconds")
        if (
            type(score.horizon_hours) is not int
            or score.horizon_hours not in ALPHA_HORIZONS_HOURS
            or score.horizon_hours != horizon_hours
        ):
            raise CrossSectionError("IC scores contain the wrong horizon")
        if score.known_at_ms > formation:
            raise CrossSectionError("IC score has a future clock")
        _finite(score.value, f"IC score for {score.symbol!r}")
        _require_sha256(score.provenance_sha256, "IC score provenance")
    decisions = require_pit_eligibility(
        snapshot, formation_time_ms=formation, symbols=symbols
    )
    eligible = tuple(
        sorted(
            (score for score in scores if decisions[score.symbol].eligible),
            key=lambda score: score.symbol,
        )
    )
    if len(eligible) < min_breadth:
        raise CrossSectionError(
            f"eligible IC breadth {len(eligible)} is below minimum {min_breadth}"
        )
    return eligible


def rank_information_coefficient(
    scores: Sequence[DiagnosticScore],
    labels: Sequence[ForwardLabel],
    snapshot: object,
    *,
    horizon_hours: int,
    min_breadth: int = 3,
) -> RankICDiagnostic:
    eligible = _eligible_diagnostic_scores(
        scores,
        snapshot,
        horizon_hours=horizon_hours,
        min_breadth=min_breadth,
    )
    formation = eligible[0].formation_time_ms
    label_index: dict[str, ForwardLabel] = {}
    for label in labels:
        _validate_label(label)
        if label.horizon_hours != horizon_hours:
            raise CrossSectionError("IC labels contain the wrong horizon")
        if label.decision_time_ms != formation:
            raise CrossSectionError("IC labels contain the wrong formation time")
        if label.symbol in label_index:
            raise CrossSectionError("duplicate IC label key")
        label_index[label.symbol] = label
    expected_symbols = {score.symbol for score in eligible}
    if set(label_index) != expected_symbols:
        raise CrossSectionError("IC requires complete score-label key alignment")
    score_ranks = _centered_midranks(
        [float(score.value) for score in eligible], "IC score"
    )
    label_ranks = _centered_midranks(
        [float(label_index[score.symbol].value) for score in eligible], "IC label"
    )
    numerator = sum(left * right for left, right in zip(score_ranks, label_ranks))
    denominator = math.sqrt(
        sum(value * value for value in score_ranks)
        * sum(value * value for value in label_ranks)
    )
    if denominator == 0:
        raise CrossSectionError("IC rank variance is zero")
    return RankICDiagnostic(formation, horizon_hours, len(eligible), numerator / denominator)


@dataclass(frozen=True)
class IncrementalICDiagnostic:
    formation_time_ms: int
    horizon_hours: int
    breadth: int
    full_ic: float
    ablated_ic: float
    delta_ic: float


def incremental_information_diagnostic(
    full_scores: Sequence[DiagnosticScore],
    ablated_scores: Sequence[DiagnosticScore],
    labels: Sequence[ForwardLabel],
    snapshot: object,
    *,
    horizon_hours: int,
    min_breadth: int = 3,
) -> IncrementalICDiagnostic:
    full = rank_information_coefficient(
        full_scores,
        labels,
        snapshot,
        horizon_hours=horizon_hours,
        min_breadth=min_breadth,
    )
    ablated = rank_information_coefficient(
        ablated_scores,
        labels,
        snapshot,
        horizon_hours=horizon_hours,
        min_breadth=min_breadth,
    )
    full_keys = {(value.symbol, value.formation_time_ms) for value in full_scores}
    ablated_keys = {(value.symbol, value.formation_time_ms) for value in ablated_scores}
    if full_keys != ablated_keys:
        raise CrossSectionError("full and ablated score keys must align exactly")
    if full.formation_time_ms != ablated.formation_time_ms or full.breadth != ablated.breadth:
        raise CrossSectionError("full and ablated diagnostics must share formation and breadth")
    return IncrementalICDiagnostic(
        full.formation_time_ms,
        horizon_hours,
        full.breadth,
        full.rank_ic,
        ablated.rank_ic,
        full.rank_ic - ablated.rank_ic,
    )
