"""Provider boundary, replay binding and failure fallback for the existing path."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_store_v20 as store
from tools import astra_wpem_product_review_v1 as product
from tools import astra_workbench_v20 as workbench
from tools import signal_llm_review as llm
from test_astra_bounded_claims_v244 import Budget
from test_astra_grounded_explanation import routed_fixture
from tests import test_astra_workbench_v20 as workbench_fx


def points():
    return {
        "response_protocol": "evidence_points@2.6.4",
        "q1": [{"text_cn": "本保单应按给出的不利方向理解已有证据；适配解释不替代程序责任账。",
                "evidence_refs": ["contract"]}],
        "q2": [{"text_cn": "当前报价不代表实际成交，补偿取舍应以本次冻结报价为依据。",
                "evidence_refs": ["quote"]}],
    }


def test_actual_provider_body_contains_only_clean_evidence():
    snapshot, evidence = routed_fixture()
    request = review.build_request(snapshot, "local-test", wpem_evidence=evidence)
    local = request["_local_review_input"]
    wire = llm._strip_local_request_fields(request)
    provided = json.loads(wire["messages"][1]["content"].split("\n本次证据：", 1)[1])
    assert provided == local["evidence_input"]
    assert local["claim_catalog"] and local["input_lineage"]
    serialized = json.dumps(provided)
    for key in ("claim_catalog", "source_bindings", "composition_inputs", "deterministic_financials",
                "projection_hash", "selected_claim_ids", "explanation_contract", "packet_hash",
                "source_book_hash", "claim_id", "counter_id"):
        assert f'"{key}"' not in serialized
    assert not any(key.startswith("_local") for key in wire)
    assert request["_local_request_hash"] == review._hash(wire)
    assert wire["thinking"] == {"type": "enabled"}
    assert wire["reasoning_effort"] == "max"

    lookup = product._grounding_lookup(local)
    for item in provided["evidence"]:
        assert lookup[item["ref"]]["provided"] == item
        assert lookup[item["ref"]]["lineage"] == local["input_lineage"][item["ref"]]


def test_cost_comparison_unknown_is_local_to_the_named_series():
    snapshot, evidence = routed_fixture()
    projection = product.build_review_input(snapshot, evidence, model="local-test")
    projection["evidence_input"]["evidence"].append({"ref": "map.cost", "relations": {
        "cp_sth_usd_vs_source_price": "below_source_price",
        "cp_lth_usd_vs_source_price": "unknown_not_same_source_day",
        "cp_all_usd_vs_source_price": "below_source_price",
    }})
    payload = points()
    payload["q1"][0] = {"text_cn": "STH成本低于Bitview源价。", "evidence_refs": ["map.cost"]}
    product.validate_evidence_points(payload, projection)
    for text in ("CP低于Bitview源价。", "LTH成本高于Bitview源价。"):
        payload["q1"][0]["text_cn"] = text
        with pytest.raises(review.ReviewValidationError, match="comparison unavailable"):
            product.validate_evidence_points(payload, projection)
    payload["q1"][0]["text_cn"] = "不能说LTH成本高于Bitview源价。"
    product.validate_evidence_points(payload, projection)


def test_unrealized_quote_caveat_does_not_claim_onchain_realized_pnl():
    # Actual provider wording about quote proceeds must not require an unrelated
    # on-chain reference merely because it contains the generic word 已实现.
    product._validate_topic_direct_refs(
        "不能把冻结报价当成已实现补偿。", ["quote"]
    )
    with pytest.raises(review.ReviewValidationError, match="direct evidence ref"):
        product._validate_topic_direct_refs(
            "链上已实现损益窗口有观测。", ["quote"]
        )
    product._validate_topic_direct_refs(
        "链上已实现损益窗口有观测。", ["map.realized"]
    )


def test_nonconfirmation_caveat_does_not_hide_uncited_flow_facts():
    caveat = "TMV仅为价格路径背景，不与主动流交叉确认。"
    product._validate_topic_direct_refs(caveat, ["tmv"])
    with pytest.raises(review.ReviewValidationError, match="direct evidence ref"):
        product._validate_topic_direct_refs(caveat + "主动流快窗偏多。", ["tmv"])
    product._validate_topic_direct_refs(caveat + "主动流快窗偏多。", ["tmv", "flow"])


@pytest.mark.parametrize("question,index", [("q1", 1), ("q2", 2), ("q2", 3)])
def test_first_actual_clean_response_retains_uncited_facts_as_rejections(question, index):
    folder = Path(__file__).resolve().parents[1] / ".artifacts/auditable_observation_20261006_01/samples/clean_main"
    if not (folder / "actual_llm/message_content.txt").exists():
        pytest.skip("actual clean response unavailable")
    projection = json.loads((folder / "review_input.json").read_bytes())
    original = json.loads((folder / "actual_llm/message_content.txt").read_bytes())
    payload = points()
    payload[question][0] = original[question][index]
    with pytest.raises(review.ReviewValidationError, match="direct evidence ref"):
        product.validate_evidence_points(payload, projection)


@pytest.mark.parametrize("field", ["evidence_input", "input_lineage"])
def test_recomputed_hash_does_not_authorize_forged_input_or_lineage(field):
    snapshot, evidence = routed_fixture()
    projection = product.build_review_input(snapshot, evidence, model="local-test")
    if field == "evidence_input":
        projection[field]["evidence"][0]["facts"] = {"forged": True}
    else:
        projection[field]["contract"]["source_path"] = "another_request.contract"
    projection["projection_hash"] = review._hash({key: value for key, value in projection.items()
                                                  if key != "projection_hash"})
    with pytest.raises(review.ReviewValidationError, match="replay mismatch"):
        review.validate_wire_response(snapshot, points(), review_input=projection, wpem_evidence=evidence)


@pytest.mark.parametrize("kind", ["timeout", "malformed", "unknown_ref"])
def test_failed_clean_request_retains_original_and_never_retries(tmp_path, kind):
    snapshot, evidence = routed_fixture()
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    ledger.record_snapshot(snapshot)
    calls = []

    def transport(*args, **kwargs):
        calls.append(args[2])
        if kind == "timeout":
            raise TimeoutError("test timeout")
        answer = points()
        answer["q1"][0]["evidence_refs"] = ["not_in_this_input"]
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": "not JSON" if kind == "malformed" else json.dumps(answer, ensure_ascii=False)}}]}

    budget = Budget()
    kwargs = dict(api_key="test-only", model="local-test", budget=budget,
                  transport=transport, research_only=True, wpem_evidence=evidence)
    result = review.run_review(snapshot, ledger, tmp_path / "review", **kwargs)
    assert result["status"] in {"failed", "rejected"}
    assert result["http_calls"] == len(calls) == 1
    assert result["decision_trace"]["input_evidence"]
    assert result["decision_trace"]["points"] == []
    assert "llm_explanation" not in result
    assert result["execution_allowed"] is False
    assert review.run_review(snapshot, ledger, tmp_path / "review", **kwargs) == result
    assert len(calls) == 1
    state_files = list((tmp_path / "review").rglob("*.json"))
    state = next(json.loads(path.read_bytes()) for path in state_files if "attempts" in json.loads(path.read_bytes()))
    assert len(state["attempts"]) == 1
    if kind != "timeout":
        assert state["attempts"][0]["raw_response"]["choices"][0]["message"]["content"]
    assert ledger.state()["snapshots"][snapshot["snapshot_id"]]["snapshot"] == snapshot


def test_success_research_copies_readonly_trace_without_attaching(tmp_path):
    snapshot, evidence = routed_fixture()
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    ledger.record_snapshot(snapshot)
    result = review.run_review(
        snapshot, ledger, tmp_path / "review", api_key="test-only", model="local-test", budget=Budget(),
        research_only=True, wpem_evidence=evidence,
        transport=lambda *args, **kwargs: {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(points(), ensure_ascii=False)}}]},
    )
    assert result["status"] == "RESEARCH_OK"
    assert result["production_attach_allowed"] is False
    assert result["selection_origin"] == "program_guardrails"
    assert result["decision_trace"]["points"]
    assert result["llm_explanation"]["q1"]["points"][0]["evidence_refs"] == ["contract"]
    assert not ledger.state()["snapshots"][snapshot["snapshot_id"]].get("review")


def test_store_replays_points_and_rejects_edited_trace_or_safety_ids():
    root = Path(__file__).resolve().parents[1] / ".artifacts/observation_baseline_20261006_01/samples/current_validated"
    if not root.exists():
        pytest.skip("real frozen sample unavailable")
    snapshot = json.loads((root / "snapshot.json").read_bytes())
    evidence = json.loads((root / "evidence.json").read_bytes())
    projection = product.build_review_input(snapshot, evidence, model="local-test")
    normalized = review.validate_wire_response(snapshot, points(), review_input=projection, wpem_evidence=evidence)
    normalized["http_calls"] = 1
    store._validate_same_snapshot_review(snapshot, normalized)
    for field, replacement in (
        ("decision_trace", {}), ("selection_origin", "llm"), ("selected_counter_ids", []),
        ("projection_hash", "not-the-frozen-input"), ("http_calls", 2),
    ):
        tampered = deepcopy(normalized)
        tampered[field] = replacement
        with pytest.raises(ValueError):
            store._validate_same_snapshot_review(snapshot, tampered)
    tampered = deepcopy(normalized)
    tampered["llm_explanation"]["q1"]["points"][0]["evidence"][0]["provided"] = {"forged": True}
    with pytest.raises(ValueError):
        store._validate_same_snapshot_review(snapshot, tampered)


def test_public_trace_uses_existing_private_proof_boundary():
    receipt = {"status": "RESEARCH_OK", "review": {
        "response_protocol": "evidence_points@2.6.4",
        "decision_trace": {"input_evidence": [{"ref": "contract", "facts": {"side": "call"},
                           "source_bindings": {"private": "canary"}}]},
        "llm_explanation": {"q1": {"points": [{"composition_inputs": {"private": "canary"}}]}},
    }}
    public = workbench._review_receipt_for_display(receipt)
    assert public["status"] == "RESEARCH_OK"
    assert "canary" not in json.dumps(public)
    assert public["review"]["decision_trace"]["input_evidence"][0]["facts"] == {"side": "call"}
    assert "canary" in json.dumps(receipt)


def test_failure_exposes_prior_valid_as_history_without_overwriting_attempt(tmp_path):
    wb, _ = workbench_fx.make_workbench(tmp_path)
    prior = {"status": "RESEARCH_OK", "snapshot_id": "old-frozen", "candidate_id": "old-candidate",
             "review": {"response_protocol": "evidence_points@2.6.4"}}
    failure = {"status": "rejected", "snapshot_id": "new-frozen", "candidate_id": "new-candidate"}
    wb._finish_review("prior", prior, workbench_fx.T)
    wb._finish_review("failed", failure, workbench_fx.T + 1)
    before = wb.config.state_path.read_bytes()
    display = wb.manual_review_state(workbench_fx.T + 2)
    assert display["last_result"] == failure
    assert display["previous_valid_result"]["snapshot_id"] == "old-frozen"
    assert display["previous_valid_result"]["historical_only"] is True
    assert wb.config.state_path.read_bytes() == before
