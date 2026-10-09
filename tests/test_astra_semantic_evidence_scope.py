import json
from pathlib import Path

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_wpem_product_review_v1 as product


REPO_ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY_SAMPLE_ROOT = REPO_ROOT / ".artifacts" / "topology_realignment_20261006_01" / "samples"


def _load_sample(name: str):
    root = TOPOLOGY_SAMPLE_ROOT / name
    if not root.exists():
        pytest.skip(f"{name} topology sample missing")
    return (
        json.loads((root / "snapshot.json").read_text(encoding="utf-8")),
        json.loads((root / "evidence.json").read_text(encoding="utf-8")),
        json.loads((root / "review_input.json").read_text(encoding="utf-8")),
    )


def _by_ref(review_input):
    return {
        item["ref"]: item
        for item in (review_input.get("evidence_input") or {}).get("evidence") or []
        if isinstance(item, dict) and isinstance(item.get("ref"), str)
    }


def _build_current_observation_266():
    snapshot, evidence, old_input = _load_sample("current_observation")
    current = product.build_review_input(
        snapshot,
        evidence,
        research_only=(old_input.get("research_scope") or {}).get("research_only") is True,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
    )
    return snapshot, evidence, old_input, current


@pytest.mark.parametrize("sample_name", ["semantic_followup", "current_observation"])
def test_265_semantic_samples_rebuild_exactly(sample_name):
    snapshot, evidence, old_input = _load_sample(sample_name)

    rebuilt = product._rebuilt_review_input_for_schema(
        snapshot,
        evidence,
        research_only=(old_input.get("research_scope") or {}).get("research_only") is True,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
        schema=old_input["schema"],
    )

    assert old_input["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_265
    assert old_input["request_contract"]["prompt_version"] == product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_265
    assert rebuilt == old_input


def test_prompt_versions_keep_264_265_and_default_266_separate():
    assert product.prompt_version_for_input({"schema": product.ROUTING_REVIEW_INPUT_SCHEMA_264}) == product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_264
    assert product.prompt_version_for_input({"schema": product.ROUTING_REVIEW_INPUT_SCHEMA_265}) == product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_265
    assert product.prompt_version_for_input({"schema": product.ROUTING_REVIEW_INPUT_SCHEMA_266}) == product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_266
    assert product.prompt_version_for_input({"schema": product.ROUTING_REVIEW_INPUT_SCHEMA_267}) == product.EVIDENCE_SYNTHESIS_PROMPT_VERSION_267
    assert product.prompt_version_for_input({"schema": product.ROUTING_REVIEW_INPUT_SCHEMA}) == product.EVIDENCE_SYNTHESIS_PROMPT_VERSION
    assert product.response_protocol_for_input({"schema": product.ROUTING_REVIEW_INPUT_SCHEMA_265}) == product.EVIDENCE_POINTS_RESPONSE_PROTOCOL
    assert product.response_protocol_for_input({"schema": product.ROUTING_REVIEW_INPUT_SCHEMA}) == product.EVIDENCE_POINTS_RESPONSE_PROTOCOL


def test_266_funding_current_ref_splits_last_rate_without_new_market_state():
    snapshot, _evidence, old_input, current = _build_current_observation_266()
    old_refs = _by_ref(old_input)
    refs = _by_ref(current)

    assert current["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA
    assert current["request_contract"]["prompt_version"] == product.EVIDENCE_SYNTHESIS_PROMPT_VERSION
    assert "funding.current" in refs
    assert "last_rate" not in refs["funding"]["facts"]
    assert refs["funding.current"]["facts"] == {"last_rate": old_refs["funding"]["facts"]["last_rate"]}
    assert refs["funding"]["facts"]["semantics"] == old_refs["funding"]["facts"]["semantics"]
    assert refs["funding"]["facts"]["tmvf_24h"] == old_refs["funding"]["facts"]["tmvf_24h"]
    assert "last_rate" not in refs["funding"]["basis"]

    futures = (((snapshot.get("fact_context") or {}).get("source_quality") or {}).get("source_details") or {}).get("binance", {}).get("futures_facts", {})
    assert futures["last_funding_rate"] == refs["funding.current"]["facts"]["last_rate"]
    assert refs["funding.current"]["asof"]["source_time_ms"] == futures["time"]
    assert refs["funding.current"]["asof"]["next_funding_time_ms"] == futures["next_funding_time"]
    assert refs["funding.current"]["qualification"] == {"source_fields_match": True}
    assert all("crowding" not in key for key in refs["funding.current"]["facts"])


def test_266_map_funding_reuses_existing_settlement_window_and_scope_only():
    _snapshot, _evidence, old_input, current = _build_current_observation_266()
    old_refs = _by_ref(old_input)
    refs = _by_ref(current)

    assert refs["map.funding"]["facts"] == old_refs["map.funding"]["facts"]
    assert refs["map.funding"]["relations"] == old_refs["map.funding"]["relations"]
    assert refs["map.funding"]["asof"] == old_refs["map.funding"]["asof"]
    assert "scope" in refs["map.funding"]
    assert "scope_clock" not in refs["map.funding"]
    assert "families" not in refs["map.funding"]


def test_266_wpem_scope_stays_local_and_does_not_add_runtime_gate():
    _snapshot, _evidence, _old_input, current = _build_current_observation_266()
    refs = _by_ref(current)

    assert "逐点原始变化" in refs["wpem.fragments"]["scope"]
    assert refs["wpem.fragments"]["relations"]["market_trend_claim_allowed"] is False
    assert refs["wpem.delta"]["facts"]["status"] == "NO_STRICT_DELTA"
    assert "当前截面和片段" in refs["wpem.path"]["scope"]
    for ref in ("wpem.fragments", "wpem.delta", "wpem.path"):
        assert "scope_clock" not in refs[ref]
    assert "decision_evidence" not in current
    assert "semantic_gate" not in current
    assert product.required_selection_payload(current)["response_protocol"] == product.EVIDENCE_POINTS_RESPONSE_PROTOCOL


def test_funding_topic_accepts_current_or_window_refs_but_rejects_uncited_funding():
    product._validate_topic_direct_refs("Funding当前last_rate只作为当前口径阅读。", ["funding.current"])
    product._validate_topic_direct_refs("Funding语义窗口不等同当前last_rate。", ["funding"])
    product._validate_topic_direct_refs("实际Funding结算窗口已给出。", ["map.funding"])
    with pytest.raises(review.ReviewValidationError, match="direct evidence ref"):
        product._validate_topic_direct_refs("Funding当前last_rate只作为当前口径阅读。", ["flow"])
