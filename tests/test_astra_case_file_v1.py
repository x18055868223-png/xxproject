import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import pytest
from copy import deepcopy
from dataclasses import replace

from tools import astra_case_file_v1 as cf
from tools import astra_workbench_v20 as workbench
from test_astra_workbench_v20 import make_workbench, write_fmz, native_gate, T

EXPIRY = 1_792_483_200_000


@pytest.fixture(autouse=True)
def _empty_research_archive(tmp_path, monkeypatch):
    # an existing archive without promotions: readings resolve to an empty list, so cases freeze
    root = tmp_path / "research_archive"
    root.mkdir()
    monkeypatch.setenv("ASTRA_RESEARCH_ARCHIVE", str(root))


def _snapshot():
    return {
        "snapshot_id": "a" * 64, "candidate_id": "c" * 24,
        "contract": {"side": "put", "short_instrument": "BTC-20OCT26-82000-P", "long_instrument": "BTC-20OCT26-80000-P",
                     "short_strike_usd": 82000.0, "long_strike_usd": 80000.0, "width_usd": 2000.0,
                     "quantity_btc": 1.0, "expiry_ms": EXPIRY},
        "liability": {"payout_table": [{"settlement_usd": 80000.0, "payout_btc": 0.025}], "breakeven_usd": [81810.81],
                      "source_hash": "x" * 64},
        "economics": {"net_credit_after_fee_btc": 0.0023125},
        "market": {"valuation_ts_ms": EXPIRY - 18 * 3_600_000, "quote": {"credit_btc": 0.0027, "gap_reasons": []}},
        "fact_context": {"status": "current", "snapshot_ts_ms": EXPIRY - 18 * 3_600_000,
                         "market_facts": {"m_die": {"state": "NO_MOVE"},
                                          "space_context": {"gex_info": {"flip_point": 81000, "atm_iv": 30.1,
                                                                         "pcr": 0.8, "stale": True}}}},
        "data_identity": {"btc_map": {"source_manifest": {"source_records": [1] * 1000},
                                      "background": {"etf": {"flow": 1}}}},
    }


def _raw_market():
    book = {"underlying_price": 82420.0, "mark_price": 0.0036, "mark_iv": 31.5, "greeks": {"delta": -0.36}}
    return {"short_book": book, "long_book": {**book, "mark_price": 0.0007, "mark_iv": 44.4, "greeks": {"delta": -0.07}}}


def test_layers_refs_quality_and_lineage():
    case = cf.build(snapshot=_snapshot(), raw_market=_raw_market(), asof_ms=EXPIRY - 18 * 3_600_000)
    assert case["schema"] == "case_file@0.1" and set(case["layers"]) == set(cf.LAYERS)
    refs = case["refs"]
    for ref, at in refs.items():
        entry = case["layers"][at["layer"]][at["index"]]
        assert entry["ref"] == ref and entry["lineage"] and entry["quality"] in {"ok", "stale", "missing", "unknown"}
    assert case["layers"]["L2"][refs["L2.baseline.p_itm_short"]["index"]]["values"]["value"] == pytest.approx(0.36, abs=0.02)
    # a stale GEX source is marked, not hidden
    assert case["layers"]["L3"][refs["L3.gex.structure"]["index"]]["quality"] == "stale"
    # no read-view item: those entries say unknown instead of looking empty-but-fine
    assert case["layers"]["L7"][refs["L7.events"]["index"]]["quality_reason"] == "read_view_item_unavailable"
    assert case["layers"]["L8"][refs["L8.decision_path"]["index"]]["quality"] == "unknown"


def test_hashes_and_raw_source_records_are_dropped_not_truncated():
    case = cf.build(snapshot=_snapshot(), raw_market=_raw_market(), asof_ms=EXPIRY - 18 * 3_600_000)
    text = json.dumps(case["layers"])
    assert "source_hash" not in text and "source_records" not in text
    payout = case["layers"]["L1"][case["refs"]["L1.payout_table"]["index"]]["values"]
    assert payout == [{"settlement_usd": 80000.0, "payout_btc": 0.025}]


def test_item_from_a_different_capture_is_ignored():
    item = {"snapshot": {"snapshot_id": "b" * 64}, "exogenous_events": [{"name": "CPI"}]}
    case = cf.build(snapshot=_snapshot(), raw_market=_raw_market(), asof_ms=0, item=item)
    assert case["layers"]["L7"][case["refs"]["L7.events"]["index"]]["quality"] == "unknown"


def test_inquirer_copy_has_no_l9():
    case = cf.build(snapshot=_snapshot(), raw_market=_raw_market(), asof_ms=0, history=[{"expiry": "x"}])
    assert "L9.history" in case["refs"]
    inquirer = cf.for_inquirer(case)
    assert "L9" not in inquirer["layers"] and not any(v["layer"] == "L9" for v in inquirer["refs"].values())


def test_build_is_deterministic():
    one = cf.build(snapshot=_snapshot(), raw_market=_raw_market(), asof_ms=5)
    two = cf.build(snapshot=_snapshot(), raw_market=_raw_market(), asof_ms=5)
    assert one["build"]["content_sha256"] == two["build"]["content_sha256"]


def test_workbench_case_file_is_built_from_the_held_capture_and_frozen(tmp_path):
    original, snapshots = make_workbench(tmp_path)
    wb = workbench.AstraWorkbench(replace(original.config, wpem_enabled=True, wpem_main_loop=True),
                                  discovery_capture=original.discovery_capture,
                                  quote_capture=original.quote_capture, clock_ms=original.clock_ms)
    write_fmz(snapshots, native_gate())
    item = wb.refresh(now_ms=T)["candidates"][0]
    snapshot_id = item["snapshot"]["snapshot_id"]
    case = wb.case_file(snapshot_id)
    assert case["snapshot_id"] == snapshot_id and case["candidate_id"] == item["snapshot"]["candidate_id"]
    assert case["layers"]["L8"][case["refs"]["L8.decision_path"]["index"]]["quality"] == "ok"
    # read-time layers of the served view are present, not missing
    for ref in ("L2.decision_reduction", "L7.events", "L7.cross_asset"):
        assert case["layers"][ref[:2]][case["refs"][ref]["index"]]["quality"] != "missing", ref
    assert wb.case_file(snapshot_id) == case                    # frozen: same content on re-read
    assert (wb.config.state_dir / "case_files" / (snapshot_id + ".json")).exists()
    with pytest.raises(workbench.WorkbenchError) as missing:
        wb.case_file("0" * 64)
    assert missing.value.status_code == 404
    with pytest.raises(workbench.WorkbenchError) as bad:
        wb.case_file("../etc/passwd")
    assert bad.value.status_code == 400


def test_dict_of_nones_is_missing_and_secret_keys_are_dropped():
    snap = _snapshot()
    snap["fact_context"]["market_facts"]["space_context"]["anchor"] = None
    snap["fact_context"]["market_facts"]["macro_pressure"] = {"score": 0.4, "api_key": "sk-x", "auth_token": "t"}
    case = cf.build(snapshot=snap, raw_market=_raw_market(), asof_ms=0)
    assert case["layers"]["L3"][case["refs"]["L3.space.anchor"]["index"]]["quality"] == "missing"
    assert case["layers"]["L7"][case["refs"]["L7.macro"]["index"]]["values"] == {"score": 0.4}


def test_fee_gap_does_not_mark_the_quote_stale():
    snap = _snapshot()
    snap["market"]["quote"].update({"gap_reasons": ["fee_unknown"], "quote_gap_reasons": []})
    case = cf.build(snapshot=snap, raw_market=_raw_market(), asof_ms=0)
    assert case["layers"]["L2"][case["refs"]["L2.quote"]["index"]]["quality"] == "ok"


def _held_workbench(tmp_path):
    original, snapshots = make_workbench(tmp_path)
    wb = workbench.AstraWorkbench(replace(original.config, wpem_enabled=True, wpem_main_loop=True),
                                  discovery_capture=original.discovery_capture,
                                  quote_capture=original.quote_capture, clock_ms=original.clock_ms)
    write_fmz(snapshots, native_gate())
    return wb, wb.refresh(now_ms=T)["candidates"][0]["snapshot"]["snapshot_id"]


def test_concurrent_first_reads_build_once_and_agree(tmp_path):
    import threading
    wb, snapshot_id = _held_workbench(tmp_path)
    results, errors = [], []
    def read():
        try:
            results.append(wb.case_file(snapshot_id))
        except Exception as exc:   # pragma: no cover - the failure being guarded against
            errors.append(exc)
    threads = [threading.Thread(target=read) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors and len(results) == 4 and all(r == results[0] for r in results)
    assert [p.name for p in (wb.config.state_dir / "case_files").iterdir()] == [snapshot_id + ".json"]


def test_failed_program_reading_is_returned_but_not_frozen(tmp_path, monkeypatch):
    wb, snapshot_id = _held_workbench(tmp_path)
    monkeypatch.setattr(workbench.decision_path, "build", lambda frame: (_ for _ in ()).throw(RuntimeError("x")))
    case = wb.case_file(snapshot_id)
    assert case["build"]["stored"] is False
    assert not (wb.config.state_dir / "case_files" / (snapshot_id + ".json")).exists()
    monkeypatch.undo()
    assert wb.case_file(snapshot_id)["layers"]["L8"][0]["quality"] == "ok"


# --- 0.1.6 annotate (docs/opus/18 §3) ---

ASOF = EXPIRY - 18 * 3_600_000


def _entry(case, ref):
    at = case["refs"][ref]
    return case["layers"][at["layer"]][at["index"]]


def _stale_snapshot():
    """FMZ fact 5.6h old and not current; GEX fetched with it; the anchor and forward are current."""
    snap = _snapshot()
    fmz_at = ASOF - 20_369_188
    snap["economics"]["valuation_spot_usd"] = 82748.25
    snap["fact_context"] = {
        "status": "stale", "snapshot_ts_ms": fmz_at, "current_price_usd": 84275.48, "price_observed_ms": fmz_at,
        "gap_reasons": ["fmz_snapshot_stale", "fmz_price_stale"],
        "market_facts": {"m_die": {"state": "NO_MOVE"}, "skew": {"rr": 1.0}, "flow": {"cvd": 2.0},
                         "macro_pressure": {"score": 0.4},
                         "space_context": {"gamma_regime": {"asset_price": 84275.48},
                                           "gex_info": {"flip_point": 81000, "spot_price": 84220.6, "atm_iv": 30.1,
                                                        "pcr": 0.8, "fetched_at_ms": fmz_at - 533_628}}}}
    return snap


def _stale_view():
    return {"expiry_gamma": {"by_expiry": {"20OCT26": {"expiry_ms": EXPIRY, "forward_usd": 82739.44,
                                                       "computed_at_ms": ASOF - 61_983, "atm_iv_pct": 40.0}}}}


def _stale_case():
    return cf.build(snapshot=_stale_snapshot(), raw_market=_raw_market(), asof_ms=ASOF, view=_stale_view())


def test_cross_section_anchor_sources_and_deviation():
    case = _stale_case()
    section = _entry(case, "L0.cross_section")
    assert section["quality"] == "ok" and section["source"] == "astra_case_file_v1.annotate"
    assert section["values"]["anchor"] == {"name_cn": "报价估值时的 Deribit 指数", "price_usd": 82748.25, "at_ms": ASOF}
    fmz, gex, forward = section["values"]["sources"]
    assert fmz["name_cn"] == "信号层（FMZ）快照价" and fmz["price_usd"] == 84275.48
    assert fmz["age_ms"] == 20_369_188 and fmz["deviation_pct"] == 1.846 and fmz["current"] is False
    assert gex["name_cn"] == "GEX 现价" and gex["age_ms"] == 20_369_188 + 533_628 and gex["current"] is False
    assert forward["name_cn"] == "本期 forward（不是现价）" and forward["deviation_pct"] == -0.011
    assert forward["age_ms"] == 61_983 and forward["current"] is True
    assert set(section["lineage"]) == {"deribit.index", "fmz.snapshot", "gexmonitor.gex_info", "deribit.book_summary"}
    assert "不是当前的来源" in section["relations"]["note_cn"]
    # new cases carry the FMZ price directly
    assert _entry(case, "L0.fmz_fact")["values"]["current_price_usd"] == 84275.48


def test_cross_section_without_anchor_is_unknown_and_skips_missing_prices():
    section = _entry(cf.build(snapshot=_snapshot(), raw_market=_raw_market(), asof_ms=ASOF), "L0.cross_section")
    assert section["quality"] == "unknown" and section["values"]["sources"] == []


def test_stale_fmz_fact_and_old_gex_propagate():
    case = _stale_case()
    for ref in ("L3.space.anchor", "L4.skew", "L5.flow", "L5.m_die", "L7.macro"):
        entry = _entry(case, ref)
        assert (entry["quality"], entry["quality_reason"]) == ("stale", "fmz_snapshot_stale"), ref
    assert _entry(case, "L5.edb")["quality"] == "missing"          # not provided: stays missing
    fact = _entry(case, "L0.fmz_fact")                             # the fact states its own status
    assert (fact["quality"], fact["quality_reason"]) == ("ok", None)
    for ref in ("L3.gex.structure", "L5.options_flow", "L4.iv"):
        entry = _entry(case, ref)
        assert (entry["quality"], entry["quality_reason"]) == ("stale", "gex_older_than_1h_at_asof"), ref


def test_existing_reason_is_kept_when_an_entry_turns_stale():
    archived = _stale_case()
    for ref, reason in (("L4.iv", "iv_not_contract_expiry"), ("L5.flow", "producer_note")):
        entry = _entry(archived, ref)
        entry["quality"], entry["quality_reason"] = "ok", reason
    out = cf.annotate(archived)
    assert (_entry(out, "L4.iv")["quality"], _entry(out, "L4.iv")["quality_reason"]) == ("stale", "iv_not_contract_expiry")
    assert (_entry(out, "L5.flow")["quality"], _entry(out, "L5.flow")["quality_reason"]) == ("stale", "producer_note")


def test_current_fact_and_fresh_gex_change_nothing():
    snap = _stale_snapshot()
    fresh = ASOF - 60_000
    snap["fact_context"].update({"status": "current", "gap_reasons": [], "snapshot_ts_ms": fresh,
                                 "price_observed_ms": fresh})
    snap["fact_context"]["market_facts"]["space_context"]["gex_info"]["fetched_at_ms"] = fresh
    case = cf.build(snapshot=snap, raw_market=_raw_market(), asof_ms=ASOF, view=_stale_view())
    for ref in cf.FMZ_DERIVED_REFS + cf.GEX_REFS:
        assert _entry(case, ref)["quality"] in {"ok", "missing"}, ref
        assert _entry(case, ref)["quality_reason"] in {None, "iv_not_contract_expiry"}, ref
    assert [s["current"] for s in _entry(case, "L0.cross_section")["values"]["sources"]] == [True, True, True]


def test_annotate_is_idempotent_pure_and_keeps_refs_valid():
    case = _stale_case()
    before = deepcopy(case)
    again = cf.annotate(case)
    assert case == before                                          # input not mutated
    assert again == case and cf.annotate(again) == again           # build() already annotated; idempotent
    for ref, at in again["refs"].items():
        assert again["layers"][at["layer"]][at["index"]]["ref"] == ref
    assert case["build"]["builder"] == "astra_case_file_v1@0.1.8" and "annotated_by" not in again["build"]


def test_annotate_upgrades_an_archived_015_case():
    old = _stale_case()
    # strip the 0.1.6 additions to get a 0.1.5-shaped body
    old["layers"]["L0"] = [e for e in old["layers"]["L0"] if e["ref"] != "L0.cross_section"]
    for key in ("current_price_usd", "price_observed_ms"):
        _entry(old, "L0.fmz_fact")["values"].pop(key)
    for entries in old["layers"].values():
        for e in entries:
            if e["quality"] == "stale":
                e["quality"], e["quality_reason"] = "ok", None
    _entry(old, "L1.payout_table")["relations"] = {}
    alignment = _entry(old, "L3.spatial_position")["relations"]["expiry_alignment"]
    for key in ("zero_dte_expiry_ms", "zero_dte_expires_before_contract"):
        alignment.pop(key)
    old["refs"] = cf._index(old["layers"])
    old["build"]["builder"] = "astra_case_file_v1@0.1.5"
    out = cf.annotate(old)
    assert out["build"]["annotated_by"] == "astra_case_file_v1@0.1.8"
    fmz = _entry(out, "L0.cross_section")["values"]["sources"][0]
    assert fmz["price_usd"] == 84275.48 and fmz["current"] is False   # read from the space context
    assert _entry(out, "L5.flow")["quality_reason"] == "fmz_snapshot_stale"
    new_alignment = _entry(out, "L3.spatial_position")["relations"]["expiry_alignment"]
    assert new_alignment["zero_dte_expiry_ms"] == cf.next_daily_expiry_ms(_entry(out, "L3.gex.structure")["asof_ms"])
    assert "note_cn" in _entry(out, "L1.payout_table")["relations"]
    assert cf.annotate(out) == out


def test_spatial_alignment_states_the_zero_dte_expiry():
    contract = {"expiry_ms": EXPIRY}
    two_days_before = EXPIRY - 40 * 3_600_000
    align = cf.spatial_alignment(contract, two_days_before, {"distance_pct": 1.0}, {"atm_iv_pct": 40.0})
    assert align["zero_dte_expiry_ms"] == cf.next_daily_expiry_ms(two_days_before) < EXPIRY
    assert align["zero_dte_expires_before_contract"] is True
    assert "此后不再存在" in align["note_cn"] and "08:00 UTC" in align["note_cn"]
    same_day = cf.spatial_alignment(contract, EXPIRY - 3_600_000, None, None)
    assert same_day["zero_dte_expiry_ms"] == EXPIRY and same_day["zero_dte_expires_before_contract"] is False
    assert "此后不再存在" not in same_day["note_cn"]
    assert cf.spatial_alignment({}, EXPIRY, None, None)["zero_dte_expires_before_contract"] is None


def test_payout_table_says_which_rows_are_illustrative():
    note = _entry(cf.build(snapshot=_snapshot(), raw_market=_raw_market(), asof_ms=ASOF),
                  "L1.payout_table")["relations"]["note_cn"]
    assert "一天内不可达" in note and "宽度/价格" in note and "封顶于宽度" in note


def test_stale_gex_entry_says_its_own_age_fields_are_from_the_snapshot_time():
    body = {"asof_ms": 10 * 3_600_000, "layers": {"L3": [{"ref": "L3.gex.structure", "values": {"spot_price": 1.0},
                                                         "relations": {"age_ms": 540_000, "stale": False},
                                                         "asof_ms": 1 * 3_600_000, "quality": "ok",
                                                         "quality_reason": None, "lineage": ["gexmonitor.gex_info"]}]},
            "refs": {}}
    out = cf.annotate(body)
    gex = next(e for e in out["layers"]["L3"] if e["ref"] == "L3.gex.structure")
    assert gex["quality"] == "stale" and gex["relations"]["age_at_asof_ms"] == 9 * 3_600_000
    assert gex["relations"]["age_ms"] == 540_000 and "估值时刻" in gex["relations"]["stale_note_cn"]
    assert cf.annotate(out) == out


def _fmz_body(status, fact_gaps=(), view_gaps=()):
    return {"asof_ms": 2 * 3_600_000, "layers": {
        "L0": [{"ref": "L0.fmz_fact", "values": {"status": status}, "quality": "ok", "lineage": ["fmz.snapshot"]},
               {"ref": "L0.gaps", "values": {"fmz_fact": list(fact_gaps), "snapshot": [], "view": list(view_gaps)},
                "quality": "ok", "lineage": []}],
        "L5": [{"ref": "L5.m_die", "values": {"state": "x"}, "quality": "ok", "quality_reason": None,
                "lineage": ["fmz.snapshot"]}]}, "refs": {}}


def test_view_time_gaps_do_not_mark_a_current_capture_stale():
    out = cf.annotate(_fmz_body("current", view_gaps=["fmz_snapshot_stale"]))
    assert _entry(out, "L5.m_die")["quality"] == "ok"


def test_stale_reason_follows_the_fact_gap_that_caused_it():
    out = cf.annotate(_fmz_body("background_only", fact_gaps=["fmz_price_stale"]))
    assert _entry(out, "L5.m_die")["quality_reason"] == "fmz_price_stale"
    out = cf.annotate(_fmz_body("background_only", fact_gaps=["fmz_runtime_not_live_public"]))
    assert _entry(out, "L5.m_die")["quality"] == "stale"
    assert _entry(out, "L5.m_die")["quality_reason"] == "fmz_fact_not_current"
