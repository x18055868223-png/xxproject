"""Inquiry view (docs/opus/14): the inquirer's sheets of one open contract, read from the archive."""
import gzip
import json
import os

import pytest

from tools import astra_inquiry_view_v1 as iv

SHORT, LONG = "BTC-22OCT26-82500-P", "BTC-22OCT26-80500-P"
KEY = f"{SHORT}|{LONG}"
CASE = "cf_a6442e9274ee149a7d822147"
LATER = "cf_bbbbbbbbbbbbbbbbbbbbbbbb"


def sheet(question="K1?"):
    return {"questions": [{"id": "K1", "priority": "high", "question_cn": question,
                           "premises": [{"claim_cn": "c", "refs": ["L4.expiry_vol", "L9.missing"]}]}],
            "anomalies": [{"id": "A1", "refs": ["L3.space.gate"]}],
            "fact_brief": {"layers": [{"layer": "L4", "facts": [{"refs": ["L4.expiry_vol"]}]}],
                           "cross_layer": [{"refs": ["L1.contract"]}]}}


def case_file(big=False):
    return {"case_id": CASE, "asof_ms": 1, "contract_cn": "Put", "build": {"builder": "astra_case_file_v1@0.1.5"},
            "refs": {"L4.expiry_vol": {"layer": "L4", "index": 0}, "L3.space.gate": {"layer": "L3", "index": 0},
                     "L1.contract": {"layer": "L1", "index": 0}},
            "layers": {"L4": [{"label_cn": "本期波动", "quality": "ok", "values": {"atm_iv_pct": 26.81}}],
                       "L3": [{"label_cn": "Space 门", "values": {"blob": "x" * (5000 if big else 10)}}],
                       "L1": [{"label_cn": "合约", "values": {}}]}}


def write_archive(root, files, big=False):
    (root / "_state").mkdir(parents=True)
    (root / "_state" / "state.json").write_text(json.dumps({"contracts": {KEY: {"inquiry": files}}}), encoding="utf-8")
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        cid = rel.rsplit("_", 2)[-2] + "_" + rel.rsplit("_", 1)[-1][:-5]
        path.write_text(json.dumps({"case_id": cid, "asof_ms": 5, "status": "PARTIAL", "prompt_version": "inquiry_prompt@0.3",
                                    "issues": [{"code": "x_dropped"}], "attempts": [{"usage": {"total_tokens": 9}}],
                                    "sheet": sheet(rel)}), encoding="utf-8")
        case = root / ("cases/" + rel[len("inquiry/"):] + ".gz")
        case.parent.mkdir(parents=True, exist_ok=True)
        case.write_bytes(gzip.compress(json.dumps(case_file(big)).encode("utf-8")))


def test_view_latest_with_cited_entries(tmp_path):
    files = [f"inquiry/2026-10-20/22_put_{CASE}.json", f"inquiry/2026-10-21/14_put_{LATER}.json"]
    write_archive(tmp_path, files)
    out = iv.InquiryReader(tmp_path).view(SHORT, LONG)
    assert [s["trigger"] for s in out["sheets"]] == ["new_contract", "scheduled"]
    sel = out["selected"]
    assert sel["file"] == files[1] and sel["status"] == "PARTIAL" and sel["issues"][0]["code"] == "x_dropped"
    # every cited ref that exists, from questions, anomalies and the fact brief; unknown refs are skipped
    assert list(sel["entries"]) == ["L4.expiry_vol", "L3.space.gate", "L1.contract"]
    assert sel["entries"]["L4.expiry_vol"]["values"] == {"atm_iv_pct": 26.81}
    assert sel["case"]["builder"] == "astra_case_file_v1@0.1.5" and out["llm_call_count"] == 0


def test_pick_an_earlier_sheet_and_reject_unlisted(tmp_path):
    files = [f"inquiry/2026-10-20/22_put_{CASE}.json", f"inquiry/2026-10-21/14_put_{LATER}.json"]
    write_archive(tmp_path, files)
    reader = iv.InquiryReader(tmp_path)
    assert reader.view(SHORT, LONG, files[0])["selected"]["file"] == files[0]
    for bad in ("../_state/state.json", "inquiry/2026-10-20/02_put_cf_000000000000000000000000.json"):
        with pytest.raises(iv.InquiryViewError) as err:
            reader.view(SHORT, LONG, bad)
        assert err.value.status == 404


def test_bad_contract_names_are_rejected(tmp_path):
    write_archive(tmp_path, [])
    for short in ("BTC-22OCT26-82500-P/../x", "", None, "ETH-22OCT26-82500-P"):
        with pytest.raises(iv.InquiryViewError) as err:
            iv.InquiryReader(tmp_path).view(short, LONG)
        assert err.value.status == 400


def test_no_sheet_and_closed_contract(tmp_path):
    write_archive(tmp_path, [])
    out = iv.InquiryReader(tmp_path).view(SHORT, LONG)
    assert out["sheets"] == [] and out["selected"] is None
    other = iv.InquiryReader(tmp_path).view("BTC-21OCT26-81000-P", "BTC-21OCT26-79000-P")
    assert other["sheets"] == [] and other["selected"] is None


def test_large_values_are_truncated_and_missing_case_is_reported(tmp_path):
    files = [f"inquiry/2026-10-20/22_put_{CASE}.json"]
    write_archive(tmp_path, files, big=True)
    reader = iv.InquiryReader(tmp_path)
    entry = reader.view(SHORT, LONG)["selected"]["entries"]["L3.space.gate"]
    assert entry["truncated"] is True and entry["values"] is None and len(entry["values_preview"].encode()) <= iv.ENTRY_MAX_BYTES
    os.remove(tmp_path / ("cases/" + files[0][len("inquiry/"):] + ".gz"))
    sel = iv.InquiryReader(tmp_path).view(SHORT, LONG)["selected"]
    assert sel["case"] is None and sel["case_error"] == "FileNotFoundError" and sel["entries"] == {}


def test_latest_ref_skips_errors_and_follows_state_changes(tmp_path):
    files = [f"inquiry/2026-10-20/22_put_{CASE}.json"]
    write_archive(tmp_path, files)
    reader = iv.InquiryReader(tmp_path)
    ref = reader.latest_ref(KEY)
    assert ref["file"] == files[0] and ref["trigger"] == "new_contract" and ref["asof_ms"] == 5
    later = f"inquiry/2026-10-21/02_put_{LATER}.json"
    (tmp_path / later).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / later).write_text(json.dumps({"status": "ERROR", "error": "Timeout"}), encoding="utf-8")
    state = tmp_path / "_state" / "state.json"
    state.write_text(json.dumps({"contracts": {KEY: {"inquiry": files + [later]}}}), encoding="utf-8")
    os.utime(state, (os.stat(state).st_atime, os.stat(state).st_mtime + 5))
    assert [s["status"] for s in reader.sheets(KEY)] == ["PARTIAL", "ERROR"]
    assert reader.latest_ref(KEY)["file"] == files[0]     # a failed run is listed, never the default


def test_hostile_state_paths_are_ignored(tmp_path):
    files = [f"inquiry/2026-10-20/22_put_{CASE}.json"]
    write_archive(tmp_path, files)
    # the state file is writable by the archive service: only the sheet file pattern is ever read
    state = tmp_path / "_state" / "state.json"
    state.write_text(json.dumps({"contracts": {KEY: {"inquiry": files + ["../../etc/passwd", "_state/state.json",
                                                                        "inquiry/2026-10-20/../../x.json"]}}}),
                     encoding="utf-8")
    reader = iv.InquiryReader(tmp_path)
    assert [s["file"] for s in reader.sheets(KEY)] == files
    with pytest.raises(iv.InquiryViewError):
        reader.view(SHORT, LONG, "../../etc/passwd")


def test_closed_contract_reads_from_closed_recent(tmp_path):
    files = [f"inquiry/2026-10-20/22_put_{CASE}.json"]
    write_archive(tmp_path, files)
    state = tmp_path / "_state" / "state.json"
    state.write_text(json.dumps({"contracts": {}, "closed_recent": {KEY: {"inquiry": files}}}), encoding="utf-8")
    assert iv.InquiryReader(tmp_path).view(SHORT, LONG)["selected"]["file"] == files[0]


def test_summary_and_failed_sheet_and_trigger_field(tmp_path):
    files = [f"inquiry/2026-10-20/22_put_{CASE}.json"]
    write_archive(tmp_path, files)
    failed = f"inquiry/2026-10-21/02_put_{LATER}.json"
    (tmp_path / failed).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / failed).write_text(json.dumps({"status": "REJECTED", "sheet": None, "trigger": "scheduled"}), encoding="utf-8")
    state = tmp_path / "_state" / "state.json"
    state.write_text(json.dumps({"contracts": {KEY: {"inquiry": files + [failed]}}}), encoding="utf-8")
    reader = iv.InquiryReader(tmp_path)
    listed = reader.sheets(KEY)
    assert [(s["status"], s["has_sheet"], s["trigger_inferred"]) for s in listed] == [("PARTIAL", True, True), ("REJECTED", False, False)]
    out = reader.view(SHORT, LONG, summary=True)
    sel = out["selected"]
    assert sel["file"] == files[0] and "sheet" not in sel and "entries" not in sel
    assert sel["summary"]["question_count"] == 1 and sel["summary"]["by_priority"]["high"] == 1
    assert sel["summary"]["questions"][0]["question_cn"] == files[0] and sel["summary"]["anomaly_count"] == 1
    # the failed run can still be opened explicitly; it has no sheet and reads no case
    explicit = reader.view(SHORT, LONG, failed)["selected"]
    assert explicit["sheet"] is None and explicit["case"] is None and explicit["status"] == "REJECTED"


def test_oversized_and_malformed_files_fail_closed(tmp_path, monkeypatch):
    files = [f"inquiry/2026-10-20/22_put_{CASE}.json"]
    write_archive(tmp_path, files)
    reader = iv.InquiryReader(tmp_path)
    monkeypatch.setattr(iv, "CASE_MAX_BYTES", 50)      # the decompressed case exceeds this
    sel = reader.view(SHORT, LONG)["selected"]
    assert sel["case_error"] == "InquiryViewError" and sel["entries"] == {}
    monkeypatch.setattr(iv, "SHEET_MAX_BYTES", 10)
    fresh = iv.InquiryReader(tmp_path)
    assert fresh.sheets(KEY)[0]["status"] == "unreadable" and fresh.latest_ref(KEY) is None
    monkeypatch.setattr(iv, "STATE_MAX_BYTES", 5)
    with pytest.raises(iv.InquiryViewError):
        iv.InquiryReader(tmp_path).sheets(KEY)


def test_wrong_shapes_are_skipped_not_raised(tmp_path):
    files = [f"inquiry/2026-10-20/22_put_{CASE}.json"]
    write_archive(tmp_path, files)
    (tmp_path / files[0]).write_text(json.dumps([1, 2]), encoding="utf-8")          # a list, not an object
    assert iv.InquiryReader(tmp_path).sheets(KEY)[0]["status"] == "unreadable"
    (tmp_path / files[0]).write_text(json.dumps({"status": "OK", "sheet": {"questions": ["x", {"premises": ["y", {"refs": "L1"}]}],
                                                                          "fact_brief": [], "anomalies": None}}), encoding="utf-8")
    out = iv.InquiryReader(tmp_path).view(SHORT, LONG)
    assert out["selected"]["entries"] == {} and out["selected"]["status"] == "OK"
    (tmp_path / "_state" / "state.json").write_text(json.dumps({"contracts": [1], "closed_recent": {KEY: {"inquiry": "x"}}}),
                                                    encoding="utf-8")
    assert iv.InquiryReader(tmp_path).sheets(KEY) == []


def test_state_rewrite_within_one_mtime_is_seen(tmp_path):
    files = [f"inquiry/2026-10-20/22_put_{CASE}.json"]
    write_archive(tmp_path, files)
    reader = iv.InquiryReader(tmp_path)
    assert len(reader.sheets(KEY)) == 1
    state = tmp_path / "_state" / "state.json"
    before = os.stat(state)
    state.write_text(json.dumps({"contracts": {KEY: {"inquiry": []}}}), encoding="utf-8")
    os.utime(state, ns=(before.st_atime_ns, before.st_mtime_ns))                   # same mtime, different size
    assert reader.sheets(KEY) == []


def test_full_view_carries_the_experience_reading_and_summary_does_not(tmp_path):
    files = [f"inquiry/2026-10-20/22_put_{CASE}.json"]
    write_archive(tmp_path, files)
    path = tmp_path / files[0]
    body = json.loads(path.read_text(encoding="utf-8"))
    out = iv.InquiryReader(tmp_path).view(SHORT, LONG)
    assert out["selected"]["lens"]["enabled"] is False          # 0.3 sheets get no reading
    body["prompt_version"] = "inquiry_prompt@0.4"
    body["sheet"]["questions"][0]["question_cn"] = "短腿 82500 的本期 Gamma 集中会钉住还是加速？"
    path.write_text(json.dumps(body), encoding="utf-8")
    out = iv.InquiryReader(tmp_path).view(SHORT, LONG)
    reading = out["selected"]["lens"]
    assert reading["enabled"] is True and reading["questions"]["K1"]["card"] == "gamma_pin"
    summary = iv.InquiryReader(tmp_path).view(SHORT, LONG, summary=True)
    assert "lens" not in summary["selected"] and "lens" not in json.dumps(summary["selected"]["summary"])
