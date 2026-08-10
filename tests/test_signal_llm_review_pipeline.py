import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "signal_llm_review.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("signal_llm_review_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


def valid_future(price=101500):
    return {
        "schema_version": "future_24h_bayesian_report@1.0.0",
        "horizon_hours": 24,
        "input_scope": "PACKET_FACTS_PLUS_MODEL_PRIOR_NO_LIVE_SEARCH",
        "live_external_data_used": False,
        "base_case": "UP",
        "posterior_weights_pct": {"up": 52, "down": 18, "range": 30},
        "report_cn": (
            "基准情景偏上，主观情景权重为上涨52%、下跌18%、区间30%；"
            "关键观察位为卡内101500，重要反证是宏观压力增强，若方向证据转弱则失效。"
        ),
        "key_levels": [{
            "price": price,
            "role_cn": "卡内现价观察位",
            "source_type": "PACKET_OBSERVED",
            "basis_cn": "精确来自卡内现价。",
        }, {
            "price": 104800,
            "role_cn": "上方主观观察位",
            "source_type": "MODEL_ESTIMATED",
            "basis_cn": "模型估算观察位，由卡内现价与结构距离推导。",
        }],
        "counter_evidence_cn": ["宏观压力可能继续增强。"],
        "invalid_if_cn": ["方向证据转弱且跌破卡内观察位。"],
    }


def minimal_card(card_id, ts_ms):
    return {
        "identity": {
            "card_id": card_id,
            "symbol": "BTC",
            "confirmed_time_ms": ts_ms,
            "confirmed_at": f"2026-08-09T00:00:0{ts_ms}+00:00",
        },
        "market_context": {"price": 101500 + ts_ms, "quote_currency": "USDT"},
        "decision": {"lean": "UP", "trade_allowed": False},
        "decision_matrix": {"execution_allowed": False},
        "quality": {"overall": "OK"},
    }


def minimal_transition(transition_id, ts_ms):
    return {
        "transition_id": transition_id,
        "current_card_id": f"C-{transition_id}",
        "previous_card_id": f"P-{transition_id}",
        "symbol": "BTC",
        "llm_review_required": True,
        "current_ts_ms": ts_ms,
    }


def test_request_contract(tool):
    blind = tool.build_blind_chat_request("blind")
    recon = tool.build_chat_request("recon")
    recovery_recon = tool.build_chat_request(
        "recovery-recon", empty_content_retry_count=1)
    transition = tool.build_transition_chat_request("transition")
    allowed_high = {
        "model", "messages", "stream", "thinking", "reasoning_effort",
        "response_format", "max_tokens",
    }
    allowed_low = allowed_high - {"reasoning_effort"}
    for request in (blind, transition):
        wire = tool._strip_local_request_fields(request)
        assert_true(set(wire) == allowed_low, "low-profile wire request keys drifted")
        assert_true("temperature" not in wire and "top_p" not in wire,
                    "thinking request must omit temperature/top_p")
        assert_true(wire["stream"] is False, "stream must be false")
        assert_true(wire["thinking"] == {"type": "disabled"},
                    "low profile must use official non-thinking mode")
        assert_true(wire["response_format"] == {"type": "json_object"},
                    "JSON mode missing")
    recon_wire = tool._strip_local_request_fields(recon)
    assert_true(set(recon_wire) == allowed_high, "high-profile wire request keys drifted")
    assert_true(recon_wire["thinking"] == {"type": "enabled"}
                and recon_wire["reasoning_effort"] == "high",
                "reconciliation must retain high thinking mode")
    assert_true("reasoning_effort" not in blind and blind["max_tokens"] == 4096,
                "blind profile mismatch")
    assert_true(recon["reasoning_effort"] == "high" and recon["max_tokens"] == 32768,
                "reconciliation profile mismatch")
    recovery_wire = tool._strip_local_request_fields(recovery_recon)
    assert_true(set(recovery_wire) == allowed_low,
                "recovery reconciliation wire request keys drifted")
    assert_true(recovery_wire["thinking"] == {"type": "disabled"},
                "recovery reconciliation must disable thinking")
    assert_true("reasoning_effort" not in recovery_recon
                and recovery_recon["max_tokens"] == 32768,
                "recovery reconciliation profile mismatch")
    assert_true(recovery_wire["response_format"] == {"type": "json_object"},
                "recovery reconciliation JSON mode missing")
    assert_true(recovery_recon["_local_call_profile"]
                == tool.CALL_PROFILE_MAIN_RECONCILIATION_RECOVERY,
                "recovery reconciliation local profile mismatch")
    assert_true("reasoning_effort" not in transition and transition["max_tokens"] == 16384,
                "transition profile mismatch")
    transition_blind = tool.build_transition_blind_chat_request("transition-blind")
    assert_true(transition_blind["thinking"] == {"type": "disabled"}
                and transition_blind["max_tokens"] == 4096,
                "transition blind profile mismatch")
    transition_recon = tool.build_transition_reconciliation_chat_request(
        "transition-reconciliation")
    assert_true(transition_recon["thinking"] == {"type": "enabled"}
                and transition_recon["reasoning_effort"] == "high"
                and transition_recon["max_tokens"] == 16384,
                "transition reconciliation profile mismatch")


def test_response_and_usage_contract(tool):
    response = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": '{"ok":true}', "reasoning_content": "SECRET_TRACE"},
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14,
                  "prompt_cache_hit_tokens": 2},
    }
    assert_true(tool.parse_chat_response(response) == {"ok": True},
                "choices[0].message.content parsing failed")
    assert_true(tool._response_usage(response) == {
        "prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14,
    }, "usage allowlist mismatch")
    assert_true("SECRET_TRACE" not in json.dumps(tool._response_usage(response)),
                "reasoning_content leaked")
    for bad in ({"choices": []}, {"choices": [{"finish_reason": "length",
                                                 "message": {"content": "{}"}}]},
                {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}):
        try:
            tool.parse_chat_response(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid/empty response must fail closed")

    empty = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": "", "reasoning_content": "SECRET_REASONING"},
        }],
        "usage": {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16},
    }
    try:
        tool.parse_chat_response(empty)
    except tool.LlmEmptyContentError as exc:
        serialized = json.dumps(exc.diagnostics, sort_keys=True)
        assert_true(exc.diagnostics["reasoning_content_present"] is True,
                    "empty-content reasoning presence was not diagnosed")
        assert_true(exc.diagnostics["reasoning_content_chars"] == 16,
                    "empty-content reasoning length was not diagnosed")
        assert_true("SECRET_REASONING" not in serialized and "SECRET_REASONING" not in str(exc),
                    "reasoning_content text leaked through diagnostics")
    else:
        raise AssertionError("empty final content needs the typed error")

    transport_error = RuntimeError("reset")
    transport_error.api_key_routes = ["bearer"]
    transport_error.llm_http_calls = 2
    transport_error.llm_call_profile = tool.CALL_PROFILE_MAIN_RECONCILIATION
    tool._attach_response_metadata(transport_error, [{
        "_api_key_route": "bearer",
        "_llm_call_routes": ["bearer"],
        "_llm_http_calls": 1,
    }])
    assert_true(transport_error.llm_http_calls == 3
                and transport_error.api_key_routes == ["bearer", "bearer"],
                "successful prior response must merge with transport attempts")


def test_future_contract(tool):
    packet = {"market_context": {"price": 101500}}
    report = valid_future()
    assert_true("future_24h_bayesian_report" in
                tool.integrated_trade_advisory_schema()["required"],
                "future report must remain required by the integrated advisory schema")
    tool._validate_future_24h_bayesian_report(report, packet)
    normalized = tool._normalize_future_24h_bayesian_report(report)
    assert_true(normalized["policy_validation"]["passed"] is True,
                "policy validation must be local")
    assert_true(normalized["posterior_weights_pct"] == {"up": 52, "down": 18, "range": 30},
                "integer weights changed")
    assert_true("flip_point" not in tool._humanize_future_24h_basis(
        "卡内gamma_regime.flip_point；精度受限"),
        "known packet paths must be humanized before sidecar output")
    mutations = []
    bad_sum = valid_future(); bad_sum["posterior_weights_pct"]["up"] = 51; mutations.append(bad_sum)
    bad_scope = valid_future(); bad_scope["input_scope"] = "PACKET_ONLY"; mutations.append(bad_scope)
    bad_news = valid_future(); bad_news["live_external_data_used"] = True; mutations.append(bad_news)
    bad_price = valid_future(99999); mutations.append(bad_price)
    bad_est = valid_future(); bad_est["key_levels"][1]["basis_cn"] = "上方推导位"; mutations.append(bad_est)
    bad_newline = valid_future(); bad_newline["report_cn"] += "\n第二段"; mutations.append(bad_newline)
    bad_base = valid_future(); bad_base["base_case"] = "DOWN"; mutations.append(bad_base)
    for bad in mutations:
        try:
            tool._validate_future_24h_bayesian_report(bad, packet)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid future report passed local validation")


def test_transition_policy_text_boundary(tool):
    sanitized = tool._sanitize_transition_cn("包内宏观评分上升，可能限制风险偏好。")
    assert_true("风险偏好" not in sanitized and "风险承受空间" in sanitized,
                "packet-derived transition inference must not look like an external-data claim")
    human_text = "\n".join(tool._transition_human_text_fields({
        "evidence_catalog_hash": "sha256:8a8892ca0cf11058153d75c6da54e25a1d3204931ec1e315a7ca1b907558c5e6",
        "transition_summary_cn": "包内事实发生变化。",
    }))
    assert_true("sha256:" not in human_text,
                "machine evidence hash leaked into transition human-text validation")
    assert_true(not tool.has_raw_human_leak(human_text),
                "hash substring was misclassified as scientific notation")
    external_review = {
        "transition_summary_cn": "新闻推动了本轮变化。",
        "observed_changes": [],
    }
    external_policy = tool._transition_policy_validation(external_review, {})
    assert_true("external_data_claim" in external_policy["issue_codes"],
                "real external-data claim was not detected")
    assert_true(external_policy["passed"] is False
                and external_policy["render_state"] == "DEGRADED_LLM_TEXT",
                "external-data claim must block transition LLM text")


def test_prompt_reasoning_contract(tool):
    blind_prompt = tool.build_blind_prompt({
        "decision": {"lean": "SHOULD_NOT_BE_VISIBLE"},
        "market_context": {"price": 101500},
    })
    assert_true("FULL_AUDIT_PACKET" not in blind_prompt,
                "blind prompt leaked the full-packet channel")
    assert_true("SHOULD_NOT_BE_VISIBLE" not in blind_prompt,
                "blind prompt leaked producer conclusion content")
    for phrase in ("同源或派生指标不得重复计票", "最强反证", "不要输出逐步思维链"):
        assert_true(phrase in blind_prompt, "blind reasoning protocol missing: " + phrase)

    blind_payload = {
        "theoretical_active_view": tool._default_theoretical_active_view("测试"),
        "gamma_regime_lens": tool._default_gamma_regime_lens("测试"),
    }
    prompt = tool.build_prompt({"market_context": {"price": 101500}}, blind_payload)
    for phrase in (
            "第一性贝叶斯审计协议", "PACKET_OBSERVED", "MODEL_PRIOR", "UNKNOWN",
            "同簇内高度相关的指标只算一次主要更新", "模型主观情景权重而非已校准胜率",
            "base_case 必须是最高权重情景", "反证优先", "模型估算观察位",
            "不要输出、复述或索取 reasoning_content", "期望 JSON 结构示例",
            "LOCAL_RESPONSE_JSON_SCHEMA"):
        assert_true(phrase in prompt, "main reasoning protocol missing: " + phrase)
    assert_true('"minItems":1' in prompt and '"maxItems":3' in prompt,
                "prompt schema must preserve array cardinality")
    assert_true('"key_premises":[]' not in prompt,
                "shape example must not contradict key-premise cardinality")
    retry_prompt = tool.build_prompt(
        {"market_context": {"price": 101500}}, blind_payload,
        empty_content_retry_count=1)
    assert_true("RETRY_RECOVERY_INSTRUCTION" in retry_prompt
                and "message.content" in retry_prompt,
                "empty-content cross-round retry instruction is missing")

    transition_prompt = tool.build_transition_review_prompt({})
    for phrase in ("delta-first", "不得重复计票", "SYSTEM_ASSERTIONS", "不得把共同出现写成已证实因果",
                   "changed_fields 非空", "同一 domain 原则上只写一条"):
        assert_true(phrase in transition_prompt,
                    "transition reasoning protocol missing: " + phrase)


def test_http_retry_and_redaction(tool):
    calls = []
    original = tool._post_chat_completion
    original_sleep = tool.time.sleep
    try:
        sleep_calls = []
        tool.time.sleep = lambda seconds: sleep_calls.append(seconds)
        def reset_once(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionResetError("reset")
            return {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
        tool._post_chat_completion = reset_once
        result = tool.call_llm("sk-test-secret-value", tool.DEFAULT_MODEL, {}, timeout=1)
        assert_true(len(calls) == 2 and result["_llm_http_calls"] == 2,
                    "connection reset should retry once")
        assert_true(sleep_calls == [], "connection reset should retry without delay")
        calls.clear()
        sleep_calls.clear()
        def busy_once(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise tool.LlmApiError(503, "service busy")
            return {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
        tool._post_chat_completion = busy_once
        result = tool.call_llm("sk-test-secret-value", tool.DEFAULT_MODEL, {}, timeout=1)
        assert_true(len(calls) == 2 and result["_llm_http_calls"] == 2,
                    "503 without Retry-After should retry once")
        assert_true(sleep_calls == [5.0],
                    "503 inline retry must apply bounded default backoff")
        calls.clear()
        sleep_calls.clear()
        def timeout(*args, **kwargs):
            calls.append(1)
            raise TimeoutError("full timeout")
        tool._post_chat_completion = timeout
        try:
            tool.call_llm("sk-test-secret-value", tool.DEFAULT_MODEL, {}, timeout=1)
        except TimeoutError:
            pass
        else:
            raise AssertionError("full timeout must fail")
        assert_true(len(calls) == 1, "full timeout must not retry inline")
    finally:
        tool._post_chat_completion = original
        tool.time.sleep = original_sleep
    redacted = tool._redact_sensitive_text(
        "Authorization Bearer sk-test-secret-value LLM_API_KEY")
    assert_true("sk-test" not in redacted and "LLM_API_KEY" not in redacted,
                "secret redaction failed")
    retry_408 = tool.LlmApiError(408, "request timeout")
    retry_408.retry_after = 0
    assert_true(tool._is_retryable_llm_error(retry_408), "408 should retry once")
    no_header = tool.LlmApiError(503, "busy")
    assert_true(tool._is_retryable_llm_error(no_header),
                "transient 503 without Retry-After should use bounded default backoff")
    assert_true(tool._inline_llm_retry_delay(no_header) == 5.0,
                "default inline HTTP retry delay drifted")
    long_wait = tool.LlmApiError(503, "busy"); long_wait.retry_after = 11
    assert_true(not tool._is_retryable_llm_error(long_wait),
                "Retry-After above 10 seconds must not retry inline")
    malformed = tool.LlmApiError(503, "busy")
    malformed.retry_after = None
    malformed.retry_after_header_present = True
    assert_true(not tool._is_retryable_llm_error(malformed),
                "present but unparseable Retry-After must not retry inline")

    with tempfile.TemporaryDirectory() as tmp:
        budget = tool.DailyHttpBudget(Path(tmp) / "usage.json", limit=1)
        real_calls = []
        tool._post_chat_completion = lambda *args, **kwargs: (
            real_calls.append(1),
            (_ for _ in ()).throw(ConnectionResetError("reset")),
        )[-1]
        try:
            tool.call_llm("sk-test-secret-value", tool.DEFAULT_MODEL, {},
                          timeout=1, budget=budget)
        except tool.DailyBudgetExceeded as exc:
            assert_true(len(real_calls) == 1 and exc.llm_http_calls == 1,
                        "budget refusal must not count as a real HTTP call")
        else:
            raise AssertionError("second reservation must fail at the daily cap")
        finally:
            tool._post_chat_completion = original


def test_daily_cap_and_single_writer(tool):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        budget = tool.DailyHttpBudget(root / "usage.json", limit=60)
        failures = []
        def reserve():
            try:
                budget.reserve()
            except Exception as exc:
                failures.append(exc)
        threads = [threading.Thread(target=reserve) for _ in range(61)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        assert_true(budget.snapshot()["http_calls_used"] == 60,
                    "daily cap reservation drifted")
        assert_true(len(failures) == 1 and isinstance(failures[0], tool.DailyBudgetExceeded),
                    "daily cap must fail closed")
        output = root / "sidecar.jsonl"
        writers = [threading.Thread(target=tool._append_jsonl,
                                    args=(output, {"row": index}))
                   for index in range(20)]
        for thread in writers: thread.start()
        for thread in writers: thread.join()
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        assert_true(len(rows) == 20, "single-writer append lost/corrupted rows")


def test_nonstream_keepalive_has_wall_clock_deadline(tool):
    class KeepaliveResponse:
        def read1(self, _size):
            return b"\n"

    original_monotonic = tool.time.monotonic
    ticks = iter((10.0, 10.4, 10.8, 11.01))
    tool.time.monotonic = lambda: next(ticks)
    try:
        try:
            tool._read_response_body_with_deadline(KeepaliveResponse(), 11.0)
        except TimeoutError as exc:
            assert_true("wall-clock deadline" in str(exc),
                        "keepalive deadline error should be explicit")
        else:
            raise AssertionError("blank keepalives must not extend request deadline")
    finally:
        tool.time.monotonic = original_monotonic

    class PlainReadErrorBody:
        def read(self):
            raise AssertionError("unbounded error-body read must not run")

    assert_true(tool._read_response_body_with_deadline(
        PlainReadErrorBody(), 99.0, allow_plain_read=False) == b"",
        "HTTP error bodies without read1 must fail closed without draining")


def test_cross_process_cap_and_append(tool):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = root / "process-usage.json"
        reserve_code = (
            "import importlib.util,sys;"
            "s=importlib.util.spec_from_file_location('llm',sys.argv[1]);"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "b=m.DailyHttpBudget(sys.argv[2],limit=60);"
            "exec(\"for _ in range(7):\\n try:b.reserve()\\n except m.DailyBudgetExceeded:pass\")"
        )
        processes = [subprocess.Popen(
            [sys.executable, "-c", reserve_code, str(TOOL), str(state)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            for _ in range(10)]
        errors = [process.communicate()[1] for process in processes
                  if process.wait() != 0]
        assert_true(not errors, "cross-process cap workers failed: " + " | ".join(errors))
        assert_true(tool.DailyHttpBudget(state, limit=60).snapshot()["http_calls_used"] == 60,
                    "cross-process cap overspent or lost reservations")

        output = root / "process-sidecar.jsonl"
        append_code = (
            "import importlib.util,sys;"
            "s=importlib.util.spec_from_file_location('llm',sys.argv[1]);"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "[m._append_jsonl(sys.argv[2],{'worker':sys.argv[3],'row':i}) for i in range(10)]"
        )
        writers = [subprocess.Popen(
            [sys.executable, "-c", append_code, str(TOOL), str(output), str(index)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            for index in range(8)]
        errors = [process.communicate()[1] for process in writers
                  if process.wait() != 0]
        assert_true(not errors, "cross-process append workers failed: " + " | ".join(errors))
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        assert_true(len(rows) == 80, "cross-process append lost/corrupted rows")


def test_retry_gate_and_cooling(tool):
    now = tool._dt.datetime.now(tool._dt.timezone.utc)
    packet_hash = "sha256:packet"
    records = [{
        "card_id": "CARD",
        "llm_review": {"status": "ERROR", "input_packet_hash": packet_hash,
                       "reviewed_at": now.isoformat()},
    }]
    state, count = tool._retry_state_for_record(
        records, "card_id", "CARD", "llm_review", packet_hash, now=now)
    assert_true((state, count) == ("COOLING", 1), "first cooldown mismatch")
    records *= 4
    state, count = tool._retry_state_for_record(
        records, "card_id", "CARD", "llm_review", packet_hash, now=now)
    assert_true((state, count) == ("TERMINAL", 4), "terminal retry mismatch")
    state, count = tool._retry_state_for_record(
        records, "card_id", "CARD", "llm_review", "sha256:new", now=now)
    assert_true((state, count) == ("READY", 0), "packet hash change must reset")
    state, _ = tool._retry_state_for_record(
        records, "card_id", "CARD", "llm_review", packet_hash,
        explicit_retry_id="CARD", now=now)
    assert_true(state == "READY", "explicit retry-id must reset")


def test_fatal_config_stops_batch(tool):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "cards.jsonl"
        output = root / "reviews.jsonl"
        for index in range(4):
            tool._append_jsonl(source, minimal_card(f"AUTH-{index}", index))

        calls = []

        def auth_failure(*args, **kwargs):
            calls.append(1)
            raise tool.LlmApiError(401, "unauthorized")

        result = tool.generate_reviews(
            source, output, api_key="unit-test-key", limit=4,
            call_llm=auth_failure, max_concurrency=4)
        rows = tool._read_jsonl(output)
        assert_true(result["attempted_cards"] == 1
                    and result["errors"] == 1
                    and result["fatal_config_stop"] is True,
                    "fatal auth error must stop the main batch after the probe")
        assert_true(len(calls) == 1 and len(rows) == 1,
                    "fatal auth error must not fan out across four records")
        review = rows[0]["llm_review"]
        assert_true(review["error_category"] == "FATAL_CONFIG"
                    and review["fatal_config_error"] is True
                    and review["record_retry_state"] == "TERMINAL",
                    "fatal main error must be classified terminal")

        ledger = root / "transitions.jsonl"
        transition_output = root / "transition-reviews.jsonl"
        calls.clear()
        for index in range(4):
            tool._append_jsonl(
                ledger, minimal_transition(f"AUTH-T-{index}", index))
        transition_result = tool.generate_transition_reviews(
            ledger, transition_output, api_key="unit-test-key", limit=4,
            call_llm=auth_failure, max_concurrency=4)
        transition_rows = tool._read_jsonl(transition_output)
        assert_true(transition_result["attempted_transitions"] == 1
                    and transition_result["errors"] == 1
                    and transition_result["fatal_config_stop"] is True,
                    "fatal auth error must stop the transition batch after the probe")
        assert_true(len(calls) == 1 and len(transition_rows) == 1,
                    "fatal transition auth error must not fan out")
        transition_review = transition_rows[0]["transition_llm_review"]
        assert_true(transition_review["error_category"] == "FATAL_CONFIG"
                    and transition_review["fatal_config_error"] is True
                    and transition_review["record_retry_state"] == "TERMINAL",
                    "fatal transition error must be classified terminal")


def test_transition_concurrency(tool):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = root / "transitions.jsonl"
        output = root / "reviews.jsonl"
        for index in range(4):
            tool._append_jsonl(ledger, {
                "transition_id": f"T-{index}", "current_card_id": f"C-{index}",
                "previous_card_id": f"P-{index}", "symbol": "BTC",
                "llm_review_required": True, "current_ts_ms": index,
            })
        active = 0
        peak = 0
        gate = threading.Lock()
        original_builder = tool.build_transition_llm_review
        try:
            def fake_call(api_key, model, request_body, timeout, **kwargs):
                nonlocal active, peak
                with gate:
                    active += 1; peak = max(peak, active)
                time.sleep(0.05)
                with gate:
                    active -= 1
                return {"choices": [{"finish_reason": "stop",
                                      "message": {"content": "{}"}}]}
            def fake_builder(transition, payload, **kwargs):
                return {"schema_version": tool.TRANSITION_OUTPUT_SCHEMA_VERSION,
                        "status": "OK", "provider": tool.PROVIDER,
                        "model": kwargs.get("model", tool.DEFAULT_MODEL),
                        "prompt_version": tool.TRANSITION_PROMPT_VERSION,
                        "blind_review_mode": "single_call_evidence_first",
                        "llm_call_count": 1,
                        "input_packet_hash": tool._sha256_json(
                            tool.build_transition_review_packet(transition))}
            tool.build_transition_llm_review = fake_builder
            result = tool.generate_transition_reviews(
                ledger, output, api_key="sk-test-secret-value", limit=4,
                call_llm=fake_call, max_concurrency=4)
        finally:
            tool.build_transition_llm_review = original_builder
        assert_true(result["written_reviews"] == 4 and peak >= 2,
                    "transition records were not processed concurrently")
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        assert_true(len(rows) == 4, "transition single writer lost rows")

        retry_ledger = root / "retry-transitions.jsonl"
        retry_output = root / "retry-reviews.jsonl"
        retry_items = []
        for index in range(2):
            item = {"transition_id": f"R-{index}", "current_card_id": f"RC-{index}",
                    "previous_card_id": f"RP-{index}", "symbol": "BTC",
                    "llm_review_required": True, "current_ts_ms": index}
            retry_items.append(item)
            tool._append_jsonl(retry_ledger, item)
            packet_hash = tool._sha256_json(tool.build_transition_review_packet(item))
            for _ in range(3):
                tool._append_jsonl(retry_output, {
                    "transition_id": item["transition_id"],
                    "transition_llm_review": {
                        "status": "ERROR", "input_packet_hash": packet_hash,
                        "reviewed_at": "2020-01-01T00:00:00+00:00",
                    },
                })
        def failing_call(*args, **kwargs):
            raise ValueError("invalid JSON")
        result = tool.generate_transition_reviews(
            retry_ledger, retry_output, api_key="sk-test-secret-value", limit=4,
            call_llm=failing_call, max_concurrency=4)
        assert_true(result["errors"] == 2, "concurrent retry failure count mismatch")
        latest = {}
        for row in tool._read_jsonl(retry_output):
            latest[row["transition_id"]] = row["transition_llm_review"]
        assert_true(all(latest[item["transition_id"]]["record_retry_count"] == 4
                        and latest[item["transition_id"]]["record_retry_state"] == "TERMINAL"
                        for item in retry_items),
                    "concurrent fourth transition failure must be terminal immediately")


def test_empty_content_cross_round_recovery(tool):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "cards.jsonl"
        output = root / "reviews.jsonl"
        card = minimal_card("EMPTY-RECOVERY", 1)
        tool._append_jsonl(source, card)
        requests = []

        def response(content, profile):
            return {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": content,
                                "reasoning_content": "SECRET_REASONING"},
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "total_tokens": 15},
                "_api_key_route": "bearer",
                "_llm_call_routes": ["bearer"],
                "_llm_http_calls": 1,
                "_llm_retry_id": "test-retry",
                "_llm_call_profile": profile,
                "_daily_http_budget": {"http_calls_used": len(requests)},
            }

        blind_payload = {
            "theoretical_active_view": tool._default_theoretical_active_view("test"),
            "gamma_regime_lens": tool._default_gamma_regime_lens("test"),
        }

        def fake_call(api_key, model, request_body, timeout, **kwargs):
            profile = kwargs.get("call_profile")
            wire = tool._strip_local_request_fields(request_body)
            requests.append((
                profile,
                request_body["messages"][-1]["content"],
                wire,
                request_body.get("_local_call_profile"),
            ))
            if profile == tool.CALL_PROFILE_MAIN_BLIND:
                return response(json.dumps(blind_payload), profile)
            return response("", profile)

        first = tool.generate_reviews(
            source, output, api_key="sk-test-secret-value", limit=1,
            call_llm=fake_call, reviewed_at="2020-01-01T00:00:00+00:00")
        assert_true(first["errors"] == 1 and len(requests) == 2,
                    "first empty-content round must use exactly two main calls")
        assert_true(requests[0][0] == tool.CALL_PROFILE_MAIN_BLIND
                    and requests[1][0] == tool.CALL_PROFILE_MAIN_RECONCILIATION,
                    "first empty-content round profile sequence drifted")
        assert_true(requests[1][2]["thinking"] == {"type": "enabled"}
                    and requests[1][2]["reasoning_effort"] == "high",
                    "first reconciliation must retain high thinking")
        error_review = tool._read_jsonl(output)[-1]["llm_review"]
        error_text = json.dumps(error_review, ensure_ascii=False)
        assert_true(error_review["error_category"] == "EMPTY_CONTENT",
                    "empty final content was not categorized")
        assert_true(error_review["llm_http_call_count"] == 2,
                    "successful blind+reconciliation HTTP calls were lost")
        assert_true(error_review["llm_call_routes"] == ["bearer", "bearer"],
                    "successful HTTP routes were lost")
        assert_true(error_review["call_profile"] == tool.CALL_PROFILE_MAIN_RECONCILIATION,
                    "failing reconciliation profile was not retained")
        assert_true(error_review["llm_call_profiles"] == [
            tool.CALL_PROFILE_MAIN_BLIND,
            tool.CALL_PROFILE_MAIN_RECONCILIATION,
        ], "main call profile sequence was not retained")
        assert_true(error_review["usage"] == {
            "blind": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15},
            "reconciliation": {"prompt_tokens": 10, "completion_tokens": 5,
                               "total_tokens": 15},
        }, "per-stage usage was not retained")
        assert_true(error_review["record_retry_state"] == "COOLING",
                    "empty content must retain record-level cooling")
        assert_true("SECRET_REASONING" not in error_text,
                    "reasoning_content leaked into the error sidecar")

        original_builder = tool.build_llm_review
        try:
            tool.build_llm_review = lambda card, payload, **kwargs: {
                "schema": tool.OUTPUT_SCHEMA_VERSION,
                "status": "OK",
                "provider": tool.PROVIDER,
                "model": kwargs.get("model", tool.DEFAULT_MODEL),
                "prompt_version": tool.PROMPT_VERSION,
                "input_packet_hash": tool._sha256_json(tool.build_review_packet(card)),
            }

            def recovered_call(api_key, model, request_body, timeout, **kwargs):
                profile = kwargs.get("call_profile")
                wire = tool._strip_local_request_fields(request_body)
                requests.append((
                    profile,
                    request_body["messages"][-1]["content"],
                    wire,
                    request_body.get("_local_call_profile"),
                ))
                content = json.dumps(blind_payload) if profile == tool.CALL_PROFILE_MAIN_BLIND else "{}"
                return response(content, profile)

            second = tool.generate_reviews(
                source, output, api_key="sk-test-secret-value", limit=1,
                call_llm=recovered_call, retry_id="EMPTY-RECOVERY")
        finally:
            tool.build_llm_review = original_builder
        assert_true(second["written_reviews"] == 1 and len(requests) == 4,
                    "cross-round recovery must use one new strict two-call sequence")
        assert_true("RETRY_RECOVERY_INSTRUCTION" in requests[-1][1],
                    "explicit retry did not receive the deterministic recovery note")
        assert_true(requests[-2][0] == tool.CALL_PROFILE_MAIN_BLIND
                    and requests[-1][0]
                    == tool.CALL_PROFILE_MAIN_RECONCILIATION_RECOVERY,
                    "recovery round profile sequence drifted")
        assert_true(requests[-1][3]
                    == tool.CALL_PROFILE_MAIN_RECONCILIATION_RECOVERY,
                    "recovery local profile was not recorded honestly")
        assert_true(requests[-1][2]["thinking"] == {"type": "disabled"}
                    and "reasoning_effort" not in requests[-1][2]
                    and requests[-1][2]["response_format"] == {"type": "json_object"}
                    and requests[-1][2]["max_tokens"] == 32768,
                    "recovery reconciliation wire contract drifted")
        ok_review = tool._read_jsonl(output)[-1]["llm_review"]
        ok_text = json.dumps(ok_review, ensure_ascii=False)
        assert_true(ok_review["llm_call_profiles"] == [
            tool.CALL_PROFILE_MAIN_BLIND,
            tool.CALL_PROFILE_MAIN_RECONCILIATION_RECOVERY,
        ], "recovery success profile sequence was not retained")
        assert_true(ok_review["usage"] == {
            "blind": {"prompt_tokens": 10, "completion_tokens": 5,
                      "total_tokens": 15},
            "reconciliation": {"prompt_tokens": 10, "completion_tokens": 5,
                               "total_tokens": 15},
        }, "recovery per-stage usage was not retained")
        assert_true("SECRET_REASONING" not in ok_text,
                    "reasoning_content leaked into the recovered sidecar")


def test_blind_empty_does_not_downgrade_first_reconciliation(tool):
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        source = root / "cards.jsonl"
        output = root / "reviews.jsonl"
        card = minimal_card("BLIND-EMPTY", 1)
        tool._append_jsonl(source, card)
        blind_payload = {
            "theoretical_active_view": tool._default_theoretical_active_view("test"),
            "gamma_regime_lens": tool._default_gamma_regime_lens("test"),
        }

        def response(content, profile):
            return {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": content,
                                "reasoning_content": "SECRET_REASONING"},
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                          "total_tokens": 15},
                "_api_key_route": "bearer",
                "_llm_call_routes": ["bearer"],
                "_llm_http_calls": 1,
                "_llm_call_profile": profile,
            }

        first_requests = []

        def blind_empty_call(api_key, model, request_body, timeout, **kwargs):
            profile = kwargs.get("call_profile")
            first_requests.append(profile)
            return response("", profile)

        first = tool.generate_reviews(
            source, output, api_key="sk-test-secret-value", limit=1,
            call_llm=blind_empty_call,
            reviewed_at="2020-01-01T00:00:00+00:00")
        assert_true(first["errors"] == 1
                    and first_requests == [tool.CALL_PROFILE_MAIN_BLIND],
                    "blind empty content should fail before reconciliation")
        first_review = tool._read_jsonl(output)[-1]["llm_review"]
        assert_true(first_review["error_category"] == "EMPTY_CONTENT"
                    and first_review["call_profile"] == tool.CALL_PROFILE_MAIN_BLIND,
                    "blind empty failure metadata drifted")

        retry_requests = []
        original_builder = tool.build_llm_review
        try:
            tool.build_llm_review = lambda card, payload, **kwargs: {
                "schema": tool.OUTPUT_SCHEMA_VERSION,
                "status": "OK",
                "provider": tool.PROVIDER,
                "model": kwargs.get("model", tool.DEFAULT_MODEL),
                "prompt_version": tool.PROMPT_VERSION,
                "input_packet_hash": tool._sha256_json(tool.build_review_packet(card)),
            }

            def recovered_call(api_key, model, request_body, timeout, **kwargs):
                profile = kwargs.get("call_profile")
                retry_requests.append((profile, tool._strip_local_request_fields(request_body)))
                content = (json.dumps(blind_payload)
                           if profile == tool.CALL_PROFILE_MAIN_BLIND else "{}")
                return response(content, profile)

            second = tool.generate_reviews(
                source, output, api_key="sk-test-secret-value", limit=1,
                call_llm=recovered_call, retry_id="BLIND-EMPTY")
        finally:
            tool.build_llm_review = original_builder

        assert_true(second["written_reviews"] == 1
                    and [item[0] for item in retry_requests] == [
                        tool.CALL_PROFILE_MAIN_BLIND,
                        tool.CALL_PROFILE_MAIN_RECONCILIATION,
                    ], "blind-empty retry must preserve first high reconciliation")
        recon_wire = retry_requests[-1][1]
        assert_true(recon_wire["thinking"] == {"type": "enabled"}
                    and recon_wire["reasoning_effort"] == "high",
                    "blind-empty retry incorrectly used non-thinking recovery")


def main():
    tool = load_tool()
    assert_true(tool.DEFAULT_MODEL == "deepseek-v4-flash", "model mismatch")
    assert_true(tool.PROVIDER == "deepseek", "provider mismatch")
    assert_true(tool.OUTPUT_SCHEMA_VERSION == "signal_llm_review@1.5.0", "schema mismatch")
    assert_true(tool.PROMPT_VERSION == "signal_llm_review_prompt@1.5.3", "prompt mismatch")
    assert_true(tool.TRANSITION_OUTPUT_SCHEMA_VERSION == "signal_transition_llm_review@1.3.0",
                "transition schema mismatch")
    assert_true(tool.TRANSITION_PROMPT_VERSION == "signal_transition_llm_review_prompt@1.3.2",
                "transition prompt mismatch")
    test_request_contract(tool)
    test_response_and_usage_contract(tool)
    test_future_contract(tool)
    test_transition_policy_text_boundary(tool)
    test_prompt_reasoning_contract(tool)
    test_empty_content_cross_round_recovery(tool)
    test_blind_empty_does_not_downgrade_first_reconciliation(tool)
    test_http_retry_and_redaction(tool)
    test_nonstream_keepalive_has_wall_clock_deadline(tool)
    test_daily_cap_and_single_writer(tool)
    test_cross_process_cap_and_append(tool)
    test_retry_gate_and_cooling(tool)
    test_fatal_config_stops_batch(tool)
    test_transition_concurrency(tool)
    print("signal_llm_review_pipeline: PASS")


if __name__ == "__main__":
    main()
