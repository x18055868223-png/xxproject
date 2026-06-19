> 当前信号层口径（r2.2 / 2026-06-19）：本因子文档可能保留早期 v0.5/v1.1 代码路径或标定说明；当前 FMZ 交付物以 `demo/最新交付物/neutral_regulation_demo_fmz.py` v1.3.0 为准。本文用于解释因子语义和历史演进，实际运行字段以当前审计 JSON、状态栏和 r2.2 总纲为准。
# 05 · NeutralRepair（DIE + Anchor 时序门）

> 模块：① 信号层 · 时序门（不进 `module_results`）
> canonical：`demo\neutral_repair.py:NeutralRepairSignalTracker`
> 因子卡：`add\neutral_repair_presignal_v1_0_requirements.md`（已复制进交付物快照）
> 在链中的角色：决定**窗口何时开**；其 `is_active` 作 EDB 的 `precondition.nr_active`
> 最后核对：2026-06-02（源码）

## 1. 一句话定位
一个状态机：合成 M-DIE（短期单向位移事件）与 Anchor（受损/修复）判断"中性回路修复窗口"是否成立。**只管时序（窗口何时开），不管方向**。

## 2. 当前具体实现（`neutral_repair.py:update`）
状态流转（核心路径）：

```
NR_IDLE
  │ |m_die| ≥ nr_mdie_event_on_abs(0.65)        → 新建/合并 episode
  ▼
NR_DISPLACEMENT_ACTIVE  （DIE 事件在/刚出，等冷却）
  │ |m_die| ≤ cooldown(0.42) 且已观察到 anchor 受损
  ▼
NR_WAIT_ANCHOR_DAMAGE → NR_WAIT_ANCHOR_REPAIR
  │ anchor_score ≥ nr_anchor_repair_score(60) 且 |nd| ≤ nr_anchor_repair_nd_abs(0.75)
  ▼
NR_REPAIR_CANDIDATE  （需连续确认 nr_repair_confirm_ticks=2 次）
  ▼
NR_REPAIR_CONFIRMED  → is_active=True  ← 窗口打开
```

- **锚受损证据**（`_mark_anchor_damage`）：anchor_score<60 / 相对事件时刻掉≥`nr_anchor_damage_drop_score`(10) / `|nd|≥nr_anchor_damage_nd_abs`(1.0) / `ANCHOR_DEVIATION_WIDE`。`nr_require_anchor_damage=True` → 不见受损不进修复。
- **反向事件**：出现反向 DIE，需 `nr_opposite_confirm_ticks`(2) 连续确认才切 episode（`nr_reset_on_opposite_event=True`）。
- **过期**：context TTL `nr_repair_context_ttl_min`(360min)、确认后信号 TTL `nr_repair_signal_ttl_min`(60min) → `NR_REPAIR_STALE`。
- **置信**（`_confidence`）：基 45，按 DIE 峰值/锚受损证据/锚分/形态加分；非 CONFIRMED 态封顶 60。
- 输出 payload 含 `state / is_active / label / confidence / event_context / anchor_context / gating / threshold_profile`，schema `SCHEMA_NEUTRAL_REPAIR_SIGNAL`。

## 3. 关键阈值（现值，`config.py:213-231`）
`nr_threshold_profile="relaxed_test"`、`nr_mdie_event_on/off_abs=0.65/0.42`、`nr_episode_merge_gap_min=45`、`nr_opposite_confirm_ticks=2`、`nr_anchor_repair_score=60`、`nr_anchor_damage_score=60`、`nr_anchor_damage_nd_abs=1.0`、`nr_anchor_repair_nd_abs=0.75`、`nr_repair_confirm_ticks=2`、`nr_repair_context_ttl_min=360`、`nr_repair_signal_ttl_min=60`。

## 4. 整合中的路径修改
**零代码改动**。但有一条**整合上线硬前提**直接挂在本因子：
- 闸 B（信号驱动实盘）要求 `nr_threshold_profile` 由 **`relaxed_test` → `production`**（总纲 v0.3 §9）。当前是 relaxed_test（事件阈放宽到 0.65），属测试档；切 production 是校准 + 上线的强制步骤。

## 5. 当前目标 / 待办
- P0 校准把 `nr_mdie_event_on/off_abs`、`nr_anchor_repair/damage_score` 按真实 DIE 事件分布定值，并产出 production profile。
- 当前实盘日志退化（reason 多为 `DIE_EVENT_DETECTED_RELAXED_065`），需累计真实区制样本。

## 6. 边界与陷阱
- **`is_active=True` 只代表窗口开，不代表方向**。方向必须读 EDB；EDB 在窗口未开时把方向降为观察预热（`NO_TRADE_BLOCKED`）。
- 这是整条链里唯一的"时序闸"，relaxed_test 档下更易触发，回测/压测看似常开，**不可据此判 edge**。
- M-DIE 或 Anchor 任一数据不足 → `NR_DATA_INSUFFICIENT`，窗口不开。
