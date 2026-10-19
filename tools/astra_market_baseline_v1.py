"""Market-implied probability baseline for one frozen Deribit BTC vertical (2026-10-20).

Program facts for the case file (docs/opus/01 §7.1). Every number comes from the
exchange's own marks in the frozen capture: each leg's mark IV, delta, mark price and
the expiry's underlying (forward) price. Nothing is fitted or forecast.

What the numbers are: risk-neutral, market-implied readings (Black-76, r=0), not
real-world probabilities. Each tail probability uses the IV of the strike it is
measured at (strikes between the legs use a linear IV interpolation); the slope term
of the smile is ignored, so a digital priced this way is an approximation. Touch uses
the driftless reflection rule, 2 x P(finish beyond the short strike), capped at 1.

Pure functions; no I/O. Missing inputs give status "unknown" with reasons, never a
default value.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

SCHEMA = "astra_market_baseline@1.0.0"
YEAR_MS = 365.0 * 24 * 3_600_000
BASIS_CN = ("风险中性、市场隐含（Black-76，r=0，按各行权价自身的 mark IV），不是真实世界概率；"
            "触界按无漂移反射原理取 2×到期实值概率")
# Provisional compensation tiers on net credit after fees / exchange mark spread. Live
# samples on 2026-10-15..19 sat at 0.78-0.80 (selling at the bid, paying taker fees), so
# "fair" is centred there; recalibrate to sample tertiles (docs/opus/06 §3). The raw
# ratio is always reported.
COMPENSATION_RICH_AT = 0.95
COMPENSATION_THIN_BELOW = 0.70


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def prob_below(forward: float, strike: float, iv: float, years: float) -> float:
    """P(S_T < strike) under Black-76 with r=0; iv as a fraction."""
    v = iv * math.sqrt(years)
    d2 = (math.log(forward / strike) - 0.5 * v * v) / v
    return _norm_cdf(-d2)


def _iv_at(strike: float, short_strike: float, short_iv: float, long_strike: float, long_iv: float) -> float:
    low, high = sorted(((short_strike, short_iv), (long_strike, long_iv)))
    if strike <= low[0]:
        return low[1]
    if strike >= high[0]:
        return high[1]
    weight = (strike - low[0]) / (high[0] - low[0])
    return low[1] + weight * (high[1] - low[1])


def baseline(*, side: str, short_strike: float, long_strike: float, forward_usd: float,
             valuation_ms: int, expiry_ms: int, short_iv_pct: float | None, long_iv_pct: float | None,
             short_delta: float | None = None, long_delta: float | None = None,
             short_mark_btc: float | None = None, long_mark_btc: float | None = None,
             net_credit_btc: float | None = None, width_usd: float | None = None) -> dict[str, Any]:
    reasons: list[str] = []
    if side not in {"put", "call"}:
        raise ValueError("side must be put or call")
    ks, kl, fwd = _num(short_strike), _num(long_strike), _num(forward_usd)
    if ks is None or kl is None or ks <= 0 or kl <= 0 or ks == kl:
        raise ValueError("two different positive strikes are required")
    if (side == "put" and ks <= kl) or (side == "call" and ks >= kl):
        raise ValueError("strike order does not match a credit spread")
    if fwd is None or fwd <= 0:
        reasons.append("forward_missing")
    years = (int(expiry_ms) - int(valuation_ms)) / YEAR_MS
    if years <= 0:
        reasons.append("expired")
    ivs = _num(short_iv_pct)
    ivl = _num(long_iv_pct)
    if ivs is None or ivs <= 0:
        reasons.append("short_iv_missing")
    if ivl is None or ivl <= 0:
        reasons.append("long_iv_missing")
    out: dict[str, Any] = {
        "schema": SCHEMA, "basis_cn": BASIS_CN, "side": side,
        "inputs": {"short_strike_usd": ks, "long_strike_usd": kl, "forward_usd": fwd,
                   "years_to_expiry": years if years > 0 else None,
                   "hours_to_expiry": years * 8760.0 if years > 0 else None,
                   "short_iv_pct": ivs, "long_iv_pct": ivl},
        "delta_reference": {"short_abs_delta": abs(_num(short_delta)) if _num(short_delta) is not None else None,
                            "long_abs_delta": abs(_num(long_delta)) if _num(long_delta) is not None else None},
    }
    out["compensation"] = _compensation(short_mark_btc, long_mark_btc, net_credit_btc)
    if reasons:
        out.update({"status": "unknown", "reasons": reasons, "probabilities": None})
        return out
    s_iv, l_iv = ivs / 100.0, ivl / 100.0

    def beyond(strike: float, iv: float) -> float:
        below = prob_below(fwd, strike, iv, years)
        return below if side == "put" else 1.0 - below

    p_itm = beyond(ks, s_iv)
    probs: dict[str, Any] = {
        "p_itm_short": p_itm,
        "p_touch_short": min(1.0, 2.0 * p_itm),
        "p_max_loss": beyond(kl, l_iv),
        "p_loss": None,
        "loss_boundaries_usd": None,
    }
    credit = _num(net_credit_btc)
    width = _num(width_usd) if width_usd is not None else abs(ks - kl)
    if credit is not None and credit > 0 and width:
        # Inverse (BTC-settled) payout: credit - payout(S) < 0 defines the loss region.
        # Put: payout grows as S falls, so loss is S < breakeven. Call: beyond the long
        # strike the payout is width/S and shrinks, so loss ends at S = width/credit.
        if side == "put":
            breakeven = ks / (1.0 + credit)  # (ks - S)/S = credit, between the strikes
            if breakeven <= kl:  # no loss down to kl; below it payout = width/S
                breakeven = width / credit
            probs["p_loss"] = beyond(breakeven, _iv_at(breakeven, ks, s_iv, kl, l_iv))
            probs["loss_boundaries_usd"] = [breakeven]
        else:
            breakeven = ks / (1.0 - credit) if credit < 1 else math.inf  # (S - ks)/S = credit
            upper = width / credit
            if breakeven < kl and upper > breakeven:
                p_low = beyond(breakeven, _iv_at(breakeven, ks, s_iv, kl, l_iv))
                p_high = beyond(upper, _iv_at(upper, ks, s_iv, kl, l_iv))
                probs["p_loss"] = max(0.0, p_low - p_high)
                probs["loss_boundaries_usd"] = [breakeven, upper]
            else:  # the credit covers the whole width: no settlement price loses
                probs["p_loss"] = 0.0
                probs["loss_boundaries_usd"] = []
    else:
        out.setdefault("notes", []).append("net_credit_unknown_p_loss_not_computed")
    out.update({"status": "ok", "reasons": [], "probabilities": probs})
    return out


def _compensation(short_mark: Any, long_mark: Any, net_credit: Any) -> dict[str, Any]:
    sm, lm, credit = _num(short_mark), _num(long_mark), _num(net_credit)
    if sm is None or lm is None or credit is None:
        return {"status": "unknown", "tier": None, "mark_spread_btc": None, "net_credit_to_mark": None}
    spread = sm - lm
    if spread <= 0:
        return {"status": "unknown", "tier": None, "mark_spread_btc": spread, "net_credit_to_mark": None}
    ratio = credit / spread
    tier = "rich" if ratio >= COMPENSATION_RICH_AT else "thin" if ratio < COMPENSATION_THIN_BELOW else "fair"
    return {"status": "ok", "tier": tier, "tier_provisional": True, "mark_spread_btc": spread,
            "net_credit_btc": credit, "net_credit_to_mark": ratio,
            "basis_cn": "扣费后净信用 ÷ 交易所两腿 mark 价差；档位阈值为草案，待样本校准"}


def from_capture(snapshot: Mapping[str, Any], raw_market: Mapping[str, Any]) -> dict[str, Any]:
    """Baseline from a frozen snapshot plus its as-of packet raw_payload.market.

    snapshot.market has the books stripped; greeks, mark_iv and the expiry forward
    are read from raw_payload.market.{short,long}_book.
    """
    contract = snapshot.get("contract") or {}
    economics = snapshot.get("economics") or {}
    short_book = raw_market.get("short_book") or {}
    long_book = raw_market.get("long_book") or {}
    forward = _num(short_book.get("underlying_price")) or _num(long_book.get("underlying_price"))
    valuation = raw_market.get("valuation_ts_ms") or (snapshot.get("market") or {}).get("valuation_ts_ms")
    return baseline(
        side=str(contract.get("side")),
        short_strike=contract.get("short_strike_usd"), long_strike=contract.get("long_strike_usd"),
        forward_usd=forward, valuation_ms=int(valuation), expiry_ms=int(contract.get("expiry_ms")),
        short_iv_pct=short_book.get("mark_iv"), long_iv_pct=long_book.get("mark_iv"),
        short_delta=(short_book.get("greeks") or {}).get("delta"),
        long_delta=(long_book.get("greeks") or {}).get("delta"),
        short_mark_btc=short_book.get("mark_price"), long_mark_btc=long_book.get("mark_price"),
        net_credit_btc=economics.get("net_credit_after_fee_btc"), width_usd=contract.get("width_usd"),
    )
