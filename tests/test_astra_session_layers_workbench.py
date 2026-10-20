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
    assert {w["code"] for w in block["warnings"]} == {"term_inversion", "premium_thickening", "busy_window_thin_cushion"}
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
    assert out == {"warning_any": True, "warning_term_inversion": False, "warning_premium_thickening": None,
                   "warning_busy_window": True, "warning_rules": None}
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
    dim = lambda w: next(x for x in w["warnings"] if x["code"] == "busy_window_thin_cushion")["evidence"]["distance_in_moves"]
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
