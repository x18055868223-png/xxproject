import math

import pytest

from tools.astra_map_v24 import build_map


T = 1_790_323_200_000


def qualified_samples(observations):
    return [{"market": "Deribit", "price_basis": "DERIBIT_BTC_USD_INDEX",
             "coverage": "COMPLETE", **obs} for obs in observations]


def snapshot(side="put", *, mu=0.00018, fee_covers=None):
    if side == "put":
        ks, kl = 97000.0, 95000.0
        be = 0.1 * ks / (0.1 + 0.00028)
    else:
        ks, kl = 103000.0, 105000.0
        be = 0.1 * ks / (0.1 - 0.00028)
    margin = None if mu is None else 0.0003 - 0.00002 - mu
    return {
        "schema": "astra_underwriting_snapshot@2.0.0",
        "snapshot_id": f"snap-{side}-{mu}-{fee_covers}",
        "candidate_id": f"candidate-{side}",
        "contract": {
            "side": side,
            "short_strike_usd": ks,
            "long_strike_usd": kl,
            "quantity_btc": 0.1,
            "expiry_ms": T + 86_400_000,
            "settlement_currency": "BTC",
            "quote_currency": "BTC",
            "contract_size": 1.0,
        },
        "market": {"reference_price_usd": 100000.0, "valuation_ts_ms": T},
        "fee": {
            "amount_btc": 0.00002,
            "basis": "configured_assumption",
            "covers": fee_covers if fee_covers is not None else ["entry", "delivery"],
        },
        "economics": {
            "visible_credit_btc": 0.0003,
            "net_credit_after_fee_btc": 0.00028,
            "mu_ref_btc": mu,
            "reference_margin_btc": margin,
            "valuation_spot_usd": 100000.0,
        },
        "liability": {"breakeven_usd": [be]},
    }


def point(reference_id, family, role, price, **extra):
    base = {
        "reference_id": reference_id,
        "revision_id": "v1",
        "family": family,
        "role": role,
        "geometry": "POINT",
        "raw_price": price,
        "market": "Deribit",
        "quote_currency": "USD",
        "price_basis": "DERIBIT_BTC_USD_INDEX",
        "quality": "OK",
        "usage_decision": {"can_use": True},
        "known_at_ms": T,
        "active_from_ms": T,
        "source_family": family.lower(),
        "coordinate_qualification": "COMPARABLE",
    }
    base.update(extra)
    return base


def zone(reference_id, low, high, **extra):
    base = {
        "reference_id": reference_id,
        "revision_id": "v1",
        "family": "TRADED_ACCEPTANCE",
        "role": "KPF_BASIN",
        "geometry": "OBSERVED_ZONE",
        "raw_low": low,
        "raw_high": high,
        "market": "Deribit",
        "quote_currency": "USD",
        "price_basis": "DERIBIT_BTC_USD_INDEX",
        "quality": "OK",
        "usage_decision": {"can_use": True},
        "known_at_ms": T,
        "active_from_ms": T,
        "source_family": "kpf",
        "coordinate_qualification": "COMPARABLE",
    }
    base.update(extra)
    return base


def by_id(map_obj, reference_id):
    return next(item for item in map_obj["reference_pool"] if item["reference_id"] == reference_id)


def liability_row(map_obj, reference_id):
    return next(item for item in map_obj["policy_link"]["region_liability_full_rows"] if item["region_id"] == reference_id)


def test_M01_default_reference_selection_is_fixed_and_limited():
    refs = [
        point("put", "OPTION_STRUCTURE", "PUT_REFERENCE", 97000),
        point("call", "OPTION_STRUCTURE", "CALL_REFERENCE", 103000),
        point("flip", "OPTION_STRUCTURE", "EFFECTIVE_FLIP", 98500),
        point("pin", "OPTION_STRUCTURE", "PIN", 100000),
        zone("current-basin", 99900, 100100),
        zone("near-basin", 96600, 96900),
        zone("far-basin", 94000, 94500),
        point("cp-sth", "ONCHAIN_COST", "CP_STH", 92000),
        point("cp-all", "ONCHAIN_COST", "CP_ALL", 80000),
    ]
    m = build_map(None, now_ms=T, snapshot=snapshot(), references=refs)
    assert len(m["display_reference_ids"]) <= 6
    assert {"put", "call", "flip", "current-basin", "near-basin", "cp-sth"}.issubset(m["display_reference_ids"])
    assert "pin" not in m["display_reference_ids"]
    assert "cp-all" not in m["display_reference_ids"]


def test_M02_display_only_kpf_is_not_strict_geometry_or_contract_mapping():
    refs = [
        zone("real-zone", 99885, 99905),
        {
            **zone("display-band", 99750, 100250),
            "geometry": "DISPLAY_ONLY",
            "raw_low": None,
            "raw_high": None,
            "display_center": 100000,
            "display_band": [99750, 100250],
        },
    ]
    m = build_map(None, now_ms=T, snapshot=snapshot(), references=refs)
    display_ref = by_id(m, "display-band")
    assert display_ref["coordinate_qualification"] == "DISPLAY_ONLY"
    assert "display_only_no_strict_geometry" in display_ref["limitations"]
    row = liability_row(m, "display-band")
    assert row["terminal_payout_range"] is None
    assert "no_contract_comparable_coordinate" in row["limitations"]
    assert any(item["relation"] == "NOT_STRICT" for item in m["co_location"])


def test_M03_rounded_display_equality_does_not_create_colocation():
    refs = [
        zone("kpf-raw", 99885, 99905, display_center=100000),
        point("cost-point", "ONCHAIN_COST", "CP_STH", 100100, display_center=100000),
    ]
    m = build_map(None, now_ms=T, snapshot=snapshot(), references=refs)
    pair = m["co_location"][0]
    assert pair["relation"] == "SEPARATE"
    assert pair["raw_distance"] == pytest.approx(195)


def test_M04_same_source_structure_references_are_not_independent_confirmation():
    refs = [
        point("anchor", "OPTION_STRUCTURE", "PUT_REFERENCE", 97000, source_family="gex"),
        point("flip", "OPTION_STRUCTURE", "EFFECTIVE_FLIP", 97000, source_family="gex"),
    ]
    m = build_map(None, now_ms=T, snapshot=snapshot(), references=refs)
    pair = m["co_location"][0]
    assert pair["relation"] == "BOUNDARY_TOUCH"
    assert pair["raw_distance"] == pytest.approx(0)
    assert pair["independent_evidence"] is False
    assert pair["evidence_note"] == "same_source_family_not_independent"


def test_M05_usdt_reference_without_conversion_stays_nominal_only():
    refs = [
        zone("deribit-zone", 96600, 96900),
        zone("binance-zone", 96600, 96900, market="Binance", quote_currency="USDT", price_basis="BTCUSDT_PERP"),
    ]
    m = build_map(None, now_ms=T, snapshot=snapshot(), references=refs)
    row = liability_row(m, "binance-zone")
    assert row["coordinate_qualification"] == "NOMINAL_ONLY"
    assert row["relation_to_short_long_be"] is None
    assert row["distance_to_short_usd"] is None
    assert row["terminal_scenario_basis"] == "nominal_number_if_official_settlement_equaled_it"
    assert "nominal_scenario_not_precise_contract_mapping" in row["limitations"]
    assert any(item["relation"] == "NOT_COMPARABLE" for item in m["co_location"])


def test_M05b_usd_reference_without_explicit_basis_clock_is_not_strict():
    refs = [
        zone("qualified-zone", 96600, 96900),
        {
            "reference_id": "unknown-usd",
            "revision_id": "v1",
            "family": "ONCHAIN_COST",
            "role": "CP_STH",
            "geometry": "POINT",
            "raw_price": 96900,
            "market": "GEX",
            "quote_currency": "USD",
            "quality": "OK",
            "usage_decision": {"can_use": True},
            "source_family": "gex",
        },
    ]
    m = build_map(None, now_ms=T, snapshot=snapshot(), references=refs)
    ref = by_id(m, "unknown-usd")
    assert ref["coordinate_qualification"] == "UNKNOWN"
    row = liability_row(m, "unknown-usd")
    assert row["terminal_payout_range"] is None
    assert row["relation_to_short_long_be"] is None
    assert any(item["relation"] == "NOT_COMPARABLE" for item in m["co_location"])


def test_M06_old_kpf_target_does_not_infer_current_acceptance_basin():
    refs = [point("old-kpf-up", "TRADED_ACCEPTANCE", "KPF_TARGET", 101000)]
    m = build_map(None, now_ms=T, snapshot=snapshot(), references=refs)
    ref = by_id(m, "old-kpf-up")
    assert ref["geometry"] == "POINT"
    assert "acceptance_basin_unknown" in ref["limitations"]
    row = liability_row(m, "old-kpf-up")
    assert "acceptance_basin_unknown" in row["limitations"]


def test_R01_reference_revision_change_is_not_a_price_break():
    prior = {"segments": {"z": {"reference_id": "z", "revision_id": "old", "last_position": "ABOVE"}}}
    m = build_map(None, now_ms=T, references=[zone("z", 100, 110, revision_id="new")], prior_state=prior)
    types = [event["event_type"] for event in m["response_events"]]
    assert types == ["REFERENCE_REVISION_CHANGED"]


def test_R02_gap_then_other_side_keeps_interval_unknown():
    prior = {"segments": {"z": {"reference_id": "z", "revision_id": "v1", "last_position": "ABOVE"}}}
    observations = [
        {"reference_id": "z", "revision_id": "v1", "known_at_ms": T, "observed_at_ms": T, "coverage": "GAP"},
        {"reference_id": "z", "revision_id": "v1", "known_at_ms": T + 1, "observed_at_ms": T + 1, "price": 90},
    ]
    m = build_map(None, now_ms=T + 1, references=[zone("z", 100, 110)], observations=qualified_samples(observations), prior_state=prior)
    types = [event["event_type"] for event in m["response_events"]]
    assert "INTERVAL_UNKNOWN" in types
    assert "SAMPLED_CROSSING_BETWEEN_SAMPLES" not in types


def test_R03_ohlc_crossing_only_reports_range_intersects():
    observations = [{
        "reference_id": "z", "revision_id": "v1", "known_at_ms": T,
        "observed_at_ms": T, "method": "OHLC", "low": 95, "high": 115, "close": 112,
    }]
    m = build_map(None, now_ms=T, references=[zone("z", 100, 110)], observations=qualified_samples(observations))
    assert any(event["event_type"] == "RANGE_INTERSECTS" for event in m["response_events"])
    assert all("BREAK" not in event["event_type"] for event in m["response_events"])


def test_R03b_observation_market_mismatch_does_not_create_forward_response():
    ref = zone("z", 100, 110, market="Binance", quote_currency="USDT", price_basis="BTCUSDT_PERP")
    observations = [{
        "reference_id": "z", "revision_id": "v1", "known_at_ms": T,
        "observed_at_ms": T, "price": 105, "market": "Deribit", "price_basis": "DERIBIT_BTC_USD_INDEX",
    }]
    m = build_map(None, now_ms=T, references=[ref], observations=observations)
    assert m["response_events"][0]["event_type"] == "OBSERVATION_PRICE_BASIS_MISMATCH"


def test_R04_observation_before_region_active_is_post_hoc_only():
    observations = [{"reference_id": "z", "revision_id": "v1", "known_at_ms": T, "observed_at_ms": T - 10, "price": 105}]
    m = build_map(None, now_ms=T, references=[zone("z", 100, 110, active_from_ms=T)], observations=qualified_samples(observations))
    assert m["response_events"][0]["event_type"] == "POST_HOC_BACKGROUND_ONLY"


def test_R05_reclaim_and_leave_events_are_both_retained():
    observations = [
        {"reference_id": "z", "revision_id": "v1", "known_at_ms": T, "observed_at_ms": T, "price": 120},
        {"reference_id": "z", "revision_id": "v1", "known_at_ms": T + 1, "observed_at_ms": T + 1, "price": 105},
        {"reference_id": "z", "revision_id": "v1", "known_at_ms": T + 2, "observed_at_ms": T + 2, "price": 95},
        {"reference_id": "z", "revision_id": "v1", "known_at_ms": T + 3, "observed_at_ms": T + 3, "price": 105},
    ]
    m = build_map(None, now_ms=T + 3, references=[zone("z", 100, 110)], observations=qualified_samples(observations))
    types = [event["event_type"] for event in m["response_events"]]
    assert types.count("ENTERED_REFERENCE") == 2
    assert "LEFT_REFERENCE" in types


def test_reference_not_yet_known_cannot_create_forward_response():
    ref = zone("z", 100, 110, active_from_ms=T - 100, known_at_ms=T)
    obs = qualified_samples([{"reference_id": "z", "observed_at_ms": T - 10, "known_at_ms": T, "price": 105}])
    result = build_map(None, now_ms=T, references=[ref], observations=obs)
    assert result["response_events"][0]["event_type"] == "POST_HOC_BACKGROUND_ONLY"


def test_repeated_same_side_sample_does_not_push_out_response_history():
    ref = zone("z", 100, 110)
    observations = qualified_samples([{"reference_id": "z", "observed_at_ms": T + i,
                                       "known_at_ms": T + i, "price": 120} for i in range(100)])
    result = build_map(None, now_ms=T + 100, references=[ref], observations=observations)
    assert len(result["response_events"]) == 1
    replay = build_map(None, now_ms=T + 100, references=[ref], observations=observations,
                       prior_state=result["response_state"])
    assert replay["response_events"] == result["response_events"]


def test_unknown_sample_basis_and_coverage_are_not_continuous_observations():
    ref = zone("z", 100, 110)
    result = build_map(None, now_ms=T, references=[ref], observations=[
        {"reference_id": "z", "observed_at_ms": T, "known_at_ms": T, "price": 105}])
    assert result["response_events"][0]["event_type"] == "OBSERVATION_PRICE_BASIS_MISMATCH"


def test_future_observation_and_missing_usage_cannot_be_current():
    bad = zone("late", 100, 110, observation_end_ms=T + 1)
    unavailable = zone("missing", 100, 110, usage_decision=None)
    result = build_map(None, now_ms=T, references=[bad, unavailable])
    assert result["display_reference_ids"] == []
    result = build_map(None, now_ms=T, references=[zone("z", 100, 110)], observations=qualified_samples([
        {"reference_id": "z", "known_at_ms": T, "observed_at_ms": T + 1, "price": 105}]))
    assert result["response_events"] == []


def test_P01_no_candidate_keeps_policy_layer_empty():
    m = build_map(None, now_ms=T, references=[zone("z", 96600, 96900)])
    assert m["policy_link"]["candidate_id"] is None
    assert m["policy_link"]["region_liability_rows"] == []


def test_P01b_current_price_argument_selects_nearest_kpf_without_candidate():
    refs = [zone("far", 94000, 94500), zone("current", 99900, 100100), zone("near", 96600, 96900)]
    m = build_map(
        None,
        now_ms=T,
        current_price_usd=100000,
        current_price_basis="DERIBIT_BTC_USD_INDEX",
        references=refs,
    )
    assert m["current_price_usd"] == 100000
    assert m["price_basis"] == "DERIBIT_BTC_USD_INDEX"
    assert "current" in m["display_reference_ids"]
    assert m["policy_link"]["candidate_id"] is None


def test_background_preserves_summary_cn_metrics_and_source_records_for_manifest():
    record = {
        "schema": "astra_data_record@2.3.0",
        "record_id": "record-1",
        "identity": {"product_id": "demo.product"},
        "time": {"first_seen_at_ms": T},
        "content": {"values": {"x": 1}},
    }
    background = {
        "source_records": [record],
        "allocation": {
            "status": "partial",
            "summary": "allocation summary",
            "summary_cn": "配置摘要",
            "metrics": {"f3": 300},
            "cutoff_at_ms": T,
            "missing": ["etf_day_0"],
            "fact_state": "PARTIAL",
            "source_record_ids": ["record-1"],
        },
    }
    m = build_map(None, now_ms=T, background=background)
    row = m["background"]["allocation"]
    assert row["summary_cn"] == "配置摘要"
    assert row["metrics"] == {"f3": 300}
    assert row["cutoff_at_ms"] == T
    assert row["missing"] == ["etf_day_0"]
    assert row["fact_state"] == "PARTIAL"
    assert "source_records" not in row
    assert m["source_manifest"]["source_records"] == [{"record_id": "record-1"}]


def test_P02_region_crossing_short_be_and_protection_is_segmented():
    m = build_map(None, now_ms=T, snapshot=snapshot(), references=[zone("wide", 94600, 97100)])
    row = liability_row(m, "wide")
    assert {"before_payout", "partial_payout", "beyond_protection"}.issubset(row["relation_to_short_long_be"])
    assert row["contains_near_breakeven"] is True
    assert row["terminal_payout_range"]["max_btc"] > row["terminal_payout_range"]["min_btc"]


def test_P03_inverse_call_region_includes_protection_strike_peak():
    m = build_map(None, now_ms=T, snapshot=snapshot("call"), references=[zone("call-zone", 104000, 106000)])
    row = liability_row(m, "call-zone")
    assert row["terminal_payout_range"]["max_btc"] == pytest.approx(0.1 * 2000 / 105000)
    assert any(item["settlement_usd"] == 105000 for item in row["terminal_payout_points"])


def test_P04_inverse_put_below_protection_keeps_btc_tail_shape():
    m = build_map(None, now_ms=T, snapshot=snapshot(), references=[zone("tail", 94000, 94500)])
    row = liability_row(m, "tail")
    values = row["terminal_payout_points"]
    low_point = next(item for item in values if item["settlement_usd"] == 94000)
    high_point = next(item for item in values if item["settlement_usd"] == 94500)
    assert low_point["payout_btc"] > high_point["payout_btc"]


def test_P05_missing_mu_leaves_M_empty_but_keeps_contract_scenarios():
    m = build_map(None, now_ms=T, snapshot=snapshot(mu=None), references=[zone("partial", 96600, 96900)])
    assert m["policy_link"]["M_ref_btc"] is None
    assert liability_row(m, "partial")["terminal_payout_range"]["max_btc"] is not None


def test_P06_entry_only_fee_scope_is_not_labeled_full_actual_net():
    m = build_map(None, now_ms=T, snapshot=snapshot(fee_covers=["entry"]), references=[zone("partial", 96600, 96900)])
    title = liability_row(m, "partial")["cost_scope"]["title"]
    assert "not_full_actual_net" in title


def test_P07_touch_event_does_not_create_exit_cashflow():
    observations = [{"reference_id": "partial", "revision_id": "v1", "known_at_ms": T, "observed_at_ms": T, "price": 96700}]
    m = build_map(None, now_ms=T, snapshot=snapshot(), references=[zone("partial", 96600, 96900)], observations=observations)
    row = liability_row(m, "partial")
    assert row["terminal_scenario_basis"] == "official_settlement_inside_contract_comparable_coordinate"
    assert all("EXIT" not in event["event_type"] for event in m["response_events"])


def test_P08_spec_examples_match_put_rows_and_call_inflection():
    refs = [zone("A", 98800, 99100), zone("B", 96600, 96900), zone("C", 94000, 94500)]
    m = build_map(None, now_ms=T, snapshot=snapshot(), references=refs)
    a, b, c = liability_row(m, "A"), liability_row(m, "B"), liability_row(m, "C")
    assert a["terminal_payout_range"] == pytest.approx({"min_btc": 0.0, "max_btc": 0.0})
    assert a["static_net_range"] == pytest.approx({"min_btc": 0.00028, "max_btc": 0.00028})
    assert b["terminal_payout_range"]["min_btc"] == pytest.approx(0.00010320, rel=1e-5)
    assert b["terminal_payout_range"]["max_btc"] == pytest.approx(0.00041408, rel=1e-5)
    assert b["static_net_range"]["min_btc"] == pytest.approx(-0.00013408, rel=1e-5)
    assert b["static_net_range"]["max_btc"] == pytest.approx(0.00017680, rel=1e-5)
    assert c["terminal_payout_range"]["min_btc"] == pytest.approx(0.00211640, rel=1e-5)
    assert c["terminal_payout_range"]["max_btc"] == pytest.approx(0.00212766, rel=1e-5)
    call = build_map(None, now_ms=T, snapshot=snapshot("call"), references=[zone("call", 104000, 106000)])
    assert liability_row(call, "call")["terminal_payout_range"]["max_btc"] == pytest.approx(0.00190476, rel=1e-5)
