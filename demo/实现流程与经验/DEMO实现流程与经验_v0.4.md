# DEMO 实现流程与经验 · v0.4（R2 执行会话骨架 · 真实数据）

> 本轮：demo v0.4，2026-06-02。承接 R1（KPF 减法绿）。

## 本轮目标
R2：把 `session_core.ExecutionSession`（复用自 codex、已验证）纳入真实执行 bundle，并用**真实 `_build_menu` 产出的真实方案**驱动会话，验证授权状态机契约。

## 本轮产出
- `session_core.py` 纳入 `realsrc/src/`（成为 FMZ bundle 的真实模块，标准库依赖、合 bundle 裸名约定）。
- `tests/test_session_flow.py`（3 测试）：用真实 `strategy._build_menu`（真实合约/行权/报价/S:PM 经 mock Deribit）产方案 → 驱动 `ExecutionSession`。

## 验证（全绿）
`python tests/run_all.py`：**63 passed, 0 failed**（R1 的 60 + 会话 3）。会话契约在**真实方案**上确认：
- `test_session_locks_real_plan_hash_stable_and_tamper_detected`：同方案 plan_hash 稳定；篡改行权（模拟重排/换腿）→ hash 变化（防误选）。
- `test_session_explicit_authorization_gate`：未显式授权(allow_real_order=False)→不可下单；显式授权+precommit 全过→可下单；任一 precommit 未过→不可下单。
- `test_session_ttl_expiry_blocks_commit`：TTL 内可下单；过期→拒绝(EXPIRED)，挡住旧报价进入执行。

## 关键决策 / 经验
- **与 codex 的实质区别**：codex 用硬编码 toy plan(net_edge=12.5)测会话；本轮 lock 的是真实 `_build_menu` 选出的真实垂直方案。会话骨架复用其形状（对的），但接真实因子数据（codex 缺的）。
- **plan_hash 是安全锚点**：取代"重启+手填 SELECTED_PLAN"。hash over 真实方案标识子集；篡改即变 → 防方案库刷新/重排后误选。
- **main() 主循环再指向延后到 R6**：`strategy.main` 仍走 ROUND_MODE（保持 R1 的 60 测试绿、零回归风险）；把"会话作主、ROUND_MODE 降兜底"的 main 重写放到 R6 bundle 装配，因为它还需 **FMZ 命令栏交互 spike**（offline 不可测）。R2 已证会话契约可用，main 接线是装配胶水。
- 会话模块标准库实现（dataclass/hashlib/json），无 demo import，符合 bundle 裸名+前缀约定，R6 可直接合入单文件。

## 下一轮（demo v0.5 = R3 VRP 嵌入 + 收口 canonical）
- 把 VRP 纯函数（assess_window/assess_candidate/forward_vol_hurdle/black_scholes_price_usd）落为 `realsrc/src/vrp_gate.py`，**4 个重复原语收口执行层 canonical**：`normalise_iv→hedge_risk._normalise_iv`、`_norm_cdf→hedge_risk._norm_cdf`、`_option_fee→accounting.acct_option_fee_ccy`、`_spread_half_cost→accounting.acct_spread_cost`（删 VRP 本地副本，保留 BS pricer 为唯一新能力）。
- 接 PRICE_GATE：真实候选过双门，BLOCK 不进可锁定方案；不进 PLAN_WEIGHTS。
- 测试：window/candidate PASS/BLOCK + 收口原语等价性 + 真实回归仍绿。
