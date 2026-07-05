# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import execution_feasibility as EF


def _q(mark, bid, ask, tick=0.0001):
    return {"mark": mark, "best_bid": bid, "best_ask": ask, "tick": tick}


def _inp(sq, pq, amount=0.1, fee=0.00006, floor=0.0, steps=3):
    return {"short_quote": sq, "protection_quote": pq, "amount": amount,
            "fee_estimate": fee, "credit_floor": floor, "max_tick_steps": steps}


# 一个可建候选（BUILDABLE 基准）
_GOOD_SHORT = _q(0.010, 0.0097, 0.0103)
_GOOD_PROT = _q(0.004, 0.0038, 0.0042)


def test_good_candidate_passes_and_grades():
    r = EF.evaluate_execution_feasibility(_inp(_GOOD_SHORT, _GOOD_PROT))
    assert r["hard_gate_passed"] and r["status"] == "PASS"
    assert r["grade"] in ("HIGHLY_BUILDABLE", "BUILDABLE", "PATIENT_ONLY")
    assert 0.0 <= r["score_norm"] <= 1.0
    assert r["economics"]["executable_credit_after_fees"] > 0
    assert r["economics"]["credit_retention_ratio"] > 0.45


# ---- §13.1 单调性 ----

def test_wider_spread_does_not_raise_score():
    base = EF.evaluate_execution_feasibility(_inp(_GOOD_SHORT, _GOOD_PROT))["score"]
    wide = EF.evaluate_execution_feasibility(_inp(_q(0.010, 0.0090, 0.0110), _GOOD_PROT))["score"]
    assert wide <= base


def test_lower_executable_credit_does_not_raise_score():
    base = EF.evaluate_execution_feasibility(_inp(_GOOD_SHORT, _GOOD_PROT))["score"]
    worse = EF.evaluate_execution_feasibility(_inp(_q(0.010, 0.0093, 0.0103), _GOOD_PROT))["score"]
    assert worse <= base                      # 短腿 bid 降 → exec credit 降、摩擦升 → 分不升


def test_crossed_market_hard_rejects():
    r = EF.evaluate_execution_feasibility(_inp(_q(0.010, 0.0103, 0.0097), _GOOD_PROT))
    assert not r["hard_gate_passed"] and r["grade"] == "REJECT"


def test_missing_protection_ask_hard_rejects():
    r = EF.evaluate_execution_feasibility(_inp(_GOOD_SHORT, {"mark": 0.004, "best_bid": 0.0038,
                                                            "best_ask": None, "tick": 0.0001}))
    assert not r["hard_gate_passed"] and "PROTECTION_QUOTE_INCOMPLETE" in r["hard_failures"]


def test_low_premium_protection_wide_relative_spread_is_softened():
    cheap_wide = _q(0.00018, 0.00010, 0.00025)
    r = EF.evaluate_execution_feasibility(_inp(_GOOD_SHORT, cheap_wide))
    assert r["hard_gate_passed"]
    assert "PROTECTION_SPREAD_SOFT_LOW_PREMIUM" in r["warnings"]
    assert r["liquidity"]["protection_low_premium_soft"] is True
    assert r["liquidity"]["protection_abs_spread"] <= EF.PROTECTION_ABS_SPREAD_MAX + 1e-12


def test_expensive_protection_wide_spread_still_rejects():
    expensive_wide = _q(0.004, 0.001, 0.004)
    r = EF.evaluate_execution_feasibility(_inp(_GOOD_SHORT, expensive_wide))
    assert not r["hard_gate_passed"]
    assert "PROTECTION_SPREAD_TOO_WIDE" in r["hard_failures"]


# ---- §13.2 典型情景 ----

def test_case_A_high_mark_unexecutable_rejected():
    # mark credit 高，但价差极宽 → REJECT（EF-02：mark 好不能覆盖不可执行）
    r = EF.evaluate_execution_feasibility(_inp(_q(0.020, 0.011, 0.029), _q(0.004, 0.0038, 0.0042)))
    assert not r["hard_gate_passed"]
    assert any("SPREAD" in f for f in r["hard_failures"])


def test_case_D_mark_ok_but_executable_credit_non_positive_rejected():
    # 两腿 mark 给出正 mark credit，但 short_bid − prot_ask < 0 → 可成交 credit 非正 → REJECT
    r = EF.evaluate_execution_feasibility(_inp(_q(0.005, 0.004, 0.006), _q(0.004, 0.0038, 0.006), fee=0.0))
    assert not r["hard_gate_passed"]
    assert "EXECUTABLE_CREDIT_NON_POSITIVE" in r["hard_failures"]


def test_retention_too_low_rejected():
    # mark credit 正常但可成交 credit 仅占很小比例 → 保留率 < 0.45 → REJECT
    r = EF.evaluate_execution_feasibility(_inp(_q(0.010, 0.0050, 0.0150), _q(0.004, 0.0038, 0.0049), fee=0.0))
    assert not r["hard_gate_passed"]
    assert ("CREDIT_RETENTION_TOO_LOW" in r["hard_failures"]
            or any("SPREAD" in f for f in r["hard_failures"]))


# ---- 折损（§14 例）----

def test_feasibility_penalty_folds_surface_value():
    # base A 高但可行性差、base B 略低但可行性好 → 折损后 B 应高于 A
    pen_a = EF.feasibility_penalty(0.35, 0.50)
    pen_b = EF.feasibility_penalty(0.88, 0.50)
    final_a = 0.82 * pen_a
    final_b = 0.74 * pen_b
    assert abs(pen_a - 0.675) < 1e-9 and abs(pen_b - 0.94) < 1e-9
    assert final_b > final_a                  # 可行性折损改变排序（B 更易建立）
    assert EF.feasibility_penalty(1.0, 0.50) == 1.0     # 满分不折损
    assert EF.feasibility_penalty(0.0, 0.50) == 0.50    # 0 分最多保留 floor
