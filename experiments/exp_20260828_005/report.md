# exp_20260828_005 Phase 1 report

## Status

`PREFORMAL_AWAITING_FRESH_PHASE2 / NEEDS_MORE_DATA`

Phase 1 produced and froze an offline candidate for a J-Quants V2 Free five-query source probe. It did not read a local API key, make a network request, execute the formal launcher, acquire new market data, or authorize empirical research.

## Single repair hypothesis

An official-schema-correct, raw-first collector can preserve every received response and a nonsecret receipt before strict parsing, while retaining the same exact-once five-query KDDI probe and fail-closed rebuild contract.

## Implemented contract

- Master uses the exact 14-field official response schema and models `ProdCat` as `product_category`; legacy `SecType` aliases are rejected. Q02 and Q03 require KDDI `ProdCat == "011"` without inferring security type from code digits.
- Daily bars use the exact 18-field non-Premium schema: `Date,Code,O,H,L,C,UL,LL,Vo,Va,AdjFactor,AdjO,AdjH,AdjL,AdjC,AdjVo,MktCap,ExRT`. Premium-only session fields are rejected. `UL` and `LL` accept only `"0"` or `"1"`; `MktCap` is nullable or finite nonnegative; `ExRT` is nullable or one of `"1"`, `"2"`, `"3"`.
- The KDDI 2025-03-28 query retains the falsifiable expectations `AdjFactor == 0.5` and `ExRT == "1"`. Ordinary rows may have `ExRT == null`.
- TSE calendar semantics treat `HolDiv` `1` and `2` as business sessions and `0` and `3` as non-sessions.
- Adjusted prices are validated for finite values and OHLC consistency, without imposing an exact adjusted/raw ratio that conflicts with the official one-decimal rounding rule.
- After redirect, status, content-type, and size checks, each raw response body and safe receipt are atomically persisted before strict JSON or semantic parsing. A parser failure retains both artifacts.
- Each safe receipt binds request/query/page identity, sanitized exact query-parameter hash, status, content type, body byte count and SHA-256, send/receive times, zero redirects, pacing wait, and the prior pagination-key hash when applicable. It stores neither API keys nor headers nor raw query parameters.
- The launcher retains all-file import-before-environment authority, exact-once reservation/ledger behavior, collector double hashing, fail-closed staging rebuild, full-tree authorization, no-clobber semantics, pacing, and pagination checks. Formal listing presence remains `UNKNOWN`.

## Development evidence

- Targeted suite: `37/37 PASS`, zero failures, errors, or skips across two development attempts.
- Static checks: Python compilation `PASS`; PowerShell launcher parse `PASS` with zero parse errors.
- Dry plan: five queries, zero network requests; plan SHA-256 `90ab7da6087a9b226a10a3e23ccaebef2acc88814a1729172437d0d89d4e7dc4`.
- Frozen files: `10/10` byte and SHA-256 matches.
- Immutable exp004 Q01 regression fixture: SHA-256 `97f544ad7fecdc2189383b5fdcef925b94914be00e2fd4a59aafb3d8cd63bdee`.
- Adversarial coverage includes schema failure after response persistence, HTTP failure persistence, receipt-write failure preventing parsing, persisted-evidence secret scan, legacy/Premium schema rejection, nullable official bar fields, rounded adjusted prices, and the exact five-query pagination chain.

The full repository test suite was not run because Phase 1 authorized targeted and static checks only.

## Frozen candidate

- Freeze manifest: `experiments/exp_20260828_005/artifacts/expected_freeze_manifest.json`
- Freeze manifest SHA-256: `75c22f6a0ef8f46e2e24514603261a07a3b6437ae64f62523e6f6c50b10a37e8`
- Launcher SHA-256: `32350e9b436e5704199ee8ba552937a1e0ece0c216d0f2453f2a50a3dbfb7ade`
- Frozen formal command SHA-256, UTF-8 without newline: `199fda972bedc08692ccd2fbb8e2bef5369ec0f142f75721f662b9a241e922b4`

The formal command is recorded in `commands.txt` but is not authorized for execution before a fresh independent Phase 2 audit and explicit GO. No empirical Alpha, IC, ML, P&L, eligibility, or backtest conclusion is supported by this phase.

## Phase 4 append-only closure

The Phase 1 status above is historical. One and only one formal invocation was
subsequently executed. It made five direct requests, received five HTTP 200
`application/json` responses with zero redirects and zero retries, and retained
five raw bodies plus five safe sidecar receipts in the source staging tree.

Postflight reconstruction verified raw/receipt bijection, body sizes and hashes,
the fixed query plan, exact schemas and query semantics. The source-bound counts
are four calendar rows, one distinct merged master row, and 4,414 distinct merged
daily-bar rows. The raw-tree SHA-256 is
`d14273cc49e9de82b0295e9ab76db8c01eea02e5f4e0af940400b64550b8209c`.
The evidence audit itself passed.

Publication nevertheless failed closed. Receipt wall-clock send gaps were
13,008, 13,001, 12,994 and 12,999 ms. The runtime guard used a monotonic clock,
which cannot be reconstructed exactly from millisecond wall-clock receipts.
Therefore pacing is recorded as
`RUNTIME_MONOTONIC_GUARD_INFERRED_NOT_REPLAYABLE`, not `PASS`. Staging and
failure control remain present; the final run path and authorization artifact
remain absent. Frozen source and run bytes were not changed.

The terminal adjudication is
`INCONCLUSIVE / RATE_PACING_POSTFLIGHT_CLOCK_DOMAIN_MISMATCH`. It authorizes no
listing-presence or historical-eligibility claim and no training, IC, P&L or
backtest. A separate experiment is required for any offline recovery view; it
must not be represented as exp005 final promotion.
