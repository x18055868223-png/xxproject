"""Regression cases across frozen input, bounded review and the preview receipt."""
from copy import deepcopy

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_workbench_preview_v20 as preview


NOW_MS = 1790478000000


def test_synthetic_option_references_use_the_policy_expiry():
    payload = preview.build_preview(NOW_MS, scenario_id="W1")
    for candidate in payload["candidates"]:
        expiry = candidate["snapshot"]["contract"]["expiry_ms"]
        refs = candidate["btc_map"]["reference_pool"]
        assert all(item["expiry_scope"] == expiry for item in refs
                   if item["family"] == "OPTION_STRUCTURE")


@pytest.mark.parametrize("side_index,adverse", [(0, "下跌"), (1, "上涨")])
def test_rising_macro_levels_do_not_become_btc_price_direction(side_index, adverse):
    snapshot = preview.build_preview(NOW_MS, scenario_id="W1")["candidates"][side_index]["snapshot"]
    wire, _ = preview._wire_response(snapshot, "W1")
    projection = review.build_review_input(snapshot, research_only=True)
    external = next(item for item in projection["claim_catalog"]["claims"]
                    if item.get("ref") == "map.background.external_conditions"
                    and item["id"].startswith("claim.q1.background."))
    wire = deepcopy(wire)
    wire["selected_claim_ids"] = [
        *projection["decision_relations"]["selection_requirements"]["claim_ids"], external["id"]]

    result = review.validate_wire_response(snapshot, wire, review_input=projection)

    assert "美元广义指数和实际收益率上升" in result["q1"]["summary_cn"]
    assert "未给出可直接采用的BTC价格方向" in result["q1"]["summary_cn"]
    assert f"本单{adverse}不利方向" in result["q1"]["summary_cn"]
    assert "源端给出的BTC价格方向" not in result["q1"]["summary_cn"]


def test_closed_structure_gate_outranks_positive_reference_compensation():
    payload = preview.build_preview(NOW_MS, scenario_id="W5")
    snapshot = payload["candidates"][0]["snapshot"]
    wire, result = preview._wire_response(snapshot, "W5")

    assert snapshot["economics"]["reference_margin_btc"] > 0
    assert result["q2"]["disposition"] == "defer_reassess"
    assert result["q2"]["strongest_counter"]["id"] == "counter.structure.opening_blocked"
    assert snapshot["execution_allowed"] is False

    wire = deepcopy(wire)
    wire["selected_counter_ids"].remove("counter.structure.opening_blocked")
    with pytest.raises(review.ReviewValidationError, match="structure"):
        review.validate_wire_response(snapshot, wire,
                                      review_input=review.build_review_input(snapshot, research_only=True))
