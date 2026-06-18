# DEMO 实现流程与经验 · v0.3（R1 KPF 减法 · 真实代码轮）

> 本轮：demo v0.3，2026-06-02。**第一个真实代码轮**——在真实执行/对冲基线副本上做 KPF 减法。

## 本轮目标
R1：把真实执行 `src/`（2043 行合成、12 模块）+ 对冲 `hedge_risk.py` 复制进 `execution_build/realsrc/`（源仓库只读），按 `kpf_cut_policy` 口径**实改**删 KPF，跑**真实** `tests/run_all.py` 全绿。

## 本轮产出（真实代码改动，共 30 处编辑）
- **src（6 文件 15 处）**：
  - `config.py`：`PLAN_WEIGHTS` 删 kpf 并等比归一 → `{win_rate:0.375, rr:0.375, signal:0.25}`；删 `KPF_CONTESTED_CORE/KPF_NEAR_BOUNDARY/KPF_FAR_RISK_ZONE` + validate_config KPF 校验（保留 UNDERLYING_REF_PRICE）。
  - `plans.py`：删 `plan_kpf_score`；`plan_assemble` 去 `kpf_core/kpf_far` 参数 + `kpf_score` 字段；`plan_prelim_score`/`plan_rank` 综合分去 kpf 项。
  - `leg_selection.py`：`legsel_pick_nearest_delta` 去 `kpf_core/kpf_near` → 纯目标 delta 选档；`legsel_protection_candidates` 去 `kpf_far` → 纯腿宽排序。
  - `strategy.py`：`_build_menu`/`_run_order` 去 `KPF_*` 传参（plan_assemble + legsel）；`_ctx_base` 去 `kpf_near/kpf_far`；build_entry_risk_anchor 调用去末位 kpf 参。
  - `accounting.py`：`acct_build_report` 删 `kpf_context` 报告块。
  - `hedge_risk.py`：删 `_BAD_KPF_BUFFER_STATES`/`_kpf_buffer_adverse`；`persistence_score` 去 `kpf_context` 参、**两项制重标定 0→LOW/1→MEDIUM/2→HIGH**（count≥2→HIGH）；`build_entry_risk_anchor` 去 `entry_kpf_buffer_state`；`evaluate_position_risk` 去 `kpf_context` 参；**SCHEMA_VERSION v0.3→v0.4**。
- **tests（5 文件 15 处）**：同步删 KPF 参/断言；删过时 `test_pick_nearest_delta_core_avoidance`；**重写 `test_kpf_buffer_changes_persistence`→`test_persistence_two_item_edb_ggr`**（验两项制 0/1/2→LOW/MED/HIGH + 无 KPF_BUFFER 确认码）；schema 断言 v0.3→v0.4；权重 {0.375,0.375,0.25}。

## 验证（全绿）
- **真实回归 `python tests/run_all.py`：60 passed, 0 failed**（含 test_plans 归一权重、test_leg_selection 删 KPF 后纯 delta/腿宽、test_hedge_risk v0.4 + 两项持续性、test_integration_dryrun 端到端空跑）。
- **KPF 功能性残留 = 0**：源码仅余「记录删除」的文档/断言（`删 KPF`/`KPF 已删`/`assert "KPF_BUFFER_WEAK_OR_BROKEN" not in c2`）；无任何 KPF 逻辑/字段/参数。
- `factor_spine.py` 自检 PASS；注册表 X5/X6/X17 → `R1_KPF_CUT_DONE`、X16 → `R1_V0_4_DONE`。

## 关键决策 / 经验
- **减法口径以 `kpf_cut_policy`（codex 复用件）为参照，但实改真实代码**：归一公式 ÷0.80、persistence 两项制映射，与复用件一致；真实代码改完用真实回归验证（不是 mock）。
- **改签名必同步改测试**：`plan_assemble` 的 kpf 是位置参数，删它必改所有调用点 + 测试；这正是 codex mock 路线绕过的真实成本——真实回归是"正确"的硬证据。
- **两项制语义变化要测**：原 3 项制 count≥3→HIGH；两项制 count≥2→HIGH（EDB+GGR 都不利即 HIGH，任一即 MEDIUM）。重写测试显式锁定该行为。
- **stale .pyc 会污染 grep**：KPF 扫描命中 `__pycache__/*.pyc` 是旧字节码，非源码残留；已清理。
- 不污染源仓库：全部在 `realsrc/` 副本上改，`Deribit期权交易执行层` 原仓库未动。

## 下一轮（demo v0.4 = R2 执行会话骨架）
把 `session_core.ExecutionSession` 接进真实 `strategy.main`：真实 `_build_menu` 产方案库 → `lock_plan`(plan_hash+TTL) → `approve`(显式) → `can_commit`；`ROUND_MODE` 降为内部/兜底，消除"重启进下单轮"。仍 `ALLOW_TRADING=False`。前置 FMZ 交互/持久化 spike（命令栏 + `_G`）。验收：同会话锁定/授权/TTL，真实回归仍绿 + 新增会话冒烟。
