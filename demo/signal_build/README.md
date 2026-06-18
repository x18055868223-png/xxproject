# signal_build —— 信号 FMZ 构建区

> 目标交付物：`nrd_signal_fmz.py`（信号层 FMZ 单文件，含 10 因子 + SignalEvidence 导出桥）。
> 当前（2026-06-04）：信号层基线已封 **v1.0.0**（工程阶段封版：observer 只读，标定后置）。`signal_bridge.py` 已实现 allow-list 六子域 + `signal_review` digest + `test_signal_bridge.py`。
> **Phase-2 P0（全链路审计指出，待办）**：现桥读「顶层键」，但真实 `EvaluationSnapshot` 嵌于 `factor_snapshot.edb` / `decision.strategy_recommendation`，需补**真实快照映射 + 运行时导出 + 本机总线 + 消费闭环**；旧 `nrd_signal_fmz.py` bundle 缺最新能力（gex_info/审计卡）且桥在 `main()` 后运行时零调用，应以 v1.0.0 单文件重建为唯一 canonical 工件。

## 1. 基线
- 基线 = 信号层 v0.5.4 单文件 `中性回路 - opus4.8\neutral_regulation_demo_fmz.py`（10 因子全 LIVE，已封版暂缓）。
- **整合改造量 ≈ 零因子改动**（总纲 v0.3 §3.1：信号层从不依赖 KPF）。唯一新增是导出桥。

## 2. 唯一整合工作：SignalEvidence 导出桥（机制 M3）
把运行时已写的 `demo/logs/snapshots.jsonl` / 内部状态，收束导出为对外 `SignalEvidencePackage`（六子域）：
```
timing_window         ← NeutralRepair(is_active/state/TTL) + Anchor
direction_evidence    ← EDB(lean/support/confidence/coverage/edb_score/reason)
strategy_recommendation ← 24h/48h expiry + side_hint（不选 strike）
pre_trade_context     ← GGR regime/veto + MPF/funding 摘要
data_quality          ← 新鲜度/缺字段/nr_threshold_profile/包版本
reject_state          ← NO_TRADE_BLOCKED/WAIT_CONFIRMATION + 原因
```
要点：执行层只消费这 6 子域，**不穿透 10 因子**（收束架构 §2）。导出桥是只读旁路，不改任何信号因子逻辑。

## 3. 构建步骤（demo v0.2+）
1. 复制基线单文件进本目录作 `nrd_signal_fmz.py`（只读快照→演进，不动源仓库）。
2. 加 `export_signal_evidence_package()`：从既有 recorder/snapshot 收束六子域，按 `nrd.integration.signal.v0.1` 输出。
3. 走本机总线（loopback/共享文件原子写）供执行 FMZ 读；fail-safe：包过期(ttl)/不可达=执行层不开新仓。
4. 验证：离线 fixture 跑通、导出包 schema 合法、六子域字段齐、与信号面板读数一致。

## 4. 注意
- 信号层 P0 校准（EDB 置信阶梯、nr profile relaxed_test→production）是**并行数据轨**，不阻塞导出桥构建，但阻塞闸 B（总纲 v0.5 §10）。
- 不在导出桥里塞执行字段（不变量：不往信号 payload 塞执行内容）。
