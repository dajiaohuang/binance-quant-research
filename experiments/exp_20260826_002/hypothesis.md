# exp_20260826_002 — Binance announcement corpus freeze

## Observation

`exp_20260826_001` established a source-bound current/forward Spot snapshot but
left every listing interval unknown. Researcher exploration, which is not formal
evidence, observed two public Binance CMS catalogs with totals 2,234 and 426 and
claimed-release interval counts 575 and 181.

## Falsifiable corpus hypothesis

The exact unauthenticated Binance CMS list and detail endpoints frozen below can
be acquired into a complete, immutable, auditable **current-visible** announcement corpus for
articles whose source-claimed publication time satisfies
`2022-12-01T00:00:00Z <= t < 2025-01-01T00:00:00Z`.

Here, complete means every article returned by both complete catalog traversals
during this acquisition. It does not include deleted articles, inaccessible
articles or historical article versions no longer returned by the CMS.

Success requires catalog totals exactly 2,234/426, interval counts exactly
575/181, two complete stable pagination passes, identical per-page semantic
anchors and ordered semantic inventory across passes, one valid detail for every selected article, exact raw/sidecar and
derived hashes, and a trusted offline rebuild. Count or anchor mismatch preserves
all evidence but terminates `INCONCLUSIVE`.

Success is capped at `NEEDS_MORE_DATA / ANNOUNCEMENT_CORPUS_AVAILABLE`. Source
`releaseDate`/`publishDate` are only `claimed_published_at`; the exact detail
version is known no earlier than its response completion during this 2026 run.
No article body is parsed into a pair, event, effective time or listing interval,
and no historical eligibility claim is permitted.

## Single primary change

Add one CMS corpus collector, trusted loader, CLI and synthetic offline tests.
Do not modify the frozen exp001 PIT collector or the Alpha kernel.

## Frozen public sources

- List catalogs 48 and 161 only:
  `https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query?type=1&catalogId={48|161}&pageNo=N&pageSize=50`.
- Detail only:
  `https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query?articleCode={32 lowercase hex}`.
- Clock bracket only:
  `https://data-api.binance.vision/api/v3/time` before and after.
- GET only; fixed `Accept-Encoding: identity`, `Accept-Language: en-US,en;q=0.9`,
  `Clienttype: web`, `lang: en` and corpus User-Agent headers; no API key, authorization, cookie,
  proxy, signature or redirect. urllib may add only a canonical `Host` matching
  the endpoint hostname; every other injected header is rejected.
- List responses are exactly `$.data.catalogs[0]`; `$.data.catalogs` must contain
  one object and its integer `catalogId` must equal the request catalog.
- Detail prose is `$.data.body`, catalog is `$.data.firstCatalogId`, and optional
  `$.data.contentJson` may contain a JSON value. Processed outputs retain content
  hashes/byte lengths, never prose values.

## Formal acquisition order

1. Binance time before.
2. Complete pass 1 over every page of catalog 48, then catalog 161.
3. Filter by claimed release time and fetch every selected article detail.
4. Complete pass 2 over every page of catalog 48, then catalog 161. Totals, page
   shapes, every page semantic hash and the full ordered semantic inventory must
   equal pass 1. Pass 2 is not merged into processed inventory.
5. Binance time after.
6. Build inventory/detail index/summary/schema/source contract and immediately
   trusted-reload them.

## Evidence and temporal semantics

- Every logical request has a root-bound consecutive attempt ledger, exact raw
  bytes, sidecar, SHA-256, method, canonical URL/params, selected headers and UTC
  request/response clocks.
- The expected successful request inventory is 108 list requests, 756 detail
  requests and 2 time requests: 866 logical requests. With three attempts per
  logical request, the absolute wire-attempt bound is 2,598; retries stay on the
  identical canonical URL.
- `max_wire_attempts=2,598` is a positive acquisition-wide hard cap enforced
  immediately before every fetch across all logical requests and retries. The
  counter increments before the fetch, so a 2,599th wire attempt cannot occur;
  the bound and realized count are frozen in the summary and rebuilt by the
  trusted loader. The successful logical request expectation remains 866.
- The corpus-local transport reads at most `max_response_bytes + 1`; the extra
  byte proves oversize and is retained before terminal failure. It disables
  proxies and redirects without modifying the frozen exp001 transport.
- Retry is allowed only for a well-formed response whose recomputed outcome is
  exactly a frozen retryable `HTTP_<status>`. Redirect, oversized, malformed,
  missing/mismatched Content-Length and header-drift outcomes are terminal.
- The exact formal CLI requires the frozen extractor SHA-256. One central check
  verifies it and the hardcoded exp001 dependency SHA before lease creation,
  immediately before every wire attempt, immediately after every returned
  response before evidence acceptance, and before artifact completion.
- `claimed_published_at` comes only from list `releaseDate` and detail
  `publishDate`; it is not a trusted historical known-at.
- `detail_version_known_at` equals the detail response completion.
- `lastUpdateTime` and any version-like source field are preserved raw and do
  not prove historical version availability.
- Response `ETag`, `Last-Modified`, `Age`, `Cache-Control` and `Date` are retained
  only as transport/version clues; none is publish/effective/known-at evidence.
- Article body/content may be retained byte-exactly but is not semantically
  parsed in this experiment.

## Failure conditions

Endpoint/query/header drift, authentication material, proxy/redirect, non-200,
missing or mismatched Content-Length, invalid JSON/schema/time, pagination gap or
loop, non-unique/mismatched `catalogs[0]`, duplicate id/code, total/category
drift, page-length violation, full-pass semantic drift, expected-count mismatch,
detail code/category/publish mismatch, response
or response/total-byte/global-wire-attempt bound, clock disorder, overwrite,
source/artifact/hash mismatch or
trusted-loader divergence fails closed and preserves acquired evidence.

## Expected terminal status

- All contracts and exact counts pass: `NEEDS_MORE_DATA` with
  `ANNOUNCEMENT_CORPUS_AVAILABLE`.
- Any formal contract/count/anchor failure: `INCONCLUSIVE`.

Neither outcome unlocks historical PIT eligibility, Alpha, IC, ML or backtesting.
