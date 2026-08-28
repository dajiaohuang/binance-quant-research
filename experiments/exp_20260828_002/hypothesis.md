# exp_20260828_002 — J-Quants V2 Free source probe offline contract

## Observation

SSPT V2 and TIPS V1 have strict synthetic data contracts, but no locally frozen,
licensed JPX source has yet been admitted into those contracts. The existing
SSPT/TIPS experiments therefore remain synthetic-only.

## Falsifiable hypothesis

A standard-library-only collector can freeze a five-query J-Quants V2 Free
source probe without leaking `JQUANTS_API_KEY`, silently following redirects,
retrying, or exceeding the pre-registered pagination budget. A strict offline
loader can then derive causal split-adjusted daily series, master-snapshot
listing spells, and leak-free SSPT/TIPS typed inputs from synthetic V2-shaped
fixtures.

## Single primary change

Add a J-Quants V2 collector/loader/adapter contract for JPX daily data. This
phase implements and tests the contract only; it does not call the API or train
or evaluate any model.

## Failure conditions

- a secret or a secret-derived value reaches argv, receipts, manifests, logs,
  exceptions, or persisted artifacts;
- any non-HTTPS, non-`api.jquants.com`, non-allowlisted path/query is accepted;
- redirects, retries, pagination-key loops or mutation, pagination beyond 25
  pages per query or 60 HTTP requests globally, duplicate JSON keys, non-finite
  values, unknown fields, invalid dates, or response-size overflow are accepted;
- future corporate-action factors are used to back-adjust an earlier formation;
- a missing master/calendar/bar observation is inferred as a negative fact;
- SSPT sector identity is not exact at formation, or TIPS q=5 does not follow
  the exact official calendar path;
- any network, training, IC, P&L, or backtest is performed in this phase.

## Ceiling

`JQUANTS_V2_FREE_SOURCE_PROBE_OFFLINE_CONTRACT_VERIFIED / NEEDS_API_KEY`.
This is not source acquisition, empirical authorization, eligibility, Alpha,
IC, P&L, or backtest evidence.
