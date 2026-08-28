# exp_20260828_007 — resumable J-Quants Free all-market daily-bars contract

## Observation

The recovered Free source probe proves that official J-Quants V2 calendar and
all-market daily-bar responses can be retained and validated, but the probe is
not a resumable historical acquisition system. Its wall-clock receipt fields
also cannot replay the runtime monotonic pacing guard.

## Falsifiable hypothesis

A new independent package can establish a raw-first, exact-once and resumable
Free all-market daily-bars acquisition contract whose bootstrap calendar and
boundary leaves become reusable data, while persisting replayable integer
`monotonic_ns` pacing evidence and keeping every licensed row outside Git.

## Single principal change

Implement `jquants_v2_bars_monthly_v1` without modifying the frozen v4,
recovery, exp005 or exp006 artifacts. The frozen bootstrap plan is:

1. `GET /v2/markets/calendar?from=2024-07-01&to=2026-05-29`;
2. `GET /v2/equities/bars/daily?date=2024-07-01`;
3. `GET /v2/equities/bars/daily?date=2026-05-29`.

The calendar must cover exactly 698 ordered civil dates, use only `HolDiv`
values `0`–`3`, contain 450–475 TSE sessions (`1` or `2`), and generate exactly
23 immutable monthly plans. Both edge-day responses are acquisition leaves and
must be reused by later monthly attempts.

Monthly collection is scaffolded only. It remains network-disabled until a
successful bootstrap supplies and freezes the session list. Each monthly batch
uses one month and one permanent attempt ID, runs oldest-first, reuses verified
prior leaves by hash, stops on the first failure, and requires a new attempt ID
for repair.

## Failure conditions

- any response is parsed before its raw body and safe receipt are durable;
- a key, authentication header, credential-bearing URL or licensed row enters a
  tracked artifact or log;
- endpoint, host, query, redirect, content type, size, pagination, page or global
  caps differ from the frozen plan;
- any HTTP request is automatically retried or collection continues after the
  first failure;
- send spacing is below 15,000,000,000 ns, including the first request cooldown,
  or offline replay uses wall UTC instead of integer monotonic evidence;
- calendar coverage, enum, session-count band, month partition or boundary-bar
  contracts fail;
- the same attempt ID can run twice, a published path can be overwritten, or a
  failed batch can be repaired in place;
- exp005/006 reuse hashes drift or reused raw bytes are copied into Git;
- monthly network acquisition, training, inference, IC, P&L or backtesting is
  authorized before bootstrap closure.

## Phase 1 boundary

Phase 1 permits preregistration, offline implementation, synthetic tests,
Python compilation, PowerShell parsing, dry-plan generation and fresh candidate
freezing. It forbids key reads, network access, formal execution, the full test
suite, monthly acquisition, training, inference, IC, P&L and backtesting.

## Success ceiling

`JQUANTS_V2_BARS_MONTHLY_BOOTSTRAP_CONTRACT_FROZEN / NEEDS_DATA`.
