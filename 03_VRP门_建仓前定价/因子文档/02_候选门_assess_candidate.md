# 02 · 候选门（assess_candidate）

> 模块：②-门 VRP · 第二层（权威 ccy full-burn）
> canonical：`系统总纲\VRP\src\vrp_model.py:assess_candidate`（:280）
> 最后核对：2026-06-02（源码）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | assess_candidate（VRP 候选门） |
| 所属回路 | ②-门 VRP · 建仓前定价 |
| 作用层 | 风险门 |
| 理论机制 | 对具体垂直候选计算可执行净 credit、hurdle 净 credit 和完整 round-trip 摩擦后的 ccy edge。 |
| 预期符号 | `candidate_vrp_edge_ccy` 为正且超过门槛才 PASS；小于门槛则 BLOCK。 |
| 适用周期 | PLAN 轮每条垂直候选经过窗口门后。 |
| 与现有因子重叠 | 与 plans/leg_selection 共用候选报价，与 forward_vol_hurdle/BS pricer 共用重定价基准。 |
| 主要失效条件 | bid/ask 过期、保护腿 IV 缺失、费用/价差摩擦低估、把 edge 加进 plan_ev。 |
| 改变的决策 | 改变候选是否能进入方案库和 VRP 相关审计字段。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位
对窗口门 PASS 后的每条**具体垂直候选**，做权威的**币种全摩擦（full-burn）净 edge** 判定：可执行报价下的净 credit，减去同结构在 forward_vol_hurdle 下 BS 重定价的净 credit，再减完整 round-trip 摩擦。**这才是 VRP 那道"硬门"。**

## 2. 当前具体实现（`vrp_model.py:assess_candidate`）
输入 `CandidateQuote`（spot/short_strike/protection_strike/dte/amount/两腿 bid-ask/executable_short_iv/executable_protection_iv/forward_vol_hurdle/...）。

1. `option_type = call(SHORT_CALL) / put(SHORT_PUT)`。
2. **可执行净 credit** = `(short_bid − protection_ask) × amount`（卖短腿吃 bid、买保护吃 ask，保守）。
3. **hurdle 净 credit** = 两腿在 `hurdle` 波动下 `black_scholes_price_usd / spot` 重定价之差 × amount（见因子 04）。
4. **完整摩擦** `full_round_trip_friction`：
   - `entry_exit_fees = 2×_option_fee(short_bid) + 2×_option_fee(protection_ask)`（进+出）。
   - `spread_reserve = spread_round_trip_multiplier × (短腿半价差 + 保护腿半价差)`。
5. **候选 edge** = `可执行净credit − hurdle净credit − full_round_trip_friction`。
6. **门**：`short_iv`/`hurdle` 缺 → `BLOCK`；`candidate_edge ≤ min_candidate_edge_ccy` → `BLOCK`（`CANDIDATE_FULL_BURN_EDGE_TOO_THIN`）。
7. 输出 `CandidateAssessment`：`candidate_vrp_edge_ccy`、`candidate_vrp_gate`、`vertical_net_credit_at_executable_quotes/at_forward_vol_hurdle`、`full_round_trip_friction`、`vrp_residual_score=max(0,edge)`、`reason_codes`。

## 3. 关键阈值（选中策略 `strict_cost_cold_guard_v1_1`）
`min_candidate_edge_ccy=0.00005`、`spread_round_trip_multiplier=3.0`（覆盖默认 2.0）、`option_fee_cap_ccy=0.0003`、`option_fee_rate=0.125`。均 PLACEHOLDER。

## 4. 整合中的路径修改（收口，核心）
- 嵌执行层 PLAN 轮，每条垂直候选过候选门，`BLOCK` 剔除。
- **3 个原语收口到 canonical**（删本地副本）：`_norm_cdf→hedge_risk._norm_cdf`、`_option_fee→accounting.acct_option_fee_ccy`、`_spread_half_cost→accounting.acct_spread_cost`。
- 候选字段 → `ExecutionPlanPackage.menu`（`vrp_window_id/candidate_vrp_gate/candidate_vrp_edge_ccy/forward_vol_hurdle/full_round_trip_friction/vrp_reason_codes`）。
- `candidate_vrp_edge_ccy` 与 `plan_ev`（风险中性）**面板分开标，不可相加**；`vrp_residual_score` 只作展示/tie-break，**不进 PLAN_WEIGHTS**。

## 5. 当前目标 / 待办
- 真测 edge（Phase 2）：把 hurdle-vs-RV 保护性升级为"IV 入场→真实到期结果"成交级回放。只有给出扣成本正期望，才议 VRP 进排序权重。

## 6. 边界与陷阱
- 候选门是**权威 full-burn**——别用窗口门的 vol-edge 替代它做交易决策。
- `executable_protection_iv` 缺失时回落用 `hurdle`（保守近似）。
- edge ≤ 阈即 BLOCK：宁可 no-trade 也不卖 underpaid vol（VRP 肥不降 EDB 门，但 VRP 薄必须挡）。
