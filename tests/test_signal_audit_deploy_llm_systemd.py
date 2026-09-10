import copy
import json
import pathlib
import re
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import signal_review_v2  # noqa: E402

DEPLOY = ROOT / "deploy" / "signal_audit"
CANARY = ROOT / "tools" / "signal_llm_review_canary_release.sh"
EXPECTED_LLM_PROVIDER = "deepseek"
EXPECTED_LLM_MODEL = "deepseek-v4-flash"
EXPECTED_LLM_SCHEMA = "signal_llm_review@2.1.0"
EXPECTED_LLM_PROMPT = "signal_llm_review_prompt@2.1.0"
EXPECTED_LLM_MODE = "single_evidence_v2"


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def read(path):
    assert_true(path.exists(), "missing deploy asset " + path.name)
    return path.read_text(encoding="utf-8")


def extract_first_integrated_probe(self_check):
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


def v2_card(card_id="card-v2"):
    return {
        "identity": {
            "card_id": card_id,
            "symbol": "BTCUSDT",
            "strategy_version": "fixture-v2",
            "as_of_ms": 1788753590501,
        },
        "decision_matrix": {"direction": "NEUTRAL"},
    }


def v2_packet(card):
    identity = card["identity"]
    return {
        "schema": "signal_evidence_packet@2.0.0",
        "identity": {
            "card_id": identity["card_id"],
            "symbol": identity["symbol"],
            "strategy_version": identity["strategy_version"],
            "as_of_ms": identity["as_of_ms"],
            "source_record_hash": None,
        },
        "facts": [
        {
            "id": "EV_SPACE",
            "topic": "structure",
            "label_cn": "空间位置",
            "value": {"distance_pct": 0.21, "float_identity": 1.0},
            "unit": "%",
            "source_refs": ["价格锚"],
            "source_group": "OPTIONS",
            "observed_at_ms": 1788753590501,
            "window": "current",
            "usable": True,
            "summary_cn": "现价仍在有效锚带附近，结构约束可以参与判断。",
            "limitations_cn": "只说明空间位置，不代表具体报价值得成交。",
            "dependencies": [],
        },
        {
            "id": "EV_FLOW",
            "topic": "pressure_response",
            "label_cn": "主动成交与价格响应",
            "value": "传导未失控",
            "unit": "",
            "source_refs": ["主动买卖流"],
            "source_group": "PRICE_FLOW",
            "observed_at_ms": 1788753590501,
            "window": "4h",
            "usable": True,
            "summary_cn": "主动流和价格响应没有形成单侧突破证据。",
            "limitations_cn": "不把吸收视为隐藏订单观测。",
            "dependencies": ["EV_SPACE"],
        },
        ],
        "limitations_cn": ["本夹具只用于 v2 self-check hash 与摘要一致性。"],
    }


def v2_payload():
    return {
        "price_bias": {
            "bias": "NEUTRAL",
            "basis_cn": "主动流和价格响应尚未体现明确方向优势，当前价格倾向中性。",
            "counter_cn": "若单侧压力推进，价格倾向可能改变。",
            "invalid_if_cn": "价格离开结构参照且主动成交同向增强时重新判断。",
            "evidence_refs": ["EV_FLOW"],
            "counter_evidence_refs": ["EV_SPACE"],
        },
        "side_evidence_ratings": {
            "put_credit": {
                "grade": "A",
                "basis_cn": "空间约束与下方压力响应共同支持 Put 侧进入人工准备。",
                "market_counter_cn": "若主动卖压继续推进，评级需要回撤。",
                "alternative_cn": "也可能只是短时低波动整理，需要看下一窗口。",
                "next_observation_cn": "观察价格是否持续留在锚带内。",
                "invalid_if_cn": "现价跌破下侧结构位且主动卖压同步增强。",
                "evidence_refs": ["EV_SPACE", "EV_FLOW"],
                "counter_evidence_refs": [],
                "unresolved_conditions_cn": ["候选报价和净补偿尚未评估。"],
            },
            "call_credit": {
                "grade": "C",
                "basis_cn": "Call 侧可以判断，但没有明显支持优势。",
                "market_counter_cn": "上方空间约束不足以构成主动关注。",
                "alternative_cn": "若上冲失败且墙位稳定，可能重新改善。",
                "next_observation_cn": "观察上方压力是否减弱。",
                "invalid_if_cn": "上行突破并带来持续响应。",
                "evidence_refs": ["EV_SPACE"],
                "counter_evidence_refs": ["EV_FLOW"],
                "unresolved_conditions_cn": [],
            },
        },
    }


def v2_review():
    card = v2_card()
    answer = v2_payload()
    for side in answer["side_evidence_ratings"].values():
        side["mechanism_cn"] = "结构位置与压力对应的价格响应共同构成当前适配论证。"
        side["evidence_roles"] = [
            {"ref": ref, "role": role, "claim_cn": "按当前结构与压力响应解释该事实。"}
            for field, role in (("evidence_refs", "supports_fit"), ("counter_evidence_refs", "counters_fit"))
            for ref in side.pop(field)
        ]
        side["strengthen_if_cn"] = ["不利压力减弱且结构仍可解释时，适配论证增强。"]
        side["weaken_if_cn"] = [side.pop("invalid_if_cn")]
    answer["side_comparison"] = {
        "relative_side": "put_credit", "basis_cn": "本卡下方约束论证较充分，上方支持有限。",
        "evidence_refs": ["EV_SPACE", "EV_FLOW"], "flip_if_cn": "若下方约束被穿透则重新比较。",
    }
    review = signal_review_v2.build_review(
        card,
        answer,
        v2_packet(card),
        model=EXPECTED_LLM_MODEL,
        reviewed_at="2026-09-08T00:00:00+00:00",
    )
    review["llm_call_count"] = 1
    review["llm_http_calls"] = 1
    review["retry_budget"] = {"limit": 2, "used": 1, "persistent": True}
    signal_review_v2.validate_persisted_review(review)
    return review


def signal_evidence_summary(review):
    return signal_review_v2.build_summary(review, v2_card())


def run_integrated_probe(self_check, review, summary=None):
    code = extract_first_integrated_probe(self_check)
    card_id = "card-v2"
    with tempfile.TemporaryDirectory() as temp_dir:
        root = pathlib.Path(temp_dir)
        source = root / "signal_review.jsonl"
        reviews = root / "signal_llm_reviews.jsonl"
        audit_root = root / "public"
        card_dir = audit_root / "signal_cards"
        card_dir.mkdir(parents=True)
        source_card = v2_card(card_id)
        source.write_text(json.dumps(source_card) + "\n", encoding="utf-8")
        reviews.write_text(json.dumps({"card_id": card_id, "llm_review": review})
                           + "\n", encoding="utf-8")
        card = copy.deepcopy(source_card)
        card["llm_review"] = review
        (card_dir / "card.json").write_text(json.dumps(card), encoding="utf-8")
        manifest = {
            "cards": [{
                "card_id": card_id,
                "path": "signal_cards/card.json",
                "summary": {
                    "signal_evidence_summary": (
                        summary if summary is not None
                        else signal_evidence_summary(review)
                    ),
                },
            }],
        }
        (card_dir / "index.json").write_text(json.dumps(manifest),
                                             encoding="utf-8")
        return subprocess.run(
            [
                sys.executable, "-", str(source), str(reviews), str(audit_root),
                str(ROOT / "tools"),
                EXPECTED_LLM_PROVIDER, EXPECTED_LLM_MODEL, EXPECTED_LLM_SCHEMA,
                EXPECTED_LLM_PROMPT, EXPECTED_LLM_MODE, "1", "2",
            ],
            input=code,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )


def assert_canary_seed_behavior(canary):
    code = extract_function_python(canary, "seed_canary_main_reviews")
    target = "target-card"
    rows = [
        {"card_id": target, "llm_review": {"status": "ERROR"}},
        {"card_id": target, "llm_review": {"status": "OK"}},
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
        assert_true(completed.returncode == 0,
                    "canary seed Python should execute")
        seeded = output.read_text(encoding="utf-8").splitlines()
        parsed = []
        for line in seeded:
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        assert_true(
            not [row for row in parsed if row.get("card_id") == target]
            and any(row.get("card_id") == "other-card" for row in parsed)
            and "not-json" in seeded
            and "CANARY_SEED_REMOVED_TARGET_ROWS=2" in completed.stdout,
            "canary seed must remove all target rows and retain unrelated history",
        )


def assert_integrated_probe_behavior(self_check):
    ok_review = v2_review()
    ok_result = run_integrated_probe(self_check, ok_review)
    assert_true(ok_result.returncode == 0,
                "v2 integrated probe should accept a valid review: "
                + (ok_result.stderr or ok_result.stdout))
    assert_true("latest_advisory_assessment_hash: sha256:" in ok_result.stdout
                and "materialized_advisory_signal_evidence_summary_hash: sha256:"
                in ok_result.stdout,
                "v2 integrated probe should execute the official prefixed hash checks")

    legacy_review = copy.deepcopy(ok_review)
    legacy_review["integrated_trade_advisory"]["future_24h_bayesian_report"] = {}
    legacy_result = run_integrated_probe(
        self_check,
        legacy_review,
        summary=signal_evidence_summary(ok_review),
    )
    assert_true(legacy_result.returncode != 0
                and "legacy fields" in (legacy_result.stdout + legacy_result.stderr),
                "v2 integrated probe must reject legacy advisory fields")

    bad_summary = signal_evidence_summary(ok_review)
    bad_summary["assessment_hash"] = "wrong"
    summary_result = run_integrated_probe(self_check, ok_review, summary=bad_summary)
    assert_true(summary_result.returncode != 0
                and "assessment hash mismatch" in (
                    summary_result.stdout + summary_result.stderr),
                "v2 integrated probe must bind manifest summary to advisory hash")


def main():
    materialize_service = read(DEPLOY / "signal-audit-materialize.service")
    llm_service = read(DEPLOY / "signal-audit-llm-review.service")
    llm_timer = read(DEPLOY / "signal-audit-llm-review.timer")
    llm_env = read(DEPLOY / "signal-audit-llm.env.example")
    runner = read(DEPLOY / "run_signal_llm_review.sh")
    install = read(DEPLOY / "install_or_update.sh")
    package = read(DEPLOY / "package_signal_audit.ps1")
    self_check = read(ROOT / "tools" / "server_self_check_signal_stack.sh")
    canary = read(CANARY)

    assert_true("EnvironmentFile=-/etc/signal-audit/llm.env" in llm_service,
                "LLM service should tolerate missing env until key is configured")
    assert_true("LLM_PROVIDER=deepseek" in llm_env
                and "LLM_API_KEY=" in llm_env
                and "LLM_BASE_URL=https://api.deepseek.com" in llm_env
                and f"LLM_MODEL={EXPECTED_LLM_MODEL}" in llm_env,
                "LLM env example should document provider-neutral DeepSeek config")
    assert_true("LLM_REVIEW_LIMIT=4" in llm_env
                and "LLM_MAX_CONCURRENCY=4" in llm_env
                and "LLM_DAILY_HTTP_CAP=60" in llm_env,
                "LLM env example should fix review limits, concurrency, and daily cap")
    for removed in (
            "TRANSITION_REVIEW_LIMIT",
            "TRANSITION_BLIND_MODE",
            "LLM_BLIND_EFFORT",
            "LLM_TRANSITION_EFFORT",
            "LLM_BLIND_TIMEOUT",
            "LLM_TRANSITION_TIMEOUT"):
        assert_true(removed not in llm_env,
                    "LLM env example should not carry removed stage variable " + removed)
    assert_true("GEMINI_" not in llm_env and "AIza" not in llm_env and "sk-" not in llm_env,
                "LLM env example must not expose legacy or real-looking keys")

    assert_true("--mode card" in runner
                and "--review-mode single_evidence_v2" in runner
                and "--transition-ledger" in runner
                and "--transition-reviews-output" not in runner
                and "--transition-limit" not in runner
                and "--blind-timeout" not in runner
                and "--transition-timeout" not in runner
                and 'entry_args+=(--retry-id "$RETRY_ID")' not in runner,
                "runner should call only the v2 card workflow")
    assert_true("ONLY_CARD_ID target verified in full source" in runner
                and "mktemp" not in runner
                and 'entry_args+=(--only-card-id "$ONLY_CARD_ID")' in runner,
                "ONLY_CARD_ID should preserve full source context for v2")
    assert_true("flock -n" in runner and "run_signal_llm_review.lock" in runner,
                "runner should keep the non-blocking flock guard")
    assert_true('entry_args+=(--automatic-exclusions "$LLM_AUTOMATIC_EXCLUSIONS")' in runner,
                "runner should pass the configured persistent historical exclusions")
    assert_true("LLM_API_KEY is not configured" in runner,
                "runner should skip cleanly before the key is configured")

    assert_true("TRANSITION_REVIEW_LIMIT" not in llm_service
                and "LLM_BLIND_EFFORT" not in llm_service
                and "LLM_TRANSITION_EFFORT" not in llm_service
                and "LLM_TRANSITION_TIMEOUT" not in llm_service,
                "LLM service should not advertise removed automatic stages")
    assert_true("ExecStartPre=/bin/systemctl start signal-audit-materialize.service" in llm_service
                and "ExecStopPost=/bin/systemctl start signal-audit-materialize.service" in llm_service,
                "LLM service should refresh materialized cards before and after review")
    assert_true("OnUnitInactiveSec=60" in llm_timer,
                "LLM timer should wait 60 seconds after the prior run completes")
    service_timeout = int(re.search(r"TimeoutStartSec=(\d+)", llm_service).group(1))
    materialize_timeout = int(re.search(
        r"TimeoutStartSec=(\d+)", materialize_service).group(1))
    assert_true(service_timeout >= materialize_timeout + 240 + 120,
                "LLM service timeout should cover one high-reasoning call and materialization")

    assert_true('EXPECTED_LLM_SCHEMA="${EXPECTED_LLM_SCHEMA:-signal_llm_review@2.1.0}"'
                in self_check
                and 'EXPECTED_LLM_PROMPT_VERSION="${EXPECTED_LLM_PROMPT_VERSION:-signal_llm_review_prompt@2.1.0}"'
                in self_check
                and 'EXPECTED_LLM_REVIEW_MODE="${EXPECTED_LLM_REVIEW_MODE:-single_evidence_v2}"'
                in self_check
                and 'EXPECTED_LLM_MAX_HTTP_ATTEMPTS="${EXPECTED_LLM_MAX_HTTP_ATTEMPTS:-2}"'
                in self_check
                and 'INTEGRATED_ADVISORY_PROTOCOL="${INTEGRATED_ADVISORY_PROTOCOL:-v2}"'
                in self_check
                and '[ "$INTEGRATED_ADVISORY_PROTOCOL" = "legacy" ]'
                in self_check,
                "self-check should default to the v2 single evidence protocol")
    first_probe = extract_first_integrated_probe(self_check)
    assert_true("validate_persisted_review" in first_probe
                and "revalidate_review" in first_probe
                and "build_summary" in first_probe
                and "side_comfort_ratings" in first_probe
                and "future_24h_bayesian_report" in first_probe
                and "canonical_hash" not in first_probe
                and "hashlib" not in first_probe,
                "v2 probe should reuse official v2 validation and reject legacy fields")
    assert_true("latest signal card has provider-neutral v2 single evidence LLM sidecar review"
                in self_check
                and "latest signal card has v2 integrated_trade_advisory" in self_check,
                "self-check should report v2 sidecar and advisory success")
    assert_integrated_probe_behavior(self_check)

    assert_true("require_file \"$TOOLS_ROOT/signal_evidence_v2.py\"" in canary
                and "require_file \"$TOOLS_ROOT/signal_review_v2.py\"" in canary
                and "require_file \"$TOOLS_ROOT/signal_review_v2_runtime.py\"" in canary,
                "canary should require the v2 review modules")
    assert_true("RECOVERY_STATUS=OWNED_BY_V2_PERSISTENT_BUDGET" in canary
                and "is_recoverable_reconciliation_empty_content" not in canary
                and "RETRY_ID=" not in canary
                and 'run_isolated_review "$TARGET_CARD_ID"' not in canary,
                "canary should not reset v2 retry budget outside the runtime")
    assert_true("LLM_SCHEMA=signal_llm_review@2.1.0" in canary
                and "LLM_PROMPT_VERSION=signal_llm_review_prompt@2.1.0" in canary
                and "LLM_REVIEW_MODE=single_evidence_v2" in canary
                and "EXPECTED_LLM_MAX_HTTP_ATTEMPTS=2" in canary,
                "canary should bind isolated self-check to v2")
    assert_canary_seed_behavior(canary)
    for module in (
            "signal_evidence_v2.py",
            "signal_review_v2.py",
            "signal_review_v2_runtime.py"):
        assert_true(module in install,
                    "install script should deploy " + module)
        assert_true(module in package,
                    "package script should include " + module)

    print("signal_audit_deploy_llm_systemd: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_audit_deploy_llm_systemd: FAIL - " + str(exc))
        sys.exit(1)
