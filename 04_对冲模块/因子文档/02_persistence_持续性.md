# 02 · persistence（持续性确认）

> 模块：③ 对冲模块
> canonical：`Deribit期权交易执行层\src\hedge_risk.py:persistence_score`
> 最后核对：2026-06-02（源码）

## 0. 轻量因子卡

| 字段 | 内容 |
|---|---|
| 因子 | persistence（持续性确认） |
| 所属回路 | ③ 对冲模块 |
| 作用层 | 风险门 |
| 理论机制 | 用 EDB adverse 与 GGR adverse 判断风险恶化是否持续，而非一次性噪声。 |
| 预期符号 | 不利确认项越多，持续性等级越高。 |
| 适用周期 | 持仓监控循环，通常随 EDB/GGR 更新刷新。 |
| 与现有因子重叠 | 与 EDB、GGR 共用方向和 gamma 风险信息；KPF 残留为待清理项，不应新增依赖。 |
| 主要失效条件 | EDB/GGR 快照过期、单 tick 噪声被当持续、KPF 残留未剥离导致旧逻辑干扰。 |
| 改变的决策 | 改变 HEDGE_READY 是否满足持续性硬前提。 |
| 当前状态 | ACTIVE |

## 1. 一句话定位
判断"风险恶化是否**持续**（而非瞬时噪声）"。是 `HEDGE_READY` 的硬前提之一。**EDB 是唯一方向证据入口**，GGR 是负 Gamma 非线性加速的例外修正。

## 2. 当前具体实现（`persistence_score`，含待删 KPF）
当前数三个确认项：
- `_edb_adverse(direction_bias, edb)`：`confidence≥50 且 coverage≥0.50 且 lean 与持仓相悖`（SHORT_CALL 怕 BULLISH/SHORT_PUT/PUT_CREDIT_SPREAD；SHORT_PUT 怕 BEARISH/SHORT_CALL/CALL_CREDIT_SPREAD）。
- `_ggr_adverse(gamma_regime)`：`veto=True`，或 `NEGATIVE_GAMMA_AMPLIFYING` 且距 flip ≤1.0%。
- `_kpf_buffer_adverse(kpf_context)`：buffer_state ∈ {ABSENT/NO_BUFFER/TOUCHED/PENETRATED/FAILED/INVALID}。**← 待删**

分级（**当前**）：`count≥3 → HIGH / ==2 → MEDIUM / else LOW`。

## 3. 整合中的路径修改（核心）
1. **删 KPF 项**（`hedge_risk.py:35-37,218-220,230-231`）：去 `_kpf_buffer_adverse`、`_BAD_KPF_BUFFER_STATES`、`KPF_BUFFER_WEAK_OR_BROKEN`、`kpf_context` 入参。
2. **持续性 = `{EDB_ADVERSE, GGR_ADVERSE}` 两项**。
3. **重标定**：`0 项→LOW / 1 项→MEDIUM / 2 项→HIGH`（总纲 v0.3 §3.3.2）。
   > 注意语义变化：当前 3 项制下 HIGH 需全中、MEDIUM 需 2 项；两项制下 HIGH = EDB+GGR 都不利、MEDIUM = 任一不利。`HEDGE_READY` 闸沿用 v1.0（`hard_risk 且 persistence≥MEDIUM 且 hedge_friction_advantage`），即**至少 EDB 或 GGR 一项不利**才可能放行，保持"HEDGE_READY 最难触发"。

## 4. 关键阈值（现值）
EDB adverse 门：`confidence≥50 且 coverage≥0.50`（`hedge_risk.py:198`，与信号层 EDB 置信档对齐）。GGR adverse：距 flip ≤1.0%（`hedge_risk.py:213`）。

## 5. 当前目标 / 待办
- Phase 1 重标定后跑 `tests/test_hedge_risk.py` + 12 万样例回放，确认错配率不劣化、`HEDGE_READY` 更难触发。
- EDB adverse 的 confidence/coverage 门随信号层 EDB 校准联动。

## 6. 边界与陷阱
- **EDB 是唯一方向入口**——对冲不自行推断方向，只读 EDB 的 lean/confidence/coverage（来自 SignalEvidencePackage）。
- 删 KPF 是审计结论的直接落实：KPF buffer 只能作持续性确认，一旦用它改触界概率就制造"虚假安心"（总纲 v0.3 §2 旁证）。
- 两项制下 MEDIUM 门变松（任一即 MEDIUM），但 `HEDGE_READY` 还要叠 hard_risk + 摩擦占优，整体仍最难触发。
