# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import recommend as R


def _manual_context(card_id="BTC #4501", amount=0.1, delta_max=0.35,
                    expires_ts_ms=31 * 60 * 1000, note="approved"):
    return {
        "schema_name": "ManualExecutionContext",
        "schema_version": "nrd.execution.manual_context.v1",
        "context_id": "manual-1",
        "created_ts_ms": 1000,
        "expires_ts_ms": expires_ts_ms,
        "operator_decision": "APPROVE_PLANNING",
        "direction_bias": "SHORT_CALL",
        "audit_reference": {"card_id": card_id, "operator_notes": note},
        "planning_scope": {
            "dte_hours_min": 24, "dte_hours_max": 72,
            "short_delta_min": 0.15, "short_delta_max": delta_max,
            "protection_width_min": 2000, "protection_width_max": 2500,
            "amount": amount,
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


def _cand(relief=0.83, sstrike=78000, pstrike=80000, vrp="PASS", budget="ALLOW",
          cid=1234, feasibility_score=76.0):
    return {
        "id": cid, "short_instrument": "BTC-31MAY26-%d-C" % sstrike,
        "protection_instrument": "BTC-31MAY26-%d-C" % pstrike,
        "short_expiry_label": "31MAY26", "short_strike": sstrike,
        "protection_strike": pstrike, "amount": 0.1, "qualified": True,
        "net_credit_effective": 0.0004, "margin_relief_ratio": relief,
        "short_delta": 0.30, "width": pstrike - sstrike,
        "vrp_state": vrp, "budget_decision": budget,
        "execution_feasibility_score": feasibility_score,
    }


def test_strategy_code_and_side():
    assert R.side_of("BTC-31MAY26-78000-C") == "CALL"
    assert R.side_of("BTC-31MAY26-70000-P") == "PUT"
    assert R.strategy_code("CALL", "31MAY26", 78000, 80000) == "VCS|CALL|31MAY26|78000|80000"


def test_manual_context_hash_uses_only_material_manual_fields():
    ctx = _manual_context()
    h1 = R.manual_context_hash(ctx)
    ignored = _manual_context(note="different operator note")
    ignored["created_ts_ms"] = 2000
    assert R.manual_context_hash(ignored) == h1
    assert R.manual_context_hash(_manual_context(delta_max=0.45)) != h1
    assert R.manual_context_hash(_manual_context(card_id="BTC #4502")) != h1
    assert R.manual_context_hash(_manual_context(expires_ts_ms=32 * 60 * 1000)) != h1


def test_build_library_unique_confirm_codes_and_manual_lineage():
    ctx = _manual_context()
    menu = [_cand(sstrike=78000, pstrike=80000, cid=1),
            _cand(sstrike=77000, pstrike=79000, cid=2),
            _cand(sstrike=76000, pstrike=78000, cid=3)]
    lib = R.build_recommendation_library(menu, "s1", ctx, 1, 1000)
    codes = [s["confirm_code"] for s in lib["recommendations"]]
    assert len(codes) == 3 and len(set(codes)) == 3
    assert all(len(c) >= 4 for c in codes)
    snap = lib["recommendations"][0]
    assert lib["manual_context_id"] == "manual-1"
    assert lib["manual_context_hash"] == R.manual_context_hash(ctx)
    assert snap["schema_name"] == "PlanApprovalSnapshot"
    assert snap["schema_version"] == "nrd.execution.plan_approval.v1"
    assert snap["approval_id"]
    assert snap["manual_context_id"] == "manual-1"
    assert snap["manual_context_hash"] == lib["manual_context_hash"]
    assert snap["audit_card_id"] == "BTC #4501"
    assert snap["operator_note"] == "approved"
    assert snap["direction_bias"] == "SHORT_CALL"
    assert snap["config_hash"]
    assert "external_package_id" not in snap
    assert "episode_id" not in snap
    assert "external_source_hash" not in snap


def test_resolve_confirm_code():
    lib = R.build_recommendation_library([_cand()], "s1", _manual_context(), 1, 1000)
    code = lib["recommendations"][0]["confirm_code"]
    assert R.resolve_confirm_code(lib, code)["plan_id"] == 1234
    assert R.resolve_confirm_code(lib, code.lower()) is not None
    assert R.resolve_confirm_code(lib, "ZZZZ") is None
    assert R.resolve_confirm_code(lib, "") is None


def test_confirm_code_stable_under_subbucket_drift_but_changes_on_material_drift():
    ctx = _manual_context()
    lib1 = R.build_recommendation_library([_cand(relief=0.83)], "s1", ctx, 1, 1000)
    code1 = lib1["recommendations"][0]["confirm_code"]
    lib_same = R.build_recommendation_library([_cand(relief=0.85)], "s1", ctx, 2, 1100)
    assert lib_same["recommendations"][0]["confirm_code"] == code1

    lib2 = R.build_recommendation_library(
        [_cand(relief=0.83)], "s1", _manual_context(card_id="BTC #4502"), 3, 1200)
    code2 = lib2["recommendations"][0]["confirm_code"]
    assert code2 != code1
    assert R.resolve_confirm_code(lib2, code1) is None
    assert R.resolve_confirm_code(lib2, code2) is not None


def test_precommit_recheck_pass_and_fail():
    lib = R.build_recommendation_library([_cand()], "s1", _manual_context(), 1, 1000)
    snap = lib["recommendations"][0]
    ok = R.precommit_recheck(snap, lib, {"entry_net_credit_positive": True, "no_unknown_orders": True})
    assert ok["passed"]
    bad = R.precommit_recheck(snap, lib, {"entry_net_credit_positive": False})
    assert not bad["passed"] and any("LIVE_CHECK_FAILED" in r for r in bad["reasons"])
    drift_lib = R.build_recommendation_library([_cand(relief=0.72)], "s1", _manual_context(), 2, 1100)
    drift = R.precommit_recheck(snap, drift_lib, {"entry_net_credit_positive": True})
    assert not drift["passed"]


def test_manual_context_change_changes_quality_code():
    lib1 = R.build_recommendation_library([_cand()], "s1", _manual_context(card_id="BTC #4501"), 1, 1000)
    lib2 = R.build_recommendation_library([_cand()], "s1", _manual_context(card_id="BTC #4502"), 1, 1000)
    assert lib1["recommendations"][0]["quality_code"] != lib2["recommendations"][0]["quality_code"]


def test_execution_feasibility_bucket_participates_in_confirm_code():
    ctx = _manual_context()
    lib1 = R.build_recommendation_library([_cand(feasibility_score=72.0)], "s1", ctx, 1, 1000)
    code1 = lib1["recommendations"][0]["confirm_code"]
    same_bucket = R.build_recommendation_library([_cand(feasibility_score=79.0)], "s1", ctx, 2, 1100)
    assert same_bucket["recommendations"][0]["confirm_code"] == code1

    worse_bucket = R.build_recommendation_library([_cand(feasibility_score=61.0)], "s1", ctx, 3, 1200)
    assert worse_bucket["recommendations"][0]["confirm_code"] != code1
    assert R.resolve_confirm_code(worse_bucket, code1) is None
