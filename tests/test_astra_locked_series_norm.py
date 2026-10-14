"""Lock series files: one row per point in time order, latest never moves backwards, old files repaired once."""
import json

from tools import astra_locked_candidate_compare_v1 as compare

from tools import astra_locked_candidate_store_v1 as locked_store
from test_astra_locked_followup import lock, setup
from test_astra_locked_retention import MINUTE, SMALL, sampled_episode

PID = lambda n: f"{n:064x}"  # noqa: E731


def row(n, ts, version):
    return {"point_id": PID(n), "step": n, "recorded_at_ms": ts, "metrics": {"metrics_version": version, "x": n}}


def lines(store, episode_id):
    return [json.loads(l) for l in store._series_path(episode_id).read_text(encoding="utf-8").splitlines()]


def test_series_upsert_is_idempotent_versioned_and_sorted(tmp_path):
    wb, _, _, snapshot = setup(tmp_path)
    store = wb.locked_candidates
    episode_id = lock(wb, snapshot)["episode"]["episode_id"]
    store._series_path(episode_id).unlink(missing_ok=True)
    store.append_series(episode_id, [row(1, 100, 2), row(2, 200, 2)])
    store.append_series(episode_id, [row(2, 200, 2)])                     # same row again
    assert [r["point_id"] for r in lines(store, episode_id)] == [PID(1), PID(2)]
    store.append_series(episode_id, [row(3, 300, 1)])
    store.append_series(episode_id, [row(0, 50, 2), row(3, 300, 2)])      # older row + upgrade
    got = lines(store, episode_id)
    assert [r["recorded_at_ms"] for r in got] == [50, 100, 200, 300]
    assert got[-1]["metrics"]["metrics_version"] == 2
    store.append_series(episode_id, [row(3, 300, 1)])                     # never downgrades
    assert lines(store, episode_id)[-1]["metrics"]["metrics_version"] == 2


def test_a_point_not_newer_than_the_latest_is_not_appended(tmp_path):
    wb, clock, episode_id = sampled_episode(tmp_path, samples=3)
    store = wb.locked_candidates
    before = store.list()[0]
    latest_id = before["latest_point_id"]
    points = [r for r in store.episode_observations(episode_id)["observations"] if r.get("kind") == "point"]
    _, earlier, _ = next(iter(store.iter_points([points[-2]["point_id"]])))
    stale = {**earlier, "recorded_at_ms": earlier["recorded_at_ms"] + 1}   # a refresh point older than latest
    out = store.append(episode_id, stale, now_ms=clock[0] + 1)
    assert out.get("stale_point") is True
    after = store.list()[0]
    assert after["latest_point_id"] == latest_id and after["point_count"] == before["point_count"]


def test_normalize_repairs_an_old_file_once_with_a_backup(tmp_path):
    wb, clock, episode_id = sampled_episode(tmp_path, samples=4)
    store = wb.locked_candidates
    path = store._series_path(episode_id)
    good = lines(store, episode_id)
    # what the old append produced: v1 rows, then the same points again as v2, one out of order
    old = [{**r, "metrics": {**r["metrics"], "metrics_version": 1}} for r in good]
    messy = [good[-1], *old, *good]
    path.write_text("".join(json.dumps(r) + "\n" for r in messy), encoding="utf-8")
    report = wb.normalize_locked_series_once()
    assert report["rewritten"] == 1 and report["removed"] == len(messy) - len(good)
    fixed = lines(store, episode_id)
    assert [r["point_id"] for r in fixed] == [r["point_id"] for r in sorted(good, key=lambda r: r["recorded_at_ms"])]
    assert all(r["metrics"].get("metrics_version") == compare.SERIES_METRICS_VERSION for r in fixed)
    backups = list(store.series_dir.glob(".bak-*/" + path.name))
    assert len(backups) == 1 and len(backups[0].read_text(encoding="utf-8").splitlines()) == len(messy)
    assert wb.normalize_locked_series_once() == {"files": 0, "removed": 0, "rewritten": 0}   # marker: once


def test_retention_does_not_thin_a_point_whose_row_is_outdated(tmp_path):
    wb, clock, episode_id = sampled_episode(tmp_path, samples=30)
    store = wb.locked_candidates
    wb.compact_locked(clock[0])
    path = store._series_path(episode_id)
    rows = lines(store, episode_id)
    path.write_text("".join(json.dumps({**r, "metrics": {**r["metrics"], "metrics_version": 1}}) + "\n" for r in rows),
                    encoding="utf-8")
    report = store.compact(clock[0], retention=SMALL, series_current=lambda r: r["metrics"].get("metrics_version") == 2)
    assert report["thinned"] == 0
