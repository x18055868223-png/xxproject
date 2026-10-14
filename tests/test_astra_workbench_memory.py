"""Memory and liveness: slim hot state, map state apart, interrupted scheduled reviews, watchdog."""
import json
import socket

from tools import astra_workbench_v20 as module
from test_astra_locked_followup import T, setup

BLOBS = {"evidence_hash": "e" * 64, "composition_inputs": {"x": "y" * 1000}, "source_bindings": {"p": 1},
         "current_cross_section": {"status": "OK"}}


def receipt(key):
    return {"schema": module.SCHEMA, "status": "RESEARCH_OK", "purpose": "observation", "candidate_id": "c-" + key,
            "snapshot_id": "s-" + key, "review": {"status": "RESEARCH_OK", "wpem_evidence": dict(BLOBS)}}


def test_receipts_in_the_hot_state_drop_replay_blobs_and_old_states_are_slimmed_on_start(tmp_path):
    wb, _, _, _ = setup(tmp_path)
    wb._finish_review("k1", receipt("k1"), T)
    disk = json.loads(wb.config.state_path.read_text(encoding="utf-8"))
    for kept in (disk["manual_review"]["last_result"], disk["manual_review"]["idempotency"]["k1"]["result"]):
        ev = kept["review"]["wpem_evidence"]
        assert ev["evidence_hash"] == "e" * 64 and "composition_inputs" not in ev and "source_bindings" not in ev
    # a state written before the change is slimmed once at start
    disk["manual_review"]["idempotency"]["old"] = {"status": "settled", "result": receipt("old"), "updated_at_ms": T}
    wb.config.state_path.write_text(json.dumps(disk), encoding="utf-8")
    module.AstraWorkbench(wb.config, clock_ms=lambda: T)
    again = json.loads(wb.config.state_path.read_text(encoding="utf-8"))
    assert "composition_inputs" not in again["manual_review"]["idempotency"]["old"]["result"]["review"]["wpem_evidence"]


def test_map_state_is_kept_apart_and_written_only_when_it_changes(tmp_path):
    wb, _, _, _ = setup(tmp_path)
    path = wb.config.btc_map_state_path
    assert path.is_file()
    before_state = wb.config.state_path.stat().st_mtime_ns
    stamp = path.stat().st_mtime_ns
    btc_map = {"cutoff_at_ms": 1, "response_state": {"segments": {}, "events": []}}
    wb._store_btc_map_state(btc_map)
    first = path.stat().st_mtime_ns
    wb._store_btc_map_state(btc_map)
    assert path.stat().st_mtime_ns == first          # unchanged content is not rewritten
    assert wb.config.state_path.stat().st_mtime_ns == before_state
    assert wb._map_prior_state()["updated_at_ms"] == 1 and stamp <= first


def test_an_interrupted_scheduled_slot_is_named_and_retried_once_in_its_window(tmp_path):
    wb, _, _, _ = setup(tmp_path)
    wb._locked_state_update(lambda s: s.setdefault("scheduled_review", {}).setdefault("slots", {}).update(
        {"2026-10-13T14": {"started_at_ms": T, "status": "running", "sides": {}}}))
    wb2 = module.AstraWorkbench(wb.config, clock_ms=lambda: T)
    slot = json.loads(wb.config.state_path.read_text(encoding="utf-8"))["scheduled_review"]["slots"]["2026-10-13T14"]
    assert slot["status"] == "interrupted" and slot["interrupted_at_ms"] == T
    wb2._run_scheduled_review = lambda slot: None
    wb2._scheduled_slot = lambda now: "2026-10-13T14"
    assert wb2._maybe_scheduled_review(T + 1) == "2026-10-13T14"
    retried = json.loads(wb.config.state_path.read_text(encoding="utf-8"))["scheduled_review"]["slots"]["2026-10-13T14"]
    assert retried["status"] == "running" and retried["retries"] == 1
    wb2._locked_state_update(lambda s: s["scheduled_review"]["slots"]["2026-10-13T14"].update(status="interrupted"))
    assert wb2._maybe_scheduled_review(T + 2) is None   # only once


def test_sd_notify_talks_to_the_systemd_socket_and_is_silent_without_it(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert module._sd_notify("READY=1") is False
    if not hasattr(socket, "AF_UNIX"):
        return
    path = str(tmp_path / "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", path)
        assert module._sd_notify("WATCHDOG=1") is True
        assert server.recv(64) == b"WATCHDOG=1"
    finally:
        server.close()
