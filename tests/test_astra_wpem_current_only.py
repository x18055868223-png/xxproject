"""WPEM step 1 (2026-10-14): refresh composes from the current point only."""
from dataclasses import replace

from tools import astra_workbench_v20 as workbench
from tools import astra_wpem_workbench_v1 as adapter
from tools import astra_candidate_asof_v1 as packets
from test_astra_workbench_v20 import make_workbench, write_fmz, native_gate, T


def _wb(tmp_path, **overrides):
    original, snapshots = make_workbench(tmp_path)
    wb = workbench.AstraWorkbench(replace(original.config, wpem_enabled=True, **overrides),
                                  discovery_capture=original.discovery_capture,
                                  quote_capture=original.quote_capture, clock_ms=original.clock_ms)
    write_fmz(snapshots, native_gate())
    return wb


def test_default_refresh_does_not_sample_or_replay_the_archive(tmp_path, monkeypatch):
    wb = _wb(tmp_path)
    assert wb.config.wpem_history_replay is False
    monkeypatch.setattr(adapter, "compose", lambda *a, **k: (_ for _ in ()).throw(AssertionError("replayed")))
    view = wb.refresh(now_ms=T)
    item = view["candidates"][0]
    assert item["wpem_evidence"]["snapshot_id"] == item["snapshot"]["snapshot_id"]
    assert "history_replay_isolated" not in item["review_validity"]
    assert item["current_questions"]["research_context"]
    assert wb._wpem_archive.index_rows() == []
    health = view["data_health"]["wpem_archive"]
    assert health["sampling"] is False and health["used_bytes"] == wb._wpem_archive.used_bytes()
    # the full proof is still served and binds the current snapshot
    proof = wb.wpem_evidence(item["snapshot"]["snapshot_id"])
    adapter.require_current_package_binding(item["snapshot"], proof)
    adapter.validate_evidence_package(item["snapshot"], proof)


def test_current_compose_matches_compose_without_history(tmp_path):
    wb = _wb(tmp_path, wpem_history_replay=True)
    view = wb.refresh(now_ms=T)
    item = view["candidates"][0]
    replayed = wb.wpem_evidence(item["snapshot"]["snapshot_id"])
    # one archived row per candidate at this point: compose() had no prior history
    assert len([r for r in wb._wpem_archive.index_rows()
                if r["candidate_id"] == item["snapshot"]["candidate_id"]]) == 1
    raw_payload = replayed["composition_inputs"]["packet_sequence"][-1]["raw_payload"]
    kwargs = dict(raw_payload=raw_payload, engine=wb.data_engine, release=view["release_identity"],
                  cross_section=replayed["composition_inputs"]["cross_section"])
    fresh = packets.CandidateResearchArchive(tmp_path / "fresh_archive")
    composed = adapter.compose(item["snapshot"], fresh, wb.config.snapshots_jsonl, now_ms=T, **kwargs)
    current = adapter.compose_current(item["snapshot"], wb.config.snapshots_jsonl,
                                      native_cache=fresh.path / "native_sources", **kwargs)
    assert current["evidence_hash"] == composed["evidence_hash"]
    assert current["packet_hash"] == composed["packet_hash"]
    assert current["packet_hash"] == replayed["packet_hash"]


def test_sources_cache_rereads_after_the_file_changes(tmp_path):
    path = tmp_path / "native.jsonl"
    path.write_bytes(b'{"a":1}\n')
    first = adapter._sources_cached(path)
    path.write_bytes(b'{"a":1}\n{"b":2}\n')
    second = adapter._sources_cached(path)
    assert len(first) == 1 and len(second) == 2
    second.clear()
    assert len(adapter._sources_cached(path)) == 2  # callers get a copy
