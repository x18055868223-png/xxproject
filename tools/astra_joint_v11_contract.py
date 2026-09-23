"""Frozen Astra joint-research v1.1 data contract.

This contract is research-side only.  It is intentionally separate from the
FMZ producer, the signal-audit D-S rating, and the v1 price-event dataset so
natural NR card research can use a common point-in-time feature surface.
"""

from __future__ import annotations

SCHEMA_VERSION = "astra_joint_research@1.1.0"
SOURCE_QUALITY_SCHEMA = "astra_joint_source_quality_ledger@1.1.0"
MARKET_OBSERVATION_SCHEMA = "astra_joint_market_observation@1.0.0"
NATURAL_FEATURE_SCHEMA = "astra_joint_natural_common_features@1.0.0"
CANDIDATE_SCHEMA = "astra_joint_natural_candidate@1.1.0"
MODEL_INPUT_SCHEMA = "astra_joint_natural_model_input@1.1.0"
PROTOCOL_SEAL_SCHEMA = "astra_joint_v11_protocol_seal@1.0.0"
DATASET_MANIFEST_SCHEMA = "astra_joint_v11_dataset_manifest@1.0.0"

SEED = 20260915

GEOMETRY_FEATURES = (
    "dte_hours",
    "short_distance_fraction",
    "width_fraction",
    "side_sign",
)

BASE_MARKET_FEATURES = (
    "ret_15",
    "ret_30",
    "ret_240",
    "ret_720",
    "ret_1440",
    "vol_15",
    "vol_30",
    "vol_240",
    "vol_720",
    "vol_1440",
    "net_flow_15",
    "net_flow_30",
    "net_flow_240",
    "hour_sin",
    "hour_cos",
)

SIDE_ALIGNED_STATISTICAL_FEATURES = (
    "adverse_return_15",
    "adverse_return_30",
    "adverse_return_240",
    "adverse_return_720",
    "adverse_return_1440",
    "adverse_flow_15",
    "adverse_flow_30",
    "adverse_flow_240",
)

MECHANISM_FEATURES = (
    "vwap_deviation_15",
    "vwap_deviation_30",
    "vwap_migration_15",
    "range_position_15",
    "range_position_30",
    "range_expansion_15",
    "efficiency_15",
    "efficiency_30",
    "pressure_response_15",
    "pressure_response_30",
)

SIDE_ALIGNED_MECHANISM_FEATURES = (
    "adverse_range_room_15",
    "adverse_range_room_30",
    "adverse_flow_response_15",
    "adverse_flow_response_30",
    "favorable_flow_response_15",
    "favorable_flow_response_30",
)

CSV_COMPAT_IGNORED_COLUMNS = (
    "favorable_range_room_15",
    "favorable_range_room_30",
)

COMMON_FEATURE_GROUPS = {
    "geometry": GEOMETRY_FEATURES,
    "statistical": GEOMETRY_FEATURES
    + BASE_MARKET_FEATURES
    + SIDE_ALIGNED_STATISTICAL_FEATURES,
    "mechanism": GEOMETRY_FEATURES + MECHANISM_FEATURES + SIDE_ALIGNED_MECHANISM_FEATURES,
    "joint": GEOMETRY_FEATURES
    + BASE_MARKET_FEATURES
    + SIDE_ALIGNED_STATISTICAL_FEATURES
    + MECHANISM_FEATURES
    + SIDE_ALIGNED_MECHANISM_FEATURES,
}

PROTOCOL_V11 = {
    "schema": SCHEMA_VERSION,
    "seed": SEED,
    "source_reuse": "astra-joint-v1-20260914 raw caches; read-only",
    "start_date": "2020-01-01",
    "last_complete_utc_date": "2026-09-13",
    "natural_training_observation": "all-market clock observations every 30 minutes",
    "sensitivity_observation_minutes": [60],
    "ordinary_dte_min_exclusive_hours": 8,
    "ordinary_dte_max_inclusive_hours": 24,
    "expiry_hour_utc": 8,
    "primary_width": 2000,
    "widths": [2000, 1500, 2500],
    "reference_price": "last fully closed Binance spot BTCUSDT 1m close at or before observation time",
    "feature_market": "Binance UM BTCUSDT 1m closed bars",
    "spot_reference_market": "Binance spot BTCUSDT 1m closed bars",
    "feature_windows_minutes": [15, 30, 240, 720, 1440],
    "feature_groups": {key: list(value) for key, value in COMMON_FEATURE_GROUPS.items()},
    "model_layers": ["geometry", "statistical", "joint"],
    "feature_amendments": {
        "2026-09-15": (
            "model feature groups omit exact affine mirrors favorable_range_room_15/30; "
            "already materialized CSV columns with those names are descriptive leftovers "
            "and must be ignored by training."
        )
    },
    "price_event_family": {
        "trigger_abs": 0.65,
        "cooldown_abs": 0.42,
        "same_direction_merge_min": 45,
        "opposite_confirm_bars": 2,
        "max_wait_min": 360,
        "checkpoints_after_cooldown_min": [0, 15, 30],
        "scope": "shadow price-rebalance research only; not a natural-card training requirement",
    },
    "rolling_validation": {
        "years": [2022, 2023, 2024, 2025],
        "lookback_months": 24,
        "train_months": 21,
        "calibration_months": 3,
        "step_months": 12,
        "horizon_months": 12,
        "final_fit_cutoff": "2026-08-31",
        "september_2026_role": "interface and domain check only",
    },
    "model_targets": {
        "main": "two-part normalized vertical-spread payout",
        "tail": "protection-leg breach and unconditional worst-five-percent loss",
        "primary_selection_metric": "expected payout mean squared error",
        "statistical_sorting": "choose the side with lower expected_loss_normalized; same-actual-width pairs are the primary side-selection comparison",
    },
    "forward_quote": "common four-leg quote is collected only after the LLM opinion into an independent ledger; it does not rewrite the frozen statistical order",
    "production_changes": False,
    "fmz_version_changed": False,
    "llm_calls_added": False,
}

IDENTITY_FIELDS = (
    "schema",
    "row_id",
    "observation_id",
    "event_family",
    "observation_kind",
    "as_of_ms",
    "as_of_utc",
    "entry_ms",
    "entry_utc",
    "expiry_ms",
    "expiry_utc",
    "delivery_date",
    "delivery_year",
    "split",
    "historical_usage_role",
    "side",
    "target_width",
    "actual_width",
    "short_strike",
    "long_strike",
    "short_name",
    "long_name",
    "short_creation_ms",
    "long_creation_ms",
    "entry_price",
    "price_source",
    "price_observation_ms",
    "price_observation_utc",
    "spot_source_file",
    "um_source_file",
    "option_source_sha256",
    "delivery_source_sha256",
    "source_observation_hash",
    "preoutcome_row_hash",
)

OUTCOME_FIELDS = (
    "settlement_price",
    "payout_btc",
    "loss_normalized",
    "short_leg_breached",
    "protection_leg_breached",
    "outcome_status",
)

CANDIDATE_FIELDS = IDENTITY_FIELDS + COMMON_FEATURE_GROUPS["joint"] + OUTCOME_FIELDS
