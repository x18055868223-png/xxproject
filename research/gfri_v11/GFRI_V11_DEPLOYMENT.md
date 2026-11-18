# GFRI v1.1 Shadow Collector Deployment & Operations Runbook

> **CURRENT STATE**: **NOT INSTALLED / NOT ENABLED**.  
> `collector_enabled = false`, `decision_authority = SHADOW_ONLY`, `player_integration_enabled = false`, `live_veto_enabled = false`.

## 1. Deployment Artifacts ([`deploy/gfri_v11/`](../../deploy/gfri_v11/gfri_v11.env.example))

| File | Role |
| :--- | :--- |
| `gfri_v11.env.example` | Default-off environment config (`GFRI_COLLECTOR_ENABLED=0`, `GFRI_DECISION_AUTHORITY=SHADOW_ONLY`, `GFRI_PLAYER_INTEGRATION_ENABLED=0`, `GFRI_LIVE_VETO_ENABLED=0`) |
| `gfri-shadow-collector.service` | Bounded systemd `oneshot` service (`MemoryMax=128M`, `CPUQuota=25%`, `ProtectSystem=strict`, `NoNewPrivileges=true`) |
| `gfri-shadow-collector.timer` | 10-minute cadence (`OnUnitInactiveSec=600`, `Persistent=false`) — **not installed until external approval** |
| `preflight_check.py` | Verifies env safety locks (`SHADOW_ONLY`, `live_veto=0`, `player_integration=0`, and `--require-disabled` check) |
| `enable_collector.sh` / `disable_collector.sh` / `rollback_gfri_v11.sh` | Idempotent lifecycle scripts |

## 2. How to Enable (Only After External Acceptance Sign-Off)

```bash
# 1. Stage env file outside repo tree (owned by service user bitnami)
sudo install -d -o bitnami -g bitnami -m 0755 /opt/gfri-shadow /opt/gfri-shadow/state /opt/gfri-shadow/raw_captures
sudo install -o bitnami -g bitnami -m 0644 deploy/gfri_v11/gfri_v11.env.example /opt/gfri-shadow/gfri_v11.env

# 2. Verify safety locks (must exit 0)
python3 deploy/gfri_v11/preflight_check.py --env-file /opt/gfri-shadow/gfri_v11.env --require-disabled

# 3. Install & start 10-minute shadow timer (writes only to /opt/gfri-shadow/state/gfri_shadow_ledger.jsonl)
sudo bash deploy/gfri_v11/enable_collector.sh
```

## 3. Monitoring & Health Verification

- Service logs: `journalctl -u gfri-shadow-collector.service -n 50 --no-pager`
- Timer status: `systemctl list-timers gfri-shadow-collector.timer`
- Latest console state & ledger integrity:
  - Inspect last line of `/opt/gfri-shadow/state/gfri_shadow_ledger.jsonl` (`data_quality`, `confirmation_status`, `shadow_decision`, `decision_authority == "SHADOW_ONLY"`).
  - Run one-shot dry check safely anytime without ledger mutation:
    `python3 -m research.gfri_v11.collector --once --interval-sec 5`

## 4. Incident Handling, Pause & Rollback

| Scenario | Immediate Action |
| :--- | :--- |
| Upstream API outage / repeated `UNKNOWN` | Collector stays non-blocking (`exit 0` with `UNKNOWN` record). Inspect `failure_reasons` in ledger; AI player / dossier pipelines remain untouched. |
| Suspected host load / disk / rate limit | Run `sudo bash deploy/gfri_v11/disable_collector.sh` — stops timer and sets `GFRI_COLLECTOR_ENABLED=0` while preserving collected ledger data. |
| Corrupted trailing JSONL line | `ShadowLedger.load_records()` skips malformed lines cleanly without altering prior records; never rewrite historical lines. |
| Complete unit removal | Run `sudo bash deploy/gfri_v11/rollback_gfri_v11.sh` — stops & deletes systemd units and reverts env flag cleanly. |
