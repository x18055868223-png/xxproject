import inspect
import json
from copy import deepcopy

import pytest

from tools import astra_underwriting_review_v20 as legacy
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_v20 as core
from tools import astra_wpem_product_review_v1 as product
from tools import astra_wpem_review_evidence_v1 as evidence
from tools import signal_llm_review as llm_core
from test_astra_bounded_claims_v244 import Budget
from test_astra_underwriting_v20 import sample


def _build_legacy_review_evidence(snapshot):
    if "include_routing" in inspect.signature(evidence.build_review_evidence).parameters:
        return evidence.build_review_evidence(snapshot, include_routing=False)
    return evidence.build_review_evidence(snapshot)


def fixture():
    snapshot = core.evaluate(sample("put"))
    package = _build_legacy_review_evidence(snapshot)
    return snapshot, package


def _response(content):
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


def _old_251_input(current):
    old = deepcopy(current)
    old.pop("request_contract", None)
    old["schema"] = "review_input@2.5.1"
    old["projection_hash"] = legacy._hash({key: value for key, value in old.items() if key != "projection_hash"})
    return old


def test_wpem_request_contract_is_explicit_and_non_wpem_request_stays_legacy_shaped():
    snapshot, package = fixture()

    request = legacy.build_request(snapshot, "local-test-model", wpem_evidence=package)
    review_input = request["_local_review_input"]
    system = request["messages"][0]["content"]
    user = request["messages"][1]["content"]
    before_frozen, frozen_json = user.rsplit("\n冻结候选：", 1)
    frozen = json.loads(frozen_json)

    assert request["thinking"] == {"type": "disabled"}
    assert request["_local_prompt_version"] == product.PROMPT_VERSION
    assert request["_local_provider_config_version"] == product.PROVIDER_CONFIG_VERSION
    assert product.PROMPT_VERSION in system
    assert "严格包含用户指定的九个顶层字段" in system
    assert "不要输出 Markdown" in system
    assert "type 字段" in system
    assert "counter_catalog_gap_reassess" in system
    assert "唯一可选 ID 白名单" in before_frozen
    assert "JSON 输出形状示例" in before_frozen
    assert "response_format" not in product.required_selection_payload(review_input)
    assert frozen["request_contract"] == review_input["request_contract"]
    assert frozen["request_contract"]["selection_authority"] == "claim_catalog"
    assert frozen["request_contract"]["nested_catalogs_selectable"] is False
    assert frozen["request_contract"]["exact_output_keys"] == product.EXACT_OUTPUT_KEYS

    legacy_request = legacy.build_request(snapshot, "local-test-model")
    assert legacy_request["thinking"] == {"type": "enabled"}
    assert "_local_prompt_version" not in legacy_request
    assert "_local_provider_config_version" not in legacy_request
    assert "唯一可选 ID 白名单" not in legacy_request["messages"][1]["content"]


def test_old_bad_provider_shapes_and_nested_foreign_ids_remain_rejected():
    snapshot, package = fixture()
    review_input = product.build_review_input(snapshot, package)
    answer = product.required_selection_payload(review_input)

    with_type = {**deepcopy(answer), "type": "json_object"}
    with pytest.raises(legacy.ReviewValidationError, match="frozen schema"):
        legacy.validate_wire_response(snapshot, with_type, review_input=review_input, wpem_evidence=package)

    two_objects = json.dumps({"type": "json_object"}) + json.dumps(answer, ensure_ascii=False)
    with pytest.raises(json.JSONDecodeError, match="Extra data"):
        llm_core.parse_chat_response(_response(two_objects))

    # any inner WPEM catalog id that the outer bounded catalog does not offer as a counter
    foreign_id = package["counter_catalog_gap_reassess"]["gaps"][0]["id"]
    outer_counters = {row["id"] for row in review_input["claim_catalog"]["counterclaims"]}
    assert foreign_id not in outer_counters
    foreign = deepcopy(answer)
    foreign["selected_counter_ids"] = [*foreign["selected_counter_ids"], foreign_id]
    with pytest.raises(legacy.ReviewValidationError, match="bounded catalog"):
        legacy.validate_wire_response(snapshot, foreign, review_input=review_input, wpem_evidence=package)


def test_legacy_251_active_state_hash_mismatch_fails_closed_without_http(tmp_path):
    snapshot, package = fixture()
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    ledger.record_snapshot(snapshot)
    current = product.build_review_input(snapshot, package, research_only=True, model="local-test-model")
    old = _old_251_input(current)
    state_path = legacy._review_state_path(
        tmp_path / "review" / "research_only",
        snapshot["candidate_id"],
        snapshot["snapshot_id"],
    )
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "schema": "astra_underwriting_review_attempts@2.5.0",
        "candidate_id": snapshot["candidate_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "packet_hash": current["packet_hash"],
        "input_hash": old["projection_hash"],
        "mode": "research_only",
        "attempts": [],
        "settled": False,
    }), encoding="utf-8")
    calls = []
    budget = Budget()

    with pytest.raises(ValueError, match="input contract mismatch"):
        legacy.run_review(
            snapshot,
            ledger,
            tmp_path / "review",
            api_key="fake-test-only",
            model="local-test-model",
            budget=budget,
            transport=lambda *args, **kwargs: calls.append(args),
            research_only=True,
            wpem_evidence=package,
        )

    assert calls == []
    assert budget.reservations == []


def test_mock_wpem_run_review_uses_required_main_ids_and_returns_research_only_no_trading(tmp_path):
    snapshot, package = fixture()
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    ledger.record_snapshot(snapshot)
    calls = []

    def transport(key, model, request, timeout, endpoint=None):
        calls.append(request)
        assert request["thinking"] == {"type": "disabled"}
        assert request["_local_prompt_version"] == product.PROMPT_VERSION
        assert request["_local_provider_config_version"] == product.PROVIDER_CONFIG_VERSION
        assert "reasoning_effort" not in request
        review_input = request["_local_review_input"]
        payload = product.required_selection_payload(review_input)
        assert set(payload) == set(product.EXACT_OUTPUT_KEYS)
        for required_key, selected_key in (
            ("claim_ids", "selected_claim_ids"),
            ("counter_ids", "selected_counter_ids"),
            ("gap_ids", "selected_gap_ids"),
            ("reassess_condition_ids", "selected_reassess_condition_ids"),
        ):
            required = review_input["wpem_selection_requirements"][required_key]
            assert set(required).issubset(payload[selected_key])
            assert len(payload[selected_key]) <= legacy.MAX_SELECTED_IDS
        return {"model": model, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}

    result = legacy.run_review(
        snapshot,
        ledger,
        tmp_path / "review",
        api_key="fake-test-only",
        model="local-test-model",
        budget=Budget(),
        transport=transport,
        research_only=True,
        wpem_evidence=package,
    )

    assert result["status"] == "RESEARCH_OK"
    assert result["schema"] == "research_review@2.5.0"
    assert result["production_attach_allowed"] is False
    assert result["input_scope"]["execution_allowed"] is False
    assert result["response_protocol"] == product.RESPONSE_PROTOCOL
    assert result["review_validity"]["review_input_version"] == "review_input@2.5.2"
    assert result["http_calls"] == 1
    assert len(calls) == 1
