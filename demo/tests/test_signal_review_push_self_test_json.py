import importlib.util
import json
import pathlib
import sys
import tempfile
from types import SimpleNamespace


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


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            "{}: expected {!r}, got {!r}".format(message, expected, actual)
        )


def main():
    mod = load_signal_module()
    pushes = []
    logs = []
    original_push = mod.fmz_push
    original_log = mod.fmz_log
    mod.fmz_push = lambda text: pushes.append(text)
    mod.fmz_log = lambda *parts: logs.append(" ".join(str(p) for p in parts))
    try:
        config_from_bare = dict(mod.CONFIG)
        config_from_bare["signal_review_push_enabled"] = False
        mod.apply_runtime_config_overrides(
            config_from_bare,
            {"signal_review_push_enabled": "true"})
        assert_true(config_from_bare["signal_review_push_enabled"],
                    "bare FMZ parameter should enable signal push")
        config_from_bare["read_only_demo"] = True
        mod.apply_runtime_config_overrides(
            config_from_bare,
            {"read_only_demo": "false"})
        assert_true(config_from_bare["read_only_demo"],
                    "bare non-push parameter should not override safety config")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = dict(mod.CONFIG)
            config["signal_review_push_test"] = True
            config["logs_dir"] = temp_dir
            config["signal_review_recorder_name"] = "signal_review"
            config["audit_static_base_url"] = ""
            runtime = SimpleNamespace(
                config=config,
                recorder=mod.JsonlRecorder(config),
            )

            mod.DemoRuntime._emit_push_self_test(runtime)

            jsonl = pathlib.Path(temp_dir) / "signal_review.jsonl"
            assert_true(jsonl.exists(), "self-test should write signal_review.jsonl")
            lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
            assert_true(len(lines) == 1, "self-test should write one jsonl record")
            record = json.loads(lines[0])
            assert_true(record["schema"]["status"] == "FINAL", "schema status")
            assert_true(record["identity"]["is_synthetic"] is True,
                        "self-test record must be synthetic")
            assert_true(record["delivery"]["fmz_push_summary"],
                        "delivery brief exists")
            assert_true(record["integrity"]["record_hash"],
                        "integrity hash exists")
            assert_true(len(pushes) == 1, "one self-test push")
            assert_true(pushes[0].startswith("【推送自检·非真实信号】"),
                        "push banner marks synthetic signal")
            assert_true(len(pushes[0]) <= 140, "self-test push stays <=140 chars")

            mod.DemoRuntime._emit_push_self_test(runtime)
            lines2 = jsonl.read_text(encoding="utf-8").strip().splitlines()
            assert_true(len(lines2) == 1, "self-test is idempotent")
            assert_true(len(pushes) == 1, "self-test push is idempotent")

        with tempfile.TemporaryDirectory() as temp_dir:
            pushes.clear()
            logs.clear()
            config = dict(mod.CONFIG)
            config["signal_review_push_enabled"] = True
            config["logs_dir"] = temp_dir
            config["signal_review_recorder_name"] = "signal_review"
            config["audit_static_base_url"] = ""
            runtime = SimpleNamespace(
                config=config,
                recorder=mod.JsonlRecorder(config),
                last_signal_recorded=True,
                signal_events=SimpleNamespace(
                    events=[mod.build_sample_review_card(config)]),
            )
            mod.DemoRuntime._emit_signal_review_card(runtime)
            jsonl = pathlib.Path(temp_dir) / "signal_review.jsonl"
            assert_true(jsonl.exists(), "real signal path should write jsonl")
            assert_equal(len(jsonl.read_text(encoding="utf-8").strip().splitlines()),
                         1, "real signal path should write one record")
            assert_equal(len(pushes), 1,
                         "enabled real signal path should push once")

        with tempfile.TemporaryDirectory() as temp_dir:
            pushes.clear()
            logs.clear()
            config = dict(mod.CONFIG)
            config["signal_review_push_enabled"] = False
            config["logs_dir"] = temp_dir
            config["signal_review_recorder_name"] = "signal_review"
            runtime = SimpleNamespace(
                config=config,
                recorder=mod.JsonlRecorder(config),
                last_signal_recorded=True,
                signal_events=SimpleNamespace(
                    events=[mod.build_sample_review_card(config)]),
            )
            mod.DemoRuntime._emit_signal_review_card(runtime)
            assert_equal(len(pushes), 0,
                         "disabled real signal path should not push")
            assert_true(any("signal_review_push_enabled=False" in item
                            for item in logs),
                        "disabled real signal path should log skip reason")
    finally:
        mod.fmz_push = original_push
        mod.fmz_log = original_log
    print("signal_review_push_self_test_json: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_review_push_self_test_json: FAIL - " + str(exc))
        sys.exit(1)
