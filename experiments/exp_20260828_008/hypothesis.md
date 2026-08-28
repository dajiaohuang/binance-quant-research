# exp_20260828_008 — trusted reuse registry binding repair

## Parent disposition

The exp007 candidate is preserved unchanged and received Phase2 `NO-GO` because
its reuse evidence was not bound into the production bootstrap/month-planning
path and its month-plan builder accepted caller-created `ReuseLeaf` values.

## Falsifiable hypothesis

A narrow v2 package can carry the frozen exp007 bootstrap security contract
while ensuring that only a reuse registry minted by the trusted loader from
exact source hashes can remove dates from monthly network plans.

## Single principal change

Create `jquants_v2_bars_monthly_v2` as a new package layered on the frozen v1
contract. Before any launcher or collector environment/key/network action, it
must verify the exact exp005 Q04 raw body, exact safe sidecar and exact exp006
closure and emit a Git-safe `EXP005_Q04_REUSE` entry for 2025-03-28 without
copying raw rows.

After the three-query bootstrap parses, the calendar must contain 2024-07-01,
2025-03-28 and 2026-05-29 as official `HolDiv` 1/2 session dates. The trusted
loader then mints boundary entries using the newly acquired raw and receipt
paths/hashes. Only that typed verified registry may build monthly plans. The
three source dates must be excluded from network dates in 2024-07, 2025-03 and
2026-05; the other 20 months must remain unchanged.

## Failure conditions

- any exp005 raw/sidecar or exp006 closure path, byte count or hash drifts;
- launcher or collector reads/exports the key or sends a request before reuse
  verification succeeds;
- a caller-created/unverified leaf or registry can affect a production plan;
- any required reuse date is not an official bootstrap session;
- a registry date, month, source kind, source path, receipt path or hash is
  missing, duplicated, mismatched or outside the immutable bootstrap plan;
- any of the three reuse dates remains in `network_dates`, or another date is
  removed;
- the frozen v1 or exp007 evidence changes;
- monthly CLI/network, formal bootstrap, training, inference, IC, P&L or
  backtesting becomes authorized in this phase.

## Phase boundary

This phase allows preregistration, offline implementation, frozen-source
read-only hash verification, synthetic targeted tests, Python compilation,
PowerShell parsing, dry-plan generation and fresh candidate freezing. It forbids
key reads, network access, formal execution, monthly acquisition, the full
repository suite and empirical research.

## Success ceiling

`JQUANTS_V2_BARS_MONTHLY_REUSE_BINDING_FROZEN / NEEDS_DATA`.
