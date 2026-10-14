"""review_input@2.7.0: the review chain no longer receives inputs without a producer.

Since 2026-10-12 the workbench has no risk_provider, so mu_ref/M_ref are always
null; since 2026-10-13 the space structure is evaluation-only. 2.7.0 leaves
those dead inputs out of the review input and the provider request, while a
qualified (future) risk reference still flows and 2.6.9 replays byte-identically.
"""
import copy
import json

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_v20 as core
from tools import astra_workbench_preview_v20 as preview
from tools import astra_wpem_product_review_v1 as product
from tools import astra_wpem_review_evidence_v1 as evidence_core
from tools import signal_llm_review as llm
from test_astra_underwriting_v20 import sample


MODEL = "local-test-model"
MU_KEYS = {
    "mu_ref_btc", "M_ref_btc", "m_ref_btc", "mu_btc", "reference_margin_btc",
    "minimum_reference_credit_btc", "required_credit_btc", "error_room_btc",
    "increment_needed_btc", "reprice_condition_cn", "overall_mu_status",
    "production_mu_ref_btc", "production_M_ref_btc", "source_shadow_mu_ref_btc",
    "source_shadow_M_ref_btc", "mu_ref_btc_delta", "M_ref_btc_delta", "risk_coverage",
}
DEAD_CATALOG_IDS = {
    "gap.compensation.mu_reference_missing",
    "claim.risk.overall_mu_reference",
    "gap.risk_reference_missing",
    "reassess.risk_reference_available",
    "counter.risk.coverage_unknown",
    "reassess.risk.coverage_evidence_required",
    "counter.risk.horizon_mismatch",
    "reassess.risk_horizon_matched",
    "counter.compensation.nonpositive_margin",
    "reassess.compensation_reprice",
    "counter.structure.opening_blocked",
    "reassess.structure_new_snapshot",
    "gap.account_margin_missing",
}
# Pinned from the untouched 2.6.9 builder (commit a71334e) for the sample
# fixtures below: (projection_hash, provider request hash).
PINNED_269 = {
    ("put", False): ("d87eaa1ba10ce914c7dfae9f721076266e14160002c4c038091b82a792e196fd",
                     "97822e0000c92f03e118a46338adcdf346921ffa2f90b4a3e9326bd46b3bb729"),
    ("put", True): ("8d787bd2aae2d01ca5dcd5dfb5d3eb237ae5a3caf8a5dc08a851f884d9ea6745",
                    "980f1f7cd7c7e883faec3d30eae6e4d0312d784d307fc571345c171df410664c"),
    ("call", False): ("ad471f95b34f896bb306d0412b68c7c500b4a406f167e1515556eb5f595f48a0",
                      "d001002d62895f8e665e243add047494c1cf1e63bbb6fffc62ae612d6288f498"),
    ("call", True): ("200499f71975c3bccf19392237c49586b1dcb4971a687d0e9c5acbc7f40c7518",
                     "b6ab12382dbba31ea61041186d61698864bd71289dcd6b4c227c7f0e2e75393f"),
}


def _fixture(side="put", *, risk=False):
    snapshot = core.evaluate(sample(side, risk=risk))
    return snapshot, evidence_core.build_review_evidence(snapshot, include_routing=True)


def _preview_fixture(scenario_id="W5", *, mu=False):
    saved = copy.deepcopy(preview.SCENARIOS[scenario_id])
    if not mu:
        preview.SCENARIOS[scenario_id]["mu_btc"] = None
    try:
        snapshot = preview.build_preview(1_790_478_000_000, scenario_id=scenario_id)["candidates"][0]["snapshot"]
    finally:
        preview.SCENARIOS[scenario_id] = saved
    return snapshot, evidence_core.build_review_evidence(snapshot, include_routing=True)


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _catalog_ids(review_input):
    return {item["id"] for role in review_input["claim_catalog"].values() for item in role}


def _by_ref(review_input):
    return {item["ref"]: item for item in review_input["evidence_input"]["evidence"]}


def _payload():
    return {
        "response_protocol": product.EVIDENCE_POINTS_RESPONSE_PROTOCOL,
        "q1": [{"text_cn": "本单不利方向由合同责任决定。", "evidence_refs": ["contract"]}],
        "q2": [{"text_cn": "冻结报价不是成交回执。", "evidence_refs": ["quote"]}],
    }


def test_version_constants_are_bumped_and_269_kept():
    assert product.ROUTING_REVIEW_INPUT_SCHEMA == "review_input@2.7.0"
    assert product.ROUTING_REVIEW_INPUT_SCHEMA_269 == "review_input@2.6.9"
    assert product.ROUTING_REVIEW_INPUT_SCHEMA_269 in product.ROUTING_REVIEW_INPUT_SCHEMAS
    assert product.EVIDENCE_SYNTHESIS_PROMPT_VERSION == "evidence_synthesis_prompt@2.4-candidate"
    assert product.prompt_version_for_input({"schema": "review_input@2.6.9"}) == "evidence_synthesis_prompt@2.3-candidate"
    assert product.prompt_version_for_input({"schema": "review_input@2.7.0"}) == product.EVIDENCE_SYNTHESIS_PROMPT_VERSION
    for schema in ("review_input@2.6.9", "review_input@2.7.0"):
        assert product.response_protocol_for_input({"schema": schema}) == "evidence_points@2.6.4"
    assert product._routing_catalog_schema_for_input_schema("review_input@2.6.9") == "wpem_product_claim_catalog@1.1.2"
    assert product._routing_catalog_schema_for_input_schema("review_input@2.7.0") == "wpem_product_claim_catalog@1.2.0"


@pytest.mark.parametrize("side", ["put", "call"])
def test_270_without_risk_producer_has_no_mu_keys_text_or_catalog_items(side):
    snapshot, pkg = _fixture(side, risk=False)
    assert review.risk_reference_absent(snapshot)
    current = product.build_review_input(snapshot, pkg, research_only=True, model=MODEL)

    assert current["schema"] == "review_input@2.7.0"
    assert current["claim_catalog_schema"] == "wpem_product_claim_catalog@1.2.0"
    assert not MU_KEYS & set(_keys(current))
    text = json.dumps(current, ensure_ascii=False)
    for needle in ("μ", "mu_ref", "M_ref", "account_margin", "opening_blocked", "借款缺口"):
        assert needle not in text
    assert "risk" not in current["source_facts"]
    assert "risk" not in _by_ref(current)
    assert current["deterministic_financials"]["schema"] == review.PRUNED_DETERMINISTIC_FINANCIALS_SCHEMA
    assert current["decision_relations"]["schema"] == review.PRUNED_DECISION_RELATIONS_SCHEMA
    assert not DEAD_CATALOG_IDS & _catalog_ids(current)
    level = _by_ref(current)["wpem.level"]["facts"]
    assert "mu_ref_btc" not in level and "M_ref_btc" not in level


def test_270_hard_boundaries_and_guardrails_are_not_forced_by_missing_mu():
    snapshot, pkg = _fixture("put", risk=False)
    current = product.build_review_input(snapshot, pkg, research_only=True, model=MODEL)
    guardrail = product.required_selection_payload(current)
    assert guardrail["applicability"] == "EXPLAINED"
    assert "gap.compensation.mu_reference_missing" not in guardrail["selected_gap_ids"]

    normalized = review.validate_wire_response(snapshot, _payload(), review_input=current, wpem_evidence=pkg)
    q2 = normalized["q2"]
    assert normalized["applicability"] == "EXPLAINED"
    assert q2["disposition"] == "continue_evaluation"
    q2_text = json.dumps({key: q2[key] for key in (
        "summary_cn", "exchange_reason_cn", "coverage_cn", "waiting_cost_cn", "observable_changes_cn")},
        ensure_ascii=False)
    assert "μ" not in q2_text and "风险参考" not in q2_text
    assert "μ" not in normalized["q1"]["summary_cn"]

    old = product._rebuilt_review_input_for_schema(
        snapshot, pkg, research_only=True, model=MODEL, schema="review_input@2.6.9")
    assert product.required_selection_payload(old)["applicability"] == "CONTESTED"
    assert review.validate_wire_response(
        snapshot, _payload(), review_input=old, wpem_evidence=pkg)["q2"]["disposition"] == "information_gap"


def test_270_space_structure_is_evaluation_only():
    snapshot, pkg = _preview_fixture("W5", mu=False)
    current = product.build_review_input(snapshot, pkg, research_only=True, model=MODEL)
    gate = _by_ref(current)["space.gate"]
    assert "evaluation_only@2026-10-13" in gate["basis"]
    assert gate["label_cn"] == product.SPACE_STRUCTURE_EVALUATION_LABEL_CN
    assert not {"gate_action", "veto"} & set(_keys(current))
    old = product._rebuilt_review_input_for_schema(
        snapshot, pkg, research_only=True, model=MODEL, schema="review_input@2.6.9")
    assert {"gate_action", "veto"} & set(_keys(old))
    assert "basis" not in _by_ref(old)["space.gate"]


def test_270_drops_structure_counter_even_if_the_old_rule_would_fire(monkeypatch):
    monkeypatch.setattr(review, "_opening_blocked", lambda snapshot: True)
    snapshot, pkg = _fixture("put", risk=False)
    current = product.build_review_input(snapshot, pkg, research_only=True, model=MODEL)
    assert "counter.structure.opening_blocked" not in _catalog_ids(current)
    assert "counter.structure.opening_blocked" not in product.required_selection_payload(current)["selected_counter_ids"]
    normalized = review.validate_wire_response(snapshot, _payload(), review_input=current, wpem_evidence=pkg)
    assert normalized["q2"]["disposition"] == "continue_evaluation"
    old = product._rebuilt_review_input_for_schema(
        snapshot, pkg, research_only=True, model=MODEL, schema="review_input@2.6.9")
    assert "counter.structure.opening_blocked" in _catalog_ids(old)


def test_270_risk_reference_routing_unit_takes_no_gap_slot():
    snapshot, pkg = _fixture("put", risk=False)
    current = product.build_review_input(snapshot, pkg, research_only=True, model=MODEL)
    old = product._rebuilt_review_input_for_schema(
        snapshot, pkg, research_only=True, model=MODEL, schema="review_input@2.6.9")
    risk_ids = {unit["evidence_id"] for unit in pkg["evidence_routing"]["units"]
                if unit.get("dependency_group") == "RISK_REFERENCE"}
    assert risk_ids

    def gap_evidence(review_input):
        return {evidence_id for item in review_input["evidence_routing"]["selection_summary"]["gaps"]
                for evidence_id in item.get("evidence_ids") or []}

    assert risk_ids & gap_evidence(old)
    assert not risk_ids & gap_evidence(current)
    # The sealed routing identity is unchanged; only the review-side view differs.
    assert current["routing_identity"] == old["routing_identity"]


@pytest.mark.parametrize("side", ["put", "call"])
def test_270_with_qualified_mu_keeps_mu_items_and_fail_closed_checks(side):
    snapshot, pkg = _fixture(side, risk=True)
    assert not review.risk_reference_absent(snapshot)
    current = product.build_review_input(snapshot, pkg, research_only=True, model=MODEL)
    assert current["schema"] == "review_input@2.7.0"
    assert "risk" in current["source_facts"]
    assert "risk" in _by_ref(current)
    assert current["deterministic_financials"]["account_basis"]["mu_ref_btc"] is not None
    assert "risk_coverage" in current["decision_relations"]
    assert {"claim.risk.overall_mu_reference", "counter.risk.coverage_unknown"} <= _catalog_ids(current)
    request = review.build_request_from_input(snapshot, MODEL, current, research_only=True, wpem_evidence=pkg)
    assert "μ/M" in request["messages"][0]["content"]
    assert '"evidence_refs":["quote","fee","risk"]' in request["messages"][1]["content"]


def test_270_nonpositive_mu_margin_still_blocks_explained():
    snapshot, pkg = _preview_fixture("W3", mu=True)
    current = product.build_review_input(snapshot, pkg, research_only=True, model=MODEL)
    assert "counter.compensation.nonpositive_margin" in _catalog_ids(current)
    normalized = review.validate_wire_response(snapshot, _payload(), review_input=current, wpem_evidence=pkg)
    assert normalized["applicability"] == "CONTESTED"
    assert normalized["q2"]["disposition"] == "reference_compensation_insufficient"


@pytest.mark.parametrize("side,risk", sorted(PINNED_269))
def test_269_replay_is_byte_identical_and_still_validates(side, risk):
    snapshot, pkg = _fixture(side, risk=risk)
    rebuilt = product._rebuilt_review_input_for_schema(
        snapshot, pkg, research_only=True, model=MODEL, schema="review_input@2.6.9")
    request = review.build_request_from_input(snapshot, MODEL, rebuilt, research_only=True, wpem_evidence=pkg)
    assert rebuilt["schema"] == "review_input@2.6.9"
    assert request["_local_prompt_version"] == "evidence_synthesis_prompt@2.3-candidate"
    assert (rebuilt["projection_hash"], request["_local_request_hash"]) == PINNED_269[(side, risk)]
    normalized = review.validate_wire_response(snapshot, _payload(), review_input=rebuilt, wpem_evidence=pkg)
    assert normalized["review_validity"]["review_input_version"] == "review_input@2.6.9"
    assert normalized["review_validity"]["projection_hash"] == rebuilt["projection_hash"]


def test_270_provider_request_has_no_mu_text_and_is_smaller():
    snapshot, pkg = _fixture("put", risk=False)
    request = review.build_request(snapshot, MODEL, wpem_evidence=pkg, research_only=True)
    assert request["_local_review_input"]["schema"] == "review_input@2.7.0"
    assert request["_local_prompt_version"] == "evidence_synthesis_prompt@2.4-candidate"
    wire = llm._strip_local_request_fields(request)
    system, user = (message["content"] for message in wire["messages"])
    for text in (system, user):
        for needle in ("μ", "mu_ref", "M_ref", "mu_btc"):
            assert needle not in text
    prompt_only = user.split("\n本次证据：", 1)[0]
    assert "风险参考" not in system + prompt_only
    assert json.loads(user.split("\n本次证据：", 1)[1]) == request["_local_review_input"]["evidence_input"]
    assert wire["thinking"] == {"type": "enabled"} and wire["reasoning_effort"] == "max"

    old_input = product._rebuilt_review_input_for_schema(
        snapshot, pkg, research_only=True, model=MODEL, schema="review_input@2.6.9")
    old_wire = llm._strip_local_request_fields(
        review.build_request_from_input(snapshot, MODEL, old_input, research_only=True, wpem_evidence=pkg))
    size = lambda value: len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    assert size(wire) < size(old_wire)
    assert "μ" in json.dumps(old_wire, ensure_ascii=False)


def test_270_store_replay_uses_recorded_schema():
    snapshot, pkg = _fixture("put", risk=False)
    current = product.build_review_input(snapshot, pkg, research_only=False, model=MODEL)
    rebuilt = product._rebuilt_review_input_for_schema(
        snapshot, pkg, research_only=False, model=MODEL, schema=current["schema"])
    assert rebuilt == current
    tampered = dict(current)
    tampered["schema"] = "review_input@2.6.9"
    with pytest.raises(review.ReviewValidationError):
        product.validate_wire_response(snapshot, _payload(), review_input=tampered, evidence=pkg)
