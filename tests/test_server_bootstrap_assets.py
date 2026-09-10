import pathlib
import shutil
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "tools" / "server_bootstrap_signal_stack.sh"
SELF_CHECK = ROOT / "tools" / "server_self_check_signal_stack.sh"
MATERIALIZE_SERVICE = ROOT / "deploy" / "signal_audit" / "signal-audit-materialize.service"
MATERIALIZE_TIMER = ROOT / "deploy" / "signal_audit" / "signal-audit-materialize.timer"
INSTALL_OR_UPDATE = ROOT / "deploy" / "signal_audit" / "install_or_update.sh"
CANARY_RELEASE = ROOT / "tools" / "signal_llm_review_canary_release.sh"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    for path in (
            BOOTSTRAP,
            SELF_CHECK,
            MATERIALIZE_SERVICE,
            MATERIALIZE_TIMER,
            INSTALL_OR_UPDATE,
            CANARY_RELEASE):
        assert_true(path.exists(), "missing asset: " + str(path))

    script = BOOTSTRAP.read_text(encoding="utf-8")
    self_check = SELF_CHECK.read_text(encoding="utf-8")
    materialize_service = MATERIALIZE_SERVICE.read_text(encoding="utf-8")
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
                "bootstrap should require an explicit reviewed ref")
    assert_true('fetch --tags xxproject "$RELEASE_REF"' in script
                and "FETCH_HEAD^{commit}" in script,
                "bootstrap should accept a tag, branch, or commit")
    assert_true('MAX_CARDS="${MAX_CARDS:-15}"' in script
                and "Environment=MAX_CARDS=15" in materialize_service
                and 'MAX_CARDS="${MAX_CARDS:-15}"' in install_or_update,
                "bootstrap and materializer should publish 15 recent cards")

    for token in (
            "install_signal_audit_v2_tools",
            "signal_evidence_v2.py",
            "signal_review_v2.py",
            "signal_review_v2_runtime.py",
            'install -m 0755 "$source" "$TOOLS_ROOT/$module"',
            "missing v2 LLM review tool"):
        assert_true(token in script,
                    "bootstrap should install v2 review asset " + token)

    for token in (
            "LLM_PROVIDER",
            "LLM_BASE_URL",
            "LLM_MODEL",
            "LLM_REVIEW_LIMIT",
            "LLM_MAX_CONCURRENCY",
            "LLM_DAILY_HTTP_CAP",
            "LLM_RECON_EFFORT",
            "LLM_RECON_TIMEOUT",
            "TRANSITION_LEDGER_SOURCE",
            "TRANSITION_STATE_SOURCE",
            "TRANSITION_LLM_REVIEWS_SOURCE",
            "10-bootstrap-overrides.conf",
            "ExecStartPre=/bin/systemctl start signal-audit-materialize.service",
            "ExecStopPost=/bin/systemctl start signal-audit-materialize.service",
            "ExecStart=$(systemd_escape_value \"$TOOLS_ROOT\")/run_signal_llm_review.sh"):
        assert_true(token in script,
                    "bootstrap should preserve deploy setting " + token)

    for removed in (
            "TRANSITION_REVIEW_LIMIT",
            "TRANSITION_BLIND_MODE",
            "LLM_BLIND_EFFORT",
            "LLM_TRANSITION_EFFORT",
            "LLM_BLIND_TIMEOUT",
            "LLM_TRANSITION_TIMEOUT"):
        assert_true(removed not in script,
                    "bootstrap should not carry removed automatic stage " + removed)

    for token in (
            "EXPECTED_LLM_SCHEMA=signal_llm_review@2.1.0",
            "EXPECTED_LLM_PROMPT_VERSION=signal_llm_review_prompt@2.1.0",
            "EXPECTED_LLM_REVIEW_MODE=single_evidence_v2",
            "EXPECTED_LLM_CALL_COUNT=1",
            "EXPECTED_LLM_MAX_HTTP_ATTEMPTS=2",
            "INTEGRATED_ADVISORY_REQUIRED=1",
            "TRANSITION_REQUIRED=1"):
        assert_true(token in script,
                    "bootstrap self-check should pin v2 token " + token)
    assert_true("TRANSITION_LLM_REQUIRED=1" not in script,
                "bootstrap should not require transition LLM in the v2 auto path")

    assert_true("--require-valid-source-tail" in materialize_service,
                "materializer service should fail loud on a corrupt latest source record")
    assert_true("signal_llm_review.py" in install_or_update
                and "signal_llm_review_entry.py" in install_or_update
                and "signal_evidence_v2.py" in install_or_update
                and "signal_review_v2.py" in install_or_update
                and "signal_review_v2_runtime.py" in install_or_update
                and "signal_llm_review_canary_release.sh" in install_or_update,
                "install script should deploy common and v2 review helpers")
    assert_true("systemctl enable --now signal-audit-llm-review.timer"
                not in install_or_update,
                "install script must not enable review timers by default")

    for token in (
            'EXPECTED_LLM_SCHEMA="${EXPECTED_LLM_SCHEMA:-signal_llm_review@2.1.0}"',
            'EXPECTED_LLM_PROMPT_VERSION="${EXPECTED_LLM_PROMPT_VERSION:-signal_llm_review_prompt@2.1.0}"',
            'EXPECTED_LLM_REVIEW_MODE="${EXPECTED_LLM_REVIEW_MODE:-single_evidence_v2}"',
            'EXPECTED_LLM_CALL_COUNT="${EXPECTED_LLM_CALL_COUNT:-1}"',
            'EXPECTED_LLM_MAX_HTTP_ATTEMPTS="${EXPECTED_LLM_MAX_HTTP_ATTEMPTS:-2}"',
            "side_evidence_ratings",
            "validate_persisted_review",
            "revalidate_review",
            "build_summary",
            "latest signal card has provider-neutral v2 single evidence LLM sidecar review",
            "latest signal card has v2 integrated_trade_advisory"):
        assert_true(token in self_check,
                    "server self-check should enforce v2 token " + token)
    assert_true("canonical_hash" not in self_check
                and "hashlib" not in self_check,
                "server self-check should not embed a parallel v2 hash algorithm")

    assert_true("require_file \"$TOOLS_ROOT/signal_evidence_v2.py\"" in canary_release
                and "require_file \"$TOOLS_ROOT/signal_review_v2.py\"" in canary_release
                and "require_file \"$TOOLS_ROOT/signal_review_v2_runtime.py\"" in canary_release,
                "canary should require v2 review modules")
    assert_true("RECOVERY_STATUS=OWNED_BY_V2_PERSISTENT_BUDGET" in canary_release
                and "RETRY_ID=" not in canary_release,
                "canary should leave retry accounting to the v2 runtime")

    if shutil.which("bash"):
        for path in (BOOTSTRAP, SELF_CHECK, CANARY_RELEASE):
            bash_check = subprocess.run(
                ["bash", "-n", str(path)],
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
            assert_true(bash_check.returncode == 0,
                        bash_check.stderr or str(path) + " should pass bash -n")

    print("server_bootstrap_assets: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("server_bootstrap_assets: FAIL - " + str(exc))
        sys.exit(1)
