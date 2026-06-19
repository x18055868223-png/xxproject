import copy
import importlib.util
import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNAL_FILE = ROOT / "demo" / "最新交付物" / "neutral_regulation_demo_fmz.py"


def load_signal_module():
    spec = importlib.util.spec_from_file_location("nrd_signal", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def valid_llm_review(extra=None):
    payload = {
        "summary_cn": "系统等待确认的结论与当前冲突结构基本一致。",
        "agreement_with_system": "部分支持",
        "main_supporting_factors": ["TMV 与短窗主动流同向，说明修复窗口内有局部买盘支撑。"],
        "main_risks_or_conflicts": ["宏观压力和期权偏斜仍为反向证据，冲突比例未低。"],
        "operator_focus": ["观察冲突比例是否下降", "确认 rank 冷启动样本是否继续积累。"],
        "invalid_if": ["价格快速脱离 pin 区域", "GEX rank 或 IV/RV rank 出现反向极端变化。"],
        "caution_level": "MEDIUM",
        "not_trading_advice": True,
    }
    if extra:
        payload.update(extra)
    return payload


def sample_record_and_config(mod):
    config = dict(mod.CONFIG)
    config.update({
        "llm_review_enabled": True,
        "llm_review_endpoint": "https://llm.example.test/v1/chat/completions",
        "llm_review_api_key": "SECRET_LLM_TOKEN",
        "llm_review_model": "audit-review-test-model",
        "llm_review_timeout_sec": 17,
        "audit_static_base_url": "https://audit.example.test",
    })
    card = mod.build_sample_review_card(config)
    record = mod.build_audit_record(card, config)
    record["delivery"]["local_jsonl"] = r"C:\Users\Xu\secret\signal_review.jsonl"
    record["delivery"]["local_card_json"] = "/home/bitnami/fmz2/logs/storage/card.json"
    record["provenance"]["api_key"] = "SHOULD_NOT_LEAK"
    return record, config


class FakeHttp:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def post_json(self, url, payload=None, headers=None, timeout_sec=None,
                  retries=None):
        self.calls.append({
            "url": url,
            "payload": payload,
            "headers": headers or {},
            "timeout_sec": timeout_sec,
            "retries": retries,
        })
        if self.exc:
            raise self.exc
        return self.result


class RecordingRecorder:
    def __init__(self):
        self.writes = []

    def write(self, name, payload):
        self.writes.append((name, copy.deepcopy(payload)))
        return True


def llm_success_result(review):
    return {
        "quality": "OK",
        "data": {
            "choices": [
                {"message": {"content": json.dumps(review, ensure_ascii=False)}}
            ]
        },
        "error": None,
    }


def test_llm_input_package_is_sanitized(mod):
    record, config = sample_record_and_config(mod)
    package = mod.build_llm_review_package(record, config)

    for key in (
        "schema",
        "identity",
        "market_context",
        "decision",
        "signal_window",
        "reasoning",
        "conflict",
        "blocking",
        "quality",
        "factor_cross_section",
        "field_glossary",
        "guardrails",
    ):
        assert_true(key in package, "package missing " + key)

    factors = package["factor_cross_section"]
    for key in (
        "tmvf",
        "micro_flow",
        "macro_pressure",
        "gamma_regime",
        "gex_info",
        "skew",
        "funding",
    ):
        assert_true(key in factors, "factor package missing " + key)

    encoded = json.dumps(package, ensure_ascii=False, sort_keys=True)
    for forbidden in filter(None, (
        config["gex_info_token"],
        config["llm_review_api_key"],
        "SECRET_LLM_TOKEN",
        "SHOULD_NOT_LEAK",
        "Bearer",
        "local_jsonl",
        "local_card_json",
        "config_snapshot",
        "record_hash",
        "C:\\Users",
        "/home/bitnami",
    )):
        assert_true(forbidden not in encoded, "LLM package leaked " + forbidden)


def test_llm_request_contains_guardrails(mod):
    record, config = sample_record_and_config(mod)
    request = mod.build_llm_review_request(record, config)
    assert_true(request["model"] == "audit-review-test-model",
                "request should use configured model")
    assert_true(request.get("response_format") == {"type": "json_object"},
                "request should request JSON object output")
    messages = request.get("messages") or []
    assert_true(len(messages) == 2, "request should have system and user messages")
    text = json.dumps(request, ensure_ascii=False)
    for expected in (
        "信号审计复核员",
        "不改变系统信号",
        "confidence 不是胜率",
        "不得编造未提供的数据",
    ):
        assert_true(expected in text, "prompt guardrail missing " + expected)


def test_llm_output_validation(mod):
    ok = mod.validate_llm_review_output(valid_llm_review({
        "decision": {"trade_allowed": True},
    }))
    assert_true(ok["status"] == "OK", "valid output should be OK")
    assert_true(ok["not_trading_advice"] is True, "fixed disclaimer")
    assert_true("decision" not in ok and "trade_allowed" not in ok,
                "validator should whitelist LLM output fields")

    invalid = mod.validate_llm_review_output({
        "summary_cn": "缺字段",
        "not_trading_advice": True,
    })
    assert_true(invalid["status"] == "INVALID_OUTPUT",
                "missing fields should be invalid")


def test_attach_llm_review_is_compatibility_noop(mod):
    record, config = sample_record_and_config(mod)
    decision_before = copy.deepcopy(record["decision"])
    blocking_before = copy.deepcopy(record["blocking"])
    reasoning_before = copy.deepcopy(record["reasoning"])
    http = FakeHttp(llm_success_result(valid_llm_review({
        "decision": {"trade_allowed": True},
    })))

    enriched = mod.attach_llm_review(record, http, config)
    assert_true(len(http.calls) == 0,
                "FMZ compatibility hook must not call LLM in-process")
    assert_true("llm_review" not in enriched,
                "FMZ compatibility hook should leave review to sidecar script")
    assert_true(enriched["decision"] == decision_before,
                "LLM must not change decision")
    assert_true(enriched["blocking"] == blocking_before,
                "LLM must not change blocking")
    assert_true(enriched["reasoning"] == reasoning_before,
                "LLM must not change reasoning")


def test_emit_signal_review_writes_base_only(mod):
    record, config = sample_record_and_config(mod)
    config["signal_review_push_enabled"] = False
    card = mod.build_sample_review_card(config)
    http = FakeHttp(llm_success_result(valid_llm_review()))
    runtime = type("RuntimeDouble", (), {})()
    runtime.config = config
    runtime.http = http
    runtime.last_signal_recorded = True
    runtime.signal_events = type("EventsDouble", (), {"events": [card]})()
    runtime.recorder = RecordingRecorder()

    mod.DemoRuntime._emit_signal_review_card(runtime)
    assert_true(len(runtime.recorder.writes) == 1,
                "FMZ emitter should write only the base audit card")
    first = runtime.recorder.writes[0][1]
    assert_true("llm_review" not in first, "base record should not contain LLM review")
    assert_true(len(http.calls) == 0, "emitter should not call LLM")


def test_attach_llm_review_never_calls_or_adds_field(mod):
    record, config = sample_record_and_config(mod)

    disabled = dict(config)
    disabled["llm_review_enabled"] = False
    http_disabled = FakeHttp(llm_success_result(valid_llm_review()))
    result = mod.attach_llm_review(copy.deepcopy(record), http_disabled, disabled)
    assert_true(len(http_disabled.calls) == 0, "disabled review should not call LLM")
    assert_true("llm_review" not in result, "disabled review should not add field")

    synthetic_record = copy.deepcopy(record)
    synthetic_record["identity"]["is_synthetic"] = True
    http_synthetic = FakeHttp(llm_success_result(valid_llm_review()))
    result = mod.attach_llm_review(synthetic_record, http_synthetic, config)
    assert_true(len(http_synthetic.calls) == 0, "synthetic self-test should not call LLM")
    assert_true("llm_review" not in result, "synthetic record should not add LLM review")

    invalid_http = FakeHttp(llm_success_result({"summary_cn": "缺字段"}))
    invalid = mod.attach_llm_review(copy.deepcopy(record), invalid_http, config)
    assert_true(len(invalid_http.calls) == 0,
                "invalid payload branch should not be reachable in FMZ process")
    assert_true("llm_review" not in invalid,
                "LLM review should be generated by sidecar only")

    error_http = FakeHttp({"quality": "ERROR", "error": "upstream timeout"})
    error = mod.attach_llm_review(copy.deepcopy(record), error_http, config)
    assert_true(len(error_http.calls) == 0,
                "HTTP error branch should not be reachable in FMZ process")
    assert_true("llm_review" not in error,
                "LLM review should be generated by sidecar only")

    missing_endpoint = dict(config)
    missing_endpoint["llm_review_endpoint"] = ""
    missing = mod.attach_llm_review(copy.deepcopy(record), FakeHttp(), missing_endpoint)
    assert_true("llm_review" not in missing,
                "missing endpoint should not add field in FMZ process")


def main():
    mod = load_signal_module()
    test_llm_input_package_is_sanitized(mod)
    test_llm_request_contains_guardrails(mod)
    test_llm_output_validation(mod)
    test_attach_llm_review_is_compatibility_noop(mod)
    test_emit_signal_review_writes_base_only(mod)
    test_attach_llm_review_never_calls_or_adds_field(mod)
    print("signal_llm_review_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_llm_review_contract: FAIL - " + str(exc))
        sys.exit(1)
