# exp_20260828_003 — J-Quants V2 Free source probe contract V2

Parent failure: `exp_20260828_002` received Phase 2 FINAL NO-GO. Its evidence is
preserved unchanged. This independent follow-up repairs six audit blockers:
query semantics, local-key launcher, external source binding, trusted staging
rebuild, pagination/bijection closure, and observation-time semantics.

Hypothesis: a five-query, bounded, zero-retry V2 Free probe can be made
fail-closed and fully rebuildable without treating historical response dates as
historical publication/known-at evidence. The resulting raw source may support
forward observation and data-format work only; it cannot open historical Alpha.

Failure includes any source/hash drift, key disclosure, path/query mutation,
redirect, retry, page-chain inconsistency, incomplete exact-date coverage,
untrusted promotion, race overwrite, backdated known-at, or listing inference
from non-adjacent/one-off master snapshots.

Phase 1 permits only implementation and synthetic development tests. No real
key read, network request, raw payload, training, IC, P&L, or backtest is
authorized.
