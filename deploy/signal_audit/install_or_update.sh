#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FRONTEND_SRC="$REPO_ROOT/deploy/signal_audit/frontend"
TOOL_SRC="$REPO_ROOT/tools/materialize_signal_cards.py"
GEMINI_TOOL_SRC="$REPO_ROOT/tools/gemini_signal_llm_review.py"
STATIC_ROOT="${STATIC_ROOT:-/opt/signal-audit}"
TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
JSONL_SOURCE="${JSONL_SOURCE:-/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl}"
LLM_REVIEWS_SOURCE="${LLM_REVIEWS_SOURCE:-}"
MAX_CARDS="${MAX_CARDS:-200}"

if [[ ! -f "$FRONTEND_SRC/index.html" || ! -f "$FRONTEND_SRC/app.js" ]]; then
  echo "missing frontend assets under $FRONTEND_SRC" >&2
  exit 2
fi

if [[ ! -f "$TOOL_SRC" ]]; then
  echo "missing materializer: $TOOL_SRC" >&2
  exit 2
fi

if [[ ! -f "$GEMINI_TOOL_SRC" ]]; then
  echo "missing Gemini review tool: $GEMINI_TOOL_SRC" >&2
  exit 2
fi

install -d "$STATIC_ROOT" "$TOOLS_ROOT"
rsync -a --delete "$FRONTEND_SRC"/ "$STATIC_ROOT"/
install -m 0755 "$TOOL_SRC" "$TOOLS_ROOT/materialize_signal_cards.py"
install -m 0755 "$GEMINI_TOOL_SRC" "$TOOLS_ROOT/gemini_signal_llm_review.py"

if [[ -f "$JSONL_SOURCE" ]]; then
  materialize_args=(
    --source "$JSONL_SOURCE" \
    --output "$STATIC_ROOT" \
    --max-cards "$MAX_CARDS"
  )
  if [[ -n "$LLM_REVIEWS_SOURCE" && -f "$LLM_REVIEWS_SOURCE" ]]; then
    materialize_args+=(--llm-reviews "$LLM_REVIEWS_SOURCE")
  fi
  /usr/bin/python3 "$TOOLS_ROOT/materialize_signal_cards.py" "${materialize_args[@]}"
else
  echo "warning: JSONL source not found yet: $JSONL_SOURCE" >&2
fi

echo "installed signal audit frontend to $STATIC_ROOT"
echo "materializer installed to $TOOLS_ROOT/materialize_signal_cards.py"
echo "Gemini review tool installed to $TOOLS_ROOT/gemini_signal_llm_review.py"
