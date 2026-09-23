import math

import pytest

from tools import astra_commercial_math_v17 as m
from tools import astra_underwriting_v16 as v16


def test_inverse_put_spread_keeps_inverse_tail_growth():
    q = 0.1
    assert m.inverse_spread_payout("put", 100000, 98000, 97000, q) == pytest.approx(0.0020618556701)
    shallow = m.inverse_spread_payout("put", 100000, 98000, 97000, q)
    deep = m.inverse_spread_payout("put", 100000, 98000, 50000, q)
    assert deep == pytest.approx(0.004)
    assert deep > shallow


def test_inverse_leg_zero_partial_and_call_symmetry():
    assert m.inverse_leg_payout("put", 100000, 101000, 0.1) == 0.0
    assert m.inverse_leg_payout("put", 100000, 97000, 0.1) == pytest.approx(3000 / 97000 * 0.1)
    assert m.inverse_leg_payout("call", 100000, 101000, 0.1) == pytest.approx(1000 / 101000 * 0.1)
    assert m.inverse_spread_payout("call", 98000, 100000, 101000, 0.1) == pytest.approx(2000 / 101000 * 0.1)


def test_spread_leg_order_and_positive_inputs_are_checked():
    with pytest.raises(ValueError, match="put credit spread"):
        m.inverse_spread_payout("put", 98000, 100000, 97000)
    with pytest.raises(ValueError, match="call credit spread"):
        m.bs_leg_prices_btc("call", 100000, 100000, 98000, 24, 0.6)
    for bad in (0, -1, math.inf, math.nan):
        with pytest.raises(ValueError):
            m.inverse_leg_payout("put", bad, 97000)


@pytest.mark.parametrize(
    "side,spot,short,long,hours,iv,quantity",
    [
        ("put", 100000, 100000, 98000, 0, 0.6, 0.1),
        ("put", 97123.4, 100000, 98000, 24, 0.6, 0.1),
        ("call", 97123.4, 98000, 100000, 16.5, 0.4, 2.0),
    ],
)
def test_bs_leg_prices_match_v16_spread_quote(side, spot, short, long, hours, iv, quantity):
    short_premium, long_premium = m.bs_leg_prices_btc(side, spot, short, long, hours, iv, quantity)
    expected_spread = float(v16.bs_spread_btc(side, spot, short, long, hours, iv)) * quantity
    assert short_premium - long_premium == pytest.approx(expected_spread, abs=1e-14)


def test_bs_t0_uses_intrinsic_btc_not_time_value():
    short_premium, long_premium = m.bs_leg_prices_btc("put", 97000, 100000, 98000, 0, 0.6, 0.1)
    assert short_premium == pytest.approx(3000 / 97000 * 0.1)
    assert long_premium == pytest.approx(1000 / 97000 * 0.1)


def test_bs_far_tail_protection_leg_stays_nonnegative_without_clipping():
    side, spot, short, long, hours, iv, quantity = "call", 30265.22, 30500.0, 32500.0, 24.0, 0.173421491151574, 0.1
    short_premium, long_premium = m.bs_leg_prices_btc(side, spot, short, long, hours, iv, quantity)
    expected_spread = float(v16.bs_spread_btc(side, spot, short, long, hours, iv)) * quantity
    assert long_premium >= 0.0
    assert long_premium == pytest.approx(2.4520623292526193e-19, abs=1e-30)
    assert short_premium - long_premium == pytest.approx(expected_spread, abs=1e-12)


def test_static_cashflow_unknown_is_not_zero_and_hedge_is_optional():
    assert m.static_cashflow(0.01, None, 0.001) == {"static_net_btc": None, "hedged_net_btc": None}
    assert m.static_cashflow(0.01, 0.004, 0.001) == pytest.approx(
        {"static_net_btc": 0.005, "hedged_net_btc": None}
    )
    assert m.static_cashflow(0.01, 0.004, 0.001, -0.002) == pytest.approx(
        {"static_net_btc": 0.005, "hedged_net_btc": 0.003}
    )


def test_exit_cashflow_does_not_double_count_terminal_payout():
    exit_result = m.exit_cashflow(0.01, 0.003, 0.001)
    static_result = m.static_cashflow(0.01, 0.02, 0.001)
    assert exit_result["static_net_btc"] == pytest.approx(0.006)
    assert static_result["static_net_btc"] == pytest.approx(-0.011)
    assert exit_result["static_net_btc"] != pytest.approx(0.01 - 0.003 - 0.02 - 0.001)


def test_required_net_credit_unknown_propagates_without_default_fee():
    assert m.required_net_credit(0.004, 0.001) == pytest.approx(0.005)
    assert m.required_net_credit(None, 0.001) is None
    assert m.required_net_credit(0.004, None) is None
