# GGR 因子卡：全局 Gamma 区制因子（门 + 空间钉）v1.0

> 第一性因子卡。目的：让你彻底明白"为什么引入全局 Gamma、它和已有 Anchor 是什么关系、它到底是不是方向、什么时候必须否决交易"。
> 配套：本因子是 EDB 的**风险/置信门 + 有条件的空间方向票**，不是普通趋势票，不单独下单。

---

## 1. 因子名称

**GGR = Global Gamma Regime**
中文名：**全局 Gamma 区制因子**
版本：v1.0（设计稿，阈值待真实数据校准）

---

## 2. 第一性目标（为什么存在）

它回答两个当前模型**完全缺失**的问题：

> **(A) 这个价格/窗口下，做市商对冲是在"钉住/抑制波动"还是在"助涨助跌/放大趋势"？**
> **(B) 到期前价格结构上最可能被吸向哪个行权价（钉价磁吸）？**

对一个**卖单边 credit spread** 的策略，这是生死攸关的：

- 正 Gamma 区制（做市商高抛低吸）→ 价格被钉/均值回归 → **卖权利金友好**；
- 负 Gamma 区制（做市商助涨助跌）→ 趋势被放大 → **你的单边价差容易被一波带穿**（卖方爆亏的典型场景）。

当前模型只用了 GEX 的 **flip 点**（Anchor 的空间锚），却**丢掉了 gamma 的符号/区制**——等于知道"中轴在哪"，却不知道"现在市场会把价格往中轴推、还是往外甩"。GGR 就是把这块已经半用的原生信息**补全**。

它**不是**一个普通方向票，**不**预测涨跌；它主要是**门控（安全/置信）**，外加在钉住区制下的**空间拉力**。

---

## 3. 它减少哪个不确定性 / 改变哪个决策

- **减少的不确定性**：(A) 这笔单边卖权利金现在到底**安不安全**（区制风险）；(B) 到期前价格结构性**落点**（钉价）。
- **改变的决策**：
  - **门**：强负 Gamma 区制 → **砍 EDB 置信 或 直接 No-Trade**（不在放大区制里卖单边）。
  - **置信**：正 Gamma/钉住 → 方向 lean 更可信 → 抬升置信。
  - **空间票**：仅在钉住区制下，若主导 gamma 行权价明显偏离现价，给一张**朝钉价**的有界方向票（价格在短到期倾向被吸向高 gamma 行权价）。
- 准入考核线同样适用：若它改不动 go/no-go、置信或方向，则降级为观察项（soul.md §2）。

---

## 4. 核心定义

**区制（regime）**——以 GEX 零 gamma（flip）为界，标准约定：

```text
price > flip   → 正 Gamma 区制（POSITIVE_GAMMA / 钉住、抑制波动）
price < flip   → 负 Gamma 区制（NEGATIVE_GAMMA / 放大、助涨助跌）
price ≈ flip   → 过渡区（拐点附近，波动区制易切换）
```

**区制强度**——离 flip 的距离 + 对冲曲线斜率（`spring`）：离 flip 越远、斜率越陡，区制越"硬"。

**空间钉（pin）**——窗口内 `gamma × open_interest` 最集中的行权价（最大 gamma 墙 / 近似 max-pain）。短到期下价格倾向被吸向它，**且只有在正 Gamma 区制下这种吸附才可信**（负 Gamma 下钉不住、反而会跳穿）。

---

## 5. 数据源与口径（两路互补）

**路 A — gexmonitor（已接入，聚合层）**：`https://gexmonitor.com/api/gex-latest`
项目 `gex_adapter.py` 已解析：`flip_point`、`hedging_curve`(price→hedging_btc 对冲曲线)、`spring`(=flip 处对冲曲线 |斜率|)、`asset_price`、`source_ts_ms`。

- 区制侧 = `sign(asset_price − flip_point)`（**复用 Anchor 已有数据**，只是补上符号维度）。
- 区制强度 ← `spring` 与 `|price − flip|`。
- 若 `raw_payload` 中存在 `net_gex / call_wall / put_wall / max_pain / gamma_by_strike`，一并提取用于钉价与净 gamma 符号（当前 adapter 未取，落地时从 `raw_payload` 补）。
- 新鲜度/接受度：复用 Anchor 现有的 `freshness / acceptance_event / pending` 机制，过期或 pending 时降权。

**路 B — Deribit（到期局部，精确层）**：`public/ticker` 取近月各档 `greeks.gamma` + `open_interest`（**与 SRD 共用同一次取数**）。

- 对 24h/48h（≤72h）到期，按行权价做 `Σ(gamma × open_interest)` 得**到期局部 GEX 剖面** → 该到期的**最大 gamma 行权价（pin）**与**局部净 gamma 符号**。
- 比聚合层更贴合"你要卖的那张到期"。

**符号约定声明（必须显式、必须当成概率而非确定）**：零售 GEX 无法知道做市商真实库存，只能用代理约定（常用：call 计正、put 计负，或"price>flip 即做市商净多 gamma"）。因此 GGR 的区制是**概率判断**，只作门/调制，不作硬方向。

---

## 6. 输出结构（进入 EDB 的门 + 空间票）

```json
{
  "factor_name": "GGR",
  "factor_version": "v1.0",
  "regime": "POSITIVE_GAMMA_PINNING",
  "regime_strength": 0.62,
  "flip_point": 73250.0,
  "asset_price": 73680.0,
  "distance_to_flip_pct": 0.59,
  "net_gex_sign": "+",
  "pin": {
    "expiry": "BTC-...",
    "pin_strike": 74000.0,
    "distance_to_pin_pct": 0.43,
    "pin_pull_direction": "UP",
    "pin_trust": 0.55
  },
  "gate_action": "SUPPORT",
  "confidence_multiplier": 1.10,
  "spatial_vote": 0.18,
  "reason_codes": ["PRICE_ABOVE_FLIP_POSITIVE_GAMMA"],
  "interpretation_cn": "价格在 flip 之上、正 Gamma 钉住区制，卖单边相对安全；窗口主导 gamma 墙在 74000，温和向上吸附。"
}
```

- `gate_action ∈ {SUPPORT, NEUTRAL, CUT_CONFIDENCE, VETO}`。
- `confidence_multiplier`：乘到 EDB 置信上（负 Gamma <1 甚至触发 VETO；正 Gamma 钉住 >1）。
- `spatial_vote ∈ [−1,+1]`：朝 pin 的有界方向票，**乘以 `pin_trust`**；`pin_trust` 在负 Gamma 区制趋零（钉不住就别信钉价）。

---

## 7. 在 EDB 中的使用方式（关键：门 vs 票，别混）

1. **首先做门**：强负 Gamma 区制 → `CUT_CONFIDENCE` 或 `VETO`（EDB 直接走中性/No-Trade）。这是 GGR 最重要的作用——**防止在放大区制里卖单边**，弥补当前模型完全没有的"环境安全判断"。
2. **再做置信调制**：正 Gamma/钉住 → 抬升 EDB 置信（方向 lean 在钉住环境更可信）。
3. **最后才给空间票**：仅在钉住区制、pin 明显偏离现价时，给一张**小权重**朝 pin 的方向票，参与 EDB 合成。
4. 与 Anchor 的分工：Anchor 判"价格是否被中性锚解释（贴合度）"，GGR 判"这个锚环境是钉住还是放大、钉向哪"。两者共用 flip，但回答不同的不确定性，不重复计权。

---

## 8. 失败场景（什么时候它会骗你）

1. **符号约定是代理**（不知道真实做市商库存）→ 当概率门用，绝不当硬方向；约定要显式记录。
2. **flip 不稳/跳变** → 复用 Anchor 的 acceptance/observation 抗抖与 freshness，过期降权。
3. **过渡区（price≈flip）** → 区制随时翻 → 降低 `regime_strength` 与 `confidence_multiplier`，别在拐点附近重仓信任。
4. **负 Gamma 下误信钉价** → `pin_trust` 在负 Gamma 趋零，已设计规避。
5. **Deribit 局部 gamma 行权价 OI 稀薄** → pin 不可靠 → 降 `pin_trust`。
6. **聚合 vs 局部冲突**（gexmonitor 全局 与 Deribit 到期局部 区制不一致）→ 以"更保守"为准（倾向降置信/否决），并记录冲突。

---

## 9. 参数表（**全部待真实数据校准，非理论最优**）

| 参数 | 初值 | 含义 |
| --- | --- | --- |
| `ggr_transition_band_pct` | 待定（如 ~0.2–0.4%） | flip 附近视为过渡区的距离 |
| `ggr_negative_cut_strength` | 待定 | 触发 CUT_CONFIDENCE 的负 Gamma 强度 |
| `ggr_negative_veto_strength` | 待定 | 触发 VETO 的强负 Gamma 强度 |
| `ggr_positive_conf_boost_max` | 待定（如 1.1–1.2） | 钉住区制最大置信加成 |
| `ggr_pin_min_oi_share` | 待定 | 认定 pin 的最小 gamma×OI 集中度 |
| `ggr_pin_trust_negative_gamma` | ~0 | 负 Gamma 下钉价信任度 |
| `ggr_spatial_vote_cap` | 待定（小） | 空间票上限（保持小权重） |

校准前不得当成已验证规则；调整必须版本化。

---

## 10. 记录与验证

- 每轮记录：`regime / regime_strength / flip / asset_price / distance_to_flip / net_gex_sign / pin_strike / distance_to_pin / gate_action / confidence_multiplier / spatial_vote`，以及聚合-局部是否冲突。
- 红灯断言（落地时）：
  - 价格在 flip 之上/之下 → 区制符号正确；
  - 单一行权价 OI/gamma 集中 → pin 命中该价；
  - 强负 Gamma → `gate_action` 为 CUT/VETO 且 EDB 置信被压；
  - 负 Gamma 下 `pin_trust≈0`、空间票≈0；
  - 不返回任何选腿/报价/下单字段（守 v0.4 执行边界）。

---

## 11. 一句话总结

**GGR 只做一件事：用 GEX 的符号/区制（不只是 flip 点）告诉 EDB"现在卖单边权利金是钉住区制的安全、还是放大区制的危险，以及到期前价格被吸向哪个行权价"——它首先是安全门，其次是置信调制，最后才是一张只在钉住区制下可信的小空间票。**
