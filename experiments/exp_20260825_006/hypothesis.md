# exp_20260825_006 — Data-agnostic fail-closed Hierarchical Alpha Research Kernel

## Observation

`exp_20260825_005` established a checksum-bound, reason-coded
`ARCHIVE_KLINE_AVAILABLE` panel. It explicitly did not establish point-in-time
`TRADING`, SPOT permission, quote asset, listing state, eligibility or
executability. Therefore no real cross-sectional factor, score, rank IC, expected
return, P&L, model fit or backtest is currently authorized.

Modern alpha research still needs a small, auditable method kernel whose APIs make
the missing evidence impossible to bypass. The kernel can be tested entirely with
synthetic fixtures before any historical universe is available.

## Falsifiable method hypothesis

A deterministic, data-agnostic Hierarchical Alpha Research Kernel can enforce all
of the following contracts without reading real market data:

1. point-in-time evidence and observation clocks are mandatory and fail closed;
2. expert identities, horizons and fixed weights are immutable and duplicate safe;
3. winsorization, ranking and residualization are isolated to one formation time;
4. forward labels begin at the next bar open and remain separate from features;
5. purge and embargo intervals cover at least `horizon + 1` bars;
6. hierarchical ensemble weights are finite, nonnegative simplexes and are never
   silently renormalized when an expert is missing;
7. an externally supplied, frozen regime multiplier is finite and bounded in
   `[0.7, 1.3]`, preserves direction, and cannot act as a learned router; and
8. expected net alpha subtracts only explicit nonnegative cost, uncertainty and
   crowding penalties.

The hypothesis fails if any required invariant can be bypassed by input order,
future appends, another time slice, noneligible symbols, missing experts, duplicate
keys, nonfinite values, rank-deficient regressions, incomplete labels or invalid
PIT semantics.

## Single primary change

Add only the data-agnostic method kernel and its synthetic unit tests in a later
implementation phase. Do not add a strategy, data loader, CLI, optimizer, model,
portfolio simulator or backtester.

## Frozen type and evidence boundaries

### PIT eligibility

- PIT membership and observation-clock evidence are distinct required types.
- Every expert input must have an explicit symbol, formation time, evidence time
  and eligibility state.
- Evidence known after formation time is future information and must be rejected.
- `UNKNOWN` is fail-closed and cannot become eligible.
- A missing symbol-time membership is a contract error, not an implicit UNKNOWN.
- Archive availability states `A/N/M/U` are a different semantic type and can
  never be passed as PIT eligibility.
- The exp005 panel SHA-256 in `manifest.json` is retained only as blocking
  evidence for a negative gate test. Its market rows are not an experiment input.

### Expert registry

- An expert key contains a stable family, name, horizon and version.
- Keys are globally unique; duplicate registration is a hard error.
- Each expert declares its required inputs, observation-clock rule and output
  direction before evaluation.
- Registry order cannot affect results.

### Single-time cross-sectional transforms

- Winsorization, average-tie ranking and residualization operate on exactly one
  UTC formation time and only on eligible symbols.
- Values from another formation time and noneligible symbols cannot change the
  current result.
- Nonfinite inputs, insufficient breadth and duplicate symbol-time keys fail.
- Residualization includes a declared intercept policy, checks matrix rank and
  verifies residual orthogonality within numerical tolerance.
- No full-sample normalization or cross-time fit is allowed.

### Labels and overlap control

- Horizons are exactly `1`, `24`, `120` and `480` hours.
- A score formed from bar `k` information receives its entry price from bar
  `k + 1` open. A horizon-`h` label exits at bar `k + h + 1` open.
- Labels are evaluation-only values and are not accepted by expert APIs.
- All interval arithmetic is UTC and half-open.
- For horizon `h`, both purge and embargo are at least `h + 1` bars. Overlapping
  formation/label intervals across train and evaluation partitions are rejected.

### Fixed hierarchical ensemble

- Within-family expert weights are finite, nonnegative and sum to one.
- Across-family weights are finite, nonnegative and sum to one.
- Weights are externally frozen before evaluation.
- Missing expert output fails the formation time; remaining experts are not
  silently renormalized.
- There is no learned router, optimizer or data-dependent weight fitting.

### External regime multiplier

- The multiplier is supplied by an external, frozen specification with PIT clock
  evidence; the kernel does not learn or infer regimes.
- Missing, nonfinite or out-of-range values are rejected.
- Allowed range is inclusive `[0.7, 1.3]`.
- Multiplication preserves the sign of a nonzero ensemble score and can amplify
  its magnitude by at most `1.3`.

### Expected net alpha

Expected net alpha is a diagnostic contract, not P&L:

```text
expected_net_alpha
= regime_adjusted_expected_gross_alpha
- explicit_trading_cost_penalty
- uncertainty_penalty
- crowding_penalty
```

Cost, uncertainty and crowding penalties must each be finite and nonnegative.
The frozen one-way cost scenarios are `0.00150`, `0.00225` and `0.00300`.
Increasing any penalty while holding all other inputs fixed cannot increase
expected net alpha.

## Preregistered synthetic tests

The later implementation must cover:

- future-append invariance;
- time-slice isolation;
- noneligible-symbol invariance;
- duplicate expert and duplicate symbol-time key rejection;
- deterministic average-tie ranking;
- low-breadth and nonfinite input rejection;
- residual orthogonality and rank-deficiency rejection;
- label entry at `k + 1` open and exit at `k + h + 1` open;
- all four horizons and UTC interval boundaries;
- purge and embargo of at least `h + 1` bars;
- missing expert rejection without weight renormalization;
- within-family and across-family simplex validation;
- regime multiplier bounds, sign preservation and missing-value rejection;
- expected net alpha monotonicity under cost, uncertainty and crowding penalties;
- archive-semantics rejection at the PIT gate.

## Success and failure conditions

Success requires every preregistered synthetic test to pass and an independent
audit to confirm that no real exp005 market row, real factor/score/IC/P&L, model
fit, backtest, network call or CLI path was used. The terminal state remains
`NEEDS_MORE_DATA` because PIT eligibility evidence still does not exist.

Any test failure, semantic bypass, undeclared data access or inability to prove
time isolation produces `INCONCLUSIVE`. No empirical alpha conclusion is allowed
from this experiment.

## Contract revision 1 — pre-execution audit blockers

Revision 1 is authoritative where it conflicts with the original preregistration
above. The original contract remains in this file as the revision-0 historical
record; it was never executed. This revision was made before any test or
validation command. Execution status remains **NOT EXECUTED**.

The audit found that a caller-provided eligibility state, an ambiguous formation
clock, coarse label intervals and a declarative-only purge contract could permit
semantic bypasses. Revision 1 therefore freezes these corrections:

1. Eligibility is derived by the kernel, never supplied as `ELIGIBLE`. A
   `POINT_IN_TIME_BINANCE_SPOT_ELIGIBILITY` snapshot must explicitly expect
   Binance, SPOT and USDT and provide per-symbol venue, market type, trading
   status, spot permission, quote asset, listing effective interval, observation
   clocks, typed evidence kinds and SHA-256 provenance. Missing or `UNKNOWN`
   components fail closed; archive-derived evidence, raw booleans/dictionaries,
   archive availability, missing membership and duplicate membership are rejected.
2. The only clock is a one-hour closed-bar clock. For feature bar `k`, decision
   and feature `known_at` equal `feature_bar_open + 1h`; entry `k+1` open equals
   decision time; exit `k+h+1` open equals `decision + h hours`. A synthetic open
   price is known exactly at its open time. A label carries all feature, decision,
   entry, exit and observation clocks.
3. A label's real information interval is
   `[entry_time, exit_time + 1 millisecond)`. Boundary tests include adjacency,
   overlap and offsets of one millisecond and one hour.
4. Purge/embargo validation operates on actual UTC milliseconds and requires a
   `PurgeEmbargoSpec`. It rejects train/evaluation label overlap, split folds at
   the same formation, insufficient pre-evaluation purge and insufficient
   post-evaluation embargo, including irregular timestamps. The shared four-
   horizon contract is `max(1,24,120,480)+1 = 481` bars.
5. Ranking is centered average midrank on `[-1, 1]`: three unique observations
   rank `[-1, 0, 1]`, while a bottom tie ranks `[-0.5, -0.5, 1]`; ranks sum to
   zero. There is no mixed-time selector. Every transform receives exactly one
   formation or fails.
6. Only provenance-bound `HIGHER_IS_BETTER` experts whose readiness is
   `SYNTHETIC_READY` may enter the exp006 synthetic ensemble. The readiness
   catalog remains deliberately blocked (`PIT_BLOCKED` or `DATA_BLOCKED`) and
   cannot be combined. Full `ExpertKey` coverage and both frozen simplexes remain
   mandatory.
7. A provenance-bound direct `EnsembleScore` is multiplied exactly once by one
   frozen PIT regime scalar for its formation. The multiplier does not apply to
   penalties. Expected net alpha accepts only the resulting
   `RegimeAdjustedScore` and preserves symbol, formation, clock and provenance.
8. Two non-fitting synthetic diagnostics are admitted: same-formation,
   same-horizon, fully aligned rank IC; and a fixed full-versus-ablated diagnostic
   reporting `full_ic`, `ablated_ic` and `delta_ic`. Missing labels, wrong clocks
   or horizons, incomplete keys, tied ranks and low breadth are rejected.

All prior prohibitions remain: no exp005 market-row read, real factor/score/IC/P&L,
model fit, backtest, network operation or CLI. Passing later synthetic tests can
only produce `NEEDS_MORE_DATA`; any invariant failure produces `INCONCLUSIVE`.

## Contract revision 2 — pre-execution direct-construction bypasses

Revision 2 is authoritative over revisions 0 and 1 only where they conflict.
Both earlier contracts remain above as historical records, and no test or
validation command has been executed under any revision. Execution status is
still **NOT EXECUTED**.

The second pre-execution audit found four constructor-level bypasses. Revision 2
therefore requires identical validation whether `PurgeEmbargoSpec` and
`ExpertRegistry` are created directly or through builders; revalidates every
directly constructed `RegimeAdjustedScore` when expected net alpha consumes it;
and binds synthetic IC inputs to an exact, provenance-bearing `DiagnosticScore`
whose horizon must match both labels and the requested diagnostic. These guards
cover allowed unique horizons, the fixed one-hour bar duration, integer 481-bar
shared boundaries, full expert specification validation, finite and bounded
regime multiplication identity, PIT clocks, duplicate keys, minimum breadth and
complete score-label alignment. All existing prohibitions and terminal statuses
remain unchanged.
