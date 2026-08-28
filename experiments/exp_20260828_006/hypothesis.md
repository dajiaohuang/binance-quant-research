# exp_20260828_006 — offline recovery of the exp005 J-Quants source probe

## Observation

`exp_20260828_005_formal_001` retained five raw response bodies, five safe
receipts, the exact query plan, an acquisition manifest, a summary, staging and
control evidence. Its final publication was correctly withheld because the
postflight pacing check compared receipt wall-clock milliseconds with a runtime
13-second monotonic-clock guard. Two observed wall-clock gaps were 6 ms and 1 ms
below 13,000 ms, so that check is not replayable as proof of the runtime clock.

## Falsifiable hypothesis

The exact source-bound exp005 staging can be revalidated offline for response
integrity, receipt/raw bijection, schema, fixed-query semantics and hashes, then
exposed through a separate recovery manifest without replaying the runtime
monotonic clock and without promoting the exp005 final path.

## Single principal change

Add an independent, read-only recovery adapter which accepts only the exact
exp005 staging path and raw-tree SHA-256. It validates all five source responses
and receipts, but records pacing as
`RUNTIME_MONOTONIC_GUARD_INFERRED_NOT_REPLAYABLE`, never `PASS`.

## Failure conditions

- any raw response, safe receipt, sidecar, query plan, source summary, control
  record or acquisition manifest is missing or hash-inconsistent;
- response/receipt paths are not bijective;
- any query, page, schema, date mapping, split expectation, HTTP status,
  content type, redirect count or retry count differs from the frozen contract;
- the validator reads an API key, accesses the network, copies licensed rows
  into Git-safe artifacts, or mutates exp005 source bytes;
- recovery is presented as exp005 final promotion or as a replayable pacing
  PASS;
- listing presence, historical eligibility, training, IC, P&L or backtesting is
  authorized.

## Success ceiling

`JQUANTS_V2_FREE_SOURCE_PROBE_OFFLINE_RECOVERED / NEEDS_MORE_DATA`.
