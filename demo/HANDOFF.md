# HANDOFF — 中性回路整合 demo（策略版本优化与迭代）

> 目的：新窗口据此**继续策略版本的优化与迭代**，无需翻历史对话。
> 日期：2026-06-02（demo v0.6，R1-R6 全落地）。
> 首读顺序：本文件 → `README.md`(demo 工作区) → `设计/03_因子注册表.md`(脊柱) → `../00_总纲/中性回路总系统整合设计总纲_v0.5.md`(权威总纲)。

---

## 0. 一句话现状
约 31 因子按「收束分层 + 注册表脊柱 + 占位安全」治理；执行/对冲真实基线已焊接整合层（KPF 减法 + 执行会话 + VRP 定价门 canonical 收口 + 四缺口域 + 对冲并入），**两份真 FMZ 单文件已合成、编译/契约/无 KPF 验证通过、80 真实测试绿**。余下 = live 接线 spike + 真实数据标定（非代码缺口）。

## 1. 两份 FMZ 交付物（最终目标，已达成 demo 级）
| 文件 | 行数 | 内容 | 验证 |
|---|---|---|---|
| `execution_build/nrd_execution_fmz.py` | 3145 | 真实执行(2043) + 整合层(会话/VRP门/缺口域/对冲) | 编译✅ / build_bundle --check✅ / 0 KPF✅ |
| `signal_build/nrd_signal_fmz.py` | 8127 | 真实信号 v0.5.4(7297, 10 因子) + SignalEvidence 导出桥 | 编译✅ / 桥就位✅ / 无执行字段污染✅ |

> 这是 demo 级交付（真实因子 + 整合契约，离线验证通过）。上线前仍需 §4 的 live 接线 + 校准。

## 2. 路径地图
- **本 demo 工作区**：`C:\Users\Xu\Documents\中性回路整合工程\demo\`
  - `realsrc = execution_build/realsrc/`：真实执行/对冲基线**副本**（已演进，源仓库只读）。src 13 模块 + tests 13 文件 + build_bundle.py。
  - `execution_build/`：整合契约模块（session_core/vrp_adapter/risk_controls/kpf_cut_policy）+ 执行 FMZ 交付物 + 整合设计_v0.2。
  - `signal_build/`：signal_bridge.py + build_signal_bundle.py + 信号 FMZ 交付物。
  - `shared/`：`factor_registry.json`(脊柱单一真相源) + `factor_spine.py`(可运行自检)。
  - `设计/`：01 收束分层架构 / 02 缺口实现规范 / 03 因子注册表。
  - `审计/CODEX推进审计_v0.1.md`：codex 并行副本审计结论（可复用 vs 跑偏）。
  - `实现流程与经验/`：v0.1→v0.6 流程日志（每轮决策+自审+边界，**迭代必读 v0.6**）。
- **源仓库（canonical，只读，勿污染）**：信号 `C:\Users\Xu\Documents\中性回路 - opus4.8\`；执行 `C:\Users\Xu\Documents\Deribit期权交易执行层\`；VRP `C:\Users\Xu\Documents\系统总纲\VRP\`。
- **权威总纲**：`中性回路整合工程\00_总纲\中性回路总系统整合设计总纲_v0.5.md`。

## 3. 因子治理脊柱（迭代时必守）
- **单一真相源** = `shared/factor_registry.json`（31 因子：信号 10 + 执行 21；LIVE/PLACEHOLDER/OFFLINE + integration 标记）。
- **加/改因子铁律**：先在注册表登记一行 → 实现成统一 `FactorResult` 形状 → 跑 `python shared/factor_spine.py` 自检 PASS。没登记的因子不允许存在。
- **收束**：31 因子→10 域→11 包→1 会话；改因子爆炸半径 = 1 域；下游只读上游的域包、不穿透因子内部。
- **占位安全方向单调**（先实现后标定不变乱的核心）：占位因子只能更安全（挡/缩/早退），绝不放松门或夸大机会；面板/注册表标「未标定」。

## 4. R 系列落地总结（每步真实回归背书）
| 步 | 内容 | 验证 |
|---|---|---|
| R1 | KPF 减法（config/plans/leg_selection/strategy/accounting/hedge_risk + 对冲两项持续性 + position_risk.v0.4 + 权重{0.375,0.375,0.25}） | 真实 run_all 60 绿、KPF 功能残留 0 |
| R2 | 执行会话骨架（session_core 入 src，真实 _build_menu 方案驱动 ExecutionSession） | plan_hash 防重排/显式授权/TTL 过期，63 绿 |
| R3 | VRP 落 `vrp_gate.py` + 4 原语收口 canonical + PRICE_GATE | **与封版快照等价性测试零漂移**，68 绿 |
| R4 | 缺口域(risk_controls)接入 + EntryRiskAnchor VRP 血缘 | 占位安全默认 + 向后兼容，73 绿 |
| R5 | 对冲并入 HEDGE_WATCH（消费 SignalEvidencePackage edb/ggr） | HEDGE_READY 三条件/退出优先，76 绿 |
| R6 | 合成两份真 FMZ + `integrated_plan_preview`（菜单→VRP→预算） | 80 绿 + 两份 FMZ 编译/契约/0 KPF |

## 5. LIVE vs PLACEHOLDER（迭代重点：把占位标定成 LIVE）
- **LIVE（已生效，逻辑真实）**：信号 10 因子(EDB/SRD/GGR/...)、执行 leg_selection/plans/spm/execution/ledger、VRP 窗口门/候选门/hurdle/BS、对冲五变量/persistence/状态机/触界概率、ExecutionSession/ApprovalIntent。
- **PLACEHOLDER（已接线、安全默认、待真实数据标定）= 四缺口 7 因子**：组合硬预算 X10 / 区制 sizing X11 / 回撤熔断 X12 / 止盈 X13 / 末日Gamma退出 X14 / 时间退出 X15 / 最小归因 X20。
- **OFFLINE**：全链净 P&L 回放 X21。
- **提升 PLACEHOLDER→LIVE 硬门槛**：真实数据标定 + 注册表翻态 + 经验日志留一行（见 02 规范 §2）。

## 6. 验证/构建命令（迭代每轮跑）
```
cd C:\Users\Xu\Documents\中性回路整合工程\demo
python execution_build/realsrc/tests/run_all.py          # 执行层真实回归（当前 80 绿）
python execution_build/realsrc/build_bundle.py --check    # 合成执行 FMZ + 名称解析/KPF 扫描
python signal_build/build_signal_bundle.py --check        # 合成信号 FMZ + 导出桥/污染检查
python shared/factor_spine.py                             # 注册表治理自检
```
> 环境：Python 3.12（`C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe`）。ps1 校验脚本被 ExecutionPolicy 拦，直接 python 跑。控制台中文乱码仅 GBK 显示，文件 UTF-8 正确。

## 7. 后续迭代优先级（策略优化路线）
**A. 闸前硬前提（blocked on 真实数据，决定能否上线，最高优先）**
1. **信号 P0 校准**：实盘累计 `snapshots.jsonl` ≥2-4 周 → `tools/calibrate_edb.py` 定 EDB 置信阶梯 35/50/68 + GGR 安全门 + nr_threshold_profile relaxed_test→production（须用户拍板风险偏好）。
2. **全链扣成本净 P&L 回放（缺口1/X21）**：把占位回放升级为真实成交级，分桶报扣成本净期望；**未为正不得进闸 B、不得宣称长期盈利**。
3. **VRP 卖方 edge**：cron 前向采集多时点 Deribit IV → 成交级到期回放，第一次真测 IV-vs-RV edge。

**B. live 接线 spike（代码可做，需真实环境/FMZ 验证）**
4. **信号导出桥字段映射**：核对 `signal_bridge` 取的 snapshot 键 vs 信号层 recorder 实际 snapshot schema（逐键）。
5. **执行 main() 会话化**：把 while 循环改"会话作主、ROUND_MODE 降兜底"，接 `integrated_plan_preview` + ExecutionSession 授权；前置 **FMZ 命令栏交互/`_G` 持久化 spike**。
6. **VRP market_context live feed**：执行层接 Deribit RV/term-IV 取数喂 VRP（当前经入参）。
7. **本机信号总线**：最简 loopback/共享文件原子写（**不要** codex 的锁/JSONL/轮转过度设计）。

**C. 占位因子标定（缺口 2/3/4，有 A 的数据后做）**
8. 组合硬预算/熔断阈（用户拍板风险偏好）；区制连续 sizing（Kelly 后置）；止盈/末日Gamma 阈（回放标定）；最小归因接真实成交分解。

## 8. 红线 / 不要重蹈（来自审计与自审）
1. **不污染源仓库**：迭代在 demo 副本做；回灌源仓库由用户决定。
2. **真实基线，不造 mock 生态**（codex 跑偏教训：17 版 sprawl 包 mock、从未接真实基线）。每步接真实代码 + 跑真实回归。
3. **不 sprawl**：一份流程日志/轮、最简总线、不过早建 manifest/delivery 机具。
4. **占位安全方向单调**：绝不让占位因子放松门或多下注。
5. **bundle 陷阱**（已修，勿复发）：模块 `from __future__` 不入 bundle 中段；收口用 canonical 同名不别名；`_spread_half_cost` 保 None/倒挂→0。
6. **VRP/缺口域只过滤、不进 PLAN_WEIGHTS、不判方向**；信号 FMZ 不塞执行字段。

## 9. 权威文档指针
- 总纲 v0.5（执行会话式+四缺口路线）：`00_总纲\中性回路总系统整合设计总纲_v0.5.md`。
- 收束架构/缺口规范/注册表：`demo\设计\01,02,03`。
- 执行整合设计（R1-R6 序列）：`demo\execution_build\执行层整合设计_v0.2.md`。
- codex 审计：`demo\审计\CODEX推进审计_v0.1.md`。
- 逐因子文档（27 篇）：`01_信号层.../02_执行层.../03_VRP门.../04_对冲模块\因子文档\`。
- VRP 封版+收口契约：`03_VRP门_建仓前定价\交付物快照\docs\`。

## 10. 新会话第一步建议
1. 跑 §6 四条命令确认当前 80 绿 + 两份 FMZ 编译（基线健康）。
2. 读 `实现流程与经验\DEMO实现流程与经验_v0.6.md`（最近状态+边界）。
3. 选优先级：若有实盘数据 → 走 §7.A（校准/回放，决定上线）；若先打磨工程 → 走 §7.B（main 会话化 + 信号桥字段映射，需 FMZ spike）。
4. 任何改动：先注册表登记/对齐 → 改 realsrc 真实代码 → 跑真实回归 + factor_spine → 重合成两份 FMZ → 追加流程日志一轮。
