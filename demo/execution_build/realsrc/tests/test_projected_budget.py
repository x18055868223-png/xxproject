# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import risk_controls as RC

LIMITS = {"max_open_positions": 1, "max_short_gamma": 0.05, "max_short_vega": 0.50,
          "max_margin": 0.50, "max_spread_loss_per_trade": 0.02}


def _prop(**over):
    p = {"short_gamma": 0.01, "short_vega": 0.10, "structure_margin": 0.012,
         "max_spread_loss": 0.01, "hedge_margin_reserve": 0.005, "fee_reserve": 0.001}
    p.update(over)
    return p


def test_allow_under_limits():
    r = RC.evaluate_projected_budget(_prop(), {"open_positions": 0}, LIMITS)
    assert r["decision"] == "ALLOW" and not r["fail_closed"]
    assert r["projected"]["open_positions"] == 1


def test_fail_closed_on_missing_input():
    r = RC.evaluate_projected_budget(_prop(short_gamma=None), {}, LIMITS)
    assert r["decision"] == "BLOCK" and r["fail_closed"]
    assert any("INPUT_INCOMPLETE" in c for c in r["reason_codes"])


def test_proposed_pushes_over_margin_even_if_current_under():
    # current 0.49 < 0.50，但拟建 +0.012+0.005+0.001=0.018 → 0.508 > 0.50 → BLOCK
    # （证明计入了拟建仓位，而非旧 PLACEHOLDER 只看 current）
    r = RC.evaluate_projected_budget(_prop(), {"margin_used": 0.49}, LIMITS)
    assert r["decision"] == "BLOCK"
    assert any("margin_used" in c for c in r["reason_codes"])


def test_open_positions_limit():
    r = RC.evaluate_projected_budget(_prop(), {"open_positions": 1}, LIMITS)   # +1 → 2 > 1
    assert r["decision"] == "BLOCK"


def test_spread_loss_per_trade_limit():
    r = RC.evaluate_projected_budget(_prop(max_spread_loss=0.03), {}, LIMITS)  # 0.03 > 0.02
    assert r["decision"] == "BLOCK"


def test_gamma_limit_with_existing_load():
    r = RC.evaluate_projected_budget(_prop(short_gamma=0.04), {"short_gamma": 0.02}, LIMITS)  # 0.06 > 0.05
    assert r["decision"] == "BLOCK"
