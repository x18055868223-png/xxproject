"""Main-chain audit log: hash chain, cadence, events, retention, workbench wiring."""
from http.server import ThreadingHTTPServer
import json
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from tools import astra_chain_audit_v1 as audit
from tools import astra_workbench_v20 as module
from test_astra_workbench_v20 import T, make_workbench, native_gate, write_fmz

BJT_NOON = 1_791_777_600_000  # 2026-10-12 12:00 Beijing
MIN = 60_000


def view(state="STRUCTURE_BLOCKED", spot=84_000.0):
    return {"schema": "astra_workbench@2.0.0", "updated_at_ms": BJT_NOON,
            "market_context": {"current_price_usd": spot, "status": "current", "strategy_version": "2.0.6",
                               "market_facts": {"m_die": {"score": 0.0, "move_shape": "NO_MOVE"}}},
            "space_gate": {"state": state, "eligible": state == "STRUCTURE_ELIGIBLE", "reason_codes": ["X"],
                           "anchor": {"score": 41.5}},
            "candidates": [], "locked_candidates": {"episodes": []}}


def test_header_periodic_slots_transition_and_chain(tmp_path):
    log = audit.ChainAuditLog(tmp_path, interval_ms=15 * MIN)
    assert [r["kind"] for r in log.observe(view(), BJT_NOON)] == ["periodic"]
    assert log.observe(view(), BJT_NOON + 5 * MIN) == []          # same 15-minute slot
    kinds = [r["kind"] for r in log.observe(view("STRUCTURE_ELIGIBLE"), BJT_NOON + 16 * MIN)]
    assert kinds == ["space_gate_transition", "periodic"]
    rows = audit.read_records(tmp_path / "chain-20261012.jsonl")
    assert rows[0]["kind"] == "header" and "field_guide_cn" in rows[0]
    assert [r["seq"] for r in rows] == [0, 1, 2, 3]
    assert audit.verify_rows(rows)["ok"]
    assert rows[2]["event"] == {"from": "STRUCTURE_BLOCKED", "to": "STRUCTURE_ELIGIBLE", "reasons": ["X"],
                                "anchor_score": 41.5, "spot_usd": 84_000.0}
    rows[1]["market"]["spot_usd"] = 1.0                            # tampering breaks the chain
    assert audit.verify_rows(rows) == {"ok": False, "at": 1, "error": "record_hash_mismatch"}


def test_restart_recovers_slot_gate_and_daily(tmp_path):
    log = audit.ChainAuditLog(tmp_path, interval_ms=15 * MIN)
    eleven_pm = BJT_NOON + 11 * 60 * MIN
    assert {r["kind"] for r in log.observe(view(), eleven_pm)} == {"periodic", "daily"}
    again = audit.ChainAuditLog(tmp_path, interval_ms=15 * MIN)
    assert again.observe(view(), eleven_pm + MIN) == []            # same slot, same day, same gate
    assert len(audit.read_records(tmp_path / audit.DAILY_FILE)) == 2  # header + one daily
    assert audit.verify_rows(audit.read_records(tmp_path / "chain-20261012.jsonl"))["ok"]


def test_retention_prunes_old_day_files_but_keeps_daily(tmp_path):
    log = audit.ChainAuditLog(tmp_path, interval_ms=15 * MIN, retention_days=2)
    old = tmp_path / "chain-20261001.jsonl"
    log.observe(view(), BJT_NOON)
    old.write_text("{}\n", encoding="utf-8")
    (tmp_path / audit.DAILY_FILE).write_text("", encoding="utf-8")
    log.observe(view(), BJT_NOON + 20 * MIN)
    assert not old.exists() and (tmp_path / audit.DAILY_FILE).exists()


def test_write_failure_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    log = audit.ChainAuditLog(blocker / "sub")
    assert log.observe(view(), BJT_NOON) == []
    assert log.last_error is not None


def test_read_file_rejects_other_names(tmp_path):
    log = audit.ChainAuditLog(tmp_path)
    log.observe(view(), BJT_NOON)
    assert log.read_file("chain-20261012.jsonl").startswith(b"{")
    for bad in ("../x", "chain-2026.jsonl", "workbench_state.json"):
        with pytest.raises(ValueError):
            log.read_file(bad)


def test_summarize_and_compare_windows(tmp_path):
    log = audit.ChainAuditLog(tmp_path, interval_ms=15 * MIN)
    for i, state in enumerate(["STRUCTURE_BLOCKED", "STRUCTURE_BLOCKED", "STRUCTURE_ELIGIBLE", "STRUCTURE_ELIGIBLE"]):
        log.observe(view(state), BJT_NOON + i * 15 * MIN)
    rows = audit._rows_from([str(tmp_path / "chain-20261012.jsonl")])
    summary = audit.summarize(rows)
    assert summary["snapshots"] == 4 and summary["space_gate_open_share"] == 0.5
    assert summary["events"] == {"space_gate_transition": 1}
    assert audit.compare(summary, summary)["space_gate_open_share"] == {"a": 0.5, "b": 0.5}


def test_workbench_refresh_writes_candidates_and_serves_files(tmp_path):
    wb, snapshots = make_workbench(tmp_path, api_key=None)
    write_fmz(snapshots, native_gate())
    wb.refresh(now_ms=T)
    files = sorted((wb.config.state_dir / "chain_audit").glob("chain-*.jsonl"))
    assert len(files) == 1
    rows = audit.read_records(files[0])
    record = rows[-1]
    assert record["kind"] == "periodic" and audit.verify_rows(rows)["ok"]
    assert {c["side"] for c in record["candidates"]} == {"put", "call"}
    put = next(c for c in record["candidates"] if c["side"] == "put")
    assert isinstance(put["short_distance_pct"], float) and put["mu_ref_btc"] is None
    assert record["versions"]["review_prompt"].startswith("evidence_synthesis_prompt@")
    wb.record_action("lock", {"created": True, "episode": {"episode_id": "lce_" + "0" * 24, "status": "active"},
                              "llm_call_count": 0}, request={"candidate_id": "c1", "snapshot_id": "s1"})
    event = audit.read_records(files[0])[-1]
    assert event["kind"] == "lock" and event["event"]["episode_id"] == "lce_" + "0" * 24
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/api/workbench/chain-audit"
    try:
        with urlopen(base) as response:
            index = json.load(response)
        assert index["files"][0]["name"] == files[0].name and index["files"][0]["chain_ok"]
        with urlopen(base + "/" + files[0].name) as response:
            assert response.headers["Content-Type"].startswith("application/x-ndjson")
            assert response.read() == files[0].read_bytes()
        with pytest.raises(HTTPError) as failure:
            urlopen(base + "/workbench_state.json")
        assert failure.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
