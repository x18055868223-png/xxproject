# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import position as P


def _approx(a, b, eps=1e-9):
    return abs(a - b) <= eps


def test_reference_capture_ratio_excludes_protection_value():
    # ref_spend = 0.0001 + 0.00002 + 0.00003 = 0.00015 ; ratio = (0.001-0.00015)/0.001 = 0.85
    r = P.reference_profit_capture_ratio(0.001, 0.0001, 0.00002, 0.00003)
    assert _approx(r, 0.85)
    assert P.reference_profit_capture_ratio(0.001, None, 0.0, 0.0) is None   # 数据缺口 → None
    assert P.reference_profit_capture_ratio(0.0, 0.0001, 0.0, 0.0) is None   # ceiling<=0 → None


def test_take_profit_qualified():
    assert P.take_profit_qualified(0.85)
    assert P.take_profit_qualified(0.80)
    assert not P.take_profit_qualified(0.79)
    assert not P.take_profit_qualified(None)                                 # 数据缺口不触发


def test_short_buyback_budget():
    assert _approx(P.short_buyback_budget(0.0002, 0.00005, 0.00002), 0.00013)
    assert P.short_buyback_budget(0.0002, 0.0003, 0.0) == 0.0                 # 不小于 0


def test_short_buyback_price_cap_reverse_from_budget():
    # (0.0002 - 0.00002)/0.1 = 0.0018 → floor 到 tick 0.0001 = 0.0018
    assert _approx(P.short_buyback_price_cap(0.0002, 0.00002, 0.1, 0.0001), 0.0018)
    assert P.short_buyback_price_cap(0.0002, 0.0, 0.0, 0.0001) == 0.0         # 数量0 → 0
    assert P.short_buyback_price_cap(0.00001, 0.00002, 0.1, 0.0001) == 0.0    # 预算不足 → 0


def test_within_exit_budget():
    assert P.within_exit_budget(0.001, 0.1, 0.00002, 0.0002)                 # 0.0001+0.00002<=0.0002
    assert not P.within_exit_budget(0.0019, 0.1, 0.00002, 0.0002)            # 超预算


def test_exit_campaign_decision_branches():
    D = P
    assert D.exit_campaign_decision(True, True, 0.0, 0.0002, True, 0.0018)["state"] == P.EXIT_WORKING_LONG
    assert D.exit_campaign_decision(False, True, 0.1, 0.0002, True, 0.0018)["state"] == P.EXIT_IDLE
    assert D.exit_campaign_decision(True, False, 0.1, 0.0002, True, 0.0018)["state"] == P.EXIT_WAIT_TRIGGER
    assert D.exit_campaign_decision(True, True, 0.1, 0.0002, False, 0.0018)["state"] == P.EXIT_PAUSED_DATA
    assert D.exit_campaign_decision(True, True, 0.1, 0.0, True, 0.0)["state"] == P.EXIT_PAUSED_BUDGET
    ok = D.exit_campaign_decision(True, True, 0.1, 0.0002, True, 0.0018)
    assert ok["state"] == P.EXIT_WORKING_SHORT and ok["can_order"]


def test_protection_recovery_decision():
    assert P.protection_recovery_decision(False, 0.1, 0.001)["state"] == "HOLD_PROTECTION_UNTIL_SHORT_FLAT"
    assert P.protection_recovery_decision(True, 0.0, 0.001)["state"] == P.EXIT_COMPLETE
    assert P.protection_recovery_decision(True, 0.1, 0)["state"] == P.EXIT_LONG_RESIDUAL      # 无 bid
    sell = P.protection_recovery_decision(True, 0.1, 0.001)
    assert sell["state"] == P.EXIT_WORKING_LONG and sell["can_sell"]
