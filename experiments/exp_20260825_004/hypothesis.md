# exp_20260825_004 — Frozen Binance Spot 1h payload acquisition

## Observation

`exp_20260825_003` established a complete metadata inventory for 9,240 Binance
Spot monthly 1h Kline ZIP objects and their 9,240 companion CHECKSUM objects for
2022-12 through 2024-12.  It did not download payload bytes, so archive Kline
coverage and row quality remain unknown.

## Falsifiable data hypothesis

If every object is fetched with its `exp_20260825_003` ETag as an `If-Match`
condition, its frozen size/ETag/Last-Modified metadata still matches, every ZIP
matches the exact official CHECKSUM, and every CSV passes the frozen schema and
time-series gates, then the observed rows can form an auditable
`ARCHIVE_KLINE_AVAILABLE` panel without filling gaps or inferring historical
tradability.

## Frozen scope

- Consume only the exact three frozen inputs and hashes recorded in
  `manifest.json`; do not re-list S3 or regenerate object keys.
- Fetch exactly 9,240 ZIP and 9,240 CHECKSUM objects from the public Binance Data
  Vision S3 bucket.  No login, API key, account endpoint, balance, order, or user
  data is allowed.
- Market: Binance Spot archive path; interval: `1h`; months: 2022-12 through
  2024-12 inclusive.
- All archive timestamps in this date range must be epoch milliseconds.  The
  2025 microsecond convention is outside this experiment.
- Preserve raw ZIP and CHECKSUM bytes.  Do not extract archive members into the
  raw directory.
- Produce deterministic normalized per-symbol Kline files and a reason-coded
  coverage panel over all 723 suffix candidates and all 18,288 UTC hours in
  `[2022-12-01T00:00:00Z, 2025-01-01T00:00:00Z)`.
- No historical `TRADING`, SPOT permission, quote-asset, listing/delisting,
  eligibility, or executability claim.  No factor, backtest, residual momentum,
  or machine-learning run.

## Required object gates

1. Frozen input hashes and exact 9,240 one-to-one ZIP/CHECKSUM pair identities.
2. Conditional GET with frozen ETag; HTTP 412 or changed ETag, Content-Length, or
   Last-Modified is `SOURCE_CHANGED`, not an accepted refresh.
3. Strict single-record CHECKSUM parsing with the exact ZIP basename.
4. Exact ZIP SHA-256 match, valid ZIP CRC, one safe regular CSV member, exact
   expected member name, no encryption/path traversal/multiple members/zip bomb.
5. Exactly 12 Kline columns; strict millisecond UTC grid and month boundaries;
   strict ordering and uniqueness; valid close time, OHLC, volumes, and trade
   count.  Gaps and zero-volume rows are recorded, never filled or deleted.

## Coverage state contract

Each symbol-hour cell uses one of four one-byte states:

- `A`: one unique row passed every gate (`ARCHIVE_KLINE_AVAILABLE`).
- `N`: the frozen inventory has no ZIP object for that symbol-month.
- `M`: a validated symbol-month object exists but this hour has no row.
- `U`: an object exists but is unavailable because acquisition or validation did
  not pass; detailed reason is in the object-quality ledger.

These states describe archive evidence only, not market status.

Object validation is atomic at `symbol × month`: if acquisition, CHECKSUM, ZIP,
CSV, or any row-level hard gate fails, every hour in that object month is `U` and
no row from that object is emitted to normalized output.  Only a wholly valid
object may contain `A` for observed rows and `M` for absent rows.  `N` is mutually
exclusive with object presence and is used only when the frozen inventory has no
ZIP for that symbol-month.

The physical panel is a UTF-8, LF-terminated, deterministic gzip CSV with 18,288
data rows.  Its first column is `open_time_utc`; the remaining 723 columns are the
exact `suffix_candidate=true` symbol strings sorted by Unicode code point.  The
header is not counted as a cell.  The newline-delimited symbol list (including a
final LF) has SHA-256
`abcfbaa4b3a44a2336de962c1da2495d254b4bf37800def41af8c66cba20d121`.

## Failure conditions

- Any frozen input mismatch, object-pair mismatch, source-object drift, unresolved
  HTTP failure, checksum mismatch, unsafe ZIP, invalid CSV, duplicate/off-grid row,
  month spill, or invalid market field prevents a complete success decision.
- Partial evidence is preserved and the experiment is `INCONCLUSIVE`; invalid
  bytes or rows are never repaired, overwritten, deduplicated, or filled.
- Failure to cover exactly 723 symbols, 18,288 hours, and 13,222,224 reason-coded
  cells is an experiment failure.

If all object and panel gates pass, this data experiment may establish a verified
`ARCHIVE_KLINE_AVAILABLE` dataset.  The repository still remains
`NEEDS_MORE_DATA` until historical status and dynamic eligibility are established
in a separate experiment.
