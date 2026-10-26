"""Decision Path: this-expiry walls, step readings, verification sessions and the lock conclusion."""
import json
import math
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from tools import astra_decision_path_store_v1 as store
from tools import astra_decision_path_v1 as dp
from tools import astra_expiry_gamma_v1 as eg
from tools import astra_workbench_v20 as workbench
from test_astra_workbench_v20 import make_workbench, write_fmz, native_gate, T

EXPIRY = 1792051200000          # 15OCT26 08:00 UTC
NOW = EXPIRY - 21 * 3_600_000


def row(name, oi, iv=40.0, fwd=83_300.0):
    return {"instrument_name": name, "open_interest": oi, "mark_iv": iv, "underlying_price": fwd}


ROWS = [row("BTC-15OCT26-83000-P", 170), row("BTC-15OCT26-80000-P", 300), row("BTC-15OCT26-83500-C", 120),
        row("BTC-15OCT26-85000-C", 200), row("BTC-15OCT26-83000-C", 10), row("BTC-16OCT26-83000-C", 999),
        row("BTC-15OCT26-81000-P", 0), {"instrument_name": "garbage", "open_interest": 5}]


# ------------------------------------------------------------------ expiry walls

def test_parse_name_and_code():
    meta = eg.parse_name("BTC-15OCT26-83000-C")
    assert meta == {"code": "15OCT26", "expiry_ms": EXPIRY, "strike": 83000.0, "kind": "call"}
    assert eg.parse_name("BTC-PERPETUAL") is None and eg.parse_name("BTC-31FEB26-1-C") is None
    assert eg.expiry_code(EXPIRY) == "15OCT26"


def test_profile_separates_gamma_and_oi_walls():
    p = eg.profile(ROWS, expiry_ms=EXPIRY, now_ms=NOW)
    walls = p["walls"]
    # gamma concentrates at the money; open interest stacks further out
    assert walls["gamma_put"] == 83000 and walls["gamma_call"] == 83500
    assert walls["oi_put"] == 80000 and walls["oi_call"] == 85000
    assert walls["pin"] == 83000
    assert p["expiry"] == "15OCT26" and p["strike_count"] == 5     # other expiry and bad rows ignored
    assert p["totals"]["put_oi_btc"] == 470.0
    assert math.isclose(p["hours_to_expiry"], 21.0)


def test_realized_vol_needs_complete_windows():
    hour = 3_600_000
    start = NOW - 200 * hour
    ticks = [start + i * hour for i in range(200)]
    closes = [80_000 * (1.001 if i % 2 else 0.999) for i in range(200)]
    out = eg.realized_vol({"ticks": ticks, "close": closes}, now_ms=NOW)
    assert out["rv_24h_pct"] > 0 and out["rv_7d_pct"] > 0
    del ticks[-5], closes[-5]   # a missing bar must not stretch the window silently
    out = eg.realized_vol({"ticks": ticks, "close": closes}, now_ms=NOW)
    assert out["rv_24h_pct"] is None and out["rv_7d_pct"] is None


def test_collect_and_due_and_staleness():
    def get_json(url, params):
        if "book_summary" in url:
            return {"result": ROWS}
        raise OSError("down")
    res = eg.collect(get_json, NOW)
    assert res["status"] == "ok" and "15OCT26" in res["expiries"]
    assert res["realized_vol"]["status"] == "fetch_failed"
    assert eg.collect(lambda u, p: (_ for _ in ()).throw(OSError()), NOW)["status"] == "fetch_failed"
    assert not eg.due(res, NOW + 60_000) and eg.due(res, NOW + eg.REFRESH_MS)
    assert eg.due({"status": "fetch_failed", "computed_at_ms": NOW}, NOW + eg.RETRY_MS)
    assert eg.for_expiry(res, EXPIRY, now_ms=NOW + 60_000)["stale"] is False
    assert eg.for_expiry(res, EXPIRY, now_ms=NOW + eg.STALE_MS + 1)["stale"] is True
    assert eg.for_expiry(res, EXPIRY + 86_400_000 * 30, now_ms=NOW) is None


# ------------------------------------------------------------------ step readings

def candidate(side="put", spot=83_345.0, short=82_500.0, long_=80_500.0):
    return {
        "side": side,
        "snapshot": {
            "snapshot_id": "snap1", "candidate_id": "cand1", "gap_reasons": ["frozen_risk_reference_missing"],
            "contract": {"side": side, "short_strike_usd": short, "long_strike_usd": long_, "expiry_ms": EXPIRY,
                         "short_instrument": f"BTC-15OCT26-{int(short)}-{'P' if side == 'put' else 'C'}",
                         "long_instrument": "x", "quantity_btc": 1.0, "width_usd": 2000.0},
            "market": {"reference_price_usd": spot, "valuation_ts_ms": NOW - 10_000,
                       "quote": {"gap_reasons": [], "available_depth_btc": {"short": 10, "long": 10},
                                 "kind": "sequential_leg_book_estimate"}},
            "economics": {"net_credit_after_entry_fee_usd_at_spot": 160.86, "net_credit_after_entry_fee_btc": 0.00193,
                          "visible_credit_btc": 0.00233},
        },
        "exogenous_events": {"events": [{"name_cn": "CPI", "at_ms": NOW + 3_600_000, "to_expiry_ms": 20 * 3_600_000}]},
        "spatial_position": {"state": "unfavorable", "reason_cn": "先遇到负 Gamma 墙", "gamma": {"regime_at_short": "negative"}},
        "decision_reduction": {"questions": [
            {"key": "market_price", "numbers": {"edge_usd": -64.18, "market_payout_usd": 225.03}},
            {"key": "worst_case", "numbers": {"loss_at_protection_btc": 0.0229, "loss_at_protection_usd": 1909.8}}]},
    }


def facts(t24="mild_bearish_bias", t48="mild_bearish_bias", regime="NEGATIVE_GAMMA_AMPLIFYING", macro="Mild Headwind"):
    return {"volume_price": {"tmvf_24h": {"data_ready": True, "final_state": t24},
                             "tmvf_48h": {"data_ready": True, "final_state": t48},
                             "micro_flow": {"combined": {"data_ready": True, "direction": "bearish"}}},
            "m_die": {"direction": "DOWN", "level": "MOVE"}, "gamma_regime": {"regime": regime},
            "macro_pressure": {"macro_regime": macro},
            "space_context": {"anchor": {"effective_flip_point": 83_355.0}}}


def frame(cand=None, **kw):
    walls = eg.for_expiry(eg.collect(lambda u, p: {"result": ROWS} if "book" in u else {"result": {}}, NOW),
                          EXPIRY, now_ms=NOW)
    return {"at_ms": NOW, "candidate": cand or candidate(), "market_facts": facts(**kw), "fmz_status": "current",
            "expiry_walls": walls, "realized_vol": {"rv_24h_pct": 20.0, "rv_7d_pct": 30.0},
            "cross_window": {"covers_us_session": True},
            "transitions": [{"transition_ts_ms": NOW - 3_600_000, "previous_state": "ANCHOR_EFFECTIVE",
                             "current_state": "ANCHOR_INEFFECTIVE"}]}


def test_put_reading_order_and_vocabulary():
    r = dp.build(frame())
    steps = r["steps"]
    assert list(steps) == list(dp.STEP_IDS) and r["path_version"] == dp.PATH_VERSION
    assert steps["D0"]["verdict"]["code"] == "readable"
    assert steps["D2"]["verdict"]["code"] in {"narrow", "standard"}
    assert "本期 Put Gamma 墙 83,000" in json.dumps(steps["D2"], ensure_ascii=False)
    # v2 (docs/opus/20): direction is one same-source line; the veto needs >12h left, a narrow buffer
    # and a move toward the short leg
    assert steps["D3"]["verdict"]["code"] == "toward_early"          # 21h left and the last 15 minutes move down
    assert "同源，不计票" in steps["D3"]["lines"][0]["k"]
    assert steps["D4"]["listed"] == ["event"]                  # macro direction is no longer read
    assert steps["D2"]["transitions_24h"] == 1
    q1 = steps["Q1"]
    assert [i["step"] for i in q1["items"]] == sorted((i["step"] for i in q1["items"]), key=dp.STEP_IDS.index)
    assert steps["Q2"]["verdict"]["code"] == "below_market"
    assert "近 24 小时实现 20.0%" in json.dumps(steps["Q2"], ensure_ascii=False)
    assert steps["H"]["verdict"]["code"] == "plan"


def test_no_premium_before_q2():
    text = json.dumps({k: v for k, v in dp.build(frame())["steps"].items() if k not in {"Q2", "AU"}}, ensure_ascii=False)
    for premium in ("161", "0.0019", "0.0023", "225", "64", "BTC）", "净收"):
        assert premium not in text


def test_call_side_macro_filter_and_stops():
    call = candidate("call", short=84_000.0, long_=86_000.0)
    steps = dp.build(frame(call, t24="bullish_bias", t48="bearish_bias", macro="Mild Headwind"))["steps"]
    assert steps["D4"]["listed"] == ["event"]
    expired = frame()
    expired["at_ms"] = EXPIRY + 1
    assert dp.build(expired)["steps"]["D0"]["verdict"]["code"] == "stop"
    no_walls = frame()
    no_walls["expiry_walls"] = None
    d0 = dp.build(no_walls)["steps"]["D0"]
    assert d0["verdict"]["code"] == "partial" and "walls" in d0["gaps"]


def test_compare_lists_changed_verdicts():
    a = dp.build(frame())
    moved = candidate(spot=82_600.0)
    b = dp.build(frame(moved))
    changes = dp.compare(a, b)
    assert any(c["step"] == "D2" for c in changes) and any(c["step"] == "spot" for c in changes)


# ------------------------------------------------------------------ store

def test_store_session_lifecycle(tmp_path):
    s = store.DecisionPathStore(tmp_path)
    reading = dp.build(frame())
    session, created = s.start(candidate=candidate(), reading=reading, now_ms=NOW)
    vid = session["verification_id"]
    assert created and session["status"] == "open" and session["entry_after_fee_btc"] == 0.00193
    again, created = s.start(candidate=candidate(), reading=reading, now_ms=NOW + 1)
    assert not created and again["verification_id"] == vid              # continue, not a second session
    s.step(vid, "D0", lean=None, now_ms=NOW + 1000)
    s.step(vid, "D2", lean="toward_decline", now_ms=NOW + 5000)
    with pytest.raises(store.DecisionPathError):
        s.step(vid, "D9", lean=None, now_ms=NOW)
    assert s.conclude(vid, "watch", now_ms=NOW + 6000)["status"] == "open"
    closed = s.conclude(vid, "decline", now_ms=NOW + 9000)
    assert closed["status"] == "closed" and closed["conclusion"]["choice"] == "decline"
    with pytest.raises(store.DecisionPathError):
        s.step(vid, "D3", lean=None, now_ms=NOW)
    rec = s.records()[-1]
    assert rec["event"] == "concluded" and rec["marks"]["D2"] == "toward_decline" and rec["settled_after"] == "D2"
    assert rec["dwell_ms"] == {"D0": 4000, "D2": 4000} and rec["decisions"][0]["choice"] == "watch"
    # a fresh store reads the same index from disk
    assert store.DecisionPathStore(tmp_path).summaries()[0]["conclusion"] == "decline"


def test_store_expires_open_sessions_and_settles(tmp_path):
    s = store.DecisionPathStore(tmp_path)
    vid = s.start(candidate=candidate(), reading=dp.build(frame()), now_ms=NOW)[0]["verification_id"]
    assert s.expire_due(EXPIRY - 1) == []
    assert s.expire_due(EXPIRY) == [vid]
    assert s.get(vid)["conclusion"]["choice"] == "unconcluded"
    assert [i["verification_id"] for i in s.unsettled_after(EXPIRY + 120_000, delay_ms=120_000)] == [vid]
    s.settle(vid, {"status": "settled", "result_btc": 0.001}, now_ms=EXPIRY + 200_000)
    assert s.unsettled_after(EXPIRY + 300_000, delay_ms=0) == []
    assert [r["event"] for r in s.records()] == ["expired_open", "settled"]


def test_outcome_breach():
    c = {"side": "put", "short_strike_usd": 82_500.0}
    assert dp.outcome(c, 0.002, 0.0, 83_000.0)["short_breached"] is False
    assert dp.outcome(c, 0.002, 0.01, 82_000.0)["short_breached"] is True
    assert dp.outcome(c, None, 0.0, 83_000.0) is None


# ------------------------------------------------------------------ workbench

def setup_wb(tmp_path):
    clock = [T]
    wb, snapshots = make_workbench(tmp_path, clock=lambda: clock[0])
    write_fmz(snapshots, native_gate())
    view = wb.refresh(now_ms=T, record_daily=False)
    return wb, clock, view["candidates"][0]["snapshot"]


def test_verify_then_lock_and_direct_lock_coexist(tmp_path):
    wb, clock, snap = setup_wb(tmp_path)
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.decision_path_start(snap["candidate_id"], snap["snapshot_id"], authorization="Bearer wrong")
    assert exc.value.code == "unauthorized"
    out = wb.decision_path_start(snap["candidate_id"], snap["snapshot_id"], authorization="Bearer secret")
    vid = out["session"]["verification_id"]
    assert out["created"] and out["llm_call_count"] == 0 and out["execution_allowed"] is False
    assert set(out["session"]["readings"]["start"]["steps"]) == set(dp.STEP_IDS)
    overview = wb.cached_view()["decision_path"]
    assert overview["open"][snap["candidate_id"]]["verification_id"] == vid
    assert wb.decision_path_start(snap["candidate_id"], snap["snapshot_id"],
                                  authorization="Bearer secret")["created"] is False
    refreshed = wb.decision_path_refresh(vid, authorization="Bearer secret")["session"]
    assert refreshed["readings"]["now"] is not None
    wb.decision_path_step(vid, "D2", "toward_underwrite", authorization="Bearer secret")
    done = wb.decision_path_conclude(vid, "lock", "dp-lock-0001", authorization="Bearer secret")
    episode_id = done["session"]["conclusion"]["episode_id"]
    assert episode_id and done["lock"]["created"]
    listed = {e["episode_id"]: e for e in wb.locked_candidate_list()["episodes"]}
    assert listed[episode_id]["verification_id"] == vid
    # the direct lock entry still works and reports the same contract as already tracked
    direct = wb.lock_candidate(snap["candidate_id"], snap["snapshot_id"], "direct-lock-01",
                               authorization="Bearer secret")
    assert direct["episode"]["episode_id"] == episode_id
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.decision_path_conclude(vid, "decline", "", authorization="Bearer secret")
    assert exc.value.code == "verification_closed"


def test_start_requires_the_displayed_policy(tmp_path):
    wb, _, snap = setup_wb(tmp_path)
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.decision_path_start(snap["candidate_id"], "not_shown_snapshot", authorization="Bearer secret")
    assert exc.value.code == "candidate_not_current"


def test_http_routes(tmp_path):
    wb, _, snap = setup_wb(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), workbench.make_handler(wb))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/api/workbench/decision-path"

    def post(url, body):
        req = Request(url, data=json.dumps(body).encode(), method="POST",
                      headers={"Authorization": "Bearer secret", "Content-Type": "application/json"})
        return json.loads(urlopen(req, timeout=20).read())
    try:
        out = post(base, {"candidate_id": snap["candidate_id"], "snapshot_id": snap["snapshot_id"]})
        vid = out["session"]["verification_id"]
        post(f"{base}/{vid}/step", {"step": "D0"})
        post(f"{base}/{vid}/conclude", {"choice": "decline"})
        got = json.loads(urlopen(f"{base}/{vid}", timeout=20).read())
        assert got["status"] == "closed" and "D0" in got["opened"]
        assert json.loads(urlopen(base, timeout=20).read())["path_version"] == dp.PATH_VERSION
    finally:
        server.shutdown()


# ------------------------------------------------------------------ offline report

def test_report_joins_settlement_and_flags_thin_steps(tmp_path):
    from tools import astra_decision_path_report as rep
    s = store.DecisionPathStore(tmp_path)
    for i in range(9):
        vid = s.start(candidate={**candidate(), "snapshot": {**candidate()["snapshot"], "candidate_id": f"c{i}"}},
                      reading=dp.build(frame()), now_ms=NOW + i)[0]["verification_id"]
        if i < 3:
            s.step(vid, "D2", lean="toward_decline", now_ms=NOW + 10)
        s.conclude(vid, "decline" if i % 2 else "lock", now_ms=NOW + 20)
        s.settle(vid, {"status": "settled", "result_usd": 100.0 - i * 30, "short_breached": i > 5}, now_ms=EXPIRY)
    result = rep.report(rep.join(rep.load(tmp_path / "records.jsonl")))
    r = result[dp.PATH_VERSION]
    assert r["sessions"] == 9 and r["settled"] == 9
    assert abs(r["steps"]["D2"]["change_rate"] - 3 / 9) < 1e-9 and not r["steps"]["D2"]["thinning_candidate"]
    assert r["steps"]["D1"]["change_rate"] == 0 and r["steps"]["D1"]["thinning_candidate"]
    assert r["conclusion_source"]["D2"] == 3
    assert "| D2 |" in rep.render(result)


# ------------------------------------------------------------------ review fixes

def test_calendar_gaps_and_fading_and_weak_leans():
    cand = candidate()
    cand["exogenous_events"] = {"events": [], "gaps": [{"undated": True, "name_cn": "CPI"}]}
    steps = dp.build(frame(cand, t24="neutral", t48="bearish_bias", macro="Neutral"))["steps"]
    assert steps["D4"]["verdict"]["code"] == "calendar_partial"
    weak = dp.build(frame())["steps"]["D3"]["lines"][0]
    assert weak["v"].startswith("量价 24h 朝责任区（弱）") and weak["tone"] == ""


def test_old_realized_vol_says_its_age():
    f = frame()
    f["realized_vol"] = {"rv_24h_pct": 20.0, "rv_7d_pct": 30.0, "last_bar_end_ms": NOW - 5 * 3_600_000}
    line = next(l for l in dp.build(f)["steps"]["Q2"]["lines"] if l["k"].startswith("隐含与实现波动"))
    assert "截至 5.0 小时前" in line["v"] and line["tone"] == "warn"


def test_conclusion_claim_and_pending_records(tmp_path, monkeypatch):
    s = store.DecisionPathStore(tmp_path)
    vid = s.start(candidate=candidate(), reading=dp.build(frame()), now_ms=NOW)[0]["verification_id"]
    s.claim(vid, now_ms=NOW)
    with pytest.raises(store.DecisionPathError) as exc:
        s.claim(vid, now_ms=NOW + 1000)
    assert exc.value.code == "verification_concluding"
    s.release(vid)
    monkeypatch.setattr(s, "_append_record", lambda row: False)    # records file not writable
    s.conclude(vid, "decline", now_ms=NOW + 2000)
    assert s.get(vid)["records_pending"] and s.records() == []
    monkeypatch.undo()
    s.expire_due(NOW + 3000)                                       # a later pass writes it
    assert [r["event"] for r in s.records()] == ["concluded"] and s.get(vid)["records_pending"] == []


def test_race_lock_and_decline(tmp_path):
    wb, _, snap = setup_wb(tmp_path)
    vid = wb.decision_path_start(snap["candidate_id"], snap["snapshot_id"], authorization="Bearer secret")["session"]["verification_id"]
    wb.decision_paths.claim(vid, now_ms=wb.clock_ms())              # another conclusion in flight
    with pytest.raises(workbench.WorkbenchError) as exc:
        wb.decision_path_conclude(vid, "lock", "dp-lock-0002", authorization="Bearer secret")
    assert exc.value.code == "verification_concluding"
    assert wb.locked_candidate_list()["episodes"] == []             # no orphan lock
    wb.decision_paths.release(vid)
    out = wb.decision_path_conclude(vid, "lock", "dp-lock-0003", authorization="Bearer secret")
    assert out["session"]["conclusion"]["lock_created"] is True


def test_expired_sessions_are_purged_after_settlement_and_links_survive(tmp_path):
    s = store.DecisionPathStore(tmp_path)
    vid = s.start(candidate=candidate(), reading=dp.build(frame()), now_ms=NOW)[0]["verification_id"]
    s.conclude(vid, "lock", now_ms=NOW + 1, episode_id="lce_" + "a" * 24, lock_created=True)
    assert [i["verification_id"] for i in s.summaries(now_ms=NOW + 2)] == [vid]
    assert s.summaries(now_ms=EXPIRY) == []                       # the list keeps only unexpired contracts
    s.settle(vid, {"status": "settled", "result_usd": 12.0}, now_ms=EXPIRY + 200_000)
    with pytest.raises(store.DecisionPathError):
        s.get(vid)                                                # deleted after settlement
    assert not list((tmp_path / "sessions").glob("dpv_*.json"))
    assert [r["event"] for r in s.records()] == ["concluded", "settled"]   # the report keeps everything
    assert store.DecisionPathStore(tmp_path).verification_for_episode() == {"lce_" + "a" * 24: vid}


def test_newest_first(tmp_path):
    s = store.DecisionPathStore(tmp_path)
    ids = []
    for i in range(3):
        c = {**candidate(), "snapshot": {**candidate()["snapshot"], "candidate_id": f"n{i}"}}
        ids.append(s.start(candidate=c, reading=dp.build(frame()), now_ms=NOW + i * 1000)[0]["verification_id"])
    assert [i["verification_id"] for i in s.summaries(now_ms=NOW)] == ids[::-1]


# ------------------------------------------------------------------ full audit fixes

def test_no_marks_or_conclusions_after_expiry(tmp_path):
    s = store.DecisionPathStore(tmp_path)
    vid = s.start(candidate=candidate(), reading=dp.build(frame()), now_ms=NOW)[0]["verification_id"]
    for call in (lambda: s.step(vid, "D0", lean=None, now_ms=EXPIRY + 60_000),
                 lambda: s.conclude(vid, "decline", now_ms=EXPIRY + 60_000)):
        with pytest.raises(store.DecisionPathError) as exc:
            call()
        assert exc.value.code == "contract_expired"
    s.step(vid, "D0", lean=None, now_ms=NOW + 1000)
    s.expire_due(EXPIRY + 6 * 3_600_000)                 # noticed six hours late
    assert s.get(vid)["conclusion"]["at_ms"] == EXPIRY
    assert s.records()[-1]["dwell_ms"]["D0"] == EXPIRY - NOW - 1000


def test_old_walls_leave_d2_and_bad_rows_are_skipped():
    f = frame()
    f["expiry_walls"] = {**f["expiry_walls"], "age_ms": 3 * 3_600_000, "stale": True}
    steps = dp.build(f)["steps"]
    assert "本期 Put" not in json.dumps(steps["D2"], ensure_ascii=False)
    assert steps["D0"]["verdict"]["code"] == "partial"
    res = eg.collect(lambda u, p: {"result": ROWS + [None, "x"]} if "book" in u else {"result": {}}, NOW)
    assert res["status"] == "ok"
    assert eg.for_expiry({"expiries": ["bad"]}, EXPIRY, now_ms=NOW) is None


def test_step_failure_reads_as_a_sentence():
    broken = frame()
    broken["candidate"] = {**candidate(), "exogenous_events": {"events": "not-a-list"}}
    d4 = dp.build(broken)["steps"]["D4"]
    assert "Error" not in json.dumps(d4["lines"], ensure_ascii=False)


# ------------------------------------------------------------------ path v2 (docs/opus/20)

H1 = 3_600_000


def clock(now=NOW, expiry=EXPIRY, hour=11, windows=None):
    return {"expiry_ms": expiry, "expiry_source": "contract", "hours_to_expiry": (expiry - now) / H1, "now_ms": now,
            "now_hour_utc": hour, "hour_ratio_now": 0.62, "phase": {"label_cn": "安静时段"},
            "remaining_clock_factor": 1.1,
            "windows": windows if windows is not None else [{"at_ms": now + 2.5 * H1, "in_hours": 2.5, "label_cn": "美股开盘"}]}


def v2frame(cand=None, warnings=None, notes=None, **kw):
    f = frame(cand)
    f["session_clock"] = clock(**kw)
    f["long_book_top"] = {"bid": 0.0004, "ask": 0.0008}
    if warnings is not None:
        f["candidate"] = {**f["candidate"], "entry_warnings": {"warnings": warnings, **({"notes": notes} if notes is not None else {})}}
    return f


def test_v2_steps_and_version():
    assert dp.STEP_IDS == ("D0", "D1", "T", "D2", "D3", "D4", "Q1", "Q2", "H", "AU")
    assert dp.PATH_VERSION == "dp-2026-10-26.3"


def test_time_step_veto_layer_goes_to_the_veto_summary_and_hours_are_described_only():
    steps = dp.build(v2frame(hour=9, warnings=[{"code": "premium_thickening", "label_cn": "保费突然变厚", "triggered": True},
                                                 {"code": "term", "label_cn": "期限倒挂", "triggered": False}]))["steps"]
    assert steps["T"]["verdict"]["code"] == "veto"
    assert [i["text"] for i in steps["Q1"]["items"] if i["step"] == "T"] == ["否决层触发 1 项"]
    assert [l["v"] for l in steps["T"]["lines"] if l["k"] == "否决层"] == ["保费突然变厚：触发", "期限倒挂：未触发"]
    hours = next(l for l in steps["T"]["lines"] if l["k"] == "建仓时点")
    assert "与 08:00 相近" in hours["v"] and hours["tone"] == ""          # 08-12 is not a veto (17 号 附录 A)
    better = dp.build(v2frame(hour=16, warnings=[{"code": "x", "triggered": False}], windows=[]))["steps"]["T"]
    assert better["verdict"]["code"] == "better_hours" and better["verdict"]["tone"] == ""
    assert dp.build(v2frame(hour=16, windows=[{"in_hours": 1.0, "at_ms": NOW + H1}]))["steps"]["T"]["verdict"]["code"] == "window_near"


BUSY_NOTE = {"code": "busy_window", "label_cn": "高波动时段提示", "role": "note", "active": True,
             "reason_cn": "美股开盘约 1.5 小时后。在这类时点按标准短腿建仓，2 小时内触及短腿约 35%（其他时点约 26%）；"
                          "这是持有中的盯盘提示，不是否决。"}
QUIET_VETOES = [{"code": "term_inversion", "label_cn": "近端倒挂", "triggered": False},
                {"code": "premium_thickening", "label_cn": "保费突然变厚", "triggered": False}]


def test_a_busy_window_is_a_holding_note_never_a_time_veto():
    # entry_warnings@0.2 (docs/opus/22): two veto lines, the note on its own line, no veto in T or Q1
    steps = dp.build(v2frame(hour=12, warnings=QUIET_VETOES, notes=[BUSY_NOTE]))["steps"]
    assert steps["T"]["verdict"]["code"] != "veto" and steps["T"]["fired"] == []
    assert [i for i in steps["Q1"]["items"] if i["step"] == "T"] == []
    assert len([l for l in steps["T"]["lines"] if l["k"] == "否决层"]) == 2
    note = next(l for l in steps["T"]["lines"] if l["k"] == "时段提示")
    assert note["v"] == BUSY_NOTE["reason_cn"] and note["tone"] == "warn" and "22 号" in note["basis"]
    off = dp.build(v2frame(hour=12, warnings=QUIET_VETOES, notes=[{**BUSY_NOTE, "active": False}]))["steps"]["T"]
    assert next(l for l in off["lines"] if l["k"] == "时段提示") == {"k": "时段提示", "v": "未触发", "tone": "",
                                                                     "basis": dp.B_BUSY_NOTE}
    assert "cushion" not in json.dumps(steps["T"], ensure_ascii=False) and not hasattr(dp, "B_THIN_CUSHION")


def test_a_legacy_busy_window_veto_reads_as_the_note():
    # an @0.1 block (cached view, saved session) whose busy-window item fired: shown as the note, not counted
    old = {"code": "busy_window_thin_cushion", "label_cn": "高波动窗口临近、缓冲不足", "triggered": True,
           "evidence": {"next_window": {"label_cn": "美股开盘", "in_hours": 1.5}, "busy_now": False, "distance_in_moves": 0.39}}
    steps = dp.build(v2frame(hour=12, warnings=QUIET_VETOES + [old]))["steps"]
    assert steps["T"]["verdict"]["code"] != "veto" and [i for i in steps["Q1"]["items"] if i["step"] == "T"] == []
    assert [l["v"] for l in steps["T"]["lines"] if l["k"] == "否决层"] == ["近端倒挂：未触发", "保费突然变厚：未触发"]
    note = next(l for l in steps["T"]["lines"] if l["k"] == "时段提示")
    assert note["v"].startswith("美股开盘约 1.5 小时后。") and "不是否决" in note["v"] and note["tone"] == "warn"
    assert dp.busy_note({"warnings": [dict(old, triggered=None, evidence={})]}) == {"active": None, "reason_cn": ""}


def test_the_holding_plan_cites_the_two_hour_touch_rate_before_the_next_window():
    f = v2frame(windows=[{"at_ms": NOW + 3 * H1, "in_hours": 3.0, "label_cn": "美股开盘"}])
    check = next(l for l in dp.build(f)["steps"]["H"]["lines"] if l["k"] == "检查点")
    assert "35%" in check["v"] and "26%" in check["v"] and "22 号" in check["basis"]


def test_no_green_outside_data_readability_and_no_veto_is_not_a_reason():
    for f in (v2frame(), v2frame(candidate("call", short=84_000.0, long_=86_000.0)), frame(candidate(spot=90_000.0))):
        steps = dp.build(f)["steps"]
        for sid, step in steps.items():
            if sid in {"D0", "AU"} or not isinstance(step, dict):
                continue
            assert (step.get("verdict") or {}).get("tone") != "ok", sid
            assert all(line.get("tone") != "ok" for line in step.get("lines") or []), sid
    far = dp.build(frame(candidate(spot=90_000.0)))["steps"]
    assert far["Q1"]["verdict"]["code"] == "no_veto" and "这不是做的理由" in far["Q1"]["verdict"]["cn"]


def test_x_grade_and_scope_readings_are_neutral():
    steps = dp.build(v2frame())["steps"]
    for step in steps.values():
        for line in (step or {}).get("lines") or []:
            if line.get("basis", "").startswith(("X", "口径")):
                assert line["tone"] == "", line


def test_holding_plan_quotes_path_statistics_only_on_their_basis():
    f = v2frame(now=EXPIRY - 18 * H1)
    f["at_ms"] = EXPIRY - 18 * H1
    h = dp.build(f)["steps"]["H"]
    text = json.dumps(h, ensure_ascii=False)
    assert "若到 16:00 UTC 仍未触及" in text and "之后触及约 30%" in text      # put side row
    assert "约一半到期越过短腿" in text and "不是平仓点" in text
    week = v2frame(expiry=EXPIRY + 2 * H1)
    assert "路径统计不适用" in json.dumps(dp.build(week)["steps"]["H"], ensure_ascii=False)


def test_price_step_reads_execution_and_the_protection_wing():
    cand = candidate()
    cand["decision_reduction"]["questions"][0]["numbers"].update(market_payout_btc=0.0025, spread_cost_usd=-33.0)
    q2 = json.dumps(dp.build(v2frame(cand))["steps"]["Q2"], ensure_ascii=False)
    assert "净信用 ÷ 两腿 mark 价差" in q2 and "77%" in q2 and "69–86%" in q2
    assert "少收约 33 美元" in q2 and "保护腿盘口点差 ÷ 中间价" in q2 and "67%；达到约 50%" in q2


def test_a_session_keeps_the_steps_it_started_with(tmp_path):
    s = store.DecisionPathStore(tmp_path)
    vid = s.start(candidate=candidate(), reading=dp.build(frame()), now_ms=NOW)[0]["verification_id"]
    path = tmp_path / "sessions" / f"{vid}.json"
    session = json.loads(path.read_text(encoding="utf-8"))
    # an old (v1) session: D5 stays markable after the v2 deploy, T is not part of it
    session["steps"] = [st for st in session["steps"] if st["id"] not in {"T", "H"}] + [{"id": "D5", "title": "变化"}]
    path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    s = store.DecisionPathStore(tmp_path)
    s.step(vid, "D5", lean="unchanged", now_ms=NOW + 1)
    with pytest.raises(store.DecisionPathError):
        s.step(vid, "T", lean=None, now_ms=NOW + 2)
    with pytest.raises(store.DecisionPathError):
        s.step(vid, "Z9", lean=None, now_ms=NOW + 3)


def test_market_state_line_with_warnings_and_only_on_its_basis():
    f = v2frame(hour=9, warnings=[{"code": "x", "label_cn": "期限倒挂", "triggered": False}])
    f["realized_vol"] = {"rv_24h_pct": 60.0, "rv_7d_pct": 40.0}        # expanding, implied 40 below realized 60
    steps = dp.build(f)["steps"]
    assert steps["T"]["market_state"] == "扩张/低于"
    line = next(l for l in steps["T"]["lines"] if l["k"] == "本期状态")
    assert line["v"].startswith("波动扩张、隐含低于实现") and "+14.8" in line["v"] and "未证明大于其他状态" in line["v"]
    assert line["basis"].startswith("回测支持（边缘") and line["tone"] == ""
    f["realized_vol"] = {"rv_24h_pct": 20.0, "rv_7d_pct": 30.0}        # contracting, implied above realized
    line = next(l for l in dp.build(f)["steps"]["T"]["lines"] if l["k"] == "本期状态")
    assert "回测无显著差异" in line["v"] and line["basis"].startswith("回测无差异")
    f["realized_vol"] = {}
    assert not any(l["k"] == "本期状态" for l in dp.build(f)["steps"]["T"]["lines"])
    week = v2frame(expiry=EXPIRY + 2 * H1)                             # not an 08:00 expiry: no state, no hour reading
    week["realized_vol"] = {"rv_24h_pct": 60.0, "rv_7d_pct": 40.0}
    t = dp.build(week)["steps"]["T"]
    assert t["market_state"] is None and not any(l["k"] in {"本期状态", "建仓时点"} for l in t["lines"])
    fallback = v2frame()
    fallback["expiry_walls"] = None                                     # IV falls back to the term structure
    fallback["realized_vol"] = {"rv_24h_pct": 60.0, "rv_7d_pct": 40.0}
    fallback["market_facts"]["space_context"]["gex_info"] = {"atm_iv": 40.0}
    assert dp.build(fallback)["steps"]["T"]["market_state"] is None


def test_a_standard_short_leg_is_not_a_veto():
    # 0.35Δ sits near 0.4 expected moves (17 号 §3.2): standard, not narrow
    f = v2frame(candidate(spot=83_345.0, short=82_500.0))
    move = dp._geometry(f)["move_pct"]
    spot = 83_345.0
    short = round(spot * (1 - 0.4 * move / 100))
    steps = dp.build(v2frame(candidate(spot=spot, short=float(short))))["steps"]
    assert steps["D2"]["verdict"]["code"] == "standard"
    assert not any(i["step"] == "D2" for i in steps["Q1"]["items"])
    tight = dp.build(v2frame(candidate(spot=spot, short=spot - 0.1 * move / 100 * spot)))["steps"]
    assert tight["D2"]["verdict"]["code"] == "narrow" and any(i["step"] == "D2" for i in tight["Q1"]["items"])


def test_v1_sessions_keep_their_marks_report_order_and_compare(tmp_path):
    from tools import astra_decision_path_report as rep
    s = store.DecisionPathStore(tmp_path)
    vid = s.start(candidate=candidate(), reading=dp.build(frame()), now_ms=NOW)[0]["verification_id"]
    path = tmp_path / "sessions" / f"{vid}.json"
    session = json.loads(path.read_text(encoding="utf-8"))
    v1_ids = ["D0", "D1", "D2", "D3", "D4", "D5", "Q1", "Q2", "AU"]
    session["steps"] = [{"id": i, "title": i} for i in v1_ids]
    session["path_version"] = "dp-2026-10-14.1"
    session["readings"]["start"]["path_version"] = "dp-2026-10-14.1"
    session["readings"]["start"]["steps"] = {i: {"verdict": {"code": "x", "cn": "x"}} for i in v1_ids}
    path.write_text(json.dumps(session, ensure_ascii=False), encoding="utf-8")
    s = store.DecisionPathStore(tmp_path)
    s.step(vid, "D5", lean="toward_decline", now_ms=NOW + 1)
    now = s.set_now(vid, dp.build(frame()), now_ms=NOW + 2)
    assert now["changes"][0] == {"step": "path_version", "from": "dp-2026-10-14.1", "to": dp.PATH_VERSION}
    assert not any(c["step"] in {"T", "H", "D5"} for c in now["changes"])
    s.conclude(vid, "decline", now_ms=NOW + 3)
    rows = [json.loads(l) for l in (tmp_path / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    rec = next(r for r in rows if r.get("event") == "concluded")
    assert rec["marks"]["D5"] == "toward_decline" and "T" not in rec["marks"] and rec["settled_after"] == "D5"
    result = rep.report([{**rec, "outcome": None}])
    assert list(result["dp-2026-10-14.1"]["steps"]) [:6] == ["D0", "D1", "D2", "D3", "D4", "D5"]


def test_positive_spread_cost_is_not_called_a_shortfall():
    cand = candidate()
    cand["decision_reduction"]["questions"][0]["numbers"].update(market_payout_btc=0.0025, spread_cost_usd=12.0)
    line = next(l for l in dp.build(v2frame(cand))["steps"]["Q2"]["lines"] if l["k"] == "成交口径成本")
    assert line["v"].startswith("按买卖价成交不低于按 mark")


def test_sessions_read_the_entry_warnings_the_page_shows(tmp_path):
    wb, clock, snap = setup_wb(tmp_path)
    out = wb.decision_path_start(snap["candidate_id"], snap["snapshot_id"], authorization="Bearer secret")
    t = out["session"]["readings"]["start"]["steps"]["T"]
    page = next(c for c in wb.cached_view()["candidates"] if (c.get("snapshot") or {}).get("snapshot_id") == snap["snapshot_id"])
    warned = (page.get("entry_warnings") or {}).get("warnings")
    assert warned          # the session reads the same warnings as the page, not "unavailable"
    rows = [l for l in t["lines"] if l["k"] == "否决层"]
    assert len(rows) == len(warned) and not any(l["v"] == "不可用" for l in rows)
