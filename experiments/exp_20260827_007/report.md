# exp_20260827_007

The fixed official source/checkpoints and later-authorized golden fixtures were acquired in two bounded commands with no retries. Nineteen allowlisted files total 562,430,552 bytes. Public sample/golden CSV files were retained as raw interface fixtures, so `real_data_accessed=true`; they were not used for model selection or empirical evaluation. The sanitized manifest contains no signed redirect query.

The mini + Tokenizer-2k contract produced 24 finite rows on CPU and CUDA using the official regression input. CUDA peak allocated memory remained below 75 MiB. Small + Tokenizer-base context-256 matched the official golden within frozen numerical tolerance.

Targeted tests passed 11/11. Full discovery ran 632 tests: 629 passed and three formal-lifecycle assertions failed because earlier consumed experiment paths exist. Exact traces are in `logs_full.txt`; the same failures predated exp007 in the paused DDGL full run, and exp007 did not create those paths. No Kronos test failed.

Artifact state is `PUBLIC_CHECKPOINT_INTERFACE_VERIFIED_POSTFLIGHT_BLOCKED`; terminal status is conservatively `INCONCLUSIVE` until the owning experiments reconcile their lifecycle assertions. Historical eligibility remains false and no Alpha, IC, P&L, backtest, accuracy validation, or model selection was run.
