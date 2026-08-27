# exp_20260827_004 — BOUND_HISTORICAL_PIT_EVIDENCE_ADAPTER_V1

## Observation

The existing hierarchical Alpha kernel has a fail-closed synthetic PIT eligibility gate, but it does not bind eligibility evidence to exact raw payloads, external hashes, source contracts, revision lineage, or deterministic resolution ledgers.

## Single hypothesis

A data-agnostic, offline adapter can strictly parse externally hash-bound historical evidence claims under one exact synthetic authority/source contract, apply non-retroactive revision and conflict rules, and produce both an order-stable semantic resolution digest and an exact-raw-bound `PITEligibilitySnapshot` compatible with the existing synthetic gate without authorizing any empirical dataset.

## Primary change

Add one lower-level bound-evidence adapter and one empirical entry point that is permanently fail-closed for the V1 policy registry. The only registered policy is `SYNTHETIC_BINANCE_HISTORICAL_PIT_FIXTURE_V1`, allowing only authority `SYNTHETIC_BINANCE_OFFICIAL_FIXTURE` and source contract `SYNTHETIC_BINANCE_HISTORICAL_PIT_SOURCE_CONTRACT_V1`, with `empirical_authorized=false`.

The fresh Phase 1 amendment replaces the prior pre-formal candidate's single canonical artifact identity with two explicit layers: a physical-provenance-free `semantic_resolution_sha256`, and a final snapshot SHA over requested scope, the semantic digest, and a `payload_id`-sorted manifest of exact raw SHA-256 values. The prior candidate hashes and Phase2 NO-GO reasons remain recorded in `logs/phase2_no_go_contract_amendment.txt`.

## Acceptance

- Strict raw-byte hash, UTF-8, JSON, schema, lineage, and requested-scope validation fails closed.
- Complete synthetic four-component evidence can pass the existing synthetic primitive gate.
- Missing, conflicting, future-known, current-only, planned, archive, and absence claims cannot create historical eligibility.
- Semantic identity is deterministic under record permutations; the final snapshot identity changes whenever any exact bound raw bytes change and remains invariant only to binding-list order.
- Every provenance claim binds `payload_id`, exact raw SHA, record-fragment SHA, and a composite replay locator.
- The empirical API rejects every V1 input and does not accept a prebuilt snapshot.
- New targeted tests and the existing 35 hierarchical-kernel tests pass.

## Semantic ceiling

This experiment uses synthetic payloads only. It does not establish a real historical universe, does not open the empirical eligibility gate, and does not authorize factor research, IC, ML, P&L, or backtesting. A successful development result remains `NEEDS_MORE_DATA`.

## Pre-registration state

`PREFORMAL_AWAITING_PHASE2 / NEEDS_MORE_DATA`; formal execution and network access remain forbidden pending a fresh independent Phase 2 review.
