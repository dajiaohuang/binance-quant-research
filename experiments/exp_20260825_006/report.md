# exp_20260825_006 terminal report

## Decision

`INCONCLUSIVE` (terminal).

The revision-2 fail-closed kernel did not clear its first narrow synthetic-test
gate. Around `2026-08-25T20:05+08:00` (approximate; no exact completion timestamp
was captured), the preregistered narrow command ran 35 tests: 34 passed and one
ended in an error. The full-repository test suite was not run.

## Failure evidence

Command:

```text
uv run python -m unittest tests.test_hierarchical_alpha -v
```

Environment versions reported for the failed run were NumPy `2.5.1` and SciPy
`1.18.0`. The residualization test reached
`residualize_cross_section`, which passed Python `list` objects into
`scipy.linalg.lstsq(matrix, target_vector)`. SciPy attempted to access an input
`.shape` and raised:

```text
AttributeError: 'list' object has no attribute 'shape'
```

The key traceback is preserved in `logs/narrow_test_attempt_1.log`. This is an
implementation compatibility error, not evidence for or against any alpha
hypothesis. No mathematical output from the failing residualization path is
accepted.

## Scope and integrity

Contract revisions 0, 1 and 2 remain preserved. No implementation or test was
changed inside this experiment after the failed run. No exp005 market rows, real
factor values, scores, IC, returns, turnover or P&L were read or produced. No
network access, model fit, backtest, CLI, dry-run or live-trading command was
used.

Because the narrow gate failed, repository regression was correctly withheld.
A single-change successor must preregister the input conversion before modifying
the kernel. exp006 cannot be reopened or relabeled as successful.
