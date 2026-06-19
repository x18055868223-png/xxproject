> 当前执行层口径（r2.2 / 2026-06-19）：本因子文档可能保留早期 v1.6.2/KPF/Phase 1 描述；当前执行交付物以 `demo/最新交付物/spm_calendar_protected_short_v1.py` `STRATEGY_VERSION=2.5.0` 为准，交易门默认关闭。本文用于解释历史设计和组件语义，不代表当前已启用交易。
# 05 · ledger + EntryRiskAnchor（库存账本 / 状态机 / 入场风险锚）

> 模块：② 执行层（EntryRiskAnchor 构造在 `hedge_risk.py`，由 `strategy.py` 在成交后挂入账本包）
> canonical：`src\ledger.py` + `src\hedge_risk.py:build_entry_risk_anchor`（`strategy.py:310-318` 调用）
> 最后核对：2026-06-02（源码 + grep 实证）

## 1. 一句话定位
持仓库存与生命周期的单一真相源：库存账本 + 13 态状态机 + `_G()` 持久化 + 启动对账；成交即落**不可覆盖的入场风险锚 EntryRiskAnchor**，供对冲模块算 drift。

## 2. 当前具体实现

### 2.1 账本与状态机（`ledger.py`）
- 状态机（`S_*`，13 态）：`NO_POSITION → SIGNAL_READY → PROTECTION_SELECTION → SPM_SIMULATION → PROTECTION_BUILDING →（PROTECTION_ACTIVE_NO_SHORT）→ SHORT_BUILDING → SHORT_ACTIVE_PROTECTED → HOLD_MONITORING → SHORT_EXPIRED_OR_CLOSED → REUSE_DECISION / EXIT_OR_WAIT_REVIEW → CLOSED`。
- 库存（`ledger_make_inventory`）：v1 限 1 张保护腿覆盖 1 张 short；`amount_free/allocated`、`reuse_count`、`last_margin_relief_ratio`、`status`。
- `ledger_allocate_short`：硬保证 `short ≤ amount_free`。
- 持久化：`_G(_LEDGER_KEY/_STATE_KEY)`，崩溃/重启恢复。
- `ledger_reconcile`：启动时对比账本与交易所实际持仓，**不一致仅告警、不自动改仓**（自动恢复留 v1.1）。
- 进场门：`ledger_can_enter(signal_state, ENTER_SIGNALS)`。

### 2.2 EntryRiskAnchor（`hedge_risk.py:build_entry_risk_anchor`）
成交后由 `strategy.py:310` 构造、`:318` 以 `"entry_risk_anchor"` 挂入账本包。字段：`entry_price / entry_dte_hours / entry_short_delta / entry_short_gamma / entry_iv / entry_loss_boundary / entry_touch_probability / entry_probability_confidence(HIGH|LOW) / entry_boundary_distance_pct / entry_edb_side / entry_gamma_regime` + **当前还带 `entry_kpf_buffer_state`**（`hedge_risk.py:133,150`）。

## 3. 关键阈值 / 配置
无算法阈值；行为由状态机 + `_G` 持久化驱动。`entry_loss_boundary` 第一版用结构 breakeven；有 IV 用 IV 触界模型、无 IV 用 delta 兜底并标 `LOW`（总纲 v0.3 §6.3）。

## 4. 整合中的路径修改
1. **删 `entry_kpf_buffer_state`**（`hedge_risk.py:133,150`）：EntryRiskAnchor 去 KPF 字段（Phase 1）。
2. **加 VRP 入场血缘**（Phase 1 VRP 收口）：`entry_vrp_window_id / entry_executable_short_iv / entry_forward_vol_hurdle / entry_candidate_vrp_edge_ccy / entry_vrp_reason_codes`（总纲 v0.4 §4.3），与对冲共 IV/vol 基线、对冲不反向重做 VRP。
3. 升格为整合契约 `PositionLedgerPackage`（`nrd.integration.position_ledger.v0.1`）：每条 short leg 真实成交后落**不可覆盖**锚。

## 5. 当前目标 / 待办
- Phase 1 删 KPF 字段后跑 `tests/test_ledger.py`。
- 闸 A 验证 `_G` 重启恢复 + 启动对账一致。

## 6. 边界与陷阱
- **EntryRiskAnchor 不可覆盖**——它是对冲模块算 drift 的基准，一旦成交写入就冻结。
- 启动对账只告警不自动改仓（v1 边界），人工介入。
- EntryRiskAnchor 虽构造在 `hedge_risk.py`，逻辑上属"执行层 ORDER 轮产物"——对冲模块只读它，不写它。
