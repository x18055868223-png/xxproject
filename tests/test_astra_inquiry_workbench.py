"""Question sheets on the workbench (docs/opus/14): the archive's trigger field, the audit step's
marks and context in Decision Path sessions and records, the report, and the HTTP endpoint."""
import gzip
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from tools import astra_case_file_v1 as cf
from tools import astra_decision_path_report as rep
from tools import astra_decision_path_store_v1 as store
from tools import astra_decision_path_v1 as dp
from tools import astra_research_archive_v1 as ar
from tools import astra_workbench_v20 as workbench
from test_astra_decision_path import EXPIRY as DP_EXPIRY, NOW as DP_NOW, candidate, frame, setup_wb
from test_astra_research_archive_v1 import EXPIRY, H, FakeDeribit, _tick, _view

CASE = "cf_" + "a" * 24


# ------------------------------------------------------------------ archive: trigger field

def test_sheets_record_how_they_were_triggered(tmp_path):
    archive, deribit = ar.Archive(tmp_path), FakeDeribit()
    _tick(archive, EXPIRY - 21 * H + 60_000, _view(), deribit, lambda case: {"status": "OK", "sheet": {}})   # 11:01 UTC
    _tick(archive, EXPIRY - 18 * H + 60_000, _view(sid="b" * 64), deribit, lambda case: {"status": "OK", "sheet": {}})
    triggers = {p.name[:2]: json.loads(p.read_text(encoding="utf-8"))["trigger"] for p in tmp_path.glob("inquiry/*/*.json")}
    assert triggers == {"11": "new_contract", "14": "scheduled"}


def test_decisions_carry_the_audit_step_context():
    row = {"verification_id": "dpv_1", "at_ms": 1, "event": "concluded", "au_mode": dp.AU_MODE,
           "step_context": {"AU": {"opened": {"inquiry_shown": "f"}}},
           "contract": {"side": "put", "short_instrument": "A", "long_instrument": "B"}}
    detail = ar.collect_decisions([], [row])[0]["detail"]
    assert detail["au_mode"] == dp.AU_MODE and detail["step_context"]["AU"]["opened"]["inquiry_shown"] == "f"


# ------------------------------------------------------------------ case file 0.1.5

def test_case_space_gate_keeps_only_the_producer_state():
    view = {"space_gate": {"state": "STRUCTURE_ELIGIBLE", "eligible": True, "since_ts_ms": 1, "reason_codes": [],
                           "producer_state": "STRUCTURE_BLOCKED", "gating": False, "note_cn": "只作评价"},
            "daily_snapshots": [{"bjt_day": "2026-10-20", "space_gate": {"state": "STRUCTURE_ELIGIBLE",
                                                                         "producer_state": "STRUCTURE_BLOCKED"}},
                                {"bjt_day": "2026-10-10", "space_gate": {"state": "STRUCTURE_BLOCKED"}}]}
    from test_astra_case_file_v1 import _raw_market, _snapshot
    case = cf.build(snapshot=_snapshot(), raw_market=_raw_market(), asof_ms=0, view=view)
    gate = case["layers"]["L3"][case["refs"]["L3.space.gate"]["index"]]["values"]
    assert gate["producer_state"] == "STRUCTURE_BLOCKED" and gate["gating"] is False
    assert not set(cf.GATE_ADMISSION_KEYS) & set(gate)
    daily = case["layers"]["L6"][case["refs"]["L6.daily"]["index"]]["values"]
    assert [d["space_gate"] for d in daily] == ["STRUCTURE_BLOCKED", "STRUCTURE_BLOCKED"]   # old snapshots fall back
    assert case["build"]["builder"] == "astra_case_file_v1@0.1.7"


# ------------------------------------------------------------------ Decision Path: marks and context

def test_audit_step_marks_follow_the_session_steps(tmp_path):
    s = store.DecisionPathStore(tmp_path)
    session, _ = s.start(candidate=candidate(), reading=dp.build(frame()), now_ms=DP_NOW)
    vid = session["verification_id"]
    assert session["au_mode"] == dp.AU_MODE and dp.markable(session["steps"], "AU")
    s.step(vid, "AU", lean=None, now_ms=DP_NOW + 1, context={"inquiry_shown": "f1"})
    s.step(vid, "AU", lean="toward_decline", now_ms=DP_NOW + 2, context={"inquiry_shown": "f2"})
    got = s.get(vid)
    assert got["step_context"]["AU"]["opened"]["inquiry_shown"] == "f1"          # first opening kept
    assert got["step_context"]["AU"]["marked"] == {"inquiry_shown": "f2", "at_ms": DP_NOW + 2, "lean": "toward_decline"}
    # a session started before the change: its audit step stays unmarked, the program steps do not
    old_steps = [{k: v for k, v in step.items() if k != "markable"} for step in dp.steps_public()]
    assert not dp.markable([{"id": "AU", "on_demand": True, "markable": None}], "AU")   # only a real boolean opens it
    assert not dp.markable(old_steps, "AU") and dp.markable(old_steps, "D2") and not dp.markable(old_steps, "D9")
    other = {**candidate(), "snapshot": {**candidate()["snapshot"], "candidate_id": "cand2"}}
    vid2 = s.start(candidate=other, reading=dp.build(frame()), now_ms=DP_NOW)[0]["verification_id"]
    path = s._path(vid2)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["steps"], raw["au_mode"] = old_steps, None
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(store.DecisionPathError) as err:
        s.step(vid2, "AU", lean="toward_decline", now_ms=DP_NOW + 3)
    assert err.value.code == "step_not_markable"
    s.step(vid2, "AU", lean=None, now_ms=DP_NOW + 3)                              # opening still works
    s.step(vid2, "D2", lean="toward_decline", now_ms=DP_NOW + 4)
    s.conclude(vid, "decline", now_ms=DP_NOW + 5)
    rec = s.records()[-1]
    assert rec["au_mode"] == dp.AU_MODE and rec["marks"]["AU"] == "toward_decline"
    assert rec["step_context"]["AU"]["marked"]["inquiry_shown"] == "f2"


def test_report_counts_the_audit_step_only_where_it_was_opened(tmp_path):
    s = store.DecisionPathStore(tmp_path)
    for i in range(4):
        cand = {**candidate(), "snapshot": {**candidate()["snapshot"], "candidate_id": f"c{i}"}}
        vid = s.start(candidate=cand, reading=dp.build(frame()), now_ms=DP_NOW + i)[0]["verification_id"]
        s.step(vid, "Q2", lean=None, now_ms=DP_NOW + 10)
        if i < 2:   # two opened the audit step, one of them changed its lean there
            s.step(vid, "AU", lean="toward_decline" if i == 0 else None, now_ms=DP_NOW + 20)
        s.conclude(vid, "decline", now_ms=DP_NOW + 30)
        s.settle(vid, {"status": "settled", "result_usd": 50.0 - i, "short_breached": False}, now_ms=DP_EXPIRY)
    r = rep.report(rep.join(rep.load(tmp_path / "records.jsonl")))[dp.PATH_VERSION]
    assert r["steps"]["AU"]["n"] == 2 and r["steps"]["AU"]["change_rate"] == 0.5
    assert set(r["steps"]["AU"]["outcomes"]) == {"toward_decline", "unchanged"}
    assert r["steps"]["D2"]["n"] == 4 and "| AU |" in rep.render({dp.PATH_VERSION: r})


# ------------------------------------------------------------------ workbench endpoint and context

def _archive(root, short, long_):
    rel = f"inquiry/2026-10-14/14_put_{CASE}.json"
    (root / "_state").mkdir(parents=True)
    (root / "_state" / "state.json").write_text(json.dumps({"contracts": {f"{short}|{long_}": {"inquiry": [rel]}}}),
                                                encoding="utf-8")
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text(json.dumps({"case_id": CASE, "asof_ms": 7, "status": "OK", "trigger": "scheduled",
                                        "sheet": {"questions": [{"id": "K1", "priority": "high", "question_cn": "Q?",
                                                                 "premises": [{"refs": ["L1.contract"]}]}]}}),
                            encoding="utf-8")
    case = root / ("cases/" + rel[len("inquiry/"):] + ".gz")
    case.parent.mkdir(parents=True)
    case.write_bytes(gzip.compress(json.dumps({"case_id": CASE, "refs": {"L1.contract": {"layer": "L1", "index": 0}},
                                               "layers": {"L1": [{"label_cn": "合约", "values": {"k": 1}}]}}).encode()))
    return rel


def test_endpoint_and_audit_step_context(tmp_path, monkeypatch):
    wb, _, snap = setup_wb(tmp_path / "wb")
    contract = snap["contract"]
    root = tmp_path / "archive"
    rel = _archive(root, contract["short_instrument"], contract["long_instrument"])
    monkeypatch.setenv("ASTRA_RESEARCH_ARCHIVE", str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), workbench.make_handler(wb))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/api/workbench"
    q = {"short": contract["short_instrument"], "long": contract["long_instrument"]}
    try:
        full = json.loads(urlopen(f"{base}/inquiry?{urlencode(q)}", timeout=20).read())
        assert full["selected"]["file"] == rel and full["selected"]["entries"]["L1.contract"]["values"] == {"k": 1}
        brief = json.loads(urlopen(f"{base}/inquiry?{urlencode({**q, 'summary': '1'})}", timeout=20).read())
        assert brief["selected"]["summary"]["questions"][0]["question_cn"] == "Q?" and "entries" not in brief["selected"]
        for bad, status in (({**q, "file": "../_state/state.json"}, 404), ({**q, "short": "x/../y"}, 400)):
            with pytest.raises(HTTPError) as err:
                urlopen(f"{base}/inquiry?{urlencode(bad)}", timeout=20)
            assert err.value.code == status

        def post(url, body):
            req = Request(url, data=json.dumps(body).encode(), method="POST",
                          headers={"Authorization": "Bearer secret", "Content-Type": "application/json"})
            return json.loads(urlopen(req, timeout=20).read())
        vid = post(f"{base}/decision-path", {"candidate_id": snap["candidate_id"],
                                             "snapshot_id": snap["snapshot_id"]})["session"]["verification_id"]
        post(f"{base}/decision-path/{vid}/step", {"step": "AU", "shown": {"inquiry_file": rel, "review_kind": "program"}})
        marked = post(f"{base}/decision-path/{vid}/step",
                      {"step": "AU", "lean": "unchanged", "shown": {"inquiry_file": "inquiry/x.json", "review_kind": 5,
                                                                   "review_at_ms": True}})["session"]
        ctx = marked["step_context"]["AU"]
        assert ctx["opened"]["inquiry_shown"] == rel and ctx["opened"]["review_shown"] == {"review_kind": "program"}
        assert ctx["opened"]["inquiry_latest"]["file"] == rel
        # only the known review kinds and a plain timestamp are kept
        assert ctx["marked"]["inquiry_shown"] == "unlisted" and ctx["marked"]["review_shown"] is None
        # an unreadable archive never fails the mark
        monkeypatch.setenv("ASTRA_RESEARCH_ARCHIVE", str(tmp_path / "missing"))
        wb._inquiry_reader_obj = None
        again = post(f"{base}/decision-path/{vid}/step", {"step": "AU", "lean": "toward_decline"})["session"]
        assert again["step_context"]["AU"]["marked"]["inquiry"] == {"unavailable": "FileNotFoundError"}
        with pytest.raises(HTTPError) as err:
            urlopen(f"{base}/inquiry?{urlencode(q)}", timeout=20)
        assert err.value.code == 503
    finally:
        server.shutdown()
