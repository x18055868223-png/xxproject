"""Exogenous events: the static calendar's integrity and its consumption on the workbench."""
from collections import Counter
from datetime import date, datetime, timezone

from tools import astra_event_calendar_v1 as cal
from tools import astra_map_sources_v24 as map_sources
from tools import astra_workbench_reading_v1 as reading
from test_astra_locked_followup import setup

MONTHLY = ("CPI", "PPI", "NFP")


def ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


def test_every_row_is_a_us_business_day_at_the_official_release_time():
    holidays = map_sources.nyse_holidays(2026, 2027)
    for kind, day, time_et, period, flags in cal.EVENTS:
        d = date.fromisoformat(day)
        assert kind in cal.KIND_CN and d.weekday() < 5 and d not in holidays, (kind, day)
        assert time_et == ("14:00" if kind == "FOMC" else "08:30"), (kind, day)
        assert set(flags) <= {"sep", "tentative"} and period
        if kind == "FOMC":
            assert d.weekday() == 2, day          # decision day: the Wednesday of a two-day meeting


def test_no_duplicates_and_rows_stay_in_date_order():
    keys = [(k, d) for k, d, *_ in cal.EVENTS]
    assert len(keys) == len(set(keys))
    assert [d for _, d, *_ in cal.EVENTS] == sorted(d for _, d, *_ in cal.EVENTS)


def test_monthly_bls_releases_are_complete_through_coverage():
    for kind in MONTHLY:
        months = Counter(d[:7] for k, d, *_ in cal.EVENTS if k == kind)
        expected = [f"{y}-{m:02d}" for y in (2026, 2027) for m in range(1, 13) if f"{y}-{m:02d}" >= "2026-10"]
        assert [m for m in expected if months[m] != 1] == [], kind


def test_bls_period_is_the_month_before_release():
    for kind, day, _, period, _ in cal.EVENTS:
        if kind in MONTHLY:
            d = date.fromisoformat(day)
            ref = 12 if d.month == 1 else d.month - 1
            assert period == f"{ref} 月", (kind, day, period)


def test_fomc_has_eight_meetings_a_year_with_four_projections():
    for year in ("2027",):
        rows = [r for r in cal.EVENTS if r[0] == "FOMC" and r[1].startswith(year)]
        assert len(rows) == 8 and sum("sep" in r[4] for r in rows) == 4
        assert all("tentative" in r[4] for r in rows)
    assert [r[1] for r in cal.EVENTS if r[0] == "FOMC" and r[1].startswith("2026")] == ["2026-10-28", "2026-12-09"]


def test_coverage_reaches_past_the_last_row_of_each_source_and_ends_a_year():
    last = {}
    for kind, day, *_ in cal.EVENTS:
        last[cal.SOURCE_OF[kind]] = max(last.get(cal.SOURCE_OF[kind], ""), day)
    assert set(last) == set(cal.COVERAGE)
    assert all(last[s] <= cal.COVERAGE[s] and cal.COVERAGE[s].endswith("-12-31") for s in last)


def test_eastern_time_follows_daylight_saving():
    assert cal.event_ms("2026-10-14", "08:30") == ms("2026-10-14T12:30:00")   # EDT
    assert cal.event_ms("2026-11-06", "08:30") == ms("2026-11-06T13:30:00")   # EST after Nov 1
    assert cal.event_ms("2027-03-10", "08:30") == ms("2027-03-10T13:30:00")   # EST before Mar 14
    assert cal.event_ms("2027-03-17", "14:00") == ms("2027-03-17T18:00:00")   # EDT after Mar 14
    assert cal.event_ms("2027-11-05", "08:30") == ms("2027-11-05T12:30:00")   # EDT until Nov 7, 2027
    assert cal.event_ms("2026-11-04", "08:30") == ms("2026-11-04T13:30:00")   # EST after Nov 1, 2026


def test_two_week_window_and_contract_window():
    now = ms("2026-10-13T14:00:00")
    up = cal.upcoming(now)
    assert [(e["kind"], e["date_et"]) for e in up["events"]] == [("CPI", "2026-10-14"), ("PPI", "2026-10-15")]
    assert up["gaps"] == []
    # a release of the last hour stays (shown as released); the horizon end is exclusive
    cpi = cal.event_ms("2026-10-14", "08:30")
    assert cal.upcoming(cpi + 3_000_000)["events"][0]["kind"] == "CPI"
    assert cal.upcoming(cpi + 3_700_000)["events"][0]["kind"] == "PPI"
    assert all(e["at_ms"] < cpi for e in cal.upcoming(cpi - 14 * 86_400_000)["events"])
    life = cal.contract_events(ms("2026-10-14T08:00:00"), ms("2026-10-15T08:00:00"))
    assert [e["kind"] for e in life["events"]] == ["CPI"]
    assert life["events"][0]["to_expiry_ms"] == ms("2026-10-15T08:00:00") - ms("2026-10-14T12:30:00")
    assert cal.contract_events(ms("2026-10-17T08:00:00"), ms("2026-10-18T08:00:00"))["events"] == []
    assert cal.contract_events(now, now - 1) is None and cal.contract_events(now, None) is None


def test_gaps_name_sources_past_their_schedule_and_undated_releases():
    late = cal.upcoming(ms("2026-12-15T00:00:00"))
    assert {"source": "BEA", "through": "2026-12-31"} not in late["gaps"]       # BEA covers all of 2026
    assert any(g.get("undated") and g["kind"] == "PCE" for g in late["gaps"])
    assert {"source": "BEA", "through": "2026-12-31"} in cal.upcoming(ms("2026-12-25T00:00:00"))["gaps"]
    assert not any(g.get("undated") for g in cal.upcoming(ms("2026-11-20T00:00:00"))["gaps"])  # not due yet
    assert not any(g.get("undated") for g in cal.upcoming(ms("2027-02-01T00:00:00"))["gaps"])  # stale entry expires
    assert {g["source"] for g in cal.upcoming(ms("2027-12-20T00:00:00"))["gaps"] if "through" in g} == {"BLS", "Fed", "BEA"}


def test_workbench_attaches_the_calendar_and_each_contract_window(tmp_path):
    wb, _, _, snapshot = setup(tmp_path)
    view = wb.cached_view()
    assert view["exogenous_events"]["schema"] == cal.SCHEMA
    item = view["candidates"][0]
    assert item["exogenous_events"]["expiry_ms"] == snapshot["contract"]["expiry_ms"]
    page = reading.workbench_page(reading.workbench_reading(view))
    assert "exogenous_events" in page and "exogenous_events" in page["candidates"][0]


def test_a_calendar_failure_never_breaks_the_view(tmp_path, monkeypatch):
    wb, _, _, _ = setup(tmp_path)
    monkeypatch.setattr(cal, "upcoming", lambda now: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(cal, "contract_events", lambda now, expiry: (_ for _ in ()).throw(RuntimeError("boom")))
    view = wb.cached_view()
    assert view["exogenous_events"]["error_type"] == "RuntimeError"
    assert view["candidates"][0]["exogenous_events"] is None
    view = {"candidates": [{"status": "error"}, {"snapshot": None}]}
    wb._attach_exogenous_events(view, ms("2026-10-13T14:00:00"))
    assert [c["exogenous_events"] for c in view["candidates"]] == [None, None]
