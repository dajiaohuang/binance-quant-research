# exp_20260828_001

Pre-registered integration regression for the worktree after the exp010/011/012 independent audit closures.

At preregistration, status was `PLANNED / NOT_RUN`. The only authorized executions were one no-retry full repository `unittest` discovery and one read-only `git diff --check`. Three exact legacy lifecycle-state failures were preregistered to remain visible rather than being hidden or repaired here. Any additional failure would have been new integration evidence.

No network, market data, Alpha, IC, P&L, backtest, model selection, eligibility evaluation, or empirical conclusion is authorized.

## Execution result

The full discovery command ran exactly once with no retry. It exited `1` after 727 tests: 724 passed, three failed, zero errored, and zero skipped. The three failures are exactly the preregistered v2, v4, and v6 `test_real_workspace_formal_paths_absent_and_network_zero` lifecycle-state assertions. No additional test failed. CUDA tests executed rather than skipping. The four-line stdout stream is complete and retained in `logs/full_stdout.txt`.

The process separated stdout and stderr in memory, but the tool return boundary truncated the 129,245-character stderr before it could be persisted through the required `apply_patch`-only write path. Rerunning is prohibited. `logs/full_stderr_capture_failure.txt` therefore preserves the exact execution metadata, terminal summary, failure identities, source lines, and the capture failure, but it is explicitly not the complete raw stderr. This prevents a fully auditable PASS conclusion even though no new test failure was observed.

`git diff --check` ran exactly once after the suite and exited `0`. Its stdout is empty. Its complete stderr contains four line-ending notices for existing modified files (`AGENTS.md`, `pyproject.toml`, `research/MODERN_ALPHA_RESEARCH_V1.md`, and `uv.lock`); no whitespace error was reported.

Final status is `RUN_CONSUMED_INCONCLUSIVE_EVIDENCE_CAPTURE_INCOMPLETE / INCONCLUSIVE`. This experiment did not modify tests or retry the three lifecycle assertions. Network requests and real-data reads remained zero. It provides integration diagnostics only and no Alpha, IC, P&L, backtest, or model-quality evidence.

## Closeout documentation remediation

After the Auditor documentation NO-GO, a record-only remediation clarified the historical preregistration wording above, marked the consumed Kronos verifier as non-rerunnable, and replaced non-copyable smoke placeholders with task-specific PowerShell temporary paths. No tests, formal commands, or `git diff --check` were rerun; no frozen source, formal record, record index, or audit-closure JSON was edited. The experiment remains `RUN_CONSUMED_INCONCLUSIVE_EVIDENCE_CAPTURE_INCOMPLETE / INCONCLUSIVE` and does not claim a full-repository PASS. The append-only remediation record is `logs/closeout_documentation_remediation.json` (`910` bytes, SHA-256 `0d36e6d0fe1d22c1c9d77682556c2c49e48bcb2f17950a99d8f791b9f99d9544`).
