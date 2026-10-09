import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_v20 as core
from test_astra_bounded_claims_v244 import Budget
from test_astra_underwriting_v20 import sample


SAMPLE_ROOT = Path(__file__).resolve().parents[1] / ".artifacts/auditable_observation_20261006_01/samples/clean_followup1"


def _before_review_module():
    path = (Path(__file__).resolve().parents[1] /
            ".artifacts/topology_realignment_20261006_01/before/tools/astra_underwriting_review_v20.py")
    if not path.exists():
        pytest.skip("topology realignment before snapshot is unavailable")
    spec = importlib.util.spec_from_file_location("tools._before_astra_underwriting_review_v20", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _frozen_clean_sample():
    if not (SAMPLE_ROOT / "review_input.json").exists():
        pytest.skip("clean followup frozen review sample is unavailable")
    return (
        json.loads((SAMPLE_ROOT / "snapshot.json").read_text(encoding="utf-8")),
        json.loads((SAMPLE_ROOT / "evidence.json").read_text(encoding="utf-8")),
        json.loads((SAMPLE_ROOT / "review_input.json").read_text(encoding="utf-8")),
    )


def test_run_review_reuses_prebuilt_wpem_input_for_request(monkeypatch, tmp_path):
    snapshot, evidence, frozen_input = _frozen_clean_sample()
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    ledger.record_snapshot(snapshot)
    wpem_build_calls = []
    sent_requests = []

    def counted_build_review_input(snapshot_arg, **kwargs):
        if kwargs.get("wpem_evidence") is not None:
            wpem_build_calls.append(kwargs["wpem_evidence"]["evidence_hash"])
        assert snapshot_arg["snapshot_id"] == snapshot["snapshot_id"]
        return deepcopy(frozen_input)

    def transport(_api_key, _model, request, _timeout, endpoint=None):
        sent_requests.append(request)
        return {"choices": [{"finish_reason": "stop", "message": {"content": "not JSON"}}]}

    monkeypatch.setattr(review, "build_review_input", counted_build_review_input)

    result = review.run_review(
        snapshot,
        ledger,
        tmp_path / "review",
        api_key="test-only",
        model="deepseek-v4-flash",
        budget=Budget(),
        transport=transport,
        research_only=True,
        wpem_evidence=evidence,
    )

    assert result["status"] == "failed"
    assert wpem_build_calls == [evidence["evidence_hash"]]
    assert len(sent_requests) == 1
    assert sent_requests[0]["_local_review_input"]["projection_hash"] == frozen_input["projection_hash"]


def test_build_request_from_input_rejects_cross_snapshot_and_hash_drift():
    snapshot = core.evaluate(sample("put"))
    other_snapshot = core.evaluate(sample("call"))
    frozen = review.build_review_input(snapshot, research_only=True)

    with pytest.raises(ValueError, match="snapshot|packet|candidate"):
        review.build_request_from_input(other_snapshot, "local-test", frozen, research_only=True)

    wrong_candidate = deepcopy(frozen)
    wrong_candidate["candidate_id"] = "another-candidate"
    with pytest.raises(ValueError, match="candidate mismatch"):
        review.build_request_from_input(snapshot, "local-test", wrong_candidate, research_only=True)

    wrong_packet_hash = deepcopy(frozen)
    wrong_packet_hash["packet_hash"] = "not-the-packet"
    with pytest.raises(ValueError, match="packet hash mismatch"):
        review.build_request_from_input(snapshot, "local-test", wrong_packet_hash, research_only=True)

    wrong_projection_hash = deepcopy(frozen)
    wrong_projection_hash["projection_hash"] = "not-the-projection"
    with pytest.raises(ValueError, match="projection hash mismatch"):
        review.build_request_from_input(snapshot, "local-test", wrong_projection_hash, research_only=True)

    wrong_scope = deepcopy(frozen)
    wrong_scope["research_scope"]["research_only"] = False
    wrong_scope["projection_hash"] = review._hash({key: value for key, value in wrong_scope.items()
                                                   if key != "projection_hash"})
    with pytest.raises(ValueError, match="research scope mismatch"):
        review.build_request_from_input(snapshot, "local-test", wrong_scope, research_only=True)

    wrong_model = deepcopy(frozen)
    wrong_model["llm_identity"] = {"requested_model": "model-a"}
    wrong_model["projection_hash"] = review._hash({key: value for key, value in wrong_model.items()
                                                   if key != "projection_hash"})
    with pytest.raises(ValueError, match="model mismatch"):
        review.build_request_from_input(snapshot, "model-b", wrong_model, research_only=True)


def test_public_build_request_matches_prebuilt_entrypoint():
    snapshot = core.evaluate(sample("put"))
    review_input = review.build_review_input(snapshot, research_only=True)

    public_request = review.build_request(snapshot, "local-test", research_only=True)
    prebuilt_request = review.build_request_from_input(
        snapshot,
        "local-test",
        review_input,
        research_only=True,
    )

    assert public_request == prebuilt_request


def test_legacy_review_input_and_request_match_before_snapshot():
    before = _before_review_module()
    snapshot = core.evaluate(sample("put"))

    for research_only in (False, True):
        current_input = review.build_review_input(snapshot, research_only=research_only)
        before_input = before.build_review_input(snapshot, research_only=research_only)
        assert json.dumps(current_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
            before_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    current_request = review.build_request(snapshot, "local-test", research_only=True)
    before_request = before.build_request(snapshot, "local-test", research_only=True)
    assert json.dumps(current_request, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
        before_request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def test_review_facts_helper_excludes_catalog_and_preserves_public_input():
    snapshot = core.evaluate(sample("put"))
    facts = review._build_review_facts(snapshot, research_only=True)
    public_input = review.build_review_input(snapshot, research_only=True)

    assert "claim_catalog" not in facts
    assert "claim_catalog_hash" not in facts
    assert "projection_hash" not in facts
    for key, value in facts.items():
        assert public_input[key] == value
    assert public_input["claim_catalog"]
    assert public_input["claim_catalog_hash"]
    assert public_input["projection_hash"]
