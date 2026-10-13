"""Workbench state stays bounded: receipt compaction, cached reads, daily snapshot tail and rotation."""
import gzip
import json

from tools import astra_workbench_v20 as module
from test_astra_workbench_v20 import T, make_workbench


def big_result(i):
    return {"status": "RESEARCH_OK", "candidate_id": f"c{i}", "snapshot_id": f"s{i}", "purpose": "observation",
            "review": {"status": "RESEARCH_OK", "applicability": "CONTESTED", "response_protocol": "evidence_points@2.6.4",
                       "decision_trace": {"pad": "x" * 5000}}}


def test_only_the_newest_receipts_stay_full_and_compacted_ones_never_display(tmp_path):
    clock = [T]
    wb, _ = make_workbench(tmp_path, api_key=None, clock=lambda: clock[0])
    for i in range(module.FULL_RECEIPTS_KEPT + 3):
        clock[0] += 1000
        wb._store_idempotency(f"k{i}", big_result(i))
    idem = wb._load_state()["manual_review"]["idempotency"]
    compacted = sorted(k for k, e in idem.items() if e["result"].get("compacted"))
    assert compacted == ["k0", "k1", "k2"]
    assert "decision_trace" not in json.dumps(idem["k0"]) and idem["k0"]["result"]["review"]["applicability"] == "CONTESTED"
    assert {row["idempotency_key"] for row in wb._manual_review_receipt_history()} == {f"k{i}" for i in range(3, 9)}
    # Replaying an old key still answers from the record instead of calling a model again.
    assert wb._idempotency_result("k0")["status"] == "RESEARCH_OK"


def test_existing_oversized_state_is_compacted_at_start(tmp_path):
    wb, _ = make_workbench(tmp_path, api_key=None)
    state = wb._load_state()
    state["manual_review"]["idempotency"] = {f"k{i}": {"status": "settled", "result": big_result(i), "updated_at_ms": T + i}
                                            for i in range(10)}
    wb._save_state(state)
    restarted = module.AstraWorkbench(wb.config, clock_ms=lambda: T)
    idem = restarted._load_state()["manual_review"]["idempotency"]
    assert sum(1 for e in idem.values() if not e["result"].get("compacted")) == module.FULL_RECEIPTS_KEPT


def test_read_only_state_parse_is_cached_until_the_file_changes(tmp_path):
    wb, _ = make_workbench(tmp_path, api_key=None)
    wb._store_idempotency("k1", big_result(1))
    first = wb._read_state_cached()
    assert wb._read_state_cached() is first
    wb._store_idempotency("k2", big_result(2))
    assert "k2" in wb._read_state_cached()["manual_review"]["idempotency"]


def test_tail_reader_matches_full_parse(tmp_path):
    path = tmp_path / "rows.jsonl"
    rows = [{"i": i, "pad": "y" * (300_000 if i % 7 == 0 else 10)} for i in range(40)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")  # no trailing newline
    assert module._read_jsonl_tail(path, 15) == rows[-15:]
    assert module._read_jsonl_tail(path, 100) == rows
    assert module._read_jsonl_tail(tmp_path / "missing.jsonl", 5) == []


def test_daily_snapshots_rotate_into_an_archive_and_keep_recent(tmp_path, monkeypatch):
    wb, _ = make_workbench(tmp_path, api_key=None)
    monkeypatch.setattr(module, "DAILY_SNAPSHOT_ROTATE_BYTES", 2000)
    rows = [{"bjt_day": f"d{i}", "pad": "z" * 200} for i in range(30)]
    path = wb.config.daily_snapshots_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    wb._rotate_daily_snapshots()
    assert [r["bjt_day"] for r in wb.recent_daily_snapshots(limit=100)] == [f"d{i}" for i in range(15, 30)]
    archives = list(path.parent.glob("daily_snapshots-archive-*.jsonl.gz"))
    assert len(archives) == 1
    assert [json.loads(line)["bjt_day"] for line in gzip.decompress(archives[0].read_bytes()).splitlines()] == [r["bjt_day"] for r in rows]


def test_each_refresh_records_its_step_timing(tmp_path):
    wb, _ = make_workbench(tmp_path, api_key=None)
    view = wb.refresh(now_ms=T)
    timing = view["refresh_timing"]
    assert {"fact_gate_map", "candidates", "wpem", "locked"} <= set(timing["steps_ms"])
    assert timing["total_ms"] == sum(timing["steps_ms"].values()) >= 0
    from tools import astra_chain_audit_v1 as chain
    record = chain.snapshot_record(view, kind="periodic", now_ms=T)
    assert record["refresh"]["total_ms"] == timing["total_ms"]
