# 最新交付物

更新时间：2026-07-16

本目录只保留当前可见的最新 FMZ 单文件交付物。历史执行层版本保留在 `demo/副本快照/`；当前执行层最新交付物已切换为 Human Audit Gate 人工审计门版本。

## 文件清单

| 文件 | 层 | 版本 | 状态 | 边界 |
|---|---|---:|---|---|
| `neutral_regulation_demo_fmz.py` | 信号层 | `demo_version=1.5.7` | 当前信号层交付物；统一 Funding/CVD/GEX 机械语义，并在北京时间 23:00 生成带“固定轮次分析”标签的只读截面审计卡 | 只读观察，不选腿、不报价、不下单 |

固定轮次只绕过 Anchor+DIE 的发卡触发条件，不伪造 `NR_REPAIR_CONFIRMED`，不改写 producer 的 direction、confidence、blocking、trade_allowed 或 execution_allowed。卡片继续写入同一 JSONL，并复用常规 Gemini 两阶段复核链。
| `spm_manual_gate_execution_fmz.py` | 执行层 | `STRATEGY_VERSION=3.0.0-manual-gate` | `MANUAL_GATE_PLAN_READY` | 独立人工审计门执行层；当前版本不消费信号层输入 |

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

## 本地验证

- `demo/execution_build_manual_gate/realsrc/tests/run_all.py`：206 passed, 0 failed
- `demo/execution_build_manual_gate/realsrc/build_bundle.py --check`：通过
- `py_compile`：源 bundle、最新执行 FMZ、最新信号 FMZ 通过
- 源 bundle 与 `demo/最新交付物/spm_manual_gate_execution_fmz.py` SHA256 一致
