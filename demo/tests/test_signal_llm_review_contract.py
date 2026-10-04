import copy
import importlib.util
import pathlib
import sys

try:
    import pytest
except Exception:
    pytest = None


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


if pytest is not None:
    @pytest.fixture(scope="module")
    def mod():
        return load_signal_module()


class NoPostHttp:
    pass


class RecordingRecorder:
    def __init__(self):
        self.writes = []

    def write(self, name, payload):
        self.writes.append((name, copy.deepcopy(payload)))
        return True


class RuntimeDouble:
    def __init__(self, config, card):
        self.config = config
        self.http = NoPostHttp()
        self.last_signal_recorded = True
        self.signal_events = type("EventsDouble", (), {"events": [card]})()
        self.recorder = RecordingRecorder()

    def _attach_near_term_market_context(self, card):
        return card


def test_old_in_process_llm_entrypoints_are_removed(mod):
    removed = (
        "llm_review_system_prompt",
        "build_llm_review_package",
        "build_llm_review_request",
        "validate_llm_review_output",
        "attach_llm_review",
        "_extract_llm_review_payload",
        "_llm_error_review",
    )
    for name in removed:
        assert_true(not hasattr(mod, name),
                    "FMZ main process should not expose old LLM entrypoint " + name)
    assert_true(not hasattr(mod.HttpClient(mod.CONFIG), "post_json"),
                "FMZ HTTP client should not retain unused POST helper")
    assert_true(mod.CONFIG.get("llm_review_enabled") is False,
                "compatibility marker should remain disabled by default")
    for key in (
        "llm_review_endpoint",
        "llm_review_api_key",
        "llm_review_model",
        "llm_review_timeout_sec",
        "llm_review_retries",
        "llm_review_temperature",
        "llm_review_prompt_version",
        "llm_review_redaction_version",
    ):
        assert_true(key not in mod.CONFIG,
                    "unused in-process LLM config should be removed: " + key)


def test_emit_signal_review_writes_base_card_only(mod):
    config = dict(mod.CONFIG)
    config.update({
        "signal_review_push_enabled": False,
        "signal_review_push_test": False,
        "audit_static_base_url": "https://audit.example.test",
    })
    card = mod.build_sample_review_card(config)
    runtime = RuntimeDouble(config, card)

    mod.DemoRuntime._emit_signal_review_card(runtime)
    assert_true(len(runtime.recorder.writes) == 1,
                "FMZ emitter should write exactly one base audit card")
    name, record = runtime.recorder.writes[0]
    assert_true(name == config.get("signal_review_recorder_name", "signal_review"),
                "emitter should use the configured signal review recorder")
    assert_true("llm_review" not in record,
                "FMZ base audit card should not contain sidecar LLM output")
    assert_true(record["decision"]["trade_allowed"] is False,
                "LLM cleanup must not change trading permission")
    assert_true(record["blocking"] == mod.build_audit_record(card, config)["blocking"],
                "LLM cleanup must not change blocking facts")


def main():
    mod = load_signal_module()
    test_old_in_process_llm_entrypoints_are_removed(mod)
    test_emit_signal_review_writes_base_card_only(mod)
    print("signal_llm_review_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_llm_review_contract: FAIL - " + str(exc))
        sys.exit(1)
