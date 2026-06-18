# 中性回路 - opus4.8 项目审计报告

审计日期：2026-05-30
审计范围：项目资产盘点 + 当前进度核实 + 缺口识别（本轮不改动代码）
审计方式：文档通读 + 实际编译 + 离线 fixture 端到端运行核实（非仅凭文档）

---

## 0. 一句话结论

代码本体健康，与 v0.4.1 设计高度一致、可编译可运行；问题集中在三处：
**新旧逻辑接缝（策略推荐未接入新门控）**、**外围文档与源稿漂移**、**尚未真正进入真实观测阶段**。

---

## 1. 资产盘点

### 1.1 根目录

| 文件 | 性质 | 状态 |
| --- | --- | --- |
| `soul.md` | 项目迭代宪法（系统论/熵减/模块边界/四步法） | **部分过时**：§3 仍列 8 模块全链路，§9-11 仍按 v0.8/v0.9 设计轮叙事，与当前窄化范围分叉 |
| `neutral_regulation_premium_model_design_final.md` | 封版设计稿 | 设计档案 |
| `draft0.1~0.8` 系列（9 份） | 历史草稿 | 设计档案，可归档 |
| `neutral_regulation_demo_spec_v0.1.md` | demo 规范 | 历史 |
| `PROJECT_MEMORY.md` | 项目记忆 | **严重过时**：写 v0.21 / 8 模块；文件索引指向不存在的 `binance_client.py`、`deribit_client.py`、`run_runtime_validation.ps1` |
| `neutral_regulation_demo_fmz.py` | **当前权威 FMZ 单文件** | v0.4.1，310KB，与 demo/ 同步 |
| `neutral_regulation_demo_fmz_v0_11/_v0_2/_v0_21.py` | 旧单文件 | 已被取代，可归档 |
| `ENVIRONMENT.md` / `.gitignore` | 环境说明 | 现行 |

### 1.2 `add/`（新因子需求源稿）

| 文件 | 落地情况 |
| --- | --- |
| `M-DIE_v1.1_final_...md` | 已落地 `demo/factors.py::compute_m_die` |
| `宏观要素.md`（MPF v1.0） | 已落地 `demo/macro_factor.py`，但**源稿与实现不一致**（见 3.C） |
| `neutral_repair_presignal_v1_0_requirements.md` | 已落地 `demo/neutral_repair.py` |
| `bias_thesis_arbiter_v1_0_design.md` | 已落地 `demo/bias_thesis.py` |

### 1.3 `demo/`（多文件源码，权威实现，23 个 .py / 8140 行）

- 运行时：`main.py`(845)、`config.py`(301)
- 模块：`modules.py`(External Gate/Anchor/TMV-F)、`factors.py`(TMV-F/M-DIE/micro-flow)、`macro_factor.py`(MPF)、`neutral_repair.py`(DIE+Anchor 状态机)、`bias_thesis.py`(倾向论证层)、`signal_events.py`、`strategy.py`、`decision.py`
- 基础设施：`gex_adapter.py`、`binance_adapter.py`、`deribit_adapter.py`、`bar_assembler.py`、`http_client.py`、`data_sources.py`、`charting.py`、`recorder.py`(状态栏)、`contracts.py`、`schemas.py`、`vocabulary.py`、`utils.py`
- 文档：`README/SCHEMAS/VALIDATION/DELIVERY_SUMMARY/FORWARD_DESIGN_V0.4/LOGIC_REVIEW_V0.4` —— **均已更新到 v0.4.1**
- 历史稿：`FORWARD_DESIGN_V0.3.md`、`CHAIN_AUDIT_PROGRESS.md`（v0.1 8 模块审计，所述模块已删）

### 1.4 `docs/superpowers/`

- `specs/2026-05-27-v041-...-design.md` + `plans/2026-05-27-v041-...md` —— v0.4.1 最新设计与实施依据。

### 1.5 `tools/`（6 个 PowerShell）

`build_fmz_single` / `static_validate_demo` / `fmz_preflight_demo` / `runtime_check_demo` / `update_delivery_summary` / `setup_environment`

---

## 2. 当前进度（已核实）

实际编译并运行离线 fixture，确认：

- ✅ **编译通过**。**Python 3.12.10 现已可用**（路径 `C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe`）—— PROJECT_MEMORY 中"本机无 Python、无法做运行期验证"的阻塞**已消除**。
- ✅ **主链路 = 3 模块**：External Gate / Anchor / TMV-F。Premium/Skew、Combo Risk、Depth/Cost、执行型 Decision、Post-Entry **已按 V0.4 设计删除**，未回流。
- ✅ **FactorSnapshot 全字段在位**：anchor / flow / m_die / macro_pressure / neutral_repair_signal / bias_thesis / signal_events / strategy_recommendation。
- ✅ **状态栏 8 表全部渲染**：总览、倾向论证层、信号事件、数据源与定时、四步法、主链路模块、宏观要素、因子输出与策略推荐。MPF 三组件原始读数（VOLQ/DXY/US10Y 当前值/参考值/3d%/bps/贡献/分层）均按要求展示。
- ✅ **FMZ 单文件同步**：`demo_version=0.4.1`、`schema_version=nrd.schema.v0.4.1`、无残留 `from demo.` 导入。
- ✅ **只读边界完好**：`read_only_demo=True`、不配腿、不下单、执行层标记 `external_execution_program`。

离线 fixture 实测输出样例：
- module_results：External Gate=Caution / Anchor=Valid / TMV-F=Bullish
- neutral_repair_signal：`NR_DISPLACEMENT_ACTIVE`
- bias_thesis：`NO_TRADE_BLOCKED`（precondition 未激活，confidence 0）
- decision：`Observe`（软门控 / READ_ONLY_DEMO）

---

## 3. 关键发现 / 缺口

### 🔴 A. 内部一致性缺口（最重要）：策略推荐未接入新门控

`demo/strategy.py:30 build_strategy_recommendation()` 仅依据 TMV-F 方向输出 Put/Call Credit Spread，**完全绕开** `neutral_repair_signal.is_active` 与 `bias_thesis`。

- 实测：本轮 NR=`NR_DISPLACEMENT_ACTIVE`、bias=`NO_TRADE_BLOCKED`，但顶层"策略推荐"仍输出 `Put Credit Spread`。
- `add/neutral_repair_presignal_v1_0_requirements.md` §9.3 明确要求把它标注为 `legacy_tmvf_direction_preview`，**至今未做**，`selection_reason` 仍是裸的 `TMVF_DIRECTION`。
- 后果：状态栏会同时出现"论证层阻断"与"推荐做 Put Credit Spread"，自相矛盾，违背 soul.md 的熵减/解释闭环原则。
- 修复方向（待定，本轮不动）：要么把 strategy_recommendation 显式降级为 legacy 预览并在 UI 标注、要么真正接入 DIE+Anchor→BiasThesis 门控后再放行方向。**注意 soul.md 要求改主流程前先加红灯断言。**

### 🟡 B. 文档漂移（误导风险）

下列资产仍停留在"全链路执行系统"叙事，与当前"前置信号 + 策略制定层"实现分叉：

- `PROJECT_MEMORY.md`：v0.21 / 8 模块 / 文件索引失效。
- `demo/CHAIN_AUDIT_PROGRESS.md`：v0.1 8 模块审计，所述 Premium/Skew、Combo Risk、Depth/Cost、Decision 执行、Post-Entry 均已删。
- `demo/FORWARD_DESIGN_V0.3.md`：已被 V0.4 取代。
- `soul.md` §3 模块清单与 §9-11 v0.8/v0.9 路线。

建议（待执行）：刷新 PROJECT_MEMORY 到 v0.4.1，并在历史稿顶部加"历史语境，非当前契约"标注（LOGIC_REVIEW_V0.4 §残留注意已部分提示）。

### 🟡 C. MPF 源稿与实现不一致

`add/宏观要素.md`（v1.0）仍描述**旧的固定 bps 分层 + 权重 VOLQ 0.42 / DXY 0.33 / US10Y 0.25**；
而 v0.4.1 实现（per spec）用 **tanh 尺度归一化 + 权重 0.35 / 0.25 / 0.40 + US10Y bps 修正**。
风险：若有人据 `add/宏观要素.md` 重新生成 MPF，会回退 v0.4.1 改进。建议给该源稿加版本注记或同步。

### 🟢 D. 阈值仍为 `relaxed_test`

`config.py`：`nr_mdie_event_on_abs=0.65`、`nr_anchor_repair_score=60`。设计文档规划生产值为 DIE>0.80 / repair≥70。
观察期有意为之，**但应作为"待观察日志后收紧"的显式开关跟踪**，避免误当生产阈值。

### 🟢 E. 尚无真实运行观测数据

`demo/logs/decisions.jsonl`、`snapshots.jsonl` 存在，但 v0.4.x 的核心目的（冷启动观测 → 反推哪些假设需修正）尚未对真实 FMZ/live 行情跑过。
这正是 soul.md 反复强调的"先观测、别堆因子"。**在缺观测数据前不宜继续扩因子。**

---

## 4. 后续方向备选（等用户明确需求后再启动）

1. **收口内部一致性（A+B+C）** —— 修策略推荐门控缝、刷新 PROJECT_MEMORY/soul/旧 FORWARD/add 源稿到 v0.4.1。契合 soul.md 当前"第一性收口"阶段。
2. **进入真实观测（D+E）** —— 用真实 FMZ/live 公开数据跑起来，捕捉日志，复盘 NR episode 与 Bias 阈值是否按预期触发，再决定是否收紧到生产阈值。
3. **向前推进 KPF 空间层** —— 按 bias_thesis 文档预留的下游，新增 KPF（关键流动性密集区/空间接近度）。**soul.md 警告：观测数据不足前别先堆因子，此项优先级应低于 2。**

---

## 5. 验证备注

- 本机可直接用 `...\Python312\python.exe -m compileall <abs_path>\demo` 编译，用 `python -c "import demo.main as m; m.run_offline_fixture_once()"` 跑离线烟雾。
- 当前环境下 PowerShell `-ExecutionPolicy Bypass` 被安全策略拦截，`tools/*.ps1` 验证脚本需用户自行运行（或调整权限）；审计用直接调用解释器的方式绕过此限制完成。
