# exp_20260826_009 report

## Outcome

`POSTFLIGHT_AWAITING_AUDITOR / INCONCLUSIVE`

The exact frozen formal line was executed once and only once after independent
Researcher and Auditor Phase2 GO. It returned exit code `46` after 0.3296141
seconds, with empty stdout and stderr. The run ID is permanently consumed and
must not be retried.

The canonical stage ledger proves this sequence:

1. wrapper self-hash passed;
2. clipboard read started and passed;
3. key validation passed;
4. pre-launch clipboard clear failed with stage exit `44`;
5. final cleanup also failed, producing the higher-priority terminal exit `46`.

There is no `COLLECTOR/START` or `COLLECTOR/EXIT` row. The collector did not
start, no Binance request occurred, and no raw response, receipt, derived row,
summary, final, staging, collector control, failure, or authorization artifact
exists. The experiment therefore has no data result.

The parent `BINANCE_READ_ONLY_API_KEY` environment variable is absent after the
run. Clipboard state was deliberately not inspected because the available API
would require reading its content. In particular, the ledger records failures
for both pre-clear and final cleanup, so emptiness must not be inferred.

After the formal process ended, the Research Lead performed a separate safety
remediation that wrote the fixed non-secret marker `CLEARED_BY_CODEX` to the
clipboard. The reported PowerShell command exited `0` with empty stdout/stderr,
made no network request, and did not read the clipboard back. This is evidence
of a successful fixed-marker overwrite only; it is not evidence that the
clipboard is empty. It was not part of the frozen formal command and does not
change the formal request count, missing data result, `INCONCLUSIVE` decision,
or consumed-run prohibition.

## Evidence

- Formal command SHA-256:
  `5da7e8bdeafba4e34fbe3b289ccbf8125578729f7dad68fed76e7b23219d5c3e`
- Reservation: 76 canonical bytes, SHA-256
  `b2ce461ee5897c2eacfc2da9e2ec134373ee6a74789cdc4441dd12b98e3b184b`
- Stage ledger: 381 canonical bytes, 6 rows, SHA-256
  `d45d0e9adf9567a7e5370a511400ef8b99f9524f9b3900afd92e9c0598faa911`
- Formal stdout: empty, 0 bytes.
- Formal stderr: empty, 0 bytes.
- Network request count: 0.

## Offline verification before formal execution

- Targeted: 45/45 PASS in 8.486 seconds.
- Full repository: 388/388 PASS in 31.908 seconds.
- `py_compile`, Windows PowerShell parsing, and strict JSON checks: PASS.

## Semantic ceiling

No forward-schedule snapshot was produced. Even a later independent experiment
could only establish current-visible `planned_at_claim` evidence, never an
effective time, historical status, permission, listing interval, or eligibility.
The fixed scope remains `historical_eligibility_ready=false`,
`eligibility_evaluated=false`, and `strict_eligible_count=0`.

## Referenced Skill

`.codex/skills/quant-strategy-research/SKILL.md` and root `AGENTS.md` were read.
The Skill-linked methodology and experiment-contract references are absent in
this checkout.
