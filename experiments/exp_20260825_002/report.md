# exp_20260825_002 进度报告

## Referenced Skills and sources

| Item | Purpose | Location |
|---|---|---|
| quant-strategy-research | 固定 Subagent 流程与数据门禁 | `.codex/skills/quant-strategy-research/SKILL.md` |
| AGENTS.md | 当前仓库执行契约 | `../../AGENTS.md` |
| Binance Public Data | 归档路径、12 字段 Kline、CHECKSUM、2025 微秒时间戳 | `https://github.com/binance/binance-public-data/blob/master/README.md` |
| Binance Spot REST API | 当前 exchangeInfo 与 Kline 接口语义 | `https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md` |

Skill 指向的本地 `methodology.md` 和 `experiment-contract.md` 仍不存在，本实验不
声称读取了它们。

## 当前状态

`INCONCLUSIVE`。冻结代码的正式 inventory 在 S3 根前缀完整读取 4 页后显式失败：
3,695 个唯一 `CommonPrefixes` 中有 5 个 Unicode symbol，ASCII-only 校验器在
`币安人生TRY` 停止。失败前未进行逐 symbol 对象枚举、未请求 exchangeInfo，也
未下载 Kline ZIP/CHECKSUM 内容。

## 范围

本轮只做 1h Spot 月度归档 inventory，月份 2022-12 至 2024-12，历史 symbol
由 S3 archive 发现。不会下载 Kline ZIP/CHECKSUM 内容、解析行情、构造 panel、
运行残差动量或使用 ML。

## 预期裁决

本实验没有达到 inventory 完成门禁，不能裁决为 `NEEDS_MORE_DATA`，因此按预注册
规则保留为 `INCONCLUSIVE`。失败证据见 `artifacts/failure_evidence.json`；修复版
由 `exp_20260825_003` 重新预注册并使用新 raw/data 版本，不覆盖或沿用本次输出。
