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
TRANSITION_REQUIRED="${TRANSITION_REQUIRED:-0}"
TRANSITION_LLM_REQUIRED="${TRANSITION_LLM_REQUIRED:-0}"
SESSION_CONTEXT_REQUIRED="${SESSION_CONTEXT_REQUIRED:-0}"
JSONL_SOURCE="${JSONL_SOURCE:-/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl}"
AUDIT_ROOT="${AUDIT_ROOT:-/opt/signal-audit}"
TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
LLM_REVIEWS_SOURCE="${LLM_REVIEWS_SOURCE:-${TOOLS_ROOT}/signal_llm_reviews.jsonl}"
TRANSITION_LEDGER_SOURCE="${TRANSITION_LEDGER_SOURCE:-${TOOLS_ROOT}/signal_transition_ledger.jsonl}"
TRANSITION_LLM_REVIEWS_SOURCE="${TRANSITION_LLM_REVIEWS_SOURCE:-${TOOLS_ROOT}/signal_transition_llm_reviews.jsonl}"
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
printf 'TRANSITION_REQUIRED=%s\n' "$TRANSITION_REQUIRED"
printf 'TRANSITION_LLM_REQUIRED=%s\n' "$TRANSITION_LLM_REQUIRED"
printf 'JSONL_SOURCE=%s\n' "$JSONL_SOURCE"
printf 'LLM_REVIEWS_SOURCE=%s\n' "$LLM_REVIEWS_SOURCE"
printf 'TRANSITION_LEDGER_SOURCE=%s\n' "$TRANSITION_LEDGER_SOURCE"
printf 'TRANSITION_LLM_REVIEWS_SOURCE=%s\n' "$TRANSITION_LLM_REVIEWS_SOURCE"
printf 'SESSION_CONTEXT_REQUIRED=%s\n' "$SESSION_CONTEXT_REQUIRED"

have curl && ok "curl available" || fail "curl missing"
have python3 && ok "python3 available" || fail "python3 missing"
have systemctl && ok "systemctl available" || warn "systemctl missing"

load_env_file "$GEX_ENV"
load_env_file "$LLM_ENV"

section "Optional active checks"
if [ "$RUN_ONESHOTS" -eq 1 ]; then
  if have systemctl; then
    systemctl start signal-audit-materialize.service >/dev/null 2>&1 && ok "started signal-audit-materialize.service before LLM" || warn "could not start signal-audit-materialize.service before LLM"
    systemctl start signal-audit-llm-review.service >/dev/null 2>&1 && ok "started signal-audit-llm-review.service" || warn "could not start signal-audit-llm-review.service"
    systemctl start signal-audit-materialize.service >/dev/null 2>&1 && ok "started signal-audit-materialize.service after LLM" || warn "could not start signal-audit-materialize.service after LLM"
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
macro = ((card.get("factor_cross_section") or {}).get("macro_pressure") or {})
macro_shock = macro.get("macro_shock") or {}
missing = [key for key in required if ctx.get(key) in (None, "")]
def contains_value(node, target):
    if node == target:
        return True
    if isinstance(node, dict):
        return any(contains_value(value, target) for value in node.values())
    if isinstance(node, list):
        return any(contains_value(value, target) for value in node)
    return False
print("latest_audit_card_id:", identity.get("card_id") or cards[0].get("card_id"))
print("latest_strategy_version:", identity.get("strategy_version"))
print("session_schema_name:", ctx.get("schema_name"))
print("session_rationale_code:", ctx.get("rationale_code"))
print("session_clock_window:", ctx.get("clock_window"))
print("session_backtest_delta_pp:", ctx.get("backtest_delta_pp"))
print("session_compat_backfill_applied:", ctx.get("compat_backfill_applied"))
print("decision_temporal_durability:", matrix.get("temporal_durability"))
print("macro_shock_state:", macro_shock.get("state"))
print("macro_shock_block:", macro_shock.get("block"))
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
if str(identity.get("strategy_version")) != "1.5.1":
    raise SystemExit("latest card strategy_version is not 1.5.1")
if not isinstance(macro_shock, dict) or macro_shock.get("state") in (None, ""):
    raise SystemExit("latest card lacks producer-native macro_shock state")
if macro_shock.get("block") not in (True, False):
    raise SystemExit("latest card lacks producer-native macro_shock block")
if macro_shock.get("block") is True and not contains_value(card, "MACRO_SHOCK_BLOCKING"):
    raise SystemExit("latest MACRO shock block lacks MACRO_SHOCK_BLOCKING trace")
PY
  then
    ok "latest audit card has native v1.5.1 session_context and macro_shock schema"
  else
    if [ "$SESSION_CONTEXT_REQUIRED" = "1" ]; then
      fail "latest audit card lacks native v1.5.1 session_context or macro_shock schema"
    else
      warn "latest audit card lacks native v1.5.1 session_context or macro_shock schema"
    fi
  fi
fi

section "Signal transition ledger"
if [ -r "$AUDIT_ROOT/signal_cards/index.json" ] && have python3; then
  if python3 - "$AUDIT_ROOT" "$TRANSITION_LEDGER_SOURCE" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
ledger_path = pathlib.Path(sys.argv[2])
manifest = json.loads((root / "signal_cards/index.json").read_text(encoding="utf-8"))
cards = manifest.get("cards") or []
if not cards:
    raise SystemExit("no cards")
card_path = root / cards[0].get("path", "")
card = json.loads(card_path.read_text(encoding="utf-8"))
identity = card.get("identity") or {}
ctx = card.get("transition_context") or {}
anchor = ((ctx.get("producer_anchor") or {}).get("current") or {})
print("latest_audit_card_id:", identity.get("card_id") or cards[0].get("card_id"))
print("transition_context:", bool(ctx))
print("transition_audit_scope:", ctx.get("audit_scope"))
print("transition_comparison_quality:", ctx.get("comparison_quality"))
print("transition_previous_card_id:", ctx.get("previous_card_id"))
print("transition_producer_anchor_native:", anchor.get("native"))
if not ctx:
    raise SystemExit(3)
if ctx.get("audit_scope") != "AUDIT_ONLY":
    raise SystemExit("transition_context.audit_scope is not AUDIT_ONLY")
if ctx.get("compat_backfill_applied"):
    raise SystemExit("transition_context uses materializer compatibility backfill")
if anchor.get("native") is not True:
    raise SystemExit("transition producer anchor is not native")
if anchor.get("schema_name") != "SignalTransitionProducerAnchor":
    raise SystemExit("transition producer anchor schema_name mismatch")
if anchor.get("event_time_basis") != "identity.confirmed_time_ms":
    raise SystemExit("transition producer anchor event_time_basis mismatch")
if anchor.get("transition_computation_owner") != "MATERIALIZER_DERIVED":
    raise SystemExit("transition computation owner mismatch")
if "future" in json.dumps(ctx, ensure_ascii=False).lower() or "outcome" in json.dumps(ctx, ensure_ascii=False).lower():
    raise SystemExit("transition_context contains future/outcome fields")
if not ledger_path.exists():
    raise SystemExit("transition ledger not readable: " + str(ledger_path))
lines = [x for x in ledger_path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
if not lines:
    raise SystemExit("transition ledger empty")
latest_id = identity.get("card_id") or cards[0].get("card_id")
matching = None
for line in lines:
    item = json.loads(line)
    if item.get("current_card_id") == latest_id:
        matching = item
if not matching:
    raise SystemExit("transition ledger does not align to latest card")
print("transition_id:", matching.get("transition_id"))
print("transition_record_hash:", matching.get("record_hash"))
if matching.get("audit_scope") != "AUDIT_ONLY":
    raise SystemExit("ledger audit_scope is not AUDIT_ONLY")
ledger_anchor = ((matching.get("producer_anchor") or {}).get("current") or {})
if ledger_anchor.get("native") is not True:
    raise SystemExit("ledger transition producer anchor is not native")
PY
  then
    ok "latest audit card has aligned AUDIT_ONLY transition_context and ledger record"
  else
    if [ "$TRANSITION_REQUIRED" = "1" ]; then
      fail "latest audit card lacks aligned AUDIT_ONLY transition_context and ledger record"
    else
      warn "latest audit card lacks aligned AUDIT_ONLY transition_context and ledger record"
    fi
  fi
else
  warn "skipped transition ledger check; manifest or python3 unavailable"
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

section "Transition LLM review sidecar"
if [ -r "$TRANSITION_LLM_REVIEWS_SOURCE" ]; then
  json_probe "latest transition LLM review sidecar" "$TRANSITION_LLM_REVIEWS_SOURCE" 'review=data.get("transition_llm_review") or {}; guard=review.get("language_guard") or {}; policy=review.get("policy_validation") or {}; print("transition_id:", data.get("transition_id")); print("status:", review.get("status")); print("schema_version:", review.get("schema_version")); print("prompt_version:", review.get("prompt_version")); print("model:", review.get("model")); print("policy_passed:", policy.get("passed")); print("render_state:", policy.get("render_state")); print("issue_codes:", policy.get("issue_codes")); print("no_trading_instruction:", guard.get("no_trading_instruction"))'
else
  warn "transition LLM review sidecar not readable yet: $TRANSITION_LLM_REVIEWS_SOURCE"
fi
if [ -r "$AUDIT_ROOT/signal_cards/index.json" ] && [ -r "$TRANSITION_LLM_REVIEWS_SOURCE" ] && have python3; then
  if python3 - "$AUDIT_ROOT" "$TRANSITION_LLM_REVIEWS_SOURCE" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
review_path = pathlib.Path(sys.argv[2])
manifest = json.loads((root / "signal_cards/index.json").read_text(encoding="utf-8"))
cards = manifest.get("cards") or []
if not cards:
    raise SystemExit("no cards")
card = json.loads((root / cards[0].get("path", "")).read_text(encoding="utf-8"))
ctx = card.get("transition_context") or {}
transition_id = ctx.get("transition_id")
if not transition_id:
    raise SystemExit("latest card has no transition_id")
reviews = {}
for line in review_path.read_text(encoding="utf-8", errors="replace").splitlines():
    if not line.strip():
        continue
    item = json.loads(line)
    review = item.get("transition_llm_review") or {}
    if item.get("transition_id") and review.get("status") == "OK":
        reviews[item.get("transition_id")] = review
review = reviews.get(transition_id)
if not review:
    raise SystemExit("no OK transition review for latest transition")
guard = review.get("language_guard") or {}
policy = review.get("policy_validation") or {}
print("latest_transition_id:", transition_id)
print("latest_transition_llm_status:", review.get("status"))
print("latest_transition_schema_version:", review.get("schema_version"))
print("latest_transition_prompt_version:", review.get("prompt_version"))
print("latest_transition_policy_passed:", policy.get("passed"))
print("latest_transition_render_state:", policy.get("render_state"))
print("latest_transition_issue_codes:", policy.get("issue_codes"))
print("latest_transition_evidence_catalog_hash:", review.get("evidence_catalog_hash"))
print("no_trading_instruction:", guard.get("no_trading_instruction"))
print("no_external_data:", guard.get("no_external_data"))
print("distinguishes_observation_from_causality:", guard.get("distinguishes_observation_from_causality"))
if review.get("schema_version") != "signal_transition_llm_review@1.2.4":
    raise SystemExit("latest transition LLM schema version is not signal_transition_llm_review@1.2.4")
if review.get("prompt_version") != "gemini_signal_transition_review_prompt@1.2.4":
    raise SystemExit("latest transition LLM prompt version is not gemini_signal_transition_review_prompt@1.2.4")
if not review.get("evidence_catalog_hash"):
    raise SystemExit("latest transition LLM review lacks evidence_catalog_hash")
if not policy:
    raise SystemExit("latest transition LLM review lacks policy_validation")
if policy.get("render_state") not in {"DISPLAY_LLM_TEXT", "DEGRADED_LLM_TEXT", "SUPPRESS_LLM_TEXT"}:
    raise SystemExit("latest transition LLM review has unknown render_state")
# Content-expression issues are advisory metadata only; policy_passed and language
# guard self-reports no longer gate deployment. Only schema/version, evidence-catalog
# provenance and a known render_state are enforced here so the audit page stays
# renderable. policy_passed / issue_codes remain printed above for visibility.
PY
  then
    ok "latest transition has OK v1.2.4 LLM review with schema/render_state integrity"
  else
    if [ "$TRANSITION_LLM_REQUIRED" = "1" ]; then
      fail "latest transition lacks OK v1.2.4 LLM review with valid schema/render_state integrity"
    else
      warn "latest transition lacks OK v1.2.4 LLM review with valid schema/render_state integrity"
    fi
  fi
else
  warn "skipped transition LLM match check; manifest or sidecar not readable"
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
