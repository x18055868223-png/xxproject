import datetime
import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
SIGNAL_FILE = (
    ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" /
    "neutral_regulation_demo_fmz.py"
)


def load_signal_module():
    spec = importlib.util.spec_from_file_location("nrd_signal_session", SIGNAL_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def utc8_ms(iso_text):
    dt = datetime.datetime.fromisoformat(iso_text)
    return int(dt.timestamp() * 1000)


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def session_for(mod, config, iso_text):
    card = mod.build_sample_review_card(config)
    confidence_before = card["conclusion"]["confidence"]
    card["confirmed_time"] = utc8_ms(iso_text)
    record = mod.build_audit_record(card, config)
    ctx = record["signal_window"].get("session_context")
    assert_true(isinstance(ctx, dict), "signal_window.session_context should exist")
    assert_true(record["decision"]["confidence"] == confidence_before,
                "session layer must not rewrite evidence confidence")
    assert_true(ctx.get("affects_confidence") is False,
                "session layer should explicitly mark confidence unchanged")
    assert_true(ctx.get("affects_blocking") is False,
                "session layer should not change blocking")
    assert_true(ctx.get("affects_trade_allowed") is False,
                "session layer should not change trading permission")
    assert_true(ctx.get("validation_basis", {}).get("research_grade")
                == "MARKET_PRIOR_VALIDATED",
                "session layer should expose the three-year validation grade")
    assert_true("2023-04-17" in ctx.get("validation_basis", {}).get("data_range", ""),
                "session layer should expose the backtest data range")
    assert_true(record["decision_matrix"]["temporal_durability"]
                == ctx["premise_durability"],
                "decision matrix should reuse session premise durability")
    return ctx, record


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)

    pre_us, _record = session_for(mod, config, "2026-04-17T19:00:00+08:00")
    assert_true(pre_us["rationale_code"] == "PRE_US_TRAPDOOR",
                "18:00-21:30 UTC+8 should be PRE_US_TRAPDOOR")
    assert_true(pre_us["adjustment_direction"] == "DECREASE",
                "PRE_US_TRAPDOOR should lower premise durability")
    assert_true(pre_us["evidence_level"] == "CONFIRMED",
                "PRE_US_TRAPDOOR should be confirmed by three-year backtest")
    assert_true(pre_us["backtest_delta_pp"] == 5.31,
                "PRE_US_TRAPDOOR should expose +5.31pp rewrite delta")
    assert_true("等美盘开后再确认" in pre_us["rationale_cn"],
                "PRE_US_TRAPDOOR should require post-US-open confirmation")

    us_deep, _record = session_for(mod, config, "2026-04-17T23:30:00+08:00")
    assert_true(us_deep["rationale_code"] == "US_DEEP_POST_CATALYST",
                "23:00-04:00 UTC+8 should be US_DEEP_POST_CATALYST")
    assert_true(us_deep["adjustment_direction"] == "INCREASE",
                "US_DEEP_POST_CATALYST should raise premise durability")
    assert_true(us_deep["evidence_level"] == "TENTATIVE",
                "US_DEEP_POST_CATALYST should stay tentative, not overclaim")
    assert_true(us_deep["backtest_delta_pp"] == -1.49,
                "US_DEEP_POST_CATALYST should expose -1.49pp rewrite delta")

    asia_morning, _record = session_for(mod, config, "2026-04-17T09:00:00+08:00")
    assert_true(asia_morning["rationale_code"] == "ASIA_MORNING",
                "08:00-11:30 UTC+8 should be ASIA_MORNING")
    assert_true(asia_morning["adjustment_direction"] == "NEUTRAL",
                "ASIA_MORNING should remain neutral")

    asia_lull, _record = session_for(mod, config, "2026-04-17T12:00:00+08:00")
    assert_true(asia_lull["rationale_code"] == "ASIA_AFTERNOON_LULL",
                "11:30-15:00 UTC+8 should be ASIA_AFTERNOON_LULL")
    assert_true(asia_lull["adjustment_direction"] == "NEUTRAL_CONSERVATIVE",
                "ASIA_AFTERNOON_LULL should not be raised on the 60m proxy alone")
    assert_true("60m" in asia_lull["rationale_cn"] and "长窗" in asia_lull["rationale_cn"],
                "ASIA_AFTERNOON_LULL should display the horizon mismatch caveat")

    post_us, _record = session_for(mod, config, "2026-04-17T04:30:00+08:00")
    assert_true(post_us["rationale_code"] == "POST_US_DEADZONE",
                "04:00-08:00 UTC+8 should be POST_US_DEADZONE")
    assert_true(post_us["adjustment_direction"] == "NEUTRAL_CONSERVATIVE",
                "POST_US_DEADZONE should remain neutral conservative")
    assert_true(post_us["backtest_delta_pp"] == 0.09,
                "POST_US_DEADZONE should expose +0.09pp neutral delta")

    for iso_text, expected in (
            ("2026-04-17T03:59:00+08:00", "US_DEEP_POST_CATALYST"),
            ("2026-04-17T04:00:00+08:00", "POST_US_DEADZONE"),
            ("2026-04-17T07:59:00+08:00", "POST_US_DEADZONE"),
            ("2026-04-17T08:00:00+08:00", "ASIA_MORNING"),
            ("2026-04-17T11:29:00+08:00", "ASIA_MORNING"),
            ("2026-04-17T11:30:00+08:00", "ASIA_AFTERNOON_LULL"),
            ("2026-04-17T17:59:00+08:00", "LONDON_EARLY"),
            ("2026-04-17T18:00:00+08:00", "PRE_US_TRAPDOOR"),
            ("2026-04-17T21:29:00+08:00", "PRE_US_TRAPDOOR"),
            ("2026-04-17T21:30:00+08:00", "US_OPEN_TURBULENCE"),
            ("2026-04-17T22:59:00+08:00", "US_OPEN_TURBULENCE"),
            ("2026-04-17T23:00:00+08:00", "US_DEEP_POST_CATALYST"),
    ):
        ctx, _record = session_for(mod, config, iso_text)
        assert_true(ctx["rationale_code"] == expected,
                    "{} should map to {}".format(iso_text, expected))

    print("signal_session_context_contract: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_session_context_contract: FAIL - " + str(exc))
        sys.exit(1)
