# Binance Quant Research

这是一个以可复现、无未来函数和样本外纪律为核心的 Binance 现货策略研究仓库。
主回测框架是 Freqtrade 2026.6；默认最高状态是 `READY_FOR_PAPER_TRADING`，
不支持实盘订单，也不包含交易所密钥。

## 当前结论

- 没有 Champion，没有策略达到 `READY_FOR_PAPER_TRADING`。
- 已完成的 SMA、SMA+ADX、1h SMA+ADX、最短持仓实验全部 `REJECTED`。
- 最新候选 `DryRunSmaCrossHtfBreakoutGateStrategy` 只完成实现、12/12 单测、
  策略加载和 7 天训练子集冒烟；冒烟 0 笔交易，完整训练尚未运行，不能称为有效策略。
- 当前 `quant-dry-run` 仍加载已拒绝的基础 SMA，只能用于短时基础设施验证，
  不得无人值守运行。

详细进度、全部指标、新策略方向和数据路线见 [PROGRESS.md](PROGRESS.md)。

## 已冻结研究条件

| 项目 | 当前口径 |
|---|---|
| 市场 | Binance public spot OHLCV |
| 品种 | BTC/USDT、ETH/USDT |
| 周期 | 5m、1h |
| 训练集 | `20230101-20250101` |
| 验证集 | `20250101-20260101` |
| 最终测试 | `20260101-20260714` |
| 初始资金 | 1000 USDT |
| 单笔 stake | 50 USDT |
| 最大仓位 | 2 |
| 单边成本代理 | 0.0015，开平分别计费 |
| 成交 | 完成 K 线信号，Freqtrade 下一根 5m open |

原始 Feather 位于本地 `user_data/data/binance/`，不会上传 GitHub；正式实验通过
SHA-256、行数和 UTC 范围引用数据。2023-03-24 的 Binance 事件窗口含零量 K 线和
缺口，原始文件不填充；Freqtrade 回测加载器会在内存中生成零量 no-action K 线，
正式候选必须披露并做敏感性检查。

## 研究流程

```text
数据审计 → 基准复现 → 预注册单一假设 → 训练集回测 → 训练门禁
→ 一次性验证 → lookahead/recursive → 成本/参数/延迟压力
→ Walk-forward/跨品种/市场状态 → 最终测试一次 → 受控模拟盘
```

训练集净收益不为正或 Profit Factor 不大于 1 的简单候选直接拒绝，不打开验证集。
完整约束见 [AGENTS.md](AGENTS.md) 和
[quant-strategy-research Skill](.codex/skills/quant-strategy-research/SKILL.md)。

## 快速开始

要求 Windows PowerShell、uv、CPython 3.12：

```powershell
$env:PYTHONUTF8 = "1"
uv sync --frozen
uv run python -m unittest discover -s tests -v
uv run freqtrade --version
uv run quant-freqtrade-research list-strategies --strategy-path strategies
uv run quant-freqtrade-research list-data --config config/freqtrade-dry-run.json --show-timerange --data-format-ohlcv feather
```

正式训练回测模板：

```powershell
uv run quant-freqtrade-research backtesting `
  --config config/freqtrade-dry-run.json `
  --strategy STRATEGY_NAME `
  --strategy-path strategies `
  --timeframe 5m `
  --timerange 20230101-20250101 `
  --fee 0.0015 `
  --cache none `
  --export trades `
  --backtest-directory experiments/exp_YYYYMMDD_NNN/backtests/train
```

`quant-freqtrade-research` 只放行数据、检查、回测和分析命令并拒绝 `trade`。
模拟盘只能从 `uv run quant-dry-run` 进入，且仍是 `--dry-run`。

## 仓库内容

- `strategies/`：全部当前及失败策略；失败实现不会删除。
- `experiments/`：Manifest、假设、参数、命令、指标、报告和回测 ZIP。
- `tests/`：下一根成交、安全 launcher、数据和 informative 1h 时序测试。
- `src/quant_research/`：安全 Freqtrade launcher 与最小显式参考实现。
- `.codex/skills/quant-strategy-research/`：本仓库固定研究工作流。
- `research/RESEARCH_LOG.md`：追加式研究记录。

本地原始行情、虚拟环境、dry-run 数据库、根日志、临时审计输出和第三方
`references/` 仓库不进入 Git；其数据哈希、来源和实际引用文件记录在实验报告中。
