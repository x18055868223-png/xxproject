# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

import test_run_cycle as RC
import fmz_shim


def _setup_manual():
    ST = RC._setup()
    ST.MANUAL_PLANNING_ALLOWED = False
    ST.MANUAL_AUDIT_CARD_ID = ""
    ST.MANUAL_AUDIT_NOTE = ""
    ST.MANUAL_CONTEXT_TTL_MIN = 30
    ST.DIRECTION_BIAS = "SHORT_CALL"
    ST.SHORT_DELTA_RANGE = (0.15, 0.35)
    ST.PROTECTION_WIDTH_RANGE = (2000, 2500)
    ST.SHORT_DTE_HOURS = (24, 72)
    ST.ORDER_AMOUNT = 0.1
    return ST


def test_planning_disabled_waits_for_manual_gate_without_external_receiver():
    ST = _setup_manual()

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("manual-gate run_cycle must not call receive_external")

    ST.receive_external = fail_if_called
    ctx = ST.run_cycle(now_ms=RC._BASE["t"] or 1000)
    assert ctx["console_phase"] == "WAIT_MANUAL_AUDIT_GATE"
    assert ctx.get("manual_gate_status") == "WAIT_MANUAL_AUDIT_GATE"
    assert not ctx.get("pending_candidates")


def test_invalid_manual_context_reports_invalid_without_building_menu():
    ST = _setup_manual()
    ST.MANUAL_PLANNING_ALLOWED = True
    ST.MANUAL_AUDIT_CARD_ID = ""
    ST.receive_external = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("external path called"))
    ctx = ST.run_cycle(now_ms=RC._BASE["t"] or 1000)
    assert ctx["console_phase"] == "MANUAL_CONTEXT_INVALID"
    assert "AUDIT_REFERENCE_MISSING" in (ctx.get("manual_context_errors") or [])
    assert not ctx.get("pending_candidates")


def test_valid_manual_context_builds_display_menu_but_not_lockable_without_vrp_context():
    ST = _setup_manual()
    ST.MANUAL_PLANNING_ALLOWED = True
    ST.MANUAL_AUDIT_CARD_ID = "BTC #4501"
    ST.MANUAL_AUDIT_NOTE = "manual approved planning"
    ST.receive_external = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("external path called"))

    ctx = ST.run_cycle()

    assert ctx["console_phase"] == "PLAN_MENU_READY"
    assert ctx.get("manual_context", {}).get("audit_reference", {}).get("card_id") == "BTC #4501"
    assert ctx.get("display_candidates_count", 0) > 0
    assert ctx.get("lockable_candidates_count") == 0
    assert ctx.get("not_lockable_reason") == "VRP_CONTEXT_MISSING"
    assert not ctx.get("pending_candidates")


def test_existing_position_manages_without_external_layer():
    ST = _setup_manual()
    ST.receive_external = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("external path called"))
    fmz_shim._G(ST._POSITION_KEY, {
        "position_id": "pos-manual",
        "manual_context_id": "manual-1",
        "audit_card_id": "BTC #4501",
        "side": "CALL",
        "short_instrument": "BTC-S-76000-C",
        "long_instrument": "BTC-S-78000-C",
        "remaining_short_qty": 0.1,
        "long_remaining_qty": 0.1,
        "short_expiry_ts": int(__import__("time").time() * 1000) + 48 * RC.H,
        "entry_risk_anchor": {
            "entry_price": RC.SPOT,
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
