# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import recommend as R


def _manual_context():
    return {
        "context_id": "manual-1",
        "expires_ts_ms": 31 * 60 * 1000,
        "direction_bias": "SHORT_CALL",
        "audit_reference": {"card_id": "BTC #4501", "operator_notes": "approved"},
        "planning_scope": {
            "dte_hours_min": 24, "dte_hours_max": 72,
            "short_delta_min": 0.15, "short_delta_max": 0.35,
            "protection_width_min": 2000, "protection_width_max": 2500,
            "amount": 0.1,
        },
        "risk_policy": {"max_loss_per_trade": 0.02, "min_net_credit": 0.0},
    }


def _lib_and_locked():
    cand = {"id": 1, "short_instrument": "BTC-X-78000-C", "protection_instrument": "BTC-X-80000-C",
            "short_expiry_label": "X", "short_strike": 78000, "protection_strike": 80000,
            "amount": 0.1, "qualified": True, "net_credit_effective": 0.0004,
            "margin_relief_ratio": 0.5, "short_delta": 0.3, "width": 2000,
            "vrp_state": "PASS", "budget_decision": "ALLOW"}
    lib = R.build_recommendation_library([cand], "s1", _manual_context(), 1, 1000)
    return lib, dict(lib["recommendations"][0])


def _live_all_pass(ctx_hash):
    return {"manual_context_valid": True, "manual_context_hash": ctx_hash,
            "approval_not_expired": True, "same_expiry": True,
            "vrp_pass": True, "spm_relief": 0.5, "min_relief": 0.10, "quotes_fresh": True,
            "net_credit_after_costs": 0.0003, "projected_budget_decision": "ALLOW",
            "ledger_reconciled": True, "no_unknown_orders": True, "spread_ok": True,
            "execution_feasibility_live": {"hard_gate_passed": True, "score": 80.0}}


def test_all_precommit_checks_pass():
    lib, locked = _lib_and_locked()
    r = R.evaluate_precommit_checks(locked, lib, _live_all_pass(lib["manual_context_hash"]))
    assert r["passed"] and not r["failed"]
    assert len(r["checks"]) == len(R.PRECOMMIT_CHECKS) == 15
    assert "manual_context_valid" in R.PRECOMMIT_CHECKS
    assert "same_manual_context" in R.PRECOMMIT_CHECKS
    assert "approval_not_expired" in R.PRECOMMIT_CHECKS
    assert "external_fresh" not in R.PRECOMMIT_CHECKS
    assert "same_external_package" not in R.PRECOMMIT_CHECKS


def test_vrp_none_fails_closed():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(lib["manual_context_hash"]); live["vrp_pass"] = None
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert not r["passed"] and "vrp_rechecked" in r["failed"]


def test_missing_manual_context_valid_fails():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(lib["manual_context_hash"]); live["manual_context_valid"] = False
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "manual_context_valid" in r["failed"]


def test_manual_context_hash_mismatch_fails():
    lib, locked = _lib_and_locked()
    live = _live_all_pass("changed-context")
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "same_manual_context" in r["failed"]


def test_expired_approval_fails():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(lib["manual_context_hash"]); live["approval_not_expired"] = False
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "approval_not_expired" in r["failed"]


def test_negative_net_credit_fails():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(lib["manual_context_hash"]); live["net_credit_after_costs"] = -0.0001
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "entry_net_credit_after_costs_positive" in r["failed"]


def test_budget_block_fails():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(lib["manual_context_hash"]); live["projected_budget_decision"] = "BLOCK"
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "projected_budget_passed" in r["failed"]


def test_plan_hash_drift_fails():
    lib, locked = _lib_and_locked()
    locked["plan_hash"] = "tampered"
    r = R.evaluate_precommit_checks(locked, lib, _live_all_pass(lib["manual_context_hash"]))
    assert "locked_plan_hash_match" in r["failed"]


def test_spm_relief_below_min_fails():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(lib["manual_context_hash"]); live["spm_relief"] = 0.05
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "spm_rechecked" in r["failed"]


def test_execution_feasibility_missing_or_false_fails_closed():
    lib, locked = _lib_and_locked()
    live = _live_all_pass(lib["manual_context_hash"]); live.pop("execution_feasibility_live")
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "execution_feasibility_rechecked" in r["failed"]

    live = _live_all_pass(lib["manual_context_hash"])
    live["execution_feasibility_live"] = {"hard_gate_passed": False,
                                          "hard_failures": ["EXECUTABLE_CREDIT_NON_POSITIVE"]}
    r = R.evaluate_precommit_checks(locked, lib, live)
    assert "execution_feasibility_rechecked" in r["failed"]
