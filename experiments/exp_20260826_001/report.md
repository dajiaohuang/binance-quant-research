# exp_20260826_001 — Binance Spot current/forward PIT snapshot

## Decision

`NEEDS_MORE_DATA`.

The preregistered public snapshot completed and passed its trusted loader. It
establishes source-bound Binance Spot status, `isSpotTradingAllowed`, quote asset
and permission fields from `known_at = 2026-08-25T18:53:18.027925Z` onward. It
does not establish listing intervals or any 2023–2024 state. Strict eligibility
is therefore 0/3,682 and the historical Alpha/IC/ML/backtest gate remains closed.

## Formal execution

The exact preregistered CLI command exited 0 in 3.7182146 seconds. It made three
unauthenticated public GETs, one to each frozen logical endpoint and no retries:

| Endpoint | Attempts | Bytes | Response interval UTC | Raw SHA-256 |
|---|---:|---:|---|---|
| time before | 1 | 28 | 18:53:16.555508–18:53:16.876508 | `983435f03cd1b1964e5ee1a928803fc02849f1c240639e52e0622fa504d1582a` |
| exchangeInfo | 1 | 17,512,657 | 18:53:16.881509–18:53:18.027925 | `93815999f9ce41e4918ea836928a8cbb7238eba89d4b1d6ad04823e69f0b4743` |
| time after | 1 | 28 | 18:53:18.053925–18:53:18.379064 | `3031fb21b2dd11b5be1bc0b7fbad5f3a750f10c03567209d8ef02803f80256ca` |

All responses were HTTP 200 with exact Content-Length. Request sidecars show
`authentication=NONE`, fixed canonical URLs, no filter other than
`showPermissionSets=true`, no redirect and no proxy/authentication header. The
Binance server-time bracket was 1787683998470 ≤ 1787683998768 ≤ 1787683999972.

## Snapshot contents

- Current response symbols: 3,681.
- Frozen archive candidates: 723.
- Union memberships: 3,682.
- Archive-only explicit UNKNOWN: 1.
- Raw status: 1,354 `TRADING`, 2,327 `BREAK`, 0 `HALT`, 1 `UNKNOWN`.
- `isSpotTradingAllowed`: 3,641 true, 40 false, 1 unknown.
- Current quote asset `USDT`: 733.
- Listing intervals null: 3,682/3,682.
- Strict eligible: 0/3,682.

The 26,917,791-byte deterministic membership artifact has SHA-256
`28dca84736c26497a79b3950fad9bd65b9f00f79e50cb6e87ca21d474c39a450`.
The trusted loader independently re-read all raw attempts and sidecars, verified
their attempt ledgers, hashes, clocks, row locators, canonical record hashes,
extractor source hash and artifact hash, then reproduced eligible count 0.

## Interpretation and limits

`BREAK` is the raw current response value; this report does not reinterpret it
as a historical suspension or delisting. The one archive-only symbol is explicit
UNKNOWN, not inferred delisted. Archive suffix, Kline appearance and first/last
Kline were never converted into status, permission, quote or listing evidence.

This snapshot is valid only at or after its 2026 known-at time. Because every
listing interval remains null, it cannot be backfilled into 2023–2024 and cannot
unlock residual momentum, factor scores, rank IC, model fitting or a backtest.
No validation/final-test data, Alpha, ML, Freqtrade or trading endpoint was used.

## Review history

Researcher and Auditor initially returned NO-GO on transport, clock, concurrency,
attempt-ledger and redirect contracts. All failures and fixes remain in `logs/`.
After the redirect hardening passed 22/22 targeted and 107/107 repository tests,
both reviewers issued limited GO for exactly this one preregistered formal command.
The limited GO did not authorize a second experiment or any strategy research.
