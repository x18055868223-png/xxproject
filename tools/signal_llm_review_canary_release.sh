#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  signal_llm_review_canary_release.sh --target-card-id CARD_ID [--promote] [--enable-timers-after-pass]
  signal_llm_review_canary_release.sh --target-card-id CARD_ID --resume-canary-root DIR [--promote] [--enable-timers-after-pass]
  signal_llm_review_canary_release.sh --rollback-backup BACKUP_DIR

Defaults are canary-only:
  - source JSONL is read from JSONL_SOURCE or the FMZ default
  - review sidecars, transition ledger/state, and static output are isolated under a temp root
  - real HTTP reservations always update the authoritative usage ledger and are never rolled back
  - no production review or static files are changed unless --promote is passed
  - timers are enabled/started only with --enable-timers-after-pass after validation passes
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$SCRIPT_DIR/materialize_signal_cards.py" ]]; then
  DEFAULT_TOOLS_ROOT="$SCRIPT_DIR"
else
  DEFAULT_TOOLS_ROOT="$REPO_ROOT/tools"
fi

TOOLS_ROOT="${TOOLS_ROOT:-$DEFAULT_TOOLS_ROOT}"
MATERIALIZER="${MATERIALIZER:-$TOOLS_ROOT/materialize_signal_cards.py}"
RUNNER="${RUNNER:-$TOOLS_ROOT/run_signal_llm_review.sh}"
if [[ ! -f "$RUNNER" && -f "$REPO_ROOT/deploy/signal_audit/run_signal_llm_review.sh" ]]; then
  RUNNER="$REPO_ROOT/deploy/signal_audit/run_signal_llm_review.sh"
fi
SELF_CHECK="${SELF_CHECK:-$TOOLS_ROOT/server_self_check_signal_stack.sh}"
if [[ ! -f "$SELF_CHECK" && -f "$REPO_ROOT/tools/server_self_check_signal_stack.sh" ]]; then
  SELF_CHECK="$REPO_ROOT/tools/server_self_check_signal_stack.sh"
fi

JSONL_SOURCE="${JSONL_SOURCE:-/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl}"
STATIC_ROOT="${STATIC_ROOT:-/opt/signal-audit}"
PROD_TOOLS_ROOT="${PROD_TOOLS_ROOT:-/opt/signal-audit-tools}"
LLM_REVIEWS_SOURCE="${LLM_REVIEWS_SOURCE:-$PROD_TOOLS_ROOT/signal_llm_reviews.jsonl}"
TRANSITION_LEDGER_SOURCE="${TRANSITION_LEDGER_SOURCE:-$PROD_TOOLS_ROOT/signal_transition_ledger.jsonl}"
TRANSITION_STATE_SOURCE="${TRANSITION_STATE_SOURCE:-$PROD_TOOLS_ROOT/signal_transition_state.json}"
TRANSITION_LLM_REVIEWS_SOURCE="${TRANSITION_LLM_REVIEWS_SOURCE:-$PROD_TOOLS_ROOT/signal_transition_llm_reviews.jsonl}"
LLM_USAGE_LEDGER="${LLM_USAGE_LEDGER:-$PROD_TOOLS_ROOT/signal_llm_usage_ledger.json}"
MAX_CARDS="${MAX_CARDS:-0}"
BACKUP_ROOT="${BACKUP_ROOT:-$PROD_TOOLS_ROOT/canary_backups}"
KEEP_CANARY_ROOT="${KEEP_CANARY_ROOT:-1}"

PROMOTE=0
ENABLE_TIMERS_AFTER_PASS=0
TARGET_CARD_ID="${TARGET_CARD_ID:-${ONLY_CARD_ID:-}}"
ROLLBACK_BACKUP=""
RESUME_CANARY_ROOT=""
BACKUP_DIR=""
PROMOTION_STARTED=0

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --target-card-id|--card-id|--only-card-id)
      TARGET_CARD_ID="${2:-}"
      shift 2
      ;;
    --target-card-id=*|--card-id=*|--only-card-id=*)
      TARGET_CARD_ID="${1#*=}"
      shift
      ;;
    --promote)
      PROMOTE=1
      shift
      ;;
    --enable-timers-after-pass)
      ENABLE_TIMERS_AFTER_PASS=1
      shift
      ;;
    --resume-canary-root)
      RESUME_CANARY_ROOT="${2:-}"
      shift 2
      ;;
    --resume-canary-root=*)
      RESUME_CANARY_ROOT="${1#*=}"
      shift
      ;;
    --rollback-backup)
      ROLLBACK_BACKUP="${2:-}"
      shift 2
      ;;
    --rollback-backup=*)
      ROLLBACK_BACKUP="${1#*=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

on_error() {
  local status="$?"
  trap - ERR
  echo "STATUS=FAIL"
  echo "TARGET_CARD_ID=${TARGET_CARD_ID:-UNKNOWN}"
  if [[ -n "${BACKUP_DIR:-}" ]]; then
    echo "ROLLBACK_COMMAND=$0 --rollback-backup $BACKUP_DIR"
    if [[ "${PROMOTION_STARTED:-0}" == "1" ]]; then
      if restore_from_backup "$BACKUP_DIR"; then
        echo "AUTO_ROLLBACK_STATUS=COMPLETE"
      else
        echo "AUTO_ROLLBACK_STATUS=FAILED"
      fi
    fi
  else
    echo "ROLLBACK_STATUS=NOT_STARTED"
  fi
  exit "$status"
}
trap on_error ERR

sha256_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    printf 'missing'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
  else
    /usr/bin/python3 - "$path" <<'PY'
import hashlib
import pathlib
import sys

print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
  fi
}

safe_name() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9_.+=@-' '_'
}

require_file() {
  if [[ ! -f "$1" ]]; then
    echo "STATUS=FAIL"
    echo "FAIL_REASON=missing required file: $1" >&2
    exit 2
  fi
}

require_target_card() {
  /usr/bin/python3 - "$JSONL_SOURCE" "$TARGET_CARD_ID" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = sys.argv[2]
for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
    if not line.strip():
        continue
    try:
        item = json.loads(line)
    except Exception:
        continue
    if not isinstance(item, dict):
        continue
    identity = item.get("identity") if isinstance(item.get("identity"), dict) else {}
    card_id = identity.get("card_id") or item.get("card_id")
    if str(card_id) == target:
        print("TARGET_FOUND=" + target)
        raise SystemExit(0)
raise SystemExit("target card not found in source JSONL: " + target)
PY
}

seed_canary_main_reviews() {
  local source="$1"
  local output="$2"
  /usr/bin/python3 - "$source" "$output" "$TARGET_CARD_ID" <<'PY'
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
output = pathlib.Path(sys.argv[2])
target = sys.argv[3]
kept = []
removed_target_ok = 0
for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
    if not line.strip():
        continue
    remove = False
    try:
        row = json.loads(line)
    except Exception:
        row = None
    if isinstance(row, dict) and str(row.get("card_id")) == target:
        review = row.get("llm_review") or {}
        if review.get("status") == "OK":
            remove = True
            removed_target_ok += 1
    if not remove:
        kept.append(line + "\n")
output.write_text("".join(kept), encoding="utf-8")
print("CANARY_SEED_REMOVED_TARGET_OK=" + str(removed_target_ok))
PY
}

is_recoverable_reconciliation_empty_content() {
  /usr/bin/python3 - "$CANARY_RUN_REVIEWS" "$CANARY_RUN_TRANSITION_REVIEWS" "$TARGET_CARD_ID" <<'PY'
import json
import pathlib
import sys

main_path = pathlib.Path(sys.argv[1])
transition_path = pathlib.Path(sys.argv[2])
target = sys.argv[3]

def rows(path):
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result

matching = [row for row in rows(main_path) if str(row.get("card_id")) == target]
if not matching:
    raise SystemExit(1)
review = matching[-1].get("llm_review") or {}
failure = review.get("failure_state") or {}
recoverable = (
    review.get("status") == "ERROR"
    and review.get("error_category") == "EMPTY_CONTENT"
    and failure.get("stage") == "RECONCILIATION"
    and failure.get("type") == "EMPTY_CONTENT"
    and failure.get("recovery_allowed") is True
    and failure.get("recovery_attempted") is False
    and bool(review.get("validated_blind_context"))
)
transition_error = any(
    (row.get("transition_llm_review") or {}).get("status") == "ERROR"
    for row in rows(transition_path)
)
raise SystemExit(0 if recoverable and not transition_error else 1)
PY
}

run_isolated_review() {
  local retry_id="${1:-}"
  TOOLS_ROOT="$TOOLS_ROOT" \
  JSONL_SOURCE="$JSONL_SOURCE" \
  LLM_REVIEWS_SOURCE="$CANARY_RUN_REVIEWS" \
  TRANSITION_LEDGER_SOURCE="$CANARY_TRANSITION_LEDGER" \
  TRANSITION_LLM_REVIEWS_SOURCE="$CANARY_RUN_TRANSITION_REVIEWS" \
  LLM_USAGE_LEDGER="$LLM_USAGE_LEDGER" \
  LLM_LOCK_FILE="$CANARY_ROOT/run_signal_llm_review.lock" \
  ONLY_CARD_ID="$TARGET_CARD_ID" \
  RETRY_ID="$retry_id" \
  LLM_REVIEW_LIMIT=1 \
  TRANSITION_REVIEW_LIMIT=1 \
  bash "$RUNNER"
}

merge_llm_reviews_for_target() {
  local production="$1"
  local canary="$2"
  local output="$3"
  /usr/bin/python3 - "$production" "$canary" "$output" "$TARGET_CARD_ID" <<'PY'
import json
import pathlib
import sys

production = pathlib.Path(sys.argv[1])
canary = pathlib.Path(sys.argv[2])
output = pathlib.Path(sys.argv[3])
target = sys.argv[4]

def read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows

def card_id(item):
    identity = item.get("identity") if isinstance(item.get("identity"), dict) else {}
    return item.get("card_id") or identity.get("card_id")

canary_rows = [item for item in read_jsonl(canary) if str(card_id(item)) == target]
if not canary_rows:
    raise SystemExit("canary LLM review sidecar has no target row: " + target)
canonical = canary_rows[-1]
review = canonical.get("llm_review") or {}
if review.get("status") != "OK":
    raise SystemExit("canonical canary LLM review is not OK: " + target)
rows = [item for item in read_jsonl(production) if str(card_id(item)) != target]
rows.append(canonical)
output.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                          for item in rows),
                  encoding="utf-8")
verified = [item for item in read_jsonl(output) if str(card_id(item)) == target]
if len(verified) != 1 or (verified[0].get("llm_review") or {}).get("status") != "OK":
    raise SystemExit("canonical target review replacement verification failed: " + target)
print("MERGED_LLM_REVIEW_ROWS=" + str(len(rows)))
print("MERGED_LLM_TARGET_ROWS=1")
print("MERGED_LLM_TARGET_STATUS=OK")
PY
}

merge_transition_reviews_for_target() {
  local production="$1"
  local canary="$2"
  local ledger="$3"
  local output="$4"
  /usr/bin/python3 - "$production" "$canary" "$ledger" "$output" "$TARGET_CARD_ID" <<'PY'
import json
import pathlib
import sys

production = pathlib.Path(sys.argv[1])
canary = pathlib.Path(sys.argv[2])
ledger = pathlib.Path(sys.argv[3])
output = pathlib.Path(sys.argv[4])
target = sys.argv[5]

def read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows

target_transition_ids = {
    item.get("transition_id")
    for item in read_jsonl(ledger)
    if str(item.get("current_card_id")) == target and item.get("transition_id")
}
canary_rows = [
    item for item in read_jsonl(canary)
    if item.get("transition_id") in target_transition_ids
]
rows = [
    item for item in read_jsonl(production)
    if item.get("transition_id") not in target_transition_ids
]
rows.extend(canary_rows)
output.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                          for item in rows),
                  encoding="utf-8")
print("TARGET_TRANSITION_IDS=" + str(len(target_transition_ids)))
print("MERGED_TRANSITION_REVIEW_ROWS=" + str(len(rows)))
PY
}

merge_usage_ledgers() {
  local production="$1"
  local canary="$2"
  local output="$3"
  /usr/bin/python3 - "$production" "$canary" "$output" <<'PY'
import json
import pathlib
import sys

production = pathlib.Path(sys.argv[1])
canary = pathlib.Path(sys.argv[2])
output = pathlib.Path(sys.argv[3])

def read_json(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

prod = read_json(production)
can = read_json(canary)
# Real canary requests reserve directly in the authoritative ledger. The
# canary copy is only an immutable release-evidence snapshot, never a counter
# to add to or restore over production.
merged = can or prod
output.write_text(json.dumps(merged, ensure_ascii=False, sort_keys=True), encoding="utf-8")
print("MERGED_USAGE_LEDGER=ok")
PY
}

backup_path() {
  local source="$1"
  local name="$2"
  local backup="$3"
  if [[ -e "$source" ]]; then
    cp -a "$source" "$backup/$name"
  else
    : > "$backup/$name.missing"
  fi
}

install_file_atomic() {
  local source="$1"
  local dest="$2"
  local mode="${3:-0644}"
  install -d "$(dirname "$dest")"
  local tmp
  tmp="$(mktemp "$(dirname "$dest")/.${name:-promote}.XXXXXX")"
  install -m "$mode" "$source" "$tmp"
  mv -f "$tmp" "$dest"
}

install_signal_cards_consistent() {
  local source="$1"
  local dest="$2"
  if [[ "$dest" != "$STATIC_ROOT/signal_cards" ]]; then
    echo "refusing signal-card install outside STATIC_ROOT: $dest" >&2
    return 2
  fi
  install -d "$(dirname "$dest")"
  install -d "$dest"
  while IFS= read -r -d '' card; do
    [[ "$(basename "$card")" == "index.json" ]] && continue
    install_file_atomic "$card" "$dest/$(basename "$card")" 0644
  done < <(find "$source" -maxdepth 1 -type f -print0)
  # Publish the manifest last. Readers either see the complete old set or the
  # complete new set; stale unreferenced files are pruned only afterwards.
  install_file_atomic "$source/index.json" "$dest/index.json" 0644
  while IFS= read -r -d '' existing; do
    [[ -f "$source/$(basename "$existing")" ]] || rm -f "$existing"
  done < <(find "$dest" -maxdepth 1 -type f -name '*.json' -print0)
}

validate_backup_scope() {
  /usr/bin/python3 - "$BACKUP_ROOT" "$1" <<'PY'
import os
import sys

root = os.path.realpath(sys.argv[1])
candidate = os.path.realpath(sys.argv[2])
if candidate == root or os.path.commonpath([root, candidate]) != root:
    raise SystemExit("rollback backup is outside BACKUP_ROOT")
PY
}

validate_static_root_scope() {
  /usr/bin/python3 - "$STATIC_ROOT" <<'PY'
import os
import pathlib
import sys

root = os.path.abspath(sys.argv[1])
if root == os.path.sep or len(pathlib.PurePath(root).parts) < 3:
    raise SystemExit("STATIC_ROOT is too broad for canary promotion")
PY
}

restore_from_backup() {
  local backup="$1"
  if [[ ! -d "$backup" ]]; then
    echo "STATUS=FAIL"
    echo "FAIL_REASON=backup not found: $backup" >&2
    exit 2
  fi
  validate_static_root_scope
  validate_backup_scope "$backup"
  if [[ -d "$backup/signal_cards" ]]; then
    install_signal_cards_consistent "$backup/signal_cards" "$STATIC_ROOT/signal_cards"
  elif [[ -f "$backup/signal_cards.missing" ]]; then
    if [[ -d "$STATIC_ROOT/signal_cards" ]]; then
      find "$STATIC_ROOT/signal_cards" -mindepth 1 -maxdepth 1 -type f -delete
      rmdir "$STATIC_ROOT/signal_cards"
    fi
  fi
  for item in \
    "llm_reviews:$LLM_REVIEWS_SOURCE" \
    "transition_ledger:$TRANSITION_LEDGER_SOURCE" \
    "transition_state:$TRANSITION_STATE_SOURCE" \
    "transition_reviews:$TRANSITION_LLM_REVIEWS_SOURCE"; do
    local name="${item%%:*}"
    local dest="${item#*:}"
    if [[ -f "$backup/$name" ]]; then
      install_file_atomic "$backup/$name" "$dest" 0644
    elif [[ -f "$backup/$name.missing" ]]; then
      rm -f "$dest"
    fi
  done
  echo "STATUS=ROLLBACK_COMPLETE"
  echo "ROLLBACK_BACKUP=$backup"
}

if [[ -n "$ROLLBACK_BACKUP" ]]; then
  restore_from_backup "$ROLLBACK_BACKUP"
  exit 0
fi

if [[ -z "$TARGET_CARD_ID" ]]; then
  echo "STATUS=FAIL"
  echo "FAIL_REASON=--target-card-id is required" >&2
  usage >&2
  exit 2
fi

validate_static_root_scope

require_file "$MATERIALIZER"
require_file "$RUNNER"
require_file "$SELF_CHECK"
require_file "$JSONL_SOURCE"

if [[ -n "$RESUME_CANARY_ROOT" ]]; then
  CANARY_ROOT="$(readlink -f "$RESUME_CANARY_ROOT")"
  case "$CANARY_ROOT" in
    /tmp/signal-llm-canary.*) ;;
    *)
      echo "FAIL_REASON=resume canary root is outside the scoped temp namespace" >&2
      exit 2
      ;;
  esac
  [[ -d "$CANARY_ROOT" ]] || {
    echo "FAIL_REASON=resume canary root does not exist: $CANARY_ROOT" >&2
    exit 2
  }
else
  CANARY_ROOT="${CANARY_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/signal-llm-canary.XXXXXX")}"
fi
CANARY_STATIC_ROOT="${CANARY_STATIC_ROOT:-$CANARY_ROOT/static}"
CANARY_REVIEWS="$CANARY_ROOT/signal_llm_reviews.merged.jsonl"
CANARY_RUN_REVIEWS="$CANARY_ROOT/signal_llm_reviews.run.jsonl"
CANARY_TRANSITION_LEDGER="$CANARY_ROOT/signal_transition_ledger.jsonl"
CANARY_TRANSITION_STATE="$CANARY_ROOT/signal_transition_state.json"
CANARY_TRANSITION_REVIEWS="$CANARY_ROOT/signal_transition_llm_reviews.merged.jsonl"
CANARY_RUN_TRANSITION_REVIEWS="$CANARY_ROOT/signal_transition_llm_reviews.run.jsonl"
CANARY_USAGE_LEDGER="$CANARY_ROOT/signal_llm_usage_ledger.json"
CANARY_MERGED_USAGE_LEDGER="$CANARY_ROOT/signal_llm_usage_ledger.merged.json"

if [[ "$KEEP_CANARY_ROOT" != "1" ]]; then
  trap 'rm -rf "$CANARY_ROOT"' EXIT
fi

install -d "$CANARY_STATIC_ROOT"
if [[ -z "$RESUME_CANARY_ROOT" ]]; then
  if [[ -f "$LLM_REVIEWS_SOURCE" ]]; then
    seed_canary_main_reviews "$LLM_REVIEWS_SOURCE" "$CANARY_RUN_REVIEWS"
  else
    : > "$CANARY_RUN_REVIEWS"
  fi
  if [[ -f "$TRANSITION_LLM_REVIEWS_SOURCE" ]]; then
    cp -a "$TRANSITION_LLM_REVIEWS_SOURCE" "$CANARY_RUN_TRANSITION_REVIEWS"
  else
    : > "$CANARY_RUN_TRANSITION_REVIEWS"
  fi
else
  require_file "$CANARY_RUN_REVIEWS"
  require_file "$CANARY_RUN_TRANSITION_REVIEWS"
  require_file "$CANARY_TRANSITION_LEDGER"
  require_file "$CANARY_TRANSITION_STATE"
fi

echo "STATUS=START"
echo "TARGET_CARD_ID=$TARGET_CARD_ID"
echo "PROMOTE=$PROMOTE"
echo "CANARY_ROOT=$CANARY_ROOT"
echo "CANARY_STATIC_ROOT=$CANARY_STATIC_ROOT"
echo "RESUME_CANARY_ROOT=${RESUME_CANARY_ROOT:-NONE}"
if command -v git >/dev/null 2>&1 && git -C "$REPO_ROOT" rev-parse HEAD >/dev/null 2>&1; then
  echo "COMMIT_SHA=$(git -C "$REPO_ROOT" rev-parse HEAD)"
else
  echo "COMMIT_SHA=UNKNOWN_INSTALLED_ASSET"
fi
/usr/bin/python3 - "$TOOLS_ROOT/signal_llm_review.py" <<'PY'
import importlib.util
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("signal_llm_review_canary_meta", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("LLM_PROVIDER=" + str(module.PROVIDER))
print("LLM_MODEL=" + str(module.DEFAULT_MODEL))
print("LLM_SCHEMA=" + str(module.OUTPUT_SCHEMA_VERSION))
print("LLM_PROMPT_VERSION=" + str(module.PROMPT_VERSION))
PY
for unit in signal-audit-materialize.service signal-audit-materialize.timer signal-audit-llm-review.service signal-audit-llm-review.timer; do
  echo "UNIT_${unit//[^A-Za-z0-9]/_}_SHA256=$(sha256_file "/etc/systemd/system/$unit")"
  if command -v systemctl >/dev/null 2>&1; then
    echo "UNIT_${unit//[^A-Za-z0-9]/_}_ACTIVE=$(systemctl is-active "$unit" 2>/dev/null || true)"
  fi
done

require_target_card

if [[ -n "$RESUME_CANARY_ROOT" ]]; then
  echo "RESUME_STATUS=REUSE_COMPLETED_HTTP_RESULTS"
else
  /usr/bin/python3 "$MATERIALIZER" \
    --source "$JSONL_SOURCE" \
    --require-valid-source-tail \
    --output "$CANARY_STATIC_ROOT" \
    --max-cards "$MAX_CARDS" \
    --transition-ledger "$CANARY_TRANSITION_LEDGER" \
    --transition-state "$CANARY_TRANSITION_STATE"

  RUNNER_RC=0
  # Always begin the exact target in an explicit retry epoch.  The isolated
  # copies above retain validated blind and completed transition history, so a
  # reconciliation-stage failure can recover without repeating either call.
  if run_isolated_review "$TARGET_CARD_ID"; then
    RUNNER_RC=0
  else
    RUNNER_RC=$?
  fi
  if [[ "$RUNNER_RC" != "0" ]]; then
    if is_recoverable_reconciliation_empty_content; then
      echo "RECOVERY_STATUS=START_RECONCILIATION_ONLY"
      run_isolated_review "$TARGET_CARD_ID"
      echo "RECOVERY_STATUS=PASS"
    else
      echo "RECOVERY_STATUS=NOT_ELIGIBLE"
      exit "$RUNNER_RC"
    fi
  else
    echo "RECOVERY_STATUS=NOT_NEEDED"
  fi
fi

merge_llm_reviews_for_target "$LLM_REVIEWS_SOURCE" "$CANARY_RUN_REVIEWS" "$CANARY_REVIEWS"
merge_transition_reviews_for_target \
  "$TRANSITION_LLM_REVIEWS_SOURCE" \
  "$CANARY_RUN_TRANSITION_REVIEWS" \
  "$CANARY_TRANSITION_LEDGER" \
  "$CANARY_TRANSITION_REVIEWS"
cp -a "$LLM_USAGE_LEDGER" "$CANARY_USAGE_LEDGER"
merge_usage_ledgers "$LLM_USAGE_LEDGER" "$CANARY_USAGE_LEDGER" "$CANARY_MERGED_USAGE_LEDGER"

/usr/bin/python3 "$MATERIALIZER" \
  --source "$JSONL_SOURCE" \
  --require-valid-source-tail \
  --output "$CANARY_STATIC_ROOT" \
  --max-cards "$MAX_CARDS" \
  --llm-reviews "$CANARY_REVIEWS" \
  --transition-ledger "$CANARY_TRANSITION_LEDGER" \
  --transition-state "$CANARY_TRANSITION_STATE" \
  --transition-reviews "$CANARY_TRANSITION_REVIEWS"

TARGET_CARD_ID="$TARGET_CARD_ID" \
ONLY_CARD_ID="$TARGET_CARD_ID" \
JSONL_SOURCE="$JSONL_SOURCE" \
AUDIT_ROOT="$CANARY_STATIC_ROOT" \
LLM_REVIEWS_SOURCE="$CANARY_REVIEWS" \
TRANSITION_LEDGER_SOURCE="$CANARY_TRANSITION_LEDGER" \
TRANSITION_LLM_REVIEWS_SOURCE="$CANARY_TRANSITION_REVIEWS" \
GEX_REQUIRED=0 \
SYSTEMD_REQUIRED=0 \
AUDIT_HTTP_REQUIRED=0 \
LLM_REQUIRED=1 \
INTEGRATED_ADVISORY_REQUIRED=1 \
TRANSITION_REQUIRED="${TRANSITION_REQUIRED:-0}" \
TRANSITION_LLM_REQUIRED="${TRANSITION_LLM_REQUIRED:-0}" \
bash "$SELF_CHECK"

echo "STATUS=PASS"
echo "TARGET_CARD_ID=$TARGET_CARD_ID"
echo "CANARY_LLM_REVIEWS_SHA256=$(sha256_file "$CANARY_REVIEWS")"
echo "CANARY_TRANSITION_LEDGER_SHA256=$(sha256_file "$CANARY_TRANSITION_LEDGER")"
echo "CANARY_TRANSITION_REVIEWS_SHA256=$(sha256_file "$CANARY_TRANSITION_REVIEWS")"
echo "CANARY_USAGE_LEDGER_SHA256=$(sha256_file "$CANARY_MERGED_USAGE_LEDGER")"
echo "CANARY_MANIFEST_SHA256=$(sha256_file "$CANARY_STATIC_ROOT/signal_cards/index.json")"

if [[ "$PROMOTE" != "1" ]]; then
  echo "PROMOTION_STATUS=SKIPPED"
  echo "ROLLBACK_STATUS=NOT_NEEDED"
  echo "NEXT_STEP=rerun with --promote to publish this validated canary"
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$BACKUP_ROOT/${STAMP}-$(safe_name "$TARGET_CARD_ID")"
install -d "$BACKUP_DIR"
backup_path "$STATIC_ROOT/signal_cards" "signal_cards" "$BACKUP_DIR"
backup_path "$LLM_REVIEWS_SOURCE" "llm_reviews" "$BACKUP_DIR"
backup_path "$TRANSITION_LEDGER_SOURCE" "transition_ledger" "$BACKUP_DIR"
backup_path "$TRANSITION_STATE_SOURCE" "transition_state" "$BACKUP_DIR"
backup_path "$TRANSITION_LLM_REVIEWS_SOURCE" "transition_reviews" "$BACKUP_DIR"

PROMOTION_STARTED=1
install_file_atomic "$CANARY_REVIEWS" "$LLM_REVIEWS_SOURCE" 0644
install_file_atomic "$CANARY_TRANSITION_LEDGER" "$TRANSITION_LEDGER_SOURCE" 0644
install_file_atomic "$CANARY_TRANSITION_STATE" "$TRANSITION_STATE_SOURCE" 0644
install_file_atomic "$CANARY_TRANSITION_REVIEWS" "$TRANSITION_LLM_REVIEWS_SOURCE" 0644
# signal_cards/fallback.js is a materializer-owned member of this directory.
# Publish it with the other card assets so backup, promotion, and rollback all
# use the same canonical path and manifest-last consistency boundary.
install_signal_cards_consistent "$CANARY_STATIC_ROOT/signal_cards" "$STATIC_ROOT/signal_cards"
PROMOTION_STARTED=0

echo "PROMOTION_STATUS=PROMOTED"
echo "BACKUP_DIR=$BACKUP_DIR"
echo "ROLLBACK_COMMAND=$0 --rollback-backup $BACKUP_DIR"
echo "PROMOTED_LLM_REVIEWS_SHA256=$(sha256_file "$LLM_REVIEWS_SOURCE")"
echo "PROMOTED_MANIFEST_SHA256=$(sha256_file "$STATIC_ROOT/signal_cards/index.json")"

if [[ "$ENABLE_TIMERS_AFTER_PASS" == "1" ]]; then
  systemctl enable --now signal-audit-materialize.timer
  systemctl enable --now signal-audit-llm-review.timer
  echo "TIMER_STATUS=ENABLED_AFTER_PASS"
else
  echo "TIMER_STATUS=UNCHANGED"
fi
