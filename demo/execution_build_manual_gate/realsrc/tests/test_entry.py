# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import position as P


def _approx(a, b, eps=1e-12):
    return abs(a - b) <= eps


def test_entry_net_credit():
    # (0.012 - 0.006)*0.1 - 0.00005 = 0.0006 - 0.00005 = 0.00055
    assert _approx(P.entry_net_credit(0.012, 0.006, 0.1, 0.00005), 0.00055)
    assert P.entry_net_credit(None, 0.006, 0.1, 0.0) is None


def test_entry_credit_capped_index_picks_most_aggressive_within_floor():
    # 逐 tick 改善：保护买价升、短腿卖价降 → 净credit 递减
    prot = [0.0060, 0.0061, 0.0062, 0.0063]     # 升序(越激进越高)
    shrt = [0.0120, 0.0119, 0.0118, 0.0117]     # 降序(越激进越低)
    # 净credit(i) = (shrt[i]-prot[i])*0.1 - 0 ；i=0:0.0006, i=1:0.00058, i=2:0.00056, i=3:0.00054
    assert P.entry_credit_capped_index(prot, shrt, 0.1, 0.0, 0.00056) == 2   # ≥0.00056 的最激进档
    assert P.entry_credit_capped_index(prot, shrt, 0.1, 0.0, 0.0) == 3       # 全满足 → 最末档
    assert P.entry_credit_capped_index(prot, shrt, 0.1, 0.0, 0.001) == -1    # 全不满足


def test_entry_campaign_decision_branches():
    E = P
    assert E.entry_campaign_decision(False, True, True, 0, 20, False, False)["state"] == E.ENTRY_IDLE
    assert E.entry_campaign_decision(True, True, True, 0, 20, True, True)["state"] == E.ENTRY_COMPLETE
    assert E.entry_campaign_decision(True, False, True, 0, 20, False, False)["state"] == E.ENTRY_PAUSED_DATA
    # 信用底线不满足 + 仍有额度 → 暂停等市场
    assert E.entry_campaign_decision(True, True, False, 5, 20, False, False)["state"] == E.ENTRY_PAUSED_CREDIT
    # 信用底线不满足 + 额度耗尽 → 放弃
    assert E.entry_campaign_decision(True, True, False, 20, 20, False, False)["state"] == E.ENTRY_ABANDONED
    # 可成交
    ok = E.entry_campaign_decision(True, True, True, 3, 20, False, False)
    assert ok["state"] == E.ENTRY_WORKING and ok["can_order"]
    # 额度耗尽仍未成交 → 放弃
    assert E.entry_campaign_decision(True, True, True, 20, 20, False, False)["state"] == E.ENTRY_ABANDONED
