# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import accounting as A


def _approx(a, b, eps=1e-12):
    return abs(a - b) <= eps


def test_option_fee_ccy_cap_and_rate():
    # 低价权利金走比例：0.125*0.001=0.000125 < 0.0003
    assert _approx(A.acct_option_fee_ccy(0.001, 1), 0.000125)
    # 高价权利金走封顶 0.0003
    assert _approx(A.acct_option_fee_ccy(0.01, 1), 0.0003)
    # 数量倍乘
    assert _approx(A.acct_option_fee_ccy(0.001, 2), 0.00025)


def test_option_fee_usd():
    # price_ccy=0.001, index=60000 -> usd=60; cap=18; 0.125*60=7.5 -> 7.5
    assert _approx(A.acct_option_fee_usd(0.001, 1, 60000), 7.5)


def test_mark_slippage_sign():
    assert _approx(A.acct_mark_slippage("buy", 0.0011, 0.0010, 1), 0.0001)
    assert _approx(A.acct_mark_slippage("sell", 0.0009, 0.0010, 1), 0.0001)


def test_chase_cost():
    assert _approx(A.acct_chase_cost("buy", 0.0010, 0.0011, 1), 0.0001)
    assert _approx(A.acct_chase_cost("sell", 0.0010, 0.0009, 1), 0.0001)


def test_spread_cost():
    assert _approx(A.acct_spread_cost(0.0009, 0.0011, 1), 0.0001)
    assert A.acct_spread_cost(None, 0.0011, 1) is None


def test_protection_realized_cost_and_amortization():
    rc = A.acct_protection_realized_cost(0.01, 0.0003, 0.0002, 0.0001, 0.006)
    assert _approx(rc, 0.0046)
    assert _approx(A.acct_protection_cost_per_day(0.0046, 7), 0.0046 / 7)
    assert _approx(A.acct_protection_cost_per_short_cycle(0.0046, 2), 0.0023)
    assert A.acct_protection_cost_per_day(0.0046, 0) is None


def test_full_burn():
    assert _approx(A.acct_full_burn(0.01, 0.0003), 0.0103)


def test_build_report_shape():
    rep = A.acct_build_report({"currency": "BTC", "short_instrument": "X",
                               "margin_relief_ratio": 0.2})
    assert rep["structure_type"] == "VERTICAL_CREDIT_SPREAD"
    assert rep["account_margin_mode"] == "S:PM"
    assert rep["execution_policy"]["maker_only"] is True
    assert rep["execution_policy"]["allow_add_on_same_direction_signal"] is False
    assert rep["spm_report"]["margin_relief_ratio"] == 0.2
