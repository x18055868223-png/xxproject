# DEMO 实现流程与经验 · v0.5（R3 VRP 嵌入 + canonical 收口）

> 本轮：demo v0.5，2026-06-02。承接 R1/R2（63 绿）。

## 本轮目标
R3：把 VRP 封版 v1.1 纯门逻辑落为执行层 src 模块 `vrp_gate.py`，**4 个重复原语收口执行层 canonical**；接 PRICE_GATE 过滤真实菜单。

## 本轮产出
- `realsrc/src/vrp_gate.py`：窗口门/候选门/forward_vol_hurdle/BS pricer + `gate_plan`/`apply_vrp_gate`（PRICE_GATE 适配）。收口：`_normalise_iv`/`_norm_cdf`←hedge_risk、`_option_fee`←accounting.acct_option_fee_ccy、`_spread_half_cost`←accounting.acct_spread_cost。
- `realsrc/tests/test_vrp_gate.py`（5 测试）：等价性(window/candidate vs 封版快照) + 预期门态 + 原语接线 + 真实菜单过滤。
- 注册表 X1-X3 → `R3_EMBEDDED_CANONICAL`（X4 BS pricer 维持 SHARED_PRIMITIVE）。

## 验证（全绿）
`python tests/run_all.py`：**68 passed, 0 failed**（R1/R2 的 63 + VRP 5）。
- **等价性硬证据**：新 canonical 收口版与 VRP 封版快照在 5 场景上 window/candidate 门决策、hurdle、`candidate_vrp_edge_ccy`、`full_round_trip_friction` **逐一致** → 收口零行为漂移。
- 原语接线：`_option_fee==acct_option_fee_ccy`、`_spread_half_cost==acct_spread_cost`（且 None/倒挂→0）、`_normalise_iv==hedge_risk._normalise_iv`。
- PRICE_GATE：薄 IV 真实菜单全被窗口门 BLOCK+有 reason；肥 IV 窗口门可放行；纯过滤不崩、partition 完整。

## 关键自审发现（本轮纠偏自己的纰漏）
1. **`_spread_half_cost` 收口陷阱（重要）**：canonical `acct_spread_cost` 对缺失返回 `None`、不挡倒挂(ask<bid)；裸替换会让 `full_round_trip_friction` 求和遇 None 崩溃。**收口 wrapper 必须保留 VRP 的 None/倒挂→0 安全语义**，只复用核心算式。已加测试锁定。
2. **别名 import 破坏 bundle**：若写 `from hedge_risk import _normalise_iv as normalise_iv`，build_bundle 剥 import 后 bundle 内无 `normalise_iv`→NameError。**收口必须直接用 canonical 同名 `_normalise_iv`**（与内联后名字一致）。
3. **VRP 比预期更保守**：`PASS_fat_iv` 场景 front/term=1.19≥1.18 被正确判 backwardation→DISTORTED_REVIEW（不是 PASS）。等价测试两版一致证明收口没错，是我场景命名没考虑期限结构；已修为干净非应激窗口。**教训：门态预期要把期限结构路由算进去**。
4. mock 报价隐含 vol 偏低→VRP 正确判 underpaid→BLOCK；真实菜单 PASS 不可强断言（依赖报价 richness），改稳健断言（薄全 BLOCK、肥窗口门可放行）。

## 下一轮（demo v0.6 = R4 缺口域接入 + 账本血缘）
- `risk_controls.py` 纳入 bundle src；测 portfolio_budget(超→BLOCK size=0)/position_manage(止盈/末日Gamma 安全默认偏早)/attribution(最小归因)/replay 分桶。
- `build_entry_risk_anchor` 加 VRP 入场血缘(entry_vrp_window_id/forward_vol_hurdle/candidate_vrp_edge_ccy，默认空、不破坏既有调用)；账本 short 记录加 session/plan_hash/approval 线索。
- 占位安全方向单调（只挡/缩/早退）；真实回归仍绿。
