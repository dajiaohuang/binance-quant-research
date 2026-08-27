# exp_20260825_005 — Offline Kline close-time anomaly revalidation

## Observation

`exp_20260825_004` downloaded and checksum-bound all 9,240 frozen ZIP/CHECKSUM
pairs, but its preregistered rule required every 1h `close_time` to equal
`open_time + 3,599,999`.  It rejected 353 symbol-months containing 354 earlier
close times.  The open times remain UTC-hour aligned and the rows are present in
official checksum-valid ZIPs.

## Falsifiable method hypothesis

Because Binance identifies a Kline by open time and exposes close time as a
separate source field, a row whose close time remains inside its declared
half-open hourly interval can be preserved as archive evidence if the exact
source close time is retained and explicitly flagged with the neutral code
`NON_NOMINAL_CLOSE_TIME_WITHIN_INTERVAL`. Replacing the equality
gate with:

```text
open_time <= close_time <= open_time + 3,599,999
```

should admit only the 354 already diagnosed early-close rows while every other
checksum, ZIP, schema, timestamp, ordering, OHLCV and object-atomicity gate remains
unchanged.

## Single primary change

Change only close-time validation from exact interval-end equality to bounded
membership within the open-time interval.  Early close time is never rounded,
extended, filled or rewritten.  Each accepted anomaly is emitted to a dedicated
ledger with anomaly code, symbol, month, ZIP key, open time, actual close time,
nominal interval end, shortfall milliseconds and source ZIP SHA-256. The flag does
not infer a halt, listing state, trading state or cause.

## Frozen scope

- Offline only: reuse the immutable raw bytes acquired by `exp_20260825_004`; no
  HTTP, API, authentication or account access.
- Revalidate all 9,240 pairs, not only the 353 prior failures.
- Bind the run to the exp004 object-quality, payload-summary and raw run-contract
  SHA-256 values recorded in `manifest.json`.
- Recheck local object size, official CHECKSUM, ZIP SHA-256, ZIP safety/CRC and all
  CSV rules from raw bytes.
- Write a new processed data version.  Never overwrite exp004 or
  `data/processed/binance_spot_v3`.
- Preserve object-month atomicity.  Any remaining hard error makes the entire
  object-month `U` and emits zero rows from it.
- No historical trading/listing/permission/eligibility claim and no factor,
  backtest or ML run.

## Success and failure conditions

Success requires all 9,240 objects to pass the revised, predeclared rule; exactly
723 symbols, 18,288 hours and 13,222,224 A/N/M/U cells; zero `U`; a complete
non-nominal close-time ledger; and deterministic artifact hashes. Frozen raw bytes
require exactly 354 flagged rows across 353 objects; any different count is
reported but makes this experiment `INCONCLUSIVE` because the frozen binding or
implementation was not reproduced.

The independently derived regression invariants are 6,687,797 normalized rows and
panel counts `A=6,687,797`, `M=72,379`, `N=6,462,048`, `U=0`. Any difference is
also `INCONCLUSIVE`; these values are validation targets, not rewritten outputs.

Any close time before its open time or after its nominal interval end remains a
hard failure.  Any other data-integrity error also fails the whole object-month.
Even a successful run ends at `NEEDS_MORE_DATA`, because archive Kline presence
does not establish historical tradability.
