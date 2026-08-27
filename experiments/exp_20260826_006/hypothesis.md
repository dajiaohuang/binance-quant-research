# exp_20260826_006 — LEAN announcement schedule claims

## Status

`PRE_REGISTERED / AWAITING_PHASE2 / NOT_RUN`

## Governing contract

`LEAN_R2_2_2` (LEAN Revision 1 + Revision 2 + Revision 2.1, renderer Revision
2.2.1, isolation Revision 2.2.2, and the implementation table) is the only
active contract. Revisions R5 through R5.7 and all runtime
forensics are superseded and must not be implemented.

## Single falsifiable hypothesis

The 756 accepted Binance CMS detail responses frozen by `exp_20260826_005` can
be revalidated offline and partitioned deterministically into `CLAIMED`,
`AMBIGUOUS`, and `NO_MATCH` articles using the exact LEAN grammar, while every
emitted syntactic pair token remains bound to action/time/pair source spans and
the four-file result is atomically published only after loader validation.

Only `OPEN_SCHEDULE_CLAIM` and `REMOVAL_SCHEDULE_CLAIM` are allowed.
`syntactic_pair_token_claim` is an opaque source token. It is not split into
base/quote roles and is not evidence of trading status, Spot permission,
listing/delisting interval, effectiveness, eligibility, or historical
availability.

## Failure conditions

Any source binding, input hash/bijection/schema, grammar, output schema/closure,
loader, authorization, or promotion failure fails closed. An ambiguous article
emits zero claims. The three coverage sets must be disjoint and exactly cover
all 756 accepted details.

## Semantic ceiling

```text
CURRENT_VISIBLE_ANNOUNCEMENT_SCHEDULE_CLAIMS_ONLY;
NOT_TRADING_STATUS_PERMISSION_LISTING_INTERVAL_EFFECTIVE_AT_OR_KNOWN_AT;
NOT_HISTORICAL_ELIGIBILITY
```

Always fixed: `historical_eligibility_ready=false`,
`eligibility_evaluated=false`, and `strict_eligible_count=0`.

No formal execution, network, credentials, eligibility, Alpha, Factor, IC, ML,
P&L, or backtest is authorized during Phase 1/Phase 2 preparation.

## Referenced Skill

`.codex/skills/quant-strategy-research/SKILL.md` was read. Its linked
`references/methodology.md` and `references/experiment-contract.md` are absent;
no external reference script was executed.
