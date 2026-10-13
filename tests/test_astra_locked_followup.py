"""Integration counterexamples for explicit locks and the existing collector."""
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tools import astra_workbench_v20 as module
from tools import astra_underwriting_public_quote_v20 as public_quote
from test_astra_workbench_v20 import (
    T, EXPIRY, make_workbench, native_gate, write_fmz, fake_quote_capture, instruments,
)


def setup(tmp_path):
    clock = [T]
    wb, snapshots = make_workbench(tmp_path, api_key=None, clock=lambda: clock[0],
                                   transport=lambda *a, **k: pytest.fail("unexpected LLM"))
    write_fmz(snapshots, native_gate())
    view = wb.refresh(now_ms=T, record_daily=False)
    return wb, snapshots, clock, view["candidates"][0]["snapshot"]


def lock(wb, snapshot, key="lock-once", **kwargs):
    return wb.lock_candidate(snapshot["candidate_id"], snapshot["snapshot_id"], key,
                             authorization="Bearer secret", **kwargs)


def test_exact_input_lock_is_independent_of_provider_risk_and_cooldown(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    wb._locked_state_update(lambda state: state.setdefault("manual_review", {}).update(
        {"last_started_ts_ms": T, "inflight": {"candidate_id": "other"}}))
    # No click-time quote/LLM refresh, even after the current candidate changes.
    clock[0] += 60_000
    wb.refresh(now_ms=clock[0], record_daily=False)
    wb.quote_capture = lambda *a, **k: pytest.fail("lock acquired market data")
    first = lock(wb, snapshot)
    again = lock(wb, snapshot)
    detail = wb.locked_candidates.detail(first["episode"]["episode_id"])
    assert first["created"] and not again["created"]
    assert first["llm_call_count"] == 0
    assert detail["baseline"]["candidate"]["snapshot"] == snapshot
    assert detail["episode"]["created_at_ms"] == clock[0]
    assert detail["baseline"]["recorded_at_ms"] == T
    assert snapshot["economics"]["mu_ref_btc"] is None


def test_lock_retry_survives_restart_without_old_memory_cache(tmp_path):
    wb, _, _, snapshot = setup(tmp_path)
    result = lock(wb, snapshot)
    restarted = module.AstraWorkbench(wb.config)
    assert restarted._lockable_points == {}
    retried = lock(restarted, snapshot)
    assert not retried["created"]
    assert retried["episode"]["episode_id"] == result["episode"]["episode_id"]
    with pytest.raises(module.WorkbenchError, match="request_key_reused"):
        restarted.lock_candidate("wrong", snapshot["snapshot_id"], "lock-once", authorization="Bearer secret")


def test_lock_and_stop_do_not_wait_for_network_refresh_lock(tmp_path):
    wb, _, _, snapshot = setup(tmp_path)
    completed = threading.Event()
    results, errors = [], []
    def local_actions():
        try:
            episode = lock(wb, snapshot)["episode"]
            results.append(wb.stop_locked_candidate(episode["episode_id"], authorization="Bearer secret"))
        except Exception as exc:
            errors.append(exc)
        finally:
            completed.set()
    # Model a collector blocked in public HTTP. Local actions must finish while
    # this lock remains held; the timeout only bounds a regression's test wait.
    with wb._refresh_lock:
        thread = threading.Thread(target=local_actions, daemon=True)
        thread.start()
        independent = completed.wait(3)
    thread.join(3)
    assert independent and not errors
    assert results[0]["episode"]["status"] == "stopped"


def test_inflight_sample_cannot_rewrite_a_stopped_record(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    entered, release = threading.Event(), threading.Event()
    original_point = wb._lockable_points[snapshot["snapshot_id"]]
    def delayed_point(*args):
        entered.set()
        assert release.wait(5)
        return original_point
    wb._capture_locked_point = delayed_point
    clock[0] += 120_000
    thread = threading.Thread(target=wb._refresh_locked_candidates,
                              args=({"candidates": []}, clock[0]), daemon=True)
    thread.start()
    try:
        assert entered.wait(3)
        stopped = wb.stop_locked_candidate(episode["episode_id"], authorization="Bearer secret")["episode"]
        saved = wb.locked_candidates.index_path.read_bytes()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert stopped["status"] == "stopped"
    assert wb.locked_candidates.index_path.read_bytes() == saved
    assert wb.locked_candidates.detail(episode["episode_id"])["episode"]["point_count"] == 1
    # A late expiry check also cannot replace the explicit stop reason.
    wb.locked_candidates.record_gap(episode["episode_id"], now_ms=EXPIRY + 1,
                                   reason="contract_expired", expired=True)
    assert wb.locked_candidates.index_path.read_bytes() == saved


def test_rotation_closed_gate_and_restart_keep_original_contract(tmp_path):
    wb, snapshots, clock, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    initial_hash = episode["t0_baseline_point_id"]
    wb = module.AstraWorkbench(wb.config, clock_ms=lambda: clock[0],
        discovery_capture=lambda **kwargs: {"candidates": {}}, quote_capture=fake_quote_capture)
    calls = []
    def capture(short, long, qty, **kwargs):
        calls.append((short, long, qty))
        return fake_quote_capture(short, long, qty, **kwargs)
    wb.quote_capture = capture
    clock[0] += 120_000
    write_fmz(snapshots, native_gate("STRUCTURE_BLOCKED", ts=clock[0] - 1000), now=clock[0])
    view = wb.refresh(now_ms=clock[0], record_daily=False)
    detail = wb.locked_candidates.detail(episode["episode_id"])
    assert all(item["snapshot"] is None for item in view["candidates"])
    contract = snapshot["contract"]
    assert calls == [(contract["short_instrument"], contract["long_instrument"], contract["quantity_btc"])]
    assert detail["episode"]["point_count"] == 2
    assert detail["episode"]["t0_baseline_point_id"] == initial_hash
    assert detail["latest"]["candidate"]["space_gate"]["state"] == "STRUCTURE_BLOCKED"
    assert detail["latest"]["candidate"]["review_gate"]["enabled"] is False
    assert detail["latest"]["candidate"]["snapshot"]["contract"] == contract


def test_new_reference_freezes_displayed_retained_point_across_update_and_restart(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    original = lock(wb, snapshot)["episode"]
    clock[0] += 120_000
    wb.refresh(now_ms=clock[0], record_daily=False)
    assert wb.locked_candidates.detail(original["episode_id"])["latest"]["candidate"]["snapshot"]["snapshot_id"] != snapshot["snapshot_id"]
    restarted = module.AstraWorkbench(wb.config, clock_ms=lambda: clock[0])
    fresh = lock(restarted, snapshot, key="explicit-old-display", new_reference=True, source_episode_id=original["episode_id"])
    assert fresh["created"]
    assert fresh["episode"]["baseline_snapshot_id"] == snapshot["snapshot_id"]
    assert fresh["episode"]["source_episode_id"] == original["episode_id"]
    assert fresh["llm_call_count"] == 0


def test_partial_quote_retains_other_facts_without_price_substitution(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    metas = {row["instrument_name"]: row for row in instruments()}
    def capture(short, long, qty, *, now_ms):
        def fetch(path, params):
            name = params["instrument_name"]
            if path.endswith("get_instrument"):
                return deepcopy(metas[name])
            if name == short:
                raise ValueError("one leg unavailable")
            return {"instrument_name": name, "state": "open", "timestamp": now_ms,
                    "index_price": 100200, "bids": [[.002, 1]], "asks": [[.003, 1]]}
        return public_quote.capture_candidate(short, long, qty, transport=fetch,
                                               clock_ms=lambda: now_ms, allow_partial=True)
    wb.quote_capture = capture
    wb.discovery_capture = lambda **kwargs: {"candidates": {}}
    clock[0] += 60_000
    wb.refresh(now_ms=clock[0], record_daily=False)
    detail = wb.locked_candidates.detail(episode["episode_id"])
    latest = detail["latest"]["candidate"]
    assert detail["episode"]["point_count"] == 2
    assert latest["snapshot"]["market"]["reference_price_usd"] == 100200
    assert latest["snapshot"]["market"]["quote"]["credit_btc"] is None
    assert latest["capture_gaps"] == ["short_book_unavailable"]
    payload = detail["latest"]["packet"]["raw_payload"]
    assert payload["market"]["short_book"] == {}
    assert payload["market"]["long_book"]["instrument_name"] == snapshot["contract"]["long_instrument"]


def test_stop_new_reference_and_expiry_are_observation_lifecycle(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    first = lock(wb, snapshot)["episode"]
    stopped = wb.stop_locked_candidate(first["episode_id"], authorization="Bearer secret")["episode"]
    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "observation_stopped_not_position_close"
    second = lock(wb, snapshot, "explicit-new", new_reference=True,
                  source_episode_id=first["episode_id"])["episode"]
    assert second["episode_id"] != first["episode_id"]
    clock[0] = EXPIRY + 1
    wb.discovery_capture = lambda **kwargs: {"candidates": {}}
    wb.quote_capture = lambda *a, **k: pytest.fail("expired or stopped capture")
    wb.refresh(now_ms=clock[0], record_daily=False)
    detail = wb.locked_candidates.detail(second["episode_id"])
    assert detail["episode"]["status"] == "expired"
    assert detail["episode"]["last_gap_reason"] == "contract_expired_settlement_unverified"
    assert detail["episode"]["point_count"] == 1
    assert wb.locked_candidates.detail(first["episode_id"])["episode"]["status"] == "stopped"


def test_source_episode_requires_explicit_new_reference(tmp_path):
    wb, _, _, snapshot = setup(tmp_path)
    first = lock(wb, snapshot)["episode"]
    wb.stop_locked_candidate(first["episode_id"], authorization="Bearer secret")
    with pytest.raises(module.WorkbenchError) as error:
        lock(wb, snapshot, "accidental-restart", source_episode_id=first["episode_id"])
    assert error.value.code == "new_reference_required"
    assert len(wb.locked_candidates.list()) == 1


def test_http_gets_are_read_only_and_mutations_require_token(tmp_path):
    wb, _, _, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    wb.quote_capture = lambda *a, **k: pytest.fail("GET capture")
    wb.discovery_capture = lambda **k: pytest.fail("GET discovery")
    before = {p: p.read_bytes() for p in wb.locked_candidates.root.rglob("*") if p.is_file()}
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/api/workbench/locked-candidates"
    try:
        with urlopen(base) as response:
            assert json.load(response)["episodes"][0]["episode_id"] == episode["episode_id"]
        with urlopen(base + "/" + episode["episode_id"]) as response:
            detail = json.load(response)
        assert detail["schema"] == "locked_candidate_followup@1.0.0"
        assert detail["llm_call_count"] == 0
        request = Request(base + "/" + episode["episode_id"] + "/stop", data=b"{}", method="POST")
        with pytest.raises(HTTPError) as failure:
            urlopen(request)
        assert failure.value.code == 401
        assert before == {p: p.read_bytes() for p in wb.locked_candidates.root.rglob("*") if p.is_file()}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_locked_manual_review_rotated_target_retains_gates_and_baseline(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    wb._current_view["candidates"] = []
    clock[0] += 60_000
    response = wb.review_candidate(snapshot["candidate_id"], "explicit-review",
        authorization="Bearer secret", locked_episode_id=episode["episode_id"], snapshot_id=snapshot["snapshot_id"])
    assert response["reason"] == "review_gate_disabled"
    assert response["llm_call_count"] == 0
    detail = wb.locked_candidates.detail(episode["episode_id"])
    assert detail["episode"]["point_count"] == 2
    assert detail["episode"]["t0_baseline_point_id"] == episode["t0_baseline_point_id"]
    assert detail["latest"]["candidate"]["snapshot"]["snapshot_id"] == response["snapshot_id"]


def test_locked_runtime_modules_are_in_both_packaging_lists():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name in ("astra_locked_candidate_store_v1.py", "astra_locked_candidate_compare_v1.py", "astra_decision_reduction_v1.py", "astra_spatial_position_v1.py", "astra_observation_replay_v1.py",
                 "astra_chain_audit_v1.py"):
        for path in ("deploy/signal_audit/package_signal_audit.ps1", "deploy/signal_audit/install_or_update.sh"):
            assert name in (root / path).read_text(encoding="utf-8-sig")


def test_series_reads_every_point_against_fixed_t0_and_caches_points(tmp_path):
    wb, snapshots, clock, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    clock[0] += 120_000
    write_fmz(snapshots, native_gate("STRUCTURE_BLOCKED", ts=clock[0] - 1000), now=clock[0])
    wb.refresh(now_ms=clock[0], record_daily=False)
    series = wb.locked_candidate_series(episode["episode_id"])
    assert series["schema"] == "locked_candidate_series@1.0.0"
    assert series["llm_call_count"] == 0 and series["execution_allowed"] is False
    points = series["points"]
    assert len(points) == 2 and points[0]["point_id"] == episode["t0_baseline_point_id"]
    assert [p["space_gate_state"] for p in points] == ["STRUCTURE_ELIGIBLE", "STRUCTURE_BLOCKED"]
    for p in points:
        assert isinstance(p["short_distance_pct"], float) and isinstance(p["spot_usd"], float)
    assert points[0]["visible_credit_btc"] == pytest.approx(snapshot["economics"]["visible_credit_btc"])
    # Retained points are immutable: a second read, even after a restart, does not touch point files.
    wb.locked_candidates.iter_points = lambda *_: pytest.fail("cached point re-read")
    assert wb.locked_candidate_series(episode["episode_id"])["points"] == points
    restarted = module.AstraWorkbench(wb.config, clock_ms=lambda: clock[0])
    restarted.locked_candidates.iter_points = lambda *_: pytest.fail("cache not persisted")
    assert restarted.locked_candidate_series(episode["episode_id"])["points"] == points


def test_series_skips_a_tampered_point_instead_of_showing_it(tmp_path):
    import gzip
    wb, snapshots, clock, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    clock[0] += 120_000
    wb.refresh(now_ms=clock[0], record_daily=False)
    latest = wb.locked_candidates.episode_observations(episode["episode_id"])["observations"][-1]["point_id"]
    path = wb.locked_candidates._point_path(latest)
    stored = json.loads(gzip.decompress(path.read_bytes()))
    stored.setdefault("patch", {}).setdefault("set", {})["/recorded_at_ms"] = 1
    if "patch" not in stored or stored.get("schema") is None:
        stored["recorded_at_ms"] = 1
    path.write_bytes(gzip.compress(json.dumps(stored).encode()))
    # Metrics already derived from the authentic content (keyed by its hash) stay as recorded.
    assert len(wb.locked_candidate_series(episode["episode_id"])["points"]) == 2
    # The durable series row was written from the authentic point when it was sampled.
    wb._series_cache = {}
    wb._series_cache_path().unlink()
    assert len(wb.locked_candidate_series(episode["episode_id"])["points"]) == 2
    # Rebuilt from disk alone, the tampered point fails its hash check and is left out, not shown.
    wb._series_cache = {}
    wb._series_cache_path().unlink()
    wb.locked_candidates._series_path(episode["episode_id"]).unlink()
    series = wb.locked_candidate_series(episode["episode_id"])
    assert [p["point_id"] for p in series["points"]] == [episode["t0_baseline_point_id"]]
    assert series["unreadable_points"] == 1


def test_series_http_get_is_read_only_and_rejects_bad_ids(tmp_path):
    wb, _, _, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    wb.quote_capture = lambda *a, **k: pytest.fail("GET capture")
    before = {p: p.read_bytes() for p in wb.locked_candidates.root.rglob("*") if p.is_file()}
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/api/workbench/locked-candidates"
    try:
        with urlopen(base + "/" + episode["episode_id"] + "/series") as response:
            body = json.load(response)
        assert len(body["points"]) == 1 and body["episode"]["episode_id"] == episode["episode_id"]
        with pytest.raises(HTTPError) as failure:
            urlopen(base + "/not-an-id/series")
        assert failure.value.code == 400
        assert before == {p: p.read_bytes() for p in wb.locked_candidates.root.rglob("*") if p.is_file()}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_sampling_warms_series_metrics_without_rereading_points(tmp_path):
    wb, snapshots, clock, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    clock[0] += 120_000
    wb.refresh(now_ms=clock[0], record_daily=False)
    observed = wb.locked_candidates.episode_observations(episode["episode_id"])["observations"]
    latest = [row["point_id"] for row in observed if row.get("kind") == "point"][-1]
    assert latest in wb._series_metrics_cache()


def test_unchanged_snapshot_is_not_evicted_before_the_lock_samples_it(tmp_path):
    wb, _, clock, snapshot = setup(tmp_path)
    lock(wb, snapshot)
    # The locked snapshot is the oldest cached point; fill the bounded cache behind it.
    for key in [k for k in wb._lockable_points if k != snapshot["snapshot_id"]]:
        del wb._lockable_points[key]
    for i in range(11):
        wb._lockable_points[f"filler-{i}"] = {"n": i}
    assert next(iter(wb._lockable_points)) == snapshot["snapshot_id"]
    # The market did not move: the same snapshot is shown again, then a new point arrives.
    wb._remember_lockable({"candidates": [dict(wb._current_view["candidates"][0])],
                           "space_gate": wb._current_view["space_gate"]}, clock[0])
    wb._lockable_points["newer"] = {"n": 99}
    assert snapshot["snapshot_id"] in wb._lockable_points


def _expire(wb, clock, minutes_after=5):
    clock[0] = EXPIRY + minutes_after * 60_000
    wb.refresh(now_ms=clock[0], record_daily=False)


def test_expired_lock_is_settled_from_the_delivery_price(tmp_path):
    from tools import astra_commercial_math_v17 as math
    wb, snapshots, clock, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    contract = snapshot["contract"]
    price = contract["short_strike_usd"] - 500 if contract["side"] == "put" else contract["short_strike_usd"] + 500
    wb.delivery_price_fetch = lambda expiry_ms: {"date": "d", "delivery_price_usd": float(price), "source": "test"}
    _expire(wb, clock)
    row = wb.locked_settlements()[episode["episode_id"]]
    payout = math.inverse_spread_payout(contract["side"], contract["short_strike_usd"], contract["long_strike_usd"],
                                        price, contract["quantity_btc"])
    assert row["status"] == "settled" and row["payout_btc"] == pytest.approx(payout)
    assert row["settlement_result_btc"] == pytest.approx(snapshot["economics"]["net_credit_after_entry_fee_btc"] - payout)
    assert row["actual_trade_pnl_recorded"] is False
    assert wb.locked_candidate_series(episode["episode_id"])["settlement"] == row
    assert wb.locked_candidate_detail(episode["episode_id"], include_raw=False)["settlement"] == row


def test_settlement_waits_for_publication_then_gives_up(tmp_path):
    wb, snapshots, clock, snapshot = setup(tmp_path)
    episode = lock(wb, snapshot)["episode"]
    calls = []
    wb.delivery_price_fetch = lambda expiry_ms: calls.append(expiry_ms) or None
    _expire(wb, clock)
    assert episode["episode_id"] not in wb.locked_settlements() and len(calls) == 1
    wb._settle_expired_locks(clock[0] + 60_000)            # within the retry interval: no new request
    assert len(calls) == 1
    wb._settle_expired_locks(clock[0] + 11 * 60_000)
    assert len(calls) == 2 and episode["episode_id"] not in wb.locked_settlements()
    wb._settle_expired_locks(EXPIRY + 49 * 3_600_000)
    assert wb.locked_settlements()[episode["episode_id"]]["status"] == "delivery_price_unavailable"
