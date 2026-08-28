# exp_20260828_007 Phase 1 report

## Result

The fresh `jquants_v2_bars_monthly_v1` candidate is frozen and paused for an
independent Phase 2 audit. Its current ceiling is
`JQUANTS_V2_BARS_MONTHLY_BOOTSTRAP_CONTRACT_FROZEN / NEEDS_DATA`.

This is an offline acquisition-contract result only. It is not a successful
bootstrap, historical dataset, eligible point-in-time universe, model,
training, inference, IC, P&L or backtest result.

## Implemented contract

- exact three-query bootstrap plan, with the two edge-day responses retained as
  reusable acquisition leaves;
- raw body then safe receipt durability before status/content/schema parsing;
- exact host/path/date-only query authority, redirect rejection, byte caps,
  prior-pagination-key-only chaining, 8 pages/query and 20 bootstrap requests;
- no HTTP retry and stop on first failure;
- 15,000,000,000 ns integer monotonic send spacing, including the full first
  request cooldown, with run/clock-domain/deadline/wait/send evidence;
- exact 698-date calendar, strict `HolDiv`, 450–475 session sanity band, exact
  23-month immutable split, exact Free18 daily-bar schema and null coherence;
- O_EXCL reservations, append-only ledgers, no-clobber publish and new-attempt
  repair rules;
- an inactive one-month-at-a-time, oldest-first monthly plan/catalog scaffold;
- source-bound pointer reuse for exp005/006 Q04 on 2025-03-28. The frozen raw
  hash remains `adac886e159f3979421b98e3d1b52fedafcafb68c931ef18f6a89597b035fad1`;
  offline parsing found 4,410 rows and did not copy them into Git.

## Offline checks

- initial targeted development run: 35 tests, 34 passed and 1 Windows
  symlink-privilege skip;
- final targeted candidate run: 38 tests, 37 passed and the same 1 skip;
- Python compilation: PASS;
- PowerShell parser: PASS;
- dry plan: PASS, with zero network requests and monthly network disabled;
- all 11 frozen files matched the candidate manifest, whose SHA-256 is
  `8fc2241efafc6635b36aeaffde66899b00cfe4f34a7414416017a9ff74be2971`.

The skipped test attempted to create a real Windows symlink. Deterministic path
traversal rejection, file/reparse checks in both Python and the launcher, raw
tamper rejection and no-clobber behavior all passed.

The first candidate-manifest validation one-liner was malformed: it compared
against a literal backslash-plus-`n`, so it raised `AssertionError`. The
corrected canonical-newline invocation passed without modifying any frozen
file. This was a validation-harness error, not candidate drift.

## Operational estimate and boundary

The bootstrap needs at least three sends (about 45 seconds of mandatory pacing
before response time) and at most 20 sends (about five minutes of pacing). A
full 460-session monthly schedule would require roughly 115 minutes at one send
per 15 seconds before network/parse overhead. Based on the retained 2025-03-28
body, expected raw daily-bar storage is approximately 0.5–0.8 GB, plus small
receipts/manifests. Actual page counts and bytes remain unknown until formal
acquisition.

No key was read; no API or other network request was made; formal execution,
monthly collection, the full repository suite and all empirical research remain
unauthorized pending the independent audit.
