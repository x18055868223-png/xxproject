"""Minimal scalar commercial math helpers for v1.7 ledgers."""
from __future__ import annotations

import math


def _side(side: str) -> str:
    if side not in ("put", "call"):
        raise ValueError("side must be 'put' or 'call'")
    return side


def _positive(name: str, value) -> float:
    x = float(value)
    if not math.isfinite(x) or x <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return x


def _nonnegative(name: str, value) -> float:
    x = float(value)
    if not math.isfinite(x) or x < 0:
        raise ValueError(f"{name} must be nonnegative and finite")
    return x


def _known(value):
    if value is None:
        return None
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("known cashflow values must be finite")
    return x


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def inverse_leg_payout(side, strike, settlement, quantity=1.0) -> float:
    """Inverse option terminal payout in BTC for one leg, positive to the option holder."""
    side = _side(side)
    strike = _positive("strike", strike)
    settlement = _positive("settlement", settlement)
    quantity = _positive("quantity", quantity)
    if side == "put":
        intrinsic = max(strike - settlement, 0.0)
    else:
        intrinsic = max(settlement - strike, 0.0)
    return quantity * intrinsic / settlement


def inverse_spread_payout(side, short, long, settlement, quantity=1.0) -> float:
    """Terminal BTC liability of a vertical credit spread before entry credit."""
    side = _side(side)
    short = _positive("short", short)
    long = _positive("long", long)
    if side == "put" and short <= long:
        raise ValueError("put credit spread requires short strike > long strike")
    if side == "call" and short >= long:
        raise ValueError("call credit spread requires short strike < long strike")
    short_payout = inverse_leg_payout(side, short, settlement, quantity)
    long_payout = inverse_leg_payout(side, long, settlement, quantity)
    return short_payout - long_payout


def _bs_leg_value_usd(side: str, spot: float, strike: float, hours: float, iv: float) -> float:
    if hours == 0:
        if side == "put":
            return max(strike - spot, 0.0)
        return max(spot - strike, 0.0)
    t = hours / 8760.0
    v = iv * math.sqrt(t)
    d1 = (math.log(spot / strike) + 0.5 * v * v) / v
    d2 = d1 - v
    if side == "put":
        return strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
    return spot * _norm_cdf(d1) - strike * _norm_cdf(d2)


def bs_leg_prices_btc(side, spot, short, long, hours, iv, quantity=1.0):
    """Scalar Black-Scholes leg premiums in BTC, matching the v1.6 r=q=0 quote."""
    side = _side(side)
    spot = _positive("spot", spot)
    short = _positive("short", short)
    long = _positive("long", long)
    hours = _nonnegative("hours", hours)
    iv = _positive("iv", iv)
    quantity = _positive("quantity", quantity)
    if side == "put" and short <= long:
        raise ValueError("put credit spread requires short strike > long strike")
    if side == "call" and short >= long:
        raise ValueError("call credit spread requires short strike < long strike")
    short_premium = quantity * _bs_leg_value_usd(side, spot, short, hours, iv) / spot
    long_premium = quantity * _bs_leg_value_usd(side, spot, long, hours, iv) / spot
    return short_premium, long_premium


def static_cashflow(credit_btc, payout_btc, option_cost_btc, hedge_net_btc=None) -> dict:
    credit = _known(credit_btc)
    payout = _known(payout_btc)
    cost = _known(option_cost_btc)
    static_net = None if None in (credit, payout, cost) else credit - payout - cost
    hedge = _known(hedge_net_btc)
    hedged_net = None if static_net is None or hedge is None else static_net + hedge
    return {"static_net_btc": static_net, "hedged_net_btc": hedged_net}


def exit_cashflow(credit_btc, buyback_btc, option_cost_btc, hedge_net_btc=None) -> dict:
    credit = _known(credit_btc)
    buyback = _known(buyback_btc)
    cost = _known(option_cost_btc)
    static_net = None if None in (credit, buyback, cost) else credit - buyback - cost
    hedge = _known(hedge_net_btc)
    hedged_net = None if static_net is None or hedge is None else static_net + hedge
    return {"static_net_btc": static_net, "hedged_net_btc": hedged_net}


def required_net_credit(mu_btc, cost_btc):
    mu = _known(mu_btc)
    cost = _known(cost_btc)
    if mu is None or cost is None:
        return None
    return mu + cost
