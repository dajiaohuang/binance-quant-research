# exp_20260826_007 report

## Current state

`FORMAL_EXECUTED_ONCE / POSTFLIGHT_AUDITED / NEEDS_MORE_DATA`

After independent Researcher and Auditor Phase 2 FINAL GO, the single frozen
formal command ran exactly once and exited 0 in 3.1896848 seconds. The run is
consumed and must not be rerun. The trusted loader accepts the committed result
as `NEEDS_MORE_DATA / ANNOUNCEMENT_SCHEDULE_CLAIM_SCAN_COMPLETE`; independent
Auditor postflight accepted the frozen evidence as PASS.

## Single change

The successor preserves LEAN_R2_2_2 semantics byte-for-value while adding a
safe fixed-parent bootstrap before atomic control reservation. The failed
exp006 source and consumed run remain untouched.

The new runner validates fixed drive/commonpath relationships and every
existing repo/data/processed/version/runs component by `lstat`, rejecting
symlinks, Windows reparse points, files, path escape, and cross-drive layout.
Only the fixed version and runs shared parents may be created before control.
Atomic control creation reserves the run; a lease failure retains control but
cannot create a failure conclusion. After lease success, controlled failures
write `failure.json`. Promotion remains a same-parent, same-drive rename.

## Frozen bindings

| File | SHA-256 |
|---|---|
| runner | 9c6c74b36aef30ce1159124f7d546b24328e032d9990c59c4cd449b9a3eb8b70 |
| extractor | d3c38f472132753038db15b9d7fca376f685e7be43e5b9050bf663f000a26fb3 |
| loader | a99e47acd5c4de4bf9a444063a878ffc0fb334786d797cf60f38280c027d2ce2 |
| source contract | 62d1f3658e285d0444863f35fe56a7756f954777d682d23edfb4bb8d0ba11f66 |
| schema | e0b75fb9cd4d82cf7b84461eff795b91ff3a1ff6d091e4a97c3c108d9778ef8c |
| parameters | 0a10cf6a52051c99cb1a5983a4841430536da2a27eb8eef2d96f20b31aa69037 |
| tests | 91cdcf8f7955e8a4dea4f77f3d1e26063a4770baa451b6132ccf724f5144875d |
| semantic diff | 5ba1fe1e0417b76dc83d767c4b28182736f71a274db2d1958ab9485d68036522 |

The exact formal command UTF-8 bytes without newline have SHA-256
`86abaf291de174a8abdc292bf7b2d1ba8ccb7d5b45f5b05608eff3d607e8260a`.

## Offline verification

- inherited and V1.1 targeted suite: 40/40 passed;
- full repository: 308/308 passed;
- six frozen fixtures: 20 syntactic claims in the fixture-only assertion;
- accepted-detail and coverage closure: 756;
- py_compile, strict experiment JSON, and read-only validation of the actual
  workspace parent layout: passed;
- exp006 frozen source hashes and absent formal paths: unchanged;
- v2 final, staging, and control paths: absent.

The absence statements above describe the Phase 2 pre-execution freeze. At
formal preflight the same three paths were independently confirmed absent.

The first v2 narrow run passed 39/40. Its only failure was a test comparing
one extra terminal LF in the independently copied source; the comparison was
changed to ignore terminal newline count. No renderer, grammar, evidence,
claim, input, lifecycle, or output behavior changed.

## Formal execution and committed evidence

- preflight observation: `2026-08-26T13:05:31.1787675+08:00`;
- exact command SHA-256: `86abaf291de174a8abdc292bf7b2d1ba8ccb7d5b45f5b05608eff3d607e8260a`;
- all six frozen bindings matched and final/staging/control were absent;
- exit code: 0; wall: 3.1896848 seconds; stdout/stderr: 0 bytes each;
- transcript: `logs/formal_execution.txt`, 1,073 bytes, SHA-256
  `76f83302c8a17037decd6cd78908cd7a5624695deac22c5085789e050406b1ab`;
- final and control exist; lease and authorization exist; staging and failure
  are absent;
- lease SHA-256: `25ccf2cdfc14f8477f43249b00582b87dd99fbc41e15e00171710b1980a7ca26`;
- authorization SHA-256:
  `e9d1a8c39b44bb0129166ebac6abacc2bec8e63ddf2e5f90ad8df890a5990e16`;
- payload tree: `e6d8ebdc9f9095d4620b2508310bde5bffdc52398a332ae6019b8bf9079e41e2`;
- final tree: `0c5ee97d978b24ebaea928d3a5ec5eeae989827b6d3abdf1300bd53799396c0b`.

| Artifact | Rows | Bytes | SHA-256 |
|---|---:|---:|---|
| `claims.jsonl` | 623 | 1,173,141 | `5cde1e1970ffa6d6c625ddcae4fb505e3d1bf7667198fc2bcb2ba9372263b605` |
| `ambiguity.jsonl` | 44 | 67,841 | `d9c299a5c75dd84dd44e86a937a90e55826374685debd5a23f3a951f9512f66f` |
| `coverage.jsonl` | 756 | 236,697 | `7efd431c29095beb6fb76ac99445a49756456306180aa72441d18f456c4db371` |
| `summary.json` | n/a | 3,854 | `361a1e5dcc3dd6cb688c0743e4a0eb455bf244eca9e5eb77a9b486ed943e07ac` |

The 756 coverage rows split into 83 `CLAIMED`, 44 `AMBIGUOUS`, and
629 `NO_MATCH`. There are 623 syntactic schedule claims: 76 OPEN and 547
REMOVAL. The six frozen positive fixtures contributed their expected 20 claims
with exact pair tokens and epochs.

The first read-only postflight helper incorrectly accessed a non-contract
`LoadedClaims.coverage_count` attribute and raised `AttributeError`; it did not
write or alter formal evidence. The corrected trusted-loader call passed. A
postflight selection of the targeted suite passed 39/39; the one excluded test
is the intentionally preflight-only assertion that formal paths are absent.

After postflight, that lifecycle test was maintained to read the audited
manifest: before formal it requires absent paths; after formal it requires
final/control/lease/authorization and rejects staging/failure. Its current SHA
is `a518dadf8ef44a3072bffaca37c4a05d917fc7d53ab625e29aae82a852c44cb4`.
The formal-time frozen test SHA remains preserved in the manifest and was not
retroactively replaced.

## Referenced Skill

`.codex/skills/quant-strategy-research/SKILL.md` and root `AGENTS.md` were read.
The two Skill-linked reference pages are absent from this checkout.

## Restrictions

No network, account, credential, eligibility, Alpha, Factor, IC, ML, P&L, or
backtest occurred. Formal execution occurred exactly once after dual Phase 2
GO. `network_access=false`, `historical_eligibility_ready=false`,
`eligibility_evaluated=false`, and `strict_eligible_count=0`.

This is not historical eligibility evidence and not a strategy result. The
formal terminal ceiling remains `NEEDS_MORE_DATA`. Independent postflight audit
passed; the run remains consumed and rerun is forbidden.
