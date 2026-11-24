# Server-Native Signal Producer (`astra-signal-producer`)

Runs `tools/astra_signal_producer_v1.py` wrapping the unmodified signal core (`signal_core/neutral_regulation_demo_fmz.py`) directly under `systemd` (`Type=notify`, `WatchdogSec=300`) with zero FMZ platform dependency.

- **Outputs**: `<logs_dir>/snapshots.jsonl`, `<logs_dir>/signal_review.jsonl`, `<logs_dir>/{space_gate_state,fixed_analysis_round_state,gex_info_cache,macro_factor_cache}.json`
- **State / Health**: `<state_dir>/runtime_checkpoint.json`, `<state_dir>/producer_health.json`
- **Live-verified pacing & restore defaults**: `ASTRA_SIGNAL_DERIBIT_TICKER_INTERVAL_MS=1200`, `ASTRA_SIGNAL_DERIBIT_WALL_TIME_MS=48000`, `ASTRA_SIGNAL_BARS_RESTORE_MAX_AGE_MS=90000`
- **Safety boundaries**: `read_only_demo=True`, `execution_allowed=False`, `fmz_platform_used=False`, shared Deribit 429 cooldown via `ASTRA_DERIBIT_COOLDOWN_FILE`.
