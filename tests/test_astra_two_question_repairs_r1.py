import copy
import json

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_v20 as core
from test_astra_underwriting_review_v20 import current_payload, good_answer
from test_astra_underwriting_v20 import sample


def _ids(catalog, role):
    return {item["id"] for item in catalog[role]}


def _pick(catalog, role, item_id):
    assert item_id in _ids(catalog, role)
    return item_id


def _first(catalog, role, prefix):
    for item in catalog[role]:
        if item["id"].startswith(prefix):
            return item["id"]
    raise AssertionError(prefix)


def _scenario(credit_btc, fee_btc, mu_btc):
    payload = sample()
    quantity = payload["candidate"]["quantity_btc"]
    long_ask = 0.003
    short_bid = long_ask + credit_btc / quantity
    payload["market"]["long_book"]["asks"] = [[long_ask, quantity]]
    payload["market"]["short_book"]["bids"] = [[short_bid, quantity]]
    payload["market"]["short_book"]["asks"] = [[short_bid + 0.001, quantity]]
    payload["fee"]["amount_btc"] = fee_btc
    payload["risk"]["mu_btc"] = mu_btc
    return core.evaluate(payload)


def _wire(snapshot, *, applicability="CONTESTED", research_only=False):
    projection = review.build_review_input(snapshot, research_only=research_only)
    catalog = projection["claim_catalog"]
    required = projection["decision_relations"]["selection_requirements"]
    counter_ids = [_pick(catalog, "counterclaims", "counter.q1.competing_context_explanation")]
    if "counter.compensation.nonpositive_margin" in _ids(catalog, "counterclaims"):
        counter_ids.insert(0, "counter.compensation.nonpositive_margin")
    gap_ids = []
    if "gap.compensation.mu_reference_missing" in _ids(catalog, "gaps"):
        gap_ids.append("gap.compensation.mu_reference_missing")
    elif catalog["gaps"]:
        gap_ids.append(catalog["gaps"][0]["id"])
    reassess_ids = [_pick(catalog, "reassess_conditions", "reassess.snapshot_rebuilt")]
    if "reassess.compensation_reprice" in _ids(catalog, "reassess_conditions"):
        reassess_ids.insert(0, "reassess.compensation_reprice")
    return {
        "response_protocol": review.RESPONSE_PROTOCOL,
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "projection_hash": projection["projection_hash"],
        "claim_catalog_hash": projection["claim_catalog_hash"],
        "applicability": applicability,
        "selected_claim_ids": [
            _first(catalog, "claims", "claim.contract."),
            *required["claim_ids"],
        ],
        "selected_counter_ids": list(dict.fromkeys([*counter_ids, *required["counter_ids"]])),
        "selected_gap_ids": gap_ids,
        "selected_reassess_condition_ids": list(dict.fromkeys([*reassess_ids, *required["reassess_condition_ids"]])),
    }


@pytest.mark.parametrize(
    "credit_btc,fee_btc,mu_btc,margin_btc,disposition",
    [
        (0.00023, 0.00002, 0.00018, 0.00003, "continue_evaluation"),
        (0.00050, 0.00002, 0.00034, 0.00014, "continue_evaluation"),
        (0.00050, 0.00002, 0.00062, -0.00014, "reference_compensation_insufficient"),
        (0.00015, 0.00002, 0.00018, -0.00005, "reference_compensation_insufficient"),
    ],
)
def test_w_scenarios_generate_program_q1_q2_without_free_text_finance(
    credit_btc, fee_btc, mu_btc, margin_btc, disposition
):
    snapshot = _scenario(credit_btc, fee_btc, mu_btc)

    result = review.validate_wire_response(snapshot, _wire(snapshot))

    assert snapshot["economics"]["reference_margin_btc"] == pytest.approx(margin_btc)
    assert result["q1"]["title_cn"] == "同一保单适配"
    assert result["selected_claim_ids"][0].startswith("claim.contract.")
    assert "不利方向" in result["q1"]["summary_cn"]
    assert result["q2"]["title_cn"] == "下一步取舍"
    assert result["q2"]["disposition"] == disposition
    assert "可能" in result["q2"]["waiting_cost_cn"]
    assert "未来价差与" in result["q2"]["waiting_cost_cn"]
    assert "跌破保护腿" in result["q2"]["liability_cn"]
    assert "无固定BTC上限" in result["q2"]["liability_cn"]
    assert "风险逐项覆盖未知" in result["q2"]["coverage_cn"]
    assert result["q2"]["exchange_reason_cn"] != result["q2"]["summary_cn"]
    if disposition == "continue_evaluation":
        assert "覆盖同一μ参考" in result["q2"]["exchange_reason_cn"]
    else:
        assert "未覆盖同一μ参考" in result["q2"]["exchange_reason_cn"]
    assert result["q2"]["strongest_counter"] is not None
    assert result["deterministic_financials"]["account_basis"]["reference_margin_btc"] == pytest.approx(margin_btc)


def test_research_scope_does_not_override_q2_economic_disposition():
    cases = [
        (_scenario(0.00023, 0.00002, 0.00018), "continue_evaluation"),
        (_scenario(0.00050, 0.00002, 0.00034), "continue_evaluation"),
        (_scenario(0.00050, 0.00002, 0.00062), "reference_compensation_insufficient"),
        (_scenario(0.00015, 0.00002, 0.00018), "reference_compensation_insufficient"),
        (core.evaluate(current_payload()), "information_gap"),
    ]
    stale_payload = sample()
    stale_payload["market"]["short_book"]["timestamp"] -= 6_000
    cases.append((core.evaluate(stale_payload), "defer_reassess"))

    for snapshot, disposition in cases:
        result = review.validate_wire_response(
            snapshot,
            _wire(snapshot, applicability="UNABLE_TO_JUDGE" if disposition in {"information_gap", "defer_reassess"} else "CONTESTED",
                  research_only=True),
            review_input=review.build_review_input(snapshot, research_only=True),
        )
        assert result["q2"]["disposition"] == disposition
        assert result["q2"]["scope_cn"] == "本地研究复核，禁止生产附着。"


def test_call_q2_coverage_describes_btc_peak_shape():
    snapshot = core.evaluate(sample("call"))

    result = review.validate_wire_response(snapshot, _wire(snapshot))

    assert "涨破保护腿" in result["q2"]["liability_cn"]
    assert "峰值后" in result["q2"]["liability_cn"]
    assert "风险逐项覆盖未知" in result["q2"]["coverage_cn"]


def test_nonpositive_margin_requires_bounded_counter_and_reprice():
    snapshot = _scenario(0.00050, 0.00002, 0.00062)

    bad = _wire(snapshot)
    bad["selected_counter_ids"] = ["counter.q1.competing_context_explanation"]
    with pytest.raises(review.ReviewValidationError, match="nonpositive reference margin"):
        review.validate_wire_response(snapshot, bad)

    bad = _wire(snapshot)
    bad["selected_reassess_condition_ids"] = ["reassess.snapshot_rebuilt"]
    with pytest.raises(review.ReviewValidationError, match="repricing"):
        review.validate_wire_response(snapshot, bad)

    bad = _wire(snapshot, applicability="EXPLAINED")
    with pytest.raises(review.ReviewValidationError, match="cautious"):
        review.validate_wire_response(snapshot, bad)


def test_missing_mu_requires_explicit_gap_and_stays_information_gap():
    snapshot = core.evaluate(current_payload())

    bad = _wire(snapshot, applicability="UNABLE_TO_JUDGE")
    bad["selected_gap_ids"] = ["gap.risk_reference_missing"]
    with pytest.raises(review.ReviewValidationError, match="missing mu"):
        review.validate_wire_response(snapshot, bad)

    result = review.validate_wire_response(snapshot, _wire(snapshot, applicability="UNABLE_TO_JUDGE"))
    assert result["q2"]["disposition"] == "information_gap"
    assert "gap.compensation.mu_reference_missing" in result["selected_gap_ids"]


def test_synthetic_risk_is_research_identity_not_real_support():
    projection = review.build_review_input(core.evaluate(sample()), research_only=True)
    claim_ids = _ids(projection["claim_catalog"], "claims")
    counter_ids = _ids(projection["claim_catalog"], "counterclaims")

    assert "claim.risk.synthetic_reference_for_research" in claim_ids
    assert "claim.risk.supported_reference" not in claim_ids
    assert "counter.risk.synthetic_not_production_support" in counter_ids


def test_map_background_catalog_uses_concrete_qualified_refs():
    from test_astra_review_closure_v243 import current_payload as closure_payload
    from test_astra_review_closure_v243 import with_closure_map

    snapshot = with_closure_map(core.evaluate(closure_payload(risk=False)))
    catalog = review.build_review_input(snapshot, research_only=True)["claim_catalog"]
    items = [
        item
        for role in ("claims", "counterclaims")
        for item in catalog[role]
        if ".background." in item["id"]
    ]

    assert items
    assert all(item["ref"].startswith("map.background.") for item in items)
    assert all(item["ref"] != "map.background" for item in items)


def test_research_review_receipt_carries_q1_q2_without_attaching_to_store(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(sample())["snapshot"]

    class Budget:
        def reserve(self, count, **kwargs):
            return {"reservation_id": "one"}

        def complete(self, reservation_id, status_category, usage):
            pass

    def transport(api_key, model, request, timeout, endpoint=None):
        answer = _wire(snapshot, applicability="CONTESTED", research_only=True)
        projection = request["_local_review_input"]
        answer["projection_hash"] = projection["projection_hash"]
        answer["claim_catalog_hash"] = projection["claim_catalog_hash"]
        return {"model": "fixture", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}

    result = review.run_review(
        snapshot,
        ledger,
        tmp_path / "states",
        api_key="fake",
        model="fake",
        budget=Budget(),
        transport=transport,
        research_only=True,
    )

    assert result["status"] == "RESEARCH_OK"
    assert result["supports"]
    assert result["q2"]["disposition"] == "continue_evaluation"
    assert result["q2"]["scope_cn"] == "本地研究复核，禁止生产附着。"
    assert ledger.state()["snapshots"][snapshot["snapshot_id"]]["review"] is None


def test_store_replays_q1_q2_and_rejects_tampering(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(current_payload())["snapshot"]
    attached = {**review.validate_wire_response(snapshot, good_answer(snapshot)), "http_calls": 1}
    ledger.attach_review(attached, snapshot_id=snapshot["snapshot_id"])

    tampered = copy.deepcopy(attached)
    tampered["q1"]["summary_cn"] = "被篡改的适配结论。"
    with pytest.raises(ValueError, match="normalized fields"):
        ledger.attach_review(tampered, snapshot_id=snapshot["snapshot_id"])
