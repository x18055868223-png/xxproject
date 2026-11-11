import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest

import astra_expiry_gamma_v1 as eg
import astra_expiry_structure_v1 as es
import astra_research_archive_v1 as ar

H = 3_600_000
E1 = 1_794_470_400_000            # 2026-11-12 08:00 UTC
E2 = E1 + 24 * H                  # 2026-11-13 08:00 UTC
E9 = E1 + 7 * 24 * H              # outside the 56h window
NOW = E1 - 20 * H                 # 2026-11-11 12:00 UTC


def _row(name, oi, iv=40.0, vol=1.0, mark=0.01, fwd=73000.0):
    return {"instrument_name": name, "open_interest": oi, "mark_iv": iv, "volume": vol, "mark_price": mark,
            "underlying_price": fwd}


def _rows():
    rows = []
    for exp in ("12NOV26", "13NOV26", "19NOV26"):
        for k, coi, poi in ((72000, 50.0, 300.0), (72500, 80.0, 120.0), (73000, 200.0, 90.0), (73500, 400.0, 10.0)):
            rows += [_row(f"BTC-{exp}-{k}-C", coi), _row(f"BTC-{exp}-{k}-P", poi)]
    return rows


def _trade(tid, ts, name="BTC-12NOV26-73000-C", direction="buy", amount=1.0, **extra):
    return {"trade_id": str(tid), "timestamp": ts, "instrument_name": name, "direction": direction, "amount": amount,
            **extra}


class Pages:
    """A fake trades endpoint over a fixed list: asc order, start inclusive, at most `count` per page."""

    def __init__(self, trades, fail=None):
        self.trades, self.fail, self.calls = sorted(trades, key=lambda t: t["timestamp"]), fail, []

    def __call__(self, start, end, count):
        self.calls.append((start, end, count))
        if self.fail:
            raise self.fail
        inside = [t for t in self.trades if start <= t["timestamp"] <= end]
        return {"trades": inside[:count], "has_more": len(inside) > count}


# ------------------------------------------------------------ structure

def test_strike_table_matches_profile_oi_totals():
    rows = _rows()
    table = es.strike_table(rows, expiry_ms=E1)
    prof = eg.profile(rows, expiry_ms=E1, now_ms=NOW)
    assert [s["k"] for s in table] == [72000.0, 72500.0, 73000.0, 73500.0]
    assert sum(s["c_oi"] for s in table) == prof["totals"]["call_oi_btc"]
    assert sum(s["p_oi"] for s in table) == prof["totals"]["put_oi_btc"]
    assert table[0] == {"k": 72000.0, "c_oi": 50.0, "p_oi": 300.0, "c_iv": 40.0, "p_iv": 40.0,
                        "c_vol24h": 1.0, "p_vol24h": 1.0, "c_mark": 0.01, "p_mark": 0.01}


def test_strike_table_keeps_a_row_profile_drops_and_leaves_missing_numbers_empty():
    rows = [_row("BTC-12NOV26-90000-C", 5.0, iv=None, vol="x", mark=None), _row("bad-name", 1.0)]
    assert es.strike_table(rows, expiry_ms=E1) == [{"k": 90000.0, "c_oi": 5.0, "p_oi": None, "c_iv": None, "p_iv": None,
                                                    "c_vol24h": None, "p_vol24h": None, "c_mark": None, "p_mark": None}]
    assert eg.profile(rows, expiry_ms=E1, now_ms=NOW) is None


def test_window_and_cadence():
    names = [r["instrument_name"] for r in _rows()]
    assert es.expiries_in_window(names, NOW) == [E1, E2]
    # 56 h: round 1 (30 h before expiry) has a full 24 h window behind it (docs/opus/33 §7.10)
    assert es.WINDOW_MS == 56 * H
    assert es.expiries_in_window(names, E2 - 56 * H) == [E1, E2]
    assert es.expiries_in_window(names, E2 - 56 * H - 1) == [E1]
    assert es.snapshot_due(None, E2, NOW)
    assert not es.snapshot_due(NOW - 20 * 60_000, E2, NOW)
    assert es.snapshot_due(NOW - 29 * 60_000 - 30_000, E2, NOW)          # timer slack
    assert es.snapshot_due(E1 - 6 * H + 1, E1, E1 - 6 * H + 600_000)       # inside 6h: every tick


def test_snapshot_line_and_expiry_without_rows():
    snap = es.snapshot(_rows(), expiry_ms=E1, now_ms=NOW)
    assert snap["schema"] == "structure_snapshot@0.1" and snap["expiry"] == "12NOV26"
    assert snap["hours_to_expiry"] == 20.0 and len(snap["strikes"]) == 4
    assert snap["profile"]["top"]["oi_call"][0]["strike"] == 73500.0
    assert set(snap["profile"]) == {"walls", "top", "totals"}
    assert es.snapshot(_rows(), expiry_ms=E1 + 3 * 24 * H, now_ms=NOW) is None


# ------------------------------------------------------------ trades

def test_fetch_pages_by_timestamp_and_dedupes_the_boundary(monkeypatch):
    monkeypatch.setattr(es, "PAGE_COUNT", 3)
    trades = [_trade(i, NOW + i * 1000) for i in range(7)] + [_trade("x", NOW + 2000)]   # same ms as id 2
    pages = Pages(trades)
    got = es.fetch_trades(pages, NOW, NOW + 60_000)
    assert sorted(t["trade_id"] for t in got["trades"]) == sorted(["0", "1", "2", "3", "4", "5", "6", "x"])
    assert got["truncated"] is False and got["covered_to_ms"] == NOW + 60_000 and got["pages"] >= 3
    assert [c[0] for c in pages.calls][:2] == [NOW, NOW + 2000]


def test_seen_ids_are_not_counted_again_and_tail_ids_cover_the_overlap():
    trades = [_trade(1, NOW - 60_000), _trade(2, NOW + 1000)]
    got = es.fetch_trades(Pages(trades), NOW - es.OVERLAP_MS, NOW + 60_000, seen_ids=["1"])
    assert [t["trade_id"] for t in got["trades"]] == ["2"]
    assert got["tail_ids"] == ["1", "2"]


def test_truncated_after_max_pages(monkeypatch):
    monkeypatch.setattr(es, "PAGE_COUNT", 2)
    monkeypatch.setattr(es, "MAX_PAGES", 2)
    got = es.fetch_trades(Pages([_trade(i, NOW + i * 1000) for i in range(10)]), NOW, NOW + 60_000)
    assert got["truncated"] is True and got["covered_to_ms"] == NOW + 2000 and got["pages"] == 2


def test_stuck_page_of_one_millisecond_is_truncated_not_looped(monkeypatch):
    monkeypatch.setattr(es, "PAGE_COUNT", 2)
    got = es.fetch_trades(Pages([_trade(i, NOW) for i in range(5)]), NOW, NOW + 60_000)
    assert got["truncated"] is True and got["pages"] == 1 and got["covered_to_ms"] == NOW


def test_malformed_page_raises_and_malformed_trade_is_skipped():
    with pytest.raises(ValueError):
        es.fetch_trades(lambda s, e, c: {"trades": "nope"}, NOW, NOW + 1)
    with pytest.raises(ValueError):
        es.fetch_trades(lambda s, e, c: {"trades": []}, NOW, NOW + 1)          # has_more missing
    bad = [_trade(1, NOW), {"trade_id": "2", "timestamp": NOW, "instrument_name": "BTC-12NOV26-73000-C"},
           _trade(3, NOW, direction="sideways"), _trade(4, NOW, name="BTC-PERPETUAL")]
    got = es.fetch_trades(Pages(bad), NOW, NOW + 1)
    assert [t["trade_id"] for t in got["trades"]] == ["1"] and got["skipped"] == 3


def test_timeout_propagates_to_the_caller():
    with pytest.raises(socket.timeout):
        es.fetch_trades(Pages([], fail=socket.timeout("slow")), NOW, NOW + 1)


def test_flow_window_reports_a_gap_beyond_the_endpoints_history():
    assert es.flow_window(NOW - 10 * 60_000, NOW) == (NOW - 15 * 60_000, None)
    start, gap = es.flow_window(NOW - 30 * H, NOW)
    assert gap == NOW - 30 * H and start == NOW - 24 * H + es.OVERLAP_MS


def test_aggregate_by_expiry_strike_and_class():
    trades = [_trade(1, NOW, amount=2.0), _trade(2, NOW, direction="sell", amount=0.5),
              _trade(3, NOW, name="BTC-12NOV26-73000-P", direction="sell", amount=1.0),
              _trade(4, NOW, amount=25.0, block_trade_id="BLOCK-1", combo_id="BTC-STRG-x"),
              _trade(5, NOW, direction="sell", amount=3.0, combo_id="BTC-CS-x", combo_trade_id="9"),
              _trade(6, NOW, name="BTC-13NOV26-72000-C", amount=1.0),
              _trade(7, NOW, name="BTC-19NOV26-72000-C", amount=1.0)]
    out = es.aggregate(trades, [E1, E2])
    assert set(out) == {E1, E2}
    call = next(r for r in out[E1] if r["k"] == 73000.0 and r["kind"] == "call")
    assert (call["buy_btc"], call["sell_btc"], call["n"]) == (2.0, 0.5, 2)
    assert (call["block_buy_btc"], call["n_block"], call["combo_sell_btc"], call["n_combo"]) == (25.0, 1, 3.0, 1)
    assert call["max_trade_btc"] == 25.0
    put = next(r for r in out[E1] if r["kind"] == "put")
    assert (put["buy_btc"], put["sell_btc"]) == (0.0, 1.0)
    assert [r["k"] for r in out[E2]] == [72000.0]
    assert es.aggregate([], [E1]) == {E1: []}


# ------------------------------------------------------------ readings

def _snap(at, c, p, k=73000.0):
    return {"at_ms": at, "strikes": [{"k": k, "c_oi": c, "p_oi": p}]}


def test_oi_change_and_gaps():
    snaps = [_snap(NOW - 24 * H, 100.0, 50.0), _snap(NOW - 6 * H - 5 * 60_000, 120.0, 40.0), _snap(NOW, 150.0, 45.0)]
    assert es.oi_change(snaps, 73000.0, NOW, 6 * H) == {"c": 30.0, "p": 5.0, "gap": False}
    assert es.oi_change(snaps, 73000.0, NOW, 24 * H) == {"c": 50.0, "p": -5.0, "gap": False}
    assert es.oi_change(snaps, 73000.0, NOW, 12 * H)["gap"] is True          # nothing within 40 min of 12h before
    assert es.oi_change(snaps, 73000.0, NOW + 2 * H, 6 * H)["gap"] is True    # no recent snapshot
    assert es.oi_change(snaps, 99000.0, NOW, 6 * H) == {"c": None, "p": None, "gap": False}
    # a snapshot after the reading time is never used
    assert es.oi_change(snaps + [_snap(NOW + 60_000, 999.0, 999.0)], 73000.0, NOW, 6 * H)["c"] == 30.0


def _flow(f, t, cells, **kw):
    return {"from_ms": f, "to_ms": t, "rows": cells, "truncated": kw.get("truncated", False),
            "gap_from_ms": kw.get("gap_from_ms")}


def test_flow_sum_covers_window_and_flags_gaps_and_truncation():
    cell = {"k": 73000.0, "kind": "call", "buy_btc": 1.0, "sell_btc": 2.0, "n": 3, "block_buy_btc": 5.0}
    step = 10 * 60_000
    rows = [_flow(NOW - 60 * 60_000 + i * step, NOW - 60 * 60_000 + (i + 1) * step, [cell]) for i in range(6)]
    out = es.flow_sum(rows, 73000.0, NOW - H, NOW)
    assert (out["c_buy"], out["c_sell"], out["c_block_buy"], out["n_c"], out["gap"], out["truncated"]) == \
        (6.0, 12.0, 30.0, 18, False, False)
    assert out["p_buy"] == 0.0
    assert es.flow_sum(rows[:2] + rows[3:], 73000.0, NOW - H, NOW)["gap"] is True        # a missing tick
    assert es.flow_sum(rows[2:], 73000.0, NOW - H, NOW)["gap"] is True                    # starts late
    assert es.flow_sum([], 73000.0, NOW - H, NOW)["gap"] is True
    rows[3]["truncated"] = True
    assert es.flow_sum(rows, 73000.0, NOW - H, NOW)["truncated"] is True
    rows[3]["gap_from_ms"] = NOW - 30 * H
    assert es.flow_sum(rows, 73000.0, NOW - H, NOW)["gap"] is True


# ------------------------------------------------------------ archive tick

class FakeDeribit:
    def __init__(self, rows=None, trades=(), summary_fail=None, trades_fail=None):
        self.rows, self.trades, self.summary_fail, self.trades_fail = rows, list(trades), summary_fail, trades_fail
        self.calls = []

    def __call__(self, path, params):
        self.calls.append(path)
        if path == "public/get_book_summary_by_currency":
            if self.summary_fail:
                raise self.summary_fail
            return self.rows if self.rows is not None else _rows()
        if path == "public/get_last_trades_by_currency_and_time":
            if self.trades_fail:
                raise self.trades_fail
            return Pages(self.trades)(params["start_timestamp"], params["end_timestamp"], params["count"])
        if path == "public/get_index_chart_data":
            return []
        if path == "public/get_delivery_prices":
            return {"data": []}
        raise AssertionError(path)


def _tick(archive, now, fake):
    return ar.tick(archive, now_ms=now, view=None, fetch_case=lambda s: {}, deribit=ar.Deribit(fake))


def _lines(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_tick_writes_snapshots_and_flow_rows_into_the_chain(tmp_path):
    archive = ar.Archive(tmp_path)
    fake = FakeDeribit(trades=[_trade(1, NOW + 5 * 60_000, amount=2.0), _trade(2, NOW + 6 * 60_000, direction="sell")])
    r1 = _tick(archive, NOW, fake)                      # first run: snapshots, cursor set, no flow fetch
    assert r1["structure"] == 2 and r1["flow"] == 0
    assert "public/get_last_trades_by_currency_and_time" not in fake.calls
    r2 = _tick(archive, NOW + 10 * 60_000, fake)        # not due for snapshots (30 min); flow written
    assert r2["structure"] == 0 and r2["flow"] == 2
    r3 = _tick(archive, NOW + 20 * 60_000, fake)        # the overlap re-reads both trades: not counted again
    assert r3["flow"] == 2
    flow = _lines(tmp_path / "flow" / "2026-11-12.jsonl")
    assert [f["from_ms"] for f in flow] == [NOW, NOW + 10 * 60_000]
    first = flow[0]["rows"]
    assert first == [{"k": 73000.0, "kind": "call", "buy_btc": 2.0, "sell_btc": 1.0, "n": 2, "block_buy_btc": 0.0,
                      "block_sell_btc": 0.0, "n_block": 0, "combo_buy_btc": 0.0, "combo_sell_btc": 0.0,
                      "n_combo": 0, "max_trade_btc": 2.0}]
    assert flow[1]["rows"] == []                         # an empty row: looked, nothing traded
    assert len(_lines(tmp_path / "structure" / "2026-11-12.jsonl")) == 1
    assert len(_lines(tmp_path / "flow" / "2026-11-13.jsonl")) == 2
    assert not (tmp_path / "structure" / "2026-11-19.jsonl").exists()
    assert archive.verify()["ok"]
    kinds = {json.loads(l)["kind"] for l in (tmp_path / "chain.jsonl").read_text(encoding="utf-8").splitlines()}
    assert {"structure", "flow"} <= kinds


def test_tampered_structure_line_is_detected(tmp_path):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW, FakeDeribit())
    p = tmp_path / "structure" / "2026-11-12.jsonl"
    p.write_text(p.read_text(encoding="utf-8").replace("12NOV26", "13NOV26"), encoding="utf-8")
    assert not archive.verify()["ok"]


@pytest.mark.parametrize("fake", [
    FakeDeribit(summary_fail=socket.timeout("slow"), trades_fail=socket.timeout("slow")),
    FakeDeribit(rows=[], trades=[]),
    FakeDeribit(rows=[{"instrument_name": None}, "junk"]),
])
def test_failures_are_reported_and_the_cursor_holds(tmp_path, fake):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW, fake)
    report = _tick(archive, NOW + 10 * 60_000, fake)
    state = archive.load_state()
    assert archive.verify()["ok"]
    if fake.trades_fail:
        assert {"structure", "flow"} <= {e["step"] for e in report["errors"]}
        assert state["flow_cursor_ms"] == NOW
    else:
        assert report.get("structure") == 0


def test_malformed_summary_is_an_error_not_a_crash(tmp_path):
    archive = ar.Archive(tmp_path)
    report = _tick(archive, NOW, FakeDeribit(rows={"not": "a list"}))
    assert "structure" in [e["step"] for e in report["errors"]]
    assert archive.load_state()["flow_cursor_ms"] == NOW     # flow still ran


def test_outage_beyond_history_is_marked_as_a_gap(tmp_path):
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW - 30 * H, FakeDeribit(rows=[]))
    _tick(archive, NOW, FakeDeribit())
    row = _lines(tmp_path / "flow" / "2026-11-12.jsonl")[0]
    assert row["gap_from_ms"] == NOW - 30 * H and row["from_ms"] == NOW - 30 * H


def test_ledger_and_outcomes_unaffected_by_collection_failure(tmp_path):
    import test_astra_research_archive_v1 as base
    archive = ar.Archive(tmp_path)
    inner = base.FakeDeribit()

    def transport(path, params):
        if path in ("public/get_book_summary_by_currency", "public/get_last_trades_by_currency_and_time"):
            raise socket.timeout("slow")
        return inner(path, params)

    asked = []
    r = ar.tick(archive, now_ms=base.NOW + 60_000, view=base._view(), fetch_case=base._case,
                deribit=ar.Deribit(transport), run_inquiry=lambda case: asked.append(1) or {"status": "OK", "sheet": {}})
    assert r["cases"] == 1 and r["rows"] == 1 and r["inquiry"] == 1 and asked == [1]
    assert {"structure"} <= {e["step"] for e in r["errors"]}
    minutes = [[base.NOW + i * 60_000, 81900.0 - i * 2.0] for i in range(0, 18 * 60 + 1)]
    inner = base.FakeDeribit(delivery=81000.0, path=minutes)
    r2 = ar.tick(archive, now_ms=base.EXPIRY + 15 * 60_000, view=None, fetch_case=base._case,
                 deribit=ar.Deribit(transport))
    assert r2["outcomes"] == 1 and {"structure", "flow"} <= {e["step"] for e in r2["errors"]}
    assert archive.verify()["ok"]


def test_endpoints_are_whitelisted_and_versions_observed():
    assert "public/get_book_summary_by_currency" in ar.PUBLIC_PATHS
    assert "public/get_last_trades_by_currency_and_time" in ar.PUBLIC_PATHS
    assert ar.observed_versions()["expiry_structure"] == "structure_snapshot@0.1+strike_flow@0.1"


def test_written_lines_match_their_schemas(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    schemas = Path(__file__).resolve().parents[1] / "deploy" / "research_archive" / "schemas"
    archive = ar.Archive(tmp_path)
    fake = FakeDeribit(trades=[_trade(1, NOW + 60_000), _trade(2, NOW + 60_000, block_trade_id="BLOCK-1")])
    _tick(archive, NOW, fake)
    _tick(archive, NOW + 10 * 60_000, fake)
    for name, rel in (("structure_snapshot_0.1", "structure/2026-11-12.jsonl"), ("strike_flow_0.1", "flow/2026-11-12.jsonl")):
        schema = json.loads((schemas / f"{name}.schema.json").read_text(encoding="utf-8"))
        for line in _lines(tmp_path / rel):
            jsonschema.validate(line, schema)


# ------------------------------------------------------------ review fixes (2026-11-11)

def test_the_settling_expiry_gets_its_last_minutes_up_to_expiry(tmp_path):
    archive = ar.Archive(tmp_path)
    before = E1 - 10 * 60_000 + 20_000                 # the 07:50 tick
    fake = FakeDeribit(trades=[_trade(1, E1 - 3 * 60_000, direction="sell", amount=5.0),
                               _trade(2, E1 + 10_000, name="BTC-13NOV26-73000-C")])
    _tick(archive, before - 10 * 60_000, fake)
    _tick(archive, before, fake)
    fake.rows = [r for r in _rows() if "12NOV26" not in r["instrument_name"]]   # settled: gone from the summary
    _tick(archive, E1 + 20_000, fake)                  # the 08:00 tick
    rows = _lines(tmp_path / "flow" / "2026-11-12.jsonl")
    assert rows[-1]["to_ms"] == E1 and rows[-1]["from_ms"] == before
    assert rows[-1]["rows"][0]["sell_btc"] == 5.0
    assert es.flow_sum(rows, 73000.0, E1 - 20 * 60_000, E1)["gap"] is False
    assert _lines(tmp_path / "flow" / "2026-11-13.jsonl")[-1]["rows"][0]["buy_btc"] == 1.0
    _tick(archive, E1 + 10 * 60_000 + 20_000, fake)    # nothing more for the settled expiry
    assert len(_lines(tmp_path / "flow" / "2026-11-12.jsonl")) == len(rows)
    assert str(E1) not in archive.load_state()["flow_written"]


def test_a_crash_between_lines_does_not_write_a_window_twice(tmp_path, monkeypatch):
    archive = ar.Archive(tmp_path)
    fake = FakeDeribit(trades=[_trade(1, NOW + 5 * 60_000), _trade(2, NOW + 5 * 60_000, name="BTC-13NOV26-73000-C")])
    _tick(archive, NOW, fake)
    real, calls = archive.append_line, []

    def flaky(rel, row, **kw):
        if rel.startswith("flow/2026-11-13"):
            calls.append(rel)
            raise OSError("disk")
        return real(rel, row, **kw)

    monkeypatch.setattr(archive, "append_line", flaky)
    report = _tick(archive, NOW + 10 * 60_000, fake)
    assert "flow" in [e["step"] for e in report["errors"]] and calls
    monkeypatch.setattr(archive, "append_line", real)
    _tick(archive, NOW + 20 * 60_000, fake)
    e1 = _lines(tmp_path / "flow" / "2026-11-12.jsonl")
    e2 = _lines(tmp_path / "flow" / "2026-11-13.jsonl")
    assert [(r["from_ms"], r["to_ms"]) for r in e1] == [(NOW, NOW + 10 * 60_000), (NOW + 10 * 60_000, NOW + 20 * 60_000)]
    assert [(r["from_ms"], r["to_ms"]) for r in e2] == [(NOW, NOW + 20 * 60_000)]
    assert es.flow_sum(e1, 73000.0, NOW, NOW + 20 * 60_000)["c_buy"] == 1.0
    assert e2[0]["rows"][0]["buy_btc"] == 1.0
    assert archive.verify()["ok"]


def test_a_row_reaching_far_before_the_window_or_overlapping_is_a_gap():
    cell = {"k": 73000.0, "kind": "call", "buy_btc": 50.0}
    wide = [_flow(NOW - 3 * H, NOW, [cell])]
    assert es.flow_sum(wide, 73000.0, NOW - 30 * 60_000, NOW)["gap"] is True
    overlap = [_flow(NOW - H, NOW - 20 * 60_000, [cell]), _flow(NOW - 30 * 60_000, NOW, [cell])]
    assert es.flow_sum(overlap, 73000.0, NOW - H, NOW)["gap"] is True


def test_a_stalled_cursor_is_reported_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(es, "PAGE_COUNT", 2)
    archive = ar.Archive(tmp_path)
    _tick(archive, NOW, FakeDeribit())
    stuck = FakeDeribit(trades=[_trade(i, NOW - 5 * 60_000) for i in range(5)])   # one millisecond, more than a page
    report = _tick(archive, NOW + 10 * 60_000, stuck)
    assert "flow" in [e["step"] for e in report["errors"]]
    assert archive.load_state()["flow_cursor_ms"] == NOW
    assert not (tmp_path / "flow").exists()


def test_fetch_stops_at_its_time_budget(monkeypatch):
    monkeypatch.setattr(es, "PAGE_COUNT", 2)
    monkeypatch.setattr(es, "MAX_FETCH_S", 0.0)
    got = es.fetch_trades(Pages([_trade(i, NOW + i * 1000) for i in range(10)]), NOW, NOW + 60_000)
    assert got["truncated"] is True and got["pages"] == 1


def test_collection_requests_use_the_short_timeout():
    assert ar.COLLECT_PATHS <= ar.PUBLIC_PATHS and es.REQUEST_TIMEOUT_S < 15


def test_gap_tolerance_matches_the_scorecards():
    import astra_research_scorecards_v1 as sc
    assert es.GAP_MS == sc.ENTRY_LOOKBACK_MS
