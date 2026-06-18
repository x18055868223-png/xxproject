# 中性回路项目记忆

更新时间：2026-05-31（阶段封版 v0.5.3）

本文档用于后续迭代快速恢复上下文。**当真实代码 / 用户最新指令与本文档冲突，以代码和用户指令为准并回写本文档**（见 §19）。
本轮已对齐到当前现实：删除了 v0.21 时代的 8 模块全链路叙事（Premium/Skew、Combo Risk、Depth/Cost、执行型 Decision、Post-Entry 已在 v0.4 移除），版本与文件索引刷新到 v0.5.3。

## 0. 当前状态（v0.5.3，权威）

- **定位**：面向短周期加密期权的中性调节模型的**前置信号 + 策略制定层**。只读观察，不选腿/不报价/不下单；具体腿/数量/下单交**外部执行程序**。No Trade 是有效输出。
- **版本**：`demo_version = 0.5.3`，`schema_version = nrd.schema.v0.5.3`。开发态=多文件包 `demo/`；交付物=单文件 `neutral_regulation_demo_fmz.py`（与包同步，CRLF+BOM）。
- **主链路（3 模块）**：`External Gate → Anchor → TMV-F`（`MODULE_SEQUENCE` 固定，进 `module_results`）。
- **因子 / 前置信号层（不在 module_results，进 FactorSnapshot）**：
  - **MPF** 宏观压力（VOLQ/DXY/US10Y 多日顺逆风，小时刷新 + LKGV 缓存）。
  - **M-DIE** 15m 短期位移。
  - **NeutralRepair（DIE+Anchor）** 修复前置信号状态机 = **时序门**（决定窗口"何时"开，其 UP/DOWN 是噪声、非方向）。
  - **EDB** 到期窗口方向合成层 = **权威方向层**：6 证据（TMV、CVD×价格 4h/12h、MACRO、FUNDING、SRD、GGR 空间钉）加权后验，`EDB_score=Σvote·eff_w/Σeff_w`，`置信=100·|EDB|·一致度·覆盖度·GGR乘子`（v0.5.3 起含信息量加权 eff_weight 与覆盖度折扣）。
  - **SRD** 25Δ 风险逆转方向票（相对偏斜 rr_z + 动量 ΔRR，**不用原始符号**，BTC 偏斜结构性为负）。
  - **GGR** 全局 Gamma 区制：**首先是单边卖权安全门**（负 Gamma 放大→砍/否决），其次置信调制，最后钉住区给小空间票。
  - **strategy_recommendation**：方向来自 EDB（有可交易 lean 时），否则回落 TMV-F 但标注 `TMVF_LEGACY_PREVIEW`；输出 signal / 24h+48h 期号 / strategy_type；执行外置。
  - **signal_events**：最近 10 个已确认 DIE+Anchor 信号事件。
- **bias_thesis**：v0.51 起退役为 legacy 共享 verdict helper（被 EDB 复用算 macro/funding 票），独立 arbiter 已删，不再是权威方向层。
- **日志**：信号生成时输出一次「信号综述」（四步法内核但不点名：情况/倾向/目标/策略，见 `recorder.build_signal_brief`）；平时 tick 只「观察摘要」。v0.5.2 起移除显式「四步法归纳」表。

**权威文档（看这些，不要只看本记忆）**：`demo/FORWARD_DESIGN_V0.4.md`、`demo/FORWARD_DESIGN_V0.5_EDB.md`、`demo/LOGIC_REVIEW_V0.4.md`、`demo/LOGIC_REVIEW_V0.5_EDB.md`、`demo/CALIBRATION_PLAN_V0.5.md`、`AUDIT_REPORT_2026-05-30.md`、`soul.md`（迭代宪法）。

## 1. 项目定位

中性回路是面向短周期加密期权的中性调节模型。当前阶段目标不是证明因子正确、也不是直接追运行期收益，而是把数据接口、主链路顺序、模块边界、方向合成逻辑、日志/状态栏观察方式搭清楚，让真实观察结果反推哪些假设需修正。

核心交易假设：在特定窗口内，用小资金、固定风险百分比、短周期、单侧保护性 Credit Spread，捕捉高补偿但风险可控的期权机会。No Trade 是有效输出。

## 2. 不可破坏的约束

- demo 默认只读观察，不能真实下单；`read_only_demo=True` 是安全边界。
- 不裸卖期权；不默认 Iron Condor。
- 本层**不选腿、不报价、不下单**，只出 signal / 期号 / strategy_type，执行交外部程序。
- 不为了"看起来更完整"加复杂逻辑；不把因子分数当已验证 alpha。
- 不把当前阶段提前推成实盘执行系统。
- 新增模块/因子前必须回答：它减少了哪个具体不确定性，且是否足以改变决策。
- 守 v0.4 边界：不改 `MODULE_SEQUENCE`，方向类只在前置信号层。

## 3. 解释口径（四步法内核）

日志总结与人工复盘仍按四步法内核组织，但 v0.5.2 起**不再单列「四步法」表**，只在信号成立时由 `recorder.build_signal_brief` 输出一次「信号综述」：

1. 情况：价格 / 锚 / DIE+Anchor 窗口 / 数据健康（只描述事实）。
2. 倾向：EDB lean / 置信 / 一致度 / 覆盖 / 证据清单（避免单因子直接定论）。
3. 目标：strategy_type + 24h/48h 期号。
4. 策略：strategy_code + 支持标签；**执行层外置**。

## 4. 主链路顺序

当前固定主链路（3 模块，进 `module_results`）：

`External Gate → Anchor → TMV-F`

- **External Gate**：外部权限、只读模式、基础数据源与事件边界。
- **Anchor**：判断中性锚是否可用（贴合度），不负责方向。
- **TMV-F**：到期窗口方向倾向 / 趋势质量 / 拥挤与反身性，作为 EDB 的主干证据之一，不独占方向决定权。

方向/择时/安全的其余判断在前置信号层（M-DIE / NeutralRepair / EDB / SRD / GGR / MPF，见 §0），**不进** `MODULE_SEQUENCE`。

> v0.21 时代的 8 模块全链路（…→ Premium/Skew → Combo Risk → Depth/Cost → 执行型 Decision → Post-Entry）**已在 v0.4 整段移除**，相关历史细节归档于旧版本快照 `neutral_regulation_demo_fmz_v0_21.py` 与 `AUDIT_REPORT_2026-05-30.md`，本记忆不再展开，避免误导。

## 5. 当前 Demo 状态（v0.5.3，阶段封版）

- 单文件交付：`neutral_regulation_demo_fmz.py`（开发态 `demo/` 包同步；旧版 `_v0_2/_v0_11/_v0_21.py` 为冻结历史快照，勿改勿信）。
- 已实现：FMZ 只读流程；中文状态栏 6 表（总览 / EDB 到期方向合成层 / 信号事件 / 数据源与定时 / 主链路与因子状态 / 宏观要素）；EDB 富表显示六证据原始数值；策略 Chart（实时价 / GEX 中轴 / Anchor 分 / TMV-F 分 / M-DIE）；信号综述日志。
- v0.5.3 关键：置信覆盖度修复（信息量加权 eff_weight + 覆盖度折扣，冷证据/缺失证据正确降置信）；**置信刻度自 v0.5.3 起与之前不可比，实盘分布重新起算**。
- 合理性自检（合成场景驱动 evaluate_edb）：置信阶梯 35/50/68 三档全可达且单调，GGR 否决/宏观阻断/时序窗口三门均正确触发，覆盖度折扣生效（见 `demo/CALIBRATION_PLAN_V0.5.md §6`）。
- 验证：`compileall` + 离线 fixture（包与单文件输出逐字一致，合约自检 0 错 0 警）全链绿。

## 6. Anchor 记忆

目标：判断中性锚是否有效、价格是否围绕锚有足够贴合度；**不判断多空方向**。
输入：GEX effective flip、GEX 源时间戳、已完成成交量柱 close、自适应锚带宽。
输出：`anchor_gravity_ref_score`（0–100）+ 分层标签（Warming/Detached/Loose/Attached/Tightly Attached）。
计算：归一化偏离取绝对值 → 维护窗口偏离历史 → `100·exp(-mean_abs)` 压成 0–100，分越高越贴锚。
暖机：依赖已完成的 10 BTC 成交量柱积累有效样本（出分快慢取决于成交速度，非固定分钟）。
历史教训：早期 Anchor 无效是 GEX payload 时间戳/价格字段解析不匹配（已修嵌套/ISO/秒毫秒），勿把 GEX 曲线节点的普通 `price` 当资产现价。
（具体常量以 `demo/config.py` 为准。）

## 7. TMV-F 记忆

目标：把趋势方向、质量、拥挤度、反身性压缩成 24h/48h 的倾向输出，作为 EDB 主干证据；不直接预测价格，不让 Funding 替代趋势。
输出：`tmv_blend ∈ [-1,+1]`（正偏多 / 负偏空 / 近 0 中性）。
架构：主口径等时间轴 Binance futures Kline（基础 1h，预留 2h/4h）；TMV core 用价格路径/动量/成交量覆盖 24h/48h；Funding Reflexivity 是修正层（只温和确认/提示拥挤/识别反向燃料，不独立造/翻方向）。
Micro Flow：等成交量柱 CVD/momentum 保留为 `micro_flow`（4h/8h/12h 视角），是 EDB 里 CVD×价格证据的来源；v0.5 起 `tmvf_micro_flow_direction_tilt=False`（CVD 独占流向，去 TMV-CVD 双计数）。

## 8. 已废弃模块与历史路线（v0.4 起移除，仅存档）

Premium/Skew（粗平均 IV）、Combo Risk、Depth/Cost、执行型 Decision（preview order_intent）、Post-Entry、以及 v0.3「候选组合集合评估」前瞻——**均在 v0.4 重构中移除**，被前置信号层 + 外置执行边界取代。细节见 `AUDIT_REPORT_2026-05-30.md` 与冻结快照。当前层**不**承担选腿/组合风险/深度成本/持仓后管理，这些属外部执行程序或后续工作线（见 §16）。

## 15. 重要文件索引（v0.5.3）

设计与规范（当前）：`soul.md`、`neutral_regulation_demo_spec_v0.1.md`、`neutral_regulation_premium_model_design_final.md`(设计轮封版稿)、`demo/FORWARD_DESIGN_V0.4.md`、`demo/FORWARD_DESIGN_V0.5_EDB.md`、`demo/LOGIC_REVIEW_V0.4.md`、`demo/LOGIC_REVIEW_V0.5_EDB.md`、`demo/CALIBRATION_PLAN_V0.5.md`、`AUDIT_REPORT_2026-05-30.md`。

历史设计档（仍在仓库，描述其各自版本期、勿当当前现实）：`demo/FORWARD_DESIGN_V0.3.md`、`demo/CHAIN_AUDIT_PROGRESS.md`、`demo/MAIN_CHAIN_LOGIC_REVIEW_V0.1.md`。

Demo 源码（`demo/`）：`config.py`、`main.py`、`modules.py`、`factors.py`、`decision.py`、`contracts.py`、`schemas.py`、`vocabulary.py`、`utils.py`、`recorder.py`、`charting.py`、`http_client.py`、`data_sources.py`、`bar_assembler.py`；适配器 `gex_adapter.py`、`binance_adapter.py`、`deribit_adapter.py`；因子 `macro_factor.py`、`neutral_repair.py`、`bias_thesis.py`(legacy helper)、`edb.py`、`skew_factor.py`、`gamma_regime.py`、`signal_events.py`、`strategy.py`。

Demo 文档：`demo/README.md`、`demo/SCHEMAS.md`、`demo/VALIDATION.md`、`demo/DELIVERY_SUMMARY.md`。

FMZ 单文件：`neutral_regulation_demo_fmz.py`（当前交付）；`_v0_2/_v0_11/_v0_21.py`（冻结历史，勿改）。

工具脚本（`tools/`）：`build_fmz_single.ps1`、`fmz_preflight_demo.ps1`、`static_validate_demo.ps1`、`runtime_check_demo.ps1`、`update_delivery_summary.ps1`、`calibrate_edb.py`(EDB/SRD/GGR 校准分布分析)。

> 注：旧索引里的 `demo/binance_client.py`、`demo/deribit_client.py`、`tools/run_runtime_validation.ps1` **当前不存在**——实际用 `binance_adapter.py` / `deribit_adapter.py` / `runtime_check_demo.ps1` 取代，已从索引移除。

## 16. 下一步工作线（封版后）

- **阈值实盘校准**：实盘累计 `demo/logs/snapshots.jsonl`（覆盖正/负 Gamma、宏观顺/逆风、不同 funding 区）后，用 `python tools/calibrate_edb.py --window-open-only` 出分布，按 `CALIBRATION_PLAN_V0.5.md` 定 P0 阈值（置信阶梯 + GGR 安全门）。当前 blocked on 实盘数据。
- **与后续模块衔接**：把本封版前置信号层与下游 **KPF 空间层 / 执行层**整合（仓库内已有 `kpf/` 项目资产）。属新工作线，宜在新对话做。

不应优先做的事：没有运行观察数据前别堆因子；别急着加真实下单；别为交易频率放松硬门控；别把 demo 阶段简化估算当实盘风控。

## 17. 迭代前检查清单

每次修改前确认：本次是设计审计 / demo 展示 / 因子返修 / 校准 / 实盘化准备中的哪一类；是否破坏只读安全边界；是否改 `MODULE_SEQUENCE`；是否让某模块越权；是否需同步单文件 FMZ；是否需更新 `demo/SCHEMAS.md`/`README.md`/`VALIDATION.md`/`DELIVERY_SUMMARY.md`；是否需回写本记忆。

## 18. 常用验证命令

> 本机已有 Python 3.12（`C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe`），运行期验证可直接做。`tools/*.ps1` 用 `-ExecutionPolicy Bypass` 在部分受限环境会被拦，可改用 python 直跑达到等效验证。

```powershell
# 编译 + 离线烟雾测试（等效运行期验证）
$env:PYTHONIOENCODING="utf-8"
& $py -m compileall "<绝对路径>\demo"
& $py -c "import demo.main as m; m.run_offline_fixture_once()"
# EDB/SRD/GGR 校准分布
& $py tools\calibrate_edb.py --window-open-only
# 单文件同步 / 预检 / 静态 / 运行期（受限环境可能被拦）
powershell -ExecutionPolicy Bypass -File tools/build_fmz_single.ps1 -Check
powershell -ExecutionPolicy Bypass -File tools/fmz_preflight_demo.ps1
powershell -ExecutionPolicy Bypass -File tools/static_validate_demo.ps1
powershell -ExecutionPolicy Bypass -File tools/runtime_check_demo.ps1
```

运行期验证失败若是环境问题（如缺 Python）≠ demo 逻辑失败。

## 19. 更新规则

每完成一个阶段性审计或 demo 版本，更新本文档：新增版本状态、记录通过/不通过、记录关键实现口径、记录不要重复争论的设计决定、记录实盘前必须补的缺口。
本文档是项目记忆，不是最终设计文档。**若与最新代码或用户指令冲突，以最新用户指令和当前代码为准，并回写本文档。**
