"""Intraday exit study (docs/opus/12 step 2)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import astra_research_intraday_exit_v1 as ix
import astra_research_scorecards_v1 as sc

H = 3_600_000
EXPIRY = 1_792_569_600_000            # Wed 2026-10-21 08:00 UTC
KEY = "BTC-21OCT26-82000-C|BTC-21OCT26-84000-C"


def _row(at, short_ask, long_bid, short_mark, long_mark, iv, dim=None):
    return {"contract_key": KEY, "at_ms": at, "side": "call", "displayed": True, "distance_in_moves": dim,
            "short": {"best_ask_price": short_ask, "mark_price": short_mark, "mark_iv": iv},
            "long": {"best_bid_price": long_bid, "mark_price": long_mark}}


def _archive(tmp_path, payout, rows=None):
    root = tmp_path / "a"
    (root / "ledger").mkdir(parents=True)
    (root / "outcomes").mkdir()
    rows = rows or [_row(EXPIRY - 20 * H, 0.0030, 0.0004, 0.0028, 0.0005, 30.0, 0.6),     # 12:00 UTC
                    _row(EXPIRY - 4 * H, 0.0012, None, 0.0010, 0.0001, 27.0, 1.2)]         # 04:00 UTC
    text = "".join(json.dumps(r) + "\n" for r in rows) + "not json\n[1, 2]\n"
    (root / "ledger" / "2026-10-20.jsonl").write_text(text, encoding="utf-8")
    (root / "outcomes" / "2026-10-21.jsonl").write_text(json.dumps(
        {"contract_key": KEY, "status": "settled", "expiry_ms": EXPIRY, "payout_btc": payout}) + "\n"
        + json.dumps({"contract_key": "x", "status": "settled"}) + "\n", encoding="utf-8")
    return root


def test_close_costs_and_missing_quotes():
    taker, mark = ix.close_costs(_row(0, 0.0030, 0.0004, 0.0028, 0.0005, 30.0))
    assert abs(taker - (0.0030 - 0.0004 + 0.0003 + 0.00005)) < 1e-12 and abs(mark - 0.0023) < 1e-12
    taker, _ = ix.close_costs(_row(0, 0.0012, None, 0.0010, 0.0001, 27.0))      # no bid: the long sells for 0
    assert abs(taker - (0.0012 + 0.00015)) < 1e-12
    assert ix.close_costs(_row(0, 0.0, 0.0002, 0.001, 0.0, 27.0))[0] is None    # a zero ask is no quote
    assert ix.close_costs({"short": {"best_ask_price": 0.001}})[0] is None       # the long leg is missing
    assert ix.close_costs({"short": "bad", "long": {}}) == (None, None)


def test_clock_factor_is_on_the_all_days_scale():
    tue = EXPIRY - 26 * H                                     # 2026-10-20 06:00 UTC
    assert abs(ix.clock_factor(tue + 8 * H, tue + 9 * H) - 2.357 * 1.193) < 1e-9
    sat = tue + 4 * 24 * H                                    # Saturday 06:00 UTC
    assert abs(ix.clock_factor(sat + 16 * H, sat + 17 * H) - 2.335 * 0.515) < 1e-9
    assert ix.clock_factor(tue, tue) is None
    assert 1.15 < ix.clock_factor(EXPIRY - 24 * H, EXPIRY) < 1.25            # a weekday is busier than average


def test_settled_rows_compare_closing_with_holding(tmp_path):
    card = ix.build(_archive(tmp_path, payout=0.0050), now_ms=EXPIRY + H)
    o = card["overall"]
    assert o["rows"] == 2 and o["contracts"] == 1 and card["enough_days"] is False
    by_row = {s["hour_utc"]: s for s in card["recent_samples"]}
    assert abs(by_row[12]["close_advantage_btc"] - (0.0050 - 0.00295)) < 1e-12      # closing would have saved
    assert o["max_close_advantage_btc"] == max(s["close_advantage_btc"] for s in by_row.values())
    assert by_row[12]["clock_factor"] > by_row[4]["clock_factor"]                     # the US session was ahead
    assert set(card["by"]["hours_to_expiry"]) == {"18-24h", "3-6h"}
    assert card["by"]["distance_in_moves"]["0.5-1"]["rows"] == 1


def test_rows_are_thinned_and_unquoted_rows_counted(tmp_path):
    rows = [_row(EXPIRY - 3 * H + m * 60_000, 0.0010, 0.0001, 0.0009, 0.0001, 30.0) for m in (0, 10, 20, 30)]
    rows.append(_row(EXPIRY - 2 * H + 30 * 60_000, None, None, 0.0005, 0.0, 30.0))
    card = ix.build(_archive(tmp_path, payout=0.0, rows=rows), now_ms=EXPIRY + H)
    assert card["overall"]["rows"] == 3 and card["overall"]["rows_without_close_quote"] == 1   # 05:00, 05:30, 06:30


def test_iv_clock_attributes_changes_to_the_hour_they_happen_in(tmp_path):
    rows = [_row(EXPIRY - 4 * H, 0.001, 0.0, 0.001, 0.0, 30.0), _row(EXPIRY - 4 * H + 30 * 60_000, 0.001, 0.0, 0.001, 0.0, 33.0),
            _row(EXPIRY - 2 * H, 0.001, 0.0, 0.001, 0.0, 20.0)]           # a 90-minute gap is not a pair
    clock = ix.build(_archive(tmp_path, payout=0.0, rows=rows), now_ms=EXPIRY)["iv_clock"]["by_hour_utc"]
    import math
    assert list(clock) == ["04"] and abs(clock["04"]["log_iv_change_per_hour"] - 2 * math.log(1.1)) < 1e-9


def test_scorecards_keep_both_cards_when_the_study_fails(tmp_path, monkeypatch):
    root = _archive(tmp_path, payout=0.0)
    assert sc.run(root, now_ms=1)["intraday_exit_rows"] == 2
    assert (root / "scorecards" / "intraday_exit.json").exists()
    monkeypatch.setattr(sc.intraday_exit, "build", lambda root, now_ms: 1 / 0)
    report = sc.run(root, now_ms=2)
    assert report["intraday_exit_error"].startswith("ZeroDivisionError")
    assert (root / "scorecards" / "views.json").exists() and (root / "scorecards" / "inquiry.json").exists()
    assert not (root / "scorecards" / "intraday_exit.json").exists()


def test_holding_and_entry_measures(tmp_path):
    rows = [dict(_row(EXPIRY - 20 * H, 0.0030, 0.0004, 0.0028, 0.0005, 30.0), index_usd=82_000.0,
                 short={"best_ask_price": 0.0030, "best_bid_price": 0.0028, "mark_price": 0.0028, "mark_iv": 30.0},
                 long={"best_bid_price": 0.0004, "best_ask_price": 0.0006, "mark_price": 0.0005}),
            dict(_row(EXPIRY - 3 * H, 0.0004, None, 0.0003, 0.0001, 27.0), index_usd=82_000.0,
                 short={"best_ask_price": 0.0004, "best_bid_price": 0.0002, "mark_price": 0.0003, "mark_iv": 27.0},
                 long={"best_bid_price": None, "best_ask_price": 0.0001, "mark_price": 0.0001})]
    root = _archive(tmp_path, payout=0.0, rows=rows)
    out = root / "outcomes" / "2026-10-21.jsonl"
    o = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    o.update(short_strike_usd=82_000.0, long_strike_usd=84_000.0)
    out.write_text(json.dumps(o) + "\n", encoding="utf-8")
    card = ix.build(root, now_ms=EXPIRY + H)
    s = {r["hour_utc"]: r for r in card["recent_samples"]}
    width = 2000 / 82_000
    assert abs(s[12]["hold_premium_to_max_loss"] - 0.0023 / (width - 0.0023)) < 1e-9
    assert s[5]["hold_premium_to_max_loss"] < s[12]["hold_premium_to_max_loss"]        # late: little premium left
    credit = 0.0028 - 0.0006 - 0.0003 - 0.000075
    assert abs(s[12]["entry_credit_btc"] - credit) < 1e-12 and abs(s[12]["entry_result_btc"] - credit) < 1e-12
    assert s[12]["us_open"] == "ahead" and s[5]["us_open"] == "passed"                   # Tue 12:00 vs Wed 05:00 UTC
    assert set(card["entry"]["by_us_open"]) == {"美股开盘尚未经历", "美股开盘已经历"}
    assert card["entry"]["by_us_open"]["美股开盘尚未经历"]["rows"] == 1
    assert card["holding"]["by_hours_to_expiry"]["18-24h"]["rows"] == 1


def test_us_open_state_has_three_cases():
    sat_noon, sun_08, mon_08 = 1_792_843_200_000, 1_792_915_200_000, 1_793_001_600_000   # 2026-10-24/25/26
    assert ix._us_open_state(sat_noon, sun_08) == "none"            # a weekend cycle has no US open
    assert ix._us_open_state(sun_08 + 4 * 3_600_000, mon_08) == "none"
    tue_open = 1_792_503_000_000                                     # Tue 2026-10-20 13:30 UTC
    assert ix._us_open_state(tue_open + 30 * 60_000, EXPIRY) == "ahead"      # still in its first hour
    assert ix._us_open_state(tue_open + 2 * H, EXPIRY) == "passed"
    xmas, xmas_exp = 1_798_200_000_000, 1_798_243_200_000           # 2026-12-25 12:00 -> 12-26 08:00 UTC
    assert ix._us_open_state(xmas, xmas_exp) == "none"
