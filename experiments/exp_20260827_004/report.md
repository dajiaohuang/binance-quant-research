# exp_20260827_004 report

## Status

`terminal_postflight_pending_auditor / NEEDS_MORE_DATA`

The experiment is limited to an offline, data-agnostic historical PIT evidence adapter. Fresh Phase 2 authorized exactly two frozen offline test commands; both have now run once. No network request, environment credential, real payload, factor, IC, ML, P&L, or backtest was used.

## Planned evidence

- Exact raw bytes are checked against an external SHA-256 before parsing.
- Strict JSON and exact schemas reject duplicate keys, non-finite numbers, invalid UTF-8, surrogates, and unknown fields.
- Revision lineage is non-retroactive and fail-closed.
- Resolution explicitly preserves missing and conflict reasons for every requested symbol/component.
- The existing kernel gate is used only as a synthetic compatibility primitive.
- The empirical entry point remains closed because the sole V1 policy is not empirically authorized.

## Offline development result

The amended lower-level adapter and empirical fail-closed boundary are implemented. Fresh targeted tests passed 27/27; the unchanged hierarchical kernel tests passed 35/35; both new Python files passed bytecode compilation. The original candidate's test-only indexing failure remains in `logs/targeted_initial_failure.txt`. The first amended compile run exposed a stray indented fragment left by an intermediate patch; that complete failure is retained in `logs/micro_amendment_initial_failure.txt` and was corrected by deleting only the orphaned lines.

The prior frozen candidate received Phase2 NO-GO because it did not bind an exact authority/source contract and conflated semantic order invariance with exact raw-byte identity. Its old hashes and rejection reasons are preserved in `logs/phase2_no_go_contract_amendment.txt`; this report does not represent the amendment as having passed on its first attempt.

A subsequent read-only Phase2 fidelity review found one canonical terminology mismatch in the `EvidenceReference` preimage. Option A changed only the preimage envelope to `active_contributors` and contributor keys to `exact_raw_sha256` and `physical_claim_id`, preserving their existing values. The rejected hashes and reason are retained in `logs/phase2_fidelity_no_go_option_a.txt`. An independent test-side canonical JSON calculation now matches the fixed vector `52e6bdf8e9ffbb2eb6b793fd4b28e2265f098bf88449840be5af09079b207f63`.

One complete four-component synthetic fixture produced an eligibility decision only through the existing synthetic primitive gate. This is compatibility evidence, not a real eligibility result. The sole V1 policy remains `empirical_authorized=false`, and the empirical entry point rejects it after building and validating the bound snapshot.

## Formal offline evidence result

Preflight recalculated the eight frozen file hashes and the two exact no-newline command hashes; all matched. Command 1, `uv run python -m unittest tests.test_historical_pit_evidence -v`, ran exactly once and passed 27/27 with exit 0. Only after that pass, command 2, `uv run python -m unittest tests.test_hierarchical_alpha -v`, ran exactly once and passed 35/35 with exit 0. Postflight recalculated the same frozen hashes and all remained unchanged. There was no retry.

The complete captured streams, observations, and timings are in `logs/formal_command_001.txt` and `logs/formal_command_002.txt`; `formal_ledger.json` and `formal_result.json` bind the logs and results. The full-repository test suite was explicitly `NOT_RUN`, so this report does not claim a repository-wide pass.

This formal evidence is only an offline contract and compatibility test result. No network access, environment read, real payload read, eligibility evaluation, or empirical authorization occurred. The maximum permitted state is `NEEDS_MORE_DATA`; `historical_eligibility_ready=false`, `eligibility_evaluated=false`, `empirical_authorized=false`, and `strict_eligible_count=0` remain fixed pending independent postflight audit.

The stale documentation in `exp_20260827_003` is recorded as independent debt only. It was not read as an input to this experiment and is not evidence for this result.

## Known limits and Phase 2 material

- Raw bytes are externally hash-verified. Every binding has a unique canonical `payload_id`; every full provenance claim carries its exact raw SHA and composite replay locator.
- `semantic_resolution_sha256` excludes physical provenance and is invariant to physical record order. The final snapshot SHA binds the semantic digest plus a payload-id-sorted exact raw manifest, so any whitespace, key-order, or record-order byte change changes the final snapshot SHA. Binding-list order alone is invariant.
- Each resolved `EvidenceReference` binds only its actual active contributors, including source contract, payload ID, exact raw SHA, locator, fragment SHA, and physical claim ID. Unrelated payload formatting changes therefore do not alter that reference.
- The adapter recognizes only the synthetic V1 policy and exact synthetic authority/source contract. Adding an empirical policy requires a separate source contract and independent experiment; changing the registry is not authorized here.
- No CLI or pyproject entry point was added. Independent postflight audit should verify the frozen module, tests, contract, schema, parameters, two exact offline command hashes, and the bound formal logs/results.

## Referenced Skills

| Skill | Purpose | Local path |
|---|---|---|
| quant-strategy-research | Pre-registration, fail-closed evidence design, test and experiment-record discipline | `.codex/skills/quant-strategy-research/SKILL.md` |
