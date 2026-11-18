# GFRI v1.1 Shadow Collector Deployment & Operations Runbook

> **CURRENT STATE**: **NOT INSTALLED / NOT ENABLED**.  
> `collector_enabled = false`, `decision_authority = SHADOW_ONLY`, `player_integration_enabled = false`, `live_veto_enabled = false`.

## 1. Deployment Artifacts ([`deploy/gfri_v11/`](../../deploy/gfri_v11/gfri_v11.env.example))

| File | Role |
| :--- | :--- |
| `gfri_v11.env.example` | Default-off config (`GFRI_COLLECTOR_ENABLED=0`, `GFRI_DECISION_AUTHORITY=SHADOW_ONLY`, `15s` confirm spacing, `60s` OB age limit, `30s` receive-lag limit) |
| `gfri-shadow-collector.service` | Bounded systemd `oneshot` service (`MemoryMax=128M`, `CPUQuota=25%`, `ProtectSystem=strict`, `NoNewPrivileges=true`, `PYTHONPATH=/opt/gfri-shadow/current`) |
| `gfri-shadow-collector.timer` | 10-minute cycle (`OnUnitInactiveSec=600`, `Persistent=false`) — **not installed until external approval** |
| `preflight_check.py` | Verifies env safety locks AND `--host-check --network-probe` (Python `>=3.12`, module imports, writable dirs, disk capacity, Linux NTP sync, upstream HTTP) |
| `enable_collector.sh` / `disable_collector.sh` / `rollback_gfri_v11.sh` | Idempotent lifecycle scripts with `bitnami:bitnami` directory ownership |

## 2. Storage Budget, Retention & Crash Recovery (P0-6, P0-7, P1-1)

| Artifact | Size / Rate (10-min cadence) | Budget & Policy |
| :--- | :--- | :--- |
| `gfri_shadow_ledger.jsonl` | `~12–17 KB/cycle` (`~1.8 MB/day`, `~115 MB / 60d`) | **Append-only permanent audit ledger**; never auto-deleted. |
| `gfri_history_index.jsonl` | `~350 B/cycle` (`~50 KB/day`, `~3 MB / 60d`) | Streaming 60d read keeps process RAM `< 10 MiB` inside `MemoryMax=128M` (`8.44 MiB` peak measured for 8,640 full records). |
| `raw_captures/cycle_*/` | `~0.6 MB/cycle` uncompressed (`~85 MB/day`, `~8–12 MB/day` gzip) | External to Git; non-destructive gzip rotation after 7 days (`raw_capture_retention_days=7`). Write failure emits explicit `FAILED`/`PARTIAL` in `raw_archive_status`, never swallowed. |
| `deribit_spec_cache.json` | `< 500 B` | Versioned (`schema_version=1`), `24h` TTL verified contract cache (`BTC-PERPETUAL`, `contract_size=10.0`, `usd`). |
| Locking & Crash Recovery | POSIX `fcntl.flock` / Windows PID+age stale break (`60s`) | On crash between ledger append and `gfri_policy_state.json` write, `load_or_recover_policy_state()` reconstructs streaks from the latest ledger audit tail. |

## 3. Target Host Preflight & Controlled Enablement (Post-Acceptance Only)

```bash
# 1. Stage env file outside repo tree (owned by bitnami)
sudo install -d -o bitnami -g bitnami -m 0755 /opt/gfri-shadow /opt/gfri-shadow/state /opt/gfri-shadow/raw_captures
sudo install -o bitnami -g bitnami -m 0644 deploy/gfri_v11/gfri_v11.env.example /opt/gfri-shadow/gfri_v11.env

# 2. Run config + Linux host preflight (WITHOUT starting timer)
python3 deploy/gfri_v11/preflight_check.py --env-file /opt/gfri-shadow/gfri_v11.env --require-shadow-only --require-disabled --host-check --network-probe

# 3. Optional one-off isolated dry run before enabling systemd timer
PYTHONPATH="$(pwd)" GFRI_STATE_DIR=/tmp/gfri_dryrun python3 -m research.gfri_v11.collector --once --interval-sec 5

# 4. ONLY after external sign-off: enable 10-minute shadow timer
sudo bash deploy/gfri_v11/enable_collector.sh
```

## 4. Incident Handling, Pause & Rollback

- **Upstream outage / stale book / disk error**: Collector appends `UNKNOWN` or `complete_replayable=false` audit record and exits `0` without disrupting AI player / dossier pipelines.
- **Immediate pause**: `sudo bash deploy/gfri_v11/disable_collector.sh` (disables timer & sets `GFRI_COLLECTOR_ENABLED=0`, preserving all state).
- **Full unit removal**: `sudo bash deploy/gfri_v11/rollback_gfri_v11.sh` (removes systemd unit files & preserves/resets config cleanly).
