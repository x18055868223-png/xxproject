import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import pytest
from dataclasses import replace

from tools import astra_case_file_v1 as cf
from tools import astra_workbench_v20 as workbench
from test_astra_workbench_v20 import make_workbench, write_fmz, native_gate, T

EXPIRY = 1_792_483_200_000


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
