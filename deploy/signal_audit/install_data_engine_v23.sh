#!/usr/bin/env bash
# Independent data-only installation: no frontend, model, LLM or trading service.
set -euo pipefail
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then echo 'run with sudo' >&2; exit 2; fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -d "$SCRIPT_DIR/../tools" ]]; then SOURCE="$(cd "$SCRIPT_DIR/.." && pwd)";
else SOURCE="$(cd "$SCRIPT_DIR/../.." && pwd)"; fi
TARGET=/opt/astra-data-engine-v23
STATE="$TARGET/state"
CONFIG=/etc/signal-audit/data-engine-maintenance.env
FMZ=/home/bitnami/fmz2/logs/storage/668422/demo/logs
for path in "$TARGET" "$TARGET/tools" "$STATE" /etc/signal-audit "$CONFIG"; do
  if [[ -L "$path" ]]; then echo "refusing symlink: $path" >&2; exit 2; fi
done
REGISTRY="$SOURCE/demo/shared/data_product_registry_v23.json"
if [[ ! -f "$REGISTRY" ]]; then REGISTRY="$SOURCE/tools/data_product_registry_v23.json"; fi
modules=(astra_data_server_v23.py astra_data_engine_v23.py astra_data_projection_v23.py astra_data_retention_v23.py astra_underwriting_fact_context_v20.py astra_underwriting_v20.py astra_commercial_math_v17.py)
for module in "${modules[@]}"; do test -f "$SOURCE/tools/$module"; done
test -f "$REGISTRY"
test -d "$FMZ"
install -d -m 0755 "$TARGET" "$TARGET/tools"
install -d -o bitnami -g bitnami -m 0750 "$STATE"
install -d -m 0700 /etc/signal-audit
for module in "${modules[@]}"; do install -m 0644 "$SOURCE/tools/$module" "$TARGET/tools/$module"; done
install -m 0644 "$REGISTRY" "$TARGET/tools/data_product_registry_v23.json"
if [[ ! -f "$CONFIG" ]]; then
  printf 'ASTRA_FMZ_LOG_DIR=%s\nASTRA_DATA_STATE_DIR=%s\n' "$FMZ" "$STATE" > "$CONFIG"
fi
chown root:bitnami "$CONFIG"
chmod 0640 "$CONFIG"
for extension in service timer; do
  install -m 0644 "$SCRIPT_DIR/data-engine-maintenance.$extension" "/etc/systemd/system/astra-data-engine-maintenance.$extension"
done
/usr/bin/python3 -c 'import sys; sys.path.insert(0,"/opt/astra-data-engine-v23/tools"); import astra_data_server_v23; from astra_data_engine_v23 import DataEngine; DataEngine("/opt/astra-data-engine-v23/state/import-check")'
chown -R bitnami:bitnami "$STATE/import-check"
systemctl daemon-reload
systemctl enable --now astra-data-engine-maintenance.timer
systemctl start astra-data-engine-maintenance.service
systemctl --no-pager status astra-data-engine-maintenance.timer
