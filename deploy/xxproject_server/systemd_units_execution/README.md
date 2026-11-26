# Isolated Execution Systemd Units (`SHADOW_ONLY` & Unarmed Default)

- Keep `systemd_units_unified/` untouched at the 21 baseline read-only units until live-execution phase authorization.
- `astra-execution-coordinator.service` runs under dedicated `User=astra-exec` (`SupplementaryGroups=research`, `UMask=0077`, `--mode SHADOW_ONLY` hardcoded in unit; live write enablement requires explicit operator drop-in + P4 authorization). `exec_store.sqlite` stays `0600 astra-exec:astra-exec`; `public_status.json` is `0644` for P5 and read-only Workbench visibility.
- `60-exec-control.conf` installs to `/etc/systemd/system/astra-workbench-v20.service.d/60-exec-control.conf` and runs Workbench as dedicated `User=astra-workbench` (`SupplementaryGroups=bitnami research`) in pure read-only cockpit mode (`ReadOnlyPaths=/var/lib/xxproject/execution`, zero execution control credentials).
- Coordinator `SO_PEERCRED` allows `{0, uid(astra-exec)}` only (`bitnami` and `astra-workbench` excluded); Workbench rejects all `/api/workbench/execution/*` POSTs with `403`.
- Verify host isolation on Linux with `sudo bash /opt/xxproject/current/deploy/xxproject_server/systemd_units_execution/linux_security_verify.sh`.
