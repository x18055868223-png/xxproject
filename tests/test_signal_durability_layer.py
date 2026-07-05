import datetime
import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGNAL_FILE = (
    ROOT / "demo" / "\u6700\u65b0\u4ea4\u4ed8\u7269" /
    "neutral_regulation_demo_fmz.py"
)


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


def assert_close(actual, expected, message, eps=1e-9):
    if actual is None or abs(actual - expected) > eps:
        raise AssertionError(
            "{}: expected {}, got {}".format(message, expected, actual)
        )


def ms_utc(year, month, day, hour, minute):
    dt = datetime.datetime(
        year, month, day, hour, minute,
        tzinfo=datetime.timezone.utc,
    )
    return int(dt.timestamp() * 1000)


def bullish_decision():
    return {
        "lean": "BULLISH_WEAK",
        "side_hint": "put_credit_spread",
        "confidence": 64,
        "trade_allowed": True,
    }


def durable_cross():
    return {
        "anchor": {
            "effective_flip_point": 100.0,
            "flip_point": 99.5,
            "band_half": 2.0,
            "anchor_score": 72.0,
        },
        "gamma_regime": {
            "regime": "POSITIVE_GAMMA_PINNING",
            "net_gamma_notional": 12_400_000.0,
        },
        "funding": {
            "last_rate": 0.00008,
            "funding_state": "normal",
        },
    }


def test_comfort_window_boundaries(mod, config):
    checks = (
        (ms_utc(2026, 7, 3, 13, 59), "NORMAL_WINDOW", "REGULAR_SESSION_ASSUMED", "NW"),
        (ms_utc(2026, 7, 3, 14, 0), "US_T2_EARLY_REPRICE", "REGULAR_SESSION_ASSUMED", "T2E"),
        (ms_utc(2026, 7, 3, 14, 59), "US_T2_EARLY_REPRICE", "REGULAR_SESSION_ASSUMED", "T2E"),
        (ms_utc(2026, 7, 3, 15, 0), "US_T2_CORE_COMFORT", "REGULAR_SESSION_ASSUMED", "T2C"),
        (ms_utc(2026, 7, 3, 17, 0), "US_T2_CORE_COMFORT", "REGULAR_SESSION_ASSUMED", "T2C"),
        (ms_utc(2026, 7, 3, 17, 1), "NORMAL_WINDOW", "REGULAR_SESSION_ASSUMED", "NW"),
        (ms_utc(2026, 7, 4, 14, 0), "US_T2_TIME_ONLY", "WEEKEND_TIME_ONLY", "T2T"),
        (ms_utc(2026, 7, 4, 17, 0), "US_T2_TIME_ONLY", "WEEKEND_TIME_ONLY", "T2T"),
        (ms_utc(2026, 7, 4, 17, 1), "NORMAL_WINDOW", "WEEKEND_TIME_ONLY", "NW"),
    )
    for event_ms, code, basis, token in checks:
        ctx = mod.build_comfort_window_context(event_ms, config)
        assert_equal(ctx["schema_name"], "SignalComfortWindow",
                     "comfort schema name")
        assert_equal(ctx["schema_version"], "nrd.signal.comfort_window.v1",
                     "comfort schema version")
        assert_equal(ctx["tag"], code, "comfort tag")
        assert_equal(ctx["calendar_state"], basis, "comfort calendar state")
        assert_equal(ctx["window_code"], code, "comfort code")
        assert_equal(ctx["session_assumption"], basis, "comfort basis")
        assert_equal(ctx["brief_token"], token, "comfort brief token")
        assert_equal(ctx["audit_scope"], "AUDIT_ONLY", "comfort audit scope")


def test_price_path_efficiency_exact_and_proxy(mod):
    exact = mod.compute_price_path_efficiency(
        price_points=[100.0, 105.0, 102.0, 110.0])
    assert_equal(exact["method"], "EXACT_PRICE_POINTS", "exact PPE method")
    assert_close(exact["value"], 10.0 / 16.0, "exact PPE value")
    assert_true("PPE_PROXY_OHLC_USED" not in exact["reason_codes"],
                "exact PPE should not be marked as proxy")

    proxy = mod.compute_price_path_efficiency(
        ohlc={"open": 100.0, "high": 112.0, "low": 98.0, "close": 110.0})
    assert_equal(proxy["method"], "PROXY_OHLC", "proxy PPE method")
    assert_close(proxy["value"], 10.0 / 14.0, "proxy PPE value")
    assert_true("PPE_PROXY_OHLC_USED" in proxy["reason_codes"],
                "OHLC PPE should declare proxy use")


def test_anchor_native_uses_anchor_axis_and_band(mod, config):
    pad = mod.build_price_anchor_durability(
        {"market_context": {"price": 100.8}},
        bullish_decision(),
        durable_cross(),
        {},
        config,
    )
    anchor = pad["sublayers"]["anchor_native"]
    assert_equal(pad["anchor_price"], 100.0,
                 "effective flip point should be anchor price")
    assert_equal(pad["anchor_price_source"], "factor_cross_section.anchor.effective_flip_point",
                 "anchor source")
    assert_close(pad["band"]["half_width"], 2.0, "anchor band half")
    assert_close(anchor["score"], 0.72, "native anchor score")
    assert_equal(anchor["state"], "ANCHOR_DURABLE", "native anchor state")


def test_options_gamma_and_funding_are_overlays_not_autobreakers(mod, config):
    assert_equal(mod.direction_sign_from_decision(
        {"lean": "NEUTRAL", "side_hint": "put_credit_spread"}), 1,
        "put credit side should map to bullish structure")
    assert_equal(mod.direction_sign_from_decision(
        {"lean": "NEUTRAL"}, selected_side="call_credit_spread"), -1,
        "call credit side should map to bearish structure")
    cross = durable_cross()
    cross["gamma_regime"] = {
        "regime": "NEGATIVE_GAMMA_AMPLIFYING",
        "net_gamma_notional": -12_400_000.0,
    }
    cross["funding"] = {"last_rate": -0.00018}
    pad = mod.build_price_anchor_durability(
        {"market_context": {"price": 100.8}},
        bullish_decision(),
        cross,
        {},
        config,
    )
    assert_true(pad["sublayers"]["options_gamma"]["state"] != "ANCHOR_BROKEN",
                "negative gamma alone must not break anchor durability")
    assert_equal(pad["sublayers"]["perp_funding"]["interpretation"],
                 "ADVERSE_LEVERAGE_BUILD",
                 "negative funding should be adverse for bullish structures")
    cross["funding"] = {"last_rate": 0.00008}
    healthy = mod.build_price_anchor_durability(
        {"market_context": {"price": 100.8}},
        bullish_decision(),
        cross,
        {},
        config,
    )
    assert_true(healthy["sublayers"]["perp_funding"]["score"] >= 0.70,
                "mild aligned funding alone should not be bad")
    assert_true(pad["state"] != "ANCHOR_BROKEN",
                "negative gamma overlay alone should not break composite")


def test_high_ppe_cannot_autopass_broken_anchor(mod, config):
    cross = durable_cross()
    cross["anchor"] = {
        "flip_point": 100.0,
        "band_half": 2.0,
        "anchor_score": 20.0,
    }
    pad = mod.build_price_anchor_durability(
        {
            "market_context": {"price": 110.0},
            "price_points": [100.0, 105.0, 110.0],
        },
        bullish_decision(),
        cross,
        {},
        config,
    )
    assert_equal(pad["sublayers"]["price_efficiency"]["ppe"], 1.0,
                 "monotonic path has high PPE")
    assert_equal(pad["sublayers"]["price_efficiency"]["interpretation"],
                 "PPE_FAVORABLE_EFFICIENT_NOT_AUTOPASS",
                 "favorable high PPE should not autopass")
    assert_equal(pad["state"], "ANCHOR_BROKEN",
                 "high PPE must not autopass a broken anchor")
    assert_true("PPE_CANNOT_AUTOPASS_ANCHOR" in pad["reason_codes"],
                "composite should document PPE autopass guard")


def test_composite_states_and_temporal_session(mod, config):
    session = mod.classify_signal_session_context(
        ms_utc(2026, 7, 3, 15, 30), config)
    layer = mod.build_signal_durability_layer(
        {
            "market_context": {"price": 100.8},
            "price_points": [100.0, 100.4, 100.8],
            "confirmed_time": ms_utc(2026, 7, 3, 15, 30),
        },
        bullish_decision(),
        durable_cross(),
        {"session_context": session},
        config,
    )
    assert_equal(layer["audit_scope"], "AUDIT_ONLY", "layer audit scope")
    assert_equal(layer["schema_version"], "nrd.signal.durability_layer.v1",
                 "layer schema version")
    assert_true(layer["headline_score"] is not None,
                "layer headline score")
    assert_equal(layer["headline_state"], "ANCHOR_DURABLE",
                 "layer headline state")
    assert_equal(layer["score_semantics"],
                 "STRUCTURE_HEALTH_INDEX_NOT_PROBABILITY",
                 "layer score semantics")
    assert_true(layer["policy"]["not_direction_factor"] is True,
                "policy not direction factor")
    assert_true(layer["policy"]["not_execution_gate"] is True,
                "policy not execution gate")
    assert_true(layer["policy"]["not_confidence_multiplier"] is True,
                "policy not confidence multiplier")
    assert_true(layer["temporal_session"]["rationale_code"]
                == session["rationale_code"],
                "temporal session mirrors display context")
    assert_equal(layer["temporal_session_score_role"], "DISPLAY_CONTEXT_ONLY",
                 "temporal session does not score price anchor")
    assert_equal(layer["price_anchor_durability"]["durability_state"], "ANCHOR_DURABLE",
                 "durable composite state")
    assert_true(isinstance(layer["price_anchor_durability"]["layer_scores"], dict),
                "price anchor exposes layer_scores")

    gap = mod.build_price_anchor_durability({}, bullish_decision(), {}, {}, config)
    assert_equal(gap["durability_state"], "ANCHOR_DATA_GAP", "missing price and anchor state")


def main():
    mod = load_signal_module()
    config = dict(mod.CONFIG)
    test_comfort_window_boundaries(mod, config)
    test_price_path_efficiency_exact_and_proxy(mod)
    test_anchor_native_uses_anchor_axis_and_band(mod, config)
    test_options_gamma_and_funding_are_overlays_not_autobreakers(mod, config)
    test_high_ppe_cannot_autopass_broken_anchor(mod, config)
    test_composite_states_and_temporal_session(mod, config)
    print("signal_durability_layer: PASS")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("signal_durability_layer: FAIL - " + str(exc))
        sys.exit(1)
