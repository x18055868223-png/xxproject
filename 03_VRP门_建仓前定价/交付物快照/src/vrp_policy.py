# -*- coding: utf-8 -*-
"""Implementation policy selected by the VRP mass-scenario traversal."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from vrp_model import ScenarioConfig


SELECTED_POLICY_NAME = "strict_cost_cold_guard_v1_1"


def selected_policy_config() -> ScenarioConfig:
    """Return the current implementation-layer VRP policy configuration."""
    return ScenarioConfig(
        low_percentile_multiplier=1.25,
        high_percentile_multiplier=0.92,
        cold_start_multiplier=1.35,
        term_backwardation_ratio=1.18,
        min_candidate_edge_ccy=0.00005,
        min_window_vol_edge=0.02,
        spread_round_trip_multiplier=3.0,
    )


def implementation_policy_package() -> Dict[str, Any]:
    """Return the selected policy with the simulation evidence used to choose it."""
    return {
        "schema_name": "VrpImplementationPolicy",
        "schema_version": "nrd.integration.vrp.implementation_policy.v1.0",
        "selected_policy_name": SELECTED_POLICY_NAME,
        "config": asdict(selected_policy_config()),
        "selection_basis": {
            "snapshot_generated_at": "2026-06-01T14:44:38.386000+00:00",
            "base_case_count": 20,
            "axis_count": 1920,
            "config_count": 7,
            "total_evaluations": 268800,
            "candidate_pass": 300,
            "danger_pass": 0,
            "cold_start_pass": 0,
            "avg_pass_edge_ccy": 0.00009488537401905692,
            "robust_score": 33.977074803811384,
        },
        "implementation_notes": [
            "cold_start_guard: RV distribution sample is penalized more heavily before min_history_days is met.",
            "full_burn_guard: candidate edge must remain positive after 3.0x spread reserve and round-trip option fees.",
            "vrp_filter_only: selected policy filters windows/candidates and does not select expiry, side, or order venue.",
        ],
    }
