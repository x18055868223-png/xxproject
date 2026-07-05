# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import recommend as R
import position as P


def _manual_context():
    return {
        "schema_name": "ManualExecutionContext",
        "schema_version": "nrd.execution.manual_context.v1",
        "context_id": "manual-1",
        "created_ts_ms": 1000,
        "expires_ts_ms": 31 * 60 * 1000,
        "operator_decision": "APPROVE_PLANNING",
        "direction_bias": "SHORT_CALL",
        "audit_reference": {"card_id": "BTC #4501", "operator_notes": "approved"},
        "planning_scope": {
            "dte_hours_min": 24, "dte_hours_max": 72,
            "short_delta_min": 0.15, "short_delta_max": 0.35,
            "protection_width_min": 2000, "protection_width_max": 2500,
            "amount": 0.1,
        },
        "risk_policy": {
            "max_loss_per_trade": 0.02,
            "min_net_credit": 0.0,
            "allow_hedge_open": False,
            "allow_hedge_reduce": True,
            "allow_auto_take_profit": True,
            "allow_auto_risk_exit": False,
        },
    }


def _cand(relief=0.83, feasibility_score=76.0):
    return {
        "id": 7,
        "short_instrument": "BTC-31MAY26-78000-C",
        "protection_instrument": "BTC-31MAY26-80000-C",
        "short_expiry_label": "31MAY26",
        "short_strike": 78000,
        "protection_strike": 80000,
        "amount": 0.1,
        "qualified": True,
        "net_credit_effective": 0.0004,
        "margin_relief_ratio": relief,
        "short_delta": 0.30,
        "width": 2000,
        "vrp_state": "PASS",
        "budget_decision": "ALLOW",
        "execution_feasibility_score": feasibility_score,
    }


def _live_all_pass(ctx_hash):
    return {
        "manual_context_valid": True,
        "manual_context_hash": ctx_hash,
        "approval_not_expired": True,
        "same_expiry": True,
        "vrp_pass": True,
        "spm_relief": 0.5,
        "min_relief": 0.10,
        "quotes_fresh": True,
        "net_credit_after_costs": 0.0003,
        "projected_budget_decision": "ALLOW",
        "ledger_reconciled": True,
        "no_unknown_orders": True,
        "spread_ok": True,
        "execution_feasibility_live": {"hard_gate_passed": True, "score": 80.0},
    }


def test_manual_context_hash_is_stable_and_material():
    ctx = _manual_context()
    h1 = R.manual_context_hash(ctx)
    h2 = R.manual_context_hash(dict(ctx))
    changed = _manual_context()
    changed["planning_scope"] = dict(changed["planning_scope"], short_delta_max=0.45)
    assert h1 == h2
    assert R.manual_context_hash(changed) != h1


def test_build_library_uses_manual_lineage_not_external_lineage():
    ctx = _manual_context()
    lib = R.build_recommendation_library([_cand()], "s1", ctx, 1, 1000)
    snap = lib["recommendations"][0]
    assert lib["manual_context_id"] == "manual-1"
    assert snap["manual_context_id"] == "manual-1"
    assert snap["audit_card_id"] == "BTC #4501"
    assert snap["direction_bias"] == "SHORT_CALL"
    assert "external_package_id" not in snap
    assert "episode_id" not in snap
    assert "external_source_hash" not in snap


def test_manual_context_change_invalidates_quality_code_and_old_confirm_code():
    ctx = _manual_context()
    lib1 = R.build_recommendation_library([_cand()], "s1", ctx, 1, 1000)
    code1 = lib1["recommendations"][0]["confirm_code"]
    changed = _manual_context()
    changed["audit_reference"] = {"card_id": "BTC #4502", "operator_notes": "changed"}
    lib2 = R.build_recommendation_library([_cand()], "s1", changed, 2, 1100)
    assert lib2["recommendations"][0]["confirm_code"] != code1
    assert R.resolve_confirm_code(lib2, code1) is None


def test_precommit_checks_manual_context_not_external_package():
    ctx = _manual_context()
    lib = R.build_recommendation_library([_cand()], "s1", ctx, 1, 1000)
    locked = lib["recommendations"][0]
    result = R.evaluate_precommit_checks(locked, lib, _live_all_pass(lib["manual_context_hash"]))
    assert result["passed"]
    assert "same_manual_context" in R.PRECOMMIT_CHECKS
    assert "same_external_package" not in R.PRECOMMIT_CHECKS

    bad_live = _live_all_pass("changed-context-hash")
    bad = R.evaluate_precommit_checks(locked, lib, bad_live)
    assert "same_manual_context" in bad["failed"]


def test_position_snapshot_preserves_manual_lineage_only_flag():
    ctx = _manual_context()
    lib = R.build_recommendation_library([_cand()], "s1", ctx, 1, 1000)
    locked = lib["recommendations"][0]
    snap = P.build_vertical_entry_snapshot(
        locked,
        {"filled": 0.1, "avg_price": 0.010},
        {"filled": 0.1, "avg_price": 0.006},
        0.00005,
        now_ts=1000,
    )
    assert snap["manual_context_id"] == "manual-1"
    assert snap["audit_card_id"] == "BTC #4501"
    assert snap["direction_bias"] == "SHORT_CALL"
    assert snap["approval_id"]
    assert snap["manual_lineage_only"] is True
    assert "external_package_id" not in snap
