# -*- coding: utf-8 -*-
"""
库存账本 + 状态机 + 持久化 + 启动对账（§8 / §9）。

库存与状态经 FMZ `_G()` 持久化，崩溃/重启后可恢复。启动时与交易所实际持仓做基础对账，
不一致仅告警，不自动改仓（自动恢复留 v1.1）。
"""

from fmz_shim import _G, Log
from deribit_io import dbt_get_positions

# ---------- 状态机（§9）----------
S_NO_POSITION              = "NO_POSITION"
S_SIGNAL_READY             = "SIGNAL_READY"
S_PROTECTION_SELECTION     = "PROTECTION_SELECTION"
S_SPM_SIMULATION           = "SPM_SIMULATION"
S_PROTECTION_BUILDING      = "PROTECTION_BUILDING"
S_PROTECTION_ACTIVE_NO_SHORT = "PROTECTION_ACTIVE_NO_SHORT"
S_SHORT_BUILDING           = "SHORT_BUILDING"
S_SHORT_ACTIVE_PROTECTED   = "SHORT_ACTIVE_PROTECTED"
S_HOLD_MONITORING          = "HOLD_MONITORING"
S_SHORT_EXPIRED_OR_CLOSED  = "SHORT_EXPIRED_OR_CLOSED"
S_REUSE_DECISION           = "REUSE_DECISION"
S_EXIT_OR_WAIT_REVIEW      = "EXIT_OR_WAIT_REVIEW"
S_SHORT_FLAT_LONG_RESIDUAL = "SHORT_FLAT_LONG_RESIDUAL"   # 短腿归零、保护腿待回收（不可直跳 CLOSED）
S_CLOSED                   = "CLOSED"

_LEDGER_KEY = "spm_ledger_v1"
_STATE_KEY = "spm_state_v1"

_DEFAULT_LEDGER = {
    "protection": None,   # 单条保护腿库存（v1 限 1 张覆盖 1 张，§8.2）
    "short": None,        # 当前近端 short 腿
    "history": [],        # 已结束结构的记账留档
}


# ---------- 持久化 ----------

def ledger_load():
    led = _G(_LEDGER_KEY)
    if not led:
        led = dict(_DEFAULT_LEDGER)
        led["history"] = []
    return led


def ledger_save(led):
    _G(_LEDGER_KEY, led)
    return led


def ledger_get_state():
    return _G(_STATE_KEY) or S_NO_POSITION


def ledger_set_state(state):
    _G(_STATE_KEY, state)
    Log("[state] ->", state)
    return state


# ---------- 库存记录（§8.1）----------

def ledger_make_inventory(instrument, option_type, strike, expiry,
                          amount, entry_price, entry_fee, margin_relief_ratio):
    return {
        "inventory_id": "prot_" + str(instrument),
        "instrument": instrument,
        "option_type": option_type,
        "strike": strike,
        "expiry": expiry,
        "amount_total": amount,
        "amount_free": amount,
        "amount_allocated": 0.0,
        "entry_price": entry_price,
        "entry_fee": entry_fee,
        "current_mark": entry_price,
        "unrealized_pnl": 0.0,
        "realized_short_premium_against_it": 0.0,
        "reuse_count": 0,
        "last_margin_relief_ratio": margin_relief_ratio,
        "status": "AVAILABLE",
    }


def ledger_allocate_short(led, amount):
    """short 占用保护腿可用量（硬保证 short <= amount_free）。"""
    prot = led.get("protection")
    if not prot:
        return False
    if amount > prot["amount_free"] + 1e-12:
        Log("[ledger] 拒绝：short 数量 %s > 保护腿可用 %s" % (amount, prot["amount_free"]))
        return False
    prot["amount_free"] -= amount
    prot["amount_allocated"] += amount
    return True


def ledger_release_short(led, amount):
    prot = led.get("protection")
    if not prot:
        return
    prot["amount_free"] += amount
    prot["amount_allocated"] = max(0.0, prot["amount_allocated"] - amount)


# ---------- 进场门控（§4.1）----------

def ledger_can_enter(signal_state, enter_signals):
    return signal_state in enter_signals


# ---------- 启动对账（§5 缺口补强）----------

def ledger_reconcile(currency, kind="option"):
    """对比 _G 账本与交易所实际期权持仓，不一致告警，不自动改仓。"""
    led = ledger_load()
    positions = dbt_get_positions(currency, kind) or []
    actual = {}
    for p in positions:
        inst = p.get("instrument_name")
        sz = p.get("size")
        if inst and sz:
            actual[inst] = sz

    expected = {}
    prot = led.get("protection")
    if prot and prot.get("status") == "AVAILABLE":
        expected[prot["instrument"]] = prot["amount_total"]
    sh = led.get("short")
    if sh:
        expected[sh["instrument"]] = -sh.get("amount", 0.0)

    for inst, sz in expected.items():
        a = actual.get(inst)
        if a is None:
            Log("[reconcile] 告警：账本有 %s(%s) 但交易所无持仓" % (inst, sz))
        elif abs(a - sz) > 1e-9:
            Log("[reconcile] 告警：%s 账本=%s 实际=%s 不一致" % (inst, sz, a))
    for inst, sz in actual.items():
        if inst not in expected:
            Log("[reconcile] 告警：交易所有持仓 %s(%s) 但账本未记录" % (inst, sz))

    return {"ledger": led, "actual": actual, "expected": expected}


# ---------- 启动恢复裁决（§11.3；纯函数，便于单测）----------

def evaluate_startup_recovery(option_positions, perp_position_qty,
                              ledger_short_qty, active_orders=None, expected_long_qty=0.0):
    """据交易所真实持仓 / 持仓记录(快照) / 活动订单建立可解释映射并裁决：
      RECOVERY_BLOCKED：身份不明活动订单；或记录有卖方短腿但交易所无期权；
        或**交易所有期权持仓但记录无对应持仓**（P0①：防 v3 持仓未被对账/恢复看见）；
      ORPHAN_HEDGE_EMERGENCY：存在 BTC-PERPETUAL 对冲持仓但已无期权卖方风险；
      OK：可解释。allow_new_open 仅 OK 时为真（恢复完成前禁开新仓）。
    ledger_short_qty/expected_long_qty 来自持仓快照（_POSITION_KEY）的剩余短/保护腿。"""
    reasons = []
    active_orders = active_orders or []
    unknown = [o for o in active_orders if not o.get("label")]
    if unknown:
        reasons.append("UNKNOWN_ACTIVE_ORDERS:%d" % len(unknown))
    opt_qty = sum(abs(p.get("size") or 0.0) for p in (option_positions or []))
    ledger_short = abs(ledger_short_qty or 0.0)
    expected_opt = ledger_short + abs(expected_long_qty or 0.0)
    if ledger_short > 1e-9 and opt_qty <= 1e-9:
        reasons.append("RECORD_SHORT_BUT_NO_EXCHANGE_OPTION")
    if opt_qty > 1e-9 and expected_opt <= 1e-9:
        reasons.append("EXCHANGE_OPTION_BUT_NO_RECORDED_POSITION")
    if reasons:
        return {"state": "RECOVERY_BLOCKED", "reasons": reasons, "allow_new_open": False}
    if abs(perp_position_qty or 0.0) > 1e-9 and ledger_short <= 1e-9:
        return {"state": "ORPHAN_HEDGE_EMERGENCY",
                "reasons": ["PERP_HEDGE_WITHOUT_OPTION_SHORT_RISK"], "allow_new_open": False}
    return {"state": "OK", "reasons": [], "allow_new_open": True}
