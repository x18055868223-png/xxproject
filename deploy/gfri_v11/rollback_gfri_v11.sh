#!/usr/bin/env bash
set -euo pipefail

# Full rollback of GFRI v1.1 systemd units (does not touch AI player, dossier, or Prompt A).
ENV_FILE="${GFRI_ENV_FILE:-/opt/gfri-shadow/gfri_v11.env}"

if [ "$(id -u)" != 0 ]; then
  echo "Run rollback_gfri_v11.sh with sudo." >&2
  exit 1
fi

systemctl disable --now gfri-shadow-collector.timer || true
systemctl stop gfri-shadow-collector.service || true
rm -f /etc/systemd/system/gfri-shadow-collector.timer /etc/systemd/system/gfri-shadow-collector.service
if [ -f "$ENV_FILE" ]; then
  sed -i 's/^GFRI_COLLECTOR_ENABLED=.*/GFRI_COLLECTOR_ENABLED=0/' "$ENV_FILE"
fi
systemctl daemon-reload
echo "Rolled back GFRI v1.1 systemd units. Production player/dossier pipelines unaffected."
