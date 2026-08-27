# exp_20260826_006 report

## Current state

FORMAL_EXECUTED_ONCE / POSTFLIGHT_AUDITED / INCONCLUSIVE

The Researcher and Auditor issued Phase 2 FINAL GO. The single frozen formal
command was executed exactly once. It exited with code 1 before creating the
lease or reading any of the 756 accepted detail inputs. The run ID is consumed
and must not be retried. The earlier R5 runtime-forensics design remains
superseded.

## Formal execution outcome

- command SHA-256 (UTF-8, no newline):
  `182eb47739ac726331014569efa9b1f5c8e15dda60895afe2d2216e888a0f372`;
- observed exit code: `1`;
- observed wall time: `0.1396267` seconds;
- failure: `CONTROL_ROOT.mkdir(parents=False, exist_ok=False)` raised
  `FileNotFoundError: [WinError 3]` because the parent directory
  `data/processed/binance_spot_announcement_claims_v1/runs` did not exist;
- lease, authorization, failure, final, staging, and control artifacts: absent;
- claims, coverage, ambiguity, summary, and payload-tree hashes/counts:
  unavailable because extraction never began;
- network access: zero.

The traceback was emitted by Python and observed in the formal command output.
The complete combined tool output is preserved at
`logs/formal_execution.txt` (1,275 bytes, SHA-256
`fa1651b3cb1e491135d6d7e42a962eac1f12de83095e8048f0fa91994e94b880`).
Because the failure happened before the runner's controlled-failure region,
there is no authoritative `failure.json`. This is an uncontrolled pre-lease
failure, not a corpus or claim result.

## Implemented scope

- minimal revalidation of the 756 frozen accepted details, including the
  detail-index/ledger/receipt/accepted-response bijection;
- DOM textContent renderer, source atoms and exact action/time/pair spans;
- conservative OPEN/REMOVAL wrappers and article-level fail-closed ambiguity;
- exact four-file schemas and 756-article coverage closure;
- six externally supplied code/contract SHA bindings at start and immediately
  before authorization;
- exclusive lease, staging validation, write-once authorization, atomic rename,
  controlled failure preservation, and committed trusted reload.

The six frozen positive fixtures rebuild to their specified 20 syntactic claims
inside the offline test gate. That is a fixture assertion, not the formal
corpus result. No Phase 1 feasibility estimate is used as an acceptance count.

## Frozen bindings

| File | SHA-256 |
|---|---|
| runner | 8f068d89e0fa66840e12706aa8954ca8e5585d271b2a76893abb385f182abe0b |
| extractor | 552aba2e02db3c0d1ad4e500bac7f3036d24d150abd74dd4ac7e756803f15dac |
| trusted loader | 76cc371b6f10e2f87b92d7fcc71e648c245fffe8a3f42b83d6be8cc4b7dad347 |
| source contract | 87f02dc043fecf8e9fb4b449c83ea60e4b6918216620d4a623219e1151ad0599 |
| schema | be475290e4b4626078ad4b103d513e07aff6cf71f22d2e09969e141fdcc32c88 |
| parameters | 24042cd2e5a99b9cf5b8a2ef42d3473b644c44fac8c91604ff24b7385dcc0ecf |

The exact formal command UTF-8 bytes without newline have SHA-256
182eb47739ac726331014569efa9b1f5c8e15dda60895afe2d2216e888a0f372.
It was the only formal command executed. No rerun or resume is permitted for
`exp_20260826_006_formal_001`.

## Offline verification

- targeted: 32/32 passed in 3.3435121 seconds;
- full repository: 268/268 passed in 19.6430828 seconds;
- py_compile and experiment JSON parse: passed;
- all six bound files and the test file rehashed to their frozen values;
- before execution, formal final, staging, and control paths were absent;
- after the failed execution, the final, staging, control, lease,
  authorization, and failure paths remain absent.
- the permitted offline postflight targeted suite passed 32/32 in
  3.3598705 seconds; this does not repair or supersede the consumed formal run.

The first LEAN narrow run failed two of 21 test expectations: fixture comparison
used preregistration order instead of the frozen output sort, and a boundary
test accidentally included raw whitespace. The failures were retained in the
terminal transcript, and only the test expectations were corrected.

## Restrictions and conclusion

No network, account, credential, eligibility, Alpha, Factor, IC, ML, P&L,
validation, or backtest work occurred. formal_executed=true,
network_access=false, historical_eligibility_ready=false,
eligibility_evaluated=false, and strict_eligible_count=0.

There is no schedule-claim research conclusion. The terminal experiment state
is `INCONCLUSIVE`. Postflight audit confirmed the run is consumed, rerun is
forbidden, and no formal outputs exist. The separately preregistered successor
must fix parent-directory creation without reusing this source or run ID.
