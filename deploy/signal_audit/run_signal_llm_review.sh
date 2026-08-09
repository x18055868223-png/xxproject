#!/usr/bin/env bash
set -euo pipefail

TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
JSONL_SOURCE="${JSONL_SOURCE:-/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl}"
LLM_REVIEWS_SOURCE="${LLM_REVIEWS_SOURCE:-$TOOLS_ROOT/signal_llm_reviews.jsonl}"
TRANSITION_LEDGER_SOURCE="${TRANSITION_LEDGER_SOURCE:-$TOOLS_ROOT/signal_transition_ledger.jsonl}"
TRANSITION_LLM_REVIEWS_SOURCE="${TRANSITION_LLM_REVIEWS_SOURCE:-$TOOLS_ROOT/signal_transition_llm_reviews.jsonl}"
LLM_PROVIDER="${LLM_PROVIDER:-deepseek}"
LLM_BASE_URL="${LLM_BASE_URL:-https://api.deepseek.com}"
LLM_MODEL="${LLM_MODEL:-deepseek-v4-flash}"
LLM_REVIEW_LIMIT="${LLM_REVIEW_LIMIT:-4}"
TRANSITION_REVIEW_LIMIT="${TRANSITION_REVIEW_LIMIT:-4}"
LLM_MAX_CONCURRENCY="${LLM_MAX_CONCURRENCY:-4}"
LLM_DAILY_HTTP_CAP="${LLM_DAILY_HTTP_CAP:-60}"
TRANSITION_BLIND_MODE="${TRANSITION_BLIND_MODE:-single_call_evidence_first}"
LLM_BLIND_EFFORT="${LLM_BLIND_EFFORT:-low}"
LLM_RECON_EFFORT="${LLM_RECON_EFFORT:-high}"
LLM_TRANSITION_EFFORT="${LLM_TRANSITION_EFFORT:-low}"
LLM_BLIND_TIMEOUT="${LLM_BLIND_TIMEOUT:-60}"
LLM_RECON_TIMEOUT="${LLM_RECON_TIMEOUT:-240}"
LLM_TRANSITION_TIMEOUT="${LLM_TRANSITION_TIMEOUT:-120}"
LLM_LOCK_FILE="${LLM_LOCK_FILE:-$TOOLS_ROOT/run_signal_llm_review.lock}"

exec 9>"$LLM_LOCK_FILE"
if ! flock -n 9; then
  echo "signal LLM review is already running; skip this timer tick"
  exit 0
fi

if [[ -z "${LLM_API_KEY:-}" ]]; then
  echo "LLM_API_KEY is not configured; edit /etc/signal-audit/llm.env"
  exit 0
fi

if [[ ! -f "$JSONL_SOURCE" ]]; then
  echo "warning: signal review JSONL source not found yet: $JSONL_SOURCE" >&2
  exit 0
fi

exec /usr/bin/python3 "$TOOLS_ROOT/signal_llm_review_entry.py" \
  --mode both \
  --provider "$LLM_PROVIDER" \
  --base-url "$LLM_BASE_URL" \
  --source "$JSONL_SOURCE" \
  --reviews-output "$LLM_REVIEWS_SOURCE" \
  --transition-ledger "$TRANSITION_LEDGER_SOURCE" \
  --transition-reviews-output "$TRANSITION_LLM_REVIEWS_SOURCE" \
  --model "$LLM_MODEL" \
  --limit "$LLM_REVIEW_LIMIT" \
  --transition-limit "$TRANSITION_REVIEW_LIMIT" \
  --concurrency "$LLM_MAX_CONCURRENCY" \
  --daily-cap "$LLM_DAILY_HTTP_CAP" \
  --transition-blind-mode "$TRANSITION_BLIND_MODE" \
  --blind-effort "$LLM_BLIND_EFFORT" \
  --recon-effort "$LLM_RECON_EFFORT" \
  --transition-effort "$LLM_TRANSITION_EFFORT" \
  --blind-timeout "$LLM_BLIND_TIMEOUT" \
  --recon-timeout "$LLM_RECON_TIMEOUT" \
  --transition-timeout "$LLM_TRANSITION_TIMEOUT"
