# exp_20260827_005 — HORIZON_IDENTITY_END_TO_END_V1

## Pre-registration state

This experiment was pre-registered before any kernel or test edit. Phase 1 authorizes only offline synthetic development and preparation for a fresh Phase 2 review. It does not authorize formal execution, empirical Alpha research, IC measurement on real data, ML, P&L, backtesting, validation-set access, or test-set access.

## Observation

The frozen precondition kernel SHA-256 is `2637ac6b686ef0d6a0a7dc4c07817fd57e2f2724984cdcccac1671528c7044c5`. Its `ExpertKey` carries `horizon_hours`, but `combine_hierarchical` can accept a registry mixing 1-hour and 24-hour experts and emits one scalar `EnsembleScore` without a horizon identity. Downstream regime and expected-net-alpha records also omit that identity.

## Single hypothesis

Making horizon identity an exact, end-to-end contract on ensemble, regime-adjusted, net-alpha, diagnostic, and multi-horizon bundle records will fail closed on mixed-horizon composition while preserving existing single-horizon numerical behavior. A multi-horizon bundle will be an identity-preserving container only, not an economic cross-horizon composer.

## Required change

- Require exact built-in integer horizons from `(1, 24, 120, 480)` at every affected consumer boundary.
- Preserve one horizon through hierarchical combination, regime adjustment, expected-net-alpha calculation, and diagnostic projection.
- Add an immutable four-horizon bundle whose only purpose is identity-preserving transport.
- Reject mixed horizons, future clocks, malformed hashes, forged arithmetic, old scalar projection, and non-exact integer horizon aliases.

## Failure conditions

The hypothesis fails if any affected consumer accepts mixed or non-exact horizons, if 24-hour values or weights change, if the bundle acquires economic aggregation semantics, if provenance does not bind horizon identity, or if the existing synthetic PIT gate and 35-test kernel contract regress.

## Semantic ceiling

Even on success the terminal status is at most `NEEDS_MORE_DATA`. Historical eligibility remains unavailable, no empirical source is authorized, and strict eligible count remains zero.
