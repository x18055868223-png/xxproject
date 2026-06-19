# -*- coding: utf-8 -*-
"""持仓生命周期：入场快照冻结 + 止盈预算锚（pos_*）。纯函数，便于单测。

设计稿 §2.2 / §8.3：入场成交后冻结 `entry_profit_ceiling_net` 为 80% 阈值的审计基准，
**入场后禁止重新计算或覆盖**。止盈预算由该冻结值反推：
    target_profit_amount = ceiling × take_profit_target_ratio (默认 0.80)
    max_total_exit_spend = ceiling − target_profit_amount      (= ceiling × 0.20)
保护腿回收价值默认按 0（不进入 80% 预算分母），见 E6。
"""

import math

DEFAULT_TAKE_PROFIT_RATIO = 0.80

# 退出活动状态
EXIT_IDLE = "IDLE"
EXIT_WAIT_TRIGGER = "WAIT_TRIGGER"
EXIT_WORKING_SHORT = "WORKING_SHORT"
EXIT_PAUSED_BUDGET = "PAUSED_BY_BUDGET"
EXIT_PAUSED_DATA = "PAUSED_BY_DATA"
EXIT_WORKING_LONG = "WORKING_LONG"
EXIT_LONG_RESIDUAL = "LONG_RESIDUAL_ONLY"
EXIT_COMPLETE = "COMPLETE"


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def entry_profit_ceiling_net(short_credit, long_debit, entry_fees):
    """入场利润上限（结算币）= 卖方实收 − 保护腿实付 − 入场手续费。任一缺失 → None。"""
    if short_credit is None or long_debit is None or entry_fees is None:
        return None
    return short_credit - long_debit - entry_fees


def build_vertical_entry_snapshot(locked, short_fill, long_fill, entry_fees,
                                  now_ts, take_profit_ratio=DEFAULT_TAKE_PROFIT_RATIO,
                                  entry_risk_anchor=None):
    """成交后冻结入场快照。short_fill/long_fill: {filled, avg_price}。
    `entry_profit_ceiling_net` 一经冻结即为审计基准，禁止后续覆盖（见 freeze_entry_ceiling）。
    `entry_risk_anchor`（hedge_risk.build_entry_risk_anchor）与 `short_expiry_ts` 一并冻结，
    供持仓后「风险严重度→仲裁」逐轮调用 evaluate_position_risk。"""
    locked = locked or {}
    sc = (short_fill or {}).get("avg_price")
    sa = (short_fill or {}).get("filled")
    lc = (long_fill or {}).get("avg_price")
    la = (long_fill or {}).get("filled")
    short_credit = (sc * sa) if (sc is not None and sa is not None) else None
    long_debit = (lc * la) if (lc is not None and la is not None) else None
    ceiling = entry_profit_ceiling_net(short_credit, long_debit, entry_fees)
    target_profit = (ceiling * take_profit_ratio) if ceiling is not None else None
    max_exit_spend = ((ceiling - target_profit)
                      if (ceiling is not None and target_profit is not None) else None)
    return {
        "schema_name": "VerticalEntrySnapshot",
        "position_id": "pos-%s" % now_ts,
        "session_id": locked.get("session_id"),
        "signal_package_id": locked.get("signal_package_id"),
        "strategy_code": locked.get("strategy_code"),
        "quality_code": locked.get("quality_code"),
        "plan_hash": locked.get("plan_hash"),
        "side": locked.get("side"),
        "short_instrument": locked.get("short_instrument"),
        "long_instrument": locked.get("long_instrument"),
        "short_fill_amount": sa, "short_fill_price": sc,
        "long_fill_amount": la, "long_fill_price": lc,
        "entry_fees": entry_fees,
        "entry_profit_ceiling_net": ceiling,            # 不可覆盖（审计基准）
        "take_profit_target_ratio": take_profit_ratio,
        "target_profit_amount": target_profit,
        "max_total_exit_spend": max_exit_spend,
        "realized_exit_spend": 0.0,
        "remaining_short_qty": sa,
        "long_remaining_qty": la,          # 保护腿剩余（回收时递减；持仓真相之一）
        "short_expiry_ts": locked.get("short_expiry"),     # 短腿到期（持仓后 DTE/风险评估用）
        "entry_risk_anchor": entry_risk_anchor,            # 入场风险锚（风险严重度→仲裁）
        "frozen_ts": now_ts,
        "immutable": True,
    }


def freeze_entry_ceiling(existing_snapshot, recomputed_ceiling=None):
    """守卫：入场后永远返回已冻结的 entry_profit_ceiling_net，忽略任何重算值。
    返回 (frozen_value, tamper_detected)；recomputed 与冻结值不一致仅供审计标记，不改值。"""
    if not existing_snapshot:
        return None, False
    frozen = existing_snapshot.get("entry_profit_ceiling_net")
    tamper = (recomputed_ceiling is not None
              and frozen is not None
              and abs(float(recomputed_ceiling) - float(frozen)) > 1e-12)
    return frozen, tamper


# ---------- E6：止盈资格（资格与成交解耦，§2.3）----------

def reference_profit_capture_ratio(entry_ceiling, conservative_short_buyback_ref,
                                   estimated_short_exit_fee, exit_reserve):
    """止盈资格参考捕获率。保护腿价值**不进分母**（默认按 0）：
    reference_exit_spend = 保守短腿买回参考 + 短腿退出费 + 退出预留
    ratio = (entry_ceiling - reference_exit_spend) / entry_ceiling
    任一输入缺失或 ceiling<=0 → None（不触发自动止盈，标记数据缺口，仅监控）。"""
    if not _is_num(entry_ceiling) or entry_ceiling <= 0:
        return None
    parts = (conservative_short_buyback_ref, estimated_short_exit_fee, exit_reserve)
    if any(not _is_num(p) for p in parts):
        return None
    ref_spend = sum(parts)
    return (entry_ceiling - ref_spend) / entry_ceiling


def take_profit_qualified(reference_ratio, target_ratio=DEFAULT_TAKE_PROFIT_RATIO):
    """资格触发：参考捕获率 >= 目标(默认 0.80)。ratio None → 未达资格(数据缺口)。"""
    return _is_num(reference_ratio) and reference_ratio >= target_ratio


# ---------- E6：低成本退出硬预算 + 价格上限（§7.2 / §7.3）----------

def short_buyback_budget(max_total_exit_spend, realized_exit_spend, fee_reserve):
    """剩余短腿买回预算 = max_total_exit_spend − 已用 − 费用预留（不小于 0）。"""
    if not _is_num(max_total_exit_spend):
        return None
    return max(0.0, max_total_exit_spend - (realized_exit_spend or 0.0) - (fee_reserve or 0.0))


def short_buyback_price_cap(remaining_budget, fee_reserve, remaining_short_qty, tick):
    """每轮价格上限由剩余预算反推并向下取整到 tick：
    cap = floor_to_tick((remaining_budget − fee_reserve) / remaining_short_qty)。
    数量<=0 或预算不足 → 0（不下单）。"""
    if not (_is_num(remaining_budget) and _is_num(remaining_short_qty)) or remaining_short_qty <= 0:
        return 0.0
    avail = remaining_budget - (fee_reserve or 0.0)
    if avail <= 0:
        return 0.0
    raw = avail / remaining_short_qty
    if tick and tick > 0:
        return math.floor(raw / tick) * tick
    return raw


def within_exit_budget(order_price, order_amount, estimated_fee, remaining_budget):
    """订单是否在剩余预算内：price*amount + fee <= remaining_budget。"""
    if not all(_is_num(x) for x in (order_price, order_amount, estimated_fee, remaining_budget)):
        return False
    return order_price * order_amount + estimated_fee <= remaining_budget + 1e-12


def exit_campaign_decision(authorized, qualified, remaining_short_qty,
                           remaining_budget, quote_ok, price_cap):
    """退出活动下一状态/是否可下单（纯函数，不做 I/O；§7）。
    优先：短腿归零→转保护腿回收；未授权→IDLE；未达资格→WAIT_TRIGGER；
    无盘口→PAUSED_BY_DATA；预算/上限不足→PAUSED_BY_BUDGET；否则→WORKING_SHORT(可买回)。"""
    if remaining_short_qty is not None and remaining_short_qty <= 0:
        return {"state": EXIT_WORKING_LONG, "can_order": False, "reason": "SHORT_FLAT"}
    if not authorized:
        return {"state": EXIT_IDLE, "can_order": False, "reason": "UNAUTHORIZED"}
    if not qualified:
        return {"state": EXIT_WAIT_TRIGGER, "can_order": False, "reason": "NOT_QUALIFIED"}
    if not quote_ok:
        return {"state": EXIT_PAUSED_DATA, "can_order": False, "reason": "NO_RELIABLE_QUOTE"}
    if not price_cap or price_cap <= 0 or not _is_num(remaining_budget) or remaining_budget <= 0:
        return {"state": EXIT_PAUSED_BUDGET, "can_order": False, "reason": "BUDGET_EXHAUSTED"}
    return {"state": EXIT_WORKING_SHORT, "can_order": True, "reason": "BUYBACK_WITHIN_BUDGET"}


def protection_recovery_decision(short_flat, prot_qty, prot_bid):
    """短腿归零后保护腿回收决策（纯）：先平短腿；无 bid → LONG_RESIDUAL_ONLY 保持等结算。"""
    if not short_flat:
        return {"state": "HOLD_PROTECTION_UNTIL_SHORT_FLAT", "can_sell": False}
    if not prot_qty or prot_qty <= 0:
        return {"state": EXIT_COMPLETE, "can_sell": False}
    if not prot_bid or prot_bid <= 0:
        return {"state": EXIT_LONG_RESIDUAL, "can_sell": False}
    return {"state": EXIT_WORKING_LONG, "can_sell": True}


# ---------- G1：开仓活动（entry campaign）：跨轮持久 maker + 信用底线（低成本 ∧ 提高成功率）----------

ENTRY_IDLE = "ENTRY_IDLE"
ENTRY_WORKING = "ENTRY_WORKING"
ENTRY_PAUSED_DATA = "ENTRY_PAUSED_DATA"
ENTRY_PAUSED_CREDIT = "ENTRY_PAUSED_CREDIT"
ENTRY_ABANDONED = "ENTRY_ABANDONED"
ENTRY_COMPLETE = "ENTRY_COMPLETE"


def entry_net_credit(short_sell_price, prot_buy_price, amount, total_fees):
    """入场净 credit = (短腿卖价 − 保护腿买价)×数量 − 总手续费。任一缺失 → None。"""
    if not all(_is_num(x) for x in (short_sell_price, prot_buy_price, amount)):
        return None
    return (short_sell_price - prot_buy_price) * amount - (total_fees or 0.0)


def entry_credit_capped_index(prot_buy_prices, short_sell_prices, amount, total_fees, credit_floor):
    """在「逐 tick 改善」价格阶梯中返回净 credit ≥ floor 的**最激进**档 index；无则 -1。
    约定：prot_buy_prices 升序(越激进越高)、short_sell_prices 降序(越激进越低) → 净credit 随 index 递减。
    这是低成本与成功率的结合点：可向触价改善以提高成交率，但永不突破信用底线。"""
    best = -1
    n = min(len(prot_buy_prices or []), len(short_sell_prices or []))
    for i in range(n):
        nc = entry_net_credit(short_sell_prices[i], prot_buy_prices[i], amount, total_fees)
        if nc is not None and nc >= credit_floor:
            best = i
    return best


def entry_campaign_decision(has_locked, quotes_ok, credit_ok, attempts, max_attempts,
                            prot_done, short_done):
    """开仓活动下一状态 / 是否可下单（纯）。信用底线不满足 → 暂停等市场或（额度耗尽）放弃。"""
    if not has_locked:
        return {"state": ENTRY_IDLE, "can_order": False, "reason": "NO_LOCKED_PLAN"}
    if prot_done and short_done:
        return {"state": ENTRY_COMPLETE, "can_order": False, "reason": "FILLED"}
    if not quotes_ok:
        return {"state": ENTRY_PAUSED_DATA, "can_order": False, "reason": "NO_RELIABLE_QUOTE"}
    if not credit_ok:
        if attempts >= max_attempts:
            return {"state": ENTRY_ABANDONED, "can_order": False, "reason": "CREDIT_FLOOR_UNREACHABLE"}
        return {"state": ENTRY_PAUSED_CREDIT, "can_order": False, "reason": "BELOW_CREDIT_FLOOR_WAIT"}
    if attempts >= max_attempts:
        return {"state": ENTRY_ABANDONED, "can_order": False, "reason": "MAX_ATTEMPTS_EXCEEDED"}
    return {"state": ENTRY_WORKING, "can_order": True, "reason": "POST_WITHIN_CREDIT_FLOOR"}


# ---------- P0①：持仓对账（快照为唯一持仓真相 vs 交易所真实期权持仓）----------

def position_reconcile(snap, option_positions):
    """以入场快照（短腿剩余 / 保护腿剩余）为期望，与交易所真实期权持仓比对。
    返回 {reconciled, reasons}。无快照(无持仓)+交易所也无我方合约 → reconciled=True。"""
    actual = {}
    for p in (option_positions or []):
        inst, sz = p.get("instrument_name"), p.get("size")
        if inst and sz:
            actual[inst] = sz
    expected = {}
    if snap:
        si, li = snap.get("short_instrument"), snap.get("long_instrument")
        rs = snap.get("remaining_short_qty") or 0.0
        lr = snap.get("long_remaining_qty")
        if lr is None:
            lr = snap.get("long_fill_amount") or 0.0
        if si and rs > 1e-12:
            expected[si] = -rs                       # 卖方腿 = 负持仓
        if li and lr > 1e-12:
            expected[li] = lr                        # 保护腿 = 正持仓
    reasons = []
    for inst, sz in expected.items():
        a = actual.get(inst)
        if a is None or abs(a - sz) > 1e-9:
            reasons.append("MISMATCH:%s exp=%s act=%s" % (inst, sz, a))
    for inst, sz in actual.items():                  # 交易所有、快照未含 → 不可解释
        if inst not in expected:
            reasons.append("UNEXPECTED:%s=%s" % (inst, sz))
    return {"reconciled": not reasons, "reasons": reasons}
