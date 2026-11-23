#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_PY="$SCRIPT_DIR/deploy_release.py"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 {preflight|install|verify|rollback|status} [args...]" >&2
  exit 2
fi

CMD="$1"
shift

case "$CMD" in
  preflight|install|verify|rollback|status)
    exec /usr/bin/python3 "$DEPLOY_PY" "$CMD" "$@"
    ;;
  *)
    echo "Unknown subcommand: $CMD" >&2
    exit 2
    ;;
esac
