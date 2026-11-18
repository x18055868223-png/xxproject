# GFRI v1.1 外部验收反馈修复与最终预运行裁定报告

## 最终裁定：`READY_FOR_SHADOW_COLLECTION`

> **锁定隔离与运行态**：`collector_enabled = false`、`decision_authority = SHADOW_ONLY`、`player_integration_enabled = false`、`live_veto_enabled = false`。全部 P0-1～P0-7 与 P1-1～P1-3 已关闭并通过独立复核与全量测试，现严格保持**默认关闭、等待外部复审批准**状态；未修改生产 `.worktrees/player-dossier-v13`、Prompt A、AI 牌手正式输入及交割卷宗。

---

## 一、P0 / P1 修复位置、测试名称与验证结果清单

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

## 二、独立 Reviewer 复核与验证证据

- **Reviewer 一审（`4256a8da`）**：检出 `confirm_cycle_observations` 未透传 `global_data_quality` / `partial_valid_side`（High #1）、`_write_cycle_manifest` 失败未同步 `audit_record["raw_archive_status"]`（Medium #2）及 3 项 Low 细节。
- **Reviewer 二审（`4256a8da`）**：逐行核对全部修复后正式给出 **`APPROVED`**（零 High / Medium 遗留阻塞）。
- **核心数值复算一致性**：修复前后对 `2026-11-18` Snap1 实盘快照复算结果完全一致（`gfri_down_1pct = 3.034746`，`gfri_up_1pct = 3.201071`，`data_quality = DEGRADED`）。
- **端到端最新实盘干跑（`2026-11-18T16:59Z`）**：产出 [`samples/gfri_v11_real_audit_sanitized.json`](samples/gfri_v11_real_audit_sanitized.json)（PUT `+5.33x` / CALL `+5.34x`，`ob_age_ms = 4,453`，`ob_exchange_to_recv_ms = 772`，实测抓到 Spot 与盘口中价 `0.293%` 偏离触发 `price_basis_divergence_degraded`）。
- **目标 Linux 主机待办（批准启用前）**：在目标服务器一次性运行 `python3 deploy/gfri_v11/preflight_check.py --env-file /opt/gfri-shadow/gfri_v11.env --require-shadow-only --require-disabled --host-check --network-probe` 实测 POSIX `fcntl.flock`、`timedatectl` NTP 与 `bitnami` 目录权限；未获外部批准前严禁运行 `enable_collector.sh` 或接入 AI 牌手。
