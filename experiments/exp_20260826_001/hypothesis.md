# exp_20260826_001 — Source-bound current/forward Binance Spot PIT snapshot

## Observation

`exp_20260825_005` proves only `ARCHIVE_KLINE_AVAILABLE` for a frozen 2022-12
through 2024-12 panel. `exp_20260825_007` proves only that the synthetic
Hierarchical Alpha Research Kernel enforces its contracts. Neither experiment
establishes historical Binance Spot `TRADING`, SPOT permission, quote asset,
listing interval, eligibility or executability.

Binance's public Spot REST `exchangeInfo` response contains current symbol status,
quote asset, `isSpotTradingAllowed`, permission sets and trading rules, but it is a
current snapshot and has no documented historical listing interval. The existing
exp003 snapshot was filtered to `permissions=SPOT&symbolStatus=TRADING`, so it is
not status-complete and cannot supply explicit negative or unknown memberships.

For this experiment, `isSpotTradingAllowed` is the sole SPOT eligibility
permission predicate. `permissions` and `permissionSets` are preserved verbatim
as source fields for audit, but are not a second permission gate and cannot
override `isSpotTradingAllowed`.

## Falsifiable data hypothesis

An unauthenticated, unfiltered current Spot `exchangeInfo` request, bracketed by
public Binance server-time requests, can form a source-bound and auditable
current/forward PIT state snapshot if exact response bytes, request/response
clocks, canonical URLs and parameters, selected headers, source hashes and
per-record hashes are preserved.

The hypothesis succeeds only if:

1. `GET /api/v3/time` before, then
   `GET /api/v3/exchangeInfo?showPermissionSets=true`, then a second
   `GET /api/v3/time` form an ordered Binance server-time bracket;
2. no request contains API-key, authorization or signing material;
3. the exchangeInfo request has no `symbol`, `symbols`, `permissions` or
   `symbolStatus` filter;
4. every response and request sidecar is written atomically without overwriting;
5. the exact exchangeInfo body, body SHA-256 and every canonical symbol-record
   SHA-256 are retained and independently reloadable;
6. all exact exp003 archive suffix candidates are merged with current response
   symbols, and an archive-only symbol is emitted as explicit `UNKNOWN` rather
   than inferred delisted;
7. evidence is known only at exchangeInfo response completion and cannot be used
   for an earlier formation time; and
8. listing start/end remain `null` for every symbol, so strict eligibility is
   false for every membership and the historical 2023–2024 research gate remains
   closed.

## Single primary change

Add one public Spot PIT snapshot collector, trusted loader, CLI and synthetic
tests. This experiment does not add an Alpha Expert, score, rank IC, model,
portfolio, backtest, Freqtrade strategy, dry-run or live-trading function.

## Frozen sources and scope

- Time URL: `https://data-api.binance.vision/api/v3/time`.
- Exchange-info URL:
  `https://data-api.binance.vision/api/v3/exchangeInfo?showPermissionSets=true`.
- Request method: `GET` only.
- Transport bound: three logical endpoints, each with at most three attempts;
  therefore at most nine wire GETs, with every retry using the same canonical
  URL. HTTP 30x is terminal, is never retried, and must never trigger a request
  to its `Location`.
- Authentication: none; API-key, secret, cookie, bearer token and signed query
  parameters are forbidden.
- Frozen archive symbol index:
  `data/raw/binance_spot_v2/inventory/symbol_index.jsonl`, SHA-256
  `0b6df35cab25c9e393f901c923c0412084afbfdc956b171e1bef655907808c16`.
- Raw output root: `data/raw/binance_spot_pit_v1/snapshots/`.
- Processed output root: `data/processed/binance_spot_pit_v1/snapshots/`.
- Formal snapshot id: `exp_20260826_001_formal_001`.

No current response, archive path, symbol suffix, first/last Kline or absence of a
Kline may be transformed into a historical listing, delisting, status, permission
or quote-asset fact.

## Clock and evidence semantics

- Local request start and response completion are UTC instants saved per attempt.
- `known_at` equals the successful exchangeInfo response completion, never the
  Binance body `serverTime` and never request start.
- Binance time-before must not exceed exchangeInfo `serverTime`; exchangeInfo
  `serverTime` must not exceed Binance time-after.
- Every status, permission and quote-asset evidence binding records its kind,
  exact raw body SHA-256, known-at instant and exact extracted field values.
- Listing evidence is absent. `listing_from_ms` and
  `listing_to_ms_exclusive` are always `null`.

## Failure conditions

- Any endpoint or query drift, redirect, authentication material, non-200 final
  response, unresolved HTTP 429/418/5xx, oversized/truncated response, invalid
  JSON, time-bracket violation, excessive clock skew, duplicate symbol, unknown
  status, missing or invalid required field, frozen symbol-index mismatch,
  evidence overwrite, artifact mismatch or loader hash mismatch fails closed.
- Every HTTP response body and attempt sidecar available before failure is
  preserved. A failed snapshot id is not silently reused.
- The dedicated HTTP opener disables environment proxies and redirect following.
  An original 30x status, headers (including `Location`) and body are retained as
  the single failed attempt; the `Location` target is never accessed.
- A successful formal fetch still terminates at `NEEDS_MORE_DATA`, because no
  listing interval or historical 2023–2024 PIT state is established.

## Expected decision

Success: `NEEDS_MORE_DATA` with strict eligible count exactly zero.

Failure: `INCONCLUSIVE`, preserving the failing attempt and without opening any
factor, validation, final-test, ML or backtest gate.
