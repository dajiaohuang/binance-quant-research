# Binance 量化研究进度

更新时间：2026-08-24（Asia/Shanghai）

## 一句话结论

当前没有可进入模拟盘的策略。基础 SMA 家族在 BTC/ETH 5m 和 1h 上均未建立
成本后正边际；最新方向已从 SMA/ADX 参数排列转为“Alpha Expert + 高周期状态
条件化”，首个 1h 突破门控候选已实现并通过工程冒烟，但完整训练结果尚未产生。

## 环境与研究边界

| 项目 | 已确认事实 |
|---|---|
| 环境 | Windows PowerShell、uv 0.11.25、CPython 3.12.13 |
| 框架 | Freqtrade 2026.6、CCXT 4.5.65 |
| 市场 | Binance spot，long-only，公开行情 |
| 品种/周期 | BTC/USDT、ETH/USDT；5m、1h |
| 资金/仓位 | 1000 USDT；stake 50；最多 2 仓 |
| 成本 | 单边 0.0015，开平分别扣；其中 0.0005 只是滑点/价差固定代理 |
| 成交 | 完成 5m 信号由 Freqtrade 延迟一根，下一根 open 成交 |
| 当前状态 | 无 Champion；无 `READY_FOR_PAPER_TRADING` |

## 数据快照

| 文件 | 行数 | SHA-256 |
|---|---:|---|
| `BTC_USDT-5m.feather` | 371602 | `040c4dec3af78c0f8011f26fe6daf52e21681e6150fdeb137acdc0b13672aaac` |
| `ETH_USDT-5m.feather` | 371602 | `f2573e2120d36438d65c2524e1a31313ea2d921d819add54ae83e5a1235feda9` |
| `BTC_USDT-1h.feather` | 30967 | `624c54c1278040d36aa13020339fc2da7c8ab7c121dc6204bbe7af8b486059e4` |
| `ETH_USDT-1h.feather` | 30967 | `db16358c830b05c542ae5802482b1bc7f1cfb4dcfeade8d6a8bc627f0174d98f` |

训练期四份数据均为 UTC、严格递增、无重复、null/inf、非法 OHLC、非正价格或
负成交量。BTC/ETH 在 2023-03-24 同时缺 16 根 5m K 线，缺口前有 14 根零量
flat K 线；1h 缺 13:00 UTC 一根，12:00 UTC 为零量。原始 Feather 保留这些事实，
但 Freqtrade 默认在内存中补 no-action K 线，这是后续敏感性检查的必选项。

## 已完成策略结果

| 策略 | 数据段 | 交易数 | 净收益 | PF | 决定 |
|---|---|---:|---:|---:|---|
| `DryRunSmaCrossStrategy` | 5m train 2023–2024 | 5064 | -67.14% | 0.4544 | `REJECTED` |
| `DryRunSmaCrossStrategy` | 5m validation 2025 | 2468 | -33.95% | 未完整汇总 | `REJECTED` |
| `DryRunSmaCrossStrategy` | 5m exposed test 2026-01-01–07-14 | 1355 | -24.64% | 未完整汇总 | 失败基准；测试已暴露 |
| `DryRunSmaCrossAdxStrategy` | 5m train 2023–2024 | 1049 | -11.73% | 约 0.535 | `REJECTED` |
| `DryRunSmaCrossAdxStrategy` | 5m validation 2025 | 447 | -5.31% | 未完整汇总 | `REJECTED`；未开最终测试 |
| `HourlySmaCrossAdxStrategy` | 1h train 2023–2024 | 99 | -1.85% | 0.71 | `REJECTED` |
| `DryRunSmaCrossMinHoldStrategy` | 5m train 2023–2024 | 4773 | -61.66% | 0.47869 | `REJECTED` |

独立复现基础 5m SMA 得到 5064 笔、-671.437 USDT、-67.14%、最终余额
328.563 USDT、胜率 20.8%、最大回撤 67.36%、Sharpe -37.76，与旧报告一致。
BTC、ETH、2023、2024 以及 24 个完整月份均亏损。约 68.9% 的交易持仓不超过
4 小时，这部分毛收益已经为负；因此失败不只是费用问题。

## 最新候选：1h 突破状态门控

实验：[exp_20260824_001](experiments/exp_20260824_001/report.md)

- 父策略：`DryRunSmaCrossStrategy`。
- 唯一变化：原 5m 入场只在同品种最后一根已完成 1h 收盘价高于此前 24 根
  完整 1h K 线最高价时允许。
- 精确公式：`close > high.shift(1).rolling(24).max()`。
- 退出、ROI、止损、仓位、5m SMA、成本和数据不变。
- 策略 SHA-256：
  `390f94e30e080feb64062a3b9b2b02fd48aef49ce5ccc89f8d3178fbd466e2bc`。
- 12/12 单测通过，策略加载 `OK`；7 天训练子集冒烟退出码 0，但 0 笔交易。
- 完整训练、PF、持仓机制、lookahead、recursive、成本压力、邻域、验证和最终
  测试均未运行。因此状态是“实现/冒烟完成，研究证据未完成”，不是可用策略。

训练基础门禁为净收益 > 0 且 PF > 1；同时要求至少 100 笔、每个品种×年份
至少 20 笔、四个单元至少三个盈利、短于等于 4 小时的占比降到 50% 以下。
不满足即拒绝或 `INCONCLUSIVE`，不会放宽突破定义。

## 讨论架构如何落地

长期研究架构来自此前讨论：

```text
Alpha Experts → Cross-sectional Alpha Model → Regime/Meta Model → Ensemble
```

本仓库不会一次性搭建复杂 ML 平台，而按证据逐层推进：

1. 当前 V1：价格 Alpha Expert（5m 趋势信号）+ 已完成 1h 状态门控。
2. 数据 V2：构造 point-in-time Binance spot universe，研究截面残差动量和流动性。
3. 数据 V3：把 funding、OI、mark/index/spot basis 作为拥挤度状态，不直接预测涨跌。
4. 只有简单规则与线性基准产生稳定 OOS cost-adjusted alpha 后，才评估
   LightGBM ranking；当前只有两个资产、没有 panel/ranking/组合分配层，不适合
   直接上 ML。

## Binance 公共 API / Data Vision 路线

首个候选不需要新增数据。下一轮扩展优先使用无需登录的 Binance 公共 Market
Data 与官方 Data Vision 归档，保留原始响应/ZIP、官方 CHECKSUM、下载时间、
schema 和时间戳单位。2025 年起 spot 归档时间戳为微秒，下载器必须显式识别。

截面研究不能用“今天仍存在的币”回填 2023 年历史。需要按当时可用信息建立
上线/下线和交易状态 universe，并至少获取 OHLCV、quote volume、交易笔数、
taker buy/sell volume 与交易规则。资金费率/OI/basis 另建数据版本；订单簿历史
若未持续归档，不能从当前深度快照事后重建。

官方来源：

- [Binance Spot public market data](https://developers.binance.com/en/docs/products/spot/rest-api)
- [Binance Public Data archive](https://github.com/binance/binance-public-data)

## 已知限制与边界事件

- 0.0015 成本全部以 fee 扣减，没有真正改变成交价或建模盘口、冲击、部分成交。
- Freqtrade 同一根 K 线内 ROI/止损的事件顺序仍是 K 线模型近似。
- 新候选 startup 从 50 提高到 300 根 5m K 线，正式父子比较需按共同有效起点复核。
- 阶段 2 Auditor 因本轮被中断，冻结候选的最终独立审计未完成。
- 一次审计递归搜索意外打印旧实验 validation/test `.meta.json` 的策略名和时间戳；
  未读取 ZIP、指标、交易或 OOS OHLC，也未用于当前候选选择。
- `exp_20260714_001` 只有原始回测目录，没有完整 Manifest/metrics/report；不能把它
  描述为完整正式实验。`exp_20260714_002` 是已完成的正式失败实验。

## 下一步

1. 用冻结 SHA 的 24 小时候选运行完整训练回测；不先跑 18/36 邻域。
2. 按共同有效起点复核父子，并检查交易数、品种×年份、短持仓比例和收益集中度。
3. 只有完整训练门禁通过，才运行 lookahead/recursive、18/36 邻域、成本与延迟。
4. 只有上述门禁通过，才一次性打开验证集；最终测试保持封闭。
5. 本候选裁决后，再为 point-in-time 多币种 universe 建立独立 Binance 数据实验。
