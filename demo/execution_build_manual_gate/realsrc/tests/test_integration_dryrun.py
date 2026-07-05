# -*- coding: utf-8 -*-
import json
import os
import sys
import time
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import fmz_shim

H = 3600000
SPOT = 73400.0
S48 = {74000: (0.45, 0.016), 75000: (0.38, 0.012), 76000: (0.30, 0.008),
       77000: (0.22, 0.005), 78000: (0.15, 0.0035), 79000: (0.10, 0.0025),
       80000: (0.06, 0.0018)}
S48P = {73000: (-0.45, 0.016), 72000: (-0.38, 0.012), 71000: (-0.30, 0.008),
        70000: (-0.22, 0.005), 69000: (-0.15, 0.0035), 68000: (-0.10, 0.0025)}
_BASE = {"t": None}


def _expiry():
    if _BASE["t"] is None:
        _BASE["t"] = int(time.time() * 1000)
    return _BASE["t"] + 48 * H


def _handler_for(chain, suffix):
    def handler(*args):
        _ex, _m, path, query = args
        qs = parse_qs(query or "")
        exp = _expiry()
        if path.endswith("/public/get_instruments"):
            return {"result": [
                {"instrument_name": "BTC-S-%d-%s" % (k, suffix), "strike": k,
                 "option_type": "call" if suffix == "C" else "put",
                 "expiration_timestamp": exp, "kind": "option", "tick_size": 0.0001}
                for k in chain
            ]}
        if path.endswith("/public/get_index_price"):
            return {"result": {"index_price": SPOT}}
        if path.endswith("/public/ticker"):
            inst = qs.get("instrument_name", ["BTC-S-76000-%s" % suffix])[0]
            strike = int(inst.split("-")[2])
            delta, mark = chain[strike]
            return {"result": {"mark_price": mark, "best_bid_price": round(mark * 0.97, 6),
                               "best_ask_price": round(mark * 1.03, 6),
                               "underlying_price": SPOT,
                               "greeks": {"delta": delta, "gamma": 0.00005},
                               "mark_iv": 0.90}}
        if path.endswith("/public/get_instrument"):
            return {"result": {"tick_size": 0.0001, "contract_size": 1, "min_trade_amount": 0.1}}
        if path.endswith("/private/get_account_summary"):
            return {"result": {"margin_model": "segregated_pm", "portfolio_margining_enabled": True}}
        if path.endswith("/private/get_positions"):
            return {"result": []}
        if path.endswith("/private/simulate_portfolio"):
            simpos = json.loads(qs.get("simulated_positions", ["{}"])[0])
            im = 0.025 if len(simpos) == 1 else 0.013
            return {"result": {"initial_margin": im, "maintenance_margin": im * 0.8,
                               "available_funds": 1.0}}
        return {"result": None}
    return handler


def _setup(ST, direction="SHORT_CALL"):
    fmz_shim._STORE.clear()
    fmz_shim._commands.clear()
    fmz_shim.exchange.io_handler = _handler_for(S48 if direction == "SHORT_CALL" else S48P,
                                                "C" if direction == "SHORT_CALL" else "P")
    ST.SETTLEMENT_CURRENCY = "BTC"
    ST.DIRECTION_BIAS = direction
    ST.MANUAL_PLANNING_ALLOWED = True
    ST.MANUAL_AUDIT_CARD_ID = "manual-card-1"
    ST.MANUAL_AUDIT_NOTE = "approved"
    ST.MANUAL_CONTEXT_TTL_MIN = 30
    ST.MENU_SIZE = 6
    ST.SHORT_DELTA_RANGE = (0.15, 0.45)
    ST.PROTECTION_WIDTH_RANGE = (2000, 2500)
    ST.SHORT_DTE_HOURS = (24, 72)
    ST.ORDER_AMOUNT = 0.1
    ST.MIN_MARGIN_RELIEF_RATIO = 0.10
    ST.MIN_SHORT_PREMIUM = 0.0005
    ST.MAX_SPREAD_RATIO = 0.60
    ST.PLAN_WEIGHTS = {"win_rate": 0.50, "rr": 0.50, "manual": 0.0}
    ST.UNDERLYING_REF_PRICE = None


def _manual_context(ST):
    now = ST._now_ms()
    ctx = ST._manual_context_for_cycle(now)
    assert ST.validate_manual_context(ctx, now)["valid"]
    return now, ctx


def test_manual_gate_builds_vertical_menu_from_deribit_chain():
    import strategy as ST
    _setup(ST, "SHORT_CALL")
    now, ctx = _manual_context(ST)
    menu, _pm_ok, _model, reason, _diag = ST._build_menu(now, ST._spot_price(), ctx)
    assert reason == "OK"
    assert 1 <= len(menu) <= 6
    assert all(m["mode"] == 2 for m in menu)
    assert all(m["short_expiry"] == m["protection_expiry"] for m in menu)
    assert [m["plan_no"] for m in menu] == list(range(1, len(menu) + 1))
    assert any(m["qualified"] for m in menu)


def test_manual_gate_menu_is_not_persisted_as_old_plan_order_state():
    import strategy as ST
    _setup(ST, "SHORT_CALL")
    now, ctx = _manual_context(ST)
    menu, _pm_ok, _model, reason, _diag = ST._build_menu(now, ST._spot_price(), ctx)
    assert reason == "OK" and menu
    assert fmz_shim._G("spm_plan_menu_v1") is None


def test_manual_gate_short_put_uses_put_legs_and_lower_protection():
    import strategy as ST
    _setup(ST, "SHORT_PUT")
    now, ctx = _manual_context(ST)
    menu, _pm_ok, _model, reason, _diag = ST._build_menu(now, ST._spot_price(), ctx)
    assert reason == "OK"
    assert menu and all(m["mode"] == 2 for m in menu)
    for item in menu:
        assert item["short_instrument"].endswith("-P")
        assert item["protection_instrument"].endswith("-P")
        assert item["protection_strike"] < item["short_strike"]
