"""Bind an existing FMZ EvaluationSnapshot to an underwriting valuation.

FMZ writes `snapshots.jsonl` on its bounded export cadence. This reader never
creates a repair episode, changes producer data, or upgrades old signal cards.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "astra_fmz_fact_context@2.0.0"
REQUIRED_FMZ_VERSION = "2.0.0"
SUPPORTED_FMZ_VERSIONS = {"2.0.0", "2.0.1"}  # explicitly tested same native contract
MAX_AGE_MS = 300_000  # technical freshness, not a fitted trading interval
MAX_SOURCE_TAIL_BYTES = 16 * 1024 * 1024


def _native_space_gate(value: Any, valuation_ts_ms: int, max_age_ms: int) -> tuple[dict[str, Any] | None, str | None]:
    """Carry only a fresh producer-native gate; never infer it from old GGR."""
    if not isinstance(value, Mapping):
        return None, "space_gate_missing"
    state = value.get("state")
    observed = value.get("observed_at_ms")
    since = value.get("since_ts_ms")
    if state not in {"STRUCTURE_ELIGIBLE", "STRUCTURE_BLOCKED"}:
        return None, "space_gate_state_invalid"
    if (not isinstance(observed, int) or isinstance(observed, bool)
            or not isinstance(since, int) or isinstance(since, bool)
            or since > observed or not 0 <= valuation_ts_ms - observed <= max_age_ms):
        return None, "space_gate_clock_invalid_or_stale"
    anchor, gamma = value.get("anchor"), value.get("net_gamma")
    if not isinstance(anchor, Mapping) or not isinstance(gamma, Mapping):
        return None, "space_gate_facts_missing"
    reasons = value.get("reason_codes")
    if not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons):
        return None, "space_gate_reasons_invalid"
    if state == "STRUCTURE_ELIGIBLE":
        score, net_value = anchor.get("score"), gamma.get("value")
        if (anchor.get("state") != "Valid" or anchor.get("ok") is not True
                or not isinstance(score, (int, float)) or isinstance(score, bool)
                or not math.isfinite(score) or score < 60
                or gamma.get("ok") is not True or gamma.get("valid") is not True
                or gamma.get("transition") is not False or gamma.get("conflict") is not False
                or gamma.get("sign") != "POSITIVE" or gamma.get("unit") != "USD"
                or gamma.get("source") != "gexmonitorapi.gex_board.total_net_gex"
                or not isinstance(gamma.get("source_ts_ms"), int)
                or isinstance(gamma.get("source_ts_ms"), bool)
                or gamma["source_ts_ms"] > observed
                or not isinstance(net_value, (int, float)) or isinstance(net_value, bool)
                or not math.isfinite(net_value) or net_value <= 0):
            return None, "space_gate_eligible_facts_inconsistent"
    return dict(value), None


def _fields(value: Any, names: tuple[str, ...]) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    return _project_json_safe({
        name: source[name] for name in names
        if name in source and source[name] is not None
    })


def _clean(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _is_non_finite_number(value: Any) -> bool:
    return (
        isinstance(value, float)
        and not isinstance(value, bool)
        and not math.isfinite(value)
    )


def _project_json_safe(value: Any) -> Any:
    """Return a JSON-safe projection while preserving dict/list shape.

    Producer payloads can contain NaN/Inf from upstream math. The source hash is
    computed from the raw line, so projection cleanup must happen after source
    identity is fixed and must not make invalid numbers look valid.
    """
    cleaned, gaps = _sanitize_projection(value)
    if gaps and isinstance(cleaned, dict):
        cleaned = dict(cleaned)
        cleaned["_projection_gaps"] = gaps
    return cleaned


def _sanitize_projection(value: Any, path: str = "") -> tuple[Any, list[dict[str, str]]]:
    if _is_non_finite_number(value):
        return None, [{
            "path": path or "$",
            "reason": "non_finite_numeric",
        }]
    if isinstance(value, Mapping):
        out = {}
        gaps: list[dict[str, str]] = []
        for key, item in value.items():
            key_text = str(key)
            item_path = key_text if not path else path + "." + key_text
            projected, item_gaps = _sanitize_projection(item, item_path)
            out[key_text] = projected
            gaps.extend(item_gaps)
        return out, gaps
    if isinstance(value, list):
        out = []
        gaps: list[dict[str, str]] = []
        for index, item in enumerate(value):
            item_path = f"{path}[{index}]" if path else f"[{index}]"
            projected, item_gaps = _sanitize_projection(item, item_path)
            out.append(projected)
            gaps.extend(item_gaps)
        return out, gaps
    return value, []


def _path(value: Any, *names: str) -> Any:
    current = value
    for name in names:
        if not isinstance(current, Mapping):
            return None
        current = current.get(name)
    return current


def _scalar(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (str, int, float)):
        return value if not isinstance(value, float) or math.isfinite(value) else None
    return None


def _scalar_mapping(value: Any, *, limit: int = 12) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    out = {}
    gaps = []
    for key, item in value.items():
        if len(out) >= limit:
            break
        if _is_non_finite_number(item):
            gaps.append({"path": str(key), "reason": "non_finite_numeric"})
            out[str(key)] = None
            continue
        scalar = _scalar(item)
        if scalar is not None:
            out[str(key)] = scalar
    if gaps:
        out["_projection_gaps"] = gaps
    return out


def _component_summaries(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out = []
    for item in items[:6]:
        if not isinstance(item, Mapping):
            continue
        out.append(_fields(item, (
            "component", "key", "source_symbol", "source_status", "tier",
            "tier_cn", "impact", "component_score", "change_pct_3d",
            "current_close", "reference_close", "current_ts_ms",
            "reference_ts_ms", "scoring_value", "scoring_unit",
            "scoring_bps")))
    return out


def _brief_evidence(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        detail = item.get("detail") if isinstance(item.get("detail"), Mapping) else {}
        out.append(_clean({
            "key": item.get("key"),
            "vote": item.get("vote"),
            "weight": item.get("weight"),
            "eff_weight": item.get("eff_weight"),
            "info": item.get("info"),
            "participation_status": item.get("participation_status"),
            "exclusion_reason": item.get("exclusion_reason"),
            "detail": _fields(detail, (
                "direction", "tmv_blend", "window_conflict",
                "tmvf_24h_final", "tmvf_48h_final", "role", "verdict",
                "cvd_norm", "cvd_sum", "price_return_pct", "strength",
                "price_confirm", "joint_active", "macro_score",
                "macro_regime", "funding_norm", "data_state", "rr_blend",
                "skew_norm_blend", "rr_z", "delta_rr", "vote_confidence",
                "regime", "regime_strength", "net_gamma_notional",
                "max_gamma_strike", "flip_point", "distance_to_flip_pct",
                "pin")),
        }))
    return out


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _tmvf_window_context(value: Any) -> dict[str, Any]:
    window = value if isinstance(value, Mapping) else {}
    core = window.get("core") if isinstance(window.get("core"), Mapping) else {}
    return _clean({
        **_fields(window, (
            "label", "data_ready", "tmv_core", "tmv_final", "final_state",
            "direction", "market_state", "window_hours", "funding_adjustment",
            "funding_effect", "funding_state", "warning", "reason_codes")),
        "core": _fields(core, (
            "label", "horizon_hours", "data_ready", "tmv_core",
            "tmv_core_raw", "state", "trend_direction", "trend_strength_pct",
            "price", "kline_open_time", "kline_count", "required_klines")),
    })


def _funding_window_context(value: Any) -> dict[str, Any]:
    window = value if isinstance(value, Mapping) else {}
    funding = window.get("funding") if isinstance(window.get("funding"), Mapping) else {}
    return _fields(funding, (
        "horizon_hours", "funding_cum", "funding_count", "funding_norm",
        "funding_state", "last_funding_rate", "funding_interval_hours",
        "window_start_time", "window_end_time", "age_ms", "data_ready",
        "reason"))


def _micro_window_context(value: Any) -> dict[str, Any]:
    return _fields(value, (
        "horizon_hours", "data_ready", "bar_count", "coverage_hours",
        "coverage_frac", "momentum", "momentum_return_pct", "momentum_norm",
        "cvd_sum", "cvd_unit", "cvd_per_bar", "cvd_norm", "score", "state",
        "confidence", "direction", "window_role", "reason",
        "price_open", "price_close", "price_return_pct"))


def _volume_price_context(flow: Any) -> dict[str, Any]:
    flow = flow if isinstance(flow, Mapping) else {}
    micro = flow.get("micro_flow") if isinstance(flow.get("micro_flow"), Mapping) else {}
    tmvf_24h = flow.get("tmvf_24h")
    tmvf_48h = flow.get("tmvf_48h")
    funding_24h = _funding_window_context(tmvf_24h)
    funding_48h = _funding_window_context(tmvf_48h)
    return _clean({
        "direction": flow.get("direction"),
        "market_state": flow.get("market_state"),
        "tmv_blend": flow.get("tmv_blend"),
        "tmv_state": flow.get("tmv_state"),
        "window_conflict": flow.get("window_conflict"),
        "tmvf_24h": _tmvf_window_context(tmvf_24h),
        "tmvf_48h": _tmvf_window_context(tmvf_48h),
        "micro_flow_effect": flow.get("micro_flow_effect"),
        "micro_flow": _clean({
            "fast_4h": _micro_window_context(micro.get("fast_4h")),
            "slow_12h": _micro_window_context(micro.get("slow_12h")),
            "combined": _fields(micro.get("combined"), (
                "score", "state", "direction", "ready_horizons",
                "max_coverage_hours", "data_ready")),
        }),
        "funding": _clean({
            "last_rate": _first_non_none(
                flow.get("last_funding_rate"),
                funding_48h.get("last_funding_rate"),
                funding_24h.get("last_funding_rate")),
            "count": _first_non_none(flow.get("funding_count"), funding_48h.get("funding_count")),
            "effect": flow.get("tmvf_funding_effect"),
            "semantics": _scalar_mapping(flow.get("tmvf_funding_semantics")),
            "tmvf_24h": funding_24h,
            "tmvf_48h": funding_48h,
        }),
        "mark_price": flow.get("mark_price"),
        "index_price": flow.get("index_price"),
        "kline_count": flow.get("kline_count"),
    })


def _macro_context(macro: Any) -> dict[str, Any]:
    macro = macro if isinstance(macro, Mapping) else {}
    return _clean({
        "macro_score": macro.get("macro_score"),
        "macro_regime": macro.get("macro_regime"),
        "summary_label_cn": macro.get("summary_label_cn"),
        "interpretation_cn": macro.get("interpretation_cn"),
        "data_status": macro.get("data_status"),
        "macro_data_confidence": macro.get("macro_data_confidence"),
        "last_data_time": macro.get("last_data_time"),
        "data_age_ms": macro.get("data_age_ms"),
        "flags": macro.get("flags") or [],
        "blocking_flags": macro.get("blocking_flags") or [],
        "legacy_blocking_flags": macro.get("legacy_blocking_flags") or [],
        "macro_shock": _fields(macro.get("macro_shock"), (
            "block", "reason", "reason_code", "score")),
        "component_scores": _scalar_mapping(macro.get("component_scores")),
        "component_summaries": _component_summaries(macro.get("components")),
        "macro_components_cn": macro.get("macro_components_cn"),
        "reason_codes": macro.get("reason_codes") or [],
    })


def _space_context(factors: Mapping[str, Any], space_gate: dict[str, Any] | None,
                   gate_gap: str | None) -> dict[str, Any]:
    anchor = factors.get("anchor") if isinstance(factors.get("anchor"), Mapping) else {}
    gamma = factors.get("gamma_regime") if isinstance(factors.get("gamma_regime"), Mapping) else {}
    gex_info = factors.get("gex_info") if isinstance(factors.get("gex_info"), Mapping) else {}
    gamma_context = _fields(gamma, (
        "regime", "regime_strength", "flip_point", "asset_price",
        "distance_to_flip_pct", "net_gex_sign", "net_gamma_notional",
        "gex_info_market_state", "gex_info_agrees", "max_gamma_strike",
        "max_gamma_oi_share", "gate_action", "confidence_multiplier",
        "spatial_vote", "spatial_weight", "veto", "data_state",
        "reason_codes"))
    if _path(gamma, "pin", "pin_strike") is not None:
        gamma_context["pin_strike"] = _path(gamma, "pin", "pin_strike")
    return _clean({
        "anchor": _fields(anchor, (
            "ready", "effective_flip_point", "raw_flip_point", "gex_source_ts_ms",
            "gex_freshness", "freshness", "normalized_deviation",
            "anchor_gravity_ref_score", "anchor_gravity_ref_label",
            "anchor_gravity_warming", "anchor_gravity_window_count")),
        "gamma_regime": gamma_context,
        "gex_info": _fields(gex_info, (
            "asset", "quality", "data_state", "availability", "stale",
            "market_state", "total_net_gex", "fetched_at_ms", "age_ms",
            "spot_price", "flip_point", "magnet_price", "n1", "n2",
            "p1", "p2", "support_walls", "resistance_walls")),
        "space_gate_state": space_gate.get("state") if space_gate else None,
        "space_gate_reasons": (space_gate or {}).get("reason_codes") or (
            [gate_gap] if gate_gap else []),
        "space_gate_observed_at_ms": (space_gate or {}).get("observed_at_ms"),
        "space_gate_since_ts_ms": (space_gate or {}).get("since_ts_ms"),
        "space_gate_anchor": (space_gate or {}).get("anchor"),
        "space_gate_net_gamma": (space_gate or {}).get("net_gamma"),
    })


def _edb_context(edb: Any) -> dict[str, Any]:
    edb = edb if isinstance(edb, Mapping) else {}
    return _clean({
        "edb_score": edb.get("edb_score"),
        "edb_score_raw": edb.get("edb_score_raw"),
        "agreement": edb.get("agreement"),
        "coverage": edb.get("coverage"),
        "confidence": edb.get("confidence"),
        "calibration_state": edb.get("calibration_state"),
        "lean": edb.get("lean"),
        "lean_pre_gate": edb.get("lean_pre_gate"),
        "side_hint": edb.get("side_hint"),
        "side_hint_pre_gate": edb.get("side_hint_pre_gate"),
        "support_label": edb.get("support_label"),
        "support_pre_gate": edb.get("support_pre_gate"),
        "next_action": edb.get("next_action"),
        "next_action_pre_gate": edb.get("next_action_pre_gate"),
        "conflict_level": edb.get("conflict_level"),
        "ggr_gate": edb.get("ggr_gate"),
        "veto_reason": edb.get("veto_reason"),
        "confidence_decomposition": edb.get("confidence_decomposition"),
        "reason_codes": edb.get("reason_codes") or [],
        "summary_cn": edb.get("summary_cn"),
        "evidence": _brief_evidence(edb.get("evidence")),
        "interpretation": "evidence_quality_not_win_rate",
    })


def _strategy_context(value: Any) -> dict[str, Any]:
    strategy = value if isinstance(value, Mapping) else {}
    return _clean({
        "signal": strategy.get("signal"),
        "strategy_code": strategy.get("strategy_code"),
        "strategy_type": strategy.get("strategy_type"),
        "summary": strategy.get("summary"),
        "selection_reason": strategy.get("selection_reason"),
        "order_layer": _scalar(strategy.get("order_layer")),
    })


def _conflicts(factors: Mapping[str, Any], gate_gap: str | None) -> list[dict[str, Any]]:
    out = []
    flow = factors.get("flow") if isinstance(factors.get("flow"), Mapping) else {}
    macro = factors.get("macro_pressure") if isinstance(factors.get("macro_pressure"), Mapping) else {}
    gamma = factors.get("gamma_regime") if isinstance(factors.get("gamma_regime"), Mapping) else {}
    edb = factors.get("edb") if isinstance(factors.get("edb"), Mapping) else {}
    if flow.get("window_conflict"):
        out.append({"layer": "volume_price", "code": "flow_window_conflict"})
    for code in macro.get("blocking_flags") or []:
        out.append({"layer": "macro_pressure", "code": code})
    if _path(macro, "macro_shock", "block"):
        out.append({"layer": "macro_pressure", "code": "macro_shock_block"})
    for code in gamma.get("reason_codes") or []:
        out.append({"layer": "space", "code": code})
    if gamma.get("veto"):
        out.append({"layer": "space", "code": "gamma_regime_veto"})
    if edb.get("veto_reason"):
        out.append({"layer": "edb", "code": edb.get("veto_reason")})
    if edb.get("conflict_level") not in (None, "LOW", "NONE"):
        out.append({"layer": "edb", "code": "edb_conflict_" + str(edb.get("conflict_level")).lower()})
    if gate_gap:
        out.append({"layer": "space_gate", "code": gate_gap})
    return out


def _source_trace(record: Mapping[str, Any], decision: Mapping[str, Any],
                  runtime: Mapping[str, Any], valuation_ts_ms: int) -> dict[str, Any]:
    price_observed = runtime.get("current_price_observed_ms")
    price_age = None
    if isinstance(price_observed, int) and not isinstance(price_observed, bool):
        price_age = valuation_ts_ms - price_observed
    return _clean({
        "schema_name": record.get("schema_name"),
        "schema_version": record.get("schema_version"),
        "decision_ts_ms": decision.get("ts_ms"),
        "strategy_version": decision.get("demo_version"),
        "runtime_mode": runtime.get("runtime_mode"),
        "current_price_source": runtime.get("current_price_source"),
        "current_price_observed_ms": price_observed,
        "current_price_age_ms": price_age,
        "source_quality": _scalar_mapping(runtime.get("source_quality")),
    })


def _market_facts(factors: Mapping[str, Any], record: Mapping[str, Any],
                  decision: Mapping[str, Any], runtime: Mapping[str, Any],
                  valuation_ts_ms: int, space_gate: dict[str, Any] | None,
                  gate_gap: str | None) -> dict[str, Any]:
    flow = factors.get("flow")
    macro = factors.get("macro_pressure")
    edb = factors.get("edb")
    return {
        "anchor": _fields(factors.get("anchor"), ("effective_flip_point", "anchor_gravity_ref_score", "gex_freshness")),
        "flow": _fields(flow, ("tmv_blend", "direction", "market_state", "micro_flow_effect")),
        "gamma_regime": _fields(factors.get("gamma_regime"), ("regime", "net_gex_sign", "net_gamma_notional", "gex_info_agrees", "data_state", "reason_codes")),
        "space_gate": space_gate,
        "edb": _fields(edb, ("edb_score", "direction", "nr_state")),
        "neutral_repair": _fields(factors.get("neutral_repair_signal"), ("state", "is_active")),
        "volume_price": _volume_price_context(flow),
        "macro_pressure": _macro_context(macro),
        "space_context": _space_context(factors, space_gate, gate_gap),
        "edb_reasoning": _edb_context(edb),
        "skew": _fields(factors.get("skew"), (
            "data_state", "rr_blend", "skew_norm_blend", "rr_z",
            "delta_rr", "vote", "vote_confidence", "greeks_epoch_ms",
            "reason_codes")),
        "strategy_recommendation": _strategy_context(factors.get("strategy_recommendation")),
        "m_die": _fields(factors.get("m_die"), (
            "state", "score", "value", "m_die", "direction", "reason_codes")),
        "conflicts": _conflicts(factors, gate_gap),
        "source_trace": _source_trace(record, decision, runtime, valuation_ts_ms),
    }


def from_evaluation_snapshot(record: Mapping[str, Any], valuation_ts_ms: int,
                             *, source_hash: str, max_age_ms: int = MAX_AGE_MS) -> dict[str, Any]:
    """Project source-identifiable context; keep source/data quality separate."""
    if not isinstance(record, Mapping) or record.get("schema_name") != "EvaluationSnapshot":
        raise ValueError("FMZ EvaluationSnapshot is required")
    decision = record.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("FMZ decision is missing")
    as_of_ms = decision.get("ts_ms")
    if isinstance(as_of_ms, bool) or not isinstance(as_of_ms, int):
        raise ValueError("FMZ decision timestamp is missing")
    if as_of_ms > valuation_ts_ms:
        raise ValueError("future FMZ evaluation cannot be used")
    runtime = decision.get("runtime_facts") if isinstance(decision.get("runtime_facts"), Mapping) else {}
    factors = record.get("factor_snapshot") if isinstance(record.get("factor_snapshot"), Mapping) else {}
    price_observed = runtime.get("current_price_observed_ms")
    gap_reasons = []
    if decision.get("demo_version") not in SUPPORTED_FMZ_VERSIONS:
        gap_reasons.append("fmz_producer_version_mismatch")
    if decision.get("symbol") != "BTC":
        gap_reasons.append("fmz_underlying_missing_or_mismatch")
    if valuation_ts_ms - as_of_ms > max_age_ms:
        gap_reasons.append("fmz_snapshot_stale")
    if not isinstance(price_observed, int) or isinstance(price_observed, bool):
        gap_reasons.append("fmz_price_observed_time_missing")
    elif price_observed > valuation_ts_ms:
        gap_reasons.append("fmz_price_from_future")
    elif valuation_ts_ms - price_observed > max_age_ms:
        gap_reasons.append("fmz_price_stale")
    if runtime.get("runtime_mode") != "live_public_read_only":
        gap_reasons.append("fmz_runtime_not_live_public")
    current_price = runtime.get("current_price")
    if (current_price is None or isinstance(current_price, bool)
            or not isinstance(current_price, (int, float))
            or _is_non_finite_number(current_price)
            or current_price <= 0
            or not runtime.get("current_price_source")):
        gap_reasons.append("fmz_price_or_source_missing")
    space_gate, gate_gap = _native_space_gate(factors.get("space_gate"), valuation_ts_ms, max_age_ms)
    if decision.get("demo_version") not in SUPPORTED_FMZ_VERSIONS:
        space_gate, gate_gap = None, "space_gate_producer_version_mismatch"
    market_facts = _market_facts(
        factors, record, decision, runtime, valuation_ts_ms, space_gate, gate_gap)
    return _project_json_safe({
        "schema": SCHEMA, "status": "current" if not gap_reasons else "background_only",
        "source_hash": source_hash, "snapshot_ts_ms": as_of_ms,
        "valuation_ts_ms": valuation_ts_ms, "age_ms": valuation_ts_ms - as_of_ms,
        "producer_schema_version": record.get("schema_version"),
        "underlying": decision.get("symbol"),
        "strategy_version": decision.get("demo_version"),
        "runtime_mode": runtime.get("runtime_mode"),
        "current_price_usd": current_price,
        "current_price_source": runtime.get("current_price_source"),
        "price_observed_ms": price_observed,
        "market_facts": market_facts,
        "space_gate_provenance": "producer_native" if space_gate is not None else "missing_or_invalid",
        "space_gate_gap_reasons": [gate_gap] if gate_gap else [],
        "source_quality": _fields(runtime, ("source_quality", "source_details")),
        "export_health": _project_json_safe(record.get("data_export_bridge") or record.get("export_health") or {}),
        "gap_reasons": gap_reasons,
    })


def _synthetic_preview_snapshot(valuation_ts_ms: int) -> dict[str, Any]:
    observed = valuation_ts_ms
    source_ts = valuation_ts_ms - 30_000
    kline_open_time = source_ts - 60 * 60 * 1000
    funding_end_time = source_ts - 2 * 60 * 60 * 1000
    funding_24h_start_time = funding_end_time - 24 * 60 * 60 * 1000
    funding_48h_start_time = funding_end_time - 48 * 60 * 60 * 1000
    macro_current_ts = source_ts - 12 * 60 * 60 * 1000
    macro_reference_ts = macro_current_ts - 3 * 24 * 60 * 60 * 1000
    return {
        "schema_name": "EvaluationSnapshot",
        "schema_version": "synthetic_preview@2.0.0",
        "decision": {
            "ts_ms": valuation_ts_ms,
            "demo_version": REQUIRED_FMZ_VERSION,
            "symbol": "BTC",
            "runtime_facts": {
                "runtime_mode": "local_synthetic_preview",
                "current_price": 100000.0,
                "current_price_source": "synthetic_local_preview",
                "current_price_observed_ms": observed,
                "source_quality": {
                    "identity": "synthetic_preview",
                    "production_eligible": False,
                },
            },
        },
        "factor_snapshot": {
            "anchor": {
                "effective_flip_point": 98500.0,
                "raw_flip_point": 98480.0,
                "gex_source_ts_ms": source_ts,
                "gex_freshness": "FRESH",
                "freshness": "FRESH",
                "normalized_deviation": 0.18,
                "anchor_gravity_ref_score": 72.0,
                "anchor_gravity_ref_label": "Valid",
                "anchor_gravity_warming": False,
                "anchor_gravity_window_count": 72,
            },
            "flow": {
                "direction": "UP",
                "market_state": "MILD_TREND",
                "tmv_blend": 0.32,
                "tmv_state": "SUPPORTED",
                "window_conflict": False,
                "tmvf_24h": {
                    "label": "24h",
                    "data_ready": True,
                    "tmv_core": 0.31,
                    "tmv_final": 0.34,
                    "final_state": "directional",
                    "funding_adjustment": 0.03,
                    "funding_effect": "confirming",
                    "funding_state": "neutral",
                    "core": {
                        "label": "24h",
                        "horizon_hours": 24,
                        "data_ready": True,
                        "tmv_core": 0.31,
                        "tmv_core_raw": 0.31,
                        "state": "directional",
                        "trend_direction": 1,
                        "trend_strength_pct": 0.18,
                        "price": 100000.0,
                        "kline_open_time": kline_open_time,
                    },
                    "funding": {
                        "horizon_hours": 24,
                        "funding_cum": 0.00006,
                        "funding_count": 3,
                        "funding_norm": 0.12,
                        "funding_state": "neutral",
                        "last_funding_rate": 0.00002,
                        "funding_interval_hours": 8,
                        "window_start_time": funding_24h_start_time,
                        "window_end_time": funding_end_time,
                        "age_ms": source_ts - funding_end_time,
                        "data_ready": True,
                    },
                },
                "tmvf_48h": {
                    "label": "48h",
                    "data_ready": True,
                    "tmv_core": 0.27,
                    "tmv_final": 0.28,
                    "final_state": "directional",
                    "funding_adjustment": 0.01,
                    "funding_effect": "neutral",
                    "funding_state": "neutral",
                    "core": {
                        "label": "48h",
                        "horizon_hours": 48,
                        "data_ready": True,
                        "tmv_core": 0.27,
                        "tmv_core_raw": 0.27,
                        "state": "directional",
                        "trend_direction": 1,
                        "trend_strength_pct": 0.23,
                        "price": 100000.0,
                        "kline_open_time": kline_open_time,
                    },
                    "funding": {
                        "horizon_hours": 48,
                        "funding_cum": 0.00010,
                        "funding_count": 6,
                        "funding_norm": 0.09,
                        "funding_state": "neutral",
                        "last_funding_rate": 0.00002,
                        "funding_interval_hours": 8,
                        "window_start_time": funding_48h_start_time,
                        "window_end_time": funding_end_time,
                        "age_ms": source_ts - funding_end_time,
                        "data_ready": True,
                    },
                },
                "micro_flow_effect": "BUY_CONFIRMS_UP",
                "micro_flow": {
                    "fast_4h": {
                        "horizon_hours": 4,
                        "data_ready": True,
                        "bar_count": 16,
                        "coverage_hours": 3.75,
                        "coverage_frac": 0.94,
                        "momentum": 0.0021,
                        "momentum_return_pct": 0.21,
                        "momentum_norm": 0.21,
                        "cvd_sum": 180.0,
                        "cvd_unit": "BTC",
                        "cvd_per_bar": 11.25,
                        "cvd_norm": 0.42,
                        "score": 0.34,
                        "state": "directional",
                        "confidence": 0.94,
                        "direction": "bullish",
                        "window_role": "fast",
                    },
                    "slow_12h": {
                        "horizon_hours": 12,
                        "data_ready": True,
                        "bar_count": 48,
                        "coverage_hours": 11.75,
                        "coverage_frac": 0.98,
                        "momentum": 0.0038,
                        "momentum_return_pct": 0.38,
                        "momentum_norm": 0.38,
                        "cvd_sum": 410.0,
                        "cvd_unit": "BTC",
                        "cvd_per_bar": 8.54,
                        "cvd_norm": 0.31,
                        "score": 0.33,
                        "state": "directional",
                        "confidence": 0.98,
                        "direction": "bullish",
                        "window_role": "slow",
                    },
                    "combined": {
                        "score": 0.33,
                        "state": "directional",
                        "direction": "bullish",
                        "ready_horizons": ["4h", "12h"],
                        "max_coverage_hours": 11.75,
                        "data_ready": True,
                    },
                },
                "tmvf_funding_effect": "BASELINE",
                "tmvf_funding_semantics": {"canonical_text_cn": "Synthetic preview; not a live funding read."},
                "last_funding_rate": 0.00002,
                "funding_count": 3,
                "mark_price": 100015.0,
                "index_price": 100000.0,
                "kline_count": 96,
            },
            "macro_pressure": {
                "macro_score": -0.12,
                "macro_regime": "Neutral",
                "data_status": "synthetic_preview",
                "macro_data_confidence": 0.75,
                "summary_label_cn": "宏观中性，暂未出现阻断性冲击。",
                "interpretation_cn": "模拟分量略偏宽松，仅用于检验页面表达。",
                "last_data_time": source_ts,
                "data_age_ms": observed - source_ts,
                "flags": [],
                "blocking_flags": [],
                "legacy_blocking_flags": [],
                "macro_shock": {"block": False, "reason": None},
                "component_scores": {"DXY": -0.04, "US10Y": -0.03, "VOLQ": 0.02},
                "components": [
                    {
                        "key": "DXY",
                        "component": "DXY",
                        "source_symbol": "DX-Y.NYB",
                        "source_status": "synthetic_preview",
                        "current_close": 103.8,
                        "reference_close": 104.2,
                        "current_ts_ms": macro_current_ts,
                        "reference_ts_ms": macro_reference_ts,
                        "change_pct_3d": -0.3838771593090188,
                        "scoring_value": -0.3838771593090188,
                        "scoring_unit": "pct",
                        "scoring_bps": -38.38771593090188,
                        "tier": "watch",
                        "tier_cn": "观察",
                        "component_score": -0.04,
                        "impact": "DXY 三日回落，风险资产逆风减轻。",
                    },
                    {
                        "key": "US10Y",
                        "component": "US10Y",
                        "source_symbol": "^TNX",
                        "source_status": "synthetic_preview",
                        "current_close": 4.11,
                        "reference_close": 4.15,
                        "current_ts_ms": macro_current_ts,
                        "reference_ts_ms": macro_reference_ts,
                        "change_pct_3d": -0.963855421686745,
                        "scoring_value": -4.0000000000000036,
                        "scoring_unit": "bps",
                        "scoring_bps": -4.0000000000000036,
                        "tier": "neutral",
                        "tier_cn": "中性",
                        "component_score": -0.03,
                        "impact": "美债收益率小幅回落。",
                    },
                    {
                        "key": "VOLQ",
                        "component": "VXN",
                        "source_symbol": "^VXN",
                        "source_status": "synthetic_preview",
                        "current_close": 21.1,
                        "reference_close": 20.0,
                        "current_ts_ms": macro_current_ts,
                        "reference_ts_ms": macro_reference_ts,
                        "change_pct_3d": 5.5,
                        "scoring_value": 5.5,
                        "scoring_unit": "pct",
                        "scoring_bps": 550.0,
                        "tier": "watch",
                        "tier_cn": "观察",
                        "component_score": 0.02,
                        "impact": "VXN 相对变化升温；此处 bps 为相对百分比基点，不是收益率 bp。",
                    },
                ],
                "macro_components_cn": "本地模拟宏观分量：DXY -38bp、US10Y -4bp、VXN +550相对bp。",
                "reason_codes": [],
            },
            "m_die": {"state": "QUIET", "score": 0.18, "reason_codes": []},
            "neutral_repair_signal": {"state": "IDLE", "is_active": False},
            "skew": {
                "data_state": "OK",
                "rr_blend": -0.08,
                "skew_norm_blend": -0.12,
                "vote": 0.04,
                "vote_confidence": 0.45,
                "greeks_epoch_ms": source_ts,
            },
            "gamma_regime": {
                "regime": "POSITIVE_GAMMA_PINNING",
                "regime_strength": 0.62,
                "flip_point": 98500.0,
                "asset_price": 100000.0,
                "distance_to_flip_pct": 1.5,
                "net_gex_sign": "POSITIVE",
                "net_gamma_notional": 4200000.0,
                "gex_info_market_state": "positive_gamma",
                "gex_info_agrees": True,
                "max_gamma_strike": 100000.0,
                "max_gamma_oi_share": 0.18,
                "pin": {"pin_strike": 100000.0},
                "gate_action": "NEUTRAL",
                "confidence_multiplier": 1.0,
                "spatial_vote": 0.18,
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
                "total_net_gex": 4200000.0,
                "spot_price": 100000.0,
                "flip_point": 98500.0,
                "magnet_price": 100000.0,
                "support_walls": [97000.0, 95000.0],
                "resistance_walls": [103000.0, 105000.0],
                "fetched_at_ms": source_ts,
                "age_ms": observed - source_ts,
            },
            "space_gate": {
                "state": "STRUCTURE_ELIGIBLE",
                "observed_at_ms": observed,
                "since_ts_ms": observed,
                "previous_state": "STRUCTURE_BLOCKED",
                "last_transition_ts_ms": observed,
                "reason_codes": [],
                "anchor": {"state": "Valid", "score": 72.0, "source_ts_ms": source_ts, "freshness": "FRESH", "ok": True},
                "net_gamma": {
                    "value": 4200000.0,
                    "unit": "USD",
                    "sign": "POSITIVE",
                    "source": "gexmonitorapi.gex_board.total_net_gex",
                    "source_ts_ms": source_ts,
                    "age_ms": observed - source_ts,
                    "valid": True,
                    "transition": False,
                    "conflict": False,
                    "ok": True,
                },
            },
            "edb": {
                "edb_score": 0.26,
                "edb_score_raw": 0.26,
                "agreement": 0.78,
                "coverage": 0.82,
                "confidence": 34,
                "calibration_state": "UNCALIBRATED",
                "lean": "MILD_BULLISH",
                "side_hint": "CALL_SIDE_CONTEXT",
                "support_label": "WEAK_SUPPORT",
                "next_action": "AUDIT_ONLY",
                "conflict_level": "LOW",
                "ggr_gate": {"regime": "POSITIVE_GAMMA_PINNING", "multiplier": 1.0, "veto": False},
                "veto_reason": None,
                "confidence_decomposition": {"strength": 0.35, "agr_factor": 0.91, "cov_factor": 0.91},
                "reason_codes": [],
                "summary_cn": "模拟量价略偏上行，但宏观与空间只是背景约束，不据此独立选边。",
                "evidence": [
                    {"key": "TMV", "vote": 0.32, "weight": 1.0, "eff_weight": 0.7, "info": 0.7, "participation_status": "ACTIVE", "detail": {"direction": "UP", "tmv_blend": 0.32}},
                    {"key": "MACRO", "vote": 0.12, "weight": 0.5, "eff_weight": 0.2, "info": 0.4, "participation_status": "ACTIVE", "detail": {"macro_regime": "Mild Tailwind"}},
                ],
            },
            "strategy_recommendation": {
                "signal": "AUDIT_ONLY",
                "strategy_code": None,
                "strategy_type": "UNDERWRITING_CONTEXT",
                "summary": "Synthetic local preview; no execution permission.",
                "selection_reason": "local_ui_demo_only",
                "order_layer": "external_manual_only",
            },
        },
    }


def synthetic_preview_context(valuation_ts_ms: int) -> dict[str, Any]:
    """Return a producer-shaped local demo context that can never be current."""
    record = _synthetic_preview_snapshot(valuation_ts_ms)
    raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    context = from_evaluation_snapshot(
        record, valuation_ts_ms, source_hash=hashlib.sha256(raw).hexdigest())
    context["status"] = "background_only"
    context["synthetic_preview"] = True
    context["production_eligible"] = False
    context["source_identity"] = {
        "kind": "synthetic_preview",
        "label": "local UI demo only",
        "production_eligible": False,
    }
    if "synthetic_preview_not_production" not in context["gap_reasons"]:
        context["gap_reasons"].append("synthetic_preview_not_production")
    context["space_gate_provenance"] = (
        "synthetic_preview" if context.get("market_facts", {}).get("space_gate")
        else context.get("space_gate_provenance"))
    context["market_facts"]["source_trace"]["synthetic_preview"] = True
    context["market_facts"]["source_trace"]["production_eligible"] = False
    return context


def latest_from_jsonl(path: str | Path, valuation_ts_ms: int,
                      *, max_age_ms: int = MAX_AGE_MS) -> dict[str, Any]:
    """Select the latest nonfuture producer row. Missing file is an explicit gap."""
    path = Path(path)
    if not path.is_file():
        return {"schema": SCHEMA, "status": "missing", "source_hash": None,
                "snapshot_ts_ms": None, "valuation_ts_ms": valuation_ts_ms,
                "market_facts": {}, "gap_reasons": ["fmz_snapshot_file_missing"]}
    chosen = None
    chosen_ts = -1
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        start = max(0, size - MAX_SOURCE_TAIL_BYTES)
        handle.seek(start)
        if start:
            handle.readline()  # Discard the partial first row of the bounded tail.
        for raw in handle:
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue  # A partially written last line never becomes a fact.
            if not isinstance(record, dict) or record.get("schema_name") != "EvaluationSnapshot":
                continue
            decision = record.get("decision")
            timestamp = decision.get("ts_ms") if isinstance(decision, dict) else None
            if isinstance(timestamp, int) and not isinstance(timestamp, bool) and chosen_ts < timestamp <= valuation_ts_ms:
                chosen = (record, hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest())
                chosen_ts = timestamp
    if chosen is None:
        return {"schema": SCHEMA, "status": "missing", "source_hash": None,
                "snapshot_ts_ms": None, "valuation_ts_ms": valuation_ts_ms,
                "market_facts": {}, "gap_reasons": ["fmz_nonfuture_snapshot_missing"]}
    context = from_evaluation_snapshot(chosen[0], valuation_ts_ms,
                                      source_hash=chosen[1], max_age_ms=max_age_ms)
    context["bridge_read"] = {"file_bytes": size, "tail_limit_bytes": MAX_SOURCE_TAIL_BYTES,
                              "bounded_tail": True}
    return context
