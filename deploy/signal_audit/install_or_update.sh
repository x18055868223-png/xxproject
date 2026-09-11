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
LLM_TOOL_SRC="$REPO_ROOT/tools/signal_llm_review.py"
LLM_ENTRY_SRC="$REPO_ROOT/tools/signal_llm_review_entry.py"
LLM_EVIDENCE_V2_SRC="$REPO_ROOT/tools/signal_evidence_v2.py"
CHANGE_DISPLAY_SRC="$REPO_ROOT/tools/signal_change_display.py"
LLM_REVIEW_V2_SRC="$REPO_ROOT/tools/signal_review_v2.py"
LLM_REVIEW_V2_RUNTIME_SRC="$REPO_ROOT/tools/signal_review_v2_runtime.py"
FACT_SEMANTICS_SRC="$REPO_ROOT/tools/signal_fact_semantics.py"
CANARY_RELEASE_SRC="$REPO_ROOT/tools/signal_llm_review_canary_release.sh"
SERVER_SELF_CHECK_SRC="$REPO_ROOT/tools/server_self_check_signal_stack.sh"
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
TRANSITION_LEDGER_SOURCE="${TRANSITION_LEDGER_SOURCE:-$TOOLS_ROOT/signal_transition_ledger.jsonl}"
TRANSITION_STATE_SOURCE="${TRANSITION_STATE_SOURCE:-$TOOLS_ROOT/signal_transition_state.json}"
TRANSITION_LLM_REVIEWS_SOURCE="${TRANSITION_LLM_REVIEWS_SOURCE:-$TOOLS_ROOT/signal_transition_llm_reviews.jsonl}"
MAX_CARDS="${MAX_CARDS:-15}"
ENABLE_SIGNAL_AUDIT_TIMERS="${ENABLE_SIGNAL_AUDIT_TIMERS:-${ENABLE_TIMERS:-0}}"
START_SIGNAL_AUDIT_TIMERS="${START_SIGNAL_AUDIT_TIMERS:-${START_TIMERS:-0}}"
RUN_INITIAL_MATERIALIZE="${RUN_INITIAL_MATERIALIZE:-0}"
RUN_INITIAL_LLM_REVIEW="${RUN_INITIAL_LLM_REVIEW:-0}"

for required in "$FRONTEND_SRC/index.html" "$FRONTEND_SRC/app.js" "$FRONTEND_SRC/VERSION.json"; do
  if [[ ! -f "$required" ]]; then
    echo "missing frontend asset: $required" >&2
    exit 2
  fi
done

if [[ ! -f "$TOOL_SRC" ]]; then
  echo "missing materializer: $TOOL_SRC" >&2
  exit 2
fi

if [[ ! -f "$LLM_TOOL_SRC" ]]; then
  echo "missing provider-neutral LLM review tool: $LLM_TOOL_SRC" >&2
  exit 2
fi

if [[ ! -f "$LLM_ENTRY_SRC" ]]; then
  echo "missing provider-neutral LLM review entrypoint: $LLM_ENTRY_SRC" >&2
  exit 2
fi

for required in "$LLM_EVIDENCE_V2_SRC" "$LLM_REVIEW_V2_SRC" "$LLM_REVIEW_V2_RUNTIME_SRC" "$CHANGE_DISPLAY_SRC"; do
  if [[ ! -f "$required" ]]; then
    echo "missing v2 evidence review tool: $required" >&2
    exit 2
  fi
done

if [[ ! -f "$FACT_SEMANTICS_SRC" ]]; then
  echo "missing deterministic fact semantics: $FACT_SEMANTICS_SRC" >&2
  exit 2
fi

if [[ ! -f "$CANARY_RELEASE_SRC" ]]; then
  echo "missing canary release helper: $CANARY_RELEASE_SRC" >&2
  exit 2
fi

if [[ ! -f "$SERVER_SELF_CHECK_SRC" ]]; then
  echo "missing server self-check helper: $SERVER_SELF_CHECK_SRC" >&2
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
frontend_rsync_args=(-a --delete)
if [[ -d "$STATIC_ROOT/signal_cards" ]]; then
  # signal_cards is runtime state produced by the materializer/canary release.
  # A code-only update must not replace it with the packaged fallback fixture,
  # even when index.json is temporarily missing or damaged.
  frontend_rsync_args+=(--exclude=/signal_cards/)
  echo "preserving existing materialized signal_cards directory during frontend update"
fi
rsync "${frontend_rsync_args[@]}" "$FRONTEND_SRC"/ "$STATIC_ROOT"/
install -m 0755 "$TOOL_SRC" "$TOOLS_ROOT/materialize_signal_cards.py"
install -m 0755 "$LLM_TOOL_SRC" "$TOOLS_ROOT/signal_llm_review.py"
install -m 0755 "$LLM_ENTRY_SRC" "$TOOLS_ROOT/signal_llm_review_entry.py"
install -m 0755 "$LLM_EVIDENCE_V2_SRC" "$TOOLS_ROOT/signal_evidence_v2.py"
install -m 0755 "$CHANGE_DISPLAY_SRC" "$TOOLS_ROOT/signal_change_display.py"
install -m 0755 "$LLM_REVIEW_V2_SRC" "$TOOLS_ROOT/signal_review_v2.py"
install -m 0755 "$LLM_REVIEW_V2_RUNTIME_SRC" "$TOOLS_ROOT/signal_review_v2_runtime.py"
install -m 0644 "$FACT_SEMANTICS_SRC" "$TOOLS_ROOT/signal_fact_semantics.py"
install -m 0755 "$LLM_RUNNER_SRC" "$TOOLS_ROOT/run_signal_llm_review.sh"
install -m 0755 "$CANARY_RELEASE_SRC" "$TOOLS_ROOT/signal_llm_review_canary_release.sh"
install -m 0755 "$SERVER_SELF_CHECK_SRC" "$TOOLS_ROOT/server_self_check_signal_stack.sh"
install -m 0644 "$LLM_ENV_EXAMPLE_SRC" "$CONFIG_ROOT/llm.env.example"
if [[ ! -f "$LLM_ENV_FILE" ]]; then
  install -m 0600 "$LLM_ENV_EXAMPLE_SRC" "$LLM_ENV_FILE"
  echo "created LLM API key template at $LLM_ENV_FILE; edit LLM_API_KEY before expecting reviews"
else
  chmod 0600 "$LLM_ENV_FILE"
fi
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  chown root:root "$LLM_ENV_FILE"
fi

install -m 0644 "$MATERIALIZE_SERVICE_SRC" /etc/systemd/system/signal-audit-materialize.service
install -m 0644 "$MATERIALIZE_TIMER_SRC" /etc/systemd/system/signal-audit-materialize.timer
install -m 0644 "$LLM_SERVICE_SRC" /etc/systemd/system/signal-audit-llm-review.service
install -m 0644 "$LLM_TIMER_SRC" /etc/systemd/system/signal-audit-llm-review.timer
systemctl daemon-reload

if [[ "$ENABLE_SIGNAL_AUDIT_TIMERS" == "1" ]]; then
  systemctl enable signal-audit-materialize.timer
  systemctl enable signal-audit-llm-review.timer
else
  echo "safe default: timers installed but not enabled; set ENABLE_SIGNAL_AUDIT_TIMERS=1 after canary validation"
fi

if [[ "$START_SIGNAL_AUDIT_TIMERS" == "1" ]]; then
  systemctl start signal-audit-materialize.timer
  systemctl start signal-audit-llm-review.timer
else
  echo "safe default: timers not started; set START_SIGNAL_AUDIT_TIMERS=1 after canary validation"
fi

if [[ "$RUN_INITIAL_MATERIALIZE" == "1" && -f "$JSONL_SOURCE" ]]; then
  materialize_args=(
    --source "$JSONL_SOURCE" \
    --require-valid-source-tail \
    --output "$STATIC_ROOT" \
    --max-cards "$MAX_CARDS" \
    --transition-ledger "$TRANSITION_LEDGER_SOURCE" \
    --transition-state "$TRANSITION_STATE_SOURCE" \
    --transition-reviews "$TRANSITION_LLM_REVIEWS_SOURCE"
  )
  if [[ -n "$LLM_REVIEWS_SOURCE" && -f "$LLM_REVIEWS_SOURCE" ]]; then
    materialize_args+=(--llm-reviews "$LLM_REVIEWS_SOURCE")
  fi
  /usr/bin/python3 "$TOOLS_ROOT/materialize_signal_cards.py" "${materialize_args[@]}"
elif [[ "$RUN_INITIAL_MATERIALIZE" == "1" ]]; then
  echo "warning: JSONL source not found yet: $JSONL_SOURCE" >&2
else
  echo "safe default: skipped initial materialization; set RUN_INITIAL_MATERIALIZE=1 to run it"
fi

if [[ "$RUN_INITIAL_LLM_REVIEW" == "1" ]]; then
  systemctl start signal-audit-llm-review.service || true
else
  echo "safe default: skipped initial LLM review; use canary release first or set RUN_INITIAL_LLM_REVIEW=1"
fi

echo "installed signal audit frontend to $STATIC_ROOT"
echo "materializer installed to $TOOLS_ROOT/materialize_signal_cards.py"
echo "provider-neutral LLM review tool installed to $TOOLS_ROOT/signal_llm_review.py"
echo "provider-neutral LLM review entrypoint installed to $TOOLS_ROOT/signal_llm_review_entry.py"
echo "v2 evidence packet tool installed to $TOOLS_ROOT/signal_evidence_v2.py"
echo "v2 evidence review tool installed to $TOOLS_ROOT/signal_review_v2.py"
echo "v2 evidence review runtime installed to $TOOLS_ROOT/signal_review_v2_runtime.py"
echo "deterministic fact semantics installed to $TOOLS_ROOT/signal_fact_semantics.py"
echo "canary release helper installed to $TOOLS_ROOT/signal_llm_review_canary_release.sh"
echo "server self-check helper installed to $TOOLS_ROOT/server_self_check_signal_stack.sh"
echo "LLM API key config lives at $LLM_ENV_FILE"
echo "LLM review sidecar lives at $LLM_REVIEWS_SOURCE"
echo "transition ledger lives at $TRANSITION_LEDGER_SOURCE"
echo "transition state lives at $TRANSITION_STATE_SOURCE"
echo "transition LLM review sidecar lives at $TRANSITION_LLM_REVIEWS_SOURCE"
