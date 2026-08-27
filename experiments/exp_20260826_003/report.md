# exp_20260826_003 formal experiment report

## Outcome

The opaque, type-preserving Binance CMS article identifier v2 was implemented,
frozen and fully offline-tested. After independent Researcher and Auditor GO,
the one frozen formal command was executed exactly once. It failed closed with
observed exit code 1 and `ERROR: detail publishDate mismatch`; no external retry
or adaptive request followed. The extractor source SHA-256 is
`30a45ed781e0335838ec7e6643c04c229c8ff53637aec2e2c7a0d928f22adc10`.
The consumed run closes `INCONCLUSIVE` with no corpus artifact.

## Implemented single change

- Article `code` is an exact JSON string matching
  `^(?:[0-9a-f]{32}|[0-9]{1,64})$`; it is never coerced, normalized or
  classified. In particular, a 32-digit value stays opaque and no `code_kind`
  field is produced.
- Detail URLs validate the opaque code, call `quote(code, safe='')`, and require
  exact parse/rebuild equality.
- Article `id` preserves exact JSON type and value. Accepted integers are
  positive non-bool values with at most 64 decimal digits; accepted strings
  match `[0-9]{1,64}`, including `"0"` and leading zeros.
- Inventory rows carry `article_id_type` plus the untouched `article_id`.
  Detail equality and uniqueness are type-aware, so integer `1` and string
  `"1"` are distinct.
- Page anchors, semantic inventory, row hashes, processed artifacts and trusted
  reload all share the same opaque/type-preserving contract.

The module is self-contained stdlib code and imports neither exp002/v1 nor the
PIT module. All successful exp002 transport, no-auth/no-proxy/no-redirect,
bounded-read, retry, atomic lease, global wire cap, clock bracket, two-full-pass,
raw evidence, source binding and trusted-loader controls are retained.

## Production fixture evidence

The tracked test-only page-38 response is exactly 7,607 bytes with SHA-256
`ab4676534a3219461555e4127326a1da9a43e9e29afce0df3e8ace3ad0c31876`.
It contains 50 articles, including 38 decimal-string codes that v1 rejected.
Tests read only this tracked fixture, never the ignored exp002 raw tree. The
fixture is regression evidence, not formal exp003 acquisition evidence.

The first text-patch staging attempt appended one LF and was rejected at 7,608
bytes. The exact no-final-LF fixture was then copied once byte-for-byte from the
ignored retained raw response; this exception and both hashes are recorded in
`logs/offline_results.txt`. All other edits used `apply_patch`.

## Offline verification

- First narrow suite: 28/28 passed in 1.687 seconds.
- First full repository suite: 158/158 passed in 3.944 seconds.
- Final frozen narrow suite: 28/28 passed in 1.479 seconds.
- Final frozen full repository suite: 158/158 passed in 3.925 seconds.
- Exp002 source remains unchanged at
  `9c98725d74ce3d5e4f6c2a9ac5f9fbe53926b9fd4dae35520db39e7a086a7d46`.
- The v2 formal raw run, processed run and three root artifacts were all absent
  at source freeze; pre-execution network request count was zero.

The final formal command contains the frozen source SHA and retains the
preregistered 866 expected logical requests and hard 2,598 wire-attempt cap.
Its UTF-8 bytes without newline hash to
`0970899a06cc5e3d324c20c0dc29e1bafc0481bc44c9b75f85e3f751d2d42c73`.
That exact command was the only formal command executed.

## Formal evidence

- Tool transcript chunks `bbe34c` and `b9e1a3` record one execution, observed
  exit code 1, empty stdout, stderr `ERROR: detail publishDate mismatch`, and
  total observed wall time 33.3436274 seconds.
- The collector froze 120 one-attempt HTTP 200/`OK` public responses: one time
  before, all 45 catalog-48 and nine catalog-161 pass-1 pages, then 65 detail
  responses. There were no retries, pass-2 pages or time-after request.
- All 120 response SHA-256 values, body lengths, Content-Length values, local
  request/complete order, 120 sidecars and unauthenticated request evidence
  passed the offline inspection. Frozen response bytes are 3,961,317; sidecars
  are 149,438 bytes; the 240 retained files total 4,110,755 bytes.
- Both pass-1 catalogs matched the preregistered totals 2,234/426 and claimed
  interval counts 575/181 before the detail invariant failed.
- The first mismatch is article id integer `216744`, opaque code
  `209355888f0042f788899dd1a04a0052`. The selected list row claims
  `releaseDate=1730975333236`, while the exact detail response claims
  `publishDate=1730975320602`, 12,634 ms earlier. Their frozen response hashes
  are respectively `adbcfcf7…d13ce5d` and `cb27c557…1a2a8d5`.
- The canonical 240-row partial raw inventory has SHA-256
  `2d9378a331993742e73000ccb0531358202bd474cc0077e51ad2456db8c51052`.
  Full paths and hashes are retained in `logs/formal_partial_raw_inventory.tsv`.
- Failure occurred before the request ledger, processed inventory/detail index
  and three trusted root artifacts were created. Trusted reload was therefore
  unavailable, rather than passed. All partial raw evidence remains immutable,
  and the run id must not be reused.

## Temporal and research boundary

Even a future independent experiment can establish only a current-visible CMS corpus
during acquisition. It cannot recover deleted articles or historical versions.
`releaseDate`/`publishDate` remain source claims; exact detail content is first
known at its response completion. This experiment produces no pair/event,
effective time, listing interval, historical eligibility, Alpha, IC, ML fit or
backtest, and does not unlock the current PIT eligibility gate.

## Decision

`INCONCLUSIVE`, artifact state null. The identifier-v2 change fixed the exp002
legacy-code failure and allowed complete pass-1 pagination, but the frozen
assumption that list `releaseDate` must exactly equal detail `publishDate` is
false for at least one retained official response. A correction requires a new
experiment, new run id and new preregistration; exp003 is not rerun or adapted.

## Referenced Skills

| Skill | Source | Purpose | Local path |
|---|---|---|---|
| quant-strategy-research | workspace skill | Preregistration, evidence freeze and research gates | `.codex/skills/quant-strategy-research/SKILL.md` |

The skill-linked `methodology.md`, `experiment-contract.md` and `source-map.md`
files were absent and are disclosed in `manifest.json`; none is claimed as
read or applied.
