# exp_20260827_001 — LOCAL_ENV_FILE_KEY_HANDOFF_V1

## Status

`PRE_REGISTERED / AWAITING_PHASE2_REVIEW / NOT_RUN`

## Single hypothesis

A fixed, git-ignored local environment file can hand one read-only Binance API
key to the already frozen five-request forward-schedule collector without using
the clipboard, putting the secret in argv, or persisting any secret-derived
value. The wrapper must reserve the run before reading the file, accept only the
frozen byte grammar, own and remove only the child environment variable it
creates, and preserve a canonical stage ledger.

This experiment supersedes the unexecuted exp_20260826_010 clipboard route. It
does not read exp010 as an input and does not alter its files.

## Semantic ceiling

The possible result remains current-visible forward OPEN/DELIST schedule claims
and current Spot metadata only. `openTime` and `delistTime` are
`planned_at_claim`, not effective times, historical status, permissions, or an
eligibility interval. A successful acquisition can terminate only as
`NEEDS_MORE_DATA`.

## Failure criteria

- source binding or formal command binding differs from the Phase2 freeze;
- the fixed local env file is missing, non-regular, reparse-linked, oversized,
  or violates the exact byte grammar;
- the parent environment already contains the key name, or handoff/cleanup
  ownership is violated;
- reservation, ledger, five-request transport, schema, loader, or atomic
  publication contracts fail;
- any secret or secret-derived value reaches argv, output, ledger, receipts,
  artifacts, exceptions, stdout, or stderr.

No formal execution or network request is authorized in Phase1/Phase2.
