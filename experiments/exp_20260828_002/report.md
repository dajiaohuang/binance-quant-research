# exp_20260828_002 report

Status: `PREFORMAL_AWAITING_PHASE2_REVIEW / NEEDS_MORE_DATA`.

This experiment is pre-registered as an offline J-Quants V2 Free source-probe
and SSPT/TIPS adapter contract. No API key has been read, no network request has
been made, and no real data, model training, IC, P&L, or backtest has been used.

The five logical queries and their date semantics are frozen in
`parameters.json`. Pagination is not a retry: each logical query allows at most
25 pages and the run allows at most 60 HTTP requests. Every page receives its
own receipt and SHA; a pagination key may only come from the immediately prior
page, all other query parameters remain immutable, and loops/missing pages/cap
overflow are inconclusive. The pre-service-floor behavior is tested only with
an offline fixture.

## Development result

- strict stdlib collector, loader and local atomic publication lifecycle added;
- exact five-query plan, 25-page per-query cap, 60-request global cap and zero
  retries frozen;
- raw response/page receipts and raw-tree bytes/SHA/bijection are rebuilt by
  the loader;
- causal split-factor views use only factors known by formation; a factor on a
  later label path is rejected rather than leaked into features;
- adjacent master snapshots provide derived listing spells only; missing rows
  remain unknown and sector/market changes retain their dated snapshot identity;
- SSPT requires one exact formation-date master row per symbol; TIPS follows an
  exact q=5 official calendar path; neither adapter allows same-close execution;
- development targeted tests: final 19/19 PASS after an earlier 13/15 run whose
  two failures are retained in `logs/development_attempt_001.txt`;
- final py_compile and dry-plan PASS; dry-plan reported network count zero.

The ignored `.env.jquants.local` was checked only for presence, ordinary-file
type, non-reparse status, gitignore match and untracked status. Its contents,
size and hash were not read. No API key was read or logged.

## Unexecuted and limitations

The candidate `--execute` command has not run and needs independent Phase 2
review. The strict short-field schema and rejection assumptions have only been
tested against synthetic fixtures; the first authorized source probe may fail
closed if the official Free response differs. No raw J-Quants payload exists,
no real data has been accessed, and no training/full repository suite/IC/P&L/
backtest has run.

The conclusion ceiling is
`JQUANTS_V2_FREE_SOURCE_PROBE_OFFLINE_CONTRACT_VERIFIED / NEEDS_API_KEY`.
