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

RUN_JSONL_SOURCE="$JSONL_SOURCE"
RUN_TRANSITION_LEDGER_SOURCE="$TRANSITION_LEDGER_SOURCE"
RUN_LLM_REVIEW_LIMIT="$LLM_REVIEW_LIMIT"
RUN_TRANSITION_REVIEW_LIMIT="$TRANSITION_REVIEW_LIMIT"
TMP_FILES=()

cleanup_tmp_files() {
  if [[ "${#TMP_FILES[@]}" -gt 0 ]]; then
    rm -f "${TMP_FILES[@]}"
  fi
}
trap cleanup_tmp_files EXIT

if [[ -n "${ONLY_CARD_ID:-}" ]]; then
  RUN_JSONL_SOURCE="$(mktemp "${TMPDIR:-/tmp}/signal-llm-card.XXXXXX.jsonl")"
  TMP_FILES+=("$RUN_JSONL_SOURCE")
  /usr/bin/python3 - "$JSONL_SOURCE" "$ONLY_CARD_ID" "$RUN_JSONL_SOURCE" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = sys.argv[2]
output = pathlib.Path(sys.argv[3])
lines = [line for line in source.read_text(encoding="utf-8", errors="replace").splitlines()
         if line.strip()]
matches = []
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
        matches.append(item)
if not matches:
    raise SystemExit("ONLY_CARD_ID not found in source JSONL: " + target)
output.write_text(json.dumps(matches[-1], ensure_ascii=False, sort_keys=True) + "\n",
                  encoding="utf-8")
print("ONLY_CARD_ID target extracted: " + target, file=sys.stderr)
PY
  RUN_LLM_REVIEW_LIMIT=1

  if [[ -f "$TRANSITION_LEDGER_SOURCE" ]]; then
    RUN_TRANSITION_LEDGER_SOURCE="$(mktemp "${TMPDIR:-/tmp}/signal-transition-ledger.XXXXXX.jsonl")"
    TMP_FILES+=("$RUN_TRANSITION_LEDGER_SOURCE")
    /usr/bin/python3 - "$TRANSITION_LEDGER_SOURCE" "$ONLY_CARD_ID" "$RUN_TRANSITION_LEDGER_SOURCE" <<'PY'
import json
import pathlib
import sys

ledger = pathlib.Path(sys.argv[1])
target = sys.argv[2]
output = pathlib.Path(sys.argv[3])
rows = []
for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
    if not line.strip():
        continue
    try:
        item = json.loads(line)
    except Exception:
        continue
    if isinstance(item, dict) and str(item.get("current_card_id")) == target:
        rows.append(item)
output.write_text(
    "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
            for item in rows),
    encoding="utf-8")
print("ONLY_CARD_ID transition rows extracted: " + str(len(rows)), file=sys.stderr)
PY
    RUN_TRANSITION_REVIEW_LIMIT=1
  fi
fi

entry_args=(
  --mode both
  --provider "$LLM_PROVIDER"
  --base-url "$LLM_BASE_URL"
  --source "$RUN_JSONL_SOURCE"
  --reviews-output "$LLM_REVIEWS_SOURCE"
  --transition-ledger "$RUN_TRANSITION_LEDGER_SOURCE"
  --transition-reviews-output "$TRANSITION_LLM_REVIEWS_SOURCE"
  --model "$LLM_MODEL"
  --limit "$RUN_LLM_REVIEW_LIMIT"
  --transition-limit "$RUN_TRANSITION_REVIEW_LIMIT"
  --concurrency "$LLM_MAX_CONCURRENCY"
  --daily-cap "$LLM_DAILY_HTTP_CAP"
  --usage-ledger "$LLM_USAGE_LEDGER"
  --transition-blind-mode "$TRANSITION_BLIND_MODE"
  --blind-effort "$LLM_BLIND_EFFORT"
  --recon-effort "$LLM_RECON_EFFORT"
  --transition-effort "$LLM_TRANSITION_EFFORT"
  --blind-timeout "$LLM_BLIND_TIMEOUT"
  --recon-timeout "$LLM_RECON_TIMEOUT"
  --transition-timeout "$LLM_TRANSITION_TIMEOUT"
)

if [[ "${#TMP_FILES[@]}" -eq 0 ]]; then
  exec /usr/bin/python3 "$TOOLS_ROOT/signal_llm_review_entry.py" "${entry_args[@]}"
fi

entry_args+=(--only-card-id "$ONLY_CARD_ID")
/usr/bin/python3 "$TOOLS_ROOT/signal_llm_review_entry.py" "${entry_args[@]}"
