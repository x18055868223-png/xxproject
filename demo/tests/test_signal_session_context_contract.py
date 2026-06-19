import datetime
import importlib.util
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


def ms_utc8(year, month, day, hour, minute):
    tz = datetime.timezone(datetime.timedelta(hours=8))
    dt = datetime.datetime(year, month, day, hour, minute, tzinfo=tz)
    return int(dt.timestamp() * 1000)


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)

    low_to_medium = mod.classify_signal_session_context(
        ms_utc8(2026, 6, 19, 15, 4), config)
    assert_true(low_to_medium["schema"] == "signal_session_context@1.0.0",
                "session context schema")
    assert_true(low_to_medium["base_zone"] == "MEDIUM",
                "15:04 static table should enter London early medium zone")
    assert_true(low_to_medium["effective_zone"] == "LOW",
                "15:04 should remain conservative low inside buffer")
    assert_true(low_to_medium["display_label"] == "LOW_TO_MEDIUM_BUFFER",
                "15:04 should display low-to-medium buffer")
    assert_true(low_to_medium["premise_durability"] == "LOW",
                "buffer premise durability should use effective zone")
    assert_true(low_to_medium["transition"]["active"] is True,
                "15:04 should mark transition active")
    assert_true(low_to_medium["affects_confidence"] is False,
                "phase 0 must not affect confidence")
    assert_true(low_to_medium["calibration_state"] == "UNCALIBRATED",
                "initial layer remains uncalibrated")

    pre_us = mod.classify_signal_session_context(
        ms_utc8(2026, 6, 19, 20, 30), config)
    assert_true(pre_us["effective_zone"] == "LOW",
                "20:30 pre-US runway should be low durability")
    assert_true(pre_us["rationale_code"] == "PRE_US_TRAPDOOR",
                "20:30 should explain US data/open trapdoor")
    assert_true(pre_us["catalyst_exposure"] == "HIGH",
                "pre-US runway should carry high catalyst exposure")

    high_delay = mod.classify_signal_session_context(
        ms_utc8(2026, 6, 19, 23, 30), config)
    assert_true(high_delay["base_zone"] == "HIGH",
                "23:30 should be in the US deep base zone")
    assert_true(high_delay["effective_zone"] == "MEDIUM",
                "high zone should be earned after the post-open digestion buffer")
    assert_true(high_delay["display_label"] == "MEDIUM_TO_HIGH_BUFFER",
                "23:30 should display medium-to-high buffer")

    missing_time_a = mod.classify_signal_session_context(None, config)
    missing_time_b = mod.classify_signal_session_context(None, config)
    assert_true(missing_time_a == missing_time_b,
                "missing timestamp classification should be deterministic")
    assert_true(missing_time_a["effective_zone"] == "LOW",
                "missing timestamp should fall back to conservative low")
    assert_true(missing_time_a["rationale_code"] == "MISSING_CONFIRMED_TIME",
                "missing timestamp should explain the fallback reason")
    assert_true(missing_time_a["affects_confidence"] is False,
                "missing timestamp fallback remains observe-only")

    card = mod.build_sample_review_card(config)
    record = mod.build_audit_record(card, config)
    session = record["signal_window"].get("session_context")
    assert_true(isinstance(session, dict), "audit record should include session_context")
    assert_true(session["affects_confidence"] is False,
                "audit record session context remains observe-only")
    assert_true(record["decision"]["confidence_semantics"]
                == "EVIDENCE_QUALITY_NOT_WIN_RATE",
                "confidence semantics must remain unchanged")
    assert_true(record["decision"]["confidence"] == card["conclusion"]["confidence"],
                "session layer must not mutate confidence")

    print("signal_session_context_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_session_context_contract: FAIL - " + str(exc))
        sys.exit(1)
