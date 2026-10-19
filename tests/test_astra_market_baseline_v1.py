import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest

import astra_market_baseline_v1 as mb

EXPIRY = 1_792_483_200_000
HOUR = 3_600_000


def _put(**over):
    args = dict(side="put", short_strike=82000.0, long_strike=80000.0, forward_usd=82420.08,
                valuation_ms=EXPIRY - int(18.74 * HOUR), expiry_ms=EXPIRY,
                short_iv_pct=31.55, long_iv_pct=44.39, short_delta=-0.36036, long_delta=-0.07184,
                short_mark_btc=0.0036, long_mark_btc=0.0007, net_credit_btc=0.0023125, width_usd=2000.0)
    args.update(over)
    return mb.baseline(**args)


def test_prob_below_matches_black76_closed_form():
    years = 0.25
    v = 0.5 * math.sqrt(years)
    d2 = (math.log(100.0 / 90.0) - 0.5 * v * v) / v
    assert mb.prob_below(100.0, 90.0, 0.5, years) == pytest.approx(0.5 * math.erfc(d2 / math.sqrt(2)))
    # at-the-money forward: slightly above one half because of the -v^2/2 term
    assert 0.5 < mb.prob_below(100.0, 100.0, 0.5, years) < 0.6


def test_live_put_sample_is_close_to_delta_and_ordered():
    out = _put()
    probs = out["probabilities"]
    assert out["status"] == "ok"
    assert probs["p_itm_short"] == pytest.approx(0.366, abs=0.005)
    assert abs(probs["p_itm_short"] - out["delta_reference"]["short_abs_delta"]) < 0.02
    assert probs["p_touch_short"] == pytest.approx(min(1.0, 2 * probs["p_itm_short"]))
    # deeper strikes are less likely: max loss < loss < short in the money
    assert probs["p_max_loss"] < probs["p_loss"] < probs["p_itm_short"]
    # breakeven matches the snapshot's own breakeven_usd (81810.81)
    assert probs["loss_boundaries_usd"][0] == pytest.approx(81810.81, abs=0.01)


def test_call_loss_region_is_bounded_above_by_width_over_credit():
    out = mb.baseline(side="call", short_strike=84000.0, long_strike=86000.0, forward_usd=83000.0,
                      valuation_ms=EXPIRY - 20 * HOUR, expiry_ms=EXPIRY,
                      short_iv_pct=35.0, long_iv_pct=40.0, net_credit_btc=0.002, width_usd=2000.0)
    probs = out["probabilities"]
    low, high = probs["loss_boundaries_usd"]
    assert low == pytest.approx(84000.0 / (1 - 0.002))
    assert high == pytest.approx(2000.0 / 0.002)
    assert 0 < probs["p_loss"] < probs["p_itm_short"]
    assert probs["p_max_loss"] < probs["p_itm_short"]


def test_credit_covering_the_width_gives_no_call_loss():
    out = mb.baseline(side="call", short_strike=84000.0, long_strike=86000.0, forward_usd=83000.0,
                      valuation_ms=EXPIRY - 20 * HOUR, expiry_ms=EXPIRY,
                      short_iv_pct=35.0, long_iv_pct=40.0, net_credit_btc=0.03, width_usd=2000.0)
    assert out["probabilities"]["p_loss"] == 0.0
    assert out["probabilities"]["loss_boundaries_usd"] == []


def test_missing_iv_or_expired_is_unknown_not_defaulted():
    out = _put(short_iv_pct=None)
    assert out["status"] == "unknown" and "short_iv_missing" in out["reasons"]
    assert out["probabilities"] is None
    expired = _put(valuation_ms=EXPIRY + 1)
    assert expired["status"] == "unknown" and "expired" in expired["reasons"]


def test_unknown_credit_keeps_tail_probabilities_but_no_loss_probability():
    out = _put(net_credit_btc=None)
    assert out["status"] == "ok"
    assert out["probabilities"]["p_loss"] is None
    assert out["compensation"]["status"] == "unknown"


def test_compensation_ratio_and_provisional_tier():
    comp = _put()["compensation"]
    assert comp["mark_spread_btc"] == pytest.approx(0.0029)
    assert comp["net_credit_to_mark"] == pytest.approx(0.0023125 / 0.0029)
    assert comp["tier"] == "fair" and comp["tier_provisional"] is True
    assert _put(net_credit_btc=0.0029)["compensation"]["tier"] == "rich"
    assert _put(net_credit_btc=0.0019)["compensation"]["tier"] == "thin"


def test_strike_order_must_match_side():
    with pytest.raises(ValueError):
        _put(short_strike=80000.0, long_strike=82000.0)


def test_from_capture_reads_greeks_from_raw_books_not_snapshot_market():
    snapshot = {"contract": {"side": "put", "short_strike_usd": 82000.0, "long_strike_usd": 80000.0,
                             "expiry_ms": EXPIRY, "width_usd": 2000.0},
                "economics": {"net_credit_after_fee_btc": 0.0023125},
                "market": {"valuation_ts_ms": EXPIRY - int(18.74 * HOUR), "quote": {}}}
    raw_market = {"valuation_ts_ms": EXPIRY - int(18.74 * HOUR),
                  "short_book": {"underlying_price": 82420.08, "mark_iv": 31.55, "mark_price": 0.0036,
                                 "greeks": {"delta": -0.36036}},
                  "long_book": {"underlying_price": 82420.08, "mark_iv": 44.39, "mark_price": 0.0007,
                                "greeks": {"delta": -0.07184}}}
    out = mb.from_capture(snapshot, raw_market)
    assert out["status"] == "ok"
    assert out["probabilities"]["p_itm_short"] == pytest.approx(_put()["probabilities"]["p_itm_short"])
    assert out["delta_reference"]["long_abs_delta"] == pytest.approx(0.07184)


def test_call_p_loss_equals_the_band_between_its_two_boundaries():
    years = 20 * HOUR / mb.YEAR_MS
    out = mb.baseline(side="call", short_strike=84000.0, long_strike=86000.0, forward_usd=83000.0,
                      valuation_ms=EXPIRY - 20 * HOUR, expiry_ms=EXPIRY,
                      short_iv_pct=35.0, long_iv_pct=40.0, net_credit_btc=0.002, width_usd=2000.0)
    low, high = out["probabilities"]["loss_boundaries_usd"]
    iv_low = 0.35 + (low - 84000.0) / 2000.0 * 0.05   # interpolated between the legs
    expected = (1 - mb.prob_below(83000.0, low, iv_low, years)) - (1 - mb.prob_below(83000.0, high, 0.40, years))
    assert out["probabilities"]["p_loss"] == pytest.approx(expected)


def test_put_credit_covering_the_width_moves_breakeven_below_the_long_strike():
    out = _put(net_credit_btc=0.03)      # W/Kl = 0.025, so no loss down to 80000
    low = out["probabilities"]["loss_boundaries_usd"][0]
    assert low == pytest.approx(2000.0 / 0.03) and low < 80000.0
    assert out["probabilities"]["p_loss"] < out["probabilities"]["p_max_loss"]


def test_valuation_at_expiry_is_unknown():
    assert _put(valuation_ms=EXPIRY)["status"] == "unknown"


def test_non_positive_mark_spread_leaves_compensation_unknown():
    assert _put(short_mark_btc=0.0007, long_mark_btc=0.0007)["compensation"]["status"] == "unknown"


def test_from_capture_with_missing_identity_is_unknown_not_an_exception():
    out = mb.from_capture({"contract": {"side": "put"}}, {})
    assert out["status"] == "unknown"
    assert {"short_strike_missing", "expiry_missing", "valuation_missing"} <= set(out["reasons"])
