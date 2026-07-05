# -*- coding: utf-8 -*-
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from urllib.parse import parse_qs

import fmz_shim

_BASE = {"t": None}
H = 3600000
SPOT = 73400.0
S48 = {
    74000: (0.45, 0.016),
    75000: (0.38, 0.012),
    76000: (0.30, 0.008),
    77000: (0.22, 0.005),
    78000: (0.15, 0.0035),
    79000: (0.10, 0.0025),
    80000: (0.06, 0.0018),
}


def _instruments(now_ms):
    exp = now_ms + 48 * H
    return [{"instrument_name": "BTC-S-%d-C" % k, "strike": k, "option_type": "call",
             "expiration_timestamp": exp, "kind": "option", "tick_size": 0.0001}
            for k in S48]


def _quote(inst):
    strike = int(inst.split("-")[2])
    delta, mark = S48[strike]
    return {"mark_price": mark, "best_bid_price": round(mark * 0.97, 6),
            "best_ask_price": round(mark * 1.03, 6), "underlying_price": SPOT,
            "greeks": {"delta": delta, "gamma": 0.00005}, "mark_iv": 0.90}


def _handler(*args):
    _ex, _m, path, query = args
    qs = parse_qs(query or "")
    if _BASE["t"] is None:
        _BASE["t"] = int(time.time() * 1000)
    now = _BASE["t"]
    if path.endswith("/public/get_instruments"):
        return {"result": _instruments(now)}
    if path.endswith("/public/get_index_price"):
        return {"result": {"index_price": SPOT}}
    if path.endswith("/public/ticker"):
        return {"result": _quote(qs.get("instrument_name", ["BTC-S-76000-C"])[0])}
    if path.endswith("/public/get_instrument"):
        return {"result": {"tick_size": 0.0001, "contract_size": 1, "min_trade_amount": 0.1}}
    if path.endswith("/private/get_account_summary"):
        return {"result": {"margin_model": "segregated_pm", "portfolio_margining_enabled": True,
                           "initial_margin": 0.02, "maintenance_margin": 0.015}}
    if path.endswith("/private/get_positions"):
        return {"result": []}
    if path.endswith("/private/simulate_portfolio"):
        simpos = json.loads(qs.get("simulated_positions", ["{}"])[0])
        im = 0.025 if len(simpos) == 1 else 0.013
        return {"result": {"initial_margin": im, "maintenance_margin": im * 0.8,
                           "available_funds": 1.0}}
    return {"result": None}


def _setup():
    fmz_shim._STORE.clear()
    fmz_shim._commands.clear()
    fmz_shim.exchange.io_handler = _handler
    import strategy as ST
    import execution as EX
    ST.SETTLEMENT_CURRENCY = "BTC"
    EX.SETTLEMENT_CURRENCY = "BTC"
    ST.MANUAL_PLANNING_ALLOWED = True
    ST.MANUAL_AUDIT_CARD_ID = "manual-card-1"
    ST.MANUAL_AUDIT_NOTE = "operator approved planning"
    ST.MANUAL_CONTEXT_TTL_MIN = 30
    ST.DIRECTION_BIAS = "SHORT_CALL"
    ST.SHORT_DELTA_RANGE = (0.15, 0.45)
    ST.PROTECTION_WIDTH_RANGE = (2000, 2500)
    ST.SHORT_DTE_HOURS = (24, 72)
    ST.ORDER_AMOUNT = 0.1
    ST.MENU_SIZE = 6
    ST.MIN_MARGIN_RELIEF_RATIO = 0.10
    ST.MIN_SHORT_PREMIUM = 0.0005
    ST.MAX_SPREAD_RATIO = 0.60
    ST.PLAN_WEIGHTS = {"win_rate": 0.50, "rr": 0.50, "manual": 0.0}
    ST.UNDERLYING_REF_PRICE = None
    ST.APPROVAL_TTL_MS = 30 * 60 * 1000
    ST.ALLOW_ENTRY_TRADING = False
    ST.ALLOW_EXIT_TRADING = False
    ST.ALLOW_HEDGE_TRADING = False
    ST.KILL_NEW_RISK = False
    ST.EMERGENCY_REDUCE_ONLY = False
    ST.RISK_EXIT_MAX_SPEND = 0.0
    ST.ROBOT_ID = "r-test"
    ST.HEDGE_VENUE = "DERIBIT"
    ST._LOCKED["detail_id"] = None
    ST.ledger_set_state(ST.S_NO_POSITION)
    return ST


def _fat_vrp_context():
    return {
        "side": "SHORT_CALL",
        "front_anchor_iv": 1.20,
        "atm_front_iv": 1.10,
        "term_reference_iv_5_10d": 1.10,
        "rv_24h": 0.20,
        "rv_72h": 0.18,
        "rv_7d": 0.16,
        "rv_percentile": 0.50,
        "history_days": 90,
        "executable_short_iv": 1.25,
        "executable_protection_iv": 1.00,
    }


def _seed_market_context(ST, now_ms=None, market_context=None):
    now_ms = now_ms or ST._now_ms()
    ctx = ST._manual_context_for_cycle(now_ms)
    ctx["market_context"] = market_context or _fat_vrp_context()
    fmz_shim._G(ST._MANUAL_CONTEXT_KEY, ctx)
    return ctx


def _candidate():
    return {
        "id": 1,
        "short_instrument": "BTC-S-76000-C",
        "protection_instrument": "BTC-S-78000-C",
        "short_expiry_label": "S",
        "short_strike": 76000,
        "protection_strike": 78000,
        "short_expiry": (_BASE["t"] or int(time.time() * 1000)) + 48 * H,
        "short_dte_hours": 48,
        "short_delta": 0.30,
        "amount": 0.1,
        "qualified": True,
        "net_credit_effective": 0.0003,
        "margin_relief_ratio": 0.40,
        "width": 2000,
        "vrp_state": "PASS",
        "budget_decision": "ALLOW",
        "execution_feasibility_score": 80.0,
    }


def test_run_cycle_no_vrp_context_displays_menu_without_codes():
    ST = _setup()
    ctx = ST.run_cycle()
    assert ctx["console_phase"] == "PLAN_MENU_READY"
    assert ctx["display_candidates_count"] > 0
    assert ctx["lockable_candidates_count"] == 0
    assert ctx["not_lockable_reason"] == "VRP_CONTEXT_MISSING"
    assert not ctx["pending_candidates"]


def test_run_cycle_vrp_context_builds_library_then_locks():
    ST = _setup()
    _seed_market_context(ST)
    first = ST.run_cycle()
    assert first["console_phase"] == "HARD_APPROVAL_WAIT"
    assert first["pending_candidates"]
    code = first["pending_candidates"][0]["confirm_code"]
    fmz_shim._commands.append("执行:" + code)
    locked = ST.run_cycle()
    assert locked["last_command"] == "EXECUTE"
    assert locked["last_command_outcome"] == "locked"
    assert locked["console_phase"] == "PLAN_LOCKED"
    assert fmz_shim._G(ST._LOCKED_KEY)


def test_run_cycle_locked_plan_expires_and_rebuilds_library():
    ST = _setup()
    ST.APPROVAL_TTL_MS = 1000
    now = int(time.time() * 1000)
    _seed_market_context(ST, now)
    first = ST.run_cycle(now)
    fmz_shim._commands.append("执行:" + first["pending_candidates"][0]["confirm_code"])
    ST.run_cycle(now + 100)
    expired = ST.run_cycle(now + 2000)
    assert expired["lineage_invalidation"] == "APPROVAL_EXPIRED"
    assert expired["console_phase"] == "HARD_APPROVAL_WAIT"
    assert expired["pending_candidates"]


def test_run_cycle_manual_context_change_invalidates_old_approval():
    ST = _setup()
    now = int(time.time() * 1000)
    _seed_market_context(ST, now)
    first = ST.run_cycle(now)
    fmz_shim._commands.append("执行:" + first["pending_candidates"][0]["confirm_code"])
    ST.run_cycle(now + 1)
    ST.MANUAL_AUDIT_CARD_ID = "manual-card-2"
    changed = ST.run_cycle(now + 2)
    assert changed["lineage_invalidation"] == "MANUAL_CONTEXT_CHANGED"
    assert fmz_shim._G(ST._LOCKED_KEY) is None


def test_precommit_vrp_missing_context_fails_closed():
    ST = _setup()
    now = int(time.time() * 1000)
    ctx = ST._manual_context_for_cycle(now)
    lib = ST.build_recommendation_library([_candidate()], "s1", ctx, 1, now,
                                          config_hash=ctx["config_signature"])
    locked = lib["recommendations"][0]
    live = ST._build_precommit_live(locked, SPOT, ctx, now)
    pre = ST.evaluate_precommit_checks(locked, lib, live)
    assert live["vrp_pass"] is None
    assert "vrp_rechecked" in pre["failed"]


def test_precommit_vrp_rechecks_when_market_context_complete():
    ST = _setup()
    now = int(time.time() * 1000)
    ctx = _seed_market_context(ST, now)
    lib = ST.build_recommendation_library([_candidate()], "s1", ctx, 1, now,
                                          config_hash=ctx["config_signature"])
    locked = lib["recommendations"][0]
    live = ST._build_precommit_live(locked, SPOT, ctx, now)
    assert live["vrp_pass"] is True
    assert (live["vrp_gate"] or {}).get("pass") is True


def test_existing_position_manages_without_manual_planning_enabled():
    ST = _setup()
    ST.MANUAL_PLANNING_ALLOWED = False
    fmz_shim._G(ST._POSITION_KEY, {
        "position_id": "pos-manual",
        "manual_context_id": "manual-1",
        "audit_card_id": "manual-card",
        "side": "CALL",
        "short_instrument": "BTC-S-76000-C",
        "long_instrument": "BTC-S-78000-C",
        "remaining_short_qty": 0.1,
        "long_remaining_qty": 0.1,
        "short_expiry_ts": int(time.time() * 1000) + 48 * H,
        "entry_risk_anchor": {
            "entry_price": SPOT,
            "entry_dte_hours": 48,
            "entry_loss_boundary": 77000,
            "entry_touch_probability": 0.10,
            "entry_probability_confidence": "HIGH",
        },
    })
    ST.ledger_set_state(ST.S_SHORT_ACTIVE_PROTECTED)
    ctx = ST.run_cycle()
    assert ctx["console_phase"] == "POSITION_MANAGE"
    assert ctx.get("entry_snapshot", {}).get("position_id") == "pos-manual"
