# Unified xxproject Server Release Package (`deploy/xxproject_server`)

Single-commit authoritative release builder, verifier, atomic installer (21 unified `systemd` units), and non-FMZ rollback manager for the server-native xxproject production stack. Ad-hoc file hot-patches directly under `/opt/*` on production hosts are prohibited; every production update must ship as a verified `/opt/xxproject/releases/<release_id>` switch.

## Commands

Build deterministic release tarball from a clean Git commit:
```bash
python3 deploy/xxproject_server/build_release.py \
  --output /tmp/xxproject-server.tar.gz \
  --source-commit "$(git rev-parse HEAD)"
```

Preflight, install, verify, and roll back (add `--root-prefix /tmp/rehearsal --no-systemd` for dry-run isolation):
```bash
python3 deploy/xxproject_server/deploy_release.py preflight --package /tmp/xxproject-server.tar.gz
python3 deploy/xxproject_server/deploy_release.py install --package /tmp/xxproject-server.tar.gz --apply-units
python3 deploy/xxproject_server/deploy_release.py verify --expected-core-version 2.0.6
python3 deploy/xxproject_server/deploy_release.py rollback --apply-units
```

## Layout & Safety Boundaries
- Code releases live in `/opt/xxproject/releases/<release_id>/`, selected atomically via `/opt/xxproject/current` and `/opt/xxproject/previous` (with legacy `.service.d/*.conf` drop-ins backed up under `/opt/xxproject/unit_backups/`).
- Live root switches refuse edits inside `07:50–08:35 UTC` (settlement) and `15:50–16:35 UTC` (auto-deal) unless explicitly overridden with `--allow-window-override`.
- Runtime state (`/var/lib/xxproject/`, `/var/lib/astra-kpf-light/`, `/opt/astra-map-data/state/`) and host configs/secrets (`/etc/signal-audit/`, `/etc/gexmonitorapi.env`, `/etc/astra-kpf-light.env`) are NEVER bundled and NEVER overwritten by install or rollback.
- `SHADOW_ONLY`, `execution_allowed=false`, and `read_only_demo=true` are enforced at both build and verify gates.
