# exp_20260826_004 — independent list/detail time claims

## Observation

`exp_20260826_003` fixed the opaque article identifier contract and completed
both catalog-48 and catalog-161 pass-1 traversals, but failed closed on the 65th
detail response. For article id integer `216744`, opaque code
`209355888f0042f788899dd1a04a0052`, the exact official list response claims
`releaseDate=1730975333236`, while the exact official detail response claims
`publishDate=1730975320602`. The detail claim is 12,634 milliseconds earlier.

The exp003 equality invariant treated two differently named source fields from
two endpoints as one timestamp. The raw evidence contradicts that invariant;
it does not establish that either claim is an effective listing time or that
one should overwrite the other.

## Falsifiable hypothesis

Changing only the temporal representation from an equality invariant to two
independent, type-checked source claims will preserve both official values and
their exact signed discrepancy without reconciliation, while retaining every
successful v2 identifier, transport, pagination, clock, size, retry, source
binding and corpus-only control.

Success remains capped at `NEEDS_MORE_DATA / ANNOUNCEMENT_CORPUS_AVAILABLE` and
means only a complete current-visible CMS corpus during acquisition. It cannot
establish historical versions, effective event times, listing intervals,
SPOT permission or historical eligibility.

## Single frozen temporal change

- List `releaseDate` is stored only as `list_release_date_claim_ms`.
- Detail `publishDate` is stored only as `detail_publish_date_claim_ms`.
- Every selected detail-index row stores both claims and exact derived
  `detail_publish_minus_list_release_claim_ms`.
- Both claims must be exact positive, non-bool JSON integers. The delta is exact
  subtraction and may have any signed magnitude.
- The detail claim is not required to equal the list claim. No tolerance,
  reconciliation, preferred timestamp, ordering assumption or expected
  discrepancy count is permitted.
- Corpus selection and all interval parameter, CLI, summary and schema names
  explicitly use `list_release_date_claim`; detail claims never change the
  selected article set.
- Old generic aliases are forbidden everywhere in processed artifacts and
  public v3 interfaces: `claimed_published_at_ms`,
  `claimed_published_at_source_field`, `detail_publish_date_ms`,
  `claimed_release_interval_ms`, `interval_counts`, `--claimed-release-start-ms`,
  `--claimed-release-end-ms-exclusive` and `--expected-interval-count`.

## Canonical discrepancy artifact

Processed v3 includes `time_claim_discrepancies.jsonl`. It is the exact subset
of selected `detail_index.jsonl` rows whose
`detail_publish_minus_list_release_claim_ms != 0`, canonically sorted by
`(catalog_id, article_code)`. An empty artifact is valid. Summary records its
path, SHA-256 and realized count only; there is no preregistered or runtime
expected mismatch count.

The trusted loader rebuilds every selected detail row and the discrepancy
subset from frozen raw list/detail responses, verifies row order, locators,
claim types and arithmetic, then checks artifact and summary hashes/counts.
Tampering with either claim, the delta, subset membership, order, path, count or
hash fails closed.

## Inherited frozen controls

- v2 opaque `code` grammar and exact type-preserving list/detail `id` contract.
- Public GET only; no authentication, cookies, proxies or redirects.
- Exact fixed list/detail/time endpoints, English CMS headers and canonical
  Host-only transport mutation.
- Exact production shapes, bounded cap+1 reads, Content-Length, selected
  transport headers and sanitized transport errors.
- Atomic exclusive run lease, no overwrite, same-URL bounded retry and one
  positive acquisition-wide wire-attempt cap of 2,598.
- Source SHA binding before lease, before and after every fetch, and before
  artifact completion.
- Binance time before/after and monotone local request/response clocks.
- Two complete ordered list traversals with matching totals, page shapes,
  per-page semantic hashes and full ordered semantic inventories. Pass 2 is
  never merged into inventory.
- Exact raw bytes, sidecars, SHA-256 evidence, root-bound attempt ledger and
  trusted offline reconstruction.
- Expected successful request inventory remains 108 list + 756 detail + 2 time
  = 866 logical requests, with at most 2,598 wire attempts.

The v3 module is self-contained stdlib code. It must not import or runtime-reuse
v2. The frozen v2 source SHA-256 is
`30a45ed781e0335838ec7e6643c04c229c8ff53637aec2e2c7a0d928f22adc10`
and v2 source/evidence/run remain untouched.

## Exact paired test fixtures

Two ignored exp003 raw responses are copied byte-for-byte into tracked,
test-only fixtures:

- `tests/fixtures/binance_cms_catalog_48_page_0014_time_claims.response` —
  9,949 bytes, SHA-256
  `adbcfcf758ce0c55b772b77de49169702b44759b3e99525e92bb16591d13ce5d`;
  target record is `$.data.catalogs[0].articles[1]`.
- `tests/fixtures/binance_cms_article_209355888f0042f788899dd1a04a0052_time_claims.response`
  — 78,567 bytes, SHA-256
  `cb27c5578fa9303516b238b0e4845640b21f397d09e6f5a90fd4446d21a2a8d5`.

Fixtures are regression evidence only, not formal exp004 acquisition evidence.
Tests depend only on tracked copies, never on ignored exp003 raw paths.

## Implementation boundary

- Experiment: `exp_20260826_004`.
- Self-contained module:
  `src/quant_research/binance_spot_announcement_corpus_v3.py`.
- Test: `tests/test_binance_spot_announcement_corpus_v3.py`.
- CLI: `quant-binance-spot-announcement-corpus-v3`.
- Extractor/version: `binance_spot_announcement_v3`.
- Raw root: `data/raw/binance_spot_announcement_v3/runs`.
- Processed root: `data/processed/binance_spot_announcement_v3/runs`.
- Formal run id: `exp_20260826_004_formal_001`.
- Root artifacts:
  `experiments/exp_20260826_004/artifacts/{corpus_summary.json,schema.json,source_contract.json}`.

No pair/event parsing, effective-time inference, listing interval, historical
eligibility, Alpha, IC, ML, backtest, validation-set or final-test work is in
scope.

## Failure conditions

Missing/non-integer/non-positive claims, any generic temporal alias, any
reconciliation/tolerance, wrong delta arithmetic, missing selected detail row,
incorrect discrepancy subset/order/hash/count, raw or derived tamper, or any
regression in inherited v2 controls fails closed. Exact catalog totals or
list-release-date-claim interval counts that differ from 2,234/426 or 575/181
preserve formal evidence but make the result `INCONCLUSIVE`.

## Two-stage freeze and execution status

This hypothesis, parameters, manifest and a formal command containing the
invalid placeholder `__FROZEN_EXTRACTOR_SHA256_AFTER_IMPLEMENTATION__` are
created before implementation. The final v3 source hash is intentionally
unknown at preregistration. After implementation and the first offline suites,
the source was frozen at SHA-256
`e3fd4ec75c450771afe654ccf08a1c1839c4b11ac9dc7ec038a0a1de722aa62e`.
Only then did that SHA replace the explicit placeholder; this post-implementation
freeze is not represented as having been known during preregistration.

Auditor GO authorizes preregistration and offline implementation only. Formal
network execution remains NO-GO. No formal or exploratory network request is
permitted in this phase.
