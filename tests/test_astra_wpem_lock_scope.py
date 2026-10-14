"""WPEM step 2 (2026-10-14): the refresh composes packages only for contracts under active
lock tracking; the other displayed candidates keep their raw capture for an on-demand review."""
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
    wb.lock_candidate(snap["candidate_id"], snap["snapshot_id"], "lock-wpem-1", authorization="Bearer secret")
    view = wb.refresh(now_ms=T + 60_000, record_daily=False)
    assert view["wpem_scope"] == {"mode": "active_locks_only", "composed": 1} and len(calls) == 1
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
