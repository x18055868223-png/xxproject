import inspect
import json
import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_v20 as core
from tools import astra_wpem_product_review_v1 as product
from tools import astra_wpem_review_evidence_v1 as evidence_core
from test_astra_underwriting_review_v20 import current_payload
from test_astra_underwriting_v20 import sample


REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTING_SAMPLE_ROOT = REPO_ROOT / ".artifacts" / "evidence_routing_20261006_01" / "samples"
UNIFIED_SAMPLE_ROOT = REPO_ROOT / ".artifacts" / "unified_evidence_v12_20261006_01" / "samples"
CURRENT_VALIDATED_SAMPLE_ROOT = (
    REPO_ROOT / ".artifacts" / "observation_baseline_20261006_01" / "samples" / "current_validated"
)
BEFORE_NORMALIZED_PATH = REPO_ROOT / ".artifacts" / "data_shortest_path_20261006_01" / "before_normalized.json"
BEFORE_NORMALIZED_SHA256 = "ff916d019def79f5d86ebaf9ba4b40832f70bbe02b9d17dec857273cea8d38b8"


@pytest.fixture(autouse=True)
def preserve_paragraph_protocol_regressions(monkeypatch):
    """These tests retain the 2.6.3 paragraph contract; current points have their own suite."""
    def build(snapshot, evidence, *, research_only=False, model=None):
        return product._rebuilt_review_input_for_schema(
            snapshot, evidence, research_only=research_only, model=model,
            schema=product.ROUTING_REVIEW_INPUT_SCHEMA_263)
    monkeypatch.setattr(product, "build_review_input", build)


class Budget:
    def __init__(self):
        self.reservations = []

    def reserve(self, count, **kwargs):
        self.reservations.append((count, kwargs))
        return {"reservation_id": str(len(self.reservations))}

    def complete(self, reservation_id, status_category, usage):
        pass


def routed_fixture():
    snapshot = core.evaluate(sample("put"))
    assert "include_routing" in inspect.signature(evidence_core.build_review_evidence).parameters
    package = evidence_core.build_review_evidence(snapshot, include_routing=True)
    return snapshot, package


def _load_sample(root: Path, name: str):
    sample_root = root / name
    if not sample_root.exists():
        pytest.skip(f"sample missing: {sample_root}")
    return (
        json.loads((sample_root / "snapshot.json").read_text(encoding="utf-8")),
        json.loads((sample_root / "evidence.json").read_text(encoding="utf-8")),
        json.loads((sample_root / "review_input.json").read_text(encoding="utf-8")),
        json.loads((sample_root / "actual_llm" / "parsed_wire_response.json").read_text(encoding="utf-8")),
    )


def _load_current_validated_sample():
    if not CURRENT_VALIDATED_SAMPLE_ROOT.exists() or not BEFORE_NORMALIZED_PATH.exists():
        pytest.skip("current_validated shortest-path baseline missing")
    return (
        json.loads((CURRENT_VALIDATED_SAMPLE_ROOT / "snapshot.json").read_text(encoding="utf-8")),
        json.loads((CURRENT_VALIDATED_SAMPLE_ROOT / "evidence.json").read_text(encoding="utf-8")),
        json.loads((CURRENT_VALIDATED_SAMPLE_ROOT / "review_input.json").read_text(encoding="utf-8")),
        json.loads((CURRENT_VALIDATED_SAMPLE_ROOT / "actual_llm" / "message_content.txt").read_text(encoding="utf-8")),
        json.loads(BEFORE_NORMALIZED_PATH.read_text(encoding="utf-8")),
    )


def _canon(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _assert_absent_keys(value, absent_keys):
    if isinstance(value, dict):
        for key, item in value.items():
            assert key not in absent_keys
            _assert_absent_keys(item, absent_keys)
    elif isinstance(value, list):
        for item in value:
            _assert_absent_keys(item, absent_keys)


def test_262_grounded_explanation_normalizes_without_changing_financials():
    snapshot, routed = routed_fixture()
    current = product.build_review_input(snapshot, routed, model="local-grounded")
    old = review.build_review_input(snapshot)
    answer = product.required_selection_payload(current)

    assert current["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_263
    assert current["response_protocol"] == product.ROUTING_RESPONSE_PROTOCOL
    assert current["request_contract"]["exact_output_keys"] == product.GROUNDED_EXPLANATION_OUTPUT_KEYS
    assert current["llm_identity"]["protocol"] == product.ROUTING_RESPONSE_PROTOCOL
    assert "explanation_contract" in current
    assert answer["response_protocol"] == product.ROUTING_RESPONSE_PROTOCOL
    assert set(answer["explanation"]) == {"q1", "q2"}

    context = current["source_facts"]["contextual_market_reads"]
    item_refs = [item["ref"] for item in context["items"]]
    assert item_refs
    assert all(ref in current["allowed_refs"] for ref in item_refs)

    normalized = review.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=routed)

    assert normalized["response_protocol"] == product.ROUTING_RESPONSE_PROTOCOL
    assert normalized["deterministic_financials"] == old["deterministic_financials"]
    assert normalized["q1"]["title_cn"] == "同一保单适配"
    assert normalized["q2"]["disposition"]
    assert normalized["llm_explanation"]["q1"]["text_cn"] == answer["explanation"]["q1"]["text_cn"]
    assert normalized["q1"]["llm_explanation"] == normalized["llm_explanation"]["q1"]
    assert normalized["q2"]["llm_explanation"] == normalized["llm_explanation"]["q2"]
    assert normalized["q1"]["llm_explanation"]["evidence"]
    assert normalized["q1"]["llm_explanation"]["evidence"][0]["ref"] in answer["explanation"]["q1"]["evidence_refs"]
    assert normalized["review_validity"]["llm_protocol"] == product.ROUTING_RESPONSE_PROTOCOL


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda payload: payload.pop("explanation"), "grounded explanation"),
        (lambda payload: payload.__setitem__("extra", True), "frozen schema"),
        (lambda payload: payload["explanation"]["q1"].__setitem__("extra", True), "item fields"),
        (lambda payload: payload["explanation"]["q1"].__setitem__("evidence_refs", ["missing.ref"]), "unavailable fact"),
        (lambda payload: payload["explanation"]["q1"].__setitem__("text_cn", "新增 1 BTC 且 μ=0.1"), "unsupported financial"),
        (lambda payload: payload["explanation"]["q1"].__setitem__("text_cn", "当前资料确认正期望并允许下单"), "unsupported financial"),
        (lambda payload: payload["explanation"]["q1"].__setitem__("text_cn", "这些事实足以覆盖全部风险"), "unsupported financial"),
        (lambda payload: payload["explanation"]["q1"].__setitem__("text_cn", "责任压力的概率路径并不支持有利结构"), "unsupported financial"),
        (lambda payload: payload["explanation"]["q1"].__setitem__("text_cn", "长" * 1201), "too long"),
        (lambda payload: payload.__setitem__("reviewed_snapshot_id", "wrong-snapshot"), "different snapshot"),
    ],
)
def test_262_grounded_explanation_rejects_bad_wire_shapes(mutator, match):
    snapshot, routed = routed_fixture()
    current = product.build_review_input(snapshot, routed, model="local-grounded")
    answer = product.required_selection_payload(current)
    broken = deepcopy(answer)

    mutator(broken)

    with pytest.raises(review.ReviewValidationError, match=match):
        review.validate_wire_response(snapshot, broken, review_input=current, wpem_evidence=routed)


def test_negative_mu_boundary_is_allowed_but_not_mu_value_claim():
    snapshot, routed = routed_fixture()
    current = product.build_review_input(snapshot, routed, model="local-grounded")
    answer = product.required_selection_payload(current)
    answer["explanation"]["q1"]["text_cn"] = (
        "当前资料可以说明μ未知，因此尚不能确认正期望，资料不足以覆盖全部风险，也不允许下单；无法判断上涨概率。这只是边界说明，不改变程序账。"
    )

    normalized = review.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=routed)

    assert "μ未知" in normalized["q1"]["llm_explanation"]["text_cn"]


def test_grounded_catalog_keeps_complementary_legal_entries_without_legacy_six_item_limit():
    snapshot, routed = routed_fixture()
    current = product.build_review_input(snapshot, routed, model="local-grounded")
    answer = product.required_selection_payload(current)
    ids = [row["id"] for row in current["claim_catalog"]["claims"]]
    assert len(ids) > review.MAX_SELECTED_IDS
    answer["selected_claim_ids"] = ids
    result = review.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=routed)
    assert result["response_protocol"] == product.ROUTING_RESPONSE_PROTOCOL
    for replacement in ("invented.claim", answer["selected_counter_ids"][0], ids[0]):
        broken = deepcopy(answer)
        broken["selected_claim_ids"][-1] = replacement
        with pytest.raises(review.ReviewValidationError):
            review.validate_wire_response(snapshot, broken, review_input=current, wpem_evidence=routed)
    by_role = review._catalog_by_role(current["claim_catalog"])
    with pytest.raises(review.ReviewValidationError, match="short id list"):
        review._selected_ids(answer, "selected_claim_ids", "claims", by_role)


def test_grounding_lookup_exposes_fee_and_source_method_break_for_existing_refs():
    snapshot, routed, _old_input, _parsed = _load_sample(UNIFIED_SAMPLE_ROOT, "real_native")
    current = product.build_review_input(snapshot, routed, model="local-grounded")
    answer = product.required_selection_payload(current)

    allowed = set(current["explanation_contract"]["allowed_evidence_refs"])
    assert "fee" in allowed
    assert "wpem.source_method_break" in allowed
    answer["explanation"]["q1"]["evidence_refs"] = list(dict.fromkeys([
        *answer["explanation"]["q1"]["evidence_refs"],
        "wpem.source_method_break",
    ]))
    answer["explanation"]["q2"]["evidence_refs"] = list(dict.fromkeys([
        *answer["explanation"]["q2"]["evidence_refs"],
        "fee",
    ]))

    normalized = review.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=routed)

    q1_evidence = {item["ref"]: item for item in normalized["q1"]["llm_explanation"]["evidence"]}
    q2_evidence = {item["ref"]: item for item in normalized["q2"]["llm_explanation"]["evidence"]}
    assert q1_evidence["wpem.source_method_break"]["source_path"] == "wpem.interpretation.breaks_cn"
    assert "breaks_cn" in q1_evidence["wpem.source_method_break"]["provided"]
    assert q2_evidence["fee"]["source_path"] == "deterministic_financials.fee/source_facts.fee"
    assert "deterministic_fee" in q2_evidence["fee"]["provided"]


def test_262_real_native_observed_points_are_available_for_limited_fragments():
    snapshot, routed, _old_input, _parsed = _load_sample(UNIFIED_SAMPLE_ROOT, "real_native")
    current = product.build_review_input(snapshot, routed, model="local-grounded")
    same_window = current["wpem"]["same_candidate_window"]
    points = same_window["observed_points"]

    assert same_window["status"] == "INSUFFICIENT"
    assert same_window["LevelDeltaPath"]["delta"]["status"] == "NO_STRICT_DELTA"
    assert same_window["LevelDeltaPath"]["path"]["status"] == "NO_PATH_DUE_TO_WINDOW_GAP"
    assert len(points) == 3
    assert [point["decision_asof_ms"] for point in points] == sorted(
        point["decision_asof_ms"] for point in points
    )
    assert [point["market"]["reference_price_usd"] for point in points] == pytest.approx([
        85969.31,
        85944.76,
        85951.12,
    ])
    assert [point["economics"]["visible_credit_btc"] for point in points] == pytest.approx([
        0.0024,
        0.0022,
        0.0023,
    ])
    assert all(point["economics"]["fee_btc"] == pytest.approx(0.00035) for point in points)
    assert all(point["contract"]["short_instrument"] == "BTC-6OCT26-86500-C" for point in points)
    assert all(point["contract"]["long_instrument"] == "BTC-6OCT26-88500-C" for point in points)
    assert all(point["source_clock"]["decision_asof_ms"] == point["decision_asof_ms"] for point in points)
    assert all(point["source_identity"]["fact_source_hash"] for point in points)
    assert all("frozen_risk_reference_missing" in point["qualification"]["snapshot_gap_reasons"] for point in points)

    assert product.OBSERVED_POINTS_REF in current["allowed_refs"]
    assert product.OBSERVED_POINTS_REF in current["explanation_contract"]["allowed_evidence_refs"]

    answer = product.required_selection_payload(current)
    answer["explanation"]["q1"]["evidence_refs"] = [product.OBSERVED_POINTS_REF]
    normalized = review.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=routed)

    evidence_by_ref = {item["ref"]: item for item in normalized["q1"]["llm_explanation"]["evidence"]}
    provided = evidence_by_ref[product.OBSERVED_POINTS_REF]["provided"]
    assert provided["window_status"] == "INSUFFICIENT"
    assert provided["delta_status"] == "NO_STRICT_DELTA"
    assert len(provided["observed_points"]) == 3
    assert provided["observed_points"][0]["market"]["reference_price_usd"] == pytest.approx(85969.31)


def test_262_current_validated_raw_replay_matches_shortest_path_baseline():
    snapshot, routed, review_input, raw_payload, before_normalized = _load_current_validated_sample()

    normalized = review.validate_wire_response(
        snapshot,
        raw_payload,
        review_input=review_input,
        wpem_evidence=routed,
    )

    assert hashlib.sha256(_canon(before_normalized).encode("utf-8")).hexdigest() == BEFORE_NORMALIZED_SHA256
    assert normalized == before_normalized
    assert hashlib.sha256(_canon(normalized).encode("utf-8")).hexdigest() == BEFORE_NORMALIZED_SHA256


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda payload: payload.__setitem__("projection_hash", "wrong-projection"), "projection hash"),
        (lambda payload: payload.__setitem__("claim_catalog_hash", "wrong-catalog"), "claim catalog hash"),
        (lambda payload: payload.__setitem__("response_protocol", product.RESPONSE_PROTOCOL), "response protocol"),
        (lambda payload: payload.__setitem__("extra", True), "frozen schema"),
        (lambda payload: payload.pop("explanation"), "grounded explanation"),
    ],
)
def test_262_current_validated_raw_replay_rejects_identity_key_and_explanation_bypass(mutator, match):
    snapshot, routed, review_input, raw_payload, _before_normalized = _load_current_validated_sample()
    broken = deepcopy(raw_payload)
    mutator(broken)

    with pytest.raises(review.ReviewValidationError, match=match):
        review.validate_wire_response(snapshot, broken, review_input=review_input, wpem_evidence=routed)


def test_263_current_validated_input_restores_compact_map_context_and_grounding():
    snapshot, routed, old_input, _raw_payload, _before_normalized = _load_current_validated_sample()

    current = product.build_review_input(
        snapshot,
        routed,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
    )
    old_rebuilt = product._rebuilt_review_input_for_schema(
        snapshot,
        routed,
        research_only=(old_input.get("research_scope") or {}).get("research_only") is True,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
        schema=old_input["schema"],
    )

    assert old_input["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_262
    assert old_rebuilt == old_input
    assert "map_context" not in old_rebuilt
    assert current["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_263
    assert current["response_protocol"] == product.ROUTING_RESPONSE_PROTOCOL
    assert current["claim_catalog_schema"] == product.ROUTING_CATALOG_SCHEMA_112
    assert current["request_contract"]["output_protocol"] == product.ROUTING_RESPONSE_PROTOCOL
    assert current["request_contract"]["prompt_version"] == product.ROUTING_PROMPT_VERSION

    map_context = current["map_context"]
    assert "policy" not in map_context
    assert "co_location" not in map_context
    assert "response_events" not in map_context
    _assert_absent_keys(map_context, {
        "request_audit", "source_values", "allocation_source_values",
        "borrow_source_values", "funding_source_values", "oi_source_values",
        "cost_basis_source_values", "realized_pnl_source_values",
        "series", "rows", "settled_records",
    })
    map_text = _canon(map_context)
    assert "M_ref_btc" not in map_text
    assert "mu_ref_btc" not in map_text
    assert "region_liability_rows" not in map_text

    backgrounds = {row["name"]: row for row in map_context["usable_background"]}
    allocation = backgrounds["allocation"]
    financing = backgrounds["financing"]
    inventory = backgrounds["inventory"]
    assert allocation["metrics"]["f3_usd_m"] == pytest.approx(202.8)
    assert allocation["metrics"]["f7_usd_m"] == pytest.approx(285.8)
    assert allocation["metrics"]["windows"]["last3"]
    assert financing["fact_state"] == "partial"
    assert financing["metrics"]["oi"]["open_interest_native_btc"] == pytest.approx(94709.017)
    assert financing["metrics"]["oi"]["current_endpoint"]
    assert financing["metrics"]["funding"]["window_24h"]["settled_count"] == 3
    assert inventory["metrics"]["cost_basis"]["cp_sth_usd"] == pytest.approx(75091.67)
    assert inventory["metrics"]["realized_pnl"]["loss_share_14d"] == pytest.approx(0.21360458480937508)
    assert "cex.usdt.borrow_rate.v1:quality:PARTIAL" in map_context["gaps"]

    records = map_context["source_manifest"]["source_records"]
    assert records
    allocation_record = next(
        row for row in records
        if row["record_id"].startswith("btc.etf.flow.daily.v1:")
    )
    assert allocation_record["content_hash"]
    assert allocation_record["time"]["observation_end_ms"] == 1791230400000
    assert allocation_record["usage_qualification"]["status"] == "USABLE"

    references = {row["ref"]: row for row in map_context["usable_references"]}
    assert references["map.reference.brk-cp-sth"]["raw_price"] == pytest.approx(75091.67)
    assert references["map.reference.brk-cp-sth"]["limitations"]

    answer = product.required_selection_payload(current)
    answer["explanation"]["q1"]["evidence_refs"] = [
        "map.background.allocation",
        "map.reference.brk-cp-sth",
    ]
    normalized = review.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=routed)
    q1_evidence = {item["ref"]: item for item in normalized["q1"]["llm_explanation"]["evidence"]}
    provided_background = q1_evidence["map.background.allocation"]["provided"]
    provided_reference = q1_evidence["map.reference.brk-cp-sth"]["provided"]
    assert provided_background["record"]["metrics"]["f3_usd_m"] == pytest.approx(202.8)
    assert provided_background["source_records"][0]["usage_qualification"]["status"] == "USABLE"
    assert provided_reference["record"]["raw_price"] == pytest.approx(75091.67)
    assert provided_reference["source_records"][0]["content_hash"]


@pytest.mark.parametrize(
    "root,name,old_schema",
    [
        (ROUTING_SAMPLE_ROOT, "real_native", product.ROUTING_REVIEW_INPUT_SCHEMA_260),
        (UNIFIED_SAMPLE_ROOT, "real_native", product.ROUTING_REVIEW_INPUT_SCHEMA_261),
    ],
)
def test_old_actual_wires_replay_without_upgrading_to_grounded_protocol(root, name, old_schema):
    snapshot, routed, old_input, parsed = _load_sample(root, name)
    current = product.build_review_input(
        snapshot,
        routed,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
    )
    rebuilt = product._rebuilt_review_input_for_schema(
        snapshot,
        routed,
        research_only=(old_input.get("research_scope") or {}).get("research_only") is True,
        model=(old_input.get("llm_identity") or {}).get("requested_model"),
        schema=old_input["schema"],
    )

    normalized = review.validate_wire_response(snapshot, parsed, review_input=old_input, wpem_evidence=routed)

    assert old_input["schema"] == old_schema
    assert rebuilt == old_input
    assert "observed_points" not in rebuilt["wpem"]["same_candidate_window"]
    assert current["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_263
    assert current["response_protocol"] == product.ROUTING_RESPONSE_PROTOCOL
    assert normalized["response_protocol"] == product.RESPONSE_PROTOCOL
    assert normalized["review_validity"]["review_input_version"] == old_schema
    assert "llm_explanation" not in normalized


def test_262_grounded_explanation_is_preserved_by_production_store_attach(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(current_payload())["snapshot"]
    routed = evidence_core.build_review_evidence(snapshot, include_routing=True)
    current = product.build_review_input(snapshot, routed, model="local-grounded")
    answer = product.required_selection_payload(current)
    normalized = review.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=routed)
    normalized["http_calls"] = 1

    annex = ledger.attach_review(normalized)

    stored_review = annex["review_annex"]["review"]
    assert stored_review["response_protocol"] == product.ROUTING_RESPONSE_PROTOCOL
    assert stored_review["llm_explanation"] == normalized["llm_explanation"]
    assert stored_review["q1"]["llm_explanation"]["evidence"]


def test_262_run_review_research_receipt_preserves_grounded_explanation(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(current_payload())["snapshot"]
    routed = evidence_core.build_review_evidence(snapshot, include_routing=True)
    calls = []

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        payload = product.required_selection_payload(request["_local_review_input"])
        return {
            "model": model,
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload, ensure_ascii=False)}}],
            "usage": {"total_tokens": 123},
        }

    result = review.run_review(
        snapshot,
        ledger,
        tmp_path / "states",
        api_key="fake-test-only",
        model="local-grounded",
        budget=Budget(),
        transport=transport,
        research_only=True,
        wpem_evidence=routed,
    )

    assert result["status"] == "RESEARCH_OK"
    assert result["schema"] == product.ROUTING_RESEARCH_REVIEW_SCHEMA
    assert result["response_protocol"] == product.ROUTING_RESPONSE_PROTOCOL
    assert result["production_attach_allowed"] is False
    assert result["llm_explanation"]["q1"] == result["q1"]["llm_explanation"]
    assert result["llm_explanation"]["q2"] == result["q2"]["llm_explanation"]
    assert result["q1"]["llm_explanation"]["evidence"]
    assert result["http_calls"] == 1
    assert len(calls) == 1
