"""BTC cross-asset reference: states, verdicts, alignment, contract window, workbench wiring."""
import json
import math
import random
from datetime import datetime, timezone

from tools import astra_cross_asset_v1 as xa
from test_astra_locked_followup import T, setup

AX = ("qqq", "gold", "dxy")


def ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


def series(n, loads, seed=7, noise=1.0):
    """btc = sum(load * axis) + noise; axes independent standard normals."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        row = {"date": f"d{i:04d}", "close_ts_ms": i}
        for k in AX:
            row[k] = rng.gauss(0, 0.01)
        row["btc"] = sum(loads.get(k, 0.0) * row[k] for k in AX) + rng.gauss(0, 0.01 * noise)
        out.append(row)
    return out


def test_relation_states_separate_weak_from_undecided():
    assert xa.state(0.6, 60) == "positive" and xa.state(-0.6, 60) == "negative"
    assert xa.state(0.0, 60) == "weak"            # the interval excludes any material relation
    assert xa.state(0.22, 60) == "uncertain"      # neither a relation nor its absence
    assert xa.state(0.0, 12) == "uncertain"       # too few sessions to call it weak
    assert xa.state(None, 60) == "uncertain"


def test_trend_compares_the_short_window_with_the_long_one():
    assert xa.trend("positive", "positive") == "stable"
    assert xa.trend("negative", "uncertain") == "fading"
    assert xa.trend("positive", "negative") == "reversed"
    assert xa.trend("weak", "negative") == "emerging"
    assert xa.trend("uncertain", "weak") == "none"


def test_main_references_include_opposite_relations_and_are_ranked():
    out = xa.assess(series(130, {"qqq": 1.2, "dxy": -0.8}))
    assert out["status"] == "ok" and out["verdict"]["kind"] == "references"
    assert out["verdict"]["main"] == ["qqq", "dxy"]
    axes = {a["key"]: a for a in out["axes"]}
    assert axes["dxy"]["long"]["state"] == "negative" and axes["dxy"]["beta"] < 0
    assert axes["gold"]["long"]["state"] in ("weak", "uncertain")
    assert set(out["attribution"]["axis_pct"]) == {"qqq", "dxy"}   # only main references attribute


def test_independent_needs_every_axis_confirmed_weak():
    out = xa.assess(series(130, {}, seed=10))
    assert {a["long"]["state"] for a in out["axes"]} == {"weak"}
    assert out["verdict"] == {"kind": "independent", "main": [], "overlaps": []} and out["attribution"] is None
    # one axis the sample cannot decide keeps the verdict open instead of calling it independent
    out = xa.assess(series(130, {}, seed=3))
    assert "uncertain" in {a["long"]["state"] for a in out["axes"]} and out["verdict"]["kind"] == "uncertain"
    assert xa.assess(series(40, {"qqq": 1}))["status"] == "insufficient_history"


def test_attribution_is_out_of_sample_and_adds_up():
    rows = series(130, {"qqq": 1.0})
    out = xa.assess(rows)
    fit = xa.ols([r["btc"] for r in rows[-61:-1]], [[r["qqq"] for r in rows[-61:-1]]])
    att = out["attribution"]
    assert math.isclose(att["explained_pct"], round(fit[0][0] * rows[-1]["qqq"] * 100, 3), abs_tol=1e-9)
    assert math.isclose(att["explained_pct"] + att["own_pct"], rows[-1]["btc"] * 100, abs_tol=2e-3)


def test_overlapping_main_references_are_flagged():
    rows = series(130, {})
    for r in rows:  # gold mostly a copy of qqq; btc follows both
        r["gold"] = 0.9 * r["qqq"] + 0.3 * r["gold"]
        r["btc"] = r["qqq"] + r["gold"] + 0.5 * r["btc"]
    out = xa.assess(rows)
    assert out["verdict"]["overlaps"] and set(out["verdict"]["overlaps"][0]["axes"]) == {"qqq", "gold"}


def chart(points):
    return {"chart": {"result": [{"timestamp": [p[0] for p in points],
                                  "indicators": {"quote": [{"close": [p[1] for p in points]}]}}]}}


def day_bars(day, base, *, anchor, close_hour=20):
    """US summer day: QQQ bars start :30 (last one 30 min), others start on the hour."""
    start = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())
    if anchor:
        hours = [13.5 + i for i in range(close_hour - 14)] + [close_hour - 0.5]
        return [(start + int(h * 3600), base + h) for h in hours]
    return [(start + h * 3600, base + h) for h in range(0, 24)]


def test_alignment_takes_every_asset_at_the_anchor_close():
    bars = {"qqq": day_bars("2026-10-08", 500, anchor=True) + day_bars("2026-10-09", 510, anchor=True, close_hour=17),
            "btc": day_bars("2026-10-08", 60000, anchor=False) + day_bars("2026-10-09", 61000, anchor=False),
            "gold": day_bars("2026-10-08", 4000, anchor=False) + day_bars("2026-10-09", 4100, anchor=False)}
    rows = xa.align(bars)
    assert [r["date"] for r in rows] == ["2026-10-08", "2026-10-09"]
    assert rows[0]["close_ts_ms"] == ms("2026-10-08T20:00:00") and rows[0]["btc"] == 60000 + 19  # bar 19:00-20:00
    assert rows[1]["close_ts_ms"] == ms("2026-10-09T17:00:00") and rows[1]["gold"] == 4100 + 16  # early close
    bars["gold"] = day_bars("2026-10-08", 4000, anchor=False)[:10]  # no gold bar near either close
    assert xa.align(bars) == []


def test_collect_drops_a_session_still_trading_and_names_a_failed_fetch():
    days = ["2026-10-0%d" % d for d in (1, 2)] + ["2026-10-%02d" % d for d in range(5, 10)]
    calls = []

    def fake(url, params):
        calls.append((url, params))
        symbol = url.rsplit("/", 1)[1]
        key = {"QQQ": "qqq", "BTC-USD": "btc", "GC=F": "gold", "DX-Y.NYB": "dxy"}[symbol]
        pts = []
        for i, d in enumerate(days):
            pts += day_bars(d, 100 + i + (hash(key) % 7), anchor=key == "qqq")
        return chart(pts)

    out = xa.collect(fake, ms("2026-10-09T18:00:00"))  # 10-09 session not closed yet
    assert out["status"] == "insufficient_history" and out["observations"] == len(days) - 2
    assert all(p == {"interval": "1h", "range": xa.FETCH_RANGE, "includePrePost": "false"} for _, p in calls)
    failed = xa.collect(lambda url, params: (_ for _ in ()).throw(TimeoutError()), T)
    assert failed["status"] == "fetch_failed" and failed["error_type"] == "TimeoutError"


def test_contract_window_knows_weekends_and_holidays():
    friday = xa.contract_window(ms("2026-10-16T08:00:00"), ms("2026-10-17T08:00:00"))
    assert friday["covers_us_session"] and friday["sessions"][0]["day"] == "2026-10-16"
    assert friday["overlap_ms"] == int(6.5 * 3600_000)
    sunday = xa.contract_window(ms("2026-10-17T08:00:00"), ms("2026-10-18T08:00:00"))
    assert sunday["covers_us_session"] is False and sunday["sessions"] == []
    thanksgiving = xa.contract_window(ms("2026-11-26T08:00:00"), ms("2026-11-27T08:00:00"))
    assert thanksgiving["covers_us_session"] is False
    assert xa.contract_window(ms("2026-10-16T08:00:00"), ms("2026-10-16T07:00:00")) is None


def test_due_after_each_us_close_and_on_retry():
    ok = {"status": "ok", "computed_at_ms": ms("2026-10-08T21:00:00"), "as_of_date": "2026-10-08",
          "as_of_close_ms": ms("2026-10-08T20:00:00")}
    assert xa.due(None, T)
    assert not xa.due(ok, ms("2026-10-09T12:00:00"))          # no new close yet
    assert xa.due(ok, ms("2026-10-09T20:30:00"))              # the 10-09 session closed
    failed = {"status": "fetch_failed", "computed_at_ms": ms("2026-10-09T20:30:00")}
    assert not xa.due(failed, ms("2026-10-09T20:40:00")) and xa.due(failed, ms("2026-10-09T20:46:00"))


def test_workbench_serves_the_card_with_the_contract_window_and_keeps_the_last_good_reading(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    good = {**xa.assess(series(130, {"qqq": 1.0})), "computed_at_ms": T}
    wb._cross_asset = None
    wb.cross_asset_get_json = lambda url, params: (_ for _ in ()).throw(ConnectionError())
    view = wb.cached_view()
    card = view["cross_asset"]
    assert card["status"] == "not_collected"
    assert card["contract_window"]["expiry_ms"] == snapshot["contract"]["expiry_ms"]
    wb._cross_asset = good
    failed = wb.refresh_cross_asset(T + 3_600_000, force=True)
    assert failed["status"] == "ok" and failed["verdict"] == good["verdict"]
    assert failed["last_attempt"]["status"] == "fetch_failed"
    stored = json.loads((wb.config.state_dir / "cross_asset.json").read_text(encoding="utf-8"))
    assert stored["last_attempt"]["error_type"] == "ConnectionError"
    reading = wb.cached_view(reading_only=True)
    assert reading["cross_asset"]["verdict"]["main"] == ["qqq"]


def test_due_settles_after_an_early_close_and_survives_bad_state():
    # Black Friday closes at 13:00 New York; the calendar says 16:00. Compare dates, not times.
    early = {"status": "ok", "as_of_date": "2026-11-27", "as_of_close_ms": ms("2026-11-27T18:00:00"),
             "computed_at_ms": ms("2026-11-27T21:30:00")}
    for hours in (22, 30, 60, 66):  # until Monday 15:30 UTC
        assert not xa.due(early, early["computed_at_ms"] + hours * 3_600_000)
    assert xa.due(early, ms("2026-11-30T21:30:00"))           # Monday's session closed
    assert xa.due({"status": "ok", "computed_at_ms": "x", "as_of_date": 5}, T)
    assert xa.due({"status": "ok", "computed_at_ms": T + 10**9}, T)   # a future clock is no clock
    kept = {"status": "ok", "as_of_date": "2026-10-08", "computed_at_ms": ms("2026-10-08T21:00:00"),
            "last_attempt": {"computed_at_ms": ms("2026-10-09T20:30:00")}}
    assert not xa.due(kept, ms("2026-10-09T20:40:00")) and xa.due(kept, ms("2026-10-09T20:46:00"))


def test_alignment_ignores_a_stray_point_at_the_close_and_non_finite_prices():
    bars = {"qqq": day_bars("2026-10-08", 500, anchor=True) + [(ms("2026-10-08T20:00:00") // 1000, 999.0)],
            "btc": day_bars("2026-10-08", 60000, anchor=False)}
    rows = xa.align(bars)
    assert rows[0]["close_ts_ms"] == ms("2026-10-08T20:00:00") and rows[0]["qqq"] == 500 + 19.5
    assert xa.parse_chart(chart([(1, float("inf")), (2, float("nan")), (3, 5.0)])) == [(3, 5.0)]


def test_refresh_never_stores_non_finite_values_and_keeps_the_good_clock(tmp_path, monkeypatch):
    wb, _, _, _ = setup(tmp_path)
    good = {**xa.assess(series(130, {"qqq": 1.0})), "computed_at_ms": T}
    wb._cross_asset = good
    monkeypatch.setattr(xa, "collect", lambda get_json, now: {"schema": xa.SCHEMA, "status": "ok", "joint_r2": float("nan")})
    kept = wb.refresh_cross_asset(T + 3_600_000, force=True)
    assert kept["computed_at_ms"] == T and kept["last_attempt"]["status"] == "invalid_values"
    wb._cross_asset = None
    bad = wb.refresh_cross_asset(T + 3_600_000, force=True)
    assert bad["status"] == "invalid_values"
    view = wb.cached_view()
    view["candidates"] = [{"snapshot": "not a dict"}]
    assert wb.cross_asset_view(view["candidates"], T)["contract_window"] is None
