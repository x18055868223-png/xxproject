import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import astra_research_archive_v1 as ar
import astra_research_inbox_v1 as inbox

AGENT = {"name": "GrokBot", "model": "GrokBot"}


def _hypothesis(**over):
    doc = {"schema": "hypothesis@0.1", "id": "h-transition-near-short", "agent": AGENT,
           "statement_cn": "Gamma 体制为 TRANSITION 且短腿距现价不足 0.6% 时，触界率高于基线",
           "rationale_cn": "对冲方向不稳", "falsification_cn": "60 个交割日后差异不超过 2 个标准误",
           "condition": {"all": [{"field": "ledger.states.gamma_regime", "op": "==", "value": "TRANSITION"},
                                 {"field": "ledger.short_from_index_pct", "op": ">", "value": -0.6}]},
           "metric": "touched_short_after_entry", "expected_direction": "riskier",
           "formation": {"description_cn": "10-20 起的样本", "samples": ["2026-10-20"]},
           "counterexamples": [], "collinear_with": []}
    doc.update(over)
    return doc


def test_condition_language_validates_and_evaluates():
    cond = _hypothesis()["condition"]
    assert inbox.check_condition(cond) == []
    row = {"ledger": {"states": {"gamma_regime": "TRANSITION"}, "short_from_index_pct": -0.4}}
    assert inbox.evaluate_condition(cond, row) is True
    assert inbox.evaluate_condition(cond, {"ledger": {"states": {}}}) is None     # missing field excludes
    bad = {"all": [{"field": "ledger.secret", "op": "==", "value": 1},
                   {"field": "ledger.states.gamma_regime", "op": ">", "value": "A"}]}
    errors = inbox.check_condition(bad)
    assert any("未知字段" in e for e in errors) and any("文本字段" in e for e in errors)


def test_ingest_registers_rejects_and_refuses_edits(tmp_path):
    archive, box, receipts = ar.Archive(tmp_path / "archive"), tmp_path / "inbox", tmp_path / "receipts"
    box.mkdir()
    (box / "h1.json").write_text(json.dumps(_hypothesis(), ensure_ascii=False), encoding="utf-8")
    (box / "bad.json").write_text(json.dumps(_hypothesis(id="h2", metric="pnl")), encoding="utf-8")
    (box / "writing.json.tmp").write_text("{", encoding="utf-8")
    report = inbox.ingest(archive, box, receipts, now_ms=1000)
    assert report == {"accepted": 1, "rejected": 1}
    assert sorted(p.name for p in box.iterdir()) == ["writing.json.tmp"]
    ok = json.loads((receipts / "h1.json.receipt.json").read_text(encoding="utf-8"))
    assert ok["status"] == "registered" and ok["received_at_ms"] == 1000
    bad = json.loads((receipts / "bad.json.receipt.json").read_text(encoding="utf-8"))
    assert bad["status"] == "rejected" and "allowed_metrics" in bad
    reg = (tmp_path / "archive/registry/hypotheses.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(reg[0])["document"]["id"] == "h-transition-near-short"
    # a registered id can never be rewritten
    (box / "h1-again.json").write_text(json.dumps(_hypothesis(statement_cn="改写")), encoding="utf-8")
    assert inbox.ingest(archive, box, receipts, now_ms=2000) == {"accepted": 0, "rejected": 1}
    assert archive.verify()["ok"] is True


def test_other_submission_kinds():
    assert inbox.validate({"schema": "postmortem@0.1", "id": "pm-2026-10-20", "agent": AGENT,
                           "expiry_date": "2026-10-20", "findings_cn": "x",
                           "inquiry_review": [{"case_id": "cf_x", "question_id": "K1",
                                               "verdict": "pointed_to_realized_risk"}]}) == []
    assert inbox.validate({"schema": "protocol_proposal@0.1", "id": "pp-1", "agent": AGENT,
                           "target": "ledger_fields", "proposal_cn": "增加字段", "evidence_cn": "h1"}) == []
    assert inbox.validate({"schema": "unknown"})


def test_supersedes_must_name_an_earlier_registered_other_id(tmp_path):
    archive, box, receipts = ar.Archive(tmp_path / "archive"), tmp_path / "inbox", tmp_path / "receipts"
    box.mkdir()
    (box / "a.json").write_text(json.dumps(_hypothesis(id="h-new", supersedes="h-missing")), encoding="utf-8")
    (box / "b.json").write_text(json.dumps(_hypothesis(id="h-self", supersedes="h-self")), encoding="utf-8")
    assert inbox.ingest(archive, box, receipts, now_ms=1) == {"accepted": 0, "rejected": 2}
    (box / "c.json").write_text(json.dumps(_hypothesis(id="h-orig")), encoding="utf-8")
    inbox.ingest(archive, box, receipts, now_ms=2)
    (box / "d.json").write_text(json.dumps(_hypothesis(id="h-rev", supersedes="h-orig")), encoding="utf-8")
    assert inbox.ingest(archive, box, receipts, now_ms=3) == {"accepted": 1, "rejected": 0}


def test_notes_and_proposals_can_supersede_only_their_own_kind(tmp_path):
    archive, box, receipts = ar.Archive(tmp_path / "archive"), tmp_path / "inbox", tmp_path / "receipts"
    box.mkdir()
    note = {"schema": "research_note@0.1", "agent": AGENT, "title_cn": "运行清单", "body_cn": "v1"}
    prop = {"schema": "protocol_proposal@0.1", "agent": AGENT, "target": "other", "proposal_cn": "x", "evidence_cn": "y"}
    (box / "n1.json").write_text(json.dumps({**note, "id": "runbook-1"}), encoding="utf-8")
    (box / "p1.json").write_text(json.dumps({**prop, "id": "pp-1"}), encoding="utf-8")
    assert inbox.ingest(archive, box, receipts, now_ms=1) == {"accepted": 2, "rejected": 0}
    # prefixed id, other kind, self-reference and a missing id are all refused
    for name, doc in {"a": {**note, "id": "runbook-2", "supersedes": "research_note:runbook-1"},
                      "b": {**note, "id": "runbook-3", "supersedes": "pp-1"},
                      "c": {**prop, "id": "pp-2", "supersedes": "pp-2"},
                      "d": {**prop, "id": "pp-3", "supersedes": "pp-missing"}}.items():
        (box / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    assert inbox.ingest(archive, box, receipts, now_ms=2) == {"accepted": 0, "rejected": 4}
    (box / "e.json").write_text(json.dumps({**note, "id": "runbook-4", "supersedes": "runbook-1"}), encoding="utf-8")
    (box / "f.json").write_text(json.dumps({**prop, "id": "pp-4", "supersedes": "pp-1"}), encoding="utf-8")
    assert inbox.ingest(archive, box, receipts, now_ms=3) == {"accepted": 2, "rejected": 0}
    assert archive.verify()["ok"] is True
