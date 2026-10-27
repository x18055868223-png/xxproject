"""WPEM step 2 (2026-10-14): the refresh composes packages only for contracts under active
lock tracking; the other displayed candidates keep their raw capture for an on-demand review."""
import json
from dataclasses import replace

from tools import astra_workbench_v20 as workbench
from tools import astra_wpem_workbench_v1 as adapter
from test_astra_workbench_v20 import make_workbench, write_fmz, native_gate, T


def _wb(tmp_path):
    original, snapshots = make_workbench(tmp_path)
    wb = workbench.AstraWorkbench(replace(original.config, wpem_enabled=True),
                                  discovery_capture=original.discovery_capture,
                                  quote_capture=original.quote_capture, clock_ms=original.clock_ms)
    write_fmz(snapshots, native_gate())
    return wb


def test_default_is_active_locks_only(tmp_path, monkeypatch):
    wb = _wb(tmp_path)
    assert wb.config.wpem_main_loop is False
    calls = []
    real = adapter.compose_current
    monkeypatch.setattr(adapter, "compose_current", lambda *a, **k: calls.append(1) or real(*a, **k))
    view = wb.refresh(now_ms=T, record_daily=False)
    assert view["wpem_scope"] == {"mode": "active_locks_only", "composed": 0} and calls == []
    assert all("current_questions" not in c and "wpem_summary" not in c for c in view["candidates"])
    shown = {c["snapshot"]["snapshot_id"] for c in view["candidates"]}
    assert set(wb._wpem_pending_payloads) == shown          # kept for an on-demand review
    assert view["release_identity"]                         # still attached

    snap = view["candidates"][0]["snapshot"]
    lock = wb.lock_candidate(snap["candidate_id"], snap["snapshot_id"], "lock-wpem-1", authorization="Bearer secret")
    # the lock composes its own t0 (2026-10-27) from the point's packet, leaving the page's payloads alone
    assert len(calls) == 1 and set(wb._wpem_pending_payloads) == shown
    t0 = wb.locked_candidates.detail(lock["episode"]["episode_id"])["baseline"]
    assert t0["candidate"]["snapshot"]["snapshot_id"] == snap["snapshot_id"]
    assert t0["candidate"]["current_questions"]["research_context"]["items"]
    view = wb.refresh(now_ms=T + 60_000, record_daily=False)
    assert view["wpem_scope"] == {"mode": "active_locks_only", "composed": 1} and len(calls) == 2
    locked = next(c for c in view["candidates"] if c["snapshot"]["candidate_id"] == snap["candidate_id"])
    other = next(c for c in view["candidates"] if c["snapshot"]["candidate_id"] != snap["candidate_id"])
    assert locked["current_questions"]["research_context"] and "wpem_evidence" in locked
    assert "current_questions" not in other
    assert set(wb._wpem_pending_payloads) == {other["snapshot"]["snapshot_id"]}


def test_main_loop_switch_restores_both_sides(tmp_path):
    original, snapshots = make_workbench(tmp_path)
    wb = workbench.AstraWorkbench(replace(original.config, wpem_enabled=True, wpem_main_loop=True),
                                  discovery_capture=original.discovery_capture,
                                  quote_capture=original.quote_capture, clock_ms=original.clock_ms)
    write_fmz(snapshots, native_gate())
    view = wb.refresh(now_ms=T, record_daily=False)
    assert view["wpem_scope"] == {"mode": "main_loop"}
    assert all("current_questions" in c for c in view["candidates"])


def test_lock_t0_carries_research_context_and_expiry_levels(tmp_path):
    """Producer level (2026-10-27): with wpem_main_loop off, the t0 a lock stores has the same
    research context and this expiry's structure as every later sample."""
    wb = _wb(tmp_path)
    view = wb.refresh(now_ms=T, record_daily=False)
    snap = view["candidates"][0]["snapshot"]
    expiry = snap["contract"]["expiry_ms"]
    code = workbench.expiry_gamma.expiry_code(expiry)
    wb._expiry_gamma = {"schema": workbench.expiry_gamma.SCHEMA, "computed_at_ms": T - 60_000, "expiries": {code: {
        "expiry": code, "walls": {"pin": 100.0, "gamma_put": 90.0, "gamma_call": 110.0, "oi_put": 85.0, "oi_call": 120.0},
        "top": {"gamma_put": [{"strike": 90.0, "value": 5.0}], "gamma_call": [{"strike": 110.0, "value": 4.0}]}}}}
    lock = wb.lock_candidate(snap["candidate_id"], snap["snapshot_id"], "lock-levels-1", authorization="Bearer secret")
    episode_id = lock["episode"]["episode_id"]
    t0 = wb.locked_candidates.detail(episode_id)["baseline"]["candidate"]
    items = {i["id"] for i in t0["current_questions"]["research_context"]["items"]}
    assert items and all(i.startswith("context.") for i in items)
    levels = t0["expiry_levels"]
    assert levels["schema"] == "lock_levels@0.1" and levels["expiry"] == code
    assert (levels["pin"], levels["gamma_put"], levels["oi_call"]) == (100.0, 90.0, 120.0)
    assert levels["stale"] is False and len(json.dumps(levels)) < 1024
    # later samples carry the block too, and the follow-up compares it without a "new fact"
    wb.refresh(now_ms=T + 60_000, record_daily=False)
    detail = wb.locked_candidate_detail(episode_id, include_raw=False)
    assert "expiry_levels" in wb.locked_candidates.detail(episode_id)["latest"]["candidate"]
    rows = {r["key"]: r for r in detail["rows"]}
    assert rows["levels.pin"]["t0"]["value"] == 100.0 and rows["levels.pin"]["status"] != "not_recorded_at_lock"
    assert all(r["status"] != "new_fact" for r in detail["rows"] if r["group"] in {"context", "opinion"})
    assert detail["verification_commitments"] is None       # not created by a Decision Path verification


def test_failed_composition_never_blocks_the_lock(tmp_path, monkeypatch):
    wb = _wb(tmp_path)
    view = wb.refresh(now_ms=T, record_daily=False)
    snap = view["candidates"][0]["snapshot"]

    def boom(*a, **k):
        raise RuntimeError("compose down")
    monkeypatch.setattr(wb, "_attach_wpem", boom)
    lock = wb.lock_candidate(snap["candidate_id"], snap["snapshot_id"], "lock-fail-1", authorization="Bearer secret")
    assert lock["created"] is True
    t0 = wb.locked_candidates.detail(lock["episode"]["episode_id"])["baseline"]["candidate"]
    assert "current_questions" not in t0 and t0["snapshot"]["snapshot_id"] == snap["snapshot_id"]


def test_lock_detail_carries_verification_commitments(tmp_path, monkeypatch):
    wb = _wb(tmp_path)
    view = wb.refresh(now_ms=T, record_daily=False)
    snap = view["candidates"][0]["snapshot"]
    episode_id = wb.lock_candidate(snap["candidate_id"], snap["snapshot_id"], "lock-commit-1",
                                   authorization="Bearer secret")["episode"]["episode_id"]
    promised = {"verification_id": "dpv_1", "path_version": "2",
                "limit": {"credit_btc": 0.002, "valid_until_ms": T + 3_600_000, "reservation_btc": 0.0015, "set_at_ms": T},
                "plan": {"choice": "hold", "label_cn": "持有到期"}}
    monkeypatch.setattr(wb.decision_paths, "verification_for_episode", lambda: {episode_id: "dpv_1"})
    monkeypatch.setattr(wb.decision_paths, "commitments", lambda vid: {**promised, "verification_id": vid}, raising=False)
    assert wb.locked_candidate_detail(episode_id, include_raw=False)["verification_commitments"] == promised

    def broken(vid):
        raise RuntimeError("store down")
    monkeypatch.setattr(wb.decision_paths, "commitments", broken, raising=False)
    assert wb.locked_candidate_detail(episode_id, include_raw=False)["verification_commitments"] is None
