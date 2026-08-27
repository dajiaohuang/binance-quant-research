from __future__ import annotations

import math
import unittest
from dataclasses import fields, replace
from enum import IntEnum

from quant_research.hierarchical_alpha import (
    ALPHA_HORIZONS_HOURS,
    HOUR_MILLISECONDS,
    AlphaContractError,
    ArchiveAvailability,
    ArchiveState,
    CrossSectionError,
    CrossSectionValue,
    DiagnosticScore,
    EnsembleContractError,
    EnsembleScore,
    EvidenceKind,
    EvidenceReference,
    ExpertKey,
    ExpertOutput,
    ExpertReadiness,
    ExpertRegistry,
    ExpertRegistryError,
    ExpertSpec,
    FamilyWeights,
    FormationPoint,
    ForwardLabel,
    HierarchicalWeights,
    LabelContractError,
    LabelInterval,
    ListingWindow,
    MarketType,
    MultiHorizonEnsemble,
    OpenPrice,
    PITEligibilityEvidence,
    PITEligibilitySnapshot,
    PITGateError,
    PIT_SEMANTICS,
    PermissionState,
    PurgeEmbargoSpec,
    RegimeAdjustedScore,
    RegimeMultiplier,
    SplitLabel,
    SplitRole,
    TradingCostPenalty,
    TradingStatus,
    Venue,
    apply_regime_multiplier,
    compose_multi_horizon,
    build_next_open_labels,
    combine_hierarchical,
    compute_expected_net_alpha,
    diagnostic_score_from_expected_net_alpha,
    incremental_information_diagnostic,
    modern_crypto_v1_readiness_catalog,
    rank_cross_section,
    rank_information_coefficient,
    require_pit_eligibility,
    residualize_cross_section,
    validate_purged_embargo_split,
    winsorize_cross_section,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
BASE = 1_700_000_000_000
FORMATION = BASE + HOUR_MILLISECONDS


def evidence_references(*, known_at_ms: int = FORMATION):
    return tuple(
        EvidenceReference(kind, known_at_ms, sha)
        for kind, sha in (
            (EvidenceKind.VENUE_MARKET_STATUS, SHA_A),
            (EvidenceKind.SPOT_PERMISSION, SHA_B),
            (EvidenceKind.QUOTE_ASSET_RULE, SHA_C),
            (EvidenceKind.LISTING_WINDOW, SHA_D),
        )
    )


def membership(
    symbol: str,
    *,
    formation_time_ms: int = FORMATION,
    venue: Venue = Venue.BINANCE,
    market_type: MarketType = MarketType.SPOT,
    trading_status: TradingStatus = TradingStatus.TRADING,
    spot_permission: PermissionState = PermissionState.ENABLED,
    quote_asset: str | None = "USDT",
    listing: ListingWindow | None = None,
    evidence: tuple[EvidenceReference, ...] | None = None,
) -> PITEligibilityEvidence:
    return PITEligibilityEvidence(
        symbol=symbol,
        formation_time_ms=formation_time_ms,
        venue=venue,
        market_type=market_type,
        trading_status=trading_status,
        spot_permission=spot_permission,
        quote_asset=quote_asset,
        listing=listing
        if listing is not None
        else ListingWindow(formation_time_ms - 10 * HOUR_MILLISECONDS, None),
        evidence=evidence if evidence is not None else evidence_references(known_at_ms=formation_time_ms),
    )


def snapshot(
    symbols=("AAAUSDT", "BBBUSDT", "CCCUSDT"),
    *,
    formation_time_ms: int = FORMATION,
    overrides: dict[str, PITEligibilityEvidence] | None = None,
) -> PITEligibilitySnapshot:
    overrides = overrides or {}
    return PITEligibilitySnapshot(
        formation_time_ms=formation_time_ms,
        expected_venue=Venue.BINANCE,
        expected_market_type=MarketType.SPOT,
        expected_quote_asset="USDT",
        memberships=tuple(
            overrides.get(symbol, membership(symbol, formation_time_ms=formation_time_ms))
            for symbol in symbols
        ),
        artifact_sha256=SHA_E,
        semantics=PIT_SEMANTICS,
    )


def values(
    raw: dict[str, float], *, formation_time_ms: int = FORMATION
) -> tuple[CrossSectionValue, ...]:
    return tuple(
        CrossSectionValue(symbol, formation_time_ms, formation_time_ms, value, SHA_A)
        for symbol, value in raw.items()
    )


def diagnostic_values(
    raw: dict[str, float],
    *,
    formation_time_ms: int = FORMATION,
    horizon_hours: int = 24,
) -> tuple[DiagnosticScore, ...]:
    return tuple(
        DiagnosticScore(
            symbol,
            formation_time_ms,
            formation_time_ms,
            horizon_hours,
            value,
            SHA_C,
        )
        for symbol, value in raw.items()
    )


def point(symbol: str = "AAAUSDT", *, bar_index: int = 7) -> FormationPoint:
    return FormationPoint(
        symbol,
        bar_index,
        BASE,
        BASE + HOUR_MILLISECONDS,
        BASE + HOUR_MILLISECONDS,
        SHA_A,
    )


def forward_label(
    symbol: str,
    decision_time_ms: int,
    horizon_hours: int,
    value: float = 0.01,
    *,
    feature_bar_index: int = 7,
) -> ForwardLabel:
    return ForwardLabel(
        symbol=symbol,
        feature_bar_index=feature_bar_index,
        feature_bar_open_time_ms=decision_time_ms - HOUR_MILLISECONDS,
        decision_time_ms=decision_time_ms,
        horizon_hours=horizon_hours,
        entry_bar_index=feature_bar_index + 1,
        entry_time_ms=decision_time_ms,
        exit_bar_index=feature_bar_index + horizon_hours + 1,
        exit_time_ms=decision_time_ms + horizon_hours * HOUR_MILLISECONDS,
        known_at_ms=decision_time_ms + horizon_hours * HOUR_MILLISECONDS,
        value=value,
        provenance_sha256=SHA_B,
    )


def synthetic_registry(
    *,
    readiness: ExpertReadiness = ExpertReadiness.SYNTHETIC_READY,
    horizon_hours: int = 24,
):
    specs = (
        ExpertSpec(
            ExpertKey("Price", "p1", horizon_hours, "v1"),
            ("synthetic_price",),
            "known_at <= formation_time",
            "HIGHER_IS_BETTER",
            readiness,
            SHA_A,
        ),
        ExpertSpec(
            ExpertKey("Price", "p2", horizon_hours, "v1"),
            ("synthetic_price_2",),
            "known_at <= formation_time",
            "HIGHER_IS_BETTER",
            readiness,
            SHA_B,
        ),
        ExpertSpec(
            ExpertKey("Flow", "f1", horizon_hours, "v1"),
            ("synthetic_flow",),
            "known_at <= formation_time",
            "HIGHER_IS_BETTER",
            readiness,
            SHA_C,
        ),
    )
    return ExpertRegistry.build(specs)


def fixed_weights(registry: ExpertRegistry) -> HierarchicalWeights:
    by_family: dict[str, list[ExpertKey]] = {}
    for key in registry.keys:
        by_family.setdefault(key.family, []).append(key)
    families = []
    for family in sorted(by_family):
        keys = sorted(by_family[family])
        weight = 1.0 / len(keys)
        families.append(FamilyWeights(family, tuple((key, weight) for key in keys)))
    across_weight = 1.0 / len(families)
    return HierarchicalWeights(
        tuple(families),
        tuple((family.family, across_weight) for family in families),
    )


def outputs(registry: ExpertRegistry, *, formation_time_ms: int = FORMATION):
    return tuple(
        ExpertOutput(
            key,
            "AAAUSDT",
            formation_time_ms,
            formation_time_ms,
            float(index + 1),
            SHA_D,
        )
        for index, key in enumerate(registry.keys)
    )


class PITEligibilityTests(unittest.TestCase):
    def test_complete_component_evidence_derives_eligibility(self):
        decisions = require_pit_eligibility(
            snapshot(), formation_time_ms=FORMATION, symbols=("AAAUSDT",)
        )
        decision = decisions["AAAUSDT"]
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.reasons, ())
        self.assertEqual(len(decision.provenance_sha256), 64)

    def test_archive_bool_and_dict_are_not_eligibility(self):
        invalid = (
            ArchiveAvailability("AAAUSDT", FORMATION, ArchiveState.AVAILABLE, SHA_A),
            True,
            {"AAAUSDT": True},
        )
        for candidate in invalid:
            with self.subTest(candidate=type(candidate).__name__):
                with self.assertRaises(PITGateError):
                    require_pit_eligibility(
                        candidate, formation_time_ms=FORMATION, symbols=("AAAUSDT",)
                    )

    def test_missing_and_duplicate_membership_are_hard_errors(self):
        with self.assertRaises(PITGateError):
            require_pit_eligibility(
                snapshot(("AAAUSDT",)),
                formation_time_ms=FORMATION,
                symbols=("AAAUSDT", "BBBUSDT"),
            )
        duplicate = replace(
            snapshot(("AAAUSDT",)),
            memberships=(membership("AAAUSDT"), membership("AAAUSDT")),
        )
        with self.assertRaises(PITGateError):
            require_pit_eligibility(
                duplicate, formation_time_ms=FORMATION, symbols=("AAAUSDT",)
            )

    def test_unknown_wrong_market_status_permission_quote_and_listing_fail_closed(self):
        cases = (
            membership("AAAUSDT", trading_status=TradingStatus.UNKNOWN),
            membership("AAAUSDT", trading_status=TradingStatus.NOT_TRADING),
            membership("AAAUSDT", spot_permission=PermissionState.UNKNOWN),
            membership("AAAUSDT", spot_permission=PermissionState.DISABLED),
            membership("AAAUSDT", market_type=MarketType.FUTURES),
            membership("AAAUSDT", quote_asset="BTC"),
            membership(
                "AAAUSDT",
                listing=ListingWindow(FORMATION + HOUR_MILLISECONDS, None),
            ),
        )
        for item in cases:
            with self.subTest(item=item):
                decision = require_pit_eligibility(
                    snapshot(("AAAUSDT",), overrides={"AAAUSDT": item}),
                    formation_time_ms=FORMATION,
                    symbols=("AAAUSDT",),
                )["AAAUSDT"]
                self.assertFalse(decision.eligible)
                self.assertTrue(decision.reasons)

    def test_missing_or_future_component_evidence_fails_closed(self):
        missing = membership("AAAUSDT", evidence=evidence_references()[:-1])
        future_refs = list(evidence_references())
        future_refs[0] = replace(future_refs[0], known_at_ms=FORMATION + 1)
        future = membership("AAAUSDT", evidence=tuple(future_refs))
        for item in (missing, future):
            decision = require_pit_eligibility(
                snapshot(("AAAUSDT",), overrides={"AAAUSDT": item}),
                formation_time_ms=FORMATION,
                symbols=("AAAUSDT",),
            )["AAAUSDT"]
            self.assertFalse(decision.eligible)

    def test_archive_derived_evidence_is_a_hard_error(self):
        refs = list(evidence_references())
        refs[0] = EvidenceReference(EvidenceKind.ARCHIVE_DERIVED, FORMATION, SHA_A)
        item = membership("AAAUSDT", evidence=tuple(refs))
        with self.assertRaises(PITGateError):
            require_pit_eligibility(
                snapshot(("AAAUSDT",), overrides={"AAAUSDT": item}),
                formation_time_ms=FORMATION,
                symbols=("AAAUSDT",),
            )


class CrossSectionTests(unittest.TestCase):
    def test_centered_average_midranks_and_ties(self):
        unique = rank_cross_section(
            values({"AAAUSDT": 1, "BBBUSDT": 2, "CCCUSDT": 3}), snapshot()
        )
        self.assertEqual([row.value for row in unique], [-1.0, 0.0, 1.0])
        tied = rank_cross_section(
            values({"AAAUSDT": 1, "BBBUSDT": 1, "CCCUSDT": 3}), snapshot()
        )
        self.assertEqual([row.value for row in tied], [-0.5, -0.5, 1.0])
        self.assertAlmostEqual(sum(row.value for row in tied), 0.0)

    def test_future_append_uses_independent_single_time_slices(self):
        current_values = values({"AAAUSDT": 3, "BBBUSDT": 1, "CCCUSDT": 2})
        before = rank_cross_section(current_values, snapshot())
        future_time = FORMATION + 100 * HOUR_MILLISECONDS
        future_values = values(
            {"AAAUSDT": -100, "BBBUSDT": 999, "CCCUSDT": 0},
            formation_time_ms=future_time,
        )
        rank_cross_section(
            future_values,
            snapshot(formation_time_ms=future_time),
        )
        after = rank_cross_section(current_values, snapshot())
        self.assertEqual(before, after)

    def test_all_transforms_reject_mixed_formations(self):
        mixed = values({"AAAUSDT": 1, "BBBUSDT": 2, "CCCUSDT": 3})
        mixed = mixed[:-1] + (replace(mixed[-1], formation_time_ms=FORMATION + 1),)
        with self.assertRaises(CrossSectionError):
            rank_cross_section(mixed, snapshot())
        with self.assertRaises(CrossSectionError):
            winsorize_cross_section(
                mixed, snapshot(), lower_quantile=0.1, upper_quantile=0.9
            )
        with self.assertRaises(CrossSectionError):
            residualize_cross_section(
                mixed,
                {"x": values({"AAAUSDT": 0, "BBBUSDT": 1, "CCCUSDT": 2})},
                snapshot(),
                include_intercept=True,
            )

    def test_noneligible_values_cannot_change_eligible_transform(self):
        base_values = values({"AAAUSDT": 1, "BBBUSDT": 2, "CCCUSDT": 3})
        base = rank_cross_section(base_values, snapshot())
        blocked = membership("DDDUSDT", quote_asset="BTC")
        extended_snapshot = snapshot(
            ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"),
            overrides={"DDDUSDT": blocked},
        )
        extended = base_values + values({"DDDUSDT": 1e100})
        result = rank_cross_section(extended, extended_snapshot)
        self.assertEqual(
            [(row.symbol, row.value) for row in base],
            [(row.symbol, row.value) for row in result],
        )

    def test_duplicate_nonfinite_low_breadth_and_all_ties_are_rejected(self):
        with self.assertRaises(CrossSectionError):
            rank_cross_section(
                values({"AAAUSDT": 1, "BBBUSDT": 2})
                + values({"AAAUSDT": 3}),
                snapshot(),
            )
        with self.assertRaises((CrossSectionError, AlphaContractError)):
            rank_cross_section(
                values({"AAAUSDT": 1, "BBBUSDT": math.inf, "CCCUSDT": 3}),
                snapshot(),
            )
        with self.assertRaises(CrossSectionError):
            rank_cross_section(
                values({"AAAUSDT": 1, "BBBUSDT": 2}),
                snapshot(("AAAUSDT", "BBBUSDT")),
            )
        with self.assertRaises(CrossSectionError):
            rank_cross_section(
                values({"AAAUSDT": 1, "BBBUSDT": 1, "CCCUSDT": 1}), snapshot()
            )

    def test_residuals_are_orthogonal_and_rank_deficiency_is_rejected(self):
        symbols = ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")
        snap = snapshot(symbols)
        target = values(dict(zip(symbols, (1.0, 2.0, 2.0, 5.0))))
        exposure = values(dict(zip(symbols, (0.0, 1.0, 2.0, 4.0))))
        residuals = residualize_cross_section(
            target, {"x": exposure}, snap, include_intercept=True, min_breadth=4
        )
        self.assertAlmostEqual(sum(row.value for row in residuals), 0.0, places=10)
        self.assertAlmostEqual(
            sum(row.value * x.value for row, x in zip(residuals, exposure)),
            0.0,
            places=10,
        )
        with self.assertRaises(CrossSectionError):
            residualize_cross_section(
                target,
                {"x": exposure, "duplicate_x": exposure},
                snap,
                include_intercept=True,
                min_breadth=4,
            )


class LabelAndSplitTests(unittest.TestCase):
    def test_next_open_clock_for_all_horizons(self):
        formation = point()
        prices = tuple(
            OpenPrice(
                "AAAUSDT",
                formation.feature_bar_index + offset,
                formation.decision_time_ms + (offset - 1) * HOUR_MILLISECONDS,
                formation.decision_time_ms + (offset - 1) * HOUR_MILLISECONDS,
                100.0 + offset,
                SHA_C,
            )
            for offset in range(1, max(ALPHA_HORIZONS_HOURS) + 2)
        )
        labels = build_next_open_labels((formation,), prices)
        self.assertEqual(tuple(label.horizon_hours for label in labels), ALPHA_HORIZONS_HOURS)
        for label in labels:
            self.assertEqual(label.entry_time_ms, formation.decision_time_ms)
            self.assertEqual(
                label.exit_time_ms,
                formation.decision_time_ms + label.horizon_hours * HOUR_MILLISECONDS,
            )
            self.assertEqual(label.known_at_ms, label.exit_time_ms)
            self.assertEqual(label.feature_bar_open_time_ms, BASE)

    def test_feature_and_open_price_clock_contracts_are_exact(self):
        invalid_formation = replace(point(), decision_time_ms=BASE + 2 * HOUR_MILLISECONDS)
        with self.assertRaises(LabelContractError):
            build_next_open_labels((invalid_formation,), ())
        formation = point()
        early_price = OpenPrice(
            "AAAUSDT",
            formation.feature_bar_index + 1,
            formation.decision_time_ms,
            formation.decision_time_ms - 1,
            100.0,
            SHA_A,
        )
        with self.assertRaises(LabelContractError):
            build_next_open_labels((formation,), (early_price,), horizons_hours=(1,))

    def test_label_is_not_an_expert_cross_section_input(self):
        label = forward_label("AAAUSDT", FORMATION, 1)
        with self.assertRaises(CrossSectionError):
            rank_cross_section((label,), snapshot(("AAAUSDT",)), min_breadth=2)

    def test_label_interval_is_entry_through_exit_plus_one_millisecond(self):
        label = forward_label("AAAUSDT", FORMATION, 1)
        interval = LabelInterval.from_label(label)
        self.assertEqual(interval.start_utc_ms, FORMATION)
        self.assertEqual(interval.end_utc_ms_exclusive, FORMATION + HOUR_MILLISECONDS + 1)
        overlaps_by_one_ms = LabelInterval("X", 1, interval.end_utc_ms_exclusive - 1, interval.end_utc_ms_exclusive + 1)
        adjacent = LabelInterval("X", 1, interval.end_utc_ms_exclusive, interval.end_utc_ms_exclusive + 1)
        one_hour_before_end = LabelInterval("X", 1, interval.end_utc_ms_exclusive - HOUR_MILLISECONDS, interval.end_utc_ms_exclusive)
        one_hour_after = LabelInterval("X", 1, interval.end_utc_ms_exclusive + HOUR_MILLISECONDS, interval.end_utc_ms_exclusive + HOUR_MILLISECONDS + 1)
        self.assertTrue(interval.overlaps(overlaps_by_one_ms))
        self.assertTrue(interval.overlaps(one_hour_before_end))
        self.assertFalse(interval.overlaps(adjacent))
        self.assertFalse(interval.overlaps(one_hour_after))

    def test_shared_multi_horizon_contract_is_481_hours(self):
        spec = PurgeEmbargoSpec.build(
            ALPHA_HORIZONS_HOURS, purge_bars=481, embargo_bars=481
        )
        self.assertEqual(spec.required_shared_bars, 481)
        with self.assertRaises(LabelContractError):
            PurgeEmbargoSpec.build(
                ALPHA_HORIZONS_HOURS, purge_bars=480, embargo_bars=481
            )

    def test_direct_purge_spec_cannot_bypass_minimum_or_hourly_bar_contract(self):
        with self.assertRaises(LabelContractError):
            PurgeEmbargoSpec(ALPHA_HORIZONS_HOURS, 480, 481)
        with self.assertRaises(LabelContractError):
            PurgeEmbargoSpec(ALPHA_HORIZONS_HOURS, 481, 481, HOUR_MILLISECONDS - 1)
        with self.assertRaises(LabelContractError):
            PurgeEmbargoSpec((1, 1), 2, 2)

    def test_label_horizon_requires_exact_builtin_int(self):
        class HorizonAlias(IntEnum):
            DAY = 24

        valid_label = forward_label("AAAUSDT", FORMATION, 24)
        for alias in (True, HorizonAlias.DAY, 24.0, "24"):
            with self.subTest(alias=repr(alias)):
                with self.assertRaises(LabelContractError):
                    PurgeEmbargoSpec.build(
                        (alias,), purge_bars=481, embargo_bars=481
                    )
                with self.assertRaises(LabelContractError):
                    LabelInterval.from_label(
                        replace(valid_label, horizon_hours=alias)
                    )

    def test_irregular_utc_purge_and_embargo_exact_boundaries_pass(self):
        spec = PurgeEmbargoSpec.build(
            ALPHA_HORIZONS_HOURS, purge_bars=481, embargo_bars=481
        )
        evaluation_start = BASE + 2_000 * HOUR_MILLISECONDS + 123
        evaluation_end = evaluation_start + 2 * HOUR_MILLISECONDS + 17
        pre_end = evaluation_start - spec.purge_milliseconds
        pre_decision = pre_end - HOUR_MILLISECONDS - 1
        post_decision = evaluation_end + spec.embargo_milliseconds
        assignments = (
            SplitLabel(forward_label("PRE", pre_decision, 1), SplitRole.TRAIN),
            SplitLabel(forward_label("EVAL", evaluation_start, 1), SplitRole.EVALUATION),
            SplitLabel(forward_label("POST", post_decision, 1), SplitRole.TRAIN),
        )
        validate_purged_embargo_split(
            assignments,
            spec,
            evaluation_start_ms=evaluation_start,
            evaluation_end_ms_exclusive=evaluation_end,
        )

    def test_purge_and_embargo_reject_one_ms_and_one_hour_inside_boundaries(self):
        spec = PurgeEmbargoSpec.build(
            ALPHA_HORIZONS_HOURS, purge_bars=481, embargo_bars=481
        )
        evaluation_start = BASE + 2_000 * HOUR_MILLISECONDS + 123
        evaluation_end = evaluation_start + 2 * HOUR_MILLISECONDS + 17
        exact_pre = evaluation_start - spec.purge_milliseconds - HOUR_MILLISECONDS - 1
        exact_post = evaluation_end + spec.embargo_milliseconds
        evaluation = SplitLabel(
            forward_label("EVAL", evaluation_start, 1), SplitRole.EVALUATION
        )
        for shift in (1, HOUR_MILLISECONDS):
            with self.subTest(side="purge", shift=shift):
                with self.assertRaises(LabelContractError):
                    validate_purged_embargo_split(
                        (
                            SplitLabel(forward_label("PRE", exact_pre + shift, 1), SplitRole.TRAIN),
                            evaluation,
                        ),
                        spec,
                        evaluation_start_ms=evaluation_start,
                        evaluation_end_ms_exclusive=evaluation_end,
                    )
            with self.subTest(side="embargo", shift=shift):
                with self.assertRaises(LabelContractError):
                    validate_purged_embargo_split(
                        (
                            evaluation,
                            SplitLabel(forward_label("POST", exact_post - shift, 1), SplitRole.TRAIN),
                        ),
                        spec,
                        evaluation_start_ms=evaluation_start,
                        evaluation_end_ms_exclusive=evaluation_end,
                    )

    def test_actual_interval_overlap_and_formation_fold_split_are_rejected(self):
        spec = PurgeEmbargoSpec.build((1,), purge_bars=2, embargo_bars=2)
        evaluation_start = BASE + 100 * HOUR_MILLISECONDS
        evaluation_end = evaluation_start + 2 * HOUR_MILLISECONDS
        with self.assertRaises(LabelContractError):
            validate_purged_embargo_split(
                (
                    SplitLabel(forward_label("PRE", evaluation_start - HOUR_MILLISECONDS, 1), SplitRole.TRAIN),
                    SplitLabel(forward_label("EVAL", evaluation_start, 1), SplitRole.EVALUATION),
                ),
                spec,
                evaluation_start_ms=evaluation_start,
                evaluation_end_ms_exclusive=evaluation_end,
            )
        with self.assertRaises(LabelContractError):
            validate_purged_embargo_split(
                (
                    SplitLabel(forward_label("AAA", evaluation_start, 1), SplitRole.EVALUATION),
                    SplitLabel(forward_label("BBB", evaluation_start, 1), SplitRole.TRAIN),
                ),
                spec,
                evaluation_start_ms=evaluation_start,
                evaluation_end_ms_exclusive=evaluation_end,
            )


class RegistryEnsembleAndNetAlphaTests(unittest.TestCase):
    def test_registry_is_duplicate_safe_order_invariant_and_higher_only(self):
        registry = synthetic_registry()
        reversed_registry = ExpertRegistry.build(reversed(registry.specs))
        self.assertEqual(registry, reversed_registry)
        with self.assertRaises(ExpertRegistryError):
            ExpertRegistry.build(registry.specs + (registry.specs[0],))
        with self.assertRaises(ExpertRegistryError):
            ExpertRegistry.build((replace(registry.specs[0], output_direction="LOWER_IS_BETTER"),))

    def test_direct_registry_construction_cannot_bypass_direction_or_horizon(self):
        valid = synthetic_registry().specs[0]
        with self.assertRaises(ExpertRegistryError):
            ExpertRegistry((replace(valid, output_direction="LOWER_IS_BETTER"),))
        with self.assertRaises(ExpertRegistryError):
            ExpertRegistry((replace(valid, key=replace(valid.key, horizon_hours=2)),))

    def test_modern_catalog_is_explicitly_blocked_and_has_no_equity_style_names(self):
        catalog = modern_crypto_v1_readiness_catalog()
        self.assertTrue(
            all(
                spec.readiness in (ExpertReadiness.PIT_BLOCKED, ExpertReadiness.DATA_BLOCKED)
                for spec in catalog.specs
            )
        )
        self.assertNotIn("Value", {spec.key.family for spec in catalog.specs})
        self.assertNotIn("Quality", {spec.key.family for spec in catalog.specs})
        with self.assertRaises(EnsembleContractError):
            combine_hierarchical(outputs(catalog), catalog, fixed_weights(catalog))

    def test_fixed_hierarchical_ensemble_is_order_invariant_and_provenanced(self):
        registry = synthetic_registry()
        weights = fixed_weights(registry)
        direct = combine_hierarchical(outputs(registry), registry, weights)
        reversed_result = combine_hierarchical(
            tuple(reversed(outputs(registry))), registry, weights
        )
        self.assertEqual(direct, reversed_result)
        self.assertEqual(direct.horizon_hours, 24)
        self.assertEqual(direct.value, 1.75)
        self.assertEqual(len(direct.provenance_sha256), 64)

    def test_each_horizon_combines_individually_and_horizon_changes_provenance(self):
        results = []
        for horizon in ALPHA_HORIZONS_HOURS:
            registry = synthetic_registry(horizon_hours=horizon)
            result = combine_hierarchical(
                outputs(registry), registry, fixed_weights(registry)
            )
            self.assertEqual(result.horizon_hours, horizon)
            self.assertEqual(result.value, 1.75)
            results.append(result.provenance_sha256)
        self.assertEqual(len(set(results)), len(ALPHA_HORIZONS_HOURS))

    def test_mixed_horizon_registry_is_rejected(self):
        base = synthetic_registry()
        mixed = ExpertRegistry.build(
            (
                replace(
                    base.specs[0],
                    key=replace(base.specs[0].key, horizon_hours=1),
                ),
                *base.specs[1:],
            )
        )
        with self.assertRaises(EnsembleContractError):
            combine_hierarchical(outputs(mixed), mixed, fixed_weights(mixed))

    def test_output_and_weight_keys_validate_before_equality(self):
        class HorizonAlias(IntEnum):
            ONE = 1

        registry = synthetic_registry(horizon_hours=1)
        base_outputs = outputs(registry)
        base_weights = fixed_weights(registry)
        valid = combine_hierarchical(
            base_outputs, registry, base_weights
        )
        self.assertEqual(valid.horizon_hours, 1)
        self.assertEqual(valid.value, 1.75)
        target = registry.keys[0]
        target_output_index = next(
            index for index, output in enumerate(base_outputs) if output.key == target
        )
        target_family_index = next(
            index
            for index, family in enumerate(base_weights.families)
            if family.family == target.family
        )
        for alias in (True, HorizonAlias.ONE, 1.0, "1"):
            forged_key = replace(target, horizon_hours=alias)
            forged_outputs = list(base_outputs)
            forged_outputs[target_output_index] = replace(
                forged_outputs[target_output_index], key=forged_key
            )
            with self.subTest(location="output", alias=repr(alias)):
                with self.assertRaises(EnsembleContractError):
                    combine_hierarchical(
                        tuple(forged_outputs), registry, base_weights
                    )
            forged_families = list(base_weights.families)
            target_family = forged_families[target_family_index]
            forged_families[target_family_index] = replace(
                target_family,
                expert_weights=tuple(
                    (forged_key if key == target else key, weight)
                    for key, weight in target_family.expert_weights
                ),
            )
            with self.subTest(location="weight", alias=repr(alias)):
                with self.assertRaises(EnsembleContractError):
                    combine_hierarchical(
                        base_outputs,
                        registry,
                        replace(base_weights, families=tuple(forged_families)),
                    )

    def test_ensemble_rejects_missing_extra_duplicate_empty_and_non_simplex_maps(self):
        registry = synthetic_registry()
        base_outputs = outputs(registry)
        weights = fixed_weights(registry)
        invalid_outputs = (
            (),
            base_outputs[:-1],
            base_outputs + (base_outputs[0],),
            base_outputs
            + (
                ExpertOutput(
                    ExpertKey("Extra", "x", 24, "v1"),
                    "AAAUSDT",
                    FORMATION,
                    FORMATION,
                    1.0,
                    SHA_A,
                ),
            ),
        )
        for candidate in invalid_outputs:
            with self.subTest(length=len(candidate)):
                with self.assertRaises(EnsembleContractError):
                    combine_hierarchical(candidate, registry, weights)
        bad_weights = replace(weights, family_weights=(("Flow", 0.1), ("Price", 0.1)))
        with self.assertRaises(EnsembleContractError):
            combine_hierarchical(base_outputs, registry, bad_weights)

    def test_blocked_registry_future_output_and_bad_provenance_cannot_ensemble(self):
        blocked = synthetic_registry(readiness=ExpertReadiness.PIT_BLOCKED)
        with self.assertRaises(EnsembleContractError):
            combine_hierarchical(outputs(blocked), blocked, fixed_weights(blocked))
        registry = synthetic_registry()
        base = list(outputs(registry))
        for replacement in (
            replace(base[0], known_at_ms=FORMATION + 1),
            replace(base[0], provenance_sha256="not-a-hash"),
        ):
            candidate = (replacement, *base[1:])
            with self.assertRaises((EnsembleContractError, AlphaContractError)):
                combine_hierarchical(candidate, registry, fixed_weights(registry))

    def test_regime_gate_preserves_sign_order_bounds_and_provenance(self):
        scores = (
            EnsembleScore("AAA", FORMATION, FORMATION, 24, -2.0, SHA_A),
            EnsembleScore("BBB", FORMATION, FORMATION, 24, 1.0, SHA_B),
            EnsembleScore("CCC", FORMATION, FORMATION, 24, 3.0, SHA_C),
        )
        adjusted = apply_regime_multiplier(
            scores, RegimeMultiplier(FORMATION, FORMATION, 1.3, SHA_D)
        )
        self.assertEqual(
            [row.symbol for row in sorted(adjusted, key=lambda row: row.adjusted_value)],
            [row.symbol for row in sorted(scores, key=lambda row: row.value)],
        )
        for source, result in zip(sorted(scores, key=lambda row: row.symbol), adjusted):
            self.assertEqual(math.copysign(1, source.value), math.copysign(1, result.adjusted_value))
            self.assertLessEqual(abs(result.adjusted_value), abs(source.value) * 1.3 + 1e-15)
            self.assertEqual(result.horizon_hours, 24)
            self.assertEqual(len(result.provenance_sha256), 64)

    def test_regime_gate_rejects_raw_future_unprovenanced_and_out_of_bounds_values(self):
        score = EnsembleScore("AAA", FORMATION, FORMATION, 24, 1.0, SHA_A)
        invalid_scores = (
            (1.0,),
            (replace(score, known_at_ms=FORMATION + 1),),
            (replace(score, provenance_sha256="bad"),),
        )
        for candidate in invalid_scores:
            with self.assertRaises((EnsembleContractError, AlphaContractError)):
                apply_regime_multiplier(
                    candidate, RegimeMultiplier(FORMATION, FORMATION, 1.0, SHA_B)
                )
        for scalar in (0.699999, 1.300001, math.nan):
            with self.assertRaises((EnsembleContractError, AlphaContractError)):
                apply_regime_multiplier(
                    (score,), RegimeMultiplier(FORMATION, FORMATION, scalar, SHA_B)
                )

    def test_regime_gate_rejects_mixed_horizons(self):
        scores = (
            EnsembleScore("AAA", FORMATION, FORMATION, 1, 1.0, SHA_A),
            EnsembleScore("BBB", FORMATION, FORMATION, 24, 2.0, SHA_B),
        )
        with self.assertRaises(EnsembleContractError):
            apply_regime_multiplier(
                scores, RegimeMultiplier(FORMATION, FORMATION, 1.0, SHA_C)
            )

    def test_expected_net_alpha_requires_adjusted_score_and_penalties_are_monotone(self):
        score = RegimeAdjustedScore(
            "AAA", FORMATION, FORMATION, 24, 0.02, 1.2, 0.024, SHA_A
        )
        base = compute_expected_net_alpha(
            score,
            trading_cost=TradingCostPenalty(0.0015, 1.0, 2),
            uncertainty_penalty=0.001,
            crowding_penalty=0.002,
        )
        stressed = compute_expected_net_alpha(
            score,
            trading_cost=TradingCostPenalty(0.003, 1.0, 2),
            uncertainty_penalty=0.002,
            crowding_penalty=0.003,
        )
        self.assertEqual(base.symbol, score.symbol)
        self.assertEqual(base.formation_time_ms, score.formation_time_ms)
        self.assertEqual(base.horizon_hours, score.horizon_hours)
        self.assertAlmostEqual(base.trading_cost_penalty, 0.003)
        self.assertLess(stressed.expected_net_alpha, base.expected_net_alpha)
        with self.assertRaises(AlphaContractError):
            compute_expected_net_alpha(
                0.024,
                trading_cost=TradingCostPenalty(0.0015, 1.0, 2),
                uncertainty_penalty=0,
                crowding_penalty=0,
            )

    def test_direct_regime_adjusted_score_cannot_bypass_internal_consistency(self):
        valid = RegimeAdjustedScore(
            "AAA", FORMATION, FORMATION, 24, 0.02, 1.2, 0.024, SHA_A
        )
        invalid = (
            replace(valid, adjusted_value=0.025),
            replace(valid, multiplier=1.31, adjusted_value=0.0262),
            replace(valid, known_at_ms=FORMATION + 1),
        )
        for score in invalid:
            with self.subTest(score=score):
                with self.assertRaises(AlphaContractError):
                    compute_expected_net_alpha(
                        score,
                        trading_cost=TradingCostPenalty(0.0015, 1.0, 2),
                        uncertainty_penalty=0,
                        crowding_penalty=0,
                    )

    def test_expected_net_alpha_internal_forgery_is_rejected_by_projection(self):
        adjusted = RegimeAdjustedScore(
            "AAA", FORMATION, FORMATION, 24, 0.02, 1.0, 0.02, SHA_A
        )
        valid = compute_expected_net_alpha(
            adjusted,
            trading_cost=TradingCostPenalty(0.0015, 1.0, 2),
            uncertainty_penalty=0.001,
            crowding_penalty=0.002,
        )
        projection = diagnostic_score_from_expected_net_alpha(valid)
        self.assertEqual(projection.horizon_hours, 24)
        self.assertEqual(projection.value, valid.expected_net_alpha)
        for forged in (
            replace(valid, expected_net_alpha=valid.expected_net_alpha + 1e-15),
            replace(valid, trading_cost_penalty=-0.1),
            replace(valid, known_at_ms=FORMATION + 1),
            replace(valid, provenance_sha256="bad"),
        ):
            with self.subTest(forged=forged):
                with self.assertRaises(AlphaContractError):
                    diagnostic_score_from_expected_net_alpha(forged)

    def test_exact_horizon_aliases_fail_at_all_consumers(self):
        class HorizonAlias(IntEnum):
            DAY = 24

        aliases = (True, HorizonAlias.DAY, 24.0, "24")
        valid_spec = synthetic_registry().specs[0]
        ensemble = EnsembleScore("AAA", FORMATION, FORMATION, 24, 1.0, SHA_A)
        adjusted = RegimeAdjustedScore(
            "AAA", FORMATION, FORMATION, 24, 1.0, 1.0, 1.0, SHA_B
        )
        net = compute_expected_net_alpha(
            adjusted,
            trading_cost=TradingCostPenalty(0.0, 1.0, 1),
            uncertainty_penalty=0.0,
            crowding_penalty=0.0,
        )
        for alias in aliases:
            with self.subTest(alias=repr(alias)):
                with self.assertRaises(ExpertRegistryError):
                    ExpertRegistry.build(
                        (
                            replace(
                                valid_spec,
                                key=replace(valid_spec.key, horizon_hours=alias),
                            ),
                        )
                    )
                with self.assertRaises((EnsembleContractError, AlphaContractError)):
                    apply_regime_multiplier(
                        (replace(ensemble, horizon_hours=alias),),
                        RegimeMultiplier(FORMATION, FORMATION, 1.0, SHA_C),
                    )
                with self.assertRaises((EnsembleContractError, AlphaContractError)):
                    compose_multi_horizon(
                        (replace(ensemble, horizon_hours=alias),)
                    )
                with self.assertRaises(AlphaContractError):
                    diagnostic_score_from_expected_net_alpha(
                        replace(net, horizon_hours=alias)
                    )
                with self.assertRaises(CrossSectionError):
                    rank_information_coefficient(
                        diagnostic_values(
                            {"AAAUSDT": 1, "BBBUSDT": 2, "CCCUSDT": 3}
                        ),
                        tuple(
                            forward_label(symbol, FORMATION, 24, value)
                            for symbol, value in (
                                ("AAAUSDT", 0.1),
                                ("BBBUSDT", 0.2),
                                ("CCCUSDT", 0.3),
                            )
                        ),
                        snapshot(),
                        horizon_hours=alias,
                    )

    def test_multi_horizon_bundle_has_exact_shape_and_is_order_invariant(self):
        scores = tuple(
            EnsembleScore(
                "AAA",
                FORMATION,
                FORMATION - horizon,
                horizon,
                float(horizon),
                sha,
            )
            for horizon, sha in zip(
                ALPHA_HORIZONS_HOURS, (SHA_A, SHA_B, SHA_C, SHA_D)
            )
        )
        direct = compose_multi_horizon(scores)
        reversed_result = compose_multi_horizon(tuple(reversed(scores)))
        self.assertEqual(direct, reversed_result)
        self.assertEqual(
            tuple(field.name for field in fields(MultiHorizonEnsemble)),
            (
                "symbol",
                "formation_time_ms",
                "known_at_ms",
                "scores",
                "provenance_sha256",
            ),
        )
        self.assertEqual(
            tuple(score.horizon_hours for score in direct.scores),
            ALPHA_HORIZONS_HOURS,
        )
        self.assertEqual(direct.known_at_ms, FORMATION - 1)
        self.assertFalse(hasattr(direct, "value"))
        self.assertFalse(hasattr(direct, "scalar"))
        self.assertFalse(hasattr(direct, "weights"))

    def test_multi_horizon_bundle_rejects_incomplete_or_malformed_inputs(self):
        scores = tuple(
            EnsembleScore("AAA", FORMATION, FORMATION, horizon, 1.0, sha)
            for horizon, sha in zip(
                ALPHA_HORIZONS_HOURS, (SHA_A, SHA_B, SHA_C, SHA_D)
            )
        )
        invalid = (
            scores[:-1],
            scores + (scores[0],),
            (replace(scores[0], horizon_hours=24), *scores[1:]),
            (replace(scores[0], symbol="BBB"), *scores[1:]),
            (replace(scores[0], formation_time_ms=FORMATION + 1), *scores[1:]),
            (replace(scores[0], known_at_ms=FORMATION + 1), *scores[1:]),
            (replace(scores[0], provenance_sha256="bad"), *scores[1:]),
            (replace(scores[0], value=math.inf), *scores[1:]),
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                with self.assertRaises((EnsembleContractError, AlphaContractError)):
                    compose_multi_horizon(candidate)

    def test_multi_horizon_direct_construction_is_fail_closed(self):
        scores = tuple(
            EnsembleScore("AAA", FORMATION, FORMATION, horizon, 1.0, sha)
            for horizon, sha in zip(
                ALPHA_HORIZONS_HOURS, (SHA_A, SHA_B, SHA_C, SHA_D)
            )
        )
        valid = compose_multi_horizon(scores)
        direct = MultiHorizonEnsemble(
            valid.symbol,
            valid.formation_time_ms,
            valid.known_at_ms,
            valid.scores,
            valid.provenance_sha256,
        )
        self.assertEqual(direct, valid)
        invalid_arguments = (
            (FORMATION, scores[:-1], valid.provenance_sha256),
            (FORMATION, scores[:-1] + (scores[0],), valid.provenance_sha256),
            (
                FORMATION,
                (replace(scores[0], symbol="BBB"), *scores[1:]),
                valid.provenance_sha256,
            ),
            (
                FORMATION,
                (replace(scores[0], formation_time_ms=FORMATION + 1), *scores[1:]),
                valid.provenance_sha256,
            ),
            (
                FORMATION,
                (replace(scores[0], known_at_ms=FORMATION + 1), *scores[1:]),
                valid.provenance_sha256,
            ),
            (
                FORMATION,
                (replace(scores[0], horizon_hours=True), *scores[1:]),
                valid.provenance_sha256,
            ),
            (FORMATION, tuple(reversed(scores)), valid.provenance_sha256),
            (FORMATION + 1, scores, valid.provenance_sha256),
            (FORMATION, scores, "f" * 64),
        )
        for known_at, candidate_scores, provenance in invalid_arguments:
            with self.subTest(
                known_at=known_at,
                horizons=tuple(score.horizon_hours for score in candidate_scores),
                provenance=provenance,
            ):
                with self.assertRaises((EnsembleContractError, AlphaContractError)):
                    MultiHorizonEnsemble(
                        "AAA",
                        FORMATION,
                        known_at,
                        candidate_scores,
                        provenance,
                    )
        with self.assertRaises(EnsembleContractError):
            MultiHorizonEnsemble(
                "AAA",
                FORMATION,
                FORMATION,
                list(scores),
                valid.provenance_sha256,
            )

    def test_multi_horizon_bundle_cannot_use_old_scalar_api_or_enter_consumers(self):
        scores = tuple(
            EnsembleScore("AAA", FORMATION, FORMATION, horizon, 1.0, sha)
            for horizon, sha in zip(
                ALPHA_HORIZONS_HOURS, (SHA_A, SHA_B, SHA_C, SHA_D)
            )
        )
        bundle = compose_multi_horizon(scores)
        with self.assertRaises(TypeError):
            MultiHorizonEnsemble(
                symbol="AAA",
                formation_time_ms=FORMATION,
                known_at_ms=FORMATION,
                scores=scores,
                provenance_sha256=SHA_A,
                value=1.0,
            )
        with self.assertRaises(EnsembleContractError):
            apply_regime_multiplier(
                (bundle,), RegimeMultiplier(FORMATION, FORMATION, 1.0, SHA_A)
            )
        with self.assertRaises(AlphaContractError):
            compute_expected_net_alpha(
                bundle,
                trading_cost=TradingCostPenalty(0.0, 1.0, 1),
                uncertainty_penalty=0.0,
                crowding_penalty=0.0,
            )


class SyntheticDiagnosticTests(unittest.TestCase):
    def test_expected_net_projection_preserves_horizon_and_supports_exact_ic(self):
        diagnostics = []
        for symbol, gross, sha in (
            ("AAAUSDT", 0.01, SHA_A),
            ("BBBUSDT", 0.02, SHA_B),
            ("CCCUSDT", 0.03, SHA_C),
        ):
            adjusted = RegimeAdjustedScore(
                symbol,
                FORMATION,
                FORMATION,
                24,
                gross,
                1.0,
                gross,
                sha,
            )
            net = compute_expected_net_alpha(
                adjusted,
                trading_cost=TradingCostPenalty(0.0, 1.0, 1),
                uncertainty_penalty=0.0,
                crowding_penalty=0.0,
            )
            diagnostics.append(diagnostic_score_from_expected_net_alpha(net))
        labels = tuple(
            forward_label(symbol, FORMATION, 24, value)
            for symbol, value in (
                ("AAAUSDT", 0.1),
                ("BBBUSDT", 0.2),
                ("CCCUSDT", 0.3),
            )
        )
        result = rank_information_coefficient(
            tuple(diagnostics), labels, snapshot(), horizon_hours=24
        )
        self.assertEqual(result.horizon_hours, 24)
        self.assertAlmostEqual(result.rank_ic, 1.0)
        wrong_labels = tuple(
            forward_label(symbol, FORMATION, 1, value)
            for symbol, value in (
                ("AAAUSDT", 0.1),
                ("BBBUSDT", 0.2),
                ("CCCUSDT", 0.3),
            )
        )
        with self.assertRaises(CrossSectionError):
            rank_information_coefficient(
                tuple(diagnostics), wrong_labels, snapshot(), horizon_hours=24
            )

    def test_horizon_changes_regime_net_and_diagnostic_provenance(self):
        provenance_chains = []
        for horizon in (1, 24):
            ensemble = EnsembleScore(
                "AAAUSDT", FORMATION, FORMATION, horizon, 0.02, SHA_A
            )
            adjusted = apply_regime_multiplier(
                (ensemble,), RegimeMultiplier(FORMATION, FORMATION, 1.0, SHA_B)
            )[0]
            net = compute_expected_net_alpha(
                adjusted,
                trading_cost=TradingCostPenalty(0.0, 1.0, 1),
                uncertainty_penalty=0.0,
                crowding_penalty=0.0,
            )
            diagnostic = diagnostic_score_from_expected_net_alpha(net)
            provenance_chains.append(
                (
                    adjusted.provenance_sha256,
                    net.provenance_sha256,
                    diagnostic.provenance_sha256,
                )
            )
            self.assertEqual(adjusted.horizon_hours, horizon)
            self.assertEqual(net.horizon_hours, horizon)
            self.assertEqual(diagnostic.horizon_hours, horizon)
        self.assertNotEqual(provenance_chains[0], provenance_chains[1])

    def test_rank_ic_and_fixed_incremental_diagnostic(self):
        scores = diagnostic_values({"AAAUSDT": 1, "BBBUSDT": 2, "CCCUSDT": 3})
        labels = tuple(
            forward_label(symbol, FORMATION, 24, value)
            for symbol, value in (
                ("AAAUSDT", 0.1),
                ("BBBUSDT", 0.2),
                ("CCCUSDT", 0.3),
            )
        )
        ic = rank_information_coefficient(
            scores, labels, snapshot(), horizon_hours=24
        )
        self.assertAlmostEqual(ic.rank_ic, 1.0)
        ablated = diagnostic_values({"AAAUSDT": 3, "BBBUSDT": 2, "CCCUSDT": 1})
        diagnostic = incremental_information_diagnostic(
            scores, ablated, labels, snapshot(), horizon_hours=24
        )
        self.assertAlmostEqual(diagnostic.full_ic, 1.0)
        self.assertAlmostEqual(diagnostic.ablated_ic, -1.0)
        self.assertAlmostEqual(diagnostic.delta_ic, 2.0)

    def test_ic_rejects_missing_wrong_horizon_wrong_time_and_low_breadth(self):
        scores = diagnostic_values({"AAAUSDT": 1, "BBBUSDT": 2, "CCCUSDT": 3})
        labels = tuple(
            forward_label(symbol, FORMATION, 24, value)
            for symbol, value in (
                ("AAAUSDT", 0.1),
                ("BBBUSDT", 0.2),
                ("CCCUSDT", 0.3),
            )
        )
        invalid_labels = (
            labels[:-1],
            (replace(labels[0], horizon_hours=1, exit_bar_index=9, exit_time_ms=FORMATION + HOUR_MILLISECONDS, known_at_ms=FORMATION + HOUR_MILLISECONDS), *labels[1:]),
            (forward_label("AAAUSDT", FORMATION + HOUR_MILLISECONDS, 24, 0.1), *labels[1:]),
        )
        for candidate in invalid_labels:
            with self.assertRaises((CrossSectionError, LabelContractError)):
                rank_information_coefficient(
                    scores, candidate, snapshot(), horizon_hours=24
                )
        with self.assertRaises(CrossSectionError):
            rank_information_coefficient(
                scores[:2],
                labels[:2],
                snapshot(("AAAUSDT", "BBBUSDT")),
                horizon_hours=24,
            )

    def test_ic_rejects_wrong_score_horizon(self):
        scores = list(
            diagnostic_values({"AAAUSDT": 1, "BBBUSDT": 2, "CCCUSDT": 3})
        )
        scores[0] = replace(scores[0], horizon_hours=1)
        labels = tuple(
            forward_label(symbol, FORMATION, 24, value)
            for symbol, value in (
                ("AAAUSDT", 0.1),
                ("BBBUSDT", 0.2),
                ("CCCUSDT", 0.3),
            )
        )
        with self.assertRaises(CrossSectionError):
            rank_information_coefficient(
                tuple(scores), labels, snapshot(), horizon_hours=24
            )

    def test_incremental_diagnostic_requires_full_key_alignment(self):
        snap = snapshot(("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"))
        full = diagnostic_values({"AAAUSDT": 1, "BBBUSDT": 2, "CCCUSDT": 3})
        ablated = diagnostic_values({"AAAUSDT": 1, "BBBUSDT": 2, "DDDUSDT": 3})
        labels = tuple(
            forward_label(symbol, FORMATION, 24, value)
            for symbol, value in (
                ("AAAUSDT", 0.1),
                ("BBBUSDT", 0.2),
                ("CCCUSDT", 0.3),
            )
        )
        with self.assertRaises(CrossSectionError):
            incremental_information_diagnostic(
                full, ablated, labels, snap, horizon_hours=24
            )


if __name__ == "__main__":
    unittest.main()
