# -*- coding: utf-8 -*-
"""R5：对冲并入 HEDGE_WATCH。

端到端：build_entry_risk_anchor（成交入场锚）→ 账本 short 记录 → hedge_watch 读
SignalEvidencePackage 的 edb/ggr + 实时行情 → 真实 evaluate_position_risk 出裁决。
验证 HEDGE_READY 三条件、低风险无意图、退出优先、DRY_INTENT_ONLY、两项持续性。
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import hedge_watch as HW
import hedge_risk as H


def _short_record(direction_bias="SHORT_CALL", entry_price=68000, boundary=70000):
    anchor = H.build_entry_risk_anchor(
        direction_bias, entry_price, 24, 0.22, 0.00005, 0.70, boundary,
        entry_edb_side="BEARISH", entry_gamma_regime="POSITIVE_GAMMA_PINNING",
        entry_vrp_window_id="SHORT_CALL-24h", entry_forward_vol_hurdle=0.40,
        entry_candidate_vrp_edge_ccy=0.0006)
    return {"instrument": "BTC-S-70000-C", "amount": 0.1, "entry_risk_anchor": anchor}


def _signal_evidence(lean, regime, dist):
    return {"schema_name": "SignalEvidencePackage",
            "direction_evidence": {"edb": {"lean": lean, "confidence": 74, "coverage": 0.82}},
            "pre_trade_context": {"ggr": {"regime": regime, "distance_to_flip_pct": dist}}}


def test_hedge_ready_when_risk_severe_persistent_and_friction_advantage():
    rec = _short_record()
    pkg = HW.watch_position(
        "p1", "SHORT_CALL", rec,
        current_market={"price": 69500, "dte_hours": 36, "short_delta": 0.72,
                        "short_gamma": 0.00012, "iv": 0.75},
        signal_evidence=_signal_evidence("BULLISH", "NEGATIVE_GAMMA_AMPLIFYING", 0.4),
        exit_friction={"option_exit_friction": "HIGH", "future_hedge_friction": "LOW"},
        recent_history=[{"ts_ms": 0, "touch_probability": rec["entry_risk_anchor"]["entry_touch_probability"]}],
        now_ms=30 * 60 * 1000)
    assert pkg["schema_version"] == "nrd.integration.position_risk.v0.4"
    assert pkg["tail_risk_state"] == H.STATE_HEDGE_READY
    assert pkg["current_risk"]["persistence"] == "HIGH"           # EDB+GGR 两项制
    assert pkg["hedge_intent"]["hedge_side"] == "BUY_FUTURE_OR_PERP"
    assert pkg["hedge_intent"]["execution_mode"] == "DRY_INTENT_ONLY"


def test_low_risk_no_hedge_intent():
    rec = _short_record()
    pkg = HW.watch_position(
        "p2", "SHORT_CALL", rec,
        current_market={"price": 66000, "dte_hours": 10, "short_delta": 0.12,
                        "short_gamma": 0.00003, "iv": 0.50},   # 价远离边界(70000)→真低风险
        signal_evidence=_signal_evidence("BEARISH", "POSITIVE_GAMMA_PINNING", 3.0),
        exit_friction={"option_exit_friction": "LOW", "future_hedge_friction": "LOW"},
        recent_history=[], now_ms=30 * 60 * 1000)
    assert pkg["tail_risk_state"] == H.STATE_NORMAL
    assert pkg["hedge_intent"] is None


def test_risk_rise_low_exit_friction_prefers_exit_not_hedge():
    rec = _short_record()
    pkg = HW.watch_position(
        "p3", "SHORT_CALL", rec,
        current_market={"price": 69500, "dte_hours": 36, "short_delta": 0.72,
                        "short_gamma": 0.00012, "iv": 0.75},
        signal_evidence=_signal_evidence("BULLISH", "NEGATIVE_GAMMA_AMPLIFYING", 0.4),
        exit_friction={"option_exit_friction": "LOW", "future_hedge_friction": "LOW"},  # 期权退出不贵
        recent_history=[], now_ms=30 * 60 * 1000)
    assert pkg["tail_risk_state"] == H.STATE_EXIT_PREFERRED   # 退出优先于对冲
    assert pkg["hedge_intent"] is None
