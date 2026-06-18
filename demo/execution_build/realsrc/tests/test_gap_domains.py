# -*- coding: utf-8 -*-
"""R4：缺口域（组合预算/赢家管理/归因/回放）安全默认 + 账本 VRP 入场血缘。

验证占位因子「安全方向单调」（只挡/缩/早退，绝不放松门或夸大机会）+ 入场锚 VRP 血缘。
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import risk_controls as RC
import hedge_risk as H


def test_portfolio_budget_blocks_on_breach_size_zero():
    over = RC.evaluate_portfolio_budget(
        current={"open_positions": 2, "short_gamma": 0.9, "short_vega": 0.0, "margin_used": 0.0},
        limits={"max_open_positions": 1, "max_short_gamma": 0.5, "max_short_vega": 1.0, "max_margin": 5000.0},
        proposed_size=1.0)
    assert over["decision"] == "BLOCK" and over["allowed_size"] == 0.0
    assert over["breaches"] and over["status"] == "PLACEHOLDER"
    within = RC.evaluate_portfolio_budget(
        current={"open_positions": 0, "short_gamma": 0.0, "short_vega": 0.0, "margin_used": 0.0},
        limits={"max_open_positions": 1, "max_short_gamma": 0.5, "max_short_vega": 1.0, "max_margin": 5000.0},
        proposed_size=5.0)
    assert within["decision"] == "ALLOW_TEST_SIZE"
    # 安全方向单调：占位绝不放行超过 proposed，且封顶 test size（偏小）
    assert within["allowed_size"] <= 5.0 and within["allowed_size"] <= 1.0


def test_position_manage_safe_defaults_early():
    tp = RC.decide_position_manage(0.75, 0.70, 24, "LOW")
    assert tp["decision"] == "TAKE_PROFIT_READY" and any("EARLY" in r for r in tp["reason_codes"])
    gd = RC.decide_position_manage(0.0, 0.70, 2, "HIGH")
    assert gd["decision"] == "GAMMA_DECAY_EXIT"
    te = RC.decide_position_manage(0.0, 0.70, 4, "LOW")
    assert te["decision"] == "TIME_EXIT_REVIEW"
    hold = RC.decide_position_manage(0.0, 0.70, 24, "LOW")
    assert hold["decision"] == "HOLD_REVIEW"
    for d in (tp, gd, te, hold):
        assert d["status"] == "PLACEHOLDER"


def test_attribution_net_and_replay_bucket():
    a1 = RC.build_attribution("s1", theta_capture=5.0, directional_pnl=-1.0, iv_rv_edge_proxy=0.5,
                              fee_cost=1.0, spread_slippage_cost=0.5, protection_cost_or_recovery=-0.2,
                              hedge_pnl=0.3)
    assert abs(a1["net_pnl_after_costs"] - (5.0 - 1.0 + 0.5 - 1.0 - 0.5 - 0.2 + 0.3)) < 1e-9
    assert a1["status"] == "PLACEHOLDER"
    rep = RC.replay_expectation([a1])
    assert rep["sample_count"] == 1 and rep["status"] == "OFFLINE"
    rows = [{"side": "SHORT_CALL", "dte_bucket": "24h", "vrp_gate": "PASS",
             "budget_decision": "ALLOW_TEST_SIZE", "net_pnl_after_costs": 1.0}]
    bucket = RC.replay_expectation_by_bucket(rows, RC.REPLAY_BUCKET_FIELDS)
    assert bucket["sample_count"] == 1 and bucket["buckets"]


def test_entry_anchor_carries_vrp_lineage():
    anchor = H.build_entry_risk_anchor(
        "SHORT_CALL", 68000, 24, 0.30, 0.00005, 0.7, 70000,
        entry_edb_side="BEARISH", entry_gamma_regime="POSITIVE_GAMMA_PINNING",
        entry_vrp_window_id="SHORT_CALL-24h", entry_forward_vol_hurdle=0.40,
        entry_candidate_vrp_edge_ccy=0.0006, entry_executable_short_iv=0.86,
        entry_vrp_reason_codes=["RV_LOW_PERCENTILE_HURDLE_UP"])
    assert anchor["entry_vrp_window_id"] == "SHORT_CALL-24h"
    assert anchor["entry_forward_vol_hurdle"] == 0.40
    assert anchor["entry_candidate_vrp_edge_ccy"] == 0.0006
    assert anchor["entry_executable_short_iv"] == 0.86
    assert anchor["entry_vrp_reason_codes"] == ["RV_LOW_PERCENTILE_HURDLE_UP"]
    # 不破坏既有锚字段（既有调用零影响）
    assert 0.0 <= anchor["entry_touch_probability"] <= 1.0 and anchor["entry_edb_side"] == "BEARISH"


def test_entry_anchor_backward_compatible_without_vrp():
    # 既有 9 参调用（无 VRP 血缘）仍工作，血缘字段取默认
    anchor = H.build_entry_risk_anchor(
        "SHORT_PUT", 68000, 48, -0.25, 0.00004, 0.65, 66000, "BULLISH", "POSITIVE_GAMMA_PINNING")
    assert anchor["entry_vrp_window_id"] == "" and anchor["entry_forward_vol_hurdle"] is None
    assert anchor["entry_vrp_reason_codes"] == []
