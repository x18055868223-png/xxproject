import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import astra_session_clock_v1 as clock

H = 3_600_000
TUE_08 = 1_792_483_200_000            # Tue 2026-10-20 08:00 UTC (US daylight time)


def test_remaining_shares_follow_the_doc_12_cycle():
    at_20 = clock.reading(TUE_08 + 12 * H)                   # 20:00 UTC, 12 h before Wed 08:00
    assert at_20["remaining_time_share"] == 0.5
    assert 0.38 < at_20["remaining_variance_share"] < 0.46  # doc 12: about 42% left at 20 UTC
    at_00 = clock.reading(TUE_08 + 16 * H)
    assert 0.21 < at_00["remaining_variance_share"] < 0.29  # about 25% at 00 UTC
    assert clock.reading(TUE_08)["remaining_variance_share"] == 1.0


def test_phases_follow_the_us_eastern_clock():
    assert clock.phase(TUE_08 + 6 * H)["code"] == "us_session"       # 14:00 UTC = 10:00 EDT
    assert clock.phase(TUE_08 - 3 * H)["code"] == "quiet"            # 05:00 UTC
    winter = 1_795_406_400_000                                         # Mon 2026-11-23 04:00 UTC (EST)
    assert clock.phase(winter + 9 * H + 45 * 60_000)["code"] == "pre_us"   # 13:45 UTC = 08:45 EST
    assert clock.phase(TUE_08 + 5 * 24 * H + 14 * H)["code"] == "weekend_cme"            # Sun 22:00 UTC


def test_windows_list_the_us_open_and_macro_releases_before_expiry():
    w = clock.reading(TUE_08 + H)["windows"]                  # Tue 09:00 UTC
    assert w[0]["kind"] == "us_open" and abs(w[0]["in_hours"] - 4.5) < 1e-9     # 13:30 UTC in daylight time
    sunday = clock.reading(TUE_08 + 5 * 24 * H)               # Sun 08:00 UTC
    assert any(x["kind"] == "cme_reopen" and abs(x["in_hours"] - 14) < 1e-9 for x in sunday["windows"])
    assert all(x["at_ms"] < sunday["expiry_ms"] for x in sunday["windows"])


def test_busy_and_quiet_levels():
    assert clock.reading(TUE_08 + 6 * H)["hour_level"] == "busy"
    assert clock.reading(TUE_08 - 3 * H)["hour_level"] == "quiet"


def test_holidays_gaps_progress_and_long_expiries():
    thanksgiving = 1_795_651_200_000                         # Thu 2026-11-26 00:00 UTC
    w = clock.windows(thanksgiving + 12 * H, thanksgiving + 20 * H)
    assert not any(x["kind"] == "us_open" for x in w)
    assert clock.phase(thanksgiving + 15 * H)["code"] == "us_holiday"
    at = clock.reading(TUE_08 + 5 * H + 40 * 60_000)        # 13:40 UTC: the open started 10 minutes ago
    first = at["windows"][0]
    assert first["kind"] == "us_open" and first["in_progress"] is True and first["in_hours"] < 0
    far = clock.reading(TUE_08, TUE_08 + 48 * H)
    assert far["beyond_cycle"] is True and far["expiry_source"] == "contract" and far["hours_to_expiry"] == 48
    assert clock.reading(TUE_08)["expiry_source"] == "next_daily_expiry"
    assert clock.reading(1_830_000_000_000)["calendar_gaps"]   # 2027-12: the calendars end


def test_hour_profile_gives_the_risk_density_per_hour():
    r = clock.reading(TUE_08 + 6 * H)
    prof = r["hour_profile"]
    assert r["now_hour_utc"] == 14 and r["now_day_type"] == "weekday"
    wd = prof["weekday"]
    assert len(wd) == 24 and abs(wd[14]["risk_ratio"] - 2.357 * 1.193) < 1e-3
    assert "value_ratio" not in wd[14] and wd[5]["risk_ratio"] < 0.7
    assert prof["weekend"][22]["risk_ratio"] > 1.1
