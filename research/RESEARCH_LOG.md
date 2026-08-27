# Research Log

## 2026-08-25 — exp_20260825_006 / exp_20260825_007

`exp_20260825_006` 预注册并实现了纯合成、data-agnostic、fail-closed 的
Hierarchical Alpha Research Kernel。首次正式窄测运行 35 项，34 项通过、1 项
error：SciPy 1.18.0 的 `scipy.linalg.lstsq` 收到 Python list 后访问 `.shape`，抛出
`AttributeError`。全仓测试没有运行，实验以 terminal `INCONCLUSIVE` 关闭；失败
记录、traceback 与 NumPy 2.5.1 / SciPy 1.18.0 版本均保留。

独立 successor `exp_20260825_007` 只允许在同一 `lstsq` 调用前将 design/target
显式转换为 `numpy.asarray(..., dtype=float)`，不改变数学、API、测试或合同。修复后
首次窄测 35/35、随后全仓 85/85；由于首次 stdout 未持久化，又在相同冻结 module/
test SHA 上按相同顺序完成 raw-evidence replay，两份 replay log 均 exit 0 且保存
SHA-256。postflight audit 为 `PASS`，实验以 `NEEDS_MORE_DATA` 完成。

该结果仅建立合成研究内核，不建立 PIT eligible universe，也未读取真实市场数据来
计算 factor、score、rank IC、return、turnover 或 P&L；没有模型 fit、回测、CLI、
网络或账户访问。下一步仍是独立建立历史 PIT Binance Spot eligibility evidence。

### Referenced Skill

- 实际读取并遵循：`.codex/skills/quant-strategy-research/SKILL.md`，用于研究角色、
  预注册、失败保留、顺序门禁与实验证据纪律。
- 该 Skill 提到的 `references/methodology.md` 与
  `references/experiment-contract.md` 在本仓库不存在，因此未读取、未使用，也未
  声称参考其内容。

## Workspace initialization — 2026-07-14

### Observation

The repository contained only its operating instructions. There was no data, existing strategy, backtest framework, dependency manifest, experiment history, or baseline result to reproduce.

### Decision

Created a minimal, framework-neutral Python research foundation with a strict OHLCV CSV contract, data validation, next-bar execution, realistic configurable costs, separate time ranges, and per-experiment artifacts.

### Baseline status

`SmaCrossStrategy` is an unexecuted, explanatory baseline. No real-data performance, sample-out-of-sample result, or paper-trading readiness can be inferred until a source dataset and market configuration are supplied.

### Conclusion

INCONCLUSIVE — requires market data and a declared research universe before a baseline can be reproduced.

## Dry-run infrastructure — 2026-07-14

### Referenced Skills

| Skill | Source | Purpose | Local path |
| --- | --- | --- | --- |
| backtesting-frameworks | wshobson/agents | Keep time splits, costs and look-ahead controls explicit | `references/quantitative-trading/plugins/quantitative-trading/skills/backtesting-frameworks/SKILL.md` |

### Decision

Added Freqtrade as the local crypto-spot dry-run framework. The configuration is `dry_run: true`, has no exchange credentials, uses a simulated wallet, static pairs, and no API server. The baseline strategy is long-only SMA crossover with completed-candle indicators and crossover checks against only the preceding row.

### Limits

The selected public exchange and pairs are a provisional infrastructure default, not a declared research universe or a performance recommendation. No historical baseline, live market connection, or simulated trade result is claimed until the strategy has been separately backtested and a controlled dry-run process has started successfully.

### Environment compatibility

The installed asynchronous DNS resolver could not contact DNS servers, while Windows system DNS and HTTPS access to the selected exchange succeeded. `quant-dry-run` therefore forces aiohttp's thread-based resolver before importing Freqtrade. A direct CCXT market-load probe succeeded with 4,498 markets. This is an environment workaround, not a strategy result.

### Dry-run launch validation

The controlled startup loaded the public market catalogue, `DryRunSmaCrossStrategy`, dry-run wallet and static pairlist. Its default initial state was `STOPPED`, so the config now explicitly sets `initial_state` to `running`; this is required for candle processing but remains fully simulated because the launcher itself supplies `--dry-run`.

The network accepted REST market metadata but rejected the Binance WebSocket endpoint. The config therefore sets `exchange.enable_ws` to `false`, making REST polling the explicit data path.

## exp_20260714_001 — SMA / ADX baseline family

### Observation

The Binance BTC/USDT and ETH/USDT 5m SMA baseline lost money in train,
validation, and the now-exposed final-test range. The 5m ADX candidate reduced
turnover but remained negative; the 1h ADX version also had negative gross
edge on train.

### Results

- Baseline train: 5064 trades, -67.14%, PF 0.4544.
- Baseline validation: 2468 trades, -33.95%.
- Baseline exposed test: 1355 trades, -24.64%.
- 5m ADX train: 1049 trades, -11.73%.
- 5m ADX validation: 447 trades, -5.31%.
- 1h ADX train: 99 trades, -1.85%, PF 0.71.

### Record limitation

The directory contains raw Freqtrade backtests but no manifest, metrics file,
or report. It is not a complete formal experiment and must not be presented as
one. All strategies remain `REJECTED`.

## exp_20260714_002 — minimum holding period

### Hypothesis

Delay regular exits by six 5m candles while preserving entry, ROI, stop loss,
position sizing, and forced exits, to test whether micro-reversals and repeated
round-trip costs explain the baseline failure.

### Result

4773 trades, -61.66%, PF 0.47869, maximum drawdown 61.87%. Fees fell by about
42.42 USDT and the loss improved by about 54.86 USDT, but no positive edge was
established. `REJECTED`; validation and final test were not opened.

## exp_20260824_001 — completed 1h breakout gate (superseded)

### Referenced Skills

The prior environment recorded the following references. On 2026-08-25 the
current checkout no longer contained `methodology.md`, `experiment-contract.md`,
or the ignored external `references/` tree, so they could not be re-read and are
not claimed as fresh evidence for the successor experiment.

| Skill | Purpose | Local path |
|---|---|---|
| quant-strategy-research | Fixed Subagent workflow and gates | `.codex/skills/quant-strategy-research/SKILL.md` |
| methodology | Time alignment, costs, robustness | `.codex/skills/quant-strategy-research/references/methodology.md` |
| experiment-contract | Artifact contract | `.codex/skills/quant-strategy-research/references/experiment-contract.md` |
| ohlcv-processing | Data integrity checklist | `references/claude-trading-skills/skills/ohlcv-processing/SKILL.md` |
| regime-detection | Regime hypothesis framing only | `references/claude-trading-skills/skills/regime-detection/SKILL.md` |
| backtesting-frameworks | Survivorship and OOS discipline | `references/quantitative-trading/plugins/quantitative-trading/skills/backtesting-frameworks/SKILL.md` |

### Observation and hypothesis

Both symbols, both train years, and all 24 full train months lost money. About
68.9% of baseline trades lasted at most four hours and had negative gross
profit. Test one structural change: permit the parent 5m entry only when the
last completed same-pair 1h close breaks above the preceding 24 completed 1h
highs.

### Implementation status

`DryRunSmaCrossHtfBreakoutGateStrategy` is frozen at SHA-256
`390f94e30e080feb64062a3b9b2b02fd48aef49ce5ccc89f8d3178fbd466e2bc`.
Twelve tests pass, Freqtrade loads the strategy, and a seven-day train smoke
backtest exits successfully with zero trades. Full train, bias checks,
robustness, validation, and final test did not run in this experiment.

### Decision

`INCONCLUSIVE / SUPERSEDED`. The four frozen Feather files were not present on
the successor host and the smoke ZIP did not contain source OHLCV. The historical
hashes were preserved; newly downloaded data was assigned to a new experiment.

### Future research direction

After this candidate is decided, construct a point-in-time Binance spot
universe for cross-sectional residual momentum/liquidity research. Do not use
today's surviving symbols as a historical universe. Funding, open interest,
and basis require a separate derivatives data version. LightGBM ranking is
deferred until a deterministic timestamp-by-pair panel and simple baselines
exist.

## exp_20260825_001 — HTF gate common-start training

### Referenced Skills and evidence

| Item | Purpose | Local path |
|---|---|---|
| quant-strategy-research | Fixed Subagent workflow and gates | `.codex/skills/quant-strategy-research/SKILL.md` |
| AGENTS.md | Current repository execution contract | `AGENTS.md` |
| exp_20260824_001 | Frozen candidate and original preregistration | `experiments/exp_20260824_001/` |

The Skill's `methodology.md` and `experiment-contract.md` references were missing
from this checkout. The limitation is recorded instead of claiming they were read.

### Data recovery and audit

Because the original local Feather files were absent, this experiment was
preregistered before a new Binance public spot OHLCV download. It uses a distinct
data version and does not reuse the old hashes. BTC/ETH 5m each contain 371,792
rows; 1h each contain 30,998 rows. All are UTC, strictly increasing, and free of
duplicates, null/inf, illegal OHLC, non-positive prices, and negative volume.

The training segment preserves the 2023-03-24 event gap: each 5m file has 16
missing candles and 14 zero-volume candles; each 1h file has one missing and one
zero-volume candle. The 1h downloader returned 14 hours beyond the requested end,
but formal training was capped before 2025-01-01 and no OOS strategy result was read.

### Subagent audit

- Researcher independently recomputed the parent failure mechanism and froze a
  sample-first decision table before the candidate result was read.
- Experimenter confirmed the informative timing, identified the missing data and
  required a strict common timerange rather than filtering old trades after the fact.
- Auditor confirmed the old experiment was unreproducible, the LF-normalized
  strategy hash still matched, and a redownload required a new experiment.

### Common-start results

Both strategies were run from `2023-01-02T01:00:00Z` to
`2025-01-01T00:00:00Z` with 1000 USDT, stake 50, max two positions, single-side
fee proxy 0.0015, and cache disabled.

| Strategy | Trades | Net P&L | Gross price P&L | Fees | PF | Sharpe | Max DD | <=4h share |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Parent | 5,057 | -670.490 | +85.501 | 755.990 | 0.4548 | -37.74 | 67.28% | 68.95% |
| 24h gate | 39 | -7.280 | -1.453 | 5.827 | 0.4776 | -0.31 | 1.02% | 64.10% |

Candidate pair x UTC close-year cells were 8/11/8/12 trades and only BTC 2024
was slightly profitable. Open-year assignment was identical. Removing the best
trade or best month made total loss worse.

### Decision

`INCONCLUSIVE`. The preregistered first gate required at least 100 total trades
and 20 in every pair x year cell. The candidate had 39 and 8/11/8/12, so the
experiment stopped. Negative gross and net edge, PF 0.4776, 64.10% short-duration
share, and only one profitable cell are additional rejection evidence.

No 18/36 neighbors, lookahead, recursive, cost stress, delay, event sensitivity,
walk-forward, validation, or final test was run. This direction will not be tuned
further; the next experiment is a point-in-time multi-symbol spot universe and
simple cross-sectional residual-momentum/liquidity baseline.

## exp_20260825_002 — archive inventory namespace failure

Preregistered a metadata-only Binance Data Vision Spot archive inventory. The frozen
implementation passed 23/23 repository tests and preserved a complete four-page root
listing, but rejected five observed Unicode symbol prefixes under an ASCII-only
validator. The formal run stopped before per-symbol listings or exchangeInfo and did
not download ZIP/CHECKSUM payloads. The experiment is retained as `INCONCLUSIVE`;
raw-page hashes and the exact failure are frozen in its artifact directory.

## exp_20260825_003 — Unicode-preserving Spot archive inventory

Preregistered a successor with a new v2 raw/processed root, exact UTF-8 symbol index,
full SHA-256 evidence directories, and no normalization or current-survivor filter.
Inventory-specific tests passed 14/14 and the repository passed 26/26 before fetch.

The formal metadata-only run completed with 3,695 archive prefixes and 723 suffix-USDT
candidates. For 2022-12 through 2024-12 it observed 9,240 monthly 1h ZIP objects and
9,240 matching CHECKSUM objects, with zero missing CHECKSUM objects and estimated ZIP
payload 292,861,199 bytes. The inventory JSONL SHA-256 is
`8be13634629f8fc21e499aaab7df46839510b3a5be4842ab620bfb3089f512b3`; the exact
symbol index SHA-256 is
`0b6df35cab25c9e393f901c923c0412084afbfdc956b171e1bef655907808c16`.

Only 462 candidates have any target-window ZIP, 261 have none, and 272 have all 25
months. `CVCUSDT` alone has an internal archive-month gap (2023-01 through 2023-04),
which remains unexplained. No Kline or CHECKSUM payload was downloaded, and current
exchangeInfo was used only as an observation-time comparison. Auditor independently
recomputed hashes, pagination, inventory pairing, raw-object coverage, and found no
blocking contradiction. The terminal decision is `NEEDS_MORE_DATA`.

The next independent experiment may download only the 9,240 frozen ZIP/CHECKSUM pairs,
verify publisher checksums and local SHA-256, audit the 12-field 1h Kline content, and
construct an `ARCHIVE_KLINE_AVAILABLE` panel. It must not call that panel a historical
`TRADING` universe or run residual momentum, backtests, or ML in the data-ingest round.

## exp_20260825_004 — Frozen Spot payload acquisition

Preregistered exact conditional S3 GETs for the 9,240 frozen ZIP/CHECKSUM pairs.
The downloader required `If-Match`, exact ETag/Content-Length/Last-Modified, official
SHA-256, safe single-member ZIP, strict UTF-8/12-column CSV, atomic object-month
validation and deterministic no-fill outputs. Independent preflight audit passed
after blockers around local reuse, truncation retry, redirects and derivation TOCTOU
were corrected. Repository tests passed 41/41 before the formal run.

All 18,480 public objects were acquired on the first request with exact frozen
metadata; 9,240/9,240 ZIPs matched official CHECKSUM. A preregistered exact
`close_time == open_time + 3,599,999` rule rejected 353 objects containing 354
checksum-valid non-nominal rows. Those months remained atomically `U`, making the
experiment `INCONCLUSIVE`. Postflight Auditor independently verified every raw
binding, normalized file and panel cell. The failed rule and its evidence were kept;
exp004 was not overwritten or resumed.

## exp_20260825_005 — Offline close-time successor and availability panel

Research review showed the 354 rows all satisfy
`open_time <= close_time <= nominal_close_time`, while all other original gates pass.
The successor changed only that one rule, retained the actual source close time, and
recorded the neutral code `NON_NOMINAL_CLOSE_TIME_WITHIN_INTERVAL`. It consumed only
frozen local exp004 evidence and had no network capability. Two preflight TOCTOU
issues were found by Lead/Auditor, corrected with immutable byte snapshots and
object-level derivation error handling, then independently re-audited. The repository
passed 50/50 tests before formal execution.

The full offline run validated 9,240/9,240 object-months and produced 6,687,797 rows.
The 354 events span 353 object-months. The 723 × 18,288 panel contains
`A=6,687,797`, `M=72,379`, `N=6,462,048`, `U=0`; all frozen regression invariants
matched and `contract_failures=[]`. Panel SHA-256 is
`716b2d5c42c3078c93707722cbd93e171b233e6492f770d3c0905a710d9ba8b2`; event ledger
SHA-256 is `2c54fcea7f3fcd5d4121cd96f9aff1d1952ddcbd4933b075c6684e4357efdb25`.
Postflight Auditor independently recomputed raw CHECKSUM/CSV contents, all 462
normalized gzip files and every panel cell, and returned PASS.

The terminal decision is `NEEDS_MORE_DATA`: this is a complete
`ARCHIVE_KLINE_AVAILABLE` panel for the frozen range, not a historical `TRADING`,
listing, permission, eligibility or executability universe. No factor, backtest or
ML work ran. The next data layer must establish point-in-time historical status and
trading-rule evidence independently.

## exp_20260826_001 — Current/forward Binance Spot PIT snapshot

After two retained Auditor NO-GO rounds hardened Content-Length, local clocks,
transport redaction, proxy/redirect behavior, exclusive snapshot leases and complete
retry ledgers, Researcher and Auditor issued limited GO for one exact public command.
The command requested only time/exchangeInfo/time from `data-api.binance.vision`,
without authentication. All three HTTP 200 responses arrived on the first attempt;
total response bytes were 17,512,713 and there were no retries or redirects.

The exchangeInfo response completed at `2026-08-25T18:53:18.027925Z`; its exact
17,512,657-byte body SHA-256 is
`93815999f9ce41e4918ea836928a8cbb7238eba89d4b1d6ad04823e69f0b4743`.
The complete response held 3,681 symbols. Union with the 723 frozen archive candidates
produced 3,682 memberships, including one archive-only explicit UNKNOWN. The
membership artifact is 26,917,791 bytes with SHA-256
`28dca84736c26497a79b3950fad9bd65b9f00f79e50cb6e87ca21d474c39a450`.

Trusted reload verified all raw/sidecar/attempt/row/source/artifact hashes and the
time bracket. Listing intervals remain null for 3,682/3,682 memberships, so strict
eligible count is 0 and the terminal decision is `NEEDS_MORE_DATA`. This creates a
current/forward evidence baseline only. It does not backfill 2023–2024, reinterpret
`BREAK` as historical delisting, or authorize Alpha, IC, ML or backtesting.

## exp_20260827_004 — Bound historical PIT evidence adapter

The offline adapter binds exact source bytes, authority/source policy, revision lineage,
known-at clocks and component-level provenance before constructing the existing synthetic
PIT compatibility snapshot. After retained Phase 2 NO-GO findings were corrected, the
two formal commands ran once each and passed adapter 27/27 plus the unchanged kernel
35/35. Independent postflight returned PASS.

The only accepted V1 policy remains an explicitly synthetic fixture. No official historical
payload was consumed and `empirical_authorized=false`, so the terminal decision remains
`NEEDS_MORE_DATA`; the adapter is a fail-closed ingestion contract, not a historical
eligibility result.

## exp_20260827_005 — End-to-end horizon identity

The experiment promoted `horizon_hours` to an exact identity on ensemble, regime-adjusted,
expected-net-alpha and diagnostic records. It rejects mixed horizons and Python equality
aliases such as bool, IntEnum and float before mapping comparisons. The public
`compose_multi_horizon` entry point creates only a canonical 1h/24h/120h/480h identity
bundle with no scalar, weights or economic aggregation semantics.

Research and Auditor reviews retained two contract-bypass NO-GO rounds and one API-name
NO-GO before the third refreeze. Formal hierarchical tests passed 48/48 and the historical
adapter regression passed 27/27, in order, once each and with no retry. Independent
postflight and record remediation closed PASS. The terminal artifact state is
`HORIZON_IDENTITY_SYNTHETIC_CONTRACT_VERIFIED / NEEDS_MORE_DATA`; no real data, Alpha,
IC, ML, P&L or backtest was used.
