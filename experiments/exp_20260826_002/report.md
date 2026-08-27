# exp_20260826_002 formal experiment report

## Outcome

Independent Researcher and Auditor reviews both issued GO for extractor SHA-256
`9c98725d74ce3d5e4f6c2a9ac5f9fbe53926b9fd4dae35520db39e7a086a7d46`.
The one preregistered formal command was then executed exactly once and failed
closed after 39 successful HTTP responses with `ERROR: invalid article code`
and observed exit code 1. It was not retried or adapted. The experiment closes
`INCONCLUSIVE`; no corpus artifact was produced.

## Implemented scope

- Added an unauthenticated, no-cookie, no-proxy, no-redirect CMS collector for
  the two preregistered list catalogs, selected article details and Binance time
  bracket.
- Added exact raw response/sidecar attempt evidence, canonical URL and header
  enforcement, bounded same-URL retry, atomic run lease and fail-closed schema,
  clock, size, pagination, count and full-pass stability checks. Its local
  transport reads only the configured response cap plus one diagnostic byte.
- Bound the production list shape to the single matching
  `$.data.catalogs[0]`, and detail shape to `body`, `firstCatalogId` and optional
  `contentJson`. Content is represented only by hashes and byte lengths outside
  exact raw responses.
- Permit only urllib's canonical endpoint `Host` mutation; reject all other
  injected, authentication or proxy headers.
- Acquire every list page twice around the selected detail fetches, with
  per-page semantic hashes and full ordered inventory equivalence. Pass 2 never
  enters processed inventory. The successful frozen expectation is 866 logical
  requests and at most 2,598 same-URL wire attempts.
- Enforce that 2,598 value as one positive, acquisition-wide runtime cap. The
  shared counter is checked and incremented immediately before every fetch, so
  list/detail growth, retries or pass drift cannot produce a 2,599th attempt.
  Summary/source-contract/schema evidence exposes the bound and realized count,
  and the trusted loader recomputes both from the root-bound attempt ledger.
- Added processed inventory/detail-index/summary/schema/source-contract outputs.
  No event, pair, effective time, listing interval or eligibility artifact is
  produced.
- Added a trusted loader that verifies source/dependency hashes, all raw attempt
  hashes and paths, response bytes and clocks, request order, full pagination,
  outcome/retry semantics, both complete list passes, selected detail set and
  every derived artifact.
- Require the exact expected extractor SHA-256 at the CLI. A central binding
  assertion checks it and the frozen dependency before lease, immediately
  before every wire attempt, immediately after every returned response before
  evidence acceptance, and before artifact completion. The trusted loader also
  independently requires the live dependency hash to equal the hardcoded value.

## Offline evidence

- Final hard-cap/TOCTOU narrow tests, including a 50-page/800-article cap
  stress fixture: 23/23 passed in 1.578 seconds.
- Final full-repository tests: 130/130 passed in 2.457 seconds.
- At implementation freeze, the formal raw run, processed run and corpus
  summary did not exist; formal execution changed only the raw-run state.
- Source SHA-256:
  `9c98725d74ce3d5e4f6c2a9ac5f9fbe53926b9fd4dae35520db39e7a086a7d46`.
- Frozen exp001 transport dependency SHA-256:
  `427b9eab83f14798fdb9b6465dddad397081d6a2c094fb27aa229fd94aee2264`.

## Formal evidence

- Exact command execution count: 1; tool-observed wall time: 11.4427733 seconds.
- Complete terminal output: stderr `ERROR: invalid article code`; stdout empty;
  observed exit code 1.
- Built-in acquisition completed 39 wire attempts with no retry: Binance time
  before plus catalog 48 pass-1 pages 1–38. All 39 responses were HTTP 200 with
  frozen outcome `OK` and authentication `NONE`.
- Partial evidence contains 39 exact response bodies and 39 sidecars: 355,184
  response bytes, 47,730 sidecar bytes and 402,914 bytes total.
- Catalog 48 reported the preregistered total 2,234. The 38 acquired pages
  contained 1,900 raw article records. No catalog 161 page, detail, pass 2 or
  time-after request was reached.
- On page 38, the first schema contradiction is
  `$.data.catalogs[0].articles[12].code = "360044545431"` for article id 27480.
  Offline inspection found 38 decimal-string codes in article indices 12–49;
  the frozen schema and detail URL allow only 32-character lowercase hex codes.
- The failing raw page SHA-256 is
  `ab4676534a3219461555e4127326a1da9a43e9e29afce0df3e8ace3ad0c31876`.
- The canonical 78-file partial raw inventory SHA-256 is
  `c3bee614c75899c07729cf3d26ff88244550871f69ffec25dad6b8d841f3be9c`.
- `request_ledger.jsonl`, processed inventory/detail index and all three trusted
  root artifacts are absent because failure occurred before artifact build.
  Therefore trusted reload was unavailable, rather than passed.
- Full transcript, failure inspection and raw inventory are retained under
  `experiments/exp_20260826_002/logs/`.

## Temporal limitation

“Complete corpus” means current-visible articles returned during both complete
catalog passes; it does not include deleted articles or unavailable historical
versions. List `releaseDate` and detail `publishDate` remain source claims. A detail body
would become exactly known only at its 2026 response-completion time. Even a
complete successful corpus cannot be used to backfill 2023–2024 known-at,
listing intervals, SPOT permission or historical eligibility.

## Referenced Skills

| Skill | Source | Purpose | Local path |
|---|---|---|---|
| quant-strategy-research | workspace skill | Preregistration, fail-closed evidence and experiment gates | `.codex/skills/quant-strategy-research/SKILL.md` |

The skill-referenced `methodology.md`, `experiment-contract.md` and
`source-map.md` files were not present and are disclosed in `manifest.json`;
none is claimed as read or used.

## Decision and limitation

Status is `INCONCLUSIVE`, artifact state is null and the consumed run id must
never be reused. The result disproves the frozen article-code schema; it does
not show that a current-visible corpus is impossible. Any correction requires a
new experiment, new run id, new preregistration and fresh review. This failed
run does not unlock historical listing intervals, eligibility, Alpha, IC, ML or
backtesting.
