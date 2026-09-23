"""Frozen joint-research v1 contract. Not consumed by the FMZ strategy."""
SCHEMA_VERSION = "astra_joint_research@1.0.0"
ASSESSMENT_SCHEMA = "astra_statistical_assessment@1.0.0"
EVENT_SCHEMA = "price_rebalance_candidate@1.0.0"
SEED = 20260914
COMMON_FEATURES = (
    "dte_hours", "short_distance_fraction", "width_fraction", "side_sign",
)
STATISTICAL_FEATURES = (
    "ret_15", "ret_30", "ret_240", "ret_720", "ret_1440",
    "vol_15", "vol_30", "vol_240", "vol_720", "vol_1440",
    "net_flow_15", "net_flow_30", "net_flow_240", "hour_sin", "hour_cos",
    "shock_magnitude", "elapsed_from_shock_min",
)
MECHANISM_FEATURES = (
    "vwap_deviation_15", "vwap_deviation_30", "vwap_migration_15",
    "range_position_15", "range_position_30", "range_expansion_15",
    "efficiency_15", "efficiency_30", "pressure_response_15", "pressure_response_30",
    "adverse_move_since_shock", "favorable_move_since_shock",
)
FEATURE_GROUPS = {
    "statistical": COMMON_FEATURES + STATISTICAL_FEATURES,
    "mechanism": COMMON_FEATURES + MECHANISM_FEATURES,
    "joint": COMMON_FEATURES + STATISTICAL_FEATURES + MECHANISM_FEATURES,
}
PROTOCOL = {
    "schema": SCHEMA_VERSION, "seed": SEED,
    "start_date": "2020-01-01", "last_complete_utc_date": "2026-09-13",
    "train_years": [2020, 2021], "selection_years": [2022], "test_years": [2023],
    "previously_used_years": [2024, 2025, 2026],
    "trigger_abs": 0.65, "cooldown_abs": 0.42, "same_direction_merge_min": 45,
    "opposite_confirm_bars": 2, "max_wait_min": 360,
    "checkpoints_after_cooldown_min": [0, 15, 30],
    "dte_min_exclusive_hours": 8, "dte_max_inclusive_hours": 24,
    "expiry_hour_utc": 8, "widths": [2000, 1500, 2500], "primary_width": 2000,
    "clock_hours_utc": [9, 15, 21],
    "gam_logistic_C": [0.1, 1.0, 10.0], "gam_gamma_alpha": [0.1, 1.0, 10.0],
    "spline_degree": 2, "spline_knots": 5,
    "catboost_depths": [3, 4], "catboost_learning_rate": 0.03,
    "catboost_max_iterations": 500, "catboost_early_stopping_rounds": 40,
    "min_training_delivery_days": 100, "min_positive_delivery_days": 30,
    "bootstrap_repetitions": 2000, "bootstrap_block_days": 7,
    "forward_calendar_days": 90, "forward_quality_check_day": 30,
    "forward_min_delivery_days": 60, "forward_min_complete_weeks": 12,
    "max_quote_age_ms": 10000, "max_leg_skew_ms": 5000,
    "export_atol": 1e-8, "export_rtol": 1e-8, "max_inference_rss_mb": 96,
    "max_training_threads": 6, "max_concurrent_training": 1,
    "net_credit_scenarios": [0.05, 0.10, 0.20],
    "primary_comparisons": ["joint_minus_statistical", "joint_minus_mechanism"],
    "candidate_selection": "nearest strictly OTM actual short; nearest target-width protection; ties narrower",
    "production_changes": False, "llm_calls_added": False,
}
