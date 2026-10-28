"""Load (2026-10-28): the ledger is replayed once and then only by its new rows, the lock sampler
does not redo the store's work, unlocked points are not decoded, caches follow file changes and
memory is handed back after pressure and after a scheduled review."""
import json

import pytest

from tools import astra_locked_candidate_store_v1 as locked_store
from tools import astra_underwriting_store_v20 as store
from tools import astra_workbench_v20 as module
from test_astra_locked_followup import lock, setup
from test_astra_workbench_v20 import T, good_review, make_workbench, native_gate, write_fmz


def _plain(state):
    return json.loads(json.dumps(state, sort_keys=True, default=sorted))


def test_a_review_replays_only_new_ledger_rows_and_the_ledger_equals_a_full_replay(tmp_path, monkeypatch):
    def transport(api_key, model, request, timeout, endpoint=None):
        packet = json.loads(request["messages"][1]["content"].split("冻结候选：", 1)[1])
        snapshot = wb.ledger.state()["snapshots"][packet["snapshot_id"]]["snapshot"]
        return {"choices": [{"finish_reason": "stop",
                             "message": {"content": json.dumps(good_review(snapshot), ensure_ascii=False)}}]}

    wb, snapshots = make_workbench(tmp_path, transport=transport)
    write_fmz(snapshots, native_gate())
    item = wb.refresh(now_ms=T)["candidates"][0]
    wb._ledger_state()
    replayed = []
    real = store._advance_state
    monkeypatch.setattr(store, "_advance_state", lambda base, rows: replayed.append(len(rows)) or real(base, rows))
    result = wb.review_candidate(item["snapshot"]["candidate_id"], "load-1", authorization="Bearer secret")
    assert result["status"] not in {"rejected", "ERROR"}
    # every replay during the review is incremental: no pass over the whole file
    total_rows = len(store.read_jsonl(wb.ledger.path))
    assert replayed and all(count < total_rows or total_rows <= 1 for count in replayed)
    assert sum(replayed) <= total_rows
    assert _plain(wb.ledger.state()) == _plain(store.rebuild_state(wb.ledger.path))
    assert _plain(wb._ledger_state()) == _plain(store.rebuild_state(wb.ledger.path))


def test_the_sampler_takes_the_point_id_from_the_store_and_only_decodes_locked_points(tmp_path, monkeypatch):
    wb, _, clock, snapshot = setup(tmp_path)
    reads = []
    points = wb._lockable_points
    real_get = type(points).get
    monkeypatch.setattr(type(points), "get", lambda self, key, default=None: reads.append(key) or real_get(self, key, default))
    wb._refresh_locked_candidates(wb._current_view, clock[0])
    assert reads == []  # nothing locked: no stored point is decompressed
    episode = lock(wb, snapshot)["episode"]
    clock[0] += wb.config.locked_sample_interval_ms
    wb.refresh(now_ms=clock[0], record_daily=False)
    detail = wb.locked_candidates.detail(episode["episode_id"])
    assert detail["episode"]["point_count"] == 2
    latest_id = detail["episode"]["latest_point_id"]
    assert locked_store._hash(detail["latest"]) == latest_id
    series = wb.locked_candidates.read_series(episode["episode_id"])
    assert latest_id in series


def test_store_append_returns_the_point_id_and_reuses_the_validated_base(tmp_path, monkeypatch):
    from test_astra_locked_candidate_store_v1 import point_at

    def run(root, *, cached):
        s = locked_store.LockedCandidateStore(root)
        episode_id = s.lock(point_at(T), request_key="r1", now_ms=T)["episode"]["episode_id"]
        calls, ids = [], []
        real = locked_store.validate_point
        monkeypatch.setattr(locked_store, "validate_point", lambda p: calls.append(1) or real(p))
        for step in (1, 2, 3):
            if not cached:
                s._delta_base_cache.clear()
            point = point_at(T + step * 300_000)
            calls.clear()
            result = s.append(episode_id, point, now_ms=T + step * 300_000)
            assert result["point_id"] == locked_store._hash(point) == result["latest_point_id"]
            ids.append((result["point_id"], len(calls)))
        monkeypatch.setattr(locked_store, "validate_point", real)
        return ids, {p.name: p.read_bytes() for p in s.points_dir.iterdir()}

    warm, warm_files = run(tmp_path / "warm", cached=True)
    cold, cold_files = run(tmp_path / "cold", cached=False)
    assert [i for i, _ in warm] == [i for i, _ in cold]
    assert warm_files == cold_files  # identical stored bytes (delta envelopes and full points)
    # cold: the point, the base, (the reconstructed point no longer); warm: the point only
    assert [n for _, n in cold] == [2, 2, 2]
    assert [n for _, n in warm] == [2, 1, 1]


def test_a_changed_base_file_is_validated_again(tmp_path, monkeypatch):
    import os
    from test_astra_locked_candidate_store_v1 import point_at
    s = locked_store.LockedCandidateStore(tmp_path)
    episode = s.lock(point_at(T), request_key="r1", now_ms=T)["episode"]
    s.append(episode["episode_id"], point_at(T + 300_000), now_ms=T + 300_000)
    base = s._point_path(episode["t0_baseline_point_id"])
    stat = base.stat()
    os.utime(base, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    calls = []
    real = locked_store.validate_point
    monkeypatch.setattr(locked_store, "validate_point", lambda p: calls.append(1) or real(p))
    s.append(episode["episode_id"], point_at(T + 600_000), now_ms=T + 600_000)
    assert len(calls) == 2


def test_recent_daily_snapshots_parse_once_per_file_change(tmp_path, monkeypatch):
    wb, _, _, _ = setup(tmp_path)
    path = wb.config.daily_snapshots_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"bjt_day": f"d{i}"}) + "\n" for i in range(3)), encoding="utf-8")
    calls = []
    real = module._read_jsonl_tail
    monkeypatch.setattr(module, "_read_jsonl_tail", lambda p, n: calls.append(n) or real(p, n))
    first = wb.recent_daily_snapshots(limit=15)
    first.append({"bjt_day": "mutated"})
    assert [r["bjt_day"] for r in wb.recent_daily_snapshots(limit=15)] == ["d0", "d1", "d2"]
    assert len(calls) == 1
    assert [r["bjt_day"] for r in wb.recent_daily_snapshots(limit=2)] == ["d1", "d2"] and len(calls) == 2
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"bjt_day": "d3"}) + "\n")
    assert [r["bjt_day"] for r in wb.recent_daily_snapshots(limit=15)][-1] == "d3" and len(calls) == 3


def test_memory_pressure_drops_the_ledger_once_and_hands_memory_back(tmp_path, monkeypatch):
    wb, _, _, _ = setup(tmp_path)
    released = []
    monkeypatch.setattr(module, "_release_memory", lambda: released.append(1))
    wb.ledger.path.parent.mkdir(parents=True, exist_ok=True)
    wb.ledger.path.touch()
    wb._ledger_state()
    assert wb._ledger_cache is not None and wb.ledger._cache is not None
    wb.rss_bytes = lambda: (wb.config.memory_soft_mb + 10) * 1_048_576
    assert wb.memory_guard()["level"] == "soft"
    assert wb._ledger_cache is None and wb.ledger._cache is None and released == [1]
    wb._ledger_state()
    assert wb.memory_guard()["level"] == "soft"
    # still under pressure: kept, or every pass would replay the whole ledger again
    assert wb._ledger_cache is not None and wb.ledger._cache is not None and released == [1, 1]
    wb.rss_bytes = lambda: 10 * 1_048_576
    assert wb.memory_guard()["level"] == "ok" and released == [1, 1]


def test_release_memory_never_raises():
    module._release_memory()


def test_series_baselines_keep_at_most_four_full_points(tmp_path, monkeypatch):
    wb, _, _, _ = setup(tmp_path)
    monkeypatch.setattr(wb.locked_candidates, "iter_points", lambda ids: iter([(ids[0], {"id": ids[0]}, None)]))
    monkeypatch.setattr(module.locked_compare, "point_metrics", lambda point, baseline: {"ok": True})
    for i in range(7):
        wb._warm_series_metrics({"t0_baseline_point_id": f"{i:064x}"}, {"n": i}, point_id=f"p{i}")
    assert len(wb._series_baselines) == module.SERIES_BASELINE_LIMIT == 4
