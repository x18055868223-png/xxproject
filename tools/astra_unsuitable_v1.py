"""Unsuitable situations: why a daily vertical is not suitable now (unsuitable@0.1, 2026-10-27).

One place that says, for the page card and the Decision Path's veto step (V), which known
situations make the trade unsuitable and what would clear them. Nothing here is a reason to
trade: "no item" only means none of these checks fired. No new factors, no probabilities, no
direction readings. Every item carries its numbers, an honest "why" and its grade:

- veto: fired entry warnings (term inversion, premium thickening; thresholds unvalidated,
  veto only), a buffer inside or narrow against this expiry's expected move, incomplete data.
- caution: even the two legs' mid does not reach the market's own estimate (the reservation
  price below), or the protection leg's book is empty or wide (its mark may be unreliable).

The reservation price restates the existing "net credit >= the two legs' mark spread" test as
a price: the lowest two-leg gross credit whose net (after fees) is not below the market's mark
estimate, i.e. mark spread + fee.

Pure functions; imports nothing from the Decision Path or the candidate readings (they import this).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

SCHEMA = "unsuitable@0.1"
# a standard 0.35 delta short leg sits near 0.4 expected moves (docs/opus/17 §3.2); "narrow" is
# clearly closer than that (unvalidated, veto only). The Decision Path and the readings use this.
NARROW_MOVES = 0.25
STANDARD_MOVES = 0.4
STANDARD_WIDTH_USD = 2000.0
# a protection leg whose bid/ask spread reaches this share of its mid: its mark may be unreliable
WIDE_BOOK = 0.5
LEGACY_BUSY_CODE = "busy_window_thin_cushion"   # @0.1 busy item: a holding note, never a veto here

B_WARNING = "未验证，只作否决 · 13 号 / 17 号 §3.2（阈值为 2026-10-20 初值）"
B_BUFFER = "距离 · 本期 ATM IV 折算；标准 0.35Δ 短腿约 0.4 倍（17 号 §3.2）；窄（< 0.25 倍）只作否决，阈值未验证"
B_DATA = "口径 · 读数前提（不是研究结论）"
B_RESERVATION = ("定义 · 保留价 = 两腿标记价差 + 费用（净收不低于市场估价的最低两腿毛信用）；把标记价当公允价是假设；"
                 "费用每腿封顶 min(0.0003, 12.5% × 腿价)，随成交价略有变化")
B_MID = "B/C · 17 号 §6（成本差 B；中间价能否成交 C，未验证）"
B_LONG_BOOK = "推论（无等级）· 17 号 §6"


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


def _f(value: float | None, digits: int = 1, unit: str = "") -> str:
    return "—" if value is None else f"{value:.{digits}f}{unit}"


def _pts(value: float | None) -> str:
    return "无数据" if value is None else f"{value:+.1f} 点"


def _usd(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def item(code: str, kind: str, title_cn: str, evidence_cn: str, why_cn: str, clears_cn: str,
         basis: str) -> dict[str, Any]:
    return {"code": code, "kind": kind, "title_cn": title_cn, "evidence_cn": evidence_cn, "why_cn": why_cn,
            "clears_cn": clears_cn, "basis": basis}


# ---------------------------------------------------------------- entry warnings

def warning_evidence(w: Mapping[str, Any]) -> str:
    """The numbers of one entry warning (the page's warningEvidence)."""
    e = w.get("evidence") if isinstance(w.get("evidence"), Mapping) else {}
    if w.get("code") == "term_inversion":
        return (f"时钟校正后：本期 {e.get('contract_expiry') or '—'} {_f(_num(e.get('contract_atm_iv_clock_adjusted')), 1, '%')}，"
                f"一周 {e.get('week_expiry') or '—'} {_f(_num(e.get('week_atm_iv_clock_adjusted')), 1, '%')}，"
                f"差 {_f(_num(e.get('gap_vol_pts')), 1)} 点")
    if w.get("code") == "premium_thickening":
        other = _num(e.get("other_side_skew_jump_vol_pts"))
        return (f"本期 IV（时钟校正）较近 6 小时 {_pts(_num(e.get('atm_jump_vol_pts')))}，"
                f"本侧偏斜 {_pts(_num(e.get('skew_jump_vol_pts')))}"
                + (f"，另一侧偏斜 {_pts(other)}" if other is not None else "，另一侧无数据"))
    return ""


_WHY = {
    "term_inversion": ("按日内时钟校正后，市场给近端的报价仍高于一周期限。一种解释是近端风险已被定价，"
                       "或有人在急买近端保护（未检验）",
                       "倒挂消失（时钟校正后本期不再高于一周期限）后，这一项才解除"),
    "premium_thickening": ("保费突然变厚，首先是有人在买保护的警告，不是多收保费的机会",
                           "保费回到近 6 小时的水平后，这一项才解除"),
}


def warning_items(block: Any) -> dict[str, Any]:
    """{"items": vetoes per fired warning, "checks": every warning with its status,
    "unknown_cn": warnings that could not be judged}. Holding notes are not read here."""
    if not isinstance(block, Mapping) or block.get("error_type"):
        return {"items": [], "checks": [], "unknown_cn": ["不建仓警示不可用"]}
    warnings = [w for w in block.get("warnings") or [] if isinstance(w, Mapping) and w.get("code") != LEGACY_BUSY_CODE]
    items, checks, unknown = [], [], []
    for w in warnings:
        label = str(w.get("label_cn") or w.get("code") or "警示")
        fired = w.get("triggered")
        checks.append({"code": w.get("code"), "label_cn": label,
                       "status_cn": "触发" if fired is True else "未触发" if fired is False else "无法判断"})
        if fired is None:
            unknown.append(f"{label}（数据不足）")
        if fired is not True:
            continue
        why, clears = _WHY.get(w.get("code"), ("这项警示已触发，先当警告", "警示不再触发后，这一项才解除"))
        if w.get("code") == "premium_thickening" and _get(w, "evidence.one_sided"):
            why += "；只有本侧变厚、另一侧没动，一种解释是有人在专门买这一侧的保护（未检验）"
        items.append(item(str(w.get("code") or "warning"), "veto", label, warning_evidence(w) or str(w.get("reason_cn") or ""),
                          why, clears, B_WARNING))
    if not warnings:
        unknown.append("不建仓警示为空")
    return {"items": items, "checks": checks, "unknown_cn": unknown}


# ---------------------------------------------------------------- buffer and data

def buffer_item(dist_pct: float | None, ratio: float | None, move_pct: float | None, side: str | None) -> dict[str, Any] | None:
    """A veto when the short leg is already crossed or narrow against the expected move."""
    dist, ratio, move = _num(dist_pct), _num(ratio), _num(move_pct)
    if dist is None:
        return None
    clears = f"改为更远的短腿（约 {STANDARD_MOVES} 倍预期波动），或等价格离开后，这一项才解除"
    if dist < 0:
        inward = "上方" if side == "put" else "下方"
        return item("inside", "veto", "现价已越过短腿", f"短腿在现价{inward} {abs(dist):.2f}%",
                    "按现价到期就要赔：收的是已经发生的风险，不是要承保的风险", clears, B_BUFFER)
    if ratio is None or ratio >= NARROW_MOVES:
        return None
    return item("narrow", "veto", "缓冲窄",
                f"短腿距现价 {dist:.2f}%，约 {ratio:.2f} 倍到期前预期波动"
                + (f"（±{move:.2f}%）" if move is not None else "") + f"；标准 0.35Δ 短腿约 {STANDARD_MOVES} 倍",
                f"不到 {NARROW_MOVES} 倍预期波动的正常波动就会触及短腿；这个阈值未验证，只作否决", clears, B_BUFFER)


def data_item(gap_labels_cn: Sequence[str]) -> dict[str, Any] | None:
    labels = [str(x) for x in gap_labels_cn or [] if x]
    if not labels:
        return None
    return item("data", "veto", "资料不完整", "缺口：" + "、".join(labels),
                "资料有缺口时，距离、价格与警示可能建立在旧的或不全的数据上，其余读数不可靠",
                "数据恢复后刷新到现在，这一项才解除", B_DATA)


# ---------------------------------------------------------------- reservation price

def _book(book: Any) -> tuple[float, float] | None:
    if not isinstance(book, Mapping):
        return None
    bid, ask = _num(book.get("bid")), _num(book.get("ask"))
    if bid is None or ask is None or ask <= 0 or not 0 <= bid <= ask:
        return None
    return bid, ask


def reservation(mark_spread_btc: Any, fee_btc: Any, visible_credit_btc: Any, short_book: Any = None,
                long_book: Any = None, qty: Any = 1.0, spot: Any = None) -> dict[str, Any]:
    """The reservation price (mark spread + fee) against the credit at the bid and at the two legs' mid.
    Books are {"bid", "ask"} in BTC per contract; every value is None when its inputs are missing."""
    mark, fee, visible = _num(mark_spread_btc), _num(fee_btc), _num(visible_credit_btc)
    qty = _num(qty) or 1.0
    spot = _num(spot)
    usd = (lambda v: v * spot if v is not None and spot else None)
    res = mark + fee if mark is not None and fee is not None else None
    bid_gap = res - visible if res is not None and visible is not None else None
    sb, lb = _book(short_book), _book(long_book)
    mid = ((sb[0] + sb[1]) / 2 - (lb[0] + lb[1]) / 2) * qty if sb and lb else None
    mid_gap = res - mid if res is not None and mid is not None else None
    long_bid_zero = bool(lb and lb[0] == 0)
    long_ratio = (lb[1] - lb[0]) / ((lb[1] + lb[0]) / 2) if lb else None
    long_wide = long_ratio is not None and long_ratio >= WIDE_BOOK
    return {"reservation_btc": res, "reservation_usd": usd(res), "mark_spread_btc": mark, "fee_btc": fee,
            "visible_credit_btc": visible, "visible_credit_usd": usd(visible),
            "bid_gap_btc": bid_gap, "bid_gap_usd": usd(bid_gap),
            "mid_credit_btc": mid, "mid_credit_usd": usd(mid), "mid_gap_btc": mid_gap, "mid_gap_usd": usd(mid_gap),
            "long_bid_zero": long_bid_zero, "long_spread_to_mid": long_ratio, "long_book_wide": long_wide,
            "reservation_reliable": res is not None and not (long_bid_zero or long_wide)}


def _amount(btc: float | None, usd: float | None) -> str:
    return f"约 {_usd(abs(usd))} 美元" if usd is not None else f"{abs(btc):.4f} BTC" if btc is not None else "—"


def mid_item(res: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A caution when even a fill at the two legs' mid stays below the market's estimate."""
    gap = _num((res or {}).get("mid_gap_btc"))
    if gap is None or gap <= 0:
        return None
    return item("mid_short", "caution", "中间价也不够市场估价",
                f"即使按两腿中间价成交，净收仍比市场估价少{_amount(gap, _num(res.get('mid_gap_usd')))}"
                f"（保留价 {res['reservation_btc']:.4f} BTC，中间价信用 {res['mid_credit_btc']:.4f} BTC）",
                "只有比中间价更好的挂单才达到市场估价，在那里能否成交不确定",
                "盘口或标记价变化、中间价不低于保留价后，这一项才解除", B_MID)


def long_book_item(res: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A caution when the protection leg's book has no bid or a spread of at least half its mid."""
    res = res or {}
    ratio = _num(res.get("long_spread_to_mid"))
    if not (res.get("long_bid_zero") or res.get("long_book_wide")):
        return None
    evidence = ("保护腿买价为 0" if res.get("long_bid_zero")
                else f"保护腿点差为中间价的 {ratio:.0%}" if ratio is not None else "保护腿盘口过宽")
    return item("long_book", "caution", "保护腿盘口空或过宽", evidence,
                "保护腿报价稀薄时，它的标记价可能失真，市场估价与保留价随之不可靠；需要提前平仓时它也难卖出",
                f"保护腿有买价、点差小于中间价的 {WIDE_BOOK:.0%} 后，这一项才解除", B_LONG_BOOK)


# ---------------------------------------------------------------- page card

_QUOTE_GAP_CN = "报价缺口"
_DEPTH_CN = "盘口深度不足"


def card(candidate: Any) -> dict[str, Any]:
    """The unsuitable items of one page candidate: warnings, buffer, quote gaps and depth, then the
    cautions of its readings' reservation price. Never raises."""
    try:
        cand = candidate if isinstance(candidate, Mapping) else {}
        items, unknown = [], []
        snap = cand.get("snapshot") if isinstance(cand.get("snapshot"), Mapping) else {}
        quote = _get(snap, "market.quote") or {}
        gaps = []
        if not snap:
            gaps.append("没有冻结快照")
        if isinstance(quote, Mapping) and quote.get("gap_reasons"):
            gaps.append(_QUOTE_GAP_CN)
        depth = quote.get("available_depth_btc") if isinstance(quote, Mapping) else None
        need = _num(_get(snap, "contract.quantity_btc")) or 1.0
        if isinstance(depth, Mapping) and any((_num(depth.get(leg)) or 0) < need for leg in ("short", "long")):
            gaps.append(_DEPTH_CN)
        data = data_item(gaps)
        if data:
            items.append(data)
        warn = warning_items(cand.get("entry_warnings"))
        items += warn["items"]
        unknown += warn["unknown_cn"]
        move = _get(cand, "readings.expected_move")
        move = move if isinstance(move, Mapping) else {}
        side = _get(snap, "contract.side") or cand.get("side")
        buffer = buffer_item(move.get("short_distance_pct"), move.get("distance_in_moves"), move.get("move_pct"), side)
        if buffer:
            items.append(buffer)
        elif _num(move.get("short_distance_pct")) is None or _num(move.get("distance_in_moves")) is None:
            unknown.append("缓冲无法计算")
        pricing = _get(cand, "readings.pricing")
        if isinstance(pricing, Mapping) and "reservation_btc" in pricing:
            for caution in (mid_item(pricing), long_book_item(pricing)):
                if caution:
                    items.append(caution)
            if pricing.get("reservation_btc") is None:
                unknown.append("市场估价缺失，保留价无法计算")
        return {"schema": SCHEMA, "items": items, "veto_count": sum(i["kind"] == "veto" for i in items),
                "caution_count": sum(i["kind"] == "caution" for i in items), "unknown_cn": unknown}
    except Exception as exc:   # a display layer never breaks the view
        return {"schema": SCHEMA, "items": [], "veto_count": 0, "caution_count": 0, "unknown_cn": [], "error_type": type(exc).__name__}
