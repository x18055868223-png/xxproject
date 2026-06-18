# 中性回路 v0.5.4 — 迁移 Handoff 包

生成：2026-06-01。新对话先读本文件 + `PROJECT_MEMORY.md` + 项目记忆（.claude）。

## 1. 当前状态（已全链压测，可信）
- 版本：demo `0.5.4` / schema `nrd.schema.v0.5.4`。**全链五步校验全绿**（§5）。
- **封版暂缓**：0.5.3 曾宣布阶段封版，但被实盘证伪（趋势行情下置信标定失效，强空信号却输出"无方向/置信3-6"）。0.5.4 已修置信标定，**需先实盘复观一段再谈封版**，不要再提前宣布。
- 架构：主链路 3 模块（External Gate / Anchor / TMV-F，进 `module_results`）+ 前置/方向层（不进 module_results）：M-DIE、NeutralRepair(DIE+Anchor 时序门)、**EDB(六证据到期方向合成=权威方向层)**、SRD(期权偏斜)、GGR(Gamma 区制安全门+空间钉)、MPF(宏观)、signal_events。只读、不选腿/不下单，执行外置。
- 前端 6 面板：总览 / EDB 到期方向合成层 / 信号事件 / 数据源与定时 / 主链路与因子状态 / 宏观要素。日志：平时只 `观察摘要`(数据健康)，**信号生成时输出一次 `信号综述`**（情况/倾向/目标/策略四步内核，不点名"四步法"）。

## 2. v0.5.4 修了什么（置信标定回归）
0.5.3 实盘：TMV 强空(-0.5~-0.67) 且价格随后跌 -2.2%，却输出"无方向/置信3-6"。根因+修复：
- 置信被 `|EDB|×一致度×覆盖×GGR` 三连乘压垮 → 改 `100×strength×带floor一致度×带floor覆盖`，strength=`clamp(|EDB|/edb_score_full=0.75,0,1)`，floor `edb_agreement_floor=0.6 / edb_coverage_floor=0.5`。
- Macro 反向拖(权重0.5) → `edb_base_weights["MACRO"] 0.5→0.3`。
- CVD 低估已确认趋势(-2.47%却vote-0.17) → 确认象限 `vote=max(分位mag, |price%|/edb_price_confirm_full_pct=0.75)`。
- 实证：面板场景 22→**76(强偏空→Call Credit Spread，与实际跌一致)**；信号行 3-6→**44(CVD真缺失→WAIT，诚实)**。

## 3. 死代码 prune：已完成 ✅
`demo/*.py` 中已无原 bias/factor-strategy 表的孤儿 helper、`build_four_step_summary`、`_factor_strategy_table`（仅 `LOGIC_REVIEW_V0.5_EDB.md` 有历史文字提及）。`bias_thesis.py` 仅保留 EDB 复用的 macro/funding verdict + 宏观分量 helper。

## 4. 新对话待推进（按优先级）
1. **0.5.4 实盘复观**：强方向是否进 50-76 区、缺 CVD/冷因子是否落 WAIT、有无过度自信；稳健后再谈阶段封版。
2. ~~**查 4h CVD 频繁"未就绪(—)"根因**~~ **已解决（2026-06-02）**：根因非数据不足，而是 `tmvf_micro_min_coverage_hours=4.0` 恰等于最小 horizon 4h，而任意跨度 H 的回看窗 `coverage_hours` 恒 < H（选窗严格 `>`），故 4h 窗 `data_ready` 永假、CVD/涨跌永久"—"、EDB 覆盖度被结构性砍 ~19%。修复：阈值 **4.0→2.0**（不变量：须 < min(horizons)，让 `ready_coverage_frac=0.65` 成为绑定就绪闸）。并修观测层：recorder `_edb_table` 的 CVD/MACRO 行 + `signal_events._build_event` 改为"优先 EDB detail、回落原始 micro_flow/macro_pressure"，EDB 零权重时也显示原始 cvd/涨跌/macro（照搬作者已有的 SRD/GGR 直读先例）。单文件已同步，preflight/static/runtime/-Check 全绿。**注意**：4h CVD 由 0 覆盖转为参与合成→EDB 覆盖度/置信分布会整体略升，校准复观从本次起重算。
3. **EDB/SRD/GGR 阈值实盘校准**：见 `demo/CALIBRATION_PLAN_V0.5.md`（CVD 分位带、SRD 基线窗、GGR cut/veto、base weights、置信切点 35/50/68、新增 score_full/floors/price_confirm）。**置信刻度自 0.5.4 变更，0.5.4 前数据不可比，重新起算。**
4. **与后续模块整合**：见记忆「总系统整合定调」——总纲 v0.3 三模块(信号/执行/对冲)，下一阶段 Phase1 减法落地。
5. 可选契约硬化：EDB 阻断时顶层 `signal`/`strategy_type` 仍带 TMV 预览值（见记忆「策略门控缺口」残留条）；如要置空需确认不破坏外部执行程序消费此结构。

## 5. 验证命令（本机 CurrentUser=RemoteSigned，用 `-File`，勿用被安全策略拦截的 `-ExecutionPolicy Bypass`）
```powershell
$py="C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe"
& $py -m compileall demo
powershell -NoProfile -File tools\build_fmz_single.ps1 -Check
powershell -NoProfile -File tools\update_delivery_summary.ps1 -Check
powershell -NoProfile -File tools\fmz_preflight_demo.ps1
powershell -NoProfile -File tools\static_validate_demo.ps1
powershell -NoProfile -File tools\runtime_check_demo.ps1 -PythonPath $py
```

## 6. 关键文件
- 权威实现 `demo/`(多文件)；交付 `neutral_regulation_demo_fmz.py`(单文件,已同步,CRLF+BOM)。
- 设计/审计：`PROJECT_MEMORY.md`、`demo/FORWARD_DESIGN_V0.5_EDB.md`、`demo/LOGIC_REVIEW_V0.5_EDB.md`、`demo/CALIBRATION_PLAN_V0.5.md`、`AUDIT_REPORT_2026-05-30.md`、`soul.md`(迭代宪法)。
- 因子卡：`add/skew_rr_directional_factor_v1.0.md`、`add/global_gamma_regime_factor_v1.0.md`、`add/M-DIE_v1.1_final_*.md`、`add/宏观要素.md`。
- 项目记忆(.claude/projects/.../memory)：current-state / edb-design / strategy-gating-gap / verification-env / integration-plan / kpf-server-audit。

**新对话开场建议**：「按 HANDOFF.md + 项目记忆推进中性回路 v0.5.4 之后：先做 0.5.4 实盘复观 + 查 4h CVD 未就绪根因，再谈封版与后续模块整合。」
