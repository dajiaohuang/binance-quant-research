# exp_20260828_004 — J-Quants V2 authority-first exact-once probe V3

Parent `exp_20260828_003` is preserved unchanged after Fresh Phase2 NO-GO.
This independent follow-up changes only runtime authority ordering, exact-once
attempt consumption and listing-presence semantics. Query dates, endpoints,
schema and model adapters otherwise remain unchanged.

Hypothesis: the fixed launcher can consume an attempt immediately after its own
hash passes, then verify the canonical external ten-file manifest and every
bound source byte before reading the local key or importing repository code.
The formal code-filtered master queries can never mint listing/presence evidence;
their only valid presence result is UNKNOWN.

Phase 1 permits preregistration, offline implementation and synthetic tests.
It forbids formal execution, real key reads, network requests, training, IC,
P&L, backtests and empirical eligibility.
