# exp_20260826_008 — Forward Spot Schedule PIT Snapshot V1.1

## Status

`PRE_REGISTERED / AWAITING_PHASE2_REVIEW / NOT_RUN`

## Observation

The historical point-in-time eligibility gate remains unresolved. Binance now
documents two API-key endpoints that expose currently visible future Spot-open
and delist schedules. These claims can be frozen together with a complete,
unfiltered current `exchangeInfo` response, but they cannot establish an
effective trading interval or historical eligibility.

## Single falsifiable hypothesis

A single five-request, one-attempt, fail-closed acquisition can freeze current
future OPEN/DELIST schedule claims and current Spot metadata into one auditable
commit domain. Exact symbol joins may be `MATCHED` or `MISSING`; neither result
may infer assets from a symbol string or establish eligibility.

## Only material change

Add API-key transport for the two read-only schedule endpoints while keeping
the key exclusively in the wrapper/child environment. The wrapper reads one
clipboard value, rejects empty, multiline, or NUL input before collector/write/
network activity, invokes the collector once, and clears environment,
clipboard, and key-bearing variables in `finally`.

## Fixed request order

1. `GET /api/v3/time`
2. `GET /sapi/v1/spot/open-symbol-list` with `X-MBX-APIKEY`
3. `GET /sapi/v1/spot/delist-schedule` with `X-MBX-APIKEY`
4. `GET /api/v3/exchangeInfo?showPermissionSets=true`
5. `GET /api/v3/time`

Every endpoint has one attempt and no retry or redirect. Failure stops before
the next wire request.

## Failure conditions

Key precondition, source binding, parent-layout, reservation, transport, HTTP,
size, JSON/schema, time bracket, loader/tree, or atomic promotion failure is
fail-closed. Once control is reserved the run is consumed. A lease-success
failure records only a controlled, non-sensitive failure code and preserves
already committed raw/receipt evidence.

## Semantic ceiling

`openTime` and `delistTime` become only `planned_at_claim_ms`. They are not
`effective_at`, current or historical trading status, listing/delisting
intervals, or eligibility evidence.

Always fixed: `terminal_status=NEEDS_MORE_DATA`,
`historical_eligibility_ready=false`, `eligibility_evaluated=false`, and
`strict_eligible_count=0`.

## Restrictions

Phase 1 authorizes only offline implementation, mock tests, and Phase 2 freeze.
No real network, formal run, credentials, eligibility, Alpha, Factor, IC, ML,
P&L, or backtest is authorized.
