# 中性回路整合工程

> 建立日期：2026-06-02
> 用途：**整合工作的唯一权威入口**。汇集三模块 + VRP 门的最新交付物快照与逐因子文档，供后续推进整合时引用——**不再参考散落的过期草稿**。
> 当前 xxproject 备份标记：`NRD-XXPROJECT-BACKUP-2026.06.22-r3.0.1`。
> **当前权威总纲：[`00_总纲/中性回路总系统整合设计总纲_v0.5.md`](00_总纲/中性回路总系统整合设计总纲_v0.5.md)**（执行会话式整合 + 四缺口补全路线，2026-06-02）。v0.4(VRP门)/v0.3(三模块)降为历史基稿；思路来源 = [`整合思路方案参考稿_v0.1`](00_总纲/中性回路整合思路方案参考稿_v0.1.md)。
> **整合落地沙箱：[`demo/`](demo/README.md)**（demo v0.1 地基轮：31 因子收束分层架构 + 因子注册表脊柱 + 缺口「先实现后标定」规范；最终产两份 FMZ，不污染原仓库）。

---

## 0. 一句话总纲
中性回路总系统 = **证据驱动的分段式卖方风险压缩流程**：
**① 中性回路**确认窗口与方向 → **②-门 VRP** 在建仓前确认这个窗口/侧在实时 IV 上扣完整摩擦后仍有厚净权利金（不够贵就 no-trade）→ **② 执行层**把通过双门的方向在 DTE/Delta 硬范围内转成可复核、可空跑、可记账的带保护腿结构 → **③ 对冲模块**只在持仓后尾部恶化且期权退出更差时给干跑期货腿压尾意图。

---

## 1. 当前权威状态（各模块版本 / 最新交付物 / canonical 源）

| 模块 | 版本（2026-06-22 校准） | 最新交付物 | canonical 源根目录 |
|---|---|---|---|
| ① 信号层 · 中性回路 | `demo 1.4.0 / schema nrd.schema.v1.0.0`，只读观察 + 信号卡审计收口 | `neutral_regulation_demo_fmz.py` | `demo/最新交付物/neutral_regulation_demo_fmz.py` |
| ② 执行层 · 选腿+下单+记账 | `STRATEGY_VERSION 2.7.0`，`ALLOW_TRADING=False` | `spm_calendar_protected_short_v1.py` | `demo/最新交付物/spm_calendar_protected_short_v1.py` |
| ②-门 VRP · 建仓前定价门 | `VRP_FACTOR_VERSION 1.1.0`，阶段封版，**未嵌入执行层** | `VRP/`（src+docs） | `<local-vrp-workspace>` |
| ③ 对冲模块 · 持仓后压尾 | 设计 v1.0 / 代码 `position_risk.v0.3`（目标 v0.4），`DRY_INTENT_ONLY` | `src/hedge_risk.py` | `<local-deribit-execution-workspace>\src\hedge_risk.py` |
| ~~KPF / SLRP~~ | **已取消、移出系统**（封存研究资产） | — | `中性回路 - opus4.8\kpf\`、`Documents\kpf`（非运行层） |

> 冲突时优先级：① 最新交付物代码的版本号/配置/字段/状态机 → ② 最新报告目录产物 → ③ 与代码同步的 README/VALIDATION → ④ 已验证审计 → ⑤ 历史设计稿（总纲 v0.3 §12）。

---

## 2. 整合架构

```
公共行情 → ① 信号 FMZ（中性回路 v1.4.0，只读）
              → SignalEvidencePackage（时序 DIE+Anchor + 方向 EDB + GGR；DIRECTION_BIAS 定侧）

SignalEvidencePackage(放行) + Deribit 实时期权链(IV/Greeks) + 自算 RV(perp 1h)
  → ② 执行 FMZ · PLAN
       ├─[VRP 窗口门] EDB 背书 expiry/侧：assess_window（BLOCK/DISTORTED→不进枚举）
       ├─ 只在 PASS 窗口枚举候选（DTE/Delta 硬范围）
       ├─[VRP 候选门] 每条垂直：assess_candidate（扣 full-burn 净 edge，BLOCK→剔除）
       └─ 双门 PASS → S:PM/既有 PLAN 排序 → ExecutionPlanPackage（含 vrp_* 字段）
  → ② ORDER → PositionLedgerPackage + EntryRiskAnchor（含 VRP 入场血缘）
  → ③ 对冲模块（驻执行 FMZ）→ PositionRiskPackage / HedgeIntentPackage（DRY_INTENT_ONLY）
```

**独立 AND 双门**：VRP 与 EDB/GGR/宏观都过才交易；VRP 只过滤、不判方向、不选期、不进主排序权重、不解 `ALLOW_TRADING`。

> **v0.5 路线升级**：执行层从「重启式 PLAN/ORDER」升级为**单一 ExecutionSession 状态机**（IDLE→SIGNAL_OBSERVED→PRICE_GATE→PLAN_READY→PLAN_LOCKED→APPROVAL_INTENT→ARMED_PREVIEW→ORDER_COMMITTING→POSITION_OPEN→POSITION_MANAGE‖HEDGE_WATCH→EXIT_OR_REUSE→CLOSED），VRP/对冲落为其子域。详见 [总纲 v0.5 §4/§6](00_总纲/中性回路总系统整合设计总纲_v0.5.md)。

---

## 3. 目录导览

```
中性回路整合工程/
├── README.md                         ← 本文件（权威入口 + 过期资料黑名单）
├── 00_总纲/                          ← 总纲 v0.5(权威) + v0.4/v0.3(历史基稿) + 思路参考稿 v0.1
├── 01_信号层_中性回路/
│   ├── 交付物快照/                   ← 单文件 + 权威文档 + 因子卡_add
│   └── 因子文档/                     ← 00_总览 + 10 因子
├── 02_执行层_Deribit/
│   ├── 交付物快照/                   ← 单文件 + 设计稿 + README
│   └── 因子文档/                     ← 00_总览 + 5 因子
├── 03_VRP门_建仓前定价/
│   ├── 交付物快照/                   ← src + README + docs(封版/收口/模拟)
│   └── 因子文档/                     ← 00_总览 + 4 因子
├── 04_对冲模块/
│   ├── 交付物快照/                   ← hedge_risk.py + 设计稿 + 模拟审计基准
│   └── 因子文档/                     ← 00_总览 + 4 因子
└── demo/                            ← 整合落地实验沙箱（demo v0.1 地基轮）
    ├── 设计/                        ← 收束分层架构 + 缺口实现规范 + 因子注册表
    ├── shared/                      ← factor_registry.json(脊柱) + factor_spine.py(可运行自检)
    ├── signal_build/ execution_build/ ← 两份 FMZ 构建区(计划)
    └── 实现流程与经验/              ← 每轮迭代日志
```

---

## 4. 因子总表（23 篇因子文档）

### ① 信号层（[总览](01_信号层_中性回路/因子文档/00_信号层总览.md)）
| 代号 | 文档 | canonical |
|---|---|---|
| External Gate | [01](01_信号层_中性回路/因子文档/01_External_Gate_外部数据门.md) | `demo/modules.py:evaluate_external_gate` |
| Anchor | [02](01_信号层_中性回路/因子文档/02_Anchor_GEX锚.md) | `demo/modules.py:evaluate_anchor` |
| TMV-F | [03](01_信号层_中性回路/因子文档/03_TMV-F_趋势动量波动因子.md) | `demo/factors.py:compute_tmvf_profile` |
| M-DIE | [04](01_信号层_中性回路/因子文档/04_M-DIE_短期单向位移因子.md) | `demo/factors.py:compute_m_die` |
| NeutralRepair | [05](01_信号层_中性回路/因子文档/05_NeutralRepair_时序门.md) | `demo/neutral_repair.py` |
| **EDB**（权威方向层） | [06](01_信号层_中性回路/因子文档/06_EDB_到期方向合成层.md) | `demo/edb.py:evaluate_edb` |
| SRD | [07](01_信号层_中性回路/因子文档/07_SRD_偏斜方向因子.md) | `demo/skew_factor.py` |
| GGR | [08](01_信号层_中性回路/因子文档/08_GGR_全局Gamma区制门.md) | `demo/gamma_regime.py` |
| MPF | [09](01_信号层_中性回路/因子文档/09_MPF_宏观压力因子.md) | `demo/macro_factor.py` |
| signal_events | [10](01_信号层_中性回路/因子文档/10_signal_events_信号事件记录.md) | `demo/signal_events.py` |

### ② 执行层（[总览](02_执行层_Deribit/因子文档/00_执行层总览.md)）
| 组件 | 文档 | canonical |
|---|---|---|
| leg_selection | [01](02_执行层_Deribit/因子文档/01_leg_selection_选腿.md) | `src/leg_selection.py` |
| plans 排序/价值 | [02](02_执行层_Deribit/因子文档/02_plans_排序与价值指标.md) | `src/plans.py` |
| spm_sim | [03](02_执行层_Deribit/因子文档/03_spm_sim_保证金释放.md) | `src/spm_sim.py` |
| execution | [04](02_执行层_Deribit/因子文档/04_execution_执行纪律.md) | `src/execution.py` |
| ledger+EntryRiskAnchor | [05](02_执行层_Deribit/因子文档/05_ledger_账本与EntryRiskAnchor.md) | `src/ledger.py` + `src/hedge_risk.py` |

### ②-门 VRP（[总览](03_VRP门_建仓前定价/因子文档/00_VRP总览.md)）
| 因子 | 文档 | canonical |
|---|---|---|
| 窗口门 | [01](03_VRP门_建仓前定价/因子文档/01_窗口门_assess_window.md) | `vrp_model.py:assess_window` |
| 候选门 | [02](03_VRP门_建仓前定价/因子文档/02_候选门_assess_candidate.md) | `vrp_model.py:assess_candidate` |
| forward_vol_hurdle | [03](03_VRP门_建仓前定价/因子文档/03_forward_vol_hurdle.md) | `vrp_model.py:forward_vol_hurdle` |
| BS pricer | [04](03_VRP门_建仓前定价/因子文档/04_BS_pricer.md) | `vrp_model.py:black_scholes_price_usd` |

### ③ 对冲模块（[总览](04_对冲模块/因子文档/00_对冲模块总览.md)）
| 因子 | 文档 | canonical |
|---|---|---|
| 五决策变量 | [01](04_对冲模块/因子文档/01_五决策变量.md) | `hedge_risk.py:evaluate_position_risk` |
| persistence | [02](04_对冲模块/因子文档/02_persistence_持续性.md) | `hedge_risk.py:persistence_score` |
| 状态机 | [03](04_对冲模块/因子文档/03_状态机.md) | `hedge_risk.py:_state_from_inputs` |
| 触界概率 | [04](04_对冲模块/因子文档/04_触界概率.md) | `hedge_risk.py:estimate_touch_probability` |

---

## 5. 契约包（模块间唯一合法耦合面，总纲 v0.3 §6 / v0.4 §4）

| 包 | schema | 生产者 → 消费者 |
|---|---|---|
| SignalEvidencePackage | `nrd.integration.signal.v0.1` | ① → ② / ③ |
| VrpGatePackage（WindowAssessment + CandidateAssessment） | 封版 v1.1 | ②-门（PLAN 轮） |
| ExecutionPlanPackage | `nrd.integration.execution_plan.v0.2` | ② PLAN（删 kpf_score、加 vrp_*） |
| PositionLedgerPackage + EntryRiskAnchor | `nrd.integration.position_ledger.v0.1` | ② ORDER → ③（加 VRP 血缘） |
| PositionRiskPackage | `nrd.integration.position_risk.v0.4` | ③（删 KPF 持续性项） |
| HedgeIntentPackage | `nrd.integration.hedge_intent.v0.1` | ③（仅 HEDGE_READY，DRY_INTENT_ONLY） |
| ~~KpfSpatialPackage~~ | **已删除** | — |

> **v0.5 新增契约包**（详见 [总纲 v0.5 §7](00_总纲/中性回路总系统整合设计总纲_v0.5.md)）：`ExecutionSessionPackage`（会话中枢，session_id 串包）、`ApprovalIntentPackage`（plan_hash+TTL，取代重启授权）、`PortfolioRiskBudgetPackage`（组合硬预算，缺口2）、`PositionManageDecision`（止盈/末日Gamma，缺口3）、`AttributionPackage`（最小归因，缺口4）、`ReplayExpectationPackage`（全链净P&L回放，缺口1）。

---

## 6. ⚠️ 过期资料黑名单（不要再参考）

> 这是本工程的核心价值之一。以下资料**已被取代/退役**，推进整合时一律不引用；以 canonical 代码 + 总纲 v0.4 + 本工程因子文档为准。

### 6.1 KPF / SLRP（整条取消，总纲 v0.3 §2）
- 任何把 KPF 作为系统层/空间证据层/选腿增量因子/方向或触界输入的叙事。
- `中性回路 - opus4.8\kpf\`、`Documents\kpf`（封存为研究资产，**非运行层**）、KPF 服务器版、AWS KPF 服务、`/latest` API、`KpfSpatialPackage`、KPF 现价重锚与跨场基差、SLRP EV 因子。
- 执行层/对冲代码中的 KPF 残留是 **Phase 1 待删项**（各因子文档已逐条标源位置），不是"现行设计"。

### 6.2 信号层过期稿（`中性回路 - opus4.8\`）
- `neutral_regulation_premium_model_draft 0.1`~`0.8` + `_design_final.md`（旧 premium model 草稿系列）。
- `neutral_regulation_demo_spec_v0.1.md`、`demo/FORWARD_DESIGN_V0.3.md`、`demo/FORWARD_DESIGN_V0.4.md`、`demo/LOGIC_REVIEW_V0.4.md`、`demo/MAIN_CHAIN_LOGIC_REVIEW_V0.1.md`、`demo/CHAIN_AUDIT_PROGRESS.md`、`docs/superpowers/specs|plans/2026-05-27-v041-*`。
- `add/bias_thesis_arbiter_v1_0_design.md`（**bias_thesis 已 v0.51 退役**，未复制进本工程）。
- 冻结快照 `neutral_regulation_demo_fmz_v0_11/v0_2/v0_21.py`（仅查史用，非交付物）。
- 当前权威信号文档 = `demo/FORWARD_DESIGN_V0.5_EDB.md` / `LOGIC_REVIEW_V0.5_EDB.md` / `CALIBRATION_PLAN_V0.5.md` / `PROJECT_MEMORY.md`（已复制进 01 交付物快照）。

### 6.3 总纲历史稿（`系统总纲\`）
- `中性回路模型总纲流程设计稿_v0.1.md`（含 §14 v0.2 增补）——降为历史背景。
- `中性回路总系统整合设计总纲_v0.3.md`——**历史基稿**：v0.4 沿用其未受 VRP 影响章节（KPF/SLRP 取消、对冲、拓扑、上线闸门、不变量、过期处理），故仍需对照阅读，但**当前权威是 v0.4**。
- 旧叙事：v0.21/v0.4.1 的 8 模块全链路、已删除的 Combo Risk / Depth Cost / Post-Entry 内置模块。

### 6.4 执行层 / VRP 局部作废段
- 执行层设计稿 `deribit_spm_calendar_protected_short_v1_0_design.md` 的 §0/§3.1/§5.2/§6.3/§13 KPF 选腿/`kpf_context` 语言（文档其余有效）。
- `INTEGRATION_HANDOFF.md` 的 KPF 三层叙事、`PROJECT_MEMORY.md §16` 旧"与下游 KPF 整合"句。
- VRP `docs/VRP执行层集成交接说明_v1.0.md` §5 宽松配置——被 `VRP执行层整合收口契约_v1.1.md` 取代，canonical = `vrp_policy.py`（未复制 v1.0 交接说明进本工程）。

---

## 7. 整合落地路线与当前阻塞（v0.5 路线，详见 [总纲 v0.5 §9](00_总纲/中性回路总系统整合设计总纲_v0.5.md)）

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 接口冻结（会话/授权/VRP/对冲/四缺口最小包） | ✅ 本稿定 |
| 1 | **减法落地**：删 KPF（执行层 6 处 + 对冲）、对冲升 `position_risk.v0.4` 两项持续性 | ⏳ **待落地**（代码仍含 KPF） |
| 2 | 执行会话体验重构：`ApprovalIntent`(plan_hash+TTL) 取代重启式 ROUND_MODE；前置 **FMZ 交互/持久化 spike** | 未开始 |
| 3 | VRP 只读嵌 PLAN + 4 原语收口 canonical | 未开始 |
| 4 | 账本/EntryRiskAnchor 增强(session_id 串包) + 最小归因(缺口4) | 未开始 |
| 5 | 组合硬风险预算(缺口2) + 基础赢家管理 止盈/末日Gamma(缺口3) | 未开始 |
| 6 | **全链扣成本净 P&L 回放(缺口1，闸前硬前提)** | 未开始 |
| 7 | 闸 A 管道验证 → 信号 P0 校准 → 闸 B 小额实盘(期权) | 未开始 |
| 8 | 对冲实盘开关(独立) + VRP edge 验证(多时点 IV) | 未开始 |

**两条并行数据轨（gate 闸 B，blocked on 真实数据，非代码问题）**：轨甲=信号 P0 校准(实盘 snapshots ≥2-4 周 → production profile)；轨乙=VRP edge(多时点 IV 成交级回放)。

**上线门槛**：三开关（`ALLOW_TRADING` 期权默认 False / `ALLOW_HEDGE_TRADING` 期货腿 Phase 3 独立 / 全局 kill-switch）+ 两闸（闸 A 管道验证可校准前做；闸 B 信号驱动须信号层 P0 校准 + `nr_threshold_profile` 由 `relaxed_test` 换 `production`）。

**当前最大阻塞**：① 信号层置信标定待实盘复观（v1.4.0 收口后）；② VRP 卖方 edge 未证（待多时点 IV 前向采集）。两者都 **blocked on 真实前向数据**，非代码问题。

---

## 8. 快照与源仓库关系（重要）
- `交付物快照/` 内文件是 **2026-06-02 的只读时点快照**，用于自包含阅读与对照。
- **canonical 权威永远是各源仓库**（§1 表）。活跃开发（尤其 Phase 1 减法）改的是源仓库，不是本工程快照。
- 推进整合时：**读本工程因子文档理解现状与改法 → 改源仓库代码 → 回归测试 → 视需要回刷本工程快照与因子文档**。
- 本工程不复制各模块 dev src 全树（避免与源仓库分叉），仅快照交付物级工件 + VRP src（独立 harness）。
