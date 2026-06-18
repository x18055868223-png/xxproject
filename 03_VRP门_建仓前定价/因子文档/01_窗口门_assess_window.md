# 01 · 窗口门（assess_window）

> 模块：②-门 VRP · 第一层（廉价 vol-space 预筛）
> canonical：`系统总纲\VRP\src\vrp_model.py:assess_window`（:163）
> 最后核对：2026-06-02（源码）

## 1. 一句话定位
对每个 EDB 背书的 expiry/侧，做一道**廉价的 vol-space 预筛**：front 锚 IV 是否高于 forward_vol_hurdle，并按期限结构/数据质量路由。**只回答"这个 expiry/侧值不值得进入枚举"**，不算 ccy full-burn（那是候选门的权威工作，上游重复只会引入 delta→strike 反演噪声）。

## 2. 当前具体实现（`vrp_model.py:assess_window`）
输入 `WindowInput`（window_id/expiry/dte/side/front_anchor_iv/atm_front_iv/term_reference_iv_5_10d/rv_24h/72h/7d/rv_percentile/history_days）。

1. IV 归一（`normalise_iv`：>3 视百分比 ÷100）。
2. 算 `forward_vol_hurdle`（见因子 03）。
3. **门判定**：
   - `front_iv` 或 `hurdle` 缺 → `BLOCK`（`WINDOW_DATA_MISSING`）。
   - 期限结构：`ratio = front_iv/term_iv`；`≥event_backwardation_ratio(1.35)` → `DISTORTED_REVIEW`（EVENT_DISTORTED）；`≥term_backwardation_ratio(1.18)` → `DISTORTED_REVIEW`（STRESSED_BACKWARDATION）；`≥0.96` → FLAT。
   - **vol-space edge**：`representative_vol_edge = front_iv − hurdle`；若仍 PASS 且 `edge < min_window_vol_edge(0.02)` → `BLOCK`（`WINDOW_VRP_EDGE_TOO_THIN`）。
4. 输出 `WindowAssessment`：`window_vrp_gate(PASS/BLOCK/DISTORTED_REVIEW)`、`representative_vol_edge`、`front_to_term_state`、`forward_vol_hurdle`、`rv_regime_anchor`、`reason_codes`。
- `eligible_windows`：返回 `window_vrp_gate==PASS` 的 expiry——**VRP 过滤窗口但不在窗口间做选择**。

## 3. 关键阈值（选中策略 `strict_cost_cold_guard_v1_1`）
`min_window_vol_edge=0.02`、`term_backwardation_ratio=1.18`、`event_backwardation_ratio=1.35`（默认）。均 `PLACEHOLDER_CALIBRATION_REQUIRED`。

## 4. 整合中的路径修改（收口）
- 嵌入执行层 PLAN 轮：对每个 EDB 背书 expiry/侧调 `assess_window`，`BLOCK/DISTORTED_REVIEW` → 该 expiry 不进枚举。
- `normalise_iv` 收口到 `hedge_risk._normalise_iv`（删本地副本）。
- 窗口字段进 `ExecutionPlanPackage` / 面板拒绝漏斗。

## 5. 当前目标 / 待办
- 窗口门是预筛，门判定行为在 v1.1 改名后**不变**（仅 `representative_structure_edge_ccy` 误标名 → 诚实改 `representative_vol_edge`），故保住既有 268k 遍历证据与选中策略。
- 阈值校准随 edge 验证（Phase 2 多时点 IV）。

## 6. 边界与陷阱
- **窗口门不做 ccy full-burn**——别在窗口级重复候选门的全摩擦计算（v1.1 修正点）。
- `representative_vol_edge` 单位是 **vol points（IV 差）**，不是 ccy；不可与候选门的 `candidate_vrp_edge_ccy` 混用。
- `DISTORTED_REVIEW`（期限倒挂/事件扭曲）走人工复核路由，不是简单 BLOCK——不进 hurdle 标量。
