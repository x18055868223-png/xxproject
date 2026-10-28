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


# ── lock t0 without a composed WPEM layer (2026-10-15 .. 10-27) and this expiry's levels ──
from tools import astra_lock_levels_v1 as lock_levels  # noqa: E402
from tools import astra_wpem_product_review_v1 as wpem_review  # noqa: E402

MARKET_FACTS = {
    "volume_price": {"direction": "Bearish", "tmv_blend": -0.5, "tmv_state": "bearish_bias",
                     "micro_flow": {"combined": {"direction": "bearish", "data_ready": True, "score": -0.3}},
                     "funding": {"last_rate": 0.0001, "effect": "neutral"}},
    "skew": {"data_state": "OK", "rr_blend": -0.03},
    "gamma_regime": {"regime": "TRANSITION", "data_state": "OK"},
    "space_gate": {"state": "STRUCTURE_BLOCKED"},
    "macro_pressure": {"macro_score": -0.15, "data_status": "full_live"},
}


def _with_market_facts(point, facts=MARKET_FACTS):
    for snapshot in (point["candidate"]["snapshot"], point["packet"]["snapshot"]):
        snapshot["fact_context"]["market_facts"] = copy.deepcopy(facts)
    return point


def _never_composed(point):
    for key in ("current_questions", "wpem_summary", "wpem_evidence"):
        point["candidate"].pop(key, None)
    return point


def _native_items(facts=MARKET_FACTS):
    """Research-context items as WPEM writes them, with the active flow qualified as unqualified."""
    out = []
    for key, _, ids, asset, source_ref, reader in followup._CONTEXT_SPECS:
        item_id, item_asset = (ids[-1], asset + "_unqualified") if key == "context.active_flow" else (ids[0], asset)
        out.append({"id": item_id, "source_asset": item_asset, "source_ref": source_ref, "readings": reader(facts),
                    "text_cn": "原读数", "source_clock_status": "RETAINED", "source_observed_ms": T})
    return out


def test_never_composed_t0_recomputes_context_from_market_facts_and_flags_it():
    baseline = _never_composed(_with_market_facts(point_from_payload(payload_with_components(), point_id="t0")))
    latest = _with_market_facts(point_from_payload(payload_with_components(), point_id="t1"))
    latest["candidate"]["current_questions"]["research_context"] = {"items": _native_items()}
    output = followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []},
                                     now_ms=T, include_raw=False)
    for key in ("context.tmv_price_path", "context.funding", "context.skew", "context.active_flow",
                "context.option_space", "context.macro"):
        row = row_by_key(output, key)
        assert row["t0"]["compat_backfill_applied"] is True, key
        assert row["t0"]["backfill_source"] == "snapshot.fact_context.market_facts"
        assert "compat_backfill_applied" not in row["t1"]           # the native end stays native
        assert row["status"] == "unchanged", (key, row["status"], row["change_cn"])
    flow = row_by_key(output, "context.active_flow")
    assert flow["t0"]["value"]["combined"]["direction"] == "bearish"
    assert flow["t0"]["value"] == wpem_review._context_flow_readings(MARKET_FACTS)
    # rows only a composition produces: not generated at lock, never "appeared after lock"
    for key in ("opinion.q1.direct_refs", "opinion.q2.direct_refs"):
        row = row_by_key(output, key)
        assert row["t0"] is None and row["status"] == "not_recorded_at_lock" and "锁定时未生成" in row["change_cn"]
    assert all(r["status"] != "new_fact" for r in output["rows"] if r["group"] in {"context", "opinion"})


def test_recomputed_context_reports_a_content_change_without_a_source_clock():
    baseline = _never_composed(_with_market_facts(point_from_payload(payload_with_components(), point_id="t0")))
    moved = copy.deepcopy(MARKET_FACTS)
    moved["volume_price"]["micro_flow"]["combined"]["direction"] = "bullish"
    latest = _with_market_facts(point_from_payload(payload_with_components(), point_id="t1"), moved)
    latest["candidate"]["current_questions"]["research_context"] = {"items": _native_items(moved)}
    row = row_by_key(followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []},
                                             now_ms=T), "context.active_flow")
    assert row["status"] == "changed" and "来源时点未留存" in row["change_cn"]
    assert row["t0"]["asof_ms"] is None and row["t1"]["value"]["combined"]["direction"] == "bullish"


def test_native_active_flow_unqualified_item_fills_the_active_flow_row():
    baseline = _with_market_facts(point_from_payload(payload_with_components(), point_id="t0"))
    latest = _with_market_facts(point_from_payload(payload_with_components(), point_id="t1"))
    for point in (baseline, latest):
        point["candidate"]["current_questions"]["research_context"] = {"items": _native_items()}
    row = row_by_key(followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []},
                                             now_ms=T), "context.active_flow")
    assert row["t0"] and row["t1"] and "compat_backfill_applied" not in row["t0"]
    assert row["status"] == "same_clock"


def _levels(**walls):
    profile = {"expiry": "30OCT26", "computed_at_ms": T, "walls": walls}
    return lock_levels.compact(profile, {"max_pain_target_expiry": "30OCT26", "max_pain_price": 96000.0},
                               {"reference_pool": [{"role": "EFFECTIVE_FLIP", "raw_price": 97500.0}]}, now_ms=T)


def test_levels_rows_compare_lock_time_with_latest_and_measure_to_the_short_strike():
    baseline = point_from_payload(payload_with_components(), point_id="t0")
    latest = point_from_payload(payload_with_components(), point_id="t1")
    baseline["candidate"]["expiry_levels"] = _levels(pin=98000.0, gamma_put=97000.0)
    latest["candidate"]["expiry_levels"] = _levels(pin=99000.0, gamma_put=97000.0)
    output = followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []},
                                     now_ms=T + 1)
    rows = {r["key"]: r for r in output["rows"] if r["group"] == "levels"}
    assert set(rows) == {key for key, *_ in followup._LEVEL_SPECS}
    assert rows["levels.pin"]["t0"]["value"] == 98000.0 and rows["levels.pin"]["t1"]["value"] == 99000.0
    assert rows["levels.pin"]["status"] == "same_clock"       # both read from the same profile clock
    assert rows["levels.max_pain_this"]["t0"]["value"] == 96000.0
    strike = baseline["candidate"]["snapshot"]["contract"]["short_strike_usd"]
    assert rows["levels.flip"]["to_short_pct"]["t1"] == pytest.approx((97500.0 - strike) / strike * 100)
    assert rows["levels.oi_call"]["t0"]["value"] is None       # missing stays missing


def test_levels_absent_at_lock_are_not_recorded_and_absent_everywhere_add_no_rows():
    baseline = point_from_payload(payload_with_components(), point_id="t0")
    latest = point_from_payload(payload_with_components(), point_id="t1")
    plain = followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []}, now_ms=T)
    assert not [r for r in plain["rows"] if r["group"] == "levels"]
    latest["candidate"]["expiry_levels"] = _levels(pin=99000.0)
    output = followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []}, now_ms=T)
    row = row_by_key(output, "levels.pin")
    assert row["t0"] is None and row["t1"]["value"] == 99000.0 and row["status"] == "not_recorded_at_lock"


def test_series_metrics_carry_the_compact_levels_only_when_present():
    baseline = point_from_payload(payload_with_components(), point_id="t0")
    latest = point_from_payload(payload_with_components(), point_id="t1")
    assert "levels" not in followup.point_metrics(latest, baseline)
    latest["candidate"]["expiry_levels"] = _levels(pin=99000.0, gamma_put=97000.0)
    levels = followup.point_metrics(latest, baseline)["levels"]
    assert levels == {"pin": 99000.0, "gamma_put": 97000.0, "gamma_call": None, "oi_put": None, "oi_call": None,
                      "max_pain_this": 96000.0, "flip": 97500.0}


def _old_lock_with_sources(point, *, this_pain=False):
    """A t0 stored before the levels block: gexmonitor and the BTC MAP are still in its snapshot."""
    expiry = point["candidate"]["snapshot"]["contract"]["expiry_ms"]
    token = lock_levels.expiry_token(expiry)
    gex = {"magnet_price": 97000.0, "max_pain_target_expiry": token if this_pain else "01JAN99", "max_pain_price": 96500.0,
           "next_expiry": {"expiry": "31DEC98", "max_pain_price": 95000.0}, "fetched_at_ms": T}
    btc_map = {"cutoff_at_ms": T, "reference_pool": [{"role": "EFFECTIVE_FLIP", "raw_price": 97400.0},
                                                     {"role": "CP_STH", "raw_price": 90000.0}]}
    for snapshot in (point["candidate"]["snapshot"], point["packet"]["snapshot"]):
        snapshot["fact_context"]["market_facts"] = {"space_context": {"gex_info": copy.deepcopy(gex)}}
        snapshot["data_identity"]["btc_map"] = copy.deepcopy(btc_map)
    return point


def test_old_lock_recomputes_recoverable_levels_from_its_snapshot_and_flags_them():
    baseline = _old_lock_with_sources(point_from_payload(payload_with_components(), point_id="t0"))
    latest = point_from_payload(payload_with_components(), point_id="t1")
    latest["candidate"]["expiry_levels"] = _levels(pin=99000.0)
    output = followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []}, now_ms=T)
    rows = {r["key"]: r for r in output["rows"] if r["group"] == "levels"}
    for key, value, source in (("levels.flip", 97400.0, "snapshot.data_identity.btc_map"),
                               ("levels.cost_sth", 90000.0, "snapshot.data_identity.btc_map"),
                               ("levels.magnet", 97000.0, "snapshot.fact_context.market_facts.space_context.gex_info")):
        t0 = rows[key]["t0"]
        assert t0["value"] == value and t0["compat_backfill_applied"] is True and t0["backfill_source"] == source, key
        assert "compat_backfill_applied" not in (rows[key]["t1"] or {})
        assert rows[key]["status"] != "not_recorded_at_lock"
    assert rows["levels.flip"]["status"] == "changed"               # 97,400 recomputed vs 97,500 native
    assert rows["levels.flip"]["to_short_pct"]["t0"] is not None
    for key in ("levels.pin", "levels.gamma_put", "levels.oi_call"):      # the expiry profile was never saved
        assert rows[key]["t0"] is None and rows[key]["status"] == "not_recorded_at_lock"
        assert rows[key]["t0_missing_reason"] == "expiry_profile_not_saved" and "无法重算" in rows[key]["change_cn"]
    pain = rows["levels.max_pain_this"]                                     # gexmonitor covered other expiries only
    assert pain["t0"]["value"] is None and pain["status"] == "not_recorded_at_lock"
    assert pain["t0_missing_reason"] == "source_not_covering_expiry"
    covered = _old_lock_with_sources(point_from_payload(payload_with_components(), point_id="t0b"), this_pain=True)
    row = row_by_key(followup.build_followup({"episode": {}, "baseline": covered, "latest": latest, "observations": []},
                                             now_ms=T), "levels.max_pain_this")
    assert row["t0"]["value"] == 96500.0 and row["t0"]["compat_backfill_applied"] is True and "t0_missing_reason" not in row


def test_old_lock_without_level_sources_stays_not_recorded():
    baseline = point_from_payload(payload_with_components(), point_id="t0")
    latest = point_from_payload(payload_with_components(), point_id="t1")
    latest["candidate"]["expiry_levels"] = _levels(pin=99000.0)
    output = followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []}, now_ms=T)
    rows = [r for r in output["rows"] if r["group"] == "levels"]
    assert rows and all(r["t0"] is None and r["status"] == "not_recorded_at_lock" for r in rows)


def test_risk_status_missing_at_both_ends_adds_no_row_and_a_real_status_still_shows():
    plain = point_from_payload(payload_with_components(risk=False), point_id="a")
    output = followup.build_followup({"episode": {}, "baseline": plain, "latest": copy.deepcopy(plain), "observations": []}, now_ms=T)
    assert not [r for r in output["rows"] if r["key"] == "risk.status"]
    supported = point_from_payload(payload_with_components(risk=True), point_id="b")
    row = row_by_key(followup.build_followup({"episode": {}, "baseline": plain, "latest": supported, "observations": []},
                                             now_ms=T), "risk.status")
    assert row["t0"] is None and row["t1"]["value"] == "supported"


def _window(status):
    return {"same_candidate_window": {"coverage_summary": {"windows": [{"status": status}, {"status": status}]},
                                      "LevelDeltaPath": {"path": {"status": "NO_PATH_DUE_TO_WINDOW_GAP"}}},
            "evidence_routing": {"schema": "routing@1", "selection_summary": {"n": 1}}, "decision_asof_ms": T}


def test_routing_graph_and_an_uncovered_window_are_trace_only_rows():
    baseline = point_from_payload(payload_with_components(), point_id="t0")
    latest = point_from_payload(payload_with_components(), point_id="t1")
    for point in (baseline, latest):
        point["candidate"]["wpem_evidence"] = _window("INSUFFICIENT_WINDOW")
    output = followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []}, now_ms=T)
    assert row_by_key(output, "context.routing_relations")["display"] == "trace"
    assert row_by_key(output, "context.latest_window")["display"] == "trace"
    latest["candidate"]["wpem_evidence"] = _window("OK")
    output = followup.build_followup({"episode": {}, "baseline": baseline, "latest": latest, "observations": []}, now_ms=T)
    assert "display" not in row_by_key(output, "context.latest_window")
    assert row_by_key(output, "context.routing_relations")["display"] == "trace"
