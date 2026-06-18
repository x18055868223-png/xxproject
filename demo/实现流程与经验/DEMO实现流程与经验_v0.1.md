# DEMO 实现流程与经验 · v0.1（地基轮）

> 用途：记录每轮 demo 迭代的流程、决策、验证与经验，供后续 demo 版本迭代。每轮追加，不覆盖。
> 本轮：demo v0.1，2026-06-02。

---

## 本轮目标
回应用户三点：① 31 因子如何收束分层（不取消双 FMZ）；② 缺口「先实现后标定」但收束干净；③ 在整合工程内建 demo 沙箱推进落地，最终产两份 FMZ + 流程经验文档。
本轮定位 = **地基轮**：先立治理脊柱，不急于堆代码（避免「做了很多无法利用」）。

## 本轮产出
1. `设计/01_因子收束分层架构.md`（Q1）：31→10域→11包→1会话四级收束；两 FMZ 分工；自顶向下阅读路径。
2. `设计/02_缺口实现规范_先实现后标定.md`（Q2）：统一 FactorResult 接口、占位安全默认（中性/保守收紧）、状态生命周期、收束干净五不变量。
3. `设计/03_因子注册表.md` + `shared/factor_registry.json`：31 因子单一真相源。
4. `shared/factor_spine.py`：可运行脊柱（统一接口 + 域编排 + 占位安全）。
5. `signal_build/` `execution_build/` 构建计划 README。
6. 本日志。

## 验证
`python shared/factor_spine.py` 实测：
- 因子总数 31（signal 10 + execution 21）。
- 状态 LIVE 23 / PLACEHOLDER 7 / OFFLINE 1。
- 自检 PASS（0 错误）：每因子有 id/层/域/状态/输出包、状态合法、id 唯一。
- 域收束演示：portfolio_risk 占位因子 X10 走 `PLACEHOLDER_CONSERVATIVE_SAFE`，不进 live、安全默认偏紧。
> 注：Windows 控制台显示中文为 GBK 乱码，文件本身 UTF-8 正确，不影响逻辑。

## 关键决策（本轮拍板）
- **不取消双 FMZ**：信号 FMZ（10 因子→1 包黑盒）+ 执行 FMZ（21 因子→域包→会话）。下游永不穿透上游因子，这是控制理解成本的根本。
- **收束单位 = 域**：改/复用因子的爆炸半径 = 1 个域；加因子必须归域 + 登记注册表，否则不允许存在。
- **占位安全方向单调**：占位只能更安全（挡/缩/早退），绝不放松门或夸大机会。这是「先实现后标定」不变乱的铁律。
- **注册表是单一真相源**：FMZ 构建/面板/回放/文档都读它；提升 PLACEHOLDER→LIVE 需真实数据标定 + 翻态 + 本日志留痕。
- **地基先行**：本轮不动实际 FMZ 代码，先把脊柱立稳。实际组装从 demo v0.2 的 Phase 1 减法切入。

## 经验记录
- 因子计数与用户「约 31」吻合：精确 31 = 10 信号 + 21 执行（含四缺口 7 占位 + 1 离线）。四缺口落到 3 个域（portfolio_risk/position_manage/attribution）+ 1 离线（replay）。
- 收束比 31→11→10→1 是「理解成本」可控的量化保证：任何时刻只需理解一层。
- 把「占位安全默认」写进可运行代码（`conservative_placeholder`/`neutral_placeholder`），比只写文档更能防止后续实现走样。
- 嵌套中文路径下 Glob 偶发不命中，文件操作走 PowerShell/Bash 更稳（沿用上一轮经验）。

## 下一轮（demo v0.2）计划
- **Phase 1 减法落地**（第一个实际代码轮）：复制执行层+对冲基线进 `execution_build/`，删 KPF 6 处、对冲 persistence 两项制、升 position_risk.v0.4；跑回归 + 离线 fixture；注册表把 X5/X6/X17 的 `KPF_CUT_PENDING`、X16 的 `V0_4_PENDING` 标记清除并翻 LIVE-clean；本日志追加 v0.2。
- 并行：signal_build 起 SignalEvidence 导出桥（不阻塞）。
- 待用户确认：是否本轮即启动 Phase 1，或先评审本地基轮设计。
