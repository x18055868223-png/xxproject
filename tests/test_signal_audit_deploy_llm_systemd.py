import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "signal_audit"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def read(path):
    assert_true(path.exists(), "missing deploy asset " + path.name)
    return path.read_text(encoding="utf-8")


def main():
    install = read(DEPLOY / "install_or_update.sh")
    materialize_service = read(DEPLOY / "signal-audit-materialize.service")
    llm_service = read(DEPLOY / "signal-audit-llm-review.service")
    llm_timer = read(DEPLOY / "signal-audit-llm-review.timer")
    llm_env = read(DEPLOY / "signal-audit-llm.env.example")
    runner = read(DEPLOY / "run_signal_llm_review.sh")
    package = read(DEPLOY / "package_signal_audit.ps1")
    deploy_readme = read(DEPLOY / "README.md")
    self_check = read(ROOT / "tools" / "server_self_check_signal_stack.sh")

    assert_true("/etc/signal-audit/llm.env" in llm_service,
                "LLM service should load the protected server env file")
    assert_true("EnvironmentFile=-/etc/signal-audit/llm.env" in llm_service,
                "LLM service should tolerate missing env until key is configured")
    assert_true("GEMINI_3_5_FLASH_API_KEY" in llm_env
                and "GEMINI_PAID_API_KEY" in llm_env
                and "GEMINI_API_KEY" in llm_env,
                "LLM env example should document low-cost, paid fallback, and legacy key names")
    assert_true("AIza" not in llm_env and "sk-" not in llm_env,
                "LLM env example must not contain a real-looking key")
    assert_true("run_signal_llm_review.sh" in llm_service,
                "LLM service should call the guarded runner")
    assert_true("--reviews-output" in runner,
                "LLM runner should write sidecar reviews")
    assert_true("Gemini API key is not configured" in runner
                and "GEMINI_3_5_FLASH_API_KEY" in runner
                and "GEMINI_PAID_API_KEY" in runner,
                "LLM runner should skip cleanly before either key is configured")
    assert_true("LLM_REVIEWS_SOURCE" in llm_service,
                "LLM service should use a stable sidecar path")
    assert_true("signal-audit-materialize.service" in llm_service,
                "LLM service should refresh materialized cards after reviews")
    assert_true("MemoryMax=256M" in llm_service,
                "LLM service should be capped for a 1GB server")
    assert_true("TimeoutStartSec=210" in llm_service,
                "LLM service timeout should allow two slow Gemini calls plus overhead")
    assert_true("OnUnitActiveSec=180" in llm_timer,
                "LLM timer should run automatically but not too aggressively")
    assert_true("SCRIPT_DIR=" in install and "DEPLOY_SRC=" in install,
                "install script should support both git and zip package layouts")
    assert_true("--exclude='*.jsonl'" in install,
                "install script must not publish local JSONL fixtures into the static root")
    assert_true("unsafe STATIC_ROOT" in install and 'STATIC_ROOT" == "/"' in install,
                "install script should reject an unsafe static root before cleanup")
    assert_true("find \"$STATIC_ROOT\" -type f -name '*.jsonl' -delete" in install,
                "install script must remove stale target-side JSONL from the public static root")
    assert_true("--exclude='*.jsonl'" in deploy_readme
                and "find /opt/signal-audit -type f -name '*.jsonl' -delete" in deploy_readme,
                "manual deployment docs must also avoid and clean public JSONL files")
    assert_true("signal-audit-llm-review.timer" in install,
                "install script should install and enable LLM timer by default")
    assert_true("signal-audit-llm.env.example" in install,
                "install script should install the env example")
    assert_true("LLM_REVIEWS_SOURCE" in materialize_service
                and "--llm-reviews" in materialize_service,
                "materializer should merge the LLM sidecar by default")
    assert_true("signal-audit-llm-review.service" in package
                and "signal-audit-llm-review.timer" in package
                and "signal-audit-llm.env.example" in package,
                "package script should include LLM systemd assets")
    assert_true("env file failed to load" in self_check
                and 'status="$?"' in self_check,
                "self-check must fail, not pass, when an env file has shell syntax errors")

    print("signal_audit_deploy_llm_systemd: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_deploy_llm_systemd: FAIL - " + str(exc))
        sys.exit(1)
