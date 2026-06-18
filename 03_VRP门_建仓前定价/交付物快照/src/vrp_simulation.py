# -*- coding: utf-8 -*-
"""VRP scenario simulation and parameter-grid scoring."""
from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict
from itertools import product
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from vrp_model import (
    BLOCK,
    DISTORTED_REVIEW,
    PASS,
    CandidateQuote,
    ScenarioConfig,
    WindowInput,
    assess_candidate,
    assess_window,
    normalise_iv,
)


def _option_type_for_side(side: str) -> str:
    return "call" if side == "SHORT_CALL" else "put"


def _usable_iv(row: Dict[str, Any]) -> Optional[float]:
    bid_iv = normalise_iv(row.get("bid_iv"))
    if bid_iv and bid_iv > 0:
        return bid_iv if row.get("bid_iv", 0) <= 3 else row.get("bid_iv")
    mark_iv = normalise_iv(row.get("mark_iv"))
    return mark_iv if mark_iv is None else (mark_iv if (row.get("mark_iv") or 0) <= 3 else row.get("mark_iv"))


def _rows(snapshot: Dict[str, Any], expiry: str, side: str) -> List[Dict[str, Any]]:
    opt_type = _option_type_for_side(side)
    return [
        r
        for r in snapshot.get("option_rows", [])
        if r.get("expiry") == expiry and r.get("option_type") == opt_type
    ]


def _closest_delta(rows: Iterable[Dict[str, Any]], target_delta: float) -> Optional[Dict[str, Any]]:
    usable = [r for r in rows if isinstance(r.get("abs_delta"), (int, float)) and _usable_iv(r) is not None]
    if not usable:
        return None
    return min(usable, key=lambda r: abs(float(r["abs_delta"]) - target_delta))


def build_window_inputs(snapshot: Dict[str, Any], side: str, target_delta: float = 0.30) -> List[WindowInput]:
    rv = snapshot.get("rv_context") or {}
    term_expiries = list(snapshot.get("term_expiries") or [])
    term_expiry = term_expiries[0] if term_expiries else None
    out: List[WindowInput] = []
    for expiry in snapshot.get("short_expiries") or []:
        front = _closest_delta(_rows(snapshot, expiry, side), target_delta)
        if not front:
            continue
        term_iv = None
        if term_expiry:
            term = _closest_delta(_rows(snapshot, term_expiry, side), target_delta)
            if term:
                term_iv = _usable_iv(term)
        out.append(
            WindowInput(
                window_id=f"{expiry}|{side}",
                expiry=expiry,
                dte_hours=float(front.get("dte_hours") or 0.0),
                side=side,
                front_anchor_iv=_usable_iv(front) or 0.0,
                atm_front_iv=front.get("mark_iv"),
                term_reference_iv_5_10d=term_iv,
                rv_24h=rv.get("rv_24h"),
                rv_72h=rv.get("rv_72h"),
                rv_7d=rv.get("rv_7d"),
                rv_percentile=rv.get("rv_percentile_90d"),
                history_days=int(rv.get("history_days") or 0),
            )
        )
    return out


def generate_candidates(
    snapshot: Dict[str, Any],
    window_id: str,
    expiry: str,
    side: str,
    delta_range: Tuple[float, float] = (0.15, 0.45),
    width_range: Tuple[float, float] = (2000.0, 2500.0),
    amount: float = 0.1,
    forward_vol_hurdle: float = 0.50,
) -> List[CandidateQuote]:
    rows = _rows(snapshot, expiry, side)
    shorts = [
        r
        for r in rows
        if isinstance(r.get("abs_delta"), (int, float))
        and delta_range[0] <= abs(float(r["abs_delta"])) <= delta_range[1]
        and (r.get("best_bid_price") or 0) > 0
        and (r.get("best_ask_price") or 0) > (r.get("best_bid_price") or 0)
        and _usable_iv(r) is not None
    ]
    candidates: List[CandidateQuote] = []
    spot = float(snapshot.get("index_price") or snapshot.get("estimated_delivery_price") or 0.0)
    for short in shorts:
        s_strike = float(short["strike"])
        for protection in rows:
            p_strike = float(protection["strike"])
            width = abs(p_strike - s_strike)
            if not (width_range[0] <= width <= width_range[1]):
                continue
            if side == "SHORT_CALL" and p_strike <= s_strike:
                continue
            if side == "SHORT_PUT" and p_strike >= s_strike:
                continue
            if (protection.get("best_ask_price") or 0) <= 0:
                continue
            candidates.append(
                CandidateQuote(
                    window_id=window_id,
                    side=side,
                    spot=spot,
                    short_strike=s_strike,
                    protection_strike=p_strike,
                    dte_hours=float(short.get("dte_hours") or 0.0),
                    amount=amount,
                    short_bid=float(short.get("best_bid_price") or 0.0),
                    short_ask=float(short.get("best_ask_price") or 0.0),
                    protection_bid=float(protection.get("best_bid_price") or 0.0),
                    protection_ask=float(protection.get("best_ask_price") or 0.0),
                    executable_short_iv=_usable_iv(short) or 0.0,
                    executable_protection_iv=_usable_iv(protection),
                    forward_vol_hurdle=forward_vol_hurdle,
                    short_instrument=str(short.get("instrument_name") or ""),
                    protection_instrument=str(protection.get("instrument_name") or ""),
                    short_delta=short.get("delta"),
                )
            )
    return candidates


def _count_states(rows: Sequence[Dict[str, Any]], field: str) -> Dict[str, int]:
    return {
        "total": len(rows),
        "pass": sum(1 for r in rows if r.get(field) == PASS),
        "block": sum(1 for r in rows if r.get(field) == BLOCK),
        "distorted_review": sum(1 for r in rows if r.get(field) == DISTORTED_REVIEW),
    }


def score_parameter_set(snapshot: Dict[str, Any], side: str, config: ScenarioConfig) -> Dict[str, Any]:
    window_inputs = build_window_inputs(snapshot, side=side)
    windows = [assess_window(w, config) for w in window_inputs]
    assessed_candidates: List[Dict[str, Any]] = []
    generated_candidate_count = 0
    for window in windows:
        if window.get("window_vrp_gate") != PASS:
            continue
        candidates = generate_candidates(
            snapshot,
            window_id=window["window_id"],
            expiry=window["expiry"],
            side=side,
            forward_vol_hurdle=window.get("forward_vol_hurdle") or 0.50,
        )
        generated_candidate_count += len(candidates)
        assessed_candidates.extend(assess_candidate(c, config) for c in candidates)

    window_summary = _count_states(windows, "window_vrp_gate")
    candidate_summary = _count_states(assessed_candidates, "candidate_vrp_gate")
    pass_edges = [
        c["candidate_vrp_edge_ccy"]
        for c in assessed_candidates
        if c.get("candidate_vrp_gate") == PASS and isinstance(c.get("candidate_vrp_edge_ccy"), (int, float))
    ]
    avg_edge = mean(pass_edges) if pass_edges else 0.0
    pass_rate = (candidate_summary["pass"] / candidate_summary["total"]) if candidate_summary["total"] else 0.0
    # Stage score: enough opportunity, higher full-burn edge, no lax all-pass bias.
    score = (
        avg_edge * 100_000
        + candidate_summary["pass"] * 0.35
        + window_summary["pass"] * 0.75
        - window_summary["distorted_review"] * 1.25
        - max(0.0, pass_rate - 0.45) * 5.0
    )
    if candidate_summary["pass"] == 0:
        score -= 10.0
    return {
        "side": side,
        "config": asdict(config),
        "stage_score": score,
        "window_summary": window_summary,
        "candidate_summary": candidate_summary,
        "generated_candidate_count": generated_candidate_count,
        "avg_pass_edge_ccy": avg_edge,
        "pass_rate": pass_rate,
        "windows": windows,
        "candidate_assessments": assessed_candidates,
    }


def parameter_grid() -> List[ScenarioConfig]:
    configs: List[ScenarioConfig] = []
    for low_mult, high_mult, term_ratio, min_edge, cold_mult, min_window_edge, spread_mult in product(
        (1.15, 1.25, 1.35),
        (0.88, 0.92, 1.00),
        (1.12, 1.18, 1.25),
        (0.0, 0.00002, 0.00005),
        (1.10, 1.20),
        (0.00, 0.02),
        (2.0, 3.0),
    ):
        configs.append(
            ScenarioConfig(
                low_percentile_multiplier=low_mult,
                high_percentile_multiplier=high_mult,
                term_backwardation_ratio=term_ratio,
                min_candidate_edge_ccy=min_edge,
                cold_start_multiplier=cold_mult,
                min_window_vol_edge=min_window_edge,
                spread_round_trip_multiplier=spread_mult,
            )
        )
    return configs


def run_grid(snapshot: Dict[str, Any], sides: Sequence[str] = ("SHORT_CALL", "SHORT_PUT")) -> Dict[str, Any]:
    runs: List[Dict[str, Any]] = []
    for config in parameter_grid():
        side_results = [score_parameter_set(snapshot, side, config) for side in sides]
        stage_score = sum(r["stage_score"] for r in side_results)
        pass_candidates = sum(r["candidate_summary"]["pass"] for r in side_results)
        distorted_windows = sum(r["window_summary"]["distorted_review"] for r in side_results)
        runs.append(
            {
                "config": asdict(config),
                "stage_score": stage_score,
                "pass_candidates": pass_candidates,
                "distorted_windows": distorted_windows,
                "side_results": side_results,
            }
        )
    runs.sort(key=lambda r: r["stage_score"], reverse=True)
    best_by_side = {}
    for side in sides:
        side_runs = []
        for r in runs:
            sr = next((x for x in r["side_results"] if x["side"] == side), None)
            if sr:
                side_runs.append({"config": r["config"], "side_result": sr})
        side_runs.sort(key=lambda x: x["side_result"]["stage_score"], reverse=True)
        best_by_side[side] = side_runs[0] if side_runs else None
    return {
        "schema_name": "VrpSimulationResult",
        "schema_version": "nrd.integration.vrp.simulation.v1.0",
        "snapshot_generated_at": snapshot.get("generated_at"),
        "currency": snapshot.get("currency"),
        "index_price": snapshot.get("index_price"),
        "short_expiries": snapshot.get("short_expiries"),
        "term_expiries": snapshot.get("term_expiries"),
        "rv_context": snapshot.get("rv_context"),
        "total_parameter_sets": len(runs),
        "best": runs[0] if runs else None,
        "best_by_side": best_by_side,
        "top_10": runs[:10],
        "all_runs_compact": [
            {
                "rank": i + 1,
                "stage_score": r["stage_score"],
                "pass_candidates": r["pass_candidates"],
            "distorted_windows": r["distorted_windows"],
            "config": r["config"],
            }
            for i, r in enumerate(runs)
        ],
    }


def write_simulation_outputs(result: Dict[str, Any], output_dir: str) -> Dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    stamp = str(result.get("snapshot_generated_at") or "snapshot").replace(":", "").replace("-", "").replace("+", "_")
    json_path = os.path.join(output_dir, f"vrp_simulation_result_{stamp}.json")
    csv_path = os.path.join(output_dir, f"vrp_parameter_grid_{stamp}.csv")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        fieldnames = [
            "rank",
            "stage_score",
            "pass_candidates",
            "distorted_windows",
            "low_percentile_multiplier",
            "high_percentile_multiplier",
            "term_backwardation_ratio",
            "min_candidate_edge_ccy",
            "cold_start_multiplier",
            "min_window_vol_edge",
            "spread_round_trip_multiplier",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in result.get("all_runs_compact") or []:
            cfg = row["config"]
            writer.writerow(
                {
                    "rank": row["rank"],
                    "stage_score": row["stage_score"],
                    "pass_candidates": row["pass_candidates"],
                    "distorted_windows": row["distorted_windows"],
                    "low_percentile_multiplier": cfg["low_percentile_multiplier"],
                    "high_percentile_multiplier": cfg["high_percentile_multiplier"],
                    "term_backwardation_ratio": cfg["term_backwardation_ratio"],
                    "min_candidate_edge_ccy": cfg["min_candidate_edge_ccy"],
                    "cold_start_multiplier": cfg["cold_start_multiplier"],
                    "min_window_vol_edge": cfg["min_window_vol_edge"],
                    "spread_round_trip_multiplier": cfg["spread_round_trip_multiplier"],
                }
            )
    return {"json": json_path, "csv": csv_path}
