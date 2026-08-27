# exp_20260825_003 — Unicode-preserving archive inventory

## Observation

`exp_20260825_002` retrieved a complete four-page archive-root listing but its frozen
ASCII-only validator rejected five observed Unicode symbol prefixes. Dropping those
prefixes would add an undocumented symbol-selection rule.

## Falsifiable engineering hypothesis

Treat every non-empty, non-nested S3 CommonPrefix segment as `ARCHIVE_OBSERVED`, retain
Unicode symbols that end with `USDT` as `SYMBOL_SUFFIX_USDT_CANDIDATE`, and store their
raw per-symbol evidence under a SHA-256 directory name. This should complete the same
metadata-only inventory without altering market or factor assumptions.

## Frozen scope and gate

- S3 Spot monthly Kline metadata, interval `1h`, 2022-12 through 2024-12.
- Root symbol discovery does not use current exchangeInfo.
- No ZIP or CHECKSUM payload download, no Kline parse, no panel, factor, backtest, or ML.
- Pagination, XML, uniqueness, KeyCount, deterministic output, raw evidence, and no-
  overwrite gates remain unchanged.
- A deterministic raw symbol index preserves the original string, UTF-8 bytes, full
  SHA-256 evidence directory mapping, and suffix-candidate flag without normalization.
- A successful inventory terminates at `NEEDS_MORE_DATA`; any live failure is preserved
  as `INCONCLUSIVE`.
