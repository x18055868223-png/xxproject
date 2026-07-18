import json
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "signal_audit"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def read(path):
    assert_true(path.exists(), "missing deploy asset " + path.name)
    return path.read_text(encoding="utf-8")


def extract_integrated_advisory_probe(self_check):
    text = self_check.replace("\r\n", "\n")
    marker = 'section "Integrated trade advisory"'
    start = text.index(marker)
    heredoc_start = text.index("<<'PY'\n", start) + len("<<'PY'\n")
    heredoc_end = text.index("\nPY\n", heredoc_start)
    return text[heredoc_start:heredoc_end]


def integrated_advisory_review(status="OK",
                               schema="signal_llm_review@1.4.0",
                               recommendation="WAIT_FOR_CONFIRMATION",
                               policy_passed=True,
                               authorization_separated=True):
    return {
        "schema": schema,
        "status": status,
        "integrated_trade_advisory": {
            "recommendation": recommendation,
            "final_conclusion_cn": "当前结论仅进入结构复核。",
            "cross_loop_rationale_cn": "接管、方向和风险回路共同支持该结论。",
            "containment_assessment": {
                "state": "INCOMPLETE",
                "basis_cn": "接管证据尚需持续确认。",
            },
            "premium_selling_fit": {
                "state": "CONDITIONAL",
                "basis_cn": "卖方结构适配仍有条件。",
            },
            "side_basis_cn": "当前不额外扩大方向判断。",
            "dominant_conflict_cn": "未见足以改写结论的新冲突。",
            "key_premises": [{
                "premise_cn": "关键接管事实仍然有效。",
                "evidence_refs": ["EV_DECISION"],
            }],
            "invalid_if": ["中性接管失效。"],
            "next_observation_cn": "继续观察接管稳定性。",
            "session_advisory": {
                "liquidity_assessment": "CAUTION",
                "warning_level": "INFO",
                "basis_cn": "时区只作流动性提醒。",
                "does_not_change_recommendation": True,
            },
            "source_alignment": "PARTIALLY_ALIGNED",
            "audit_only": True,
            "trade_authorization": False,
            "policy_validation": {
                "passed": policy_passed,
                "authorization_is_not_structure_gate": authorization_separated,
            },
        },
    }


def run_integrated_advisory_probe(self_check, review_records,
                                  materialized_review=None):
    code = extract_integrated_advisory_probe(self_check)
    card_id = "20260718T000000+0800-BTC-INTEGRATED-ADVISORY"
    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        reviews = root / "signal_llm_reviews.jsonl"
        audit_root = root / "public"
        card_dir = audit_root / "signal_cards"
        card_dir.mkdir(parents=True)
        source_card = {"identity": {"card_id": card_id}}
        source.write_text(json.dumps(source_card) + "\n", encoding="utf-8")
        reviews.write_text(
            "\n".join(json.dumps(record) for record in review_records) + "\n",
            encoding="utf-8")
        selected_review = materialized_review or review_records[-1]["llm_review"]
        materialized_card = {
            "identity": {"card_id": card_id},
            "llm_review": selected_review,
        }
        (card_dir / "latest.json").write_text(json.dumps(materialized_card),
                                              encoding="utf-8")
        (card_dir / "index.json").write_text(json.dumps({
            "cards": [{
                "card_id": card_id,
                "path": "signal_cards/latest.json",
            }],
        }), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-c", code, str(source), str(reviews), str(audit_root)],
            capture_output=True,
            text=True,
            check=False,
        )


def assert_integrated_advisory_probe_behavior(self_check):
    card_id = "20260718T000000+0800-BTC-INTEGRATED-ADVISORY"
    ok_review = integrated_advisory_review()
    ok_record = {"card_id": card_id, "llm_review": ok_review}
    ok_result = run_integrated_advisory_probe(self_check, [ok_record])
    assert_true(ok_result.returncode == 0,
                "strict integrated advisory probe should accept valid v1.4.0 OK review")

    error_review = integrated_advisory_review(status="ERROR")
    error_result = run_integrated_advisory_probe(
        self_check,
        [ok_record, {"card_id": card_id, "llm_review": error_review}],
        materialized_review=ok_review,
    )
    assert_true(error_result.returncode != 0
                and "latest matching llm_review is not OK"
                in (error_result.stdout + error_result.stderr),
                "strict probe must not pass an ERROR latest sidecar review")

    old_schema = integrated_advisory_review(schema="signal_llm_review@1.3.0")
    old_schema_result = run_integrated_advisory_probe(
        self_check, [{"card_id": card_id, "llm_review": old_schema}])
    assert_true(old_schema_result.returncode != 0
                and "schema is not signal_llm_review@1.4.0"
                in (old_schema_result.stdout + old_schema_result.stderr),
                "strict probe must reject old LLM review schemas")

    invalid_enum = integrated_advisory_review(recommendation="BUY_NOW")
    invalid_enum_result = run_integrated_advisory_probe(
        self_check, [{"card_id": card_id, "llm_review": invalid_enum}])
    assert_true(invalid_enum_result.returncode != 0
                and "recommendation invalid"
                in (invalid_enum_result.stdout + invalid_enum_result.stderr),
                "strict probe must reject unknown advisory recommendations")

    coupled_authorization = integrated_advisory_review(
        authorization_separated=False)
    coupled_result = run_integrated_advisory_probe(
        self_check,
        [{"card_id": card_id, "llm_review": coupled_authorization}],
    )
    assert_true(coupled_result.returncode != 0
                and "authorization/structure separation is not verified"
                in (coupled_result.stdout + coupled_result.stderr),
                "strict probe must require authorization/structure separation")

    blank_conclusion = integrated_advisory_review()
    blank_conclusion["integrated_trade_advisory"]["final_conclusion_cn"] = ""
    blank_result = run_integrated_advisory_probe(
        self_check, [{"card_id": card_id, "llm_review": blank_conclusion}])
    assert_true(blank_result.returncode != 0
                and "final_conclusion_cn is blank"
                in (blank_result.stdout + blank_result.stderr),
                "strict probe must reject an advisory hidden by blank human text")

    missing_alignment = integrated_advisory_review()
    missing_alignment["integrated_trade_advisory"].pop("source_alignment")
    missing_alignment_result = run_integrated_advisory_probe(
        self_check, [{"card_id": card_id, "llm_review": missing_alignment}])
    assert_true(missing_alignment_result.returncode != 0
                and "fields invalid"
                in (missing_alignment_result.stdout + missing_alignment_result.stderr),
                "strict probe must reject missing advisory contract fields")

    invalid_nested_enum = integrated_advisory_review()
    invalid_nested_enum["integrated_trade_advisory"][
        "containment_assessment"]["state"] = ""
    invalid_nested_result = run_integrated_advisory_probe(
        self_check, [{"card_id": card_id, "llm_review": invalid_nested_enum}])
    assert_true(invalid_nested_result.returncode != 0
                and "containment_assessment invalid"
                in (invalid_nested_result.stdout + invalid_nested_result.stderr),
                "strict probe must independently validate nested state enums")

    raw_enum_text = integrated_advisory_review()
    raw_enum_text["integrated_trade_advisory"][
        "final_conclusion_cn"] = "ESTABLISHED、FIT、HIGH。"
    raw_enum_result = run_integrated_advisory_probe(
        self_check, [{"card_id": card_id, "llm_review": raw_enum_text}])
    assert_true(raw_enum_result.returncode != 0
                and "human text contains raw codes"
                in (raw_enum_result.stdout + raw_enum_result.stderr),
                "strict probe must reject nested enum leakage in human text")

    lowercase_raw_enum_text = integrated_advisory_review()
    lowercase_raw_enum_text["integrated_trade_advisory"][
        "final_conclusion_cn"] = "established、fit、high、caution、info。"
    lowercase_raw_result = run_integrated_advisory_probe(
        self_check,
        [{"card_id": card_id, "llm_review": lowercase_raw_enum_text}],
    )
    assert_true(lowercase_raw_result.returncode != 0
                and "human text contains raw codes"
                in (lowercase_raw_result.stdout + lowercase_raw_result.stderr),
                "strict probe must reject lowercase nested enum leakage")

    specific_legs = integrated_advisory_review()
    specific_legs["integrated_trade_advisory"][
        "final_conclusion_cn"] = "复核 62800/62000 Put 价差。"
    specific_legs_result = run_integrated_advisory_probe(
        self_check, [{"card_id": card_id, "llm_review": specific_legs}])
    assert_true(specific_legs_result.returncode != 0
                and "contains execution parameters"
                in (specific_legs_result.stdout + specific_legs_result.stderr),
                "strict probe must reject concrete spread legs")

    materialized_mismatch = integrated_advisory_review(recommendation="NO_TRADE")
    mismatch_result = run_integrated_advisory_probe(
        self_check,
        [ok_record],
        materialized_review=materialized_mismatch,
    )
    assert_true(mismatch_result.returncode != 0
                and "did not pass through integrated_trade_advisory"
                in (mismatch_result.stdout + mismatch_result.stderr),
                "strict probe must verify materialized advisory passthrough")


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
    assert_true("INTEGRATED_ADVISORY_REQUIRED=\"${INTEGRATED_ADVISORY_REQUIRED:-0}\"" in self_check
                and "INTEGRATED_ADVISORY_REQUIRED=%s\\n" in self_check
                and "signal_llm_review@1.4.0" in self_check
                and "integrated_trade_advisory" in self_check
                and "ADVISORY_RECOMMENDATIONS" in self_check
                and "latest matching llm_review is not OK" in self_check
                and "policy_validation.passed" in self_check
                and "authorization_is_not_structure_gate" in self_check
                and "materialized_advisory_passthrough" in self_check,
                "self-check should optionally enforce strict integrated trade advisory schema")
    assert_true('EXPECTED_SIGNAL_VERSION="${EXPECTED_SIGNAL_VERSION:-1.5.6}"'
                in self_check,
                "self-check should default to the current FMZ producer version")
    assert_integrated_advisory_probe_behavior(self_check)

    print("signal_audit_deploy_llm_systemd: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_deploy_llm_systemd: FAIL - " + str(exc))
        sys.exit(1)
