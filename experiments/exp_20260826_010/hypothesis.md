# exp_20260826_010 — CLIPBOARD_NONEMPTY_OVERWRITE_V1

## Observation

The exp009 exact-once formal wrapper passed clipboard read and validation, but
both `Set-Clipboard -Value ''` calls failed. The collector never started and
the consumed run closed `INCONCLUSIVE` with zero network requests.

## Falsifiable hypothesis

Replacing only those two empty-string clipboard writes with one fixed nonempty
marker variable, `$clipboardOverwriteMarker='CLEARED_BY_CODEX'`, will preserve
all reservation, ledger, exit, collector, parser, join, and semantic contracts
while allowing both the pre-launch and final cleanup writes to succeed in the
same Windows PowerShell environment.

## Single primary change

The exp010 wrapper differs from exp009 only by declaring the fixed marker and
passing it to the two `Set-Clipboard` calls. It must never read back, compare,
hash, measure, or log clipboard content after either write. Collector and
loader changes are restricted to the independent exp010 identity and paths.

## Failure conditions

- any real clipboard read, credential access, network request, or formal run during Phase2;
- any wrapper change beyond the fixed-marker declaration and two substitutions;
- any collector/loader semantic difference after identity/path normalization;
- marker write followed by clipboard readback, comparison, hashing, length, or logging;
- any regression in reservation, ledger, exit priority, source binding, transport, parser, join, or atomic publication;
- any exp010 formal final/staging/control/reservation/ledger present at Phase2 freeze.

## Semantic ceiling

Maximum later outcome remains `NEEDS_MORE_DATA`. `planned_at_claim` is not
effective time, historical status, permission, listing interval, or eligibility.
`historical_eligibility_ready=false`, `eligibility_evaluated=false`, and
`strict_eligible_count=0` remain fixed.
