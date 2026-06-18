# -*- coding: utf-8 -*-
"""BTC-PERPETUAL 对冲生命周期（hedge_*）。纯函数，便于单测。

设计稿 §10 + 补充意见 P0-5 + 用户 v2.1 补充（对冲场所可选）：
  - 对冲工具默认 DERIBIT BTC-PERPETUAL（反向、与期权同所、便于统一账本/恢复）；
    **可选** BINANCE BTCUSDC 永续（线性、USDC maker 0 费）——对冲腿非高频，maker 等成交可省成本。
    场所为**操作者显式配置选择**（HEDGE_VENUE），**非运行时自动切换**（避免补充意见所警示的 UNBOUND）。
  - 目标数量随**剩余卖方期权敞口**变化；短腿归零 / 结构 CLOSED|SETTLED → 目标立即归零（不等保护腿）；
  - **HEDGE_OPEN/INCREASE 非 reduce_only**（reduce_only 无法建仓）；HEDGE_REDUCE/UNWIND 强制 reduce_only；
  - 期权卖方风险消失但 perp 仍有持仓 → 孤儿对冲紧急态（持续 reduce_only 清理，会话不得 CLOSED）。
  - 换算：DERIBIT 反向(USD 合约)=delta_btc·spot/contract_size；BINANCE 线性(BTC)=delta_btc。
"""
HEDGE_INSTRUMENT = "BTC-PERPETUAL"
HEDGE_VENUE = "DERIBIT"

VENUE_DERIBIT = "DERIBIT"
VENUE_BINANCE = "BINANCE"

_EPS = 1e-9


def hedge_venue_config(venue, binance_instrument="BTCUSDC", binance_maker_only=True):
    """返回场所配置 {venue, instrument, linear, maker_only}。
    DERIBIT：BTC-PERPETUAL、反向、对冲开仓可 taker(prompt)；
    BINANCE：BTCUSDC 永续、线性(BTC)、USDC maker 0 费 → 默认强制 maker(post-only)。"""
    if str(venue or VENUE_DERIBIT).upper() == VENUE_BINANCE:
        return {"venue": VENUE_BINANCE, "instrument": binance_instrument,
                "linear": True, "maker_only": bool(binance_maker_only)}
    return {"venue": VENUE_DERIBIT, "instrument": HEDGE_INSTRUMENT,
            "linear": False, "maker_only": False}


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def hedge_side(side):
    """SHORT_CALL 风险上升(delta 正) → BUY BTC-PERP；SHORT_PUT → SELL。"""
    s = str(side or "").upper()
    if s in ("CALL", "SHORT_CALL"):
        return "buy"
    if s in ("PUT", "SHORT_PUT"):
        return "sell"
    return None


def hedge_target_contracts(remaining_short_qty, structure_delta, reduction_ratio,
                           spot, contract_size, min_trade_amount,
                           option_structure_state="OPEN", linear=False):
    """对冲目标数量。硬不变量：短腿归零 或 结构 CLOSED/SETTLED → 0（不等保护腿出售）。
    linear=False(Deribit 反向)：USD 合约 = |rem·delta·ratio|·spot / contract_size；
    linear=True (Binance 线性)：BTC 数量 = |rem·delta·ratio|。结果取整到 min_trade。"""
    if str(option_structure_state).upper() in ("CLOSED", "SETTLED"):
        return 0.0
    if not remaining_short_qty or remaining_short_qty <= _EPS:
        return 0.0
    if not _is_num(structure_delta):
        return 0.0
    delta_btc = abs(remaining_short_qty * structure_delta * (reduction_ratio or 1.0))
    if linear:
        raw = delta_btc
    else:
        if not (_is_num(spot) and _is_num(contract_size)) or contract_size <= 0:
            return 0.0
        raw = delta_btc * spot / contract_size
    if min_trade_amount and min_trade_amount > 0:
        return round(raw / min_trade_amount) * min_trade_amount
    return raw


def hedge_order_action(current_qty, target_qty, min_trade_amount=0.0):
    """据当前 vs 目标决定动作 + reduce_only（P0-5）。
    目标>当前 → HEDGE_OPEN/INCREASE(非 reduce_only)；目标<当前 → HEDGE_REDUCE/UNWIND(reduce_only)。"""
    cur = abs(current_qty or 0.0)
    tgt = abs(target_qty or 0.0)
    step = abs(tgt - cur)
    thr = max(_EPS, (min_trade_amount or 0.0) * 0.5)
    if step <= thr:
        return {"action": "HEDGE_HOLD", "reduce_only": False, "delta_contracts": 0.0}
    if tgt > cur:
        return {"action": ("HEDGE_INCREASE" if cur > _EPS else "HEDGE_OPEN"),
                "reduce_only": False, "delta_contracts": step}
    return {"action": ("HEDGE_UNWIND" if tgt <= _EPS else "HEDGE_REDUCE"),
            "reduce_only": True, "delta_contracts": step}


def hedge_orphan(option_short_qty, perp_qty):
    """期权卖方风险已消失(short<=0) 但 perp 仍有持仓 → 孤儿对冲（须 reduce_only 清理）。"""
    return (not option_short_qty or option_short_qty <= _EPS) and abs(perp_qty or 0.0) > _EPS


def settlement_guard(remaining_short_qty, near_expiry, settled, perp_qty):
    """到期/交割保护：已交割 → 目标强制 0（perp 未归零即孤儿）；临近到期 → 不新增、随剩余短腿归零。"""
    if settled:
        return {"target": 0.0, "orphan": abs(perp_qty or 0.0) > _EPS, "reason": "SETTLED_FORCE_ZERO"}
    if near_expiry:
        flat = (not remaining_short_qty or remaining_short_qty <= _EPS)
        return {"target": (0.0 if flat else None), "orphan": False,
                "reason": "NEAR_EXPIRY_NO_NEW_HEDGE"}
    return {"target": None, "orphan": False, "reason": "NORMAL"}
