"""Session clock: where the present sits in BTC's intraday variance cycle (docs/opus/12).

A pure function of the time (and the static event calendar), so the read view, the case
file and the archive all show the same reading. Facts only: the cycle shape was stable in
all 8 quarters of two years of Binance BTCUSDT 1-minute data; nothing here is a rule.

- Hourly variance ratios relative to the all-days mean (weekday tables x1.193, weekend
  x0.515, from doc 12), applied minute by minute by the UTC hour and day type.
- remaining_*: from now to the next daily expiry (08:00 UTC) or a given expiry: the share
  of time left and the share of the cycle's variance left (both relative to the full
  24-hour cycle ending at that expiry).
- windows: the next high-variance moments before the horizon: the US equity open
  (09:30 US Eastern on weekdays, NYSE full-day holidays excluded), US macro releases
  from the event calendar (with their own time), and the CME bitcoin futures reopen
  (Sunday 18:00 US Eastern).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

try:
    from . import astra_event_calendar_v1 as event_calendar
except ImportError:  # script-style import
    import astra_event_calendar_v1 as event_calendar

SCHEMA = "astra_session_clock_v1@1.0.0"
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
EXPIRY_HOUR_UTC = 8
CLOCK_WEEKDAY = (1.081, 1.087, 0.852, 0.643, 0.544, 0.516, 0.556, 0.612, 0.637, 0.585, 0.519, 0.581,
                 0.871, 1.521, 2.357, 2.093, 1.423, 1.376, 1.213, 1.065, 1.153, 1.162, 0.972, 0.582)
CLOCK_WEEKEND = (0.871, 0.889, 0.828, 0.634, 0.705, 0.718, 0.581, 0.636, 0.672, 0.627, 0.56, 0.613,
                 0.758, 0.917, 1.233, 1.206, 1.355, 1.152, 1.426, 1.064, 0.97, 1.266, 2.335, 1.983)
WEEKDAY_SCALE, WEEKEND_SCALE = 1.193, 0.515
# NYSE full-day closures (no 09:30 open); half days still open at 09:30. Source: NYSE holiday
# calendar for 2026-2027 (Saturday holidays observed on Friday, Sunday ones on Monday).
US_MARKET_HOLIDAYS = frozenset({
    "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
    "2027-09-06", "2027-11-25", "2027-12-24"})
HOLIDAYS_THROUGH = "2027-12-31"
WINDOW_GRACE_MS = HOUR_MS   # a window that started within the last hour is still in progress
BUSY_AT = 1.25   # the page's "波动集中" tier uses the same line      # an hour at least this busy (x all-days mean) counts as a high-variance hour
QUIET_BELOW = 0.8
PHASES_ET = (  # weekday phases on the US Eastern clock (daylight time handled), minutes from midnight
    (0, 8 * 60, "quiet", "安静时段（亚洲上午至欧洲上午）"), (8 * 60, 9 * 60 + 30, "pre_us", "美股开盘前（含 8:30 数据）"),
    (9 * 60 + 30, 16 * 60, "us_session", "美股时段（全天波动最集中）"), (16 * 60, 23 * 60, "us_evening", "美股收盘后"),
    (23 * 60, 24 * 60, "quiet", "安静时段（亚洲上午至欧洲上午）"))


def _utc(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)


def hour_ratio(ms: int) -> float:
    moment = _utc(ms)
    if moment.weekday() >= 5:
        return CLOCK_WEEKEND[moment.hour] * WEEKEND_SCALE
    return CLOCK_WEEKDAY[moment.hour] * WEEKDAY_SCALE


def variance_between(start_ms: int, end_ms: int) -> float:
    """Integrated variance ratio (hours x ratio) over [start, end)."""
    total, t = 0.0, start_ms
    while t < end_ms:
        step = min((t // HOUR_MS + 1) * HOUR_MS, end_ms) - t
        total += hour_ratio(t) * step / HOUR_MS
        t += step
    return total


def hour_profile() -> dict[str, Any]:
    """Risk density: each UTC hour's price variance relative to the all-days hourly mean (1.0),
    for weekdays and weekends. A fact about the hour, not a cost-performance verdict: the US
    session is also where the day is priced, and late quiet hours leave little premium against
    an unchanged maximum loss (docs/opus/13)."""
    def rows(table, scale):
        return [{"hour_utc": h, "risk_ratio": round(table[h] * scale, 3)} for h in range(24)]
    return {"weekday": rows(CLOCK_WEEKDAY, WEEKDAY_SCALE), "weekend": rows(CLOCK_WEEKEND, WEEKEND_SCALE),
            "basis_cn": "风险密度：该小时过去两年的价格方差相对全天每小时平均（=1）；只描述时段，不评价入场或持有"}


def next_expiry_ms(now_ms: int) -> int:
    moment = _utc(now_ms)
    candidate = moment.replace(hour=EXPIRY_HOUR_UTC, minute=0, second=0, microsecond=0)
    if candidate <= moment:
        candidate += dt.timedelta(days=1)
    return int(candidate.timestamp() * 1000)


def _et_ms(day: dt.date, hh: int, mm: int) -> int:
    offset = event_calendar.eastern_offset_hours(day)
    local = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=dt.timezone(dt.timedelta(hours=offset)))
    return int(local.timestamp() * 1000)


def windows(now_ms: int, until_ms: int) -> list[dict[str, Any]]:
    """High-variance moments from an hour ago (still in progress) until the horizon."""
    start = now_ms - WINDOW_GRACE_MS
    out = []
    start_day = (_utc(start) - dt.timedelta(days=1)).date()
    for i in range(0, (until_ms - start) // DAY_MS + 3):
        day = start_day + dt.timedelta(days=i)
        if day.weekday() < 5 and day.isoformat() not in US_MARKET_HOLIDAYS:
            out.append({"kind": "us_open", "label_cn": "美股开盘", "at_ms": _et_ms(day, 9, 30)})
        if day.weekday() == 6:
            out.append({"kind": "cme_reopen", "label_cn": "CME 比特币期货周日开盘", "at_ms": _et_ms(day, 18, 0)})
    for e in event_calendar.between(start, until_ms):
        out.append({"kind": "macro", "label_cn": e["name_cn"], "at_ms": e["at_ms"], "event_kind": e["kind"],
                    "tentative": bool(e.get("tentative"))})
    return sorted((w | {"in_hours": (w["at_ms"] - now_ms) / HOUR_MS, "in_progress": w["at_ms"] <= now_ms}
                   for w in out if start <= w["at_ms"] < until_ms), key=lambda w: w["at_ms"])


def phase(now_ms: int) -> dict[str, str]:
    """Named on the US Eastern clock and date (the US session defines the busy part)."""
    moment = _utc(now_ms)
    et = moment + dt.timedelta(hours=event_calendar.eastern_offset_hours(moment.date()))
    if et.weekday() >= 5:
        if et.weekday() == 6 and et.hour >= 17:
            return {"code": "weekend_cme", "label_cn": "周日晚间（CME 期货开盘前后，周末最忙）"}
        return {"code": "weekend", "label_cn": "周末（整体约为平日一半的波动）"}
    if et.date().isoformat() in US_MARKET_HOLIDAYS:
        return {"code": "us_holiday", "label_cn": "美股休市日"}
    minute = et.hour * 60 + et.minute
    code, label = next((c, l) for lo, hi, c, l in PHASES_ET if lo <= minute < hi)
    return {"code": code, "label_cn": label}


def reading(now_ms: int, expiry_ms: int | None = None) -> dict[str, Any]:
    """The clock at now, toward the given expiry (default: the next 08:00 UTC)."""
    given = isinstance(expiry_ms, int) and expiry_ms > now_ms
    expiry = expiry_ms if given else next_expiry_ms(now_ms)
    cycle_start = expiry - DAY_MS
    left = variance_between(max(now_ms, cycle_start), expiry)
    full = variance_between(cycle_start, expiry)
    hours_left = (expiry - now_ms) / HOUR_MS
    now_ratio = hour_ratio(now_ms)
    busy = windows(now_ms, expiry)
    gaps = event_calendar.coverage_gaps(expiry, now_ms)
    if _utc(expiry).date().isoformat() > HOLIDAYS_THROUGH:
        gaps = gaps + [{"source": "NYSE holidays", "through": HOLIDAYS_THROUGH}]
    moment = _utc(now_ms)
    return {"schema": SCHEMA, "now_ms": now_ms, "expiry_ms": expiry, "hours_to_expiry": hours_left,
            "now_hour_utc": moment.hour, "now_day_type": "weekend" if moment.weekday() >= 5 else "weekday",
            "hour_profile": hour_profile(),
            "expiry_source": "contract" if given else "next_daily_expiry", "beyond_cycle": hours_left > 24.0,
            "calendar_gaps": gaps,
            "phase": phase(now_ms), "hour_ratio_now": now_ratio,
            "hour_level": "busy" if now_ratio >= BUSY_AT else "quiet" if now_ratio < QUIET_BELOW else "normal",
            "remaining_time_share": min(1.0, hours_left / 24.0),
            "remaining_variance_share": left / full if full else None,
            # over the whole time left (more than one cycle when the expiry is further away)
            "remaining_clock_factor": variance_between(now_ms, expiry) / hours_left if hours_left > 0 else None,
            "windows": busy,
            "basis_cn": "BTC 日内方差节律（Binance 1 分钟，2024-10 至 2026-09，8 个季度形状一致；12 号文档）。美股开盘按 09:30 美东，已排除 NYSE 全日休市。"
                        "remaining_clock_factor >1 表示到期前剩下的时段比全天平均更忙。只描述时段，不是交易规则。"}
