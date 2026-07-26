import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
PIPELINE_TEST = ROOT / "tests" / "test_signal_llm_review_pipeline.py"
INSTALLER = ROOT / "deploy" / "signal_audit" / "install_or_update.sh"
RUNNER = ROOT / "deploy" / "signal_audit" / "run_signal_llm_review.sh"

sys.path.insert(0, str(TOOLS))


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def expect_value_error(fn, expected_text, message):
    try:
        fn()
    except ValueError as exc:
        assert_true(expected_text in str(exc), message + ": " + str(exc))
        return
    raise AssertionError(message + ": expected ValueError")


def main():
    fixture = load_module(PIPELINE_TEST, "signal_llm_pipeline_fixture_for_entry")
    entry = load_module(
        TOOLS / "gemini_signal_llm_review_entry.py",
        "gemini_signal_llm_review_entry_test",
    )

    card = fixture.review_context_card("CARD-ENTRY-PARTIAL-REPAIR")
    payload = fixture.model_payload()
    payload["integrated_trade_advisory"] = fixture.integrated_trade_advisory(
        "WAIT_FOR_CONFIRMATION"
    )
    payload["integrated_trade_advisory"]["source_alignment"] = "ALIGNED"
    payload["theoretical_active_view"]["bias"] = "MIXED_UNCLEAR"

    review = entry.build_llm_review(
        card,
        payload,
        model="gemini-3.5-flash",
        reviewed_at="2026-07-19T00:00:00+00:00",
    )
    advisory = review["integrated_trade_advisory"]
    policy = advisory["policy_validation"]
    assert_true(review["status"] == "OK", "bounded repair should yield an OK review")
    assert_true(
        review["prompt_version"] == "gemini_signal_review_prompt@1.4.7",
        "entrypoint should record the patched prompt version",
    )
    assert_true(
        advisory["source_alignment"] == "PARTIALLY_ALIGNED",
        "non-exact ALIGNED claim should normalize to PARTIALLY_ALIGNED",
    )
    assert_true(
        policy["source_alignment_repair_applied"] is True
        and policy["source_alignment_claimed"] == "ALIGNED"
        and policy["source_alignment_final"] == "PARTIALLY_ALIGNED"
        and policy["source_alignment_repair_reason"]
        == "ALIGNED_REQUIRES_EXACT_RECOGNIZED_DIRECTION_MATCH",
        "repair trace should preserve claimed/final alignment and reason",
    )
    assert_true(
        policy["passed"] is True
        and policy["producer_hard_block_respected"] is True
        and policy["waiting_signal_not_upgraded"] is True,
        "all original policy gates should remain passed and visible",
    )

    exact_card = fixture.review_context_card("CARD-ENTRY-EXACT-ALIGNED")
    exact_payload = fixture.model_payload()
    exact_payload["integrated_trade_advisory"] = fixture.integrated_trade_advisory(
        "WAIT_FOR_CONFIRMATION"
    )
    exact_payload["integrated_trade_advisory"]["source_alignment"] = "ALIGNED"
    exact_payload["theoretical_active_view"]["bias"] = "BULLISH_LEAN"
    exact_review = entry.build_llm_review(exact_card, exact_payload)
    exact_policy = exact_review["integrated_trade_advisory"]["policy_validation"]
    assert_true(
        exact_review["integrated_trade_advisory"]["source_alignment"] == "ALIGNED"
        and exact_policy["source_alignment_repair_applied"] is False,
        "an exact recognized direction match should remain ALIGNED without repair",
    )

    divergent_card = fixture.review_context_card("CARD-ENTRY-DIVERGENT-WAIT")
    divergent_payload = fixture.model_payload()
    divergent_payload["integrated_trade_advisory"] = fixture.integrated_trade_advisory(
        "WAIT_FOR_CONFIRMATION"
    )
    divergent_payload["integrated_trade_advisory"]["source_alignment"] = "ALIGNED"
    divergent_payload["theoretical_active_view"]["bias"] = "BEARISH_LEAN"
    divergent_review = entry.build_llm_review(divergent_card, divergent_payload)
    divergent_advisory = divergent_review["integrated_trade_advisory"]
    assert_true(
        divergent_advisory["source_alignment"] == "DIVERGENT"
        and divergent_advisory["policy_validation"][
            "source_alignment_repair_reason"
        ] == "DIRECT_DIRECTION_OPPOSITION_REQUIRES_DIVERGENT",
        "directly opposed directions should normalize to DIVERGENT for a wait conclusion",
    )

    spread_card = fixture.review_context_card("CARD-ENTRY-SPREAD-CONFLICT")
    spread_payload = fixture.model_payload()
    spread_payload["integrated_trade_advisory"] = fixture.integrated_trade_advisory(
        "SELL_PUT_SPREAD_REVIEW"
    )
    spread_payload["integrated_trade_advisory"]["source_alignment"] = "ALIGNED"
    spread_payload["theoretical_active_view"]["bias"] = "BEARISH_LEAN"
    expect_value_error(
        lambda: entry.build_llm_review(spread_card, spread_payload),
        "blind theoretical direction conflicts with spread recommendation",
        "alignment repair must not legalize a directionally opposed spread",
    )

    invalid_card = fixture.review_context_card("CARD-ENTRY-INVALID-ENUM")
    invalid_payload = fixture.model_payload()
    invalid_payload["integrated_trade_advisory"] = fixture.integrated_trade_advisory(
        "WAIT_FOR_CONFIRMATION"
    )
    invalid_payload["integrated_trade_advisory"]["source_alignment"] = (
        "NOT_A_REAL_ALIGNMENT"
    )
    expect_value_error(
        lambda: entry.build_llm_review(invalid_card, invalid_payload),
        "invalid integrated_trade_advisory.source_alignment",
        "unknown source_alignment enums must remain fail-closed",
    )

    language_card = fixture.review_context_card("CARD-ENTRY-PROHIBITIVE-LANGUAGE")
    language_payload = fixture.model_payload()
    language_payload["integrated_trade_advisory"] = fixture.integrated_trade_advisory(
        "WAIT_FOR_CONFIRMATION"
    )
    language_payload["integrated_trade_advisory"]["final_conclusion_cn"] = (
        "当前结论不构成开仓依据，也不建议下单。"
    )
    language_payload["integrated_trade_advisory"][
        "premium_selling_fit"
    ]["basis_cn"] = "尚不具备入场条件。"
    language_payload["integrated_trade_advisory"]["invalid_if"] = [
        "若关键阻断解除，也不得直接开仓。"
    ]
    language_review = entry.build_llm_review(language_card, language_payload)
    language_advisory = language_review["integrated_trade_advisory"]
    language_policy = language_advisory["policy_validation"]
    human_text = entry.core._integrated_advisory_human_text(language_advisory)
    assert_true(
        language_review["status"] == "OK",
        "explicitly prohibitive execution wording should be rewritten before validation",
    )
    assert_true(
        language_policy["prohibitive_execution_language_repair_applied"] is True
        and language_policy["prohibitive_execution_language_repair_count"] >= 3
        and "final_conclusion_cn"
        in language_policy["prohibitive_execution_language_repair_fields"],
        "bounded language repair should be fully traceable",
    )
    assert_true(
        not any(
            pattern.search(human_text)
            for _, pattern in entry.core.ADVISORY_EXECUTION_TEXT_PATTERNS
        ),
        "rewritten prohibitive text must no longer trigger core execution patterns",
    )
    assert_true(
        "交易执行依据" in human_text
        and "暂不进入交易复核" in human_text,
        "rewritten text should retain the original prohibitive meaning",
    )

    positive_card = fixture.review_context_card("CARD-ENTRY-POSITIVE-EXECUTION")
    positive_payload = fixture.model_payload()
    positive_payload["integrated_trade_advisory"] = fixture.integrated_trade_advisory(
        "WAIT_FOR_CONFIRMATION"
    )
    positive_payload["integrated_trade_advisory"]["final_conclusion_cn"] = (
        "建议开仓并继续观察。"
    )
    expect_value_error(
        lambda: entry.build_llm_review(positive_card, positive_payload),
        "integrated_trade_advisory contains execution parameters",
        "positive execution language must remain fail-closed",
    )

    prompt = entry.build_prompt(
        entry.core.build_review_packet(card),
        {
            "theoretical_active_view": payload["theoretical_active_view"],
            "gamma_regime_lens": payload["gamma_regime_lens"],
        },
    )
    assert_true(
        "只有 producer decision.lean" in prompt
        and "不得把 recommendation 相同" in prompt,
        "full prompt should include the exact source_alignment mapping",
    )
    assert_true(
        "即使是否定、免责声明或风险边界" in prompt
        and "不构成交易执行依据" in prompt,
        "full prompt should forbid blocked execution words in human-readable fields",
    )
    assert_true(
        "canonical_funding_semantics" in prompt
        and "|raw funding|<=0.01%（含等号）" in prompt,
        "bounded runtime prompt must retain the core canonical Funding rule",
    )

    installer = INSTALLER.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")
    assert_true(
        "gemini_signal_llm_review_entry.py" in installer,
        "installer should deploy the bounded entrypoint",
    )
    assert_true(
        'exec /usr/bin/python3 "$TOOLS_ROOT/gemini_signal_llm_review_entry.py"'
        in runner,
        "runtime wrapper should execute the bounded entrypoint",
    )

    print("signal_llm_review_entry_alignment: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_llm_review_entry_alignment: FAIL - " + str(exc))
        sys.exit(1)
