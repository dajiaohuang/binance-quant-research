# exp_20260824_001 最终报告（数据不可复现）

## Referenced Skills

| Skill | Purpose | Local path |
|---|---|---|
| quant-strategy-research | 固定 Subagent 工作流与门禁 | `.codex/skills/quant-strategy-research/SKILL.md` |
| methodology | 时间、成本和稳健性口径 | `.codex/skills/quant-strategy-research/references/methodology.md` |
| experiment-contract | 实验产物契约 | `.codex/skills/quant-strategy-research/references/experiment-contract.md` |
| ohlcv-processing | OHLCV 完整性检查 | `references/claude-trading-skills/skills/ohlcv-processing/SKILL.md` |
| regime-detection | 市场状态假设框架；未复制默认阈值 | `references/claude-trading-skills/skills/regime-detection/SKILL.md` |
| backtesting-frameworks | 幸存者偏差和样本外纪律 | `references/quantitative-trading/plugins/quantitative-trading/skills/backtesting-frameworks/SKILL.md` |

## 观察与假设

基础 5m SMA 在两个品种、两个训练年份和 24 个完整月份均亏损。约 68.9% 的
交易不超过 4 小时且毛收益已经为负。候选只增加一个同品种已完成 1h 突破门控，
尝试筛掉不能持续的短周期交叉。

## 代码变化

新增 `strategies/DryRunSmaCrossHtfBreakoutGateStrategy.py`，SHA-256 为
`390f94e30e080feb64062a3b9b2b02fd48aef49ce5ccc89f8d3178fbd466e2bc`。
公式为 `close > high.shift(1).rolling(24).max()`；候选只清除父策略入场，
指标、退出、ROI、止损、仓位和配置保持不变。新增 6 组测试方法，共 12/12 单测通过。

## 数据与实验条件

- Binance spot BTC/USDT、ETH/USDT；5m + 同品种 1h。
- 训练 `20230101-20250101`，验证 `20250101-20260101`，最终测试
  `20260101-20260714`。
- 初始 1000 USDT、stake 50、最多 2 仓、单边成本代理 0.0015。
- 原始数据哈希见 `manifest.json`；原始缺口保留，Freqtrade 内存补 no-action K 线。

## 执行命令

完整命令与退出码见 `commands.txt`。已执行单测、策略加载和 7 天训练子集冒烟。

## 基准结果

独立复现 5064 笔、-671.437 USDT（-67.14%）、PF 0.4544、胜率 20.8%、
最大回撤 67.36%、Sharpe -37.76。

## 候选结果

工程测试通过。训练子集冒烟请求 `20230101-20230108`，因 300 根 warm-up 的
有效区间为 2023-01-02 01:00 至 2023-01-08 00:00，0 笔交易。该结果只证明
管线可运行，不能评价边际。完整训练尚未运行。

## 样本外结果

验证集和最终测试均未运行。

## 偏差检查

单测确认 14:00–15:00 的 1h 值最早在 14:55 的 5m K 线闭合后可用，
Freqtrade 再把信号移到 15:00 open；追加未来行不改变历史 gate。正式
`lookahead-analysis` 和 `recursive-analysis` 尚未运行。

## 成本压力

未运行。只有基础完整训练通过后才允许执行。

## 参数邻域与 Walk-forward

18/36 小时邻域、延迟、事件窗口敏感性和 Walk-forward 均未运行，且在 24 小时
主参数通过训练门禁前禁止运行。

## 集中度和风险

候选完整训练未运行，因此没有可报告的品种×年份、月份、持仓和收益集中度。

## 失败、异常和未执行测试

- 冒烟 0 笔，显示门控严格，但样本太短，不能提前放宽定义。
- startup 由 50 提高到必要的 300；正式父子比较需按共同有效起点复核。
- 阶段 2 Auditor 的最终冻结审计因任务中断未完成。
- 一次 Auditor 递归搜索意外打印旧实验 OOS `.meta.json` 的策略名和时间戳，
  未读取指标、交易、ZIP 或 OOS OHLC，不用于当前选择。
- 完整训练、正式偏差检查、稳健性、验证和最终测试全部未执行。

## 决定

`INCONCLUSIVE / SUPERSEDED`。当前主机缺少 Manifest 声明的四份本地 Feather，
smoke ZIP 不包含源 OHLCV，因此冻结完整训练无法按原数据字节复现。候选 LF
规范化源码仍匹配冻结 SHA，问题在数据材料而不是已发现的代码漂移。

本实验没有使用重新下载的数据回填，也没有运行验证或最终测试。新数据版本和
严格共同起点复现在 `exp_20260825_001` 中独立记录。

## 下一轮方向

`exp_20260825_001` 已接替本实验完成训练裁决；之后单独建立 point-in-time
Binance 多币种 universe，研究截面残差动量/流动性。funding、OI 与 basis 属于
另一数据版本。

## 产物索引

- `manifest.json`
- `hypothesis.md`
- `parameters.json`
- `metrics.json`
- `commands.txt`
- `backtests/smoke/`
- `logs/smoke.log`
- `../../strategies/DryRunSmaCrossHtfBreakoutGateStrategy.py`
- `../../tests/test_htf_breakout_gate_strategy.py`
