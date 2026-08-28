# exp_20260828_009 — zero-write preflight and artifact-authoritative planning

## Parent disposition

exp008 is preserved unchanged and received Fresh Phase2 `NO-GO` for two narrow
reasons: its launcher created `formal_control` before reuse preflight passed,
and its public registry class retained forgeable construction/introspection
paths.

## Falsifiable hypothesis

A separate v3 package can preserve all acquisition gates while making the
reuse preflight truly read-only until success and making production month
planning depend only on an exact registry artifact that the trusted planner
re-reads and independently revalidates against immutable source files.

## Changes allowed

1. Move every directory, reservation, staging, ledger, temporary and registry
   write strictly after read-only reuse preflight success. Failed preflight must
   leave the exact before/after filesystem tree identical.
2. Remove registry objects from public authority. The public planner accepts
   only the canonical registry artifact path and expected SHA-256, revalidates
   exp005 raw/sidecar, exp006 closure and bootstrap boundary raw/receipts at
   call time, verifies a private domain-bound registry value, then returns
   immutable plans. No caller object, mapping, subclass, copy or pickle can
   carry planning authority.

## Failure conditions

- any filesystem write or path creation occurs before reuse preflight PASS;
- drift failure changes any tree entry, size or hash;
- a caller object or fabricated registry path/hash can exclude a network date;
- planning succeeds without re-reading current source bindings;
- prior raw-first, exact schema, pagination, pacing, exact-once, no-clobber,
  session/date/month or monthly-disable gate regresses;
- frozen v1/v2 or exp007/008 evidence changes;
- key read, network, formal, monthly, training, inference, IC, P&L or backtest
  occurs in this phase.

The user has authorized future network acquisition, but this frozen-candidate
phase does not authorize formal execution.

## Success ceiling

`JQUANTS_V2_BARS_MONTHLY_ZERO_WRITE_PREFLIGHT_AND_TRUSTED_PLANNER_FROZEN / NEEDS_DATA`.
