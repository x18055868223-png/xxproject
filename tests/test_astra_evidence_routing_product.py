import inspect
import json
from copy import deepcopy
from pathlib import Path

import pytest

from tools import astra_underwriting_v20 as core
from tools import astra_underwriting_review_v20 as legacy
from tools import astra_underwriting_store_v20 as store
from tools import astra_wpem_product_review_v1 as product
from tools import astra_wpem_review_evidence_v1 as evidence_core
from test_astra_bounded_claims_v244 import Budget
from test_astra_underwriting_v20 import sample


REPO_ROOT = Path(__file__).resolve().parents[1]
RICH_SAMPLE_ROOT = REPO_ROOT / ".artifacts" / "decision_workspace_filled_preview_20261006_01" / "samples"
FINAL_SAMPLE_ROOT = REPO_ROOT / ".artifacts" / "evidence_routing_20261006_01" / "samples"


@pytest.fixture(autouse=True)
def retain_catalog_wire_replay_contract(monkeypatch):
    """Keep the historical routing/catalog wire under its own 2.6.3 input."""
    def build(snapshot, evidence, *, research_only=False, model=None):
        return product._rebuilt_review_input_for_schema(
            snapshot, evidence, research_only=research_only, model=model,
            schema=product.ROUTING_REVIEW_INPUT_SCHEMA_263 if product.has_routing(evidence)
            else product.REVIEW_INPUT_SCHEMA)
    monkeypatch.setattr(product, "build_review_input", build)


def _build_legacy_review_evidence(snapshot):
    if "include_routing" in inspect.signature(evidence_core.build_review_evidence).parameters:
        return evidence_core.build_review_evidence(snapshot, include_routing=False)
    return evidence_core.build_review_evidence(snapshot)


def _build_routed_review_evidence(snapshot):
    assert "include_routing" in inspect.signature(evidence_core.build_review_evidence).parameters
    return evidence_core.build_review_evidence(snapshot, include_routing=True)


def fixture():
    snapshot = core.evaluate(sample("put"))
    package = _build_legacy_review_evidence(snapshot)
    return snapshot, package


def routed_fixture():
    snapshot = core.evaluate(sample("put"))
    package = _build_routed_review_evidence(snapshot)
    return snapshot, package


def _routed_from_private_proof(*parts):
    proof_path = RICH_SAMPLE_ROOT.joinpath(*parts, "private_proof.json")
    if not proof_path.exists():
        pytest.skip(f"rich routing fixture missing: {proof_path}")
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    composition = proof["composition_inputs"]
    snapshots = [
        packet["snapshot"]
        for packet in composition["packet_sequence"]
        if packet.get("snapshot", {}).get("snapshot_id") == proof["snapshot_id"]
    ]
    assert snapshots
    snapshot = snapshots[0]
    package = evidence_core.build_review_evidence(
        snapshot,
        packet_sequence=composition.get("packet_sequence") or (),
        qualification_reports=composition.get("qualification_reports") or (),
        cross_section=composition.get("cross_section") or (),
        risk_reference=composition.get("risk_reference"),
        release_identity=composition.get("release_identity"),
        native_sources={},
        include_routing=True,
    )
    return snapshot, package


def _reseal_evidence(row):
    body = {k: deepcopy(v) for k, v in row.items() if k not in {"evidence_hash", "review_evidence_hash"}}
    digest = legacy._hash(body)
    body["evidence_hash"] = digest
    body["review_evidence_hash"] = digest
    return body


def _final_sample(name):
    root = FINAL_SAMPLE_ROOT / name
    if not (root / "snapshot.json").exists():
        pytest.skip(f"final routing fixture missing: {root}")
    return (
        json.loads((root / "snapshot.json").read_text(encoding="utf-8")),
        json.loads((root / "evidence.json").read_text(encoding="utf-8")),
        json.loads((root / "review_input.json").read_text(encoding="utf-8")),
    )


def test_routing_evidence_builds_261_input_with_compact_catalog_and_request_contract():
    snapshot, routed = routed_fixture()
    current = product.build_review_input(snapshot, routed, model="local-routing")

    assert current["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_263
    assert current["request_contract"]["prompt_version"] == product.ROUTING_PROMPT_VERSION
    assert current["claim_catalog_schema"] == product.ROUTING_CATALOG_SCHEMA_112
    assert current["request_contract"]["routing_schema"] == "evidence_routing@1.0.0"
    assert current["request_contract"]["routing_hash"] == current["routing_identity"]["routing_hash"]
    assert current["evidence_routing"]["routing_hash"] == current["routing_identity"]["routing_hash"]
    assert "fmz_fact_context" not in current["source_facts"]
    assert current["source_facts"]["routed_dependency_blocks"]
    assert "contextual_market_reads" in current["source_facts"]
    assert "text_cn" not in current["source_facts"]["routed_dependency_blocks"][0]
    assert current["evidence_routing"]["dependency_representatives"]
    assert current["deterministic_financials"] == legacy.build_review_input(snapshot)["deterministic_financials"]
    assert current["deterministic_account"] == legacy.build_review_input(snapshot)["deterministic_account"]

    for role in ("claims", "counterclaims", "gaps", "reassess_conditions"):
        blocks = []
        for item in current["evidence_routing"]["selection_summary"][role]:
            assert set((
                "id", "ref", "claim_cn", "text_cn", "q1_reason_cn", "q2_reason_cn",
                "block_id", "evidence_ids", "q1_role", "q2_role",
            )).issubset(item)
            assert item["ref"] in current["allowed_refs"]
            blocks.append(item["block_id"])
        assert len(blocks) == len(set(blocks))


def test_routing_required_ids_are_strict_and_normalized_into_q1_q2_observations():
    snapshot, routed = routed_fixture()
    current = product.build_review_input(snapshot, routed)
    answer = product.required_selection_payload(current)

    for required_key, selected_key in (
        ("claim_ids", "selected_claim_ids"),
        ("counter_ids", "selected_counter_ids"),
        ("gap_ids", "selected_gap_ids"),
        ("reassess_condition_ids", "selected_reassess_condition_ids"),
    ):
        assert set(current["routing_selection_requirements"][required_key]).issubset(answer[selected_key])

    result = legacy.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=routed)

    assert result["response_protocol"] == current["response_protocol"]
    assert result["q1"]["routing_evidence_ids"]
    assert result["q2"]["routing_evidence_ids"]
    assert result["q1"]["routing_observations_cn"] != result["q2"]["routing_observations_cn"]
    assert all("selected_id" in row for row in result["q1"]["routing_observation_trace"])
    assert result["review_validity"]["routing_hash"] == current["routing_identity"]["routing_hash"]
    assert result["review_validity"]["routing_schema"] == "evidence_routing@1.0.0"

    missing = deepcopy(answer)
    for required_key, selected_key in (
        ("counter_ids", "selected_counter_ids"),
        ("gap_ids", "selected_gap_ids"),
        ("claim_ids", "selected_claim_ids"),
        ("reassess_condition_ids", "selected_reassess_condition_ids"),
    ):
        required = current["routing_selection_requirements"][required_key]
        if required:
            missing[selected_key].remove(required[0])
            break
    with pytest.raises(legacy.ReviewValidationError, match="routing required evidence"):
        legacy.validate_wire_response(snapshot, missing, review_input=current, wpem_evidence=routed)


def test_routing_request_binds_local_identity_and_frozen_tail():
    snapshot, routed = routed_fixture()
    request = legacy.build_request(snapshot, "local-routing", wpem_evidence=routed)
    current = request["_local_review_input"]
    frozen_text = request["messages"][1]["content"].rsplit("\n冻结候选：", 1)[1]
    frozen, end = json.JSONDecoder().raw_decode(frozen_text)
    assert frozen_text[end:].startswith("\n输出前最后核对两种不同数组：")

    assert current["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_263
    assert frozen == current
    assert request["_local_prompt_version"] == product.ROUTING_PROMPT_VERSION
    assert request["_local_evidence_routing_schema"] == current["routing_identity"]["schema"]
    assert request["_local_evidence_routing_hash"] == current["routing_identity"]["routing_hash"]
    assert "Evidence Routing 元数据由本地程序冻结" in request["messages"][0]["content"]
    assert "contextual_market_reads" in request["messages"][0]["content"]


def test_261_contextual_market_reads_preserve_actual_background_without_required_support():
    snapshot, routed, _old_input = _final_sample("real_native")
    current = product.build_review_input(snapshot, routed)
    context = current["source_facts"]["contextual_market_reads"]

    assert "schema" not in context
    assert "经验独立性验证" in context["independence_note_cn"]
    assert context["observed_fragment_cn"]
    assert all("不足以概括完整同窗路径" in text for text in context["observed_fragment_cn"])
    item_text = "\n".join(item["text_cn"] for item in context["items"])
    assert "direction=Bullish" in item_text
    assert "last_rate=5.28e-05" in item_text
    assert "vote=-0.1990315990721552" in item_text
    assert "data_status=full_live" in item_text
    assert "combined.direction=neutral_to_bearish" in item_text
    assert "SpaceGate=STRUCTURE_BLOCKED" in item_text
    assert all({"label_cn", "text_cn", "source_ref", "asof_ms", "readings"}.issubset(item) for item in context["items"])

    context_entries = [
        item for item in current["evidence_routing"]["selection_summary"]["claims"]
        if item["id"] == "claim.routing.contextual_market_readings"
    ]
    assert context_entries
    assert context_entries[0]["q1_role"] == "OBSERVATION"
    assert context_entries[0]["q2_role"] == "OBSERVATION"
    assert "claim.routing.contextual_market_readings" not in current["routing_selection_requirements"]["claim_ids"]


def test_261_same_option_space_support_keeps_space_gate_limitation_reachable():
    snapshot, routed, _old_input = _final_sample("real_native")
    current = product.build_review_input(snapshot, routed)
    answer = product.required_selection_payload(current)
    result = legacy.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=routed)

    gap = [
        item for item in current["evidence_routing"]["selection_summary"]["gaps"]
        if item["id"] == "gap.routing.key_limitations"
    ][0]
    assert any(evidence_id.startswith("eu.space_gate_gamma_regime") for evidence_id in gap["evidence_ids"])
    assert any(evidence_id.startswith("eu.space_gate_gamma_regime") for evidence_id in result["q1"]["routing_evidence_ids"])


def test_real_rich_relief_package_selects_flow_mechanism_and_stays_compact():
    snapshot, routed = _routed_from_private_proof("relief_thinner", "put")
    current = product.build_review_input(snapshot, routed)
    answer = product.required_selection_payload(current)
    result = legacy.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=routed)

    compact = deepcopy(current)
    points = compact["wpem"]["same_candidate_window"].pop("observed_points")
    map_context = compact.pop("map_context")
    assert map_context["usable_background"] and map_context["source_manifest"]
    assert "policy" not in map_context  # Financial authority is not duplicated.
    # Keep the existing routing budget separate from restored factual MAP data.
    assert len(json.dumps(compact, ensure_ascii=False, separators=(",", ":"))) < 50_000
    assert points and len(points) <= len(routed["composition_inputs"]["packet_sequence"])
    assert all("raw_payload" not in point for point in points)
    assert "claim.routing.mechanism_support" in answer["selected_claim_ids"]
    assert "counter.routing.primary_counter" in answer["selected_counter_ids"]
    assert "gap.routing.key_limitations" in answer["selected_gap_ids"]
    assert any(evidence_id.startswith("eu.active_flow") for evidence_id in result["q1"]["routing_evidence_ids"])
    assert any(evidence_id.startswith("eu.active_flow") for evidence_id in result["q2"]["routing_evidence_ids"])
    assert not any(evidence_id.startswith("eu.anchor") for evidence_id in result["q2"]["routing_evidence_ids"])
    assert result["q1"]["routing_observations_cn"] != result["q2"]["routing_observations_cn"]


def test_real_counter_flow_package_routes_flow_as_primary_counter():
    snapshot, routed = _routed_from_private_proof("pressure_thicker", "call")
    current = product.build_review_input(snapshot, routed)
    answer = product.required_selection_payload(current)
    result = legacy.validate_wire_response(snapshot, answer, review_input=current, wpem_evidence=routed)

    assert "counter.routing.primary_counter" in answer["selected_counter_ids"]
    assert any(evidence_id.startswith("eu.active_flow") for evidence_id in result["q1"]["routing_evidence_ids"])
    assert any(evidence_id.startswith("eu.active_flow") for evidence_id in result["q2"]["routing_evidence_ids"])
    counter_trace = [
        row for row in result["q1"]["routing_observation_trace"]
        if row["selected_id"] == "counter.routing.primary_counter"
    ]
    assert counter_trace and any(evidence_id.startswith("eu.active_flow") for evidence_id in counter_trace[0]["evidence_ids"])


def test_routing_package_cannot_replay_old_25x_inputs_and_old_package_stays_252():
    snapshot, package = fixture()
    old = product.build_review_input(snapshot, package)
    _, routed = routed_fixture()
    current = product.build_review_input(snapshot, routed)

    assert old["schema"] == product.REVIEW_INPUT_SCHEMA
    assert old["request_contract"]["prompt_version"] == product.PROMPT_VERSION
    assert "evidence_routing" not in old

    old_shape = deepcopy(current)
    old_shape["schema"] = product.REVIEW_INPUT_SCHEMA
    old_shape["projection_hash"] = legacy._hash({key: value for key, value in old_shape.items() if key != "projection_hash"})
    answer = product.required_selection_payload(old_shape)
    with pytest.raises(legacy.ReviewValidationError, match="routing evidence cannot replay"):
        legacy.validate_wire_response(snapshot, answer, review_input=old_shape, wpem_evidence=routed)


def test_routing_hash_participates_in_review_status_binding():
    snapshot, routed = routed_fixture()
    current = product.build_review_input(snapshot, routed)
    result = legacy.validate_wire_response(
        snapshot,
        product.required_selection_payload(current),
        review_input=current,
        wpem_evidence=routed,
    )

    changed_binding = deepcopy(result)
    changed_binding["review_validity"]["routing_hash"] = "stale-routing-hash"

    assert product.review_status(result, snapshot, routed) == "CURRENT"
    assert product.review_status(changed_binding, snapshot, routed) == "STALE"


@pytest.mark.parametrize("sample_name", ["real_native", "neutral_bear_conflict"])
def test_old_260_actual_wire_replays_exactly_after_261_default(sample_name):
    snapshot, routed, old_input = _final_sample(sample_name)
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
    parsed = json.loads((FINAL_SAMPLE_ROOT / sample_name / "actual_llm" / "parsed_wire_response.json").read_text(encoding="utf-8"))
    result = legacy.validate_wire_response(snapshot, parsed, review_input=old_input, wpem_evidence=routed)

    assert old_input["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_260
    assert old_input["request_contract"]["prompt_version"] == product.ROUTING_PROMPT_VERSION_260
    assert rebuilt == old_input
    assert current["schema"] == product.ROUTING_REVIEW_INPUT_SCHEMA_263
    assert current["projection_hash"] != old_input["projection_hash"]
    assert result["status"] == "OK"


def test_forged_routing_without_hash_is_rejected_by_evidence_replay():
    snapshot, routed = routed_fixture()
    forged = deepcopy(routed)
    forged["evidence_routing"].pop("routing_hash", None)
    forged = _reseal_evidence(forged)

    with pytest.raises(legacy.ReviewValidationError, match="evidence_routing_rebuild_mismatch"):
        product.build_review_input(snapshot, forged)


def test_old_settled_wpem_state_is_stale_for_routing_input_without_http(tmp_path):
    snapshot = core.evaluate(sample("put"))
    old_package = _build_legacy_review_evidence(snapshot)
    routed = _build_routed_review_evidence(snapshot)
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    ledger.record_snapshot(snapshot)
    old_input = product.build_review_input(snapshot, old_package, research_only=True, model="local-routing")
    state_path = legacy._review_state_path(
        tmp_path / "review" / "research_only",
        snapshot["candidate_id"],
        snapshot["snapshot_id"],
    )
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "schema": product.REVIEW_STATE_SCHEMA,
        "candidate_id": snapshot["candidate_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "packet_hash": old_input["packet_hash"],
        "input_hash": old_input["projection_hash"],
        "mode": "research_only",
        "attempts": [{"status": "HTTP_OK"}],
        "settled": True,
        "result": {
            "schema": product.RESEARCH_REVIEW_SCHEMA,
            "status": "RESEARCH_OK",
            "reviewed_snapshot_id": snapshot["snapshot_id"],
            "response_protocol": product.RESPONSE_PROTOCOL,
            "http_calls": 1,
        },
    }), encoding="utf-8")
    calls = []

    result = legacy.run_review(
        snapshot,
        ledger,
        tmp_path / "review",
        api_key="fake-test-only",
        model="local-routing",
        budget=Budget(),
        transport=lambda *args, **kwargs: calls.append(args),
        research_only=True,
        wpem_evidence=routed,
    )

    assert result["status"] == "stale_protocol_review"
    assert result["legacy_status"] == "RESEARCH_OK"
    assert calls == []
