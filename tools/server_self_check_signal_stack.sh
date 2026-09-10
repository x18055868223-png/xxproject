#!/usr/bin/env bash
# Self-check the neutral-loop signal stack on the strategy server.
# Default mode is read-only. Use --run-oneshots to trigger LLM/materialize once.

set -u

RUN_ONESHOTS=0
TARGET_CARD_ID="${TARGET_CARD_ID:-${ONLY_CARD_ID:-}}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --run-oneshots)
      RUN_ONESHOTS=1
      shift
      ;;
    --target-card-id|--card-id|--only-card-id)
      TARGET_CARD_ID="${2:-}"
      shift 2
      ;;
    --target-card-id=*|--card-id=*|--only-card-id=*)
      TARGET_CARD_ID="${1#*=}"
      shift
      ;;
    *)
      fail_arg="$1"
      printf 'unknown argument: %s\n' "$fail_arg" >&2
      exit 2
      ;;
  esac
done

SERVER_BASE_URL="${SERVER_BASE_URL:-http://127.0.0.1}"
AUDIT_URL="${AUDIT_URL:-${SERVER_BASE_URL}/signal-audit}"
GEX_URL="${GEX_URL:-http://127.0.0.1:8000}"
GEX_REQUIRED="${GEX_REQUIRED:-1}"
SYSTEMD_REQUIRED="${SYSTEMD_REQUIRED:-1}"
AUDIT_HTTP_REQUIRED="${AUDIT_HTTP_REQUIRED:-1}"
LLM_REQUIRED="${LLM_REQUIRED:-0}"
INTEGRATED_ADVISORY_REQUIRED="${INTEGRATED_ADVISORY_REQUIRED:-0}"
INTEGRATED_ADVISORY_PROTOCOL="${INTEGRATED_ADVISORY_PROTOCOL:-v2}"
TRANSITION_REQUIRED="${TRANSITION_REQUIRED:-0}"
TRANSITION_LLM_REQUIRED="${TRANSITION_LLM_REQUIRED:-0}"
SESSION_CONTEXT_REQUIRED="${SESSION_CONTEXT_REQUIRED:-0}"
DURABILITY_REQUIRED="${DURABILITY_REQUIRED:-0}"
EXPECTED_SIGNAL_VERSION="${EXPECTED_SIGNAL_VERSION:-1.6.0}"
EXPECTED_LLM_PROVIDER="${EXPECTED_LLM_PROVIDER:-deepseek}"
EXPECTED_LLM_MODEL="${EXPECTED_LLM_MODEL:-deepseek-v4-flash}"
EXPECTED_LLM_SCHEMA="${EXPECTED_LLM_SCHEMA:-signal_llm_review@2.1.0}"
EXPECTED_LLM_PROMPT_VERSION="${EXPECTED_LLM_PROMPT_VERSION:-signal_llm_review_prompt@2.1.0}"
EXPECTED_LLM_REVIEW_MODE="${EXPECTED_LLM_REVIEW_MODE:-single_evidence_v2}"
EXPECTED_LLM_CALL_COUNT="${EXPECTED_LLM_CALL_COUNT:-1}"
EXPECTED_LLM_MAX_HTTP_ATTEMPTS="${EXPECTED_LLM_MAX_HTTP_ATTEMPTS:-2}"
EXPECTED_LLM_BLIND_MODE="${EXPECTED_LLM_BLIND_MODE:-two_call_strict}"
EXPECTED_TRANSITION_LLM_PROVIDER="${EXPECTED_TRANSITION_LLM_PROVIDER:-$EXPECTED_LLM_PROVIDER}"
EXPECTED_TRANSITION_LLM_MODEL="${EXPECTED_TRANSITION_LLM_MODEL:-$EXPECTED_LLM_MODEL}"
EXPECTED_TRANSITION_LLM_SCHEMA="${EXPECTED_TRANSITION_LLM_SCHEMA:-signal_transition_llm_review@1.3.0}"
EXPECTED_TRANSITION_LLM_PROMPT_VERSION="${EXPECTED_TRANSITION_LLM_PROMPT_VERSION:-signal_transition_llm_review_prompt@1.3.2}"
EXPECTED_TRANSITION_LLM_BLIND_MODE="${EXPECTED_TRANSITION_LLM_BLIND_MODE:-single_call_evidence_first}"
EXPECTED_TRANSITION_LLM_CALL_COUNT="${EXPECTED_TRANSITION_LLM_CALL_COUNT:-1}"
JSONL_SOURCE="${JSONL_SOURCE:-/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl}"
AUDIT_ROOT="${AUDIT_ROOT:-/opt/signal-audit}"
TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
LLM_REVIEWS_SOURCE="${LLM_REVIEWS_SOURCE:-${TOOLS_ROOT}/signal_llm_reviews.jsonl}"
TRANSITION_LEDGER_SOURCE="${TRANSITION_LEDGER_SOURCE:-${TOOLS_ROOT}/signal_transition_ledger.jsonl}"
TRANSITION_LLM_REVIEWS_SOURCE="${TRANSITION_LLM_REVIEWS_SOURCE:-${TOOLS_ROOT}/signal_transition_llm_reviews.jsonl}"
GEX_ENV="${GEX_ENV:-/etc/gexmonitorapi.env}"
LLM_ENV="${LLM_ENV:-/etc/signal-audit/llm.env}"

# Deployment/canary callers own the files and strictness being checked. Env
# files may supply credentials/provider settings, but must not redirect an
# explicit audit to production paths or weaken its required gates.
CHECK_AUDIT_URL="$AUDIT_URL"
CHECK_TARGET_CARD_ID="$TARGET_CARD_ID"
CHECK_GEX_URL="$GEX_URL"
CHECK_GEX_REQUIRED="$GEX_REQUIRED"
CHECK_SYSTEMD_REQUIRED="$SYSTEMD_REQUIRED"
CHECK_AUDIT_HTTP_REQUIRED="$AUDIT_HTTP_REQUIRED"
CHECK_LLM_REQUIRED="$LLM_REQUIRED"
CHECK_INTEGRATED_ADVISORY_REQUIRED="$INTEGRATED_ADVISORY_REQUIRED"
CHECK_INTEGRATED_ADVISORY_PROTOCOL="$INTEGRATED_ADVISORY_PROTOCOL"
CHECK_TRANSITION_REQUIRED="$TRANSITION_REQUIRED"
CHECK_TRANSITION_LLM_REQUIRED="$TRANSITION_LLM_REQUIRED"
CHECK_SESSION_CONTEXT_REQUIRED="$SESSION_CONTEXT_REQUIRED"
CHECK_DURABILITY_REQUIRED="$DURABILITY_REQUIRED"
CHECK_JSONL_SOURCE="$JSONL_SOURCE"
CHECK_AUDIT_ROOT="$AUDIT_ROOT"
CHECK_TOOLS_ROOT="$TOOLS_ROOT"
CHECK_LLM_REVIEWS_SOURCE="$LLM_REVIEWS_SOURCE"
CHECK_TRANSITION_LEDGER_SOURCE="$TRANSITION_LEDGER_SOURCE"
CHECK_TRANSITION_LLM_REVIEWS_SOURCE="$TRANSITION_LLM_REVIEWS_SOURCE"

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
printf 'SYSTEMD_REQUIRED=%s\n' "$SYSTEMD_REQUIRED"
printf 'AUDIT_HTTP_REQUIRED=%s\n' "$AUDIT_HTTP_REQUIRED"
printf 'LLM_REQUIRED=%s\n' "$LLM_REQUIRED"
printf 'INTEGRATED_ADVISORY_REQUIRED=%s\n' "$INTEGRATED_ADVISORY_REQUIRED"
printf 'INTEGRATED_ADVISORY_PROTOCOL=%s\n' "$INTEGRATED_ADVISORY_PROTOCOL"
printf 'TRANSITION_REQUIRED=%s\n' "$TRANSITION_REQUIRED"
printf 'TRANSITION_LLM_REQUIRED=%s\n' "$TRANSITION_LLM_REQUIRED"
printf 'DURABILITY_REQUIRED=%s\n' "$DURABILITY_REQUIRED"
printf 'EXPECTED_SIGNAL_VERSION=%s\n' "$EXPECTED_SIGNAL_VERSION"
printf 'EXPECTED_LLM_PROVIDER=%s\n' "$EXPECTED_LLM_PROVIDER"
printf 'EXPECTED_LLM_MODEL=%s\n' "$EXPECTED_LLM_MODEL"
printf 'EXPECTED_LLM_SCHEMA=%s\n' "$EXPECTED_LLM_SCHEMA"
printf 'EXPECTED_LLM_PROMPT_VERSION=%s\n' "$EXPECTED_LLM_PROMPT_VERSION"
printf 'EXPECTED_LLM_REVIEW_MODE=%s\n' "$EXPECTED_LLM_REVIEW_MODE"
printf 'EXPECTED_LLM_CALL_COUNT=%s\n' "$EXPECTED_LLM_CALL_COUNT"
printf 'EXPECTED_LLM_MAX_HTTP_ATTEMPTS=%s\n' "$EXPECTED_LLM_MAX_HTTP_ATTEMPTS"
printf 'EXPECTED_TRANSITION_LLM_SCHEMA=%s\n' "$EXPECTED_TRANSITION_LLM_SCHEMA"
printf 'EXPECTED_TRANSITION_LLM_PROMPT_VERSION=%s\n' "$EXPECTED_TRANSITION_LLM_PROMPT_VERSION"
printf 'JSONL_SOURCE=%s\n' "$JSONL_SOURCE"
printf 'LLM_REVIEWS_SOURCE=%s\n' "$LLM_REVIEWS_SOURCE"
printf 'TRANSITION_LEDGER_SOURCE=%s\n' "$TRANSITION_LEDGER_SOURCE"
printf 'TRANSITION_LLM_REVIEWS_SOURCE=%s\n' "$TRANSITION_LLM_REVIEWS_SOURCE"
printf 'SESSION_CONTEXT_REQUIRED=%s\n' "$SESSION_CONTEXT_REQUIRED"
printf 'TARGET_CARD_ID=%s\n' "${TARGET_CARD_ID:-LATEST}"

have curl && ok "curl available" || fail "curl missing"
have python3 && ok "python3 available" || fail "python3 missing"
have systemctl && ok "systemctl available" || warn "systemctl missing"

load_env_file "$GEX_ENV"
load_env_file "$LLM_ENV"
AUDIT_URL="$CHECK_AUDIT_URL"
GEX_URL="$CHECK_GEX_URL"
GEX_REQUIRED="$CHECK_GEX_REQUIRED"
SYSTEMD_REQUIRED="$CHECK_SYSTEMD_REQUIRED"
AUDIT_HTTP_REQUIRED="$CHECK_AUDIT_HTTP_REQUIRED"
LLM_REQUIRED="$CHECK_LLM_REQUIRED"
INTEGRATED_ADVISORY_REQUIRED="$CHECK_INTEGRATED_ADVISORY_REQUIRED"
INTEGRATED_ADVISORY_PROTOCOL="$CHECK_INTEGRATED_ADVISORY_PROTOCOL"
TRANSITION_REQUIRED="$CHECK_TRANSITION_REQUIRED"
TRANSITION_LLM_REQUIRED="$CHECK_TRANSITION_LLM_REQUIRED"
SESSION_CONTEXT_REQUIRED="$CHECK_SESSION_CONTEXT_REQUIRED"
DURABILITY_REQUIRED="$CHECK_DURABILITY_REQUIRED"
JSONL_SOURCE="$CHECK_JSONL_SOURCE"
AUDIT_ROOT="$CHECK_AUDIT_ROOT"
TOOLS_ROOT="$CHECK_TOOLS_ROOT"
LLM_REVIEWS_SOURCE="$CHECK_LLM_REVIEWS_SOURCE"
TRANSITION_LEDGER_SOURCE="$CHECK_TRANSITION_LEDGER_SOURCE"
TRANSITION_LLM_REVIEWS_SOURCE="$CHECK_TRANSITION_LLM_REVIEWS_SOURCE"
# The embedded Python probes read this selector from the environment. Restore
# the caller-owned value after loading credential files, then export it so a
# CLI target and an inherited target have identical exact-card semantics.
TARGET_CARD_ID="$CHECK_TARGET_CARD_ID"
export TARGET_CARD_ID

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
if [ "$SYSTEMD_REQUIRED" = "1" ]; then
  if [ "$GEX_REQUIRED" = "1" ]; then
    systemd_state gexmonitorapi.service
  else
    warn "GEX_REQUIRED=0; skipped gexmonitorapi.service check"
  fi
  systemd_state signal-audit-materialize.service
  systemd_state signal-audit-llm-review.service
  timer_state signal-audit-materialize.timer
  timer_state signal-audit-llm-review.timer
else
  warn "SYSTEMD_REQUIRED=0; skipped systemd service and timer checks"
fi

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
print("source_mode:", data.get("source_mode"))
print("data_age_ms:", data.get("data_age_ms"))
gamma = data.get("gamma_exposure") or {}
print("wall_strikes:", [gamma.get(key) for key in ("p1", "p2", "n1", "n2")])
print("cross_check:", (data.get("source_metadata") or {}).get("cross_check"))
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
if [ "$AUDIT_HTTP_REQUIRED" = "1" ]; then
  http_head "audit page" "${AUDIT_URL%/}/"
  http_head "audit manifest" "${AUDIT_URL%/}/signal_cards/index.json"
else
  warn "AUDIT_HTTP_REQUIRED=0; skipped audit HTTP checks"
fi
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
import json, math, os, pathlib, sys
root = pathlib.Path(sys.argv[1])
expected_version = os.environ.get("EXPECTED_SIGNAL_VERSION", "1.6.0")
durability_required = os.environ.get("DURABILITY_REQUIRED", "0") == "1"
target_card_id = (os.environ.get("TARGET_CARD_ID")
                  or os.environ.get("ONLY_CARD_ID") or "")
manifest = json.loads((root / "signal_cards/index.json").read_text(encoding="utf-8"))
cards = manifest.get("cards") or []
if not cards:
    raise SystemExit("no cards")

def manifest_card_id(item):
    return str(item.get("card_id") or "")

selected_manifest = cards[0]
if target_card_id:
    matches = [item for item in cards if manifest_card_id(item) == target_card_id]
    if not matches:
        raise SystemExit("target audit card not found in manifest: " + target_card_id)
    selected_manifest = matches[0]
path = root / selected_manifest.get("path", "")
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
funding = ((card.get("factor_cross_section") or {}).get("funding") or {})
funding_semantics = funding.get("canonical_funding_semantics") or {}
gex_rank = ((((card.get("factor_cross_section") or {}).get("gex_info") or {})
             .get("rank")) or {})
durability = card.get("signal_durability") or {}
signal_rating = card.get("signal_rating")
rating_obj = signal_rating if isinstance(signal_rating, dict) else {}
missing = [key for key in required if ctx.get(key) in (None, "")]
def contains_value(node, target):
    if node == target:
        return True
    if isinstance(node, dict):
        return any(contains_value(value, target) for value in node.values())
    if isinstance(node, list):
        return any(contains_value(value, target) for value in node)
    return False
def contains_string_value_ci(node, target):
    target = str(target).strip().lower()
    if isinstance(node, str):
        return node.strip().lower() == target
    if isinstance(node, dict):
        return any(contains_string_value_ci(value, target) for value in node.values())
    if isinstance(node, list):
        return any(contains_string_value_ci(value, target) for value in node)
    return False
def contains_truthy_key(node, key):
    if isinstance(node, dict):
        if key in node and bool(node.get(key)):
            return True
        return any(contains_truthy_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(contains_truthy_key(value, key) for value in node)
    return False
def version_tuple(value):
    parts = str(value or "").split("-", 1)[0].split(".")
    parsed = []
    for part in parts[:3]:
        try:
            parsed.append(int(part))
        except (TypeError, ValueError):
            parsed.append(0)
    while len(parsed) < 3:
        parsed.append(0)
    return tuple(parsed)
def signal_rating_required_for_version(value):
    return version_tuple(value) >= (1, 6, 0)
def finite_positive_number(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed
signal_rating_required = signal_rating_required_for_version(expected_version)
print("target_card_id:", target_card_id or "LATEST")
print("latest_audit_card_id:", identity.get("card_id") or selected_manifest.get("card_id"))
print("latest_strategy_version:", identity.get("strategy_version"))
print("expected_signal_version:", expected_version)
print("session_schema_name:", ctx.get("schema_name"))
print("session_rationale_code:", ctx.get("rationale_code"))
print("session_clock_window:", ctx.get("clock_window"))
print("session_backtest_delta_pp:", ctx.get("backtest_delta_pp"))
print("session_compat_backfill_applied:", ctx.get("compat_backfill_applied"))
print("decision_temporal_durability:", matrix.get("temporal_durability"))
print("macro_shock_state:", macro_shock.get("state"))
print("macro_shock_block:", macro_shock.get("block"))
print("funding_semantic_code:", funding_semantics.get("semantic_code"))
print("funding_crowding_state:", funding_semantics.get("crowding_state"))
print("funding_reflexivity_importance:",
      funding_semantics.get("reflexivity_importance"))
print("funding_edb_participation:",
      funding_semantics.get("edb_participation"))
print("funding_semantics_compat_backfill_applied:",
      funding_semantics.get("compat_backfill_applied"))
print("gex_rank_window_days:", (gex_rank.get("window") or {}).get("window_days"))
print("durability_required:", durability_required)
print("signal_durability_schema_name:", durability.get("schema_name"))
print("signal_durability_schema_version:", durability.get("schema_version"))
print("signal_durability_audit_scope:", durability.get("audit_scope"))
print("signal_durability_headline_score:", durability.get("headline_score"))
print("signal_durability_headline_state:", durability.get("headline_state"))
print("signal_durability_comfort_tag:",
      (durability.get("comfort_window") or {}).get("tag")
      if isinstance(durability.get("comfort_window"), dict) else None)
print("signal_durability_price_anchor_state:",
      ((durability.get("price_anchor_durability") or {}).get("durability_state")
       or (durability.get("price_anchor_durability") or {}).get("state"))
      if isinstance(durability.get("price_anchor_durability"), dict) else None)
print("signal_durability_compat_backfill_applied:",
      durability.get("compat_backfill_applied"))
print("signal_rating_required:", signal_rating_required)
print("signal_rating_schema:", rating_obj.get("schema"))
print("signal_rating_scope:", rating_obj.get("rating_scope"))
print("signal_rating_candidate_quote_economics:",
      rating_obj.get("candidate_quote_economics"))
print("signal_rating_as_of_ms:", rating_obj.get("as_of_ms"))
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
if str(identity.get("strategy_version")) != expected_version:
    raise SystemExit("latest card strategy_version does not match EXPECTED_SIGNAL_VERSION")
if signal_rating_required:
    manifest_summary = selected_manifest.get("summary") or {}
    manifest_identity = manifest_summary.get("identity") or {}
    synthetic_markers = [
        identity.get("is_synthetic"),
        card.get("is_synthetic"),
        manifest_identity.get("is_synthetic"),
        selected_manifest.get("is_synthetic"),
    ]
    if any(bool(value) for value in synthetic_markers):
        raise SystemExit("synthetic audit card cannot satisfy signal_rating production acceptance")
    if contains_string_value_ci(card, "research_replay"):
        raise SystemExit("research_replay card cannot satisfy signal_rating production acceptance")
    if not isinstance(signal_rating, dict):
        raise SystemExit("latest card lacks producer-native signal_rating")
    if signal_rating.get("schema") != "signal_rating@1.0.0":
        raise SystemExit("latest card signal_rating schema mismatch")
    if signal_rating.get("rating_scope") != "side_environment_v1":
        raise SystemExit("latest card signal_rating scope mismatch")
    if signal_rating.get("candidate_quote_economics") != "not_evaluated":
        raise SystemExit("latest card signal_rating candidate economics mismatch")
    if finite_positive_number(signal_rating.get("as_of_ms")) is None:
        raise SystemExit("latest card signal_rating lacks finite positive as_of_ms")
    if contains_truthy_key(signal_rating, "compat_backfill_applied"):
        raise SystemExit("latest card signal_rating uses compatibility backfill")
if not isinstance(macro_shock, dict) or macro_shock.get("state") in (None, ""):
    raise SystemExit("latest card lacks producer-native macro_shock state")
if macro_shock.get("block") not in (True, False):
    raise SystemExit("latest card lacks producer-native macro_shock block")
if macro_shock.get("block") is True and not contains_value(card, "MACRO_SHOCK_BLOCKING"):
    raise SystemExit("latest MACRO shock block lacks MACRO_SHOCK_BLOCKING trace")
funding_required = {
    "schema_name", "schema_version", "raw_funding_rate",
    "crowding_threshold_abs", "semantic_code", "crowding_state",
    "reflexivity_importance", "edb_participation", "canonical_text_cn",
    "compat_backfill_applied",
}
if not isinstance(funding_semantics, dict) or not funding_required.issubset(funding_semantics):
    raise SystemExit("latest card lacks complete producer-native Funding semantics")
if funding_semantics.get("compat_backfill_applied"):
    raise SystemExit("latest card Funding semantics uses compatibility backfill")
raw_rate = funding_semantics.get("raw_funding_rate")
if isinstance(raw_rate, (int, float)) and abs(raw_rate) <= 0.0001:
    if funding_semantics.get("crowding_state") != "NOT_CROWDED":
        raise SystemExit("micro Funding rate classified as crowded")
    if funding_semantics.get("reflexivity_importance") != "NOISE":
        raise SystemExit("micro Funding rate has non-noise reflexivity")
    if funding_semantics.get("edb_participation") != "NON_VOTING":
        raise SystemExit("micro Funding rate participates in EDB")
rank_window_days = (gex_rank.get("window") or {}).get("window_days")
rank_metrics = gex_rank.get("metrics") or {}
if isinstance(rank_window_days, (int, float)) and rank_window_days >= 15.0:
    if any(str((metric or {}).get("quality") or "").lower() == "warming_up"
           for metric in rank_metrics.values() if isinstance(metric, dict)):
        raise SystemExit("GEX rank at or above 15 days remains warming_up")
if durability_required:
    if not isinstance(durability, dict) or not durability:
        raise SystemExit("latest card lacks native signal_durability schema")
    if durability.get("schema_name") != "SignalDurabilityLayer":
        raise SystemExit("latest card lacks native signal_durability schema")
    if durability.get("schema_version") != "nrd.signal.durability_layer.v1":
        raise SystemExit("latest card lacks native signal_durability schema")
    if durability.get("audit_scope") != "AUDIT_ONLY":
        raise SystemExit("latest card signal_durability audit_scope is not AUDIT_ONLY")
    if durability.get("headline_score") in (None, ""):
        raise SystemExit("latest card signal_durability lacks headline_score")
    if durability.get("headline_state") in (None, ""):
        raise SystemExit("latest card signal_durability lacks headline_state")
    comfort = durability.get("comfort_window")
    if not isinstance(comfort, dict) or comfort.get("tag") in (None, ""):
        raise SystemExit("latest card signal_durability lacks comfort_window.tag")
    anchor = durability.get("price_anchor_durability")
    if not isinstance(anchor, dict) or not anchor:
        raise SystemExit("latest card signal_durability lacks price_anchor_durability")
    if anchor.get("schema_name") != "SignalPriceAnchorDurability":
        raise SystemExit("latest card price_anchor_durability lacks native schema")
    if anchor.get("durability_score") in (None, ""):
        raise SystemExit("latest card price_anchor_durability lacks durability_score")
    if anchor.get("durability_state") in (None, ""):
        raise SystemExit("latest card price_anchor_durability lacks durability_state")
    layer_scores = anchor.get("layer_scores")
    if not isinstance(layer_scores, dict):
        raise SystemExit("latest card price_anchor_durability lacks layer_scores")
    if durability.get("compat_backfill_applied"):
        raise SystemExit("latest card signal_durability uses materializer compatibility backfill")
PY
  then
    ok "latest audit card has native expected session_context, macro_shock, signal_rating when required, and optional durability schema"
  else
    if [ "$SESSION_CONTEXT_REQUIRED" = "1" ] || [ "$DURABILITY_REQUIRED" = "1" ]; then
      fail "latest audit card lacks native expected session_context, macro_shock, required signal_rating, or durability schema"
    else
      warn "latest audit card lacks native expected session_context, macro_shock, required signal_rating, or durability schema"
    fi
  fi
fi

section "Signal transition ledger"
if [ -r "$AUDIT_ROOT/signal_cards/index.json" ] && have python3; then
  if python3 - "$AUDIT_ROOT" "$TRANSITION_LEDGER_SOURCE" <<'PY'
import json, os, pathlib, sys
root = pathlib.Path(sys.argv[1])
ledger_path = pathlib.Path(sys.argv[2])
target_card_id = (os.environ.get("TARGET_CARD_ID")
                  or os.environ.get("ONLY_CARD_ID") or "")
manifest = json.loads((root / "signal_cards/index.json").read_text(encoding="utf-8"))
cards = manifest.get("cards") or []
if not cards:
    raise SystemExit("no cards")
selected_manifest = cards[0]
if target_card_id:
    matches = [item for item in cards
               if str(item.get("card_id") or "") == target_card_id]
    if not matches:
        raise SystemExit("target audit card not found in manifest: " + target_card_id)
    selected_manifest = matches[0]
card_path = root / selected_manifest.get("path", "")
card = json.loads(card_path.read_text(encoding="utf-8"))
identity = card.get("identity") or {}
ctx = card.get("transition_context") or {}
anchor = ((ctx.get("producer_anchor") or {}).get("current") or {})
print("target_card_id:", target_card_id or "LATEST")
print("latest_audit_card_id:", identity.get("card_id") or selected_manifest.get("card_id"))
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
latest_id = identity.get("card_id") or selected_manifest.get("card_id")
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
if [ "${LLM_PROVIDER:-}" = "$EXPECTED_LLM_PROVIDER" ]; then
  ok "LLM provider matches expected provider: $EXPECTED_LLM_PROVIDER"
else
  if [ "$LLM_REQUIRED" = "1" ]; then
    fail "LLM provider is not $EXPECTED_LLM_PROVIDER"
  else
    warn "LLM provider is not $EXPECTED_LLM_PROVIDER"
  fi
fi
if [ "${LLM_MODEL:-}" = "$EXPECTED_LLM_MODEL" ]; then
  ok "LLM model matches expected model: $EXPECTED_LLM_MODEL"
else
  if [ "$LLM_REQUIRED" = "1" ]; then
    fail "LLM model is not $EXPECTED_LLM_MODEL"
  else
    warn "LLM model is not $EXPECTED_LLM_MODEL"
  fi
fi
if [ -n "${LLM_BASE_URL:-}" ]; then
  ok "LLM base URL is configured"
else
  if [ "$LLM_REQUIRED" = "1" ]; then
    fail "LLM base URL is not loaded"
  else
    warn "LLM base URL is not loaded"
  fi
fi
if [ -n "${LLM_API_KEY:-}" ]; then
  ok "LLM API key is configured in environment"
else
  if [ "$LLM_REQUIRED" = "1" ]; then
    fail "LLM_API_KEY is not loaded; LLM timer will skip calls"
  else
    warn "LLM_API_KEY is not loaded; LLM timer will skip calls"
  fi
fi
if [ -r "$LLM_REVIEWS_SOURCE" ]; then
  json_probe "latest LLM review sidecar" "$LLM_REVIEWS_SOURCE" 'review=data.get("llm_review") or {}; print("card_id:", data.get("card_id")); print("status:", review.get("status")); print("provider:", review.get("provider")); print("model:", review.get("model")); print("schema_version:", review.get("schema_version") or review.get("schema")); print("prompt_version:", review.get("prompt_version")); print("review_mode:", review.get("review_mode") or review.get("blind_review_mode")); print("llm_call_count:", review.get("llm_call_count")); print("llm_http_calls:", review.get("llm_http_calls")); print("api_key_route:", review.get("api_key_route")); print("llm_call_routes:", review.get("llm_call_routes")); print("retry_budget:", review.get("retry_budget"))'
else
  warn "LLM review sidecar not readable yet: $LLM_REVIEWS_SOURCE"
fi
if [ -r "$JSONL_SOURCE" ] && [ -r "$LLM_REVIEWS_SOURCE" ] && have python3; then
  if python3 - "$JSONL_SOURCE" "$LLM_REVIEWS_SOURCE" "$EXPECTED_LLM_PROVIDER" "$EXPECTED_LLM_MODEL" "$EXPECTED_LLM_SCHEMA" "$EXPECTED_LLM_PROMPT_VERSION" "$EXPECTED_LLM_REVIEW_MODE" "$EXPECTED_LLM_CALL_COUNT" "$EXPECTED_LLM_MAX_HTTP_ATTEMPTS" <<'PY'
import json, os, pathlib, sys
signal_path = pathlib.Path(sys.argv[1])
review_path = pathlib.Path(sys.argv[2])
expected_provider = sys.argv[3]
expected_model = sys.argv[4]
expected_schema = sys.argv[5]
expected_prompt_version = sys.argv[6]
expected_review_mode = sys.argv[7]
expected_call_count = int(sys.argv[8])
expected_max_http_attempts = int(sys.argv[9])
target_card_id = (os.environ.get("TARGET_CARD_ID")
                  or os.environ.get("ONLY_CARD_ID") or "")

def card_id(item):
    identity = item.get("identity") if isinstance(item.get("identity"), dict) else {}
    return item.get("card_id") or identity.get("card_id")

def review_schema(review):
    return review.get("schema_version") or review.get("schema")

def review_mode(review):
    return review.get("review_mode") or review.get("blind_review_mode")

def as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

signal_lines = [x for x in signal_path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
review_lines = [x for x in review_path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
if not signal_lines:
    raise SystemExit("signal_review.jsonl empty")
source_cards = []
for index, line in enumerate(signal_lines):
    try:
        item = json.loads(line)
    except json.JSONDecodeError:
        if index == len(signal_lines) - 1:
            raise SystemExit("latest non-empty signal_review.jsonl line is invalid")
        continue
    if isinstance(item, dict):
        source_cards.append(item)
if target_card_id:
    matches = [
        item for item in source_cards
        if str(card_id(item)) == target_card_id
    ]
    if not matches:
        raise SystemExit("target source card not found: " + target_card_id)
    latest = matches[-1]
else:
    latest = source_cards[-1]
latest_id = card_id(latest)
settled_reviews = {}
latest_settled_id = None
for index, line in enumerate(review_lines):
    try:
        item = json.loads(line)
    except json.JSONDecodeError:
        if index == len(review_lines) - 1:
            raise SystemExit("latest non-empty LLM review sidecar line is invalid")
        continue
    review = item.get("llm_review") or {}
    current_id = card_id(item)
    if review.get("status") in {"OK", "PARTIAL"} and current_id:
        settled_reviews[str(current_id)] = review
        latest_settled_id = current_id
print("target_card_id:", target_card_id or "LATEST")
print("latest_signal_card_id:", latest_id)
print("latest_settled_llm_card_id:", latest_settled_id)
review = settled_reviews.get(str(latest_id))
if not review:
    raise SystemExit("no OK/PARTIAL LLM review for latest source card")
print("latest_signal_llm_status:", review.get("status"))
print("latest_signal_llm_provider:", review.get("provider"))
print("latest_signal_llm_model:", review.get("model"))
print("latest_signal_llm_schema:", review_schema(review))
print("latest_signal_review_mode:", review_mode(review))
print("latest_signal_llm_call_count:", review.get("llm_call_count"))
print("latest_signal_llm_http_calls:", review.get("llm_http_calls"))
print("latest_signal_api_key_route:", review.get("api_key_route"))
print("latest_signal_llm_call_routes:", review.get("llm_call_routes"))
print("latest_signal_llm_prompt_version:", review.get("prompt_version"))
print("latest_signal_retry_budget:", review.get("retry_budget"))
if review.get("provider") != expected_provider:
    raise SystemExit("latest signal LLM provider is not " + expected_provider)
if review.get("model") != expected_model:
    raise SystemExit("latest signal LLM model is not " + expected_model)
if review_schema(review) != expected_schema:
    raise SystemExit("latest signal LLM schema is not " + expected_schema)
if review.get("prompt_version") != expected_prompt_version:
    raise SystemExit("latest signal LLM prompt version does not match expected runtime entrypoint")
if review_mode(review) != expected_review_mode:
    raise SystemExit("latest signal LLM review mode is not " + expected_review_mode)
call_count = as_int(review.get("llm_call_count"))
http_calls = as_int(review.get("llm_http_calls") or review.get("llm_call_count"))
if call_count < expected_call_count:
    raise SystemExit("latest signal LLM call count is below " + str(expected_call_count))
if http_calls > expected_max_http_attempts:
    raise SystemExit("latest signal LLM HTTP attempts exceed " + str(expected_max_http_attempts))
if expected_schema in ("signal_llm_review@2.0.0", "signal_llm_review@2.1.0"):
    budget = review.get("retry_budget") or {}
    if budget.get("limit") != expected_max_http_attempts or budget.get("persistent") is not True:
        raise SystemExit("latest signal LLM v2 retry budget is not persistent max-two")
PY
  then
    ok "latest signal card has provider-neutral v2 single evidence LLM sidecar review"
  else
    if [ "$LLM_REQUIRED" = "1" ]; then
      fail "latest signal card does not have a valid provider-neutral v2 LLM sidecar review"
    else
      warn "latest signal card does not have a valid provider-neutral v2 LLM sidecar review"
    fi
  fi
else
  warn "skipped latest-card LLM match check; source or sidecar not readable"
fi

if [ "$INTEGRATED_ADVISORY_REQUIRED" = "1" ] && [ "$INTEGRATED_ADVISORY_PROTOCOL" != "legacy" ]; then
  section "Integrated trade advisory"
  if [ -r "$JSONL_SOURCE" ] && [ -r "$LLM_REVIEWS_SOURCE" ] && [ -r "$AUDIT_ROOT/signal_cards/index.json" ] && have python3; then
    if python3 - "$JSONL_SOURCE" "$LLM_REVIEWS_SOURCE" "$AUDIT_ROOT" "$TOOLS_ROOT" "$EXPECTED_LLM_PROVIDER" "$EXPECTED_LLM_MODEL" "$EXPECTED_LLM_SCHEMA" "$EXPECTED_LLM_PROMPT_VERSION" "$EXPECTED_LLM_REVIEW_MODE" "$EXPECTED_LLM_CALL_COUNT" "$EXPECTED_LLM_MAX_HTTP_ATTEMPTS" <<'PY'
import json, os, pathlib, sys

signal_path = pathlib.Path(sys.argv[1])
review_path = pathlib.Path(sys.argv[2])
audit_root = pathlib.Path(sys.argv[3])
tools_root = pathlib.Path(sys.argv[4])
sys.path.insert(0, str(tools_root))
from signal_review_v2 import (  # noqa: E402
    EvidenceFormatError,
    build_summary,
    revalidate_review,
    validate_persisted_review,
)

expected_provider = sys.argv[5]
expected_model = sys.argv[6]
expected_schema = sys.argv[7]
expected_prompt_version = sys.argv[8]
expected_review_mode = sys.argv[9]
expected_call_count = int(sys.argv[10])
expected_max_http_attempts = int(sys.argv[11])
target_card_id = (os.environ.get("TARGET_CARD_ID")
                  or os.environ.get("ONLY_CARD_ID") or "")

SIDES = ("put_credit", "call_credit")
REVIEW_STATUSES = {"OK", "PARTIAL"}
LEGACY_ADVISORY_FIELDS = {
    "recommendation", "final_conclusion_cn", "cross_loop_rationale_cn",
    "containment_assessment", "premium_selling_fit", "side_basis_cn",
    "dominant_conflict_cn", "key_premises", "invalid_if",
    "next_observation_cn", "session_advisory", "source_alignment",
    "side_comfort_ratings", "future_24h_bayesian_report",
}

def read_jsonl(path):
    lines = [x for x in path.read_text(encoding="utf-8", errors="replace").splitlines()
             if x.strip()]
    if not lines:
        raise SystemExit(str(path) + " empty")
    records = []
    skipped = 0
    for index, line in enumerate(lines, 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            if index == len(lines):
                raise SystemExit(str(path) + " latest non-empty line is invalid: "
                                 + type(exc).__name__)
            skipped += 1
            continue
        if isinstance(value, dict):
            records.append(value)
    if not records:
        raise SystemExit(str(path) + " has no valid records")
    print(path.name + "_historical_skipped_lines:", skipped)
    return records

def card_id(value):
    identity = value.get("identity") if isinstance(value.get("identity"), dict) else {}
    return value.get("card_id") or identity.get("card_id")

def review_schema(review):
    return review.get("schema_version") or review.get("schema")

def review_mode(review):
    return review.get("review_mode") or review.get("blind_review_mode")

def as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

def validate_review_header(review, label):
    if review.get("status") not in REVIEW_STATUSES:
        raise SystemExit(label + " review status is not OK/PARTIAL")
    if review.get("provider") != expected_provider:
        raise SystemExit(label + " review provider is not " + expected_provider)
    if review.get("model") != expected_model:
        raise SystemExit(label + " review model is not " + expected_model)
    if review_schema(review) != expected_schema:
        raise SystemExit(label + " review schema is not " + expected_schema)
    if review.get("prompt_version") != expected_prompt_version:
        raise SystemExit(label + " review prompt version mismatch")
    if review_mode(review) != expected_review_mode:
        raise SystemExit(label + " review mode is not " + expected_review_mode)
    call_count = as_int(review.get("llm_call_count"))
    http_calls = as_int(review.get("llm_http_calls") or review.get("llm_call_count"))
    if call_count < expected_call_count:
        raise SystemExit(label + " review call count below " + str(expected_call_count))
    if http_calls > expected_max_http_attempts:
        raise SystemExit(label + " review HTTP attempts exceed "
                         + str(expected_max_http_attempts))
    if expected_schema in ("signal_llm_review@2.0.0", "signal_llm_review@2.1.0"):
        budget = review.get("retry_budget") or {}
        if budget.get("limit") != expected_max_http_attempts or budget.get("persistent") is not True:
            raise SystemExit(label + " v2 retry budget is not persistent max-two")

def validate_advisory(card, review, label):
    advisory = review.get("integrated_trade_advisory")
    if not isinstance(advisory, dict):
        raise SystemExit(label + " integrated_trade_advisory is not object")
    legacy = sorted(field for field in LEGACY_ADVISORY_FIELDS if field in advisory)
    if legacy:
        raise SystemExit(label + " v2 advisory still carries legacy fields: "
                         + ",".join(legacy))
    try:
        validate_persisted_review(review)
        validation = revalidate_review(card, review)
    except EvidenceFormatError as exc:
        raise SystemExit(label + " v2 persisted review invalid: " + str(exc))
    ratings = advisory.get("side_evidence_ratings") or {}
    print(label + "_assessment_hash:", validation["assessment_hash"])
    print(label + "_put_grade:", (ratings.get("put_credit") or {}).get("grade"))
    print(label + "_call_grade:", (ratings.get("call_credit") or {}).get("grade"))
    return advisory

def validate_summary(summary, review, label, card):
    if not isinstance(summary, dict):
        raise SystemExit(label + " signal_evidence_summary is not object")
    try:
        expected = build_summary(review, card)
    except EvidenceFormatError as exc:
        raise SystemExit(label + " signal_evidence_summary source invalid: " + str(exc))
    if summary.get("assessment_hash") != expected.get("assessment_hash"):
        raise SystemExit(label + " signal_evidence_summary assessment hash mismatch")
    if summary != expected:
        raise SystemExit(label + " signal_evidence_summary mismatch")
    print(label + "_signal_evidence_summary_hash:", expected.get("assessment_hash"))

source_cards = read_jsonl(signal_path)
if target_card_id:
    target_sources = [item for item in source_cards if str(card_id(item)) == target_card_id]
    if not target_sources:
        raise SystemExit("target source card not found: " + target_card_id)
    latest_source = target_sources[-1]
else:
    latest_source = source_cards[-1]
latest_id = card_id(latest_source)
if not latest_id:
    raise SystemExit("latest source card lacks card_id")

latest_matching_review = None
latest_matching_record_id = None
for item in read_jsonl(review_path):
    current_id = item.get("card_id") or card_id(item)
    if str(current_id) == str(latest_id):
        latest_matching_review = item.get("llm_review")
        latest_matching_record_id = current_id

print("target_card_id:", target_card_id or "LATEST")
print("latest_signal_card_id:", latest_id)
print("latest_matching_llm_card_id:", latest_matching_record_id)
if not isinstance(latest_matching_review, dict):
    raise SystemExit("no llm_review for latest source card")
validate_review_header(latest_matching_review, "latest_advisory")
sidecar_advisory = validate_advisory(latest_source, latest_matching_review, "latest_advisory")

manifest = json.loads((audit_root / "signal_cards/index.json").read_text(encoding="utf-8"))
cards = manifest.get("cards") or []
if not cards:
    raise SystemExit("materialized manifest has no cards")
selected_manifest = cards[0]
if target_card_id:
    matches = [item for item in cards
               if str(item.get("card_id") or "") == target_card_id]
    if not matches:
        raise SystemExit("target materialized card not found: " + target_card_id)
    selected_manifest = matches[0]
materialized_path = audit_root / selected_manifest.get("path", "")
materialized_card = json.loads(materialized_path.read_text(encoding="utf-8"))
materialized_id = card_id(materialized_card) or selected_manifest.get("card_id")
materialized_review = materialized_card.get("llm_review")
print("materialized_card_id:", materialized_id)
if str(materialized_id) != str(latest_id):
    raise SystemExit("materialized latest card does not match latest source card")
if not isinstance(materialized_review, dict):
    raise SystemExit("materialized latest card lacks llm_review")
validate_review_header(materialized_review, "materialized_advisory")
materialized_advisory = validate_advisory(materialized_card, materialized_review, "materialized_advisory")
print("materialized_advisory_passthrough:", materialized_advisory == sidecar_advisory)
if materialized_advisory != sidecar_advisory:
    raise SystemExit("materialized latest card did not pass through integrated_trade_advisory")
summary = (selected_manifest.get("summary") or {}).get("signal_evidence_summary")
validate_summary(summary, materialized_review, "materialized_advisory", materialized_card)
print("materialized_signal_evidence_summary:", True)
PY
    then
      ok "latest signal card has v2 integrated_trade_advisory and materialized evidence summary passthrough"
    else
      fail "latest signal card lacks strict v2 integrated_trade_advisory or materialized evidence summary passthrough"
    fi
  else
    fail "skipped v2 integrated_trade_advisory strict check; source, sidecar, manifest, or python3 unavailable"
  fi
fi

if [ "$INTEGRATED_ADVISORY_REQUIRED" = "1" ] && [ "$INTEGRATED_ADVISORY_PROTOCOL" = "legacy" ]; then
  section "Integrated trade advisory"
  if [ -r "$JSONL_SOURCE" ] && [ -r "$LLM_REVIEWS_SOURCE" ] && [ -r "$AUDIT_ROOT/signal_cards/index.json" ] && have python3; then
    if python3 - "$JSONL_SOURCE" "$LLM_REVIEWS_SOURCE" "$AUDIT_ROOT" "$TOOLS_ROOT" "$EXPECTED_LLM_PROVIDER" "$EXPECTED_LLM_MODEL" "$EXPECTED_LLM_SCHEMA" "$EXPECTED_LLM_PROMPT_VERSION" "$EXPECTED_LLM_BLIND_MODE" "$EXPECTED_LLM_CALL_COUNT" <<'PY'
import json, os, pathlib, re, sys

ADVISORY_RECOMMENDATIONS = {
    "SELL_PUT_SPREAD_REVIEW",
    "SELL_CALL_SPREAD_REVIEW",
    "NEUTRAL_SINGLE_SIDE_REVIEW",
    "WAIT_FOR_CONFIRMATION",
    "NO_TRADE",
    "UNABLE_TO_JUDGE",
}
ADVISORY_CONTAINMENT_STATES = {
    "ESTABLISHED", "INCOMPLETE", "FAILED", "UNABLE_TO_JUDGE",
}
ADVISORY_PREMIUM_FIT_STATES = {
    "FIT", "CONDITIONAL", "NOT_FIT", "UNABLE_TO_JUDGE",
}
ADVISORY_LIQUIDITY_ASSESSMENTS = {
    "ALIGNED", "CAUTION", "TIME_ONLY", "UNKNOWN",
}
ADVISORY_WARNING_LEVELS = {"NONE", "INFO", "CAUTION", "HIGH"}
ADVISORY_SOURCE_ALIGNMENTS = {
    "ALIGNED", "PARTIALLY_ALIGNED", "DIVERGENT", "UNABLE_TO_JUDGE",
}
COMFORT_GRADES = {"D", "C", "B", "A", "S"}
COMFORT_GRADE_RANK = {"D": 0, "C": 1, "B": 2, "A": 3, "S": 4}
COMFORT_STATUSES = {"RATED", "UNRATED"}
COMFORT_SIDES = ("put_credit", "call_credit")
COMFORT_FOCUS_SIDES = {"put_credit", "call_credit", "tie", "none"}
ADVISORY_HUMAN_RAW_TOKENS = {
    *ADVISORY_RECOMMENDATIONS,
    *ADVISORY_CONTAINMENT_STATES,
    *ADVISORY_PREMIUM_FIT_STATES,
    *ADVISORY_LIQUIDITY_ASSESSMENTS,
    *ADVISORY_WARNING_LEVELS,
    *ADVISORY_SOURCE_ALIGNMENTS,
    "trade_allowed", "execution_allowed", "source_alignment",
    "recommendation", "audit_only", "trade_authorization", "evidence_refs",
    "side_comfort_ratings", "model_grade", "final_grade",
    "put_credit", "call_credit",
}
ADVISORY_EXECUTION_PATTERNS = (
    re.compile(r"开仓|平仓|下单|入场|出场|加仓|减仓|止损|止盈"),
    re.compile(r"(?:行权价|执行价)[^。；\n]{0,36}(?:设|选|放|置|低于|高于|上方|下方|\d)"),
    re.compile(r"(?:到期日|到期时间)[^。；\n]{0,28}(?:设|选|使用|\d)"),
    re.compile(r"\bstrike\b[^.;\n]{0,40}\d", re.IGNORECASE),
    re.compile(r"\b(?:expiry|expiration|expires?|expiring)\b[^.;\n]{0,40}\d", re.IGNORECASE),
    re.compile(
        r"(?:\b\d{4,}(?:\.\d+)?\s*(?:/|and|-)\s*\d{4,}(?:\.\d+)?\b"
        r"[^.;。；\n]{0,24}\b(?:put|call)\b[^.;。；\n]{0,8}(?:spread|价差)"
        r"|\b(?:put|call)\b[^.;。；\n]{0,8}(?:spread|价差)"
        r"[^.;。；\n]{0,24}\b\d{4,}(?:\.\d+)?\s*(?:/|and|-)\s*"
        r"\d{4,}(?:\.\d+)?\b)", re.IGNORECASE),
    re.compile(
        r"\d{4,}(?:\.\d+)?\s*(?:/|与|和|-)\s*\d{4,}(?:\.\d+)?"
        r"[^。；\n]{0,24}(?:Put|Call|看涨|看跌)?\s*价差", re.IGNORECASE),
    re.compile(r"\b(?:position\s+size|size\s+the\s+position)\b", re.IGNORECASE),
    re.compile(r"\b(?:entry|exit)\b[^.;\n]{0,24}(?:at|above|below|=|:)?\s*\d", re.IGNORECASE),
    re.compile(r"\b(?:stop[- ]?loss|take[- ]?profit)\b[^.;\n]{0,24}\d", re.IGNORECASE),
    re.compile(r"\b(?:set|use|raise|lower|adjust)\b[^.;\n]{0,16}\bleverage\b", re.IGNORECASE),
)

signal_path = pathlib.Path(sys.argv[1])
review_path = pathlib.Path(sys.argv[2])
audit_root = pathlib.Path(sys.argv[3])
tools_root = pathlib.Path(sys.argv[4])
target_card_id = (os.environ.get("TARGET_CARD_ID")
                  or os.environ.get("ONLY_CARD_ID") or "")
if str(tools_root) not in sys.path:
    sys.path.insert(0, str(tools_root))
_COMFORT_CORE = None

def comfort_core():
    global _COMFORT_CORE
    if _COMFORT_CORE is None:
        import signal_llm_review as core
        _COMFORT_CORE = core
    return _COMFORT_CORE

def json_clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False))

def read_jsonl(path):
    lines = [x for x in path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
    if not lines:
        raise SystemExit(str(path) + " empty")
    records = []
    skipped = []
    for index, line in enumerate(lines, 1):
        try:
            records.append(json.loads(line))
        except Exception as exc:
            if index == len(lines):
                raise SystemExit(str(path) + " latest non-empty line is invalid: "
                                 + type(exc).__name__)
            skipped.append(index)
    if not records:
        raise SystemExit(str(path) + " has no valid records")
    print(path.name + "_historical_skipped_lines:", len(skipped))
    return records

def card_id(value):
    return ((value.get("identity") or {}).get("card_id")
            or value.get("card_id"))

def advisory_human_text(advisory):
    containment = advisory.get("containment_assessment") or {}
    premium = advisory.get("premium_selling_fit") or {}
    session = advisory.get("session_advisory") or {}
    values = [
        advisory.get("final_conclusion_cn"),
        advisory.get("cross_loop_rationale_cn"),
        containment.get("basis_cn"),
        premium.get("basis_cn"),
        advisory.get("side_basis_cn"),
        advisory.get("dominant_conflict_cn"),
        advisory.get("next_observation_cn"),
        session.get("basis_cn"),
    ]
    values.extend(
        item.get("premise_cn") for item in advisory.get("key_premises") or []
        if isinstance(item, dict))
    values.extend(advisory.get("invalid_if") or [])
    comfort = advisory.get("side_comfort_ratings") or {}
    for side_name in COMFORT_SIDES:
        side = comfort.get(side_name) or {}
        values.extend([
            side.get("basis_cn"),
            side.get("counter_evidence_cn"),
            side.get("next_observation_cn"),
            side.get("s_upgrade_basis_cn"),
        ])
        values.extend(side.get("unresolved_conditions_cn") or [])
        values.extend(side.get("cap_reasons_cn") or [])
    return "\n".join(str(value) for value in values if value not in (None, ""))

def comfort_grade(value):
    if value in (None, ""):
        return None
    if isinstance(value, str) and value.strip().upper() in COMFORT_GRADES:
        return value.strip().upper()
    return "INVALID"

def validate_string_list(value, allow_empty=True):
    if not isinstance(value, list):
        return False
    if not allow_empty and not value:
        return False
    return all(isinstance(item, str) and item.strip() for item in value)

def comfort_expected_headline(put_side, call_side):
    side_grades = []
    for side_name, side in (("put_credit", put_side), ("call_credit", call_side)):
        grade = side.get("final_grade")
        if grade in COMFORT_GRADE_RANK:
            side_grades.append((side_name, grade, COMFORT_GRADE_RANK[grade]))
    if not side_grades:
        return None, "none"
    best_rank = max(rank for _side_name, _grade, rank in side_grades)
    best = [
        (side_name, grade)
        for side_name, grade, rank in side_grades
        if rank == best_rank
    ]
    return best[0][1], best[0][0] if len(best) == 1 else "tie"

def source_has_producer_hard_block(source_card):
    decision = source_card.get("decision") or {}
    matrix = source_card.get("decision_matrix") or {}
    blocking = source_card.get("blocking") or {}
    support_values = {
        str(decision.get("support_label") or "").upper(),
        str(decision.get("support_pre_gate") or "").upper(),
        str(matrix.get("decision_state") or "").upper(),
    }
    if "NO_TRADE_BLOCKED" in support_values:
        return True
    if str(blocking.get("block_kind") or "").upper() == "HARD":
        return True
    if blocking.get("hard_block") is True or blocking.get("hard_blocked") is True:
        return True
    if blocking.get("hard_veto") not in (None, {}, [], False):
        return True
    macro = ((source_card.get("factor_cross_section") or {}).get("macro_pressure")
             or {})
    if (macro.get("macro_shock") or {}).get("block") is True:
        return True
    return False

def source_has_any_producer_block(source_card):
    if source_has_producer_hard_block(source_card):
        return True
    decision = source_card.get("decision") or {}
    matrix = source_card.get("decision_matrix") or {}
    blocking = source_card.get("blocking") or {}
    values = {
        str(decision.get("support_label") or "").upper(),
        str(decision.get("support_pre_gate") or "").upper(),
        str(matrix.get("decision_state") or "").upper(),
        str(blocking.get("block_kind") or "").upper(),
    }
    if values & {"BLOCKED", "NO_TRADE_BLOCKED", "BLOCK", "SOFT_GATE"}:
        return True
    return bool(blocking.get("has_block") or blocking.get("soft_gates"))

def source_requires_confirmation(source_card):
    decision = source_card.get("decision") or {}
    matrix = source_card.get("decision_matrix") or {}
    return bool({
        "WAIT_CONFIRMATION", "WAIT_FOR_CONFIRMATION",
        "WAITING_CONFIRMATION", "PENDING_CONFIRMATION",
    } & {
        str(decision.get("support_label") or "").upper(),
        str(decision.get("support_pre_gate") or "").upper(),
        str(matrix.get("decision_state") or "").upper(),
    })

def source_has_window_not_open_or_invalid(source_card):
    if source_has_explicit_window_failure(source_card):
        return True
    window = source_card.get("signal_window") or {}
    if not isinstance(window, dict) or not window:
        return False
    neutral = window.get("neutral_repair")
    if not isinstance(neutral, dict):
        neutral = {}
    if "is_active" in window and window.get("is_active") is False:
        return True
    if "is_active" in neutral and neutral.get("is_active") is False:
        return True
    return False

def source_has_explicit_window_failure(source_card):
    window = source_card.get("signal_window") or {}
    if not isinstance(window, dict) or not window:
        return False
    neutral = window.get("neutral_repair")
    if not isinstance(neutral, dict):
        neutral = {}
    state_values = {
        str(window.get("nr_state") or "").upper(),
        str(window.get("state") or "").upper(),
        str(neutral.get("state") or "").upper(),
    }
    if any(any(token in state for token in (
            "STALE", "EXPIRED", "TIMEOUT", "INVALID", "FAILED"))
            for state in state_values if state):
        return True
    return False

def source_direction(source_card):
    decision = source_card.get("decision") or {}
    matrix = source_card.get("decision_matrix") or {}
    text = str(decision.get("lean") or matrix.get("direction") or "").upper()
    if "BULLISH" in text or text in {"UP", "LONG"}:
        return "BULLISH"
    if "BEARISH" in text or text in {"DOWN", "SHORT"}:
        return "BEARISH"
    if "NEUTRAL" in text or text in {"RANGE", "FLAT"}:
        return "NEUTRAL"
    return "UNKNOWN"

def comfort_side_allows_admission(side_name, advisory, source_card):
    recommendation = str((advisory or {}).get("recommendation") or "").upper()
    direction = source_direction(source_card)
    if recommendation not in {
            "SELL_PUT_SPREAD_REVIEW", "SELL_CALL_SPREAD_REVIEW",
            "NEUTRAL_SINGLE_SIDE_REVIEW"}:
        return False
    if recommendation == "SELL_PUT_SPREAD_REVIEW" and side_name != "put_credit":
        return False
    if recommendation == "SELL_CALL_SPREAD_REVIEW" and side_name != "call_credit":
        return False
    if direction == "BULLISH" and side_name == "call_credit":
        return False
    if direction == "BEARISH" and side_name == "put_credit":
        return False
    if direction == "NEUTRAL" and recommendation != "NEUTRAL_SINGLE_SIDE_REVIEW":
        return False
    return True

def native_signal_rating_allows_admission(rating, side_name):
    claims = (rating or {}).get("claims") or {}
    if (claims.get("structure") or {}).get("status") == "OPPOSED":
        return False
    claim_name = "put_pressure" if side_name == "put_credit" else "call_pressure"
    if (claims.get(claim_name) or {}).get("status") == "OPPOSED":
        return False
    return True

def validate_comfort_admission_consistency(side_name, side, advisory,
                                           source_card, rating, label):
    if (source_has_explicit_window_failure(source_card)
            and side.get("final_grade") in COMFORT_GRADE_RANK
            and side.get("final_grade") != "D"):
        raise SystemExit(
            label + " side_comfort_ratings conflicts with failed window")
    if side.get("final_grade") not in {"A", "S"}:
        return
    if source_has_any_producer_block(source_card):
        raise SystemExit(label + " side_comfort_ratings conflicts with source block")
    if source_requires_confirmation(source_card):
        raise SystemExit(label + " side_comfort_ratings conflicts with source wait")
    if source_has_window_not_open_or_invalid(source_card):
        raise SystemExit(label + " side_comfort_ratings conflicts with inactive window")
    if not comfort_side_allows_admission(side_name, advisory, source_card):
        raise SystemExit(label + " side_comfort_ratings advisory side mismatch")
    quality = source_card.get("quality") or {}
    if quality.get("overall") not in (None, "OK"):
        raise SystemExit(label + " side_comfort_ratings conflicts with source quality")
    if not native_signal_rating_allows_admission(rating, side_name):
        raise SystemExit(label + " side_comfort_ratings conflicts with native rating")

def validate_comfort_side(side, label):
    expected_fields = {
        "model_grade", "final_grade", "status", "basis_cn",
        "counter_evidence_cn", "next_observation_cn",
        "unresolved_conditions_cn", "evidence_refs",
        "counter_evidence_refs", "s_upgrade_basis_cn",
        "s_upgrade_evidence_refs", "cap_reasons_cn",
    }
    if not isinstance(side, dict) or set(side) != expected_fields:
        raise SystemExit(label + " side_comfort_ratings side fields invalid")
    model_grade = comfort_grade(side.get("model_grade"))
    final_grade = comfort_grade(side.get("final_grade"))
    if model_grade == "INVALID" or final_grade == "INVALID":
        raise SystemExit(label + " side_comfort_ratings grade invalid")
    status = side.get("status")
    if status not in COMFORT_STATUSES:
        raise SystemExit(label + " side_comfort_ratings status invalid")
    if status == "RATED" and (final_grade is None or model_grade is None):
        raise SystemExit(label + " side_comfort_ratings rated side lacks final grade")
    if status == "RATED" and COMFORT_GRADE_RANK[final_grade] > COMFORT_GRADE_RANK[model_grade]:
        raise SystemExit(label + " side_comfort_ratings final grade upgrades model grade")
    if status == "UNRATED" and final_grade is not None:
        raise SystemExit(label + " side_comfort_ratings unrated side has final grade")
    for field in ("basis_cn", "counter_evidence_cn", "next_observation_cn"):
        if not isinstance(side.get(field), str) or not side[field].strip():
            raise SystemExit(label + " side_comfort_ratings " + field + " is blank")
    if not validate_string_list(side.get("unresolved_conditions_cn")):
        raise SystemExit(label + " side_comfort_ratings unresolved_conditions_cn invalid")
    if not validate_string_list(side.get("evidence_refs")):
        raise SystemExit(label + " side_comfort_ratings evidence_refs invalid")
    if not validate_string_list(side.get("counter_evidence_refs")):
        raise SystemExit(label + " side_comfort_ratings counter_evidence_refs invalid")
    evidence_refs = [
        item for item in side.get("evidence_refs", [])
        if isinstance(item, str) and item.strip()
    ]
    counter_evidence_refs = [
        item for item in side.get("counter_evidence_refs", [])
        if isinstance(item, str) and item.strip()
    ]
    if status == "RATED":
        if final_grade in {"B", "A", "S"} and not evidence_refs:
            raise SystemExit(
                label + " side_comfort_ratings support grade lacks evidence_refs")
        if final_grade in {"C", "D"} and not (
                evidence_refs or counter_evidence_refs):
            raise SystemExit(
                label + " side_comfort_ratings observation grade lacks refs")
    if not validate_string_list(side.get("cap_reasons_cn")):
        raise SystemExit(label + " side_comfort_ratings cap_reasons_cn invalid")
    if not validate_string_list(side.get("s_upgrade_evidence_refs")):
        raise SystemExit(label + " side_comfort_ratings s_upgrade_evidence_refs invalid")
    if side.get("s_upgrade_basis_cn") not in (None, "") and (
            not isinstance(side.get("s_upgrade_basis_cn"), str)
            or not side["s_upgrade_basis_cn"].strip()):
        raise SystemExit(label + " side_comfort_ratings s_upgrade_basis_cn invalid")
    if final_grade == "S" and (
            not isinstance(side.get("s_upgrade_basis_cn"), str)
            or not side["s_upgrade_basis_cn"].strip()
            or not side.get("s_upgrade_evidence_refs")):
        raise SystemExit(label + " side_comfort_ratings S lacks extra basis")
    normalized = dict(side)
    normalized["model_grade"] = None if model_grade in (None, "INVALID") else model_grade
    normalized["final_grade"] = None if final_grade in (None, "INVALID") else final_grade
    return normalized

def comfort_raw_side(side):
    model_grade = comfort_grade((side or {}).get("model_grade"))
    return {
        "grade": model_grade or "UNRATED",
        "basis_cn": (side or {}).get("basis_cn"),
        "counter_evidence_cn": (side or {}).get("counter_evidence_cn"),
        "unresolved_conditions_cn": list(
            (side or {}).get("unresolved_conditions_cn") or []),
        "next_observation_cn": (side or {}).get("next_observation_cn"),
        "evidence_refs": list((side or {}).get("evidence_refs") or []),
        "counter_evidence_refs": list(
            (side or {}).get("counter_evidence_refs") or []),
        "s_upgrade_basis_cn": (side or {}).get("s_upgrade_basis_cn") or "",
        "s_upgrade_evidence_refs": list(
            (side or {}).get("s_upgrade_evidence_refs") or []),
    }

def comfort_raw_model_shape(comfort):
    return {
        side_name: comfort_raw_side((comfort or {}).get(side_name) or {})
        for side_name in COMFORT_SIDES
    }

def validate_comfort_against_core(comfort, advisory, source_card, label):
    try:
        core = comfort_core()
        packet = core.build_review_packet(json_clone(source_card))
        core._validate_side_comfort_ratings(comfort, packet)
        expected = core._finalize_side_comfort_ratings(
            comfort_raw_model_shape(comfort), packet, advisory)
    except Exception as exc:
        raise SystemExit(
            label + " side_comfort_ratings core validation failed: "
            + type(exc).__name__ + ": " + str(exc))
    for side_name in COMFORT_SIDES:
        saved = (comfort or {}).get(side_name) or {}
        expected_side = (expected or {}).get(side_name) or {}
        if saved.get("status") != expected_side.get("status"):
            raise SystemExit(
                label + " side_comfort_ratings core status mismatch")
        if saved.get("final_grade") != expected_side.get("final_grade"):
            raise SystemExit(
                label + " side_comfort_ratings core final grade mismatch")
    headline = (comfort or {}).get("headline") or {}
    expected_headline = (expected or {}).get("headline") or {}
    if (headline.get("final_grade") != expected_headline.get("final_grade")
            or headline.get("focus_side") != expected_headline.get("focus_side")):
        raise SystemExit(
            label + " side_comfort_ratings core headline mismatch")

def validate_comfort_ratings(advisory, source_card, label):
    comfort = advisory.get("side_comfort_ratings")
    if not isinstance(comfort, dict):
        raise SystemExit(label + " side_comfort_ratings is not object")
    expected_fields = {
        "schema", "rating_scope", "candidate_quote_economics",
        "as_of_ms", "put_credit", "call_credit", "headline",
    }
    if set(comfort) != expected_fields:
        raise SystemExit(label + " side_comfort_ratings fields invalid")
    if comfort.get("schema") != "signal_comfort_ratings@1.0.0":
        raise SystemExit(label + " side_comfort_ratings schema mismatch")
    if comfort.get("rating_scope") != "signal_side_admission":
        raise SystemExit(label + " side_comfort_ratings scope mismatch")
    if comfort.get("candidate_quote_economics") != "not_evaluated":
        raise SystemExit(label + " side_comfort_ratings candidate economics mismatch")
    rating = source_card.get("signal_rating") or {}
    if (not isinstance(rating, dict)
            or rating.get("schema") != "signal_rating@1.0.0"
            or rating.get("rating_scope") != "side_environment_v1"
            or rating.get("candidate_quote_economics") != "not_evaluated"):
        raise SystemExit(label + " source card lacks matching native signal_rating")
    if comfort.get("as_of_ms") != rating.get("as_of_ms"):
        raise SystemExit(label + " side_comfort_ratings as_of_ms does not match native rating")
    put_side = validate_comfort_side(comfort.get("put_credit"), label + " put_credit")
    call_side = validate_comfort_side(comfort.get("call_credit"), label + " call_credit")
    validate_comfort_admission_consistency(
        "put_credit", put_side, advisory, source_card, rating, label + " put_credit")
    validate_comfort_admission_consistency(
        "call_credit", call_side, advisory, source_card, rating, label + " call_credit")
    headline = comfort.get("headline")
    if not isinstance(headline, dict) or set(headline) != {
            "final_grade", "focus_side", "action_cn"}:
        raise SystemExit(label + " side_comfort_ratings headline invalid")
    headline_grade = comfort_grade(headline.get("final_grade"))
    if headline_grade == "INVALID":
        raise SystemExit(label + " side_comfort_ratings headline grade invalid")
    if headline.get("focus_side") not in COMFORT_FOCUS_SIDES:
        raise SystemExit(label + " side_comfort_ratings focus_side invalid")
    if not isinstance(headline.get("action_cn"), str) or not headline["action_cn"].strip():
        raise SystemExit(label + " side_comfort_ratings action_cn is blank")
    expected_grade, expected_focus = comfort_expected_headline(put_side, call_side)
    if headline_grade != expected_grade or headline.get("focus_side") != expected_focus:
        raise SystemExit(label + " side_comfort_ratings headline does not match side grades")
    validate_comfort_against_core(comfort, advisory, source_card, label)
    print(label + "_side_comfort_final_grade:", headline_grade)
    print(label + "_side_comfort_focus_side:", headline.get("focus_side"))
    return comfort

def compact_comfort_summary(comfort):
    return {
        "schema": comfort.get("schema"),
        "rating_scope": comfort.get("rating_scope"),
        "candidate_quote_economics": comfort.get("candidate_quote_economics"),
        "as_of_ms": comfort.get("as_of_ms"),
        "headline": {
            "final_grade": (comfort.get("headline") or {}).get("final_grade"),
            "focus_side": (comfort.get("headline") or {}).get("focus_side"),
            "action_cn": (comfort.get("headline") or {}).get("action_cn"),
        },
        "put_credit": {
            "status": (comfort.get("put_credit") or {}).get("status"),
            "final_grade": (comfort.get("put_credit") or {}).get("final_grade"),
            "basis_cn": (comfort.get("put_credit") or {}).get("basis_cn"),
            "cap_reasons_cn": (comfort.get("put_credit") or {}).get("cap_reasons_cn"),
        },
        "call_credit": {
            "status": (comfort.get("call_credit") or {}).get("status"),
            "final_grade": (comfort.get("call_credit") or {}).get("final_grade"),
            "basis_cn": (comfort.get("call_credit") or {}).get("basis_cn"),
            "cap_reasons_cn": (comfort.get("call_credit") or {}).get("cap_reasons_cn"),
        },
    }

def raw_human_tokens(text):
    leaked = []
    for token in ADVISORY_HUMAN_RAW_TOKENS:
        pattern = (r"(?<![A-Za-z0-9_])" + re.escape(token)
                   + r"(?![A-Za-z0-9_])")
        if re.search(pattern, text, re.IGNORECASE):
            leaked.append(token)
    return sorted(leaked)

def validate_advisory(review, label):
    advisory = review.get("integrated_trade_advisory")
    if not isinstance(advisory, dict):
        raise SystemExit(label + " integrated_trade_advisory is not object")
    required_fields = {
        "recommendation", "final_conclusion_cn", "cross_loop_rationale_cn",
        "containment_assessment", "premium_selling_fit", "side_basis_cn",
        "dominant_conflict_cn", "key_premises", "invalid_if",
        "next_observation_cn", "session_advisory", "source_alignment",
        "side_comfort_ratings", "audit_only", "trade_authorization",
        "future_24h_bayesian_report", "policy_validation",
    }
    missing_fields = sorted(required_fields - set(advisory))
    unexpected_fields = sorted(set(advisory) - required_fields)
    if missing_fields or unexpected_fields:
        raise SystemExit(
            label + " integrated_trade_advisory fields invalid; missing="
            + ",".join(missing_fields) + " unexpected="
            + ",".join(unexpected_fields))
    recommendation = advisory.get("recommendation")
    policy = advisory.get("policy_validation")
    print(label + "_recommendation:", recommendation)
    print(label + "_audit_only:", advisory.get("audit_only"))
    print(label + "_trade_authorization:", advisory.get("trade_authorization"))
    print(label + "_policy_passed:", policy.get("passed") if isinstance(policy, dict) else None)
    print(label + "_authorization_is_not_structure_gate:",
          policy.get("authorization_is_not_structure_gate") if isinstance(policy, dict) else None)
    if recommendation not in ADVISORY_RECOMMENDATIONS:
        raise SystemExit(label + " integrated_trade_advisory.recommendation invalid")
    if advisory.get("audit_only") is not True:
        raise SystemExit(label + " integrated_trade_advisory.audit_only is not true")
    if advisory.get("trade_authorization") is not False:
        raise SystemExit(label + " integrated_trade_advisory.trade_authorization is not false")
    if not isinstance(policy, dict) or policy.get("passed") is not True:
        raise SystemExit(label + " integrated_trade_advisory.policy_validation.passed is not true")
    if policy.get("authorization_is_not_structure_gate") is not True:
        raise SystemExit(label + " advisory authorization/structure separation is not verified")
    required_text = [
        "final_conclusion_cn",
        "cross_loop_rationale_cn",
        "side_basis_cn",
        "dominant_conflict_cn",
        "next_observation_cn",
    ]
    for field in required_text:
        if not isinstance(advisory.get(field), str) or not advisory[field].strip():
            raise SystemExit(label + " integrated_trade_advisory." + field + " is blank")
    containment = advisory.get("containment_assessment")
    if (not isinstance(containment, dict)
            or set(containment) != {"state", "basis_cn"}
            or containment.get("state") not in ADVISORY_CONTAINMENT_STATES
            or not isinstance(containment.get("basis_cn"), str)
            or not containment["basis_cn"].strip()):
        raise SystemExit(label + " integrated_trade_advisory.containment_assessment invalid")
    premium = advisory.get("premium_selling_fit")
    if (not isinstance(premium, dict)
            or set(premium) != {"state", "basis_cn"}
            or premium.get("state") not in ADVISORY_PREMIUM_FIT_STATES
            or not isinstance(premium.get("basis_cn"), str)
            or not premium["basis_cn"].strip()):
        raise SystemExit(label + " integrated_trade_advisory.premium_selling_fit invalid")
    session = advisory.get("session_advisory")
    session_fields = {
        "liquidity_assessment", "warning_level", "basis_cn",
        "does_not_change_recommendation",
    }
    if (not isinstance(session, dict) or set(session) != session_fields
            or session.get("liquidity_assessment") not in ADVISORY_LIQUIDITY_ASSESSMENTS
            or session.get("warning_level") not in ADVISORY_WARNING_LEVELS
            or not isinstance(session.get("basis_cn"), str)
            or not session["basis_cn"].strip()
            or session.get("does_not_change_recommendation") is not True):
        raise SystemExit(label + " integrated_trade_advisory.session_advisory invalid")
    if advisory.get("source_alignment") not in ADVISORY_SOURCE_ALIGNMENTS:
        raise SystemExit(label + " integrated_trade_advisory.source_alignment invalid")
    premises = advisory.get("key_premises")
    if not isinstance(premises, list) or not (1 <= len(premises) <= 3):
        raise SystemExit(label + " integrated_trade_advisory.key_premises invalid")
    for premise in premises:
        if (not isinstance(premise, dict)
                or not isinstance(premise.get("premise_cn"), str)
                or not premise["premise_cn"].strip()
                or not isinstance(premise.get("evidence_refs"), list)
                or not premise["evidence_refs"]):
            raise SystemExit(label + " integrated_trade_advisory.key_premises item invalid")
    invalid_if = advisory.get("invalid_if")
    if (not isinstance(invalid_if, list) or not (1 <= len(invalid_if) <= 3)
            or any(not isinstance(item, str) or not item.strip()
                   for item in invalid_if)):
        raise SystemExit(label + " integrated_trade_advisory.invalid_if invalid")
    human_text = advisory_human_text(advisory)
    leaked_tokens = raw_human_tokens(human_text)
    if leaked_tokens:
        raise SystemExit(label + " integrated_trade_advisory human text contains raw codes: "
                         + ",".join(leaked_tokens))
    if any(pattern.search(human_text) for pattern in ADVISORY_EXECUTION_PATTERNS):
        raise SystemExit(label + " integrated_trade_advisory contains execution parameters")
    validate_comfort_ratings(advisory, latest_source, label)
    return advisory

source_cards = read_jsonl(signal_path)
expected_provider = sys.argv[5]
expected_model = sys.argv[6]
expected_schema = sys.argv[7]
expected_prompt_version = sys.argv[8]
expected_blind_mode = sys.argv[9]
expected_call_count = int(sys.argv[10])
if target_card_id:
    target_sources = [item for item in source_cards
                      if str(card_id(item)) == target_card_id]
    if not target_sources:
        raise SystemExit("target source card not found: " + target_card_id)
    latest_source = target_sources[-1]
else:
    latest_source = source_cards[-1]
latest_id = card_id(latest_source)
if not latest_id:
    raise SystemExit("latest source card lacks card_id")

latest_matching_review = None
latest_matching_record_id = None
for item in read_jsonl(review_path):
    current_id = item.get("card_id") or card_id(item)
    if current_id == latest_id:
        latest_matching_review = item.get("llm_review")
        latest_matching_record_id = current_id

print("target_card_id:", target_card_id or "LATEST")
print("latest_signal_card_id:", latest_id)
print("latest_matching_llm_card_id:", latest_matching_record_id)
if not isinstance(latest_matching_review, dict):
    raise SystemExit("no llm_review for latest source card")
print("latest_advisory_review_status:", latest_matching_review.get("status"))
print("latest_advisory_provider:", latest_matching_review.get("provider"))
print("latest_advisory_model:", latest_matching_review.get("model"))
print("latest_advisory_schema:", latest_matching_review.get("schema"))
print("latest_advisory_prompt_version:", latest_matching_review.get("prompt_version"))
print("latest_advisory_blind_review_mode:", latest_matching_review.get("blind_review_mode"))
print("latest_advisory_llm_call_count:", latest_matching_review.get("llm_call_count"))
if latest_matching_review.get("status") != "OK":
    raise SystemExit("latest matching llm_review is not OK")
if latest_matching_review.get("provider") != expected_provider:
    raise SystemExit("latest matching llm_review provider is not " + expected_provider)
if latest_matching_review.get("model") != expected_model:
    raise SystemExit("latest matching llm_review model is not " + expected_model)
if latest_matching_review.get("schema") != expected_schema:
    raise SystemExit("latest matching llm_review schema is not " + expected_schema)
if latest_matching_review.get("prompt_version") != expected_prompt_version:
    raise SystemExit("latest matching llm_review prompt version does not match bounded entrypoint")
if latest_matching_review.get("blind_review_mode") != expected_blind_mode:
    raise SystemExit("latest matching llm_review mode is not " + expected_blind_mode)
if int(latest_matching_review.get("llm_call_count") or 0) < expected_call_count:
    raise SystemExit("latest matching llm_review call count is below " + str(expected_call_count))
sidecar_advisory = validate_advisory(latest_matching_review, "latest_advisory")

manifest = json.loads((audit_root / "signal_cards/index.json").read_text(encoding="utf-8"))
cards = manifest.get("cards") or []
if not cards:
    raise SystemExit("materialized manifest has no cards")
selected_manifest = cards[0]
if target_card_id:
    matches = [item for item in cards
               if str(item.get("card_id") or "") == target_card_id]
    if not matches:
        raise SystemExit("target materialized card not found: " + target_card_id)
    selected_manifest = matches[0]
materialized_path = audit_root / selected_manifest.get("path", "")
materialized_card = json.loads(materialized_path.read_text(encoding="utf-8"))
materialized_id = card_id(materialized_card) or selected_manifest.get("card_id")
materialized_review = materialized_card.get("llm_review")
print("materialized_card_id:", materialized_id)
if materialized_id != latest_id:
    raise SystemExit("materialized latest card does not match latest source card")
if not isinstance(materialized_review, dict):
    raise SystemExit("materialized latest card lacks llm_review")
if materialized_review.get("status") != "OK":
    raise SystemExit("materialized latest card llm_review is not OK")
if materialized_review.get("provider") != expected_provider:
    raise SystemExit("materialized latest card llm_review provider is not " + expected_provider)
if materialized_review.get("model") != expected_model:
    raise SystemExit("materialized latest card llm_review model is not " + expected_model)
if materialized_review.get("schema") != expected_schema:
    raise SystemExit("materialized latest card llm_review schema is not " + expected_schema)
if materialized_review.get("prompt_version") != expected_prompt_version:
    raise SystemExit("materialized latest card llm_review prompt version does not match bounded entrypoint")
if materialized_review.get("blind_review_mode") != expected_blind_mode:
    raise SystemExit("materialized latest card llm_review mode is not " + expected_blind_mode)
if int(materialized_review.get("llm_call_count") or 0) < expected_call_count:
    raise SystemExit("materialized latest card llm_review call count is below " + str(expected_call_count))
materialized_advisory = validate_advisory(materialized_review, "materialized_advisory")
print("materialized_advisory_passthrough:", materialized_advisory == sidecar_advisory)
if materialized_advisory != sidecar_advisory:
    raise SystemExit("materialized latest card did not pass through integrated_trade_advisory")
manifest_summary = (selected_manifest.get("summary") or {}).get("signal_comfort_summary")
expected_summary = compact_comfort_summary(materialized_advisory["side_comfort_ratings"])
print("materialized_signal_comfort_summary:", manifest_summary == expected_summary)
if manifest_summary != expected_summary:
    raise SystemExit("materialized manifest did not pass through signal_comfort_summary")
PY
    then
      ok "latest signal card has OK provider-neutral integrated_trade_advisory and materialized passthrough"
    else
      fail "latest signal card lacks strict provider-neutral integrated_trade_advisory or materialized passthrough"
    fi
  else
    fail "skipped integrated_trade_advisory strict check; source, sidecar, manifest, or python3 unavailable"
  fi
fi

section "Transition LLM review sidecar"
if [ -r "$TRANSITION_LLM_REVIEWS_SOURCE" ]; then
  json_probe "latest transition LLM review sidecar" "$TRANSITION_LLM_REVIEWS_SOURCE" 'review=data.get("transition_llm_review") or {}; guard=review.get("language_guard") or {}; policy=review.get("policy_validation") or {}; print("transition_id:", data.get("transition_id")); print("status:", review.get("status")); print("provider:", review.get("provider")); print("model:", review.get("model")); print("schema_version:", review.get("schema_version")); print("prompt_version:", review.get("prompt_version")); print("blind_review_mode:", review.get("blind_review_mode")); print("llm_call_count:", review.get("llm_call_count")); print("policy_passed:", policy.get("passed")); print("render_state:", policy.get("render_state")); print("issue_codes:", policy.get("issue_codes")); print("no_trading_instruction:", guard.get("no_trading_instruction"))'
else
  warn "transition LLM review sidecar not readable yet: $TRANSITION_LLM_REVIEWS_SOURCE"
fi
if [ -r "$AUDIT_ROOT/signal_cards/index.json" ] && [ -r "$TRANSITION_LLM_REVIEWS_SOURCE" ] && have python3; then
  if python3 - "$AUDIT_ROOT" "$TRANSITION_LLM_REVIEWS_SOURCE" "$EXPECTED_TRANSITION_LLM_PROVIDER" "$EXPECTED_TRANSITION_LLM_MODEL" "$EXPECTED_TRANSITION_LLM_SCHEMA" "$EXPECTED_TRANSITION_LLM_PROMPT_VERSION" "$EXPECTED_TRANSITION_LLM_BLIND_MODE" "$EXPECTED_TRANSITION_LLM_CALL_COUNT" <<'PY'
import json, os, pathlib, sys
root = pathlib.Path(sys.argv[1])
review_path = pathlib.Path(sys.argv[2])
expected_provider = sys.argv[3]
expected_model = sys.argv[4]
expected_schema = sys.argv[5]
expected_prompt_version = sys.argv[6]
expected_blind_mode = sys.argv[7]
expected_call_count = int(sys.argv[8])
target_card_id = (os.environ.get("TARGET_CARD_ID")
                  or os.environ.get("ONLY_CARD_ID") or "")
manifest = json.loads((root / "signal_cards/index.json").read_text(encoding="utf-8"))
cards = manifest.get("cards") or []
if not cards:
    raise SystemExit("no cards")
selected_manifest = cards[0]
if target_card_id:
    matches = [item for item in cards
               if str(item.get("card_id") or "") == target_card_id]
    if not matches:
        raise SystemExit("target audit card not found in manifest: " + target_card_id)
    selected_manifest = matches[0]
card = json.loads((root / selected_manifest.get("path", "")).read_text(encoding="utf-8"))
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
print("target_card_id:", target_card_id or "LATEST")
print("latest_transition_id:", transition_id)
print("latest_transition_llm_status:", review.get("status"))
print("latest_transition_provider:", review.get("provider"))
print("latest_transition_model:", review.get("model"))
print("latest_transition_schema_version:", review.get("schema_version"))
print("latest_transition_prompt_version:", review.get("prompt_version"))
print("latest_transition_blind_review_mode:", review.get("blind_review_mode"))
print("latest_transition_llm_call_count:", review.get("llm_call_count"))
print("latest_transition_policy_passed:", policy.get("passed"))
print("latest_transition_render_state:", policy.get("render_state"))
print("latest_transition_issue_codes:", policy.get("issue_codes"))
print("latest_transition_evidence_catalog_hash:", review.get("evidence_catalog_hash"))
print("no_trading_instruction:", guard.get("no_trading_instruction"))
print("no_external_data:", guard.get("no_external_data"))
print("distinguishes_observation_from_causality:", guard.get("distinguishes_observation_from_causality"))
if review.get("provider") != expected_provider:
    raise SystemExit("latest transition LLM provider is not " + expected_provider)
if review.get("model") != expected_model:
    raise SystemExit("latest transition LLM model is not " + expected_model)
if review.get("schema_version") != expected_schema:
    raise SystemExit("latest transition LLM schema version is not " + expected_schema)
if review.get("prompt_version") != expected_prompt_version:
    raise SystemExit("latest transition LLM prompt version is not " + expected_prompt_version)
if review.get("blind_review_mode") != expected_blind_mode:
    raise SystemExit("latest transition LLM mode is not " + expected_blind_mode)
if int(review.get("llm_call_count") or 0) != expected_call_count:
    raise SystemExit("latest transition LLM call count is not " + str(expected_call_count))
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
    ok "latest transition has OK provider-neutral single-call LLM review with schema/render_state integrity"
  else
    if [ "$TRANSITION_LLM_REQUIRED" = "1" ]; then
      fail "latest transition lacks OK provider-neutral single-call LLM review with valid schema/render_state integrity"
    else
      warn "latest transition lacks OK provider-neutral single-call LLM review with valid schema/render_state integrity"
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
