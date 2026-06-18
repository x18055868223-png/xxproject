#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FRONTEND_SRC="$REPO_ROOT/deploy/signal_audit/frontend"
TOOL_SRC="$REPO_ROOT/tools/materialize_signal_cards.py"
STATIC_ROOT="${STATIC_ROOT:-/opt/signal-audit}"
TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
JSONL_SOURCE="${JSONL_SOURCE:-/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl}"
MAX_CARDS="${MAX_CARDS:-200}"

if [[ ! -f "$FRONTEND_SRC/index.html" || ! -f "$FRONTEND_SRC/app.js" ]]; then
  echo "missing frontend assets under $FRONTEND_SRC" >&2
  exit 2
fi

if [[ ! -f "$TOOL_SRC" ]]; then
  echo "missing materializer: $TOOL_SRC" >&2
  exit 2
fi

install -d "$STATIC_ROOT" "$TOOLS_ROOT"
rsync -a --delete "$FRONTEND_SRC"/ "$STATIC_ROOT"/
install -m 0755 "$TOOL_SRC" "$TOOLS_ROOT/materialize_signal_cards.py"

if [[ -f "$JSONL_SOURCE" ]]; then
  /usr/bin/python3 "$TOOLS_ROOT/materialize_signal_cards.py" \
    --source "$JSONL_SOURCE" \
    --output "$STATIC_ROOT" \
    --max-cards "$MAX_CARDS"
else
  echo "warning: JSONL source not found yet: $JSONL_SOURCE" >&2
fi

echo "installed signal audit frontend to $STATIC_ROOT"
echo "materializer installed to $TOOLS_ROOT/materialize_signal_cards.py"
