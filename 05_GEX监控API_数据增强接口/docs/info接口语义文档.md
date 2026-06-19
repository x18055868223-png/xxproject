# `/v1/info` 接口数据与字段语义文档

本文件说明 `GET /v1/info` 返回的每个字段「是什么、什么单位、怎么解读」,让调用方不必去翻原站
也能准确理解数值含义。语义依据 [GEX Monitor](https://gexmonitor.com/btc/options/analytics?tab=gex)
BTC 分析页四个 tab 的真实标签与图例。

- **数据来源**:`gex`(GEX 看板)、`gamma`(Gamma Exposure)、`volatility`(波动率)、`flow`(资金流向)四个公开页面。
- **刷新**:服务端默认每 ~30 分钟自动抓取并缓存;调用方直接轮询本接口即可,无需频繁触发刷新。
- **鉴权**:请求头 `Authorization: Bearer <API_TOKEN>`。
- **完整示例**:见同目录 [`info.sample.json`](info.sample.json)(下文按分组拆解)。

> ⚠️ 免责:数据转引自 GEX Monitor 公开页面,**仅供研究,不构成投资建议**。深度逐 strike 面板在原站登录墙后,本接口不包含。

---

## 1. 顶层字段(envelope)

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `asset` | string | 标的,固定 `"BTC"` |
| `fetched_at` | string(UTC ISO8601) | 最近一次抓取时间;四个 tab 分别抓取,各自时间见 `sections.*.fetched_at` |
| `stale` | bool | 上一轮刷新是否发生过失败(`true` 表示返回的是上一次成功的旧缓存) |
| `availability` | string | `ready`=全部命中;`partial`=部分字段缺失或有错误;`missing`=从未成功抓取 |
| `missing_fields` | string[] | 未取到的字段路径(如 `"gamma_exposure.n2"`);全部命中时为 `[]` |
| `field_status` | object | **只列出有问题的字段**及原因;全部正常时为 `{}`。`reason`:`not_found_in_rendered_page`(页面中未解析到)/ `not_yet_fetched`(尚未抓取) |
| `rank` | object | 本地历史分位排名上下文;每次全量刷新追加一条轻量历史,默认用最近 30 天窗口计算(见第 6 节) |
| `sections` | object | 每个 tab 的抓取审计(见第 7 节) |

---

## 2. `gex_board` —— GEX 总览

```json
"gex_board": { "total_net_gex": -67000000.0, "dvol": 42.9, "market_state": "negative_gamma" }
```

| 字段 | 类型/单位 | 页面来源 | 含义与解读 |
| --- | --- | --- | --- |
| `total_net_gex` | number(USD 名义) | `TOTAL NET GEX` | 做市商对 BTC 期权的**净 gamma 敞口**。**负值=交易商净空 gamma**→需追涨杀跌对冲,**放大**波动;正值=净多 gamma→高抛低吸,**抑制**波动。示例 `-67000000` 即页面 `$-67M`(卡片概览值,见第 7 节) |
| `dvol` | number(%) | `DVOL` | Deribit BTC 波动率指数,30 天前瞻年化隐含波动率,可理解为「BTC 版 VIX」。`42.9` ≈ 42.9% |
| `market_state` | string | `MM Short/Long Gamma` | 做市商 gamma 体制的归一化标签:`negative_gamma`(净空 gamma)/ `positive_gamma`(净多)/ `neutral`。⚠️ **这不是**页面顶部 `Market State: Critical/Neutral` 那个「波动风险等级」标签——本字段表达的是 **gamma 正负体制**(见第 7 节) |

---

## 3. `gamma_exposure` —— 关键价格位

所有字段都是 **BTC 美元价格(USD)**。语义直接取自页面 Chart Legend。

```json
"gamma_exposure": { "n2":60000, "n1":65000, "flip_point":67372.727, "volatility_trigger":65000,
                    "spot_price":66684.5, "magnet_price":70000, "p1":80000, "p2":82000 }
```

| 字段 | 页面来源 | 含义与解读 |
| --- | --- | --- |
| `spot_price` | `SPOT PRICE` | 当前 BTC 现货价,作为其余价位的参照基准 |
| `flip_point` | `FLIP` / `Gamma Flip Point` | **Gamma 翻转位**(净 GEX≈0 的价格)。现价**在其上方→正 gamma 体制**(波动偏低、易均值回归);**在其下方→负 gamma 体制**(波动放大、易走趋势)。页面原文:"price above it means low volatility, below means high volatility" |
| `n1` / `n2` | `N1/N2 — Negative GEX Wall (Support)` | **负 GEX 墙=支撑位**(低于现价)。`n1` 通常是更近/更强的支撑,`n2` 更远(示例 n1=65000 比 n2=60000 离现价更近) |
| `p1` / `p2` | `P1/P2 — Positive GEX Wall (Resistance)` | **正 GEX 墙=阻力位**(高于现价)。`p1` 更近,`p2` 更远 |
| `magnet_price` | `MAGNET` / `A1/A2 — Highest Abs GEX (Magnet Level)` | **磁吸位**:绝对 GEX 最大的行权价。临近到期时价格易被「钉」向此处 |
| `volatility_trigger` | `VOL TRIGGER` | **波动触发位**:价格跌破后,交易商负 gamma 对冲加剧、波动率往往显著放大的阈值 |

---

## 4. `volatility` —— 波动率

```json
"volatility": { "iv_rv_ratio":1.26, "pcr":0.93,
  "term_structure":[ {"expiry":"4 JUN 26","atm_iv":47.2,"skew_25d":-12.8}, ... ] }
```

| 字段 | 类型/单位 | 页面来源 | 含义与解读 |
| --- | --- | --- | --- |
| `iv_rv_ratio` | number(倍数) | `IV/RV RATIO` | 隐含波动率 ÷ 已实现波动率。**>1=期权偏贵**(IV 高于已实现);`1.26`=IV 比 RV 高约 26%。页面诊断语:"IV above RV, options overpriced" |
| `pcr` | number(无量纲) | `PCR (VOLUME)` | **成交量 Put/Call 比**。>1=看跌成交占优(避险/看空情绪偏强);<1=看涨占优 |
| `term_structure` | object[] | `Term Details` 表 | 各到期的期限结构数组(每项见下) |
| └ `expiry` | string | — | 到期日,如 `"4 JUN 26"` |
| └ `atm_iv` | number(%) | `ATM IV` | 平值期权隐含波动率,年化 %。一般近月高、远月低=波动率倒挂(短期紧张) |
| └ `skew_25d` | number(%) | `25D SKEW` | 25-delta 偏斜(风险逆转)。**负值=看跌期权比看涨贵**(下行避险/偏空),绝对值越大偏斜越极端 |

---

## 5. `flow` —— 资金流向

```json
"flow": { "call_premium":27700000, "put_premium":51800000, "call_put_bias":"35% Call",
          "put_call_ratio":1.88, "abnormal_signal":"Put-led selling is setting the tone." }
```

| 字段 | 类型/单位 | 页面来源 | 含义与解读 |
| --- | --- | --- | --- |
| `call_premium` | number(USD 名义) | `CALL PREMIUM` | 窗口内**看涨**期权成交权利金。示例 `27700000` 即 `$27.7M` |
| `put_premium` | number(USD 名义) | `PUT PREMIUM` | 窗口内**看跌**期权成交权利金 |
| `put_call_ratio` | number(无量纲) | `P/C RATIO` | **权利金加权 Put/Call 比**(≈ `put_premium/call_premium`)。>1=看跌权利金占优。示例 `1.88` ≈ 51.8M/27.7M |
| `call_put_bias` | **string** | `CALL / PUT TILT` | 看涨权利金占比文本,如 `"35% Call"` 表示看涨占 35%(看跌 65%)→**看跌主导**。⚠️ 是字符串,不是数值 |
| `abnormal_signal` | string | `FLOW READ` | 资金流读数摘要(定性),如 `"Put-led selling is setting the tone."` |

---

## 6. `rank` —— 本地历史分位排名

`rank` 不改变原始指标,只给当前值增加「在本地历史里处于什么位置」的上下文。服务每次 `section=all` 的成功刷新会向 `HISTORY_FILE` 追加一行 JSONL 快照;单独刷新某个 tab 不会写入历史,避免半截数据污染序列。

```json
"rank": {
  "window": {
    "mode": "rolling_30d_or_available",
    "lookback_days": 30,
    "sample_count": 96,
    "history_retained_count": 96,
    "start_at": "2026-06-01T14:52:45+00:00",
    "end_at": "2026-06-03T14:52:45+00:00",
    "window_days": 2.0
  },
  "metrics": {
    "gex_board.total_net_gex": {
      "value": -67000000.0,
      "percentile": 0.22,
      "rank_pct": 22.0,
      "sample_count": 96,
      "quality": "warming_up",
      "abs_percentile": 0.81,
      "abs_rank_pct": 81.0
    }
  }
}
```

| 字段 | 含义 |
| --- | --- |
| `rank.window.sample_count` | 本次 rank 计算实际使用的窗口样本数。历史不足 30 天时使用已有全部样本;超过 30 天时只取最近 30 天。 |
| `rank.window.history_retained_count` | 本地已保留的全量历史样本数。历史不会因 rank 窗口滚动而删除。 |
| `rank.metrics.*.value` | 当前指标值,与原字段同单位。 |
| `rank.metrics.*.percentile` / `rank_pct` | 当前值在窗口样本中的分位。`0.86` / `86.0` 表示高于或等于窗口内约 86% 的样本。 |
| `rank.metrics.gex_board.total_net_gex.abs_percentile` | `netGEX` 绝对值强度分位。负值本身的 `percentile` 越低代表越偏负,`abs_percentile` 越高代表净 GEX 规模越极端。 |
| `quality` | `missing`=当前值缺失;`single_sample`=仅一个样本;`warming_up`=样本可用但不足完整 lookback 窗口;`ok`=窗口覆盖完整。 |

当前纳入 rank 的指标:`gex_board.total_net_gex`、`gex_board.dvol`、`volatility.iv_rv_ratio`、`volatility.pcr`、`flow.call_share_pct`、`flow.put_call_ratio`。

---

## 7. `sections` —— 每个 tab 的抓取审计

```json
"sections": { "gex_board": { "fetched_at":"...", "last_success_at":"...", "last_error":null,
  "source_url":"https://gexmonitor.com/btc/options/analytics?tab=gex",
  "content_hash":"743cc8...", "missing_fields":[] }, ... }
```

| 字段 | 含义 |
| --- | --- |
| `fetched_at` | 该 tab 最近一次抓取时间 |
| `last_success_at` | 最近一次成功时间(用于判断单个 tab 是否变旧) |
| `last_error` | 最近一次错误信息,正常为 `null` |
| `source_url` | 抓取来源页面 |
| `content_hash` | 该页可见文本的 SHA-256;两次相同表示内容未变,可用于去重/变更检测 |
| `missing_fields` | 该 tab 内未取到的字段 |

---

## 8. 数值格式与重要语义提示

**格式约定**
- 后缀自动展开:`K`=×10³、`M`=×10⁶、`B`=×10⁹、`T`=×10¹²;`$`、千分位逗号、正负号均已归一化为纯数值。
- 单位:价格类(gamma 各位、premium)= **USD**;波动率/偏斜(dvol、atm_iv、skew_25d)= **%**;比率(iv_rv_ratio、pcr、put_call_ratio)= **无量纲**。
- `total_net_gex`、`dvol` 取自页面**卡片概览值**,可能是四舍五入的(如 `$-67M`),非精确到元/小数。

**容易混淆的点**
1. `gex_board.market_state` 表示**做市商 gamma 正负体制**(由 `MM Short/Long Gamma` 推导),**不等于**页面 `Market State: Critical/Neutral` 那个波动风险等级标签。
2. `flow.call_put_bias` 是**字符串**(`"35% Call"`),其余指标多为数值。
3. 缺字段不报错:取不到的字段返回 `null`(数组返回 `[]`)并记入 `missing_fields` / `field_status`。

**解读小抄(快速判断市场体制)**
- 现价 vs `flip_point`:在上方→正 gamma(偏稳、均值回归);在下方→负 gamma(偏野、易趋势)。
- `total_net_gex` 为负 + `market_state=negative_gamma`:预期日内波动**放大**。
- 支撑看 `n1/n2`,阻力看 `p1/p2`,磁吸看 `magnet_price`,破位放量看 `volatility_trigger`。
- `pcr` / `put_call_ratio` > 1 → 偏空/避险;`iv_rv_ratio` > 1 → 期权偏贵;`skew_25d` 越负 → 下行避险越浓。
