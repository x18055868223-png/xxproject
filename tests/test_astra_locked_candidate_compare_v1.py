import copy
import json
from pathlib import Path

import pytest

from tools import astra_candidate_asof_v1 as packets
from tools import astra_locked_candidate_compare_v1 as followup
from tools import astra_underwriting_v20 as core
from tests.test_astra_underwriting_v20 import T, sample


def payload_with_components(side="put", *, risk=True):
    payload = sample(side, risk=risk)
    payload["fee"].update(entry_fee_btc=0.00003, delivery_fee_btc=0.0000075)
    return payload


def point_from_payload(payload, *, point_id="p0", recorded_at_ms=T + 1):
    snapshot = core.evaluate(payload)
    packet = packets.build_packet(snapshot, raw_payload=payload)
    candidate = {
        "candidate_id": snapshot["candidate_id"],
        "side": snapshot["contract"]["side"],
        "status": "snapshot",
        "remaining_to_expiry_ms": snapshot["contract"]["expiry_ms"] - snapshot["market"]["valuation_ts_ms"],
        "snapshot": copy.deepcopy(snapshot),
        "current_questions": {
            "q1": {"summary_cn": "原 Q1 只保留直接证据。", "routing_evidence_ids": ["wpem.window"]},
            "q2": {"summary_cn": "原 Q2 只保留补偿引用。", "routing_evidence_ids": ["fee"]},
        },
        "wpem_summary": {"path_cn": "窗口不足，只读观察。", "compensation_cn": "补偿仅按冻结账本。"},
    }
    return {"point_id": point_id, "candidate": candidate, "packet": packet, "recorded_at_ms": recorded_at_ms}


def row_by_key(output, key):
    return next(row for row in output["rows"] if row["key"] == key)


def test_signed_boundary_distance_put_and_call_use_boundary_denominator():
    put = core.signed_boundary_distance("put", 100000, 97000, boundary_kind="short")
    call = core.signed_boundary_distance("call", 100000, 103000, boundary_kind="short")
    crossed_put = core.signed_boundary_distance("put", 96000, 97000, boundary_kind="short")

    assert put["signed_distance_pct"] == pytest.approx(3000 / 97000 * 100)
    assert call["signed_distance_pct"] == pytest.approx(3000 / 103000 * 100)
    assert crossed_put["signed_distance_pct"] < 0
    assert core.signed_boundary_distance("put", None, 97000, boundary_kind="short") is None


def test_reference_close_quote_uses_short_ask_and_long_bid_without_delivery_fee():
    payload = payload_with_components()
    snapshot = core.evaluate(payload)
    quote = core.reference_close_quote(
        snapshot["contract"],
        payload["market"],
        T,
        fee={"basis": "configured_assumption", "close_fee_btc": 0.00002, "delivery_fee_btc": 1, "covers": ["early_exit"]},
    )

    assert quote["short_buy_btc"] == pytest.approx(0.0008)
    assert quote["long_sale_btc"] == pytest.approx(0.0002)
    assert quote["debit_btc"] == pytest.approx(0.0006)
    assert quote["close_fee_btc"] == pytest.approx(0.00002)
    assert quote["net_debit_after_close_fee_btc"] == pytest.approx(0.00062)
    assert quote["executable_or_filled"] is False


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda p: p["market"]["long_book"].update(bids=[]), "long_depth_insufficient"),
        (lambda p: p["market"]["short_book"].update(timestamp=T - 6000), "short_book_stale_or_future"),
        (lambda p: p["market"]["long_book"].update(timestamp=T - 3000), "leg_book_time_skew"),
        (lambda p: p["market"]["short_book"].update(bids=[[0.009, 0.1]], asks=[[0.008, 0.1]]), "short_crossed_book"),
    ],
)
def test_reference_close_quote_keeps_quote_gaps_for_missing_depth_stale_skew_and_crossed_books(mutate, reason):
    payload = payload_with_components()
    mutate(payload)
    snapshot = core.evaluate(payload)
    quote = core.reference_close_quote(snapshot["contract"], payload["market"], T)

    assert quote["debit_btc"] is None
    assert reason in quote["gap_reasons"]
    assert "close_fee_unknown" in quote["gap_reasons"]


def test_followup_keeps_baseline_breakeven_fixed_and_separates_open_credit_from_close_cost():
    base_payload = payload_with_components()
    latest_payload = payload_with_components()
    latest_payload["market"]["short_book"]["bids"] = [[0.009, 0.1]]
    latest_payload["market"]["short_book"]["asks"] = [[0.012, 0.1]]
    latest_payload["market"]["long_book"]["bids"] = [[0.001, 0.1]]
    latest_payload["market"]["long_book"]["asks"] = [[0.003, 0.1]]
    latest_payload["market"]["valuation_ts_ms"] = T + 300_000
    latest_payload["market"]["received_ts_ms"] = T + 300_000
    latest_payload["market"]["short_book"]["timestamp"] = T + 300_000
    latest_payload["market"]["long_book"]["timestamp"] = T + 300_000
    latest_payload["risk"]["input_ts_ms"] = T + 300_000
    latest_payload["risk"]["feature_end_ts_ms"] = T + 240_000
    latest_payload["risk"]["horizon_hours"] = (latest_payload["metadata"]["short"]["expiration_timestamp"] - (T + 300_000)) / 3_600_000

    baseline = point_from_payload(base_payload, point_id="t0", recorded_at_ms=T + 10)
    latest = point_from_payload(latest_payload, point_id="t1", recorded_at_ms=T + 300_010)
    output = followup.build_followup({"episode": {"id": "ep"}, "baseline": baseline, "latest": latest, "observations": []}, now_ms=T + 300_010)

    assert output["overview"]["baseline_breakeven_usd"] == baseline["candidate"]["snapshot"]["liability"]["breakeven_usd"]
    assert output["overview"]["baseline_breakeven_usd"] != latest["candidate"]["snapshot"]["liability"]["breakeven_usd"]
    assert row_by_key(output, "quote.visible_credit_btc")["status"] == "changed"
    assert "增加" in row_by_key(output, "quote.visible_credit_btc")["change_cn"]
    close = row_by_key(output, "close.debit_btc")
    assert close["status"] == "changed"
    assert close["t1"]["value"] > close["t0"]["value"]
    assert output["overview"]["close_estimate"]["debit_btc"] == pytest.approx(0.0011)


def test_followup_derives_close_fee_only_inside_existing_daily_option_support_range():
    payload = payload_with_components()
    for leg in ("short", "long"):
        payload["metadata"][leg]["taker_commission"] = 0.0003
        payload["metadata"][leg]["settlement_period"] = "day"
    point = point_from_payload(payload)

    output = followup.build_followup({"episode": {"id": "ep"}, "baseline": point, "latest": None, "observations": []}, now_ms=T)

    close = output["overview"]["close_estimate"]
    assert close["close_fee_btc"] == pytest.approx(0.000055)
    assert close["net_debit_after_close_fee_btc"] == pytest.approx(0.000655)
    assert "delivery_fee_btc" not in json.dumps(close["fee_detail"], ensure_ascii=False)


def test_same_source_clock_is_not_treated_as_new_confirmation():
    base_payload = payload_with_components()
    latest_payload = payload_with_components()
    latest_payload["market"]["short_book"]["bids"] = [[0.009, 0.1]]
    latest_payload["market"]["short_book"]["asks"] = [[0.01, 0.1]]
    baseline = point_from_payload(base_payload)
    latest = point_from_payload(latest_payload)

    output = followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []}, now_ms=T)

    assert row_by_key(output, "quote.visible_credit_btc")["status"] == "same_clock"
    assert "未变化" in row_by_key(output, "quote.visible_credit_btc")["change_cn"]


def test_source_or_window_switch_keeps_both_values_but_no_precise_delta():
    base_payload = payload_with_components()
    latest_payload = payload_with_components()
    latest_payload["market"]["valuation_ts_ms"] = T + 300_000
    latest_payload["market"]["received_ts_ms"] = T + 300_000
    latest_payload["market"]["short_book"]["timestamp"] = T + 300_000
    latest_payload["market"]["long_book"]["timestamp"] = T + 300_000
    latest_payload["risk"]["input_ts_ms"] = T + 300_000
    latest_payload["risk"]["feature_end_ts_ms"] = T + 240_000
    latest_payload["risk"]["horizon_hours"] = (latest_payload["metadata"]["short"]["expiration_timestamp"] - (T + 300_000)) / 3_600_000
    baseline = point_from_payload(base_payload)
    latest = point_from_payload(latest_payload)
    baseline["candidate"]["current_questions"]["research_context"] = {"items": [{
        "id": "context.tmv_price_path",
        "producer_label_cn": "TMV 路径",
        "text_cn": "24h 来源",
        "readings": {"value": 0.1, "unit": "score"},
        "source_asset": "tmv_price_path",
        "source_ref": "fact_context.market_facts.volume_price.tmvf_24h",
        "source_clock_status": "RETAINED",
        "source_observed_ms": T,
    }]}
    latest["candidate"]["current_questions"]["research_context"] = {"items": [{
        "id": "context.tmv_price_path",
        "producer_label_cn": "TMV 路径",
        "text_cn": "48h 来源",
        "readings": {"value": 0.2, "unit": "score"},
        "source_asset": "tmv_price_path",
        "source_ref": "fact_context.market_facts.volume_price.tmvf_48h",
        "source_clock_status": "RETAINED",
        "source_observed_ms": T + 300_000,
    }]}

    output = followup.build_followup({
        "episode": {},
        "baseline": baseline,
        "latest": latest,
        "observations": [],
    }, now_ms=T + 300_000)

    row = row_by_key(output, "context.tmv_price_path")
    assert row["status"] == "not_precise"
    assert row["t0"]["value"]["value"] == 0.1
    assert row["t1"]["value"]["value"] == 0.2


def test_missing_t0_or_t1_is_not_zero_filled_and_risk_gap_is_row_local():
    no_risk = point_from_payload(payload_with_components(risk=False), point_id="no-risk")
    with_risk = point_from_payload(payload_with_components(risk=True), point_id="with-risk")
    appeared = followup.build_followup({"episode": {}, "baseline": no_risk, "latest": with_risk, "observations": []}, now_ms=T)
    disappeared = followup.build_followup({"episode": {}, "baseline": with_risk, "latest": no_risk, "observations": []}, now_ms=T)

    assert row_by_key(appeared, "risk.mu_ref_btc")["status"] == "new_fact"
    assert row_by_key(disappeared, "risk.mu_ref_btc")["status"] == "latest_missing"
    assert row_by_key(appeared, "quote.visible_credit_btc")["t0"]["value"] == pytest.approx(0.0004)


def test_missing_latest_source_packet_stays_readable_with_close_gap():
    baseline = point_from_payload(payload_with_components())
    latest_snapshot = copy.deepcopy(baseline["candidate"]["snapshot"])
    latest = {"point_id": "latest-source-missing", "candidate": {"snapshot": latest_snapshot}, "recorded_at_ms": T + 10}

    output = followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []}, now_ms=T + 10)

    assert output["latest"]["snapshot_id"] == latest_snapshot["snapshot_id"]
    assert output["overview"]["close_estimate"]["debit_btc"] is None
    assert "short_book_missing" in output["overview"]["close_estimate"]["gap_reasons"]


def test_wrong_locked_contract_is_rejected_as_not_comparable():
    baseline = point_from_payload(payload_with_components("put"))
    latest = point_from_payload(payload_with_components("call"))

    with pytest.raises(ValueError, match="locked_candidate_identity_mismatch"):
        followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []}, now_ms=T)


def test_public_output_does_not_leak_private_packet_or_provider_fields():
    payload = payload_with_components()
    point = point_from_payload(payload)
    point["candidate"]["local_path"] = r"C:\Users\Xu\secret.json"
    point["candidate"]["provider_reasoning"] = "private reasoning"
    detail = {
        "episode": {"id": "ep", "local_path": r"C:\tmp\episode.json"},
        "baseline": point,
        "latest": None,
        "observations": [{"provider_raw": "private", "local_path": r"C:\tmp\raw.json", "status": "kept"}],
    }

    output = followup.build_followup(detail, now_ms=T)
    body = json.dumps(output, ensure_ascii=False)

    assert "private reasoning" not in body
    assert "provider_raw" not in body
    assert "C:\\Users\\Xu" not in body
    assert "raw_payload" in body
    assert output["baseline"]["public_packet"]["raw_payload"]["market"]["short_book"]["instrument_name"] == payload["candidate"]["short_instrument"]
    assert output["process"]["other_events"][0]["status"] == "kept"

def test_process_uses_store_point_ids_and_keeps_gaps_separate():
    baseline = point_from_payload(payload_with_components(), point_id="local-t0")
    latest_payload = payload_with_components()
    latest_payload["market"]["valuation_ts_ms"] = T + 300_000
    latest_payload["market"]["received_ts_ms"] = T + 300_000
    latest_payload["market"]["short_book"]["timestamp"] = T + 300_000
    latest_payload["market"]["long_book"]["timestamp"] = T + 300_000
    latest_payload["risk"]["input_ts_ms"] = T + 300_000
    latest_payload["risk"]["feature_end_ts_ms"] = T + 240_000
    latest_payload["risk"]["horizon_hours"] = (latest_payload["metadata"]["short"]["expiration_timestamp"] - (T + 300_000)) / 3_600_000
    latest = point_from_payload(latest_payload, point_id="local-t1")

    output = followup.build_followup({
        "episode": {"id": "ep", "t0_baseline_point_id": "store-t0", "latest_point_id": "store-t1"},
        "baseline_decision_asof_ms": T,
        "latest_decision_asof_ms": T + 300_000,
        "intermediate_packets_replayed": False,
        "baseline": baseline,
        "latest": latest,
        "observations": [
            {"kind": "point", "point_id": "store-t0", "asof_ms": T},
            {"kind": "gap", "from_ms": T + 1, "to_ms": T + 299_999, "reason": "no_sample"},
            {"kind": "point", "point_id": "store-t0", "asof_ms": T},
            {"kind": "point", "point_id": "store-t1", "asof_ms": T + 300_000},
        ],
    }, now_ms=T + 300_010)

    assert output["baseline"]["point_id"] == "store-t0"
    assert output["latest"]["point_id"] == "store-t1"
    assert output["process"]["observed_count"] == 2
    assert output["process"]["max_gap_ms"] == 300_000
    assert output["process"]["index_range"]["baseline_decision_asof_ms"] == T
    assert output["process"]["index_range"]["latest_decision_asof_ms"] == T + 300_000
    assert output["process"]["index_range"]["intermediate_packets_replayed"] is False
    assert len(output["process"]["gaps"]) == 1


def test_public_output_does_not_leak_review_display_private_request_fields():
    point = point_from_payload(payload_with_components())
    point["candidate"]["review_display"] = {"current": {
        key: {"value": "PRIVATE_CANARY_" + key}
        for key in ("source_bindings", "request_http_body", "provider_request", "request_body", "request_payload")
    }}
    output = followup.build_followup({"episode": {}, "baseline": point, "latest": None, "observations": []}, now_ms=T)
    body = json.dumps(output, ensure_ascii=False)
    assert "PRIVATE_CANARY" not in body
    assert "provider_request" not in body


def test_missing_baseline_breakeven_gets_explicit_unknown_row_and_reference_result():
    payload = payload_with_components()
    for leg in ("short", "long"):
        payload["metadata"][leg]["taker_commission"] = 0.0003
        payload["metadata"][leg]["settlement_period"] = "day"
    point = point_from_payload(payload)
    point["candidate"]["snapshot"]["liability"]["breakeven_usd"] = None
    point["packet"]["snapshot"]["liability"]["breakeven_usd"] = None

    output = followup.build_followup({"episode": {}, "baseline": point, "latest": None, "observations": []}, now_ms=T)

    row = row_by_key(output, "liability.baseline_breakeven_unknown")
    assert row["status"] == "unknown"
    assert output["overview"]["baseline_breakeven_usd"] == []
    assert output["overview"]["reference_result"]["formula"].startswith("t0_entry_credit_btc")
    assert output["overview"]["reference_result"]["actual_trade_pnl_recorded"] is False
    assert row_by_key(output, "reference.reference_result_btc")["status"] == "reference_only"
    assert row_by_key(output, "reference.reference_result_btc")["t0"] is None
    assert row_by_key(output, "reference.reference_result_btc")["t1"]["value"] == output["overview"]["reference_result"]["reference_result_btc"]


def test_missing_source_clock_and_missing_values_do_not_create_precise_changes():
    meta = {key: "same" for key in ("semantic_key", "method", "product", "source", "window", "unit")}
    left = {"value": 1, "asof_ms": None, "meta": meta}
    right = {"value": 2, "asof_ms": T, "meta": meta}
    assert followup._change_cn(left, right)[1] == "not_precise"
    left["value"] = right["value"] = None
    assert followup._change_cn(left, right)[1] == "missing"


def test_process_uses_decision_clock_and_excludes_baseline_from_post_lock_count():
    output = followup._process({
        "episode": {"t0_baseline_point_id": "t0"},
        "observations": [
            {"kind": "point", "point_id": "t0", "decision_asof_ms": T, "recorded_at_ms": T + 10},
            {"kind": "point", "point_id": "t1", "decision_asof_ms": T + 100, "recorded_at_ms": T + 120},
            {"kind": "point", "point_id": "t2", "decision_asof_ms": T + 200, "recorded_at_ms": T + 220},
        ],
    })
    assert output["first_ms"] == T
    assert output["post_lock_observed_count"] == 2
    assert output["post_lock_span_ms"] == 100


def test_public_review_explanation_requires_current_accepted_strict_binding():
    point = point_from_payload(payload_with_components())
    candidate = point["candidate"]
    snapshot = candidate["snapshot"]
    accepted = {
        "status": "accepted",
        "summary_cn": "已接受解释",
        "display_binding": {
            "selector_status": "CURRENT",
            "same_input": True,
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": snapshot["snapshot_id"],
        },
    }
    candidate["review_display"] = {"current": accepted}
    assert followup.public_review_explanation(candidate)["summary_cn"] == "已接受解释"

    stale = copy.deepcopy(candidate)
    stale["review_display"]["current"]["display_binding"]["selector_status"] = "STALE"
    assert followup.public_review_explanation(stale) is None

    rejected = copy.deepcopy(candidate)
    rejected["review_display"]["current"]["status"] = "rejected"
    assert followup.public_review_explanation(rejected) is None

    mismatched = copy.deepcopy(candidate)
    mismatched["review_display"]["current"]["display_binding"]["snapshot_id"] = "other"
    assert followup.public_review_explanation(mismatched) is None


@pytest.mark.parametrize("status", [None, "CURRENT", "ATTACHED"])
def test_current_binding_without_accepted_status_is_not_an_accepted_review(status):
    candidate = point_from_payload(payload_with_components())["candidate"]
    candidate["review_display"] = {"current": {
        "summary_cn": "只绑定了输入，未被接受",
        "selector_status": "CURRENT",
        "display_binding": {
            "selector_status": "CURRENT", "same_input": True,
            "candidate_id": candidate["candidate_id"],
            "snapshot_id": candidate["snapshot"]["snapshot_id"],
        },
    }}
    if status is not None:
        candidate["review_display"]["current"]["status"] = status
    assert followup.public_review_explanation(candidate) is None


def test_real_initial_public_view_context_refs_keep_missing_source_clock_none():
    fixture = Path(".artifacts/locked_candidate_followup_20261008_01/initial_public_view.json")
    if not fixture.exists():
        pytest.skip("real locked candidate follow-up fixture not present")
    view = json.loads(fixture.read_text(encoding="utf-8-sig"))
    candidate = view["candidates"][0]
    asof = candidate["snapshot"]["market"]["valuation_ts_ms"]
    point = {"point_id": "local", "candidate": candidate, "packet": {}, "recorded_at_ms": asof}

    output = followup.build_followup({
        "episode": {"id": "ep", "t0_baseline_point_id": "store-real-t0"},
        "baseline": point,
        "latest": None,
        "observations": [
            {"kind": "point", "point_id": "store-real-t0", "asof_ms": asof},
            {"kind": "gap", "from_ms": asof + 1, "to_ms": asof + 10_000, "reason": "waiting"},
        ],
    }, now_ms=asof + 10_000)

    keys = {row["key"] for row in output["rows"]}
    assert {"context.latest_window", "context.tmv_price_path", "context.active_flow", "context.option_space", "context.macro", "context.routing_relations"}.issubset(keys)
    assert row_by_key(output, "context.active_flow")["t0"]["asof_ms"] is None
    assert output["baseline"]["point_id"] == "store-real-t0"
    assert "fact_context" in output["baseline"]["public_candidate"]["snapshot"]
    assert "data_identity" in output["baseline"]["public_candidate"]["snapshot"]
    assert output["process"]["observed_count"] == 1
    assert len(output["process"]["gaps"]) == 1


def _bound(payload, fee=None):
    snapshot = core.evaluate(payload)
    fee = fee or {"basis": "unknown", "gap_reasons": ["close_fee_unavailable"]}
    quote = core.reference_close_quote(snapshot["contract"], payload["market"], T, fee=fee)
    return quote, followup._close_upper_bound(snapshot["contract"], payload["market"], quote, fee)


def test_protection_leg_without_bid_gives_a_zero_valued_close_upper_bound():
    # 2026-10-15/16: near expiry the deep OTM protection leg had no bid at all.
    payload = payload_with_components()
    payload["market"]["long_book"]["bids"] = []
    fee = {"basis": "configured_assumption", "close_fee_btc": 0.00002, "covers": ["early_exit"]}
    quote, bound = _bound(payload, fee)
    assert quote["debit_btc"] is None and quote["quote_gap_reasons"] == ["long_depth_insufficient"]
    assert bound["basis"] == "long_bid_shortfall_valued_zero"
    assert bound["debit_upper_bound_btc"] == pytest.approx(0.0008)  # short ask, protection leg at 0
    assert bound["net_debit_upper_bound_after_close_fee_btc"] == pytest.approx(0.00082)
    assert bound["long_bid_filled_btc"] == 0 and bound["executable_or_filled"] is False


def test_partial_protection_bids_count_and_only_the_shortfall_is_zero():
    payload = payload_with_components()
    quantity = core.evaluate(payload)["contract"]["quantity_btc"]
    payload["market"]["long_book"]["bids"] = [[0.002, quantity / 2]]
    _, bound = _bound(payload)
    assert bound["long_sale_partial_btc"] == pytest.approx(0.002 * quantity / 2)
    assert bound["debit_upper_bound_btc"] == pytest.approx(0.0008 - 0.002 * quantity / 2)
    assert bound["net_debit_upper_bound_after_close_fee_btc"] is None  # no fee range known


@pytest.mark.parametrize("mutate", [
    lambda p: p["market"]["short_book"].update(asks=[]),
    lambda p: (p["market"]["long_book"].update(bids=[]), p["market"]["short_book"].update(timestamp=T - 6000)),
])
def test_no_upper_bound_when_any_other_gap_remains(mutate):
    payload = payload_with_components()
    mutate(payload)
    quote, bound = _bound(payload)
    assert quote["debit_btc"] is None and bound is None


def test_followup_reports_a_lower_bound_result_when_the_protection_leg_has_no_bid():
    # The close fee is unavailable for the same reason (no bids to sell into); the bound
    # carries the fee of its own trades: short bought back in full, protection leg unsold.
    base_payload = payload_with_components()
    latest_payload = payload_with_components()
    for payload in (base_payload, latest_payload):
        for leg in ("short", "long"):
            payload["metadata"][leg].update(taker_commission=0.0003, settlement_period="day")
    latest_payload["market"]["long_book"]["bids"] = []
    baseline = point_from_payload(base_payload, point_id="t0", recorded_at_ms=T + 10)
    latest = point_from_payload(latest_payload, point_id="t1", recorded_at_ms=T + 20)
    output = followup.build_followup({"episode": {"id": "ep"}, "baseline": baseline, "latest": latest, "observations": []},
                                     now_ms=T + 30)
    reference = output["overview"]["reference_result"]
    assert reference["reference_result_btc"] is None and reference["close_fee_btc"] is None
    short_ask = 0.0008 / core.evaluate(latest_payload)["contract"]["quantity_btc"]
    commission = latest_payload["metadata"]["short"]["taker_commission"]
    fee = min(commission, 0.125 * short_ask) * core.evaluate(latest_payload)["contract"]["quantity_btc"]
    assert reference["close_net_debit_upper_bound_btc"] == pytest.approx(0.0008 + fee)
    assert reference["reference_result_lower_bound_btc"] == pytest.approx(
        reference["entry_after_fee_btc"] - 0.0008 - fee)
    assert reference["bound_basis"] == "long_bid_shortfall_valued_zero"
    assert any("保守估计" in line for line in output["limitations_cn"])
    row = row_by_key(output, "reference.reference_result_btc")
    assert row["status"] == "unknown" and "不低于" in row["change_cn"]


def test_bound_fee_covers_the_partial_protection_sale():
    payload = payload_with_components()
    for leg in ("short", "long"):
        payload["metadata"][leg].update(taker_commission=0.0003, settlement_period="day")
    quantity = core.evaluate(payload)["contract"]["quantity_btc"]
    payload["market"]["long_book"]["bids"] = [[0.002, quantity / 2]]
    short_ask = 0.0008 / quantity
    expected = min(0.0003, 0.125 * short_ask) * quantity + min(0.0003, 0.125 * 0.002) * quantity / 2
    fee = followup._close_fee_bound({"quantity_btc": quantity}, payload["metadata"], payload["market"])
    assert fee == pytest.approx(expected)
    payload["market"]["long_book"]["bids"] = [["bad", 1]]
    assert followup._close_fee_bound({"quantity_btc": quantity}, payload["metadata"], payload["market"]) is None
