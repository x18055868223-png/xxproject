# 最新交付物

更新时间：2026-10-12

本目录只保留当前可见的最新 FMZ 单文件交付物。历史执行层版本保留在 `demo/副本快照/`；当前执行层最新交付物已切换为 Human Audit Gate 人工审计门版本。

## 文件清单

本目录信号层文件是 `demo_version=2.0.6`。2026-10-11 起 FMZ 本体运行在 EU 新机托管者（账号 69494805，robot 668422），原生输出由同机工作台链消费；自然新卡与三层版本一致性仍需单独验收。2.0.2 及更早的发布记录见[发布回执](../../docs/astra/22_GEX时间补丁发布与FMZ1.6.2交付.md)与 Git 历史。

| 文件 | 层 | 版本 | 状态 | 边界 |
|---|---|---:|---|---|
| `neutral_regulation_demo_fmz.py` | 信号层 | `demo_version=2.0.6` | 跟随 gexmonitor 上游改版：不再消费上游已停止提供的字段；新数据进入快照/审计卡/状态栏；SpaceGate 只检查所用字段 | 只读观察，不选腿、不报价、不下单 |
| `spm_manual_gate_execution_fmz.py` | 执行层 | `STRATEGY_VERSION=3.0.0-manual-gate` | `MANUAL_GATE_PLAN_READY` | 独立人工审计门执行层；当前版本不消费信号层输入 |

固定轮次只绕过 Anchor+DIE 的发卡触发条件，不伪造 `NR_REPAIR_CONFIRMED`，不改写 producer 的 direction、confidence、blocking、trade_allowed 或 execution_allowed。卡片继续写入同一 JSONL，并复用现有 LLM 复核链。

`signal_rating@1.0.0` 只回答结构依据、Put/Call 信用价差侧别压力、反对理由和未知项；候选两腿、报价、费用、补偿、退出条件和自动执行许可仍不在信号层评价。旧 `confidence`、`lean`、`side_hint`、`support_label`、NR、trigger 与权限消费者兼容保留。

当前 D–S 为一次 LLM 综合评审的总体证据等级；价格倾向、两侧比较和建议来自同次评审。B 可以提出人工研究建议，原信号窗口、硬风险和机器权限单独保留。历史路线见 [Astra v2.2](../../docs/astra/17_证据校正与建议式辅助闭环_v2.2.md)，另有[通俗说明](../../docs/astra/09_当前版本评级与推理链_通俗说明.md)与[兼容约定](../../docs/astra/10_版本兼容与数据复用约定.md)。用户曾于2026-09-11审阅当时页面并要求“推送并更新”，该次发布顺序与结果见[Astra v2.2发布回执](../../docs/astra/18_v2.2发布与兼容验收.md)；该历史验收不能代替 2.0.6 的自然新卡验收。

## 2.0.6 变更（2026-10-12）

- **重启后量价不再空窗**：策略启动的第一个实时轮次，用 Binance 现货最近 12 小时已收盘 1 分钟 K 线（含主动买入量）重建 10 BTC 量柱，主动净买 = 主动买 − 主动卖，跨柱按成交量比例拆分。4h/12h 主动成交窗口和 SpaceGate 锚点评分不再需要等待数小时积累。
- 只在量柱历史为空时执行一次；只用一分钟前已收盘的 K 线，避免与随后的实时成交重复计数；回补量柱标记 `source=kline_backfill_1m`，运行事实新增 `bar_backfill`（条数、时间范围、来源）。K 线读取失败时照常从实时成交积累。
- 配置：`volume_bar_backfill_enabled`（默认开）、`volume_bar_backfill_hours`（默认 12，1–16）。部署：完整替换 robot 668422 源码后重启。

## 2.0.5 变更（2026-10-12）

- **停止不再报错**：在 FMZ 点“停止”时，`Sleep()` 抛出的 `StopError` 被识别为正常退出（日志“收到停止指令 / 策略正常退出”，进程退出码 0）；循环内的异常兜底不再吞掉它。其它异常照常抛出。
- **去除弃用警告**：时间格式化改用 `datetime.fromtimestamp(..., timezone.utc)`，输出不变。
- **净伽马新鲜窗 20 分钟 → 2 小时**（`space_gate_net_gamma_fresh_ms=7200000`）：gexmonitor 上游可能一小时以上才更新，旧窗口会把正常数据周期性判为过期。工作台侧同步放宽：数据产品注册表 GEX 产品有效期 2 小时、FMZ 快照有效期 10 分钟（健康 15 分钟），事实层 `MAX_AGE_MS=600000`，与 300 秒导出节奏匹配。
- 部署：完整替换 robot 668422 的源码后重启；参数无新增或删除。若 FMZ 参数里手动填过 `space_gate_net_gamma_fresh_ms`，请删除该覆盖或改为 7200000。

## 2.0.4 变更（2026-10-11）

gexmonitor.com 约 2026-08-29 改版后，gexmonitorapi 改读 `dashboard-bundle` 与 `gex-latest`。

- **不再消费**：`iv_rv_ratio`、`call_premium`、`put_premium`、`call_put_bias`、`abnormal_signal` 及其衍生排名（上游已不提供，不做缺失标注）。它们此前不进入任何评分、权重或门控，删除本身不改变数值。
- **新进入快照的上游数据**：近端 0DTE/周度 Gamma 墙（`near_term_walls`，审计卡另派生 `near_term_call_wall` / `near_term_put_wall`）、PCR（成交量/持仓）、ATM IV 与分位、风险逆转偏斜、期权成交量与持仓、巨鲸大宗成交 1h/24h、最大痛点与最近到期摘要、对冲流情景；期限结构补 `days_to_expiry`。FMZ 状态栏新增「近端墙位 / 期权情绪 / 巨鲸 / 到期」四行。
- **SpaceGate**：只检查 `total_net_gex` 及其自身源时钟新鲜度（且时间语义协议须为 `gex_time_semantics@1.0.0`），不再看整包 quality/stale/data_state/availability。GGR 保持原保守规则（整包 partial 时只展示）。
- **会改变实盘信号行为（需观察）**：旧服务因上游停供字段永远报 `partial`，GGR 从未使用 gex_info、SpaceGate 从未放行。新服务的顶层 `availability`/`stale` 只描述信号核心（gex-latest 的 gex_board 与 gamma_exposure），增强数据另报 `enrichment_availability`/`enrichment_stale`；核心正常时为 `ready`。因此 GGR 会开始用 gex_info 做体制交叉校验（可能降级为 TRANSITION）与 pin 回退，SpaceGate 可能首次进入 `STRUCTURE_ELIGIBLE`。这对 2.0.3 同样成立（取决于服务端部署）。GEX 服务刷新间隔改为 900 秒，使 SpaceGate 20 分钟新鲜窗内不出现周期性失效。

## 本轮信号层部署（2.0.3）

替换前在 FMZ 保存当前源码与参数以便回滚。将 `neutral_regulation_demo_fmz.py` 完整替换到 robot 668422，保持 `read_only_demo=True`、推送关闭、`data_export_enabled=True`。

- **GEX 增强接口**：默认 `gex_info_base_url=http://127.0.0.1:8000`，对应 EU 新机 loopback 上的 gexmonitorapi（`PUBLIC_READ=true`，`/v1/info` 免 token）。策略 worker 与宿主共享网络命名空间，已实测可达。若 FMZ 参数里仍有旧的 `gex_info_base_url` 或 `gex_info_token`，请删除或改为新值；`gex_info_token` 已不再读取。
- **已删除的死配置**：`gex_info_token`、`llm_review_enabled`、`status_max_lines`、`signal_session_boundary_buffer_min`、`signal_session_high_confirmation_min`（及未被调用的 `_session_buffer_transition`）。它们此前不影响任何计算或输出。
- **截面导出（持续，非脉冲）**：默认相对路径 `demo/logs/snapshots.jsonl`，保留最近 12 条。触发条件为启动、SpaceGate 状态变化（提前写）、距上次成功满 `data_export_interval_sec`（默认 300 秒，运行时限制 180–600 秒），写失败按 180 秒重试。逐轮 DecisionSnapshot 诊断日志默认关闭（`decision_snapshot_diagnostic_log_enabled=False`）。这些行为自 2.0.1 起未改变。

`source_available_at_ms`、`ingested_at_ms`、`snapshot_exported_ms` 的时钟语义沿用 2.0.2：均为 producer 本地时点，不证明服务器接收或落盘。

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

以下为此前执行层交付记录；本轮工作台候选未重新运行执行交易回归。信号与审计链本轮验证另见工作台验收和阶段发布协议。

- `demo/execution_build_manual_gate/realsrc/tests/run_all.py`：206 passed, 0 failed
- `demo/execution_build_manual_gate/realsrc/build_bundle.py --check`：通过
- `py_compile`：源 bundle、最新执行 FMZ、最新信号 FMZ 通过
- 源 bundle 与 `demo/最新交付物/spm_manual_gate_execution_fmz.py` SHA256 一致
