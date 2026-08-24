# Research Log

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

## exp_20260824_001 — completed 1h breakout gate (in progress)

### Referenced Skills

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
robustness, validation, and final test have not run.

### Decision

`IN_PROGRESS`, not a strategy-quality result. The next authorized experiment is
the frozen 24-hour primary train run only. Do not run 18/36 neighbors or open
validation until the primary training gate passes.

### Future research direction

After this candidate is decided, construct a point-in-time Binance spot
universe for cross-sectional residual momentum/liquidity research. Do not use
today's surviving symbols as a historical universe. Funding, open interest,
and basis require a separate derivatives data version. LightGBM ranking is
deferred until a deterministic timestamp-by-pair panel and simple baselines
exist.
