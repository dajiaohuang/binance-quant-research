# exp_20260825_002 — Binance Spot archive inventory

## 观察

当前研究只有 BTC/ETH Feather 和当前时点的 StaticPairList，不能验证截面 Alpha。
探索性只读检查发现，Binance Data Vision 的 Spot 月度 Kline 归档可以通过 S3
`CommonPrefixes` 分页枚举历史 symbol；本次检查看到 3,695 个 Spot symbol 前缀，
其中 723 个字符串以 `USDT` 结尾。当前 active Spot USDT 列表明显更小，因此用
今天的市场列表作为 2023–2024 下载种子会产生幸存者偏差。

## 本轮可证伪目标

不下载任何 Kline ZIP，只实现并运行确定性的 `archive inventory → manifest`：

1. 从归档根前缀完整分页发现 symbol，不以当前 `exchangeInfo` 作为种子；
2. 对所有 `*USDT` 候选枚举 `1h` 月度对象，限定 2022-12 至 2024-12；
3. 保存每页原始 XML、请求参数、抓取时间和 SHA-256；
4. 保存 ZIP/CHECKSUM 对象 key、Size、ETag、Last-Modified，汇总 ZIP 数、官方
   CHECKSUM 对象数、缺 CHECKSUM 数与预计 ZIP 总字节；
5. 另存一份当前 `exchangeInfo` 原始快照，仅用于观察时点对照。

## 语义边界

允许的状态只有：

```text
ARCHIVE_OBSERVED
ARCHIVE_MONTH_ZIP_PRESENT
ARCHIVE_MONTH_CHECKSUM_PRESENT
PUBLISHER_CHECKSUM_UNAVAILABLE
SYMBOL_SUFFIX_USDT_CANDIDATE
```

归档对象存在不等于历史可交易；`LastModified` 不是上市时间；`ETag` 不作为内容
SHA-256；当前 `exchangeInfo` 不回填过去。首月前、末月后和内部缺月分别统计，
不解释为上线、下线、停牌或数据缺失，也不因缺月/缺 CHECKSUM 删除 symbol。

## 冻结范围

- Binance Data Vision Spot monthly klines archive；interval `1h`。
- symbol 发现：全部 archive CommonPrefixes；候选筛选仅按 symbol 字符串后缀
  `USDT`，诚实标记为候选而非历史 quoteAsset 事实。
- 对象月份：2022-12 至 2024-12，共 25 个完整月。
- 不下载 ZIP/CHECKSUM 内容，不解析行情，不生成 panel，不运行策略或 ML。
- 仅访问公开数据，不访问账户、订单、密钥或私有接口。

## 完成门禁

1. 第一页无 continuation token；每页 token 严格衔接；最后一页
   `IsTruncated=false`。
2. 原始 XML 可解析且逐页保存 SHA-256；token、CommonPrefix、object key 无重复。
3. 对象 key 必须严格匹配：
   `data/spot/monthly/klines/<SYMBOL>/1h/<SYMBOL>-1h-YYYY-MM.zip`
   或同名 `.CHECKSUM`。
4. inventory 排序后可确定性重建；对象元数据和原始页均有可核查哈希。
5. 输出 ZIP/CHECKSUM 数、缺 CHECKSUM、ZIP 总字节、候选 symbol 数、首末对象月、
   前导/内部/尾部缺月；不按完整性筛掉历史 symbol。
6. 所有新增单元测试离线通过；live fetch 失败必须保留错误并标为 `INCONCLUSIVE`。

门禁全部通过后，本实验仍只能裁决为 `NEEDS_MORE_DATA`：尚缺 ZIP 校验与解析、
动态 eligibility、真实历史交易状态、历史交易规则、保守退市退出和 timestamp × pair
面板。只有后续独立数据实验通过，才允许预注册周频残差动量。

## 未来 H1（本实验禁止执行）

唯一保留方向是周频 28 日、跳过最近 1 日、以 90 日 BTC beta 计算的残差动量。
由于 2022-12 只提供约 31 天 warm-up，若后续坚持训练期一开始就产生信号，必须
把行情范围前移；否则首个调仓时点至少推迟到每个资产拥有完整 90 日历史之后。
