# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import hedge_risk as H


def test_short_call_hedge_ready_uses_drift_persistence_and_exit_friction():
    entry = H.build_entry_risk_anchor(
        direction_bias="SHORT_CALL",
        entry_price=73000,
        entry_dte_hours=48,
        entry_short_delta=0.22,
        entry_short_gamma=0.00005,
        entry_iv=0.70,
        entry_loss_boundary=76000,
        entry_edb_side="BEARISH",
        entry_gamma_regime="POSITIVE_GAMMA_PINNING",
    )

    pkg = H.evaluate_position_risk(
        position_id="p1",
        direction_bias="SHORT_CALL",
        entry_risk_anchor=entry,
        current_price=75500,
        dte_hours=36,
        short_delta=0.72,
        short_gamma=0.00012,
        iv=0.75,
        loss_boundary=76000,
        edb={"lean": "BULLISH", "confidence": 74, "coverage": 0.82},
        gamma_regime={"regime": "NEGATIVE_GAMMA_AMPLIFYING", "distance_to_flip_pct": 0.4},
        exit_friction={"option_exit_friction": "HIGH", "future_hedge_friction": "LOW"},
        recent_history=[{"ts_ms": 0, "touch_probability": entry["entry_touch_probability"]}],
        now_ms=30 * 60 * 1000,
    )

    assert pkg["schema_version"] == "nrd.integration.position_risk.v0.4"
    assert pkg["tail_risk_state"] == H.STATE_HEDGE_READY
    assert pkg["current_risk"]["touch_probability_drift"] > 0.25
    assert pkg["current_risk"]["recent_deterioration_slope"] > 0.0
    assert pkg["current_risk"]["tail_exposure_acceleration"] == "HIGH"
    assert pkg["current_risk"]["persistence"] == "HIGH"
    assert pkg["hedge_intent"]["hedge_side"] == "BUY_FUTURE_OR_PERP"
    assert pkg["hedge_intent"]["execution_mode"] == "DRY_INTENT_ONLY"
    assert pkg["hedge_intent"]["hedge_size_mode"] in ("MEDIUM", "FULL")


def test_risk_rise_prefers_option_exit_when_exit_friction_is_low():
    entry = H.build_entry_risk_anchor(
        direction_bias="SHORT_PUT",
        entry_price=73000,
        entry_dte_hours=48,
        entry_short_delta=-0.20,
        entry_short_gamma=0.00004,
        entry_iv=0.65,
        entry_loss_boundary=70000,
        entry_edb_side="BULLISH",
        entry_gamma_regime="POSITIVE_GAMMA_PINNING",
    )

    pkg = H.evaluate_position_risk(
        position_id="p2",
        direction_bias="SHORT_PUT",
        entry_risk_anchor=entry,
        current_price=70400,
        dte_hours=30,
        short_delta=-0.62,
        short_gamma=0.00010,
        iv=0.72,
        loss_boundary=70000,
        edb={"lean": "BEARISH", "confidence": 70, "coverage": 0.80},
        gamma_regime={"regime": "NEGATIVE_GAMMA_AMPLIFYING", "distance_to_flip_pct": -0.5},
        exit_friction={"option_exit_friction": "LOW", "future_hedge_friction": "LOW"},
        recent_history=[],
        now_ms=30 * 60 * 1000,
    )

    assert pkg["tail_risk_state"] == H.STATE_EXIT_PREFERRED
    assert pkg["hedge_intent"] is None


def test_persistence_two_item_edb_ggr():
    """整合 Phase1：持续性两项制 {EDB_ADVERSE, GGR_ADVERSE}，KPF 已删；0→LOW/1→MEDIUM/2→HIGH。"""
    bullish = {"lean": "BULLISH", "confidence": 70, "coverage": 0.80}   # 对 SHORT_CALL 不利
    bearish = {"lean": "BEARISH", "confidence": 70, "coverage": 0.80}   # 对 SHORT_CALL 有利
    neg_gamma = {"regime": "NEGATIVE_GAMMA_AMPLIFYING", "distance_to_flip_pct": 0.4}
    calm = {"regime": "CALM_TRANSITION", "distance_to_flip_pct": 3.0}

    s0, c0 = H.persistence_score("SHORT_CALL", edb=bearish, gamma_regime=calm)        # 0 项
    s1, c1 = H.persistence_score("SHORT_CALL", edb=bullish, gamma_regime=calm)        # 仅 EDB
    s2, c2 = H.persistence_score("SHORT_CALL", edb=bullish, gamma_regime=neg_gamma)   # EDB+GGR

    assert s0 == H.PERSISTENCE_LOW and c0 == []
    assert s1 == H.PERSISTENCE_MEDIUM and c1 == ["EDB_ADVERSE"]
    assert s2 == H.PERSISTENCE_HIGH and "EDB_ADVERSE" in c2 and "GGR_ADVERSE" in c2
    assert "KPF_BUFFER_WEAK_OR_BROKEN" not in c2
