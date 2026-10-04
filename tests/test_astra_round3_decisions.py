import copy

import pytest

from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_v20 as core
from test_astra_round2_relations import _candidate, _wire
from test_astra_underwriting_review_v20 import current_payload
from test_astra_underwriting_v20 import sample


def _review(snapshot, *, applicability="CONTESTED"):
    wire, projection = _wire(snapshot, applicability=applicability)
    return review.validate_wire_response(snapshot, wire, review_input=projection), projection


def _q2_text(q2):
    fields = [
        q2["summary_cn"],
        q2["exchange_reason_cn"],
        q2["coverage_cn"],
        q2["liability_cn"],
        q2["waiting_cost_cn"],
        *q2["observable_changes_cn"],
    ]
    return "\n".join(fields)


def test_positive_margin_keeps_watch_action_without_new_coverage_gate():
    w1_put, w1_projection = _review(_candidate("W1", "put"))
    w2_call, _ = _review(_candidate("W2", "call"))

    assert w1_put["q2"]["disposition"] == "continue_evaluation"
    assert "保留关注" in w1_put["q2"]["summary_cn"]
    assert "终审前讨论" in w1_put["q2"]["summary_cn"]
    assert "未知本身不禁止继续终审讨论" in w1_put["q2"]["coverage_cn"]
    assert len(w1_put["q2"]["observable_changes_cn"]) <= 2
    assert w1_put["q2"]["observable_changes_cn"]
    assert "执行许可" not in _q2_text(w1_put["q2"])
    assert w1_projection["decision_relations"]["schema"] == "astra_underwriting_decision_relations@2.4.7"
    assert w1_put["response_protocol"] == "bounded_claim_ids@2.4.7"

    assert w2_call["q2"]["disposition"] == "continue_evaluation"
    assert "是否接受该反证由交易员权衡" in w2_call["q2"]["summary_cn"]
    assert "counter.direction.flow_adverse_not_quantified" in w2_call["selected_counter_ids"]
    assert "风险逐项覆盖未知" in w2_call["q2"]["coverage_cn"]


def test_nonpositive_blocked_and_missing_mu_keep_priority():
    w4_call, _ = _review(_candidate("W4", "call"))
    assert w4_call["q2"]["disposition"] == "reference_compensation_insufficient"
    assert "放弃这份冻结账" in w4_call["q2"]["summary_cn"]
    assert "c > 0.00020000 BTC" in w4_call["q2"]["observable_changes_cn"][0]

    w5_call, _ = _review(_candidate("W5", "call"))
    assert w5_call["q2"]["disposition"] == "defer_reassess"
    assert w5_call["q2"]["strongest_counter"]["id"] == "counter.structure.opening_blocked"
    assert "结构门" in w5_call["q2"]["summary_cn"]
    assert "原生结构门恢复" in w5_call["q2"]["observable_changes_cn"][0]

    missing_mu, _ = _review(_candidate("MISSING_MU", "put"), applicability="UNABLE_TO_JUDGE")
    assert missing_mu["q2"]["disposition"] == "information_gap"
    assert "资料不足" in missing_mu["q2"]["summary_cn"]
    assert "gap.compensation.mu_reference_missing" in missing_mu["selected_gap_ids"]
    assert "μ" in missing_mu["q2"]["observable_changes_cn"][0]


def test_qualified_and_unqualified_map_observables_are_distinct():
    qualified_map, qualified_projection = _review(_candidate("W1", "put"))
    assert qualified_projection["decision_relations"]["map_policy"]["qualified"] is True
    assert any("触达、进入或离开" in item for item in qualified_map["q2"]["observable_changes_cn"])

    no_map_snapshot = core.evaluate(sample())
    no_map, no_map_projection = _review(no_map_snapshot)
    assert no_map_projection["decision_relations"]["map_policy"]["qualified"] is False
    assert any("没有同版合格MAP响应可用" in item for item in no_map["q2"]["observable_changes_cn"])
    assert "MAP责任区未知" in no_map["q2"]["waiting_cost_cn"]


def test_round3_derived_action_text_is_protected_by_existing_replay(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(current_payload())["snapshot"]
    wire, projection = _wire(snapshot, research_only=False)
    normalized = review.validate_wire_response(snapshot, wire, review_input=projection)
    attached = {**normalized, "http_calls": 1}

    ledger.attach_review(attached, snapshot_id=snapshot["snapshot_id"])

    tampered_summary = copy.deepcopy(attached)
    tampered_summary["q2"]["summary_cn"] = "tampered"
    with pytest.raises(ValueError, match="normalized fields"):
        ledger.attach_review(tampered_summary, snapshot_id=snapshot["snapshot_id"])

    tampered_changes = copy.deepcopy(attached)
    tampered_changes["q2"]["observable_changes_cn"] = ["tampered"]
    with pytest.raises(ValueError, match="normalized fields"):
        ledger.attach_review(tampered_changes, snapshot_id=snapshot["snapshot_id"])


def test_old_round2_wire_protocol_is_not_promoted_to_current_review():
    snapshot = _candidate("W1", "put")
    wire, projection = _wire(snapshot)
    wire["response_protocol"] = "bounded_claim_ids@2.4.6"

    with pytest.raises(review.ReviewValidationError, match="response protocol is not current"):
        review.validate_wire_response(snapshot, wire, review_input=projection)
