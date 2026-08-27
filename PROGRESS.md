# Binance 量化研究进度

更新时间：2026-08-27（Asia/Singapore）

## 一句话结论

当前没有可进入模拟盘的策略。SMA/ADX/高周期门控路线已经停止调参；Spot archive
和 current/forward PIT 证据仍不能建立 2023–2024 历史 eligibility。现代 Alpha
内核、历史证据适配器和多周期身份合同已经通过离线合成验证，但终态仍是
`NEEDS_MORE_DATA`；在 historical PIT gate 打开前不运行真实截面因子、ML 或回测。

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
| `BTC_USDT-5m.feather` | 371792 | `b68e0a0487cd38e005c969852d5872e604260f13b5681fd3c1ae932828198878` |
| `ETH_USDT-5m.feather` | 371792 | `cb228c791f0aa2479f7d9db3ae9711ce4e2b0e58114749f4831297b2f4289db5` |
| `BTC_USDT-1h.feather` | 30998 | `d4a5022d0cc465e21cb5064f647be4fc74b3a4ffef9cd9c8bc1503130342f678` |
| `ETH_USDT-1h.feather` | 30998 | `c56f7312f4acb469e61854904239b6bd62a2198f9f03fad97d43819705659615` |

训练期四份数据均为 UTC、严格递增、无重复、null/inf、非法 OHLC、非正价格或
负成交量。BTC/ETH 在 2023-03-24 同时缺 16 根 5m K 线，缺口前有 14 根零量
flat K 线；1h 缺 13:00 UTC 一根，12:00 UTC 为零量。原始 Feather 保留这些事实，
但 Freqtrade 默认在内存中补 no-action K 线，这是后续敏感性检查的必选项。
这是一份 2026-08-25 新下载并独立冻结的数据版本；旧 2026-08-24 Manifest 的
历史哈希仍保留，但对应本地文件没有随 Git 迁移。1h 下载器实际多返回到
2026-07-15 14:00 UTC，训练严格截止 2025-01-01。

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
| `DryRunSmaCrossHtfBreakoutGateStrategy` | common-start train 2023–2024 | 39 | -0.73% | 0.4776 | `INCONCLUSIVE`；样本和机制失败 |

独立复现基础 5m SMA 得到 5064 笔、-671.437 USDT、-67.14%、最终余额
328.563 USDT、胜率 20.8%、最大回撤 67.36%、Sharpe -37.76，与旧报告一致。
BTC、ETH、2023、2024 以及 24 个完整月份均亏损。约 68.9% 的交易持仓不超过
4 小时，这部分毛收益已经为负；因此失败不只是费用问题。

## 最新候选：1h 突破状态门控

实验：[exp_20260825_001](experiments/exp_20260825_001/report.md)

- 父策略：`DryRunSmaCrossStrategy`。
- 唯一变化：原 5m 入场只在同品种最后一根已完成 1h 收盘价高于此前 24 根
  完整 1h K 线最高价时允许。
- 精确公式：`close > high.shift(1).rolling(24).max()`。
- 退出、ROI、止损、仓位、5m SMA 和成本不变；因旧本地数据丢失，新实验在新
  快照上同时重跑父子策略，不与旧数值直接比较。
- 策略 SHA-256：
  `390f94e30e080feb64062a3b9b2b02fd48aef49ce5ccc89f8d3178fbd466e2bc`。
- 12/12 单测通过，策略加载 `OK`；完整训练共同窗口为 2023-01-02 01:00 UTC
  至 2025-01-01 00:00 UTC。
- 候选 39 笔、净收益 -7.280 USDT（-0.73%）、成本前价格 P&L -1.453、费用
  5.827、PF 0.4776、Sharpe -0.31、最大回撤 1.02%。
- ≤4h 交易 25 笔，占 64.10%，净亏 -10.657；四个品种×年份单元交易数
  8/11/8/12，只有 BTC 2024 微利 +0.250 USDT。

新实验在看结果前冻结“样本充分性优先”：至少 100 笔且每个单元至少 20。
因此正式裁决为 `INCONCLUSIVE`。成本前负边际、PF 0.4776、64.10% 短持仓和
仅 1/4 单元盈利是额外拒绝证据；不运行 18/36 邻域或样本外。

## Spot archive inventory

成功实验：[exp_20260825_003](experiments/exp_20260825_003/report.md)。前序
`exp_20260825_002` 因 ASCII-only symbol 校验器拒绝 5 个真实 Unicode archive
前缀而显式失败，已保留为 `INCONCLUSIVE`，没有覆盖或续跑旧证据。

| 项目 | 结果 |
|---|---:|
| archive symbol 前缀 | 3,695 |
| `USDT` 字符串后缀候选 | 723 |
| 2022-12 至 2024-12 月度 1h ZIP 元数据 | 9,240 |
| 匹配 CHECKSUM 元数据 | 9,240 |
| 缺 CHECKSUM | 0 |
| 预计 ZIP payload | 292,861,199 bytes |
| 有任意目标月份 ZIP 的候选 | 462 |
| 目标窗口零覆盖候选 | 261 |
| 完整 25 个月候选 | 272 |

inventory JSONL SHA-256 为
`8be13634629f8fc21e499aaab7df46839510b3a5be4842ab620bfb3089f512b3`；symbol
index SHA-256 为 `0b6df35cab25c9e393f901c923c0412084afbfdc956b171e1bef655907808c16`。
唯一内部月份断层是 `CVCUSDT` 的 2023-01 至 2023-04，不能填充或解释为停牌/
下市。当前快照与 archive 的 250/11 个集合差异也不能回填成历史状态。

该 inventory 实验本身没有下载 ZIP/CHECKSUM payload；后继 payload 结果如下。

## Spot archive payload 与可用性面板

- `exp_20260825_004` 精确下载了 9,240 ZIP 与 9,240 CHECKSUM。18,480 个对象均
  HTTP 200，ETag/size/Last-Modified 与冻结 inventory 一致，9,240/9,240 ZIP 匹配
  官方 SHA-256。原始 ZIP 292,861,199 bytes，CHECKSUM 818,811 bytes。
- exp004 预注册的 `close_time == open_time + 3,599,999` 门禁拒绝 353 个对象月；
  354 条 checksum-valid 原始行的 close time 早于名义区间末端，因此 exp004
  终态为 `INCONCLUSIVE`，失败月整月 `U`，没有部分行泄出。
- `exp_20260825_005` 作为独立、纯离线 successor，只把该门禁改为
  `open_time <= close_time <= nominal_close_time`。实际 close time 不改写，统一以
  `NON_NOMINAL_CLOSE_TIME_WITHIN_INTERVAL` 中性标记。
- exp005 全量重验 9,240/9,240 对象月，得到 6,687,797 行；354 条标记分布在
  353 个对象月，所有冻结回归不变量命中，`contract_failures=[]`。
- 面板为 723 symbols × 18,288 hours：`A=6,687,797`、`M=72,379`、
  `N=6,462,048`、`U=0`。面板 SHA-256：
  `716b2d5c42c3078c93707722cbd93e171b233e6492f770d3c0905a710d9ba8b2`。
- 事件 ledger SHA-256：
  `2c54fcea7f3fcd5d4121cd96f9aff1d1952ddcbd4933b075c6684e4357efdb25`。

独立 Auditor 重算全部 CHECKSUM、原始 CSV、462 个 normalized gzip 和每个 panel
cell 后给出 PASS。这个结果只证明 archive Kline 可用性；不能据此声称历史
quoteAsset、SPOT permission、`TRADING`、上市/下市、eligibility 或可执行性。

## 讨论架构如何落地

### 2026-08-25 V1 kernel

分层 Alpha 的 data-agnostic、fail-closed 研究内核已经建立，代码位于
`src/quant_research/hierarchical_alpha.py`。`exp_20260825_006` 的首次窄测因 SciPy
1.18.0 不接受 Python list 的 `lstsq` 输入而以 `INCONCLUSIVE` 关闭；单一修复实验
`exp_20260825_007` 显式转换 NumPy float arrays，随后窄测 35/35、全仓 85/85，并
通过 postflight audit，终态 `NEEDS_MORE_DATA`。

这只证明合成内核合同可运行。真实 Expert、Linear/LightGBM/Neural 模型、rank IC、
P&L 和回测仍被 PIT universe 门禁阻断；RL 不在 V1 路线中。下一步仍是独立冻结
Binance Spot 历史 status、permission、quote asset 与 listing effective interval
证据，不能把 archive availability 当作 eligibility。架构入口见
`research/MODERN_ALPHA_RESEARCH_V1.md`。

长期研究架构来自此前讨论：

```text
Alpha Experts → Cross-sectional Alpha Model → Regime/Meta Model → Ensemble
```

本仓库不会一次性搭建复杂 ML 平台，而按证据逐层推进：

1. 已裁决 V1：价格 Alpha Expert（5m 趋势信号）+ 1h 状态门控，未建立边际。
2. 数据 V2：archive inventory、payload 和 `ARCHIVE_KLINE_AVAILABLE` 面板已完成；
   下一层补独立历史状态与 eligibility，之后才研究截面残差动量和流动性。
3. 数据 V3：把 funding、OI、mark/index/spot basis 作为拥挤度状态，不直接预测涨跌。
4. 只有简单规则与线性基准产生稳定 OOS cost-adjusted alpha 后，才评估
   LightGBM ranking；当前只有两个资产、没有 panel/ranking/组合分配层，不适合
   直接上 ML。

## Binance 公共 API / Data Vision 路线

下一轮扩展优先使用无需登录的 Binance 公共 Market
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
- 候选 startup 从 50 提高到 300 根 5m K 线；父子已用严格共同 timerange 重跑。
- Researcher、Experimenter 和 Auditor 均完成独立审查；Auditor 确认旧数据缺失
  必须换实验，新实验按预注册样本优先裁决 `INCONCLUSIVE`。
- 一次审计递归搜索意外打印旧实验 validation/test `.meta.json` 的策略名和时间戳；
  未读取 ZIP、指标、交易或 OOS OHLC，也未用于当前候选选择。
- `exp_20260714_001` 只有原始回测目录，没有完整 Manifest/metrics/report；不能把它
  描述为完整正式实验。`exp_20260714_002` 是已完成的正式失败实验。

## 2026-08-27 合同加固

- `exp_20260827_004` 建立 bound historical PIT evidence adapter。正式运行依次通过
  adapter 27/27 与既有 kernel 35/35，独立 postflight PASS；唯一可用 policy 仍是
  synthetic fixture，因此 `empirical_authorized=false`。
- `exp_20260827_005` 把 horizon identity 提升为 Ensemble、RegimeAdjusted、
  ExpectedNetAlpha 和 DiagnosticScore 的端到端合同，并增加
  `compose_multi_horizon` exact-four identity bundle。正式运行通过 48/48 与 27/27，
  各执行一次、零重试，最终闭包为
  `HORIZON_IDENTITY_SYNTHETIC_CONTRACT_VERIFIED / NEEDS_MORE_DATA`。
- 这两轮没有读取真实行情、API Key、验证集或最终测试集，也没有产生真实 Alpha、
  IC、模型、P&L 或回测结果。

## 下一步

`exp_20260826_001` 已完成 current/forward PIT snapshot：正式 public API fetch 只有
time/exchangeInfo/time 各一次，known-at 为 `2026-08-25T18:53:18.027925Z`；3,682
membership 中 listing-null=3,682、strict eligible=0，终态 `NEEDS_MORE_DATA`。
它只为今后持续 forward snapshot 提供可审计起点，没有形成 2023–2024 历史
eligibility。

1. 新建独立历史状态/交易规则数据实验，为 symbol-time 收集可审计的
   `TRADING`、SPOT permission、quote asset、上线/下线和 eligibility 证据；不得
   用当前 exchangeInfo 或 archive Kline 出现回填过去。
2. 明确状态未知时的 universe 排除规则、证据冲突优先级和退市时可成交语义，
   并与已完成的 `ARCHIVE_KLINE_AVAILABLE` 面板分层保存。
3. 只有历史状态门禁通过后，才预注册简单截面残差动量/流动性基准，先做 rank IC、decay、turnover
   和成本后分位数组合；不直接上 LightGBM。
4. funding、OI、mark/index/spot basis 另建衍生品数据版本，不能与 spot 原始数据
   混写或用当前快照回填历史。
5. 如果没有出现新的、可命名且可审计的历史官方来源，只允许继续完善离线研究
   基础设施；下一项优先实现与 forward label horizon 对齐的 purge/embargo split
   builder，不得借此绕过 historical eligibility gate 运行实证。
