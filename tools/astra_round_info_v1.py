"""Round information for one candidate (docs/opus/27 rulebook v0.2 §5-§7, docs/opus/28 step 2).

Four numbers the player's rulebook asks for, computed by the program, never by a model:

- required edge: the friction hurdle (reservation price minus the gross credit one would fill at)
  over the two legs' mark spread. The true expected payout has to be lower than the market's
  estimate by this share for the trade not to lose. The reservation price already holds every
  hold fee, so the share includes fees. A fill at or above the reservation price needs no edge (0).
- max loss (BTC): q x width / S0 minus the net income, with S0 the index at the reading. It is
  fixed when the reading is taken and is the unit results are counted in (multiples of max loss).
- rounds: two fixed times a day, 02:00 and 14:00 UTC (the scheduled review hours). An expiry
  becomes the candidate at 20:00 UTC with 36 h left (astra_underwriting_discovery_v20 picks the
  expiry nearest 24 h), so each expiry and side has two rounds, about 30 h and 18 h before expiry.
- pass cost (round 1 only): what waiting for round 2 gives up. An approximation on the same
  contract: the spread's value is taken to scale with the square root of the remaining clock
  variance (astra_session_clock_v1); the program may pick another strike by round 2.

Pure functions; a missing input gives None, never an exception.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

try:
    from . import astra_session_clock_v1 as session_clock
except ImportError:  # script-style import
    import astra_session_clock_v1 as session_clock

SCHEMA = "round_info@0.1"
HOUR_MS = 3_600_000
ROUND_HOURS_UTC = (2, 14)
ROUND_WINDOW_MS = 45 * 60_000      # the scheduled review's window: a round is open this long
SAME_EXPIRY_MIN_HOURS = 12.0       # below this the program shows the next expiry (20:00 UTC switch)
NOTE_CN = ("两轮固定在 UTC 02:00 与 14:00（北京时间 10:00 与 22:00），分别约在到期前 30 与 18 小时；"
           "其他时刻的读数照常显示，只供参考。")
PASS_NOTE_CN = ("粗略近似：同一张保单、按剩余方差的平方根估算保费衰减。价外价差的实际衰减通常更快，这个数偏低，"
                "不宜与风险占比直接相比；第二轮程序可能已换了行权价。")
ROUND_HOURS_LEFT = {1: 30.0, 2: 18.0}   # hours to an 08:00 UTC expiry at the 02:00 and 14:00 UTC rounds
ROUND_TOLERANCE_HOURS = 1.0


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def required_edge(res: Mapping[str, Any] | None) -> dict[str, Any]:
    """{"bid", "mid", "reliable", "note_cn"} from an astra_unsuitable_v1.reservation() result.
    bid / mid are shares (0.18 = 18%); 0 when that fill is not below the reservation price."""
    res = res or {}
    mark = _num(res.get("mark_spread_btc"))
    if _num(res.get("reservation_btc")) is None or mark is None:
        return {"bid": None, "mid": None, "reliable": False, "note_cn": "市场估价缺失，所需优势算不出"}
    if mark <= 0:
        return {"bid": None, "mid": None, "reliable": False, "note_cn": "两腿标记价差不大于 0，所需优势无意义"}
    if res.get("reservation_reliable") is False:
        return {"bid": None, "mid": None, "reliable": False, "note_cn": "保护腿盘口空或过宽，保留价不可靠，所需优势不给出"}

    def share(gap: Any) -> float | None:
        g = _num(gap)
        return None if g is None else max(0.0, g) / mark

    return {"bid": share(res.get("bid_gap_btc")), "mid": share(res.get("mid_gap_btc")), "reliable": True, "note_cn": None}


def required_edge_at(credit_btc: Any, res: Mapping[str, Any] | None) -> float | None:
    """The required edge of a fill at `credit_btc` (gross, same basis as the reservation price):
    the player's limit price is checked with this, the same formula as required_edge."""
    res = res or {}
    credit, reserve, mark = _num(credit_btc), _num(res.get("reservation_btc")), _num(res.get("mark_spread_btc"))
    if credit is None or reserve is None or mark is None or mark <= 0 or res.get("reservation_reliable") is False:
        return None
    return max(0.0, reserve - credit) / mark


def max_loss(quantity_btc: Any, width_usd: Any, spot_usd: Any, net_income_btc: Any) -> dict[str, Any]:
    """{"btc", "usd"}: q x W / S0 minus the net income (BTC), and that at S0 in USD."""
    q, w, s, n = _num(quantity_btc), _num(width_usd), _num(spot_usd), _num(net_income_btc)
    if q is None or w is None or s is None or s <= 0 or n is None:
        return {"btc": None, "usd": None}
    btc = q * w / s - n
    return {"btc": btc, "usd": btc * s}


def _slot_starts(now_ms: int) -> list[int]:
    """Round start times from yesterday to the day after tomorrow (UTC), ascending."""
    day0 = now_ms // (24 * HOUR_MS) * (24 * HOUR_MS)
    return sorted(day0 + d * 24 * HOUR_MS + h * HOUR_MS for d in (-1, 0, 1, 2) for h in ROUND_HOURS_UTC)


def round_number(at_ms: int, expiry_ms: int) -> int:
    """1 for the round about 30 h before expiry, 2 for the one about 18 h before."""
    return 1 if (expiry_ms - at_ms) / HOUR_MS >= 24 else 2


def rounds(now_ms: Any, expiry_ms: Any, *, mark_spread_btc: Any = None, spot_usd: Any = None) -> dict[str, Any] | None:
    """The current round (if a round window is open), the next round and, in round 1, the pass cost."""
    if not isinstance(now_ms, int) or not isinstance(expiry_ms, int) or isinstance(now_ms, bool):
        return None
    starts = _slot_starts(now_ms)
    open_at = next((s for s in starts if s <= now_ms < s + ROUND_WINDOW_MS), None)
    current = round_number(open_at, expiry_ms) if open_at is not None and expiry_ms > open_at else None
    if current is not None and abs((expiry_ms - open_at) / HOUR_MS - ROUND_HOURS_LEFT[current]) > ROUND_TOLERANCE_HOURS:
        current = None   # not a daily 08:00 expiry at its own round (e.g. a lock on another expiry)
    nxt = next(s for s in starts if s > now_ms)
    left = (expiry_ms - nxt) / HOUR_MS
    same = left >= SAME_EXPIRY_MIN_HOURS
    following = expiry_ms + 24 * HOUR_MS    # the next daily expiry, shown once this one is under 12 h
    out: dict[str, Any] = {
        "schema": SCHEMA, "rounds_utc": list(ROUND_HOURS_UTC), "current": current,
        "current_at_ms": open_at if current is not None else None,
        "next": {"round": round_number(nxt, expiry_ms if same else following), "at_ms": nxt, "same_expiry": same,
                 "hours_left_at": round(left if same else (following - nxt) / HOUR_MS, 1)},
        "pass_cost": None, "note_cn": NOTE_CN}
    if current == 1 and same and expiry_ms > now_ms:
        total = session_clock.variance_between(now_ms, expiry_ms)
        after = session_clock.variance_between(nxt, expiry_ms)
        mark, spot = _num(mark_spread_btc), _num(spot_usd)
        share = (total - after) / total if total > 0 else None
        premium = mark * (1 - math.sqrt(after / total)) if mark is not None and total > 0 else None
        out["pass_cost"] = {"premium_btc": premium, "premium_usd": premium * spot if premium is not None and spot else None,
                            "variance_share": share, "note_cn": PASS_NOTE_CN}
    return out
