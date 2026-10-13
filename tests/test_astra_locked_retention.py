"""Lock sampling runs on its own thread, and full point files are thinned by age while every
sample keeps its metrics row for the process chart."""
import pytest

from tools import astra_locked_candidate_store_v1 as locked_store
from test_astra_locked_followup import lock, setup

MINUTE = 60_000
SMALL = {"full_window_ms": 10 * MINUTE, "dense_bucket_ms": 30 * MINUTE, "dense_until_ms": 3 * 86_400_000,
         "sparse_bucket_ms": 60 * MINUTE, "sparse_until_ms": 30 * 86_400_000,
         "collapse_after_ms": 7 * 86_400_000, "drop_after_ms": 60 * 86_400_000}


def sampled_episode(tmp_path, samples=40):
    wb, _, clock, snapshot = setup(tmp_path)
    episode_id = lock(wb, snapshot)["episode"]["episode_id"]
    for _ in range(samples):
        clock[0] += MINUTE
        wb.refresh(now_ms=clock[0], record_daily=False)
    return wb, clock, episode_id


def point_files(wb):
    return {p.name[:-len(".json.gz")] for p in wb.locked_candidates.points_dir.glob("*.json.gz")}


def test_thinning_keeps_t0_latest_recent_window_and_every_metric(tmp_path):
    wb, clock, episode_id = sampled_episode(tmp_path)
    before = wb.locked_candidates.list()[0]
    files_before = point_files(wb)
    wb.compact_locked(clock[0])  # backfills series rows for every retained point
    report = wb.locked_candidates.compact(clock[0], retention=SMALL)
    after = wb.locked_candidates.list()[0]
    assert report["thinned"] > 0 and report["deleted_files"] > 0 and report["freed_bytes"] > 0
    assert after["point_count"] == before["point_count"]
    assert after["retained_point_count"] < before["point_count"]
    files = point_files(wb)
    assert {after["t0_baseline_point_id"], after["latest_point_id"]} <= files
    assert len(files) < len(files_before)
    # the recent window is kept in full
    rows = wb.locked_candidates.episode_observations(episode_id)["observations"]
    recent = [r for r in rows if r["kind"] == "point" and clock[0] - r["recorded_at_ms"] <= SMALL["full_window_ms"]]
    assert recent and not any(r.get("thinned") for r in recent)
    # the chart still has every sample, read from the series file
    series = wb.locked_candidate_series(episode_id)
    assert len(series["points"]) == before["point_count"]
    # a thinned sample cannot be opened as an evidence pair
    thinned = next(r for r in rows if r.get("thinned"))
    with pytest.raises(locked_store.LockedCandidateError) as raised:
        wb.locked_candidates.detail_pair(episode_id, after["t0_baseline_point_id"], thinned["point_id"])
    assert raised.value.code == "point_thinned"
    # sampling continues after thinning and the budget counts retained evidence only
    clock[0] += MINUTE
    wb.refresh(now_ms=clock[0], record_daily=False)
    assert wb.locked_candidates.list()[0]["point_count"] == before["point_count"] + 1


def test_a_point_without_its_metrics_row_is_not_thinned(tmp_path):
    wb, clock, episode_id = sampled_episode(tmp_path, samples=20)
    wb.locked_candidates._series_path(episode_id).unlink()
    report = wb.locked_candidates.compact(clock[0], retention=SMALL)
    assert report["thinned"] == 0 and report["deleted_files"] == 0


def test_ended_policy_is_folded_then_dropped(tmp_path):
    wb, clock, episode_id = sampled_episode(tmp_path, samples=30)
    wb.compact_locked(clock[0])
    wb.stop_locked_candidate(episode_id, authorization="Bearer secret")
    count = wb.locked_candidates.list()[0]["point_count"]
    later = clock[0] + 8 * 86_400_000
    report = wb.locked_candidates.compact(later, retention=SMALL)
    assert report["folded_rows"] > 0
    summary = wb.locked_candidates.list()[0]
    assert summary["point_count"] == count  # folded samples are still counted
    assert len(wb.locked_candidate_series(episode_id)["points"]) == count
    report = wb.locked_candidates.compact(clock[0] + 61 * 86_400_000, retention=SMALL)
    assert report["dropped_episodes"] == [episode_id]
    assert wb.locked_candidates.list() == []
    assert point_files(wb) == set()
    assert not wb.locked_candidates._series_path(episode_id).exists()


def test_the_sampler_samples_from_the_cached_view_and_refresh_defers_to_it(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    episode_id = lock(wb, snapshot)["episode"]["episode_id"]
    wb._sampler_running = True
    clock[0] += 2 * MINUTE
    wb.refresh(now_ms=clock[0], record_daily=False)
    assert wb.locked_candidates.list()[0]["point_count"] == 1  # refresh no longer samples
    wb.sample_locked_once(clock[0])
    assert wb.locked_candidates.list()[0]["point_count"] == 2
    assert wb.locked_candidates.read_series(episode_id)  # metrics row written at sampling time
    # within the interval nothing new is taken; slightly early (capture time) still samples
    wb.sample_locked_once(clock[0] + 10_000)
    assert wb.locked_candidates.list()[0]["point_count"] == 2
    clock[0] += MINUTE - 10_000
    wb.refresh(now_ms=clock[0], record_daily=False)
    wb.sample_locked_once(clock[0])
    assert wb.locked_candidates.list()[0]["point_count"] == 3


def test_a_sampler_capture_does_not_touch_the_refresh_buffers(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    lock(wb, snapshot)
    wb._sampler_running = True
    sentinel = {"refresh-snapshot": {"raw": True}}
    wb._locked_payloads.update(sentinel)
    wb._wpem_pending_payloads.update(sentinel)
    # force an own-contract capture by hiding the refresh's lockable points
    wb._lockable_points = type(wb._lockable_points)()
    clock[0] += 2 * MINUTE
    wb.sample_locked_once(clock[0])
    assert wb.locked_candidates.list()[0]["point_count"] == 2
    assert wb._locked_payloads == sentinel and wb._wpem_pending_payloads == sentinel


def test_compaction_relieves_an_exhausted_byte_budget(tmp_path):
    wb, clock, _episode_id = sampled_episode(tmp_path)
    wb.compact_locked(clock[0])
    store = wb.locked_candidates
    store.max_bytes = store._total_bytes() - 1  # already over budget: appends would stop
    report = store.compact(clock[0], retention=SMALL)
    assert report["deleted_files"] > 0 and store._total_bytes() <= store.max_bytes


def test_a_kept_delta_keeps_its_base_whoever_owns_it(tmp_path):
    wb, clock, episode_id = sampled_episode(tmp_path, samples=5)
    store = wb.locked_candidates
    summary = store.list()[0]
    t0, latest = summary["t0_baseline_point_id"], summary["latest_point_id"]
    assert store._delta_base_id(t0) is None
    assert store._delta_base_id(latest) == t0  # later samples are stored as deltas on t0
    # even if the index no longer names t0, a kept delta still protects its base file
    state = store._read_state()
    state["episodes"][episode_id]["t0_baseline_point_id"] = "f" * 64  # e.g. another policy's file
    state["episodes"][episode_id]["observations"] = [
        r for r in state["episodes"][episode_id]["observations"] if r.get("point_id") != t0]
    assert t0 in store._referenced_point_ids(state)


def test_a_budget_paused_sample_writes_no_chart_row(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    episode_id = lock(wb, snapshot)["episode"]["episode_id"]
    wb._sampler_running = True
    wb.locked_candidates.max_samples = 1  # the t0 point already fills it
    clock[0] += 2 * MINUTE
    wb.sample_locked_once(clock[0])
    assert wb.locked_candidates.list()[0]["status"] == "paused"
    stored = {r["point_id"] for r in wb.locked_candidates.episode_observations(episode_id)["observations"]
              if r["kind"] == "point"}
    assert set(wb.locked_candidates.read_series(episode_id)) <= stored  # t0's own row at most
    assert len(wb.locked_candidate_series(episode_id)["points"]) == 1


def test_sampler_gates_on_the_pass_start_not_on_when_a_capture_finished(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    lock(wb, snapshot)
    wb._sampler_running = True
    start = clock[0] + 2 * MINUTE
    clock[0] = start + 20_000  # this capture finishes 20 s into the pass
    wb.sample_locked_once(start)
    assert wb.locked_candidates.list()[0]["point_count"] == 2
    clock[0] = start + MINUTE
    wb.refresh(now_ms=clock[0], record_daily=False)
    wb.sample_locked_once(start + MINUTE)
    assert wb.locked_candidates.list()[0]["point_count"] == 3


def test_a_paused_policy_still_ends_at_expiry(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    lock(wb, snapshot)
    wb._sampler_running = True
    wb.locked_candidates.max_samples = 1
    clock[0] += 2 * MINUTE
    wb.sample_locked_once(clock[0])
    summary = wb.locked_candidates.list()[0]
    assert summary["status"] == "paused"
    wb.sample_locked_once(summary["contract"]["expiry_ms"] + 1)
    summary = wb.locked_candidates.list()[0]
    assert summary["status"] == "expired" and summary["stopped_at_ms"] == summary["contract"]["expiry_ms"] + 1


def test_an_unreadable_kept_file_stops_deletion_for_that_pass(tmp_path, monkeypatch):
    wb, clock, _episode_id = sampled_episode(tmp_path)
    wb.compact_locked(clock[0])
    files_before = point_files(wb)

    def unreadable(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(locked_store.gzip, "open", unreadable)
    report = wb.locked_candidates.compact(clock[0], retention=SMALL)
    assert report["thinned"] > 0 and report["deleted_files"] == 0
    assert point_files(wb) == files_before
    monkeypatch.undo()
    assert wb.locked_candidates.compact(clock[0], retention=SMALL)["deleted_files"] > 0


def test_a_sampler_capture_leaves_no_entries_in_the_page_caches(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    lock(wb, snapshot)
    wb._sampler_running = True
    wb._lockable_points = type(wb._lockable_points)()  # force an own-contract capture
    packages_before = set(wb._wpem_packages)
    clock[0] += 2 * MINUTE
    wb.sample_locked_once(clock[0])
    assert wb.locked_candidates.list()[0]["point_count"] == 2
    shown = {c["snapshot"]["snapshot_id"] for c in wb._current_view["candidates"]}
    assert set(wb._lockable_points) <= shown
    assert set(wb._wpem_packages) <= packages_before | shown
