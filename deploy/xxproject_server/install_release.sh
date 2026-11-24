#!/usr/bin/env bash
set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_PY="$SCRIPT_DIR/deploy_release.py"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 {preflight|install|verify|rollback|status} [args...]" >&2
  exit 2
fi

CMD="$1"
shift

case "$CMD" in
  preflight|verify|status)
    exec /usr/bin/python3 "$DEPLOY_PY" "$CMD" "$@"
    ;;
  install|rollback)
    has_apply=0
    has_no_systemd=0
    filtered=()
    for arg in "$@"; do
      case "$arg" in
        --apply-units)
          has_apply=1
          filtered+=("$arg")
          ;;
        --no-apply-units)
          has_apply=2
          ;;
        --no-systemd)
          has_no_systemd=1
          filtered+=("$arg")
          ;;
        *)
          filtered+=("$arg")
          ;;
      esac
    done
    if [[ $has_apply -eq 0 && $has_no_systemd -eq 0 ]]; then
      filtered+=(--apply-units)
    fi
    exec /usr/bin/python3 "$DEPLOY_PY" "$CMD" "${filtered[@]}"
    ;;
  *)
    echo "Unknown subcommand: $CMD" >&2
    exit 2
    ;;
esac
