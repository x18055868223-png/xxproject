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
SERVER_SELF_CHECK_SRC="$REPO_ROOT/tools/server_self_check_signal_stack.sh"
LLM_ENV_EXAMPLE_SRC="$DEPLOY_SRC/signal-audit-llm.env.example"
MATERIALIZE_SERVICE_SRC="$DEPLOY_SRC/signal-audit-materialize.service"
MATERIALIZE_TIMER_SRC="$DEPLOY_SRC/signal-audit-materialize.timer"
WORKBENCH_SERVICE_SRC="$DEPLOY_SRC/workbench-v20.service"
WORKBENCH_ENV_EXAMPLE_SRC="$DEPLOY_SRC/workbench-v20.env.example"
STATIC_ROOT="${STATIC_ROOT:-/opt/signal-audit}"
TOOLS_ROOT="${TOOLS_ROOT:-/opt/signal-audit-tools}"
CONFIG_ROOT="${CONFIG_ROOT:-/etc/signal-audit}"
LLM_ENV_FILE="${LLM_ENV_FILE:-$CONFIG_ROOT/llm.env}"
WORKBENCH_ENV_FILE="${WORKBENCH_ENV_FILE:-$CONFIG_ROOT/workbench-v20.env}"
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
ENABLE_WORKBENCH="${ENABLE_WORKBENCH:-0}"
START_WORKBENCH="${START_WORKBENCH:-0}"
APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS="${APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS:-0}"

if [[ "$RUN_INITIAL_LLM_REVIEW" == "1" ]]; then
  echo "RUN_INITIAL_LLM_REVIEW is retired in workbench v2.0; manual POST only" >&2
  exit 2
fi
if [[ "$APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS" != "1" ]]; then
  for requested_action in \
    ENABLE_SIGNAL_AUDIT_TIMERS START_SIGNAL_AUDIT_TIMERS ENABLE_WORKBENCH START_WORKBENCH; do
    if [[ "${!requested_action}" == "1" ]]; then
      echo "$requested_action requires APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS=1" >&2
      exit 2
    fi
  done
fi

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

if [[ ! -f "$SERVER_SELF_CHECK_SRC" ]]; then
  echo "missing server self-check helper: $SERVER_SELF_CHECK_SRC" >&2
  exit 2
fi

for required in "$LLM_ENV_EXAMPLE_SRC" "$MATERIALIZE_SERVICE_SRC" "$MATERIALIZE_TIMER_SRC"; do
  if [[ ! -f "$required" ]]; then
    echo "missing deployment asset: $required" >&2
    exit 2
  fi
done

workbench_modules=(
  astra_workbench_v20.py
  astra_workbench_session_v1.py
  astra_workbench_reading_v1.py
  astra_locked_candidate_store_v1.py
  astra_locked_candidate_compare_v1.py
  astra_decision_reduction_v1.py
  astra_spatial_position_v1.py
  astra_cross_asset_v1.py
  astra_event_calendar_v1.py
  astra_thesis_ledger_v1.py
  astra_hold_review_v1.py
  astra_expiry_gamma_v1.py
  astra_decision_path_v1.py
  astra_decision_path_store_v1.py
  astra_decision_path_report.py
  astra_observation_replay_v1.py
  astra_chain_audit_v1.py
  astra_risk_qualification_v244.py
  astra_commercial_math_v17.py
  astra_underwriting_v20.py
  astra_underwriting_fact_context_v20.py
  astra_underwriting_discovery_v20.py
  astra_underwriting_public_quote_v20.py
  astra_underwriting_real_replay_v20.py
  astra_underwriting_store_v20.py
  astra_underwriting_review_v20.py
  astra_underwriting_guidance_v20.py
  astra_candidate_asof_v1.py
  astra_candidate_features_v1.py
  astra_wpem_native_v1.py
  astra_wpem_shadow_v1.py
  astra_wpem_review_evidence_v1.py
  astra_evidence_routing_v1.py
  astra_wpem_product_review_v1.py
  astra_wpem_workbench_v1.py
  astra_wpem_reference_v1.py
  astra_wpem_results_v1.py
  astra_release_identity_v1.py
  astra_data_engine_v23.py
  astra_data_projection_v23.py
  astra_data_retention_v23.py
  astra_data_server_v23.py
  astra_map_sources_v24.py
  astra_map_data_server_v24.py
  astra_map_v24.py
  astra_map_projection_v24.py
  astra_map_presentation_v241.py
)
joint_modules=(
  astra_joint_contract.py
  astra_joint_data.py
  astra_joint_dataset.py
  astra_joint_sources.py
  astra_joint_v11_contract.py
  astra_joint_v11_data.py
  astra_joint_v11_inference.py
  astra_joint_v11_model.py
  astra_joint_projection.py
  astra_joint_bridge.py
)
data_engine_deploy_assets=(
  data-engine-maintenance.service
  data-engine-maintenance.timer
  data-engine-maintenance.env.example
  install_data_engine_v23.sh
)
DATA_REGISTRY_SRC="$REPO_ROOT/demo/shared/data_product_registry_v23.json"
if [[ ! -f "$DATA_REGISTRY_SRC" ]]; then
  DATA_REGISTRY_SRC="$REPO_ROOT/tools/data_product_registry_v23.json"
fi
if [[ ! -f "$DATA_REGISTRY_SRC" ]]; then
  echo "missing data product registry: $DATA_REGISTRY_SRC" >&2
  exit 2
fi
for module in "${workbench_modules[@]}"; do
  if [[ ! -f "$REPO_ROOT/tools/$module" ]]; then
    echo "missing workbench module: $REPO_ROOT/tools/$module" >&2
    exit 2
  fi
done
for module in "${joint_modules[@]}"; do
  if [[ ! -f "$REPO_ROOT/tools/$module" ]]; then
    echo "missing joint review module: $REPO_ROOT/tools/$module" >&2
    exit 2
  fi
done
for required in "$WORKBENCH_SERVICE_SRC" "$WORKBENCH_ENV_EXAMPLE_SRC"; do
  if [[ ! -f "$required" ]]; then
    echo "missing workbench deployment asset: $required" >&2
    exit 2
  fi
done
for asset in "${data_engine_deploy_assets[@]}"; do
  if [[ ! -f "$DEPLOY_SRC/$asset" ]]; then
    echo "missing data engine deployment asset: $DEPLOY_SRC/$asset" >&2
    exit 2
  fi
done

loaded_systemd_unit() {
  local unit="$1"
  local load_state
  if ! load_state="$(systemctl show -p LoadState --value "$unit" 2>/dev/null)"; then
    echo "failed to inspect systemd unit before install: $unit" >&2
    exit 2
  fi
  case "$load_state" in
    loaded)
      return 0
      ;;
    not-found)
      echo "systemd unit not found; skip quiesce: $unit"
      return 1
      ;;
    *)
      echo "refusing install: unexpected LoadState=$load_state for $unit" >&2
      exit 2
      ;;
  esac
}

stop_loaded_unit() {
  local unit="$1"
  if loaded_systemd_unit "$unit"; then
    if ! systemctl stop "$unit"; then
      echo "failed to stop systemd unit before install: $unit" >&2
      exit 2
    fi
  fi
}

disable_loaded_timer() {
  local unit="$1"
  if loaded_systemd_unit "$unit"; then
    if ! systemctl disable --now "$unit"; then
      echo "failed to disable systemd timer before install: $unit" >&2
      exit 2
    fi
  fi
}

if [[ "$APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS" == "1" ]]; then
  disable_loaded_timer signal-audit-materialize.timer
  stop_loaded_unit signal-audit-materialize.service
  disable_loaded_timer signal-audit-llm-review.timer
  stop_loaded_unit signal-audit-llm-review.service
  stop_loaded_unit astra-workbench-v20.service
else
  echo "safe default: service actions disabled; set APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS=1 to inspect, stop, install, reload, enable, or start systemd units"
fi

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
for joint_module in "${joint_modules[@]}"; do
  install -m 0755 "$REPO_ROOT/tools/$joint_module" "$TOOLS_ROOT/$joint_module"
done
# Joint collection is a separate, explicitly installed shadow service. Merely
# updating the compatible reader never starts collection or a forward window.
install -m 0644 "$FACT_SEMANTICS_SRC" "$TOOLS_ROOT/signal_fact_semantics.py"
install -m 0755 "$SERVER_SELF_CHECK_SRC" "$TOOLS_ROOT/server_self_check_signal_stack.sh"
for module in "${workbench_modules[@]}"; do
  install -m 0644 "$REPO_ROOT/tools/$module" "$TOOLS_ROOT/$module"
done
sanitize_installed_source_defaults() {
  local target
  local dev_artifact_prefix=".artifact""s"
  local escaped_dev_artifact_prefix="\\.artifact""s"
  target="$TOOLS_ROOT/astra_joint_v11_data.py"
  if [[ -f "$target" ]]; then
    sed -i \
      -e 's|^DEFAULT_SOURCE_ROOT = .*|DEFAULT_SOURCE_ROOT = Path(os.environ.get("ASTRA_JOINT_V11_SOURCE_ROOT", "offline/astra-joint-v1"))|' \
      -e 's|^DEFAULT_OUTPUT_ROOT = .*|DEFAULT_OUTPUT_ROOT = Path(os.environ.get("ASTRA_JOINT_V11_OUTPUT_ROOT", "offline/astra-joint-v11"))|' \
      "$target"
  fi
  target="$TOOLS_ROOT/astra_joint_sources.py"
  if [[ -f "$target" ]]; then
    sed -i \
      -e "s|${escaped_dev_artifact_prefix}/astra-local-validation-20260914|offline/astra-local-validation-20260914|g" \
      -e "s|${escaped_dev_artifact_prefix}/astra-light-study-20260913|offline/astra-light-study-20260913|g" \
      "$target"
  fi
  target="$TOOLS_ROOT/astra_workbench_v20.py"
  if [[ -f "$target" ]]; then
    sed -i -e "s|\"${escaped_dev_artifact_prefix}/astra-workbench-v20\"|\"state/astra-workbench-v20\"|g" "$target"
  fi
  target="$TOOLS_ROOT/astra_observation_replay_v1.py"
  if [[ -f "$target" ]]; then
    sed -i \
      -e "s|${escaped_dev_artifact_prefix}/observation_lineage_20261007_01|offline/observation_lineage_20261007_01|g" \
      -e "s|${escaped_dev_artifact_prefix}/semantic_contract_closure_20261007_01|offline/semantic_contract_closure_20261007_01|g" \
      -e "s|${escaped_dev_artifact_prefix}/semantic_synthesis_20261007_01|offline/semantic_synthesis_20261007_01|g" \
      -e "s|${escaped_dev_artifact_prefix}/consumption_ui_audit_20261007_01|offline/consumption_ui_audit_20261007_01|g" \
      "$target"
  fi
  target="$TOOLS_ROOT/astra_underwriting_real_replay_v20.py"
  if [[ -f "$target" ]]; then
    sed -i -e "s|${escaped_dev_artifact_prefix}/astra-credit-space-20260914|offline/astra-credit-space-20260914|g" "$target"
  fi
  unset dev_artifact_prefix
}
sanitize_installed_source_defaults
if [[ -f "$DEPLOY_SRC/release_candidate_manifest.json" ]]; then
  install -m 0644 "$DEPLOY_SRC/release_candidate_manifest.json" "$TOOLS_ROOT/release_candidate_manifest.json"
elif [[ -f "$DEPLOY_SRC/signal_audit/release_candidate_manifest.json" ]]; then
  install -m 0644 "$DEPLOY_SRC/signal_audit/release_candidate_manifest.json" "$TOOLS_ROOT/release_candidate_manifest.json"
fi
install -m 0644 "$DATA_REGISTRY_SRC" "$TOOLS_ROOT/data_product_registry_v23.json"
if [[ -f "$DEPLOY_SRC/phase_release_manifest.json" ]]; then
  install -m 0644 "$DEPLOY_SRC/phase_release_manifest.json" "$TOOLS_ROOT/phase_release_manifest.json"
elif [[ -f "$DEPLOY_SRC/signal_audit/phase_release_manifest.json" ]]; then
  install -m 0644 "$DEPLOY_SRC/signal_audit/phase_release_manifest.json" "$TOOLS_ROOT/phase_release_manifest.json"
fi
# Data maintenance uses its own installer and never starts through a page update.
for asset in "${data_engine_deploy_assets[@]}"; do
  install -m 0644 "$DEPLOY_SRC/$asset" "$TOOLS_ROOT/$asset"
done
install -m 0644 "$LLM_ENV_EXAMPLE_SRC" "$CONFIG_ROOT/llm.env.example"
install -m 0644 "$WORKBENCH_ENV_EXAMPLE_SRC" "$CONFIG_ROOT/workbench-v20.env.example"
if [[ ! -f "$LLM_ENV_FILE" ]]; then
  install -m 0600 "$LLM_ENV_EXAMPLE_SRC" "$LLM_ENV_FILE"
  echo "created LLM API key template at $LLM_ENV_FILE; edit LLM_API_KEY before expecting reviews"
else
  chmod 0600 "$LLM_ENV_FILE"
fi
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  chown root:root "$LLM_ENV_FILE"
fi
if [[ ! -f "$WORKBENCH_ENV_FILE" ]]; then
  install -m 0600 "$WORKBENCH_ENV_EXAMPLE_SRC" "$WORKBENCH_ENV_FILE"
  echo "created disabled-by-default workbench config at $WORKBENCH_ENV_FILE"
else
  chmod 0600 "$WORKBENCH_ENV_FILE"
fi
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  chown root:root "$WORKBENCH_ENV_FILE"
fi

if [[ "$APPLY_SIGNAL_AUDIT_SERVICE_ACTIONS" == "1" ]]; then
  install -m 0644 "$MATERIALIZE_SERVICE_SRC" /etc/systemd/system/signal-audit-materialize.service
  install -m 0644 "$MATERIALIZE_TIMER_SRC" /etc/systemd/system/signal-audit-materialize.timer
  install -m 0644 "$WORKBENCH_SERVICE_SRC" /etc/systemd/system/astra-workbench-v20.service
  systemctl daemon-reload

  if [[ "$ENABLE_SIGNAL_AUDIT_TIMERS" == "1" ]]; then
    systemctl enable signal-audit-materialize.timer
  else
    echo "safe default: timers installed but not enabled; set ENABLE_SIGNAL_AUDIT_TIMERS=1 after canary validation"
  fi

  if [[ "$START_SIGNAL_AUDIT_TIMERS" == "1" ]]; then
    systemctl start signal-audit-materialize.timer
  else
    echo "safe default: timers not started; set START_SIGNAL_AUDIT_TIMERS=1 after canary validation"
  fi

  if [[ "$ENABLE_WORKBENCH" == "1" ]]; then
    systemctl enable astra-workbench-v20.service
  else
    echo "safe default: workbench service installed but not enabled"
  fi
  if [[ "$START_WORKBENCH" == "1" ]]; then
    systemctl start astra-workbench-v20.service
  else
    echo "safe default: workbench service not started"
  fi
else
  echo "safe default: systemd unit files not written and daemon-reload not run"
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

echo "installed signal audit frontend to $STATIC_ROOT"
echo "materializer installed to $TOOLS_ROOT/materialize_signal_cards.py"
echo "provider-neutral LLM review tool installed to $TOOLS_ROOT/signal_llm_review.py"
echo "provider-neutral LLM review entrypoint installed to $TOOLS_ROOT/signal_llm_review_entry.py"
echo "v2 evidence packet tool installed to $TOOLS_ROOT/signal_evidence_v2.py"
echo "v2 evidence review tool installed to $TOOLS_ROOT/signal_review_v2.py"
echo "v2 evidence review runtime installed to $TOOLS_ROOT/signal_review_v2_runtime.py"
echo "deterministic fact semantics installed to $TOOLS_ROOT/signal_fact_semantics.py"
echo "server self-check helper installed to $TOOLS_ROOT/server_self_check_signal_stack.sh"
echo "LLM API key config lives at $LLM_ENV_FILE"
echo "LLM review sidecar lives at $LLM_REVIEWS_SOURCE"
echo "transition ledger lives at $TRANSITION_LEDGER_SOURCE"
echo "transition state lives at $TRANSITION_STATE_SOURCE"
echo "transition LLM review sidecar lives at $TRANSITION_LLM_REVIEWS_SOURCE"
echo "workbench service is astra-workbench-v20.service; config: $WORKBENCH_ENV_FILE"
