# exp_20260825_005 report

## Decision

`NEEDS_MORE_DATA`. The offline successor passed every frozen data-integrity and
regression gate. This is a successful archive-availability dataset, not a
historical trading universe.

## Result

- 9,240 / 9,240 symbol-month ZIPs are valid; zero object-months remain `U`.
- 6,687,797 exact source rows were normalized across 462 symbols.
- 354 rows across 353 object-months carry the neutral code
  `NON_NOMINAL_CLOSE_TIME_WITHIN_INTERVAL`.
- Each flagged row preserves the source close time and records the nominal close,
  shortfall, ZIP key and source ZIP SHA-256. No timestamp was rounded or extended.
- Panel: 723 symbols × 18,288 hours = 13,222,224 cells.
- State counts: `A=6,687,797`, `M=72,379`, `N=6,462,048`, `U=0`.
- `contract_failures` is empty.

## Artifact hashes

- Coverage panel: `716b2d5c42c3078c93707722cbd93e171b233e6492f770d3c0905a710d9ba8b2`
- Object quality: `45194f662a72fd7776acfb825095c234227c6fb6353a7d2e1edfbd326a76f06c`
- Non-nominal close ledger: `2c54fcea7f3fcd5d4121cd96f9aff1d1952ddcbd4933b075c6684e4357efdb25`
- Revalidation summary: `14c63f57aa65014a0541b9764de90a5a0f1d597566732f1064c062a68abbdb47`

## Method boundary

The revised rule is scoped only to the frozen Binance Spot 1h archive for
2022-12 through 2024-12. Binance documentation identifies Klines by open time and
provides close time as a separate field, but does not guarantee this bounded-close
contract for every market, interval or period. The rule must not be generalized
without a new experiment.

## Independent postflight audit

`PASS`. The Auditor independently recomputed all 9,240 official CHECKSUM bindings,
parsed all 6,687,797 raw rows, checked every event-ledger row, reconciled all 462
normalized gzip files and every panel cell, and confirmed v3/exp004 hashes remained
unchanged. No partial v4 file exists.

## Allowed conclusion

This experiment establishes a complete, reason-coded `ARCHIVE_KLINE_AVAILABLE`
panel with preserved non-nominal close-time events. It cannot establish historical
trading status, listing, permission, eligibility or executability. It runs no
factor, strategy, backtest or machine-learning work.
