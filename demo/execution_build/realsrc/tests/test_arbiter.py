# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import risk_controls as RC


def test_priority_recovery_orphan_inflight():
    assert RC.unified_action_arbiter({"recovery_blocked": True})["preferred_action"] == "RECOVERY_BLOCKED"
    assert RC.unified_action_arbiter({"orphan_hedge": True})["preferred_action"] == "ORPHAN_HEDGE_EMERGENCY"
    assert RC.unified_action_arbiter({"in_flight_order": True})["preferred_action"] == "MANAGE_IN_FLIGHT"
    # 恢复阻塞优先于一切
    r = RC.unified_action_arbiter({"recovery_blocked": True, "orphan_hedge": True, "exit_preferred": True})
    assert r["preferred_action"] == "RECOVERY_BLOCKED"


def test_exit_executable_when_authorized():
    r = RC.unified_action_arbiter({"exit_preferred": True, "exit_authorized": True,
                                   "exit_executable": True})
    assert r["preferred_action"] == "EXIT_PREFERRED" and r["executable_action"] == "EXIT_PREFERRED"
    assert r["blocked_reason"] is None


def test_exit_unauthorized_falls_back_to_hedge():
    # P0-4 核心：退出未授权但对冲可执行 → executable 回退对冲，不压住风险收口
    r = RC.unified_action_arbiter({"exit_preferred": True, "exit_authorized": False,
                                   "hedge_executable": True})
    assert r["preferred_action"] == "EXIT_PREFERRED"
    assert r["executable_action"] == "HEDGE_READY"
    assert r["blocked_reason"] == "EXIT_NOT_AUTHORIZED"
    assert r["fallback_action"] == "HEDGE_READY"


def test_exit_paused_by_budget_falls_back_to_hedge():
    r = RC.unified_action_arbiter({"exit_preferred": True, "exit_authorized": True,
                                   "exit_executable": False, "exit_pause_reason": "PAUSED_BY_BUDGET",
                                   "hedge_executable": True})
    assert r["executable_action"] == "HEDGE_READY"
    assert r["blocked_reason"] == "EXIT_PAUSED_BY_BUDGET"


def test_exit_blocked_no_hedge_holds():
    r = RC.unified_action_arbiter({"take_profit_ready": True, "exit_authorized": False,
                                   "hedge_executable": False})
    assert r["preferred_action"] == "TAKE_PROFIT_READY"
    assert r["executable_action"] == "HOLD" and r["blocked_reason"] == "EXIT_NOT_AUTHORIZED"


def test_take_profit_executable_when_authorized():
    r = RC.unified_action_arbiter({"take_profit_ready": True, "exit_authorized": True,
                                   "exit_executable": True})
    assert r["preferred_action"] == "TAKE_PROFIT_READY" and r["executable_action"] == "TAKE_PROFIT_READY"


def test_hedge_ready_executable_and_blocked():
    assert RC.unified_action_arbiter({"hedge_ready": True, "hedge_executable": True})["executable_action"] == "HEDGE_READY"
    r = RC.unified_action_arbiter({"hedge_ready": True, "hedge_executable": False})
    assert r["executable_action"] == "HOLD" and r["blocked_reason"] == "HEDGE_NOT_EXECUTABLE"


def test_default_hold():
    r = RC.unified_action_arbiter({})
    assert r["preferred_action"] == "HOLD" and r["executable_action"] == "HOLD"
