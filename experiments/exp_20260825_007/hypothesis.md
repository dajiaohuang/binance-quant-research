# exp_20260825_007 — Explicit NumPy conversion before SciPy least squares

## Parent observation

`exp_20260825_006` terminated `INCONCLUSIVE` after its first narrow synthetic
test run executed 35 tests, with 34 passing and one error. In NumPy `2.5.1` and
SciPy `1.18.0`, `residualize_cross_section` passed Python lists directly to
`scipy.linalg.lstsq`. SciPy accessed `.shape` and raised
`AttributeError: 'list' object has no attribute 'shape'`.

The failure occurred before the residual orthogonality assertions and is an
input-container compatibility defect. It provides no empirical alpha evidence.

## Falsifiable hypothesis

Converting only the completed design matrix and target vector with
`numpy.asarray(..., dtype=float)` immediately before `scipy.linalg.lstsq` will
make the residualization path compatible with SciPy `1.18.0` while preserving
the frozen least-squares mathematics, API and all revision-2 contracts.

The hypothesis succeeds only if the frozen narrow suite passes all 35 tests and
the full repository suite then passes. Any narrow failure, regression failure,
contract change or unregistered additional implementation change closes this
successor as `INCONCLUSIVE`.

## Single allowed change

In `residualize_cross_section`, immediately before the existing least-squares
call:

```python
design_array = numpy.asarray(matrix, dtype=float)
target_array = numpy.asarray(target_vector, dtype=float)
```

Pass those two arrays to the existing `scipy.linalg.lstsq` call. Variable names
may differ, but there may be no other behavioral change. NumPy `2.5.1` is already
present through the repository's direct SciPy dependency, so this experiment
adds no dependency and changes no dependency declaration.

## Frozen inputs

- Pre-fix module SHA-256:
  `c19fd1953b18205d4757e54d67b4d1127dd11d2f52f47738a2b9d9159275601e`
- Frozen test SHA-256:
  `6d13ba56248305b9990f9422be79d476b64228d266691f4b6db8ac9416935870`
- Parent environment: NumPy `2.5.1`, SciPy `1.18.0`.

The mathematical design, intercept policy, rank check, residual calculation,
orthogonality tolerance, return type and public function signature are frozen.
The test file and all PIT, clock, label, purge/embargo, expert, ensemble, regime,
penalty and diagnostic contracts are frozen.

## Execution order and decision

First run only the 35-test narrow suite. Run the full repository suite only if
all 35 narrow tests pass. Success remains `NEEDS_MORE_DATA`, because a working
synthetic kernel does not supply the missing point-in-time Binance Spot
eligibility evidence or any real alpha result. Failure is `INCONCLUSIVE`.

No real data, network operation, factor/score/IC/P&L calculation, model fit,
backtest or CLI is authorized.
