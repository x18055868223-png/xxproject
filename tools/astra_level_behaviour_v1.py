"""Level cards and expiry reviews (docs/opus/32 §2-§3, docs/opus/33 §3.1, docs/opus/34 §3): what
the index did at this expiry's open-interest levels before a reading, what it did after, and the
open-interest change and aggressor flow at them. Pure functions; level_rules@0.1.

Only observed facts. Dealer side and liquidation levels are unknown and say so on every card.
Gamma top three and the pin are derived readings (doc 31 §3.1: they mostly restate the spot). Flow
is aggressor buy / sell only: the aggressor is not the client and the passive side is not the
dealer.

- Levels: the hand's two legs; the call and put open-interest top three of this expiry; the gamma
  top three and the pin (derived); controls: for each leg or open-interest level, the nearest
  listed strike below and above that is neither a leg nor an open-interest level. Controls need the
  expiry's strike grid, which only the structure snapshots record (from 2026-11-11): a backfilled
  card has none and says so in data_gaps.
- role_class: leg > oi > derived > control; `roles` lists every role of the strike.
- Behaviour at K over a window (ported unchanged from docs/opus/research_30/hands_extract.py):
  crossings, closest approach, minutes within 100 USD, the first crossing, how far past K it went
  within 60 minutes, whether it came back, whether the window ended past K.
- A card uses data at or before its read_at_ms only: callers cut their inputs, build_card raises
  when handed a later point, snapshot or flow row.
- Distances: USD and multiples of the expected move at the reading (expected_move_usd of the
  expiry walls). A level's distance is signed (k - index, positive above); the short leg's is
  positive when out of the money.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Iterable, Mapping, Sequence

try:
    from . import astra_expiry_structure_v1 as expiry_structure
except ImportError:  # script-style import
    import astra_expiry_structure_v1 as expiry_structure

RULES = "level_rules@0.1"
CARD_SCHEMA = "level_card@0.1"
REVIEW_SCHEMA = "level_review@0.1"
H = 3_600_000
DAY = 24 * H
NEAR_USD = 100.0                  # minutes_within_100usd
APPROACH_SHARE = 0.10             # approach_no_cross: no crossing and closest <= 10% of the expected move
WINDOWS = {"6h": 6 * H, "24h": DAY}
PATH_EDGE_MS = 10 * 60_000        # a behaviour window needs index points this close to both ends
ROUNDS = ("r1", "r2", "late4h", "late1h")
ROLE_ORDER = ("leg", "oi", "derived", "control")
UNKNOWN = ["dealer_side", "liquidation_levels"]
KIND_ROLES = {"oi_call": "oi", "oi_put": "oi", "gamma_call": "derived", "gamma_put": "derived"}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def at(points: Sequence[Sequence[float]], ms: int) -> float | None:
    """Last index point at or before ms."""
    if not points or points[0][0] > ms:
        return None
    lo, hi = 0, len(points) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if points[mid][0] <= ms:
            lo = mid
        else:
            hi = mid - 1
    return points[lo][1]


def behaviour(points, k, start, end):
    """What the index did at level k in (start, end]: crossings, closest approach, the first crossing and the hour after it."""
    seg = [(t, p) for t, p in points if start < t <= end]
    if len(seg) < 2:
        return None
    crossings, first, prev = 0, None, seg[0][1] >= k
    for i, (t, p) in enumerate(seg):
        s = p >= k
        if s != prev:
            crossings += 1
            if first is None:
                first = i
            prev = s
    out = {"crossings": crossings, "closest_usd": round(min(abs(p - k) for _, p in seg), 1),
           "minutes_within_100usd": sum(1 for _, p in seg if abs(p - k) <= 100), "first_cross_ms": None}
    if first is not None:
        t0 = seg[first][0]
        after = [p for t, p in seg[first:] if t <= t0 + H]
        sign = 1 if seg[first][1] >= k else -1          # direction of the first crossing
        out.update(first_cross_ms=t0, first_cross_dir="up" if sign > 0 else "down",
                   beyond_60m_usd=round(max(sign * (p - k) for p in after), 1),
                   back_within_60m=any(sign * (p - k) < 0 for p in after),
                   end_beyond=bool(sign * (seg[-1][1] - k) > 0))
    return out


def covered(points: Sequence[Sequence[float]], start: int, end: int) -> bool:
    """Index points reach within PATH_EDGE_MS of both ends of (start, end]."""
    seg = [t for t, _ in points if start < t <= end]
    return bool(seg) and seg[0] - start <= PATH_EDGE_MS and end - seg[-1] <= PATH_EDGE_MS


def _strike(value: Any) -> float | None:
    return _num(value)


def levels_for(walls: Mapping[str, Any], contract: Mapping[str, Any] | None,
               strikes: Sequence[float] | None) -> dict[float, list[str]]:
    """strike -> roles: legs, open-interest and gamma top three of each kind, the pin, controls."""
    roles: dict[float, list[str]] = {}

    def add(k: Any, role: str) -> None:
        k = _strike(k)
        if k is not None and role not in roles.setdefault(k, []):
            roles[k].append(role)

    for kind in ("oi_call", "oi_put", "gamma_call", "gamma_put"):
        for rank, item in enumerate((walls.get("top") or {}).get(kind) or [], 1):
            if isinstance(item, Mapping):
                add(item.get("strike"), f"{kind}#{rank}")
    add((walls.get("walls") or {}).get("pin"), "pin")
    if contract:
        add(contract.get("short_strike_usd"), "short")
        add(contract.get("long_strike_usd"), "long")
    if strikes:
        for k in controls(sorted({float(s) for s in strikes}), roles):
            add(k, "control")
    return roles


def role_class(roles: Iterable[str]) -> str:
    classes = {"leg" if r in ("short", "long") else "derived" if r == "pin"
               else "control" if r in ("control", "spot_control") else KIND_ROLES[r.split("#")[0]] for r in roles}
    return next(c for c in ROLE_ORDER if c in classes)


def controls(grid: Sequence[float], roles: Mapping[float, list[str]]) -> list[float]:
    """For each leg or open-interest level, the nearest listed strike below and above that is
    neither a leg nor an open-interest level."""
    key = {k for k, r in roles.items() if role_class(r) in ("leg", "oi")}
    out = set()
    for k in key:
        below = [s for s in grid if s < k and s not in key]
        above = [s for s in grid if s > k and s not in key]
        if below:
            out.add(below[-1])
        if above:
            out.add(above[0])
    return sorted(out)


def _short_fields(contract: Mapping[str, Any], index: float | None, move: float | None,
                  walls: Mapping[str, Any]) -> dict[str, Any]:
    side = contract.get("side")
    ks = _num(contract.get("short_strike_usd"))
    top = walls.get("top") or {}
    oi = {kind: [_num(i.get("strike")) for i in top.get(kind) or [] if isinstance(i, Mapping)]
          for kind in ("oi_call", "oi_put")}
    out: dict[str, Any] = {"k": ks, "distance_usd": None, "distance_in_moves": None,
                           "is_oi_level": None, "oi_levels_between": None, "nearest_oi_between": None}
    if ks is None:
        return out
    same = oi["oi_put" if side == "put" else "oi_call"]
    out["is_oi_level"] = (ks in same) if same else None     # no open-interest top: unknown, not False
    if index is not None:
        out["distance_usd"] = round(index - ks if side == "put" else ks - index, 1)
        if move:
            out["distance_in_moves"] = round(out["distance_usd"] / move, 3)
        lo, hi = sorted((index, ks))
        between = sorted({k for k in oi["oi_call"] + oi["oi_put"] if k is not None and lo < k < hi},
                         key=lambda k: abs(k - index))
        if oi["oi_call"] or oi["oi_put"]:   # without any open-interest top, the count is unknown, not 0
            out["oi_levels_between"] = len(between)
            out["nearest_oi_between"] = between[0] if between else None
    return out


def _check_cut(read_at_ms: int, points: Sequence[Sequence[float]], snapshots: Sequence[Mapping[str, Any]],
               flow_rows: Sequence[Mapping[str, Any]]) -> None:
    later = (lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > read_at_ms)
    late = ([t for t, _ in points if later(t)][:1] + [s["at_ms"] for s in snapshots if later(s.get("at_ms"))][:1]
            + [r["to_ms"] for r in flow_rows if later(r.get("to_ms"))][:1])
    if late:
        raise ValueError(f"data after read_at_ms handed to a card: {late[0]} > {read_at_ms}")


def build_card(*, expiry_ms: int, rnd: str, side: str, read_at_ms: int, walls: Mapping[str, Any],
               points: Sequence[Sequence[float]], case: Mapping[str, Any] | None = None,
               contract: Mapping[str, Any] | None = None, strikes: Sequence[float] | None = None,
               snapshots: Sequence[Mapping[str, Any]] = (), flow_rows: Sequence[Mapping[str, Any]] = (),
               backfilled: bool = False, extra_gaps: Sequence[str] = ()) -> dict[str, Any]:
    """One level card. side is put / call for a round card, structure for a late look (no hand)."""
    if rnd not in ROUNDS:
        raise ValueError(rnd)
    _check_cut(read_at_ms, points, snapshots, flow_rows)
    gaps = list(extra_gaps)
    index = at(points, read_at_ms)
    move = _num(walls.get("expected_move_usd"))
    if index is None:
        gaps.append("index_at_read")
    if move is None:
        gaps.append("expected_move")
    if side != "structure" and contract is None:
        gaps.append("no_case")
    if not strikes:
        gaps.append("strike_grid")
    roles = levels_for(walls, contract, strikes)
    if side == "structure" and strikes and index is not None:
        # a late look's control for the pin: the listed strike nearest the spot that is not the pin
        pin = _num((walls.get("walls") or {}).get("pin"))
        near = [float(k) for k in strikes if _num(k) is not None and float(k) != pin]
        if near:
            k = min(near, key=lambda x: abs(x - index))
            roles.setdefault(k, []).append("spot_control")
    oi_live = bool(snapshots)
    if not oi_live:
        gaps.append("oi_change")
    if not flow_rows:
        gaps.append("flow")
    snap = expiry_structure._at_or_before(snapshots, read_at_ms) if snapshots else None
    if oi_live and snap is None:
        gaps.append("oi_at_read")
    levels = []
    for k in sorted(roles):
        before = behaviour(points, k, read_at_ms - DAY, read_at_ms) if covered(points, read_at_ms - DAY, read_at_ms) else None
        level: dict[str, Any] = {
            "k": k, "roles": roles[k], "role_class": role_class(roles[k]),
            "distance_usd": round(k - index, 1) if index is not None else None,
            "distance_in_moves": round((k - index) / move, 3) if index is not None and move else None,
            "before_24h": before,
            "oi": dict(zip(("c", "p"), expiry_structure._oi_at(snap, k))) if snap else None,
            "oi_change": {w: expiry_structure.oi_change(snapshots, k, read_at_ms, ms) for w, ms in WINDOWS.items()}
            if oi_live else None,
            "flow": {w: expiry_structure.flow_sum(flow_rows, k, read_at_ms - ms, read_at_ms) for w, ms in WINDOWS.items()}
            if flow_rows else None}
        levels.append(level)
    if any(l["before_24h"] is None for l in levels):
        gaps.append("path_before_24h")
    card = {"schema": CARD_SCHEMA, "rules": RULES, "expiry_ms": expiry_ms,
            "expiry_day": dt.datetime.fromtimestamp(expiry_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
            "round": rnd, "side": side, "read_at_ms": read_at_ms,
            "case_id": (case or {}).get("case_id"),
            "contract_key": f"{contract['short_instrument']}|{contract['long_instrument']}" if contract else None,
            "index_usd": index, "expected_move_usd": move,
            "hours_to_expiry": round((expiry_ms - read_at_ms) / H, 2),
            "short": _short_fields(contract, index, move, walls) if contract else None,
            "levels": levels, "unknown": list(UNKNOWN), "data_gaps": sorted(set(gaps)), "backfilled": backfilled}
    return card


def build_review(card: Mapping[str, Any], points: Sequence[Sequence[float]], *, delivery_price_usd: float | None,
                 payout_btc: float | None = None, snapshots: Sequence[Mapping[str, Any]] = (),
                 flow_rows: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """What happened after the card's reading, at the same levels, until expiry."""
    read, expiry = card["read_at_ms"], card["expiry_ms"]
    index, move = card.get("index_usd"), card.get("expected_move_usd")
    seg = [p for t, p in points if read < t <= expiry]
    complete = covered(points, read, expiry)
    ratio = (lambda usd: round(usd / move, 3) if usd is not None and move else None)
    short = None
    if card.get("short") and card["short"].get("k") is not None:
        ks = card["short"]["k"]
        put = (card.get("side") == "put")
        hits = [t for t, p in points if read < t <= expiry and (p <= ks if put else p >= ks)]
        short = {"touched": bool(hits) if complete else None,
                 "first_touch_ms": hits[0] if hits and complete else None,
                 "payout_btc": payout_btc}
    levels = []
    for lv in card.get("levels") or []:
        after = behaviour(points, lv["k"], read, expiry) if complete else None
        levels.append({
            "k": lv["k"], "roles": lv["roles"], "role_class": lv["role_class"], "after": after,
            "approach_no_cross": (after["crossings"] == 0 and after["closest_usd"] <= APPROACH_SHARE * move)
            if after and move else None,
            "oi_change_to_expiry": expiry_structure.oi_change(snapshots, lv["k"], expiry, expiry - read)
            if snapshots else None,
            "flow_to_expiry": expiry_structure.flow_sum(flow_rows, lv["k"], read, expiry) if flow_rows else None})
    return {"schema": REVIEW_SCHEMA, "rules": RULES, "expiry_ms": expiry, "expiry_day": card["expiry_day"],
            "round": card["round"], "side": card["side"], "card_read_at_ms": read, "case_id": card.get("case_id"),
            "delivery_price_usd": delivery_price_usd,
            "settle_in_moves": ratio(delivery_price_usd - index) if delivery_price_usd is not None and index is not None else None,
            "range_in_moves": {"up": ratio(max(seg) - index), "down": ratio(index - min(seg))}
            if seg and index is not None and complete else None,
            "path_complete": complete, "short": short, "levels": levels, "backfilled": card.get("backfilled", False)}


# ---------------------------------------------------------------- phenomena ledger (docs/opus/32 §4)

PHENOMENA_SCHEMA = "phenomena@0.1"
PIN_NEAR_SHARE = 0.25          # P2: late4h spot within 0.25 expected moves of the pin
FAR_SHARE = 0.5                # P3: the first crossing went past the short by more than 0.5 moves
HEAVY_RATIO = 2.0              # P6: aggressor buy above twice the sell, or the reverse
PHENOMENA = (
    ("P1", "持仓最大的行权价像边界",
     "该侧持仓第一在现价外侧、距离不超过 1 个预期波动；正例：事后接近但未穿越；反例：被穿过且到期仍在另一侧"),
    ("P2", "临近到期钉住",
     "到期前 4 小时现价离 Pin 不超过 0.25 个预期波动；正例：交割价离 Pin 比当时现价更近；反例：更远；相等为不适用。"
     "对照：离现价最近的普通行权价（非 Pin），同一条件"),
    ("P3", "安静之后的第一次穿越走得远",
     "短腿读牌前 24 小时没有被穿过、读牌后被穿过；正例：首次穿越后 60 分钟内越过短腿超过 0.5 个预期波动；反例：不超过"),
    ("P4", "被穿过后多数会折回",
     "任一持仓集中位（含兼作腿的）读牌后被穿过；正例：60 分钟内回到原来一侧；反例：没有。同一到期日同一轮同一行权价只计一次"),
    ("P5", "持仓在减少的集中位更容易被穿过",
     "P1 的适用条件，且读牌前 24 小时该位该类型持仓变化可得；正例：减少且被穿过，或增加且未被穿过；反例：其余；不变为不适用"),
    ("P6", "主动买量偏向一侧的集中位",
     "P1 的适用条件，且读牌前 24 小时该位该类型的成交完整（无缺口、未截断）；主动买超过主动卖两倍或相反时按偏向分组记录是否被穿过，不预设方向"),
)
RESULTS = ("for", "against", "n/a", "missing")


def _outside(k: float, index: float, side: str) -> bool:
    return k < index if side == "put" else k > index


def _is_oi(level: Mapping[str, Any]) -> bool:
    return any(r.startswith("oi_") for r in level.get("roles") or [])


def _p1_level(card: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, str]:
    """The card side's open-interest #1 level and why it is or is not read: ok / n/a / missing."""
    side, index, move = card.get("side"), card.get("index_usd"), card.get("expected_move_usd")
    level = next((l for l in card.get("levels") or [] if f"oi_{side}#1" in l["roles"]), None)
    if index is None or not move or level is None:
        return None, "missing"
    if _outside(level["k"], index, side) and abs(level["k"] - index) <= move:
        return level, "ok"
    return None, "n/a"


def _controls_near(card: Mapping[str, Any], k: float) -> list[Mapping[str, Any]]:
    """The control levels next to k: the nearest control below and above."""
    ctl = [l for l in card.get("levels") or [] if "control" in l["roles"]]
    below = [l for l in ctl if l["k"] < k]
    above = [l for l in ctl if l["k"] > k]
    return ([max(below, key=lambda l: l["k"])] if below else []) + ([min(above, key=lambda l: l["k"])] if above else [])


def _p1_result(review_level: Mapping[str, Any] | None) -> str:
    a = (review_level or {}).get("after")
    if not a or review_level.get("approach_no_cross") is None:
        return "missing"
    if review_level["approach_no_cross"]:
        return "for"
    return "against" if a["crossings"] and a.get("end_beyond") else "n/a"


def _p5_result(level: Mapping[str, Any], after: Mapping[str, Any] | None, kind: str) -> str:
    change = (level.get("oi_change") or {}).get("24h") or {}
    if not after or change.get("gap") is not False or change.get(kind) is None:
        return "missing"
    if change[kind] == 0:
        return "n/a"
    return "for" if (change[kind] < 0) == (after["crossings"] > 0) else "against"


def _p6_group(level: Mapping[str, Any], after: Mapping[str, Any] | None, kind: str) -> str:
    flow = (level.get("flow") or {}).get("24h") or {}
    if not after or flow.get("gap") is not False or flow.get("truncated") is not False:
        return "missing"
    buy, sell = flow.get(f"{kind}_buy") or 0.0, flow.get(f"{kind}_sell") or 0.0
    return "buy_heavy" if buy > HEAVY_RATIO * sell else "sell_heavy" if sell > HEAVY_RATIO * buy else "n/a"


def _tally() -> dict[str, int]:
    return {"applies": 0, "for": 0, "against": 0, "not_applicable": 0, "missing": 0}


def _count(t: dict[str, int], result: str) -> None:
    if result in ("for", "against"):
        t["applies"] += 1
        t[result] += 1
    elif result == "missing":
        t["missing"] += 1
    else:
        t["not_applicable"] += 1


def phenomena(pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]], *, now_ms: int) -> dict[str, Any]:
    """Mechanical counts over every (card, review) pair: for / against / not applicable, with data that
    were missing counted apart, and the same rule at the adjacent control strikes. r1 and r2 (and the
    late looks) are separate readings of one expiry: their cards are counted separately, so expiry_days
    is the number of independent settlements behind the counts. No thresholds, conclusions or signals."""
    items = {pid: {"id": pid, "title_cn": t, "definition_cn": d, **_tally(), "control": _tally(), "cases": []}
             for pid, t, d in PHENOMENA}
    items["P6"]["groups"] = {g: {"n": 0, "crossed": 0} for g in ("buy_heavy", "sell_heavy")}
    items["P6"]["control"]["groups"] = {g: {"n": 0, "crossed": 0} for g in ("buy_heavy", "sell_heavy")}
    days, p4_seen = set(), set()

    def note(pid: str, card: Mapping[str, Any], result: str, k: float | None) -> None:
        _count(items[pid], result)
        items[pid]["cases"].append({"expiry_day": card["expiry_day"], "round": card["round"], "side": card["side"],
                                    "k": k, "result": result, "backfilled": card.get("backfilled", False)})

    def p6(target: dict[str, Any], group: str, after: Mapping[str, Any] | None) -> None:
        if group in ("buy_heavy", "sell_heavy"):
            target["groups"][group]["n"] += 1
            target["groups"][group]["crossed"] += int(after["crossings"] > 0)
            target["applies"] += 1
        elif group == "missing":
            target["missing"] += 1
        else:
            target["not_applicable"] += 1

    for card, review in pairs:
        days.add(card["expiry_day"])
        after = {lv["k"]: lv for lv in review.get("levels") or []}
        side, index, move = card.get("side"), card.get("index_usd"), card.get("expected_move_usd")
        if side in ("put", "call"):
            kind = "c" if side == "call" else "p"
            p1, why = _p1_level(card)
            note("P1", card, _p1_result(after.get(p1["k"])) if p1 else why, p1["k"] if p1 else None)
            if p1:
                a = (after.get(p1["k"]) or {}).get("after")
                note("P5", card, _p5_result(p1, a, kind), p1["k"])
                group = _p6_group(p1, a, kind)
                p6(items["P6"], group, a)
                items["P6"]["cases"].append({"expiry_day": card["expiry_day"], "round": card["round"], "side": side,
                                             "k": p1["k"], "result": group, "backfilled": card.get("backfilled", False)})
                # the controls next to the P1 level, read by the same rules when they meet the same condition
                for c in _controls_near(card, p1["k"]):
                    if not (_outside(c["k"], index, side) and abs(c["k"] - index) <= move):
                        continue
                    ca = (after.get(c["k"]) or {}).get("after")
                    _count(items["P1"]["control"], _p1_result(after.get(c["k"])))
                    _count(items["P5"]["control"], _p5_result(c, ca, kind))
                    p6(items["P6"]["control"], _p6_group(c, ca, kind), ca)
            # P3: the short leg quiet before, crossed after; the controls next to it the same way
            ks = (card.get("short") or {}).get("k")
            if ks is not None:
                lv = next((l for l in card["levels"] if l["k"] == ks), {})
                b, a = lv.get("before_24h"), (after.get(ks) or {}).get("after")
                if b is None or a is None or not move:
                    res = "missing"
                elif b["crossings"] == 0 and a["crossings"] > 0:
                    res = "for" if a["beyond_60m_usd"] > FAR_SHARE * move else "against"
                else:
                    res = "n/a"
                note("P3", card, res, ks)
                for c in _controls_near(card, ks):
                    cb, ca = c.get("before_24h"), (after.get(c["k"]) or {}).get("after")
                    if cb is None or ca is None or not move:
                        _count(items["P3"]["control"], "missing")
                    elif cb["crossings"] == 0 and ca["crossings"] > 0:
                        _count(items["P3"]["control"], "for" if ca["beyond_60m_usd"] > FAR_SHARE * move else "against")
                    else:
                        _count(items["P3"]["control"], "n/a")
        # P2: the late look four hours before expiry; its control is the listed strike nearest the spot
        if card.get("round") == "late4h":
            pin = next((l for l in card.get("levels") or [] if "pin" in l["roles"]), None)
            delivery = review.get("delivery_price_usd")
            if pin is None or index is None or not move or delivery is None:
                res = "missing"
            elif abs(index - pin["k"]) > PIN_NEAR_SHARE * move:
                res = "n/a"
            else:
                d0, d1 = abs(index - pin["k"]), abs(delivery - pin["k"])
                res = "for" if d1 < d0 else "against" if d1 > d0 else "n/a"
            note("P2", card, res, pin["k"] if pin else None)
            spot = next((l for l in card.get("levels") or [] if "spot_control" in l["roles"]), None)
            if spot and index is not None and move and delivery is not None:
                if abs(index - spot["k"]) > PIN_NEAR_SHARE * move:
                    _count(items["P2"]["control"], "n/a")
                else:
                    d0, d1 = abs(index - spot["k"]), abs(delivery - spot["k"])
                    _count(items["P2"]["control"], "for" if d1 < d0 else "against" if d1 > d0 else "n/a")
        # P4: open-interest levels (legs included) crossed after the reading; once per expiry, round, strike
        for lv in card.get("levels") or []:
            key = (card.get("expiry_ms"), card.get("round"), lv["k"])
            is_oi, is_ctl = _is_oi(lv), lv["role_class"] == "control"
            if not (is_oi or is_ctl) or key in p4_seen:
                continue
            p4_seen.add(key)
            a = (after.get(lv["k"]) or {}).get("after")
            res = "missing" if a is None else "n/a" if not a["crossings"] else "for" if a["back_within_60m"] else "against"
            if is_oi:
                note("P4", card, res, lv["k"])
            else:
                _count(items["P4"]["control"], res)
    return {"schema": PHENOMENA_SCHEMA, "rules": RULES, "derived": True, "built_at_ms": now_ms,
            "expiry_days": len(days), "pairs": len(pairs), "items": list(items.values()),
            "note_cn": "机械计数：正例、反例、不适用，以及对照位上的同口径计数；数据缺失单独计为“缺数据”，不算不适用。"
                       "同一到期日的两轮与晚读是同一次交割的不同读牌，各自计数，独立交割数看到期日数。"
                       "不设门槛、不下结论、不产生信号；要把某条现象变成可用读数，须按 26 号 §4.1 登记假设并走前向验证。"}


def phenomena_text(body: Mapping[str, Any]) -> str:
    lines = [f"现象台账（{body['rules']}）", body["note_cn"], "",
             f"到期日 {body['expiry_days']} 个，卡与对谱 {body['pairs']} 对。", ""]
    for it in body["items"]:
        c = it["control"]
        lines.append(f"{it['id']} {it['title_cn']}：适用 {it['applies']}（正例 {it['for']}，反例 {it['against']}），"
                     f"不适用 {it['not_applicable']}，缺数据 {it['missing']}；"
                     f"对照位 适用 {c['applies']}（正例 {c['for']}，反例 {c['against']}），缺数据 {c['missing']}")
        if it["id"] == "P6":
            for label, g in (("", it["groups"]), ("对照位 ", c["groups"])):
                lines.append(f"    {label}主动买偏多 {g['buy_heavy']['n']} 次（其后被穿过 {g['buy_heavy']['crossed']}），"
                             f"主动卖偏多 {g['sell_heavy']['n']} 次（其后被穿过 {g['sell_heavy']['crossed']}）")
        lines.append(f"    定义：{it['definition_cn']}")
    return "\n".join(lines) + "\n"
