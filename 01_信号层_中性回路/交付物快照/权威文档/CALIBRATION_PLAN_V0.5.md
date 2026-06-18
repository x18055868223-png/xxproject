# EDB / SRD / GGR 阈值校准方案 v0.5（校准准备 / 任务2）

> 状态：**校准准备**。本文档只盘点阈值、定方法、给采数工具，**不臆造任何数值、不改一处 config**。
> 真正定值要等实盘快照累计够量，再按本方案逐项回填。守 soul.md：改阈值必须版本化、先红灯断言。

---

## 0. 为什么要校准 / 数据从哪来 / 用什么工具

- **依据**：`demo/config.py:231` 自述——"All thresholds below are robust starting DEFAULTS, not proven optima. Calibrate on real data."（v0.5 EDB/SRD/GGR 全部阈值都是稳健起点，非已证最优。）
- **数据源**：运行时本就把每 tick 写进 `demo/logs/snapshots.jsonl`（append-only）。其中 `schema_version ≥ nrd.schema.v0.5.x` 的记录已含完整 `factor_snapshot.edb / .skew / .gamma_regime` payload——这就是校准原料，**无需新增埋点**。
- **工具**：`tools/calibrate_edb.py`（纯 stdlib，读日志→打印每个阈值对应的经验分布）。它只测量、不改值，是校准回路的"量"那一半。
  ```
  python tools/calibrate_edb.py [snapshots.jsonl] [--window-open-only] [--min-version v0.5]
  ```
- **当前局限**：截至本文档，日志仅 5 条且全是同一离线 fixture → 分布退化（CVD 全 WARMING、SRD vote 冷为 0、GGR 全 TRANSITION）。harness 已对 <200 条打 WARNING。**必须先实盘累计**（建议 ≥ 2–4 周、覆盖不同区制）再读分布。

---

## 1. 校准优先级（先校直接门控"是否给交易支持"的，再校形状项）

| 级别 | 阈值簇 | 为什么优先 |
|---|---|---|
| **P0** | `edb_conf_strong / edb_conf_weak / edb_conf_neutral_min` | 直接决定 `TRADE_SUPPORT_STRONG / WEAK / No-Trade` 的产出比例 |
| **P0** | `ggr_negative_veto_strength / ggr_negative_cut_strength` | 安全门：是否否决/砍单边卖权，错设直接放行危险区制 |
| **P1** | `edb_base_weights`（六证据相对话语权） | 决定哪类证据主导后验方向 |
| **P1** | `edb_cvd_pctl_*` + `edb_informative_vote_abs` + coverage 口径 | 决定"信息量/覆盖度"折扣，v0.5.3 置信刻度的核心 |
| **P1** | `edb_tmv_vote_ref / edb_macro_vote_ref / srd_vote_scale` | 各证据 vote 的饱和标度 |
| **P2** | `edb_neutral_score_abs / edb_score_smooth_n`、SRD baseline 窗、GGR transition band、pin 项 | 形状/平滑，影响小、最后微调 |

---

## 2. 阈值逐项清单

### 2A. EDB（`demo/edb.py` + `config.py`）

| 键 | 现值 | 门控/作用 | 误设后果 | 校准方法（数据真值见 §3） | harness 看哪段 |
|---|---|---|---|---|---|
| `edb_base_weights.TMV` | 1.00 | TMV 证据基础权重 | 过高→TMV 一票独大 | 各证据 vote 对前向方向的 IC，按 IC 比例配权 | [3] |
| `edb_base_weights.CVD` | 0.70 | CVD(×2窗)基础权重 | — | 同上 | [3] |
| `edb_base_weights.MACRO` | 0.50 | 宏观权重 | — | 同上 | [3] |
| `edb_base_weights.FUNDING` | 0.25 | 资金费率权重 | — | 同上 | [3] |
| `edb_base_weights.SRD` | 0.70 | 偏斜权重 | — | 同上 | [3] |
| `edb_base_weights.GGR_SPATIAL` | 0.25 | gamma 空间钉权重 | — | 同上（仅钉住区制有效） | [3][6] |
| `edb_tmv_vote_ref` | 0.45 | \|tmv_blend\| 饱和到 ±1 的参考 | 太小→TMV 永远 ±1 | 取 \|tmv_blend\| 的 p85–p90 作饱和点 | flow.tmv_blend 分布* |
| `edb_macro_vote_ref` | 0.46 | \|macro_score\| 饱和参考 | 同上 | \|macro_score\| 的 p85–p90 | [5]/macro* |
| `edb_informative_vote_abs` | 0.15 | \|vote\|≥此为满信息，以下 eff_weight→0 | 太高→真证据被当冷证据扣权 | 看各证据 \|vote\| 分布，取"噪声/信号"分界 | [3] info/eff_weight |
| `edb_conf_neutral_min` | 35 | 置信<此→中性 No-Trade | 太低→噪声也出方向 | window-open 子样本置信分布 + 前向胜率拐点 | [1][2] |
| `edb_conf_weak` | 50 | 弱支持门槛 | — | 同上：让 WEAK≈次高分位段 | [2] |
| `edb_conf_strong` | 68 | 强支持门槛 | 太低→滥发强支持 | 让 STRONG≈top 15–25%（用户定风险偏好） | [2] |
| `edb_cvd_pctl_weak` | 0.40 | \|cvd_norm\| 滚动分位→WEAK | — | 验证分位段与前向流确认率对齐 | [4] |
| `edb_cvd_pctl_moderate` | 0.70 | →MODERATE（开始计 active 票） | 太低→弱流也确认 | 同上 | [4] |
| `edb_cvd_pctl_strong` | 0.88 | →STRONG | — | 同上 | [4] |
| `edb_cvd_strength_window` | 240 | cvd_norm 分位滚动窗 | 太短→分位漂移 | 看分位稳定性随窗长 | [4] |
| `edb_cvd_strength_min_history` | 20 | 够此条才出分位(否则 WARMING) | — | 暖机期长度，经验取 | [4] WARMING 占比 |
| `edb_price_neutral_return_pct_abs` | 0.05 | CVD×价格象限的价格死区(%) | 太大→价格信号被吞 | 看窗口收益率分布的"贴零"宽度 | window 收益* |
| `edb_neutral_score_abs` | 0.12 | \|EDB_score\|<此→中性 lean | — | 与置信门联动，P2 微调 | [1] \|edb_score\| |
| `edb_score_smooth_n` | 1 | EDB_score EMA 长度(1=关) | >1 会引滞后 | 仅当实盘抖动过大才>1，权衡反转滞后 | [1] |

\* 标注的 flow/macro/window 原始量当前 harness 未单列，可按需扩展（见 §4 待办）。

### 2B. SRD 偏斜（`demo/skew_factor.py` + `config.py`）

| 键 | 现值 | 作用 | 校准方法 | harness |
|---|---|---|---|---|
| `srd_target_delta` | 0.25 | 25Δ RR 构造 delta | 结构性，一般不校 | — |
| `srd_atm_delta` | 0.50 | ATM 归一 delta | 结构性 | — |
| `srd_rr_baseline_window` | 240 | robust-z 基线滚动窗 | 看 rr_z 稳定性 vs 窗长 | [5] rr_z |
| `srd_rr_baseline_min_history` | 12 | 基线暖机最小条数 | 暖机期；冷则 vote_confidence×0.6 | [5] vote_confidence |
| `srd_delta_rr_lookback` | 6 | ΔRR 动量回看 | 看 delta_rr 分布尺度 | [5] delta_rr |
| `srd_min_open_interest` | 1.0 | 期权 OI 过滤下限 | 看被滤档位占比 | — |
| `srd_near_expiry_downweight_hours` | 8.0 | 临近到期降权小时 | 经验，结合 IV 噪声 | [5] |
| `srd_vote_scale` | 1.0 | SRD 总 vote 增益 | 让 \|vote\| 分布与其他证据可比 | [3]SRD/[5]vote |

### 2C. GGR Gamma 区制（`demo/gamma_regime.py` + `config.py`）

| 键 | 现值 | 作用 | 误设后果 | 校准方法 | harness |
|---|---|---|---|---|---|
| `ggr_transition_band_pct` | 0.003 | \|price-flip\|/price≤此=TRANSITION | 太宽→永远过渡区 | 看 distance_to_flip_pct 分布 + 区制翻转噪声 | [6] dist_to_flip |
| `ggr_negative_veto_strength` | 0.80 | 负gamma 强度≥此→**否决**单边卖权 | **安全攸关**：太高→危险区漏防 | 负gamma 段事后已实现波动验证（强负确实更危才留） | [6] regime_strength + 事后波动 |
| `ggr_negative_cut_strength` | 0.50 | 负gamma 强度≥此→砍置信 | — | 同上，定 cut→veto 的斜坡 | [6] |
| `ggr_negative_conf_floor` | 0.40 | 砍区的乘子下限 | — | 砍多狠，结合胜率 | [6] conf_mult |
| `ggr_positive_conf_boost_max` | 1.15 | 正gamma 钉住最大置信加成 | 太高→过度自信 | 正gamma 段前向胜率确认才给加成 | [6] conf_mult |
| `ggr_pin_min_oi_share` | 0.15 | 信任 pin 的最小 gamma-OI 集中度 | — | 看 max_gamma_oi_share 分布 | [6]/pin |
| `ggr_pin_trust_negative_gamma` | 0.0 | 负区制 pin 信任(0=忽略) | 设>0 危险 | 默认保持 0，除非证据充分 | — |
| `ggr_spatial_vote_cap` | 0.25 | \|spatial_vote\| 上限 | — | 限幅，让空间票≪主干 | [6] \|spatial_vote\| |
| `ggr_pin_distance_ref_pct` | 0.02 | 空间票幅度的距离标度 | — | 看 distance_to_pin 分布 | [6]/pin |

### 2D. 硬编码常量（不在 config，若要校准需先提升为 config 键）

> 这些数字嵌在逻辑里，**当前不建议动**；若校准发现需要调，先抽到 `config.py` 再改（守 soul.md 可追溯）。

- `edb.py`：TMV `window_conflict` 可靠性 ×0.45；CVD 四象限权重(active 1.0/0.45、吸收 0.6/0.3、price_only 0.25、flat 0.1)、吸收票系数 0.4、price_only 0.3、WARMING ×0.5、慢窗 role_w 1.1；MACRO cached/partial ×0.5；FUNDING 系数(0.5/0.3/0.2)与权重(0.8/0.5/0.4)；`_conflict_level` 切点 0.80/0.65/0.50；coverage 把 CVD 记 2×。
- `skew_factor.py`：24h/48h 混合 0.45/0.55；`raw = 0.6·rr_z + 0.4·delta_term`；rr_z 限幅 ±3/3；delta_term 增益 20 或 (Δ/atm)·4；vote_confidence `0.5+0.15·n_expiry`、暖机 ×0.6、临近到期 ×0.6；`_lean_label` ±0.30（仅显示，EDB 不用）。
- `gamma_regime.py`：`_REGIME_STRENGTH_SPAN=0.012`；过渡区强度 ×0.4 标度；中性乘子 0.95/0.98；pin_trust ×0.5 标度。

---

## 3. 校准方法学（关键：方向真值怎么定）

1. **方向真值(label)** = 信号确认后**到期窗口(24–72h)的实际收益符号**（卖权窗口的相关跨度）。所有"方向类"校准（base_weights、各 vote_ref、置信阶梯）都用这个 label 做回看胜率/IC。
2. **分布回填法**：实盘累计 N 条快照 → `calibrate_edb.py` 出分布 → 按目标命中率定 cut。先量后定，不拍脑袋。
3. **置信阶梯定法**（P0）：只在 **window-open 子样本**（`--window-open-only`）上做。让 `STRONG ≈ 前向胜率最高的 top 15–25%`、`WEAK ≈ 次高分位段`、其余落 No-Trade。**最终分位由用户的风险偏好拍板**（多发弱信号 vs 少而精）。
4. **安全门定法**（P0，GGR）：取历史"强负 gamma 放大段"，看其事后**已实现波动/最大回撤**是否显著高于正/过渡区。若确实更危，保留 `veto=0.80`；否则下调。这是事后验证型校准，不是分位型。
5. **权重定法**（P1）：对每个证据，算其 vote 与前向 label 的 **IC（信息系数 / 命中率）**，按 IC 相对比例设 `edb_base_weights`。IC≈0 的证据应降权而非保留默认。
6. **饱和参考定法**：`*_vote_ref` 取对应原始量(\|tmv_blend\|、\|macro_score\|)的 p85–p90 作为"满票"点，避免常年 ±1 饱和。

---

## 4. harness 现状与待扩展

- **已覆盖**：EDB 后验(\|score\|/置信/覆盖/一致度)、分类产出 tally、置信阶梯模拟(window-open)、六证据 vote/weight/eff_weight/info、CVD 强度分位、SRD 原始量、GGR 区制/强度/距flip/乘子/否决率/空间票。
- **待扩展（按需）**：(a) 单列 `flow.tmv_blend`、`macro_score`、窗口收益率原始分布（供 §2A 标 \* 的项）；(b) 接入前向收益 label 做 IC/胜率交叉表（需要把 snapshot 与之后价格对齐——可另写 `tools/label_forward_returns.py`）；(c) 按区制/时段分层出分布。
- **运行**：`python tools/calibrate_edb.py --window-open-only`（仅看可交易窗口）。<200 条会打 WARNING。

---

## 5. 边界（本轮不做什么）

- **不臆造数字**：本轮零 config 数值改动；定值留待实盘分布。
- **只动 config 阈值**，不动结构、不改 schema/版本号。
- **守 v0.4 边界**：EDB/SRD/GGR 仍是前置信号层，不选腿/不报价/不下单；MODULE_SEQUENCE 不变。
- 改任一 P0 阈值前，按 soul.md 先加红灯断言（runtime_check），并在本文件记录"现值→新值→依据(分布快照)"。

---

## 6. 合理性自检结果（2026-05-31，无需实盘数据）

封版前用合成场景直接驱动 `evaluate_edb`，验证**阈值内部一致、置信阶梯可达、各门正确触发**（这是"检查合理性"，非数据校准）。结论：**当前 v0.5.x 默认值合理，封版前无需改任何阈值**。

| 场景 | lean | 置信 | 覆盖 | 一致度 | support | 门 |
|---|---|---|---|---|---|---|
| 1 强多对齐(窗口开) | BULLISH_STRONG | 93 | 94% | 100% | TRADE_SUPPORT_STRONG | — |
| 2 强空对齐 | BEARISH_STRONG | 84 | 94% | 100% | TRADE_SUPPORT_STRONG | — |
| 3 冲突(TMV多/CVD空) | NEUTRAL | 24 | 93% | 72% | WAIT_CONFIRMATION | — |
| 4 负Gamma否决 | NEUTRAL | 0 | — | — | NO_TRADE_BLOCKED | GGR_NEGATIVE_GAMMA_VETO |
| 5 强多但窗口关 | BULLISH(预热) | 92 | 93% | 100% | NO_TRADE_BLOCKED | 时序门 |
| 6 中等多 | NEUTRAL | 45 | 86% | 100% | WAIT_CONFIRMATION | [35,50) |
| 7 稀疏 TMV+MACRO(CVD缺/SRD冷) | NEUTRAL | 38 | **39%** | 100% | WAIT_CONFIRMATION | 覆盖度折扣 |
| 8 宏观硬阻断 | NEUTRAL | 0 | — | — | NO_TRADE_BLOCKED | MACRO_BLOCKING |
| 9 中强多 | BULLISH_WEAK | 59 | 89% | 100% | TRADE_SUPPORT_WEAK | [50,68) |

**读法**：阶梯三档全可达且单调（WAIT 45 → WEAK 59 → STRONG 93）；三道门（GGR 否决/宏观阻断/时序窗口）均正确触发；v0.5.3 覆盖度折扣在场景 7 验证生效（TMV+MACRO 满一致但 CVD 缺+SRD 冷 → 覆盖 39% → 置信压到 38，否则约 80）。复算脚本：合成输入直灌 `evaluate_edb`（一次性自检，未入库）。

**仍需实盘校准的不是"是否合理"而是"最优切点"**：阶梯 35/50/68 与 GGR veto 0.80 当前是稳健起点，要用 §3 的前向收益 label 把它们移到经验最优——这部分照旧 blocked on 实盘数据。

---

### 附：一句话给下一步
先让实盘把 `snapshots.jsonl` 跑厚（覆盖正/负gamma、宏观顺/逆风、不同 funding 区），再 `python tools/calibrate_edb.py --window-open-only` 出分布，**P0 两簇（置信阶梯 + GGR 安全门）先定**，其余按 §1 优先级跟进。
