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
    assert_true(config["demo_version"] == "1.5.1",
                "FMZ signal deliverable version should match r3.3.1 macro dual-axis contract")

    london = mod.classify_signal_session_context(
        ms_utc8(2026, 6, 19, 15, 4), config)
    assert_true(london["schema_name"] == "SignalSessionPremiseDurabilityContext",
                "session context schema name")
    assert_true(london["schema_version"] == "1.0.0",
                "session context schema version")
    assert_true(london["clock_window"] == "15:00-18:00",
                "15:04 should map to London early UTC+8 bucket")
    assert_true(london["base_zone"] == "MEDIUM",
                "15:04 static table should enter London early medium zone")
    assert_true(london["effective_zone"] == "NEUTRAL",
                "London early remains neutral observe-only")
    assert_true(london["backtest_delta_pp"] == -1.37,
                "London early should carry calibrated backtest delta")
    assert_true(isinstance(london["validation_basis"], dict),
                "session context should include validation basis")
    assert_true(london["validation_basis"]["source_document"]
                == "结论档案_各时段信号耐久度_2023-2026_v1",
                "session context should cite the durability archive")
    assert_true(london["affects_confidence"] is False,
                "phase 0 must not affect confidence")
    assert_true(london["confidence_policy"] == "DO_NOT_MULTIPLY_CONFIDENCE",
                "session layer must not multiply confidence")

    pre_us = mod.classify_signal_session_context(
        ms_utc8(2026, 6, 19, 20, 30), config)
    assert_true(pre_us["effective_zone"] == "LOWER_DURABILITY_CONFIRMED",
                "20:30 pre-US runway should lower durability")
    assert_true(pre_us["rationale_code"] == "PRE_US_TRAPDOOR",
                "20:30 should explain US data/open trapdoor")
    assert_true(pre_us["backtest_delta_pp"] == 5.31,
                "pre-US runway should carry confirmed fragile delta")
    assert_true(pre_us["catalyst_exposure"] == "NEAR_US_DATA_AND_OPEN",
                "pre-US runway should carry high catalyst exposure")

    high_delay = mod.classify_signal_session_context(
        ms_utc8(2026, 6, 19, 23, 30), config)
    assert_true(high_delay["base_zone"] == "HIGH",
                "23:30 should be in the US deep base zone")
    assert_true(high_delay["effective_zone"] == "RAISE_DURABILITY_TENTATIVE",
                "US deep should carry tentative durability raise")
    assert_true(high_delay["display_label"] == "升耐久（中等信心）",
                "23:30 should display the calibrated durability label")

    asia = mod.classify_signal_session_context(
        ms_utc8(2026, 6, 24, 9, 7), config)
    assert_true(asia["rationale_code"] == "ASIA_MORNING",
                "09:07 should classify as Asia morning")
    assert_true(asia["clock_window"] == "08:00-11:30",
                "Asia morning should include clock window")
    assert_true(asia["backtest_delta_pp"] == 0.02,
                "Asia morning should include backtest delta")
    assert_true(asia["evidence_level"] == "NEUTRAL",
                "Asia morning should include evidence level")

    missing_time_a = mod.classify_signal_session_context(None, config)
    missing_time_b = mod.classify_signal_session_context(None, config)
    assert_true(missing_time_a == missing_time_b,
                "missing timestamp classification should be deterministic")
    assert_true(missing_time_a["effective_zone"] == "LOW",
                "missing timestamp should fall back to conservative low")
    assert_true(missing_time_a["rationale_code"] == "MISSING_CONFIRMED_TIME",
                "missing timestamp should explain the fallback reason")
    assert_true(missing_time_a["schema_name"] == "SignalSessionPremiseDurabilityContext",
                "missing timestamp should still use full schema")
    assert_true(missing_time_a["affects_confidence"] is False,
                "missing timestamp fallback remains observe-only")

    card = mod.build_sample_review_card(config)
    record = mod.build_audit_record(card, config)
    transition_source = record["provenance"].get("transition_audit_source")
    assert_true(isinstance(transition_source, dict),
                "producer should emit native transition audit source anchor")
    assert_true(transition_source["schema_name"] == "SignalTransitionProducerAnchor",
                "transition anchor schema name")
    assert_true(transition_source["schema_version"] == "1.0.0",
                "transition anchor schema version")
    assert_true(transition_source["audit_scope"] == "AUDIT_ONLY",
                "transition anchor must stay audit-only")
    assert_true(transition_source["event_time_ms"] == record["identity"]["confirmed_time_ms"],
                "transition anchor should use producer-native event time")
    assert_true(transition_source["event_time_basis"] == "identity.confirmed_time_ms",
                "transition anchor should declare the event time source")
    assert_true(transition_source["transition_computation_owner"] == "MATERIALIZER_DERIVED",
                "producer must not compute transition deltas")
    session = record["signal_window"].get("session_context")
    assert_true(isinstance(session, dict), "audit record should include session_context")
    assert_true(session["affects_confidence"] is False,
                "audit record session context remains observe-only")
    assert_true(session["schema_name"] == "SignalSessionPremiseDurabilityContext",
                "audit record should carry full session context schema")
    assert_true(record["decision_matrix"]["temporal_durability"]
                == session["premise_durability"],
                "decision matrix should mirror session premise durability")
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
