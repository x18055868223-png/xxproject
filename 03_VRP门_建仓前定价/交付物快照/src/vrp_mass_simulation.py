# -*- coding: utf-8 -*-
"""Massive VRP stress-case traversal anchored to real Deribit snapshots."""
from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, replace
from itertools import product
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from vrp_model import (
    DISTORTED_REVIEW,
    PASS,
    CandidateQuote,
    ScenarioConfig,
    WindowInput,
    assess_candidate,
    assess_window,
    normalise_iv,
)
from vrp_simulation import build_window_inputs, generate_candidates


def named_policy_configs() -> List[Tuple[str, ScenarioConfig]]:
    return [
        (
            "loose_snapshot_best",
            ScenarioConfig(
                low_percentile_multiplier=1.15,
                high_percentile_multiplier=0.88,
                term_backwardation_ratio=1.12,
                min_candidate_edge_ccy=0.0,
                min_window_vol_edge=0.0,
                spread_round_trip_multiplier=2.0,
            ),
        ),
        (
            "balanced_v1_1",
            ScenarioConfig(
                low_percentile_multiplier=1.25,
                high_percentile_multiplier=0.92,
                term_backwardation_ratio=1.18,
                min_candidate_edge_ccy=0.00002,
                min_window_vol_edge=0.02,
                spread_round_trip_multiplier=2.5,
            ),
        ),
        (
            "strict_cost_v1_1",
            ScenarioConfig(
                low_percentile_multiplier=1.25,
                high_percentile_multiplier=0.92,
                term_backwardation_ratio=1.18,
                min_candidate_edge_ccy=0.00005,
                min_window_vol_edge=0.02,
                spread_round_trip_multiplier=3.0,
            ),
        ),
        (
            "strict_cost_cold_guard_v1_1",
            ScenarioConfig(
                low_percentile_multiplier=1.25,
                high_percentile_multiplier=0.92,
                cold_start_multiplier=1.35,
                term_backwardation_ratio=1.18,
                min_candidate_edge_ccy=0.00005,
                min_window_vol_edge=0.02,
                spread_round_trip_multiplier=3.0,
            ),
        ),
        (
            "expansion_guard_v1_1",
            ScenarioConfig(
                low_percentile_multiplier=1.35,
                high_percentile_multiplier=0.92,
                term_backwardation_ratio=1.18,
                min_candidate_edge_ccy=0.00002,
                min_window_vol_edge=0.02,
                spread_round_trip_multiplier=3.0,
            ),
        ),
        (
            "term_guard_v1_1",
            ScenarioConfig(
                low_percentile_multiplier=1.25,
                high_percentile_multiplier=1.00,
                term_backwardation_ratio=1.12,
                min_candidate_edge_ccy=0.00002,
                min_window_vol_edge=0.02,
                spread_round_trip_multiplier=2.5,
            ),
        ),
        (
            "wide_spread_guard_v1_1",
            ScenarioConfig(
                low_percentile_multiplier=1.25,
                high_percentile_multiplier=0.92,
                term_backwardation_ratio=1.18,
                min_candidate_edge_ccy=0.00002,
                min_window_vol_edge=0.02,
                spread_round_trip_multiplier=4.0,
            ),
        ),
    ]


def compact_axes() -> List[Dict[str, Any]]:
    return list(
        _axes_product(
            rv_percentiles=(0.05, 0.50),
            rv_scales=(1.0,),
            front_iv_multipliers=(1.0, 1.30),
            term_ratios=(1.0, 1.30),
            spread_factors=(1.0,),
            history_days_values=(90,),
        )
    )


def full_axes() -> List[Dict[str, Any]]:
    return list(
        _axes_product(
            rv_percentiles=(0.05, 0.20, 0.50, 0.80, 0.95),
            rv_scales=(0.75, 1.00, 1.25, 1.50),
            front_iv_multipliers=(0.85, 1.00, 1.15, 1.35),
            term_ratios=(0.85, 1.00, 1.15, 1.30),
            spread_factors=(1.00, 2.00, 3.00),
            history_days_values=(10, 90),
        )
    )


def _axes_product(
    rv_percentiles: Sequence[float],
    rv_scales: Sequence[float],
    front_iv_multipliers: Sequence[float],
    term_ratios: Sequence[float],
    spread_factors: Sequence[float],
    history_days_values: Sequence[int],
) -> Iterable[Dict[str, Any]]:
    for rv_pct, rv_scale, iv_mult, term_ratio, spread_factor, history_days in product(
        rv_percentiles,
        rv_scales,
        front_iv_multipliers,
        term_ratios,
        spread_factors,
        history_days_values,
    ):
        labels = []
        if rv_pct <= 0.20 and iv_mult <= 1.15:
            labels.append("LOW_RV_EXPANSION_TRAP")
        if rv_pct >= 0.80 and rv_scale >= 1.25:
            labels.append("STORM_MEAN_REVERSION")
        if term_ratio >= 1.30:
            labels.append("TERM_STRESS_BACKWARDATION")
        if spread_factor >= 3.0:
            labels.append("WIDE_SPREAD")
        if history_days < 30:
            labels.append("COLD_START")
        if iv_mult >= 1.35:
            labels.append("FRONT_IV_RICH")
        yield {
            "rv_percentile": rv_pct,
            "rv_scale": rv_scale,
            "front_iv_multiplier": iv_mult,
            "term_ratio": term_ratio,
            "spread_factor": spread_factor,
            "history_days": history_days,
            "labels": labels,
        }


def build_base_cases(snapshot: Dict[str, Any], sides: Sequence[str] = ("SHORT_CALL", "SHORT_PUT")) -> List[Dict[str, Any]]:
    base_cases: List[Dict[str, Any]] = []
    for side in sides:
        windows = build_window_inputs(snapshot, side=side)
        for window in windows:
            candidates = generate_candidates(
                snapshot,
                window_id=window.window_id,
                expiry=window.expiry,
                side=side,
                forward_vol_hurdle=0.50,
            )
            for idx, candidate in enumerate(candidates):
                base_cases.append(
                    {
                        "base_id": f"{window.window_id}|{idx}|{candidate.short_instrument}|{candidate.protection_instrument}",
                        "side": side,
                        "expiry": window.expiry,
                        "window": window,
                        "candidate": candidate,
                    }
                )
    return base_cases


def _widen_quote(bid: float, ask: float, factor: float) -> Tuple[float, float]:
    if ask <= bid:
        return bid, ask
    mid = (bid + ask) / 2.0
    half = (ask - bid) / 2.0 * factor
    return max(0.0, mid - half), mid + half


def _scaled(value: Optional[float], factor: float) -> Optional[float]:
    v = normalise_iv(value)
    if v is None:
        return None
    return max(0.01, v * factor)


def evaluate_mass_case(base: Dict[str, Any], axis: Dict[str, Any], config: ScenarioConfig) -> Dict[str, Any]:
    window: WindowInput = base["window"]
    candidate: CandidateQuote = base["candidate"]
    front_iv = _scaled(window.front_anchor_iv, axis["front_iv_multiplier"])
    term_ref = front_iv / axis["term_ratio"] if front_iv and axis["term_ratio"] else None
    rv_24h = _scaled(window.rv_24h, axis["rv_scale"])
    rv_72h = _scaled(window.rv_72h, axis["rv_scale"])
    rv_7d = _scaled(window.rv_7d, axis["rv_scale"])
    stressed_window = replace(
        window,
        front_anchor_iv=front_iv or 0.0,
        atm_front_iv=front_iv,
        term_reference_iv_5_10d=term_ref,
        rv_24h=rv_24h or 0.0,
        rv_72h=rv_72h or 0.0,
        rv_7d=rv_7d or 0.0,
        rv_percentile=axis["rv_percentile"],
        history_days=axis["history_days"],
    )
    window_assessment = assess_window(stressed_window, config)
    candidate_assessment = None
    short_bid, short_ask = _widen_quote(candidate.short_bid, candidate.short_ask, axis["spread_factor"])
    prot_bid, prot_ask = _widen_quote(candidate.protection_bid, candidate.protection_ask, axis["spread_factor"])
    if window_assessment["window_vrp_gate"] == PASS:
        stressed_candidate = replace(
            candidate,
            short_bid=short_bid,
            short_ask=short_ask,
            protection_bid=prot_bid,
            protection_ask=prot_ask,
            executable_short_iv=_scaled(candidate.executable_short_iv, axis["front_iv_multiplier"]) or 0.0,
            executable_protection_iv=_scaled(candidate.executable_protection_iv, axis["front_iv_multiplier"]),
            forward_vol_hurdle=window_assessment["forward_vol_hurdle"],
        )
        candidate_assessment = assess_candidate(stressed_candidate, config)

    cand_gate = candidate_assessment["candidate_vrp_gate"] if candidate_assessment else ""
    edge = candidate_assessment["candidate_vrp_edge_ccy"] if candidate_assessment else None
    labels = list(axis["labels"])
    danger_pass = cand_gate == PASS and any(
        label in labels for label in ("LOW_RV_EXPANSION_TRAP", "TERM_STRESS_BACKWARDATION", "WIDE_SPREAD", "COLD_START")
    )
    return {
        "base_id": base["base_id"],
        "side": base["side"],
        "expiry": base["expiry"],
        "short_instrument": candidate.short_instrument,
        "protection_instrument": candidate.protection_instrument,
        "rv_percentile": axis["rv_percentile"],
        "rv_scale": axis["rv_scale"],
        "front_iv_multiplier": axis["front_iv_multiplier"],
        "term_ratio": axis["term_ratio"],
        "spread_factor": axis["spread_factor"],
        "history_days": axis["history_days"],
        "labels": "|".join(labels),
        "window_gate": window_assessment["window_vrp_gate"],
        "candidate_gate": cand_gate,
        "candidate_edge_ccy": edge,
        "forward_vol_hurdle": window_assessment.get("forward_vol_hurdle"),
        "window_reason_codes": "|".join(window_assessment.get("reason_codes") or []),
        "candidate_reason_codes": "|".join((candidate_assessment or {}).get("reason_codes") or []),
        "danger_pass": danger_pass,
    }


def _empty_metrics(config_name: str, config: ScenarioConfig) -> Dict[str, Any]:
    return {
        "config_name": config_name,
        "config": asdict(config),
        "total_cases": 0,
        "window_pass": 0,
        "window_distorted": 0,
        "candidate_evaluated": 0,
        "candidate_pass": 0,
        "danger_pass": 0,
        "low_rv_expansion_pass": 0,
        "term_backwardation_pass": 0,
        "wide_spread_pass": 0,
        "cold_start_pass": 0,
        "thin_edge_pass": 0,
        "pass_edges": [],
    }


def _update_metrics(metrics: Dict[str, Any], row: Dict[str, Any]) -> None:
    metrics["total_cases"] += 1
    if row["window_gate"] == PASS:
        metrics["window_pass"] += 1
    if row["window_gate"] == DISTORTED_REVIEW:
        metrics["window_distorted"] += 1
    if row["candidate_gate"]:
        metrics["candidate_evaluated"] += 1
    if row["candidate_gate"] == PASS:
        metrics["candidate_pass"] += 1
        edge = row["candidate_edge_ccy"] or 0.0
        metrics["pass_edges"].append(edge)
        labels = set((row["labels"] or "").split("|"))
        if row["danger_pass"]:
            metrics["danger_pass"] += 1
        if "LOW_RV_EXPANSION_TRAP" in labels:
            metrics["low_rv_expansion_pass"] += 1
        if "TERM_STRESS_BACKWARDATION" in labels:
            metrics["term_backwardation_pass"] += 1
        if "WIDE_SPREAD" in labels:
            metrics["wide_spread_pass"] += 1
        if "COLD_START" in labels:
            metrics["cold_start_pass"] += 1
        if edge < 0.00002:
            metrics["thin_edge_pass"] += 1


def _finalize_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    edges = metrics.pop("pass_edges")
    metrics["avg_pass_edge_ccy"] = mean(edges) if edges else 0.0
    metrics["candidate_pass_rate"] = (
        metrics["candidate_pass"] / metrics["candidate_evaluated"] if metrics["candidate_evaluated"] else 0.0
    )
    metrics["danger_pass_rate"] = metrics["danger_pass"] / metrics["candidate_pass"] if metrics["candidate_pass"] else 0.0
    metrics["robust_score"] = (
        metrics["avg_pass_edge_ccy"] * 200_000
        + metrics["candidate_pass"] * 0.05
        - metrics["danger_pass"] * 7.0
        - metrics["low_rv_expansion_pass"] * 5.0
        - metrics["term_backwardation_pass"] * 15.0
        - metrics["wide_spread_pass"] * 2.5
        - metrics["cold_start_pass"] * 1.0
        - metrics["thin_edge_pass"] * 4.0
    )
    return metrics


def run_mass_simulation(
    snapshot: Dict[str, Any],
    output_dir: str,
    sides: Sequence[str] = ("SHORT_CALL", "SHORT_PUT"),
    configs: Optional[Sequence[Tuple[str, ScenarioConfig]]] = None,
    axes: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    os.makedirs(output_dir, exist_ok=True)
    configs = list(configs or named_policy_configs())
    axes = list(axes or full_axes())
    base_cases = build_base_cases(snapshot, sides=sides)
    stem = str(snapshot.get("generated_at") or "snapshot").replace(":", "").replace("-", "").replace("+", "_")
    case_csv = os.path.join(output_dir, f"mass_vrp_case_traversal_{stem}.csv")
    summary_json = os.path.join(output_dir, f"mass_vrp_summary_{stem}.json")
    ranking_csv = os.path.join(output_dir, f"mass_vrp_config_ranking_{stem}.csv")

    fieldnames = [
        "config_name",
        "base_id",
        "side",
        "expiry",
        "short_instrument",
        "protection_instrument",
        "rv_percentile",
        "rv_scale",
        "front_iv_multiplier",
        "term_ratio",
        "spread_factor",
        "history_days",
        "labels",
        "window_gate",
        "candidate_gate",
        "candidate_edge_ccy",
        "forward_vol_hurdle",
        "window_reason_codes",
        "candidate_reason_codes",
        "danger_pass",
    ]
    metrics_by_config = {name: _empty_metrics(name, config) for name, config in configs}
    with open(case_csv, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for config_name, config in configs:
            metrics = metrics_by_config[config_name]
            for base in base_cases:
                for axis in axes:
                    row = evaluate_mass_case(base, axis, config)
                    row["config_name"] = config_name
                    writer.writerow(row)
                    _update_metrics(metrics, row)

    ranking = [_finalize_metrics(m) for m in metrics_by_config.values()]
    ranking.sort(key=lambda x: x["robust_score"], reverse=True)
    with open(ranking_csv, "w", encoding="utf-8", newline="") as fh:
        fieldnames_rank = [
            "rank",
            "config_name",
            "robust_score",
            "total_cases",
            "candidate_evaluated",
            "candidate_pass",
            "candidate_pass_rate",
            "danger_pass",
            "danger_pass_rate",
            "low_rv_expansion_pass",
            "term_backwardation_pass",
            "wide_spread_pass",
            "cold_start_pass",
            "thin_edge_pass",
            "avg_pass_edge_ccy",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames_rank)
        writer.writeheader()
        for idx, row in enumerate(ranking, start=1):
            writer.writerow({k: row.get(k) for k in fieldnames_rank} | {"rank": idx})

    summary = {
        "schema_name": "VrpMassScenarioSimulation",
        "schema_version": "nrd.integration.vrp.mass_scenario.v1.0",
        "snapshot_generated_at": snapshot.get("generated_at"),
        "currency": snapshot.get("currency"),
        "index_price": snapshot.get("index_price"),
        "base_case_count": len(base_cases),
        "axis_count": len(axes),
        "config_count": len(configs),
        "total_evaluations": len(base_cases) * len(axes) * len(configs),
        "axes": axes,
        "ranking": ranking,
        "selected_policy": ranking[0] if ranking else None,
        "paths": {
            "summary_json": summary_json,
            "case_csv": case_csv,
            "ranking_csv": ranking_csv,
        },
    }
    with open(summary_json, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    return summary
