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
    assert_true(entry.ENTRY_VERSION == "signal_llm_review_entry@1.1.5",
                "entry version mismatch")
    assert_true(entry.PROMPT_VERSION == "signal_llm_review_prompt@1.5.4",
                "entry prompt mismatch")
    assert_true(entry.core.PROVIDER == "deepseek", "entry provider mismatch")
    assert_true(entry.core.DEFAULT_MODEL == "deepseek-v4-flash", "entry model mismatch")
    assert_true(set(entry._HUMAN_CODE_REPLACEMENTS)
                == set(entry.core.ADVISORY_HUMAN_RAW_TOKENS),
                "human-code replacement map drifted from the core validator")
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
    none_payload = {
        "integrated_trade_advisory": {
            "recommendation": "NONE",
            "final_conclusion_cn": "当前没有主结论，记为 NONE。",
            "containment_assessment": {
                "state": "NONE",
                "basis_cn": "NONE",
            },
            "session_advisory": {
                "liquidity_assessment": "TIME_ONLY",
                "basis_cn": "当前为 TIME_ONLY。",
            },
            "key_premises": [{
                "premise_cn": "NONE",
                "evidence_refs": ["NONE"],
            }],
            "invalid_if": ["NONE"],
        },
    }
    repaired, trace = entry._repair_advisory_human_codes(none_payload)
    repaired_advisory = repaired["integrated_trade_advisory"]
    assert_true(trace["repair_applied"] and trace["repair_count"] == 5
                and trace["repair_tokens"] == ["NONE", "TIME_ONLY"],
                "advisory human-code repair trace mismatch")
    assert_true(repaired_advisory["recommendation"] == "NONE"
                and repaired_advisory["containment_assessment"]["state"] == "NONE"
                and repaired_advisory["session_advisory"]["liquidity_assessment"]
                == "TIME_ONLY"
                and repaired_advisory["key_premises"][0]["evidence_refs"] == ["NONE"],
                "machine enum or evidence fields were modified")
    assert_true("NONE" not in repaired_advisory["final_conclusion_cn"]
                and repaired_advisory["containment_assessment"]["basis_cn"] == "无"
                and repaired_advisory["session_advisory"]["basis_cn"]
                == "当前为 仅时间维度。"
                and repaired_advisory["key_premises"][0]["premise_cn"] == "无"
                and repaired_advisory["invalid_if"] == ["无"],
                "human fields retained raw NONE")
    level_payload = {
        "integrated_trade_advisory": {
            "recommendation": "NO_TRADE",
            "future_24h_bayesian_report": {
                "key_levels": [{
                    "price": 101500,
                    "role_cn": "卡内现价",
                    "source_type": "PACKET_OBSERVED",
                    "basis_cn": "来自卡内现价",
                }, {
                    "price": 104800,
                    "role_cn": "上方观察位",
                    "source_type": "PACKET_OBSERVED",
                    "basis_cn": "根据结构距离推导",
                }],
            },
        },
    }
    level_repaired, level_trace = entry._repair_misclassified_observed_levels(
        level_payload,
        {"market_context": {"price": 101500}},
    )
    levels = level_repaired["integrated_trade_advisory"][
        "future_24h_bayesian_report"
    ]["key_levels"]
    assert_true(level_trace == {
        "repair_applied": True,
        "repair_count": 1,
        "repair_indexes": [1],
    }, "misclassified key-level repair trace mismatch")
    assert_true(levels[0]["source_type"] == "PACKET_OBSERVED"
                and levels[0]["basis_cn"] == "来自卡内现价",
                "matched packet-observed level was modified")
    assert_true(levels[1]["price"] == 104800
                and levels[1]["source_type"] == "MODEL_ESTIMATED"
                and levels[1]["basis_cn"].startswith("模型估算观察位："),
                "unmatched observed level was not safely downgraded")
    assert_true(level_repaired["integrated_trade_advisory"]["recommendation"]
                == "NO_TRADE",
                "key-level repair changed the recommendation")
    structure_payload = {
        "theoretical_active_view": {"bias": "BULLISH"},
        "integrated_trade_advisory": {
            "recommendation": "SELL_PUT_SPREAD_REVIEW",
            "containment_assessment": {"state": "INCOMPLETE"},
            "premium_selling_fit": {"state": "FIT"},
            "future_24h_bayesian_report": {"base_case": "UP"},
        },
    }
    structure_repaired, structure_trace = (
        entry._repair_unsafe_structure_recommendation(
            structure_payload,
            {"decision": {"lean": "BULLISH"}},
        )
    )
    assert_true(
        structure_repaired["integrated_trade_advisory"]["recommendation"]
        == "NO_TRADE"
        and structure_trace["repair_applied"] is True
        and structure_trace["repair_reasons"] == ["CONTAINMENT_NOT_ESTABLISHED"],
        "unsafe spread recommendation was not narrowed to NO_TRADE",
    )
    unable_repaired, unable_trace = entry._repair_unsafe_structure_recommendation(
        {
            "integrated_trade_advisory": {
                "recommendation": "UNABLE_TO_JUDGE",
                "containment_assessment": {"state": "INCOMPLETE"},
                "premium_selling_fit": {"state": "NOT_FIT"},
            },
        },
        {},
    )
    assert_true(
        unable_repaired["integrated_trade_advisory"]["recommendation"]
        == "NO_TRADE"
        and unable_trace["repair_reasons"]
        == ["UNABLE_RECOMMENDATION_WITHOUT_UNABLE_ASSESSMENT"],
        "inconsistent unable recommendation was not narrowed to NO_TRADE",
    )
    help_result = subprocess.run(
        [sys.executable, str(TOOLS / "signal_llm_review_entry.py"), "--help"],
        cwd=ROOT, text=True, capture_output=True, check=False)
    assert_true(help_result.returncode == 0, help_result.stderr)
    for flag in ("--provider", "--api-key", "--base-url", "--concurrency",
                 "--daily-cap", "--blind-timeout", "--recon-timeout",
                 "--transition-timeout", "--retry-id", "--only-card-id"):
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
