# DEMO 实现流程与经验 · v0.2（审计 Codex + 复用纠偏轮）

> 本轮：demo v0.2，2026-06-02。承接 v0.1 地基轮 + 审计平行推进的 Codex 副本。

## 本轮目标
用户给入 `中性回路整合工程 - codex`（Codex 基于地基轮自主推进 v0.2→v0.17）。要求：先审、判扎实可复用 vs 跑偏，再以"交付两份真 FMZ"为目标推进；执行层为重点，信号层少动；在本路径（非 codex 副本）实现。

## 本轮产出
1. `审计/CODEX推进审计_v0.1.md` —— 审计结论。
2. 移植 5 个扎实契约模块进本路径（`execution_build/`: session_core/vrp_adapter/risk_controls/kpf_cut_policy；`signal_build/`: signal_bridge）。
3. `execution_build/verify_integration_core.py` —— 唯一冒烟，验证移植件跑通（含真实 VRP）。
4. `execution_build/执行层整合设计_v0.2.md` —— 锚定真实基线的执行层设计 + R1-R6 build 序列。
5. 本日志。

## 审计结论（一句话）
**Codex 的契约/流程骨架扎实可复用（已移植验证），但宏观跑偏**：17 版 sprawl 包装 mock 生态，从未接真实基线，两份真 FMZ 未达成（执行 bundle 569 行 vs 真实 2043；信号 151 vs 7297）。地基脊柱未被污染（哈希全 SAME）。

## 验证
`python execution_build/verify_integration_core.py` 全 PASS：
- kpf_cut_policy：权重归一 {0.375,0.375,0.25}、persistence 两项制。
- **vrp_adapter：连真实 VRP v1.1.0，window+candidate 双门 PASS**（唯一接真实代码的复用件，确认在本路径可用）。
- session_core：dry-run 不可下单、TTL 过期阻断（安全门生效）。
- risk_controls：预算超限 BLOCK size=0、止盈 TAKE_PROFIT_READY、归因 net、回放 bucket。

## 关键决策 / 经验
- **复用 Codex 的"形状"，不复用其"对象"**：会话/VRP门/缺口的契约形状是对的（直接移植省重造），但它建在 mock 上；本路径把这些形状焊到**真实基线**。
- **纠偏要点**：真实基线非 mock；砍 sprawl（一份日志、最简总线、无 delivery/bundle 机具）；信号层只加导出桥。
- **判 mock 的硬指标**：行数对照真实基线最直接（信号 bundle 2% 当即暴露空壳）。审 AI 推进时，先量"是否接真实基线 + 交付物规模 vs 基线"，再看局部代码漂亮与否。
- **sprawl 反模式**：测试先行/版本化/manifest 是好纪律，但若对象是 mock、且不收口到真实交付物，纪律会变成"用勤奋掩盖方向偏离"。审计要看宏观对齐，不被局部工整迷惑。
- 移植件路径依赖：`vrp_adapter` 用 `parents[2]` 定位 VRP 快照，必须放在 `execution_build/` 层（不可再下嵌一层），否则路径错。

## 下一轮（demo v0.3 = R1 KPF 减法，第一个真实代码轮）
1. 复制真实执行 `src/`（12 模块）+ `hedge_risk.py` 进 `execution_build/realsrc/`（demo 副本，源仓库只读）。
2. 按 `kpf_cut_policy` 口径**实改**真实代码删 KPF 6 处 + 对冲两项持续性 + 升 position_risk.v0.4。
3. 跑真实 `tests/run_all.py` 全绿 + KPF 扫描 0 命中；注册表 X5/X6/X17/X16 翻 LIVE-clean；factor_spine 自检。
4. 本日志追加 v0.3。
> 待用户确认：本轮即启动 R1，或先评审审计+设计。
