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


def test_page_reads_share_one_body_until_the_view_changes(tmp_path):
    wb, _, clock, _ = setup(tmp_path)
    calls = []
    original = wb.cached_view
    wb.cached_view = lambda **kw: calls.append(kw) or original(**kw)
    first = wb.view_body("page")
    second = wb.view_body("page")
    assert first[0] is second[0] and len(calls) == 1
    json.loads(first[0])                                  # a complete JSON document
    clock[0] += module.VIEW_BODY_TTL_MS + 1
    wb.view_body("page")
    assert len(calls) == 2                                # expired
    wb.invalidate_view_body()
    wb.view_body("page")
    assert len(calls) == 3                                # refresh or action moves the version


def test_memory_guard_levels_shed_load_and_record_changes(tmp_path):
    wb, _, clock, _ = setup(tmp_path)
    events = []
    wb.chain_audit = type("A", (), {"event": lambda self, kind, now, detail, view=None: events.append(kind)})()
    rss = [500 * 1_048_576]
    wb.rss_bytes = lambda: rss[0]
    assert wb.memory_guard()["level"] == "ok" and events == []
    body, _ = wb.view_body("page")
    rss[0] = 3000 * 1_048_576
    assert wb.memory_guard()["level"] == "hard" and events == ["memory_hard"]
    clock[0] += 60_000
    assert wb.view_body("page")[0] is body                # stale body rather than a rebuild
    wb.invalidate_view_body()
    try:
        wb.view_body("page")
        raise AssertionError("expected 503")
    except module.WorkbenchError as exc:
        assert exc.status_code == 503
    rss[0] = 400 * 1_048_576
    clock[0] += module.MEMORY_EVENT_INTERVAL_MS
    assert wb.memory_guard()["level"] == "ok" and events == ["memory_hard", "memory_ok"]
    view = wb.cached_view()
    assert view["runtime"]["memory"]["level"] == "ok" and view["runtime"]["threads"] >= 1


def test_busy_server_answers_503_instead_of_queueing_and_ignores_hangups():
    import http.client
    import threading
    from http.server import BaseHTTPRequestHandler

    release = threading.Event()
    entered = threading.Event()

    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            release.wait(5)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    server = module.WorkbenchHTTPServer(("127.0.0.1", 0), Slow, max_active=1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        first = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        first.request("GET", "/")
        assert entered.wait(5)
        second = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        second.request("GET", "/")
        busy = second.getresponse()
        assert busy.status == 503 and json.loads(busy.read())["code"] == "server_busy"
        release.set()
        assert first.getresponse().status == 200
        try:
            raise BrokenPipeError()
        except BrokenPipeError:
            server.handle_error(None, ("127.0.0.1", 1))  # silent
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def test_chain_audit_periodic_record_carries_runtime_memory():
    from tools import astra_chain_audit_v1 as chain
    body = chain.snapshot_record({"runtime": {"memory": {"level": "ok", "rss_mb": 512.0}, "threads": 7}},
                                 kind="periodic", now_ms=T)
    assert body["runtime"]["memory"]["rss_mb"] == 512.0


def test_a_retry_uses_a_fresh_key_and_a_transient_failure_is_retried_once(tmp_path):
    wb, _, _, _ = setup(tmp_path)
    keys = []

    def fake_review(candidate_id, key, **kw):
        keys.append(key)
        return {"status": "rejected", "reason": "leg_book_time_skew", "snapshot_id": "s"}

    wb.review_candidate = fake_review
    wb._scheduled_slot = lambda now: "2026-10-13T14"
    real_run = wb._run_scheduled_review
    assert wb._maybe_scheduled_review(T, wait=True) == "2026-10-13T14"
    slot = lambda: json.loads(wb.config.state_path.read_text(encoding="utf-8"))["scheduled_review"]["slots"]["2026-10-13T14"]
    assert slot()["status"] == "incomplete"                      # only transient failures
    assert wb._maybe_scheduled_review(T + 1, wait=True) == "2026-10-13T14"
    assert all(k.endswith("-r1") for k in keys[len(keys) // 2:]) and not any(k.endswith("-r1") for k in keys[:len(keys) // 2])
    assert wb._maybe_scheduled_review(T + 2, wait=True) is None   # once
    assert real_run is not None


def test_view_rebuild_and_refresh_invalidation_cannot_deadlock(tmp_path):
    import threading
    import time
    wb, _, _, _ = setup(tmp_path)
    entered = threading.Event()
    original = wb.cached_view

    def slow(**kw):
        entered.set()
        time.sleep(0.2)
        return original(**kw)

    wb.cached_view = slow
    done = {}

    def reader():
        done["r"] = len(wb.view_body("page")[0])

    def refresher():
        with wb._view_lock:               # what refresh() does while it replaces the view
            entered.wait(2)
            wb.invalidate_view_body()
        done["f"] = True

    threads = [threading.Thread(target=refresher), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert not any(t.is_alive() for t in threads) and done.get("f") and done.get("r")


def test_sustained_hard_memory_stops_the_watchdog_ping(tmp_path, monkeypatch):
    wb, _, _, _ = setup(tmp_path)
    pings = []
    monkeypatch.setattr(module, "_sd_notify", lambda message: pings.append(message) or True)
    wb.rss_bytes = lambda: 4000 * 1_048_576
    wb.refresh = lambda **kw: None
    wb._maybe_scheduled_review = lambda now: None
    passes = []
    original_wait = wb._background_stop.wait

    def wait(timeout):
        passes.append(1)
        return len(passes) >= module.HARD_PASSES_BEFORE_RESTART + 2

    wb._background_stop.wait = wait
    wb._sampler_running = True
    wb.config.cross_asset_enabled = False
    monkeypatch.setattr(module.threading, "Thread", lambda target, **kw: type("T", (), {"start": lambda self: target(), "is_alive": lambda self: False, "join": lambda self, timeout=None: None})() if kw.get("name") == "astra-workbench-v20" else type("N", (), {"start": lambda self: None, "is_alive": lambda self: False, "join": lambda self, timeout=None: None})())
    wb.start_background()
    assert pings.count("WATCHDOG=1") == module.HARD_PASSES_BEFORE_RESTART - 1
    wb._background_stop.wait = original_wait


def test_memory_eviction_keeps_the_current_candidates_proofs(tmp_path):
    wb, _, _, _ = setup(tmp_path)
    current = [item["snapshot"]["snapshot_id"] for item in wb._current_view["candidates"]]
    for key in ("old-1", "old-2", *current):
        wb._wpem_packages[key] = {"evidence_hash": key}
    wb.rss_bytes = lambda: 2000 * 1_048_576
    wb.memory_guard()
    assert set(wb._wpem_packages) == set(current)


def test_map_state_migrates_from_the_old_place(tmp_path):
    wb, _, _, _ = setup(tmp_path)
    wb.config.btc_map_state_path.unlink()
    wb._locked_state_update(lambda s: s.update(btc_map={"schema": "x", "updated_at_ms": 5}))
    fresh = module.AstraWorkbench(wb.config, clock_ms=lambda: T)
    assert fresh._map_prior_state()["updated_at_ms"] == 5


def test_one_broken_candidate_projection_does_not_fail_the_page(tmp_path):
    wb, _, _, _ = setup(tmp_path)
    wb._map_review_delta = lambda snapshot, state: (_ for _ in ()).throw(KeyError("boom"))
    view = wb.cached_view()
    item = view["candidates"][0]
    assert item["projection_error"] == "KeyError"
    assert "candidate_projection_failed" in item["review_gate"]["reasons"]
    assert wb.view_body("page")[0]
