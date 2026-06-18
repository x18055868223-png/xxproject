# demo —— 整合落地实验沙箱

> 位置：`中性回路整合工程\demo\`
> 性质：**整合路径的具体落地实验沙箱**。在此把约 31 因子按收束架构组装成两份 FMZ 文件；**不污染原仓库**。
> 区别：本 `demo` ≠ 信号层自带的 `中性回路 - opus4.8\demo\`（那是信号层源码包）。本 demo 是整合工程的落地工作区。
> 当前：**demo v0.1（地基轮）**，2026-06-02。

---

## 0. 目标与最终交付物
- **最终交付物 = 两份 FMZ 单文件**：① 信号层 FMZ + ② 执行层 FMZ，**包含全量因子（31）且符合设计稿（总纲 v0.5）**。
- 过程**可迭代**（demo v0.1 → v0.2 → …），每轮在 [实现流程与经验](实现流程与经验/) 留痕。
- 设计与实现**收束干净**：占位先实现、后基于真实数据标定，但绝不污染判断（见下）。

## 1. 三条治理基线（先读这三件，根除「做了很多无法利用」）
1. **[设计/01_因子收束分层架构](设计/01_因子收束分层架构.md)** —— 31因子→10域→11包→1会话；自顶向下阅读路径；两 FMZ 分工。
2. **[设计/02_缺口实现规范_先实现后标定](设计/02_缺口实现规范_先实现后标定.md)** —— 统一因子接口、占位安全默认、状态生命周期、收束干净五不变量。
3. **[设计/03_因子注册表](设计/03_因子注册表.md)** + **[shared/factor_registry.json](shared/factor_registry.json)** —— 31因子单一索引（脊柱）。

## 2. 目录结构
```
demo/
├── README.md                       ← 本文件
├── 审计/                            ← 对 codex 副本推进的审计结论
│   └── CODEX推进审计_v0.1.md
├── 设计/                            ← 三条治理基线（Q1 架构 / Q2 规范 / 注册表）
├── shared/                         ← factor_registry.json(脊柱) + factor_spine.py(可运行自检)
├── signal_build/                   ← 信号 FMZ 构建区 + signal_bridge.py(复用·导出桥)
├── execution_build/                ← 执行 FMZ 构建区（整合主战场）
│   ├── 执行层整合设计_v0.2.md       ← 锚定真实基线的执行层设计 + R1-R6 序列
│   ├── session_core.py vrp_adapter.py risk_controls.py kpf_cut_policy.py ← 移植自 codex,已验证
│   └── verify_integration_core.py  ← 整合核验冒烟（含真实 VRP 双门跑通）
└── 实现流程与经验/                  ← 每轮迭代日志（v0.1 地基 / v0.2 审计复用）
```

## 3. 怎么用脊柱（标准动作）
- 看系统全貌：`python shared/factor_spine.py`（打印 31 因子治理摘要 + 自检 + 域收束演示）。
- 找某因子：查 `03_因子注册表` 或 `factor_registry.json`（域/状态/输出包/源/标定需求一行答全）。
- 加/改因子：先改注册表登记 → 实现成 `FactorResult` 形状 → 跑 `factor_spine.py` 自检 PASS。

## 4. 收束干净·五不变量（违反即「乱」，必须挡回）
1. 一因子 = 一模块 + 一注册行 + 一域；没登记的因子不存在。
2. 因子只经域包对外；跨域不直读因子内部。
3. 占位安全方向单调：只能挡/缩/早退，绝不放松门或夸大机会。
4. 占位可见：面板/日志/注册表标「未标定」，不伪装生产能力。
5. 提升留痕：PLACEHOLDER→LIVE 需真实数据标定 + 注册表翻态 + 经验日志一行。

## 5. 进度
- **demo v0.1（地基轮 ✅）**：收束分层架构 + 缺口规范 + 因子注册表 + 可运行脊柱（自检 PASS：31 因子 / LIVE 23·PLACEHOLDER 7·OFFLINE 1）+ 两 FMZ 构建计划。
- **demo v0.2（审计 codex + 复用纠偏 ✅）**：审计 codex 17 版推进（[审计结论](审计/CODEX推进审计_v0.1.md)）——契约骨架**扎实可复用**、宏观**跑偏**(mock 未接真实基线，执行 bundle 569 行 vs 真实 2043 / 信号 151 vs 7297)；移植 5 个契约模块进本路径并验证（`verify_integration_core.py` 全 PASS，含真实 VRP 双门）；[执行层整合设计_v0.2](execution_build/执行层整合设计_v0.2.md) 锚定真实基线 + R1-R6 序列。
- **demo v0.3（R1 KPF 减法 · 真实代码 ✅）**：在真实执行/对冲基线副本（`execution_build/realsrc/`，源仓库只读）实改删 KPF（src 6 + tests 5，30 处编辑）；**真实 `tests/run_all.py` 60 passed / 0 failed**；KPF 功能性残留 0；对冲两项持续性 + `position_risk.v0.4`；权重归一 {0.375,0.375,0.25}。
- **demo v0.4（R2 执行会话骨架 · 真实数据 ✅）**：`session_core` 纳入 bundle src；`test_session_flow` 用**真实 `_build_menu` 方案**驱动 ExecutionSession（plan_hash 防重排 / 显式授权 / TTL 过期）；**63 passed / 0 failed**。
- **demo v0.5（R3 VRP 嵌入+收口 canonical ✅）**：`vrp_gate.py`（4 原语收口）；**与封版快照等价性测试零漂移**；PRICE_GATE 过滤真实菜单。
- **demo v0.6（R4 缺口域 + R5 对冲并入 + R6 合成两份真 FMZ ✅）**：risk_controls/hedge_watch 接入 + EntryRiskAnchor VRP 血缘 + `integrated_plan_preview`；**执行 FMZ `execution_build/nrd_execution_fmz.py`(3145 行) + 信号 FMZ `signal_build/nrd_signal_fmz.py`(8127 行) 已合成**，编译/契约/0 KPF/factor_spine 全过，**执行层真实回归 80 passed/0 failed**。
- ✅ **R 系列完成。续做见 [`HANDOFF.md`](HANDOFF.md)（策略版本优化与迭代）。**

## 6. 下一步（R 系列已完成 → 策略优化迭代，见 [`HANDOFF.md`](HANDOFF.md)）
R1-R6 全落地（真实基线焊接整合层，两份真 FMZ 已合成、80 真实测试绿）。后续在**新窗口**按 HANDOFF §7 推进：
- **A 闸前硬前提（blocked on 真实数据）**：信号 P0 校准 / 全链扣成本净 P&L 回放 / VRP 卖方 edge。
- **B live 接线 spike**：信号桥字段映射核对 / main() 会话化（FMZ 命令栏交互 spike）/ VRP IV-RV 实时 feed / 最简本机总线。
- **C 占位因子标定**：组合预算·熔断·sizing / 止盈·末日Gamma / 最小归因（先实现后标定 → 翻 LIVE）。
> 迭代铁律：注册表登记 → 改 realsrc 真实代码 → 跑真实回归 + factor_spine → 重合成两份 FMZ → 追加流程日志一轮。源仓库只读、不污染。

## 7. 不污染保证
demo 只在本文件夹内新建/演进；原仓库 `中性回路 - opus4.8` / `Deribit期权交易执行层` / `系统总纲\VRP` **只读、不改**。两份 FMZ 在 demo 内构建完成、验证通过后，是否回灌各源仓库由用户决定。
