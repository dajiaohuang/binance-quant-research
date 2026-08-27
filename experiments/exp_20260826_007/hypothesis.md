# exp_20260826_007 — Parent Bootstrap V1.1

## Status

`PRE_REGISTERED / PHASE1_FINAL_GO / NOT_RUN`

## Observation

The single formal run of `exp_20260826_006` failed before lease creation because
the fixed `data/processed/binance_spot_announcement_claims_v1/runs` parent did
not exist and the frozen runner used `CONTROL_ROOT.mkdir(parents=False)`.
Postflight audited that experiment as `INCONCLUSIVE`; its run is consumed and
rerun is forbidden.

## Single falsifiable hypothesis

Without changing any `LEAN_R2_2_2` renderer, grammar, evidence, input-binding,
coverage, or output semantics, a new independent v2 runner can safely validate
and create only its fixed version/runs shared parents, revalidate them, and then
atomically reserve the new control directory before writing the lease. This
should close the missing-parent failure while preserving fail-closed lifecycle
and same-volume atomic promotion.

## Only material change

`Parent Bootstrap V1.1`:

1. final/staging/control preexistence remains zero-write;
2. fixed repo/data/processed/version/runs paths are checked by drive,
   `commonpath`, `lstat`, and symlink/reparse-point rejection;
3. only the fixed version and runs shared parents may be created before run
   reservation, followed by an exact recheck;
4. atomic control-directory creation reserves and consumes the run;
5. lease is write-once; a post-control/pre-lease failure retains control but
   writes no failure; after lease, controlled failures write one failure;
6. staging and final remain siblings on the same drive and parent for atomic
   rename.

## Failure conditions

Any path escape, cross-drive target, symlink/reparse component, unexpected
non-directory component, parent-creation failure, reservation/lease failure,
source drift, input/coverage/output failure, or promotion failure fails closed.
Infrastructure failures use exit code 24. No exp006 source, run, or formal
artifact may be changed or reused.

## Semantic ceiling

```text
CURRENT_VISIBLE_ANNOUNCEMENT_SCHEDULE_CLAIMS_ONLY;
NOT_TRADING_STATUS_PERMISSION_LISTING_INTERVAL_EFFECTIVE_AT_OR_KNOWN_AT;
NOT_HISTORICAL_ELIGIBILITY
```

Always fixed: `historical_eligibility_ready=false`,
`eligibility_evaluated=false`, `strict_eligible_count=0`.

No formal execution, network, credentials, eligibility, Alpha, Factor, IC, ML,
P&L, or backtest is authorized during this phase.

## Referenced Skill

`.codex/skills/quant-strategy-research/SKILL.md` and root `AGENTS.md` were read.
The Skill-linked `references/methodology.md` and
`references/experiment-contract.md` are absent; no external reference script
was executed.
