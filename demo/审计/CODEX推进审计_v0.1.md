# Codex 推进审计 v0.1

> 对象：`C:\Users\Xu\Documents\中性回路整合工程 - codex\demo\`（Codex 基于地基轮自主推进 v0.2→v0.17）。
> 方法：深读核心代码 + 哈希对比地基 + 行数对照真实基线 + 跑通验证。
> 结论：**契约/流程骨架扎实可复用；但宏观跑偏——17 版 sprawl 包装 mock 生态，始终未接真实基线，两份真 FMZ 未达成。**
> 日期：2026-06-02。

---

## 0. 结论速览

| 维度 | 判定 |
|---|---|
| 地基（设计/shared 脊柱） | ✅ **未被污染**（5 文件哈希全 SAME，Codex 在脊柱上构建） |
| 执行层契约/流程骨架 | ✅ **扎实可复用**（已移植 5 模块并验证跑通） |
| 安全默认/占位纪律 | ✅ 正确遵循我的 02 规范 |
| 测试先行工程纪律 | ✅ 好（但数量 sprawl） |
| **两份真 FMZ 交付** | ❌ **未达成**（mock 骨架，非真实因子） |
| 宏观收束（项目级干净） | ❌ **跑偏**（17 版/51 日志/16 测试/多 bundle/过度总线） |
| 真实基线接线 | ❌ **从未接**（自承"不接真实 recorder/snapshot"） |

---

## 1. 扎实、可复用（已移植进本路径并验证）

| 模块 | 评价 | 验证 |
|---|---|---|
| `session_core.py` | ExecutionSession + ApprovalIntent，忠实 v0.5 §6（plan_hash/TTL/precommit/can_commit）。干净。 | dry-run 不可下单、TTL 过期阻断 ✓ |
| `vrp_adapter.py` | **唯一连真实代码者**：载入 VRP 快照、保持纯过滤、从 EDB lean 取 side。 | 真实 VRP v1.1.0 双门 PASS ✓ |
| `risk_controls.py` | 四缺口因子（组合预算/持仓管理/归因/回放）+ 安全默认（BLOCK/早退）。对齐 02 规范。 | 预算超限 BLOCK size=0、止盈、归因、回放 bucket ✓ |
| `kpf_cut_policy.py` | Phase1 减法纯 helper（归一{0.375,0.375,0.25}、strip_kpf、两项持续性），不改源仓库。 | 权重归一、persistence 两项制 ✓ |
| `signal_bridge.py` | allow-list 导出 SignalEvidencePackage，窄而干净，不漏执行字段。 | 结构核对 ✓ |

> 这些是 Codex 的真实价值：**把 v0.5 的会话/VRP门/四缺口契约写成了干净、对齐、可跑的形状**。直接复用，省去重造。

---

## 2. 跑偏 / 投入错配（已剔除，不带入本路径）

### 2.1 宏观 sprawl（最大问题，违背"收束干净"项目级目标）
- 17 个版本（v0.2→v0.17）× 3 类文档（实现计划/测试与审计/实现流程与经验）= **51 篇过程日志** + **16 个测试文件** + **3 个 delivery index** + 多个生成 bundle。
- 对一个**从未接真实基线**的 demo，这是过度仪式。工程纪律（测试先行/版本化/manifest）本身是优点，但**用在了 mock 对象上**。

### 2.2 两份"FMZ"是 mock 骨架，非真实交付（决定性证据）
| 文件 | 行数 | 真实基线 | 占比 |
|---|---:|---:|---:|
| codex 执行 expanded bundle v0.7 | 569 | spm_calendar_protected_short_v1.py **2043** | ~28% |
| codex 信号 expanded bundle v0.6 | 151 | neutral_regulation_demo_fmz.py **7297** | **~2%** |
- 信号 bundle 151 行 ≈ 空壳，**不含 10 个真信号因子**；执行 bundle 含会话/VRP门**流程**但不含真实 leg_selection/plans/spm/execution/ledger/hedge 逻辑。
- `nrd_execution_fmz.py` 用硬编码 mock（net_edge=12.5、假 IV、假报价）。Codex 自承（交付索引 v0.17）：**"final FMZ sealing is not complete"**。

### 2.3 过早/过度基础设施（在真实交付物存在前就堆）
- `signal_bus.py` 15KB：TTL 锁 / JSONL 事件 / manifest 轮转 / outbox——远超用户要的"本机 loopback/共享文件原子写"简单总线。
- `build_delivery_manifest.py` 18KB + 3 个 delivery index：给还不存在的真实交付物算校验和。
- `build_expanded_bundle.py` **60KB** 生成 23KB mock bundle——大量精力在不会成为最终交付物的脚手架上。

### 2.4 信号侧过度投入（违背"信号层先不做太多改动"）
- v0.4 + v0.16 + v0.17 大量花在信号总线/outbox 轮转。信号层 v0.5.4 已能输出正确结论，本不该在此投入。

---

## 3. 根因
Codex 把"测试先行 + 版本化 + manifest 校验"的**正确工程纪律，用在了错误对象上**：越做越深地完善一个 mock 生态的外围（总线/manifest/delivery/bundle 生成器），却始终没把**真实的 31 个因子接进两份真 FMZ**。纪律对，对象错；局部每文件干净，宏观项目级 sprawl。

---

## 4. 纠偏方向（本路径据此推进，详见 [执行层整合设计_v0.2](../execution_build/执行层整合设计_v0.2.md)）
1. **锚定真实基线，不造 mock**：两份 FMZ = **演进真实单文件**（执行 2043 行 + 对冲 hedge_risk + VRP 快照 + 缺口域），不是另起 mock。
2. **复用 Codex 契约模块作"焊点"**（已移植验证）：session_core/vrp_adapter/risk_controls/kpf_cut_policy/signal_bridge 作为整合层，会话编排去调**真实因子函数**。
3. **砍 sprawl**：不复制 17 版流程、不过度建总线/manifest/delivery、保留**一份**干净流程日志；总线用最简本机方案。
4. **信号层近零改动**：只加 signal_bridge 导出桥（复用 Codex 的）。
5. **收束红线不变**：每域因子先登记注册表、跑 factor_spine 自检、占位安全方向单调。

---

## 5. 复用 / 弃用清单
**复用（已移植进本路径 `demo/execution_build/` + `demo/signal_build/`，验证 PASS）：** session_core.py、vrp_adapter.py、risk_controls.py、kpf_cut_policy.py、signal_bridge.py + 其契约形状/经验。
**弃用（不带入本路径）：** signal_bus.py（过度总线，改最简本机）、build_delivery_manifest.py + delivery index（过早）、build_expanded_bundle.py + 所有 generated mock bundle（mock 脚手架）、quote_snapshot/fill_ledger 的 mock 文件总线层（真实接线时重做）、17 版 sprawl 日志（仅留经验提炼）。
**借鉴（不直接搬，真实接线时重写干净版）：** 测试先行纪律 + 契约测试用例的覆盖点（window/candidate/TTL/hash/budget/replay 分桶）。
