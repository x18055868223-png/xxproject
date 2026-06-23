import pathlib
import shutil
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "tools" / "server_bootstrap_signal_stack.sh"
MIGRATION = ROOT / "deploy" / "signal_audit" / "SERVER_MIGRATION.md"
MIGRATION_ZH = ROOT / "deploy" / "signal_audit" / "SERVER_MIGRATION_ZH.md"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    assert_true(BOOTSTRAP.exists(),
                "new-server bootstrap script should exist")
    assert_true(MIGRATION.exists(),
                "server migration README should exist")
    assert_true(MIGRATION_ZH.exists(),
                "Chinese server migration quick runbook should exist")

    script = BOOTSTRAP.read_text(encoding="utf-8")
    doc = MIGRATION.read_text(encoding="utf-8")
    doc_zh = MIGRATION_ZH.read_text(encoding="utf-8")

    assert_true(script.startswith("#!/usr/bin/env bash"),
                "bootstrap should be a bash script")
    assert_true("set -euo pipefail" in script,
                "bootstrap should fail closed")
    assert_true("https://github.com/x18055868223-png/xxproject.git" in script,
                "bootstrap should default to the xxproject primary repo")
    assert_true('RELEASE_REF="${RELEASE_REF:-r3.1.1}"' in script,
                "bootstrap should default to the current r3.1.1 release")
    for token in (
            "install_or_update.sh",
            "server_self_check_signal_stack.sh",
            "--run-oneshots",
            "JSONL_SOURCE",
            "LLM_ENV_FILE",
            "GEX_ENV_FILE",
            "GEX_STATE_DIR",
            "GEX_BIND_HOST",
            "GEX_REQUIRED",
            "CACHE_FILE=",
            "HISTORY_FILE=",
            "10-bootstrap-overrides.conf",
            "EnvironmentFile=",
            "ExecStart=",
            ".service.d",
            "find_gex_source_dir",
            "run_as_gex_user",
            "IMPORT_HISTORY_DIR",
            "INSTALL_GEX",
            "need rsync",
            "RUN_SELF_CHECK"):
        assert_true(token in script, "bootstrap should mention " + token)
    assert_true("REPLACE_WITH" in script,
                "bootstrap may create templates, not real secrets")
    assert_true("AIza" not in script and "sk-" not in script,
                "bootstrap must not embed API keys")

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
            "r3.1.1",
            "/etc/signal-audit/llm.env",
            "/etc/gexmonitorapi.env",
            "/var/lib/gexmonitorapi",
            "signal_review.jsonl",
            "signal_llm_reviews.jsonl",
            "server_self_check_signal_stack.sh --run-oneshots",
            "GEX_REQUIRED=0",
            "GEX_BIND_HOST=0.0.0.0",
            "GEMINI_API_KEY",
            "API_TOKEN",
            "FMZ",
            "history",
            "raw.githubusercontent.com/x18055868223-png/xxproject/r3.1.1",
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
            "r3.1.1",
            "server_bootstrap_signal_stack.sh",
            "SERVER_MIGRATION.md",
            "/etc/signal-audit/llm.env",
            "/etc/gexmonitorapi.env",
            "GEMINI_API_KEY",
            "API_TOKEN",
            "signal_review.jsonl",
            "signal_llm_reviews.jsonl",
            "GEX_REQUIRED=0",
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
