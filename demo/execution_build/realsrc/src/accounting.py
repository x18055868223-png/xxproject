# -*- coding: utf-8 -*-
"""
损耗记账（acct_*，§11）+ 全量信息报告（§13）。

口径统一以**结算币（BTC/ETH）**计价；期权权利金/mark 在 Deribit 即以标的币报价。
USD 仅用 index_price 换算展示。全部为纯函数，便于单测。
"""

OPTION_FEE_CAP_CCY = 0.0003   # 每张封顶（BTC/ETH 同值，结算币计）
OPTION_FEE_RATE    = 0.125    # 权利金比例上限 12.5%


# ---------- A. 显性交易费（§1.2）----------

def acct_option_fee_ccy(option_price_ccy, amount):
    """结算币计：MIN(0.0003, 0.125*option_price) * amount。"""
    per = min(OPTION_FEE_CAP_CCY, OPTION_FEE_RATE * option_price_ccy)
    return per * amount


def acct_option_fee_usd(option_price_ccy, amount, index_price):
    """USD 展示：MIN(0.0003*index, 0.125*option_price_usd) * amount。"""
    option_price_usd = option_price_ccy * index_price
    per = min(OPTION_FEE_CAP_CCY * index_price, OPTION_FEE_RATE * option_price_usd)
    return per * amount


# ---------- B. mark 偏离 ----------

def acct_mark_slippage(side, fill_price, mark_price, amount):
    """成交价相对 mark 的不利偏离（正=不利）。"""
    if side == "buy":
        return (fill_price - mark_price) * amount
    return (mark_price - fill_price) * amount


# ---------- C. 一步追价损耗 ----------

def acct_chase_cost(side, price0, final_price, amount):
    """相对初始挂价 price0 的追价损耗（正=不利）。"""
    if side == "buy":
        return (final_price - price0) * amount
    return (price0 - final_price) * amount


# ---------- D. bid/ask 价差损耗（参考：半价差）----------

def acct_spread_cost(best_bid, best_ask, amount):
    if best_bid is None or best_ask is None:
        return None
    return (best_ask - best_bid) / 2.0 * amount


# ---------- 远期保护腿真实成本（§11.2）----------

def acct_protection_realized_cost(entry_price, entry_fee, exit_fee=0.0,
                                  spread_slippage=0.0, exit_value=0.0):
    return entry_price + entry_fee + exit_fee + spread_slippage - exit_value


def acct_protection_cost_per_day(realized_cost, protected_days):
    if not protected_days:
        return None
    return realized_cost / protected_days


def acct_protection_cost_per_short_cycle(realized_cost, covered_cycle_count):
    if not covered_cycle_count:
        return None
    return realized_cost / covered_cycle_count


# ---------- F. full-burn 压力测试（§11.3，仅压测口径，不作默认真实成本）----------

def acct_full_burn(entry_price, entry_fee):
    return entry_price + entry_fee


# ---------- §13 全量报告 ----------

def acct_build_report(ctx):
    """组装设计稿 §13 结构 + 选腿/执行/记账明细，作为每次进场前的核对载体。
    ctx 为已采集字段的 dict；缺失字段以 None 占位。"""
    g = ctx.get
    return {
        "structure_type": "VERTICAL_CREDIT_SPREAD",
        "account_margin_mode": "S:PM",
        "settlement_currency": g("currency"),
        "signal_state": g("signal_state"),
        "direction_bias": g("direction_bias"),
        "allow_trading": g("allow_trading"),
        "state": g("state"),
        "short_leg": {
            "instrument": g("short_instrument"),
            "strike": g("short_strike"),
            "dte_hours": g("short_dte_hours"),
            "side": "SELL",
            "role": "NEAR_TERM_SHORT_PREMIUM",
            "mark": g("short_mark"),
            "best_bid": g("short_bid"),
            "best_ask": g("short_ask"),
            "tick_size": g("short_tick"),
        },
        "protection_leg": {
            "instrument": g("protection_instrument"),
            "strike": g("protection_strike"),
            "dte_days": g("protection_dte_days"),
            "side": "BUY",
            "role": "FAR_TERM_ECONOMIC_PROTECTION",
            "is_inventory_reuse": g("is_inventory_reuse") or False,
            "delta": g("protection_delta"),
            "mark": g("protection_mark"),
            "best_bid": g("protection_bid"),
            "best_ask": g("protection_ask"),
            "tick_size": g("protection_tick"),
        },
        "spm_report": {
            "im_short_only": g("im_short_only"),
            "im_with_protection": g("im_with_protection"),
            "margin_relief_abs": g("margin_relief_abs"),
            "margin_relief_ratio": g("margin_relief_ratio"),
            "min_required_ratio": g("min_required_ratio"),
            "pm_accepted": g("pm_accepted"),
            "account_margin_model": g("account_margin_model"),
        },
        "cost_report": {
            "estimated_entry_fee": g("estimated_entry_fee"),
            "estimated_mark_slippage": g("estimated_mark_slippage"),
            "estimated_chase_slippage": g("estimated_chase_slippage"),
            "estimated_spread_cost": g("estimated_spread_cost"),
            "short_premium_income": g("short_premium_income"),
            "full_burn_cost": g("full_burn_cost"),
            "protection_cost_per_day": g("protection_cost_per_day"),
            "protection_cost_per_short_cycle": g("protection_cost_per_short_cycle"),
            "expected_recoverable_value": g("expected_recoverable_value"),
            "cost_basis_note_cn": "保护腿真实成本按退出残值与覆盖周期摊销，不按买入价一次性计入；full_burn 仅压测。",
        },
        "execution_policy": {
            "maker_only": True,
            "max_chase_steps": g("max_chase_steps"),
            "protection_first": True,
            "allow_add_on_same_direction_signal": False,
        },
    }
