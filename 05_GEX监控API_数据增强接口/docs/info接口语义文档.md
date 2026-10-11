# `/v1/info` 接口数据与字段语义文档

本文件说明 `GET /v1/info` 每个字段「是什么、什么单位、怎么解读」。口径对应 2026-10 上游改版后的公开 JSON：

- **数据来源**(每轮刷新两次主请求 + 一次可选交叉校验):
  - `gex`:`/api/gex-latest?asset=BTC&exchange=all&lite=true` → `gex_board`、`gamma_exposure`、`gamma_profiles`
  - `bundle`:`/api/dashboard-bundle?asset=BTC` → `volatility`、`flow`、`expiry`(以及现价/DVOL 备用)
  - `options_chain`:`/api/options-chain?asset=BTC`,仅用于 `source_metadata.cross_check` 墙位交叉校验,失败不影响 `stale`
  - 已停用:`/api/volatility-metrics`(改版后为 2MB 时间序列)、`/api/price`(由 bundle 的 `price.price` 代替)
- **刷新**:服务端默认每 ~30 分钟抓取并缓存;调用方直接轮询本接口即可。
- **鉴权**:`Authorization: Bearer <API_TOKEN>`(`PUBLIC_READ=true` 时 `/v1/info` 免 token)。
- **完整示例**:[`info.sample.json`](info.sample.json)(由测试夹具生成,`gex_time_semantics.fields` 与 `sections` 已截断)。

> ⚠️ 数据转引自 GEX Monitor 公开接口,**仅供研究,不构成投资建议**。

---

## 1. 顶层字段

| 字段 | 含义 |
| --- | --- |
| `asset` | 固定 `"BTC"` |
| `fetched_at` / `latest_attempt_at` | 最近一次抓取 / 尝试时间(UTC ISO8601) |
| `data_age_ms` | 距 `fetched_at` 的毫秒数 |
| `stale` | 上一轮刷新中 `gex` 或 `bundle` 请求失败(返回的可能是旧缓存) |
| `availability` | `missing`=从未成功;`partial`=有必需字段缺失,或 `gex`/`bundle` 出错;`ready`=全部必需字段齐全 |
| `missing_fields` | 缺失的**必需**字段路径(如 `"volatility.pcr"`) |
| `field_status` | 只列非 `ok` 字段:`missing`(必需字段缺失)/ `optional_missing`(可选字段缺失,不影响 availability)/ `not_yet_fetched` |
| `gex_time_semantics` | 每字段时间语义(第 8 节) |
| `source_mode` / `source_urls` / `source_metadata` | 数据源模式、URL、错误(`errors` 键为 `gex`/`bundle`/`options_chain`)与交叉校验 |
| `rank` | 本地历史分位(第 9 节) |
| `sections` | 每段抓取审计(第 10 节) |

**必需 vs 可选**:除下列项外,全部字段为必需。可选:`gamma_profiles.zero_dte`(每日到期换月前后可能缺失)、`volatility.term_structure[].skew_25d`、`flow.whale_*.lead_expiry` / `active_strike`(零成交窗口为 `null`)。数组为空 `[]` 视为缺失;零成交的 whale 窗口(全 0)**视为有值**。

---

## 2. `gex_board`

| 字段 | 单位 | 来源 | 含义 |
| --- | --- | --- | --- |
| `total_net_gex` | USD | `gex.total_gex` | 交易商净 gamma 敞口。负=净空 gamma(放大波动),正=净多 gamma(抑制波动) |
| `dvol` | % | `gex.dvol`,备用 `bundle.dvol.value` → `bundle.volMetrics.metrics.dvolIndex` | Deribit DVOL(30 天隐含波动率指数)。上游已不给原生时间,`time_basis="dvol_time_unknown"` |
| `market_state` | 枚举 | 派生 | `total_net_gex` 符号 → `positive_gamma`/`negative_gamma`/`neutral`;无 GEX 时用现价 vs flip |

## 3. `gamma_exposure`(来自 `profiles.total`)

`n2`/`n1`(负 GEX 墙,支撑)、`p1`/`p2`(正 GEX 墙,阻力)、`flip_point`(gamma 翻转位,`gex.flip_point`,备用 `meta.flip`)、`volatility_trigger`、`spot_price`(`gex.asset_price`,备用 `bundle.price.price`)、`magnet_price`(`meta.magnet_a1`,备用 `magnet_a2`)——均为 USD 价格。

| 字段 | 含义 |
| --- | --- |
| `hedging_flows.total_gamma_usd` | 现价每变动 1% 交易商 delta 变化(USD) |
| `hedging_flows.scenarios[]` | `{price_change_pct, hedging_usd}`:价格变动 X% 时模型估算的交易商对冲名义金额(USD,正负号沿用上游;上游模型假设交易商持仓方向,非观测值) |

## 4. `gamma_profiles`(新增)

`zero_dte`(上游 `profiles["0dte"]`)与 `weekly`,结构相同:

```json
{ "p1": 84000.0, "p2": 84500.0, "n1": 82500.0, "n2": 81500.0,
  "walls": { "p1": {"strike": 84000.0, "gex_usd": 12413166.5, "distance_pct": 1.21}, "...": {} },
  "volatility_trigger": 84000.0, "magnet_price": 84000.0 }
```

`walls.*.gex_usd`=该行权价 GEX(USD),`distance_pct`=距现价百分比。**不提供分桶 flip**:上游分桶 `meta.flip` 抄自 total,不可信;翻转位请用 `gamma_exposure.flip_point`。

## 5. `volatility`

| 字段 | 单位 | 来源 | 含义 |
| --- | --- | --- | --- |
| `pcr` | 比值 | `bundle.pcr.data[latest].pcr_volume` | 成交量 Put/Call 比(期权链快照) |
| `pcr_oi` | 比值 | `pcr.data[latest].pcr_oi` | 持仓量 Put/Call 比 |
| `atm_iv` | % | `volMetrics.metrics.atmIV` | 近月 ATM 隐含波动率 |
| `iv_percentile` | 0–100 | `volMetrics.metrics.ivPercentile` | IV 历史分位 |
| `term_structure[]` | — | `ivTerm.data`(小时级) | `{expiry, days_to_expiry, atm_iv, skew_25d}`;`skew_25d` 仅当 `rrSkewSummary` 有 `methodology="exact_expiry"` 且到期日完全相同的行时填入该行 `rr`,否则 `null` |
| `rr_skew[]` | % | `rrSkewSummary.rows` | `{tenor, days, expiry, call_25d_iv, put_25d_iv, rr, chg_1d, chg_1w, chg_1m, methodology}`。`rr = call25Δ IV − put25Δ IV`,负=看跌更贵;插值期限 `expiry=null`、`methodology="constant_maturity_variance_interpolation"` |

## 6. `flow`

| 字段 | 含义 |
| --- | --- |
| `put_call_ratio` | **`volatility.pcr` 的别名**(成交量 PCR),保留旧字段名 |
| `call_volume` / `put_volume` | 看涨 / 看跌成交量(合约数),PCR 的分子分母 |
| `call_oi` / `put_oi` | 看涨 / 看跌持仓(合约数),**仅为 `pcr_oi` 的计算输入**,不是全市场绝对 OI |
| `whale_1h` / `whale_24h` | 大单流窗口汇总:`net_flow_usd`、`buy_volume_usd`、`sell_volume_usd`、`execution_net_flow_usd`、`total_volume_usd`(USD),`trade_count`、`call_count`、`put_count`、`buy_call_count`、`sell_call_count`、`buy_put_count`、`sell_put_count`(笔数),`lead_expiry`、`active_strike`(零成交时为 `null`) |

## 7. `expiry`(新增)

| 字段 | 含义 |
| --- | --- |
| `max_pain_price` / `max_pain_target_expiry` | `maxPain.latest`:**月度目标到期**的最大痛点价格与到期日(如 `30OCT26`) |
| `next_expiry` | `expirySummary.data`:最近一个到期(通常次日 08:00 UTC)的 `{expiry, expires_at, hours_to_expiry, put_call_ratio_oi, max_pain_price, spot_to_max_pain_pct, call_oi, put_oi, notional_usd, expiry_oi_share_pct}`;注意其 `max_pain_price` 是**该到期**的最大痛点,与上面的月度值不同 |

---

## 8. `gex_time_semantics`(`gex_time_semantics@1.0.0`)

`fields` 的键为 `"section.field"`,值为 `{source_ref, observed_at_ms, generated_at_ms, fetched_at_ms, time_basis, time_errors}`。按字段来源取**分段时间**:

| 来源 | `generated_at_ms` | `time_basis` |
| --- | --- | --- |
| gex 字段 | `gex.timestamp`;墙位/vol trigger/magnet/分桶用 profile `meta.updateTime` | `upstream_result_time` / `profile_group_update_time` |
| `gex_board.dvol` | 上游无原生时间 → `null` | `dvol_time_unknown`(上游恢复 `dvol_timestamp` 时为 `dvol_native_time`) |
| bundle 段 | `meta.sections.<段名>.data_timestamp`;为空时用该段自身 `timestamp`(`volMetrics`、`ivTerm` 为小时级) | `bundle_section_data_timestamp` / `bundle_section_timestamp` |
| `expiry.max_pain_*` | 同上;自身时间为 `maxPain.latest.timestamp`(epoch **秒**) | 同上 |
| `flow.whale_*` | `whaleFlowSummary.timestamp`;`observed_at_ms`=`latest_trade_timestamp` | `whale_summary_time` |

**永不**把 bundle 顶层 `data_timestamp`(= 最旧分段的时间)当作单个字段的时间。时间解析失败写入 `time_errors`(如 `generated_at_parse_failed:<source_ref>`)。

## 9. `rank` —— 本地历史分位

每次 `section=all` 成功刷新向 `HISTORY_FILE` 追加一行 `{fetched_at, metrics, metric_sources}`;默认取最近 30 天窗口。纳入指标:`gex_board.total_net_gex`、`gex_board.dvol`、`volatility.pcr`、`volatility.pcr_oi`、`volatility.atm_iv`、`flow.put_call_ratio`、`flow.whale_24h.net_flow_usd`(从 dict 中扁平取出的标量)。

- **同源才比较**:每个指标只与 `metric_sources` 相同(同一上游字段)的历史样本比较。改版前无 `metric_sources` 的旧行按旧来源推定:`total_net_gex`/`dvol` 仍是 `gex-latest` 同字段,可延续;旧 `volatility-metrics` 的 pcr 与新期权链快照 pcr 不混算。
- 旧历史中已删除的指标会被忽略。
- `percentile`/`rank_pct`:当前值 ≥ 窗口样本的比例;`total_net_gex` 另有 `abs_percentile`。
- `quality`:`missing` / `single_sample` / `warming_up`(窗口 <15 天)/ `ok`。`sample_count` 为同源样本数。

## 10. `sections`

键为 `gex_board`、`gamma_exposure`、`gamma_profiles`、`volatility`、`flow`、`expiry`。每段含 `fetched_at`、`latest_attempt_at`、`last_success_at`、`last_error`、`source_url`、`content_hash`、`missing_fields`、`field_status`、`gex_time_semantics`。某个上游失败时(`gex` → 前三段,`bundle` → 后三段),**仅该来源的段**保留上一次成功值,并在 `last_error` 写入错误。`POST /v1/refresh?section=` 接受上述段名或 `all`。

## 11. 已移除字段

`volatility.iv_rv_ratio`、`flow.call_premium`、`flow.put_premium`、`flow.call_put_bias`、`flow.abnormal_signal` 与 rank 指标 `flow.call_share_pct` 已彻底移除(上游不再提供可靠来源)。从旧 `cache.json` 恢复时也会按当前字段注册表过滤,不会再出现。
