# Modern Alpha Research V1

## 结论与非结论

本仓库已经建立并通过测试一个纯合成、fail-closed 的分层 Alpha 研究内核。
它能强制检查 point-in-time（PIT）资格证据、观测时钟、单时点截面变换、未来
标签、purge/embargo、固定分层集成、外部状态调节和净 Alpha 惩罚。

这不是一个已经发现 Alpha 的策略。V1 没有读取真实行情来计算因子，没有产生
真实 score、rank IC、收益、换手或 P&L，没有拟合 Linear、LightGBM、神经网络，
也没有回测。当前结论只能是 `NEEDS_MORE_DATA`：首先需要可审计的历史 PIT
Binance Spot eligibility universe。

## 研究架构

```text
PIT Evidence / Clock Gate
          │
          ▼
Immutable Alpha Expert Registry
          │
          ▼
Single-formation Representation
(winsorize → centered average rank → residualize)
          │
          ▼
Future model ladder — all blocked today
(Linear → LightGBM → Neural; no RL)
          │
          ▼
Next-open labels: 1h / 24h / 120h / 480h
          │
          ▼
Per-horizon fixed two-level simplex ensemble
          │
          ▼
Common external regime regulator [0.7, 1.3]
(V1 conservative stub; not a learned router)
          │
          ▼
Expected Net Alpha
          │
          ▼
rank IC and fixed full-vs-ablated ΔIC diagnostics
```

模型梯子只是未来晋级顺序，不是当前实现结果。只有简单线性基准在严格 PIT、
purged time split 和成本门禁上显示稳定增量后，才允许评估 LightGBM；只有树模型
仍有未解释的稳定残差且样本量充分时，才允许神经网络。V1 不做强化学习，也不以
复杂度替代信号证据。

## Expert readiness

| Family | V1 状态 | 原因与允许边界 |
|---|---|---|
| Price | `PIT_BLOCKED` | archive 有 OHLC，但 archive availability 不证明当时 eligible |
| Liquidity | `PIT_BLOCKED` | archive 有 volume/quote volume，仍缺 PIT universe 与可交易语义 |
| Flow | `PIT_BLOCKED` | archive 有 trades、taker buy volume/quote volume，仍不能推导资格 |
| Risk | `PIT_BLOCKED` | 可由价格构造候选，但必须先通过 PIT membership gate |
| RelativeValueResidual | `PIT_BLOCKED` | 截面残差要求同一时点的合格 universe 与完整键对齐 |
| Structural | `DATA_BLOCKED` | 缺 point-in-time supply/schedule 数据 |
| Derivatives | `DATA_BLOCKED` | 缺冻结的 funding、OI、mark/index 与 spot basis 数据版本 |
| Microstructure | `DATA_BLOCKED` | 缺历史盘口、价差、深度和冲击数据 |
| EventNLP | `DATA_BLOCKED` | 缺带发布时间和可用延迟的 PIT 事件语料 |

`Value`、`Quality` 不直接照搬股票定义。加密资产没有可直接等价于企业账面价值、
盈利质量或财报发布日期的统一口径；若未来提出同名 family，必须先写出加密原生、
可证伪且 point-in-time 的定义。

Binance archive 目前已验证部分 OHLCV、quote volume、trade count 和 taker 子集，
但它只表示 `ARCHIVE_KLINE_AVAILABLE`。它不证明历史 `TRADING` 状态、SPOT
permission、quote asset、listing 生效区间、成交规则或可执行性，不能当作 PIT
eligibility。

## 冻结数学与时钟合同

### PIT eligibility

资格不是调用者传入的布尔值，而由内核从分项证据推导：venue 必须为 Binance、
market type 为 SPOT、quote asset 为 USDT、status 为 `TRADING`、spot permission
启用、formation 位于 listing 的 `[effective_from, effective_to)` 内，并且每项证据
都有 `known_at <= formation_time`、类型和 SHA-256。缺项、`UNKNOWN` 和未来证据
fail closed；archive-derived evidence、重复或缺失 membership 直接拒绝。

### 唯一 1h 时钟

对 feature bar `k`：

```text
decision_time = feature_bar_open_time + 1h
feature_known_at = decision_time
entry_time      = bar k+1 open = decision_time
exit_time(h)    = bar k+h+1 open = decision_time + h hours
label_known_at  = exit_time(h)
```

合成 `OpenPrice` 只能在自己的 open time 被知道。标签不能作为 Expert 输入。

### 单时点 representation

所有截面输入必须属于同一 formation；混合时点、重复 symbol-time、非有限值和低
breadth 均拒绝。只使用 derived eligible symbols：

- winsorization 使用当前截面的冻结分位数；
- rank 使用 `[-1, 1]` 上 centered average midrank，ties 取平均且总和为零；
- residualization 使用显式 intercept 规则，拒绝 rank-deficient design，并验证
  `Xᵀ residual ≈ 0`。

### 标签重叠与 purge/embargo

真实标签信息区间为半开区间：

```text
[entry_time, exit_time + 1 millisecond)
```

每个 horizon 至少 purge 和 embargo `h+1` 根 1h bar。共享
`1/24/120/480h` 研究时取 `max(h)+1 = 481` 根。验证使用实际 UTC 毫秒，拒绝
train/evaluation label overlap；同一 formation 的所有 symbols/horizons 必须属于
同一 fold。evaluation 前检查 purge，evaluation 后检查 embargo，包含不规则时间戳
和 1ms 边界。

### 固定分层集成、horizon identity 与 regime regulator

每个 family 内以及 family 之间的权重分别是外部冻结的非负 simplex：

```text
w >= 0,  Σw = 1
```

必须提供完整 `ExpertKey` map；missing、extra、duplicate 或 empty 都拒绝，缺 Expert
时不重新归一。只有测试用 `SYNTHETIC_READY` Expert 能进入 exp006/007 内核；真实
catalog 仍全部 blocked。

`ExpertOutput.key` 与每个 within-family weight key 都会在 consumer 边界重新执行
exact `ExpertKey` 验证，再进入 dict/set 比较。验证覆盖非空 family/name/version 与
exact built-in integer horizon；因此 `True == 1`、`IntEnum == int` 或 `1.0 == 1`
之类的 Python 键相等语义不能绕过 horizon 合同。

每次 `combine_hierarchical` 只能组合一个且恰好一个 horizon。`EnsembleScore`、
`RegimeAdjustedScore`、`ExpectedNetAlpha` 和它们的 `DiagnosticScore` 全程保留同一个
`horizon_hours`；所有 horizon 门禁只接受 exact built-in `int` 且值必须属于
`1/24/120/480`。`bool`、`IntEnum`、float 和字符串即使数值等价也拒绝。这样 1h
score 不会在 regime、成本惩罚或 IC 边界被误当成 24h score。

`MultiHorizonEnsemble` 只是把同一 symbol、同一 formation 的四个已完成单周期
`EnsembleScore` 按 horizon 排序并绑定 provenance。它没有 `value`、scalar 或
weights，不能进入 regime 或 expected-net-alpha consumer，也不是经济意义上的
cross-horizon composer。跨周期效用、权重、资本配置、horizon-specific regime 与
horizon-specific cost 都不在 V1 范围内；未来要做必须另立可证伪合同。
该 frozen dataclass 的直接构造与 builder 使用同一 fail-closed 不变量：必须恰有按
`1/24/120/480` 排序的四个合法 score、同 symbol/formation、`known_at` 等于各 score
最大值且不晚于 formation，并重算内容绑定的 provenance。不能通过绕开 builder
伪造一个看似合法的 bundle。

regime multiplier 是同一 formation 全截面共享的外部 PIT scalar，只在 ensemble
之后乘一次：

```text
adjusted_gross = ensemble_score × multiplier
0.7 <= multiplier <= 1.3
```

它保持方向和排序，最多放大 1.3 倍，不学习 router，也不乘到成本或其他惩罚上。
V1 把它视为保守 stub；未来若没有独立冻结的 PIT regime evidence，就必须拒绝，
不能从当前 score 反推。

### Expected Net Alpha 与 diagnostics

```text
expected_net_alpha
= regime_adjusted_expected_gross_alpha
- explicit_trading_cost_penalty
- uncertainty_penalty
- crowding_penalty
```

三个 penalty 都必须 finite 且非负，增加任一 penalty 不能提高净 Alpha。交易成本
显式区分 one-way rate、每 leg turnover 和 legs，禁止把单边费率静默当 round trip。
冻结压力场景是单边 `0.00150 / 0.00225 / 0.00300`。

rank IC 只接受 horizon-bound、带 PIT 时钟与 provenance 的 `DiagnosticScore`，并要求
同 formation、同 horizon、完整 score-label 键和足够 breadth。增量诊断只比较预先
固定的 full 与 ablated score：

```text
delta_ic = full_ic - ablated_ic
```

这些是纯诊断 API；V1 没有用真实数据算过 IC。

## 核心 API

- `require_pit_eligibility`：从分项证据推导 eligibility decision/reasons。
- `ExpertRegistry.build`、`modern_crypto_v1_readiness_catalog`：不可重复、顺序无关的
  Expert 合同与 readiness catalog。
- `winsorize_cross_section`、`rank_cross_section`、
  `residualize_cross_section`：单 formation、eligible-only representation。
- `build_next_open_labels`、`LabelInterval`、`PurgeEmbargoSpec`、
  `validate_purged_embargo_split`：next-open 标签与真实 UTC overlap 门禁。
- `combine_hierarchical`：单 horizon、固定两层 simplex ensemble。
- `compose_multi_horizon`：四周期 identity-preserving bundle；不做经济组合。
- `apply_regime_multiplier`、`compute_expected_net_alpha`：有 provenance 的共同
  regulator 与非负 penalty 合同。
- `diagnostic_score_from_expected_net_alpha`：不排名、不缩放的同 horizon 诊断投影。
- `rank_information_coefficient`、`incremental_information_diagnostic`：冻结的
  rank IC/ΔIC 合成诊断。

实现与证据：

- [内核实现](../src/quant_research/hierarchical_alpha.py)
- [合成测试](../tests/test_hierarchical_alpha.py)
- [exp006：首次窄测兼容性错误，INCONCLUSIVE](../experiments/exp_20260825_006/report.md)
- [exp007：修复、35/35 与 85/85、NEEDS_MORE_DATA](../experiments/exp_20260825_007/report.md)
- [exp_20260827_005：horizon identity 候选，等待 Phase 2](../experiments/exp_20260827_005/report.md)

## 模型晋级门

任何真实模型工作开始前，必须依次满足：

1. 冻结历史 PIT Binance Spot eligibility snapshot，并证明不是 archive availability
   的重命名；
2. 冻结每个 Expert 的输入、方向、观测延迟、provenance、缺失与异常策略；
3. 只在训练期完成 representation/fit，使用 next-open labels 和 481h shared purge/
   embargo；
4. 先建立线性基准，检查多 horizon rank IC、固定 ablation ΔIC、时间窗口稳定性和
   截面 breadth；
5. 只有线性结果在预注册门槛与成本后稳定，才一次性评估 LightGBM；神经网络需要
   额外样本量、稳定增量和复杂度论证；
6. 通过训练门禁后才允许一次性验证；最终测试、回测和模拟盘仍遵守仓库总门禁。

## 当前下一步

下一实验应只建立独立的 PIT eligibility 数据版本：为每个 symbol-time 保存 Binance
Spot status、permission、quote asset、listing effective interval、证据发布时间/
抓取时间、来源和 SHA-256，并审计 survivorship 与 delisting。完成前不计算真实
factor、score、IC/P&L，不拟合任何模型，也不回测。
