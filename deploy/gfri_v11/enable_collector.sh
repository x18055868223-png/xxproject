#!/usr/bin/env bash
set -euo pipefail

# Enable GFRI v1.1 shadow collector ONLY after external acceptance sign-off.
ENV_FILE="${GFRI_ENV_FILE:-/opt/gfri-shadow/gfri_v11.env}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ "$(id -u)" != 0 ]; then
  echo "Run enable_collector.sh with sudo after external review approval." >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing env file: $ENV_FILE (copy gfri_v11.env.example first)" >&2
  exit 1
fi

python3 "$SCRIPT_DIR/preflight_check.py" --env-file "$ENV_FILE" --require-shadow-only

mkdir -p /opt/gfri-shadow/state /opt/gfri-shadow/raw_captures
chown -R bitnami:bitnami /opt/gfri-shadow/state /opt/gfri-shadow/raw_captures
ln -sfn "$REPO_ROOT" /opt/gfri-shadow/current
cp "$SCRIPT_DIR/gfri-shadow-collector.service" /etc/systemd/system/gfri-shadow-collector.service
cp "$SCRIPT_DIR/gfri-shadow-collector.timer" /etc/systemd/system/gfri-shadow-collector.timer

sed -i 's/^GFRI_COLLECTOR_ENABLED=.*/GFRI_COLLECTOR_ENABLED=1/' "$ENV_FILE"
systemctl daemon-reload
systemctl enable --now gfri-shadow-collector.timer
systemctl status gfri-shadow-collector.timer --no-pager
