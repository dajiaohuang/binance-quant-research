# exp_20260827_002 — STANDARD_ENV_GRAMMAR_V1

## Status

`PRE_REGISTERED / AWAITING_OFFLINE_IMPLEMENTATION / NOT_RUN`

## Single hypothesis

A standard, tightly bounded `.env.binance.local` grammar can accept the common
forms rejected by exp_20260827_001 while preserving the same secret-isolation,
single-read, reservation, lifecycle, five-request acquisition, loader, and
semantic contracts. The only primary change is the env-file parser.

The parser accepts one optional leading UTF-8 BOM, strict UTF-8 containing only
ASCII thereafter, per-line LF or CRLF endings that may be mixed, blank lines,
ASCII comments, and exactly one column-zero `BINANCE_READ_ONLY_API_KEY` assignment
whose value matches `[A-Za-z0-9_-]+`. It rejects bare CR, NUL, non-ASCII,
duplicates, other assignments, quotes, inline comments, expansion syntax, and
extra `=` characters.

## Semantic ceiling

The collector can produce only current-visible future OPEN/DELIST schedule
claims plus current Spot metadata. `openTime` and `delistTime` remain
`planned_at_claim`, not effective time, historical status, permission, listing
interval, or eligibility evidence. Success can terminate only as
`NEEDS_MORE_DATA`.

## Failure criteria

- any of the seven Phase2 bindings or the exact formal line drifts;
- env path/type/reparse/cap/single-read/parser rules fail;
- any secret-bearing buffer, text, match, capture, or substring escapes the
  common cleanup lifecycle or reaches output, ledger, exception text, argv, or
  persistent artifacts;
- v5 collector/loader behavior differs from v4 beyond mechanical identity and
  path changes;
- reservation, ledger, five-request transport, schema, trusted reload, or
  atomic publication fails.

Phase1 authorizes offline implementation, mocks, tests, and freeze preparation
only. It does not authorize reading the real env-file contents/size/hash,
network access, formal execution, eligibility, Alpha, IC, ML, P&L, or backtest.
