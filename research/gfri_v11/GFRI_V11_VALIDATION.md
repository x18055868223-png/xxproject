# GFRI v1.1 Validation & Experimental Evidence Report

## 1. Automated Regression Suite Results (47 Tests Passing)

Executed in repository root against `.worktrees/gfri-v11`:
- **Test Suite**: `C:\Users\Xu\AppData\Local\Programs\Python\Python312\python.exe -m pytest .worktrees/gfri-v11/research/gfri_v11/tests -v` -> **`48 passed`** (`0 failed`, `0 skipped`).
- **Host & Lock Preflight**: `preflight_check.py --require-shadow-only --require-disabled --host-check --network-probe` -> **`HOST_PREFLIGHT_OK` / `PREFLIGHT_OK`**.
- **Default-Off Scheduled Guard**: `python -m research.gfri_v11.collector --scheduled` -> exit `0` (`COLLECTOR_DISABLED (GFRI_COLLECTOR_ENABLED=0; SHADOW_ONLY)`).
- **Bash Static Syntax**: `bash -n enable_collector.sh disable_collector.sh rollback_gfri_v11.sh` -> exit `0`.

## 2. P0 & P1 Fix Closure Matrix

| Issue ID | Fix Location & Mechanism | Regression Test Coverage |
| :--- | :--- | :--- |
| **P0-1 Contract Spec Fail-Closed** | `core.py` (`validate_verified_spec_cache`, `evaluate_single_observation`), `collector.py` (`deribit_spec_cache.json`, `24h` TTL). No default `"usd"` assumption when `get_instrument` fails without valid cache. | `test_p0_1_deribit_instrument_and_verified_spec_cache_fail_closed`, `test_p01_spec_cache_fallback_and_expired_cache_fails_closed` |
| **P0-2 Single-Side Isolation** | `core.py` (`global_data_quality`, `partial_valid_side`), `policy.py` (`_evaluate_side_policy`, `_compute_partial_valid_side`). Valid CALL/PUT survives opposite-side truncation while top-level stays `UNKNOWN`. | `test_p0_2_partial_side_validity_call_valid_while_put_truncated`, `test_p02_side_independence_during_partial_unknown_and_audit_archive` |
| **P0-3 Independent OB Freshness** | `core.py` (`compute_directional_depth` validates `ob_age_ms <= 60s` and `ob_exchange_to_recv_ms <= 30s`), `config.py` (`confirm_interval_sec=15s`). Rejects 20m stale book even when `ob_skew_ms == 0`. | `test_p0_3_order_book_age_and_receive_lag_gates` |
| **P0-4 Two-Sample Conflict Guard** | `core.py` (`_confirm_side` `two_sample_severe_divergence` guard for `count == 2`, `spread_ratio > 2.0`). Blocks `[0.5, 8.0]` / `[1.0, 5.0]` from `CONFIRMED` while preserving `gfri_fast_max`; confirms `[4.0, 4.1]` & `[0.5, 4.0, 4.1]`. | `test_p0_4_two_sample_divergence_and_confirmation_sequences`, `test_missing_spot_fails_closed_and_two_of_three_high_is_not_ratio_blip` |
| **P0-5 Strict `price_only` Proof** | `core.py` (`extract_gex_snapshot`) requires explicit `scenario_sets.price_only` dict (`iv_mode="fixed"`, `time_forward_hours=0`, `units.hedging_usd="usd_notional"`); missing metadata degrades to `NET_GEX_LINEAR` / `DEGRADED`. | `test_p0_5_missing_price_only_metadata_rejects_native_tier` |
| **P0-6 Bounded History & Memory** | `history.py` (`gfri_history_index.jsonl`, streaming `ShadowLedger.load_history_window`, `extract_index_entry`). Keeps full `gfri_shadow_ledger.jsonl` intact. | `test_p06_bounded_memory_index_and_bit_identical_refs` |
| **P0-7 Explicit Archive Status & Dual Hashes** | `collector.py` (`_write_archive_entry` uses `os.O_BINARY` + exact HTTP `raw_bytes`, disk SHA-256 readback, `raw_http_sha256` vs `reserialized_json_sha256`, `manifest.json`), `policy.py` (`raw_archive_status`). | `test_p07_raw_archive_hashes_manifest_and_failure_statuses` |
| **P1-1 Crash & Stale Lock Recovery** | `history.py` (`fcntl.flock` POSIX / stale PID+age break Windows), `policy.py` (`reconstruct_policy_state_from_ledger`), `collector.py` (`_load_tail_audit_records`). | `test_p11_crash_recovery_from_ledger_and_stale_lock_recovery`, `test_p11_crash_recovery_and_p13_host_preflight` |
| **P1-2 Freeze Clock Semantics** | `policy.py` (`cycle_asof_ms`, `clock_role="SHADOW_HIGH_FREQ_CYCLE"`), `history.py` (`select_snapshot_asof_episode`). | `test_p12_select_snapshot_asof_episode_excludes_future_and_honors_archive` |
| **P1-3 Host Readiness Preflight** | `deploy/gfri_v11/preflight_check.py` (`run_host_checks`: Python, imports, perms, disk, RAM, NTP, live HTTP probe). | `test_p11_crash_recovery_and_p13_host_preflight` |

## 3. Parity, Capacity (60d / 8,640 Cycles) & Fresh Live Verification

1. **Qualified Sample Parity Check**: Re-evaluating v1.0 `2026-11-18` Snap1 raw capture (`gex_latest_20261118.json` + `deribit_btc_perpetual_book_depth1000_20261118.json` + `deribit_btc_perpetual_instrument_20261118.json`) yielded **identical** qualified GFRI values: `gfri_down_1pct = 3.034746`, `gfri_up_1pct = 3.201071`, `data_quality = DEGRADED`, `deribit_spec_source = live_instrument`.
2. **60-Day Capacity Benchmark (`tracemalloc`)**: Streaming `8,640` full `gfri_audit_v11` records (`115.71 MiB` JSONL file) through `ShadowLedger.load_history_window(reference_ms=now, max_age_ms=60d)` measured **`current = 8.32 MiB`, `peak = 8.44 MiB`** (< `7%` of `systemd MemoryMax=128M`) with bit-identical `compute_near_term_ref` and `compute_long_term_ref` outputs.
3. **Fresh End-to-End Live Dry Run (`2026-11-18T16:59Z`)**:
   - Output artifact: [`samples/gfri_v11_real_audit_sanitized.json`](samples/gfri_v11_real_audit_sanitized.json) (`cycle_asof_ms = 1795021161079`).
   - **Live Basis Guard Triggered**: Rapid spot move (`gex_spot_usd = $62,439.79` vs `ob_ref_price_usd = $62,622.75`, `0.293%` > `0.20%`) accurately triggered `price_basis_divergence_degraded` alongside `secondary_venues_unavailable:bybit,okx,binance`.
   - **Clocks & Telemetry**: `gex_age_ms = 76,319`, `ob_age_ms = 4,453`, `ob_received_age_ms = 3,681`, `ob_exchange_to_recv_ms = 772`, `ob_skew_ms = 71,866` — all well inside `60s` OB age / `30s` recv-lag gates.
   - **Confirmed GFRI**: PUT `-$127.87M / $24.00M` (`592` bid levels) -> **`+5.33x`** (`fast_max 5.55x`); CALL `+$127.93M / $23.97M` (`952` ask levels) -> **`+5.34x`** (`fast_max 5.37x`); OKX ref `$80.53M / $88.71M` (`1.59x / 1.44x`); `shadow_decision = WARMING_UP`.
   - **Isolation**: `.worktrees/player-dossier-v13` clean, zero Prompt A / dossier / player edits, zero scheduled timers active.
