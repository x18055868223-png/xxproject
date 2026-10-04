import copy
import json

import pytest

from tools import astra_underwriting_review_v20 as r
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_v20 as u
from test_astra_underwriting_v20 import sample


class Budget:
    def __init__(self):
        self.reservations = []

    def reserve(self, count, **kwargs):
        self.reservations.append(kwargs)
        return {"reservation_id": str(len(self.reservations))}

    def complete(self, reservation_id, status_category, usage):
        pass


def current_payload():
    payload = sample(risk=False)
    payload["market"]["reference_source"] = "deribit_index"
    payload["market"]["short_book"]["index_price"] = 100000
    payload["market"]["long_book"]["index_price"] = 100000
    return payload


def good_answer(snapshot, *, research_only=False):
    projection = r.build_review_input(snapshot, research_only=research_only)
    catalog = projection["claim_catalog"]
    required = projection["decision_relations"]["selection_requirements"]

    def first(role, prefix=None):
        for item in catalog[role]:
            if prefix is None or item["id"].startswith(prefix):
                return item["id"]
        return catalog[role][0]["id"]

    def maybe(role, item_id):
        return [item_id] if any(item["id"] == item_id for item in catalog[role]) else []

    gap_ids = maybe("gaps", "gap.compensation.mu_reference_missing")
    if not gap_ids:
        gap_ids = [first("gaps", "gap.risk_reference")]
    counter_ids = maybe("counterclaims", "counter.compensation.nonpositive_margin")
    if not counter_ids:
        counter_ids = [first("counterclaims", "counter.contract.")]
    reassess_ids = maybe("reassess_conditions", "reassess.compensation_reprice")
    if not reassess_ids:
        reassess_ids = [first("reassess_conditions")]

    return {
        "response_protocol": r.RESPONSE_PROTOCOL,
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "projection_hash": projection["projection_hash"],
        "claim_catalog_hash": projection["claim_catalog_hash"],
        "applicability": "UNABLE_TO_JUDGE",
        "selected_claim_ids": list(dict.fromkeys([first("claims", "claim.contract."), *required["claim_ids"]])),
        "selected_counter_ids": list(dict.fromkeys([*counter_ids, *required["counter_ids"]])),
        "selected_gap_ids": gap_ids,
        "selected_reassess_condition_ids": list(dict.fromkeys([*reassess_ids, *required["reassess_condition_ids"]])),
    }


def response(payload):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload, ensure_ascii=False)}}],
            "usage": {"total_tokens": 99}}


def test_one_bounded_review_reuses_frozen_result_and_isolates_new_manual_snapshot(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "underwriting.jsonl")
    payload = current_payload()
    snapshot = ledger.record_payload(payload)["snapshot"]
    budget = Budget()
    calls = []
    snapshots = {snapshot["snapshot_id"]: snapshot}

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        return response(good_answer(
            snapshots[request["_local_snapshot_id"]],
            research_only=request["_local_review_input"]["research_scope"]["research_only"],
        ))

    review = r.run_review(snapshot, ledger, tmp_path / "states", api_key="fake", model="fake",
                          budget=budget, transport=transport)
    assert review["status"] == "OK"
    assert review["reviewed_snapshot_id"] == snapshot["snapshot_id"]
    assert review["q1"]["title_cn"] == "同一保单适配"
    assert review["q2"]["disposition"] == "information_gap"
    assert len(calls) == len(budget.reservations) == 1
    assert snapshot["snapshot_id"] in calls[0]["messages"][1]["content"]
    assert "frozen_risk_reference_missing" in calls[0]["messages"][1]["content"]
    assert r.run_review(snapshot, ledger, tmp_path / "states", api_key="fake", model="fake",
                        budget=budget, transport=transport) == review
    changed = copy.deepcopy(payload)
    changed["market"]["short_book"]["bids"] = [[0.006, 0.1]]
    newer = ledger.record_payload(changed)["snapshot"]
    assert newer["candidate_id"] == snapshot["candidate_id"]
    assert newer["snapshot_id"] != snapshot["snapshot_id"]
    snapshots[newer["snapshot_id"]] = newer
    result = r.run_review(newer, ledger, tmp_path / "states", api_key="fake", model="fake",
                          budget=budget, transport=transport)
    assert result["status"] == "OK"
    assert result["supports"]
    assert result["reviewed_snapshot_id"] == newer["snapshot_id"]
    assert len(calls) == len(budget.reservations) == 2
    assert r.run_review(newer, ledger, tmp_path / "states", api_key="fake", model="fake",
                        budget=budget, transport=transport) == result
    assert len(calls) == 2


def test_legacy_candidate_attempt_state_is_reused_for_same_snapshot(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "underwriting.jsonl")
    snapshot = ledger.record_payload(current_payload())["snapshot"]
    state_dir = tmp_path / "states"
    state_dir.mkdir()
    legacy = state_dir / f"candidate-{snapshot['candidate_id']}.json"
    old_result = {"status": "rejected", "reviewed_snapshot_id": snapshot["snapshot_id"], "http_calls": 1}
    legacy.write_text(json.dumps({"candidate_id": snapshot["candidate_id"],
                                  "snapshot_id": snapshot["snapshot_id"],
                                  "attempts": [{"status": "SEMANTIC_REJECTED"}],
                                  "settled": True, "result": old_result}), encoding="utf-8")
    calls = []
    assert r.run_review(snapshot, ledger, state_dir, api_key="fake", model="fake",
                        budget=Budget(), transport=lambda *a, **k: calls.append(a)) == old_result
    assert calls == []
    assert r._review_state_path(state_dir, snapshot["candidate_id"], snapshot["snapshot_id"]) == legacy


def test_format_recovery_uses_one_shared_second_attempt(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "underwriting.jsonl")
    snapshot = ledger.record_payload(current_payload())["snapshot"]
    budget = Budget()
    calls = []

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        if len(calls) == 1:
            return response("bad")  # valid JSON string, invalid review object
        return response(good_answer(snapshot))

    result = r.run_review(snapshot, ledger, tmp_path / "states", api_key="fake", model="fake",
                          budget=budget, transport=transport)
    # Semantic invalidity is final; it is never retried for a nicer answer.
    assert result["status"] == "rejected"
    assert len(calls) == len(budget.reservations) == 1
    saved = json.loads(r._review_state_path(tmp_path / "states", snapshot["candidate_id"], snapshot["snapshot_id"]).read_text(encoding="utf-8"))
    assert saved["attempts"][0]["raw_response"] == response("bad")
    assert saved["attempts"][0]["response_hash"] == r._hash(response("bad"))
    assert saved["attempts"][0]["error_reason"]

    ledger2 = store.UnderwritingLedger(tmp_path / "second.jsonl")
    snapshot2 = ledger2.record_payload(current_payload())["snapshot"]
    calls2 = []

    def malformed_then_valid(api_key, model, request, timeout, endpoint=None):
        calls2.append(request)
        if len(calls2) == 1:
            return {"choices": [{"finish_reason": "stop", "message": {"content": "{not json"}}]}
        return response(good_answer(snapshot2))

    recovery = r.run_review(snapshot2, ledger2, tmp_path / "other_states", api_key="fake", model="fake",
                            budget=Budget(), transport=malformed_then_valid)
    assert recovery["status"] == "OK"
    assert recovery["http_calls"] == len(calls2) == 2
    recovered_state = json.loads(r._review_state_path(tmp_path / "other_states", snapshot2["candidate_id"], snapshot2["snapshot_id"]).read_text(encoding="utf-8"))
    assert [item["status"] for item in recovered_state["attempts"]] == ["FORMAT_ERROR", "HTTP_OK"]
    assert all("raw_response" in item and "response_hash" in item for item in recovered_state["attempts"])


def test_unavailable_refs_numbers_and_synthetic_are_rejected(tmp_path):
    snapshot = u.evaluate(current_payload())
    answer = good_answer(snapshot)
    answer["selected_claim_ids"] = ["claim.risk.supported_reference"]
    with pytest.raises(r.ReviewValidationError, match="bounded catalog"):
        r.validate_wire_response(snapshot, answer)
    answer = good_answer(snapshot)
    answer["summary_cn"] = "胜率 81% 已确认。"
    with pytest.raises(r.ReviewValidationError, match="frozen schema"):
        r.validate_wire_response(snapshot, answer)
    ledger = store.UnderwritingLedger(tmp_path / "underwriting.jsonl")
    synthetic = ledger.record_payload(sample())["snapshot"]
    with pytest.raises(ValueError, match="synthetic"):
        r.run_review(synthetic, ledger, tmp_path / "states", api_key="fake", model="fake",
                     budget=Budget(), transport=lambda *_: None)


def test_missing_compensation_inputs_cannot_be_explained_without_gap():
    snapshot = u.evaluate(current_payload())
    answer = good_answer(snapshot)
    answer["applicability"] = "EXPLAINED"
    with pytest.raises(r.ReviewValidationError, match="explicit gap"):
        r.validate_wire_response(snapshot, answer)
    answer["applicability"] = "UNABLE_TO_JUDGE"
    answer["selected_gap_ids"] = []
    with pytest.raises(r.ReviewValidationError, match="explicit gap"):
        r.validate_wire_response(snapshot, answer)


def test_missing_mu_requires_explicit_compensation_gap():
    snapshot = u.evaluate(current_payload())
    answer = good_answer(snapshot)
    answer["selected_gap_ids"] = ["gap.risk_reference_missing"]
    with pytest.raises(r.ReviewValidationError, match="missing mu"):
        r.validate_wire_response(snapshot, answer)


def test_local_review_persistence_failure_never_spends_a_recovery_http_call(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(current_payload())["snapshot"]
    calls = []
    original_attach = ledger.attach_review

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        return response(good_answer(snapshot))

    def fail_once(*args, **kwargs):
        raise OSError("local disk unavailable")

    ledger.attach_review = fail_once
    result = r.run_review(snapshot, ledger, tmp_path / "states", api_key="fake", model="fake",
                          budget=Budget(), transport=transport)
    assert result["status"] == "persistence_failed"
    assert len(calls) == 1
    ledger.attach_review = original_attach
    recovered = r.run_review(snapshot, ledger, tmp_path / "states", api_key="fake", model="fake",
                             budget=Budget(), transport=transport)
    assert recovered["status"] == "OK"
    assert len(calls) == 1
