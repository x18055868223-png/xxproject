#!/usr/bin/env bash
set -euo pipefail

TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
JSONL_SOURCE="${JSONL_SOURCE:-/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl}"
LLM_REVIEWS_SOURCE="${LLM_REVIEWS_SOURCE:-$TOOLS_ROOT/signal_llm_reviews.jsonl}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"
LLM_REVIEW_LIMIT="${LLM_REVIEW_LIMIT:-2}"
LLM_REVIEW_TIMEOUT="${LLM_REVIEW_TIMEOUT:-60}"

if [[ -z "${GEMINI_3_5_FLASH_API_KEY:-}${GEMINI_FLASH_API_KEY:-}${GEMINI_LOW_COST_API_KEY:-}${GEMINI_API_KEY:-}${GEMINI_PAID_API_KEY:-}${GEMINI_FALLBACK_API_KEY:-}${GEMINI_PRO_API_KEY:-}" ]]; then
  echo "Gemini API key is not configured; edit /etc/signal-audit/llm.env"
  echo "preferred: GEMINI_3_5_FLASH_API_KEY for low-cost calls, GEMINI_PAID_API_KEY for fallback"
  exit 0
fi

if [[ ! -f "$JSONL_SOURCE" ]]; then
  echo "warning: signal review JSONL source not found yet: $JSONL_SOURCE" >&2
  exit 0
fi

exec /usr/bin/python3 "$TOOLS_ROOT/gemini_signal_llm_review.py" \
  --source "$JSONL_SOURCE" \
  --reviews-output "$LLM_REVIEWS_SOURCE" \
  --model "$GEMINI_MODEL" \
  --limit "$LLM_REVIEW_LIMIT" \
  --timeout "$LLM_REVIEW_TIMEOUT"
