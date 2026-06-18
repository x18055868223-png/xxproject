# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import position as P


def _approx(a, b, eps=1e-12):
    return abs(a - b) <= eps


_LOCKED = {"session_id": "s1", "signal_package_id": "pkg1", "strategy_code": "VCS|CALL|X|78000|80000",
           "quality_code": "abcd1234", "plan_hash": "ph16", "side": "CALL",
           "short_instrument": "BTC-X-78000-C", "long_instrument": "BTC-X-80000-C"}


def test_entry_profit_ceiling_math():
    # 卖方实收 0.010×0.1=0.001 ; 保护实付 0.006×0.1=0.0006 ; 费 0.00005
    assert _approx(P.entry_profit_ceiling_net(0.001, 0.0006, 0.00005), 0.00035)
    assert P.entry_profit_ceiling_net(None, 0.0006, 0.0) is None


def test_build_snapshot_80pct_budget():
    snap = P.build_vertical_entry_snapshot(
        _LOCKED, {"filled": 0.1, "avg_price": 0.010}, {"filled": 0.1, "avg_price": 0.006},
        0.00005, now_ts=1000)
    # ceiling = 0.001 - 0.0006 - 0.00005 = 0.00035
    assert _approx(snap["entry_profit_ceiling_net"], 0.00035)
    # 目标利润 = 80% ; 最大退出支出 = 20%
    assert _approx(snap["target_profit_amount"], 0.00035 * 0.80)
    assert _approx(snap["max_total_exit_spend"], 0.00035 * 0.20)
    assert snap["remaining_short_qty"] == 0.1 and snap["immutable"]
    assert snap["position_id"] == "pos-1000" and snap["side"] == "CALL"


def test_missing_fill_yields_none_ceiling():
    snap = P.build_vertical_entry_snapshot(_LOCKED, {"filled": 0.1}, {"filled": 0.1, "avg_price": 0.006},
                                           0.0, now_ts=1)
    assert snap["entry_profit_ceiling_net"] is None
    assert snap["max_total_exit_spend"] is None


def test_position_reconcile_matched_mismatch_unexpected():
    snap = {"short_instrument": "S", "long_instrument": "L",
            "remaining_short_qty": 0.1, "long_remaining_qty": 0.1}
    assert P.position_reconcile(snap, [{"instrument_name": "S", "size": -0.1},
                                       {"instrument_name": "L", "size": 0.1}])["reconciled"]
    assert not P.position_reconcile(snap, [{"instrument_name": "S", "size": -0.05}])["reconciled"]
    assert not P.position_reconcile(None, [{"instrument_name": "X", "size": 1}])["reconciled"]
    assert P.position_reconcile(None, [])["reconciled"]
    # 短腿归零(快照短=0、保护仍在) → 只期望保护腿
    flat = {"short_instrument": "S", "long_instrument": "L",
            "remaining_short_qty": 0.0, "long_remaining_qty": 0.1}
    assert P.position_reconcile(flat, [{"instrument_name": "L", "size": 0.1}])["reconciled"]


def test_freeze_entry_ceiling_guard_ignores_recompute():
    snap = P.build_vertical_entry_snapshot(
        _LOCKED, {"filled": 0.1, "avg_price": 0.010}, {"filled": 0.1, "avg_price": 0.006},
        0.00005, now_ts=1000)
    frozen, tamper = P.freeze_entry_ceiling(snap, recomputed_ceiling=0.00099)
    assert _approx(frozen, 0.00035)        # 仍返回冻结值，忽略重算
    assert tamper is True                  # 标记篡改尝试（审计），但不改值
    frozen2, tamper2 = P.freeze_entry_ceiling(snap, recomputed_ceiling=0.00035)
    assert _approx(frozen2, 0.00035) and tamper2 is False
