from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from tools import astra_observation_replay_v1 as replay


ROOT = Path(__file__).resolve().parents[1]


def test_fresh3_replay_preserves_same_input_semantic_digest(tmp_path):
    report = replay.run_replay(ROOT, tmp_path, cases=("fresh_3",), replayed_at_ms=1234567890)

    case = report["cases"][0]
    assert case["status"] == "PASS"
    assert case["first_divergence"] is None
    assert case.get("llm_http_calls", report["llm_http_calls"]) == 0

    view = replay.read_json(tmp_path / "samples/fresh_3/view.json")
    original = replay.read_json(ROOT / ".artifacts/consumption_ui_audit_20261007_01/samples/fresh_3/view.json")
    assert replay.view_semantic_digest(view) == replay.view_semantic_digest(original)
    assert view["observation_replay"]["original_consumed_at_ms"] is None
    assert view["observation_replay"]["llm_http_calls"] == 0
    assert view["observation_replay"]["first_divergence"] is None


def test_tampered_frozen_request_evidence_trips_clean_input_boundary():
    folder = ROOT / ".artifacts/semantic_synthesis_20261007_01/samples/call_early_v105"
    request = replay.read_json(folder / "actual_llm/request_http_body.bin")
    frozen = replay.read_json(folder / "review_input.json")
    tampered = deepcopy(request)
    prompt, evidence_text = tampered["messages"][1]["content"].split("\n本次证据：", 1)
    evidence = json.loads(evidence_text)
    evidence["program_facts"]["candidate_id"] = "tampered-candidate"
    tampered["messages"][1]["content"] = prompt + "\n本次证据：" + json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    check = replay.edge(
        "clean evidence -> original rejected LLM input",
        "PASS" if replay.evidence_input_from_request(tampered) == frozen["evidence_input"] else "FAIL",
        "tampered clean evidence",
    )

    assert replay.first_divergence([check]) == {
        "edge": "clean evidence -> original rejected LLM input",
        "reason_cn": "tampered clean evidence",
    }


def test_missing_artifact_fails_closed_without_invented_data(tmp_path):
    missing_root = tmp_path / "missing_repo"
    missing_root.mkdir()
    original_socket = socket.socket

    with pytest.raises(replay.ReplayError) as exc:
        replay.run_replay(missing_root, tmp_path / "out", cases=("fresh_3",))

    assert "missing_artifact" in str(exc.value)
    assert socket.socket is original_socket


def test_rejected_semantic_fail_is_preserved():
    ctx = replay.ReplayContext(ROOT, replayed_at_ms=1234567890)
    view, case = ctx.replay_current_rejected()

    assert case["status"] == "PASS"
    assert view["observation_replay"]["original_analysis_identity"]["original_disposition"] == "semantic_rejected"
    assert view["observation_replay"]["original_analysis_identity"]["semantic_verdict"] == "FAIL"
    assert view["candidates"][0]["review_display"]["current"] is None
    assert any(check["edge"] == "LLM final -> semantic validation" for check in case["checks"])


def test_public_scrub_removes_private_raw_and_keeps_source_path():
    payload = {
        "provider_response": {"secret": True},
        "reasoning_content": "private",
        "local_path": r"C:\Users\Xu\secret\provider.json",
        "lineage": {"source_path": "source_facts.quote"},
    }

    cleaned = replay.scrub_public(deepcopy(payload))
    assert "provider_response" not in cleaned
    assert "reasoning_content" not in cleaned
    assert "local_path" not in cleaned
    assert cleaned["lineage"]["source_path"] == "source_facts.quote"


def test_manifest_case_specs_support_non_default_names():
    manifest = {
        "cases": [
            {"name": "fresh_3_copy", "kind": "accepted_current", "sample": "fresh_3"},
            {"name": "fallback_copy", "kind": "current_rejected", "current_sample": "call_early_v105"},
        ]
    }

    selected = replay.case_specs_from_manifest(manifest, ("fresh_3_copy",))
    assert selected == [{"name": "fresh_3_copy", "kind": "accepted_current", "sample": "fresh_3"}]


def test_rejected_semantic_verdict_requires_original_final_raw_and_receipt(tmp_path):
    folder = tmp_path / "actual_llm"
    folder.mkdir()
    files = {"raw_http_body_sha256": "provider_http_body.bin",
             "message_content_sha256": "message_content.txt",
             "original_receipt_sha256": "receipt.json"}
    semantic = {}
    for key, name in files.items():
        (folder / name).write_bytes(b"original frozen bytes")
        semantic[key] = replay.file_sha256(folder / name)
    assert replay.rejected_final_hashes_match(tmp_path, semantic)
    for name in files.values():
        (folder / name).write_bytes(b"mismatched bytes")
        assert not replay.rejected_final_hashes_match(tmp_path, semantic)
        (folder / name).write_bytes(b"original frozen bytes")
    (folder / "message_content.txt").unlink()
    assert not replay.rejected_final_hashes_match(tmp_path, semantic)


def test_missing_old_renderer_is_not_filled_from_current_renderer(tmp_path):
    frontend = tmp_path / "deploy/signal_audit/frontend"
    frontend.mkdir(parents=True)
    (frontend / "VERSION.json").write_text('{"frontend_cache_token":"new-renderer"}', encoding="utf-8")
    original = replay.ReplayContext(tmp_path).original_renderer_identity()
    assert original["source"] == "not_recorded"
    assert original["frontend_cache_token"] is None
