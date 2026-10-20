"""Entry warnings: the "what not to do" layer for opening a daily vertical (docs/opus/13).

Warnings only veto; nothing here recommends a trade. Thresholds are first values chosen
by the user and Claude on 2026-10-20 and are NOT validated: every warning says so, and the
archive records them on each ledger row so the outcomes after a warning can be counted.

Implied volatilities of different horizons are compared per unit of intraday clock (doc 12):
an IV is divided by the square root of the average variance ratio of the time it covers,
so a weekday daily is not "inverted" just because weekdays are busier than weeks.

- term_inversion: the contract's own clock-adjusted ATM IV exceeds the clock-adjusted IV of
  the expiry nearest one week by at least INVERSION_VOL_PTS (near-dated protection is
  being bought now).
- premium_thickening: against the median of the last LOOKBACK hours of the same expiry
  (one row per 30-minute slot, at least MIN_SLOTS slots over MIN_SPAN hours), either the
  clock-adjusted ATM IV of the expiry rose by IV_JUMP_PTS (the whole expiry repriced), or
  this side's skew (short-leg IV minus ATM IV) of the same contract rose by IV_JUMP_PTS (the
  skew is compared only within one contract: another strike sits elsewhere on the smile). A skew
  jump while the other side's skew moved less than OTHER_SIDE_FLAT_PTS either way is marked
  one-sided (possibly informed buying).
- busy_window_thin_cushion: the market is in a busy hour now, or a high-variance window (US
  open, macro release, Sunday CME reopen) starts within WINDOW_HOURS or started within the
  last hour, and the short leg is less than CUSHION_MOVES expected moves away.
Each warning is triggered true / false, or null when its inputs are missing.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

try:
    from . import astra_session_clock_v1 as session_clock
except ImportError:  # script-style import
    import astra_session_clock_v1 as session_clock

RULES = "entry_warnings@0.1"
INVERSION_VOL_PTS = 0.5
LOOKBACK_HOURS = 6
IV_JUMP_PTS = 3.0
OTHER_SIDE_FLAT_PTS = 1.0
WINDOW_HOURS = 2.0
CUSHION_MOVES = 0.5
SLOT_MS = 30 * 60_000
MIN_SLOTS = 4
MIN_SPAN_HOURS = 2.0
HOUR_MS = 3_600_000
UNVALIDATED_CN = "阈值未经验证（2026-10-20 初值），只作否决提醒"
THRESHOLD_CN = {
    "gap_vol_pts_at_least": "时钟校正后本期高于一周至少（vol 点）",
    "jump_vol_pts_at_least": "较近 6 小时中位上升至少（vol 点）",
    "lookback_hours": "回看小时数",
    "other_side_flat_below_vol_pts": "另一侧变化小于（vol 点，判断单侧）",
    "window_within_hours": "窗口在多少小时内",
    "distance_in_moves_below": "σ 距离低于",
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def clock_adjusted(iv: float | None, start_ms: int, end_ms: int) -> float | None:
    """IV per unit of intraday clock over [start, end): iv / sqrt(average variance ratio)."""
    if iv is None or end_ms <= start_ms:
        return None
    factor = session_clock.variance_between(start_ms, end_ms) / ((end_ms - start_ms) / HOUR_MS)
    return iv / math.sqrt(factor) if factor > 0 else None


def _warning(code: str, label_cn: str, triggered: bool | None, evidence: Mapping[str, Any],
             threshold: Mapping[str, Any], reason_cn: str) -> dict[str, Any]:
    return {"code": code, "label_cn": label_cn, "triggered": triggered, "evidence": dict(evidence),
            "threshold": dict(threshold), "threshold_cn": {THRESHOLD_CN.get(k, k): v for k, v in threshold.items()},
            "reason_cn": reason_cn, "validated": False}


def term_inversion(term_structure: Any, contract_expiry_label: str | None, own_atm_iv: Any,
                   now_ms: int, contract_expiry_ms: int | None) -> dict[str, Any]:
    terms = [t for t in term_structure or [] if isinstance(t, Mapping)]
    label = (contract_expiry_label or "").upper()
    own_term = next((t for t in terms if label and str(t.get("expiry") or "").upper() == label), None)
    # like with like: both IVs from the same term structure when it has the contract's expiry
    if _num((own_term or {}).get("atm_iv")) is not None:
        own, own_source = _num(own_term["atm_iv"]), "gexmonitor.term_structure"
    else:
        own = _num(own_atm_iv)
        own_source = "deribit.expiry_gamma" if own is not None else None
    week = min((t for t in terms if (_num(t.get("days_to_expiry")) or 0) >= 4.5),
               key=lambda t: abs((_num(t.get("days_to_expiry")) or 0) - 7.0), default=None)
    week_iv, week_days = _num((week or {}).get("atm_iv")), _num((week or {}).get("days_to_expiry"))
    own_adj = clock_adjusted(own, now_ms, contract_expiry_ms) if isinstance(contract_expiry_ms, int) else None
    week_adj = clock_adjusted(week_iv, now_ms, now_ms + int(week_days * 24 * HOUR_MS)) if week_days else None
    gap = own_adj - week_adj if own_adj is not None and week_adj is not None else None
    triggered = None if gap is None else gap >= INVERSION_VOL_PTS
    return _warning("term_inversion", "近端倒挂", triggered,
                    {"contract_expiry": contract_expiry_label, "contract_atm_iv": own, "contract_atm_iv_source": own_source,
                     "week_atm_iv_source": "gexmonitor.term_structure" if week_iv is not None else None,
                     "contract_atm_iv_clock_adjusted": own_adj,
                     "week_expiry": (week or {}).get("expiry"), "week_atm_iv": week_iv, "week_atm_iv_clock_adjusted": week_adj,
                     "gap_vol_pts": gap},
                    {"gap_vol_pts_at_least": INVERSION_VOL_PTS},
                    "按日内时钟校正后，本期 IV 仍高于一周期限：有人急需近端保护，先当警告。" if triggered else
                    "按日内时钟校正后，期限结构正常。" if triggered is False else "期限结构数据缺失，无法判断。")


def _slots(history: Iterable[Mapping[str, Any]], side: str, expiry_ms: int, now_ms: int,
           contract_key: str | None = None) -> list[dict[str, float]]:
    """Last row per 30-minute slot of the same side and expiry (and contract, when given) over
    the lookback, with the clock-adjusted ATM IV and the side's skew (short IV - ATM IV)."""
    slots: dict[int, Mapping[str, Any]] = {}
    for r in history or []:
        at = r.get("at_ms")
        if (r.get("side") != side or r.get("displayed") is not True or r.get("expiry_ms") != expiry_ms
                or not isinstance(at, int) or not now_ms - LOOKBACK_HOURS * HOUR_MS <= at < now_ms):
            continue
        if contract_key is not None and r.get("contract_key") != contract_key:
            continue
        prior = slots.get(at // SLOT_MS)
        if prior is None or at >= prior["at_ms"]:
            slots[at // SLOT_MS] = r
    out = []
    for r in slots.values():
        atm = _num(r.get("expiry_atm_iv_pct"))
        short = _num(r.get("short_mark_iv"))
        if short is None and isinstance(r.get("short"), Mapping):   # a full ledger row
            short = _num(r["short"].get("mark_iv"))
        out.append({"at_ms": r["at_ms"], "atm_adj": clock_adjusted(atm, r["at_ms"], expiry_ms),
                    "skew": short - atm if short is not None and atm is not None else None})
    return out


def _enough(slots: list[Mapping[str, Any]], key: str) -> list[float] | None:
    values = [(s["at_ms"], s[key]) for s in slots if s[key] is not None]
    if len(values) < MIN_SLOTS or (max(t for t, _ in values) - min(t for t, _ in values)) / HOUR_MS < MIN_SPAN_HOURS:
        return None
    return [v for _, v in values]


def premium_thickening(side: str, expiry_ms: int, now_ms: int, atm_iv: Any, short_iv: Any, other_short_iv: Any,
                       history: Iterable[Mapping[str, Any]], contract_key: str | None = None,
                       other_contract_key: str | None = None) -> dict[str, Any]:
    rows = list(history or [])
    other = "call" if side == "put" else "put"
    mine = _slots(rows, side, expiry_ms, now_ms)                    # the whole expiry's ATM IV
    mine_contract = _slots(rows, side, expiry_ms, now_ms, contract_key) if contract_key else mine
    theirs = _slots(rows, other, expiry_ms, now_ms, other_contract_key) if other_contract_key else []
    atm, short, oshort = _num(atm_iv), _num(short_iv), _num(other_short_iv)
    atm_adj = clock_adjusted(atm, now_ms, expiry_ms)
    skew = short - atm if short is not None and atm is not None else None
    oskew = oshort - atm if oshort is not None and atm is not None else None
    atm_hist, skew_hist, oskew_hist = _enough(mine, "atm_adj"), _enough(mine_contract, "skew"), _enough(theirs, "skew")
    atm_jump = atm_adj - _median(atm_hist) if atm_adj is not None and atm_hist else None
    skew_jump = skew - _median(skew_hist) if skew is not None and skew_hist else None
    other_jump = oskew - _median(oskew_hist) if oskew is not None and oskew_hist else None
    if atm_jump is None and skew_jump is None:
        triggered = None
    else:
        triggered = (atm_jump is not None and atm_jump >= IV_JUMP_PTS) or (skew_jump is not None and skew_jump >= IV_JUMP_PTS)
    skew_fired = skew_jump is not None and skew_jump >= IV_JUMP_PTS
    one_sided = bool(skew_fired and other_jump is not None and abs(other_jump) < OTHER_SIDE_FLAT_PTS)
    if not triggered:
        if triggered is None:
            reason = "近 6 小时同一到期的记录不足，无法判断。"
        elif skew_jump is None:
            reason = "与近 6 小时相比，本期 IV（时钟校正）没有异常上升（本合约偏斜记录不足，未检查）。"
        elif atm_jump is None:
            reason = "与近 6 小时相比，本侧偏斜没有异常上升（整体 IV 记录不足，未检查）。"
        else:
            reason = "与近 6 小时相比，本期 IV（时钟校正）与本侧偏斜都没有异常上升。"

    elif skew_fired and one_sided:
        reason = "本侧偏斜突然变厚而另一侧没动：可能有人知道些什么，先当警告。"
    elif skew_fired and other_jump is None:
        reason = "本侧偏斜突然变厚；另一侧无数据，无法判断是否单侧。先当警告。"
    elif skew_fired:
        reason = "两侧偏斜同步变厚，更像普遍的风险重估。先当警告。"
    else:
        reason = "本期整体 IV（时钟校正后）突然抬升：整条到期在被重新定价，先当警告。"
    return _warning("premium_thickening", "保费突然变厚" + ("（单侧）" if one_sided else ""), triggered,
                    {"atm_iv": atm, "atm_iv_clock_adjusted": atm_adj, "atm_jump_vol_pts": atm_jump,
                     "skew_vol_pts": skew, "skew_jump_vol_pts": skew_jump, "other_side_skew_jump_vol_pts": other_jump,
                     "one_sided": one_sided, "slots": len(mine), "checked": [k for k, v in (("atm", atm_jump), ("skew", skew_jump)) if v is not None]},
                    {"jump_vol_pts_at_least": IV_JUMP_PTS, "lookback_hours": LOOKBACK_HOURS,
                     "other_side_flat_below_vol_pts": OTHER_SIDE_FLAT_PTS}, reason)


def busy_window_thin_cushion(clock_reading: Mapping[str, Any] | None, distance_in_moves: Any) -> dict[str, Any]:
    if not isinstance(clock_reading, Mapping) or clock_reading.get("error_type"):
        near, busy_now = None, False
        clock_ok = False
    else:
        clock_ok = True
        near = next((w for w in clock_reading.get("windows") or []
                     if _num(w.get("in_hours")) is not None and -1.0 <= w["in_hours"] <= WINDOW_HOURS), None)
        busy_now = clock_reading.get("hour_level") == "busy"
    dim = _num(distance_in_moves)
    active = near is not None or busy_now
    if not clock_ok:
        triggered = None
    elif not active:
        triggered = False
    else:
        triggered = None if dim is None else dim < CUSHION_MOVES
    if near is not None:
        when = "正在进行" if near["in_hours"] <= 0 else f"约 {near['in_hours']:.1f} 小时后"
        what = f"{near['label_cn']}{when}"
    else:
        what = "当前处于高波动时段" if busy_now else ""
    reason = (f"{what}，短腿只有 {dim:.2f} 倍预期波动的缓冲。" if triggered else
              "2 小时内没有高波动窗口，当前也不在高波动时段。" if triggered is False and not active else
              "缓冲足够。" if triggered is False else "缺少时钟或 σ 距离，无法判断。")
    return _warning("busy_window_thin_cushion", "高波动窗口临近、缓冲不足", triggered,
                    {"next_window": near, "busy_now": busy_now, "distance_in_moves": dim},
                    {"window_within_hours": WINDOW_HOURS, "distance_in_moves_below": CUSHION_MOVES}, reason)


def evaluate(*, side: str, now_ms: int, contract_expiry_ms: int | None, contract_expiry_label: str | None,
             term_structure: Any, expiry_atm_iv: Any, short_iv: Any, other_short_iv: Any, distance_in_moves: Any,
             history: Iterable[Mapping[str, Any]] | None, clock_reading: Mapping[str, Any] | None,
             contract_key: str | None = None, other_contract_key: str | None = None) -> dict[str, Any]:
    items = [term_inversion(term_structure, contract_expiry_label, expiry_atm_iv, now_ms, contract_expiry_ms),
             premium_thickening(side, contract_expiry_ms, now_ms, expiry_atm_iv, short_iv, other_short_iv, history or [],
                                contract_key, other_contract_key)
             if isinstance(contract_expiry_ms, int) else
             _warning("premium_thickening", "保费突然变厚", None, {}, {}, "合约到期缺失，无法判断。"),
             busy_window_thin_cushion(clock_reading, distance_in_moves)]
    fired = [w["code"] for w in items if w["triggered"]]
    return {"rules": RULES, "side": side, "at_ms": now_ms, "warnings": items, "triggered": fired,
            "any_triggered": bool(fired), "unknown": [w["code"] for w in items if w["triggered"] is None],
            "note_cn": UNVALIDATED_CN}
