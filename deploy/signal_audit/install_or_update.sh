#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -d "$SCRIPT_DIR/../frontend" && -d "$SCRIPT_DIR/../tools" ]]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  DEPLOY_SRC="$SCRIPT_DIR"
else
  REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
  DEPLOY_SRC="$REPO_ROOT/deploy/signal_audit"
fi
FRONTEND_SRC="$REPO_ROOT/deploy/signal_audit/frontend"
if [[ ! -d "$FRONTEND_SRC" ]]; then
  FRONTEND_SRC="$REPO_ROOT/frontend"
fi
TOOL_SRC="$REPO_ROOT/tools/materialize_signal_cards.py"
GEMINI_TOOL_SRC="$REPO_ROOT/tools/gemini_signal_llm_review.py"
LLM_RUNNER_SRC="$DEPLOY_SRC/run_signal_llm_review.sh"
LLM_ENV_EXAMPLE_SRC="$DEPLOY_SRC/signal-audit-llm.env.example"
MATERIALIZE_SERVICE_SRC="$DEPLOY_SRC/signal-audit-materialize.service"
MATERIALIZE_TIMER_SRC="$DEPLOY_SRC/signal-audit-materialize.timer"
LLM_SERVICE_SRC="$DEPLOY_SRC/signal-audit-llm-review.service"
LLM_TIMER_SRC="$DEPLOY_SRC/signal-audit-llm-review.timer"
STATIC_ROOT="${STATIC_ROOT:-/opt/signal-audit}"
TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
CONFIG_ROOT="${CONFIG_ROOT:-/etc/signal-audit}"
LLM_ENV_FILE="${LLM_ENV_FILE:-$CONFIG_ROOT/llm.env}"
JSONL_SOURCE="${JSONL_SOURCE:-/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl}"
LLM_REVIEWS_SOURCE="${LLM_REVIEWS_SOURCE:-$TOOLS_ROOT/signal_llm_reviews.jsonl}"
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

for required in "$LLM_RUNNER_SRC" "$LLM_ENV_EXAMPLE_SRC" "$MATERIALIZE_SERVICE_SRC" "$MATERIALIZE_TIMER_SRC" "$LLM_SERVICE_SRC" "$LLM_TIMER_SRC"; do
  if [[ ! -f "$required" ]]; then
    echo "missing deployment asset: $required" >&2
    exit 2
  fi
done

install -d "$STATIC_ROOT" "$TOOLS_ROOT" "$CONFIG_ROOT"
chmod 0700 "$CONFIG_ROOT"
rsync -a --delete "$FRONTEND_SRC"/ "$STATIC_ROOT"/
install -m 0755 "$TOOL_SRC" "$TOOLS_ROOT/materialize_signal_cards.py"
install -m 0755 "$GEMINI_TOOL_SRC" "$TOOLS_ROOT/gemini_signal_llm_review.py"
install -m 0755 "$LLM_RUNNER_SRC" "$TOOLS_ROOT/run_signal_llm_review.sh"
install -m 0644 "$LLM_ENV_EXAMPLE_SRC" "$CONFIG_ROOT/llm.env.example"
if [[ ! -f "$LLM_ENV_FILE" ]]; then
  install -m 0600 "$LLM_ENV_EXAMPLE_SRC" "$LLM_ENV_FILE"
  echo "created LLM API key template at $LLM_ENV_FILE; edit GEMINI_CHANNEL1_API_KEY/GEMINI_CHANNEL2_API_KEY before expecting reviews"
else
  chmod 0600 "$LLM_ENV_FILE"
fi

install -m 0644 "$MATERIALIZE_SERVICE_SRC" /etc/systemd/system/signal-audit-materialize.service
install -m 0644 "$MATERIALIZE_TIMER_SRC" /etc/systemd/system/signal-audit-materialize.timer
install -m 0644 "$LLM_SERVICE_SRC" /etc/systemd/system/signal-audit-llm-review.service
install -m 0644 "$LLM_TIMER_SRC" /etc/systemd/system/signal-audit-llm-review.timer
systemctl daemon-reload
systemctl enable --now signal-audit-materialize.timer
systemctl enable --now signal-audit-llm-review.timer

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
  systemctl start signal-audit-llm-review.service || true
else
  echo "warning: JSONL source not found yet: $JSONL_SOURCE" >&2
fi

echo "installed signal audit frontend to $STATIC_ROOT"
echo "materializer installed to $TOOLS_ROOT/materialize_signal_cards.py"
echo "Gemini review tool installed to $TOOLS_ROOT/gemini_signal_llm_review.py"
echo "LLM API key config lives at $LLM_ENV_FILE"
echo "LLM review sidecar lives at $LLM_REVIEWS_SOURCE"
