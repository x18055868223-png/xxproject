#!/usr/bin/env bash
# Self-check the neutral-loop signal stack on the strategy server.
# Default mode is read-only. Use --run-oneshots to trigger LLM/materialize once.

set -u

RUN_ONESHOTS=0
if [ "${1:-}" = "--run-oneshots" ]; then
  RUN_ONESHOTS=1
fi

SERVER_BASE_URL="${SERVER_BASE_URL:-http://127.0.0.1}"
AUDIT_URL="${AUDIT_URL:-${SERVER_BASE_URL}/signal-audit}"
GEX_URL="${GEX_URL:-http://127.0.0.1:8000}"
GEX_REQUIRED="${GEX_REQUIRED:-1}"
LLM_REQUIRED="${LLM_REQUIRED:-0}"
SESSION_CONTEXT_REQUIRED="${SESSION_CONTEXT_REQUIRED:-0}"
JSONL_SOURCE="${JSONL_SOURCE:-/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl}"
AUDIT_ROOT="${AUDIT_ROOT:-/opt/signal-audit}"
TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
LLM_REVIEWS_SOURCE="${LLM_REVIEWS_SOURCE:-${TOOLS_ROOT}/signal_llm_reviews.jsonl}"
GEX_ENV="${GEX_ENV:-/etc/gexmonitorapi.env}"
LLM_ENV="${LLM_ENV:-/etc/signal-audit/llm.env}"

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

section() {
  printf '\n== %s ==\n' "$1"
}

ok() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf '[OK] %s\n' "$1"
}

warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  printf '[WARN] %s\n' "$1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf '[FAIL] %s\n' "$1"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

load_env_file() {
  file="$1"
  if [ -r "$file" ]; then
    # shellcheck disable=SC1090
    set -a; . "$file"; set +a
    ok "loaded env file: $file"
  else
    warn "env file not readable or absent: $file"
  fi
}

systemd_state() {
  unit="$1"
  if ! have systemctl; then
    warn "systemctl not available; skipped $unit"
    return
  fi
  if ! systemctl list-unit-files "$unit" >/dev/null 2>&1; then
    warn "unit not found: $unit"
    return
  fi
  active="$(systemctl show "$unit" -p ActiveState --value 2>/dev/null || true)"
  result="$(systemctl show "$unit" -p Result --value 2>/dev/null || true)"
  sub="$(systemctl show "$unit" -p SubState --value 2>/dev/null || true)"
  if [ "$active" = "active" ] || { [ "$active" = "inactive" ] && [ "${result:-success}" = "success" ]; }; then
    ok "$unit ActiveState=$active SubState=$sub Result=${result:-n/a}"
  else
    fail "$unit ActiveState=$active SubState=$sub Result=${result:-n/a}"
  fi
}

timer_state() {
  unit="$1"
  if ! have systemctl; then
    warn "systemctl not available; skipped $unit"
    return
  fi
  active="$(systemctl show "$unit" -p ActiveState --value 2>/dev/null || true)"
  next="$(systemctl list-timers "$unit" --no-pager --no-legend 2>/dev/null | awk '{print $1" "$2" "$3" "$4}' || true)"
  if [ "$active" = "active" ]; then
    ok "$unit active; next=${next:-unknown}"
  else
    fail "$unit ActiveState=$active"
  fi
}

http_head() {
  label="$1"
  url="$2"
  if ! have curl; then
    warn "curl not available; skipped $label"
    return
  fi
  code="$(curl -k -L -s -o /dev/null -w '%{http_code}' "$url" || true)"
  case "$code" in
    2*|3*) ok "$label HTTP $code $url" ;;
    *) fail "$label HTTP $code $url" ;;
  esac
}

json_probe() {
  label="$1"
  file="$2"
  python_code="$3"
  if ! have python3; then
    warn "python3 not available; skipped $label"
    return
  fi
  if [ ! -r "$file" ]; then
    warn "$label file not readable: $file"
    return
  fi
  if python3 - "$file" <<PY
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
lines = [x for x in path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
if not lines:
    raise SystemExit("empty")
data = json.loads(lines[-1])
$python_code
PY
  then
    ok "$label JSON parsed"
  else
    fail "$label JSON parse failed: $file"
  fi
}

section "Environment"
printf 'AUDIT_URL=%s\n' "$AUDIT_URL"
printf 'GEX_URL=%s\n' "$GEX_URL"
printf 'GEX_REQUIRED=%s\n' "$GEX_REQUIRED"
printf 'LLM_REQUIRED=%s\n' "$LLM_REQUIRED"
printf 'JSONL_SOURCE=%s\n' "$JSONL_SOURCE"
printf 'LLM_REVIEWS_SOURCE=%s\n' "$LLM_REVIEWS_SOURCE"
printf 'SESSION_CONTEXT_REQUIRED=%s\n' "$SESSION_CONTEXT_REQUIRED"

have curl && ok "curl available" || fail "curl missing"
have python3 && ok "python3 available" || fail "python3 missing"
have systemctl && ok "systemctl available" || warn "systemctl missing"

load_env_file "$GEX_ENV"
load_env_file "$LLM_ENV"

section "Optional active checks"
if [ "$RUN_ONESHOTS" -eq 1 ]; then
  if have systemctl; then
    systemctl start signal-audit-llm-review.service >/dev/null 2>&1 && ok "started signal-audit-llm-review.service" || warn "could not start signal-audit-llm-review.service"
    systemctl start signal-audit-materialize.service >/dev/null 2>&1 && ok "started signal-audit-materialize.service" || warn "could not start signal-audit-materialize.service"
  fi
else
  warn "read-only mode; pass --run-oneshots to trigger LLM/materializer once"
fi

section "Systemd services"
if [ "$GEX_REQUIRED" = "1" ]; then
  systemd_state gexmonitorapi.service
else
  warn "GEX_REQUIRED=0; skipped gexmonitorapi.service check"
fi
systemd_state signal-audit-materialize.service
systemd_state signal-audit-llm-review.service
timer_state signal-audit-materialize.timer
timer_state signal-audit-llm-review.timer

section "FMZ signal JSONL"
if [ -r "$JSONL_SOURCE" ]; then
  bytes="$(wc -c < "$JSONL_SOURCE" 2>/dev/null || echo 0)"
  lines="$(wc -l < "$JSONL_SOURCE" 2>/dev/null || echo 0)"
  ok "signal_review.jsonl readable; lines=$lines bytes=$bytes"
  json_probe "latest signal review card" "$JSONL_SOURCE" 'print("card_id:", data.get("identity", {}).get("card_id")); print("llm_review:", bool(data.get("llm_review")))'
else
  fail "signal_review.jsonl not readable: $JSONL_SOURCE"
fi

section "GEX Monitor API"
if [ "$GEX_REQUIRED" = "1" ]; then
  http_head "gex health" "${GEX_URL%/}/health"
  if [ -n "${API_TOKEN:-}" ]; then
    tmp_gex="$(mktemp)"
    if curl -k -s -H "Authorization: Bearer ${API_TOKEN}" "${GEX_URL%/}/v1/info" > "$tmp_gex"; then
      if python3 - "$tmp_gex" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
rank = data.get("rank") or {}
window = rank.get("window") or {}
print("availability:", data.get("availability"))
print("stale:", data.get("stale"))
print("rank_samples:", window.get("sample_count"))
print("net_gex:", (data.get("gex_board") or {}).get("total_net_gex"))
PY
      then
        ok "gex /v1/info JSON parsed with rank context"
      else
        fail "gex /v1/info returned invalid JSON"
      fi
    else
      fail "gex /v1/info request failed"
    fi
    rm -f "$tmp_gex"
  else
    warn "API_TOKEN not loaded; skipped authenticated /v1/info"
  fi
else
  warn "GEX_REQUIRED=0; skipped GEX Monitor API active checks"
fi

section "Signal audit frontend"
http_head "audit page" "${AUDIT_URL%/}/"
http_head "audit manifest" "${AUDIT_URL%/}/signal_cards/index.json"
if [ -r "$AUDIT_ROOT/signal_cards/index.json" ]; then
  if python3 - "$AUDIT_ROOT/signal_cards/index.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
cards = data.get("cards") or []
print("cards:", len(cards))
print("first:", cards[0].get("card_id") if cards else None)
PY
  then
    ok "audit manifest parsed"
  else
    fail "audit manifest parse failed"
  fi
else
  fail "audit manifest not readable at $AUDIT_ROOT/signal_cards/index.json"
fi
if [ -r "$AUDIT_ROOT/signal_cards/index.json" ] && have python3; then
  if python3 - "$AUDIT_ROOT" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / "signal_cards/index.json").read_text(encoding="utf-8"))
cards = manifest.get("cards") or []
if not cards:
    raise SystemExit("no cards")
path = root / cards[0].get("path", "")
card = json.loads(path.read_text(encoding="utf-8"))
identity = card.get("identity") or {}
ctx = ((card.get("signal_window") or {}).get("session_context") or {})
matrix = card.get("decision_matrix") or {}
required = [
    "schema_name", "clock_window", "adjustment_direction", "evidence_level",
    "backtest_delta_pp", "validation_basis", "confidence_policy",
]
missing = [key for key in required if ctx.get(key) in (None, "")]
print("latest_audit_card_id:", identity.get("card_id") or cards[0].get("card_id"))
print("latest_strategy_version:", identity.get("strategy_version"))
print("session_schema_name:", ctx.get("schema_name"))
print("session_rationale_code:", ctx.get("rationale_code"))
print("session_clock_window:", ctx.get("clock_window"))
print("session_backtest_delta_pp:", ctx.get("backtest_delta_pp"))
print("session_compat_backfill_applied:", ctx.get("compat_backfill_applied"))
print("decision_temporal_durability:", matrix.get("temporal_durability"))
if ctx.get("schema_name") != "SignalSessionPremiseDurabilityContext":
    raise SystemExit(2)
if missing:
    raise SystemExit("missing session context fields: " + ",".join(missing))
if not isinstance(ctx.get("validation_basis"), dict):
    raise SystemExit("validation_basis not structured")
if matrix.get("temporal_durability") != ctx.get("premise_durability"):
    raise SystemExit("decision_matrix temporal_durability mismatch")
if ctx.get("compat_backfill_applied"):
    raise SystemExit("latest card uses materializer compatibility backfill")
if str(identity.get("strategy_version")) != "1.4.1":
    raise SystemExit("latest card strategy_version is not 1.4.1")
PY
  then
    ok "latest audit card has native v1.4.1 session_context premise durability schema"
  else
    if [ "$SESSION_CONTEXT_REQUIRED" = "1" ]; then
      fail "latest audit card lacks native v1.4.1 session_context premise durability schema"
    else
      warn "latest audit card lacks native v1.4.1 session_context premise durability schema"
    fi
  fi
fi

section "LLM review sidecar"
CHANNEL1_KEY="${GEMINI_CHANNEL1_API_KEY:-}"
CHANNEL2_KEY="${GEMINI_CHANNEL2_API_KEY:-}"
if [ -n "$CHANNEL1_KEY" ]; then
  ok "Gemini channel 1 key is configured in environment"
else
  if [ "$LLM_REQUIRED" = "1" ]; then
    fail "Gemini channel 1 key is not loaded; free/low-cost first pass is disabled"
  else
    warn "Gemini channel 1 key is not loaded; free/low-cost first pass is disabled"
  fi
fi
if [ -n "$CHANNEL2_KEY" ]; then
  ok "Gemini channel 2 key is configured in environment"
else
  if [ "$LLM_REQUIRED" = "1" ]; then
    fail "Gemini channel 2 key is not loaded; paid fallback is disabled"
  else
    warn "Gemini channel 2 key is not loaded; paid fallback is disabled"
  fi
fi
if [ -z "$CHANNEL1_KEY" ] && [ -z "$CHANNEL2_KEY" ]; then
  if [ "$LLM_REQUIRED" = "1" ]; then
    fail "no Gemini channel key loaded; LLM timer will skip calls"
  else
    warn "no Gemini channel key loaded; LLM timer will skip calls"
  fi
fi
if [ -r "$LLM_REVIEWS_SOURCE" ]; then
  json_probe "latest LLM review sidecar" "$LLM_REVIEWS_SOURCE" 'review=data.get("llm_review") or {}; print("card_id:", data.get("card_id")); print("status:", review.get("status")); print("model:", review.get("model")); print("blind_review_mode:", review.get("blind_review_mode")); print("llm_call_count:", review.get("llm_call_count")); print("api_key_route:", review.get("api_key_route")); print("llm_call_routes:", review.get("llm_call_routes"))'
else
  warn "LLM review sidecar not readable yet: $LLM_REVIEWS_SOURCE"
fi
if [ -r "$JSONL_SOURCE" ] && [ -r "$LLM_REVIEWS_SOURCE" ] && have python3; then
  if python3 - "$JSONL_SOURCE" "$LLM_REVIEWS_SOURCE" <<'PY'
import json, pathlib, sys
signal_path = pathlib.Path(sys.argv[1])
review_path = pathlib.Path(sys.argv[2])
signal_lines = [x for x in signal_path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
review_lines = [x for x in review_path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
if not signal_lines:
    raise SystemExit("signal_review.jsonl empty")
latest = json.loads(signal_lines[-1])
latest_id = (latest.get("identity") or {}).get("card_id") or latest.get("card_id")
ok_reviews = {}
latest_ok_id = None
for line in review_lines:
    item = json.loads(line)
    review = item.get("llm_review") or {}
    card_id = item.get("card_id") or ((item.get("identity") or {}).get("card_id"))
    if review.get("status") == "OK" and card_id:
        ok_reviews[card_id] = review
        latest_ok_id = card_id
print("latest_signal_card_id:", latest_id)
print("latest_ok_llm_card_id:", latest_ok_id)
review = ok_reviews.get(latest_id)
if not review:
    raise SystemExit(3)
print("latest_signal_llm_status:", review.get("status"))
print("latest_signal_blind_review_mode:", review.get("blind_review_mode"))
print("latest_signal_llm_call_count:", review.get("llm_call_count"))
print("latest_signal_api_key_route:", review.get("api_key_route"))
print("latest_signal_llm_call_routes:", review.get("llm_call_routes"))
if review.get("blind_review_mode") != "two_call_strict" or int(review.get("llm_call_count") or 0) < 2:
    raise SystemExit(4)
PY
  then
    ok "latest signal card has OK two-call LLM sidecar review"
  else
    if [ "$LLM_REQUIRED" = "1" ]; then
      fail "latest signal card does not have an OK two-call LLM sidecar review"
    else
      warn "latest signal card does not have an OK two-call LLM sidecar review"
    fi
  fi
else
  warn "skipped latest-card LLM match check; source or sidecar not readable"
fi

section "Listening ports and memory"
if have ss; then
  ss -ltnp 2>/dev/null | grep -E ':(80|8000)\s' || warn "ports 80/8000 not visible in ss output"
else
  warn "ss not available"
fi
have free && free -h || warn "free not available"

section "Summary"
printf 'PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
if [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi
exit 0
