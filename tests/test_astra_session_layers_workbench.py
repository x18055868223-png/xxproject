"""Session clock and entry warnings on the read view, the page, the case file and the ledger (docs/opus/13)."""
import json

from tools import astra_entry_warnings_v1 as ew
from tools import astra_research_archive_v1 as ar
from tools import astra_session_clock_v1 as clock
from tools import astra_workbench_reading_v1 as reading
from tools import astra_workbench_v20 as workbench
from test_astra_locked_followup import setup


def test_view_and_page_carry_the_clock_and_each_candidates_warnings(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_RESEARCH_ARCHIVE", str(tmp_path / "no-archive"))
    wb, _, _, snapshot = setup(tmp_path)
    view = wb.cached_view()
    assert view["session_clock"]["schema"] == clock.SCHEMA
    assert view["session_clock"]["expiry_ms"] == snapshot["contract"]["expiry_ms"]
    block = view["candidates"][0]["entry_warnings"]
    assert block["rules"] == ew.RULES and block["history_available"] is False
    assert [w["code"] for w in block["warnings"]] == ["term_inversion", "premium_thickening"]
    assert [n["code"] for n in block["notes"]] == ["busy_window"] and "busy_window" not in block["unknown"]
    assert "premium_thickening" in block["unknown"]          # no archive: never shown as "no warning"
    page = reading.workbench_page(reading.workbench_reading(view))
    assert "session_clock" in page and "entry_warnings" in page["candidates"][0]


def test_the_clock_follows_read_time_not_refresh_time(tmp_path):
    wb, _, clock_ms, _ = setup(tmp_path)
    first = wb.cached_view()["session_clock"]["now_ms"]
    clock_ms[0] += 3_600_000
    assert wb.cached_view()["session_clock"]["now_ms"] == first + 3_600_000


def test_a_failure_in_the_layers_never_breaks_the_view(tmp_path, monkeypatch):
    wb, _, _, _ = setup(tmp_path)
    monkeypatch.setattr(clock, "reading", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    view = wb.cached_view()
    assert view["session_clock"]["error_type"] == "RuntimeError"
    assert view["candidates"][0]["entry_warnings"]["error_type"] == "RuntimeError"


def test_history_comes_from_the_archive_ledger_and_is_cached(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    (root / "ledger").mkdir(parents=True)
    wb, _, clock_ms, _ = setup(tmp_path)
    now = clock_ms[0]
    day = ar._utc(now).strftime("%Y-%m-%d")
    rows = [{"side": "put", "displayed": True, "at_ms": now - (i + 1) * 1_800_000, "comp_ratio_after_fee": 0.7,
             "short": {"mark_iv": 30.0}} for i in range(5)]
    (root / "ledger" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setenv("ASTRA_RESEARCH_ARCHIVE", str(root))
    assert len(wb._ledger_history(now)) == 5
    (root / "ledger" / f"{day}.jsonl").write_text("", encoding="utf-8")
    assert len(wb._ledger_history(now + 1000)) == 5                       # cached for a minute
    assert wb._ledger_history(now + 61_000) == []


def test_ledger_rows_copy_the_displayed_candidates_warnings():
    block = {"warnings": [{"code": "term_inversion", "triggered": False}, {"code": "premium_thickening", "triggered": None},
                          {"code": "busy_window_thin_cushion", "triggered": True}]}
    out = ar._warning_fields(block)
    # an @0.1 block: the busy-window item keeps its column but no longer counts in warning_any
    assert out == {"warning_any": None, "warning_term_inversion": False, "warning_premium_thickening": None,
                   "warning_busy_window": True, "warning_rules": None, "note_busy_window": None}
    v02 = {"rules": "entry_warnings@0.2", "warnings": [{"code": "term_inversion", "triggered": False},
                                                     {"code": "premium_thickening", "triggered": False}],
           "notes": [{"code": "busy_window", "role": "note", "active": True}]}
    assert ar._warning_fields(v02) == {"warning_any": False, "warning_term_inversion": False,
                                       "warning_premium_thickening": False, "warning_busy_window": None,
                                       "warning_rules": "entry_warnings@0.2", "note_busy_window": True}
    v02["notes"][0]["active"] = None
    assert ar._warning_fields(v02)["note_busy_window"] is None and ar._warning_fields(v02)["warning_any"] is False
    v02["warnings"][1]["triggered"] = True
    assert ar._warning_fields(v02)["warning_any"] is True
    # a block that says it is @0.1 keeps the @0.1 scope of warning_any, so rows stratify cleanly by warning_rules
    assert ar._warning_fields({**block, "rules": "entry_warnings@0.1"})["warning_any"] is True
    quiet = ar._warning_fields({"warnings": [{"code": "term_inversion", "triggered": False},
                                             {"code": "premium_thickening", "triggered": None}]})
    assert quiet["warning_any"] is None                                     # one unknown: not "no warning"
    assert ar._warning_fields(None)["warning_any"] is None
    ctx = ar.market_context({"candidates": [{"snapshot": {"contract": {"short_instrument": "A", "long_instrument": "B"}},
                                             "entry_warnings": block}]}, None, now_ms=1_792_490_400_000)
    assert ctx["warnings"]["A|B"] is block


def test_market_marks_carry_the_legs_mark_iv():
    marks = workbench._market_marks({"market": {"short_book": {"mark_price": 0.003, "mark_iv": 29.5, "timestamp": 1},
                                                "long_book": {"mark_price": 0.0005, "mark_iv": 38.0, "timestamp": 2}}})
    assert marks["short_mark_iv"] == 29.5 and marks["long_mark_iv"] == 38.0


def test_sigma_sign_and_same_expiry_other_side():
    from tools import astra_workbench_v20 as wbm
    expiry = 1_792_569_600_000
    now = expiry - 20 * 3_600_000
    def item(side, strike, exp=expiry, iv=30.0):
        return {"snapshot": {"contract": {"side": side, "expiry_ms": exp, "short_strike_usd": strike,
                                          "short_instrument": f"S{side}{exp}", "long_instrument": f"L{side}{exp}"},
                             "market": {"reference_price_usd": 100_000.0}},
                "market_marks": {"short_mark_iv": iv}}
    by_expiry = {"x": {"expiry_ms": expiry, "atm_iv_pct": 30.0}}
    put, call = item("put", 99_000), item("call", 101_000)
    dim = lambda w: next(x for x in w["notes"] if x["code"] == "busy_window")["evidence"]["distance_in_moves"]
    a = wbm._entry_warnings_for(put, now, {}, by_expiry, {"put": put, "call": call}, [])
    b = wbm._entry_warnings_for(call, now, {}, by_expiry, {"put": put, "call": call}, [])
    assert dim(a) > 0 and abs(dim(a) - dim(b)) < 1e-9                       # both out of the money, symmetric
    later = item("call", 101_000, exp=expiry + 86_400_000, iv=99.0)
    c = wbm._entry_warnings_for(put, now, {}, by_expiry, {"put": put, "call": later}, [])
    pt = next(x for x in c["warnings"] if x["code"] == "premium_thickening")
    assert pt["evidence"]["other_side_skew_jump_vol_pts"] is None             # another expiry is not the other side


def test_archive_rows_pick_up_the_view_warnings_by_contract_key():
    block = {"rules": "entry_warnings@0.1", "warnings": [{"code": "term_inversion", "triggered": True}]}
    view = {"candidates": [{"snapshot": {"contract": {"short_instrument": "A", "long_instrument": "B"}}, "entry_warnings": block}]}
    ctx = ar.market_context(view, None, now_ms=1_792_490_400_000)
    record = {"key": "A|B", "side": "call", "short_strike_usd": 82_000.0, "expiry_ms": 1_792_569_600_000}
    fields = ar.research_fields(record, {"short_from_index_pct": 0.5}, ctx, now_ms=1_792_490_400_000)
    assert fields["warning_term_inversion"] is True and fields["warning_rules"] == "entry_warnings@0.1"
    assert fields["session_phase"] and fields["session_clock"] == clock.SCHEMA


def _with_expiry_gamma(wb, snapshot, iv=30.0):
    from tools import astra_expiry_gamma_v1 as eg
    expiry = snapshot["contract"]["expiry_ms"]
    wb._expiry_gamma = {"schema": eg.SCHEMA, "status": "ok", "computed_at_ms": wb.clock_ms(),
                        "expiries": {eg.expiry_code(expiry): {"expiry": eg.expiry_code(expiry), "expiry_ms": expiry,
                                                              "atm_iv_pct": iv, "forward_usd": 100_000.0}},
                        "realized_vol": {"rv_24h_pct": 28.0, "rv_7d_pct": 31.0}}


def test_page_view_carries_the_readings_and_the_note_records_their_distance(tmp_path, monkeypatch):
    from tools import astra_candidate_readings_v1 as readings
    monkeypatch.setenv("ASTRA_RESEARCH_ARCHIVE", str(tmp_path / "no-archive"))
    wb, _, _, snapshot = setup(tmp_path)
    _with_expiry_gamma(wb, snapshot)
    view = wb.cached_view()
    item = view["candidates"][0]
    move = item["readings"]["expected_move"]
    assert item["readings"]["schema"] == readings.SCHEMA and move["iv_source"] == "deribit_mark_own_expiry"
    assert move["distance_in_moves"] is not None and move["distance_in_moves"] > 0
    note = next(n for n in item["entry_warnings"]["notes"] if n["code"] == "busy_window")
    assert note["evidence"]["distance_in_moves"] == move["distance_in_moves"]
    page = reading.workbench_page(reading.workbench_reading(view))
    assert page["candidates"][0]["readings"] == json.loads(json.dumps(item["readings"]))
    # a stale own-expiry row: the readings fall back to the term structure and the note follows them
    wb._expiry_gamma["computed_at_ms"] = wb.clock_ms() - 3 * 3_600_000
    stale = wb.cached_view()["candidates"][0]
    move = stale["readings"]["expected_move"]
    assert move["iv_source"] in {None, "gexmonitor_term"}
    note = next(n for n in stale["entry_warnings"]["notes"] if n["code"] == "busy_window")
    assert note["evidence"]["distance_in_moves"] == move["distance_in_moves"]      # None included: never the stale IV


def test_entry_warnings_keep_their_own_distance_without_readings():
    from tools import astra_workbench_v20 as wbm
    expiry = 1_792_569_600_000
    now = expiry - 20 * 3_600_000
    put = {"snapshot": {"contract": {"side": "put", "expiry_ms": expiry, "short_strike_usd": 99_000.0},
                        "market": {"reference_price_usd": 100_000.0}}, "market_marks": {}}
    by_expiry = {"x": {"expiry_ms": expiry, "atm_iv_pct": 30.0}}
    dim = lambda w: next(x for x in w["notes"] if x["code"] == "busy_window")["evidence"]["distance_in_moves"]
    assert wbm._readings_dim(put) is wbm.OWN_DISTANCE
    own = dim(wbm._entry_warnings_for(put, now, {}, by_expiry, {"put": put}, [], distance_in_moves=wbm._readings_dim(put)))
    assert own is not None and own > 0
    assert dim(wbm._entry_warnings_for(put, now, {}, by_expiry, {"put": put}, [], distance_in_moves=0.42)) == 0.42
    assert dim(wbm._entry_warnings_for(put, now, {}, by_expiry, {"put": put}, [], distance_in_moves=None)) is None
    assert wbm._readings_dim({"readings": {"schema": "x", "error_type": "ValueError"}}) is wbm.OWN_DISTANCE
    assert wbm._readings_dim({"readings": {"expected_move": {"distance_in_moves": None}}}) is None


def test_cycle_start_rows_are_read_once_per_expiry(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    (root / "ledger").mkdir(parents=True)
    wb, _, clock_ms, _ = setup(tmp_path)
    now = clock_ms[0]
    expiry = now + 6 * 3_600_000                      # the cycle started 18 h ago, on its own UTC date
    start = expiry - 24 * 3_600_000
    day = ar._utc(start).strftime("%Y-%m-%d")
    rows = [{"at_ms": start + m * 60_000, "expiry_ms": expiry, "expiry_atm_iv_pct": 30.0 + m / 10} for m in (0, 30, 59)]
    rows += [{"at_ms": start + 61 * 60_000, "expiry_ms": expiry, "expiry_atm_iv_pct": 99.0},   # after the first hour
             {"at_ms": start + 10 * 60_000, "expiry_ms": expiry + 1, "expiry_atm_iv_pct": 99.0}]   # another expiry
    (root / "ledger" / f"{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setenv("ASTRA_RESEARCH_ARCHIVE", str(root))
    got = wb._cycle_start_rows(expiry, now)
    assert [r["at_ms"] for r in got] == [start, start + 30 * 60_000, start + 59 * 60_000]
    (root / "ledger" / f"{day}.jsonl").unlink()      # cached: the first hour is over and never changes
    assert wb._cycle_start_rows(expiry, now) == got
    assert wb._cycle_start_rows(None, now) is None and wb._cycle_start_rows(now + 48 * 3_600_000, now) is None
    # the warnings' history stays a 7-hour tail of today's file (and yesterday's only before 07:00 UTC)
    assert wb._ledger_history(now) == [] or all(now - r["at_ms"] <= 7 * 3_600_000 for r in wb._ledger_history(now))


def test_lock_detail_says_the_conditional_remaining_risk(tmp_path):
    from test_astra_locked_followup import lock
    from tools import astra_path_stats_v1 as ps
    wb, _, clock_ms, snapshot = setup(tmp_path)
    episode_id = lock(wb, snapshot)["episode"]["episode_id"]
    now = wb.locked_candidate_detail(episode_id, include_raw=False)["overview"]["path_now"]
    # 24 h left on an 08:00 expiry, the short leg 3% away: the next survival row (12:00 UTC)
    assert now["applicable"] is True and now["touched"] is False and now["side"] == "put"
    assert now["hours_left"] == 24.0 and now["samples_checked"] >= 1
    assert now["survival"] == {"hour_utc": 12, "later_touch_pct": 51, "beyond_pct": 23}
    assert now["basis_cn"] == ps.SURVIVAL_NOTE_CN and now["touch_cn"] is None
    # a sample since lock reached the short leg: what a touch at that many hours left has led to
    baseline = wb.locked_candidates.episode_observations(episode_id)["baseline_point_id"]
    series = wb.locked_candidates.read_series(episode_id) or {}
    metrics = dict((series.get(baseline) or {}).get("metrics") or wb._series_metrics_cache().get(baseline) or {})
    metrics["short_distance_pct"] = -0.2
    clock_ms[0] += 2 * 3_600_000
    touch_at = clock_ms[0] - 3_600_000
    wb.locked_candidates.append_series(episode_id, [{"point_id": baseline, "recorded_at_ms": touch_at - 1, "metrics": metrics}])
    first, _ = wb._lock_first_touch(episode_id, {})
    assert first is not None
    live = {"episode": {"status": "active"}, "latest": {"recorded_at_ms": clock_ms[0] - 60_000}}
    touched = wb._lock_path_now(episode_id, {**live, "fixed_contract": snapshot["contract"], "overview": {}}, clock_ms[0])
    assert touched["touched"] is True and touched["survival"] is None
    assert touched["touch_cn"] == ps.touch_text((snapshot["contract"]["expiry_ms"] - first) / 3_600_000)
    assert touched["basis_cn"] == ps.TOUCH_NOTE_CN
    # outside the basis: not an 08:00 expiry, or more than a day left
    later = wb._lock_path_now(episode_id, {**live, "fixed_contract": {**snapshot["contract"],
                                                              "expiry_ms": snapshot["contract"]["expiry_ms"] + 86_400_000}},
                              clock_ms[0])
    assert later["applicable"] is False and later["why_cn"] == "距到期超过一天，路径统计不适用"
    odd = wb._lock_path_now(episode_id, {**live, "fixed_contract": {**snapshot["contract"],
                                                            "expiry_ms": snapshot["contract"]["expiry_ms"] + 3_600_000}},
                            clock_ms[0])
    assert odd["applicable"] is False and "08:00" in odd["why_cn"]
    # frozen samples never say "not touched yet": a stopped lock, or one without a fresh sample
    stopped = wb._lock_path_now(episode_id, {**live, "episode": {"status": "stopped"},
                                             "fixed_contract": snapshot["contract"], "overview": {}}, clock_ms[0])
    assert stopped["applicable"] is False and stopped["survival"] is None and "结束" in stopped["why_cn"]
    stale = wb._lock_path_now(episode_id, {**live, "latest": {"recorded_at_ms": clock_ms[0] - 11 * 60_000},
                                           "fixed_contract": snapshot["contract"], "overview": {}}, clock_ms[0])
    assert stale["applicable"] is False and stale["touched"] is None and "10 分钟" in stale["why_cn"]


def test_decision_path_item_fills_only_the_missing_blocks(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRA_RESEARCH_ARCHIVE", str(tmp_path / "no-archive"))
    wb, _, _, snapshot = setup(tmp_path)
    _with_expiry_gamma(wb, snapshot)
    page_item = wb.cached_view()["candidates"][0]
    bare = {k: v for k, v in page_item.items() if k not in {"readings", "entry_warnings"}}
    out = wb._dp_item(bare, wb.clock_ms())
    assert out["readings"]["expected_move"] == page_item["readings"]["expected_move"]
    note = next(n for n in out["entry_warnings"]["notes"] if n["code"] == "busy_window")
    assert note["evidence"]["distance_in_moves"] == out["readings"]["expected_move"]["distance_in_moves"]
    frozen = {"rules": "entry_warnings@0.2", "warnings": [], "notes": [], "frozen": True}
    kept = wb._dp_item({**bare, "entry_warnings": frozen}, wb.clock_ms())
    assert kept["entry_warnings"] == frozen and kept["readings"]["schema"]
