#!/usr/bin/env bash
set -euo pipefail

# Safely quiesce GFRI v1.1 shadow collector without deleting collected state.
ENV_FILE="${GFRI_ENV_FILE:-/opt/gfri-shadow/gfri_v11.env}"

if [ "$(id -u)" != 0 ]; then
  echo "Run disable_collector.sh with sudo." >&2
  exit 1
fi

systemctl disable --now gfri-shadow-collector.timer || true
systemctl stop gfri-shadow-collector.service || true
if [ -f "$ENV_FILE" ]; then
  sed -i 's/^GFRI_COLLECTOR_ENABLED=.*/GFRI_COLLECTOR_ENABLED=0/' "$ENV_FILE"
fi
systemctl daemon-reload
echo "GFRI v1.1 shadow collector disabled; preserved state in /opt/gfri-shadow/state."
