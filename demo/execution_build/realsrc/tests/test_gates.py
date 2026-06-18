# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import gates as G


def _allowed(action, e, x, h, kill, emer):
    return G.gate_decision(action, e, x, h, kill, emer)["allowed"]


def test_entry_gate_requires_allow_and_no_kill_no_emergency():
    assert _allowed(G.ACTION_ENTRY, True, False, False, False, False)
    assert not _allowed(G.ACTION_ENTRY, False, False, False, False, False)   # 门关
    assert not _allowed(G.ACTION_ENTRY, True, False, False, True, False)     # kill 阻断进场
    assert not _allowed(G.ACTION_ENTRY, True, False, False, False, True)     # emergency 阻断进场


def test_exit_gate_not_blocked_by_kill_or_emergency():
    # 期权退出降风险：kill / emergency 不阻断，仅 allow_exit 控制
    assert _allowed(G.ACTION_EXIT, False, True, False, True, True)
    assert not _allowed(G.ACTION_EXIT, False, False, False, False, False)


def test_hedge_open_blocked_by_emergency_only_not_kill():
    assert _allowed(G.ACTION_HEDGE_OPEN, False, False, True, False, False)
    assert _allowed(G.ACTION_HEDGE_OPEN, False, False, True, True, False)    # kill 不阻断对冲开（降尾部）
    assert not _allowed(G.ACTION_HEDGE_OPEN, False, False, True, False, True)  # emergency 禁开/加
    assert not _allowed(G.ACTION_HEDGE_OPEN, False, False, False, False, False)  # 门关


def test_hedge_reduce_always_reduce_only_and_survives_kill_emergency():
    d = G.gate_decision(G.ACTION_HEDGE_REDUCE, False, False, True, True, True)
    assert d["allowed"] and d["reduce_only"] is True
    d2 = G.gate_decision(G.ACTION_HEDGE_REDUCE, False, False, False, False, False)
    assert (not d2["allowed"]) and d2["reduce_only"] is True   # 门关仍标记 reduce_only


def test_unknown_action_fails_closed():
    d = G.gate_decision("WAT", True, True, True, False, False)
    assert not d["allowed"]


def test_gate_summary_covers_all_actions():
    s = G.gate_summary(True, True, True, False, False)
    assert set(s.keys()) == set(G.ALL_ACTIONS)
    assert all("reason" in v and "allowed" in v for v in s.values())
