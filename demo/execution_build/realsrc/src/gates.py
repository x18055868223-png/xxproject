# -*- coding: utf-8 -*-
"""执行授权门控（gate_*）：把单一 ALLOW_TRADING 拆分为分动作门控，
避免「禁新开仓」误伤风险收口（退出 / 对冲减仓 / 孤儿清理）。纯函数，便于单测。

动作（action）：
  ENTRY         开立新垂直价差（**新增风险**）
  EXIT          买回卖方短腿 / 卖出保护腿（期权，**降风险**）
  HEDGE_OPEN    建立 / 增加 BTC-PERPETUAL 对冲（开 / 加仓；reduce_only 无法建仓，故非 reduce_only）
  HEDGE_REDUCE  对冲减仓 / 平仓（**强制 reduce_only**）

门控旗标（默认全安全）：
  allow_entry / allow_exit / allow_hedge   分动作总开关
  kill_new_risk         急停：停新风险并撤开仓单，但**不阻断**退出 / 对冲减仓 / 对账 / 孤儿清理
  emergency_reduce_only 紧急只减：**禁止任何开 / 加仓**，对冲强制 reduce_only

设计依据（补充意见 P0-3）：单一主门会在「禁新开仓」时连带关闭风险退出与对冲归零，
故必须拆分；急停只停新风险，恢复需重新对账并要求新的计划硬批准。
"""

ACTION_ENTRY = "ENTRY"
ACTION_EXIT = "EXIT"
ACTION_HEDGE_OPEN = "HEDGE_OPEN"
ACTION_HEDGE_REDUCE = "HEDGE_REDUCE"

ALL_ACTIONS = (ACTION_ENTRY, ACTION_EXIT, ACTION_HEDGE_OPEN, ACTION_HEDGE_REDUCE)


def _d(action, allowed, reduce_only, reason):
    return {"action": action, "allowed": bool(allowed),
            "reduce_only": bool(reduce_only), "reason": reason}


def gate_decision(action, allow_entry, allow_exit, allow_hedge,
                  kill_new_risk, emergency_reduce_only):
    """对单个动作给出门控裁决。

    返回 {"action", "allowed": bool, "reduce_only": bool, "reason": str}。
    `reduce_only` 仅对 HEDGE_* 有意义（HEDGE_REDUCE 恒 True）。
    """
    allow_entry = bool(allow_entry)
    allow_exit = bool(allow_exit)
    allow_hedge = bool(allow_hedge)
    kill = bool(kill_new_risk)
    emer = bool(emergency_reduce_only)

    if action == ACTION_ENTRY:
        if emer:
            return _d(action, False, False, "ENTRY_BLOCKED_EMERGENCY_REDUCE_ONLY")
        if kill:
            return _d(action, False, False, "ENTRY_BLOCKED_KILL_NEW_RISK")
        if not allow_entry:
            return _d(action, False, False, "ENTRY_GATE_OFF")
        return _d(action, True, False, "ENTRY_ALLOWED")

    if action == ACTION_EXIT:
        # 期权退出为降风险动作：kill / emergency 均不阻断，只由 allow_exit 控制
        if not allow_exit:
            return _d(action, False, False, "EXIT_GATE_OFF")
        return _d(action, True, False, "EXIT_ALLOWED")

    if action == ACTION_HEDGE_OPEN:
        # 紧急只减禁止开 / 加仓；但 kill_new_risk 不阻断对冲开（对冲压缩尾部 = 降险）
        if emer:
            return _d(action, False, False, "HEDGE_OPEN_BLOCKED_EMERGENCY_REDUCE_ONLY")
        if not allow_hedge:
            return _d(action, False, False, "HEDGE_GATE_OFF")
        return _d(action, True, False, "HEDGE_OPEN_ALLOWED")

    if action == ACTION_HEDGE_REDUCE:
        # 对冲减仓恒强制 reduce_only；kill / emergency 下仍允许（风险收口）
        if not allow_hedge:
            return _d(action, False, True, "HEDGE_GATE_OFF")
        return _d(action, True, True, "HEDGE_REDUCE_ALLOWED")

    return _d(action, False, False, "UNKNOWN_ACTION")


def gate_summary(allow_entry, allow_exit, allow_hedge,
                 kill_new_risk, emergency_reduce_only):
    """返回各动作裁决 dict，供面板「交互控制台」一眼看清当前可执行动作。"""
    return {a: gate_decision(a, allow_entry, allow_exit, allow_hedge,
                             kill_new_risk, emergency_reduce_only)
            for a in ALL_ACTIONS}
