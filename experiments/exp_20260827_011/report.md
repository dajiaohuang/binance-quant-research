# exp_20260827_011

Pre-registered Phase1 implementation of an independent typed SSPT clean-room contract. The experiment is synthetic-only and starts in `PLANNED` state.

Authority is *Pre-training Time Series Models with Stock Data Customization* (`arXiv:2506.16746`, DOI `10.1145/3711896.3737005`, KDD 2025). The repository identity `a2940e4eac7202d2d8c1dfc1e88fa3c811485b8a` is recorded only as unlicensed interface evidence: its source, scripts, pickle files, and market payloads are not copied, imported, executed, or downloaded. A possible repository URL redirect does not change the fixed commit identity or the no-license boundary.

All five reported datasets remain `NO_GO_NO_LICENSE_OR_PROVENANCE`. This experiment cannot authorize empirical Alpha, IC, P&L, backtesting, historical eligibility, or data conclusions.

## Phase1 development result

The independent `sspt_v2` namespace now implements the typed synthetic contract: a self-verifying market calendar and same-day cross-section clock; complete 30-session warmup for the frozen 25-feature order; fit-once typed TRAIN-only min-max state; immediate-next-session return identity; SCC stable symbol identity and formation-known SSC sector registries; deterministic exact-top-k SHA-256 MAP masks with the full pre-mask raw-close-window mean target; three-head pretraining; full-cross-section Equation 5; four freeze modes; deterministic eval/inference; and an atomic two-file safetensors checkpoint that binds config, registry, scaler, freeze, implementation tree, source contract, schema, parameters, tensor manifest, hashes, and finiteness.

The initial targeted run retained 11 development errors caused by three implementation mistakes. After minimal repairs, the first development suite passed 23/23. Final `py_compile` passed. Separate CPU and CUDA development smokes each completed one three-head pretrain update, one frozen fine-tune update, deterministic inference, and strict checkpoint roundtrip. CUDA peak allocation was 76,795,392 bytes, below the frozen 2 GiB cap. These are contract smokes, not performance or model-quality evidence.

## Phase2 NO-GO and repair

The first Phase2 review rejected the candidate because the smoke implementation-tree projection omitted file byte counts and therefore disagreed with the frozen tree, and because CUDA inference evidence was produced only after moving the trained model to CPU. The same review round also required external checkpoint content identity, exact `MAP_TO_ZERO` handling for constant scaler columns, and rejection of fractional, boolean, or negative public integer arrays before conversion. The original rejection is retained in `logs/phase2_no_go_implementation_tree_gpu.txt`.

The repaired implementation now computes the implementation tree from the complete UTF-8-path-sorted canonical list of exact `path/bytes/sha256` rows and revalidates all seven source files at smoke start and immediately before checkpoint publication. `CheckpointBindings` receives that externally frozen value. External loads require both the frozen manifest ID and weights SHA; a self-consistent attacker rewrite of safetensors plus all internal manifest hashes is rejected by the unchanged external identity. Constant training columns always map to zero, and public timestamp, known-at, and class-target arrays accept only nonnegative integer dtypes.

The repaired CUDA smoke performs two finite, bitwise-identical eval/inference calls while the model remains on `cuda:0`, then moves to CPU for the strict checkpoint roundtrip. The fresh targeted development run passed 28/28. A preceding 27/28 run failed only because the test's known-vector expected SHA accidentally contained 65 characters; that failure is retained in `logs/targeted_phase2_repair_initial_failure.txt`.

## Formal execution

After independent Fresh Phase2 FINAL GO, the frozen candidate command ran exactly once with no retry. It exited 0 and reported 28 tests run, 28 passed, zero failed, and zero skipped. The contemporaneous execution ledger binds UTC start/end, the exact command SHA, process wall time, complete empty stdout and complete stderr, and their hashes. Postflight rechecked all 16 frozen rows and the seven-file implementation tree without drift. Full repository discovery remained `NOT_RUN` as required.

Immediately after formal execution and before independent review, the then-current self-check status was `FORMAL_COMPLETE_POSTFLIGHT_PENDING_INDEPENDENT_AUDIT`. The semantic ceiling remained `NEEDS_MORE_DATA`: this proves only the frozen synthetic typed-method contract and does not establish empirical model quality, Alpha, IC, P&L, eligibility, or backtest validity.

Full repository discovery was intentionally `NOT_RUN`: the formal authorization covered only the frozen targeted contract. Formal execution count is one. Network requests, retries, real-data reads, upstream source execution, payload downloads, and pickle loads all remain zero.

At that pre-audit point, status was `FORMAL_COMPLETE_POSTFLIGHT_PENDING_INDEPENDENT_AUDIT`; terminal ceiling remained `NEEDS_MORE_DATA`. Historical eligibility was unavailable and empirical authorization was false.

## Independent audit closure

Independent postflight review returned `FINAL PASS`. The append-only closure is `artifacts/independent_audit_closure.json` (1271 bytes, SHA-256 `33f7169de0a8e461c7755bf862e382d4a7f2c6e112c3b8ea0de9dd6483cdebcf`). It was created after formal execution and does not alter the formal ledger, result, record index, frozen source tree, tests, or retained development failures. Status is now `POSTFLIGHT_INDEPENDENT_AUDIT_PASSED`; the artifact and terminal ceilings remain `SYNTHETIC_TYPED_METHOD_CONTRACT_FORMAL_VERIFIED / NEEDS_MORE_DATA`.
