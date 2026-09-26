#!/usr/bin/env bash
set -euo pipefail
SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ "$(id -u)" != 0 ]; then
  echo 'Run this data-only installer with sudo.' >&2
  exit 1
fi
python3 "$SOURCE_ROOT/deploy/astra_map_data/install_map_data.py" "$SOURCE_ROOT"
bash "$SOURCE_ROOT/deploy/kpf_light/install_kpf_light.sh"
systemctl daemon-reload
systemctl enable --now astra-map-data.timer astra-kpf-light.timer
echo 'Installed same-server data services. Inspect current_map.json and KPF status; installation does not establish completeness.'
