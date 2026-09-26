import json
import math

import pytest

from tools import astra_underwriting_fact_context_v20 as f
from tools import astra_underwriting_v20 as u
from test_astra_underwriting_v20 import T, sample


def fmz_row(ts=T - 1000, observed=T - 1500, mode="live_public_read_only"):
    return {"schema_name": "EvaluationSnapshot", "schema_version": "1.6.2",
            "decision": {"ts_ms": ts, "demo_version": "2.0.0", "symbol": "BTC",
                         "runtime_facts": {"runtime_mode": mode,
                                           "current_price": 100010,
                                           "current_price_source": "binance_spot",
                                           "current_price_observed_ms": observed,
                                           "source_quality": {"binance": "OK"}}},
            "factor_snapshot": {"anchor": {"effective_flip_point": 99000},
                                "flow": {"direction": "DOWN", "tmv_blend": -0.2},
                                "neutral_repair_signal": {"state": "IDLE", "is_active": False}}}


def rich_fmz_row():
    row = fmz_row()
    row["decision"]["runtime_facts"]["source_quality"] = {
        "binance": "OK",
        "deribit": "OK",
    }
    row["factor_snapshot"] = {
        "anchor": {
            "ready": True,
            "effective_flip_point": 99000,
            "raw_flip_point": 98950,
            "gex_source_ts_ms": T - 3000,
            "gex_freshness": "FRESH",
            "freshness": "FRESH",
            "normalized_deviation": 0.24,
            "anchor_gravity_ref_score": 74,
            "anchor_gravity_ref_label": "Valid",
            "anchor_gravity_warming": False,
            "anchor_gravity_window_count": 80,
        },
        "flow": {
            "direction": "DOWN",
            "market_state": "TRENDING_DOWN",
            "tmv_blend": -0.42,
            "tmv_state": "SUPPORTED",
            "window_conflict": True,
            "tmvf_24h": {
                "label": "24h",
                "data_ready": True,
                "tmv_core": -0.47,
                "tmv_final": -0.5,
                "final_state": "directional",
                "funding_adjustment": -0.03,
                "funding_effect": "confirming",
                "funding_state": "neutral",
                "core": {
                    "label": "24h",
                    "horizon_hours": 24,
                    "data_ready": True,
                    "tmv_core": -0.47,
                    "tmv_core_raw": -0.47,
                    "state": "directional",
                    "trend_direction": -1,
                    "trend_strength_pct": 0.62,
                    "price": 100020,
                    "kline_open_time": T - 3600_000,
                },
                "funding": {
                    "horizon_hours": 24,
                    "funding_cum": 0.00008,
                    "funding_count": 3,
                    "funding_norm": 0.18,
                    "funding_state": "neutral",
                    "last_funding_rate": 0.00003,
                    "funding_interval_hours": 8,
                    "window_start_time": T - 26 * 3600_000,
                    "window_end_time": T - 2 * 3600_000,
                    "age_ms": 2 * 3600_000,
                    "data_ready": True,
                },
            },
            "tmvf_48h": {
                "label": "48h",
                "data_ready": True,
                "tmv_core": 0.12,
                "tmv_final": 0.1,
                "final_state": "neutral_to_bullish",
                "funding_adjustment": -0.02,
                "funding_effect": "neutral",
                "funding_state": "neutral",
                "core": {
                    "label": "48h",
                    "horizon_hours": 48,
                    "data_ready": True,
                    "tmv_core": 0.12,
                    "tmv_core_raw": 0.12,
                    "state": "neutral_to_bullish",
                    "trend_direction": 1,
                    "trend_strength_pct": 0.21,
                    "price": 100020,
                    "kline_open_time": T - 3600_000,
                },
                "funding": {
                    "horizon_hours": 48,
                    "funding_cum": 0.00011,
                    "funding_count": 6,
                    "funding_norm": 0.14,
                    "funding_state": "neutral",
                    "last_funding_rate": 0.00003,
                    "funding_interval_hours": 8,
                    "window_start_time": T - 50 * 3600_000,
                    "window_end_time": T - 2 * 3600_000,
                    "age_ms": 2 * 3600_000,
                    "data_ready": True,
                },
            },
            "micro_flow_effect": "SELL_CONFIRMS_DOWN",
            "micro_flow": {
                "fast_4h": {
                    "horizon_hours": 4,
                    "data_ready": True,
                    "bar_count": 16,
                    "coverage_hours": 3.75,
                    "coverage_frac": 0.94,
                    "momentum_return_pct": -0.8,
                    "momentum_norm": -0.8,
                    "cvd_norm": -0.7,
                    "cvd_sum": -210,
                    "cvd_unit": "BTC",
                    "cvd_per_bar": -13.125,
                    "score": -0.73,
                    "state": "directional",
                    "confidence": 0.94,
                    "direction": "bearish",
                    "window_role": "fast",
                },
                "slow_12h": {
                    "horizon_hours": 12,
                    "data_ready": True,
                    "bar_count": 48,
                    "coverage_hours": 11.75,
                    "coverage_frac": 0.98,
                    "momentum_return_pct": -1.1,
                    "momentum_norm": -1.0,
                    "cvd_norm": -0.4,
                    "cvd_sum": -390,
                    "cvd_unit": "BTC",
                    "cvd_per_bar": -8.125,
                    "score": -0.79,
                    "state": "directional",
                    "confidence": 0.98,
                    "direction": "bearish",
                    "window_role": "slow",
                },
                "combined": {"score": -0.77, "state": "directional",
                             "direction": "bearish", "ready_horizons": ["4h", "12h"],
                             "max_coverage_hours": 11.75, "data_ready": True},
            },
            "tmvf_funding_effect": "BASELINE",
            "tmvf_funding_semantics": {"edb_vote_allowed": False},
            "last_funding_rate": 0.00003,
            "funding_count": 3,
            "mark_price": 100020,
            "index_price": 100000,
            "kline_count": 96,
        },
        "macro_pressure": {
            "macro_score": 0.31,
            "macro_regime": "Mild Headwind",
            "data_status": "live",
            "macro_data_confidence": 0.8,
            "flags": ["DXY_UP"],
            "blocking_flags": [],
            "legacy_blocking_flags": [],
            "macro_shock": {"block": False},
            "component_scores": {"DXY": 0.12, "VOLQ": 0.19},
            "components": [
                {"component": "DXY", "key": "DXY", "source_symbol": "DX-Y.NYB",
                 "source_status": "OK", "current_close": 105.2,
                 "reference_close": 104.7, "current_ts_ms": T - 86_400_000,
                 "reference_ts_ms": T - 4 * 86_400_000,
                 "change_pct_3d": 0.4775549188156438,
                 "scoring_value": 0.4775549188156438,
                 "scoring_unit": "pct", "scoring_bps": 47.75549188156438,
                 "tier": "watch", "tier_cn": "观察",
                 "component_score": 0.12, "impact": "DXY_UP"},
                {"component": "US10Y", "key": "US10Y", "source_symbol": "^TNX",
                 "source_status": "OK", "current_close": 4.36,
                 "reference_close": 4.29, "current_ts_ms": T - 86_400_000,
                 "reference_ts_ms": T - 4 * 86_400_000,
                 "change_pct_3d": 1.6317016317016316,
                 "scoring_value": 7.000000000000028,
                 "scoring_unit": "bps", "scoring_bps": 7.000000000000028,
                 "tier": "watch", "tier_cn": "观察",
                 "component_score": 0.19, "impact": "US10Y_UP"},
            ],
            "macro_components_cn": "DXY OK",
            "reason_codes": [],
        },
        "m_die": {"state": "QUIET", "score": 0.2, "reason_codes": []},
        "neutral_repair_signal": {"state": "IDLE", "is_active": False},
        "skew": {"data_state": "OK", "rr_blend": -0.1, "vote": -0.05, "vote_confidence": 0.4},
        "gamma_regime": {
            "regime": "POSITIVE_GAMMA_PINNING",
            "regime_strength": 0.6,
            "flip_point": 99000,
            "asset_price": 100010,
            "distance_to_flip_pct": 1.0,
            "net_gex_sign": "POSITIVE",
            "net_gamma_notional": 2_500_000,
            "gex_info_market_state": "positive_gamma",
            "gex_info_agrees": True,
            "max_gamma_strike": 100000,
            "pin": {"pin_strike": 100000},
            "gate_action": "NEUTRAL",
            "confidence_multiplier": 1.0,
            "spatial_vote": 0.2,
            "spatial_weight": 0.5,
            "veto": False,
            "data_state": "OK",
            "reason_codes": [],
        },
        "gex_info": {
            "asset": "BTC",
            "quality": "OK",
            "data_state": "OK",
            "availability": "ready",
            "stale": False,
            "market_state": "positive_gamma",
            "total_net_gex": 2_500_000,
            "observed_at_ms": T - 3000,
        },
        "space_gate": {
            "state": "STRUCTURE_ELIGIBLE",
            "observed_at_ms": T - 1200,
            "since_ts_ms": T - 1200,
            "previous_state": "STRUCTURE_BLOCKED",
            "last_transition_ts_ms": T - 1200,
            "reason_codes": [],
            "anchor": {"state": "Valid", "score": 74, "source_ts_ms": T - 3000, "freshness": "FRESH", "ok": True},
            "net_gamma": {"value": 2_500_000, "unit": "USD", "sign": "POSITIVE",
                          "source": "gexmonitorapi.gex_board.total_net_gex",
                          "source_ts_ms": T - 3000, "valid": True,
                          "transition": False, "conflict": False, "ok": True},
        },
        "edb": {
            "edb_score": -0.35,
            "edb_score_raw": -0.38,
            "agreement": 0.74,
            "coverage": 0.81,
            "confidence": 39,
            "calibration_state": "UNCALIBRATED",
            "lean": "MILD_BEARISH",
            "side_hint": "PUT_SIDE_CONTEXT",
            "support_label": "WEAK_SUPPORT",
            "next_action": "AUDIT_ONLY",
            "conflict_level": "MEDIUM",
            "ggr_gate": {"regime": "POSITIVE_GAMMA_PINNING", "multiplier": 1.0, "veto": False},
            "veto_reason": None,
            "confidence_decomposition": {"strength": 0.47},
            "reason_codes": ["LOW_CONFIDENCE"],
            "summary_cn": "量价偏空，但置信度未校准。",
            "evidence": [
                {"key": "TMV", "vote": -0.8, "weight": 1.0, "eff_weight": 0.8,
                 "info": 0.8, "participation_status": "ACTIVE",
                 "detail": {"direction": "DOWN", "tmv_blend": -0.42}},
                {"key": "GGR_SPATIAL", "vote": 0.2, "weight": 0.0,
                 "participation_status": "GATE_ONLY",
                 "detail": {"regime": "POSITIVE_GAMMA_PINNING"}},
            ],
        },
        "strategy_recommendation": {
            "signal": "AUDIT_ONLY",
            "strategy_code": None,
            "strategy_type": "UNDERWRITING_CONTEXT",
            "summary": "manual audit only",
            "selection_reason": "edb_uncalibrated",
            "order_layer": "external_manual_only",
        },
    }
    return row


def test_existing_fmz_tick_is_selected_without_nr_event(tmp_path):
    path = tmp_path / "snapshots.jsonl"
    past = fmz_row()
    future = fmz_row(ts=T + 1)
    path.write_text(json.dumps(past) + "\n" + json.dumps(future) + "\n", encoding="utf-8")
    context = f.latest_from_jsonl(path, T)
    assert context["status"] == "current"
    assert context["snapshot_ts_ms"] == T - 1000
    assert context["market_facts"]["neutral_repair"]["state"] == "IDLE"
    assert context["current_price_source"] == "binance_spot"
    assert len(context["source_hash"]) == 64
    payload = sample(risk=False)
    payload["fmz_fact_context"] = context
    snap = u.evaluate(payload)
    assert snap["fact_context"]["source_hash"] == context["source_hash"]
    assert snap["economics"]["reference_margin_btc"] is None


def test_stale_offline_and_future_fact_guards(tmp_path):
    stale = f.from_evaluation_snapshot(fmz_row(ts=T - 400_000), T, source_hash="a" * 64)
    assert stale["status"] == "background_only"
    assert "fmz_snapshot_stale" in stale["gap_reasons"]
    offline = f.from_evaluation_snapshot(fmz_row(mode="offline_fixture"), T, source_hash="b" * 64)
    assert "fmz_runtime_not_live_public" in offline["gap_reasons"]
    with pytest.raises(ValueError, match="future FMZ"):
        f.from_evaluation_snapshot(fmz_row(ts=T + 1), T, source_hash="c" * 64)
    missing = f.latest_from_jsonl(tmp_path / "missing.jsonl", T)
    assert missing["status"] == "missing"


def test_non_btc_or_unidentified_fmz_cannot_be_current():
    for symbol in ("ETH", None):
        row = fmz_row()
        row["decision"]["symbol"] = symbol
        context = f.from_evaluation_snapshot(row, T, source_hash="f" * 64)
        assert context["status"] == "background_only"
        assert "fmz_underlying_missing_or_mismatch" in context["gap_reasons"]


def test_nonfixture_risk_requires_same_current_fmz_source():
    payload = sample()
    payload["risk"]["source_kind"] = "frozen_model"
    snap = u.evaluate(payload)
    assert snap["risk"]["status"] == "unusable"
    assert snap["economics"]["reference_margin_btc"] is None
    assert "risk_fmz_fact_binding_missing_or_stale" in snap["gap_reasons"]
    context = f.from_evaluation_snapshot(fmz_row(), T, source_hash="d" * 64)
    payload["fmz_fact_context"] = context
    payload["risk"]["source_fact_hash"] = context["source_hash"]
    snap = u.evaluate(payload)
    assert snap["risk"]["status"] == "supported"
    assert snap["economics"]["reference_margin_btc"] == pytest.approx(0.0001125)


def test_native_space_gate_projects_without_inventing_old_gate():
    old = f.from_evaluation_snapshot(fmz_row(), T, source_hash="a" * 64)
    assert old["market_facts"]["space_gate"] is None
    assert old["space_gate_gap_reasons"] == ["space_gate_missing"]
    assert old["status"] == "current"  # old fact identity remains readable, but cannot open G1
    row = fmz_row()
    gate = {"state": "STRUCTURE_ELIGIBLE", "observed_at_ms": T - 1000,
            "since_ts_ms": T - 1000, "previous_state": "STRUCTURE_BLOCKED",
            "last_transition_ts_ms": T - 1000, "reason_codes": [],
            "anchor": {"state": "Valid", "score": 70, "ok": True},
            "net_gamma": {"value": 12_000_000, "unit": "USD", "sign": "POSITIVE",
                          "source": "gexmonitorapi.gex_board.total_net_gex",
                          "source_ts_ms": T - 2000, "valid": True,
                          "transition": False, "conflict": False, "ok": True}}
    row["factor_snapshot"]["space_gate"] = gate
    current = f.from_evaluation_snapshot(row, T, source_hash="b" * 64)
    assert current["space_gate_provenance"] == "producer_native"
    assert current["market_facts"]["space_gate"] == gate


def test_projects_readable_market_skeleton_from_producer_shaped_snapshot():
    context = f.from_evaluation_snapshot(rich_fmz_row(), T, source_hash="9" * 64)
    facts = context["market_facts"]
    assert context["status"] == "current"
    assert facts["flow"]["tmv_blend"] == -0.42
    assert facts["volume_price"]["tmvf_24h"]["tmv_final"] == -0.5
    assert facts["volume_price"]["tmvf_24h"]["core"]["trend_strength_pct"] == 0.62
    assert facts["volume_price"]["tmvf_48h"]["core"]["kline_open_time"] == T - 3600_000
    assert facts["volume_price"]["micro_flow"]["fast_4h"]["horizon_hours"] == 4
    assert facts["volume_price"]["micro_flow"]["fast_4h"]["cvd_unit"] == "BTC"
    assert facts["volume_price"]["micro_flow"]["fast_4h"]["momentum_return_pct"] == -0.8
    assert facts["volume_price"]["micro_flow"]["slow_12h"]["coverage_frac"] == 0.98
    assert facts["volume_price"]["funding"]["last_rate"] == 0.00003
    assert facts["volume_price"]["funding"]["tmvf_24h"]["funding_cum"] == 0.00008
    assert facts["volume_price"]["funding"]["tmvf_48h"]["window_start_time"] == T - 50 * 3600_000
    assert facts["macro_pressure"]["macro_regime"] == "Mild Headwind"
    macro_components = facts["macro_pressure"]["component_summaries"]
    assert macro_components[0]["current_close"] == 105.2
    assert macro_components[0]["reference_ts_ms"] == T - 4 * 86_400_000
    assert macro_components[1]["scoring_unit"] == "bps"
    assert macro_components[1]["scoring_bps"] == pytest.approx(7.0)
    assert facts["space_context"]["anchor"]["anchor_gravity_ref_score"] == 74
    assert facts["space_context"]["gamma_regime"]["net_gamma_notional"] == 2_500_000
    assert facts["space_context"]["gex_info"]["total_net_gex"] == 2_500_000
    assert facts["edb_reasoning"]["interpretation"] == "evidence_quality_not_win_rate"
    assert facts["edb_reasoning"]["evidence"][0]["key"] == "TMV"
    assert facts["edb_reasoning"]["side_hint"] == "PUT_SIDE_CONTEXT"
    assert {"layer": "volume_price", "code": "flow_window_conflict"} in facts["conflicts"]
    assert {"layer": "edb", "code": "edb_conflict_medium"} in facts["conflicts"]
    assert facts["source_trace"]["runtime_mode"] == "live_public_read_only"
    assert facts["source_trace"]["current_price_age_ms"] == 1500
    assert facts["space_gate"]["state"] == "STRUCTURE_ELIGIBLE"


def test_projection_sanitizes_nested_nonfinite_numbers_without_losing_shape():
    row = rich_fmz_row()
    macro_component = row["factor_snapshot"]["macro_pressure"]["components"][0]
    macro_component["current_close"] = math.nan
    macro_component["scoring_bps"] = math.inf
    flow = row["factor_snapshot"]["flow"]
    flow["micro_flow"]["fast_4h"]["cvd_norm"] = math.nan
    flow["micro_flow"]["slow_12h"]["momentum_return_pct"] = -math.inf
    flow["tmvf_24h"]["funding"]["funding_norm"] = math.nan
    flow["tmvf_48h"]["funding"]["last_funding_rate"] = math.inf
    row["factor_snapshot"]["gex_info"]["support_walls"] = [97000.0, math.nan, 95000.0]
    row["factor_snapshot"]["gex_info"]["resistance_walls"] = [103000.0, math.inf]

    context = f.from_evaluation_snapshot(row, T, source_hash="7" * 64)
    json.dumps(context, allow_nan=False)
    facts = context["market_facts"]
    component = facts["macro_pressure"]["component_summaries"][0]
    assert component["current_close"] is None
    assert component["scoring_bps"] is None
    assert {gap["path"] for gap in component["_projection_gaps"]} == {
        "current_close", "scoring_bps"}
    assert facts["volume_price"]["micro_flow"]["fast_4h"]["cvd_norm"] is None
    assert facts["volume_price"]["micro_flow"]["slow_12h"]["momentum_return_pct"] is None
    assert facts["volume_price"]["funding"]["tmvf_24h"]["funding_norm"] is None
    assert facts["volume_price"]["funding"]["tmvf_48h"]["last_funding_rate"] is None
    assert facts["space_context"]["gex_info"]["support_walls"] == [97000.0, None, 95000.0]
    assert facts["space_context"]["gex_info"]["resistance_walls"] == [103000.0, None]
    micro_gap_paths = {
        gap["path"] for gap in
        facts["volume_price"]["micro_flow"]["fast_4h"]["_projection_gaps"]}
    assert "cvd_norm" in micro_gap_paths
    wall_gap_paths = {
        gap["path"] for gap in
        facts["space_context"]["gex_info"]["_projection_gaps"]}
    assert "support_walls[1]" in wall_gap_paths
    assert context["source_hash"] == "7" * 64


def test_blocked_native_gate_and_top_level_price_are_json_safe_not_promoted():
    row = fmz_row()
    row["decision"]["runtime_facts"]["current_price"] = math.nan
    row["factor_snapshot"]["space_gate"] = {
        "state": "STRUCTURE_BLOCKED",
        "observed_at_ms": T - 1000,
        "since_ts_ms": T - 1000,
        "reason_codes": ["anchor_score_missing"],
        "anchor": {"state": "Invalid", "score": math.nan, "ok": False},
        "net_gamma": {"value": math.inf, "unit": "USD", "ok": False},
    }
    context = f.from_evaluation_snapshot(row, T, source_hash="8" * 64)
    json.dumps(context, allow_nan=False)
    assert context["status"] == "background_only"
    assert "fmz_price_or_source_missing" in context["gap_reasons"]
    assert context["current_price_usd"] is None
    assert context["market_facts"]["space_gate"]["state"] == "STRUCTURE_BLOCKED"
    assert context["market_facts"]["space_gate"]["anchor"]["score"] is None
    assert context["market_facts"]["space_gate"]["net_gamma"]["value"] is None
    root_gap_paths = {gap["path"] for gap in context["_projection_gaps"]}
    assert "current_price_usd" in root_gap_paths
    assert "market_facts.space_gate.anchor.score" in root_gap_paths
    assert "market_facts.space_gate.net_gamma.value" in root_gap_paths


def test_non_positive_top_level_price_is_not_current():
    row = fmz_row()
    row["decision"]["runtime_facts"]["current_price"] = 0
    context = f.from_evaluation_snapshot(row, T, source_hash="0" * 64)
    json.dumps(context, allow_nan=False)
    assert context["status"] == "background_only"
    assert context["current_price_usd"] == 0
    assert "fmz_price_or_source_missing" in context["gap_reasons"]


def test_inconsistent_or_future_native_gate_fails_closed():
    row = fmz_row()
    gate = {"state": "STRUCTURE_ELIGIBLE", "observed_at_ms": T + 1,
            "since_ts_ms": T - 1000, "reason_codes": [],
            "anchor": {"state": "Valid", "score": 70, "ok": True},
            "net_gamma": {"value": 1, "unit": "USD", "sign": "POSITIVE",
                          "source": "gexmonitorapi.gex_board.total_net_gex",
                          "source_ts_ms": T - 2000, "valid": True, "transition": False,
                          "conflict": False, "ok": True}}
    row["factor_snapshot"]["space_gate"] = gate
    future = f.from_evaluation_snapshot(row, T, source_hash="c" * 64)
    assert future["market_facts"]["space_gate"] is None
    assert future["space_gate_gap_reasons"] == ["space_gate_clock_invalid_or_stale"]
    gate["observed_at_ms"] = T - 1000
    gate["net_gamma"]["value"] = -1
    inconsistent = f.from_evaluation_snapshot(row, T, source_hash="d" * 64)
    assert inconsistent["market_facts"]["space_gate"] is None
    assert inconsistent["space_gate_gap_reasons"] == ["space_gate_eligible_facts_inconsistent"]
    gate["net_gamma"]["value"] = math.nan
    nonfinite = f.from_evaluation_snapshot(row, T, source_hash="e" * 64)
    json.dumps(nonfinite, allow_nan=False)
    assert nonfinite["market_facts"]["space_gate"] is None
    assert nonfinite["space_gate_gap_reasons"] == ["space_gate_eligible_facts_inconsistent"]


def test_legacy_producer_cannot_open_native_space_gate():
    row = fmz_row()
    row["decision"]["demo_version"] = "1.6.2"
    row["factor_snapshot"]["space_gate"] = {
        "state": "STRUCTURE_ELIGIBLE", "observed_at_ms": T - 1000,
        "since_ts_ms": T - 1000, "reason_codes": [],
        "anchor": {"state": "Valid", "score": 70, "ok": True},
        "net_gamma": {"value": 1, "unit": "USD", "sign": "POSITIVE",
                      "source": "gexmonitorapi.gex_board.total_net_gex",
                      "source_ts_ms": T - 2000, "valid": True,
                      "transition": False, "conflict": False, "ok": True},
    }
    context = f.from_evaluation_snapshot(row, T, source_hash="e" * 64)
    assert context["status"] == "background_only"
    assert context["market_facts"]["space_gate"] is None
    assert "fmz_producer_version_mismatch" in context["gap_reasons"]
    assert context["space_gate_gap_reasons"] == ["space_gate_producer_version_mismatch"]
    assert context["market_facts"]["source_trace"]["strategy_version"] == "1.6.2"
    assert context["market_facts"]["space_context"]["space_gate_reasons"] == ["space_gate_producer_version_mismatch"]


def test_synthetic_preview_context_is_explicit_and_never_current():
    context = f.synthetic_preview_context(T)
    assert context["status"] == "background_only"
    assert context["synthetic_preview"] is True
    assert context["production_eligible"] is False
    assert context["source_identity"]["kind"] == "synthetic_preview"
    assert "synthetic_preview_not_production" in context["gap_reasons"]
    assert "fmz_runtime_not_live_public" in context["gap_reasons"]
    assert context["market_facts"]["space_gate"]["state"] == "STRUCTURE_ELIGIBLE"
    assert context["market_facts"]["source_trace"]["synthetic_preview"] is True
    assert context["market_facts"]["edb_reasoning"]["interpretation"] == "evidence_quality_not_win_rate"
    facts = context["market_facts"]
    components = facts["macro_pressure"]["component_summaries"]
    assert [item["key"] for item in components] == ["DXY", "US10Y", "VOLQ"]
    assert components[2]["component"] == "VXN"
    assert components[2]["source_symbol"] == "^VXN"
    assert components[1]["scoring_unit"] == "bps"
    assert components[2]["scoring_unit"] == "pct"
    assert facts["volume_price"]["tmvf_24h"]["core"]["price"] == 100000.0
    assert facts["volume_price"]["micro_flow"]["slow_12h"]["coverage_hours"] == 11.75
    assert facts["volume_price"]["funding"]["tmvf_48h"]["funding_interval_hours"] == 8
