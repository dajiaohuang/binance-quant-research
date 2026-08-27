# exp_20260826_003 — opaque article identifier contract v2

## Observation

`exp_20260826_002` failed closed during the first traversal of catalog 48 page
38. The frozen v1 collector required every article `code` to be 32 lowercase
hexadecimal characters, but the exact retained response contains 38 legacy
decimal-string codes. The first is `"360044545431"` at
`$.data.catalogs[0].articles[12].code`. All successful transport, pagination,
clock, retry, size, source-binding and corpus-only controls remain applicable.

## Falsifiable hypothesis

Changing only the article identifier representation to an opaque,
type-preserving v2 contract will accept both current 32-character lowercase
hexadecimal strings and legacy decimal strings without normalization, while
preserving exact list-to-detail identity, deterministic uniqueness and every
other exp002 control.

This is one inseparable change because list parsing, detail URL construction,
detail equality, uniqueness, row hashing and trusted reload must all use the
same identifier semantics. Success still means only a complete, auditable
**current-visible** CMS corpus during acquisition. It cannot establish deleted
articles, historical versions, events, effective times, listing intervals or
historical eligibility.

## Frozen opaque identifier contract

### Article code

- Exact JSON type must be `str`.
- Exact grammar is `^(?:[0-9a-f]{32}|[0-9]{1,64})$`.
- Preserve the exact string byte-for-byte after JSON decoding. No coercion,
  case conversion, trimming, integer conversion or normalization is allowed.
- Never produce or infer `code_kind`. A 32-character all-digit value matches
  both alternatives and must remain opaque.
- Detail URL construction validates first, then applies
  `urllib.parse.quote(code, safe='')`.
- Canonical URL validation parses exactly one `articleCode`, validates the
  decoded value, and requires exact parse/rebuild equality.

### Article id

- Preserve the exact JSON type and value.
- Accepted integer form: `type(id) is int`, excluding bool, positive, and its
  base-10 representation has at most 64 digits.
- Accepted string form: exact grammar `^[0-9]{1,64}$`; `"0"` and leading zeros
  are valid and preserved.
- Store an exact type tag (`int` or `str`) beside the untouched value.
- Detail `id` must have the identical Python/JSON type and identical value as
  the selected list row.
- ID uniqueness is type-aware: integer `1` and string `"1"` are distinct.
- Code uniqueness remains exact-string uniqueness. Neither identifier may be
  substituted for the other.

## Inherited frozen corpus controls

- Public GET only; no authentication, cookies, proxy or redirect.
- List catalogs 48 and 161 only, `pageSize=50`, fixed English headers and exact
  production list shape `$.data.catalogs[0]`.
- Detail endpoint only, exact production fields `body`, `firstCatalogId` and
  optional `contentJson`; prose remains hash/byte-length only in processed data.
- Binance public time before and after.
- Two complete ordered list traversals; all page shapes, totals, per-page
  semantic hashes and complete ordered semantic inventories must match. Pass 2
  is never merged into processed inventory.
- Every request attempt retains exact raw bytes, sidecar, hash, canonical URL,
  selected headers and local clocks. Reads are bounded at cap + 1.
- Retry only exact well-formed retryable HTTP outcomes on the identical URL.
- Atomic exclusive run lease, no overwrite, global positive wire-attempt cap,
  source SHA binding before lease/before and after each fetch/before artifacts,
  and trusted offline reconstruction of all raw and derived evidence.
- Expected success request inventory remains 108 list + 756 detail + 2 time =
  866 logical requests, with absolute wire cap 2,598.

## Test-only production fixture

The exact 7,607-byte exp002 failure response will be copied, byte-for-byte, to
`tests/fixtures/binance_cms_catalog_48_page_0038_legacy_codes.response` with
SHA-256
`ab4676534a3219461555e4127326a1da9a43e9e29afce0df3e8ace3ad0c31876`.
It is test-only evidence. Tests must use the tracked fixture and must not depend
on the ignored exp002 raw tree. It is not formal exp003 acquisition evidence.

## Implementation boundary

- New self-contained module:
  `src/quant_research/binance_spot_announcement_corpus_v2.py`.
- New test:
  `tests/test_binance_spot_announcement_corpus_v2.py`.
- New CLI: `quant-binance-spot-announcement-corpus-v2`.
- Extractor/version: `binance_spot_announcement_v2`.
- Raw root: `data/raw/binance_spot_announcement_v2/runs`.
- Processed root: `data/processed/binance_spot_announcement_v2/runs`.
- Formal run id: `exp_20260826_003_formal_001`.
- Formal artifacts: `experiments/exp_20260826_003/artifacts/{corpus_summary.json,schema.json,source_contract.json}`.
- The implementation is self-contained and must not import exp002/v1 helpers.
  Exp002 source, evidence and consumed run id remain untouched.

## Failure conditions

Any code/id coercion, normalization, `code_kind` output, type-insensitive ID
collision, list/detail type or value mismatch, noncanonical detail URL, fixture
hash mismatch, or regression in any inherited exp002 control fails closed.
Exact totals or interval counts that differ from 2,234/426 or 575/181 preserve
formal evidence but make the formal result `INCONCLUSIVE`. A complete contract
pass is capped at `NEEDS_MORE_DATA / ANNOUNCEMENT_CORPUS_AVAILABLE`.

## Two-stage freeze and execution status

This document, parameters and a non-executable formal command template are
created before implementation. The final extractor SHA-256 is intentionally
unknown at preregistration. After implementation and the first offline test
suites, the source was frozen at SHA-256
`30a45ed781e0335838ec7e6643c04c229c8ff53637aec2e2c7a0d928f22adc10`.
Only then did that hash replace the explicit placeholder in `commands.txt` and
enter `manifest.json`; this post-implementation freeze is not represented as
having been known during preregistration.

Auditor GO authorizes preregistration and offline implementation only. Formal
network execution remains NO-GO and must not occur in this phase. No Alpha, IC,
ML, eligibility derivation or backtest is permitted.
