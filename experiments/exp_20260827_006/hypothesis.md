# exp_20260827_006 — DDGL synthetic contract

## Classification

- Research classification: `METHOD_REPRODUCTION_ON_NEW_DATA`
- Stage: `SYNTHETIC_CONTRACT_ONLY`
- Empirical authorization: `false`
- Maximum terminal status: `NEEDS_MORE_DATA`

## Source identity and limits

The source identity was supplied by the Research Lead and is not independently
downloaded or executed in this experiment:

- paper DOI: `10.1145/3770855.3817765`;
- venue metadata: KDD 2026, CC BY 4.0, reported as Crossref-confirmed by the Lead;
- community repository: `doitforlove/DDGL-Net-2026`;
- community commit: `9c1152d8572550d0a869d898f65f208c52706747`;
- community license: MIT;
- source status: `UNVERIFIED_THIRD_PARTY`.

The implementation is a clean-room, independently written synthetic contract.
It is not official code, is not copied from the community repository, and must
not be described as paper-faithful without a later source-fidelity audit.

## Observation

The repository has fail-closed PIT, horizon, label, purge/embargo, ensemble and
provenance primitives, but no trainable neural-model boundary.  PyTorch is not
currently installed and empirical PIT eligibility is not authorized.

## Falsifiable hypothesis

A small independently written DDGL-shaped model can accept strictly separated
coarse, fine and global-market synthetic inputs, preserve clocks and horizon
identity, learn through a finite deterministic MSE update, round-trip a
weights-only checkpoint, remain permutation equivariant over assets, and emit
synthetic-only `ExpertOutput` values without enabling empirical research.

## Single primary change

Add one synthetic DDGL model contract: DDGE-style temporal/cross-sectional
encoders for fine and coarse inputs, MACM-style feature-wise fusion with global
market context, and a base-plus-residual mixture-of-experts head.  Add only the
dependency, configuration, tests and CLI needed to validate that contract.

## Success conditions

- strict configuration rejects unknown or empirical fields;
- invalid shape, clock, horizon, provenance and label placement fail closed;
- CPU forward and backward are finite and at least one parameter updates;
- weights-only checkpoint load reproduces inference;
- asset permutation and global-market broadcasting satisfy the frozen tests;
- synthetic inference cannot accept labels;
- empirical registration remains rejected;
- targeted and repository tests pass;
- CPU and, only if the resource gate permits, guarded GPU smoke emit auditable
  metrics without market data, IC, P&L or backtesting.

## Failure conditions

Any non-finite result, hidden empirical path, label leakage, clock violation,
permutation failure, checkpoint mismatch, memory-cap violation, test failure or
dependency incompatibility makes the experiment `INCONCLUSIVE`.  A passing
synthetic contract remains `NEEDS_MORE_DATA`, never evidence of Alpha.

## Explicit non-goals

No market data, archive payload, validation/final split, IC, P&L, backtest,
portfolio allocation, hyperparameter search, external source execution or
paper-fidelity claim is authorized.

