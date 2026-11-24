"""Regression and durability tests for DataEngine split/batching and UnderwritingLedger lazy index."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import astra_data_engine_v23 as de
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_v20 as core
from tools import astra_workbench_v20 as workbench
from test_astra_underwriting_store_v20 import payload as store_payload
from test_astra_workbench_v20 import T, good_review, make_workbench, native_gate, write_fmz


INDEX_PRODUCT = "deribit.btc.index.v1"
BOOK_PRODUCT = "deribit.btc.vertical.two_leg_book.v1"
INSTRUMENT_PRODUCT = "deribit.btc.option.instrument.v1"


def _plain(obj):
    return json.loads(json.dumps(obj, sort_keys=True, default=sorted))


def test_concurrent_multiprocess_reserve_request_enforces_budget_under_open_batch(tmp_path: Path) -> None:
    state_dir = tmp_path / "engine"
    engine = de.DataEngine(state_dir)
    limit = engine._product_budget_limit(engine._product(INDEX_PRODUCT))  # 20
    repo_root = str(Path(__file__).resolve().parents[1])

    worker_code = """
import sys
from tools.astra_data_engine_v23 import DataEngine, DataEngineError
e = DataEngine(sys.argv[1])
product = sys.argv[2]
now_ms = int(sys.argv[3])
loops = int(sys.argv[4])
ok = 0
exhausted = 0
for _ in range(loops):
    try:
        e.reserve_request(product, now_ms=now_ms, count=1)
        ok += 1
    except DataEngineError as exc:
        if exc.code == "FETCH_BUDGET_EXHAUSTED":
            exhausted += 1
        else:
            raise
print(f"{ok},{exhausted}")
"""

    with engine.batch_cache_writes():
        # Hold an unflushed cache mutation in memory while sibling processes reserve budget.
        engine.ingest(
            "fmz.spacegate.current.v1",
            {"state": "STRUCTURE_ELIGIBLE"},
            observation_end_ms=T,
            retrieved_at_ms=None,
            ingested_at_ms=T,
        )
        assert not engine.state_path.exists()
        engine.reserve_request(INDEX_PRODUCT, now_ms=T, count=2)
        env = dict(os.environ, PYTHONPATH=repo_root)
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", worker_code, str(state_dir), INDEX_PRODUCT, str(T), "10"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            for _ in range(3)
        ]
        child_ok = 0
        child_exhausted = 0
        for proc in procs:
            out, err = proc.communicate(timeout=20)
            assert proc.returncode == 0, err
            ok_s, ex_s = out.strip().splitlines()[-1].split(",")
            child_ok += int(ok_s)
            child_exhausted += int(ex_s)

        assert 2 + child_ok == limit
        assert child_ok + child_exhausted == 30
        with pytest.raises(de.DataEngineError) as exc:
            engine.reserve_request(INDEX_PRODUCT, now_ms=T, count=1)
        assert exc.value.code == "FETCH_BUDGET_EXHAUSTED"

    assert engine.state_path.exists()
    ctrl = json.loads(engine.control_path.read_text(encoding="utf-8"))
    minute = T // 60_000
    assert ctrl["fetch_budget"]["product"][f"{INDEX_PRODUCT}|{minute}"] == limit


def test_control_plane_quota_cooldown_backoff_survive_aborted_batch_and_restart(tmp_path: Path) -> None:
    state_dir = tmp_path / "engine"
    engine = de.DataEngine(state_dir)
    limit = engine._product_budget_limit(engine._product(INDEX_PRODUCT))

    class _AbortBatch(RuntimeError):
        pass

    with pytest.raises(_AbortBatch):
        with engine.batch_cache_writes():
            engine.reserve_request(INDEX_PRODUCT, now_ms=T, count=limit - 2)
            engine.set_provider_cooldown("deribit", until_ms=T + 60_000, reason="DERIBIT_HTTP_429", now_ms=T)
            with pytest.raises(de.DataEngineError) as fetch_err:
                engine.fetch(INDEX_PRODUCT, lambda: (_ for _ in ()).throw(RuntimeError("boom")), now_ms=T, scope={"index": "btc_usd"})
            assert fetch_err.value.code == "FETCH_ERROR"
            raise _AbortBatch("simulated abrupt interruption before explicit user action")

    de._STATE_CACHE.clear()
    restarted = de.DataEngine(state_dir)
    cd = restarted.provider_cooldown("deribit", now_ms=T + 5_000)
    assert cd is not None and cd["reason"] == "DERIBIT_HTTP_429"
    # Backoff from failed fetch is durable across restart without repeating external call.
    with pytest.raises(de.DataEngineError) as backoff_err:
        restarted.fetch(INDEX_PRODUCT, lambda: pytest.fail("loader called during backoff"), now_ms=T + 1_000, scope={"index": "btc_usd"})
    assert backoff_err.value.code == "FETCH_BACKOFF_ACTIVE"
    # Consumed budget (limit - 2 + 1 = limit - 1) survived: only 1 slot remains.
    restarted.reserve_request(INDEX_PRODUCT, now_ms=T, count=1)
    with pytest.raises(de.DataEngineError) as budget_err:
        restarted.reserve_request(INDEX_PRODUCT, now_ms=T, count=1)
    assert budget_err.value.code == "FETCH_BUDGET_EXHAUSTED"


def test_interrupted_write_on_control_or_state_preserves_prior_valid_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    e = de.DataEngine(tmp_path / "engine")
    e.ingest(INDEX_PRODUCT, {"index_price": 100_000.0}, observation_end_ms=T, retrieved_at_ms=T, scope={"s": 1})
    e.reserve_request(INDEX_PRODUCT, now_ms=T, count=1)
    state_before = e.state_path.read_bytes()
    ctrl_before = e.control_path.read_bytes()

    def boom(*_args, **_kwargs):
        raise OSError("simulated power loss")

    monkeypatch.setattr(de.os, "replace", boom)
    with pytest.raises(OSError):
        e.reserve_request(INDEX_PRODUCT, now_ms=T, count=1)
    with pytest.raises(OSError):
        e.ingest(INDEX_PRODUCT, {"index_price": 100_100.0}, observation_end_ms=T + 1000, retrieved_at_ms=T + 1000, scope={"s": 2})
    monkeypatch.undo()

    assert e.state_path.read_bytes() == state_before
    assert e.control_path.read_bytes() == ctrl_before
    assert not list(e.state_dir.glob("*.lock"))


def test_batch_flush_merges_concurrent_external_process_cache_writes(tmp_path: Path) -> None:
    state_dir = tmp_path / "engine"
    e1 = de.DataEngine(state_dir)
    e2 = de.DataEngine(state_dir)
    e1.ingest(INDEX_PRODUCT, {"index_price": 99_000.0}, observation_end_ms=T, retrieved_at_ms=T, scope={"k": "init"})

    with e1.batch_cache_writes():
        e1.ingest(INDEX_PRODUCT, {"index_price": 100_000.0}, observation_end_ms=T + 100, retrieved_at_ms=T + 100, scope={"k": "from_e1"})
        # External engine mutates disk while e1's batch is still buffered in memory.
        e2.ingest(INDEX_PRODUCT, {"index_price": 101_000.0}, observation_end_ms=T + 200, retrieved_at_ms=T + 200, scope={"k": "from_e2"})

    de._STATE_CACHE.clear()
    fresh = de.DataEngine(state_dir)
    assert fresh.read(INDEX_PRODUCT, now_ms=T + 500, usage="fetch", scope={"k": "from_e1"})["content"]["values"]["index_price"] == 100_000.0
    assert fresh.read(INDEX_PRODUCT, now_ms=T + 500, usage="fetch", scope={"k": "from_e2"})["content"]["values"]["index_price"] == 101_000.0


def test_semantic_scope_expiration_keeps_fresh_large_caches_and_prunes_expired(tmp_path: Path) -> None:
    e = de.DataEngine(tmp_path / "engine")
    now = T + 20 * 86_400_000
    large_universe = [{"instrument_name": f"BTC-OPT-{i}", "pad": "U" * 500} for i in range(1100)]

    with e.batch_cache_writes():
        # Large legitimate fresh payload (> 600 KB) must NOT be discarded by an arbitrary file-size cap.
        e.ingest(
            "deribit.btc.option.universe.v1",
            {"result": large_universe},
            observation_end_ms=now - 30_000,
            retrieved_at_ms=now - 30_000,
            scope={"currency": "BTC", "expired": False, "kind": "option"},
        )
        e.ingest(
            "fmz.bridge.snapshots.v1",
            {"source_hash": "h1"},
            observation_end_ms=now - 30_000,
            retrieved_at_ms=None,
            ingested_at_ms=now - 30_000,
        )
        # 124 stale two_leg_book scopes (> 2h old) + 4 fresh (< 5m).
        for i in range(124):
            ts = now - (7_200_000 + i * 60_000)
            e.ingest(BOOK_PRODUCT, {"idx": i}, observation_end_ms=ts, retrieved_at_ms=ts, scope={"old": i})
        for i in range(4):
            ts = now - (15_000 + i * 1000)
            e.ingest(BOOK_PRODUCT, {"fresh": i}, observation_end_ms=ts, retrieved_at_ms=ts, scope={"fresh": i})
        # Expired option contracts (> 1h past contract expiration_timestamp) vs active unexpired options.
        for i in range(40):
            ts = now - 600_000  # recently observed quote, but contract already expired 3h ago
            e.ingest(
                INSTRUMENT_PRODUCT,
                {"short": {"expiration_timestamp": now - 10_800_000}, "long": {"expiration_timestamp": now - 10_800_000}},
                observation_end_ms=ts,
                retrieved_at_ms=ts,
                scope={"expired_opt": i},
            )
        for i in range(5):
            ts = now - 600_000
            e.ingest(
                INSTRUMENT_PRODUCT,
                {"short": {"expiration_timestamp": now + 86_400_000}, "long": {"expiration_timestamp": now + 86_400_000}},
                observation_end_ms=ts,
                retrieved_at_ms=ts,
                scope={"active_opt": i},
            )

    assert e.state_path.stat().st_size > 500_000
    on_disk = json.loads(e.state_path.read_text(encoding="utf-8"))
    cur = on_disk["current"]
    assert on_disk["fetch_budget"] == {} and on_disk["provider_cooldowns"] == {}
    book_keys = [k for k in cur if k.startswith(BOOK_PRODUCT + "|")]
    inst_keys = [k for k in cur if k.startswith(INSTRUMENT_PRODUCT + "|")]
    assert len(book_keys) == 4
    assert len(inst_keys) == 5
    assert e.read("deribit.btc.option.universe.v1", now_ms=now, usage="fetch",
                  scope={"currency": "BTC", "expired": False, "kind": "option"})["usage_decision"]["can_use"]
    assert e.read("fmz.bridge.snapshots.v1", now_ms=now, usage="current_fact")["usage_decision"]["can_use"]


def test_underwriting_ledger_lazy_index_preserves_file_hash_and_exact_old_recent_lock_ui(tmp_path: Path) -> None:
    clock = [T]
    reviews: list[str] = []

    def transport(api_key, model, request, timeout, endpoint=None):
        packet = json.loads(request["messages"][1]["content"].split("冻结候选：", 1)[1])
        sid = packet["snapshot_id"]
        reviews.append(sid)
        snap = wb.ledger.get_snapshot(sid)
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(good_review(snap), ensure_ascii=False)}}]}

    wb, snapshots = make_workbench(tmp_path, clock=lambda: clock[0], transport=transport)
    # 1. Record an older version, attach a genuine review, then supersede with newer quotes.
    p_old = store_payload("manual")
    p_old["fact_context"] = {"pad": "A" * 50_000}
    c_old = wb.ledger.record_payload(p_old, now_ms=T - 300_000)
    old_sid = c_old["snapshot"]["snapshot_id"]
    p_mid = deepcopy(p_old)
    p_mid["market"]["short_book"]["bids"] = [[0.039, 0.5]]
    c_mid = wb.ledger.record_payload(p_mid, now_ms=T - 120_000)
    mid_sid = c_mid["snapshot"]["snapshot_id"]

    # Refresh current view -> triggers candidate capture + locks/reviews.
    write_fmz(snapshots, native_gate(ts=T - 1000), now=T)
    view = wb.refresh(now_ms=T, record_daily=False)
    cur_item = view["candidates"][0]
    cur_snap = cur_item["snapshot"]
    cur_sid = cur_snap["snapshot_id"]
    rev = wb.review_candidate(cur_snap["candidate_id"], "rev-1", authorization="Bearer secret")
    assert rev["status"] == "OK"

    # Restart a fresh Workbench + Ledger reading the exact same underwriting.jsonl untouched on disk.
    raw_bytes = wb.ledger.path.read_bytes()
    before_sha = hashlib.sha256(raw_bytes).hexdigest()

    restarted_wb, _ = make_workbench(tmp_path, clock=lambda: clock[0], transport=transport)
    idx = restarted_wb._ledger_index()
    assert set(idx["snapshots"]) == {old_sid, mid_sid, cur_sid}
    assert all("snapshot" not in entry for entry in idx["snapshots"].values())
    assert len(restarted_wb.ledger._snapshot_lru) == 0

    expected_full = store.rebuild_state(restarted_wb.ledger.path)
    for sid in (old_sid, mid_sid, cur_sid):
        assert _plain(restarted_wb.ledger.get_snapshot(sid)) == _plain(expected_full["snapshots"][sid]["snapshot"])
        assert _plain(restarted_wb.ledger.card_annex(sid)) == _plain(store.build_card_annex(expected_full, sid))
    assert _plain(restarted_wb.ledger.state()) == _plain(expected_full)

    # Lock, view, and settlement remain completely intact after restart using lazy index.
    restarted_wb.refresh(now_ms=clock[0], record_daily=False)
    locked = restarted_wb.lock_candidate(cur_snap["candidate_id"], cur_sid, "lock-1", authorization="Bearer secret")
    assert locked["created"] is True
    episode_id = locked["episode"]["episode_id"]
    detail = restarted_wb.locked_candidates.detail(episode_id)
    assert detail["baseline"]["candidate"]["snapshot"]["snapshot_id"] == cur_sid
    # Historical read-only page renders cleanly.
    page = json.loads(restarted_wb.view_body("page")[0])
    assert len(page["candidates"]) == 2
    assert hashlib.sha256(restarted_wb.ledger.path.read_bytes()).hexdigest() == before_sha


def test_underwriting_ledger_bounded_lru_and_crash_truncated_replace_recovery(tmp_path: Path) -> None:
    path = tmp_path / "underwriting.jsonl"
    ledger = store.UnderwritingLedger(path)
    sids = []
    for i in range(10):
        p = store_payload("manual")
        p["market"]["short_book"]["bids"] = [[round(0.040 - i * 0.001, 4), 0.5]]
        card = ledger.record_payload(p, now_ms=T + i)
        sids.append(card["snapshot"]["snapshot_id"])

    ledger.drop_cache()
    for sid in sids:
        assert ledger.get_snapshot(sid)["snapshot_id"] == sid
    assert len(ledger._snapshot_lru) <= store.SNAPSHOT_LRU_MAX

    # Unterminated partial trailing line must not poison index or corrupt cached snapshots.
    scratch = store.UnderwritingLedger(tmp_path / "scratch.jsonl")
    p_new = store_payload("fixed_round")
    p_new["market"]["short_book"]["bids"] = [[0.025, 0.5]]
    new_sid = scratch.record_payload(p_new, now_ms=T + 100)["snapshot"]["snapshot_id"]
    raw_new_line = scratch.path.read_bytes()
    with path.open("ab") as fh:
        fh.write(raw_new_line[: len(raw_new_line) // 2])
    assert set(ledger.index()["snapshots"]) == set(sids)
    with path.open("ab") as fh:
        fh.write(raw_new_line[len(raw_new_line) // 2 :])
    assert new_sid in ledger.index()["snapshots"]
    assert ledger.get_snapshot(new_sid)["snapshot_id"] == new_sid

    # Rewriting file with fewer rows clears stale LRU and rebuilds cleanly.
    kept_lines = path.read_bytes().splitlines(keepends=True)[:1]
    path.write_bytes(b"".join(kept_lines))
    idx_after = ledger.index()
    assert len(idx_after["snapshots"]) == 1
    assert len(ledger._snapshot_lru) == 0
    assert _plain(ledger.state()) == _plain(store.rebuild_state(path))


def test_legacy_control_state_is_migrated_when_cache_write_precedes_control_write(tmp_path: Path) -> None:
    state_dir = tmp_path / "engine"
    state_dir.mkdir(parents=True, exist_ok=True)
    minute = T // 60_000
    lock_key = de._state_key(INDEX_PRODUCT, {"index": "btc_usd"})
    legacy_payload = {
        "schema": de.ENGINE_SCHEMA,
        "current": {},
        "known_records": {},
        "health_events": [],
        "fetch_status": {
            lock_key: {"attempts": 2, "last_error_at_ms": T - 1000, "next_retry_at_ms": T + 30_000},
        },
        "fetch_budget": {
            "provider": {f"deribit|{minute}": 19},
            "product": {f"{INDEX_PRODUCT}|{minute}": 19},
        },
        "provider_cooldowns": {
            "deribit": {"provider_id": "deribit", "until_ms": T + 45_000, "reason": "DERIBIT_HTTP_429", "recorded_at_ms": T},
        },
    }
    legacy_path = state_dir / de.STATE_FILE
    legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    # Cache-only ingest (e.g. qualify_fmz / refresh hitting cooldown) runs BEFORE any control write.
    e = de.DataEngine(state_dir)
    assert not e.control_path.exists()
    with e.batch_cache_writes():
        e.ingest(
            "fmz.spacegate.current.v1",
            {"state": "STRUCTURE_ELIGIBLE"},
            observation_end_ms=T,
            retrieved_at_ms=None,
            ingested_at_ms=T,
        )

    assert e.control_path.exists()
    # Rollback readability invariant: legacy top-level schema keys stay intact on state_path.
    state_disk = json.loads(e.state_path.read_text(encoding="utf-8"))
    assert state_disk["schema"] == de.ENGINE_SCHEMA
    assert {"current", "known_records", "health_events", "fetch_status", "fetch_budget", "provider_cooldowns"} <= set(state_disk)

    de._STATE_CACHE.clear()
    restarted = de.DataEngine(state_dir)
    cd = restarted.provider_cooldown("deribit", now_ms=T + 1_000)
    assert cd is not None and cd["reason"] == "DERIBIT_HTTP_429"
    with pytest.raises(de.DataEngineError) as backoff_err:
        restarted.fetch(INDEX_PRODUCT, lambda: pytest.fail("loader called during migrated backoff"),
                        now_ms=T + 1_000, scope={"index": "btc_usd"})
    assert backoff_err.value.code == "FETCH_BACKOFF_ACTIVE"
    restarted.reserve_request(INDEX_PRODUCT, now_ms=T, count=1)  # 19 + 1 == 20
    with pytest.raises(de.DataEngineError) as exhausted:
        restarted.reserve_request(INDEX_PRODUCT, now_ms=T, count=1)
    assert exhausted.value.code == "FETCH_BUDGET_EXHAUSTED"


def test_mid_batch_crash_does_not_advance_last_success_ahead_of_state_path(tmp_path: Path) -> None:
    state_dir = tmp_path / "engine"
    e = de.DataEngine(state_dir)
    product_id = "btc.etf.flow.daily.v1"  # min_fetch_interval_ms=3600000, fetch.ttl_ms=0

    class _CrashMidBatch(RuntimeError):
        pass

    calls = [0]

    def loader():
        calls[0] += 1
        return {"values": {"net_flow": 100.0}, "observation_end_ms": T, "retrieved_at_ms": T}

    def boom_replace(*_args, **_kwargs):
        raise OSError("crash on exit flush")

    # Simulate abrupt termination before flush_cache commits state_path.
    orig_replace = de.os.replace
    with pytest.raises(_CrashMidBatch):
        with e.batch_cache_writes():
            rec = e.fetch(product_id, loader, now_ms=T)
            assert rec["fetch_decision"]["status"] == "FETCHED"
            # Within the active batch, second fetch hits min_fetch_interval_ms properly.
            again_in_batch = e.fetch(product_id, loader, now_ms=T + 10_000)
            assert again_in_batch["fetch_decision"]["status"] == "AWAIT_SOURCE_CHECK"
            de.os.replace = boom_replace
            try:
                raise _CrashMidBatch("kill mid-batch")
            finally:
                pass
    de.os.replace = orig_replace
    de._STATE_CACHE.clear()

    restarted = de.DataEngine(state_dir)
    # Budget WAS persisted immediately in control_path (used=1 of 2), while last_success was NOT persisted.
    assert restarted.fetch_status(product_id) is None
    rec2 = restarted.fetch(product_id, loader, now_ms=T + 10_000)
    assert rec2["fetch_decision"]["status"] == "FETCHED" and calls[0] == 2
    assert restarted.fetch_status(product_id)["last_success_at_ms"] == T + 10_000
    # After successful unbatched fetch, min_fetch_interval_ms suppresses redundant third call.
    rec3 = restarted.fetch(product_id, loader, now_ms=T + 20_000)
    assert rec3["fetch_decision"]["status"] == "AWAIT_SOURCE_CHECK" and calls[0] == 2


def test_ledger_multi_chunk_unterminated_review_and_hot_path_no_full_hydration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wb, snapshots = make_workbench(tmp_path, clock=lambda: T)
    write_fmz(snapshots, native_gate(ts=T - 1000), now=T)
    view = wb.refresh(now_ms=T, record_daily=False)
    snap = view["candidates"][0]["snapshot"]
    wb.ledger.record_snapshot(snap, now_ms=T)

    # 1. Hot paths (_ledger_index, refresh, _map_review_delta) must NOT populate full wb._ledger_cache.
    wb._locked_state_update(lambda s: s.setdefault("manual_review", {}).update({
        "last_result": {"candidate_id": snap["candidate_id"], "snapshot_id": snap["snapshot_id"], "status": "OK"},
    }))
    restarted, _ = make_workbench(tmp_path, clock=lambda: T)
    assert restarted._ledger_cache is None
    restarted._ledger_index()
    assert restarted._ledger_cache is None
    restarted.refresh(now_ms=T + 1000, record_daily=False)
    assert restarted._ledger_cache is None

    # 2. Multi-chunk indexing (< _INDEX_CHUNK_BYTES lowered for test) + unterminated trailing REVIEW_EVENT matches rebuild_state().
    scratch = store.UnderwritingLedger(tmp_path / "rev_scratch.jsonl")
    scratch.record_snapshot(snap, now_ms=T)
    review_line_payload = {
        "schema": store.EVENT_SCHEMA,
        "event_type": store.REVIEW_EVENT,
        "recorded_ts_ms": T + 50,
        "snapshot_id": snap["snapshot_id"],
        "candidate_id": snap["candidate_id"],
        "review_hash": store._hash({"ok": True}),
        "review": {"status": "OK", "summary_cn": "test"},
        "event_id": "e-unterm-1",
    }
    monkeypatch.setattr(store, "_INDEX_CHUNK_BYTES", 64)
    wb2, _ = make_workbench(tmp_path, clock=lambda: T)
    assert _plain(wb2.ledger.state()) == _plain(store.rebuild_state(wb2.ledger.path))
    with wb2.ledger.path.open("ab") as fh:
        fh.write(store._canonical(review_line_payload).encode("utf-8"))  # intentionally no trailing newline
    assert _plain(wb2.ledger.state()) == _plain(store.rebuild_state(wb2.ledger.path))

