# exp_20260825_004 report

## Decision

`INCONCLUSIVE`. The exact frozen public payload scope was downloaded, but 353 of
9,240 symbol-month pairs failed the preregistered exact close-time rule. Their
months remain atomically `U`; no row from those objects entered normalized output.

## Frozen inputs

The experiment consumes only the three `exp_20260825_003` artifacts and exact
SHA-256 values recorded in `manifest.json`.  Independent Researcher and Auditor
checks confirmed 18,480 unique records form exactly 9,240 ZIP/CHECKSUM pairs with
no duplicate keys, missing CHECKSUM, or orphan CHECKSUM.

## Referenced Skills and sources

- `.codex/skills/quant-strategy-research/SKILL.md` — reproducible experiment and
  independent review workflow.
- The Skill-referenced `methodology.md` and `experiment-contract.md` files are not
  present in this workspace and were not used.
- Binance public-data README — official Kline schema, monthly public download,
  CHECKSUM convention, pre-2025 millisecond timestamps, and archive-update warning.
- Binance Spot REST documentation — public market-data-only endpoint semantics.

## Preflight findings

- Public Data Vision/S3 objects do not require login or an API key.
- Binance may replace archived objects after publication.  Every request therefore
  uses the frozen inventory ETag as `If-Match` and also checks response metadata.
- Small sample evidence shows that a monthly ZIP can begin mid-month or contain only
  a few rows.  ZIP existence is not full-month coverage or historical trading status.
- Researcher review required object-month atomic validation, a frozen UTF-8 symbol
  order, an exact normalized schema, and no unledgered online smoke fetch.  These
  constraints were incorporated before code freeze.  The 723-symbol newline list
  SHA-256 is `abcfbaa4b3a44a2336de962c1da2495d254b4bf37800def41af8c66cba20d121`.
- Available disk space at preregistration was about 28.7 GB versus about 293.7 MB of
  frozen compressed payload bytes.

## Acquisition result

- 9,240 ZIP and 9,240 CHECKSUM objects returned HTTP 200.
- All 18,480 objects matched frozen ETag, Content-Length, Last-Modified and byte
  count; all 9,240 ZIPs matched their official SHA-256 CHECKSUM.
- ZIP bytes: 292,861,199; CHECKSUM bytes: 818,811.
- Every request succeeded on its first attempt; no local object was reused.
- All 9,240 pair receipts exist. The object-quality ledger contains both object
  evidence records for every pair.
- No login, API key, account, balance, order or trading endpoint was used.

This establishes that all scoped raw payload bytes were acquired and integrity
bound. It does not make every symbol-month a valid derived Kline object.

## Validation result

8,887 pairs passed the full preregistered CSV gate. The remaining 353 pairs have
354 rows where source `close_time` is earlier than `open_time + 3,599,999`:

- 351 symbols at `2023-03-24T12:00:00Z`, with close times around
  `12:39:40Z` (roughly 20 minutes early);
- `AEURUSDT` in 2023-12, two rows ending 24 and 28 minutes early;
- `FDUSDUSDT` in 2023-07, one row ending 15 minutes early.

These are checksum-valid source rows, not corrupted downloads. The experiment
does not relax its preregistered gate after seeing them. A successor experiment
must decide whether an early close time inside the declared 1h interval is a
preservable anomaly and must revalidate every object offline from immutable raw
bytes.

## Derived evidence

- 723 symbols × 18,288 UTC hours = 13,222,224 reason-coded cells.
- `A`: 6,431,739; `M`: 65,805; `N`: 6,462,048; `U`: 262,632.
- Normalized rows: 6,431,739 across 462 symbols.
- Coverage panel SHA-256:
  `4e62a3757df62545ab23aa3e57a14098427dd97c17a8fdf64fbd449e67ec96f6`.
- Object-quality SHA-256:
  `3e7675986d7c3f22b1c02ff0beae4532015dc2993be9f69b984d0223f5c6acc2`.

The `U` total is exactly the 353 rejected object-months multiplied by their 744
hours. Gaps were classified and never filled.

## Independent postflight audit

`PASS`. The Auditor independently recomputed all 9,240 official CHECKSUM bindings,
verified all 18,480 local object hashes and frozen response identities, confirmed
there are no partial files, checked every panel cell, and reconciled all 462
normalized files and 6,431,739 rows to the quality ledger. This permits the narrow
statement that the complete frozen raw payload scope was acquired. It does not
promote the 353 `U` months to valid derived data.

## Allowed conclusion

All exact raw payloads were obtained, but this experiment cannot claim a complete
`ARCHIVE_KLINE_AVAILABLE` panel. Historical status, permissions, trading rules,
dynamic eligibility, factors, backtests, and machine learning remain unexecuted.
