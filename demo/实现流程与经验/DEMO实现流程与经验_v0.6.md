# DEMO 实现流程与经验 · v0.6（R4/R5/R6 — 缺口域/对冲/两份真 FMZ 合成）

> 本轮：demo v0.6，2026-06-02。一路推进 R4→R5→R6，全程真实回归背书 + 严格自审。

## 完成（R1-R6 全落地，真实基线副本、源仓库只读）
- **R4 缺口域接入**：risk_controls 入 bundle src；`build_entry_risk_anchor` 加 VRP 入场血缘（默认安全、向后兼容）。`test_gap_domains`(5)。
- **R5 对冲并入 HEDGE_WATCH**：`hedge_watch.watch_position` 读 SignalEvidencePackage edb/ggr + EntryRiskAnchor → 真实 evaluate_position_risk。`test_hedge_watch`(3)。
- **R6 合成两份真 FMZ**：build_bundle 升级（加 4 整合模块 + __future__ 处理 + --check 整合层校验+KPF 扫描）；strategy 加 `integrated_plan_preview`（真实菜单→VRP 双门→组合硬预算）+ `test_integrated_flow`(4)。
  - 执行 FMZ `execution_build/nrd_execution_fmz.py` **3145 行**；信号 FMZ `signal_build/nrd_signal_fmz.py` **8127 行**。

## 验证（全绿）
- 执行层真实回归：**80 passed, 0 failed**（R1 60 / R2 3 / R3 5 / R4 5 / R5 3 / R6 4）。
- 执行 FMZ `build_bundle.py --check` + 信号 FMZ `build_signal_bundle.py --check` 全过；两份编译通过；factor_spine 自检 PASS。

## 关键自审（纠偏自己的纰漏）
1. bundle `from __future__` 中段拼接→SyntaxError；2. 顶部注入 future→dataclass 在合成命名空间查 `__bundle__` 崩；3. 别名 import 破坏 bundle；4. `_spread_half_cost` 收口须保 None/倒挂→0；5. 障碍模型对中等 IV/DTE 敏感；6. VRP 期限 backwardation→DISTORTED_REVIEW（比预期保守）。

## 交付边界（诚实，见 HANDOFF）
信号导出桥字段映射未对真实 recorder snapshot 校验；执行 main() 仍走 ROUND_MODE（integrated_plan_preview/会话已就位待 FMZ spike）；VRP IV/RV live feed；占位因子待标定；闸 B 硬前提未动。

## 状态
两份真 FMZ 已合成、验证通过、80 真实测试绿。R 系列（真实基线焊接整合层）完成；余为 live 接线 spike + 校准（数据轨）。已出 HANDOFF 供新窗口续做策略优化迭代。
