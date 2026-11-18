#!/usr/bin/env bash
set -euo pipefail

# Enable GFRI v1.1 shadow collector ONLY after external acceptance sign-off.
# Includes atomic ERR trap rollback so partial failures never leave half-enabled state.
ENV_FILE="${GFRI_ENV_FILE:-/opt/gfri-shadow/gfri_v11.env}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SYSTEMD_SERVICE="${GFRI_SYSTEMD_SERVICE:-/etc/systemd/system/gfri-shadow-collector.service}"
SYSTEMD_TIMER="${GFRI_SYSTEMD_TIMER:-/etc/systemd/system/gfri-shadow-collector.timer}"
CURRENT_LINK="${GFRI_CURRENT_LINK:-/opt/gfri-shadow/current}"
STATE_DIR="${GFRI_STATE_DIR:-/opt/gfri-shadow/state}"
RAW_DIR="${GFRI_RAW_CAPTURE_DIR:-/opt/gfri-shadow/raw_captures}"

if [ "$(id -u)" != 0 ]; then
  echo "Run enable_collector.sh with sudo after external review approval." >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing env file: $ENV_FILE (copy gfri_v11.env.example first)" >&2
  exit 1
fi

# Capture pre-change state for clean rollback on error
HAD_SERVICE=0
HAD_TIMER=0
PRIOR_ENABLED="$(grep -E '^GFRI_COLLECTOR_ENABLED=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || echo '0')"
[ -f "$SYSTEMD_SERVICE" ] && HAD_SERVICE=1
[ -f "$SYSTEMD_TIMER" ] && HAD_TIMER=1
ARMED_ROLLBACK=0

cleanup_on_error() {
  local rc=$?
  if [ "$ARMED_ROLLBACK" -eq 1 ]; then
    echo "ERROR: enable_collector.sh failed (rc=$rc); rolling back to clean non-enabled state..." >&2
    systemctl disable --now gfri-shadow-collector.timer 2>/dev/null || true
    systemctl stop gfri-shadow-collector.service 2>/dev/null || true
    sed -i "s/^GFRI_COLLECTOR_ENABLED=.*/GFRI_COLLECTOR_ENABLED=${PRIOR_ENABLED:-0}/" "$ENV_FILE" || true
    if [ "$HAD_TIMER" -eq 0 ]; then
      rm -f "$SYSTEMD_TIMER"
    fi
    if [ "$HAD_SERVICE" -eq 0 ]; then
      rm -f "$SYSTEMD_SERVICE"
    fi
    systemctl daemon-reload 2>/dev/null || true
    echo "ROLLED_BACK_ON_ERROR: collector remains disabled (GFRI_COLLECTOR_ENABLED=${PRIOR_ENABLED:-0})." >&2
  fi
  exit "$rc"
}
trap cleanup_on_error ERR

if [ "$STATE_DIR" = "/opt/gfri-shadow/state" ] && [ "$RAW_DIR" = "/opt/gfri-shadow/raw_captures" ]; then
  mkdir -p /opt/gfri-shadow/state /opt/gfri-shadow/raw_captures
  chown -R bitnami:bitnami /opt/gfri-shadow/state /opt/gfri-shadow/raw_captures
else
  mkdir -p "$STATE_DIR" "$RAW_DIR"
  chown -R bitnami:bitnami "$STATE_DIR" "$RAW_DIR"
fi

/usr/bin/python3 "$SCRIPT_DIR/preflight_check.py" \
  --env-file "$ENV_FILE" \
  --require-shadow-only \
  --state-dir "$STATE_DIR" \
  --raw-dir "$RAW_DIR" \
  --host-check \
  --network-probe

ARMED_ROLLBACK=1
mkdir -p "$(dirname "$CURRENT_LINK")" "$(dirname "$SYSTEMD_SERVICE")"
ln -sfn "$REPO_ROOT" "$CURRENT_LINK"
cp "$SCRIPT_DIR/gfri-shadow-collector.service" "$SYSTEMD_SERVICE"
cp "$SCRIPT_DIR/gfri-shadow-collector.timer" "$SYSTEMD_TIMER"

if command -v systemd-analyze >/dev/null 2>&1; then
  if [ "$SYSTEMD_SERVICE" = "/etc/systemd/system/gfri-shadow-collector.service" ]; then
    systemd-analyze verify "$SYSTEMD_SERVICE" "$SYSTEMD_TIMER"
  fi
fi

systemctl daemon-reload
sed -i 's/^GFRI_COLLECTOR_ENABLED=.*/GFRI_COLLECTOR_ENABLED=1/' "$ENV_FILE"
systemctl enable --now gfri-shadow-collector.timer
ARMED_ROLLBACK=0
trap - ERR

systemctl status gfri-shadow-collector.timer --no-pager
