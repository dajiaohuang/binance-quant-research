# exp_20260828_003 report

Status: `PREFORMAL_AWAITING_FRESH_PHASE2 / NEEDS_MORE_DATA`.

This independent follow-up preserves exp002 and repairs its Phase 2 FINAL NO-GO.
Dates are frozen around the official KDDI 9433 split effective 2025-04-01,
whose ex-right/post-split trading date is 2025-03-28 (official JPX source
recorded in parameters). Weekend HTTP 400 is no longer
assumed: Q03 requires HTTP 200 and the official next-business-day mapping from
2025-03-30 to returned Date 2025-03-31. Q05 freezes the KDDI expectation
the falsifiable 2025-03-28 API expectation `AdjFactor=0.5` and `ExRT='1'`;
this is not a preclaimed response fact. All success queries require nonempty exact
coverage. Every actual page request is paced at least 13 seconds apart under
an injected monotonic clock/sleeper; development tests do not really sleep.

Historical dates in a response do not create historical known-at. Availability
is the later of the policy-derived observation timestamp and actual receipt.
Presence intervals require verified adjacent official-session full master
snapshots; one or non-adjacent snapshots yield UNKNOWN.

No formal or network execution is authorized in this phase.

The independent implementation is now frozen behind an external ten-file
manifest. The PowerShell launcher self-verifies before reading the fixed local
env file, enforces a strict single-assignment grammar and size/type checks,
hands the key to the child environment only, then removes it. The collector
rechecks the external freeze before the first request and immediately before
promotion. The trusted loader rebuilds the exact five-query/page chain, raw and
receipt bijection, pacing, coverage, manifest, summary, raw tree and entire
staging tree before a same-volume no-clobber rename.

Development evidence: targeted tests 27/27 PASS; Python compile, PowerShell
parse and strict JSON parse PASS. Synthetic tests used injected transports and
clocks; they made zero real requests and did not read `.env.jquants.local`.
The file was checked only for presence, git-ignore status and absence of a
preexisting parent-process key environment variable. Formal final, staging and
control paths remain absent. Full repository tests were not run.

The KDDI field expectation remains unobserved until a separately authorized
formal probe. Even a successful probe would only establish current acquisition
and policy-derived observation semantics. It would not backdate known-at,
derive a listing spell from the filtered master query, authorize empirical
training, or open historical eligibility.
