#!/usr/bin/env bash
set -euo pipefail

TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
JSONL_SOURCE="${JSONL_SOURCE:-/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl}"
LLM_REVIEWS_SOURCE="${LLM_REVIEWS_SOURCE:-$TOOLS_ROOT/signal_llm_reviews.jsonl}"
TRANSITION_LEDGER_SOURCE="${TRANSITION_LEDGER_SOURCE:-$TOOLS_ROOT/signal_transition_ledger.jsonl}"
LLM_PROVIDER="${LLM_PROVIDER:-deepseek}"
LLM_BASE_URL="${LLM_BASE_URL:-https://api.deepseek.com}"
LLM_MODEL="${LLM_MODEL:-deepseek-v4-flash}"
LLM_REVIEW_LIMIT="${LLM_REVIEW_LIMIT:-4}"
LLM_MAX_CONCURRENCY="${LLM_MAX_CONCURRENCY:-4}"
LLM_DAILY_HTTP_CAP="${LLM_DAILY_HTTP_CAP:-60}"
LLM_RECON_EFFORT="${LLM_RECON_EFFORT:-high}"
LLM_RECON_TIMEOUT="${LLM_RECON_TIMEOUT:-240}"
LLM_USAGE_LEDGER="${LLM_USAGE_LEDGER:-$TOOLS_ROOT/signal_llm_usage_ledger.json}"
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

RUN_LLM_REVIEW_LIMIT="$LLM_REVIEW_LIMIT"

if [[ -n "${ONLY_CARD_ID:-}" ]]; then
  /usr/bin/python3 - "$JSONL_SOURCE" "$ONLY_CARD_ID" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = sys.argv[2]
lines = [line for line in source.read_text(encoding="utf-8", errors="replace").splitlines()
         if line.strip()]
found = False
for index, line in enumerate(lines, 1):
    try:
        item = json.loads(line)
    except Exception as exc:
        if index == len(lines):
            raise SystemExit(
                "latest non-empty source line is invalid while selecting ONLY_CARD_ID="
                + target + ": " + type(exc).__name__)
        continue
    if not isinstance(item, dict):
        continue
    identity = item.get("identity") if isinstance(item.get("identity"), dict) else {}
    card_id = identity.get("card_id") or item.get("card_id")
    if str(card_id) == target:
        found = True
if not found:
    raise SystemExit("ONLY_CARD_ID not found in source JSONL: " + target)
print("ONLY_CARD_ID target verified in full source: " + target, file=sys.stderr)
PY
  RUN_LLM_REVIEW_LIMIT=1
fi

entry_args=(
  --mode card
  --review-mode single_evidence_v2
  --provider "$LLM_PROVIDER"
  --base-url "$LLM_BASE_URL"
  --source "$JSONL_SOURCE"
  --reviews-output "$LLM_REVIEWS_SOURCE"
  --transition-ledger "$TRANSITION_LEDGER_SOURCE"
  --model "$LLM_MODEL"
  --limit "$RUN_LLM_REVIEW_LIMIT"
  --concurrency "$LLM_MAX_CONCURRENCY"
  --daily-cap "$LLM_DAILY_HTTP_CAP"
  --usage-ledger "$LLM_USAGE_LEDGER"
  --recon-effort "$LLM_RECON_EFFORT"
  --recon-timeout "$LLM_RECON_TIMEOUT"
)

if [[ -n "${ONLY_CARD_ID:-}" ]]; then
  entry_args+=(--only-card-id "$ONLY_CARD_ID")
fi

exec /usr/bin/python3 "$TOOLS_ROOT/signal_llm_review_entry.py" "${entry_args[@]}"
