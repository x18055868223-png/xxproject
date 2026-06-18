# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import recommend as R


def _lib_and_locked():
    cand = {"id": 1, "short_instrument": "BTC-X-78000-C", "protection_instrument": "BTC-X-80000-C",
            "short_expiry_label": "X", "short_strike": 78000, "protection_strike": 80000,
            "amount": 0.1, "qualified": True, "net_credit_effective": 0.0004,
            "margin_relief_ratio": 0.5, "short_delta": 0.3, "width": 2000,
            "vrp_state": "PASS", "budget_decision": "ALLOW"}
    lib = R.build_recommendation_library([cand], "s1", "pkg1", 1, 1000)
    return lib, dict(lib["recommendations"][0])


def _live_all_pass():
    return {"signal_fresh": True, "sig_package_id": "pkg1", "same_expiry": True,
            "vrp_pass": True, "spm_relief": 0.5, "min_relief": 0.10, "quotes_fresh": True,
            "net_credit_after_costs": 0.0003, "projected_budget_decision": "ALLOW",
            "ledger_reconciled": True, "no_unknown_orders": True, "spread_ok": True}


def test_all_thirteen_pass():
    lib, locked = _lib_and_locked()
    r = R.evaluate_precommit_checks(locked, lib, _live_all_pass())
    assert r["passed"] and not r["failed"]
    assert len(r["checks"]) == 13


def test_vrp_none_fails_closed():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(); live["vrp_pass"] = None
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert not r["passed"] and "vrp_rechecked" in r["failed"]


def test_missing_signal_package_fails():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(); live["sig_package_id"] = None
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "same_signal_package" in r["failed"]


def test_negative_net_credit_fails():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(); live["net_credit_after_costs"] = -0.0001
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "entry_net_credit_after_costs_positive" in r["failed"]


def test_budget_block_fails():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(); live["projected_budget_decision"] = "BLOCK"
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "projected_budget_passed" in r["failed"]


def test_plan_hash_drift_fails():
    lib, locked = _lib_and_locked()
    locked["plan_hash"] = "tampered"
    r = R.evaluate_precommit_checks(locked, lib, _live_all_pass())
    assert "locked_plan_hash_match" in r["failed"]


def test_spm_relief_below_min_fails():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(); live["spm_relief"] = 0.05  # < min 0.10
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "spm_rechecked" in r["failed"]
