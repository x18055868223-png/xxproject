import pathlib
import shutil
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "tools" / "server_bootstrap_signal_stack.sh"
SELF_CHECK = ROOT / "tools" / "server_self_check_signal_stack.sh"
MIGRATION = ROOT / "deploy" / "signal_audit" / "SERVER_MIGRATION.md"
MIGRATION_ZH = ROOT / "deploy" / "signal_audit" / "SERVER_MIGRATION_ZH.md"
MATERIALIZE_SERVICE = ROOT / "deploy" / "signal_audit" / "signal-audit-materialize.service"
MATERIALIZE_TIMER = ROOT / "deploy" / "signal_audit" / "signal-audit-materialize.timer"
INSTALL_OR_UPDATE = ROOT / "deploy" / "signal_audit" / "install_or_update.sh"
CANARY_RELEASE = ROOT / "tools" / "signal_llm_review_canary_release.sh"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    assert_true(BOOTSTRAP.exists(),
                "new-server bootstrap script should exist")
    assert_true(SELF_CHECK.exists(),
                "server self-check script should exist")
    assert_true(MIGRATION.exists(),
                "server migration README should exist")
    assert_true(MIGRATION_ZH.exists(),
                "Chinese server migration quick runbook should exist")
    assert_true(MATERIALIZE_SERVICE.exists(),
                "materializer systemd service should exist")
    assert_true(MATERIALIZE_TIMER.exists(),
                "materializer systemd timer should exist")
    assert_true(INSTALL_OR_UPDATE.exists(),
                "signal audit install/update script should exist")
    assert_true(CANARY_RELEASE.exists(),
                "signal audit canary release script should exist")

    script = BOOTSTRAP.read_text(encoding="utf-8")
    self_check = SELF_CHECK.read_text(encoding="utf-8")
    doc = MIGRATION.read_text(encoding="utf-8")
    doc_zh = MIGRATION_ZH.read_text(encoding="utf-8")
    materialize_service = MATERIALIZE_SERVICE.read_text(encoding="utf-8")
    materialize_timer = MATERIALIZE_TIMER.read_text(encoding="utf-8")
    install_or_update = INSTALL_OR_UPDATE.read_text(encoding="utf-8")
    canary_release = CANARY_RELEASE.read_text(encoding="utf-8")

    assert_true(script.startswith("#!/usr/bin/env bash"),
                "bootstrap should be a bash script")
    assert_true("set -euo pipefail" in script,
                "bootstrap should fail closed")
    assert_true("https://github.com/x18055868223-png/xxproject.git" in script,
                "bootstrap should default to the xxproject primary repo")
    assert_true('RELEASE_REF="${RELEASE_REF:-}"' in script
                and "RELEASE_REF is required" in script,
                "bootstrap should fail closed unless an explicit reviewed ref is provided")
    assert_true('fetch --tags xxproject "$RELEASE_REF"' in script
                and "FETCH_HEAD^{commit}" in script,
                "bootstrap should accept an explicit tag, branch, or commit")
    for token in (
            "install_or_update.sh",
            "server_self_check_signal_stack.sh",
            "--run-oneshots",
            "JSONL_SOURCE",
            "TRANSITION_LEDGER_SOURCE",
            "TRANSITION_STATE_SOURCE",
            "TRANSITION_LLM_REVIEWS_SOURCE",
            "LLM_ENV_FILE",
            "LLM_PROVIDER",
            "LLM_BASE_URL",
            "LLM_MODEL",
            "LLM_REVIEW_LIMIT",
            "TRANSITION_REVIEW_LIMIT",
            "LLM_MAX_CONCURRENCY",
            "LLM_DAILY_HTTP_CAP",
            "TRANSITION_BLIND_MODE",
            "GEX_ENV_FILE",
            "GEX_STATE_DIR",
            "GEX_BIND_HOST",
            "GEX_REQUIRED",
            "SESSION_CONTEXT_REQUIRED=1",
            "CACHE_FILE=",
            "HISTORY_FILE=",
            "10-bootstrap-overrides.conf",
            "EnvironmentFile=",
            "ExecStartPre=",
            "ExecStart=",
            ".service.d",
            "find_gex_source_dir",
            "run_as_gex_user",
            "IMPORT_HISTORY_DIR",
            "INSTALL_GEX",
            "need rsync",
            "RUN_SELF_CHECK"):
        assert_true(token in script, "bootstrap should mention " + token)
    for token in (
            "--require-valid-source-tail",
            "--transition-ledger \\${TRANSITION_LEDGER_SOURCE}",
            "--transition-state \\${TRANSITION_STATE_SOURCE}",
            "--transition-reviews \\${TRANSITION_LLM_REVIEWS_SOURCE}",
            "signal_transition_ledger.jsonl",
            "signal_transition_state.json",
            "signal_transition_llm_reviews.jsonl"):
        assert_true(token in script,
                    "bootstrap materializer override should preserve " + token)
    for token in (
            'Environment="TRANSITION_LEDGER_SOURCE=',
            'Environment="TRANSITION_LLM_REVIEWS_SOURCE=',
            'Environment="LLM_PROVIDER=',
            'Environment="LLM_BASE_URL=',
            'Environment="LLM_MODEL=',
            'Environment="LLM_MAX_CONCURRENCY=',
            'Environment="LLM_DAILY_HTTP_CAP=',
            "ExecStartPre=/bin/systemctl start signal-audit-materialize.service",
            "ExecStartPost=",
            "ExecStopPost=/bin/systemctl start signal-audit-materialize.service"):
        assert_true(token in script,
                    "bootstrap LLM override should preserve " + token)
    assert_true("ExecStart=\\${TOOLS_ROOT}/run_signal_llm_review.sh" not in script,
                "systemd ExecStart executable path must not start with an environment variable")
    assert_true('ExecStart=$(systemd_escape_value "$TOOLS_ROOT")/run_signal_llm_review.sh' in script,
                "bootstrap should render an absolute LLM runner path into the drop-in")
    assert_true("REPLACE_WITH" in script,
                "bootstrap may create templates, not real secrets")
    assert_true("AIza" not in script and "sk-" not in script,
                "bootstrap must not embed API keys")
    assert_true("GEMINI_" not in script and "LLM_API_KEY/API_TOKEN" in script,
                "bootstrap should not reference Gemini env vars for secrets")
    assert_true("r3.3.1" not in script and "r3.3.1" not in doc
                and "r3.3.1" not in doc_zh,
                "bootstrap and migration docs must not silently deploy the obsolete release")
    assert_true("--require-valid-source-tail" in materialize_service,
                "materializer service should fail loud on a corrupt latest source record")
    assert_true("--require-valid-source-tail" in install_or_update,
                "direct post-install materialization should fail loud on a corrupt tail")
    assert_true("signal_llm_review.py" in install_or_update
                and "signal_llm_review_entry.py" in install_or_update
                and "gemini_signal_llm_review.py" not in install_or_update
                and "LLM_API_KEY before expecting reviews" in install_or_update,
                "install script should deploy provider-neutral LLM assets")
    assert_true("signal_llm_review_canary_release.sh" in install_or_update
                and "server_self_check_signal_stack.sh" in install_or_update
                and "ENABLE_SIGNAL_AUDIT_TIMERS" in install_or_update
                and "START_SIGNAL_AUDIT_TIMERS" in install_or_update
                and "RUN_INITIAL_MATERIALIZE" in install_or_update
                and "RUN_INITIAL_LLM_REVIEW" in install_or_update
                and "systemctl enable --now signal-audit-llm-review.timer" not in install_or_update,
                "install script should deploy canary helper and keep timers/LLM opt-in")
    assert_true("--target-card-id" in canary_release
                and "--promote" in canary_release
                and "PROMOTION_STATUS=SKIPPED" in canary_release
                and "ROLLBACK_COMMAND=" in canary_release
                and "LLM_USAGE_LEDGER" in canary_release
                and "SYSTEMD_REQUIRED=0" in canary_release
                and "AUDIT_HTTP_REQUIRED=0" in canary_release,
                "canary release script should publish exact-target rollback-aware canaries")
    assert_true("Restart=" not in materialize_service
                and "RestartSec=" not in materialize_service,
                "oneshot materializer service must not use Restart")
    for token in (
            "SESSION_CONTEXT_REQUIRED",
            "DURABILITY_REQUIRED",
            "TARGET_CARD_ID",
            "SYSTEMD_REQUIRED",
            "AUDIT_HTTP_REQUIRED",
            "EXPECTED_SIGNAL_VERSION",
            "EXPECTED_LLM_PROVIDER",
            "EXPECTED_LLM_MODEL",
            "EXPECTED_LLM_SCHEMA",
            "EXPECTED_LLM_PROMPT_VERSION",
            "EXPECTED_LLM_BLIND_MODE",
            "EXPECTED_LLM_CALL_COUNT",
            "SignalSessionPremiseDurabilityContext",
            "compat_backfill_applied",
            "strategy_version",
            "1.5.7",
            "deepseek",
            "deepseek-v4-flash",
            "signal_llm_review@1.5.0",
            "signal_llm_review_prompt@1.5.4",
            "two_call_strict",
            "single_call_evidence_first",
            "macro_shock",
            "signal_durability",
            "SignalDurabilityLayer",
            "nrd.signal.durability_layer.v1",
            "SignalPriceAnchorDurability",
            "headline_score",
            "headline_state",
            "comfort_window",
            "price_anchor_durability",
            "audit_scope",
            "AUDIT_ONLY",
            "MACRO_SHOCK_BLOCKING",
            "latest card lacks producer-native macro_shock state",
            "latest MACRO shock block lacks MACRO_SHOCK_BLOCKING trace",
            "latest card uses materializer compatibility backfill",
            "latest card strategy_version does not match EXPECTED_SIGNAL_VERSION",
            "latest card lacks native signal_durability schema",
            'if [ "$SESSION_CONTEXT_REQUIRED" = "1" ] || [ "$DURABILITY_REQUIRED" = "1" ]; then',
            "latest audit card lacks native expected session_context, macro_shock, or durability schema",
            "latest card signal_durability uses materializer compatibility backfill",
            "TRANSITION_REQUIRED",
            "TRANSITION_LLM_REQUIRED",
            "TRANSITION_LEDGER_SOURCE",
            "TRANSITION_LLM_REVIEWS_SOURCE",
            "EXPECTED_TRANSITION_LLM_SCHEMA",
            "signal_transition_llm_review@1.3.0",
            "signal_transition_llm_review_prompt@1.3.2",
            "policy_validation",
            "policy_passed",
            "render_state",
            "issue_codes",
            "evidence_catalog_hash",
            "latest signal card has OK provider-neutral strict two-call LLM sidecar review",
            "latest transition has OK provider-neutral single-call LLM review"):
        assert_true(token in self_check,
                    "server self-check should enforce " + token)

    if shutil.which("bash"):
        bash_check = subprocess.run(
            ["bash", "-n", str(BOOTSTRAP)],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        assert_true(bash_check.returncode == 0,
                    bash_check.stderr or "bootstrap should pass bash -n")

    for token in (
            "xxproject",
            "DEPLOY_REF=codex/integrated-trade-advisory-next-design",
            "/etc/signal-audit/llm.env",
            "/etc/gexmonitorapi.env",
            "/var/lib/gexmonitorapi",
            "signal_review.jsonl",
            "signal_llm_reviews.jsonl",
            "server_self_check_signal_stack.sh --run-oneshots",
            "GEX_REQUIRED=0",
            "GEX_BIND_HOST=0.0.0.0",
            "API_TOKEN",
            "FMZ",
            "history",
            "raw.githubusercontent.com/x18055868223-png/xxproject/${DEPLOY_REF}",
            "SESSION_CONTEXT_REQUIRED=1",
            "SignalSessionPremiseDurabilityContext",
            "active verification",
            "rsync --delete",
            "commit hash"):
        assert_true(token in doc, "migration README should mention " + token)
    assert_true("do not commit" in doc.lower() or "never commit" in doc.lower(),
                "migration README should warn against committing secrets")
    assert_true("AIza" not in doc_zh and "sk-" not in doc_zh,
                "Chinese migration runbook must not embed API keys")
    for token in (
            "xxproject",
            "DEPLOY_REF=codex/integrated-trade-advisory-next-design",
            "server_bootstrap_signal_stack.sh",
            "SERVER_MIGRATION.md",
            "/etc/signal-audit/llm.env",
            "/etc/gexmonitorapi.env",
            "API_TOKEN",
            "signal_review.jsonl",
            "signal_llm_reviews.jsonl",
            "GEX_REQUIRED=0",
            "SESSION_CONTEXT_REQUIRED=1",
            "server_self_check_signal_stack.sh --run-oneshots",
            "FAIL=0",
            "signal-audit-deploy"):
        assert_true(token in doc_zh,
                    "Chinese migration runbook should mention " + token)

    print("server_bootstrap_assets: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("server_bootstrap_assets: FAIL - " + str(exc))
        sys.exit(1)
