# B｜迁移等价性与可靠性验证报告

**验证裁决：`READY_FOR_SERVER_CUTOVER`**（全部离线确定性等价、全链路 E2E、发布/回滚生命周期与 `xx-eu` 隔离双轨实测通过；正式切换按文档 D 执行窗口停 FMZ 后切流）。

---

## 1. 算法与契约保真机制

`tools/astra_signal_producer_v1.py`（`astra-signal-producer@1.0.0`）直接加载未修改的 `neutral_regulation_demo_fmz.py`（`2.0.6`，LF SHA-256 `6d9a34e1d0cd4d11fa4d6c8f8edae9165d9190beb672fafc63e3dfd9c8072132`），仅替换 FMZ 运行时外设（日志、休眠、状态推送与路径参数），保留全部因子计算与状态机：
- **只读与无伪造底线：** 强制 `read_only_demo=True`（任何 `False` 覆写直接抛出 `READ_ONLY_DEMO_REQUIRED`）。`EvaluationSnapshot` 顶层新增 `producer_identity`（`platform="xxproject_server_native"`, `fmz_platform_used=False`），严格保留 `clock_provenance` 精确 7 键不被污染；`signal_review.jsonl` 更新 `provenance`/`delivery` 后自动剔除旧 `integrity` 并调用核内 `_audit_integrity` 重算 `record_hash`。
- **缓存与文件卫生：** 接管 `gex_info_cache.json` 与 `macro_factor_cache.json` 为临时文件+`fsync`+`os.replace` 原子写；启动时修复 `signal_review.jsonl` 残缺尾行换行，隔离全损坏的 `snapshots.jsonl`。

---

## 2. 确定性等价与故障恢复矩阵

| 验证项目 | 覆盖范围与测试条件 | 实测结果 |
| :--- | :--- | :--- |
| 确定性核心等价 (`DemoRuntime` vs `NativeSignalRuntime`) | 同一 mock 行情流与冻结时钟驱动 5 阶段：720 根 1m K 线回补启动、5m 窗内稳态节流、SpaceGate 正负切换 (`STRUCTURE_ELIGIBLE` <-> `STRUCTURE_BLOCKED`)、Neutral Repair 状态机、23:00 BJT (`15:01 UTC`) 固定轮 | `test_astra_signal_producer_v1.py` **7 passed (1.23s)**；全部因子数值差 `<= 1e-9`，枚举、`clock_provenance`、`SignalSessionPremiseDurabilityContext@1.0.0`、`temporal_durability` 与 `record_hash` 100% 通过 |
| 状态持久化与分窗恢复 | 检查点覆盖短重启（`<=10m` 恢复 `bars_tail` + `cvd_hist` + `rr_hist` + `nr_context` + 保留原 `source_ts_ms` 的 `gex_state`）、中停机（`10m–24h` 跳过 `bars_tail` 触发 1m 回补）、超期（`>24h` 仍恢复 `seen_episode_ids`/`seen_fixed_round_slots` 防重复发卡） | 短/中/超期 3 分支实测全过；同日重启不重复输出 23:00 固定轮审计卡 |
| 信号中断、上游断连与 429 熔断 | `SIGTERM`/`stop_event` 安全保存检查点并写 `state="stopped"`；Binance/GEX 全失败不崩溃；Deribit `public/ticker` HTTP 429 立即触发局部 + `ASTRA_DERIBIT_COOLDOWN_FILE` 共享冷却并拦截后续请求 | `run(max_loops=1)` 存活上游异常；429 后 `deribit_429_cooldown_active` 短路拦截，`0` 次多余 HTTP；无任何 `Log`/`exchange`/`_G` 全局依赖 |
| 下游全链路 E2E (`NativeSignalRuntime` -> 牌局记分 + 物化) | 原生输出经 `fact_context` -> `astra_wpem_native_v1` -> `DataEngine`/`map_data` -> `Workbench` -> `research_archive` -> `ARD` -> `AI Player (prewarm->auto_deal Accept->SHADOW_LOCK_CREATED->sync->settle)` -> `materialize_signal_cards` (`SESSION_CONTEXT_REQUIRED=1`) -> 跨进程冷却 | `test_server_native_e2e_chain.py` **1 passed (2.69s)**；`wpem_native` 无契约/时钟缺口，`actual_execution_net_btc=None`（`SHADOW_ONLY`），`compat_backfill_applied` 非 `True`，Workbench  discovery/`refresh_expiry_gamma` 与 `research_archive.Deribit.book` 均服从跨进程 429 冷却 |

---

## 3. 合流 `6311071c` 后回归与演练汇总

| 测试批次 | 命令与范围 | 通过 / 失败 / 跳过 | 说明 |
| :--- | :--- | :--- | :--- |
| Post-Merge Suite A（新套 + `6311071c` P0-REST/Player 核心套） | `test_astra_signal_producer_v1`, `test_server_native_e2e_chain`, `test_xxproject_server_release`, `test_deribit_discovery_reliability`, `test_astra_workbench_v20`, `test_astra_research_archive_v1`, `test_astra_player_{e2e_v13,tracking_v13,dossier_v1}`, `test_astra_release_check_v1` | **131 passed, 0 failed, 0 skipped** (120.65s) | 在合流 `6311071c` 后运行；完整验证 `min_book_interval_s=1.05`、`_cached_book`、`retry_pending_shadow_locks`、并发视图隔离、`astra_deribit_cooldown_v1` 与统一发布包生命周期 |
| Post-Merge Suite B（FMZ 核心 + MAP/DataEngine/KPF/物化） | `test_fmz_{205,206,23,204,20,cleanup_v244}`, `test_astra_map_data_server_v24`, `test_astra_data_engine_v23`, `test_astra_kpf_light_v1`, `test_materializer_tail_window` | **97 passed, 0 failed, 0 skipped** (11.44s) | 在合流 `6311071c` 后运行；核心算法契约与历史物化器（`SESSION_CONTEXT_REQUIRED=1`）全绿 |
| Post-Merge 4 独立 FMZ 契约脚本 | `test_fmz_157_{fixed_analysis_round,producer_contract}`, `test_fmz_161_near_term_context`, `test_fmz_162_gex_time_semantics` | **4/4 PASS** (exit 0) | 在合流 `6311071c` 后运行；157/161/162 契约全绿 |
| 基线外围下游辅助套（合流前运行，合流未改动这些文件） | `data_{server,retention,workbench}`, `server_data_package`, `player_input`, `anchor_relative` 等合集 | **269 passed, 1 failed, 1 skipped** (97.77s) | 唯一 skip：`test_astra_data_retention_v23.py:225`（Windows 无 symlink 权限）。唯一 fail：`test_astra_anchor_relative_v1.py:275`（`607d66fe`/`6311071c` 基线上先于迁移已存在的 `ai_input.md` 冻结文案漂移）。 |
| Linux 隔离安装/升级/回滚演练（最终版见 Step 3 复核） | `xx-eu` `/tmp` 真机测试：`preflight -> install v1 -> verify -> seed 状态/密钥 -> upgrade v2 -> verify -> rollback -> status` | **已于初版及最终合流后重跑验证通过** | 真实 Linux POSIX `current`/`previous` 符号链接切换成功，`gex_runtime_venv_present=true`，`/var/lib/...` 与 `/etc/signal-audit/llm.env` 原样保留 |

---

## 4. `xx-eu` 隔离双轨实测观测（`/tmp/xxproject-native-verify-live/`，非 15:50–16:35 UTC 交易窗）

- **7 轮连续实测（17:02:01–17:08:27 UTC）：** `last_tick_ok=true`，数据源 `binance=OK, deribit=OK, gex=OK`（接入 `http://127.0.0.1:8000`），成功触发 startup + 5m interval 2 笔真实 `EvaluationSnapshot`；期间因与 FMZ `668422` 同 IP 并发触发 2 次瞬态 Deribit 429，熔断退避后自动恢复且零崩溃（正式切流停 FMZ 后可消除双轨并发争用）。
- **2 轮重启恢复（68.3s 间隔）：** `checkpoint_restored` 返回 `restored=true`, `bars_restored=true`, `cvd_hist_4h_len=7`, `gex_effective_restored=true`, `fmz_platform_used=false`。
- **与 live Bot `668422` 并行对照（时间差 127.31s）：** 双方 `schema_version` (`nrd.schema.v1.0.0`)、`demo_version` (`2.0.6`)、`space_gate_state` (`STRUCTURE_BLOCKED`)、`net_gamma.source` (`gexmonitorapi.gex_board.total_net_gex`)、`net_gamma.value` (`-76490334.74`)、`anchor_flip` (`61446.201582413574`)、`edb_lean` (`BULLISH`)、`clock_provenance` 7 键及 `SignalSessionPremiseDurabilityContext@1.0.0` 完全一致。原生快照经 `fact_context`、`dp.qualify_fmz` (`usage="current_fact" -> USABLE`) 及 `from_evaluation_snapshot` 投影均无缺口。测试完成后已完整清除临时目录（`CLEANUP_OK`）。
