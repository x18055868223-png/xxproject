import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "signal_audit"
CANARY = ROOT / "tools" / "signal_llm_review_canary_release.sh"
EXPECTED_LLM_PROVIDER = "deepseek"
EXPECTED_LLM_MODEL = "deepseek-v4-flash"
EXPECTED_LLM_SCHEMA = "signal_llm_review@1.5.0"
EXPECTED_LLM_PROMPT = "signal_llm_review_prompt@1.5.5"
EXPECTED_LLM_MODE = "two_call_strict"
EXPECTED_LLM_CALL_COUNT = 2
EXPECTED_TRANSITION_SCHEMA = "signal_transition_llm_review@1.3.0"
EXPECTED_TRANSITION_PROMPT = "signal_transition_llm_review_prompt@1.3.2"


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


def extract_function_python(script, function_name):
    text = script.replace("\r\n", "\n")
    start = text.index(function_name + "() {")
    heredoc_start = text.index("<<'PY'\n", start) + len("<<'PY'\n")
    heredoc_end = text.index("\nPY\n", heredoc_start)
    return text[heredoc_start:heredoc_end]


def assert_canary_seed_behavior(canary):
    code = extract_function_python(canary, "seed_canary_main_reviews")
    target = "target-card"
    rows = [
        {"card_id": target, "llm_review": {"status": "ERROR", "validated_blind_context": {"ok": True}}},
        {"card_id": target, "llm_review": {"status": "OK", "prompt_version": "old"}},
        {"card_id": "other-card", "llm_review": {"status": "OK"}},
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "production.jsonl"
        output = root / "canary.jsonl"
        source.write_text(
            "".join(json.dumps(row) + "\n" for row in rows) + "not-json\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [sys.executable, "-", str(source), str(output), target],
            input=code,
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(completed.returncode == 0, "canary seed Python should execute")
        seeded = output.read_text(encoding="utf-8").splitlines()
        parsed = []
        for line in seeded:
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        target_rows = [row for row in parsed if row.get("card_id") == target]
        assert_true(
            len(target_rows) == 1
            and target_rows[0]["llm_review"]["status"] == "ERROR"
            and any(row.get("card_id") == "other-card" for row in parsed)
            and "not-json" in seeded
            and "CANARY_SEED_REMOVED_TARGET_OK=1" in completed.stdout,
            "canary seed must retain recovery history and unrelated rows but remove stale target OK",
        )


def integrated_advisory_review(status="OK",
                               schema=EXPECTED_LLM_SCHEMA,
                               recommendation="WAIT_FOR_CONFIRMATION",
                               policy_passed=True,
                               authorization_separated=True):
    return {
        "schema": schema,
        "status": status,
        "provider": EXPECTED_LLM_PROVIDER,
        "model": EXPECTED_LLM_MODEL,
        "prompt_version": EXPECTED_LLM_PROMPT,
        "blind_review_mode": EXPECTED_LLM_MODE,
        "llm_call_count": EXPECTED_LLM_CALL_COUNT,
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
            "future_24h_bayesian_report": {
                "schema_version": "future_24h_bayesian_report@1.0.0",
                "horizon_hours": 24,
                "input_scope": "PACKET_FACTS_PLUS_MODEL_PRIOR_NO_LIVE_SEARCH",
                "live_external_data_used": False,
                "base_case": "RANGE",
                "posterior_weights_pct": {"up": 30, "down": 30, "range": 40},
                "report_cn": "包内事实支持区间基准情景，继续观察反证与失效条件。",
                "key_levels": [],
                "counter_evidence_cn": ["方向证据仍可能增强。"],
                "invalid_if_cn": ["包内方向证据显著改变。"],
                "policy_validation": {"passed": True},
            },
            "policy_validation": {
                "passed": policy_passed,
                "authorization_is_not_structure_gate": authorization_separated,
            },
        },
    }


def run_integrated_advisory_probe(self_check, review_records,
                                  materialized_review=None,
                                  historical_source_corruption=False,
                                  corrupt_source_tail=False,
                                  historical_review_corruption=False,
                                  corrupt_review_tail=False,
                                  source_card_ids=None,
                                  manifest_card_ids=None,
                                  target_card_id=""):
    code = extract_integrated_advisory_probe(self_check)
    card_id = "20260718T000000+0800-BTC-INTEGRATED-ADVISORY"
    source_card_ids = source_card_ids or [card_id]
    manifest_card_ids = manifest_card_ids or [card_id]
    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        reviews = root / "signal_llm_reviews.jsonl"
        audit_root = root / "public"
        card_dir = audit_root / "signal_cards"
        card_dir.mkdir(parents=True)
        source_lines = []
        if historical_source_corruption:
            source_lines.append('{"historical": broken json')
        for source_id in source_card_ids:
            source_lines.append(json.dumps({"identity": {"card_id": source_id}}))
        if corrupt_source_tail:
            source_lines.append('{"latest": broken json')
        source.write_text("\n".join(source_lines) + "\n", encoding="utf-8")
        review_lines = []
        if historical_review_corruption:
            review_lines.append('{"historical": broken review')
        review_lines.extend(json.dumps(record) for record in review_records)
        if corrupt_review_tail:
            review_lines.append('{"latest": broken review')
        reviews.write_text("\n".join(review_lines) + "\n", encoding="utf-8")
        selected_review = materialized_review or review_records[-1]["llm_review"]
        manifest_cards = []
        for index, manifest_id in enumerate(manifest_card_ids):
            filename = f"card-{index}.json"
            materialized_card = {"identity": {"card_id": manifest_id}}
            if manifest_id == card_id:
                materialized_card["llm_review"] = selected_review
            (card_dir / filename).write_text(json.dumps(materialized_card),
                                             encoding="utf-8")
            manifest_cards.append({
                "card_id": manifest_id,
                "path": "signal_cards/" + filename,
            })
        (card_dir / "index.json").write_text(json.dumps({"cards": manifest_cards}),
                                             encoding="utf-8")
        env = None
        if target_card_id:
            env = dict(os.environ)
            env["TARGET_CARD_ID"] = target_card_id
        return subprocess.run(
            [
                sys.executable, "-c", code, str(source), str(reviews),
                str(audit_root), EXPECTED_LLM_PROVIDER, EXPECTED_LLM_MODEL,
                EXPECTED_LLM_SCHEMA, EXPECTED_LLM_PROMPT, EXPECTED_LLM_MODE,
                str(EXPECTED_LLM_CALL_COUNT),
            ],
            env=env,
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

    exact_target_result = run_integrated_advisory_probe(
        self_check,
        [ok_record],
        source_card_ids=["OLDER-CARD", card_id],
        manifest_card_ids=["OLDER-CARD", card_id],
        target_card_id=card_id,
    )
    assert_true(exact_target_result.returncode == 0
                and "target_card_id: " + card_id in exact_target_result.stdout
                and "materialized_card_id: " + card_id in exact_target_result.stdout,
                "exact target probe should select the requested card instead of manifest head")

    missing_target_result = run_integrated_advisory_probe(
        self_check,
        [ok_record],
        source_card_ids=["OLDER-CARD", card_id],
        manifest_card_ids=["OLDER-CARD", card_id],
        target_card_id="MISSING-CARD",
    )
    assert_true(missing_target_result.returncode != 0
                and "target source card not found"
                in (missing_target_result.stdout + missing_target_result.stderr),
                "exact target probe must fail instead of falling back to latest")

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
                and "schema is not signal_llm_review@1.5.0"
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

    historical_corruption_result = run_integrated_advisory_probe(
        self_check,
        [ok_record],
        historical_source_corruption=True,
    )
    assert_true(historical_corruption_result.returncode == 0
                and "signal_review.jsonl_historical_skipped_lines: 1"
                in historical_corruption_result.stdout,
                "strict probe should mirror materializer tolerance for historical corruption")

    historical_review_result = run_integrated_advisory_probe(
        self_check,
        [ok_record],
        historical_review_corruption=True,
    )
    assert_true(historical_review_result.returncode == 0
                and "signal_llm_reviews.jsonl_historical_skipped_lines: 1"
                in historical_review_result.stdout,
                "strict probe should tolerate historical sidecar corruption")

    corrupt_tail_result = run_integrated_advisory_probe(
        self_check,
        [ok_record],
        corrupt_source_tail=True,
    )
    assert_true(corrupt_tail_result.returncode != 0
                and "latest non-empty line is invalid"
                in (corrupt_tail_result.stdout + corrupt_tail_result.stderr),
                "strict probe must still reject a corrupt latest source tail")

    corrupt_review_tail_result = run_integrated_advisory_probe(
        self_check,
        [ok_record],
        corrupt_review_tail=True,
    )
    assert_true(corrupt_review_tail_result.returncode != 0
                and "latest non-empty line is invalid"
                in (corrupt_review_tail_result.stdout
                    + corrupt_review_tail_result.stderr),
                "strict probe must reject a corrupt latest sidecar tail")

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
    canary = read(CANARY)

    assert_true("/etc/signal-audit/llm.env" in llm_service,
                "LLM service should load the protected server env file")
    assert_true("EnvironmentFile=-/etc/signal-audit/llm.env" in llm_service,
                "LLM service should tolerate missing env until key is configured")
    assert_true("LLM_PROVIDER=deepseek" in llm_env
                and "LLM_API_KEY=" in llm_env
                and "LLM_BASE_URL=https://api.deepseek.com" in llm_env
                and f"LLM_MODEL={EXPECTED_LLM_MODEL}" in llm_env,
                "LLM env example should document provider-neutral DeepSeek config")
    assert_true("LLM_REVIEW_LIMIT=4" in llm_env
                and "TRANSITION_REVIEW_LIMIT=4" in llm_env
                and "LLM_MAX_CONCURRENCY=4" in llm_env
                and "LLM_DAILY_HTTP_CAP=60" in llm_env,
                "LLM env example should fix review limits, concurrency, and daily cap")
    assert_true("LLM_BLIND_EFFORT=low" in llm_env
                and "LLM_RECON_EFFORT=high" in llm_env
                and "LLM_TRANSITION_EFFORT=low" in llm_env
                and "LLM_BLIND_TIMEOUT=60" in llm_env
                and "LLM_RECON_TIMEOUT=240" in llm_env
                and "LLM_TRANSITION_TIMEOUT=120" in llm_env,
                "LLM env example should document per-stage efforts and timeouts")
    for legacy_name in ("GEMINI_API_KEY=", "GEMINI_PAID_API_KEY=", "GEMINI_FALLBACK_API_KEY=",
                        "GEMINI_CHANNEL1_API_KEY=", "GEMINI_CHANNEL2_API_KEY="):
        assert_true(legacy_name not in llm_env,
                    "LLM env example should not expose legacy key entry " + legacy_name)
    assert_true("AIza" not in llm_env and "sk-" not in llm_env,
                "LLM env example must not contain a real-looking key")
    assert_true("run_signal_llm_review.sh" in llm_service,
                "LLM service should call the guarded runner")
    assert_true("--reviews-output" in runner,
                "LLM runner should write sidecar reviews")
    assert_true("ONLY_CARD_ID" in runner
                and "ONLY_CARD_ID not found in source JSONL" in runner
                and "RUN_LLM_REVIEW_LIMIT=1" in runner
                and "RUN_TRANSITION_REVIEW_LIMIT=1" in runner
                and 'entry_args+=(--only-card-id "$ONLY_CARD_ID")' in runner,
                "LLM runner should enforce exact-card review mode when ONLY_CARD_ID is set")
    assert_true("LLM_USAGE_LEDGER" in runner
                and '--usage-ledger "$LLM_USAGE_LEDGER"' in runner
                and 'entry_args+=(--retry-id "$RETRY_ID")' in runner,
                "LLM runner should forward usage-ledger and explicit recovery identity")
    assert_true("LLM_API_KEY is not configured" in runner,
                "LLM runner should skip cleanly before the provider-neutral key is configured")
    assert_true("flock -n" in runner
                and "run_signal_llm_review.lock" in runner,
                "LLM runner should use a non-blocking flock guard")
    assert_true("GEMINI_" not in runner,
                "LLM runner should not read any Gemini environment variables")
    assert_true('exec /usr/bin/python3 "$TOOLS_ROOT/signal_llm_review_entry.py"' in runner
                and "--provider" in runner
                and "--base-url" in runner
                and "--concurrency" in runner
                and "--daily-cap" in runner
                and "--blind-mode" not in runner
                and "--recon-effort" in runner
                and "--transition-timeout" in runner,
                "LLM runner should invoke the provider-neutral entrypoint with DeepSeek controls")
    assert_true("LLM_REVIEWS_SOURCE" in llm_service,
                "LLM service should use a stable sidecar path")
    assert_true("signal-audit-materialize.service" in llm_service,
                "LLM service should refresh materialized cards after reviews")
    assert_true("ExecStartPre=/bin/systemctl start signal-audit-materialize.service" in llm_service,
                "LLM service should materialize before review so transition ledger is current")
    assert_true("ExecStopPost=/bin/systemctl start signal-audit-materialize.service" in llm_service,
                "LLM service must materialize after both successful and failed review runs")
    assert_true("ExecStartPost=" not in llm_service,
                "success-only post materialization would delay ERROR sidecar visibility")
    assert_true('--api-key "$LLM_API_KEY"' not in runner,
                "runner must not expose LLM_API_KEY through process arguments")
    assert_true("MemoryMax=256M" in llm_service,
                "LLM service should be capped for a 1GB server")
    assert_true("TimeoutStartSec=900" in llm_service,
                "LLM service timeout must cover the full-output stage budgets and materialization")
    service_timeout = int(re.search(r"TimeoutStartSec=(\d+)", llm_service).group(1))
    stage_budget = 60 + 240 + 120
    materialize_timeout = int(re.search(
        r"TimeoutStartSec=(\d+)", materialize_service).group(1))
    assert_true(service_timeout >= materialize_timeout + stage_budget + 120,
                "LLM service timeout must cover pre-materialization, stage budgets, and slack")
    assert_true("OnUnitInactiveSec=60" in llm_timer,
                "LLM timer should wait 60 seconds after the prior run completes")
    assert_true("SCRIPT_DIR=" in install and "DEPLOY_SRC=" in install,
                "install script should support both git and zip package layouts")
    assert_true('ENABLE_SIGNAL_AUDIT_TIMERS="${ENABLE_SIGNAL_AUDIT_TIMERS:-${ENABLE_TIMERS:-0}}"' in install
                and 'START_SIGNAL_AUDIT_TIMERS="${START_SIGNAL_AUDIT_TIMERS:-${START_TIMERS:-0}}"' in install
                and 'RUN_INITIAL_LLM_REVIEW="${RUN_INITIAL_LLM_REVIEW:-0}"' in install
                and 'RUN_INITIAL_MATERIALIZE="${RUN_INITIAL_MATERIALIZE:-0}"' in install
                and "safe default: timers installed but not enabled" in install
                and "safe default: skipped initial LLM review" in install,
                "install script should default to install plus daemon-reload only")
    assert_true("systemctl enable --now signal-audit-materialize.timer" not in install
                and "systemctl enable --now signal-audit-llm-review.timer" not in install,
                "install script must not enable/start timers by default")
    assert_true("signal_llm_review_canary_release.sh" in install
                and "server_self_check_signal_stack.sh" in install,
                "install script should deploy the canary and self-check helpers")
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
    assert_true(materialize_timeout == 300,
                "materializer service timeout should match the server-applied 300 second cap")
    assert_true("--mode" in runner and "both" in runner
                and "TRANSITION_LEDGER_SOURCE" in runner
                and "TRANSITION_LLM_REVIEWS_SOURCE" in runner,
                "LLM runner should invoke card and transition review modes together")
    assert_true("LLM_BLIND_MODE" not in llm_env
                and "Environment=LLM_BLIND_MODE" not in llm_service
                and "--blind-mode" not in runner
                and "TRANSITION_BLIND_MODE=single_call_evidence_first" in llm_env
                and "--transition-blind-mode" in runner,
                "main card reviews should not be forced to single-call while transition stays single-call")
    assert_true("TRANSITION_REQUIRED" in self_check
                and "TRANSITION_LLM_REQUIRED" in self_check
                and "transition_context" in self_check
                and "no_trading_instruction" in self_check,
                "server self-check should validate transition context and transition LLM guard")
    assert_true("TARGET_CARD_ID" in self_check
                and "--target-card-id" in self_check
                and "target audit card not found in manifest" in self_check
                and "target source card not found" in self_check
                and "SYSTEMD_REQUIRED=0; skipped systemd service and timer checks" in self_check
                and "AUDIT_HTTP_REQUIRED=0; skipped audit HTTP checks" in self_check,
                "server self-check should support exact target canary mode without latest fallback")
    assert_true("latest non-empty signal_review.jsonl line is invalid" in self_check
                and "latest non-empty LLM review sidecar line is invalid" in self_check
                and "except json.JSONDecodeError" in self_check,
                "exact-target LLM check should skip historical corrupt lines but reject a corrupt tail")
    assert_true('CHECK_LLM_REVIEWS_SOURCE="$LLM_REVIEWS_SOURCE"' in self_check
                and 'LLM_REVIEWS_SOURCE="$CHECK_LLM_REVIEWS_SOURCE"' in self_check
                and 'TRANSITION_REQUIRED="$CHECK_TRANSITION_REQUIRED"' in self_check,
                "env files must not redirect explicit canary paths or weaken strict gates")
    assert_true("started signal-audit-materialize.service before LLM" in self_check
                and "started signal-audit-materialize.service after LLM" in self_check,
                "server self-check active mode should materialize before and after LLM")
    transition_units = list(DEPLOY.glob("signal-transition-*"))
    assert_true(not transition_units,
                "T0/T1 must reuse existing services, not add signal-transition units")
    assert_true("signal-audit-llm-review.service" in package
                and "signal-audit-llm-review.timer" in package
                and "signal-audit-llm.env.example" in package
                and "signal_fact_semantics.py" in package,
                "package script should include LLM systemd assets")
    assert_true("signal_llm_review.py" in package
                and "signal_llm_review_entry.py" in package
                and "signal_llm_review_canary_release.sh" in package
                and "server_self_check_signal_stack.sh" in package
                and "gemini_signal_llm_review.py" not in package
                and "gemini_signal_llm_review_entry.py" not in package,
                "package script should include provider-neutral LLM tools")
    assert_true("signal_fact_semantics.py" in install,
                "install script should deploy deterministic fact semantics")
    assert_true("signal_llm_review.py" in install
                and "signal_llm_review_entry.py" in install
                and "LLM_API_KEY before expecting reviews" in install,
                "install script should deploy provider-neutral LLM tools and env")
    assert_true("GEX_REQUIRED" in self_check
                and "skipped gexmonitorapi.service check" in self_check
                and "skipped GEX Monitor API active checks" in self_check,
                "self-check should support signal-audit-only hosts without GEX")
    assert_true("LLM provider matches expected provider" in self_check
                and "LLM model matches expected model" in self_check
                and "LLM API key is configured in environment" in self_check
                and "api_key_route" in self_check,
                "self-check should expose provider-neutral key readiness and route")
    assert_true("LLM_REQUIRED" in self_check
                and "latest signal card has OK provider-neutral strict two-call LLM sidecar review" in self_check
                and "blind_review_mode" in self_check
                and "llm_call_count" in self_check,
                "self-check should prove latest card has a strict two-call LLM review when required")
    assert_true("INTEGRATED_ADVISORY_REQUIRED=\"${INTEGRATED_ADVISORY_REQUIRED:-0}\"" in self_check
                and "INTEGRATED_ADVISORY_REQUIRED=%s\\n" in self_check
                and EXPECTED_LLM_SCHEMA in self_check
                and EXPECTED_LLM_PROMPT in self_check
                and EXPECTED_LLM_PROVIDER in self_check
                and EXPECTED_LLM_MODEL in self_check
                and EXPECTED_LLM_MODE in self_check
                and f'EXPECTED_LLM_CALL_COUNT="${{EXPECTED_LLM_CALL_COUNT:-{EXPECTED_LLM_CALL_COUNT}}}"' in self_check
                and "integrated_trade_advisory" in self_check
                and "ADVISORY_RECOMMENDATIONS" in self_check
                and "latest matching llm_review is not OK" in self_check
                and "policy_validation.passed" in self_check
                and "authorization_is_not_structure_gate" in self_check
                and "materialized_advisory_passthrough" in self_check,
                "self-check should optionally enforce strict integrated trade advisory schema")
    assert_true('EXPECTED_SIGNAL_VERSION="${EXPECTED_SIGNAL_VERSION:-1.5.7}"'
                in self_check,
                "self-check should default to the current FMZ producer version")
    assert_true(
        f'EXPECTED_LLM_PROMPT_VERSION="${{EXPECTED_LLM_PROMPT_VERSION:-{EXPECTED_LLM_PROMPT}}}"'
        in self_check
        and "prompt version does not match bounded entrypoint" in self_check,
        "self-check should require the current bounded runtime prompt",
    )
    assert_true(EXPECTED_TRANSITION_SCHEMA in self_check
                and EXPECTED_TRANSITION_PROMPT in self_check
                and "latest transition has OK provider-neutral single-call LLM review" in self_check,
                "self-check should require the current provider-neutral transition review contract")
    assert_integrated_advisory_probe_behavior(self_check)
    assert_canary_seed_behavior(canary)

    assert_true("--target-card-id" in canary
                and "--promote" in canary
                and "PROMOTE=0" in canary
                and "PROMOTION_STATUS=SKIPPED" in canary
                and "STATUS=PASS" in canary
                and "STATUS=FAIL" in canary
                and "ROLLBACK_COMMAND=" in canary
                and "ROLLBACK_BACKUP" in canary
                and "ONLY_CARD_ID=\"$TARGET_CARD_ID\"" in canary
                and "LLM_USAGE_LEDGER=\"$LLM_USAGE_LEDGER\"" in canary
                and "TARGET_CARD_ID=\"$TARGET_CARD_ID\"" in canary
                and "SYSTEMD_REQUIRED=0" in canary
                and "AUDIT_HTTP_REQUIRED=0" in canary,
                "canary release helper should be exact-target, isolated, and rollback-reporting")
    assert_true(
        "export TARGET_CARD_ID" in self_check
        and 'TARGET_CARD_ID="${2:-}"' in self_check
        and 'CHECK_TARGET_CARD_ID="$TARGET_CARD_ID"' in self_check
        and 'TARGET_CARD_ID="$CHECK_TARGET_CARD_ID"' in self_check,
        "self-check CLI target must be exported to every embedded Python probe",
    )
    assert_true("COMMIT_SHA=" in canary
                and "LLM_PROVIDER=" in canary
                and "LLM_MODEL=" in canary
                and "LLM_SCHEMA=" in canary
                and "LLM_PROMPT_VERSION=" in canary
                and "UNIT_${unit//[^A-Za-z0-9]/_}_SHA256=" in canary
                and 'LLM_USAGE_LEDGER="$LLM_USAGE_LEDGER"' in canary
                and 'cp -a "$LLM_USAGE_LEDGER" "$CANARY_USAGE_LEDGER"' in canary
                and "merged = can or prod" in canary
                and "validate_backup_scope" in canary
                and "validate_static_root_scope" in canary
                and "PROMOTION_STARTED=1" in canary
                and "AUTO_ROLLBACK_STATUS=COMPLETE" in canary
                and 'install_file_atomic "$source/index.json"' in canary
                and 'install_signal_cards_consistent "$CANARY_STATIC_ROOT/signal_cards" "$STATIC_ROOT/signal_cards"' in canary
                and '"fallback:$STATIC_ROOT/fallback.js"' not in canary
                and 'install_file_atomic "$CANARY_STATIC_ROOT/fallback.js"' not in canary
                and "is_recoverable_reconciliation_empty_content" in canary
                and 'run_isolated_review "$TARGET_CARD_ID"' in canary
                and 'seed_canary_main_reviews "$LLM_REVIEWS_SOURCE" "$CANARY_RUN_REVIEWS"' in canary
                and 'cp -a "$TRANSITION_LLM_REVIEWS_SOURCE" "$CANARY_RUN_TRANSITION_REVIEWS"' in canary
                and 'run_isolated_review ""' not in canary
                and "RECOVERY_STATUS=START_RECONCILIATION_ONLY" in canary,
                "canary must preserve history, target a new retry epoch, report release identity, and preserve usage")
    assert_true("--resume-canary-root" in canary
                and "/tmp/signal-llm-canary.*" in canary
                and "RESUME_STATUS=REUSE_COMPLETED_HTTP_RESULTS" in canary
                and 'require_file "$CANARY_RUN_REVIEWS"' in canary,
                "canary should resume scoped completed HTTP artifacts without another API call")
    assert_true("canonical canary LLM review is not OK" in canary
                and "MERGED_LLM_TARGET_ROWS=1" in canary
                and "canonical target review replacement verification failed" in canary,
                "canary promotion must canonicalize the exact target to one verified OK row")
    assert_true('"usage_ledger:$LLM_USAGE_LEDGER"' not in canary
                and 'install_file_atomic "$CANARY_MERGED_USAGE_LEDGER" "$LLM_USAGE_LEDGER"' not in canary,
                "real HTTP usage must never be rolled back with review/static promotion")
    assert_true("systemctl enable --now signal-audit-materialize.timer" in canary
                and "TIMER_STATUS=ENABLED_AFTER_PASS" in canary,
                "canary helper may enable timers only after validation pass")

    print("signal_audit_deploy_llm_systemd: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_deploy_llm_systemd: FAIL - " + str(exc))
        sys.exit(1)
