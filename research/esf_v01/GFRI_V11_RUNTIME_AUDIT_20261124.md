# GFRI v1.1 影子运行工程与数据完整性审计报告（2026-11-18 至 2026-11-24）

审计快照时间：`2026-11-24T07:52:00Z` · 覆盖窗口：`2026-11-18T18:10:27Z` – `2026-11-24T07:47:43Z`（`133.62h` / `755` 周期） · 运行节点：`xx-eu`（`SHADOW_ONLY`）

---

## 1. 核心审计结论与下游可用性裁决

**裁决：工程运行健康、账本与原始抓包零损坏、跨日口径 100% 可比；数据可直接进入 11-21 / 11-23 事件研究与 ESF v0.1 联合检验，但必须严格执行「双层样本分层」与「数值型 DEGRADED 保留规则」。**

1. **调度零漏跑（真实节拍 `638.0s` = `135.42` 周期/天）**：`systemd` 定时器采用 `OnUnitInactiveSec=600s`（上次服务退出后倒计时 600 秒），叠加单次采集确认中位耗时 `36.07s`（3 次间隔 15s 的盘口确认采样）及调度裕度后，真实稳态周期为 **`638.00s`（`10.633 分钟`）**，完整自然日理论周期数为 **`135.42`**（而非机械按 10 分钟整除的 `144`）。全窗口实测 **`755` 周期**（理论预期 `754.97`），`0` 次服务失败、`0` 次调度漏跑。
2. **账本与双哈希归档零损坏**：`ShadowLedger.verify_consistency()` 通过（`consistent=true`，主账本与紧凑索引均为 `755` 条，`0` 截断尾行）；原始 HTTP 响应归档累计校验 **`6,730` 个文件（`679,243,431` 字节）**，磁盘回读 **`sha256_mismatch_count = 0`**、`missing_file_count = 0`。
3. **关键质量分层（严禁误删 44 个快市数值型 `DEGRADED` 周期）**：
   - **数值有效层（事件研究主集，`740 / 755 = 98.01%`）**：含双边 `status == "VALID"` 的 **`696` 周期**，以及双边 `status == "DEGRADED"` 但具备有效 `gfri_confirmed` 的 **`44` 周期**（其中 `42/44` 源于 BTC 快市引发的 `price_basis_divergence_degraded`，即 GEX 现货与永续盘口中位价偏离处于 `0.20%–0.50%` 降级带）。若机械剔除 `DEGRADED`，将直接抹去 `11-21 06:19–07:12Z`（`6.035x`）与 `11-23 14:33–14:44Z`（`4.149x`）、`16:41:45Z`（`2.643x`）等核心快市样本。另外注意：全部 `740` 个数值周期顶层 `data_quality` 均为 `DEGRADED`，原因仅为 `coin_margined_only` 口径下固定附带 `secondary_venues_unavailable:{bybit,okx,binance}` 提示，不影响主指标有效性。
   - **正式可重放校准层（`REPLAYABLE`，`732 / 755 = 96.95%`）**：原始抓包 `SAVED` 且完整可重放（含 `728` 个数值有效周期 + `4` 个合规 fail-closed `UNKNOWN` 周期）。其余 **`23` 个 `PARTIAL / NON_REPLAYABLE` 周期**拆分为：`2026-11-20 19:31–21:17Z` Deribit 公共 API 故障期的 **`11` 个连续 `UNKNOWN` 周期**，以及 **`12` 个单次盘口 HTTP 抖动周期**（保留 `2/3` 有效确认样本供实时监控，但严格排除在 `formal_research_long_ref` 之外）。
   - **空值层（`gfri_confirmed = null`，`15 / 755 = 1.99%`）**：`14` 个 `UNKNOWN` + `1` 个 `DEGRADED + insufficient_confirmations`（`2026-11-21T04:22:52Z`），按原样披露缺口，**零插值、零回填**。
4. **跨日模型与口径稳定性**：全部 `745` 个上游 GEX 有效周期共享唯一 `comparability` 五元组（`code_version=1.1.0`、`deribit_btc_perpetual`、`deribit_coin_only_v1`、`HEDGE_FLOW_NATIVE`、`coin_margined_only`，`NET_GEX_LINEAR` 降级回退 `0` 次），且全部 `740` 个数值周期连续相邻 `snapshot_id` 零重复（`100%` 周期级鲜活更新）。
5. **最小全局对齐说明**：本审计确认 `gfri_cycles_20261118_20261124.jsonl` 可由 `research.esf_v01.gfri_event_analysis` 与后续 ESF v0.1 模块直接读取使用；当前采集器保持严格生产隔离（`GFRI_DECISION_AUTHORITY=SHADOW_ONLY`、`GFRI_PLAYER_INTEGRATION_ENABLED=0`、`GFRI_LIVE_VETO_ENABLED=0`），在 prospective 结算样本达标前不接入实盘否决链路。

---

## 2. 逐日运行覆盖与质量分解（UTC 自然日）

| UTC 日期 | 首末周期 (UTC) | 活跃时长 (h) | 理论周期 (`638s`) | 实测周期 | `SAVED` (`REPLAYABLE`) | `PARTIAL` (`NON_REPLAY`) | `FAILED` | 数值有效 GFRI (`VALID` + `DEGRADED`) | 空值 (`null`) | `HEDGE_FLOW_NATIVE` | OKX 参考可用 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `2026-11-18` | `18:10:27Z` – `23:53:35Z` | 5.83 | 33.4 | **34** | 34 | 0 | 0 | **34** (`33 + 1`) | 0 | 34 | 34 |
| `2026-11-19` | `00:04:14Z` – `23:50:15Z` | 24.00 | 135.4 | **135** | 135 | 0 | 0 | **135** (`128 + 7`) | 0 | 135 | 135 |
| `2026-11-20` | `00:00:54Z` – `23:56:49Z` | 24.00 | 135.4 | **136** | 125 | 11 | 0 | **122** (`113 + 9`) | 14 | 126 | 125 |
| `2026-11-21` | `00:07:24Z` – `23:53:30Z` | 24.00 | 135.4 | **135** | 131 | 4 | 0 | **134** (`122 + 12`) | 1 | 135 | 134 |
| `2026-11-22` | `00:04:07Z` – `23:49:56Z` | 24.00 | 135.4 | **135** | 130 | 5 | 0 | **135** (`131 + 4`) | 0 | 135 | 135 |
| `2026-11-23` | `00:00:33Z` – `23:58:39Z` | 24.00 | 135.4 | **136** | 134 | 2 | 0 | **136** (`127 + 9`) | 0 | 136 | 135 |
| `2026-11-24` | `00:09:16Z` – `07:47:43Z` | 7.80 | 44.0 | **44** | 43 | 1 | 0 | **44** (`42 + 2`) | 0 | 44 | 44 |
| **合计** | **`11-18T18:10Z` – `11-24T07:47Z`** | **133.62** | **755.0** | **755** | **732 (96.95%)** | **23 (3.05%)** | **0** | **740 (98.01%: `696 + 44`)** | **15 (1.99%)** | **745 (98.68%)** | **742 (98.28%)** |

- **影子状态机决策分布（全 `755` 周期）**：`WARMING_UP = 4`（首日冷启动前 4 周期）、`SHADOW_PAUSE_BOTH = 578`、`SHADOW_PAUSE_PUT = 107`、`NO_VETO = 51`、`UNKNOWN = 15`。
- **数值型 `DEGRADED`（`44` 周期）附加降级原因统计**（除固定 `secondary_venues_unavailable` 外）：纯 `price_basis_divergence_degraded` **`31` 次**；`price_basis_divergence_degraded + single_blip_filtered` **`10` 次**；`price_basis_divergence_degraded` 伴随单次盘口超时/接收延迟/单样本缺失各 **`1` 次**（合计 `3` 次）。

---

## 3. 服务资源包络、版本演进与调度间隔统计（`xx-eu`）

### 3.1 `systemd` 单元与执行负载
- **运行单元与配额**：`gfri-shadow-collector.timer` / `.service`，非特权用户 `bitnami`，硬限额 `MemoryMax=128M`、`CPUQuota=25%`，`OnBootSec=180s`、`OnUnitInactiveSec=600s`、`AccuracySec=5s`、`Persistent=false`，工作区 `working_tree_clean=true`。
- **服务健康与耗时**：累计完成 **`755` 次成功运行**，`failed_service_runs = 0`、`journal_err_entries = 0`。单周期服务执行时长：`min = 34.687s`、`p50 = 36.070s`、`p95 = 38.340s`、`max = 84.253s`（最大耗时出现在外部 HTTP 重试/超时等待期）；单次 CPU 时间抽样 `1.119s – 1.310s`。
- **相邻周期冻结间隔（`freeze_at_ms` 差分）**：
  - **全部间隔（`n = 754`）**：`min = 178.00s`、`p10 = 636.00s`、`p50 = 638.00s`、`p90 = 641.01s`、`p95 = 641.99s`、`max = 685.01s`、`mean = 637.98s`。
  - **常规调度间隔（剔除第 3→4 周期版本升级手动重启后，`n = 753`）**：`min = 634.98s`、`p50 = 638.00s`、`p95 = 641.99s`、`max = 685.01s`（`11.42m`），证明全窗口不存在任何超过 12 分钟的调度中断。

### 3.2 部署提交演进与口径一致性
- **Commit `f3e565e48d59`（周期 `#1–#3`，`2026-11-18T18:10:27Z` – `18:31:44Z`）**：初始上线运行 3 个预热周期。
- **Commit `855ff4ba593f`（周期 `#4–#755`，`2026-11-18T18:34:07Z` 升级生效，首周期 `18:34:42Z`，距 `#3` 间隔 `178.00s`）**：显式落盘 `long_ref_scope="REALTIME_MONITORING_ONLY"` 与 `formal_research_scope="FORMAL_REPLAYABLE_RESEARCH"` 元字段；GFRI 核心分子/分母计算公式、`policy_version="gfri_shadow_policy_v11_candidate_1"` 与 `comparability` 五元组在升级前后完全一致。

---

## 4. 账本一致性、原始抓包哈希与缺口完整披露

### 4.1 账本与原始文件校验汇总

| 审计项 | 校验结果 | 关键指标明细 |
| :--- | :---: | :--- |
| `ShadowLedger.verify_consistency()` | **PASS** | `consistent=true`, `ledger_partial_tail=false`, `corrupt_tail_info=null`, `meta_synced=true` |
| 主账本 vs 历史索引行数对齐 | **PASS** | `ledger_count = 755`, `index_count = 755`, `index_malformed = 0`, 末条时间戳均为 `1795506463235` |
| 原始抓包 SHA-256 全量回读 | **PASS** | `6,730` 个原始文件 / `679,243,431` 字节（约 `647.8 MiB`），`sha256_mismatch_count = 0`, `missing_file_count = 0` |
| 归档状态与重放分类 | **PASS** | `SAVED / REPLAYABLE = 732`；`PARTIAL / NON_REPLAYABLE = 23`；`FAILED = 0`（抓包扫描时刻 `754` 目录，拉取打包时刻含末周期共 `755` 目录） |

### 4.2 全部 `23` 个 `PARTIAL` 抓包与全部 `15` 个空值周期分类披露（零插值）

1. **A 类：Deribit 公共 API 故障窗口（`11` 个连续周期，同时属于 `PARTIAL` 与空值 `UNKNOWN`）**
   - **时间窗**：周期 `#280–#290`（`2026-11-20T19:31:07Z` 至 `2026-11-20T21:17:14Z`，历时约 `106 分钟`）。
   - **表现**：Deribit 盘口 3 次采样全部失败（`19:31:07Z` 为 `TimeoutError`，`19:41:45Z–21:17:14Z` 为 `HTTPError`；前 10 周期 `deribit_instrument` 亦返回错误，仅存 `5` 个原始文件 `gex_latest + okx_*`，末周期 `21:17:14Z` 恢复 `deribit_instrument` 存 `6` 个文件）。同期 `19:41:45Z–21:17:14Z` 共 `10` 周期上游 GEXMonitor 亦因源端中断标记 `gex_source_marked_stale` / `gex_source_expired`，采集器全部按契约 fail-closed 输出 `data_quality = UNKNOWN`、`gfri_confirmed = null`。
2. **B 类：单样本 Deribit 盘口 HTTP 瞬时抖动（`12` 个离散周期，属 `PARTIAL / NON_REPLAYABLE` 但保留数值有效 GFRI）**
   - **时间点**：`2026-11-21`（`12:00:23Z` 缺 `s2`、`15:44:07Z` 缺 `s1`、`19:49:10Z` 缺 `s2`、`19:59:45Z` 缺 `s1`）、`2026-11-22`（`00:14:46Z` 缺 `s1`、`00:25:22Z` 缺 `s1`、`07:41:24Z` 缺 `s2`、`18:19:36Z` 缺 `s3`、`22:56:32Z` 缺 `s3`）、`2026-11-23`（`05:41:26Z` 缺 `s3`、`21:50:47Z` 缺 `s3`）、`2026-11-24`（`03:21:03Z` 缺 `s2`）。
   - **表现**：每周期保存 `8/9` 个原始文件，剩余 `2/3` 个 Deribit 盘口样本通过一致性门限（无 `two_sample_severe_divergence`），因此生成有效 `gfri_confirmed`（`11` 个 `VALID`、`1` 个 `DEGRADED`）用于实时监控，同时标记 `NON_REPLAYABLE` 并自动排除出 `formal_research_long_ref`。
3. **C 类：归档完整（`SAVED / REPLAYABLE`）但触发质量门限 fail-closed 的空值周期（`4` 个周期，与 A 类 `11` 个合计构成 `15` 个空值周期）**
   - `#276` `2026-11-20T18:47:49Z`：`price_basis_divergence_exceeded`（GEX 现货与盘口中位价偏离 `> 0.50%`）→ `UNKNOWN`。
   - `#279` `2026-11-20T19:19:42Z`：API 故障前夕盘口陈旧与接收延迟超限（`ob_age_ms = 338,631ms`、`ob_exchange_to_recv_ms = 337,852ms`）→ `UNKNOWN`。
   - `#291` `2026-11-20T21:27:54Z`：Deribit 盘口刚恢复的首周期，上游 GEX 现货尚未追平导致 `price_basis_divergence_exceeded` → `UNKNOWN`。
   - `#330` `2026-11-21T04:22:52Z`：急跌底部反弹瞬间同时触发 `price_basis_divergence_exceeded` 与 `insufficient_confirmations`（两侧 `status = DEGRADED`，顶层 `data_quality = UNKNOWN`，`gfri_confirmed = null`）。

---

## 5. 时钟新鲜度、跨源偏斜与上游 GEXMonitor 稳定性

### 5.1 时钟新鲜度与偏斜分位数（对比契约门限）

| 指标 | 样本范围 | `min` | `p50` | `p90` | `p95` | `p99` | `max` | 契约硬门限 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `gex_age_ms` (s) | 数值有效 (`n=740`) | 37.70s | **187.97s** | 310.09s | 327.60s | 337.77s | 499.04s | `<= 3,600s` (60m) |
| `gex_age_ms` (s) | 全量周期 (`n=755`) | 37.70s | 188.59s | 313.14s | 330.87s | 1,521.15s | 6,320.20s* | 故障期超限已拦截 |
| `ob_age_ms` (s) | 数值有效 (`n=740`) | 0.51s | **1.15s** | 1.87s | 1.98s | 3.48s | 22.46s | `<= 60.0s` |
| `ob_age_ms` (s) | 含盘口全集 (`n=744`) | 0.51s | 1.16s | 1.88s | 1.98s | 11.58s | 338.63s* | `#279` 超限已拦截 |
| `ob_skew_ms` (s) | 数值有效 (`n=740`) | 36.68s | **186.75s** | 308.11s | 326.20s | 336.46s | 497.08s | `<= 900.0s` (15m) |
| `ob_exchange_to_recv_ms` (ms) | 数值有效 (`n=740`) | 14ms | **100ms** | 837ms | 951ms | 1,022ms | 2,635ms | `<= 30,000ms` (30s) |

> \*注：全量最大 `gex_age_ms = 6,320.20s` 出现在 `2026-11-20` API 故障期，`ob_age_ms = 338.63s` 出现在 `#279`（`19:19:42Z`），均已被门限拦截为 `UNKNOWN`（未进入数值有效集）。在全部 `740` 个数值有效周期中，GEX 快照中位年龄仅 **`3.13 分钟`**（P99 `5.63 分钟`），Deribit 盘口中位年龄 **`1.15 秒`**、网络接收延迟中位数 **`100 毫秒`**。

### 5.2 上游 GEXMonitor 模型口径与快照刷新验证
- **固定模型配置**：`methodology_version = "deribit_coin_only_v1"`、`universe_policy = "coin_margined_only"`、`hf_model_version = "2.0"`（`full_option_delta_repricing` 精确阶梯重定价曲线 `hedging_flows.price_only_curve`，`iv_mode="fixed"`、`time_forward_hours=0`）。
- **期权覆盖广度**：在 `754` 个含 GEX 元数据的周期中，纳入计算的 Deribit BTC 期权合约数 `instrument_count` 介于 **`759 – 891`**（中位数 **`827`**），覆盖到期日个数 `expiry_count` 介于 **`10 – 12`**（`10` 个占 `48` 周期、`11` 个占 `352` 周期、`12` 个占 `354` 周期，随每日 `08:00 UTC` 交割与新周期挂牌自然滚动）。
- **无静默快照冻结**：`754` 个周期共出现 **`746` 个独立 `snapshot_id`**；仅有的 `8` 次相邻周期重复全部发生在 `2026-11-20T19:52:21Z–21:17:14Z` 故障停摆期（均已标为 `UNKNOWN`）。**在全部 `740` 个数值有效周期中，相邻周期 `snapshot_id` 重复次数为 `0`（100% 每周期更新）。**
- **已知源端固有滞后说明**：Deribit 公共期权持仓量（OI）存在文档已登记的 `8h` 披露滞后（审计契约显式记录 `oi_source_lag_hours = 8`），而现货价格与盘口深度为秒级实时刷新。

---

## 6. 复现实验入口

在工作树根目录运行以下命令即可确定性复现本报告全部统计指标与单元测试断言：

```powershell
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe -m research.esf_v01.gfri_event_analysis
C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe -m pytest research/esf_v01/tests/test_gfri_event_analysis.py -v
```
