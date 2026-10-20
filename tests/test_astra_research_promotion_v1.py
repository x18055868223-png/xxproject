import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import pytest
from dataclasses import replace

import astra_research_archive_v1 as ar
import astra_research_promotion_v1 as rp

COND = {"field": "ledger.states.gamma_regime", "op": "==", "value": "TRANSITION"}


def _archive(tmp_path, status="supported", milestones=("60", "90", "120")):
    archive = ar.Archive(tmp_path / "archive")
    (archive.root / "registry").mkdir(parents=True)
    (archive.root / "validation").mkdir()
    hyp = {"submission_id": "hypothesis:h1", "received_at_ms": 1,
           "document": {"id": "h1", "condition": COND, "metric": "touched_short_after_entry",
                        "expected_direction": "riskier", "statement_cn": "过渡体制下更易触界"}}
    (archive.root / "registry" / "hypotheses.jsonl").write_text(json.dumps(hyp) + "\n", encoding="utf-8")
    ev = {"hypothesis_id": "h1", "status": status, "metric": "touched_short_after_entry",
          "expected_direction": "riskier", "effect": 0.12, "t": 3.1, "threshold": 2.0, "k": 1,
          "milestones": {m: {"pass": True, "against": False} for m in milestones}, "evaluated_at_ms": 5}
    (archive.root / "validation" / "2027-02-20.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
    return archive


def test_candidates_list_supported_not_yet_promoted(tmp_path):
    archive = _archive(tmp_path)
    assert rp.write_candidates(archive.root, now_ms=10) == {"candidates": 1}
    body = json.loads((archive.root / "promotions" / "candidates.json").read_text(encoding="utf-8"))
    assert body["candidates"][0]["hypothesis_id"] == "h1" and body["candidates"][0]["final"] is True
    rp.promote(archive, "h1", now_ms=20, label_cn="过渡体制触界读数", approval_cn="用户：同意晋升 h1")
    assert rp.write_candidates(archive.root, now_ms=30) == {"candidates": 0}


def test_promote_requires_supported_final_and_user_words(tmp_path):
    with pytest.raises(ValueError):
        rp.promote(_archive(tmp_path / "a", status="weakened"), "h1", now_ms=1, label_cn="x", approval_cn="y")
    provisional = _archive(tmp_path / "b", milestones=("60", "90"))
    with pytest.raises(ValueError, match="provisional"):
        rp.promote(provisional, "h1", now_ms=1, label_cn="x", approval_cn="y")
    action = rp.promote(provisional, "h1", now_ms=1, label_cn="x", approval_cn="y", allow_provisional=True)
    assert action["provisional"] is True
    with pytest.raises(ValueError):
        rp.promote(_archive(tmp_path / "c"), "h1", now_ms=1, label_cn="x", approval_cn=" ")


def test_promote_and_retire_bump_the_research_protocol_and_chain(tmp_path):
    archive = _archive(tmp_path)
    action = rp.promote(archive, "h1", now_ms=100, label_cn="过渡体制触界读数", approval_cn="用户：同意")
    assert action["reading_id"] == "R-001" and action["protocol_version"] == "research_protocol@1.1"
    assert action["condition"] == COND and action["evaluation"]["t"] == 3.1
    with pytest.raises(ValueError, match="already"):
        rp.promote(archive, "h1", now_ms=101, label_cn="x", approval_cn="y")
    assert [r["reading_id"] for r in rp.active_readings(archive.root)] == ["R-001"]
    retired = rp.retire(archive, "R-001", now_ms=200, reason_cn="样本外失效")
    assert retired["protocol_version"] == "research_protocol@1.2" and rp.active_readings(archive.root) == []
    versions = (archive.root / "protocols" / "versions.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(v)["source"] for v in versions] == ["approved_by_user", "approved_by_user"]
    assert archive.verify()["ok"] is True


def test_evaluate_readings_on_a_ledger_shaped_row(tmp_path):
    archive = _archive(tmp_path)
    rp.promote(archive, "h1", now_ms=1, label_cn="过渡体制触界读数", approval_cn="同意")
    readings = rp.active_readings(archive.root)
    row = {"at_ms": 0, "expiry_ms": 20 * 3_600_000, "states": {"gamma_regime": "TRANSITION"}}
    out = rp.evaluate_readings(readings, row)
    assert out["readings"][0]["applies"] is True and "更高" in out["readings"][0]["meaning_cn"]
    assert rp.evaluate_readings(readings, {**row, "states": {}})["readings"][0]["applies"] is None
    assert rp.evaluate_readings([], row)["readings"] == []


def test_case_file_carries_research_readings(tmp_path, monkeypatch):
    from tools import astra_workbench_v20 as workbench
    from test_astra_workbench_v20 import make_workbench, write_fmz, native_gate, T
    archive = _archive(tmp_path)
    rp.promote(archive, "h1", now_ms=1, label_cn="过渡体制触界读数", approval_cn="同意")
    monkeypatch.setenv("ASTRA_RESEARCH_ARCHIVE", str(archive.root))
    original, snapshots = make_workbench(tmp_path / "wb")
    wb = workbench.AstraWorkbench(replace(original.config, wpem_enabled=True, wpem_main_loop=True),
                                  discovery_capture=original.discovery_capture,
                                  quote_capture=original.quote_capture, clock_ms=original.clock_ms)
    write_fmz(snapshots, native_gate())
    sid = wb.refresh(now_ms=T)["candidates"][0]["snapshot"]["snapshot_id"]
    case = wb.case_file(sid)
    entry = case["layers"]["L8"][case["refs"]["L8.research_readings"]["index"]]
    assert entry["quality"] == "ok" and entry["values"]["readings"][0]["reading_id"] == "R-001"
    assert entry["values"]["readings"][0]["applies"] in {True, False, None}


def test_protocol_version_parsing_and_reading_ids(tmp_path):
    archive = _archive(tmp_path)
    archive.append_line("protocols/versions.jsonl", {"component": "research_protocol", "version": "research_protocol@2.0",
                                                     "source": "approved_by_user"}, at_ms=1, kind="protocol")
    assert rp.next_protocol_version(archive.root) == "research_protocol@2.1"
    first = rp.promote(archive, "h1", now_ms=2, label_cn="x", approval_cn="同意")
    with pytest.raises(ValueError):
        rp.retire(archive, "R-999", now_ms=3, reason_cn="x")
    rp.retire(archive, first["reading_id"], now_ms=4, reason_cn="失效")
    with pytest.raises(ValueError):
        rp.retire(archive, first["reading_id"], now_ms=5, reason_cn="again")
    again = rp.promote(archive, "h1", now_ms=6, label_cn="x", approval_cn="再次同意")
    assert again["reading_id"] == "R-002" and again["protocol_version"] == "research_protocol@2.3"
    archive.append_line("protocols/versions.jsonl", {"component": "research_protocol", "version": "research_protocol@x",
                                                     "source": "approved_by_user"}, at_ms=7, kind="protocol")
    with pytest.raises(ValueError, match="unparsable"):
        rp.next_protocol_version(archive.root)


def test_reading_whose_hypothesis_is_no_longer_supported_needs_review(tmp_path):
    archive = _archive(tmp_path)
    rp.promote(archive, "h1", now_ms=1, label_cn="x", approval_cn="同意")
    later = {"hypothesis_id": "h1", "status": "superseded", "milestones": {}}
    (archive.root / "validation" / "2027-03-01.jsonl").write_text(json.dumps(later) + "\n", encoding="utf-8")
    rp.write_candidates(archive.root, now_ms=2)
    body = json.loads((archive.root / "promotions" / "candidates.json").read_text(encoding="utf-8"))
    assert body["needs_review"] == [{"reading_id": "R-001", "hypothesis_id": "h1", "current_status": "superseded"}]


def test_missing_archive_and_state_gap_are_reported(tmp_path):
    with pytest.raises(FileNotFoundError):
        rp.active_readings(tmp_path / "nowhere")
    out = rp.evaluate_readings([], {"at_ms": 0, "expiry_ms": 10}, states_at_ms=45 * 60_000)
    assert out["states_gap_ms"] == 45 * 60_000 and out["states_stale"] is True


def test_candidate_failure_does_not_repeat_validation(tmp_path, monkeypatch):
    archive = ar.Archive(tmp_path / "a")
    runs = []
    monkeypatch.setattr(ar.research_validation, "run", lambda a, now_ms: runs.append(now_ms) or {"evaluated": 0})
    monkeypatch.setattr(ar.research_promotion, "write_candidates", lambda root, now_ms: 1 / 0)
    t0 = 1_792_486_800_000 + 40 * 60_000      # 2026-10-20 09:40 UTC
    deribit = ar.Deribit(lambda path, params: {"data": []})
    r1 = ar.tick(archive, now_ms=t0, view=None, fetch_case=lambda s: {}, deribit=deribit)
    ar.tick(archive, now_ms=t0 + 600_000, view=None, fetch_case=lambda s: {}, deribit=deribit)
    assert len(runs) == 1 and r1["errors"][0]["step"] == "promotion_candidates"


def test_case_not_frozen_when_readings_unavailable_and_locked_evaluated(tmp_path, monkeypatch):
    from tools import astra_workbench_v20 as workbench
    from test_astra_workbench_v20 import make_workbench, write_fmz, native_gate, T
    original, snapshots = make_workbench(tmp_path / "wb")
    wb = workbench.AstraWorkbench(replace(original.config, wpem_enabled=True, wpem_main_loop=True),
                                  discovery_capture=original.discovery_capture,
                                  quote_capture=original.quote_capture, clock_ms=original.clock_ms)
    write_fmz(snapshots, native_gate())
    sid = wb.refresh(now_ms=T)["candidates"][0]["snapshot"]["snapshot_id"]
    monkeypatch.setenv("ASTRA_RESEARCH_ARCHIVE", str(tmp_path / "missing"))
    case = wb.case_file(sid)
    assert case["build"]["stored"] is False
    assert case["layers"]["L8"][case["refs"]["L8.research_readings"]["index"]]["quality"] == "unknown"
    archive = _archive(tmp_path)
    locked_cond = {"field": "ledger.locked", "op": "==", "value": False}
    (archive.root / "registry" / "hypotheses.jsonl").write_text(json.dumps(
        {"submission_id": "hypothesis:h1", "received_at_ms": 1,
         "document": {"id": "h1", "condition": locked_cond, "metric": "touched_short_after_entry",
                      "expected_direction": "riskier"}}) + "\n", encoding="utf-8")
    rp.promote(archive, "h1", now_ms=1, label_cn="未锁定", approval_cn="同意")
    monkeypatch.setenv("ASTRA_RESEARCH_ARCHIVE", str(archive.root))
    case = wb.case_file(sid)
    reading = case["layers"]["L8"][case["refs"]["L8.research_readings"]["index"]]["values"]["readings"][0]
    assert reading["applies"] is True and case["build"].get("stored") is not False
