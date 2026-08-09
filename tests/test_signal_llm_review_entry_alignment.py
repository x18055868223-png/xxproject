import importlib.util
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    core = load(TOOLS / "signal_llm_review.py", "signal_llm_review_entry_core")
    entry = load(TOOLS / "signal_llm_review_entry.py", "signal_llm_review_entry_test")
    assert_true(entry.ENTRY_VERSION == "signal_llm_review_entry@1.1.0",
                "entry version mismatch")
    assert_true(entry.PROMPT_VERSION == "signal_llm_review_prompt@1.5.3",
                "entry prompt mismatch")
    assert_true(entry.core.PROVIDER == "deepseek", "entry provider mismatch")
    assert_true(entry.core.DEFAULT_MODEL == "deepseek-v4-flash", "entry model mismatch")
    blind_payload = {
        "theoretical_active_view": entry.core._default_theoretical_active_view("测试"),
        "gamma_regime_lens": entry.core._default_gamma_regime_lens("测试"),
    }
    entry_prompt = entry.build_prompt({"market_context": {"price": 101500}}, blind_payload)
    assert_true("第一性贝叶斯审计协议" in entry_prompt,
                "entry did not inherit the core reasoning protocol")
    assert_true("source_alignment 精确映射" in entry_prompt,
                "entry alignment suffix missing")
    retry_prompt = entry.build_prompt(
        {"market_context": {"price": 101500}},
        blind_payload,
        empty_content_retry_count=1,
    )
    assert_true("RETRY_RECOVERY_INSTRUCTION" in retry_prompt,
                "entry did not forward the empty-content retry context")
    help_result = subprocess.run(
        [sys.executable, str(TOOLS / "signal_llm_review_entry.py"), "--help"],
        cwd=ROOT, text=True, capture_output=True, check=False)
    assert_true(help_result.returncode == 0, help_result.stderr)
    for flag in ("--provider", "--api-key", "--base-url", "--concurrency",
                 "--daily-cap", "--blind-timeout", "--recon-timeout",
                 "--transition-timeout", "--retry-id"):
        assert_true(flag in help_result.stdout, "missing CLI flag " + flag)
    installer = (ROOT / "deploy/signal_audit/install_or_update.sh").read_text(encoding="utf-8")
    runner = (ROOT / "deploy/signal_audit/run_signal_llm_review.sh").read_text(encoding="utf-8")
    package = (ROOT / "deploy/signal_audit/package_signal_audit.ps1").read_text(encoding="utf-8")
    for text in (installer, package):
        assert_true("signal_llm_review.py" in text, "provider-neutral core not packaged")
        assert_true("gemini_signal_llm_review.py" not in text, "legacy runtime still referenced")
    assert_true("signal_llm_review_entry.py" in runner,
                "provider-neutral entrypoint not used by runner")
    assert_true("GEMINI_" not in runner, "legacy env compatibility remains")
    assert_true("LLM_BASE_URL:-https://api.deepseek.com" in runner,
                "DeepSeek base URL mismatch")
    print("signal_llm_review_entry_alignment: PASS")


if __name__ == "__main__":
    main()
