# v0.5 EDB 到期窗口方向合成层 — 落地设计稿

> 本稿是 v0.4.1 之后的方向层重构定稿设计。仍是**前置信号层**：不选腿、不读盘口做流动性筛选、不算组合风险、不生成可执行下单。
> 配套因子卡：`add/skew_rr_directional_factor_v1.0.md`(SRD)、`add/global_gamma_regime_factor_v1.0.md`(GGR)、`add/M-DIE...md`、`add/宏观要素.md`(MPF)。
> 阅读前先读 `soul.md`。本稿所有阈值均标注"**待真实数据校准、非理论最优**"。

---

## 1. 本版本目标

把 v0.4.1 里"TMV 单人逐 tick 选边 + CVD/Macro 惰性摆设 + 置信塌缩成 0/62"的方向层，重构成：

> **时序与方向解耦**：DIE+Anchor 只决定"窗口何时开"；一个稳定的**到期窗口(24–72h)方向合成层 EDB** 用多源证据合成"窗口期价格更倾向哪边"，置信反映证据收敛程度（信息熵减），方向不明就老实中性。

解决三个已实证的根因：
1. 置信 0/62 塌缩（CVD 永远"弱" + 一堆撞 62 的硬顶）；
2. CVD 双重计数（micro_flow 既改 TMV 方向又被当 CVD 重读）+ 决策惰性；
3. 选边高频敏感、且与到期周期不匹配。

---

## 2. 策略边界（不变）

- 仍只读、`read_only_demo=True`、`placement_allowed=False`。
- `module_results` 仍只有 External Gate / Anchor / TMV-F，**MODULE_SEQUENCE 不变**。
- EDB 与新因子（SRD/GGR）只进 `FactorSnapshot` 与状态栏，不新增主模块。
- 公共输出到 signal / 24h·48h 期号 / strategy_type 为止；选腿、报价、风控、下单由外部执行程序处理。
- 不裸卖、不默认 Iron Condor、单边带保护 Credit Spread（保护腿由外部执行层配）。

---

## 3. 本轮新增与修正

**新增因子（已判定应纳入）：**
- **SRD**：Deribit 25Δ 风险逆转方向票（期权需求面，按目标到期取）。
- **GGR**：全局 Gamma 区制门 + 空间钉（gexmonitor 聚合 + Deribit 到期局部 gamma×OI）。

**修正：**
- TMV-F **不再用 micro_flow tilt 改方向**（消除与 CVD 的双重计数）；TMV 的方向票 = 纯 1h-kline core。
- CVD strength **改滚动分布标定**（不再用永不可达的固定 0.35 阈值）；CVD 必须**联合价格**走四象限。
- Macro 转**有实权重的方向票**（不再是被 62 顶夹掉的 ±2）。
- 置信度改 `|EDB|×一致度`，**拆掉所有撞 62 的硬顶**。
- `strategy_recommendation` 读 EDB 结论（闭环），不再裸用 TMV 方向。
- `bias_thesis` 的论证逻辑迁移/重构进 `edb`（保留其 CVD×价格四象限的雏形）。

---

## 4. 主流程

每个 tick：

```text
External Gate → Anchor → TMV-F            （module_results，不变）
M-DIE → NeutralRepair(DIE+Anchor 状态机)  （时序门，不变）

EDB（每 tick 持续维护，保持"方向预热"，避免开窗瞬间方向冷启动）：
  收集 6 类证据 → 各出 (vote, weight) 或 (gate)
  → 合成 EDB_score、一致度 agreement、置信 conf
  → GGR 门 乘/否决 conf
  → 分类 lean ∈ {Bullish / Bearish / Neutral} + support

信号发射：NR.is_active 为真（窗口开） 且 EDB lean 明确 → 输出该侧 credit-spread 信号；
          NR 未激活 或 EDB 中性 → No-Trade（合法结论）
```

关键：**EDB 持续运行**（不只在 NR 确认时算），所以窗口一开方向是"热"且稳的；NR 只是放行闸。

---

## 5. EDB 证据合成模型（核心）

### 5.1 证据 → (vote, weight)

每个**方向证据**输出 `vote∈[-1,+1]`（正=偏多 lean=倾向卖 Put Spread；负=偏空=倾向卖 Call Spread）与 `weight≥0`。GGR 是**门**（外加小空间票），单列。

| 证据 | vote 形态（设计） | weight 取决于 | 角色 |
| --- | --- | --- | --- |
| **TMV**（主干） | `clamp(tmv_blend / tmvf_core_strong_abs, -1, 1)` | 高基重 × 可靠度；`window_conflict` **降权**(非归零)、数据陈旧降权 | 主干 |
| **CVD×价格** | 四象限（见 5.2） | 滚动标定后的 CVD 强度 × 覆盖度 | 独立流量证据 |
| **Macro** | `clamp(-macro_score / macro_ref, -1, 1)`（顺风=负score=偏多=正vote） | 中权重 × `macro_data_confidence` | 环境 lean |
| **Funding** | 反身性小票（拥挤多→偏空燃料；拥挤空→偏多燃料） | 低权重、有界 | 反身性 |
| **SRD**（skew） | 由 `rr_z + ΔRR`（相对基线，非原始符号）合成 | 数据质量 × 偏离/动量幅度；临近到期降权 | 期权需求面方向 |
| **GGR 空间** | `pin_pull_direction × pin_trust`（仅正Gamma可信） | 小上限权重 | 有条件空间钉 |

> 方向票来源覆盖你要的全部语义：量价(TMV)、主动成交联合价格(CVD)、宏观环境(Macro)、仓位反身性(Funding)、期权需求(SRD)、做市商结构(GGR)。

### 5.2 CVD×价格四象限（联合价格，不单看）

CVD 强度先经**滚动分布标定**（见 §8），再与价格符号联合：

| | 价格↑ | 价格↓ |
| --- | --- | --- |
| **CVD↑** | 主买推动 → vote **+强**、高权重 | 买盘被吸收/暗派发 → vote **−小**、低权重、**抬冲突** |
| **CVD↓** | 卖盘被吸收/空头回补 → vote **+小**、低权重、抬冲突 | 主卖推动 → vote **−强**、高权重 |

确认象限=高权重同向；背离象限=低权重、且拉低一致度（→更易中性）。

### 5.3 合成、一致度、置信

```text
EDB_score   = Σ(vote_i · weight_i) / Σ weight_i                  # ∈[-1,1]
agreement   = Σ{ weight_i : sign(vote_i)=sign(EDB_score) } / Σ weight_i   # ∈[0,1]，证据收敛度=熵减
conf_raw    = 100 · |EDB_score| · agreement                     # 真实区间，无 62 顶
conf        = clamp( conf_raw · ggr_confidence_multiplier , 0, 100 )
```

- **一致度**就是"信息论增强回路"的落地：证据越多越同向 → agreement 高、conf 高（后验尖、熵低）；证据冲突 → agreement 低 → conf 低 → 中性（**真不明=合法结论**）。
- **无硬翻面锁**：`lean = sign(EDB_score)` 每 tick 直接取；稳定性来自"多加权证据不会被单 tick 噪声推翻"，而非冻结。可选 `edb_score_smooth_n`（默认 1–2，仅去抖、不滞后；默认偏小以保住反转及时性）。

### 5.4 GGR 门（先门、后调制、最后才空间票）

```text
若 GGR.regime = 强负Gamma(放大) 且强度≥veto阈 → 强制 Neutral / No-Trade（不在放大区制卖单边）
否则 ggr_confidence_multiplier ∈ [负Gamma<1 … 正Gamma钉住>1(有上限)]
GGR 空间票 仅在正Gamma区制、pin 明显偏离现价时进入 5.1，权重小
```

### 5.5 分类输出

```text
若 GGR veto / 数据不足 / NR 未激活 → Neutral（No-Trade）
否则若 |EDB_score| < edb_neutral_abs 或 conf < conf_neutral_min → Neutral（真方向不明）
否则若 conf ≥ conf_strong → STRONG lean
否则若 conf ≥ conf_weak  → WEAK lean
否则 → Neutral
side: EDB_score>0 → Put Credit Spread；<0 → Call Credit Spread
```

---

## 6. 字段契约（EDB 输出，进 FactorSnapshot["edb"]）

```json
{
  "schema_name": "EDB",
  "schema_version": "edb.v0.5",
  "precondition": { "nr_active": true, "nr_state": "NR_REPAIR_CONFIRMED" },
  "edb_score": -0.41,
  "agreement": 0.78,
  "confidence": 57,
  "lean": "BEARISH_WEAK",
  "side_hint": "call_credit_spread",
  "support_label": "TRADE_SUPPORT_WEAK",
  "next_action": "ALLOW_DOWNSTREAM_WITH_CAUTION",
  "ggr_gate": { "regime": "POSITIVE_GAMMA_PINNING", "multiplier": 1.08, "veto": false },
  "evidence": [
    {"key":"TMV","vote":-0.55,"weight":0.40,"detail":{"tmv_blend":-0.25,"window_conflict":false}},
    {"key":"CVD","vote":-0.10,"weight":0.12,"detail":{"quadrant":"price_down_cvd_up_absorb","strength_pctl":0.3}},
    {"key":"MACRO","vote":+0.18,"weight":0.18,"detail":{"macro_score":-0.18,"regime":"Mild Tailwind"}},
    {"key":"FUNDING","vote":+0.05,"weight":0.05,"detail":{"effect":"opposite_crowding_fuel"}},
    {"key":"SRD","vote":-0.30,"weight":0.20,"detail":{"rr_z":-0.9,"delta_rr":-0.006}},
    {"key":"GGR_SPATIAL","vote":+0.10,"weight":0.05,"detail":{"pin_strike":74000,"pin_trust":0.4}}
  ],
  "conflict_level": "MILD",
  "reason_codes": ["EDB_AGREEMENT_MODERATE"],
  "summary_cn": "弱偏空：TMV 与 SRD 偏空主导，Macro 温和顺风部分抵消；正Gamma钉住区制安全，置信 57。"
}
```

要求：状态栏**展示每条证据的 vote/weight 与关键数值**（不只显示最终 lean），延续 v0.4.1 的"数值可追溯"原则。

---

## 7. 数据源与取数（守边界、控调用量）

| 因子 | 端点 | 字段 |
| --- | --- | --- |
| TMV/CVD/M-DIE | Binance(已实现) | klines / aggTrades / funding |
| Macro | Yahoo(已实现) | VOLQ/DXY/US10Y 日线 |
| 期号(已实现) | Deribit `public/get_instruments` | strike/option_type/expiry |
| **SRD + GGR 局部** | Deribit `public/ticker`（近月平值附近各档，**两因子共用一次取数**） | `greeks.delta`、`greeks.gamma`、`mark_iv`、`open_interest`、`underlying_price` |
| **GGR 聚合** | gexmonitor(已接入) | `flip_point`、`hedging_curve`、`spring`、`asset_price`；落地时从 `raw_payload` 补 `net_gex/call_wall/put_wall/max_pain`(若有) |

只读公共 GET，不触执行边界。Deribit ticker 仅取**方向/区制所需**(delta/gamma/IV/OI)，**不取用于选腿/报价**。

---

## 8. CVD 重标定口径（修死分支）

问题：`cvd_norm=净/总`，固定阈值 0.35(moderate)/0.60(strong) 实盘永不可达 → 永远"弱"。

口径：保留 `cvd_norm` 原值，但**强度分层改为滚动分布标定**（复用 `factors.robust_distribution_normalize` 的稳健 z 思路）：

```text
维护每个 horizon 的 cvd_norm 滚动历史（窗口 cvd_strength_window，待校准）
strength_z = 稳健标定(cvd_norm, 历史)
分层 NEUTRAL/WEAK/MODERATE/STRONG 按 strength_z 的分位带（待校准），而非绝对值
```

效果：MODERATE/STRONG = "相对近期分布的离群净流"，可真正触发确认/背离，使 CVD 重新进入决策。

同时**去双重计数**：`modules.evaluate_tmvf` 不再调用 `_tmvf_apply_micro_flow_tilt` 改方向；micro_flow 仅作为 CVD 证据数据进入 EDB。

---

## 9. 决策输出（闭环）

- `strategy_recommendation` 改读 `EDB.side_hint + EDB.support_label`（窗口开 + lean 明确才给方向；否则 No-Trade）。
- 删除"裸用 TMV 方向 + 不读 EDB"的旧路径（即上一轮审计的 🔴A 闭环缺口）。
- 仍只输出 signal/期号/strategy_type，执行层标记 `external_execution_program`。

---

## 10. 落地改动清单（实现时，先红灯后绿灯）

| 文件 | 改动 |
| --- | --- |
| `demo/edb.py`（新） | 证据合成、一致度、置信、分类、GGR 门 |
| `demo/skew_factor.py`（新） | SRD：按到期取 25Δ RR、rr_z、ΔRR、vote |
| `demo/deribit_adapter.py` | 加 `public/ticker`，归一化 greeks/mark_iv/OI |
| `demo/gex_adapter.py` | snapshot 增 regime/net_gex_sign/walls/max_pain（从 raw_payload） |
| `demo/gamma_regime.py`（新或并入gex） | GGR：区制门 + 到期局部 gamma×OI 钉价 |
| `demo/factors.py` | micro_flow 加滚动分布强度；CVD×价格四象限辅助 |
| `demo/modules.py` | evaluate_tmvf 去掉 micro_flow 方向 tilt（TMV vote=core） |
| `demo/main.py` | tick 内装配 SRD/GGR/EDB，写入 FactorSnapshot |
| `demo/strategy.py` | 读 EDB 结论（闭环） |
| `demo/config.py` | EDB/SRD/GGR/CVD 重标定 参数（全部待校准） |
| `demo/recorder.py` | EDB 证据表（vote/weight/数值）、SRD/GGR 行 |
| `tools/runtime_check_demo.ps1` | 红灯断言（§11） |
| FMZ 单文件 + 文档 | 同步重建 |

MODULE_SEQUENCE、只读边界、`module_results` 三段不变。

---

## 11. 红灯断言（验收标准）

- **置信连续**：扫描证据输入，confidence 取到连续区间，**不再只有 0/62**。
- **CVD 复活**：现实 net/gross 在相对历史离群时能到 MODERATE/STRONG，确认/背离象限能触发。
- **去双计数**：相同 1h-kline core 下，TMV vote 不随 micro_flow 改变。
- **SRD 正确性**：结构性为负但在修复的偏斜 → vote 看涨（**不得看空**）。
- **GGR 门**：price<flip 强负Gamma → conf 被砍/veto；正Gamma 集中行权价 → 命中 pin；负Gamma → pin_trust≈0、空间票≈0。
- **冲突→中性**：证据强反向 → agreement 低 → Neutral（真不明）。
- **边界**：无任何 leg/quote/order/candidate 字段；MODULE_SEQUENCE 未变；read-only 标记在位。

---

## 12. 深度思考审计（soul.md §7）

1. **冗余/独立性**：6 证据分别来自 1h趋势/成交量柱流/3日跨资产/永续仓位/期权需求/做市商结构——条件相对独立；已去 TMV-CVD 双计数。✅
2. **能否改决策**：重构后每条都给加权 vote/门；上线后用观测验证；若某条长期被支配→降级为观察项（准入考核线保留）。✅
3. **噪声/延迟/流动性**：SRD/GGR 增加 Deribit 调用（速率/延迟）→ 仅近月平值 + 刷新节流；翼部稀薄降权。⚠️ 已设计但需观测。
4. **经验阈值伪装理论**：所有阈值标"待校准"，校准版本化。✅
5. **路径/Gamma/磨损风险**：GGR 负Gamma 门正是防"短Gamma 顶趋势爆亏"；执行磨损仍由外部层。✅
6. **纸面可行真实不可执行**：方向层不下单；执行可行性外置。✅
7. **持仓后=重新预测？**：本稿不动 Post-Entry（外置）。
8. **裸卖/无保护**：仍单边带保护 Credit Spread 框架；保护腿外置。✅
9. **长期量化视角缺什么**：置信现在有实义，可供下游分档/仓位；仍缺真实 P&L 归因（下一阶段观测补）。

审计分类：
- **立即采纳**：时序/方向解耦、CVD 重标定+去双计数、置信=|EDB|×一致度、GGR 安全门、闭环。
- **进入观察**：6 因子各自的边际贡献、SRD/GGR 阈值校准、Deribit 调用稳定性。
- **暂不采用**：OI/Coinbase溢价/清算图（边际信息低或数据重，待 EDB 仍频繁不明时再议）。

---

## 13. 下一版任务

1. 按 §10 实现，§11 红灯先行。
2. 真实数据校准：CVD 滚动窗口与分位带、SRD 基线窗口、GGR 区制/veto/pin 阈值、各证据基重。
3. 观测每条证据是否真的移动 EDB；不达准入线者降级观察。
4. 观测 EDB 在真实行情下方向稳定性 vs 反转及时性的平衡（验证"无硬锁、靠信息量稳"是否成立）。
5. 之后再评估是否引入 KPF 空间层 / 真实 P&L 收益归因。

---

## 14. 一句话总结

**v0.5 把方向层从"TMV 单人选边 + 一堆摆设 + 0/62 塌缩"重构为"DIE+Anchor 定时序、EDB 用六源独立证据按一致度(熵减)合成到期窗口方向、GGR 把住单边卖权的安全门、证据冲突就老实中性、反转就即时翻"——让每个因子要么真正推动决策、要么自动降级为观察。**
