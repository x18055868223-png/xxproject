# GFRI v1.1 第二轮外部验收最终收口与启动验收单

## 最终裁定：`READY_FOR_SHADOW_COLLECTION`

> **可连续 60 天运行结论**：**可以**。在目标 Linux 主机 `xx-eu` (`vmi3648530`) 实测 `bitnami` 沙箱权限、`Python 3.12.3`、NTP 同步、公网三端连通、单轮实时采样 Peak RSS **`36.8 MiB`**、8,640 条（60 天）重索引/流式读取 Peak RSS **`47.9 MiB`**（远低于 `MemoryMax=128M`）、原始 HTTP 与磁盘 SHA-256 逐文件一致、`ShadowLedger` 账本/索引故障自愈零重复，以及 `enable_collector.sh` 失败自动回滚。
> **当前安全锁**：`collector_enabled = false`、`decision_authority = SHADOW_ONLY`、`player_integration_enabled = false`、`live_veto_enabled = false`、`systemd timer` 保持未安装/未启用，待最终人工启动指令。

---

## 一、第二轮最终收口项（A / B / C）核验清单

| 收口项 | 核心实现 | 测试与实机证据 | 结论 |
| :--- | :--- | :--- | :--- |
| **A. 主账本与轻量索引一致性** | `history.py` 增加 `.meta.json` 偏移/计数检查点与行级 `entry_id`，支持 O(tail) 增量补洞与流式原子重建，绝不改写 append-only 主账本 | `test_section_a_ledger_index_consistency_and_fault_recovery`、`test_section_a_b_end_to_end_index_fault_injection_and_archive_status` | **PASS**（账本成功+索引注入故障+重启补洞零重复，损坏索引自愈） |
| **B. 监控样本与可回放研究样本分离** | `history.py` / `policy.py` / `collector.py` 持久化 `raw_archive_status_label`、`complete_replayable`、`sample_replay_class` (`REPLAYABLE` / `NON_REPLAYABLE`)；`require_replayable_only=True` 严格过滤研究样本 | `test_section_b_replayable_vs_non_replayable_samples` | **PASS**（覆盖 `SAVED`/`PARTIAL`/`FAILED`/`NOT_CONFIGURED`，索引重建标签不丢，监控披露完整） |
| **C. 目标 Linux 主机实机预检** | `deploy/gfri_v11/enable_collector.sh` 增加 `ERR` trap 自动回滚；在 `xx-eu` 通过 `systemd-run` (`User=bitnami`, `MemoryMax=128M`, `ProtectSystem=strict`) 实机验证 | `xx-eu` preflight、9 文件 SHA-256 实盘回放、`enable_collector.sh` 回滚脚本演练 | **PASS**（NTP `SYNCED`、`56,070.1 MiB` 可用空间、Live RSS `36.8 MiB`、60d RSS `47.9 MiB`、回滚 `GFRI_COLLECTOR_ENABLED=0`） |

---

## 二、启动验收单（Startup Acceptance Sheet）

- **测试结果**：`pytest research/gfri_v11/tests -v` -> **`51 passed`**；Snap1 基准复算一致（`3.034746 / 3.201071`）；`bash -n deploy/gfri_v11/enable_collector.sh` 通过。
- **实盘样本证据**：[`samples/gfri_v11_real_audit_sanitized.json`](samples/gfri_v11_real_audit_sanitized.json)（`2026-11-18T17:36Z` `xx-eu` 沙箱实跑，PUT `+5.40x` / CALL `+5.11x`，`raw_archive_status_label=SAVED`，`sample_replay_class=REPLAYABLE`，`shadow_decision=WARMING_UP`）。
- **主机安全态**：`xx-eu` 已清理临时验证目录，`systemctl list-unit-files '*gfri*'` 为 `0 unit files`；生产 `.worktrees/player-dossier-v13` 与 Prompt A 无任何改动。
- **启动授权步骤**：外部复审确认后，在 `xx-eu` 按 [`GFRI_V11_DEPLOYMENT.md`](GFRI_V11_DEPLOYMENT.md) 安装 `/opt/gfri-shadow/gfri_v11.env` 并执行 `sudo bash deploy/gfri_v11/enable_collector.sh` 开启 10 分钟只读影子采集。

---

## 三、第一轮 P0 / P1 修复矩阵（已关闭）

| 问题编号 | 修复位置 | 回归测试名称 | 实测结果 |
| :--- | :--- | :--- | :--- |
| **P0-1 合约单位失效** | `core.py` (`check_deribit_instrument_unit`, `validate_verified_spec_cache`, `evaluate_single_observation`), `collector.py` (`_save_verified_deribit_spec_cache`, `deribit_spec_cache.json`) | `test_p0_1_deribit_instrument_and_verified_spec_cache_fail_closed`; `test_p01_spec_cache_fallback_and_expired_cache_fails_closed` | **PASS**（无合约/错规格/过期缓存均 fail-closed 为 `UNKNOWN`；24h 签名缓存可复用） |
| **P0-2 单侧隔离不遮蔽** | `core.py` (`global_data_quality`, `partial_valid_side` in both `evaluate_single_observation` & `confirm_cycle_observations`), `policy.py` (`_evaluate_side_policy`, `_compute_partial_valid_side`) | `test_p0_2_partial_side_validity_call_valid_while_put_truncated`; `test_p02_side_independence_during_partial_unknown_and_audit_archive`; `test_p02_end_to_end_single_side_truncation_preserves_valid_side_policy` | **PASS**（PUT 截断为 `UNKNOWN` 时 CALL `3.75x` 保持 `VALID` 与 `SHADOW_PAUSE`，端到端保真） |
| **P0-3 盘口独立时效门禁** | `config.py` (`max_ob_age_ms=60000`, `max_ob_receive_lag_ms=30000`, `confirm_interval_sec=15.0`), `core.py` (`compute_directional_depth`, `timestamps.ob_age_ms/ob_received_age_ms/ob_exchange_to_recv_ms`) | `test_p0_3_order_book_age_and_receive_lag_gates` | **PASS**（`ob_skew_ms=0` 且双源同落后 20 分钟必失败；超 30s 接收延迟失败） |
| **P0-4 两样本冲突保护** | `core.py` (`_confirm_side`: `two_sample_severe_divergence` + `upper_outliers` 孤立异常判定) | `test_p0_4_two_sample_divergence_and_confirmation_sequences`; `test_missing_spot_fails_closed_and_two_of_three_high_is_not_ratio_blip` | **PASS**（`[0.5,8.0]`/`[1.0,5.0]` -> `INSUFFICIENT_CONFIRMATIONS` 且保留 `gfri_fast_max`；`[4.0,4.1]`/`[0.5,4.0,4.1]` -> `CONFIRMED`, `single_blip_detected=False`） |
| **P0-5 原生元数据严格举证** | `core.py` (`extract_gex_snapshot` 强制 `scenario_sets.price_only` 存在且 `iv_mode="fixed"`、`time_forward_hours=0`、`units.hedging_usd="usd_notional"`) | `test_p0_5_missing_price_only_metadata_rejects_native_tier` | **PASS**（删元数据拒绝 `NATIVE_PRICE_ONLY`，退化为 `NET_GEX_LINEAR / DEGRADED` 或 `UNKNOWN`，绝不冒充 `spot_linked_12h`） |
| **P0-6 有界历史索引与容量** | `history.py` (`gfri_history_index.jsonl`, `extract_index_entry`, `ShadowLedger.load_history_window`), `collector.py` (`load_history_window`) | `test_p06_bounded_memory_index_and_bit_identical_refs` | **PASS**（8,640 条 115.71 MiB 全量记录实测峰值内存 **`8.44 MiB`** << `128M`；4h/60d 计算与全量加载 bit-identical；原始 `gfri_shadow_ledger.jsonl` 永久追写不删历史） |
| **P0-7 归档显式状态与双哈希** | `collector.py` (`_write_archive_entry` `os.O_BINARY`, 落盘 SHA-256 回读核验, `raw_http_sha256` vs `reserialized_json_sha256`, `manifest.json`), `policy.py` (`raw_archive_status`) | `test_p07_raw_archive_hashes_manifest_and_failure_statuses` | **PASS**（覆盖正常、阻塞目录 `FAILED`、单文件/manifest 失败 `PARTIAL`；`complete_replayable=False` 与 `failure_reasons` 同步） |
| **P1-1 崩溃恢复与防死锁** | `history.py` (`fcntl.flock` / Windows PID+60s 过期锁恢复), `policy.py` (`reconstruct_policy_state_from_ledger`), `collector.py` (`_load_tail_audit_records`, `load_or_recover_policy_state`) | `test_p11_crash_recovery_from_ledger_and_stale_lock_recovery`; `test_p11_crash_recovery_and_p13_host_preflight` | **PASS**（追加账本后 `policy_state` 损坏或陈旧可无损重建连击计数；过期 `.lock` 可自愈） |
| **P1-2 冻结时钟语义区分** | `policy.py` (`cycle_asof_ms`, `clock_role="SHADOW_HIGH_FREQ_CYCLE"`), `history.py` (`select_snapshot_asof_episode`) | `test_p12_select_snapshot_asof_episode_excludes_future_and_honors_archive` | **PASS**（10 分钟影子节奏与 14:00 UTC `episode_freeze` 严格分离，绝不事后回填未来快照） |
| **P1-3 宿主机预检与演练** | `deploy/gfri_v11/preflight_check.py` (`--host-check --network-probe`), `deploy/gfri_v11/gfri_v11.env.example` | `test_p11_crash_recovery_and_p13_host_preflight`; CLI `--host-check --network-probe` | **PASS**（校验 Python 3.12、导入、权限、磁盘、Linux 内存/NTP 与三端公网联通，不启 timer） |

---

## 四、独立 Reviewer 复核与基线核验

- **第一轮 Reviewer（`4256a8da`）**：二审逐行复核并给出 **`APPROVED`**；第二轮最终收口（A/B/C 改动）提交前由独立 `reviewer` 复审 `7f7577eb..HEAD`。
- **核心数值复算一致性**：对 `2026-11-18` Snap1 实盘快照复算结果完全一致（`gfri_down_1pct = 3.034746`，`gfri_up_1pct = 3.201071`，`data_quality = DEGRADED`）。
- **上线约束**：未获外部书面/明确启动批准前，严禁在生产环境启用 `gfri-shadow-collector.timer` 或将 `decision_authority` 切换出 `SHADOW_ONLY`。
