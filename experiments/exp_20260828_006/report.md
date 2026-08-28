# exp_20260828_006 offline recovery report

## Outcome

`JQUANTS_V2_FREE_SOURCE_PROBE_OFFLINE_RECOVERED / NEEDS_MORE_DATA`

The exact exp005 staging was recovered as a separate, read-only source view. It
was not promoted into the exp005 final path. No API key was read, no network
request was made, and no licensed raw row was copied into a Git-safe artifact.

## Source-bound result

- Five raw response bodies and five safe sidecar receipts are bijective.
- All five receipts record direct HTTP 200, `application/json`, zero redirects,
  zero retries and no pagination key.
- Every body byte count and SHA-256 matches its acquisition-manifest entry and
  receipt. The raw-tree SHA-256 is
  `d14273cc49e9de82b0295e9ab76db8c01eea02e5f4e0af940400b64550b8209c`.
- The fixed plan, source summary, run identity, reservation, stage ledger,
  failure record and lease all match their exact source bindings.
- Staging and failure control exist. Final publication and authorization do not.

The schema/query reconstruction yielded four calendar rows, one distinct merged
master row and 4,414 distinct merged daily bars. Q04 contains 4,410 symbols and
188 null-OHLC rows; the five-session KDDI range and preregistered split/ex-right
expectation both match. The non-trading master request maps to the next business
date as preregistered, and both sector classifications are nonempty.

## Pacing interpretation

The wall-clock receipt gaps are 13,008, 13,001, 12,994 and 12,999 ms. These
millisecond timestamps cannot exactly replay the runtime monotonic-clock guard.
Pacing is therefore
`RUNTIME_MONOTONIC_GUARD_INFERRED_NOT_REPLAYABLE`, not `PASS`. Recovery depends
on semantic and cryptographic evidence, not on rewriting the failed pacing
postflight.

## Verification

- Offline recovery command: `PASS`.
- Targeted tests: `12/12 PASS`, zero failures, errors or skips.
- Python compilation: `PASS`.
- Frozen evidence: `10/10` byte and SHA-256 matches; freeze-manifest SHA-256
  `d443e45f59a545609a3dec713b53c9ce0d8898ac541fb4f4fa1d071f06bb3928`.
- All 13 JSON files across exp005/exp006 parse successfully; the three
  Git-safe recovery artifacts have zero key/header-pattern hits.
- Full repository suite: not run; this experiment was deliberately limited to
  the source-bound recovery surface.

Two development attempts failed before the successful run: the first assumed
the reservation used pretty JSON rather than canonical compact JSON; the second
compared master dataclasses including distinct receipt times instead of only
their source identity fields. Both were corrected before the successful
targeted run and are retained in the development log.

## Authorization boundary

This result validates a narrow Free-plan source probe only. Code-filtered master
queries cannot establish historical listing presence, so listing remains
`UNKNOWN`, strict eligible count remains zero, and historical eligibility is not
ready. Training, inference, IC, P&L and backtesting remain unauthorized.

The independent offline audit returned `PASS` and ran the targeted offline
suite exactly once (`PASS_12_OF_12_ONCE`). The root-captured audit evidence is
stored at `logs/independent_offline_audit.txt`, SHA-256
`40c48ce2dc783804031da6fd39294525a0798caa093bac3316661071f82be0f8`.
This is an evidence binding, not a cryptographic signature. The separate closure
manifest has SHA-256
`4e8488fa3ca8ec5636093edca43b1a709f8077803bac4f10e80951e726f98bf6`;
the original ten-file candidate freeze remains unchanged.

The allowed claim remains exactly
`JQUANTS_V2_FREE_SOURCE_PROBE_OFFLINE_RECOVERED / NEEDS_MORE_DATA`. Exp005 final
promotion, replayable pacing proof, historical listing eligibility/PIT universe,
and training/inference/IC/P&L/backtest claims remain prohibited. No broad or
full-suite change is authorized by this closure.
