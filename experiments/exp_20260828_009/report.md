# exp_20260828_009 report

## Result

The two-fix v3 candidate is frozen and paused for Fresh Phase2. Its maximum
claim is
`JQUANTS_V2_BARS_MONTHLY_ZERO_WRITE_PREFLIGHT_AND_TRUSTED_PLANNER_FROZEN / NEEDS_DATA`.

exp008 remains frozen and its Fresh Phase2 `NO-GO` is preserved. The user has
authorized future network acquisition, but this phase did not authorize or run
the formal command.

## Fix 1 — truly read-only preflight

The launcher now completes self/freeze verification and the exact exp005
raw/sidecar plus exp006 closure preflight before the first `formal_control`
reference or any CreateDirectory, CreateNew, staging, ledger, temporary,
registry or source-binding write. Python is invoked with `-B` to prohibit
bytecode-cache creation.

Only after `PASS_READ_ONLY_ZERO_WRITES` does it create the O_EXCL control
reservation, then the O_EXCL raw attempt, then the attempt-owned source binding,
and only afterward inspect the environment key. The collector revalidates the
attempt-owned binding before key validation or transport use.

The focused test intercepted write-mode `open`, `os.open`, `mkdir`, `makedirs`,
Path mkdir/write/touch/rename and rejected any call during successful preflight;
none occurred. A forced source-hash drift failed and left the watched
`formal_control` and v3 raw trees exactly identical by path, kind, byte count
and SHA-256.

## Fix 2 — artifact-authoritative planner

v3 exports no registry class, constructor or mint function. The public planner
accepts only the concrete canonical `reuse_registry.json` path and an expected
artifact SHA-256. Every call independently:

1. re-parses the v3 bootstrap raw/receipt tree;
2. revalidates exp005 Q04 raw, sidecar and exp006 closure from their exact paths;
3. revalidates both bootstrap edge raw bodies and receipts;
4. reconstructs the expected registry with a private domain binding;
5. matches canonical path, file hash and full document before constructing
   immutable monthly plans.

Manual mappings, copied and pickled mappings, arbitrary objects, path-like
wrappers, fabricated paths/hashes, tampered documents, and wrong/missing/
duplicate source entries all failed. Replacing a source hash after one
successful planner call caused the next call to fail, proving call-time source
revalidation. The positive fixture excluded exactly 2024-07-01, 2025-03-28 and
2026-05-29; the other 20 months retained all session dates as network dates.

## Evidence and boundary

- focused v3 tests: 15/15 PASS;
- frozen v2/v1 regressions: 62 tests OK, 61 pass and one Windows symlink
  privilege skip;
- Python compilation, PowerShell parsing and dry plan: PASS;
- 18-file candidate freeze SHA-256:
  `c137e0bdc8a3b16ef13ef6ad1d918038a49310d5ba85150f8be8de3159527873`.

Key reads, network requests, formal executions and retries remain 0. Monthly
CLI/network, the full repository suite, training, inference, IC, P&L and
backtesting remain disabled or unauthorized.

## Append-only formal postflight closure

Fresh Phase2 authorized the frozen command, which executed exactly once with
exit code 0 and zero retries. The run made three direct J-Quants requests; all
three returned HTTP 200 JSON without redirects. The validated calendar contains
698 civil dates, 465 sessions and 23 monthly plans. The two boundary daily-bar
leaves contain 4,372 and 4,451 rows respectively. The reuse registry binds the
two boundary leaves and exp005 Q04 for 2025-03-28.

The independent postflight closure binds acquisition-manifest SHA-256
`2b2ffae4f948124cb949c213d5c1ad34ac6e83ebdc3a1486bbf079fc221929df`,
raw-tree SHA-256
`5eaed53748fd46141987d37032b99a1f812ca35251f101beecbd25d3eead84f8`,
registry SHA-256
`5e1ac6c740d51281f3840fa998e14d315d3aba114ed4f5ba73aa11337af773bc`
and session-list semantic SHA-256
`e51e2d635155f34119e18bf4e9cc9d6640ae00c73b212ed67dd4f09e42ea90e5`.
The closure-manifest SHA-256 is
`d850789b436bb918b3c621085a0ea7cc76b4c9ba6ab3c7d5a2a60373ffae650a`.

The exact allowed claim is
`JQUANTS_V2_FREE_ALL_MARKET_DAILY_BARS_BOOTSTRAP_VALIDATED / NEEDS_MORE_DATA`.
This does not establish a complete historical panel, historical listing/PIT
eligibility, training, inference, IC, P&L or backtest results. Monthly network
acquisition remains separately gated.
