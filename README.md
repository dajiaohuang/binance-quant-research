# Binance Quant Research

这是一个以可复现、无未来函数和样本外纪律为核心的 Binance 现货策略研究仓库。
主回测框架是 Freqtrade 2026.6；默认最高状态是 `READY_FOR_PAPER_TRADING`，
不支持实盘订单，也不包含交易所密钥。

## 当前结论

- 没有 Champion，没有策略达到 `READY_FOR_PAPER_TRADING`。
- 已完成的 SMA、SMA+ADX、1h SMA+ADX、最短持仓实验全部 `REJECTED`。
- 最新 `exp_20260825_001` 已完成 24 小时高周期突破门控训练：39 笔、净收益
  -0.73%、成本前 P&L -1.45 USDT、PF 0.4776，裁决 `INCONCLUSIVE`。样本不足，
  且边际与短持仓机制同时失败；未打开验证或最终测试。
- `exp_20260825_004` 已完整获取 9,240 个 Binance Spot 月度 1h ZIP 与 CHECKSUM；
  严格 close-time 恒等式实验因 353 个异常对象月保留为 `INCONCLUSIVE`。
- `exp_20260825_005` 已离线重验全部原始对象并建立无回填
  `ARCHIVE_KLINE_AVAILABLE` 面板：6,687,797 根 K 线，723×18,288 个状态单元，
  `U=0`；354 条区间内非名义 close time 原样保留并中性标记。
- `exp_20260826_001` 建立了从 2026 known-at 起可用的 current/forward Spot PIT
  快照，但 3,682/3,682 个 membership 的 listing interval 仍未知，strict eligible=0。
- `exp_20260827_004` 和 `exp_20260827_005` 分别完成历史 PIT 证据适配器合同、
  以及 1h/24h/120h/480h Alpha horizon identity 的端到端合成验证；这两项都不是
  真实 Alpha、IC、收益或回测结果。
- 数据状态仍为 `NEEDS_MORE_DATA`，因为 archive Kline 可用性不是历史
  `TRADING`/上市/权限/eligibility 证据。下一条路线是独立补历史状态，不是继续
  搜索 SMA/ADX/突破参数，也不直接上 ML。
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

## Modern Alpha Research V1

当前新增了一个通过合成测试的 fail-closed 分层 Alpha 研究内核，但实证研究仍为
`NEEDS_MORE_DATA`：缺少历史 PIT Binance Spot eligibility universe，因此不允许
计算真实 factor/score/IC/P&L、拟合模型或回测。完整架构、合同、readiness 与下一步
见 [Modern Alpha Research V1](research/MODERN_ALPHA_RESEARCH_V1.md)。

`exp_20260825_006` 因 SciPy list 输入兼容性错误以 `INCONCLUSIVE` 关闭；独立
successor `exp_20260825_007` 完成预注册修复并通过窄测 35/35、全仓 85/85 与
postflight audit，终态为 `NEEDS_MORE_DATA`，不是策略或收益结论。

`exp_20260826_001` 又冻结了一份无需认证的 Binance Spot current/forward PIT
快照：3,681 个当前 symbol 与 archive candidates 合并为 3,682 条 membership，
artifact SHA-256 为
`28dca84736c26497a79b3950fad9bd65b9f00f79e50cb6e87ca21d474c39a450`。
所有 listing interval 仍未知，因此 strict eligible=0。这不是历史 universe，不能
回填 2023–2024，也没有解锁 Alpha、IC、模型或回测。

`exp_20260827_004` 通过 27/27 evidence-adapter tests 和 35/35 既有 kernel tests；
`exp_20260827_005` 通过 48/48 horizon contract tests 和 27/27 adapter regression，
两条正式命令各执行一次、零重试，并完成独立 postflight。最新内核会拒绝混合
horizon 和 Python 数值别名绕过，并用 `compose_multi_horizon` 提供无标量、无权重
的 exact-four identity bundle。终态仍是 `NEEDS_MORE_DATA`。

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
uv run quant-release-tests
uv run freqtrade --version
uv run quant-freqtrade-research list-strategies --strategy-path strategies
uv run quant-freqtrade-research list-data --config config/freqtrade-dry-run.json --show-timerange --data-format-ohlcv feather
```

绝大多数公共数据研究不需要 API Key。确需只读 Key 的本地实验，可复制
`.env.example` 为 `.env.binance.local` 后仅在本机填写；所有 `.env*` 默认忽略，
唯独无秘密的 `.env.example` 可以提交。不要使用带交易或提现权限的 Key，详见
[SECURITY.md](SECURITY.md)。

`quant-release-tests` 运行除三个 consumed-run workspace-absence precondition 之外的
全部测试。这三个测试只适用于对应正式运行前；其 reservation/ledger 现已作为失败
实验的不可变证据保留。因此在完整证据仓库上直接运行原始 `unittest discover` 会
得到这三个预期失败，发布入口不会删除证据或改写冻结测试来制造全绿。

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
- `experiments/`：Manifest、假设、参数、命令、指标、报告和回测 ZIP；入口见
  [实验索引](experiments/README.md)。
- `tests/`：下一根成交、安全 launcher、数据和 informative 1h 时序测试。
- `src/quant_research/`：安全 Freqtrade launcher 与最小显式参考实现。
- `.codex/skills/quant-strategy-research/`：本仓库固定研究工作流。
- `research/RESEARCH_LOG.md`：追加式研究记录。

本地原始行情、虚拟环境、dry-run 数据库、根日志、临时审计输出和第三方
`references/` 仓库不进入 Git；其数据哈希、来源和实际引用文件记录在实验报告中。
