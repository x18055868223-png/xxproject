"""Candidate readings: one place for the second-order numbers of a candidate (docs/opus/23 §1.3, §2.2).

- expected_move: the contract's own expected move to expiry from ONE ATM IV (this expiry's
  Deribit mark ATM IV; the gexmonitor term structure row of the same expiry only when the
  Deribit row is stale or missing), with the short leg's and the breakeven's distance in
  expected moves. The card, the map and the busy-window note read this one number
  (docs/opus/23 §1.3: four values of the same concept were on the page).
- pricing: net credit against the market's own price of the spread (the two legs' marks),
  split into the bid/ask cost and the fee (recoverable by execution or not), the skew cost of
  the protection leg (structural), and the worst case per unit of net credit (docs/opus/23 ①④).
- pricing also restates the market-estimate test as a price (unsuitable.reservation): the reservation
  price (mark spread + fee) against the credit at the bid and, with both legs' books, at the mid.
- clock_price: the ATM IV per unit of remaining intraday clock, against the same expiry at its
  cycle start and the clock-adjusted realized vol (docs/opus/23 ②). Mechanism B, the reading
  itself is untested: description only.

Pure functions, display only: nothing here weighs, decides or vetoes. Every value is None
when its inputs are missing; build never raises (an error returns {"schema", "error_type"}).
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

if __package__:
    from . import astra_decision_path_v1 as decision_path
    from . import astra_entry_warnings_v1 as entry_warnings
    from . import astra_session_clock_v1 as session_clock
    from . import astra_unsuitable_v1 as unsuitable
else:  # script-style import
    import astra_decision_path_v1 as decision_path
    import astra_entry_warnings_v1 as entry_warnings
    import astra_session_clock_v1 as session_clock
    import astra_unsuitable_v1 as unsuitable

SCHEMA = "candidate_readings@0.1"
HOUR_MS = 3_600_000
YEAR_HOURS = 8760.0
CYCLE_MS = 24 * HOUR_MS
REF_WINDOW_MS = 60 * 60_000          # rows within the first hour of the expiry's 24-hour cycle
NARROW_MOVES = unsuitable.NARROW_MOVES
# D2 of the Decision Path writes this bound as a literal ("标准附近" below it, "较远" from it on);
# mirrored here, keep the two equal.
STANDARD_MOVES_MAX = 0.55
WALLS_MAX_AGE_MS = decision_path.WALLS_MAX_AGE_MS
IV_SOURCE_CN = {"deribit_mark_own_expiry": "本期 Deribit 标记 ATM IV",
                "gexmonitor_term": "期限结构 ATM IV（gexmonitor）",
                "gexmonitor_term_nearest": "期限结构 ATM IV（gexmonitor，最近期限，非本期）",
                "gexmonitor_atm": "gexmonitor ATM IV（非本期）"}
BAND_CN = {"inside": "已进入", "narrow": "窄", "standard": "标准附近", "wide": "较远"}
# the same wording as the Decision Path's D1 "最坏形状"
TAIL_CN = {"put": "跌破保护腿后按 BTC 计赔付仍增加（币本位）",
           "call": "涨过保护腿后按 USD 计封顶，按 BTC 计随价格上涨递减"}
CLOCK_BASIS_CN = "机制 B（17 号 §3.1 附录 A：报价跟不上风险释放）；当期读数未检验，只作描述"
CLOCK_MEANING_CN = "按剩余时段的平均波动强度折算后的隐含波动：数值越高，剩下每单位时钟风险收的保费越多"
# the reservation fields pricing carries (unsuitable.reservation)
RESERVATION_KEYS = ("reservation_btc", "reservation_usd", "bid_gap_btc", "bid_gap_usd", "mid_credit_btc", "mid_credit_usd",
                    "mid_gap_btc", "mid_gap_usd", "long_bid_zero", "long_spread_to_mid", "long_book_wide",
                    "reservation_reliable")
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _get(obj: Any, path: str) -> Any:
    for key in path.split("."):
        if not isinstance(obj, Mapping):
            return None
        obj = obj.get(key)
    return obj


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _token(contract: Mapping[str, Any]) -> str | None:
    """Deribit expiry code of the contract (e.g. 27OCT26): from the instrument, else from the expiry."""
    parts = str(contract.get("short_instrument") or "").split("-")
    if len(parts) > 1 and parts[1]:
        return parts[1].upper()
    expiry = contract.get("expiry_ms")
    if isinstance(expiry, int) and not isinstance(expiry, bool):
        import datetime as dt
        d = dt.datetime.fromtimestamp(expiry / 1000, dt.timezone.utc)
        return f"{d.day}{_MONTHS[d.month - 1]}{d.year % 100:02d}"
    return None


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black76(side: str, forward: float, strike: float, years: float, iv_pct: float) -> float | None:
    """Undiscounted Black-76 value in USD per 1 BTC of underlying (no rates)."""
    if forward <= 0 or strike <= 0 or years <= 0 or iv_pct <= 0:
        return None
    sd = iv_pct / 100.0 * math.sqrt(years)
    d1 = (math.log(forward / strike) + sd * sd / 2) / sd
    d2 = d1 - sd
    if side == "call":
        return forward * _ncdf(d1) - strike * _ncdf(d2)
    return strike * _ncdf(-d2) - forward * _ncdf(-d1)


# ---------------------------------------------------------------- expected move

def own_iv(contract: Mapping[str, Any], by_expiry: Mapping[str, Any] | None,
           gex_info: Mapping[str, Any] | None, now_ms: int | None = None) -> tuple[float | None, str | None, Mapping[str, Any]]:
    """(ATM IV %, source, the contract's own by_expiry row or {})."""
    token, expiry = _token(contract), contract.get("expiry_ms")
    rows = by_expiry if isinstance(by_expiry, Mapping) else {}
    row = next((r for r in rows.values() if isinstance(r, Mapping) and isinstance(expiry, int)
                and r.get("expiry_ms") == expiry), None)
    if row is None and token:
        row = next((r for k, r in rows.items() if isinstance(r, Mapping) and str(k).upper() == token), None)
    row = row or {}
    age = _num(row.get("age_ms"))
    # the Decision Path's rule exactly (decision_path.resolve_iv): this expiry's Deribit row while it
    # is younger than WALLS_MAX_AGE_MS (the profile's own 20-minute `stale` flag is not used), then the
    # gexmonitor term structure; so the card, the busy-window note and D2 read one IV
    walls = {} if age is not None and age > WALLS_MAX_AGE_MS else row
    expiry_ms = contract.get("expiry_ms")
    hours = (expiry_ms - now_ms) / HOUR_MS if isinstance(expiry_ms, int) and isinstance(now_ms, int) else None
    iv, source = decision_path.resolve_iv(walls, gex_info or {}, contract, hours)
    return iv, source, row


def _band(dist: float | None, ratio: float | None) -> str | None:
    if dist is None:
        return None
    if dist < 0:
        return "inside"
    if ratio is None:
        return None
    if ratio < NARROW_MOVES:
        return "narrow"
    return "standard" if ratio < STANDARD_MOVES_MAX else "wide"


def expected_move(item: Mapping[str, Any], *, now_ms: int, by_expiry: Mapping[str, Any] | None,
                  gex_info: Mapping[str, Any] | None) -> dict[str, Any]:
    snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), Mapping) else {}
    contract = snapshot.get("contract") if isinstance(snapshot.get("contract"), Mapping) else {}
    side, expiry = contract.get("side"), contract.get("expiry_ms")
    iv, source, row = own_iv(contract, by_expiry, gex_info, now_ms)
    hours = (expiry - now_ms) / HOUR_MS if isinstance(expiry, int) and not isinstance(expiry, bool) else None
    spot = _num(_get(snapshot, "market.reference_price_usd")) or _num(row.get("forward_usd"))
    move_pct = iv * math.sqrt(hours / YEAR_HOURS) if iv is not None and hours is not None and hours > 0 else None
    move_usd = spot * move_pct / 100 if spot is not None and move_pct is not None else None
    adverse = -1.0 if side == "put" else 1.0 if side == "call" else None
    short = _num(contract.get("short_strike_usd"))
    # positive: outside the money (the same sign as decision_path._geometry)
    dist_usd = (short - spot) * adverse if adverse is not None and short is not None and spot else None
    dist_pct = dist_usd / spot * 100 if dist_usd is not None else None
    ratio = dist_pct / move_pct if dist_pct is not None and move_pct else None
    roots = [r for r in (_get(snapshot, "liability.breakeven_usd") or []) if _num(r) is not None] \
        if isinstance(_get(snapshot, "liability.breakeven_usd"), list) else []
    be = _num(roots[0]) if roots else None
    be_pct = (be - spot) * adverse / spot * 100 if be is not None and adverse is not None and spot else None
    band = _band(dist_pct, ratio)
    return {"iv_pct": iv, "iv_source": source, "iv_source_cn": IV_SOURCE_CN.get(source),
            "hours": hours, "spot_usd": spot, "move_pct": move_pct, "move_usd": move_usd,
            "short_strike_usd": short, "short_distance_pct": dist_pct, "short_distance_usd": dist_usd,
            "distance_in_moves": ratio, "band": band, "band_cn": BAND_CN.get(band),
            "breakeven_usd": be, "breakeven_distance_pct": be_pct,
            "breakeven_in_moves": be_pct / move_pct if be_pct is not None and move_pct else None}


# ---------------------------------------------------------------- pricing

def _fee_basis_cn(fee: Mapping[str, Any]) -> str:
    basis, assumption = fee.get("basis"), fee.get("assumption_basis")
    if basis == "actual" and not assumption:
        return "实际费用"
    if assumption == "public_taker_capped_daily_delivery_zero":
        return "按公开 taker 费率估算（含入场两腿；日期权交割费为 0），非账户实际费用"
    if basis == "configured_assumption" or assumption:
        return f"配置假设（{assumption or basis}），非账户实际费用"
    return "费用未知"


def pricing(item: Mapping[str, Any], *, move: Mapping[str, Any], by_expiry_row: Mapping[str, Any],
            books: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """`books`: {"short": {"bid", "ask"}, "long": {"bid", "ask"}} in BTC (the capture's top of book);
    without them the mid fields of the reservation price are None."""
    snapshot = item.get("snapshot") if isinstance(item.get("snapshot"), Mapping) else {}
    contract = snapshot.get("contract") if isinstance(snapshot.get("contract"), Mapping) else {}
    eco = snapshot.get("economics") if isinstance(snapshot.get("economics"), Mapping) else {}
    fee = snapshot.get("fee") if isinstance(snapshot.get("fee"), Mapping) else {}
    marks = item.get("market_marks") if isinstance(item.get("market_marks"), Mapping) else {}
    side = contract.get("side")
    spot = move.get("spot_usd")
    usd = (lambda v: v * spot if v is not None and spot else None)
    qty = _num(contract.get("quantity_btc")) or 1.0
    credit = _num(eco.get("visible_credit_btc"))
    net = _num(eco.get("net_credit_after_fee_btc"))
    # the fee is what the net credit deducted (all hold fees; equals snapshot.fee.amount_btc)
    # never the entry fee alone: a snapshot with an incomplete fee says the total is unknown
    fee_btc = credit - net if credit is not None and net is not None else _num(fee.get("amount_btc"))
    if net is None and credit is not None and fee_btc is not None:
        net = credit - fee_btc
    net_usd = _num(eco.get("net_credit_after_fee_usd_at_spot"))
    if net_usd is None:
        net_usd = usd(net)
    short_mark, long_mark = _num(marks.get("short_mark_btc")), _num(marks.get("long_mark_btc"))
    # the market's own price of the spread (the same definition as decision_reduction R1)
    mark_spread = (short_mark - long_mark) * qty if short_mark is not None and long_mark is not None else None
    mark_spread_usd = usd(mark_spread)
    edge_btc = net - mark_spread if net is not None and mark_spread is not None else None
    spread_cost_btc = credit - mark_spread if credit is not None and mark_spread is not None else None
    short_iv, long_iv = _num(marks.get("short_mark_iv")), _num(marks.get("long_mark_iv"))
    skew = long_iv - short_iv if long_iv is not None and short_iv is not None else None
    hours, long_k = move.get("hours"), _num(contract.get("long_strike_usd"))
    # the protection leg priced at its own mark IV minus at the short leg's IV, both on the snapshot's
    # spot (one capture), so the line does not move with a forward read at another moment
    skew_cost_usd = None
    if long_iv is not None and short_iv is not None and long_k and spot and hours is not None and hours > 0 \
            and side in {"put", "call"}:
        rich = black76(side, spot, long_k, hours / YEAR_HOURS, long_iv)
        flat = black76(side, spot, long_k, hours / YEAR_HOURS, short_iv)
        if rich is not None and flat is not None:
            skew_cost_usd = (rich - flat) * qty
    width = _num(contract.get("width_usd"))
    if width is None and _num(contract.get("short_strike_usd")) is not None and long_k is not None:
        width = abs(_num(contract.get("short_strike_usd")) - long_k)
    worst = next((q.get("numbers") for q in _get(item, "decision_reduction.questions") or []
                  if isinstance(q, Mapping) and q.get("key") == "worst_case"), None) or {}
    loss = _num(worst.get("loss_at_protection_usd"))
    if loss is None and width is not None and net_usd is not None:
        loss = width * qty - net_usd
    assumed = fee.get("basis") == "configured_assumption" or bool(fee.get("assumption_basis"))
    books = books if isinstance(books, Mapping) else {}
    reserve = unsuitable.reservation(mark_spread, fee_btc, credit, books.get("short"), books.get("long"), qty, spot)
    return {"credit_btc": credit, "credit_usd": usd(credit), "fee_btc": fee_btc, "fee_usd": usd(fee_btc),
            "fee_assumed": assumed, "fee_basis_cn": _fee_basis_cn(fee),
            "net_credit_btc": net, "net_credit_usd": net_usd,
            "mark_spread_btc": mark_spread, "mark_spread_usd": mark_spread_usd,
            "edge_usd": usd(edge_btc), "spread_cost_usd": usd(spread_cost_btc),
            "fee_cost_usd": 0.0 - usd(fee_btc) if usd(fee_btc) is not None else None,
            "short_mark_iv": short_iv, "long_mark_iv": long_iv, "skew_vol_pts": skew,
            "skew_cost_usd": skew_cost_usd,
            "width_usd": width, "loss_at_protection_usd": loss,
            "worst_to_net_ratio": loss / net_usd if loss is not None and net_usd is not None and net_usd > 0 else None,
            "tail_cn": TAIL_CN.get(side), **{k: reserve[k] for k in RESERVATION_KEYS}}


# ---------------------------------------------------------------- clock price

def clock_price(item: Mapping[str, Any], *, now_ms: int, iv: float | None,
                realized_vol: Mapping[str, Any] | None, history: Iterable[Mapping[str, Any]] | None,
                iv_source: str | None = "deribit_mark_own_expiry") -> dict[str, Any]:
    expiry = _get(item, "snapshot.contract.expiry_ms")
    expiry = expiry if isinstance(expiry, int) and not isinstance(expiry, bool) else None
    adjusted = entry_warnings.clock_adjusted(iv, now_ms, expiry) if expiry is not None else None
    ref = ref_at = None
    samples = 0
    # the archive records this expiry's Deribit ATM IV: compare only like with like
    if expiry is not None and iv_source == "deribit_mark_own_expiry":
        start = expiry - CYCLE_MS
        used = []
        for r in history or []:
            at = r.get("at_ms") if isinstance(r, Mapping) else None
            if not isinstance(at, int) or r.get("expiry_ms") != expiry or not start <= at <= start + REF_WINDOW_MS:
                continue
            value = entry_warnings.clock_adjusted(_num(r.get("expiry_atm_iv_pct")), at, expiry)
            if value is not None:
                used.append((at, value))
        if used:
            ref, ref_at, samples = _median([v for _, v in used]), min(t for t, _ in used), len(used)
    rv = realized_vol if isinstance(realized_vol, Mapping) else {}
    rv24 = _num(rv.get("rv_24h_pct"))
    end = rv.get("last_bar_end_ms") if isinstance(rv.get("last_bar_end_ms"), int) else now_ms
    rv_adj = None
    if rv24 is not None:
        factor = session_clock.variance_between(end - CYCLE_MS, end) / 24.0
        rv_adj = rv24 / math.sqrt(factor) if factor > 0 else None
    return {"iv_clock_adjusted": adjusted, "ref_cycle_start": ref, "ref_at_ms": ref_at, "ref_samples": samples,
            "ref_source": "deribit_mark_own_expiry",   # the archive ledger records this expiry's Deribit ATM IV
            "rv24_pct": rv24, "rv24_clock_adjusted": rv_adj,
            "basis_cn": CLOCK_BASIS_CN, "meaning_cn": CLOCK_MEANING_CN}


# ---------------------------------------------------------------- the readings

def build(item: Mapping[str, Any], *, now_ms: int, by_expiry: Mapping[str, Any] | None,
          gex_info: Mapping[str, Any] | None, realized_vol: Mapping[str, Any] | None,
          history: Iterable[Mapping[str, Any]] | None, books: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        move = expected_move(item, now_ms=now_ms, by_expiry=by_expiry, gex_info=gex_info)
        contract = _get(item, "snapshot.contract") or {}
        _iv, _source, row = own_iv(contract, by_expiry, gex_info, now_ms)
        return {"schema": SCHEMA, "at_ms": now_ms, "expected_move": move,
                "pricing": pricing(item, move=move, by_expiry_row=row, books=books),
                "clock_price": clock_price(item, now_ms=now_ms, iv=move["iv_pct"], realized_vol=realized_vol,
                                           history=history, iv_source=move["iv_source"])}
    except Exception as exc:  # a display layer never breaks the view
        return {"schema": SCHEMA, "at_ms": now_ms, "error_type": type(exc).__name__}
