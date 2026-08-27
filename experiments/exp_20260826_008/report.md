# exp_20260826_008 report

## Current state

`FORMAL_EXECUTED_AWAITING_POSTFLIGHT_AUDIT / INCONCLUSIVE`

The independent collector, trusted loader, Windows PowerShell wrapper, tests,
and seven-file Phase 2 v2 freeze are complete. The first Phase 2 review returned
NO-GO on two wrapper-only fail-closed gaps; those gaps were fixed and refrozen.
Both Researcher and Auditor subsequently returned v2 FINAL GO. Formal execution
is authorized only after the user copies a compliant read-only API Key and uses
the exact frozen command whose UTF-8/no-newline SHA-256 is
`ee744da16648cfad8857c05d01b02c9d1274c439134ee1b3789d19b0d01d1c70`.
The user then confirmed that an API Key had been copied. The exact frozen
wrapper command was executed once. It returned observed exit code 1 after
0.2857559 seconds with empty stdout and stderr. It created no final, staging,
or control path, so the collector never reserved the run and made zero network
requests. Postflight found the clipboard empty and the key environment absent.
The run ID is consumed by policy and must not be retried or resumed.

Because no formal artifact exists, the trusted loader and tree verification
are not applicable. The terminal result is `INCONCLUSIVE`, pending Auditor
postflight review. The wrapper intentionally emits no pre-collector diagnostic,
so the evidence does not distinguish an empty/invalid clipboard from another
prelaunch wrapper failure.

## Intended evidence

The experiment will freeze five exact response bodies and five safe receipts,
then derive `plans.jsonl`, `current_symbols.jsonl`, `joins.jsonl`, and a summary
inside one atomically published run root. Exact joins may be `MATCHED` or
`MISSING`; symbol text is never decomposed to infer assets.

## Frozen files

| File | SHA-256 |
|---|---|
| wrapper | `0121c290f141d0c977a9b1c1d34528c29c01b908bed31c1b9263f8d251a26c4e` |
| collector | `593281457fbe451ad252e917e9d3fbdc1c888151d8217c1221735e8b49f9552f` |
| trusted loader | `d1522b0d73589d8e5be31cc3257d681f7c07ac2cffd4b4ca81fcd2c5c5726164` |
| source contract | `cb66a8564377af2d8d5198e8185f475ee3d1829560089f4c51a59405ab5eea29` |
| schema | `25f5295266246ed6cec3475c10157c8b642590715d69221d166a2501e8eb47d4` |
| parameters | `ff18f7d47eed18cbccedf2cdc667ccd55c66daa79827d03835df1bd24e630a26` |
| tests | `01eaeceee58ba8bc377523a93397aee5277448b7a3d17840495bdd0714ff7426` |

The exact 771-byte formal command has UTF-8/no-newline SHA-256
`ee744da16648cfad8857c05d01b02c9d1274c439134ee1b3789d19b0d01d1c70`.
It contains no API key.

## Offline verification

- targeted mock/security/lifecycle suite: 35/35 passed;
- full repository after authorized exp007 lifecycle-test maintenance: 343/343
  passed;
- py_compile, PowerShell parse-only validation, and strict experiment JSON:
  passed;
- real exp008 final, staging, and control paths: absent;
- formal executions: 1; real network requests: 0; no formal artifacts.

The first targeted run passed 26/28. Both failures came from scanning the
copied test source itself, which contains the sentinel constant, as if it were
an output. The scan was narrowed to final/staging/control plus captured
stdout/stderr/log evidence; no collector semantics changed. A later
PowerShell cleanup harness was also corrected to use `ExecutionPolicy Bypass`.
The first explicit parse-only shell command had an outer-variable quoting
error; the correctly isolated parse command passed.

The wrapper now hashes its own whole bytes from `$PSCommandPath` before the
first clipboard call. A .NET `ReadAllBytes`/SHA-256 path is used because the
isolated Windows PowerShell 5.1 test host did not expose `Get-FileHash` without
module auto-loading. After a valid key is read, the prelaunch clipboard clear
must succeed before the environment is set or the collector starts. The final
cleanup re-clears clipboard/environment/key variables; a collector nonzero is
preserved, while a collector zero plus cleanup failure becomes fixed nonzero.
Drift, prelaunch-clear failure, final-clear failure after a zero fake collector,
and stdout/stderr secret scans all pass. The temporary Python REPL used to
diagnose the PowerShell host was exited; its four reported process IDs are gone.

## Key handling and user procedure

Create or reuse a Binance API key with Spot & Margin Trading, Futures,
Withdrawals, and other trading/transfer permissions disabled; an IP whitelist
may be added. Copy only the API Key—not the Secret—and do not paste it into
chat or a command line. After dual Phase 2 approval, copy the key to the
clipboard and run only the frozen wrapper command. The wrapper reads it with
`Get-Clipboard -Raw`, injects it temporarily into the child environment, and
clears the environment, clipboard, and key-bearing variables in `finally`.
An empty, whitespace-only, multiline, or NUL clipboard causes zero collector
requests and zero experiment filesystem writes.

## Referenced Skill

`.codex/skills/quant-strategy-research/SKILL.md` and root `AGENTS.md` were read.
The Skill-linked `references/methodology.md` and
`references/experiment-contract.md` are absent in this checkout.

## Restrictions

No real network, formal execution, credential capture, eligibility, Alpha,
Factor, IC, ML, P&L, or backtest occurred. The terminal ceiling remains
`NEEDS_MORE_DATA`.
