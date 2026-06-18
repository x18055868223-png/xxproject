# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import execution as EX

# 捕获原始依赖，patch 后还原，避免泄漏到后续测试文件（execution 早于 integration 运行）
_ORIG = {k: getattr(EX, k) for k in
         ("dbt_ticker", "dbt_get_instrument", "dbt_place_order",
          "dbt_get_order_state", "dbt_cancel", "Sleep")}


def _restore_ex():
    for k, v in _ORIG.items():
        setattr(EX, k, v)
    EX.ALLOW_ENTRY_TRADING = False
    EX.KILL_NEW_RISK = False
    EX.EMERGENCY_REDUCE_ONLY = False
    EX.ALLOW_TRADING = False
    EX.KILL_SWITCH = False


def _approx(a, b, eps=1e-9):
    return abs(a - b) <= eps


def test_buy_price_step0_uses_min_mark_ask():
    # mark 低于 ask-tick -> step0 = mark
    assert _approx(EX.exec_buy_price(0.0010, 0.0013, 0.0001, 0), 0.0010)
    # mark 高于 ask-tick -> 封顶 ask-tick
    assert _approx(EX.exec_buy_price(0.0013, 0.0013, 0.0001, 0), 0.0012)


def test_buy_price_chase_one_step_clamped():
    # step1 = step0 + tick，封顶 ask-tick
    assert _approx(EX.exec_buy_price(0.0010, 0.0013, 0.0001, 1), 0.0011)
    assert _approx(EX.exec_buy_price(0.0011, 0.0013, 0.0001, 1), 0.0012)  # 不超过 ask-tick


def test_sell_price_step0_uses_max_mark_bid():
    assert _approx(EX.exec_sell_price(0.0010, 0.0007, 0.0001, 0), 0.0010)
    # mark 低于 bid+tick -> 封底 bid+tick
    assert _approx(EX.exec_sell_price(0.0005, 0.0007, 0.0001, 0), 0.0008)


def test_sell_price_chase_one_step_clamped():
    assert _approx(EX.exec_sell_price(0.0010, 0.0007, 0.0001, 1), 0.0009)
    assert _approx(EX.exec_sell_price(0.0009, 0.0007, 0.0001, 1), 0.0008)  # 不低于 bid+tick


def test_tick_rounding_no_cross():
    # mark 不在 tick 网格上 -> 买价向下取整，卖价向上取整，避免越价
    assert _approx(EX.exec_buy_price(0.00105, 0.0013, 0.0001, 0), 0.0010)
    assert _approx(EX.exec_sell_price(0.00105, 0.0007, 0.0001, 0), 0.0011)


def test_price_for_dispatch():
    assert _approx(EX.exec_price_for("buy", 0.0010, 0.0007, 0.0013, 0.0001, 0), 0.0010)
    assert _approx(EX.exec_price_for("sell", 0.0010, 0.0007, 0.0013, 0.0001, 0), 0.0010)


def test_spread_ratio():
    assert abs(EX.exec_spread_ratio({"best_bid": 0.0097, "best_ask": 0.0103}) - 0.06) < 1e-6
    assert EX.exec_spread_ratio({"best_bid": None, "best_ask": 0.01}) is None


def _mock_quote(_inst):
    return {"mark_price": 0.01, "best_bid_price": 0.0097, "best_ask_price": 0.0103,
            "greeks": {"delta": 0.3}}


def test_maker_fill_dry_shows_intent():
    EX.ALLOW_TRADING = False
    EX.dbt_ticker = _mock_quote
    EX.dbt_get_instrument = lambda i: {"tick_size": 0.0001}
    r = EX.exec_maker_only_fill("sell", "X", 0.1)
    assert r["dry"] and r["filled"] == 0.0 and r["intended_prices"]
    _restore_ex()


def test_maker_fill_live_fills():
    EX.ALLOW_ENTRY_TRADING = True
    EX.dbt_ticker = _mock_quote
    EX.dbt_get_instrument = lambda i: {"tick_size": 0.0001}
    EX.dbt_place_order = lambda side, inst, amt, price, **k: {
        "order": {"order_id": "1", "order_state": "open", "filled_amount": 0.0}}
    EX.dbt_get_order_state = lambda oid: {
        "order": {"order_id": oid, "order_state": "filled", "filled_amount": 0.1,
                  "average_price": 0.0097}}
    EX.dbt_cancel = lambda oid: {"order_id": oid}
    EX.Sleep = lambda ms: None
    r = EX.exec_maker_only_fill("sell", "X", 0.1)
    assert not r["dry"] and _approx(r["filled"], 0.1) and _approx(r["avg_price"], 0.0097)
    _restore_ex()


def test_maker_fill_wide_spread_guard():
    EX.ALLOW_ENTRY_TRADING = True
    EX.dbt_ticker = lambda i: {"mark_price": 0.01, "best_bid_price": 0.005,
                               "best_ask_price": 0.02, "greeks": {}}
    EX.dbt_get_instrument = lambda i: {"tick_size": 0.0001}
    r = EX.exec_maker_only_fill("sell", "X", 0.1)
    assert r.get("reason") == "WIDE_SPREAD" and r["filled"] == 0.0
    _restore_ex()
