# exp_20260714_002 实验报告

## Referenced Skills

| Skill | Local path | Purpose |
|---|---|---|
| quant-strategy-research | `.codex/skills/quant-strategy-research/SKILL.md` | 固定 Subagent 研究流程和门禁 |
| methodology | `.codex/skills/quant-strategy-research/references/methodology.md` | 成本、时间切分、成交时序和风险口径 |
| experiment-contract | `.codex/skills/quant-strategy-research/references/experiment-contract.md` | 实验产物和失败归档 |
| backtesting-frameworks | `references/quantitative-trading/plugins/quantitative-trading/skills/backtesting-frameworks/SKILL.md` | 回测偏差和样本外原则 |
| regime-detection / volatility-modeling | `references/claude-trading-skills/skills/` | 诊断阶段评估高级方向；本轮未实现 |

## 观察与假设

训练集基准 5064 笔交易，毛价差不足以覆盖成本；ADX 已减少交易但仍然亏损。假设只增加 6 根 5m K 线（30 分钟）最短持仓，限制普通交叉退出，保留止损、ROI、入场和所有指标不变，可以减少微小反转和双边成本。

## 代码变化

新增 `strategies/DryRunSmaCrossMinHoldStrategy.py`，SHA-256 为 `a90507fc6dd2928620266042d9a22c1ae9cb5bf22487915719d2d1ea48718d74`。父策略未修改；候选只通过 `confirm_trade_exit` 延迟普通退出，止损和强制退出立即允许。

## 数据与实验条件

- Binance spot，BTC/USDT、ETH/USDT；5m；训练集 `20230101-20250101`。
- Feather 数据哈希与 [manifest.json](manifest.json) 一致；每个文件 371602 行。
- 初始资金 1000 USDT，stake 50 USDT，最多 2 个仓位。
- 单边成本率 0.0015，订单按下一根可交易 K 线成交。
- 数据中保留 2023-03-24 的缺口和零成交量窗口，未反向填充。
- 当前目录不是 Git 仓库，Commit 为 `null`。

## 执行命令

完整命令和退出码见 [commands.txt](commands.txt)。单元测试 6/6 通过，四个策略均可加载。独立基准回测、基准 lookahead 和 recursive 检查均退出码 0。

## 基准结果

基准 `DryRunSmaCrossStrategy`：5064 笔，净收益 -671.437 USDT（-67.14%），PF 0.4544，胜率 20.8%，最大回撤约 67.36%，估算费用 757.04 USDT。BTC 与 ETH 均亏损，24 个完整月份全部亏损；因此没有打开候选验证集的理由，只有先验证训练期假设。

## 候选结果

候选 `DryRunSmaCrossMinHoldStrategy`：4773 笔，净收益 -616.578 USDT（-61.66%），最终余额 383.422 USDT，PF 0.47869，胜率 22.38%，最大回撤 61.87%，估算费用 714.616 USDT，平均持仓 3 小时 42 分钟。

相对基准，交易数下降 5.75%，估算费用下降约 42.42 USDT，亏损改善约 54.86 USDT；但 PF 仍低于 1，净收益仍显著为负。改善不足以证明存在可交易边际。

## 样本外结果

未运行验证集和最终测试集。训练门禁失败后按规则停止，避免污染冻结测试集。

## 偏差检查

基准的 `lookahead-analysis` 报告无偏差，`recursive-analysis` 报告无指标递归方差；这些检查只验证基准回测链路，不能替代候选检查。候选未进入偏差检查阶段。

## 成本压力

未运行候选 +50% 和 +100% 成本压力，因为基础成本训练门禁已失败。基础成本下的交易费用已占亏损的主要部分，最短持仓改善了费用但没有让单笔边际转正。

## 参数邻域与 Walk-forward

未运行。3、6、12 根持仓邻域和 Walk-forward 不再对本候选开放；继续调同一假设应建立新实验并重新预注册。

## 失败、异常和未执行测试

- 候选训练 PF < 1、净收益 < 0，状态为 `REJECTED`。
- 候选未运行 lookahead、recursive、验证、最终测试、成本压力和 Walk-forward。
- 一次错误地向 `lookahead-analysis` 传入不支持的 `--cache none`，退出码 2；移除该参数后基准检查退出码 0。该错误不影响候选结论。

## 决定

`REJECTED`。本轮只证明最短持仓能略微减少换手成本，不能证明策略存在可交易优势，也不能进入 dry-run。

## 下一轮方向

重新派出 Researcher，基于“flat/低波动和下行后入场状态持续亏损”的证据，评估一个真正单一的市场状态或多时间尺度确认假设。若所需数据超出 OHLCV，标记 `NEEDS_MORE_DATA`，不要伪造盘口、资金费率或链上信号。

## 产物索引

- [manifest.json](manifest.json)
- [hypothesis.md](hypothesis.md)
- [metrics.json](metrics.json)
- [commands.txt](commands.txt)
- [candidate strategy](../../strategies/DryRunSmaCrossMinHoldStrategy.py)
- [training backtest archive](backtests/train/backtest-result-2026-07-14_16-59-49.zip)
