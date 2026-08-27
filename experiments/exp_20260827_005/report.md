# exp_20260827_005 report

## Status

`TERMINAL_POSTFLIGHT_RECORD_RECHECK_PENDING / NEEDS_MORE_DATA`

The pre-registration was created before implementation. After the third refreeze received Fresh Phase 2 final approval, both frozen formal commands were executed once in order with no retries.

Formal results:

- hierarchical contract: 48/48, exit 0;
- historical adapter compatibility: 27/27, exit 0;
- all seven frozen hashes matched before execution, after command 1, and after command 2;
- full repository discovery remained `NOT_RUN`;
- network requests, real-data access, empirical Alpha, eligibility evaluation, IC, ML, P&L and backtests remained zero/not run.

Complete transcripts are stored in `logs/formal_001_hierarchical.txt` and `logs/formal_002_historical_adapter.txt`. The formal record index SHA-256 is `9313f7e0141c4f7cd5d0dbb45f9521c63b0f81edd06e48df0588f66d9986d58f`.

## Development result

The candidate now carries exact horizon identity from the expert registry through ensemble, regime adjustment, expected-net-alpha, and diagnostic projection. It rejects mixed horizons and numeric aliases such as bool, IntEnum, float, and string. Output and expert-weight keys are validated before Python mapping equality can collapse aliases. The four-horizon `MultiHorizonEnsemble` is an immutable identity bundle with no scalar, value, or weights and is rejected by regime/net consumers; direct construction independently validates its exact-four ordering, clocks, shared identity, score integrity, and recomputed provenance.

The existing 24-hour synthetic ensemble arithmetic is unchanged (`1.75` under the frozen fixture weights), while every supported horizon can be combined individually. Horizon changes alter provenance throughout the downstream chain.

Development checks:

- hierarchical kernel after Phase 2 NO-GO remediation: 48/48 passed;
- historical PIT adapter compatibility: 27/27 passed;
- `py_compile`: passed;
- full repository discovery: `NOT_RUN` by Phase 1 contract.

The original defect remains recorded in `logs/precondition_mixed_horizon_defect.txt`: the old kernel accepted a 1h/24h registry and returned scalar `2.0` without horizon identity.

The first Phase 2 research review returned `NO_GO` because output/weight keys could rely on Python equality before exact validation and direct `MultiHorizonEnsemble` construction was not self-validating. Both findings and the prior hashes are retained in `logs/phase2_research_review_no_go.txt`; those findings were remediated and the refrozen candidate subsequently received Fresh Phase 2 GO.

A subsequent Fresh Phase 2 Auditor review returned `NO_GO` because the public function was named `build_multi_horizon_ensemble` instead of the frozen contract name `compose_multi_horizon`. The implementation, imports, calls, schema, parameters, and documentation now use only `compose_multi_horizon`; no legacy alias is retained and no calculation changed. The finding is retained in `logs/fresh_phase2_auditor_api_name_no_go.txt`.

After that API-name remediation and third refreeze, Fresh Phase 2 received final GO. The two formal commands then ran once each, in the frozen order, with no retries: hierarchical 48/48 exit 0 and historical adapter 27/27 exit 0. Postflight computation found the indexed formal evidence internally consistent, but the Auditor returned `FINAL FAIL` for stale non-indexed record state. That record-only closure has now been remediated and awaits an independent read-only recheck; audit completion is not claimed.

## Explicit non-results

- No real source is read.
- No historical eligibility is established.
- No empirical Alpha, IC, ML, P&L, or backtest result is produced.
- No validation or final-test data is opened.
- Full repository tests are not authorized for this development phase.

The maximum successful terminal status is `NEEDS_MORE_DATA`.

## Phase 2 candidate bindings

| File | SHA-256 |
|---|---|
| `src/quant_research/hierarchical_alpha.py` | `5db9584b1b456fbe69d0c0937edbcb625adf6ec0b588ef76400da21e037093dc` |
| `tests/test_hierarchical_alpha.py` | `1796ac727b8fd066a4d68bb8e179841ebe80457bee65b59044bbca9014294da1` |
| `research/MODERN_ALPHA_RESEARCH_V1.md` | `d086417c366079655629781134bb7de4d16fff3659efbc898739fd3d112c2ae4` |
| `hypothesis.md` | `25c07a36ae604c0f7dd1a0b26a03be4df12d800d2d65daa872336269c00e7c8d` |
| `artifacts/schema.json` | `a5fe791fc2772125984835acad5a0e54162bcdc0e6ad6b34b375645ade6b8112` |
| `parameters.json` | `ad2bcb9daf5e391509806c9520db7ddea705f2bd48d700cfbe7506651f19c363` |
| `commands.txt` | `f7a8bc2bd2a432a42856bc28505c6ee9f93a4fc785c371355b1bb6723bbd78b7` |

The formal commands were each executed exactly once after Fresh Phase 2 GO, with retry count zero. Their hashes, in executed order, are `20410fbbc49fddcf45d6c052cda48b85aa832aa9c5f2530f27dd9dfb2c80c8e1` and `e7c8f8cec9a2cece6c31895cc18c671b5325ecfcaf5b49488db30fb430a6de0b`. These synthetic contract tests do not establish any empirical result.

The `quant-strategy-research` skill guided the pre-registration and failure-retention workflow. Its referenced `methodology.md` and `experiment-contract.md` files were not present in the installed skill directory, so no claim is made that those missing references were used.

## Out of scope

The bundle is not an economic composer. Cross-horizon weights, utility, allocation, horizon-specific regime, and horizon-specific cost remain deliberately unimplemented.
