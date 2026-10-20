"""Server-side fixes from the researcher's self-audit of 2026-10-20 (A4-A8)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import astra_case_file_v1 as cf
import astra_research_archive_v1 as ar
import astra_research_scorecards_v1 as sc
from test_astra_research_archive_v1 import EXPIRY, H, FakeDeribit, _tick, _view

CONTRACT_EXPIRY = 1_792_569_600_000     # 2026-10-21 08:00 UTC


def test_expiry_labels_and_the_nearest_daily_expiry():
    assert cf.expiry_label(CONTRACT_EXPIRY) == "21OCT26"
    assert cf.expiry_label(1_793_520_000_000) == "1NOV26"          # no leading zero, like Deribit
    before_eight = CONTRACT_EXPIRY - 27 * H                          # 10-20 05:00 UTC
    assert cf.next_daily_expiry_ms(before_eight) == CONTRACT_EXPIRY - 24 * H
    assert cf.next_daily_expiry_ms(CONTRACT_EXPIRY - 24 * H) == CONTRACT_EXPIRY   # exactly 08:00 rolls over


def test_spatial_and_iv_readings_say_when_they_belong_to_another_expiry():
    contract = {"expiry_ms": CONTRACT_EXPIRY}
    spatial = {"distance_pct": 0.6}
    row = {"atm_iv_pct": 28.19}
    early = cf.spatial_alignment(contract, CONTRACT_EXPIRY - 27 * H, spatial, row)    # 05:00, 0DTE = 20OCT
    assert early["zero_dte_walls_are_contract_expiry"] is False and early["nearest_daily_expiry"] == "20OCT26"
    move = 28.19 * (27 / 8760) ** 0.5
    assert abs(early["expected_move_pct_contract_expiry"] - move) < 1e-9
    assert abs(early["distance_in_moves_contract_expiry"] - 0.6 / move) < 1e-9
    late = cf.spatial_alignment(contract, CONTRACT_EXPIRY - 20 * H, spatial, row)     # 12:00, 0DTE = 21OCT
    assert late["zero_dte_walls_are_contract_expiry"] is True
    gex = {"term_structure": [{"expiry": "20OCT26", "atm_iv": 21.48}, {"expiry": "21OCT26", "atm_iv": 28.19}],
           "rr_skew": [{"tenor": "1d", "expiry": "20OCT26", "rr": -1.1}]}
    iv = cf.iv_alignment(contract, gex)
    assert iv["term_front_is_contract_expiry"] is False and iv["rr_1d_is_contract_expiry"] is False
    assert iv["contract_expiry_term"]["atm_iv"] == 28.19


def test_ledger_rows_say_what_changed():
    last = {"short_from_index_pct": -0.5, "breakeven_from_index_pct": -0.2, "long_from_index_pct": -2.0,
            "states": {"m_die_level": "A", "space_gate": "X", "gamma_regime": "G"}, "displayed": True, "locked": False}
    flicker = {**last, "states": {**last["states"], "m_die_level": "B"}}
    assert ar.change_detail(last, flicker) == ["state:m_die_level"]
    crossed = {**last, "short_from_index_pct": 0.1, "breakeven_from_index_pct": 0.4}
    assert ar.change_detail(last, crossed) == ["crossed_short", "crossed_breakeven", "distance"]
    assert ar.change_detail(last, dict(last)) == [] and ar.change_detail(None, last) == []


def test_new_contract_gets_questions_outside_the_scheduled_hours(tmp_path):
    archive, deribit, seen = ar.Archive(tmp_path), FakeDeribit(), []
    def inquiry(case):
        seen.append(case["case_id"])
        return {"status": "OK", "sheet": {}}
    off_hours = EXPIRY - 21 * H + 60_000        # 2026-10-19 11:01 UTC, not an inquiry slot
    first = _tick(archive, off_hours, _view(), deribit, inquiry)
    assert first["inquiry"] == 1
    # the same contract an hour later already has questions: nothing new until the scheduled slot
    second = _tick(archive, off_hours + H, _view(sid="b" * 64), deribit, inquiry)
    assert second["cases"] == 1 and second["inquiry"] == 0
    record = next(iter(archive.load_state()["contracts"].values()))
    assert len(record["inquiry"]) == 1


def test_reconcile_recounts_rows_and_relinks_inquiry(tmp_path):
    archive, deribit = ar.Archive(tmp_path), FakeDeribit()
    _tick(archive, EXPIRY - 18 * H + 60_000, _view(), deribit, lambda case: {"status": "OK", "sheet": {}})
    state = archive.load_state()
    key = next(iter(state["contracts"]))
    state["contracts"][key].update({"inquiry": [], "ledger_rows": 0})     # counters that started late
    archive.save_state(state)
    assert ar.reconcile_state(archive) == {"contracts": 1, "changed": 1}
    record = archive.load_state()["contracts"][key]
    assert record["ledger_rows"] == 1 and len(record["inquiry"]) == 1
    assert ar.reconcile_state(archive)["changed"] == 0                    # idempotent


def test_views_signed_with_the_model_name_count_for_the_researcher(tmp_path):
    root = tmp_path / "a"
    (root / "registry").mkdir(parents=True)
    view = {"submission_id": "episode_view:v1", "received_at_ms": 1,
            "document": {"id": "v1", "agent": {"name": "GrokBot", "model": "grok-bot"}, "contract_key": "k",
                         "view": {"risk_vs_baseline": "in_line", "grade": "C", "lean": "watch", "confidence": "low"}}}
    (root / "registry" / "episode_views.jsonl").write_text(json.dumps(view) + "\n", encoding="utf-8")
    row = sc.view_scorecard(root, now_ms=2)["views"][0]
    assert row["agent"] == "Cici" and row["agent_signed"] == "GrokBot"


def test_unknown_expiry_is_neither_a_match_nor_a_mismatch():
    iv = cf.iv_alignment({}, {})
    assert iv["term_front_is_contract_expiry"] is None and iv["rr_1d_is_contract_expiry"] is None
    iv = cf.iv_alignment({"expiry_ms": CONTRACT_EXPIRY}, {"rr_skew": [{"tenor": "1d", "expiry": None}]})
    assert iv["rr_1d_is_contract_expiry"] is None
    assert cf.spatial_alignment({}, CONTRACT_EXPIRY - 27 * H, None, None)["zero_dte_walls_are_contract_expiry"] is None
    # walls fetched at 07:55 still belong to the expiring day even if the case is valued after 08:00
    at = cf.spatial_alignment({"expiry_ms": CONTRACT_EXPIRY}, CONTRACT_EXPIRY - 24 * H + 5 * 60_000, {}, None,
                              walls_at_ms=CONTRACT_EXPIRY - 24 * H - 5 * 60_000)
    assert at["zero_dte_walls_are_contract_expiry"] is False


def test_unscheduled_inquirer_runs_are_capped_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "NEW_CONTRACT_INQUIRY_DAILY_MAX", 1)
    archive, deribit, seen = ar.Archive(tmp_path), FakeDeribit(), []
    def inquiry(case):
        seen.append(case["case_id"])
        return {"status": "OK", "sheet": {}}
    off_hours = EXPIRY - 21 * H + 60_000
    _tick(archive, off_hours, _view(), deribit, inquiry)
    other = _view(short="BTC-20OCT26-81000-P", long="BTC-20OCT26-79000-P", sid="c" * 64)
    _tick(archive, off_hours + H, other, deribit, inquiry)       # a second new contract, cap reached
    assert len(seen) == 1


def test_operator_commands_refuse_while_a_tick_holds_the_lock(tmp_path):
    archive = ar.Archive(tmp_path)
    (tmp_path / "_state").mkdir()
    (tmp_path / "_state" / "tick.lock").write_text("", encoding="utf-8")
    import pytest
    with pytest.raises(SystemExit, match="running"):
        with ar.operator_lock(archive):
            pass
    (tmp_path / "_state" / "tick.lock").unlink()
    with ar.operator_lock(archive):
        assert (tmp_path / "_state" / "tick.lock").exists()
    assert not (tmp_path / "_state" / "tick.lock").exists()
