# 最新交付物

更新时间：2026-09-11

本目录只保留当前可见的最新 FMZ 单文件交付物。历史执行层版本保留在 `demo/副本快照/`；当前执行层最新交付物已切换为 Human Audit Gate 人工审计门版本。

## 文件清单

本目录信号层已发布为1.6.2，用户可见根目录与xxproject发布文件逐字节一致。服务器已兼容新旧数据；FMZ实例需用户替换，原生前向链以自然新卡为准。见[发布回执](../../docs/astra/22_GEX时间补丁发布与FMZ1.6.2交付.md)。

| 文件 | 层 | 版本 | 状态 | 边界 |
|---|---|---:|---|---|
| `neutral_regulation_demo_fmz.py` | 信号层 | `demo_version=1.6.2` | 已发布并同步：原生 GEX 字段时间说明；保留1.6.1的15/30分钟缓存摘要及旧信号行为。实例替换与自然新卡待验收 | 只读观察，不选腿、不报价、不下单 |
| `spm_manual_gate_execution_fmz.py` | 执行层 | `STRATEGY_VERSION=3.0.0-manual-gate` | `MANUAL_GATE_PLAN_READY` | 独立人工审计门执行层；当前版本不消费信号层输入 |

固定轮次只绕过 Anchor+DIE 的发卡触发条件，不伪造 `NR_REPAIR_CONFIRMED`，不改写 producer 的 direction、confidence、blocking、trade_allowed 或 execution_allowed。卡片继续写入同一 JSONL，并复用现有 LLM 复核链。

`signal_rating@1.0.0` 只回答结构依据、Put/Call 信用价差侧别压力、反对理由和未知项；候选两腿、报价、费用、补偿、退出条件和自动执行许可仍不在信号层评价。旧 `confidence`、`lean`、`side_hint`、`support_label`、NR、trigger 与权限消费者兼容保留。

当前 D–S 为一次 LLM 综合评审的总体证据等级；价格倾向、两侧比较和建议来自同次评审。B 可以提出人工研究建议，原信号窗口、硬风险和机器权限单独保留。当前路线见 [Astra v2.2](../../docs/astra/17_证据校正与建议式辅助闭环_v2.2.md)，另有[通俗说明](../../docs/astra/09_当前版本评级与推理链_通俗说明.md)与[兼容约定](../../docs/astra/10_版本兼容与数据复用约定.md)。用户已于2026-09-11审阅当前页面并明确要求“推送并更新”；发布顺序为服务器先兼容1.6.0/1.6.1，再同步根目录交付，随后由用户替换FMZ实例。执行结果见[Astra v2.2发布回执](../../docs/astra/18_v2.2发布与兼容验收.md)。不能用交付文件、测试或Git版本代替自然新卡验收。

## 执行层说明

新执行层由 `demo/execution_build_manual_gate/realsrc/` 生成。当前主链路是：人工审计门参数 → Deribit option-chain → 同期垂直候选 → S:PM/执行可行性/VRP/预算过滤 → 短确认码 → 预提交 → 开仓活动；已有持仓继续进入持仓管理、退出、对冲和恢复路径。

人工审计门参数包括：

- `MANUAL_PLANNING_ALLOWED`
- `DIRECTION_BIAS`
- `SHORT_DTE_HOURS`
- `SHORT_DELTA_RANGE`
- `PROTECTION_WIDTH_RANGE`
- `ORDER_AMOUNT`
- `MANUAL_AUDIT_CARD_ID`
- `MANUAL_AUDIT_NOTE`
- `MANUAL_CONTEXT_TTL_MIN`

没有执行侧 VRP `market_context` 时，只展示计划候选，不生成可锁定确认码；预提交继续 fail-closed。

## 安全状态

所有真实交易门仍保持默认关闭：`ALLOW_ENTRY_TRADING=False`、`ALLOW_EXIT_TRADING=False`、`ALLOW_HEDGE_TRADING=False`、`DRY_RUN_PASSED=False`。

本次只做本地重建、测试和打包；未声明 FMZ 真机 dry-run、交易所只读验收或实盘可用。

## 执行层历史本地验证

以下为此前执行层交付记录；本次Astra推送未重新运行执行交易回归。信号与审计链本轮验证另见Astra进度和推送验收回执。

- `demo/execution_build_manual_gate/realsrc/tests/run_all.py`：206 passed, 0 failed
- `demo/execution_build_manual_gate/realsrc/build_bundle.py --check`：通过
- `py_compile`：源 bundle、最新执行 FMZ、最新信号 FMZ 通过
- 源 bundle 与 `demo/最新交付物/spm_manual_gate_execution_fmz.py` SHA256 一致
