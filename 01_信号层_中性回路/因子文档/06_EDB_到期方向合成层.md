> 当前信号层口径（r2.2 / 2026-06-19）：本因子文档可能保留早期 v0.5/v1.1 代码路径或标定说明；当前 FMZ 交付物以 `demo/最新交付物/neutral_regulation_demo_fmz.py` v1.3.0 为准。本文用于解释因子语义和历史演进，实际运行字段以当前审计 JSON、状态栏和 r2.2 总纲为准。
# 06 · EDB（到期窗口方向合成层）—— 权威方向层

> 模块：① 信号层 · 方向层（**权威**，不进 `module_results`）
> canonical：`demo\edb.py:evaluate_edb`
> 设计稿：`demo\FORWARD_DESIGN_V0.5_EDB.md` / `demo\LOGIC_REVIEW_V0.5_EDB.md`
> 最后核对：2026-06-05（源码，demo v1.1.0；算法仍 v0.5.4 标定口径，本版仅加 `calibration_state` 诚实标注，未改方向/置信算法）

## 1. 一句话定位
系统**唯一权威方向层**。把六类独立证据的有符号方向票按权重合成后验方向，置信 = 强度 × 一致度 × 覆盖度（带 floor 调制）× GGR 乘子。取代已退役的 bias_thesis。

## 2. 当前具体实现（`edb.py:evaluate_edb`）

### 2.1 证据票（vote∈[−1,1] + 相对 weight）
| key | 来源 | base_weight | vote 取法 |
|---|---|---|---|
| TMV | `flow.tmv_blend` | 1.00 | `clamp(blend/edb_tmv_vote_ref(0.45))`；窗口冲突 reliability×0.45 |
| CVD_4h / CVD_12h | `flow.micro_flow` + 滚动分布 | 0.70 | 四象限（量价共振/吸收/纯价）；强度=滚动分位；v0.5.4 价格强确认可顶起 vote |
| MACRO | MPF `macro_score` | 0.30 | `−score/edb_macro_vote_ref(0.46)`（逆风=看空） |
| FUNDING | funding verdict | 0.25 | 反身性：拥挤多头=下行燃料=小看空票 |
| SRD | `skew.vote` | 0.70 | 相对偏斜方向票 × vote_confidence |
| GGR_SPATIAL | `gamma_regime.spatial_vote` | 0.25 | 仅钉住区制给小空间票 |

### 2.2 信息量加权（v0.5.3）
`info = clamp(|vote|/edb_informative_vote_abs(0.15), 0,1)`；`eff_weight = weight·info`。冷证据（vote≈0）eff_weight≈0，不稀释 score/一致度。

### 2.3 合成
- `edb_score = Σ(vote·eff_weight)/Σeff_weight`（`edb_score_smooth_n=1` 即不平滑，抗滞后）。
- `agreement = 同向 eff_weight 占比`。
- `coverage = present 方向证据 eff_weight / 期望总权重(TMV+2·CVD+MACRO+FUNDING+SRD)`。

### 2.4 置信（v0.5.4 重标定，关键）
```
strength  = clamp(|edb_score| / edb_score_full(0.75), 0, 1)
agr_factor = edb_agreement_floor(0.60) + 0.40·agreement     ← 带 floor
cov_factor = edb_coverage_floor(0.50) + 0.50·coverage       ← 带 floor
confidence = clamp(100·strength·agr_factor·cov_factor · ggr_multiplier, 0, 100)
```
**为什么改**：v0.5.3 的 `100·|EDB|·一致度·覆盖·GGR` 四连乘把强趋势压垮（实盘强空只给 3-6）。0.5.4 把一致度/覆盖改为**带 floor 的调制**，强而一致、较完整的读数能真正进可交易区，真冲突/稀疏仍低。

**置信语义（v1.1.0 新增 `calibration_state`）**：payload 带 `calibration_state`（读 `config.edb_calibration_state`，现 `UNCALIBRATED`）。在前向标签标定前，`confidence` 是**证据后验质量分**（强度×一致×覆盖×Gamma门），**不是真实胜率/盈亏概率**——审计卡结论句、置信链、推送综述、桥 digest 均显式标「未校准」。这是落实"未校准 confidence 不得被解释为胜率、执行层不得线性放大"的红线。P0 校准签收后把 `edb_calibration_state` 翻成 `CALIBRATED`，所有"未校准"提示自动消失，无需改码。`calibration_state` 为新增非破坏字段，故 `schema_version` 仍 `nrd.schema.v1.0.0`。

### 2.5 门（veto / 调制）
- GGR `veto=True` → `GGR_NEGATIVE_GAMMA_VETO`；MACRO `MACRO_BLOCKING`；FUNDING `FUNDING_HARD_WARNING` → 任一 veto → confidence=0。
- GGR `confidence_multiplier` 在非 veto 时乘进置信。

### 2.6 分类（`_classify`）→ support_label / side_hint
- veto → `NO_TRADE_BLOCKED`。
- `|edb_score|<edb_neutral_score_abs(0.12)` 或 `confidence<edb_conf_neutral_min(35)`：窗口开→`WAIT_CONFIRMATION`，否则 `NO_TRADE_BLOCKED`。
- 窗口未开（`precondition.nr_active=False`）→ 方向仅预热，`NO_TRADE_BLOCKED`（`WAIT_DIE_ANCHOR_WINDOW`）。
- `confidence≥edb_conf_strong(68)` → `TRADE_SUPPORT_STRONG`；`≥edb_conf_weak(50)` → `TRADE_SUPPORT_WEAK`。
- side_hint：`edb_score>0`(看多)→`SIDE_PUT_CREDIT_SPREAD`；`<0`(看空)→`SIDE_CALL_CREDIT_SPREAD`。

## 3. 关键阈值（现值，`config.py:240-303`）
base_weights `{TMV:1.00, CVD:0.70, MACRO:0.30, FUNDING:0.25, SRD:0.70, GGR_SPATIAL:0.25}`；`tmv_vote_ref=0.45`、`macro_vote_ref=0.46`、`neutral_score_abs=0.12`、`informative_vote_abs=0.15`、置信档 `neutral_min/weak/strong=35/50/68`、`score_full=0.75`、`agreement_floor=0.60`、`coverage_floor=0.50`、`price_confirm_full_pct=0.75`；CVD 分位 `weak/moderate/strong=0.40/0.70/0.88`、`strength_window=240`。
> v0.5.4 起置信刻度与之前**不可比**，实盘置信分布重新起算。

## 4. 整合中的路径修改
**零代码改动**。EDB 的输出是整个整合方向链的源头：
- `support_label` → 执行层 `SIGNAL_STATE`（只接受 `TRADE_SUPPORT_STRONG/WEAK`）。
- `edb_score` 符号 / `side_hint` → 执行层 `DIRECTION_BIAS`。
- `confidence` → 执行层 `SIGNAL_CONFIDENCE`。
- `{edb_score, lean, agreement, coverage, ggr_gate}` → 对冲模块 `EDB_ADVERSE` 持续性判定的**唯一方向入口**。
- 执行层与 VRP 与 EDB/GGR/宏观是**独立 AND 双门**：VRP 肥不降 EDB 门。

## 5. 当前目标 / 待办（P0 校准核心）
- 置信阶梯 35/50/68、`score_full`、两个 floor 是 P0 第一簇校准对象（直接改实盘置信刻度，**须用户拍板风险偏好**）。
- 真值口径：信号确认后到期窗口（24–72h）实际收益符号；`base_weights` 按各证据 vote 对前向 label 的 IC 配权。
- **已知未修的口径风险**（`AUDIT_REPORT_2026-05-30.md`）：覆盖度只在 present 证据上算；0.5.3/0.5.4 已通过信息量加权 + 覆盖度折扣大幅缓解，但仍需实盘验证置信分布是否合理。

## 6. 边界与陷阱
- **方向不明→中性·暂停是合法结论**，但前提是证据已做真（否则是算法假不明）。
- 无硬翻面锁：lean 跟随 `sign(edb_score)` 即时翻，稳定性靠证据广度不靠机械滞后——真反转能及时退出。
- 不要把 EDB 的 `confidence` 与 NeutralRepair 的 `confidence` 混为一谈：前者是方向置信，后者是时序修复置信。
