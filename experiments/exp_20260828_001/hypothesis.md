# exp_20260828_001 — post-closure integration regression

## Observation

Kronos exp010, SSPT exp011, and TIPS exp012 now have independent postflight closures. Their targeted formal runs are complete, but the combined dirty worktree has not been exercised by one contemporaneous full repository discovery after all three implementations were present.

## Falsifiable hypothesis

One no-retry full `unittest` discovery will introduce no failures beyond the three already documented lifecycle-state assertions in the v2, v4, and v6 forward-schedule suites. A subsequent read-only `git diff --check` will identify any whitespace errors without modifying the tree.

## Frozen execution

1. Run `uv run --extra modern-ml python -m unittest discover -s tests -v` exactly once.
2. Preserve complete stdout and stderr and record exact totals, exit code, wall time, and CUDA test execution.
3. Run `git diff --check` exactly once after the suite and preserve its complete output.
4. Do not repair or rerun any failure in this experiment.

## Known pre-existing lifecycle failures

- `test_binance_spot_forward_schedule_pit_v2.ForwardSchedulePitTests.test_real_workspace_formal_paths_absent_and_network_zero`
- `test_binance_spot_forward_schedule_pit_v4.ForwardSchedulePitTests.test_real_workspace_formal_paths_absent_and_network_zero`
- `test_binance_spot_forward_schedule_pit_v6.ForwardSchedulePitTests.test_real_workspace_formal_paths_absent_and_network_zero`

Any other failure or error is new integration evidence and must be reported separately. The experiment reads no market data, performs no network request, and cannot establish Alpha, IC, P&L, backtest performance, or empirical model quality.

## Referenced skill

- `.codex/skills/quant-strategy-research/SKILL.md` — reproducible experiment and failure-preservation workflow.
