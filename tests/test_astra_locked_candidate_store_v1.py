import copy
from concurrent.futures import ThreadPoolExecutor
import gzip

import pytest

from tools import astra_candidate_asof_v1 as packets
from tools import astra_locked_candidate_store_v1 as locked
from tools import astra_underwriting_v20 as core
from tests.test_astra_candidate_asof_v1 import snapshot_at
from tests.test_astra_underwriting_v20 import T


def point_at(now=T, *, side="put", quantity=0.1, risk=False):
    snapshot, raw = snapshot_at(now=now, side=side, quantity=quantity)
    if risk is False:
        raw["risk"] = None
        snapshot = core.evaluate(raw)
    packet = packets.build_packet(snapshot, raw_payload=raw)
    return {
        "candidate": {"candidate_id": snapshot["candidate_id"], "snapshot": copy.deepcopy(snapshot),
                      "public_projection": "single-candidate-fixture"},
        "packet": packet,
        "recorded_at_ms": now,
    }


def files(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def stored_json(path):
    return locked.store.json.loads(gzip.decompress(path.read_bytes()).decode("utf-8-sig"))


def write_stored_json(path, value):
    path.write_bytes(gzip.compress(locked._canonical_bytes(value), compresslevel=9, mtime=0))


def delta_envelope(base_point, target_point, *, base_point_id, target_point_id):
    sets, deletes = locked._build_delta_patch(base_point, target_point)
    checked = locked.validate_point(target_point)
    return {
        "schema": locked.POINT_DELTA_SCHEMA,
        "point_id": target_point_id,
        "base_point_id": base_point_id,
        "candidate_id": checked["candidate_id"],
        "contract_key": checked["contract_key"],
        "patch": {"set": sets, "delete": deletes},
    }


def test_missing_mu_and_no_review_packet_can_lock(tmp_path):
    point = point_at(risk=False)
    store = locked.LockedCandidateStore(tmp_path)
    result = store.lock(point, request_key="first", now_ms=T)
    assert result["created"] is True
    detail = store.detail(result["episode"]["episode_id"])
    assert detail["baseline"]["packet"]["replay"]["mu_ref_btc"] is None
    assert detail["episode"]["point_count"] == 1
    assert detail["episode"]["status"] == "active"


def test_duplicate_request_default_existing_and_new_reference_restart(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    point = point_at()
    first = store.lock(point, request_key="r1", now_ms=T)
    repeated = store.lock(point, request_key="r1", now_ms=T)
    other_key = store.lock(point, request_key="r2", now_ms=T)
    fresh = store.lock(point, request_key="r3", now_ms=T, new_reference=True)
    restarted = locked.LockedCandidateStore(tmp_path)
    assert repeated == {"created": False, "episode": first["episode"]}
    assert other_key["created"] is False
    assert other_key["episode"]["episode_id"] == first["episode"]["episode_id"]
    assert fresh["created"] is True
    assert fresh["episode"]["episode_id"] != first["episode"]["episode_id"]
    assert [row["episode_id"] for row in restarted.active()] == [
        first["episode"]["episode_id"], fresh["episode"]["episode_id"]]
    with pytest.raises(locked.LockedCandidateError, match="different contract"):
        store.lock(point_at(quantity=1), request_key="r1", now_ms=T)


def test_lookup_request_is_readonly_restart_idempotency_by_exact_snapshot(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    point = point_at()
    snapshot = point["packet"]["snapshot"]
    first = store.lock(point, request_key="r1", now_ms=T)
    restarted = locked.LockedCandidateStore(tmp_path)
    assert restarted.lookup_request(
        "r1",
        candidate_id=snapshot["candidate_id"],
        snapshot_id=snapshot["snapshot_id"],
    ) == {"created": False, "episode": first["episode"]}
    assert restarted.lookup_request(
        "missing",
        candidate_id=snapshot["candidate_id"],
        snapshot_id=snapshot["snapshot_id"],
    ) is None
    with pytest.raises(locked.LockedCandidateError) as exc:
        restarted.lookup_request("r1", candidate_id=snapshot["candidate_id"], snapshot_id="different")
    assert exc.value.code == "request_key_reused"
    with pytest.raises(locked.LockedCandidateError) as exc:
        restarted.lookup_request(
            "r1",
            candidate_id=snapshot["candidate_id"],
            snapshot_id=snapshot["snapshot_id"],
            new_reference=True,
        )
    assert exc.value.code == "request_key_reused"


def test_t0_baseline_is_not_overwritten_by_later_observations(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    first = point_at(T)
    later = point_at(T + 300_000)
    episode_id = store.lock(first, request_key="r1", now_ms=T)["episode"]["episode_id"]
    baseline_id = store.detail(episode_id)["episode"]["t0_baseline_point_id"]
    summary = store.append(episode_id, later, now_ms=T + 300_000)
    detail = store.detail(episode_id)
    assert summary["latest_point_id"] != baseline_id
    assert detail["episode"]["t0_baseline_point_id"] == baseline_id
    assert detail["baseline"]["packet"]["snapshot"]["snapshot_id"] == first["packet"]["snapshot"]["snapshot_id"]
    assert detail["latest"]["packet"]["snapshot"]["snapshot_id"] == later["packet"]["snapshot"]["snapshot_id"]


def test_append_writes_delta_when_smaller_and_reconstructs_exact_point(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    first = point_at(T)
    later = point_at(T + 300_000)
    episode_id = store.lock(first, request_key="r1", now_ms=T)["episode"]["episode_id"]
    baseline_id = store.detail(episode_id)["episode"]["t0_baseline_point_id"]
    baseline_path = store._point_path(baseline_id)
    baseline_bytes = baseline_path.read_bytes()

    summary = store.append(episode_id, later, now_ms=T + 300_000)
    latest_id = summary["latest_point_id"]
    latest_path = store._point_path(latest_id)
    stored = stored_json(latest_path)
    detail = store.detail(episode_id)
    signature = store.point_read_signature(latest_id)

    assert stored["schema"] == locked.POINT_DELTA_SCHEMA
    assert stored["base_point_id"] == baseline_id
    assert latest_path.stat().st_size < len(locked._prepare_point_for_write(later)["gz"])
    assert baseline_path.read_bytes() == baseline_bytes
    assert locked.store._canonical(detail["latest"]) == locked.store._canonical(later)
    assert locked._hash(detail["latest"]) == latest_id
    assert signature["point"]["storage"] == "delta"
    assert signature["dependencies"] == [{
        "role": "base_point",
        "point_id": baseline_id,
        "point_hash": baseline_id,
        "stat": list(store._point_file_stat(baseline_id)),
    }]


def test_detail_pair_returns_exact_retained_t1_without_latest_drift(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    first = point_at(T)
    middle = point_at(T + 300_000)
    final = point_at(T + 600_000)
    episode_id = store.lock(first, request_key="r1", now_ms=T)["episode"]["episode_id"]
    baseline_id = store.detail(episode_id)["episode"]["t0_baseline_point_id"]
    middle_summary = store.append(episode_id, middle, now_ms=T + 300_000)
    final_summary = store.append(episode_id, final, now_ms=T + 600_000)

    pair = store.detail_pair(episode_id, baseline_id, middle_summary["latest_point_id"])

    assert final_summary["latest_point_id"] != middle_summary["latest_point_id"]
    assert pair["episode"]["latest_point_id"] == middle_summary["latest_point_id"]
    assert pair["selected_latest_step"] == 1
    assert [row["step"] for row in pair["observations"]] == [0, 1]
    assert pair["latest"]["packet"]["snapshot"]["snapshot_id"] == middle["packet"]["snapshot"]["snapshot_id"]


def test_detail_pair_rejects_cross_episode_or_rewritten_baseline(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    first = store.lock(point_at(T), request_key="r1", now_ms=T)["episode"]
    second = store.lock(point_at(T, quantity=1), request_key="r2", now_ms=T)["episode"]

    with pytest.raises(locked.LockedCandidateError) as wrong_baseline:
        store.detail_pair(first["episode_id"], second["t0_baseline_point_id"], first["latest_point_id"])
    assert wrong_baseline.value.code == "baseline_point_mismatch"

    with pytest.raises(locked.LockedCandidateError) as wrong_latest:
        store.detail_pair(first["episode_id"], first["t0_baseline_point_id"], second["latest_point_id"])
    assert wrong_latest.value.code == "latest_point_not_retained"


def test_concurrent_same_lock_is_idempotent(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    point = point_at()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.lock(point, request_key="same", now_ms=T), range(2)))
    assert [result["created"] for result in results].count(True) == 1
    assert len(store.list()) == 1


def test_wrong_contract_and_bad_hash_are_rejected(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(), request_key="r1", now_ms=T)["episode"]["episode_id"]
    with pytest.raises(locked.LockedCandidateError) as wrong:
        store.append(episode_id, point_at(quantity=1), now_ms=T)
    assert wrong.value.code == "contract_mismatch"
    bad = point_at()
    bad["packet"]["packet_hash"] = "0" * 64
    with pytest.raises(locked.LockedCandidateError) as packet_error:
        store.lock(bad, request_key="bad", now_ms=T)
    assert packet_error.value.code == "invalid_packet"


def test_damaged_gzip_or_point_hash_fails_closed_on_read(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(), request_key="r1", now_ms=T)["episode"]["episode_id"]
    point_id = store.detail(episode_id)["episode"]["t0_baseline_point_id"]
    path = tmp_path / "points" / (point_id + ".json.gz")
    path.write_bytes(gzip.compress(b'{"recorded_at_ms":1}\n', mtime=0))
    assert store.list()[0]["episode_id"] == episode_id
    with pytest.raises(locked.LockedCandidateError) as exc:
        store.detail(episode_id)
    assert exc.value.code == "point_hash_mismatch"
    path.write_bytes(b"not gzip")
    assert store.list()[0]["episode_id"] == episode_id
    with pytest.raises(locked.LockedCandidateError) as exc:
        store.detail(episode_id)
    assert exc.value.code == "point_read_failed"


def test_missing_one_point_does_not_break_other_episode_list_or_detail(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    damaged = store.lock(point_at(T), request_key="r1", now_ms=T)["episode"]
    healthy = store.lock(point_at(T, quantity=1), request_key="r2", now_ms=T)["episode"]
    store._point_path(damaged["t0_baseline_point_id"]).unlink()

    listed = {row["episode_id"]: row for row in store.list()}

    assert listed[healthy["episode_id"]]["status"] == "active"
    assert listed[damaged["episode_id"]]["status"] == "active"
    assert store.detail(healthy["episode_id"])["episode"]["episode_id"] == healthy["episode_id"]
    with pytest.raises(locked.LockedCandidateError) as exc:
        store.detail(damaged["episode_id"])
    assert exc.value.code == "point_missing"


def test_capacity_pause_preserves_latest_and_records_gap(tmp_path):
    store = locked.LockedCandidateStore(tmp_path, max_samples=2)
    episode_id = store.lock(point_at(T), request_key="r1", now_ms=T)["episode"]["episode_id"]
    first_append = store.append(episode_id, point_at(T + 300_000), now_ms=T + 300_000)
    paused = store.append(episode_id, point_at(T + 600_000), now_ms=T + 600_000)
    detail = store.detail(episode_id)
    assert first_append["status"] == "active"
    assert paused["status"] == "paused"
    assert paused["latest_point_id"] == first_append["latest_point_id"]
    assert paused["last_gap_reason"] == "sample_budget_exhausted"
    assert detail["observations"][-1]["gap"]["reason"] == "sample_budget_exhausted"


def test_lock_byte_budget_fails_without_fake_episode_or_t0_loss(tmp_path):
    store = locked.LockedCandidateStore(tmp_path, max_bytes=1)
    with pytest.raises(locked.LockedCandidateError) as exc:
        store.lock(point_at(), request_key="r1", now_ms=T)
    assert exc.value.code == "byte_budget_exhausted"
    assert not store.list()
    assert not (tmp_path / "points").exists()


def test_append_write_failure_is_atomic_and_visible(tmp_path, monkeypatch):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(T), request_key="r1", now_ms=T)["episode"]["episode_id"]
    before = store.detail(episode_id)["episode"]["latest_point_id"]

    def fail_write(_checked, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_ensure_point_file", fail_write)
    failed = store.append(episode_id, point_at(T + 300_000), now_ms=T + 300_000)
    detail = store.detail(episode_id)
    assert failed["status"] == "error"
    assert failed["latest_point_id"] == before
    assert detail["latest"]["packet"]["snapshot"]["snapshot_id"] == detail["baseline"]["packet"]["snapshot"]["snapshot_id"]
    assert detail["observations"][-1]["gap"]["reason"] == "point_write_failed:OSError"


def test_stop_and_expired_gap_stop_observation_without_close_claim(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    stopped_id = store.lock(point_at(), request_key="r1", now_ms=T)["episode"]["episode_id"]
    stopped = store.stop(stopped_id, now_ms=T + 1)
    expired_id = store.lock(point_at(), request_key="r2", now_ms=T + 2, new_reference=True)["episode"]["episode_id"]
    expired = store.record_gap(expired_id, now_ms=T + 3, reason="contract_expired", expired=True)
    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "observation_stopped_not_position_close"
    assert expired["status"] == "expired"
    assert expired["stop_reason"] == "expired"
    assert [row["episode_id"] for row in store.active()] == []


def test_list_is_light_and_detail_does_episode_point_validation(tmp_path, monkeypatch):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(), request_key="r1", now_ms=T)["episode"]["episode_id"]

    def fail_replay(_packet):
        raise ValueError("should only run on detail")

    monkeypatch.setattr(locked.packets, "replay_packet", fail_replay)
    assert store.list()[0]["episode_id"] == episode_id
    with pytest.raises(locked.LockedCandidateError) as exc:
        store.detail(episode_id)
    assert exc.value.code == "invalid_packet"


def test_list_does_not_stat_every_historical_point(tmp_path, monkeypatch):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(), request_key="r1", now_ms=T)["episode"]["episode_id"]

    def fail_stat(_point_id):
        raise AssertionError("list must not stat retained point files")

    monkeypatch.setattr(store, "_point_file_stat", fail_stat)
    assert store.list()[0]["episode_id"] == episode_id
    with pytest.raises(AssertionError):
        store.detail(episode_id)


def test_read_paths_do_not_recompress_points(tmp_path, monkeypatch):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(), request_key="r1", now_ms=T)["episode"]["episode_id"]

    def fail_compress(*_args, **_kwargs):
        raise AssertionError("read path must not recompress point")

    monkeypatch.setattr(locked.gzip, "compress", fail_compress)
    assert store.list()
    assert store.detail(episode_id)["episode"]["episode_id"] == episode_id


def test_bad_index_point_id_cannot_escape_points_directory(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(), request_key="r1", now_ms=T)["episode"]["episode_id"]
    state = locked.store.read_json(tmp_path / "index.json")
    state["episodes"][episode_id]["latest_point_id"] = "../outside"
    locked.store.write_json(tmp_path / "index.json", state)
    summary = store.list()[0]
    assert summary["status"] == "error"
    assert summary["index_error"]["code"] == "invalid_point_id"
    with pytest.raises(locked.LockedCandidateError) as exc:
        store.detail(episode_id)
    assert exc.value.code == "invalid_point_id"


def test_verified_point_cache_does_not_keep_snapshot_body_and_stat_replacement_revalidates(tmp_path, monkeypatch):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(), request_key="r1", now_ms=T)["episode"]["episode_id"]
    point_id = store.detail(episode_id)["episode"]["t0_baseline_point_id"]

    cached = store._point_verified_cache[point_id][1]
    assert "snapshot" not in cached
    assert "contract" not in cached

    calls = {"count": 0}
    original_validate = locked.validate_point

    def counting_validate(point):
        calls["count"] += 1
        return original_validate(point)

    monkeypatch.setattr(locked, "validate_point", counting_validate)
    store.detail(episode_id)
    assert calls["count"] == 0
    old_stat = store._point_file_stat(point_id)
    monkeypatch.setattr(store, "_point_file_stat", lambda _point_id: (old_stat[0], old_stat[1] + 1, old_stat[2] + 1))
    store.detail(episode_id)
    assert calls["count"] == 1


def test_delta_patch_corruption_is_rejected_without_breaking_list(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(T), request_key="r1", now_ms=T)["episode"]["episode_id"]
    summary = store.append(episode_id, point_at(T + 300_000), now_ms=T + 300_000)
    path = store._point_path(summary["latest_point_id"])
    envelope = stored_json(path)
    assert envelope["schema"] == locked.POINT_DELTA_SCHEMA
    envelope["patch"]["set"]["not/a/json-pointer"] = 1
    write_stored_json(path, envelope)

    assert store.list()[0]["episode_id"] == episode_id
    with pytest.raises(locked.LockedCandidateError) as exc:
        store.detail(episode_id)
    assert exc.value.code == "delta_patch_invalid"


def test_delta_missing_or_changed_base_is_rejected_after_cache(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(T), request_key="r1", now_ms=T)["episode"]["episode_id"]
    baseline_id = store.detail(episode_id)["episode"]["t0_baseline_point_id"]
    summary = store.append(episode_id, point_at(T + 300_000), now_ms=T + 300_000)
    latest_id = summary["latest_point_id"]
    assert store.point_read_signature(latest_id)["point"]["storage"] == "delta"
    store._point_path(baseline_id).write_bytes(b"not gzip")
    with pytest.raises(locked.LockedCandidateError) as changed:
        store.point_read_signature(latest_id)
    assert changed.value.code == "point_read_failed"

    store = locked.LockedCandidateStore(tmp_path)
    with pytest.raises(locked.LockedCandidateError):
        store.detail(episode_id)


def test_delta_missing_base_reports_dependency_failure(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(T), request_key="r1", now_ms=T)["episode"]["episode_id"]
    baseline_id = store.detail(episode_id)["episode"]["t0_baseline_point_id"]
    latest_id = store.append(episode_id, point_at(T + 300_000), now_ms=T + 300_000)["latest_point_id"]
    store._point_path(baseline_id).unlink()

    with pytest.raises(locked.LockedCandidateError) as exc:
        store.point_read_signature(latest_id)
    assert exc.value.code == "delta_base_missing"


def test_delta_cycle_and_cross_candidate_base_are_rejected(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    first = point_at(T)
    later = point_at(T + 300_000)
    other = point_at(T, quantity=1)
    first_episode = store.lock(first, request_key="r1", now_ms=T)["episode"]
    other_episode = store.lock(other, request_key="r2", now_ms=T)["episode"]
    latest_id = store.append(first_episode["episode_id"], later, now_ms=T + 300_000)["latest_point_id"]
    latest_path = store._point_path(latest_id)

    envelope = stored_json(latest_path)
    envelope["base_point_id"] = latest_id
    write_stored_json(latest_path, envelope)
    with pytest.raises(locked.LockedCandidateError) as cycle:
        store.point_read_signature(latest_id)
    assert cycle.value.code == "delta_cycle"

    wrong_base = store.detail(other_episode["episode_id"])["baseline"]
    wrong_env = delta_envelope(
        wrong_base,
        later,
        base_point_id=other_episode["t0_baseline_point_id"],
        target_point_id=latest_id,
    )
    write_stored_json(latest_path, wrong_env)
    with pytest.raises(locked.LockedCandidateError) as cross:
        store.point_read_signature(latest_id)
    assert cross.value.code == "delta_base_mismatch"


def test_new_reference_from_delta_t0_keeps_existing_delta_and_writes_next_point_full(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    first_id = store.lock(point_at(T), request_key="r1", now_ms=T)["episode"]["episode_id"]
    delta_id = store.append(first_id, point_at(T + 300_000), now_ms=T + 300_000)["latest_point_id"]
    delta_snapshot = store.detail(first_id)["episode"]["latest_snapshot_id"]
    delta_bytes = store._point_path(delta_id).read_bytes()
    assert stored_json(store._point_path(delta_id))["schema"] == locked.POINT_DELTA_SCHEMA

    source_point = store.retained_point(first_id, delta_snapshot)
    second = store.lock(
        source_point,
        request_key="r2",
        now_ms=T + 300_001,
        new_reference=True,
        source_episode_id=first_id,
    )["episode"]
    full_id = store.append(second["episode_id"], point_at(T + 600_000), now_ms=T + 600_000)["latest_point_id"]

    assert second["t0_baseline_point_id"] == delta_id
    assert store._point_path(delta_id).read_bytes() == delta_bytes
    assert stored_json(store._point_path(full_id)).get("schema") != locked.POINT_DELTA_SCHEMA


def test_request_key_has_bounded_size(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    with pytest.raises(locked.LockedCandidateError) as exc:
        store.lock(point_at(), request_key="x" * 257, now_ms=T)
    assert exc.value.code == "request_key_too_long"


def test_request_key_count_is_bounded_without_evicting_retry_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(locked, "MAX_REQUEST_KEYS", 2)
    store = locked.LockedCandidateStore(tmp_path)
    point = point_at()
    first = store.lock(point, request_key="one", now_ms=T)
    store.lock(point, request_key="two", now_ms=T)
    with pytest.raises(locked.LockedCandidateError) as failure:
        store.lock(point, request_key="three", now_ms=T)
    assert failure.value.code == "request_key_budget_exhausted"
    assert store.lock(point, request_key="one", now_ms=T)["episode"] == first["episode"]


def test_byte_limit_pauses_before_writing_orphan_point_and_survives_restart(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    episode = store.lock(point_at(), request_key="one", now_ms=T)["episode"]
    next_point = point_at(T + 300_000)
    checked = locked._prepare_point_for_write(next_point)
    # There is room for the point alone, but not index growth and control reserve.
    store.max_bytes = store._total_bytes() + len(checked["gz"]) + 32
    summary = store.append(episode["episode_id"], next_point, now_ms=T + 300_000)
    assert summary["status"] == "paused"
    assert summary["last_gap_reason"] == "byte_budget_exhausted"
    assert not store._point_path(checked["point_id"]).exists()
    restored = locked.LockedCandidateStore(tmp_path, max_bytes=store.max_bytes)
    assert restored.detail(episode["episode_id"])["episode"]["latest_point_id"] == episode["latest_point_id"]
    assert restored.list()[0]["status"] == "paused"


def test_index_failure_keeps_previous_latest_and_persists_error(tmp_path, monkeypatch):
    store = locked.LockedCandidateStore(tmp_path)
    episode = store.lock(point_at(), request_key="one", now_ms=T)["episode"]
    original = locked._atomic_write_bytes
    failed = [False]
    def write(path, data):
        if path == store.index_path and not failed[0]:
            failed[0] = True
            raise OSError("one index replacement failed")
        return original(path, data)
    monkeypatch.setattr(locked, "_atomic_write_bytes", write)
    result = store.append(episode["episode_id"], point_at(T + 300_000), now_ms=T + 300_000)
    assert result["status"] == "error"
    assert result["latest_point_id"] == episode["latest_point_id"]
    assert result["last_gap_reason"] == "index_write_failed:OSError"
    assert locked.LockedCandidateStore(tmp_path).active() == []


def test_gap_byte_pressure_keeps_pause_and_expiry_durable(tmp_path):
    store = locked.LockedCandidateStore(tmp_path, max_bytes=6000)
    episode = store.lock(point_at(), request_key="one", now_ms=T)["episode"]
    for index in range(1, 5):
        state = store.record_gap(episode["episode_id"], now_ms=T + index,
                                 reason="missing_locked_candidate_capture")
        if state["status"] == "paused":
            break
    assert state["status"] == "paused"
    expired = store.record_gap(episode["episode_id"], now_ms=T + 99,
                              reason="contract_expired_settlement_unverified", expired=True)
    assert expired["status"] == "expired"
    restored = locked.LockedCandidateStore(tmp_path, max_bytes=6000)
    assert restored.list()[0]["status"] == "expired"
    assert restored.detail(episode["episode_id"])["episode"]["t0_baseline_point_id"] == episode["t0_baseline_point_id"]


def test_new_reference_request_key_binds_source_episode(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    point = point_at()
    first = store.lock(point, request_key="first", now_ms=T)["episode"]
    second = store.lock(point, request_key="second", now_ms=T, new_reference=True)["episode"]
    store.lock(point, request_key="derived", now_ms=T, new_reference=True, source_episode_id=first["episode_id"])
    with pytest.raises(locked.LockedCandidateError) as error:
        store.lookup_request("derived", candidate_id=first["candidate_id"], snapshot_id=first["latest_snapshot_id"],
                             new_reference=True, source_episode_id=second["episode_id"])
    assert error.value.code == "request_key_reused"


def test_gap_records_are_bounded_but_cumulative_stats_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(locked, "MAX_STORED_GAPS_PER_EPISODE", 2)
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(), request_key="r1", now_ms=T)["episode"]["episode_id"]
    store.record_gap(episode_id, now_ms=T + 1, reason="a")
    store.record_gap(episode_id, now_ms=T + 2, reason="b")
    summary = store.record_gap(episode_id, now_ms=T + 3, reason="b")
    detail = store.detail(episode_id)
    assert summary["status"] == "paused"
    assert summary["gap_count"] == 3
    assert summary["stored_gap_count"] == 2
    assert summary["gap_storage_truncated"] is True
    assert summary["first_gap"]["reason"] == "a"
    assert summary["last_gap"]["reason"] == "b"
    assert summary["max_gap"] == {"reason": "b", "count": 2}
    assert len([row for row in detail["observations"] if row["kind"] == "gap"]) == 2


def test_read_methods_do_not_write(tmp_path):
    store = locked.LockedCandidateStore(tmp_path)
    episode_id = store.lock(point_at(), request_key="r1", now_ms=T)["episode"]["episode_id"]
    before = files(tmp_path)
    assert store.list()
    assert store.active()
    assert store.detail(episode_id)["episode"]["episode_id"] == episode_id
    assert files(tmp_path) == before
