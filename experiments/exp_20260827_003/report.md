# exp_20260827_003 report

Status: `POSTFORMAL_AWAITING_AUDITOR / NEEDS_MORE_DATA`.

The single change is a direct, self-hashing frozen launcher. The v6 wrapper,
env parser, collector, loader, network plan, schemas, derived data, and semantic
ceiling remain value-equivalent to v5 after mechanical identity/path
normalization.

Researcher and Auditor gave final Phase2 GO. The frozen direct launcher was
executed exactly once. The real local env file was checked only for allowed
metadata before execution; neither preflight nor postflight read its content,
size, or hash. Eligibility, Alpha, IC, ML, P&L, and backtest remain forbidden.

## Formal outcome

- Preflight passed all eight frozen file hashes and the exact formal-line SHA
  `6f30b127981af3fec837178871540eb48f90947aae5abc37ce75fdf504d0cd9a`.
  The env path was an ordinary non-reparse, ignored and untracked file; the
  parent key environment variable and all v6 formal paths were absent.
- The one authorized direct literal command exited `0` in `2.7293683s`.
  Stdout and stderr were both empty. The run is consumed and must not be rerun.
- Wrapper reservation and the canonical 8-row stage ledger are present. The
  ledger ends in `COLLECTOR/EXIT 0` followed by `FINAL_CLEANUP/PASS`.
- Exactly five ordered GETs completed, each once and with HTTP 200. The API-key
  header was sent only on the OPEN and DELIST schedule endpoints. Five raw
  responses and five safe receipts were committed; staging and failure are
  absent, while collector lease and authorization are present.
- Trusted reload independently rebuilt and accepted the committed result.
  Summary SHA is
  `5bd203f9b2af98a00a0f2a46e3825c4052d257f241a8e514320af15a65ea7d19`,
  artifact-tree SHA is
  `8a85e6c0b096823d959acea5276450c830bad7542dfb08b2b6906935d903cbab`,
  and authorized final-tree SHA is
  `5f1c91f8f2cc88e35f441cce37a68a36e952781a436ccdcc5e70584c41671263`.
- The four root outputs are: `plans.jsonl` 3 rows / 987 bytes / SHA
  `995611db49958399099661749d32ab6c0fdf0d4206251b3b6e484431a9d5f223`;
  `joins.jsonl` 3 / 1,074 /
  `8ead684469e907d8018d8ffbe9bd8978029a2bf92a1e102f3b296788393a9cb7`;
  `current_symbols.jsonl` 3,685 / 11,929,096 /
  `8fdb648317dca5acf7b2e157d452f1cfaf9f20e814065afa4e212067db25a366`;
  and `summary.json` 6,787 bytes /
  `5bd203f9b2af98a00a0f2a46e3825c4052d257f241a8e514320af15a65ea7d19`.
- The snapshot contains 3 plan rows, 3 join rows, and 3,685 current-symbol
  rows. All three plans are DELIST claims for `ICXUSDT`, `SCRTUSDT`, and
  `STORJUSDT` at `2026-09-03T03:00:00Z`; all three joins are `MATCHED`.
  The OPEN schedule response was an empty array.
- Binance server-time observations were nondecreasing and 966 ms apart. They
  bracket only this observation, not an historical effective interval.
- Terminal result is `NEEDS_MORE_DATA /
  FORWARD_SPOT_SCHEDULE_PIT_SNAPSHOT_COMPLETE`. The semantic ceiling remains
  current-visible forward schedule claims plus current Spot metadata only;
  `historical_eligibility_ready=false`, `eligibility_evaluated=false`, and
  `strict_eligible_count=0`.

Independent Auditor postflight remains pending.

## Offline implementation result

- Targeted v6 tests: 49/49 PASS in 23.016s. The preserved first run had 49
  tests and 23 reported failures caused by pre-freeze SHA/formal placeholders,
  a synthetic marker quoting error, and one copied EOF newline.
- Full repository: 567 PASS / 2 expected lifecycle mismatches / 569 total in
  109.118s. The only mismatches are old exp009 and exp001 tests whose real
  reservations were previously consumed; their evidence was not modified.
- py_compile, PowerShell 5.1 parsing, and strict JSON: PASS.
- Synthetic launcher tests prove one wrapper call, exact argv tokens, every
  allowed child code, exits 48/49/50, and empty stdout/stderr.
- v6 wrapper, collector, and loader are exactly v5 after mechanical identity
  and path normalization. Collector summaries retain exactly seven code
  bindings; the eighth launcher binding exists only in the experiment manifest.
- Those path-absence and network-zero statements describe the Phase2
  pre-execution checkpoint. After the authorized run, final, collector control,
  wrapper reservation, and wrapper ledger are present; staging is absent and
  the exact network request count is five.
- The real `.env.binance.local` content, size, and hash were not read.

## Frozen Phase2 candidate

- launcher: `5da75c6a0239b6553828d85ce1dfcc63ecc44b340cc4b5d6b403511810653b2d`
- wrapper: `6e7ed42a32c7b7a2765f1c711d19ad4fb6190d17bb6130496bf6651cba0f8c55`
- collector: `ff76fd9e7cd3164e98c883ded78dd0e3bf4c89165e7b168b1471fec96fb1eb8d`
- loader: `42a8cb233e535af3e5bf665b5da16d77176469bd3fb58816eb21aa1a51a49a1e`
- source contract: `b7776b2ce3eed23468a8eb3d146e481c1e570b0220e247710696f999bbc0b2d2`
- schema: `129ff866fd2e4c06e43fee2aac0c869b3d908d9da4fc7a8eea70beec4c83d43e`
- parameters: `919a4db96af6ed4cf8cdc22c6ee023ea5c20f839ffe7412fa7676c77727368c1`
- tests: `7ec9259e35bad895b1956efa1ebab946c69c5ad3b7e42468b3a387a54c480db9`
- formal line: `6f30b127981af3fec837178871540eb48f90947aae5abc37ce75fdf504d0cd9a`

No eligibility evaluation, Alpha, IC, ML, P&L, or backtest occurred. Formal
execution is complete exactly once and the evidence awaits Auditor postflight.

## Referenced Skills

- `.codex/skills/quant-strategy-research/SKILL.md` — reproducible experiment
  and evidence-gate workflow.
