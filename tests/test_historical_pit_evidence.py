from __future__ import annotations

import hashlib
import json
import unittest

from quant_research.hierarchical_alpha import (
    EvidenceKind,
    MarketType,
    PermissionState,
    TradingStatus,
    Venue,
    require_pit_eligibility,
)
from quant_research.historical_pit_evidence import (
    ALLOWED_AUTHORITY_ID,
    ALLOWED_SOURCE_CONTRACT_ID,
    EmpiricalPITAuthorizationError,
    HistoricalPITEvidenceError,
    RawPayloadBinding,
    RawRecordLocator,
    SCHEMA_VERSION,
    SYNTHETIC_POLICY_ID,
    build_bound_historical_pit_snapshot,
    replay_bound_record_fragment,
    require_trusted_empirical_pit_eligibility,
)


EXPECTED_STATUS_EVIDENCE_REFERENCE_SHA256 = (
    "52e6bdf8e9ffbb2eb6b793fd4b28e2265f098bf88449840be5af09079b207f63"
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def record(
    source_id: str,
    component: str,
    value: str | None,
    *,
    symbol: str = "FOOUSDT",
    start: int | None = 100,
    end: int | None = 300,
    published: int = 50,
    semantics: str = "HISTORICAL_EFFECTIVE_FACT",
    action: str = "ASSERT",
    target: str | None = None,
) -> dict[str, object]:
    return {
        "assertion_semantics": semantics,
        "component": component,
        "effective_from_ms": start,
        "effective_to_ms_exclusive": end,
        "published_at_ms": published,
        "revision_action": action,
        "revision_target_source_id": target,
        "source_id": source_id,
        "symbol": symbol,
        "value": value,
    }


def payload(
    records: list[dict[str, object]],
    *,
    authority: str = ALLOWED_AUTHORITY_ID,
) -> dict[str, object]:
    return {
        "authority_id": authority,
        "market_type": "SPOT",
        "records": records,
        "schema_version": SCHEMA_VERSION,
        "venue": "BINANCE",
    }


def binding(
    value: dict[str, object] | bytes,
    *,
    source_contract_id: str = ALLOWED_SOURCE_CONTRACT_ID,
    payload_id: str = "payload-a",
    policy_id: str = SYNTHETIC_POLICY_ID,
) -> RawPayloadBinding:
    raw = value if type(value) is bytes else canonical(value)
    return RawPayloadBinding(
        raw_bytes=raw,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        source_contract_id=source_contract_id,
        payload_id=payload_id,
        policy_id=policy_id,
    )


def complete_records(*, symbol: str = "FOOUSDT") -> list[dict[str, object]]:
    return [
        record("status", "TRADING_STATUS", "TRADING", symbol=symbol),
        record("permission", "SPOT_PERMISSION", "ENABLED", symbol=symbol),
        record("quote", "QUOTE_ASSET", "USDT", symbol=symbol),
        record("listing", "LISTING_WINDOW", "LISTED", symbol=symbol),
    ]


class HistoricalPITEvidenceTests(unittest.TestCase):
    def build(
        self,
        records: list[dict[str, object]],
        *,
        formation: int = 200,
        symbols: tuple[str, ...] = ("FOOUSDT",),
    ):
        return build_bound_historical_pit_snapshot(
            [binding(payload(records))],
            formation,
            symbols,
            expected_quote_asset="USDT",
        )

    def test_complete_synthetic_snapshot_is_kernel_compatible(self) -> None:
        result = self.build(complete_records())
        decisions = require_pit_eligibility(
            result.snapshot,
            formation_time_ms=200,
            symbols=("FOOUSDT",),
        )
        self.assertTrue(decisions["FOOUSDT"].eligible)
        membership = result.snapshot.memberships[0]
        self.assertEqual(membership.trading_status, TradingStatus.TRADING)
        self.assertEqual(membership.spot_permission, PermissionState.ENABLED)
        self.assertEqual(membership.quote_asset, "USDT")
        self.assertEqual(
            {reference.kind for reference in membership.evidence},
            {
                EvidenceKind.VENUE_MARKET_STATUS,
                EvidenceKind.SPOT_PERMISSION,
                EvidenceKind.QUOTE_ASSET_RULE,
                EvidenceKind.LISTING_WINDOW,
            },
        )
        self.assertFalse(result.policy_info.empirical_authorized)

    def test_evidence_reference_exact_preimage_matches_fixed_vector(self) -> None:
        result = self.build([record("status", "TRADING_STATUS", "TRADING")])
        resolution = next(
            item
            for item in result.resolution_ledger
            if item.component == "TRADING_STATUS"
        )
        active_claims = sorted(
            (
                claim
                for claim in result.claims
                if claim.claim_id in resolution.active_claim_ids
            ),
            key=lambda item: item.claim_id,
        )
        independent_preimage = {
            "active_contributors": [
                {
                    "exact_raw_sha256": claim.raw_payload_sha256,
                    "json_pointer": claim.raw_locator.json_pointer,
                    "payload_id": claim.payload_id,
                    "physical_claim_id": claim.claim_id,
                    "record_fragment_sha256": claim.record_fragment_sha256,
                    "source_contract_id": claim.source_contract_id,
                }
                for claim in active_claims
            ],
            "component": "TRADING_STATUS",
            "formation_time_ms": 200,
            "normalized_value": "TRADING",
            "policy_id": SYNTHETIC_POLICY_ID,
            "symbol": "FOOUSDT",
        }
        independent_bytes = json.dumps(
            independent_preimage,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        independently_computed = hashlib.sha256(independent_bytes).hexdigest()
        reference = next(
            item
            for item in result.snapshot.memberships[0].evidence
            if item.kind is EvidenceKind.VENUE_MARKET_STATUS
        )
        self.assertEqual(
            independently_computed,
            EXPECTED_STATUS_EVIDENCE_REFERENCE_SHA256,
        )
        self.assertEqual(reference.sha256, EXPECTED_STATUS_EVIDENCE_REFERENCE_SHA256)

    def test_missing_and_future_known_claims_resolve_unknown(self) -> None:
        future = record("status", "TRADING_STATUS", "TRADING", published=201)
        result = self.build([future])
        membership = result.snapshot.memberships[0]
        self.assertEqual(membership.trading_status, TradingStatus.UNKNOWN)
        self.assertEqual(len(result.resolution_ledger), 4)
        self.assertTrue(
            all(
                row.reason == "NO_ACTIVE_HISTORICAL_EFFECTIVE_FACT"
                for row in result.resolution_ledger
            )
        )

    def test_nonhistorical_semantics_never_qualify(self) -> None:
        for semantics in (
            "CURRENT_OBSERVATION",
            "PLANNED_SCHEDULE_CLAIM",
            "ARCHIVE_AVAILABILITY",
            "RESPONSE_ABSENCE",
        ):
            with self.subTest(semantics=semantics):
                result = self.build(
                    [record("status", "TRADING_STATUS", "TRADING", semantics=semantics)]
                )
                self.assertEqual(
                    result.snapshot.memberships[0].trading_status,
                    TradingStatus.UNKNOWN,
                )
                self.assertEqual(result.snapshot.memberships[0].evidence, ())

    def test_effective_interval_is_half_open(self) -> None:
        claim = record("status", "TRADING_STATUS", "TRADING", start=100, end=200)
        self.assertEqual(
            self.build([claim], formation=100).snapshot.memberships[0].trading_status,
            TradingStatus.TRADING,
        )
        self.assertEqual(
            self.build([claim], formation=199).snapshot.memberships[0].trading_status,
            TradingStatus.TRADING,
        )
        self.assertEqual(
            self.build([claim], formation=200).snapshot.memberships[0].trading_status,
            TradingStatus.UNKNOWN,
        )

    def test_same_scalar_value_merges_and_different_value_conflicts(self) -> None:
        merged = self.build(
            [
                record("status_a", "TRADING_STATUS", "TRADING", published=40),
                record("status_b", "TRADING_STATUS", "TRADING", published=50),
            ]
        )
        status_resolution = next(
            row for row in merged.resolution_ledger if row.component == "TRADING_STATUS"
        )
        self.assertEqual(status_resolution.component, "TRADING_STATUS")
        self.assertEqual(status_resolution.state, "RESOLVED")
        self.assertEqual(len(status_resolution.active_claim_ids), 2)
        conflict = self.build(
            [
                record("status_a", "TRADING_STATUS", "TRADING", published=40),
                record("status_b", "TRADING_STATUS", "NOT_TRADING", published=50),
            ]
        )
        self.assertEqual(
            conflict.snapshot.memberships[0].trading_status,
            TradingStatus.UNKNOWN,
        )
        self.assertEqual(
            next(
                row for row in conflict.resolution_ledger if row.component == "TRADING_STATUS"
            ).reason,
            "CONFLICTING_ACTIVE_HISTORICAL_EFFECTIVE_FACTS",
        )

    def test_listing_requires_exact_interval_identity(self) -> None:
        same = self.build(
            [
                record("listing_a", "LISTING_WINDOW", "LISTED", published=40),
                record("listing_b", "LISTING_WINDOW", "LISTED", published=50),
            ]
        )
        self.assertIsNotNone(same.snapshot.memberships[0].listing)
        different = self.build(
            [
                record("listing_a", "LISTING_WINDOW", "LISTED", start=100, published=40),
                record("listing_b", "LISTING_WINDOW", "LISTED", start=101, published=50),
            ]
        )
        self.assertIsNone(different.snapshot.memberships[0].listing)

    def test_replace_and_cancel_are_nonretroactive(self) -> None:
        replacement_records = [
            record("old", "TRADING_STATUS", "TRADING", start=0, end=500, published=50),
            record(
                "new",
                "TRADING_STATUS",
                "NOT_TRADING",
                start=0,
                end=500,
                published=150,
                action="REPLACE",
                target="old",
            ),
        ]
        self.assertEqual(
            self.build(replacement_records, formation=100).snapshot.memberships[0].trading_status,
            TradingStatus.TRADING,
        )
        self.assertEqual(
            self.build(replacement_records, formation=200).snapshot.memberships[0].trading_status,
            TradingStatus.NOT_TRADING,
        )
        cancellation_records = [
            record("old", "TRADING_STATUS", "TRADING", start=0, end=500, published=50),
            record(
                "cancel",
                "TRADING_STATUS",
                None,
                start=None,
                end=None,
                published=150,
                action="CANCEL",
                target="old",
            ),
        ]
        self.assertEqual(
            self.build(cancellation_records, formation=100).snapshot.memberships[0].trading_status,
            TradingStatus.TRADING,
        )
        self.assertEqual(
            self.build(cancellation_records, formation=200).snapshot.memberships[0].trading_status,
            TradingStatus.UNKNOWN,
        )

    def test_revision_dangling_cycle_branch_and_tie_fail_whole_call(self) -> None:
        cases = {
            "dangling": [
                record(
                    "replacement",
                    "TRADING_STATUS",
                    "TRADING",
                    action="REPLACE",
                    target="missing",
                )
            ],
            "cycle": [
                record(
                    "a",
                    "TRADING_STATUS",
                    "TRADING",
                    published=20,
                    action="REPLACE",
                    target="b",
                ),
                record(
                    "b",
                    "TRADING_STATUS",
                    "TRADING",
                    published=10,
                    action="REPLACE",
                    target="a",
                ),
            ],
            "branch": [
                record("root", "TRADING_STATUS", "TRADING", published=10),
                record(
                    "a",
                    "TRADING_STATUS",
                    "TRADING",
                    published=20,
                    action="REPLACE",
                    target="root",
                ),
                record(
                    "b",
                    "TRADING_STATUS",
                    "TRADING",
                    published=30,
                    action="REPLACE",
                    target="root",
                ),
            ],
            "tie": [
                record("a", "TRADING_STATUS", "TRADING", published=10),
                record("b", "TRADING_STATUS", "TRADING", published=10),
            ],
        }
        for name, records in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(HistoricalPITEvidenceError):
                    self.build(records)

    def test_revision_may_cross_bound_sources_with_same_frozen_lineage(self) -> None:
        first = binding(
            payload([record("root", "TRADING_STATUS", "TRADING", published=10)]),
            payload_id="payload-root",
        )
        second = binding(
            payload(
                [
                    record(
                        "next",
                        "TRADING_STATUS",
                        "NOT_TRADING",
                        published=20,
                        action="REPLACE",
                        target="root",
                    )
                ]
            ),
            payload_id="payload-replacement",
        )
        result = build_bound_historical_pit_snapshot(
            [first, second],
            200,
            ("FOOUSDT",),
            expected_quote_asset="USDT",
        )
        self.assertEqual(
            result.snapshot.memberships[0].trading_status,
            TradingStatus.NOT_TRADING,
        )

    def test_policy_authority_contract_and_legacy_aliases_fail_closed(self) -> None:
        legacy_records = (
            [record("legacy_component", "STATUS", "TRADING")],
            [
                record(
                    "legacy_semantics",
                    "TRADING_STATUS",
                    "TRADING",
                    semantics="HISTORICAL_FACT",
                )
            ],
            [
                record(
                    "legacy_absence",
                    "TRADING_STATUS",
                    "TRADING",
                    semantics="ABSENCE_INFERENCE",
                )
            ],
        )
        candidates = [
            binding(payload(complete_records(), authority="SYNTHETIC_AUTHORITY")),
            binding(
                payload(complete_records()),
                source_contract_id="SYNTHETIC_CONTRACT_A",
            ),
            binding(payload(complete_records()), policy_id="UNKNOWN_POLICY"),
            *(binding(payload(items)) for items in legacy_records),
        ]
        for candidate in candidates:
            with self.subTest(payload_id=candidate.payload_id):
                with self.assertRaises(HistoricalPITEvidenceError):
                    build_bound_historical_pit_snapshot(
                        [candidate],
                        200,
                        ("FOOUSDT",),
                        expected_quote_asset="USDT",
                    )

    def test_valid_payload_plus_bad_unrequested_payload_fails_whole_call(self) -> None:
        bad_record = record(
            "bar_status", "TRADING_STATUS", "TRADING", symbol="BARUSDT"
        )
        bad_record["unknown"] = "forbidden"
        with self.assertRaises(HistoricalPITEvidenceError):
            build_bound_historical_pit_snapshot(
                [
                    binding(payload(complete_records()), payload_id="valid"),
                    binding(payload([bad_record]), payload_id="bad-unrequested"),
                ],
                200,
                ("FOOUSDT",),
                expected_quote_asset="USDT",
            )

    def test_duplicate_payload_id_fails_whole_call(self) -> None:
        with self.assertRaises(HistoricalPITEvidenceError):
            build_bound_historical_pit_snapshot(
                [
                    binding(payload(complete_records()), payload_id="duplicate"),
                    binding(payload([]), payload_id="duplicate"),
                ],
                200,
                ("FOOUSDT",),
                expected_quote_asset="USDT",
            )

    def test_cross_payload_conflict_is_explicit_unknown(self) -> None:
        result = build_bound_historical_pit_snapshot(
            [
                binding(
                    payload(
                        [record("status_a", "TRADING_STATUS", "TRADING", published=40)]
                    ),
                    payload_id="payload-a",
                ),
                binding(
                    payload(
                        [
                            record(
                                "status_b",
                                "TRADING_STATUS",
                                "NOT_TRADING",
                                published=50,
                            )
                        ]
                    ),
                    payload_id="payload-b",
                ),
            ],
            200,
            ("FOOUSDT",),
            expected_quote_asset="USDT",
        )
        self.assertEqual(
            result.snapshot.memberships[0].trading_status,
            TradingStatus.UNKNOWN,
        )

    def test_assert_replace_cancel_chain_across_formations(self) -> None:
        raw_bindings = [
            binding(
                payload(
                    [
                        record(
                            "asserted",
                            "TRADING_STATUS",
                            "TRADING",
                            start=0,
                            end=500,
                            published=50,
                        )
                    ]
                ),
                payload_id="assertion",
            ),
            binding(
                payload(
                    [
                        record(
                            "replacement",
                            "TRADING_STATUS",
                            "NOT_TRADING",
                            start=0,
                            end=500,
                            published=100,
                            action="REPLACE",
                            target="asserted",
                        )
                    ]
                ),
                payload_id="replacement",
            ),
            binding(
                payload(
                    [
                        record(
                            "cancellation",
                            "TRADING_STATUS",
                            None,
                            start=None,
                            end=None,
                            published=200,
                            action="CANCEL",
                            target="replacement",
                        )
                    ]
                ),
                payload_id="cancellation",
            ),
        ]
        observed = []
        for formation in (75, 150, 250):
            result = build_bound_historical_pit_snapshot(
                raw_bindings,
                formation,
                ("FOOUSDT",),
                expected_quote_asset="USDT",
            )
            observed.append(result.snapshot.memberships[0].trading_status)
        self.assertEqual(
            observed,
            [TradingStatus.TRADING, TradingStatus.NOT_TRADING, TradingStatus.UNKNOWN],
        )

    def test_nonhistorical_terminal_revision_creates_unknown_not_negative(self) -> None:
        result = self.build(
            [
                record(
                    "root",
                    "TRADING_STATUS",
                    "TRADING",
                    start=0,
                    end=500,
                    published=50,
                ),
                record(
                    "current_only",
                    "TRADING_STATUS",
                    "NOT_TRADING",
                    start=0,
                    end=500,
                    published=100,
                    semantics="CURRENT_OBSERVATION",
                    action="REPLACE",
                    target="root",
                ),
            ]
        )
        self.assertEqual(
            result.snapshot.memberships[0].trading_status,
            TradingStatus.UNKNOWN,
        )

    def test_all_four_components_conflict_and_listing_uses_exact_triple(self) -> None:
        cases = (
            (
                "TRADING_STATUS",
                record("a", "TRADING_STATUS", "TRADING", published=40),
                record("b", "TRADING_STATUS", "NOT_TRADING", published=50),
            ),
            (
                "SPOT_PERMISSION",
                record("a", "SPOT_PERMISSION", "ENABLED", published=40),
                record("b", "SPOT_PERMISSION", "DISABLED", published=50),
            ),
            (
                "QUOTE_ASSET",
                record("a", "QUOTE_ASSET", "USDT", published=40),
                record("b", "QUOTE_ASSET", "USDC", published=50),
            ),
            (
                "LISTING_WINDOW",
                record("a", "LISTING_WINDOW", "LISTED", start=100, published=40),
                record("b", "LISTING_WINDOW", "LISTED", start=101, published=50),
            ),
        )
        for component, first, second in cases:
            with self.subTest(component=component):
                result = self.build([first, second])
                resolution = next(
                    item for item in result.resolution_ledger if item.component == component
                )
                self.assertEqual(resolution.state, "UNKNOWN")
                self.assertEqual(
                    resolution.reason,
                    "CONFLICTING_ACTIVE_HISTORICAL_EFFECTIVE_FACTS",
                )
        listing = self.build(
            [record("listing", "LISTING_WINDOW", "LISTED", start=100, end=300)]
        )
        resolution = next(
            item
            for item in listing.resolution_ledger
            if item.component == "LISTING_WINDOW"
        )
        self.assertEqual(resolution.normalized_value, ("LISTED", 100, 300))

    def test_invalid_raw_integrity_and_json_contract_fail_whole_call(self) -> None:
        good = binding(payload(complete_records()))
        bad_hash = RawPayloadBinding(
            raw_bytes=good.raw_bytes,
            expected_sha256="0" * 64,
            source_contract_id=good.source_contract_id,
            payload_id=good.payload_id,
        )
        bad_values = [
            bad_hash,
            binding(b"\xff"),
            binding(b'{"x":1,"x":2}'),
            binding(b'{"schema_version":"x","authority_id":"a","venue":"BINANCE","market_type":"SPOT","records":[],"extra":0}'),
            binding(b'{"schema_version":"BINANCE_HISTORICAL_PIT_EVIDENCE_V1","authority_id":"a","venue":"BINANCE","market_type":"SPOT","records":[],"x":NaN}'),
            binding(
                b'{"schema_version":"BINANCE_HISTORICAL_PIT_EVIDENCE_V1","authority_id":"\\ud800","venue":"BINANCE","market_type":"SPOT","records":[]}'
            ),
        ]
        for candidate in bad_values:
            with self.subTest(raw=candidate.raw_bytes[:30]):
                with self.assertRaises(HistoricalPITEvidenceError):
                    build_bound_historical_pit_snapshot(
                        [candidate],
                        200,
                        ("FOOUSDT",),
                        expected_quote_asset="USDT",
                    )

    def test_record_unknown_field_and_invalid_value_fail_whole_call(self) -> None:
        unknown = record("status", "TRADING_STATUS", "TRADING")
        unknown["extra"] = True
        invalid = record("status", "TRADING_STATUS", "HALTED")
        for candidate in ([unknown], [invalid]):
            with self.assertRaises(HistoricalPITEvidenceError):
                self.build(candidate)

    def test_unclaimed_requested_symbol_gets_explicit_unknown_membership(self) -> None:
        result = build_bound_historical_pit_snapshot(
            [binding(payload(complete_records()))],
            200,
            ("BARUSDT", "FOOUSDT"),
            expected_quote_asset="USDT",
        )
        self.assertEqual(
            [membership.symbol for membership in result.snapshot.memberships],
            ["BARUSDT", "FOOUSDT"],
        )
        unknown = result.snapshot.memberships[0]
        self.assertEqual(unknown.trading_status, TradingStatus.UNKNOWN)
        self.assertEqual(unknown.spot_permission, PermissionState.UNKNOWN)
        self.assertIsNone(unknown.quote_asset)
        self.assertIsNone(unknown.listing)
        self.assertEqual(unknown.evidence, ())

    def test_symbol_suffix_never_infers_quote_asset(self) -> None:
        result = self.build([record("status", "TRADING_STATUS", "TRADING")])
        membership = result.snapshot.memberships[0]
        self.assertEqual(membership.symbol, "FOOUSDT")
        self.assertIsNone(membership.quote_asset)
        self.assertEqual(
            next(
                row for row in result.resolution_ledger if row.component == "QUOTE_ASSET"
            ).state,
            "UNKNOWN",
        )

    def test_record_reorder_preserves_semantics_but_changes_raw_bound_identity(self) -> None:
        records_a = complete_records()
        records_b = list(reversed(records_a))
        single_a = build_bound_historical_pit_snapshot(
            [binding(payload(records_a))],
            200,
            ("FOOUSDT",),
            expected_quote_asset="USDT",
        )
        single_b = build_bound_historical_pit_snapshot(
            [binding(payload(records_b))],
            200,
            ("FOOUSDT",),
            expected_quote_asset="USDT",
        )
        self.assertEqual(
            single_a.semantic_resolution_sha256,
            single_b.semantic_resolution_sha256,
        )
        self.assertNotEqual(
            single_a.snapshot.artifact_sha256,
            single_b.snapshot.artifact_sha256,
        )
        references_a = {item.kind: item.sha256 for item in single_a.snapshot.memberships[0].evidence}
        references_b = {item.kind: item.sha256 for item in single_b.snapshot.memberships[0].evidence}
        self.assertNotEqual(
            references_a[EvidenceKind.VENUE_MARKET_STATUS],
            references_b[EvidenceKind.VENUE_MARKET_STATUS],
        )

    def test_binding_list_order_does_not_change_final_snapshot_sha(self) -> None:
        records_a = complete_records()

        left = binding(
            payload(records_a[:2]), payload_id="payload-left"
        )
        right = binding(
            payload(records_a[2:]), payload_id="payload-right"
        )
        ordered = build_bound_historical_pit_snapshot(
            [left, right],
            200,
            ("FOOUSDT",),
            expected_quote_asset="USDT",
        )
        reversed_bindings = build_bound_historical_pit_snapshot(
            [right, left],
            200,
            ("FOOUSDT",),
            expected_quote_asset="USDT",
        )
        self.assertEqual(
            ordered.snapshot.artifact_sha256,
            reversed_bindings.snapshot.artifact_sha256,
        )

    def test_unrelated_raw_format_change_does_not_change_active_reference(self) -> None:
        selected = binding(
            payload([record("status", "TRADING_STATUS", "TRADING")]),
            payload_id="selected",
        )
        empty_payload = payload([])
        compact = binding(empty_payload, payload_id="unrelated")
        pretty_raw = (json.dumps(empty_payload, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        pretty = binding(pretty_raw, payload_id="unrelated")
        first = build_bound_historical_pit_snapshot(
            [selected, compact],
            200,
            ("FOOUSDT",),
            expected_quote_asset="USDT",
        )
        second = build_bound_historical_pit_snapshot(
            [selected, pretty],
            200,
            ("FOOUSDT",),
            expected_quote_asset="USDT",
        )
        self.assertEqual(
            first.semantic_resolution_sha256,
            second.semantic_resolution_sha256,
        )
        self.assertNotEqual(
            first.snapshot.artifact_sha256,
            second.snapshot.artifact_sha256,
        )
        first_status = next(
            item
            for item in first.snapshot.memberships[0].evidence
            if item.kind is EvidenceKind.VENUE_MARKET_STATUS
        )
        second_status = next(
            item
            for item in second.snapshot.memberships[0].evidence
            if item.kind is EvidenceKind.VENUE_MARKET_STATUS
        )
        self.assertEqual(first_status.sha256, second_status.sha256)

    def test_claim_has_locator_fragment_hash_and_stable_identity(self) -> None:
        result = self.build([record("status", "TRADING_STATUS", "TRADING")])
        claim = result.claims[0]
        self.assertEqual(
            claim.raw_locator,
            RawRecordLocator(
                source_contract_id=ALLOWED_SOURCE_CONTRACT_ID,
                payload_id="payload-a",
                raw_payload_sha256=result.raw_payloads[0].exact_raw_sha256,
                json_pointer="/records/0",
            ),
        )
        replayed = replay_bound_record_fragment(
            [binding(payload([record("status", "TRADING_STATUS", "TRADING")]))],
            claim.raw_locator,
        )
        self.assertEqual(hashlib.sha256(replayed).hexdigest(), claim.record_fragment_sha256)
        self.assertEqual(claim.payload_id, "payload-a")
        self.assertEqual(claim.raw_payload_sha256, result.raw_payloads[0].exact_raw_sha256)
        self.assertRegex(claim.record_fragment_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(claim.claim_id, r"^[0-9a-f]{64}$")
        self.assertEqual(claim.published_at_ms, 50)

    def test_empirical_boundary_rejects_snapshot_and_all_v1_bindings(self) -> None:
        raw_bindings = [binding(payload(complete_records()))]
        result = build_bound_historical_pit_snapshot(
            raw_bindings,
            200,
            ("FOOUSDT",),
            expected_quote_asset="USDT",
        )
        with self.assertRaises(EmpiricalPITAuthorizationError):
            require_trusted_empirical_pit_eligibility(
                result.snapshot,
                formation_time_ms=200,
                requested_symbols=("FOOUSDT",),
            )
        with self.assertRaises(EmpiricalPITAuthorizationError):
            require_trusted_empirical_pit_eligibility(
                raw_bindings,
                formation_time_ms=200,
                requested_symbols=("FOOUSDT",),
            )

    def test_scope_arguments_are_explicit_and_fail_closed(self) -> None:
        raw_bindings = [binding(payload(complete_records()))]
        for kwargs in (
            {"requested_symbols": (), "expected_quote_asset": "USDT"},
            {
                "requested_symbols": ("FOOUSDT", "FOOUSDT"),
                "expected_quote_asset": "USDT",
            },
            {"requested_symbols": ("FOO USDT",), "expected_quote_asset": "USDT"},
            {"requested_symbols": ("FOOUSDT",), "expected_quote_asset": ""},
            {
                "requested_symbols": ("FOOUSDT",),
                "expected_market_type": MarketType.MARGIN,
                "expected_quote_asset": "USDT",
            },
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HistoricalPITEvidenceError):
                    build_bound_historical_pit_snapshot(raw_bindings, 200, **kwargs)


if __name__ == "__main__":
    unittest.main()
