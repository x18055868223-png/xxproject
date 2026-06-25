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
    self_check = read(ROOT / "tools" / "server_self_check_signal_stack.sh")

    assert_true("/etc/signal-audit/llm.env" in llm_service,
                "LLM service should load the protected server env file")
    assert_true("EnvironmentFile=-/etc/signal-audit/llm.env" in llm_service,
                "LLM service should tolerate missing env until key is configured")
    assert_true("GEMINI_CHANNEL1_API_KEY" in llm_env
                and "GEMINI_CHANNEL2_API_KEY" in llm_env,
                "LLM env example should document two Gemini key channels")
    for legacy_name in ("GEMINI_API_KEY=", "GEMINI_PAID_API_KEY=", "GEMINI_FALLBACK_API_KEY="):
        assert_true(legacy_name not in llm_env,
                    "LLM env example should not expose legacy key entry " + legacy_name)
    assert_true("AIza" not in llm_env and "sk-" not in llm_env,
                "LLM env example must not contain a real-looking key")
    assert_true("run_signal_llm_review.sh" in llm_service,
                "LLM service should call the guarded runner")
    assert_true("--reviews-output" in runner,
                "LLM runner should write sidecar reviews")
    assert_true("GEMINI_CHANNEL1_API_KEY/GEMINI_CHANNEL2_API_KEY are not configured" in runner,
                "LLM runner should skip cleanly before both key channels are configured")
    assert_true("GEMINI_API_KEY:-" not in runner
                and "GEMINI_PAID_API_KEY:-" not in runner
                and "GEMINI_FALLBACK_API_KEY:-" not in runner,
                "LLM runner should only read the two channel key names")
    assert_true("LLM_REVIEWS_SOURCE" in llm_service,
                "LLM service should use a stable sidecar path")
    assert_true("signal-audit-materialize.service" in llm_service,
                "LLM service should refresh materialized cards after reviews")
    assert_true("ExecStartPre=/bin/systemctl start signal-audit-materialize.service" in llm_service,
                "LLM service should materialize before review so transition ledger is current")
    assert_true("ExecStartPost=/bin/systemctl start signal-audit-materialize.service" in llm_service,
                "LLM service should materialize after review so sidecars are merged")
    assert_true("MemoryMax=256M" in llm_service,
                "LLM service should be capped for a 1GB server")
    assert_true("TimeoutStartSec=300" in llm_service,
                "LLM service timeout should allow two slow Gemini calls plus channel fallback overhead")
    assert_true("OnUnitActiveSec=180" in llm_timer,
                "LLM timer should run automatically but not too aggressively")
    assert_true("SCRIPT_DIR=" in install and "DEPLOY_SRC=" in install,
                "install script should support both git and zip package layouts")
    assert_true("signal-audit-llm-review.timer" in install,
                "install script should install and enable LLM timer by default")
    assert_true("signal-audit-llm.env.example" in install,
                "install script should install the env example")
    assert_true("LLM_REVIEWS_SOURCE" in materialize_service
                and "--llm-reviews" in materialize_service,
                "materializer should merge the LLM sidecar by default")
    assert_true("TRANSITION_LEDGER_SOURCE" in materialize_service
                and "--transition-ledger" in materialize_service
                and "TRANSITION_LLM_REVIEWS_SOURCE" in materialize_service
                and "--transition-reviews" in materialize_service,
                "materializer should build and merge transition sidecars without a new service")
    assert_true("--mode" in runner and "both" in runner
                and "TRANSITION_LEDGER_SOURCE" in runner
                and "TRANSITION_LLM_REVIEWS_SOURCE" in runner,
                "LLM runner should invoke card and transition review modes together")
    assert_true("TRANSITION_REQUIRED" in self_check
                and "TRANSITION_LLM_REQUIRED" in self_check
                and "transition_context" in self_check
                and "no_trading_instruction" in self_check,
                "server self-check should validate transition context and transition LLM guard")
    assert_true("started signal-audit-materialize.service before LLM" in self_check
                and "started signal-audit-materialize.service after LLM" in self_check,
                "server self-check active mode should materialize before and after LLM")
    transition_units = list(DEPLOY.glob("signal-transition-*"))
    assert_true(not transition_units,
                "T0/T1 must reuse existing services, not add signal-transition units")
    assert_true("signal-audit-llm-review.service" in package
                and "signal-audit-llm-review.timer" in package
                and "signal-audit-llm.env.example" in package,
                "package script should include LLM systemd assets")
    assert_true("GEX_REQUIRED" in self_check
                and "skipped gexmonitorapi.service check" in self_check
                and "skipped GEX Monitor API active checks" in self_check,
                "self-check should support signal-audit-only hosts without GEX")
    assert_true("Gemini channel 1 key is configured" in self_check
                and "Gemini channel 2 key is configured" in self_check
                and "api_key_route" in self_check,
                "self-check should expose Gemini key channel readiness and route")
    assert_true("LLM_REQUIRED" in self_check
                and "latest signal card has OK two-call LLM sidecar review" in self_check
                and "blind_review_mode" in self_check
                and "llm_call_count" in self_check,
                "self-check should prove latest card has a two-call LLM review when required")

    print("signal_audit_deploy_llm_systemd: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_deploy_llm_systemd: FAIL - " + str(exc))
        sys.exit(1)
