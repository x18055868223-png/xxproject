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
    classes = {"leg" if r in ("short", "long") else "derived" if r == "pin" else "control" if r == "control"
               else KIND_ROLES[r.split("#")[0]] for r in roles}
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
