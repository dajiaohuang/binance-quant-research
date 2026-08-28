# exp_20260828_008 report

## Result

The narrow reuse-binding repair is frozen and paused for a Fresh Phase2 audit.
The allowed ceiling is
`JQUANTS_V2_BARS_MONTHLY_REUSE_BINDING_FROZEN / NEEDS_DATA`.

The exp007 candidate received Phase2 `NO-GO`; its 11-file freeze manifest and
all frozen v1 sources revalidated unchanged. exp008 creates a separate v2
package and does not modify them.

## Repair

The production order is now:

1. verify launcher and the 17-file candidate freeze;
2. perform an exact, read-only exp005 Q04 raw/sidecar and exp006 closure check;
3. acquire the O_EXCL control reservation;
4. acquire the O_EXCL raw-attempt reservation and write the Git-safe source
   registry into that attempt staging with no-clobber semantics;
5. only then inspect/import the environment key;
6. collector independently revalidates both immutable sources and the
   attempt-owned registry before key validation or transport use.

After synthetic Q1/Q2/Q3 parsing, the trusted loader requires 2024-07-01,
2025-03-28 and 2026-05-29 to be official sessions. It mints exactly three
source-bound entries: first bootstrap edge, `EXP005_Q04_REUSE`, and last
bootstrap edge. The month planner accepts only its immutable typed registry.
The positive fixture excluded exactly those three dates from 2024-07, 2025-03
and 2026-05; the other 20 monthly plans were byte-semantic unchanged with
`network_dates == session_dates`.

Negative coverage rejects raw, sidecar and closure drift; caller leaves;
non-session boundaries; missing/wrong-month/wrong-kind/wrong-path/wrong-hash and
duplicate registry entries; registry/plan mismatch; pre-existing attempt and
registry races. Monthly CLI/network remains absent.

## Offline evidence

- v2 initial run: 22 passed, 1 error of 23. The new loader had delegated final
  acquisition-manifest verification to the frozen v1 function, whose expected
  run ID is exp007. A narrow v2 verifier fixed only that version binding.
- v2 repaired run: 23/23 passed.
- v2 final run after adding wrong-registry-month coverage: 24/24 passed.
- frozen v1 regression: 38 tests OK, 37 passed and one real Windows symlink
  creation test skipped for unavailable privilege.
- Python compile, PowerShell parse and read-only dry plan: PASS.
- exp007 frozen candidate unchanged: PASS.
- exp008 17-file candidate freeze SHA-256:
  `758be2c679ba655723c8df91aba30aa48b1937beb42870e15af7c35b7bde9598`.

No key was read, no network or formal request was made, no registry or licensed
row was emitted before an attempt reservation, and no monthly acquisition,
training, inference, IC, P&L or backtest is authorized.
