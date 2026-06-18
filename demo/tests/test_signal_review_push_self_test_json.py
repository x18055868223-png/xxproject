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


def main():
    mod = load_signal_module()
    pushes = []
    original_push = mod.fmz_push
    mod.fmz_push = lambda text: pushes.append(text)
    try:
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
    finally:
        mod.fmz_push = original_push
    print("signal_review_push_self_test_json: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_review_push_self_test_json: FAIL - " + str(exc))
        sys.exit(1)
