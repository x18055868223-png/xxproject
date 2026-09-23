#!/usr/bin/env bash
set -euo pipefail
: "${ASTRA_JOINT_ROOT:?dedicated research directory required}"
: "${ASTRA_JOINT_MODEL:?frozen portable model required}"
TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
exec 9>"$ASTRA_JOINT_ROOT/process.lock"
flock -n 9 || exit 0
/usr/bin/python3 - "$TOOLS_ROOT" "$ASTRA_JOINT_ROOT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from astra_joint_shadow import Ledger
if Ledger(sys.argv[2]).window() is None:
    raise SystemExit('Forward window has not been explicitly started; collection remains disabled.')
PY
/usr/bin/python3 "$TOOLS_ROOT/astra_joint_shadow.py" once --folder "$ASTRA_JOINT_ROOT" --artifact "$ASTRA_JOINT_MODEL"
if [[ -f "${JSONL_SOURCE:-}" && -n "${ASTRA_JOINT_ASSESSMENTS:-}" ]]; then
  if ! timeout 5s flock -n "$ASTRA_JOINT_ROOT/registry.lock" /usr/bin/python3 "$TOOLS_ROOT/astra_joint_card_statistics.py" \
    --ledger-folder "$ASTRA_JOINT_ROOT" --source "$JSONL_SOURCE" --artifact "$ASTRA_JOINT_MODEL" \
    --contracts "$ASTRA_JOINT_ROOT" --registry "$ASTRA_JOINT_ASSESSMENTS"; then
    echo "Natural-card statistics pending or failed; original review remains available" >&2
  fi
fi
/usr/bin/python3 "$TOOLS_ROOT/astra_joint_operations.py" --folder "$ASTRA_JOINT_ROOT"
if [[ -n "${ASTRA_JOINT_OUTPUT:-}" ]]; then
  /usr/bin/python3 "$TOOLS_ROOT/astra_joint_shadow.py" publish --folder "$ASTRA_JOINT_ROOT" --output "$ASTRA_JOINT_OUTPUT"
  /usr/bin/python3 "$TOOLS_ROOT/astra_joint_v11_forward.py" --folder "$ASTRA_JOINT_ROOT" --output "$ASTRA_JOINT_OUTPUT/astra_nr_forward_report.json"
fi
