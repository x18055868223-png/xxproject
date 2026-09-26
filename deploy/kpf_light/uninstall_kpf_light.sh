#!/usr/bin/env bash
set -euo pipefail

systemctl disable --now astra-kpf-light.timer 2>/dev/null || true
systemctl stop astra-kpf-light.service 2>/dev/null || true
rm -f /etc/systemd/system/astra-kpf-light.service /etc/systemd/system/astra-kpf-light.timer
systemctl daemon-reload

echo "Removed Astra KPF light systemd units. Data under /var/lib/astra-kpf-light was not deleted."
