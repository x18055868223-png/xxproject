"""Exogenous events: scheduled US macro releases on the path to expiry (static calendar).

A static calendar kept in the repository, maintained once a year from the official
schedules (they are published a year ahead). Runtime fetching is not used: BLS refuses
non-browser clients and FRED's release-date API needs a key (checked 2026-10-13).
Display only: no direction, no score, no gate.

Sources (all times US Eastern, as published):
  BLS  CPI, PPI, Employment Situation   https://www.bls.gov/schedule/2026/home.htm, .../2027/home.htm
  Fed  FOMC meetings                    https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
  BEA  GDP, Personal Income and Outlays https://www.bea.gov/news/schedule

Annual maintenance: add the next year's rows, move COVERAGE forward, and re-run
tests/test_astra_event_calendar.py (it checks weekdays, times, holidays and coverage).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

SCHEMA = "astra_event_calendar_v1@1.0.0"
CALENDAR_VERSION = "2026-10-14"
HORIZON_DAYS = 14

KIND_CN = {
    "FOMC": "FOMC 利率决议",
    "CPI": "CPI 消费者物价",
    "NFP": "非农就业",
    "PCE": "PCE 个人收入与支出",
    "PPI": "PPI 生产者物价",
    "GDP": "GDP",
}
SOURCE_OF = {"CPI": "BLS", "PPI": "BLS", "NFP": "BLS", "FOMC": "Fed", "GDP": "BEA", "PCE": "BEA"}

# How far each source's published schedule reaches (checked on CALENDAR_VERSION): BLS and
# the Fed have published all of 2027, BEA only 2026.
COVERAGE = {"BLS": "2027-12-31", "Fed": "2027-12-31", "BEA": "2026-12-31"}
# Releases the source lists without a date yet: (kind, period, source, shown from, shown
# until). Shown once the horizon reaches about when it would normally fall, and for a month
# after that, so a forgotten entry cannot claim "undated" forever.
# 2026-10-14: BEA lists every 2026 Personal Income and Outlays date (re-checked against
# bea.gov/news/schedule/full; the earlier 11-04 / 12-02 rows were other BEA releases).
PENDING: tuple[tuple[str, str, str, str, str], ...] = ()

# (kind, date ET, time ET, period in Chinese, flags). FOMC: the decision day (statement
# 14:00 ET, press conference 14:30); "sep" = with economic projections; 2027 FOMC dates are
# tentative until confirmed at the preceding meeting.
EVENTS: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    # ---- 2026 (from October)
    ("NFP", "2026-10-02", "08:30", "9 月", ()),
    ("CPI", "2026-10-14", "08:30", "9 月", ()),
    ("PPI", "2026-10-15", "08:30", "9 月", ()),
    ("FOMC", "2026-10-28", "14:00", "10 月会议", ()),
    ("GDP", "2026-10-29", "08:30", "三季度初值", ()),
    ("PCE", "2026-10-29", "08:30", "9 月", ()),
    ("NFP", "2026-11-06", "08:30", "10 月", ()),
    ("CPI", "2026-11-10", "08:30", "10 月", ()),
    ("PPI", "2026-11-13", "08:30", "10 月", ()),
    ("GDP", "2026-11-25", "08:30", "三季度修正值", ()),
    ("PCE", "2026-11-25", "08:30", "10 月", ()),
    ("NFP", "2026-12-04", "08:30", "11 月", ()),
    ("FOMC", "2026-12-09", "14:00", "12 月会议", ("sep",)),
    ("CPI", "2026-12-10", "08:30", "11 月", ()),
    ("PPI", "2026-12-15", "08:30", "11 月", ()),
    ("GDP", "2026-12-23", "08:30", "三季度终值", ()),
    ("PCE", "2026-12-23", "08:30", "11 月", ()),
    # ---- 2027 (BLS and Fed; BEA has not published 2027 yet)
    ("NFP", "2027-01-08", "08:30", "12 月", ()),
    ("CPI", "2027-01-13", "08:30", "12 月", ()),
    ("PPI", "2027-01-14", "08:30", "12 月", ()),
    ("FOMC", "2027-01-27", "14:00", "1 月会议", ("tentative",)),
    ("NFP", "2027-02-05", "08:30", "1 月", ()),
    ("CPI", "2027-02-11", "08:30", "1 月", ()),
    ("PPI", "2027-02-12", "08:30", "1 月", ()),
    ("NFP", "2027-03-05", "08:30", "2 月", ()),
    ("CPI", "2027-03-10", "08:30", "2 月", ()),
    ("PPI", "2027-03-12", "08:30", "2 月", ()),
    ("FOMC", "2027-03-17", "14:00", "3 月会议", ("sep", "tentative")),
    ("NFP", "2027-04-02", "08:30", "3 月", ()),
    ("CPI", "2027-04-13", "08:30", "3 月", ()),
    ("PPI", "2027-04-14", "08:30", "3 月", ()),
    ("FOMC", "2027-04-28", "14:00", "4 月会议", ("tentative",)),
    ("NFP", "2027-05-07", "08:30", "4 月", ()),
    ("CPI", "2027-05-12", "08:30", "4 月", ()),
    ("PPI", "2027-05-13", "08:30", "4 月", ()),
    ("NFP", "2027-06-04", "08:30", "5 月", ()),
    ("FOMC", "2027-06-09", "14:00", "6 月会议", ("sep", "tentative")),
    ("CPI", "2027-06-10", "08:30", "5 月", ()),
    ("PPI", "2027-06-11", "08:30", "5 月", ()),
    ("NFP", "2027-07-02", "08:30", "6 月", ()),
    ("CPI", "2027-07-14", "08:30", "6 月", ()),
    ("PPI", "2027-07-15", "08:30", "6 月", ()),
    ("FOMC", "2027-07-28", "14:00", "7 月会议", ("tentative",)),
    ("NFP", "2027-08-06", "08:30", "7 月", ()),
    ("PPI", "2027-08-10", "08:30", "7 月", ()),
    ("CPI", "2027-08-11", "08:30", "7 月", ()),
    ("NFP", "2027-09-03", "08:30", "8 月", ()),
    ("PPI", "2027-09-10", "08:30", "8 月", ()),
    ("CPI", "2027-09-14", "08:30", "8 月", ()),
    ("FOMC", "2027-09-15", "14:00", "9 月会议", ("sep", "tentative")),
    ("NFP", "2027-10-08", "08:30", "9 月", ()),
    ("CPI", "2027-10-14", "08:30", "9 月", ()),
    ("PPI", "2027-10-15", "08:30", "9 月", ()),
    ("FOMC", "2027-10-27", "14:00", "10 月会议", ("tentative",)),
    ("NFP", "2027-11-05", "08:30", "10 月", ()),
    ("CPI", "2027-11-10", "08:30", "10 月", ()),
    ("PPI", "2027-11-12", "08:30", "10 月", ()),
    ("NFP", "2027-12-03", "08:30", "11 月", ()),
    ("FOMC", "2027-12-08", "14:00", "12 月会议", ("sep", "tentative")),
    ("CPI", "2027-12-10", "08:30", "11 月", ()),
    ("PPI", "2027-12-15", "08:30", "11 月", ()),
)


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def eastern_offset_hours(day: date) -> int:
    """US Eastern: daylight time from the second Sunday of March to the first Sunday of
    November (the switch is at 02:00, before any release time)."""
    return -4 if _nth_sunday(day.year, 3, 2) <= day < _nth_sunday(day.year, 11, 1) else -5


def event_ms(day: str, time_et: str) -> int:
    d = date.fromisoformat(day)
    hh, mm = (int(x) for x in time_et.split(":"))
    local = datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone(timedelta(hours=eastern_offset_hours(d))))
    return int(local.timestamp() * 1000)


def events() -> list[dict[str, Any]]:
    out = []
    for kind, day, time_et, period, flags in EVENTS:
        out.append({"kind": kind, "name_cn": KIND_CN[kind], "period_cn": period, "date_et": day, "time_et": time_et,
                    "at_ms": event_ms(day, time_et), "source": SOURCE_OF[kind],
                    "sep": "sep" in flags, "tentative": "tentative" in flags})
    return sorted(out, key=lambda e: e["at_ms"])


def _day_start_ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _day_end_ms(day: str) -> int:
    """End of a UTC date (exclusive). Every ET release time falls on its own UTC date."""
    return _day_start_ms(day) + 86_400_000


def between(start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    return [e for e in events() if start_ms <= e["at_ms"] < end_ms]


def coverage_gaps(end_ms: int, now_ms: int | None = None) -> list[dict[str, Any]]:
    """Sources whose published schedule ends before end_ms, plus releases still undated."""
    now = end_ms if now_ms is None else now_ms
    gaps = [{"source": s, "through": through} for s, through in COVERAGE.items() if _day_end_ms(through) < end_ms]
    pending = [{"kind": k, "name_cn": KIND_CN[k], "period_cn": p, "source": s, "undated": True}
               for k, p, s, shown_from, shown_until in PENDING
               if _day_start_ms(shown_from) <= end_ms and now < _day_end_ms(shown_until)]
    return gaps + pending


def upcoming(now_ms: int, days: int = HORIZON_DAYS, *, grace_ms: int = 3_600_000) -> dict[str, Any]:
    """The next `days` days, keeping releases of the last hour so the page can show them as released."""
    end = now_ms + days * 86_400_000
    return {"schema": SCHEMA, "calendar_version": CALENDAR_VERSION, "horizon_days": days,
            "from_ms": now_ms, "to_ms": end, "events": between(now_ms - grace_ms, end),
            "coverage": dict(COVERAGE), "gaps": coverage_gaps(end, now_ms)}


def contract_events(now_ms: int, expiry_ms: int | None) -> dict[str, Any] | None:
    """Scheduled releases inside the contract's remaining life, each with the time left to expiry."""
    if not isinstance(expiry_ms, int) or isinstance(expiry_ms, bool) or expiry_ms <= now_ms:
        return None
    items = [{**e, "to_expiry_ms": expiry_ms - e["at_ms"]} for e in between(now_ms, expiry_ms)]
    return {"schema": SCHEMA, "from_ms": now_ms, "expiry_ms": expiry_ms, "events": items,
            "gaps": coverage_gaps(expiry_ms, now_ms)}
