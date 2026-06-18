# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import spm_sim as SPM


def _approx(a, b, eps=1e-12):
    return abs(a - b) <= eps


def test_relief_math():
    r = SPM.spm_relief(100.0, 80.0)
    assert _approx(r["relief_abs"], 20.0) and _approx(r["relief_ratio"], 0.2)


def test_relief_zero_base():
    r = SPM.spm_relief(0.0, 0.0)
    assert r["relief_ratio"] == 0.0


def test_account_is_pm():
    assert SPM.spm_account_is_portfolio_margin({"portfolio_margining_enabled": True})[0] is True
    assert SPM.spm_account_is_portfolio_margin({"margin_model": "segregated_pm"})[0] is True
    assert SPM.spm_account_is_portfolio_margin({"margin_model": "segregated_sm"})[0] is False
    assert SPM.spm_account_is_portfolio_margin({})[0] is False


def test_evaluate_candidates_picks_first_passing():
    # 注入假的 simulate_portfolio
    def fake_sim(cur, simpos, add_positions=True):
        if len(simpos) == 1:                 # 场景 B（仅 short）
            return {"initial_margin": 100.0, "maintenance_margin": 80.0,
                    "available_funds": 5.0}
        # 场景 C（short + 某保护腿）
        if "A" in simpos:
            return {"initial_margin": 95.0}   # relief ratio 0.05 -> 不达标
        if "B" in simpos:
            return {"initial_margin": 80.0}   # relief ratio 0.20 -> 达标
        return {"initial_margin": 100.0}

    SPM.dbt_simulate_portfolio = fake_sim     # monkeypatch
    cands = [{"instrument_name": "A"}, {"instrument_name": "B"}]
    rep = SPM.spm_evaluate_candidates("BTC", "SHORT", cands, 0.1, min_ratio=0.10)
    assert rep["accepted"] is True
    assert rep["protection_instrument"] == "B"
    assert _approx(rep["relief_ratio"], 0.2)
    assert len(rep["attempts"]) == 2          # A 试过后才到 B


def test_evaluate_candidates_all_fail_returns_best():
    def fake_sim(cur, simpos, add_positions=True):
        if len(simpos) == 1:
            return {"initial_margin": 100.0}
        return {"initial_margin": 98.0}        # ratio 0.02 恒不达标

    SPM.dbt_simulate_portfolio = fake_sim
    cands = [{"instrument_name": "A"}, {"instrument_name": "B"}]
    rep = SPM.spm_evaluate_candidates("BTC", "SHORT", cands, 0.1, min_ratio=0.10)
    assert rep["accepted"] is False
    assert rep["relief_ratio"] is not None     # 仍返回最优尝试供核对
