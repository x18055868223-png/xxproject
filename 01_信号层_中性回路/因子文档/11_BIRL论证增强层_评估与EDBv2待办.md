# 11 · BIRL 倾向性论证增强层 —— 方案评估与 EDB v2 待办

> 性质：**设计评估记录 / 押后清单**（不是已落地因子，不进 `module_results`，不进 FactorSnapshot）
> 来源方案：[`docs/信号层倾向性论增层优化思路参考.md`](../../docs/信号层倾向性论增层优化思路参考.md)（2026-06-05）
> 评估对照：`中性回路 - opus4.8/demo/edb.py`（EDB v0.5.4）+ `signal_review.py` + `config.py`（demo v1.1.0）
> 结论：**v1.1 不引入完整 BIRL 引擎**；只采纳两项零标定依赖的真实改进（已落地，见 §4）。其余作为 **EDB v2 候选**押后，待真实标定数据。

---

## 1. 背景与定位

参考稿提出 **BIRL（Bayesian Information Reasoning Layer，贝叶斯信息论倾向性论证增强层）**：把 EDB 的「六证据加权投票」升级为「分簇 + 降相关 + 因果折扣 + 熵度量置信 + 状态机防跳变 + 全 EvidenceUnit 可追溯」的双后验体系（BIRL-A 方向后验属信号层 / BIRL-B 可执行后验属执行层）。

关键事实（参考稿写作时未完全纳入）：

1. **历史**：v0.3 曾有 `bias_thesis` 倾向性论证层 arbiter，已于 **v0.51 退役**，被 EDB 取代；现 `bias_thesis.py` 仅剩 funding/macro verdict helper 供 EDB 复用。BIRL 若落地是「EDB v2」，不是从零新建。
2. **EDB 现状**：EDB 已实现 BIRL 的多数骨架——带 floor 的加权后验、`agreement`、`coverage`、信息量死区（`info`→`eff_weight`）、GGR veto/调制、平滑钩子（`edb_score_smooth_n`）、`confidence_decomposition`、中文 `summary_cn` + 审计卡。
3. **第一性约束**：参考稿自身（§10.1、§16）承认**本工程无可直接验证 BIRL 的真实 `snapshots.jsonl`，所有权重/公式/阈值只能作设计参考**，且主张 **shadow 优先、不替换 EDB**。

因此评估主轴是：**哪些是「已有」、哪些「零成本可采纳」、哪些「依赖标定数据必须押后」、哪些「与现有哲学冲突需先论证」。**

---

## 2. 逐项裁决（采纳 / 已有 / 押后 / 暂不做）

| # | BIRL 主张（参考稿 §） | 裁决 | 依据（对照现码） |
|---|---|---|---|
| 1 | log-odds 后验替代加权投票（§3.4） | **押后→EDB v2** | 加权均值后验在「符号 + 单调置信」上与 log-odds 当前等效；log-odds 只有在**每证据 LLR 尺度被真实标定**后才增值。重写封版权威层内核、用未标定尺度，高风险、零已证收益。 |
| 2 | TMV 作 prior、其余作 evidence（§3.5） | **已有（实质）** | EDB 已给 TMV 最高权重（1.00 vs CVD 0.70 / MACRO 0.30 / FUND 0.25）。无 log-odds 时「prior vs evidence」纯语义重述。 |
| 3 | 每证据 `causal_discount` 因果折扣（§3.2） | **押后→EDB v2** | 理论上最有价值的一项，但**必须用真实 `snapshots.jsonl` 的相关性矩阵**（§10.4）求得。无数据期硬编码=假精度。 |
| 4 | `correlation_penalty` / `cluster_cap`（§3.4） | **押后→EDB v2** | 同上：需 corr(TMV,SRD)、corr(TMV,Macro)… 当前无。 |
| 5 | CVD flow-only 与 price-confirm 拆分（§3.6） | **已有（部分）** | EDB 已 `tmvf_micro_flow_direction_tilt=False`（CVD 独占流，不回灌 TMV 价格路径），CVD 用量价四象限。进一步拆分需标定证明增益。 |
| 6 | 熵置信 entropy-based confidence（§4.2） | **押后** | `strength×agreement×coverage` 已是 (1−熵) 的单调代理。置信**刻度本就是 P0 第一校准对象**，无数据期改刻度会污染校准基线。 |
| 7 | `coverage_independent` 扣同源覆盖（§4.3） | **押后→EDB v2** | 需同源相关性数据。EDB 已有 `coverage_present` + 信息量加权。 |
| 8 | 稳定性状态机 + 方向迟滞（§5.1–5.2） | **暂不做（哲学冲突）** | EDB **刻意「不设硬滞后锁」**——既定设计：稳定性靠证据广度、真反转及时翻。状态机部分与此矛盾；覆盖一个刻意选择须先有设计论证 + 标定。 |
| 9 | 死区 Deadband（§5.3） | **已有** | `info = clamp(|vote|/0.15)` 即死区，亚阈票 `eff_weight≈0`。 |
| 10 | 后验低通平滑（§5.4） | **已有（钩子在）** | `edb_score_smooth_n` 已存在，默认 1（关，抗滞后）。标定后可开。 |
| 11 | `UNCALIBRATED` 标注 + 「置信≠胜率」诚实（§4.1、不变量 #7） | **采纳（已落地 v1.1）** | 零成本、诚实、不需数据。见 §4-B。 |
| 12 | `trace_summary_cn` 富论证（§6） | **已有（大体）** | `summary_cn` + 审计卡已出中文叙述 + 冲突/对立项分解。 |
| 13 | gex_info 并入 BIRL 簇（§14） | **押后 / 已软接** | gex_info 已是软、只降级上下文层。提升为簇增表面、不改行为。 |
| 14 | BIRL-B 可执行后验 / delta 分档 / `plan_win_rate`→`delta_otm_proxy` 改名（§8–9） | **范畴外** | 属**执行层**（`spm_calendar v1.6.2`），不在信号 v1.1。转执行层轨道。 |
| 15 | 推送 bug 修复（§15） | **采纳（已落地 v1.1）** | 确认 bug。见 §4-A。 |
| 16 | shadow 优先、不替换 EDB（§14、§16） | **认同** | 与参考稿自身结论及本工程纪律一致。 |

---

## 3. 总结论：完整 BIRL 现在不必引入

- BIRL 最有价值的三项（`causal_discount` / `correlation_penalty` / `coverage_independent`）**全部以真实前向标签/相关性矩阵为前提**；本工程当前日志退化（多为同一离线 fixture），无法标定。无数据期落地=给封版加一层**不可调、不可证**的机器，恰是参考稿 §16 自我告诫的「项目当前最缺的不是更多分数，而是真实前向样本与扣成本回放」。
- 状态机 + 方向迟滞与 EDB「不设硬滞后锁」的既定哲学**直接冲突**，覆盖需独立论证。
- 其余多数主张 EDB **已实现或已有钩子**，重写=风险无收益。
- 因此把完整 BIRL 定位为 **EDB v2 候选设计**，按参考稿 Phase 0–4 推进，**第一阶段（影子/校准）的前置是先有真实 `snapshots.jsonl`**。

---

## 4. v1.1 实际落地（两项零标定依赖改进）

### A. 推送修复（确认 bug，P0）
- `utils.fmz_push`：`Log(str(text)+"@")` → `Log(str(text)+" @")`。FMZ 以「日志正文以 `@` 结尾」触发 app/邮件推送，但 token 须**空格分隔**（` @`）；裸 `text@`（尤其多行正文末尾）可能只进普通日志、不进推送队列——即用户实测「出信号未推送」。
- `main._emit_signal_review_card` 调用点加 `try/except`：审计卡渲染异常→降级为带卡号一行简讯，**绝不让渲染错误吞掉推送**。
- 诊断要点：**全工程仅一处推送调用点**，故与 FMZ「20s 只推最后一条」节流**无关**（参考稿的次要猜测可排除）；推送合并本就已做。
- 诚实边界：FMZ 实盘 `@` 解析无法在本机离线 shim 验证；本修复对齐 FMZ 文档约定，且是唯一代码级嫌疑，需实盘最终确认。

### B. 置信诚实标注（零标定）
- EDB 新增 `calibration_state`（读 `config.edb_calibration_state`，现 `UNCALIBRATED`）。
- 审计卡结论句、置信链、推送综述、桥 digest 均显式标「未校准·非真实胜率/盈亏概率」。直接落实参考稿不变量 #7（未校准 confidence 不得解释为胜率）。
- 标定签收后翻 `config.edb_calibration_state="CALIBRATED"`，所有「未校准」提示**自动消失，无需改码**。
- `calibration_state` 为新增非破坏字段 → `schema_version` 仍 `nrd.schema.v1.0.0`。

### 验收（v1.1 单独记录）
两个新增点有**针对性、可重跑、驱动真实代码路径**的验收：`tools/v1_1_acceptance_check.py`（推送 token 静态+功能 / 审计卡 fallback 真实 `DemoRuntime._emit_signal_review_card` 路径 / calibration_state 在 edb·卡·digest·推送可见 + CALIBRATED 开关清除提示）→ PASS。最终综合验收 6/6 全绿（继承 5 项 + 本项）。完整记录见 `demo/副本快照/2026-06-05_信号v1.1.0_.../v1.1验收记录.md`。

---

## 5. EDB v2（BIRL）推进前置 / 触发条件

按参考稿 §10–11 与本工程 `CALIBRATION_PLAN_V0.5`：

1. **真实样本**：`snapshots.jsonl` ≥ 2–4 周，覆盖 正/负 gamma·transition、宏观顺/逆风、funding 拥挤/中性、CVD 同向/背离、SRD 冷启/正常、趋势/震荡/急涨急跌。
2. **真值标签**：信号确认后 24/48/72h 收益符号 + 窗口内最大不利/有利偏移；卖方侧扣 bid/ask·fee·保护腿·滑点·对冲·退出的组合净 PnL。
3. **相关性矩阵**：corr(TMV,CVD_price_confirm)、corr(TMV,SRD)、corr(TMV,Macro)、corr(SRD,GGR)、corr(GGR regime,realized_vol) —— 决定 `causal_discount` / `cluster_cap` / `correlation_penalty`。
4. **消融验证**：TMV only → +CVD → +SRD → +Macro → +GGR → Full，每簇须证明边际信息量（IC↑ / Brier·logloss↓ / 冲突场景误放↓ / 强信号净 PnL 分桶↑）；IC≈0 的簇降权或只留 trace。
5. **门槛**：仅当 BIRL 在**方向标签**与**扣成本净 PnL**两侧都优于 EDB，才允许 `BIRL support_label` 替代 EDB（参考稿 Phase 3）。
6. **若届时仍要先做 shadow**：在**同一条** Signal Review 推送内并入 BIRL 子标题（参考稿 §15.3），不新增第二条推送（避免 20s 节流覆盖）；shadow 必须标「并行参考，不改变 EDB 放行」。

---

## 6. 不变量（沿用参考稿 §13，落地时遵守）
1. BIRL-A 不选腿/不报价/不下单；2. BIRL-B 不重判方向；3. VRP 只做执行层定价硬门；4. GGR 主要安全门非方向确定性；5. Macro 主要 regime 修正/veto；6. CVD price-confirm 不与 TMV 重复计方向；7. 未校准前 confidence 不解释为真实胜率；8. 执行层不得线性放大未校准 confidence；9. 所有输出可追溯到 EvidenceUnit；10. 任何新增权重/阈值须版本化并用前向标签校准。
