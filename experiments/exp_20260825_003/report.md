# exp_20260825_003 progress report

## Referenced Skill

This experiment follows `.codex/skills/quant-strategy-research/SKILL.md` and the
repository `AGENTS.md`. The Skill-referenced local `methodology.md` and
`experiment-contract.md` are absent and are not claimed as read.

## Current status

`NEEDS_MORE_DATA`. The metadata-only inventory completed under the frozen command.
This successor changed only archive symbol namespace handling and raw evidence
directory naming after the preserved `exp_20260825_002` engineering failure.

The Unicode/symbol-index-specific suite passed 14/14 tests and the full repository
passed 26/26 before formal fetch. The formal run took about 31 seconds and made only
public ListObjectsV2 and one current exchangeInfo request; no ZIP or CHECKSUM payload
was downloaded.

## Inventory results

- 3,695 exact archive symbol prefixes; 723 strings end in `USDT`.
- 9,240 target-window 1h monthly ZIP objects and 9,240 matching CHECKSUM objects;
  zero target-window ZIPs lack a CHECKSUM object.
- Estimated ZIP payload size is 292,861,199 bytes (metadata estimate only).
- 462 candidates have at least one target-window ZIP; 261 have none; 272 have all
  25 months. None are dropped for incomplete coverage.
- `CVCUSDT` alone has an internal archive-month gap, 2023-01 through 2023-04. This is
  not interpreted as a halt, delisting, or missing market data until Klines and a
  historical state source are inspected.
- The current snapshot has 484 active Spot USDT symbols. The archive has 250 suffix
  candidates absent from that current set, while 11 current symbols are absent from
  the archive root. These are observation-set differences, not listing/delisting facts.
- Raw evidence contains 1,457 files (24,043,020 bytes), including 727 listing XML pages
  and 728 request sidecars. Every response was HTTP 200; no request needed a retry.

Key hashes:

- inventory JSONL: `8be13634629f8fc21e499aaab7df46839510b3a5be4842ab620bfb3089f512b3`
- symbol index: `0b6df35cab25c9e393f901c923c0412084afbfdc956b171e1bef655907808c16`
- summary: `168fa33118114fd088d03ceb87f920755aea8b088b13f891a37984e75065605d`

## Expected decision

The terminal decision is `NEEDS_MORE_DATA`: archive observation is not historical
tradability, and no Kline payload, historical state ledger, dynamic eligibility
panel, execution model, or factor evidence exists yet. The 2022-12 start also does
not provide the 90-day warm-up needed to rank from 2023-01; the next independent data
experiment must extend the range or delay the first eligible rebalance.
