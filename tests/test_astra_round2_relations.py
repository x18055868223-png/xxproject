import copy

import pytest

from tools import astra_map_v24 as maps
from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_v20 as core
from tools import astra_workbench_preview_v20 as preview
from test_astra_underwriting_review_v20 import current_payload
from test_astra_underwriting_review_v20 import good_answer
from test_astra_underwriting_v20 import T
from test_astra_underwriting_v20 import sample


NOW_MS = 1_790_478_000_000


def _ids(catalog, role):
    return {item["id"] for item in catalog[role]}


def _dedupe(items):
    out = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _first(catalog, role, prefix=None):
    for item in catalog[role]:
        if prefix is None or item["id"].startswith(prefix):
            return item["id"]
    raise AssertionError((role, prefix))


def _maybe(catalog, role, item_id):
    return [item_id] if item_id in _ids(catalog, role) else []


def _catalog_cn_text(catalog):
    texts = []
    for role in ("claims", "counterclaims"):
        texts.extend(str(item.get("claim_cn", "")) for item in catalog[role])
    for role in ("gaps", "reassess_conditions"):
        texts.extend(str(item.get("text_cn", "")) for item in catalog[role])
    return "\n".join(texts)


def _wire(snapshot, *, applicability="CONTESTED", research_only=True):
    projection = review.build_review_input(snapshot, research_only=research_only)
    catalog = projection["claim_catalog"]
    req = projection["decision_relations"]["selection_requirements"]

    claim_ids = _dedupe(req["claim_ids"] + _maybe(catalog, "claims", "claim.risk.overall_mu_reference"))
    if not claim_ids:
        claim_ids = [_first(catalog, "claims", "claim.contract.")]

    counter_ids = _dedupe(
        req["counter_ids"]
        + _maybe(catalog, "counterclaims", "counter.compensation.nonpositive_margin")
        + _maybe(catalog, "counterclaims", "counter.structure.opening_blocked")
        + _maybe(catalog, "counterclaims", "counter.execution.permission_closed")
    )
    if not counter_ids:
        counter_ids = [_first(catalog, "counterclaims")]

    gap_ids = []
    for item_id in ("gap.compensation.mu_reference_missing", "gap.risk_reference_missing", "gap.account_margin_missing"):
        gap_ids.extend(_maybe(catalog, "gaps", item_id))
    if not gap_ids:
        gap_ids = [_first(catalog, "gaps")]

    reassess_ids = _dedupe(
        req["reassess_condition_ids"]
        + _maybe(catalog, "reassess_conditions", "reassess.compensation_reprice")
        + _maybe(catalog, "reassess_conditions", "reassess.structure_new_snapshot")
        + _maybe(catalog, "reassess_conditions", "reassess.snapshot_rebuilt")
    )
    if not reassess_ids:
        reassess_ids = [_first(catalog, "reassess_conditions")]

    return {
        "response_protocol": review.RESPONSE_PROTOCOL,
        "reviewed_snapshot_id": projection["snapshot_id"],
        "projection_hash": projection["projection_hash"],
        "claim_catalog_hash": projection["claim_catalog_hash"],
        "applicability": applicability,
        "selected_claim_ids": claim_ids[:6],
        "selected_counter_ids": counter_ids[:6],
        "selected_gap_ids": gap_ids[:6],
        "selected_reassess_condition_ids": reassess_ids[:6],
    }, projection


def _candidate(scenario_id, side):
    candidates = preview.build_preview(NOW_MS, scenario_id=scenario_id)["candidates"]
    return next(item["snapshot"] for item in candidates if item["side"] == side)


def test_w2_call_up_flow_is_adverse_and_coverage_unknown():
    snapshot = _candidate("W2", "call")
    wire, projection = _wire(snapshot)

    result = review.validate_wire_response(snapshot, wire, review_input=projection)

    assert projection["decision_relations"]["flow"]["relation"] == "adverse"
    assert projection["decision_relations"]["risk_coverage"]["soft_risk_coverage"] == "UNKNOWN"
    assert "claim.direction.flow_adverse" in result["selected_claim_ids"]
    assert "counter.direction.flow_adverse_not_quantified" in result["selected_counter_ids"]
    assert "counter.risk.coverage_unknown" in result["selected_counter_ids"]
    assert result["q2"]["strongest_counter"]["id"] == "counter.direction.flow_adverse_not_quantified"
    assert "本地模拟或背景口径显示量价上行" in result["q1"]["summary_cn"]
    assert "对Call为贴近本侧不利方向" in result["q1"]["summary_cn"]
    assert "fact_context" not in result["q1"]["summary_cn"]
    assert "风险逐项覆盖未知" in result["q2"]["coverage_cn"]
    assert "只作定性背景" in result["q2"]["coverage_cn"]
    assert "Call在卖出腿与保护腿之间" in result["q2"]["liability_cn"]
    catalog_cn = _catalog_cn_text(projection["claim_catalog"])
    assert "flow.direction" not in catalog_cn
    assert "volume_price.direction" not in catalog_cn
    assert "QUANTIFIED" not in catalog_cn
    assert "源端量价方向" in catalog_cn
    assert "定量归因证据" in catalog_cn
    flow_counter = next(
        item for item in projection["claim_catalog"]["counterclaims"]
        if item["id"] == "counter.direction.flow_adverse_not_quantified"
    )
    assert flow_counter["ref"] == "fact_context.market_facts.flow.direction"


def test_w1_w2_positive_margin_accounts_remain_distinct_without_threshold_label():
    w1 = _candidate("W1", "put")
    w2 = _candidate("W2", "put")

    w1_wire, w1_projection = _wire(w1)
    w2_wire, w2_projection = _wire(w2)
    r1 = review.validate_wire_response(w1, w1_wire, review_input=w1_projection)
    r2 = review.validate_wire_response(w2, w2_wire, review_input=w2_projection)

    assert "c=0.00023000 BTC" in r1["q2"]["exchange_reason_cn"]
    assert "M=0.00003000 BTC" in r1["q2"]["exchange_reason_cn"]
    assert "c=0.00050000 BTC" in r2["q2"]["exchange_reason_cn"]
    assert "M=0.00014000 BTC" in r2["q2"]["exchange_reason_cn"]
    assert r1["q2"]["exchange_reason_cn"] != r2["q2"]["exchange_reason_cn"]
    assert "thin" not in r1["q2"]["exchange_reason_cn"].lower()
    assert "阈值" not in r1["q2"]["exchange_reason_cn"]


def test_w4_call_reprice_condition_uses_required_credit_not_extra_premium():
    snapshot = _candidate("W4", "call")
    wire, projection = _wire(snapshot, applicability="CONTESTED")

    result = review.validate_wire_response(snapshot, wire, review_input=projection)

    assert result["q2"]["disposition"] == "reference_compensation_insufficient"
    assert "counter.compensation.nonpositive_margin" in result["selected_counter_ids"]
    text = " ".join(result["q2"]["reassess_if_cn"] + [result["q2"]["exchange_reason_cn"]])
    assert "c > 0.00020000 BTC" in text
    assert "c = 0.00020000 BTC 只打平" in text
    assert "至少增加 0.00005000 BTC 才打平" in text


def test_missing_mu_gate_and_horizon_mismatch_stay_fail_closed():
    missing_mu = _candidate("MISSING_MU", "put")
    missing_wire, missing_projection = _wire(missing_mu, applicability="UNABLE_TO_JUDGE")
    missing_result = review.validate_wire_response(
        missing_mu,
        missing_wire,
        review_input=missing_projection,
    )
    assert missing_result["q2"]["disposition"] == "information_gap"
    assert "gap.compensation.mu_reference_missing" in missing_result["selected_gap_ids"]
    assert "没有可用整体μ" in missing_result["q2"]["coverage_cn"]

    # Since 2026-10-13 the space structure is evaluated, not gated: a closed producer gate
    # no longer forces deferral or the opening-blocked counter.
    gate = _candidate("W5", "put")
    gate_wire, gate_projection = _wire(gate)
    gate_result = review.validate_wire_response(gate, gate_wire, review_input=gate_projection)
    assert gate_result["q2"]["disposition"] != "defer_reassess"
    assert "counter.structure.opening_blocked" not in gate_result["selected_counter_ids"]

    payload = sample()
    payload["risk"]["horizon_hours"] = 12
    horizon = core.evaluate(payload)
    horizon_wire, horizon_projection = _wire(horizon, applicability="UNABLE_TO_JUDGE")
    horizon_result = review.validate_wire_response(horizon, horizon_wire, review_input=horizon_projection)
    assert "counter.risk.horizon_mismatch" in horizon_result["selected_counter_ids"]
    assert "reassess.risk_horizon_matched" in horizon_result["selected_reassess_condition_ids"]
    assert "risk_horizon_mismatch" in horizon_projection["decision_relations"]["risk_coverage"]["gap_reasons"]
    assert "期限与当前到期不匹配" in horizon_result["q2"]["coverage_cn"]


def _record(record_id, values):
    return {
        "record_id": record_id,
        "identity": {"product_id": "nominal.only.test", "currency": "USD", "unit": "USD", "asset": "BTC"},
        "time": {"first_seen_at_ms": T - 1, "observation_end_ms": T - 2},
        "content": {"values": values},
        "usage_qualification": {"can_use": True, "status": "USABLE", "qualified_at_ms": T},
    }


def _nominal_zone(reference_id, low, high, source_id):
    return {
        "reference_id": reference_id,
        "revision_id": "v1",
        "family": "TRADED_ACCEPTANCE",
        "role": "KPF_BASIN",
        "geometry": "OBSERVED_ZONE",
        "raw_low": low,
        "raw_high": high,
        "market": "Binance",
        "quote_currency": "USDT",
        "price_basis": "BTCUSDT_PERP",
        "quality": "OK",
        "usage_decision": {"can_use": True},
        "known_at_ms": T,
        "active_from_ms": T,
        "observation_end_ms": T,
        "source_family": "nominal",
        "coordinate_qualification": "NOMINAL_ONLY",
        "source_record_ids": [source_id],
    }


def test_nonqualified_map_reference_does_not_become_policy_relation():
    snapshot = core.evaluate(sample())
    source_records = [_record("nominal-1", {"zone": [96600, 96900]})]
    mapping = maps.build_map(
        None,
        now_ms=T,
        snapshot=snapshot,
        references=[_nominal_zone("nominal-zone", 96600, 96900, "nominal-1")],
    )
    manifest = {
        "schema": "btc_map_source_manifest@1",
        "cutoff_at_ms": T,
        "candidate_id": snapshot["candidate_id"],
        "source_records": source_records,
    }
    manifest["manifest_hash"] = core._hash({key: value for key, value in manifest.items() if key != "manifest_hash"})
    mapping["source_manifest"] = manifest
    mapping["map_id"] = core._hash({key: value for key, value in mapping.items() if key != "map_id"})
    bound = core.attach_btc_map(snapshot, mapping)

    projection = review.build_review_input(bound, research_only=True)

    assert projection["decision_relations"]["map_policy"]["qualified"] is False
    assert "map.policy" not in projection["allowed_refs"]
    assert "counter.map.qualitative_only" not in _ids(projection["claim_catalog"], "counterclaims")
    bound_wire, bound_projection = _wire(bound)
    assert "MAP合格责任区域" not in review.validate_wire_response(
        bound,
        bound_wire,
        review_input=bound_projection,
    )["q1"]["summary_cn"]


def test_store_replays_program_q1_q2_with_liability_cn(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(current_payload())["snapshot"]
    normalized = review.validate_wire_response(snapshot, good_answer(snapshot))
    attached = {**normalized, "http_calls": 1}

    ledger.attach_review(attached, snapshot_id=snapshot["snapshot_id"])

    assert ledger.state()["snapshots"][snapshot["snapshot_id"]]["review"] is not None
    tampered = copy.deepcopy(attached)
    tampered["q2"]["liability_cn"] = "tampered"
    with pytest.raises(ValueError, match="normalized fields"):
        ledger.attach_review(tampered, snapshot_id=snapshot["snapshot_id"])
