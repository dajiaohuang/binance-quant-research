# exp_20260826_009 — WRAPPER_STAGE_AND_NATIVE_EXIT_PROPAGATION_V1.1

## Observation

`exp_20260826_008` consumed its one authorized wrapper invocation before the
collector reserved a run. The outer execution surface observed exit `1` with
no stage evidence, so the precise pre-collector failure could not be audited.

## Falsifiable hypothesis

A wrapper-owned, pre-clipboard CreateNew reservation plus a canonical stage
ledger and explicit outer `$LASTEXITCODE` propagation can make every authorized
pre-collector outcome auditable without changing any Binance request, parser,
derived-row, join, temporal, or semantic rule.

## Single primary change

Only wrapper diagnostics and outer native exit propagation change. The exp009
collector and trusted loader mechanically change experiment/run/version paths
and seven binding paths from exp008; the five GETs and all data semantics must
remain value-equivalent.

## Failure conditions

- any real clipboard read, credential access, network call, or formal run during Phase2;
- any semantic difference from exp008 after identity/path normalization;
- reservation or ledger fails its frozen CreateNew/canonical/matrix contract;
- wrapper stdout/stderr contains evidence or secret-derived material;
- synthetic Windows PowerShell 5.1 tests do not prove exact native exit propagation;
- real exp009 final/staging/control/reservation/ledger exists at Phase2 freeze.

## Semantic ceiling

`NEEDS_MORE_DATA`; `planned_at_claim` is not effective time, historical status,
permission, listing interval, or eligibility. Eligibility/Alpha/IC/ML/backtest
remain prohibited.

