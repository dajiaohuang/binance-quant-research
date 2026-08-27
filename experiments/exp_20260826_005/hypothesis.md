# exp_20260826_005 preregistered hypothesis

## Observation

`exp_20260826_004` preserved a correct fail-closed prefix after three HTTP 429
responses, but v3 did not make the retry schedule, monotonic wall budget, or
body/sidecar commit authorization independently reconstructible. A crash could
also leave raw files without an immutable receipt explaining whether another
wire request was authorized.

## Single hypothesis

A self-contained v4 collector that changes only acquisition timing and retry
evidence can preserve every v3 corpus/identifier/time-claim semantic while
making each wire transition, terminal stop, and successful raw corpus exactly
reconstructible from a write-once receipt chain and bounded monotonic schedule.

## Single primary change

Replace v3's generic retry loop with one frozen absolute decision matrix,
monotonic 10,800-second wall deadline, and body -> sidecar -> receipt commit
protocol. Bind the accepted success corpus to a runtime contract, global
receipt chain, raw-tree digest, and exact logical/attempt/detail bijections.
Nothing else changes: CMS endpoints, two full list passes, opaque code and
type-preserving id rules, independent list/detail time claims, discrepancy
artifact, current-visible-only scope, source TOCTOU checks, no-auth/no-proxy/
no-redirect transport, byte bounds, and no eligibility/event/Alpha semantics
are inherited exactly from v3 and reimplemented without importing v3.

## Frozen retry decision matrix

Retryable HTTP statuses are exactly `{408, 429, 500, 502, 503, 504}`, and only a
well-formed recomputed `HTTP_<status>` outcome is retryable. Attempts are
absolute numbers 1..4.

- A case-insensitive `Retry-After` header on any retryable response is terminal,
  including empty, zero, or malformed values.
- HTTP 429 without Retry-After: attempt 1 requests 30 s before attempt 2;
  attempt 2 requests 60 s before attempt 3; attempt 3 requests 120 s before
  attempt 4; attempt 4 is terminal.
- Other retryable statuses without Retry-After: attempt 1 requests 1 s before
  attempt 2; attempt 2 requests 2 s before attempt 3; attempts 3 and 4 are
  terminal.
- Malformed/nonretryable/redirect/header-drift/transport outcomes are terminal.
- The current outcome selects the delay. Thus `429,500,429,OK` requests
  `30,2,120`; `500,429,500` requests `1,60` then terminates; and
  `500,500,429,OK` requests `1,2,120`.
- An accepted OK requests exactly the hardcoded 1-second base delay before the
  next logical request. There is no sleep before the first wire or after the
  accepted final `time_after`.

The only timing tuple CLI inputs are exactly
`--http-429-backoff-seconds 30 60 120` and
`--other-retryable-backoff-seconds 1 2`; old timeout/attempt/wire/pacing flags
are rejected. The base logical delay (1 s), request timeout cap (30 s), wall
budget (10,800 s), wire cap (3,464), retryable set, and policy are hardcoded.

## Monotonic wall contract

After the exclusive lease succeeds, capture `S=monotonic_ns()` immediately,
set `D=S+10_800_000_000_000`, and write the runtime contract. Every source check
and wire decision is evidence-bound.

- `now >= D` before an action is exhausted.
- A requested delay greater than or equal to remaining time is terminal before
  sleep.
- A completed sleep must satisfy `post >= pre + requested_delay_ns` and
  `post < D`.
- Prefetch monotonic time must be `< D`; timeout is
  `min(30_000_000_000, D-prefetch)` and must remain positive with no up-round.
- If postfetch is at/after D with stable source, save body+sidecar, commit a
  terminal accepted=false receipt, and fail.
- Postfetch source drift saves no response and writes `terminal_schedule.json`.
- Source binding is checked before/after runtime contract; before/after sleep;
  prefetch; postfetch/pre-raw; post-raw/pre-receipt; around raw summary; and
  before/after final artifacts.
- The acceptance-time monotonic sequence is checked through the accepted
  `time_after` receipt. Monotonic absolute values are process-local claims only.

## Receipt and controlled-terminal contract

Each attempt's immutable receipt is
`attempt_NNNN.receipt.json`, written last after its exact body and sidecar and,
for `NEXT_WIRE`, after the validated sleep. It contains exactly the frozen core
identity, decision, timing, path/hash, and previous-receipt fields defined in
`parameters.json`. Receipt bytes are canonical JSON and form a global SHA-256
chain; the first previous hash is null. A receipt cannot exist without its exact
body and sidecar and is never updated.

An OK receipt is deferred until the caller parses the accepted response and
decides whether the next logical request exists. Parser/schema abort writes
`NO_NEXT_WIRE / COLLECTOR_ABORTED_BEFORE_NEXT_WIRE`. The final accepted
`time_after` writes `NO_NEXT_WIRE / RUN_WIRE_COMPLETE`. Retry receipts authorize
only the immediately following attempt of the same canonical URL.

`raw_run/terminal_schedule.json` is an independent write-once failure record,
not an attempt, wire request, or receipt-chain member. It covers no-wire or
receiptless failures such as budget exhaustion, transport exception, postfetch
source drift, or post-raw/pre-receipt source drift. Its presence forbids a
successful corpus. An unhandled crash may leave a prefix or orphan, but cannot
produce a loadable success.

## Success evidence and exact bijections

Successful completion requires all of the following:

- selected detail key set exactly equals accepted final-OK detail key set;
- attempt-key sets for body, sidecar, receipt, and ledger are identical and each
  occurs once;
- no terminal schedule, orphan, pending OK, missing logical request, or extra
  artifact exists;
- `raw_run/runtime_contract.json`, all attempt artifacts, and
  `raw_run/request_ledger.jsonl` are committed before
  `raw_run/summary.json`;
- raw summary binds runtime-contract SHA, final receipt-chain head, ordered
  receipt-tree SHA, sorted relative raw-artifact-tree SHA, selected/detail keyset
  hashes, attempt/body/sidecar/receipt keyset hashes, and logical/wire/outcome
  counts;
- `raw_run/summary.json` is excluded from its own raw-tree digest; processed and
  root artifacts, including the external corpus summary, are outside that
  promised tree;
- the external corpus summary binds the raw summary path and SHA.

The trusted loader must reconstruct all retry decisions, delays, monotonic
constraints, hashes, keysets, logical ordering, two list passes, details,
discrepancies, bracket, and final receipt. An incomplete prefix or crash cannot
be accepted as success.

## Expected successful formal result

If totals remain 2,234/426, list-claim interval counts remain 575/181, all 756
details complete, both list passes are identical, clock/source/receipt gates
pass, and all exact bijections hold, the maximum result is
`NEEDS_MORE_DATA / ANNOUNCEMENT_CORPUS_AVAILABLE`. It is still a
current-visible corpus, not historical eligibility.

## Failure conditions

The formal result is `INCONCLUSIVE` or fail-closed if any frozen count, schema,
source, timing, retry, receipt, bijection, hash, page-stability, clock, or byte
contract fails. Any `terminal_schedule.json`, exhausted schedule, missing
receipt, orphan, Retry-After on retryable status, source drift, deadline breach,
or trusted-loader mismatch forbids success.

## Scope exclusions

No pair/event/effective-time/listing-interval/eligibility derivation; no Alpha,
factor, IC, ML, backtest, validation, or final-test market data; no API key,
cookie, account endpoint, proxy, or redirect. This phase is offline
implementation only and does not authorize the formal command.

## Two-stage source freeze

The extractor SHA is intentionally unknown at preregistration. The formal
command contains an invalid placeholder until implementation and offline tests
are complete. Only then may the stable v4 source SHA replace the placeholder in
the command and manifest; the source must not contain its own hash literal.

