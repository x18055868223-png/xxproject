import copy
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from tools import astra_workbench_v20 as workbench
from tools import astra_wpem_workbench_v1 as wpem_adapter
from test_astra_auditable_observation_pipeline import points
from test_astra_observation_wiring import _wpem_workbench
from test_astra_workbench_v20 import T, fake_quote_capture, native_gate


def _review_over_http(wb, candidate_id, key):
    server = ThreadingHTTPServer(("127.0.0.1", 0), workbench.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps({
            "candidate_id": candidate_id,
            "idempotency_key": key,
            "purpose": "observation",
        }).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/workbench/review",
            data=body,
            headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            return json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _future_put_quote(short_instrument, long_instrument, quantity_btc, *, now_ms):
    payload = fake_quote_capture(short_instrument, long_instrument, quantity_btc, now_ms=now_ms)
    if short_instrument.endswith("-P"):
        payload["market"]["long_book"]["timestamp"] = payload["market"]["valuation_ts_ms"] + 1
    return payload


def test_history_failure_uses_current_only_evidence_for_observation_input(tmp_path, monkeypatch):
    calls = []
    captured = {}

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(points(), ensure_ascii=False)}}]}

    def fake_run_review(snapshot, ledger, review_state_dir, *, wpem_evidence=None, research_only=False, **kwargs):
        captured["snapshot"] = copy.deepcopy(snapshot)
        captured["wpem_evidence"] = copy.deepcopy(wpem_evidence)
        return {
            "status": "RESEARCH_OK",
            "response_protocol": "evidence_points@2.6.4",
            "http_calls": 0,
            "research_only": research_only,
            "production_attach_allowed": False,
            "execution_allowed": False,
        }

    wb, _snapshots = _wpem_workbench(tmp_path, transport=transport)
    monkeypatch.setattr(wpem_adapter, "compose", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("history archive corrupt")))
    monkeypatch.setattr(wpem_adapter, "compose_current", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("history archive corrupt")))
    monkeypatch.setattr(workbench.review_core, "run_review", fake_run_review)

    view = wb.refresh(now_ms=T)
    item = view["candidates"][0]
    snapshot = copy.deepcopy(item["snapshot"])
    assert item["observation_review"] == {"enabled": True, "reasons": []}
    assert "structure_gate_not_eligible" not in item["review_gate"]["reasons"]  # evaluated, not gated
    assert item["snapshot"]["economics"] == snapshot["economics"]
    assert item["review_validity"]["history_replay_isolated"] is True
    assert item["current_questions"]["audit"]["temporal_scope"] == "current_snapshot_only"
    assert any("历史证据回放未通过" in text for text in item["wpem_summary"]["breaks_cn"])

    result = _review_over_http(wb, snapshot["candidate_id"], "topology-current-only")

    assert result["status"] == "RESEARCH_OK"
    assert calls == []
    assert captured["snapshot"]["snapshot_id"] == snapshot["snapshot_id"]
    evidence = captured["wpem_evidence"]
    packet = evidence["source_bindings"]["current_packet"]
    assert packet["sources"]["raw_payload_hash"]
    replay = workbench.candidate_asof.replay_packet(packet)
    assert replay["raw_quote_status"] == "raw_leg_books_replayed"
    assert len(evidence["composition_inputs"]["packet_sequence"]) == 1
    assert item["review_validity"]["history_replay_error_type"] == "RuntimeError"
    assert evidence["snapshot_id"] == snapshot["snapshot_id"]
    assert evidence["same_candidate_window"]["status"] == "INSUFFICIENT"
    path = evidence["same_candidate_window"]["LevelDeltaPath"]["path"]
    delta = evidence["same_candidate_window"]["LevelDeltaPath"]["delta"]
    assert path["status"] == "NO_PATH_DUE_TO_WINDOW_GAP"
    assert delta["status"] == "NO_STRICT_DELTA"


def test_corrupt_composed_package_is_discarded_before_cache(tmp_path, monkeypatch):
    wb, _snapshots = _wpem_workbench(tmp_path)
    real_build = wpem_adapter.current_only_evidence

    def corrupt_compose(snapshot, *args, **kwargs):
        package = real_build(snapshot, release=kwargs.get("release"), cross_section=kwargs.get("cross_section") or ())
        package["candidate_id"] = "wrong-candidate"
        return package

    monkeypatch.setattr(wpem_adapter, "compose", corrupt_compose)
    monkeypatch.setattr(wpem_adapter, "compose_current", corrupt_compose)

    item = wb.refresh(now_ms=T)["candidates"][0]
    proof = wb.wpem_evidence(item["snapshot"]["snapshot_id"])

    assert proof["candidate_id"] == item["snapshot"]["candidate_id"]
    assert proof["snapshot_id"] == item["snapshot"]["snapshot_id"]
    assert item["review_validity"]["history_replay_isolated"] is True
    assert item["review_validity"]["reason"] == "history_replay_isolated_current_only"


def test_invalid_current_snapshot_still_blocks_without_fallback_proof(tmp_path):
    wb, _snapshots = _wpem_workbench(tmp_path, quote_capture=_future_put_quote)
    view = wb.refresh(now_ms=T)
    put = next(item for item in view["candidates"] if item["side"] == "put")

    assert put["wpem_evidence"]["status"] == "BLOCKED"
    assert put["review_validity"]["reason"] == "evidence_replay_failed"
    assert "history_replay_isolated" not in put["review_validity"]
    with pytest.raises(Exception) as exc_info:
        wb.wpem_evidence(put["snapshot"]["snapshot_id"])
    assert getattr(exc_info.value, "status_code", None) == 404


def test_current_only_public_get_does_not_call_llm_or_change_space_financials(tmp_path, monkeypatch):
    calls = []
    wb, snapshots = _wpem_workbench(tmp_path, transport=lambda *args, **kwargs: calls.append(args))
    snapshots.write_text("", encoding="utf-8")
    from tests import test_astra_workbench_v20 as fx
    fx.write_fmz(snapshots, native_gate("STRUCTURE_BLOCKED"))
    monkeypatch.setattr(wpem_adapter, "compose", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("history source missing")))

    item = wb.refresh(now_ms=T)["candidates"][0]
    snapshot = item["snapshot"]
    before = copy.deepcopy(snapshot["economics"])
    public = item["wpem_evidence"]
    proof = wb.wpem_evidence(snapshot["snapshot_id"])

    assert "structure_gate_not_eligible" not in item["review_gate"]["reasons"]  # evaluated, not gated
    assert item["observation_review"] == {"enabled": True, "reasons": []}
    assert snapshot["economics"] == before
    assert public["evidence_hash"] == proof["evidence_hash"]
    assert "composition_inputs" not in public
    assert "composition_inputs" in proof
    assert calls == []
