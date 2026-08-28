# exp_20260828_010 preregistration

## Observation

exp_20260828_009 validated and froze the J-Quants V2 Free bootstrap: 465
official sessions across 23 months, with source-bound reusable daily-bar leaves
for 2024-07-01, 2025-03-28 and 2026-05-29. It did not acquire the remaining
monthly panel.

## Single hypothesis

A resumable, append-only monthly executor can acquire the remaining exact 462
all-market daily-bar dates while preserving the exp009/exp005/exp006 source
bindings, strict Free18 semantics, monotonic rate evidence and immutable raw
provenance.

## Single change

Add independent package `jquants_v2_bars_monthly_v4` and its launcher. It must:

- revalidate the exact exp009 final, raw tree, registry and session plan plus
  exp005/006 sources at startup and before every month;
- process the 23 monthly plans in chronological order and exclude exactly the
  three source-bound reuse dates, leaving exactly 462 network dates;
- reserve each month/attempt with O_EXCL, publish one immutable no-clobber shard
  per month, stop at the first failure and permit continuation only under a new
  batch/attempt ID;
- write each raw body and its safe receipt before HTTP or schema interpretation,
  use only a prior response pagination key, enforce eight pages per date, make
  no retries and follow no redirects;
- enforce exact Free18 fields, exact requested date, nonempty daily output,
  unique ordered Date+Code identities and existing null-coherence rules;
- persist/replay a full 15,000,000,000 ns first cooldown and inter-request
  spacing within every month attempt, plus rolling at most five sends/minute;
- build a deterministic global catalog only from all 23 immutable shards and
  never overwrite an existing catalog;
- load `.env.jquants.local` into `JQUANTS_API_KEY` only inside the launcher and
  remove the process environment value on exit.

## Expected result

Offline tests, compilation, PowerShell parsing and dry planning prove the
executor contract without reading the key or making network requests. A frozen
candidate and exact one-shot formal command can then be submitted for an
independent Fresh Phase2 audit.

## Failure conditions

Any source drift, date/count mismatch, nonchronological plan, reuse-date request,
preexisting reservation/final/catalog conflict, HTTP/redirect/content/schema
failure, missing or fabricated pagination predecessor, more than eight pages,
short monotonic spacing, duplicate Date+Code, raw/receipt mismatch, secret
artifact, retry, overwrite, or mutation before the required preflight causes
failure. The executor stops immediately and does not retry.

## Claim ceiling

Before formal acquisition the maximum claim is
`JQUANTS_V2_FREE_MONTHLY_EXECUTOR_FROZEN / NEEDS_NETWORK_AUDIT`.
Even after successful acquisition, listing eligibility/PIT universe, training,
inference, IC, P&L and backtesting remain unauthorized.

