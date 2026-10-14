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
    assert steps["D2"]["verdict"]["code"] in {"narrow", "medium"}
    assert "本期 Put Gamma 墙 83,000" in json.dumps(steps["D2"], ensure_ascii=False)
    assert steps["D3"]["verdict"]["code"] == "toward_amplified"
    assert steps["D4"]["listed"] == ["event", "macro"]          # headwind points at a Put's liability
    assert steps["D5"]["verdict"]["code"] == "persistent" and steps["D5"]["transitions_24h"] == 1
    q1 = steps["Q1"]
    assert 1 <= len(q1["items"]) <= 3 and [i["step"] for i in q1["items"]] == sorted(
        (i["step"] for i in q1["items"]), key=dp.STEP_IDS.index)
    assert steps["Q2"]["verdict"]["code"] == "below_market"
    assert "近 24h 实现 20.0%" in json.dumps(steps["Q2"], ensure_ascii=False)


def test_no_premium_before_q2():
    text = json.dumps({k: v for k, v in dp.build(frame())["steps"].items() if k not in {"Q2", "AU"}}, ensure_ascii=False)
    for premium in ("161", "0.0019", "0.0023", "225", "64", "美元（"):
        assert premium not in text


def test_call_side_macro_filter_and_stops():
    call = candidate("call", short=84_000.0, long_=86_000.0)
    steps = dp.build(frame(call, t24="bullish_bias", t48="bearish_bias", macro="Mild Headwind"))["steps"]
    assert steps["D4"]["listed"] == ["event"]          # a headwind does not push a Call into its liability
    assert steps["D5"]["verdict"]["code"] == "back_and_forth"
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
    assert steps["D5"]["verdict"]["code"] == "fading"
    weak = dp.build(frame())["steps"]["D3"]["lines"][0]
    assert weak["v"] == "朝责任区（弱）" and weak["tone"] == "warn"
    assert dp.build(frame())["steps"]["Q1"]["verdict"]["cn"].startswith("需注意")


def test_old_realized_vol_says_its_age():
    f = frame()
    f["realized_vol"] = {"rv_24h_pct": 20.0, "rv_7d_pct": 30.0, "last_bar_end_ms": NOW - 5 * 3_600_000}
    line = next(l for l in dp.build(f)["steps"]["Q2"]["lines"] if l["k"] == "RV 对 IV")
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
