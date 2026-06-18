# -*- coding: utf-8 -*-
"""Neutral Regulation Premium Model demo for FMZ Python.

Generated from the multi-file demo package. This file is read-only by default:
no private keys, no signed trading calls, no order placement.
"""


# ================================================================
# SOURCE: demo/vocabulary.py
# ================================================================
"""Canonical vocabulary for demo states, modules, and reason codes."""

QUALITY_OK = "OK"
QUALITY_STALE = "STALE"
QUALITY_MISSING = "MISSING"
QUALITY_INVALID = "INVALID"
QUALITY_ERROR = "ERROR"
QUALITY_STATES = (
    QUALITY_OK,
    QUALITY_STALE,
    QUALITY_MISSING,
    QUALITY_INVALID,
    QUALITY_ERROR,
)

DECISION_TRADE = "Trade"
DECISION_NO_TRADE = "No Trade"
DECISION_OBSERVE = "Observe"
DECISION_STATES = (
    DECISION_TRADE,
    DECISION_NO_TRADE,
    DECISION_OBSERVE,
)

REJECT_NONE = "none"
REJECT_HARD = "hard_gate"
REJECT_SOFT = "soft_gate"
REJECT_DATA = "data_insufficient"
REJECT_DISCRETIONARY = "discretionary"
REJECT_TYPES = (
    REJECT_NONE,
    REJECT_HARD,
    REJECT_SOFT,
    REJECT_DATA,
    REJECT_DISCRETIONARY,
)

MODULE_EXTERNAL_GATE = "External Gate"
MODULE_ANCHOR = "Anchor"
MODULE_TMVF = "TMV-F"
MODULE_SEQUENCE = (
    MODULE_EXTERNAL_GATE,
    MODULE_ANCHOR,
    MODULE_TMVF,
)

STATE_CLEAR = "Clear"
STATE_CAUTION = "Caution"
STATE_BLOCKED = "Blocked"
STATE_VALID = "Valid"
STATE_WEAK = "Weak"
STATE_INVALID = "Invalid"
STATE_UNCLEAR = "Unclear"
STATE_ADEQUATE = "Adequate"
STATE_MARGINAL = "Marginal"
STATE_INADEQUATE = "Inadequate"

DIRECTION_BULLISH = "Bullish"
DIRECTION_NEUTRAL_TO_BULLISH = "Neutral-to-Bullish"
DIRECTION_NEUTRAL = "Neutral"
DIRECTION_NEUTRAL_TO_BEARISH = "Neutral-to-Bearish"
DIRECTION_BEARISH = "Bearish"
DIRECTION_UNCLEAR = "Unclear"
DIRECTION_STATES = (
    DIRECTION_BULLISH,
    DIRECTION_NEUTRAL_TO_BULLISH,
    DIRECTION_NEUTRAL,
    DIRECTION_NEUTRAL_TO_BEARISH,
    DIRECTION_BEARISH,
    DIRECTION_UNCLEAR,
)

MARKET_ANCHOR_MEAN_REVERSION = "Anchor Mean-Reversion"
MARKET_DIRECTIONAL_DRIFT = "Directional Drift"
MARKET_TREND_ACCELERATION = "Trend Acceleration"
MARKET_FUNDING_CROWDED = "Funding Crowded"
MARKET_UNCLEAR = "Unclear"

SIDE_NONE = "none"
SIDE_PUT_CREDIT_SPREAD = "put_credit_spread"
SIDE_CALL_CREDIT_SPREAD = "call_credit_spread"

SELECTION_TMVF_DIRECTION = "TMVF_DIRECTION"

REASON_READ_ONLY_DEMO = "READ_ONLY_DEMO"
REASON_NO_ORDER_PLACEMENT = "NO_ORDER_PLACEMENT_IMPLEMENTED"
REASON_CODES = (
    REASON_READ_ONLY_DEMO,
    "ALL_SOURCES_UNAVAILABLE",
    "ANCHOR_SOURCE_MISSING",
    "ANCHOR_BAR_MISSING",
    "ANCHOR_EXPIRED",
    "ANCHOR_BAND_UNAVAILABLE",
    "ANCHOR_STALE",
    "ANCHOR_DEVIATION_WIDE",
    "ANCHOR_GRAVITY_WARMING",
    "ANCHOR_GRAVITY_LOW",
    "GEX_RAW_MISSING",
    "GEX_EFFECTIVE_MISSING",
    "GEX_PENDING",
    "GEX_SPOT_GUARD_PENDING",
    "GEX_PENDING_CONSENSUS",
    "GEX_VELOCITY_REJECTED",
    "TMVF_BAR_WINDOW_COLD",
    "TMVF_UNCLEAR",
    "TMVF_KLINE_WINDOW_COLD",
    "TMVF_FUNDING_HISTORY_MISSING",
    "TMVF_WINDOW_CONFLICT",
    "TMVF_MICRO_FLOW_UNALIGNED",
    "TMVF_MICRO_FLOW_TILT",
    "TMVF_MICRO_FLOW_CONFLICT",
    "PRICE_FAR_FROM_ANCHOR",
    "FUNDING_CROWDED",
    REASON_NO_ORDER_PLACEMENT,
)

# ================================================================
# SOURCE: demo/config.py
# ================================================================
"""Runtime configuration for the neutral regulation demo.

All numbers here are initial demo defaults. They are not strategy proof.
"""


CONFIG = {
    "strategy_name": "neutral_regulation_demo",
    "demo_version": "0.5.4",
    "schema_version": "nrd.schema.v0.5.4",
    "asset": "BTC",
    "spot_symbol": "BTCUSDT",
    "futures_symbol": "BTCUSDT",
    "deribit_currency": "BTC",
    "read_only_demo": True,
    "live_fetch_enabled": True,
    "offline_fixture_enabled": False,
    "max_main_loops": 0,
    "loop_sleep_ms": 60000,
    "continue_on_tick_error": True,
    "error_sleep_ms": 15000,
    "startup_log_enabled": True,
    "tick_summary_log_enabled": True,
    "tick_summary_log_every": 1,
    "state_change_log_enabled": True,
    "log_status_enabled": True,
    "status_max_lines": 42,
    "chart_enabled": True,
    "chart_reset_on_start": True,
    "funding_observe_neutral_abs": 0.00005,
    "funding_observe_light_abs": 0.0001,

    "http_timeout_sec": 5,
    "http_retries": 2,
    "http_retry_delays": [0.6, 1.2],

    "binance_spot_base_url": "https://api.binance.com",
    "binance_futures_base_url": "https://fapi.binance.com",
    "binance_spot_agg_trades_path": "/api/v3/aggTrades",
    "binance_futures_agg_trades_path": "/fapi/v1/aggTrades",
    "agg_trades_limit": 1000,
    "spot_depth_limit": 20,

    "macro_yahoo_base_url": "https://query1.finance.yahoo.com/v8/finance/chart",
    "macro_refresh_sec": 3600,
    "macro_range": "10d",
    "macro_interval": "1d",
    "macro_cache_file": "demo/logs/macro_factor_cache.json",
    "macro_symbols": {
        "VOLQ": "^VOLQ",
        "DXY": "DX-Y.NYB",
        "US10Y": "^TNX",
    },
    "macro_symbol_candidates": {
        "VOLQ": ["^VOLQ", "^VXN", "^VIX"],
        "DXY": ["DX-Y.NYB", "DX=F"],
        "US10Y": ["^TNX"],
    },
    "macro_component_weights": {
        "VOLQ": 0.35,
        "DXY": 0.25,
        "US10Y": 0.40,
    },
    "macro_component_scales": {
        "VOLQ": 8.0,
        "DXY": 0.75,
        "US10Y": 12.0,
    },
    "macro_bps_tiers": {
        "VOLQ": [150, 300, 450, 700],
        "DXY": [20, 45, 80, 130],
        "US10Y": [4, 9, 16, 28],
    },
    "macro_volq_shock_bps": 450,
    "macro_volq_single_factor_blocking": False,

    "deribit_base_url": "https://www.deribit.com/api/v2",
    "deribit_index_name": "btc_usd",
    "deribit_instruments_refresh_sec": 300,
    "deribit_min_expiry_hours": 12,
    "deribit_max_expiry_days": 45,
    "strategy_expiry_targets_hours": [24, 48],

    "gex_base_url": "https://gexmonitor.com/api/gex-latest",
    "gex_exchange": "all",
    "gex_lite": "true",
    "gex_min_fetch_interval_ms": 60000,
    "gex_freshness_stale_ms": 4200000,
    "gex_freshness_expired_ms": 4200000,
    "gex_accept_small_absorb_frac": 0.15,
    "gex_accept_consensus_count": 3,
    "gex_accept_candidate_frac": 0.15,
    "gex_accept_bootstrap_band_pct": 0.01,
    "gex_accept_spot_guard_frac": 0.05,
    "gex_accept_spot_guard_sigma": 10.0,
    "gex_accept_watchdog_jump": 2.0,
    "gex_accept_force_accept_sec": 300,
    "gex_accept_guard_multiplier": 2.0,
    "gex_velocity_guard_frac_per_sec": 0.002,
    "gex_observation_window": 5,
    "gex_observation_stddev_mean_max": 0.01,

    "volume_bar_n": 10.0,
    "bar_history_size": 2500,
    "slow_std_window": 60,
    "drain_enabled": True,
    "max_drain_rounds": 5,
    "max_drain_wall_time_ms": 3000,
    "cvd_gap_degrade_enabled": True,

    "band_base_sigma": 3.0,
    "band_max_sigma_bonus": 3.0,
    "band_spring_midpoint": 5.0,
    "band_fallback_half_pct": 0.005,
    "band_half_min_pct": 0.001,
    "band_half_max_pct": 0.015,
    "deviation_threshold": 1.0,
    "anchor_weak_deviation": 1.5,
    "anchor_gravity_window": 144,
    "anchor_gravity_warmup": 20,
    "anchor_gravity_trim_each_side": 1,
    "anchor_gravity_valid_score": 60.0,
    "anchor_gravity_weak_score": 30.0,

    "tmvf_kline_interval": "1h",
    "tmvf_kline_interval_hours": 1,
    "tmvf_supported_interval_hours": [1, 2, 4],
    "tmvf_kline_limit": 160,
    "tmvf_refresh_sec": 300,
    "tmvf_funding_lookback_days": 35,
    "tmvf_funding_limit": 1000,
    "tmvf_funding_interval_hours": 8,
    "tmvf_core_neutral_abs": 0.05,
    "tmvf_core_directional_abs": 0.20,
    "tmvf_core_strong_abs": 0.45,
    "tmvf_component_max_abs": 0.80,
    "tmvf_min_trend_pct": 0.0005,
    "tmvf_24h_trend_weight": 0.50,
    "tmvf_24h_momentum_weight": 0.30,
    "tmvf_24h_volume_weight": 0.20,
    "tmvf_24h_ema_fast": 6,
    "tmvf_24h_ema_slow": 18,
    "tmvf_24h_macd_fast": 6,
    "tmvf_24h_macd_slow": 13,
    "tmvf_24h_macd_signal": 5,
    "tmvf_24h_volume_window": 6,
    "tmvf_48h_trend_weight": 0.50,
    "tmvf_48h_momentum_weight": 0.30,
    "tmvf_48h_volume_weight": 0.20,
    "tmvf_48h_ema_fast": 12,
    "tmvf_48h_ema_slow": 36,
    "tmvf_48h_macd_fast": 12,
    "tmvf_48h_macd_slow": 26,
    "tmvf_48h_macd_signal": 9,
    "tmvf_48h_volume_window": 8,
    "tmvf_avg_volume_window": 24,
    "tmvf_momentum_multiplier": 5.0,
    "tmvf_funding_confirm_abs": 0.15,
    "tmvf_funding_crowded_abs": 0.55,
    "tmvf_funding_extreme_abs": 0.85,
    "tmvf_funding_adjustment_cap": 0.20,
    "tmvf_blend_24h_weight": 0.40,
    "tmvf_blend_48h_weight": 0.60,
    "tmvf_micro_horizons_hours": [4, 8, 12],
    "tmvf_micro_min_bars": 8,
    # MUST stay strictly below min(tmvf_micro_horizons_hours)=4. A look-back
    # window of span H can never internally cover H hours (newest..oldest gap is
    # < H), so an absolute floor >= the smallest horizon makes that window's
    # data_ready unsatisfiable forever. At 4.0 the 4h window was structurally
    # dead (coverage_hours always < 4.0); 8h/12h cleared it and masked the bug.
    # Kept as a true sub-floor so tmvf_micro_ready_coverage_frac is the binding
    # readiness gate (4h ready at 0.65*4=2.6h span).
    "tmvf_micro_min_coverage_hours": 2.0,
    "tmvf_micro_ready_coverage_frac": 0.65,
    "tmvf_micro_neutral_abs": 0.15,
    "tmvf_micro_directional_abs": 0.35,
    "tmvf_micro_momentum_norm": 0.01,
    "tmvf_micro_momentum_weight": 0.65,
    "tmvf_micro_cvd_weight": 0.35,
    "tmvf_momentum_threshold": 0.0015,
    "tmvf_cvd_threshold": 0.0,

    "m_die_interval": "1m",
    "m_die_window_bars": 15,
    "m_die_kline_limit": 40,
    "m_die_return_floor": 0.0006,
    "m_die_micro_return_floor": 0.00005,
    "m_die_z_start": 0.6,
    "m_die_z_full": 1.8,
    "m_die_r_start": 0.0006,
    "m_die_r_full": 0.0025,
    "m_die_e_start": 0.35,
    "m_die_e_full": 0.85,
    "m_die_p_start": 0.45,
    "m_die_p_full": 0.70,
    "m_die_eps": 1e-8,

    "nr_threshold_profile": "relaxed_test",
    "nr_mdie_event_threshold": 0.65,
    "nr_mdie_cooldown_abs": 0.42,
    "nr_mdie_event_on_abs": 0.65,
    "nr_mdie_event_off_abs": 0.42,
    "nr_episode_merge_gap_min": 45,
    "nr_opposite_confirm_ticks": 2,
    "nr_anchor_repair_score": 60.0,
    "nr_anchor_damage_score": 60.0,
    "nr_anchor_damage_nd_abs": 1.0,
    "nr_anchor_damage_drop_score": 10.0,
    "nr_anchor_repair_nd_abs": 0.75,
    "nr_repair_confirm_ticks": 2,
    "nr_repair_context_ttl_min": 360,
    "nr_repair_signal_ttl_min": 60,
    "nr_require_anchor_damage": True,
    "nr_allow_nd_damage_evidence": True,
    "nr_reset_on_opposite_event": True,
    "signal_event_max_count": 10,

    # v0.51 retired the Bias Thesis arbiter; only the macro hard-block flag
    # (read by evaluate_macro_verdict, now an EDB helper) remains.
    "bias_macro_blocking_enabled": True,

    # --- v0.5 EDB direction layer + SRD(skew) + GGR(gamma regime) ---
    # All thresholds below are robust starting DEFAULTS, not proven optima.
    # Calibrate on real data; keep changes versioned (soul.md).
    "edb_enabled": True,
    # evidence base importance; effective weight = base * reliability, then
    # normalized by sum, so absolute scale does not matter, only relative.
    "edb_base_weights": {
        "TMV": 1.00,
        "CVD": 0.70,
        "MACRO": 0.30,
        "FUNDING": 0.25,
        "SRD": 0.70,
        "GGR_SPATIAL": 0.25,
    },
    "edb_tmv_vote_ref": 0.45,       # |tmv_blend| where TMV vote saturates (=strong_abs)
    "edb_macro_vote_ref": 0.46,     # |macro_score| where macro vote saturates (=headwind)
    "edb_score_smooth_n": 1,        # denoise length; 1 = no smoothing (anti-lag default)
    "edb_neutral_score_abs": 0.12,  # |EDB_score| below -> neutral lean
    # informativeness + coverage (v0.5.3): missing/uninformative evidence must
    # lower confidence (info-theory: less independent info -> higher entropy).
    "edb_informative_vote_abs": 0.15,  # |vote| at which an evidence is fully
                                       # informative; below scales weight to 0
    "edb_conf_neutral_min": 35,     # confidence below -> neutral (No-Trade)
    "edb_conf_weak": 50,            # weak lean threshold
    "edb_conf_strong": 68,          # strong lean threshold
    # v0.5.4 confidence recalibration: stop the multiplicative crush. |EDB| is
    # mapped via score_full, and agreement/coverage are FLOORED modulators so a
    # strong, aligned, reasonably-complete read can actually reach tradeable
    # range while genuine conflict/sparse data still reads low.
    "edb_score_full": 0.75,         # |EDB_score| at which strength saturates
    "edb_agreement_floor": 0.60,    # agreement modulator floor
    "edb_coverage_floor": 0.50,     # coverage modulator floor
    "edb_price_confirm_full_pct": 0.75,  # |price%| where CVD confirm saturates
    # CVD strength by rolling distribution of |cvd_norm| (replaces fixed abs
    # thresholds that net/gross imbalance could never reach). Percentile-based
    # => adapts to the asset's own distribution, no hallucinated absolute cut.
    "edb_cvd_strength_window": 240,
    "edb_cvd_strength_min_history": 20,
    "edb_cvd_pctl_weak": 0.40,
    "edb_cvd_pctl_moderate": 0.70,
    "edb_cvd_pctl_strong": 0.88,
    "edb_price_neutral_return_pct_abs": 0.05,  # joint CVD x price neutral band (%)
    # SRD (skew / 25-delta risk reversal) — direction vote from RELATIVE skew
    "srd_enabled": True,
    "srd_target_delta": 0.25,
    "srd_atm_delta": 0.50,
    "srd_rr_baseline_window": 240,
    "srd_rr_baseline_min_history": 12,
    "srd_delta_rr_lookback": 6,
    "srd_min_open_interest": 1.0,
    "srd_near_expiry_downweight_hours": 8.0,
    "srd_vote_scale": 1.0,
    "deribit_option_strikes_each_side": 8,
    "deribit_option_refresh_sec": 300,
    # GGR (global gamma regime gate + spatial pin)
    "ggr_enabled": True,
    "ggr_transition_band_pct": 0.003,
    "ggr_negative_cut_strength": 0.50,
    "ggr_negative_veto_strength": 0.80,
    "ggr_positive_conf_boost_max": 1.15,
    "ggr_negative_conf_floor": 0.40,
    "ggr_pin_min_oi_share": 0.15,
    "ggr_pin_trust_negative_gamma": 0.0,
    "ggr_spatial_vote_cap": 0.25,
    "ggr_pin_distance_ref_pct": 0.02,
    # de-double-count: micro_flow no longer tilts TMV direction (CVD owns flow)
    "tmvf_micro_flow_direction_tilt": False,

    "logs_dir": "demo/logs",
}

CONFIG_RUNTIME_OVERRIDE_PREFIX = "NRD_"
CONFIG_RUNTIME_OVERRIDES_NAME = "NRD_CONFIG_OVERRIDES"


def apply_runtime_config_overrides(config=None, scope=None):
    """Apply optional FMZ/global parameter overrides to CONFIG-like dicts."""
    active = CONFIG if config is None else config
    source = scope if isinstance(scope, dict) else globals()

    dict_overrides = source.get(CONFIG_RUNTIME_OVERRIDES_NAME)
    if isinstance(dict_overrides, dict):
        for key, value in dict_overrides.items():
            if key in active:
                active[key] = _coerce_config_value(value, active[key])

    for key in list(active.keys()):
        prefixed_name = CONFIG_RUNTIME_OVERRIDE_PREFIX + key.upper()
        if prefixed_name in source:
            active[key] = _coerce_config_value(source[prefixed_name],
                                              active[key])
    return active


def _coerce_config_value(value, current):
    if isinstance(current, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "y", "on")
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(float(value))
        except Exception:
            return current
    if isinstance(current, float):
        try:
            return float(value)
        except Exception:
            return current
    if isinstance(current, list):
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, str):
            return [item.strip() for item in value.split(",")]
        return current
    if isinstance(current, str):
        return "" if value is None else str(value)
    return value

# ================================================================
# SOURCE: demo/schemas.py
# ================================================================
"""Canonical schema names and helpers for demo JSON payloads."""



SCHEMA_VERSION = CONFIG.get("schema_version", "nrd.schema.v0.1")

SCHEMA_HTTP_RESULT = "HttpResult"
SCHEMA_DATA_SOURCE_MANIFEST = "DataSourceManifest"
SCHEMA_MARKET_TRADE = "MarketTrade"
SCHEMA_VOLUME_BAR = "VolumeBar"
SCHEMA_GEX_ANCHOR_SNAPSHOT = "GexAnchorSnapshot"
SCHEMA_MODULE_RESULT = "ModuleResult"
SCHEMA_FACTOR_SNAPSHOT = "FactorSnapshot"
SCHEMA_STRATEGY_RECOMMENDATION = "StrategyRecommendation"
SCHEMA_MACRO_PRESSURE = "MacroPressureFactor"
SCHEMA_MDIE = "MicroDirectionalImbalanceExtent"
SCHEMA_NEUTRAL_REPAIR_SIGNAL = "NeutralRepairPreSignal"
SCHEMA_EDB = "EdbDirectional"
SCHEMA_SKEW = "SkewRiskReversal"
SCHEMA_GAMMA_REGIME = "GlobalGammaRegime"
SCHEMA_DECISION_SNAPSHOT = "DecisionSnapshot"
SCHEMA_RUNTIME_FACTS = "RuntimeFacts"
SCHEMA_CONTRACT_AUDIT = "ContractAudit"
SCHEMA_EVALUATION_SNAPSHOT = "EvaluationSnapshot"


def add_schema(payload, schema_name, config=None):
    """Attach the stable demo schema envelope to a dict payload."""
    if not isinstance(payload, dict):
        return payload
    active_config = config or CONFIG
    payload.setdefault("schema_name", schema_name)
    payload.setdefault("schema_version",
                       active_config.get("schema_version", SCHEMA_VERSION))
    return payload

# ================================================================
# SOURCE: demo/data_sources.py
# ================================================================
"""Machine-readable public market-data source manifest."""



DATA_SOURCE_MANIFEST = (
    {
        "source": "binance_spot_agg_trades",
        "venue": "Binance Spot",
        "base_url_config_key": "binance_spot_base_url",
        "path": "/api/v3/aggTrades",
        "adapter_method": "fetch_spot_agg_trades",
        "http_method": "GET",
        "auth": "none",
        "params": ["symbol", "limit", "fromId"],
        "raw_schema": "HttpResult",
        "normalized_schema": "MarketTrade",
        "used_by": ["BarAssembler", "TMV-F", "current_price"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
            "NON_LIST_PAYLOAD->INVALID",
            "BAD_TRADE_FIELD->DROP_ROW",
        ],
    },
    {
        "source": "binance_spot_depth",
        "venue": "Binance Spot",
        "base_url_config_key": "binance_spot_base_url",
        "path": "/api/v3/depth",
        "adapter_method": "fetch_spot_depth",
        "http_method": "GET",
        "auth": "none",
        "params": ["symbol", "limit"],
        "raw_schema": "HttpResult",
        "normalized_schema": "depth_mid_float",
        "used_by": ["current_price"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
            "MISSING_BID_OR_ASK->MISSING_PRICE",
        ],
    },
    {
        "source": "binance_futures_agg_trades",
        "venue": "Binance USD-M Futures",
        "base_url_config_key": "binance_futures_base_url",
        "path": "/fapi/v1/aggTrades",
        "adapter_method": "fetch_futures_agg_trades",
        "http_method": "GET",
        "auth": "none",
        "params": ["symbol", "limit", "fromId"],
        "raw_schema": "HttpResult",
        "normalized_schema": "MarketTrade",
        "used_by": ["future_extension"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
            "NON_LIST_PAYLOAD->INVALID",
        ],
    },
    {
        "source": "binance_futures_premium_index",
        "venue": "Binance USD-M Futures",
        "base_url_config_key": "binance_futures_base_url",
        "path": "/fapi/v1/premiumIndex",
        "adapter_method": "fetch_premium_index",
        "http_method": "GET",
        "auth": "none",
        "params": ["symbol"],
        "raw_schema": "HttpResult",
        "normalized_schema": "futures_facts",
        "used_by": ["TMV-F", "current_price"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
            "BAD_NUMERIC_FIELD->NONE",
        ],
    },
    {
        "source": "binance_futures_klines",
        "venue": "Binance USD-M Futures",
        "base_url_config_key": "binance_futures_base_url",
        "path": "/fapi/v1/klines",
        "adapter_method": "fetch_futures_klines",
        "http_method": "GET",
        "auth": "none",
        "params": ["symbol", "interval", "limit"],
        "raw_schema": "HttpResult",
        "normalized_schema": "TmvfKline",
        "used_by": ["TMV-F", "M-DIE"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
            "NON_LIST_PAYLOAD->INVALID",
            "BAD_KLINE_FIELD->DROP_ROW",
        ],
    },
    {
        "source": "binance_futures_funding_rate",
        "venue": "Binance USD-M Futures",
        "base_url_config_key": "binance_futures_base_url",
        "path": "/fapi/v1/fundingRate",
        "adapter_method": "fetch_funding_rate",
        "http_method": "GET",
        "auth": "none",
        "params": ["symbol", "startTime", "endTime", "limit"],
        "raw_schema": "HttpResult",
        "normalized_schema": "FundingPoint",
        "used_by": ["TMV-F"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
            "NON_LIST_PAYLOAD->INVALID",
            "BAD_FUNDING_FIELD->DROP_ROW",
        ],
    },
    {
        "source": "binance_futures_open_interest",
        "venue": "Binance USD-M Futures",
        "base_url_config_key": "binance_futures_base_url",
        "path": "/fapi/v1/openInterest",
        "adapter_method": "fetch_open_interest",
        "http_method": "GET",
        "auth": "none",
        "params": ["symbol"],
        "raw_schema": "HttpResult",
        "normalized_schema": "future_extension",
        "used_by": ["future_extension"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
        ],
    },
    {
        "source": "binance_futures_taker_ratio",
        "venue": "Binance USD-M Futures",
        "base_url_config_key": "binance_futures_base_url",
        "path": "/futures/data/takerlongshortRatio",
        "adapter_method": "fetch_taker_buy_sell_volume",
        "http_method": "GET",
        "auth": "none",
        "params": ["symbol", "period", "limit"],
        "raw_schema": "HttpResult",
        "normalized_schema": "future_extension",
        "used_by": ["future_extension"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
        ],
    },
    {
        "source": "deribit_instruments",
        "venue": "Deribit",
        "base_url_config_key": "deribit_base_url",
        "path": "/public/get_instruments",
        "adapter_method": "get_instruments",
        "http_method": "GET",
        "auth": "none",
        "params": ["currency", "kind", "expired"],
        "raw_schema": "HttpResult",
        "normalized_schema": "instrument_dict",
        "used_by": ["StrategyRecommendation", "expiry_discovery"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
            "MISSING_RESULT->INVALID",
            "INACTIVE_OR_BAD_INSTRUMENT->DROP_ROW",
        ],
    },
    {
        "source": "deribit_index_price",
        "venue": "Deribit",
        "base_url_config_key": "deribit_base_url",
        "path": "/public/get_index_price",
        "adapter_method": "get_index_price",
        "http_method": "GET",
        "auth": "none",
        "params": ["index_name"],
        "raw_schema": "HttpResult",
        "normalized_schema": "index_price_float",
        "used_by": ["current_price"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
            "MISSING_RESULT->INVALID",
        ],
    },
    {
        "source": "gexmonitor_latest",
        "venue": "GEXMonitor",
        "base_url_config_key": "gex_base_url",
        "path": "/api/gex-latest",
        "adapter_method": "fetch_latest",
        "http_method": "GET",
        "auth": "none",
        "params": ["asset", "exchange", "lite", "t"],
        "raw_schema": "HttpResult",
        "normalized_schema": "GexAnchorSnapshot",
        "used_by": ["Anchor", "current_price"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
            "UNRECOGNIZED_PAYLOAD->INVALID",
            "FETCH_INTERVAL_HIT->CACHED_NO_REINGEST",
        ],
    },
    {
        "source": "yahoo_macro_chart",
        "venue": "Yahoo Finance",
        "base_url_config_key": "macro_yahoo_base_url",
        "path": "/{symbol}",
        "adapter_method": "MacroPressureFactor.refresh",
        "http_method": "GET",
        "auth": "none",
        "params": ["range", "interval", "includePrePost", "events"],
        "raw_schema": "HttpResult",
        "normalized_schema": "MacroPressureFactor",
        "used_by": ["MPF"],
        "quality_rules": [
            "HTTP_OR_NETWORK_ERROR->ERROR",
            "JSON_PARSE_ERROR->INVALID",
            "EMPTY_CHART_RESULT->MISSING",
            "BAD_CLOSE_FIELD->DROP_ROW",
            "CACHE_FALLBACK->STALE",
        ],
    },
)


def build_data_source_manifest(config=None):
    config = config or CONFIG
    return add_schema({
        "source_count": len(DATA_SOURCE_MANIFEST),
        "sources": [dict(item) for item in DATA_SOURCE_MANIFEST],
    }, SCHEMA_DATA_SOURCE_MANIFEST, config)


def validate_data_source_manifest(config=None):
    config = config or CONFIG
    errors = []
    warnings = []
    checks = {
        "source_count": len(DATA_SOURCE_MANIFEST),
        "all_public": True,
        "all_get": True,
        "base_url_config_keys_exist": True,
        "required_fields_present": True,
    }
    required = (
        "source",
        "venue",
        "base_url_config_key",
        "path",
        "adapter_method",
        "http_method",
        "auth",
        "params",
        "raw_schema",
        "normalized_schema",
        "used_by",
        "quality_rules",
    )
    seen = set()
    for index, item in enumerate(DATA_SOURCE_MANIFEST):
        label = "source_" + str(index)
        missing = [key for key in required if key not in item]
        if missing:
            checks["required_fields_present"] = False
            errors.append(label + "_missing_" + ",".join(missing))
        source_name = item.get("source")
        if source_name in seen:
            errors.append(label + "_duplicate_source")
        seen.add(source_name)
        if item.get("auth") != "none":
            checks["all_public"] = False
            errors.append(label + "_auth_not_none")
        if item.get("http_method") != "GET":
            checks["all_get"] = False
            errors.append(label + "_method_not_get")
        if item.get("base_url_config_key") not in config:
            checks["base_url_config_keys_exist"] = False
            errors.append(label + "_missing_base_url_config")
        if not item.get("quality_rules"):
            warnings.append(label + "_missing_quality_rules")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }

# ================================================================
# SOURCE: demo/contracts.py
# ================================================================
"""Runtime contract checks for demo payloads and safety invariants."""



def validate_evaluation_contract(decision_snapshot, module_results,
                                 factor_snapshot=None, config=None):
    """Validate the per-evaluation payload contract without raising."""
    config = config or CONFIG
    errors = []
    warnings = []
    checks = {}

    _check_schema(decision_snapshot, SCHEMA_DECISION_SNAPSHOT,
                  "decision_snapshot", checks, errors, config)
    checks["decision_state"] = decision_snapshot.get("decision") in DECISION_STATES
    if not checks["decision_state"]:
        errors.append("decision_not_in_vocabulary")

    checks["reject_type"] = decision_snapshot.get("reject_type") in REJECT_TYPES
    if not checks["reject_type"]:
        errors.append("reject_type_not_in_vocabulary")

    runtime_facts = decision_snapshot.get("runtime_facts") or {}
    _check_schema(runtime_facts, SCHEMA_RUNTIME_FACTS,
                  "runtime_facts", checks, errors, config)

    active_factor_snapshot = (
        factor_snapshot or decision_snapshot.get("factor_snapshot") or {})
    _check_schema(active_factor_snapshot, SCHEMA_FACTOR_SNAPSHOT,
                  "factor_snapshot", checks, errors, config)
    _check_v04_factor_snapshot(active_factor_snapshot, checks, errors, config)

    _check_modules(module_results, checks, errors, config)
    legacy_order_key = "order" + "_intent"
    checks["legacy_order_boundary_absent"] = (
        legacy_order_key not in decision_snapshot)
    if not checks["legacy_order_boundary_absent"]:
        errors.append("legacy_order_boundary_present")
    _check_strategy_recommendation(
        decision_snapshot.get("strategy_recommendation"),
        checks, errors, config)
    _check_data_source_manifest(checks, errors, warnings, config)

    audit = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }
    return add_schema(audit, SCHEMA_CONTRACT_AUDIT, config)


def _check_schema(payload, expected_name, label, checks, errors, config):
    is_dict = isinstance(payload, dict)
    checks[label + "_is_dict"] = is_dict
    if not is_dict:
        errors.append(label + "_not_dict")
        return
    checks[label + "_schema_name"] = payload.get("schema_name") == expected_name
    checks[label + "_schema_version"] = (
        payload.get("schema_version") == config.get("schema_version"))
    if not checks[label + "_schema_name"]:
        errors.append(label + "_schema_name_mismatch")
    if not checks[label + "_schema_version"]:
        errors.append(label + "_schema_version_mismatch")


def _check_modules(module_results, checks, errors, config):
    checks["module_results_is_list"] = isinstance(module_results, list)
    if not checks["module_results_is_list"]:
        errors.append("module_results_not_list")
        return

    module_names = [item.get("module") for item in module_results
                    if isinstance(item, dict)]
    checks["module_sequence"] = module_names == list(MODULE_SEQUENCE)
    if not checks["module_sequence"]:
        errors.append("module_sequence_mismatch")

    checks["module_count"] = len(module_names) == len(MODULE_SEQUENCE)
    if not checks["module_count"]:
        errors.append("module_count_mismatch")

    all_schema_ok = True
    all_quality_ok = True
    for index, item in enumerate(module_results):
        label = "module_" + str(index)
        _check_schema(item, SCHEMA_MODULE_RESULT, label, checks, errors, config)
        if not isinstance(item, dict):
            all_schema_ok = False
            all_quality_ok = False
            continue
        if item.get("schema_name") != SCHEMA_MODULE_RESULT:
            all_schema_ok = False
        if item.get("quality") not in QUALITY_STATES:
            all_quality_ok = False
            errors.append(label + "_quality_not_in_vocabulary")
    checks["module_schemas"] = all_schema_ok
    checks["module_quality_states"] = all_quality_ok


def _check_strategy_recommendation(strategy_recommendation, checks, errors,
                                   config):
    if not isinstance(strategy_recommendation, dict):
        checks["strategy_recommendation_is_dict"] = False
        errors.append("strategy_recommendation_not_dict")
        return
    _check_schema(strategy_recommendation, SCHEMA_STRATEGY_RECOMMENDATION,
                  "strategy_recommendation", checks, errors, config)
    checks["strategy_recommendation_has_signal"] = (
        strategy_recommendation.get("signal") is not None)
    checks["strategy_recommendation_has_strategy_type"] = (
        "strategy_type" in strategy_recommendation)
    checks["strategy_recommendation_has_expiry_24h"] = (
        isinstance(strategy_recommendation.get("expiry_24h"), dict)
        and strategy_recommendation["expiry_24h"].get("target_hours") == 24.0)
    checks["strategy_recommendation_has_expiry_48h"] = (
        isinstance(strategy_recommendation.get("expiry_48h"), dict)
        and strategy_recommendation["expiry_48h"].get("target_hours") == 48.0)
    checks["strategy_recommendation_external_order_layer"] = (
        strategy_recommendation.get("order_layer")
        == "external_execution_program")
    blocked_keys = (
        "legs",
        "orders",
        "sell" + "_leg",
        "buy" + "_leg",
        "quantity",
        "limit_price",
        "pricing",
        "risk_snapshot",
        "cost_snapshot",
    )
    checks["strategy_recommendation_no_execution_fields"] = not any(
        key in strategy_recommendation for key in blocked_keys)
    for key in (
            "strategy_recommendation_has_signal",
            "strategy_recommendation_has_strategy_type",
            "strategy_recommendation_has_expiry_24h",
            "strategy_recommendation_has_expiry_48h",
            "strategy_recommendation_external_order_layer",
            "strategy_recommendation_no_execution_fields"):
        if not checks[key]:
            errors.append(key + "_failed")


def _check_v04_factor_snapshot(factor_snapshot, checks, errors, config):
    if not isinstance(factor_snapshot, dict):
        checks["factor_snapshot_v04_signal_path"] = False
        errors.append("factor_snapshot_v04_signal_path_failed")
        return
    _check_schema(
        factor_snapshot.get("neutral_repair_signal"),
        SCHEMA_NEUTRAL_REPAIR_SIGNAL,
        "neutral_repair_signal",
        checks,
        errors,
        config,
    )
    _check_schema(
        factor_snapshot.get("edb"),
        SCHEMA_EDB,
        "edb",
        checks,
        errors,
        config,
    )
    forbidden = (
        "combo_risk",
        "depth_cost",
        "candidate" + "_combo",
        "order" + "_intent",
    )
    checks["factor_snapshot_no_removed_option_fields"] = not any(
        key in factor_snapshot for key in forbidden)
    if not checks["factor_snapshot_no_removed_option_fields"]:
        errors.append("factor_snapshot_removed_option_field_present")


def _check_data_source_manifest(checks, errors, warnings, config):
    manifest_audit = validate_data_source_manifest(config)
    checks["data_source_manifest"] = manifest_audit.get("ok") is True
    checks["data_source_count"] = manifest_audit.get(
        "checks", {}).get("source_count")
    for error in manifest_audit.get("errors", []):
        errors.append("data_source_manifest:" + error)
    for warning in manifest_audit.get("warnings", []):
        warnings.append("data_source_manifest:" + warning)

# ================================================================
# SOURCE: demo/utils.py
# ================================================================
"""Small standard-library helpers shared by demo modules."""

import datetime
import builtins
import math
import time


def now_ms():
    return int(time.time() * 1000)


def safe_float(value):
    try:
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except Exception:
        return None


def safe_int(value):
    try:
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        return int(float(value))
    except Exception:
        return None


def utc8_text(timestamp_ms):
    if timestamp_ms is None:
        return "-"
    dt = datetime.datetime.utcfromtimestamp(timestamp_ms / 1000.0)
    dt = dt + datetime.timedelta(hours=8)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def population_std(values):
    vals = [safe_float(v) for v in values]
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    mean = sum(vals) / float(len(vals))
    variance = sum((v - mean) ** 2 for v in vals) / float(len(vals))
    return math.sqrt(variance)


def detrended_std(values):
    vals = [safe_float(v) for v in values]
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n < 3:
        return None

    mean_x = (n - 1) / 2.0
    mean_y = sum(vals) / float(n)
    var_x = 0.0
    cov_xy = 0.0
    for i, y in enumerate(vals):
        dx = i - mean_x
        var_x += dx * dx
        cov_xy += dx * (y - mean_y)
    if var_x <= 0:
        return population_std(vals)
    slope = cov_xy / var_x
    intercept = mean_y - slope * mean_x
    residuals = [vals[i] - (slope * i + intercept) for i in range(n)]
    return population_std(residuals)


def clamp(value, lower, upper):
    if value is None:
        return None
    return max(lower, min(upper, value))


def fmz_log(*parts):
    text = " ".join(str(p) for p in parts)
    log_func = globals().get("Log") or getattr(builtins, "Log", None)
    if callable(log_func):
        log_func(text)
    else:
        print(text)


def fmz_status(text):
    status_func = globals().get("LogStatus") or getattr(builtins, "LogStatus", None)
    if callable(status_func):
        status_func(text)
    else:
        print(text)


def fmz_sleep(ms):
    sleep_func = globals().get("Sleep") or getattr(builtins, "Sleep", None)
    if callable(sleep_func):
        sleep_func(ms)
    else:
        time.sleep(ms / 1000.0)

# ================================================================
# SOURCE: demo/charting.py
# ================================================================
"""FMZ chart maintenance for the read-only demo."""

import builtins



class DemoChart:
    def __init__(self, config=None):
        self.config = config or CONFIG
        self.chart = None
        self.initialized = False
        self.points_added = 0
        self.last_error = None

    def update(self, decision_snapshot):
        if not self.config.get("chart_enabled", True):
            return False
        chart_func = globals().get("Chart") or getattr(builtins, "Chart", None)
        if not callable(chart_func):
            self.last_error = "Chart函数不可用"
            return False
        try:
            if not self.initialized:
                self._init_chart(chart_func)
            ts_ms = decision_snapshot.get("ts_ms")
            runtime = decision_snapshot.get("runtime_facts") or {}
            factors = decision_snapshot.get("factor_snapshot") or {}
            anchor = factors.get("anchor") or {}
            flow = factors.get("flow") or {}

            price = safe_float(runtime.get("current_bar_close"))
            if price is None:
                price = safe_float(runtime.get("current_price"))
            anchor_score = safe_float(anchor.get("anchor_gravity_ref_score"))
            anchor_axis = safe_float(anchor.get("effective_flip_point"))
            if anchor_axis is None:
                anchor_axis = safe_float(anchor.get("flip_point"))
            tmv_score = safe_float(flow.get("tmv_blend"))
            m_die = safe_float((factors.get("m_die") or {}).get("m_die"))
            if ts_ms is None:
                return False
            if price is not None:
                self.chart.add(0, [ts_ms, price])
                self.points_added += 1
            if anchor_axis is not None:
                self.chart.add(1, [ts_ms, anchor_axis])
                self.points_added += 1
            if anchor_score is not None:
                self.chart.add(2, [ts_ms, anchor_score])
                self.points_added += 1
            if tmv_score is not None:
                self.chart.add(3, [ts_ms, tmv_score])
                self.points_added += 1
            if m_die is not None:
                self.chart.add(4, [ts_ms, m_die])
                self.points_added += 1
            self.last_error = None
            return True
        except Exception as error:
            self.last_error = str(error)
            fmz_log("图表更新失败", str(error))
            return False

    def snapshot(self):
        return {
            "enabled": bool(self.config.get("chart_enabled", True)),
            "initialized": self.initialized,
            "points_added": self.points_added,
            "last_error": self.last_error,
        }

    def _init_chart(self, chart_func):
        chart_config = {
            "title": {"text": "NRD 0.4.1 前置信号观察图"},
            "xAxis": {"type": "datetime"},
            "yAxis": [
                {"title": {"text": "BTC价格"}, "opposite": False},
                {
                    "title": {"text": "Anchor分数"},
                    "opposite": True,
                    "min": 0,
                    "max": 100,
                    "plotLines": [
                        {"value": 30, "color": "#9E9E9E",
                         "dashStyle": "Dot", "width": 1},
                        {"value": 60, "color": "#9E9E9E",
                         "dashStyle": "Dot", "width": 1},
                        {"value": 90, "color": "#9E9E9E",
                         "dashStyle": "Dot", "width": 1},
                    ],
                },
                {
                    "title": {"text": "TMV-F分数"},
                    "opposite": True,
                    "min": -1,
                    "max": 1,
                    "plotLines": [
                        {"value": 0, "color": "#9E9E9E",
                         "dashStyle": "ShortDash", "width": 1},
                    ],
                },
            ],
            "series": [
                {"id": "price", "name": "实时成交价",
                 "data": [], "yAxis": 0, "color": "#2196F3"},
                {"id": "processed_anchor_axis", "name": "处理后中轴",
                 "data": [], "yAxis": 0, "color": "#7E57C2",
                 "lineWidth": 2, "dashStyle": "ShortDash"},
                {"id": "anchor_score", "name": "Anchor分数",
                 "data": [], "yAxis": 1, "color": "#009688",
                 "lineWidth": 2},
                {"id": "tmv_score", "name": "TMV-F分数",
                 "data": [], "yAxis": 2, "color": "#F44336",
                 "lineWidth": 2},
                {"id": "m_die", "name": "M-DIE 15m",
                 "data": [], "yAxis": 2, "color": "#FF9800",
                 "lineWidth": 2},
            ],
        }
        self.chart = chart_func(chart_config)
        if (self.config.get("chart_reset_on_start", True)
                and self.chart and hasattr(self.chart, "reset")):
            self.chart.reset()
        self.initialized = True

# ================================================================
# SOURCE: demo/http_client.py
# ================================================================
"""HTTP JSON client for FMZ Python and local demo runs."""

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request



class HttpClient:
    def __init__(self, config=None):
        self.config = config or CONFIG
        self._ssl_context = ssl.create_default_context()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ssl_context))

    def get_json(self, url, params=None, headers=None, timeout_sec=None,
                 retries=None):
        timeout = timeout_sec or self.config["http_timeout_sec"]
        retry_count = self.config["http_retries"] if retries is None else retries
        retry_delays = self.config.get("http_retry_delays", [0.6, 1.2])
        full_url = self._with_params(url, params)
        req_headers = {
            "User-Agent": "Mozilla/5.0 (neutral-regulation-demo)",
            "Accept": "application/json,text/plain,*/*",
            "Cache-Control": "no-cache",
        }
        if headers:
            req_headers.update(headers)

        last_error = None
        for attempt in range(retry_count + 1):
            try:
                req = urllib.request.Request(
                    url=full_url, headers=req_headers, method="GET")
                resp = self._opener.open(req, timeout=timeout)
                status = getattr(resp, "status", 200)
                body = resp.read().decode("utf-8", errors="replace")
                if status != 200:
                    return add_schema({
                        "quality": QUALITY_ERROR,
                        "data": None,
                        "error": "http_status_" + str(status),
                        "url": full_url,
                    }, SCHEMA_HTTP_RESULT, self.config)
                try:
                    return add_schema({
                        "quality": QUALITY_OK,
                        "data": json.loads(body),
                        "error": None,
                        "url": full_url,
                    }, SCHEMA_HTTP_RESULT, self.config)
                except Exception as error:
                    return add_schema({
                        "quality": QUALITY_INVALID,
                        "data": None,
                        "error": "json_parse_error:" + str(error),
                        "url": full_url,
                    }, SCHEMA_HTTP_RESULT, self.config)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                    OSError) as error:
                last_error = str(error)
                if attempt < retry_count:
                    delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                    time.sleep(delay)

        return add_schema({
            "quality": QUALITY_ERROR,
            "data": None,
            "error": last_error or "unknown_http_error",
            "url": full_url,
        }, SCHEMA_HTTP_RESULT, self.config)

    @staticmethod
    def _with_params(url, params):
        if not params:
            return url
        query = urllib.parse.urlencode(params)
        sep = "&" if "?" in url else "?"
        return url + sep + query

# ================================================================
# SOURCE: demo/macro_factor.py
# ================================================================
"""Macro Pressure Factor (MPF) computation and LKGV cache handling."""

import json
import math
import os
import urllib.parse



MACRO_COMPONENT_ORDER = ("VOLQ", "DXY", "US10Y")


class MacroPressureFactor:
    def __init__(self, http_client, config=None):
        self.http = http_client
        self.config = config or CONFIG
        self.last_refresh_ms = None
        self.last_snapshot = None

    def is_stale(self):
        if self.last_snapshot is None or self.last_refresh_ms is None:
            return True
        refresh_ms = int(self.config.get("macro_refresh_sec", 3600)) * 1000
        if refresh_ms <= 0:
            return False
        return now_ms() - self.last_refresh_ms >= refresh_ms

    def refresh(self):
        snapshot = compute_macro_pressure(
            self._load_components(), self.config)
        self.last_refresh_ms = now_ms()
        self.last_snapshot = snapshot
        return snapshot

    def _load_components(self):
        cached = self._read_cache()
        components = []
        cache_changed = False
        for key in MACRO_COMPONENT_ORDER:
            component = self._fetch_component(key)
            if component is None:
                component = self._component_from_cache(key, cached)
            else:
                cache_changed = True
                cached[key] = component
            if component is None:
                component = unavailable_macro_component(key, self.config)
            components.append(component)
        if cache_changed:
            self._write_cache(cached)
        return components

    def _fetch_component(self, key):
        candidates = (self.config.get("macro_symbol_candidates") or {}).get(key)
        if not candidates:
            symbol = (self.config.get("macro_symbols") or {}).get(key)
            candidates = [symbol] if symbol else []
        for symbol in candidates:
            bars = self._fetch_chart_bars(symbol)
            if bars:
                return build_macro_component(
                    key, symbol, "live", bars, self.config)
        return None

    def _fetch_chart_bars(self, symbol):
        if not symbol:
            return []
        base = str(self.config.get("macro_yahoo_base_url", "")).rstrip("/")
        url = base + "/" + urllib.parse.quote(str(symbol), safe="")
        result = self.http.get_json(url, params={
            "range": self.config.get("macro_range", "10d"),
            "interval": self.config.get("macro_interval", "1d"),
            "includePrePost": "false",
            "events": "history",
        }, timeout_sec=self.config.get("http_timeout_sec", 5))
        if result.get("quality") != QUALITY_OK:
            return []
        return parse_yahoo_chart_bars(result.get("data"))

    def _read_cache(self):
        path = self.config.get("macro_cache_file")
        if not path or not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        components = payload.get("components")
        return components if isinstance(components, dict) else {}

    def _write_cache(self, components):
        path = self.config.get("macro_cache_file")
        if not path:
            return False
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "updated_at_ms": now_ms(),
                    "components": components,
                }, handle, ensure_ascii=False, sort_keys=True)
            return True
        except Exception:
            return False

    def _component_from_cache(self, key, cached):
        component = cached.get(key) if isinstance(cached, dict) else None
        if not isinstance(component, dict):
            return None
        cached_component = dict(component)
        current_ts_ms = safe_float(cached_component.get("current_ts_ms"))
        if current_ts_ms is not None:
            cache_age_ms = now_ms() - current_ts_ms
            if cache_age_ms > 7 * 24 * 60 * 60 * 1000:
                return None
            cached_component["cache_age_ms"] = cache_age_ms
        cached_component["source_status"] = "lkgv_cache"
        return cached_component


def parse_yahoo_chart_bars(payload):
    try:
        result = (payload.get("chart", {}).get("result") or [])[0]
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [])[0]
        closes = quote.get("close") or []
    except Exception:
        return []
    bars_by_ts = {}
    for ts, close in zip(timestamps, closes):
        timestamp = safe_float(ts)
        close_value = safe_float(close)
        if timestamp is None or close_value is None or close_value <= 0:
            continue
        bars_by_ts[int(timestamp)] = {
            "timestamp": int(timestamp),
            "close": close_value,
        }
    return [bars_by_ts[key] for key in sorted(bars_by_ts.keys())]


def build_macro_component(key, source_symbol, source_status, bars, config=None):
    config = config or CONFIG
    bars = [bar for bar in bars or [] if safe_float(bar.get("close"))]
    bars.sort(key=lambda item: item.get("timestamp") or 0)
    if len(bars) < 2:
        return unavailable_macro_component(key, config, source_symbol)
    current = bars[-1]
    reference = _reference_bar(bars, current.get("timestamp"))
    current_close = safe_float(current.get("close"))
    reference_close = safe_float(reference.get("close")) if reference else None
    if current_close is None or reference_close is None or reference_close <= 0:
        return unavailable_macro_component(key, config, source_symbol)
    current_ts_ms = int(current.get("timestamp") or 0) * 1000
    reference_ts_ms = int(reference.get("timestamp") or 0) * 1000
    return macro_component_from_values(
        key, source_symbol, source_status, current_close, reference_close,
        current_ts_ms, reference_ts_ms, config, cache_age_ms=0)


def macro_component_from_values(key, source_symbol, source_status,
                                current_close, reference_close,
                                current_ts_ms=None, reference_ts_ms=None,
                                config=None, cache_age_ms=0):
    config = config or CONFIG
    current_close = safe_float(current_close)
    reference_close = safe_float(reference_close)
    if current_close is None or reference_close is None or reference_close <= 0:
        return unavailable_macro_component(key, config, source_symbol)
    scoring = macro_scoring_value(key, current_close, reference_close)
    change_pct_3d = scoring.get("change_pct_3d")
    scoring_value = scoring.get("scoring_value")
    scoring_unit = scoring.get("scoring_unit")
    scoring_bps = scoring.get("scoring_bps")
    strength, tier, tier_cn = macro_strength(key, scoring_bps, config)
    weight = float((config.get("macro_component_weights") or {}).get(key, 0.0))
    scale = float((config.get("macro_component_scales") or {}).get(key, 1.0))
    if scale <= 0:
        scale = 1.0
    normalized_pressure = math.tanh((safe_float(scoring_value) or 0.0) / scale)
    component_score = normalized_pressure * weight
    return {
        "key": key,
        "source_symbol": source_symbol,
        "source_status": source_status,
        "current_close": current_close,
        "reference_close": reference_close,
        "current_ts_ms": current_ts_ms,
        "reference_ts_ms": reference_ts_ms,
        "change_pct_3d": change_pct_3d,
        "scoring_value": scoring_value,
        "scoring_unit": scoring_unit,
        "scoring_bps": scoring_bps,
        "abs_scoring_bps": abs(scoring_bps),
        "component_scale": scale,
        "normalized_pressure": normalized_pressure,
        "tier": tier,
        "tier_cn": tier_cn,
        "strength": abs(normalized_pressure),
        "tier_strength": strength,
        "weight": weight,
        "component_score": component_score,
        "impact": macro_impact_text(key, scoring_bps),
        "meaning_cn": macro_component_meaning_cn(key),
        "observation_cn": macro_component_observation_cn(
            key, scoring_bps, tier_cn),
        "cache_age_ms": 0,
    }


def macro_scoring_value(key, current_close, reference_close):
    current_close = safe_float(current_close)
    reference_close = safe_float(reference_close)
    if current_close is None or reference_close is None or reference_close <= 0:
        return {
            "change_pct_3d": None,
            "scoring_value": None,
            "scoring_unit": None,
            "scoring_bps": None,
        }
    change_pct_3d = (current_close / reference_close - 1.0) * 100.0
    if key == "US10Y":
        multiplier = 10.0
        if max(abs(current_close), abs(reference_close)) < 20:
            multiplier = 100.0
        scoring_value = (current_close - reference_close) * multiplier
        scoring_unit = "bps"
        scoring_bps = scoring_value
    else:
        scoring_value = change_pct_3d
        scoring_unit = "pct"
        scoring_bps = change_pct_3d * 100.0
    return {
        "change_pct_3d": change_pct_3d,
        "scoring_value": scoring_value,
        "scoring_unit": scoring_unit,
        "scoring_bps": scoring_bps,
    }


def unavailable_macro_component(key, config=None, source_symbol=None):
    config = config or CONFIG
    if source_symbol is None:
        source_symbol = (config.get("macro_symbols") or {}).get(key)
    return {
        "key": key,
        "source_symbol": source_symbol,
        "source_status": "unavailable",
        "current_close": None,
        "reference_close": None,
        "current_ts_ms": None,
        "reference_ts_ms": None,
        "change_pct_3d": None,
        "scoring_value": None,
        "scoring_unit": None,
        "scoring_bps": None,
        "abs_scoring_bps": None,
        "component_scale": (config.get("macro_component_scales") or {}).get(
            key),
        "normalized_pressure": 0.0,
        "tier": "unavailable",
        "tier_cn": "不可用",
        "strength": 0.0,
        "tier_strength": 0.0,
        "weight": float((config.get("macro_component_weights") or {}).get(
            key, 0.0)),
        "component_score": 0.0,
        "impact": "数据不可用",
        "meaning_cn": macro_component_meaning_cn(key),
        "observation_cn": "暂无可用数据，宏观压力贡献按 0 处理。",
        "cache_age_ms": None,
    }


def compute_macro_pressure(components, config=None, ts_ms=None):
    config = config or CONFIG
    clean = []
    by_key = {item.get("key"): item for item in components or []
              if isinstance(item, dict)}
    for key in MACRO_COMPONENT_ORDER:
        clean.append(normalize_macro_component(
            by_key.get(key), key, config))
    confidence = macro_data_confidence(clean)
    raw_score = sum(safe_float(item.get("component_score")) or 0.0
                    for item in clean)
    macro_score = clamp(raw_score * confidence, -1.0, 1.0)
    last_data_ms = max([
        safe_float(item.get("current_ts_ms")) or 0.0 for item in clean
    ] or [0.0])
    last_data_ms = int(last_data_ms) if last_data_ms > 0 else None
    active_ts_ms = ts_ms or now_ms()
    data_age_ms = None if last_data_ms is None else active_ts_ms - last_data_ms
    status = macro_data_status(clean)
    flags = macro_flags(clean, config)
    blocking_flags = macro_blocking_flags(clean, macro_score, config)
    snapshot = {
        "factor_name": "MPF",
        "factor_version": "v1.1",
        "window": "3d",
        "refresh_sec": int(config.get("macro_refresh_sec", 3600)),
        "last_refresh_ms": active_ts_ms,
        "last_data_time": last_data_ms,
        "data_age_ms": data_age_ms,
        "macro_score": macro_score,
        "macro_regime": classify_macro_regime(macro_score),
        "summary_label_cn": macro_regime_label_cn(macro_score, flags),
        "macro_data_confidence": confidence,
        "data_status": status,
        "quality": _macro_quality(status),
        "flags": flags,
        "blocking_flags": blocking_flags,
        "components": clean,
        "interpretation_cn": macro_interpretation_cn(
            macro_score, clean, flags),
    }
    return add_schema(snapshot, SCHEMA_MACRO_PRESSURE, config)


def normalize_macro_component(component, key, config=None):
    config = config or CONFIG
    if not isinstance(component, dict):
        return unavailable_macro_component(key, config)
    current_close = safe_float(component.get("current_close"))
    reference_close = safe_float(component.get("reference_close"))
    if current_close is None or reference_close is None:
        return unavailable_macro_component(
            key, config, component.get("source_symbol"))
    return macro_component_from_values(
        key,
        component.get("source_symbol")
        or (config.get("macro_symbols") or {}).get(key),
        component.get("source_status") or "live",
        current_close,
        reference_close,
        component.get("current_ts_ms"),
        component.get("reference_ts_ms"),
        config,
        component.get("cache_age_ms", 0),
    )


def offline_macro_pressure_snapshot(config=None, ts_ms=None):
    config = config or CONFIG
    active_ts_ms = ts_ms or now_ms()
    current_ts = int(active_ts_ms / 1000)
    reference_ts = current_ts - 72 * 60 * 60
    bars = {
        "VOLQ": [
            {"timestamp": reference_ts, "close": 20.0},
            {"timestamp": current_ts, "close": 20.9},
        ],
        "DXY": [
            {"timestamp": reference_ts, "close": 104.0},
            {"timestamp": current_ts, "close": 103.7},
        ],
        "US10Y": [
            {"timestamp": reference_ts, "close": 44.0},
            {"timestamp": current_ts, "close": 44.8},
        ],
    }
    components = []
    symbols = config.get("macro_symbols") or {}
    for key in MACRO_COMPONENT_ORDER:
        components.append(build_macro_component(
            key, symbols.get(key), "live", bars[key], config))
    return compute_macro_pressure(components, config, ts_ms=active_ts_ms)


def macro_strength(key, bps, config=None):
    config = config or CONFIG
    tiers = (config.get("macro_bps_tiers") or {}).get(key) or []
    value = abs(safe_float(bps) or 0.0)
    labels = (
        (0.0, "noise", "噪音"),
        (0.22, "mild", "轻微"),
        (0.48, "moderate", "中等"),
        (0.74, "strong", "强"),
        (1.0, "extreme", "极端"),
    )
    if len(tiers) < 4 or value < tiers[0]:
        return labels[0]
    if value < tiers[1]:
        return labels[1]
    if value < tiers[2]:
        return labels[2]
    if value < tiers[3]:
        return labels[3]
    return labels[4]


def macro_data_confidence(components):
    statuses = [item.get("source_status") for item in components or []]
    if not statuses or all(status == "unavailable" for status in statuses):
        return 0.0
    if any(status == "unavailable" for status in statuses):
        return 0.65
    if any(status == "lkgv_cache" for status in statuses):
        return 0.72
    return 1.0


def macro_data_status(components):
    statuses = [item.get("source_status") for item in components or []]
    if not statuses or all(status == "unavailable" for status in statuses):
        return "unavailable"
    if any(status == "unavailable" for status in statuses):
        return "partial"
    if any(status == "lkgv_cache" for status in statuses):
        return "cached"
    return "full_live"


def classify_macro_regime(score):
    score = safe_float(score) or 0.0
    if score >= 0.46:
        return "Headwind"
    if score >= 0.18:
        return "Mild Headwind"
    if score <= -0.46:
        return "Tailwind"
    if score <= -0.18:
        return "Mild Tailwind"
    return "Neutral"


def macro_regime_label_cn(score, flags=None):
    if "MIXED_MACRO" in (flags or []):
        return "宏观混合"
    mapping = {
        "Headwind": "宏观逆风",
        "Mild Headwind": "温和逆风",
        "Neutral": "宏观中性",
        "Mild Tailwind": "温和顺风",
        "Tailwind": "宏观顺风",
    }
    return mapping.get(classify_macro_regime(score), "宏观状态未知")


def macro_flags(components, config=None):
    config = config or CONFIG
    flags = []
    by_key = {item.get("key"): item for item in components or []}
    volq = by_key.get("VOLQ") or {}
    volq_bps = safe_float(volq.get("scoring_bps"))
    if volq_bps is not None:
        if volq_bps > 0 and volq.get("tier") in (
                "moderate", "strong", "extreme"):
            flags.append("VOLATILITY_RISING")
        if volq_bps >= float(config.get("macro_volq_shock_bps", 450)):
            flags.append("VOLATILITY_SHOCK")
    positive = False
    negative = False
    for item in components or []:
        if item.get("tier") not in ("mild", "moderate", "strong", "extreme"):
            continue
        pressure = safe_float(item.get("normalized_pressure")) or 0.0
        if abs(pressure) < 0.18:
            continue
        positive = positive or pressure > 0
        negative = negative or pressure < 0
    if positive and negative:
        flags.append("MIXED_MACRO")
    return flags


def macro_blocking_flags(components, macro_score, config=None):
    config = config or CONFIG
    flags = []
    if safe_float(macro_score) is not None and macro_score >= 0.46:
        flags.append("MACRO_HEADWIND_BLOCK")
    by_key = {item.get("key"): item for item in components or []}
    volq = by_key.get("VOLQ") or {}
    volq_bps = safe_float(volq.get("scoring_bps")) or 0.0
    volq_shock = (
        volq_bps >= float(config.get("macro_volq_shock_bps", 450)))
    if volq_shock and config.get("macro_volq_single_factor_blocking", False):
        flags.append("VOLATILITY_SHOCK")
    if volq_shock:
        for key in ("DXY", "US10Y"):
            item = by_key.get(key) or {}
            pressure = safe_float(item.get("normalized_pressure")) or 0.0
            if pressure > 0.18:
                flags.append("VOLATILITY_SHOCK_CONFIRMED")
                break
    return sorted(set(flags))


def macro_impact_text(key, bps):
    value = safe_float(bps)
    if value is None:
        return "数据不可用"
    if abs(value) < 1e-12:
        return "接近中性"
    pressure = "压力上升" if value > 0 else "压力下降"
    mapping = {
        "VOLQ": "波动率",
        "DXY": "美元",
        "US10Y": "长端利率",
    }
    return mapping.get(key, key) + pressure


def macro_component_meaning_cn(key):
    mapping = {
        "VOLQ": "美股科技波动率代理；上升通常代表避险和波动压力升高。",
        "DXY": "美元指数代理；上升通常代表全球美元流动性收紧。",
        "US10Y": "美国10年期收益率代理；上行通常抬升风险资产折现压力。",
    }
    return mapping.get(key, "宏观压力观察项。")


def macro_component_observation_cn(key, bps, tier_cn):
    value = safe_float(bps)
    if value is None:
        return "暂无可用数据，先不解读。"
    if abs(value) < 1e-12:
        direction = "基本不变"
    elif value > 0:
        direction = "压力增加"
    else:
        direction = "压力缓和"
    units = "收益率点差" if key == "US10Y" else "3日变化"
    return "{0}：{1}，强度 {2}。".format(units, direction, tier_cn)


def macro_interpretation_cn(score, components, flags=None):
    if "MIXED_MACRO" in (flags or []):
        return "宏观环境呈混合状态，波动压力与美元/利率线索存在抵消。"
    regime = classify_macro_regime(score)
    mapping = {
        "Headwind": "宏观环境呈明确逆风，风险资产承压概率上升。",
        "Mild Headwind": "宏观环境呈温和逆风，倾向压制风险偏好。",
        "Neutral": "宏观环境接近中性，组件信息相互抵消或变化较小。",
        "Mild Tailwind": "宏观环境呈温和顺风，风险偏好压力有所缓和。",
        "Tailwind": "宏观环境呈明确顺风，外部压力明显下降。",
    }
    available = [
        item.get("key") for item in components or []
        if item.get("source_status") != "unavailable"
    ]
    suffix = "；可用组件：" + ",".join(available) if available else "；暂无可用组件"
    return mapping.get(regime, regime) + suffix


def _reference_bar(bars, current_ts):
    if not bars or current_ts is None:
        return None
    target = current_ts - 72 * 60 * 60
    candidates = [bar for bar in bars if (bar.get("timestamp") or 0) <= target]
    if candidates:
        return candidates[-1]
    return bars[0] if len(bars) > 1 else None


def _macro_quality(status):
    if status == "full_live":
        return QUALITY_OK
    if status in ("cached", "partial"):
        return QUALITY_MISSING if status == "partial" else "STALE"
    if status == "unavailable":
        return QUALITY_MISSING
    return QUALITY_ERROR

# ================================================================
# SOURCE: demo/neutral_repair.py
# ================================================================
"""DIE + Anchor neutral repair pre-signal state machine."""



class NeutralRepairSignalTracker:
    def __init__(self, config=None):
        self.config = config or CONFIG
        self.context = None
        self.last_output = None

    def update(self, m_die, anchor, runtime_facts=None):
        cfg = self.config
        active_ts_ms = now_ms()
        m_val = safe_float((m_die or {}).get("m_die"))
        m_abs = abs(m_val or 0.0)
        m_data_ok = ((m_die or {}).get("data_status") or {}).get(
            "data_state") == "OK"
        facts = (anchor or {}).get("facts") or {}
        anchor_score = safe_float(facts.get("anchor_gravity_ref_score"))
        nd = safe_float(facts.get("normalized_deviation"))
        anchor_state = (anchor or {}).get("state")
        anchor_reasons = (anchor or {}).get("reasons") or []

        if not m_data_ok or anchor_score is None:
            self.context = None
            return self._output(
                "NR_DATA_INSUFFICIENT", active_ts_ms, m_die, anchor,
                is_active=False, reason_codes=["NR_DATA_INSUFFICIENT"])
        if anchor_state == STATE_INVALID:
            self.context = None
            return self._output(
                "NR_DATA_INSUFFICIENT", active_ts_ms, m_die, anchor,
                is_active=False, reason_codes=["ANCHOR_INVALID"])

        event_threshold = float(cfg.get(
            "nr_mdie_event_on_abs",
            cfg.get("nr_mdie_event_threshold", 0.65)))
        cooldown = float(cfg.get(
            "nr_mdie_event_off_abs",
            cfg.get("nr_mdie_cooldown_abs", 0.42)))
        if m_abs >= event_threshold:
            if self.context is None:
                self.context = self._new_context(
                    m_die, anchor, runtime_facts, active_ts_ms)
            elif self._is_opposite_event(self.context, m_val):
                self.context["opposite_event_count"] = (
                    int(self.context.get("opposite_event_count") or 0) + 1)
                required = int(cfg.get("nr_opposite_confirm_ticks", 2))
                if self.context["opposite_event_count"] < required:
                    self._mark_anchor_damage(anchor)
                    return self._output(
                        "NR_OPPOSITE_EVENT_CONFLICT",
                        active_ts_ms,
                        m_die,
                        anchor,
                        reason_codes=["DIE_OPPOSITE_EVENT_PENDING_CONFIRM"])
                self.context = self._new_context(
                    m_die, anchor, runtime_facts, active_ts_ms)
            else:
                merge_gap = self._last_event_gap_min(active_ts_ms)
                if merge_gap is None or merge_gap <= float(
                        cfg.get("nr_episode_merge_gap_min", 45)):
                    self._update_context_peak(m_die, anchor, active_ts_ms)
                else:
                    self.context = self._new_context(
                        m_die, anchor, runtime_facts, active_ts_ms)
            self._mark_anchor_damage(anchor)
            return self._output(
                "NR_DISPLACEMENT_ACTIVE", active_ts_ms, m_die, anchor,
                reason_codes=["DIE_EVENT_DETECTED_RELAXED_065"])

        if self.context is None:
            return self._output("NR_IDLE", active_ts_ms, m_die, anchor)

        if self._context_age_min(active_ts_ms) > float(
                cfg.get("nr_repair_context_ttl_min", 360)):
            return self._output(
                "NR_REPAIR_STALE", active_ts_ms, m_die, anchor,
                reason_codes=["NR_REPAIR_CONTEXT_TTL_EXPIRED"])

        if self.context.get("confirmed_at_ms") is not None:
            signal_age_min = (
                active_ts_ms - self.context["confirmed_at_ms"]) / 60000.0
            if signal_age_min > float(cfg.get("nr_repair_signal_ttl_min", 60)):
                self.context = None
                return self._output(
                    "NR_REPAIR_STALE", active_ts_ms, m_die, anchor,
                    reason_codes=["NR_REPAIR_SIGNAL_TTL_EXPIRED"])

        self._mark_anchor_damage(anchor)
        require_damage = bool(cfg.get("nr_require_anchor_damage", True))
        if require_damage and not self.context.get("anchor_damage_observed"):
            self.context["repair_confirm_count"] = 0
            return self._output("NR_WAIT_ANCHOR_DAMAGE", active_ts_ms,
                                m_die, anchor)
        if m_abs > cooldown:
            return self._output("NR_DISPLACEMENT_ACTIVE", active_ts_ms,
                                m_die, anchor)

        repair_score = float(cfg.get("nr_anchor_repair_score", 60.0))
        if anchor_score < repair_score:
            self.context["repair_confirm_count"] = 0
            return self._output("NR_WAIT_ANCHOR_REPAIR", active_ts_ms,
                                m_die, anchor)
        repair_nd_abs = safe_float(cfg.get("nr_anchor_repair_nd_abs"))
        if (repair_nd_abs is not None and nd is not None
                and abs(nd) > repair_nd_abs):
            self.context["repair_confirm_count"] = 0
            return self._output("NR_WAIT_ANCHOR_REPAIR", active_ts_ms,
                                m_die, anchor)

        self.context["repair_confirm_count"] = (
            int(self.context.get("repair_confirm_count") or 0) + 1)
        required = int(cfg.get("nr_repair_confirm_ticks", 2))
        if self.context["repair_confirm_count"] < required:
            return self._output(
                "NR_REPAIR_CANDIDATE", active_ts_ms, m_die, anchor,
                reason_codes=["ANCHOR_REPAIR_CANDIDATE_60_PLUS"])

        if self.context.get("confirmed_at_ms") is None:
            self.context["confirmed_at_ms"] = active_ts_ms
        return self._output(
            "NR_REPAIR_CONFIRMED", active_ts_ms, m_die, anchor,
            is_active=True,
            reason_codes=[
                "DIE_EVENT_DETECTED_RELAXED_065",
                "ANCHOR_DAMAGE_OBSERVED",
                "ANCHOR_REPAIRED_60_PLUS",
            ])

    def _new_context(self, m_die, anchor, runtime_facts, active_ts_ms):
        m_val = safe_float((m_die or {}).get("m_die")) or 0.0
        facts = (anchor or {}).get("facts") or {}
        direction = "UP" if m_val > 0 else "DOWN"
        return {
            "episode_id": "nr_{0}_{1}".format(active_ts_ms, direction),
            "event_id": "nr_{0}_{1}".format(active_ts_ms, direction),
            "episode_direction": direction,
            "event_direction": direction,
            "first_event_ms": active_ts_ms,
            "event_start_ms": active_ts_ms,
            "event_last_seen_ms": active_ts_ms,
            "last_event_seen_ms": active_ts_ms,
            "event_count_merged": 1,
            "opposite_event_count": 0,
            "peak_abs_m_die": abs(m_val),
            "event_peak_abs_mdie": abs(m_val),
            "peak_m_die": m_val,
            "event_peak_mdie": m_val,
            "event_move_shape": (m_die or {}).get("move_shape"),
            "price_at_event": (runtime_facts or {}).get("current_price"),
            "anchor_score_at_event": safe_float(
                facts.get("anchor_gravity_ref_score")),
            "anchor_nd_at_event": safe_float(facts.get("normalized_deviation")),
            "min_anchor_score_after_event": safe_float(
                facts.get("anchor_gravity_ref_score")),
            "max_abs_nd_after_event": abs(
                safe_float(facts.get("normalized_deviation")) or 0.0),
            "anchor_damage_observed": False,
            "anchor_damage_evidence": [],
            "repair_confirm_count": 0,
            "confirmed_at_ms": None,
        }

    def _update_context_peak(self, m_die, anchor, active_ts_ms):
        if self.context is None:
            return
        m_val = safe_float((m_die or {}).get("m_die")) or 0.0
        facts = (anchor or {}).get("facts") or {}
        self.context["event_count_merged"] = (
            int(self.context.get("event_count_merged") or 0) + 1)
        self.context["opposite_event_count"] = 0
        self.context["event_last_seen_ms"] = active_ts_ms
        self.context["last_event_seen_ms"] = active_ts_ms
        if abs(m_val) > safe_float(self.context.get("event_peak_abs_mdie")):
            self.context["peak_abs_m_die"] = abs(m_val)
            self.context["event_peak_abs_mdie"] = abs(m_val)
            self.context["peak_m_die"] = m_val
            self.context["event_peak_mdie"] = m_val
            self.context["event_move_shape"] = (m_die or {}).get("move_shape")
        anchor_score = safe_float(facts.get("anchor_gravity_ref_score"))
        nd = safe_float(facts.get("normalized_deviation"))
        if anchor_score is not None:
            current_min = safe_float(
                self.context.get("min_anchor_score_after_event"))
            self.context["min_anchor_score_after_event"] = (
                anchor_score if current_min is None
                else min(current_min, anchor_score))
        if nd is not None:
            self.context["max_abs_nd_after_event"] = max(
                safe_float(self.context.get("max_abs_nd_after_event")) or 0.0,
                abs(nd),
            )

    def _mark_anchor_damage(self, anchor):
        if self.context is None:
            return False
        facts = (anchor or {}).get("facts") or {}
        anchor_score = safe_float(facts.get("anchor_gravity_ref_score"))
        nd = safe_float(facts.get("normalized_deviation"))
        reasons = set((anchor or {}).get("reasons") or [])
        evidence = []
        if anchor_score is not None:
            current_min = safe_float(
                self.context.get("min_anchor_score_after_event"))
            self.context["min_anchor_score_after_event"] = (
                anchor_score if current_min is None
                else min(current_min, anchor_score))
            if anchor_score < float(
                    self.config.get("nr_anchor_damage_score", 60.0)):
                evidence.append("ANCHOR_DAMAGE_OBSERVED_BELOW_60")
            score_at_event = safe_float(
                self.context.get("anchor_score_at_event"))
            damage_drop = safe_float(
                self.config.get("nr_anchor_damage_drop_score"))
            if (score_at_event is not None and damage_drop is not None
                    and score_at_event - anchor_score >= damage_drop):
                evidence.append("ANCHOR_DAMAGE_OBSERVED_SCORE_DROP")
        if nd is not None:
            self.context["max_abs_nd_after_event"] = max(
                safe_float(self.context.get("max_abs_nd_after_event")) or 0.0,
                abs(nd),
            )
            if (self.config.get("nr_allow_nd_damage_evidence", True)
                    and abs(nd) >= float(
                        self.config.get("nr_anchor_damage_nd_abs", 1.0))):
                evidence.append("ANCHOR_DAMAGE_OBSERVED_ND_1_PLUS")
        if "ANCHOR_DEVIATION_WIDE" in reasons:
            evidence.append("ANCHOR_DAMAGE_OBSERVED_DEVIATION_WIDE")
        if evidence:
            self.context["anchor_damage_observed"] = True
            seen = self.context.setdefault("anchor_damage_evidence", [])
            for item in evidence:
                if item not in seen:
                    seen.append(item)
            return True
        return False

    def _is_opposite_event(self, context, m_val):
        if not self.config.get("nr_reset_on_opposite_event", True):
            return False
        if m_val is None:
            return False
        direction = "UP" if m_val > 0 else "DOWN"
        return context.get("event_direction") != direction

    def _context_age_min(self, active_ts_ms):
        if self.context is None:
            return None
        start = safe_float(self.context.get("event_start_ms"))
        if start is None:
            return None
        return max(0.0, (active_ts_ms - start) / 60000.0)

    def _last_event_gap_min(self, active_ts_ms):
        if self.context is None:
            return None
        last_seen = safe_float(self.context.get("last_event_seen_ms"))
        if last_seen is None:
            last_seen = safe_float(self.context.get("event_last_seen_ms"))
        if last_seen is None:
            return None
        return max(0.0, (active_ts_ms - last_seen) / 60000.0)

    def _output(self, state, active_ts_ms, m_die, anchor, is_active=False,
                reason_codes=None):
        facts = (anchor or {}).get("facts") or {}
        anchor_score = safe_float(facts.get("anchor_gravity_ref_score"))
        nd = safe_float(facts.get("normalized_deviation"))
        event_context = self._public_event_context(active_ts_ms)
        anchor_context = self._public_anchor_context(anchor_score, nd)
        require_damage = bool(self.config.get("nr_require_anchor_damage", True))
        event_threshold = float(self.config.get(
            "nr_mdie_event_on_abs",
            self.config.get("nr_mdie_event_threshold", 0.65)))
        cooldown = float(self.config.get(
            "nr_mdie_event_off_abs",
            self.config.get("nr_mdie_cooldown_abs", 0.42)))
        gating = {
            "m_die_event_ok": abs(safe_float((m_die or {}).get("m_die"))
                                  or 0.0) >= event_threshold,
            "m_die_cooldown_ok": abs(safe_float((m_die or {}).get("m_die"))
                                     or 0.0) <= cooldown,
            "anchor_damage_ok": (
                not require_damage
                or bool(anchor_context.get("anchor_damage_observed"))),
            "anchor_repair_ok": (
                anchor_score is not None and anchor_score >= float(
                    self.config.get("nr_anchor_repair_score", 60.0))),
            "not_stale": state != "NR_REPAIR_STALE",
            "data_ready": state != "NR_DATA_INSUFFICIENT",
        }
        confidence = self._confidence(state, event_context, anchor_context,
                                      gating)
        payload = {
            "threshold_profile": self.config.get(
                "nr_threshold_profile", "relaxed_test"),
            "state": state,
            "is_active": bool(is_active),
            "label": _label_for_state(state, is_active),
            "confidence": confidence,
            "event_context": event_context,
            "anchor_context": anchor_context,
            "gating": gating,
            "reason_codes": reason_codes or [],
            "interpretation_cn": _interpretation_cn(state, is_active),
        }
        self.last_output = add_schema(
            payload, SCHEMA_NEUTRAL_REPAIR_SIGNAL, self.config)
        return self.last_output

    def _public_event_context(self, active_ts_ms):
        if not self.context:
            return None
        ctx = self.context
        return {
            "event_id": ctx.get("event_id"),
            "episode_id": ctx.get("episode_id") or ctx.get("event_id"),
            "episode_direction": ctx.get("episode_direction")
                                 or ctx.get("event_direction"),
            "event_direction": ctx.get("event_direction"),
            "first_event_ms": ctx.get("first_event_ms")
                              or ctx.get("event_start_ms"),
            "event_start_ms": ctx.get("event_start_ms"),
            "last_event_seen_ms": ctx.get("last_event_seen_ms")
                                  or ctx.get("event_last_seen_ms"),
            "event_last_seen_ms": ctx.get("event_last_seen_ms"),
            "event_age_min": self._context_age_min(active_ts_ms),
            "event_count_merged": int(ctx.get("event_count_merged") or 0),
            "opposite_event_count": int(ctx.get("opposite_event_count") or 0),
            "peak_abs_m_die": ctx.get("peak_abs_m_die")
                              or ctx.get("event_peak_abs_mdie"),
            "event_peak_abs_mdie": ctx.get("event_peak_abs_mdie"),
            "peak_m_die": ctx.get("peak_m_die")
                          or ctx.get("event_peak_mdie"),
            "event_peak_mdie": ctx.get("event_peak_mdie"),
            "event_move_shape": ctx.get("event_move_shape"),
            "price_at_event": ctx.get("price_at_event"),
        }

    def _public_anchor_context(self, anchor_score, nd):
        ctx = self.context or {}
        return {
            "anchor_score": anchor_score,
            "anchor_repair_score": self.config.get("nr_anchor_repair_score"),
            "anchor_score_at_event": ctx.get("anchor_score_at_event"),
            "min_anchor_score_after_event": ctx.get(
                "min_anchor_score_after_event"),
            "anchor_damage_observed": bool(ctx.get("anchor_damage_observed")),
            "anchor_damage_evidence": ctx.get("anchor_damage_evidence") or [],
            "normalized_deviation": nd,
            "max_abs_nd_after_event": ctx.get("max_abs_nd_after_event"),
            "repair_confirm_count": int(ctx.get("repair_confirm_count") or 0),
        }

    def _confidence(self, state, event_context, anchor_context, gating):
        if state in ("NR_IDLE", "NR_DATA_INSUFFICIENT", "NR_REPAIR_STALE"):
            return 0
        score = 45.0
        peak = safe_float((event_context or {}).get("event_peak_abs_mdie")) or 0
        if peak >= 0.90:
            score += 15
        elif peak >= 0.80:
            score += 11
        elif peak > 0.65:
            score += 7
        evidence = anchor_context.get("anchor_damage_evidence") or []
        if "ANCHOR_DAMAGE_OBSERVED_BELOW_60" in evidence:
            score += 8
        if "ANCHOR_DAMAGE_OBSERVED_ND_1_PLUS" in evidence:
            score += 6
        if "ANCHOR_DAMAGE_OBSERVED_DEVIATION_WIDE" in evidence:
            score += 6
        anchor_score = safe_float(anchor_context.get("anchor_score"))
        if anchor_score is not None:
            if anchor_score >= 70:
                score += 15
            elif anchor_score >= 65:
                score += 11
            elif anchor_score >= 60:
                score += 8
        if (event_context or {}).get("event_move_shape") == "DRIFT_RUN":
            score += 4
        elif (event_context or {}).get("event_move_shape") == "IMPULSE_SHIFT":
            score += 2
        cap = 100.0
        if (self.config.get("nr_require_anchor_damage", True)
                and not gating.get("anchor_damage_ok")):
            cap = min(cap, 45.0)
        if not gating.get("m_die_cooldown_ok"):
            cap = min(cap, 50.0)
        if state == "NR_REPAIR_CANDIDATE":
            cap = min(cap, 60.0)
        if state != "NR_REPAIR_CONFIRMED":
            cap = min(cap, 60.0)
        return int(round(clamp(score, 0.0, cap)))


def _label_for_state(state, is_active=False):
    if is_active or state == "NR_REPAIR_CONFIRMED":
        return "BASE_NEUTRAL_REPAIR_SIGNAL_ACTIVE"
    mapping = {
        "NR_IDLE": "WAIT_DIE_EVENT",
        "NR_DISPLACEMENT_ACTIVE": "DIE_EVENT_ACTIVE_WAIT_COOLDOWN",
        "NR_WAIT_ANCHOR_DAMAGE": "WAIT_ANCHOR_DAMAGE",
        "NR_WAIT_ANCHOR_REPAIR": "WAIT_ANCHOR_REPAIR",
        "NR_REPAIR_CANDIDATE": "REPAIR_CANDIDATE_NEEDS_CONFIRMATION",
        "NR_REPAIR_STALE": "REPAIR_CONTEXT_STALE",
        "NR_DATA_INSUFFICIENT": "DATA_INSUFFICIENT",
        "NR_OPPOSITE_EVENT_CONFLICT": "OPPOSITE_DIE_EVENT_PENDING_CONFIRM",
    }
    return mapping.get(state, state)


def _interpretation_cn(state, is_active=False):
    if is_active or state == "NR_REPAIR_CONFIRMED":
        return "DIE 单向事件后已经观察到 Anchor 受损与修复，基础中性回路修复信号成立。"
    mapping = {
        "NR_IDLE": "当前没有可用的 DIE 事件上下文，等待短周期单向再定价事件。",
        "NR_DISPLACEMENT_ACTIVE": "DIE 单向偏移仍在或刚刚出现，等待其冷却后再检查 Anchor 修复。",
        "NR_WAIT_ANCHOR_DAMAGE": "已有 DIE 事件，但尚未观察到 Anchor 受损证据。",
        "NR_WAIT_ANCHOR_REPAIR": "已观察到 Anchor 受损，正在等待 Anchor 回到修复阈值。",
        "NR_REPAIR_CANDIDATE": "Anchor 已回到修复阈值，但确认次数不足。",
        "NR_REPAIR_STALE": "DIE 事件上下文或修复信号已经过期。",
        "NR_DATA_INSUFFICIENT": "M-DIE 或 Anchor 数据不足，暂不能判断修复链路。",
        "NR_OPPOSITE_EVENT_CONFLICT": "出现反向 DIE 事件，等待连续确认后再切换 episode。",
    }
    return mapping.get(state, state)

# ================================================================
# SOURCE: demo/bias_thesis.py
# ================================================================
"""Shared macro/funding verdict helpers (consumed by the EDB layer).

v0.51: the standalone Bias Thesis arbiter (confidence/CVD/label machinery) was
retired and fully replaced by the EDB directional layer (``demo/edb.py``). This
module now only keeps the small, non-redundant verdict + macro-component display
helpers that EDB still calls. Nothing here computes a direction or confidence.
"""



def evaluate_funding_verdict(flow, config=None):
    effect = (flow or {}).get("tmvf_funding_effect")
    mapping = {
        "neutral": "FUNDING_NEUTRAL",
        "confirming": "FUNDING_MILD_CONFIRM",
        "opposite_crowding_fuel": "FUNDING_OPPOSITE_FUEL",
        "overcrowded": "FUNDING_CROWDED_WARNING",
        "extreme_overcrowded": "FUNDING_HARD_WARNING",
        "crowded_without_price_confirmation": "FUNDING_HARD_WARNING",
    }
    item_48 = (flow or {}).get("tmvf_48h") or {}
    funding = item_48.get("funding") or {}
    verdict = mapping.get(effect, "FUNDING_NEUTRAL")
    rate = (flow or {}).get("last_funding_rate")
    return {
        "effect": effect,
        "verdict": verdict,
        "funding_state": item_48.get("funding_state")
                         or funding.get("funding_state"),
        "last_funding_rate": rate,
        "funding_norm": funding.get("funding_norm"),
        "funding_cum": funding.get("funding_cum"),
        "funding_count": funding.get("funding_count"),
        "interpretation_cn": _funding_interpretation_cn(verdict, effect, rate),
    }


def evaluate_macro_verdict(macro_pressure, config=None):
    config = config or CONFIG
    macro_pressure = macro_pressure or {}
    flags = macro_pressure.get("flags") or []
    blocking_flags = macro_pressure.get("blocking_flags") or []
    status = macro_pressure.get("data_status")
    regime = macro_pressure.get("macro_regime")
    if status == "unavailable":
        verdict = "MACRO_UNAVAILABLE"
    elif (config.get("bias_macro_blocking_enabled", True)
          and blocking_flags):
        verdict = "MACRO_BLOCKING"
    else:
        verdict = {
            "Tailwind": "MACRO_SUPPORTIVE",
            "Mild Tailwind": "MACRO_MILD_SUPPORTIVE",
            "Neutral": "MACRO_NEUTRAL",
            "Mild Headwind": "MACRO_MILD_ADVERSE",
            "Headwind": "MACRO_ADVERSE",
        }.get(regime, "MACRO_NEUTRAL")
    diagnostics = _macro_diagnostics(macro_pressure)
    return {
        "verdict": verdict,
        "macro_score": macro_pressure.get("macro_score"),
        "macro_regime": regime,
        "data_status": status,
        "macro_data_confidence": macro_pressure.get("macro_data_confidence"),
        "flags": flags,
        "blocking_flags": blocking_flags,
        "component_scores": _macro_component_scores(macro_pressure),
        "macro_diagnostics": diagnostics["macro_diagnostics"],
        "macro_components_cn": summarize_macro_components_cn(macro_pressure),
        "reason_codes": diagnostics["reason_codes"],
    }


def _macro_component_scores(macro_pressure):
    scores = {}
    for item in (macro_pressure or {}).get("components") or []:
        key = item.get("key")
        if not key:
            continue
        scores[key] = {
            "component_score": item.get("component_score"),
            "normalized_pressure": item.get("normalized_pressure"),
            "scoring_value": item.get("scoring_value"),
            "scoring_unit": item.get("scoring_unit"),
            "tier_cn": item.get("tier_cn"),
            "current_close": item.get("current_close"),
            "reference_close": item.get("reference_close"),
        }
    return scores


def _macro_diagnostics(macro_pressure):
    macro_pressure = macro_pressure or {}
    reason_codes = []
    components = []
    required = (
        "key",
        "source_symbol",
        "source_status",
        "current_close",
        "reference_close",
        "change_pct_3d",
        "scoring_bps",
        "tier",
        "component_score",
        "impact",
    )
    for item in macro_pressure.get("components") or []:
        missing = [key for key in required if key not in item]
        if "tier_cn" not in item and "tier" not in item:
            missing.append("tier_cn")
        if missing and "MACRO_COMPONENT_FIELD_MISSING" not in reason_codes:
            reason_codes.append("MACRO_COMPONENT_FIELD_MISSING")
        components.append({
            "component": item.get("component") or item.get("key") or "-",
            "key": item.get("key") or item.get("component") or "-",
            "source_symbol": item.get("source_symbol") or "-",
            "source_status": item.get("source_status") or "-",
            "current_close": item.get("current_close"),
            "reference_close": item.get("reference_close"),
            "change_pct_3d": item.get("change_pct_3d"),
            "scoring_bps": item.get("scoring_bps"),
            "tier": item.get("tier") or "-",
            "tier_cn": item.get("tier_cn") or item.get("tier") or "-",
            "component_score": item.get("component_score"),
            "impact": item.get("impact") or "-",
        })
    return {
        "macro_diagnostics": {
            "macro_score": macro_pressure.get("macro_score"),
            "macro_regime": macro_pressure.get("macro_regime"),
            "data_status": macro_pressure.get("data_status"),
            "macro_data_confidence": macro_pressure.get(
                "macro_data_confidence"),
            "flags": macro_pressure.get("flags") or [],
            "components": components,
        },
        "reason_codes": reason_codes,
    }


def summarize_macro_components_cn(macro_pressure):
    diagnostics = _macro_diagnostics(macro_pressure)["macro_diagnostics"]
    lines = []
    for item in diagnostics.get("components") or []:
        key = item.get("component") or item.get("key") or "-"
        line = (
            str(key) + ": "
            + str(item.get("source_symbol") or "-") + " "
            + str(item.get("source_status") or "-")
            + "｜3d " + _fmt_signed_pct_value(item.get("change_pct_3d"))
            + "｜bps " + _fmt_signed_value(item.get("scoring_bps"), 0)
            + "｜tier " + str(item.get("tier_cn") or item.get("tier") or "-")
            + "｜贡献 " + _fmt_signed_value(item.get("component_score"), 2)
        )
        if item.get("impact") and item.get("impact") != "-":
            line += "｜" + str(item.get("impact"))
        lines.append(line)
    return "\n".join(lines)


def _funding_interpretation_cn(verdict, effect, rate):
    if verdict == "FUNDING_MILD_CONFIRM":
        return "Funding 温和确认，但不单独生成方向"
    if verdict == "FUNDING_OPPOSITE_FUEL":
        return "反向拥挤燃料，仅作反身性观察"
    if verdict == "FUNDING_CROWDED_WARNING":
        return "同向拥挤升温，作为论证扣分"
    if verdict == "FUNDING_HARD_WARNING":
        return "Funding 过度拥挤，触发硬阻断"
    if rate is None and not effect:
        return "Funding 数据不足或中性"
    return "Funding 中性/轻微，不单独生成方向"


def _fmt_signed_value(value, digits=2):
    try:
        if value is None:
            return "-"
        template = "{:+." + str(int(digits)) + "f}"
        return template.format(float(value))
    except Exception:
        return str(value)


def _fmt_signed_pct_value(value):
    try:
        if value is None:
            return "-"
        return "{:+.2f}%".format(float(value))
    except Exception:
        return str(value)

# ================================================================
# SOURCE: demo/edb.py
# ================================================================
"""EDB: Expiry-window Directional Bias layer (v0.5).

Decouples DIRECTION from TIMING. DIE+Anchor (NeutralRepair) decides WHEN a
premium-selling window is open; EDB decides WHICH SIDE the 24-72h expiry window
leans, by combining independent evidence votes into a posterior:

    EDB_score = Σ(vote_i · weight_i) / Σ weight_i        ∈ [-1, 1]
    agreement = Σ{weight_i : sign(vote_i)=sign(EDB_score)} / Σ weight_i
    confidence = 100 · |EDB_score| · agreement · ggr_multiplier

More independent, aligned evidence -> sharper posterior (lower entropy) -> higher
confidence. Genuine conflict -> low agreement -> Neutral (a valid conclusion).
No hard hysteresis lock: lean follows sign(EDB_score) each tick; stability is an
emergent property of evidence breadth, so a real reversal still flips promptly.

Evidence (signed vote in [-1,1], + relative weight):
  TMV (1h trend backbone) / CVD x price (rolling-distribution strength) /
  MACRO (multi-day lean) / FUNDING (reflexivity) / SRD (skew) /
  GGR spatial pin.  GGR regime is additionally a GATE/veto on confidence.

Boundary: pre-signal only. No legs/quotes/orders. Not in module_results.
"""


CVD_WARMING = "WARMING"
CVD_NEUTRAL = "NEUTRAL"
CVD_WEAK = "WEAK"
CVD_MODERATE = "MODERATE"
CVD_STRONG = "STRONG"


def evaluate_edb(flow, macro_pressure, neutral_repair_signal, skew=None,
                 gamma_regime=None, cvd_history=None, prev_edb_score=None,
                 config=None):
    config = config or CONFIG
    flow = flow or {}
    precondition_active = bool((neutral_repair_signal or {}).get("is_active"))

    funding = evaluate_funding_verdict(flow, config)
    macro = evaluate_macro_verdict(macro_pressure, config)

    evidence = []
    evidence.append(_tmv_vote(flow, config))
    evidence.extend(_cvd_votes(flow, cvd_history, config))
    evidence.append(_macro_vote(macro, config))
    evidence.append(_funding_vote(funding, config))
    evidence.append(_srd_vote(skew, config))
    evidence.append(_ggr_spatial_vote(gamma_regime, config))
    evidence = [item for item in evidence if item and item.get("weight", 0) > 0]

    info_ref = float(config.get("edb_informative_vote_abs", 0.15))
    for item in evidence:
        info = clamp(abs(item["vote"]) / max(info_ref, 1e-9), 0.0, 1.0)
        item["info"] = info
        item["eff_weight"] = item["weight"] * info
    raw_score = _weighted_score(evidence)
    edb_score = _smooth(raw_score, prev_edb_score, config)
    agreement = _agreement(evidence, edb_score)
    coverage = _coverage(evidence, config)
    # v0.5.4: strength mapping + FLOORED agreement/coverage modulators so the
    # confidence no longer collapses from multiplying three sub-1 factors.
    score_full = float(config.get("edb_score_full", 0.75))
    agr_floor = float(config.get("edb_agreement_floor", 0.60))
    cov_floor = float(config.get("edb_coverage_floor", 0.50))
    strength = clamp(abs(edb_score) / max(score_full, 1e-9), 0.0, 1.0)
    agr_factor = agr_floor + (1.0 - agr_floor) * agreement
    cov_factor = cov_floor + (1.0 - cov_floor) * coverage
    conf_raw = 100.0 * strength * agr_factor * cov_factor

    # --- gates (GGR regime, macro shock, funding hard warning) ---
    veto = False
    veto_reason = None
    ggr = gamma_regime or {}
    ggr_mult = safe_float(ggr.get("confidence_multiplier"))
    if ggr_mult is None:
        ggr_mult = 1.0
    if ggr.get("veto"):
        veto, veto_reason = True, "GGR_NEGATIVE_GAMMA_VETO"
    if macro.get("verdict") == "MACRO_BLOCKING":
        veto, veto_reason = True, "MACRO_BLOCKING"
    if funding.get("verdict") == "FUNDING_HARD_WARNING":
        veto, veto_reason = True, "FUNDING_HARD_WARNING"

    confidence = clamp(conf_raw * ggr_mult, 0.0, 100.0)
    if veto:
        confidence = 0.0

    lean, support, side_hint, next_action = _classify(
        edb_score, confidence, precondition_active, veto, config)
    conflict = _conflict_level(agreement)

    payload = {
        "factor_name": "EDB",
        "factor_version": "v0.5",
        "precondition": {
            "nr_active": precondition_active,
            "nr_state": (neutral_repair_signal or {}).get("state"),
        },
        "edb_score": edb_score,
        "edb_score_raw": raw_score,
        "agreement": agreement,
        "coverage": coverage,
        "confidence": int(round(confidence)),
        "lean": lean,
        "side_hint": side_hint,
        "support_label": support,
        "next_action": next_action,
        "conflict_level": conflict,
        "ggr_gate": {
            "regime": ggr.get("regime"),
            "multiplier": ggr_mult,
            "veto": bool(ggr.get("veto")),
        },
        "veto_reason": veto_reason,
        "evidence": evidence,
        "reason_codes": _reason_codes(evidence, veto_reason, precondition_active),
        "summary_cn": _summary_cn(lean, support, confidence, edb_score,
                                  agreement, coverage, evidence, veto_reason,
                                  precondition_active),
    }
    return add_schema(payload, SCHEMA_EDB, config)


# --------------------------------------------------------------------------
# evidence votes
# --------------------------------------------------------------------------

def _base_weight(key, config):
    return float((config.get("edb_base_weights") or {}).get(key, 0.0))


def _tmv_vote(flow, config):
    direction = flow.get("direction")
    blend = safe_float(flow.get("tmv_blend"))
    base = _base_weight("TMV", config)
    if direction == DIRECTION_UNCLEAR or blend is None:
        return {"key": "TMV", "vote": 0.0, "weight": 0.0,
                "detail": {"direction": direction, "tmv_blend": blend}}
    ref = float(config.get("edb_tmv_vote_ref", 0.45))
    vote = clamp(blend / max(ref, 1e-9), -1.0, 1.0)
    reliability = 1.0
    if flow.get("window_conflict"):
        reliability *= 0.45  # downweight (not zero) on 24h/48h disagreement
    return {
        "key": "TMV",
        "vote": vote,
        "weight": base * reliability,
        "detail": {
            "direction": direction,
            "tmv_blend": blend,
            "window_conflict": bool(flow.get("window_conflict")),
            "tmvf_24h_final": (flow.get("tmvf_24h") or {}).get("tmv_final"),
            "tmvf_48h_final": (flow.get("tmvf_48h") or {}).get("tmv_final"),
        },
    }


def _cvd_votes(flow, cvd_history, config):
    micro = (flow or {}).get("micro_flow") or {}
    history = cvd_history or {}
    out = []
    for label, role in (("fast_4h", "4h"), ("slow_12h", "12h")):
        window = micro.get(label) or {}
        vote = _cvd_window_vote(window, history.get(role) or [], role, config)
        if vote:
            out.append(vote)
    return out


def _cvd_window_vote(window, history, role, config):
    base = _base_weight("CVD", config)
    cvd_norm = safe_float(window.get("cvd_norm"))
    price_pct = _price_return_pct(window)
    if cvd_norm is None or price_pct is None or not window.get("data_ready"):
        return {"key": "CVD_" + role, "vote": 0.0, "weight": 0.0,
                "detail": {"role": role, "data_ready": bool(
                    window.get("data_ready"))}}
    strength, pctl = _cvd_strength(abs(cvd_norm), history, config)
    neutral_pct = float(config.get("edb_price_neutral_return_pct_abs", 0.05))
    price_sign = 1 if price_pct > neutral_pct else (
        -1 if price_pct < -neutral_pct else 0)
    cvd_sign = 1 if cvd_norm > 0 else (-1 if cvd_norm < 0 else 0)
    active = strength in (CVD_MODERATE, CVD_STRONG)
    mag = 0.0 if pctl is None else clamp(pctl, 0.0, 1.0)
    # v0.5.4: a price-confirmed move must not be undervalued just because the
    # CVD percentile is mid in a sustained trend. Blend percentile magnitude
    # with price-confirmation magnitude on the two confirm quadrants.
    price_full = float(config.get("edb_price_confirm_full_pct", 0.75))
    price_confirm = clamp(abs(price_pct) / max(price_full, 1e-9), 0.0, 1.0)
    confirm_mag = max(mag, price_confirm)
    confirm_active = active or price_confirm >= 0.5

    # joint CVD x price quadrant (the four cases must read differently)
    if cvd_sign > 0 and price_sign > 0:        # buy drives up
        verdict, vote, w = ("BUY_CONFIRMS_UP", +confirm_mag,
                            (1.0 if confirm_active else 0.45))
    elif cvd_sign < 0 and price_sign < 0:      # sell drives down
        verdict, vote, w = ("SELL_CONFIRMS_DOWN", -confirm_mag,
                            (1.0 if confirm_active else 0.45))
    elif cvd_sign > 0 and price_sign < 0:      # buying absorbed / hidden supply
        verdict, vote, w = "BUY_ABSORBED_BEARISH", -0.4 * mag, (
            0.6 if active else 0.3)
    elif cvd_sign < 0 and price_sign > 0:      # selling absorbed / short cover
        verdict, vote, w = "SELL_ABSORBED_BULLISH", +0.4 * mag, (
            0.6 if active else 0.3)
    elif price_sign != 0:                      # price moves, flow flat
        verdict, vote, w = "PRICE_ONLY", 0.3 * price_sign, 0.25
    else:
        verdict, vote, w = "FLAT", 0.0, 0.1
    if strength == CVD_WARMING:
        w *= 0.5  # distribution not warmed up -> trust price-only lightly
    role_w = 1.0 if role == "4h" else 1.1  # slow window slightly steadier
    return {
        "key": "CVD_" + role,
        "vote": clamp(vote, -1.0, 1.0),
        "weight": base * w * role_w,
        "detail": {
            "role": role,
            "verdict": verdict,
            "cvd_norm": cvd_norm,
            "cvd_sum": window.get("cvd_sum"),
            "price_return_pct": price_pct,
            "strength": strength,
            "strength_pctl": pctl,
        },
    }


def _cvd_strength(abs_cvd_norm, history, config):
    clean = [safe_float(v) for v in history or []]
    clean = [v for v in clean if v is not None]
    min_hist = int(config.get("edb_cvd_strength_min_history", 20))
    if len(clean) < min_hist:
        return CVD_WARMING, None
    le = sum(1 for v in clean if v <= abs_cvd_norm)
    pctl = le / float(len(clean))
    if pctl >= float(config.get("edb_cvd_pctl_strong", 0.88)):
        return CVD_STRONG, pctl
    if pctl >= float(config.get("edb_cvd_pctl_moderate", 0.70)):
        return CVD_MODERATE, pctl
    if pctl >= float(config.get("edb_cvd_pctl_weak", 0.40)):
        return CVD_WEAK, pctl
    return CVD_NEUTRAL, pctl


def _macro_vote(macro, config):
    score = safe_float(macro.get("macro_score"))
    base = _base_weight("MACRO", config)
    if score is None or macro.get("verdict") in ("MACRO_UNAVAILABLE",
                                                 "MACRO_BLOCKING"):
        return {"key": "MACRO", "vote": 0.0, "weight": 0.0,
                "detail": {"verdict": macro.get("verdict")}}
    ref = float(config.get("edb_macro_vote_ref", 0.46))
    # macro_score>0 = headwind = bearish for risk asset -> negative vote
    vote = clamp(-score / max(ref, 1e-9), -1.0, 1.0)
    conf = safe_float(macro.get("macro_data_confidence"))
    reliability = conf if conf is not None else 1.0
    if macro.get("data_status") in ("cached", "partial"):
        reliability *= 0.5
    return {
        "key": "MACRO",
        "vote": vote,
        "weight": base * reliability,
        "detail": {
            "macro_score": score,
            "macro_regime": macro.get("macro_regime"),
            "verdict": macro.get("verdict"),
            "components_cn": macro.get("macro_components_cn"),
        },
    }


def _funding_vote(funding, config):
    base = _base_weight("FUNDING", config)
    verdict = funding.get("verdict")
    norm = safe_float(funding.get("funding_norm"))
    # reflexivity: crowded longs (norm>0) = downside fuel -> bearish small vote
    vote, w = 0.0, 0.0
    if verdict == "FUNDING_CROWDED_WARNING" and norm is not None:
        vote, w = clamp(-norm, -1.0, 1.0) * 0.5, 0.8
    elif verdict == "FUNDING_OPPOSITE_FUEL" and norm is not None:
        vote, w = clamp(-norm, -1.0, 1.0) * 0.3, 0.5
    elif verdict == "FUNDING_MILD_CONFIRM" and norm is not None:
        vote, w = clamp(norm, -1.0, 1.0) * 0.2, 0.4
    return {"key": "FUNDING", "vote": vote, "weight": base * w,
            "detail": {"verdict": verdict, "funding_norm": norm}}


def _srd_vote(skew, config):
    base = _base_weight("SRD", config)
    skew = skew or {}
    if skew.get("data_state") != "OK":
        return {"key": "SRD", "vote": 0.0, "weight": 0.0,
                "detail": {"data_state": skew.get("data_state")}}
    vote = safe_float(skew.get("vote")) or 0.0
    conf = safe_float(skew.get("vote_confidence")) or 0.0
    return {"key": "SRD", "vote": clamp(vote, -1.0, 1.0), "weight": base * conf,
            "detail": {"rr_blend": skew.get("rr_blend"),
                       "skew_norm_blend": skew.get("skew_norm_blend"),
                       "rr_z": skew.get("rr_z"),
                       "delta_rr": skew.get("delta_rr"),
                       "vote_confidence": conf}}


def _ggr_spatial_vote(gamma_regime, config):
    base = _base_weight("GGR_SPATIAL", config)
    ggr = gamma_regime or {}
    vote = safe_float(ggr.get("spatial_vote")) or 0.0
    w = safe_float(ggr.get("spatial_weight")) or 0.0
    return {"key": "GGR_SPATIAL", "vote": clamp(vote, -1.0, 1.0),
            "weight": base * w,
            "detail": {"regime": ggr.get("regime"),
                       "regime_strength": ggr.get("regime_strength"),
                       "net_gamma_notional": ggr.get("net_gamma_notional"),
                       "max_gamma_strike": ggr.get("max_gamma_strike"),
                       "flip_point": ggr.get("flip_point"),
                       "distance_to_flip_pct": ggr.get("distance_to_flip_pct"),
                       "pin": (ggr.get("pin") or {}).get("pin_strike")}}


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------

def _weighted_score(evidence):
    wsum = sum(item.get("eff_weight", item["weight"]) for item in evidence)
    if wsum <= 0:
        return 0.0
    return clamp(
        sum(item["vote"] * item.get("eff_weight", item["weight"])
            for item in evidence) / wsum,
        -1.0, 1.0)


def _smooth(raw_score, prev_score, config):
    n = int(config.get("edb_score_smooth_n", 1) or 1)
    prev = safe_float(prev_score)
    if n <= 1 or prev is None:
        return raw_score
    alpha = 2.0 / (n + 1.0)
    return clamp(alpha * raw_score + (1.0 - alpha) * prev, -1.0, 1.0)


def _agreement(evidence, edb_score):
    wsum = sum(item.get("eff_weight", item["weight"]) for item in evidence)
    if wsum <= 0:
        return 0.0
    target = 1 if edb_score > 0 else (-1 if edb_score < 0 else 0)
    if target == 0:
        return 0.0
    agree = sum(item.get("eff_weight", item["weight"]) for item in evidence
                if (item["vote"] > 0) - (item["vote"] < 0) == target)
    return clamp(agree / wsum, 0.0, 1.0)


def _coverage(evidence, config):
    """Fraction of EXPECTED direction-evidence weight that is informatively
    present. Missing CVD or a cold/uninformative SRD lowers coverage, which
    lowers confidence (less independent info -> higher entropy)."""
    bw = config.get("edb_base_weights") or {}
    total = ((safe_float(bw.get("TMV")) or 0.0)
             + 2.0 * (safe_float(bw.get("CVD")) or 0.0)
             + (safe_float(bw.get("MACRO")) or 0.0)
             + (safe_float(bw.get("FUNDING")) or 0.0)
             + (safe_float(bw.get("SRD")) or 0.0))
    if total <= 0:
        return 0.0
    dir_keys = ("TMV", "CVD_4h", "CVD_12h", "MACRO", "FUNDING", "SRD")
    present = sum(item.get("eff_weight", 0.0) for item in evidence
                  if item.get("key") in dir_keys)
    return clamp(present / total, 0.0, 1.0)


def _conflict_level(agreement):
    if agreement >= 0.80:
        return "NONE"
    if agreement >= 0.65:
        return "MILD"
    if agreement >= 0.50:
        return "MATERIAL"
    return "SEVERE"


def _classify(edb_score, confidence, precondition_active, veto, config):
    side_for_sign = (SIDE_PUT_CREDIT_SPREAD if edb_score > 0
                     else SIDE_CALL_CREDIT_SPREAD)
    if veto:
        return "NEUTRAL", "NO_TRADE_BLOCKED", SIDE_NONE, "NO_TRADE_BLOCKED"
    neutral_abs = float(config.get("edb_neutral_score_abs", 0.12))
    conf_min = float(config.get("edb_conf_neutral_min", 35))
    if abs(edb_score) < neutral_abs or confidence < conf_min:
        if precondition_active:
            return ("NEUTRAL", "WAIT_CONFIRMATION", SIDE_NONE,
                    "WAIT_FOR_EVIDENCE")
        return "NEUTRAL", "NO_TRADE_BLOCKED", SIDE_NONE, "NO_TRADE_BLOCKED"

    lean = "BULLISH" if edb_score > 0 else "BEARISH"
    if not precondition_active:
        # direction is "warm" for display but window not open -> no trade
        return (lean, "NO_TRADE_BLOCKED", SIDE_NONE,
                "WAIT_DIE_ANCHOR_WINDOW")
    if confidence >= float(config.get("edb_conf_strong", 68)):
        return (lean + "_STRONG", "TRADE_SUPPORT_STRONG", side_for_sign,
                "ALLOW_DOWNSTREAM")
    if confidence >= float(config.get("edb_conf_weak", 50)):
        return (lean + "_WEAK", "TRADE_SUPPORT_WEAK", side_for_sign,
                "ALLOW_DOWNSTREAM_WITH_CAUTION")
    return "NEUTRAL", "WAIT_CONFIRMATION", SIDE_NONE, "WAIT_FOR_EVIDENCE"


def _price_return_pct(window):
    for key in ("price_return_pct", "momentum_return_pct"):
        value = safe_float((window or {}).get(key))
        if value is not None:
            return value
    momentum = safe_float((window or {}).get("momentum"))
    if momentum is not None:
        return momentum * 100.0
    return None


def _reason_codes(evidence, veto_reason, precondition_active):
    codes = []
    if veto_reason:
        codes.append(veto_reason)
    if not precondition_active:
        codes.append("DIE_ANCHOR_WINDOW_NOT_ACTIVE")
    active_keys = [item["key"] for item in evidence if item["weight"] > 0]
    if active_keys:
        codes.append("EVIDENCE:" + ",".join(active_keys))
    return codes


def _summary_cn(lean, support, confidence, edb_score, agreement, coverage,
                evidence, veto_reason, precondition_active):
    if veto_reason:
        zh = {
            "GGR_NEGATIVE_GAMMA_VETO": "负Gamma放大区制，单边卖权被否决",
            "MACRO_BLOCKING": "宏观硬阻断",
            "FUNDING_HARD_WARNING": "资金费率极端拥挤，硬阻断",
        }.get(veto_reason, veto_reason)
        return "EDB 阻断：" + zh + "，本轮不形成可交易方向。"
    if not precondition_active:
        head = "DIE+Anchor 窗口未开，方向仅作观察预热"
    else:
        head = {"TRADE_SUPPORT_STRONG": "强方向支持",
                "TRADE_SUPPORT_WEAK": "弱方向支持",
                "WAIT_CONFIRMATION": "证据未收敛，等待确认"}.get(
                    support, support)
    keys = ",".join(item["key"] for item in evidence)
    return ("{0}：EDB={1:+.2f} / 一致度={2:.0%} / 覆盖={3:.0%}"
            " / 置信={4} / 证据[{5}]。").format(
        head, edb_score, agreement, coverage, int(round(confidence)), keys)

# ================================================================
# SOURCE: demo/signal_events.py
# ================================================================
"""In-memory event log for confirmed DIE + Anchor repair signals."""



def _prefer(primary, key, fallback, fb_key):
    """EDB evidence detail first, raw factor payload as fallback, so observable
    readings (cvd_sum / 涨跌 / macro score) survive into the signal-event row
    even when EDB drops the evidence to zero weight and strips its detail."""
    value = (primary or {}).get(key)
    return value if value is not None else (fallback or {}).get(fb_key)


class SignalEventTracker:
    def __init__(self, config=None):
        self.config = config or CONFIG
        self.max_events = int(self.config.get("signal_event_max_count", 10))
        self.events = []
        self.seen_episode_ids = set()

    def maybe_record(self, neutral_repair_signal, factor_snapshot,
                     runtime_facts=None):
        signal = neutral_repair_signal or {}
        if signal.get("state") != "NR_REPAIR_CONFIRMED":
            return False
        event_context = signal.get("event_context") or {}
        episode_id = event_context.get("episode_id") or event_context.get(
            "event_id")
        if not episode_id or episode_id in self.seen_episode_ids:
            return False
        self.seen_episode_ids.add(episode_id)
        self.events.insert(0, self._build_event(
            episode_id, signal, factor_snapshot or {}, runtime_facts or {}))
        if len(self.events) > self.max_events:
            removed = self.events[self.max_events:]
            self.events = self.events[:self.max_events]
            for item in removed:
                old_id = item.get("episode_id")
                if old_id:
                    self.seen_episode_ids.discard(old_id)
        return True

    def snapshot(self):
        return {
            "max_events": self.max_events,
            "event_count": len(self.events),
            "events": [dict(item) for item in self.events],
        }

    def _build_event(self, episode_id, signal, factor_snapshot, runtime_facts):
        event_context = signal.get("event_context") or {}
        anchor_context = signal.get("anchor_context") or {}
        edb = factor_snapshot.get("edb") or {}
        ev = {item.get("key"): item for item in (edb.get("evidence") or [])}
        tmv = (ev.get("TMV") or {}).get("detail") or {}
        fast = (ev.get("CVD_4h") or {}).get("detail") or {}
        slow = (ev.get("CVD_12h") or {}).get("detail") or {}
        macro = (ev.get("MACRO") or {}).get("detail") or {}
        srd = (ev.get("SRD") or {}).get("detail") or {}
        ggr = (ev.get("GGR_SPATIAL") or {}).get("detail") or {}
        flow = factor_snapshot.get("flow") or {}
        micro = flow.get("micro_flow") or {}
        fast_raw = micro.get("fast_4h") or {}
        slow_raw = micro.get("slow_12h") or {}
        macro_raw = factor_snapshot.get("macro_pressure") or {}
        price = runtime_facts.get("current_price")
        if price is None:
            price = runtime_facts.get("bar_close")
        return {
            "episode_id": episode_id,
            "confirmed_time": now_ms(),
            "price_at_confirmation": safe_float(price),
            "price_at_event": event_context.get("price_at_event"),
            "episode_direction": (
                event_context.get("episode_direction")
                or event_context.get("event_direction")),
            "peak_m_die": (
                event_context.get("peak_m_die")
                or event_context.get("event_peak_mdie")),
            "peak_abs_m_die": (
                event_context.get("peak_abs_m_die")
                or event_context.get("event_peak_abs_mdie")),
            "event_count_merged": event_context.get("event_count_merged"),
            "anchor_score_at_event": anchor_context.get(
                "anchor_score_at_event"),
            "min_anchor_score_after_event": anchor_context.get(
                "min_anchor_score_after_event"),
            "anchor_score_at_confirmation": anchor_context.get(
                "anchor_score"),
            "anchor_nd_at_confirmation": anchor_context.get(
                "normalized_deviation"),
            "edb_lean": edb.get("lean"),
            "edb_support": edb.get("support_label"),
            "edb_confidence": edb.get("confidence"),
            "edb_score": edb.get("edb_score"),
            "conflict_level": edb.get("conflict_level"),
            "side_hint": edb.get("side_hint"),
            "tmv_blend": tmv.get("tmv_blend"),
            "tmv_direction": tmv.get("direction"),
            "macro_score": _prefer(macro, "macro_score", macro_raw,
                                   "macro_score"),
            "macro_regime": _prefer(macro, "macro_regime", macro_raw,
                                    "macro_regime"),
            "fast_4h_cvd_btc": _prefer(fast, "cvd_sum", fast_raw, "cvd_sum"),
            "fast_4h_return_pct": _prefer(fast, "price_return_pct", fast_raw,
                                          "momentum_return_pct"),
            "slow_12h_cvd_btc": _prefer(slow, "cvd_sum", slow_raw, "cvd_sum"),
            "srd_rr_blend": srd.get("rr_blend"),
            "ggr_regime": ggr.get("regime"),
            "funding_rate": flow.get("last_funding_rate"),
        }

# ================================================================
# SOURCE: demo/binance_adapter.py
# ================================================================
"""Binance public market-data adapter."""



class BinanceAdapter:
    def __init__(self, http_client, config=None):
        self.http = http_client
        self.config = config or CONFIG

    def fetch_spot_agg_trades(self, from_id=None, limit=None):
        params = {
            "symbol": self.config["spot_symbol"],
            "limit": int(limit or self.config["agg_trades_limit"]),
        }
        if from_id is not None:
            params["fromId"] = int(from_id)
        url = (self.config["binance_spot_base_url"]
               + self.config["binance_spot_agg_trades_path"])
        result = self.http.get_json(url, params=params)
        if result["quality"] != QUALITY_OK:
            return result
        if not isinstance(result["data"], list):
            result["quality"] = QUALITY_INVALID
            result["error"] = "spot_agg_trades_not_list"
            result["data"] = None
        return result

    def fetch_spot_depth(self, limit=20):
        url = self.config["binance_spot_base_url"] + "/api/v3/depth"
        return self.http.get_json(url, params={
            "symbol": self.config["spot_symbol"],
            "limit": int(limit),
        })

    def fetch_futures_agg_trades(self, from_id=None, limit=None):
        params = {
            "symbol": self.config["futures_symbol"],
            "limit": int(limit or self.config["agg_trades_limit"]),
        }
        if from_id is not None:
            params["fromId"] = int(from_id)
        url = (self.config["binance_futures_base_url"]
               + self.config["binance_futures_agg_trades_path"])
        result = self.http.get_json(url, params=params)
        if result["quality"] != QUALITY_OK:
            return result
        if not isinstance(result["data"], list):
            result["quality"] = QUALITY_INVALID
            result["error"] = "futures_agg_trades_not_list"
            result["data"] = None
        return result

    def fetch_futures_depth(self, limit=20):
        url = self.config["binance_futures_base_url"] + "/fapi/v1/depth"
        return self.http.get_json(url, params={
            "symbol": self.config["futures_symbol"],
            "limit": int(limit),
        })

    def fetch_premium_index(self):
        url = self.config["binance_futures_base_url"] + "/fapi/v1/premiumIndex"
        result = self.http.get_json(url, params={
            "symbol": self.config["futures_symbol"],
        })
        return result

    def fetch_futures_klines(self, interval=None, limit=None):
        url = self.config["binance_futures_base_url"] + "/fapi/v1/klines"
        result = self.http.get_json(url, params={
            "symbol": self.config["futures_symbol"],
            "interval": interval or self.config["tmvf_kline_interval"],
            "limit": int(limit or self.config["tmvf_kline_limit"]),
        })
        if result["quality"] != QUALITY_OK:
            return result
        if not isinstance(result["data"], list):
            result["quality"] = QUALITY_INVALID
            result["error"] = "futures_klines_not_list"
            result["data"] = None
        return result

    def fetch_funding_rate(self, start_time=None, end_time=None, limit=None):
        url = self.config["binance_futures_base_url"] + "/fapi/v1/fundingRate"
        params = {
            "symbol": self.config["futures_symbol"],
            "limit": int(limit or self.config["tmvf_funding_limit"]),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        result = self.http.get_json(url, params=params)
        if result["quality"] != QUALITY_OK:
            return result
        if not isinstance(result["data"], list):
            result["quality"] = QUALITY_INVALID
            result["error"] = "funding_rate_not_list"
            result["data"] = None
        return result

    def fetch_open_interest(self):
        url = self.config["binance_futures_base_url"] + "/fapi/v1/openInterest"
        return self.http.get_json(url, params={
            "symbol": self.config["futures_symbol"],
        })

    def fetch_taker_buy_sell_volume(self, period="5m", limit=30):
        url = (self.config["binance_futures_base_url"]
               + "/futures/data/takerlongshortRatio")
        return self.http.get_json(url, params={
            "symbol": self.config["futures_symbol"],
            "period": period,
            "limit": int(limit),
        })

    @staticmethod
    def normalize_agg_trade(item):
        if not isinstance(item, dict):
            return None
        trade_id = safe_int(item.get("a"))
        price = safe_float(item.get("p"))
        qty = safe_float(item.get("q"))
        ts_ms = safe_int(item.get("T"))
        is_buyer_maker = item.get("m")
        if (trade_id is None or price is None or price <= 0
                or qty is None or qty <= 0
                or not isinstance(is_buyer_maker, bool)):
            return None
        signed_qty = -qty if is_buyer_maker else qty
        return add_schema({
            "id": trade_id,
            "price": price,
            "qty": qty,
            "signed_qty": signed_qty,
            "ts_ms": ts_ms,
        }, SCHEMA_MARKET_TRADE)

    @staticmethod
    def best_mid_from_depth(depth_payload):
        if not isinstance(depth_payload, dict):
            return None
        bids = depth_payload.get("bids") or []
        asks = depth_payload.get("asks") or []
        if not bids or not asks:
            return None
        best_bid = safe_float(bids[0][0]) if len(bids[0]) >= 2 else None
        best_ask = safe_float(asks[0][0]) if len(asks[0]) >= 2 else None
        if best_bid is None or best_ask is None or best_bid <= 0 or best_ask <= 0:
            return None
        return (best_bid + best_ask) / 2.0

    @staticmethod
    def normalize_premium_index(payload):
        if not isinstance(payload, dict):
            return {}
        return {
            "mark_price": safe_float(payload.get("markPrice")),
            "index_price": safe_float(payload.get("indexPrice")),
            "last_funding_rate": safe_float(payload.get("lastFundingRate")),
            "next_funding_time": safe_int(payload.get("nextFundingTime")),
            "time": safe_int(payload.get("time")),
        }

    @staticmethod
    def normalize_kline(row):
        if not isinstance(row, list) or len(row) < 7:
            return None
        open_time = safe_int(row[0])
        open_price = safe_float(row[1])
        high = safe_float(row[2])
        low = safe_float(row[3])
        close = safe_float(row[4])
        volume = safe_float(row[5])
        close_time = safe_int(row[6])
        if (open_time is None or close_time is None
                or open_price is None or high is None or low is None
                or close is None or close <= 0 or volume is None):
            return None
        return {
            "open_time": open_time,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "close_time": close_time,
        }

    @staticmethod
    def normalize_funding_point(row):
        if not isinstance(row, dict):
            return None
        funding_rate = safe_float(row.get("fundingRate"))
        funding_time = safe_int(row.get("fundingTime"))
        if funding_rate is None or funding_time is None:
            return None
        return {
            "funding_rate": funding_rate,
            "funding_time": funding_time,
            "mark_price": safe_float(row.get("markPrice")),
        }

# ================================================================
# SOURCE: demo/deribit_adapter.py
# ================================================================
"""Deribit public market-data adapter."""



class DeribitAdapter:
    def __init__(self, http_client, config=None):
        self.http = http_client
        self.config = config or CONFIG

    def public_get(self, method, params=None):
        url = self.config["deribit_base_url"] + "/" + method
        result = self.http.get_json(url, params=params or {})
        if result["quality"] != QUALITY_OK:
            return result
        data = result["data"]
        if isinstance(data, dict) and "result" in data:
            result["data"] = data.get("result")
            return result
        result["quality"] = QUALITY_INVALID
        result["error"] = "deribit_missing_result"
        result["data"] = None
        return result

    def get_instruments(self, currency=None, kind="option", expired=False):
        return self.public_get("public/get_instruments", {
            "currency": currency or self.config["deribit_currency"],
            "kind": kind,
            "expired": "true" if expired else "false",
        })

    def get_index_price(self, index_name="btc_usd"):
        return self.public_get("public/get_index_price", {
            "index_name": index_name,
        })

    def get_ticker(self, instrument_name):
        return self.public_get("public/ticker", {
            "instrument_name": instrument_name,
        })

    @staticmethod
    def normalize_ticker(data, instrument=None):
        """Normalize public/ticker into per-option greeks for SRD/GGR.

        Only direction/regime inputs (delta, gamma, mark_iv, OI). No legs,
        quotes, or order fields are produced.
        """
        if not isinstance(data, dict):
            return None
        greeks = data.get("greeks") or {}
        inst = instrument or {}
        name = data.get("instrument_name") or inst.get("instrument_name")
        option_type = inst.get("option_type")
        if option_type is None and isinstance(name, str):
            if name.endswith("-C"):
                option_type = "call"
            elif name.endswith("-P"):
                option_type = "put"
        expiry = safe_int(inst.get("expiration_ts_ms"))
        hours = None
        if expiry:
            hours = (expiry - now_ms()) / (60 * 60 * 1000.0)
        return {
            "instrument_name": name,
            "option_type": option_type,
            "strike": safe_float(inst.get("strike")),
            "delta": safe_float(greeks.get("delta")),
            "gamma": safe_float(greeks.get("gamma")),
            "vega": safe_float(greeks.get("vega")),
            "mark_iv": safe_float(data.get("mark_iv")),
            "open_interest": safe_float(data.get("open_interest")),
            "underlying_price": safe_float(data.get("underlying_price")),
            "index_price": safe_float(data.get("index_price")),
            "expiration_ts_ms": expiry,
            "hours_to_expiry": hours,
        }

    @staticmethod
    def normalize_instrument(item):
        if not isinstance(item, dict):
            return None
        name = item.get("instrument_name")
        if not name:
            return None
        return {
            "instrument_name": name,
            "base_currency": item.get("base_currency"),
            "expiration_ts_ms": safe_int(item.get("expiration_timestamp")),
            "strike": safe_float(item.get("strike")),
            "option_type": item.get("option_type"),
            "tick_size": safe_float(item.get("tick_size")),
            "contract_size": safe_float(item.get("contract_size")),
            "min_trade_amount": safe_float(item.get("min_trade_amount")),
            "state": item.get("state"),
            "is_active": bool(item.get("is_active")),
        }

# ================================================================
# SOURCE: demo/skew_factor.py
# ================================================================
"""SRD: Skew / 25-delta Risk Reversal directional factor (v0.5).

Pure compute over normalized Deribit option greeks. Emits a signed direction
vote for EDB. Direction comes from RELATIVE skew (rr_z vs rolling baseline +
skew momentum delta_rr), never from the raw sign of RR, because BTC 25d skew
is structurally negative (put premium is the norm).

Boundary: reads only IV/delta/OI for direction. No legs, quotes, orders.
"""



def evaluate_skew_rr(option_quotes, rr_history=None, config=None, ts_ms=None):
    """option_quotes: list of normalized option dicts with keys
    option_type ('call'/'put'), strike, delta, mark_iv (fraction or %),
    open_interest, expiration_ts_ms, hours_to_expiry.
    rr_history: list of past blended RR floats (oldest..newest) for baseline.
    """
    config = config or CONFIG
    quotes = _clean_quotes(option_quotes, config)
    by_expiry = {}
    for quote in quotes:
        by_expiry.setdefault(quote["expiration_ts_ms"], []).append(quote)

    per_expiry = {}
    blended_terms = []
    for label, target_hours in (("24h", 24.0), ("48h", 48.0)):
        chosen = _nearest_expiry(by_expiry, target_hours)
        if chosen is None:
            per_expiry[label] = {"data_state": "MISSING"}
            continue
        result = _expiry_rr(by_expiry[chosen], config)
        per_expiry[label] = result
        if result.get("data_state") == "OK":
            # weight 48h slightly more (closer to seller-leg horizon span)
            weight = 0.45 if label == "24h" else 0.55
            blended_terms.append((result["rr_25"], result["atm_iv"], weight))

    payload = {
        "factor_name": "SRD",
        "factor_version": "v1.0",
        "per_expiry": per_expiry,
        "rr_blend": None,
        "skew_norm_blend": None,
        "rr_z": 0.0,
        "delta_rr": 0.0,
        "vote": 0.0,
        "vote_confidence": 0.0,
        "lean": "NEUTRAL",
        "data_state": "MISSING",
        "reason_codes": [],
    }
    if not blended_terms:
        payload["reason_codes"].append("SRD_NO_VALID_25D")
        return add_schema(payload, SCHEMA_SKEW, config)

    wsum = sum(w for _rr, _atm, w in blended_terms)
    rr_blend = sum(rr * w for rr, _atm, w in blended_terms) / max(wsum, 1e-9)
    atm_blend = sum(atm * w for _rr, atm, w in blended_terms) / max(wsum, 1e-9)
    skew_norm = rr_blend / atm_blend if atm_blend and atm_blend > 0 else 0.0
    payload["rr_blend"] = rr_blend
    payload["skew_norm_blend"] = skew_norm
    payload["data_state"] = "OK"

    history = [safe_float(v) for v in (rr_history or [])]
    history = [v for v in history if v is not None]
    rr_z = _robust_z(rr_blend, history, config)
    delta_rr = _delta_rr(rr_blend, history, config)
    payload["rr_z"] = rr_z
    payload["delta_rr"] = delta_rr

    # direction = relative deviation + momentum, NOT raw sign of rr_blend.
    raw = 0.6 * rr_z + 0.4 * _delta_rr_term(delta_rr, atm_blend)
    vote = clamp(raw * float(config.get("srd_vote_scale", 1.0)), -1.0, 1.0)

    confidence = _vote_confidence(per_expiry, history, config)
    payload["vote"] = vote
    payload["vote_confidence"] = confidence
    payload["lean"] = _lean_label(vote, config)
    if rr_z > 0 or delta_rr > 0:
        payload["reason_codes"].append("SKEW_TILTING_BULLISH_VS_BASELINE")
    elif rr_z < 0 or delta_rr < 0:
        payload["reason_codes"].append("SKEW_TILTING_BEARISH_VS_BASELINE")
    return add_schema(payload, SCHEMA_SKEW, config)


def _clean_quotes(option_quotes, config):
    out = []
    min_oi = float(config.get("srd_min_open_interest", 0.0) or 0.0)
    for item in option_quotes or []:
        if not isinstance(item, dict):
            continue
        opt = (item.get("option_type") or "").lower()
        if opt not in ("call", "put"):
            continue
        delta = safe_float(item.get("delta"))
        iv = _iv_fraction(safe_float(item.get("mark_iv")))
        strike = safe_float(item.get("strike"))
        expiry = item.get("expiration_ts_ms")
        if delta is None or iv is None or iv <= 0 or strike is None or not expiry:
            continue
        oi = safe_float(item.get("open_interest"))
        if oi is not None and oi < min_oi:
            continue
        out.append({
            "option_type": opt,
            "strike": strike,
            "delta": delta,
            "mark_iv": iv,
            "open_interest": oi,
            "expiration_ts_ms": int(expiry),
            "hours_to_expiry": safe_float(item.get("hours_to_expiry")),
        })
    return out


def _iv_fraction(mark_iv):
    """Deribit mark_iv is in percent (e.g. 65.4). Normalize to fraction."""
    if mark_iv is None:
        return None
    return mark_iv / 100.0 if mark_iv > 3.0 else mark_iv


def _nearest_expiry(by_expiry, target_hours, ref_ms=None):
    if not by_expiry:
        return None
    best = None
    best_dist = None
    for expiry, quotes in by_expiry.items():
        hours = None
        for quote in quotes:
            hours = quote.get("hours_to_expiry")
            if hours is not None:
                break
        if hours is None:
            continue
        dist = abs(hours - target_hours)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = expiry
    return best


def _expiry_rr(quotes, config):
    target = float(config.get("srd_target_delta", 0.25))
    atm = float(config.get("srd_atm_delta", 0.50))
    calls = [q for q in quotes if q["option_type"] == "call"]
    puts = [q for q in quotes if q["option_type"] == "put"]
    call_iv = _iv_at_abs_delta(calls, target)
    put_iv = _iv_at_abs_delta(puts, target)
    atm_iv = _atm_iv(calls + puts, atm)
    if call_iv is None or put_iv is None or atm_iv is None or atm_iv <= 0:
        return {"data_state": "INSUFFICIENT"}
    hours = None
    for q in quotes:
        if q.get("hours_to_expiry") is not None:
            hours = q["hours_to_expiry"]
            break
    return {
        "data_state": "OK",
        "call_25d_iv": call_iv,
        "put_25d_iv": put_iv,
        "atm_iv": atm_iv,
        "rr_25": call_iv - put_iv,
        "skew_norm": (call_iv - put_iv) / atm_iv,
        "hours_to_expiry": hours,
    }


def _iv_at_abs_delta(side_quotes, target_abs_delta):
    """Interpolate IV at |delta| = target across one option side."""
    points = []
    for q in side_quotes:
        ad = abs(q["delta"])
        if 0.0 < ad < 1.0:
            points.append((ad, q["mark_iv"]))
    if len(points) < 1:
        return None
    points.sort(key=lambda item: item[0])
    if len(points) == 1:
        return points[0][1]
    # bracket target
    for i in range(len(points) - 1):
        lo_d, lo_iv = points[i]
        hi_d, hi_iv = points[i + 1]
        if lo_d <= target_abs_delta <= hi_d and hi_d != lo_d:
            w = (target_abs_delta - lo_d) / (hi_d - lo_d)
            return lo_iv * (1.0 - w) + hi_iv * w
    # target outside range -> nearest available delta
    nearest = min(points, key=lambda item: abs(item[0] - target_abs_delta))
    return nearest[1]


def _atm_iv(quotes, atm_delta):
    points = [(abs(q["delta"]), q["mark_iv"]) for q in quotes
              if 0.0 < abs(q["delta"]) < 1.0]
    if not points:
        return None
    nearest = min(points, key=lambda item: abs(item[0] - atm_delta))
    return nearest[1]


def _robust_z(value, history, config):
    min_hist = int(config.get("srd_rr_baseline_min_history", 12))
    if len(history) < min_hist:
        return 0.0
    window = int(config.get("srd_rr_baseline_window", 240))
    sample = history[-window:]
    med = _median(sample)
    mad = _median([abs(x - med) for x in sample])
    scale = mad * 1.4826
    if scale <= 1e-9:
        # fall back to std-like scale
        mean = sum(sample) / len(sample)
        var = sum((x - mean) ** 2 for x in sample) / len(sample)
        scale = var ** 0.5
    if scale <= 1e-9:
        return 0.0
    return clamp((value - med) / scale, -3.0, 3.0) / 3.0  # normalize to [-1,1]


def _delta_rr(value, history, config):
    lookback = int(config.get("srd_delta_rr_lookback", 6))
    if len(history) < 1:
        return 0.0
    ref = history[-lookback] if len(history) >= lookback else history[0]
    return value - ref


def _delta_rr_term(delta_rr, atm_iv):
    if atm_iv is None or atm_iv <= 0:
        return clamp(delta_rr * 20.0, -1.0, 1.0)
    # express momentum relative to ATM vol scale, then bound
    return clamp((delta_rr / atm_iv) * 4.0, -1.0, 1.0)


def _vote_confidence(per_expiry, history, config):
    ok = [v for v in per_expiry.values() if v.get("data_state") == "OK"]
    if not ok:
        return 0.0
    conf = 0.5 + 0.15 * len(ok)  # more expiries agreeing on data -> steadier
    min_hist = int(config.get("srd_rr_baseline_min_history", 12))
    if len(history) < min_hist:
        conf *= 0.6  # baseline not warmed up
    # near-expiry downweight
    down_h = float(config.get("srd_near_expiry_downweight_hours", 8.0))
    hours = [v.get("hours_to_expiry") for v in ok
             if v.get("hours_to_expiry") is not None]
    if hours and min(hours) < down_h:
        conf *= 0.6
    return clamp(conf, 0.0, 1.0)


def _lean_label(vote, config):
    if vote >= 0.30:
        return "BULLISH_TILT"
    if vote <= -0.30:
        return "BEARISH_TILT"
    return "NEUTRAL"


def _median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0

# ================================================================
# SOURCE: demo/gamma_regime.py
# ================================================================
"""GGR: Global Gamma Regime factor (v0.5).

Pure compute. Answers two things the model was missing:
  (A) is dealer hedging PINNING (positive gamma, safe to sell single-side
      premium) or AMPLIFYING (negative gamma, dangerous)?  -> a safety gate.
  (B) which strike is the structural magnet (pin) into the window?  -> a small
      spatial vote, trusted ONLY in the pinning regime.

Sources: gexmonitor snapshot (flip_point/spring/asset_price, already ingested;
optional net_gex/walls from raw_payload) + Deribit per-strike gamma x OI.

GGR is primarily a GATE and confidence modulator, NOT a trend vote. The gamma
sign is a dealer-inventory proxy => treated as probabilistic, never as a hard
directional certainty.
"""


# distance (as fraction of price) beyond the transition band over which regime
# strength ramps from 0.4 to 1.0. Robust default, not a proven optimum.
_REGIME_STRENGTH_SPAN = 0.012


def evaluate_gamma_regime(gex_snapshot, current_price, option_quotes=None,
                          config=None):
    config = config or CONFIG
    gex = gex_snapshot or {}
    flip = safe_float(gex.get("flip_point"))
    price = safe_float(current_price)
    if price is None:
        price = safe_float(gex.get("asset_price"))

    payload = {
        "factor_name": "GGR",
        "factor_version": "v1.0",
        "regime": "UNKNOWN",
        "regime_strength": 0.0,
        "flip_point": flip,
        "asset_price": price,
        "distance_to_flip_pct": None,
        "net_gex_sign": _net_gex_sign(gex),
        "net_gamma_notional": _net_gamma_notional(option_quotes),
        "max_gamma_strike": None,
        "max_gamma_oi_share": None,
        "pin": None,
        "gate_action": "NEUTRAL",
        "confidence_multiplier": 1.0,
        "spatial_vote": 0.0,
        "spatial_weight": 0.0,
        "veto": False,
        "data_state": "MISSING",
        "reason_codes": [],
    }
    if flip is None or flip <= 0 or price is None or price <= 0:
        payload["reason_codes"].append("GGR_FLIP_OR_PRICE_MISSING")
        return add_schema(payload, SCHEMA_GAMMA_REGIME, config)

    dist_frac = abs(price - flip) / price
    payload["distance_to_flip_pct"] = dist_frac * 100.0
    payload["data_state"] = "OK"

    band = float(config.get("ggr_transition_band_pct", 0.003))
    if dist_frac <= band:
        regime = "TRANSITION"
        strength = clamp(dist_frac / max(band, 1e-9) * 0.4, 0.0, 0.4)
    else:
        strength = clamp(0.4 + (dist_frac - band) / _REGIME_STRENGTH_SPAN,
                         0.4, 1.0)
        regime = ("POSITIVE_GAMMA_PINNING" if price > flip
                  else "NEGATIVE_GAMMA_AMPLIFYING")
    # net_gex sign (if provided) can flip the inferred regime conservatively:
    # only DOWNGRADE trust on disagreement, never upgrade.
    sign = payload["net_gex_sign"]
    if regime == "POSITIVE_GAMMA_PINNING" and sign == "-":
        regime = "TRANSITION"
        strength = min(strength, 0.4)
        payload["reason_codes"].append("GGR_AGG_NET_GEX_DISAGREES")
    elif regime == "NEGATIVE_GAMMA_AMPLIFYING" and sign == "+":
        regime = "TRANSITION"
        strength = min(strength, 0.4)
        payload["reason_codes"].append("GGR_AGG_NET_GEX_DISAGREES")
    payload["regime"] = regime
    payload["regime_strength"] = strength

    _apply_gate(payload, regime, strength, config)
    max_strike, max_share = _max_gamma_strike(gex, option_quotes)
    payload["max_gamma_strike"] = max_strike
    payload["max_gamma_oi_share"] = max_share
    _apply_pin(payload, gex, option_quotes, price, regime, config)
    return add_schema(payload, SCHEMA_GAMMA_REGIME, config)


def _net_gamma_notional(option_quotes):
    """Signed sum of gamma*OI (calls +, puts -). Dealer-inventory proxy; sign
    indicates a tangible 'how much gamma' read, not a certainty."""
    total = 0.0
    found = False
    for item in option_quotes or []:
        if not isinstance(item, dict):
            continue
        gamma = safe_float(item.get("gamma"))
        oi = safe_float(item.get("open_interest"))
        if gamma is None or oi is None:
            continue
        sign = 1.0 if (item.get("option_type") == "call") else -1.0
        total += sign * gamma * oi
        found = True
    return total if found else None


def _apply_gate(payload, regime, strength, config):
    boost_max = float(config.get("ggr_positive_conf_boost_max", 1.15))
    cut = float(config.get("ggr_negative_cut_strength", 0.50))
    veto = float(config.get("ggr_negative_veto_strength", 0.80))
    floor = float(config.get("ggr_negative_conf_floor", 0.40))
    if regime == "POSITIVE_GAMMA_PINNING":
        payload["gate_action"] = "SUPPORT"
        payload["confidence_multiplier"] = 1.0 + (boost_max - 1.0) * strength
        payload["reason_codes"].append("PRICE_ABOVE_FLIP_POSITIVE_GAMMA")
    elif regime == "NEGATIVE_GAMMA_AMPLIFYING":
        if strength >= veto:
            payload["gate_action"] = "VETO"
            payload["confidence_multiplier"] = 0.0
            payload["veto"] = True
            payload["reason_codes"].append("STRONG_NEGATIVE_GAMMA_VETO")
        elif strength >= cut:
            # lerp 1.0 -> floor as strength goes cut -> veto
            span = max(veto - cut, 1e-9)
            mult = 1.0 - (1.0 - floor) * ((strength - cut) / span)
            payload["gate_action"] = "CUT_CONFIDENCE"
            payload["confidence_multiplier"] = clamp(mult, floor, 1.0)
            payload["reason_codes"].append("NEGATIVE_GAMMA_CUTS_CONFIDENCE")
        else:
            payload["gate_action"] = "NEUTRAL"
            payload["confidence_multiplier"] = 0.95
            payload["reason_codes"].append("MILD_NEGATIVE_GAMMA")
    else:
        payload["gate_action"] = "NEUTRAL"
        payload["confidence_multiplier"] = 0.98
        payload["reason_codes"].append("GAMMA_FLIP_TRANSITION_ZONE")


def _apply_pin(payload, gex, option_quotes, price, regime, config):
    pin_strike, share = _max_gamma_strike(gex, option_quotes)
    if pin_strike is None or pin_strike <= 0:
        return
    min_share = float(config.get("ggr_pin_min_oi_share", 0.15))
    if share is not None and share < min_share:
        return
    if regime == "POSITIVE_GAMMA_PINNING":
        # trust scales with concentration share (bounded)
        pin_trust = clamp((share or min_share) / max(min_share, 1e-9) * 0.5,
                          0.0, 1.0)
    else:
        pin_trust = float(config.get("ggr_pin_trust_negative_gamma", 0.0))
    dist_frac = (pin_strike - price) / price
    ref = float(config.get("ggr_pin_distance_ref_pct", 0.02))
    cap = float(config.get("ggr_spatial_vote_cap", 0.25))
    direction = 1.0 if dist_frac > 0 else (-1.0 if dist_frac < 0 else 0.0)
    magnitude = clamp(abs(dist_frac) / max(ref, 1e-9), 0.0, 1.0)
    spatial_vote = clamp(direction * magnitude * pin_trust, -cap, cap)
    payload["pin"] = {
        "pin_strike": pin_strike,
        "distance_to_pin_pct": dist_frac * 100.0,
        "pin_pull_direction": "UP" if dist_frac > 0 else (
            "DOWN" if dist_frac < 0 else "FLAT"),
        "gamma_oi_share": share,
        "pin_trust": pin_trust,
    }
    payload["spatial_vote"] = spatial_vote
    # spatial weight is the trust itself (0 in negative gamma -> no influence)
    payload["spatial_weight"] = pin_trust


def _max_gamma_strike(gex, option_quotes):
    """Prefer Deribit per-strike gamma x OI; fall back to gexmonitor walls."""
    by_strike = {}
    for item in option_quotes or []:
        if not isinstance(item, dict):
            continue
        strike = safe_float(item.get("strike"))
        gamma = safe_float(item.get("gamma"))
        oi = safe_float(item.get("open_interest"))
        if strike is None or gamma is None or oi is None:
            continue
        by_strike[strike] = by_strike.get(strike, 0.0) + abs(gamma) * abs(oi)
    if by_strike:
        total = sum(by_strike.values())
        if total <= 0:
            return None, None
        pin = max(by_strike.items(), key=lambda kv: kv[1])
        return pin[0], pin[1] / total
    # fallback: gexmonitor walls / max_pain in raw_payload
    raw = gex.get("raw_payload") if isinstance(gex, dict) else None
    for key in ("max_pain", "maxPain", "call_wall", "callWall",
                "put_wall", "putWall"):
        value = _find_number(raw, key)
        if value is not None and value > 0:
            return value, None
    return None, None


def _net_gex_sign(gex):
    raw = gex.get("raw_payload") if isinstance(gex, dict) else None
    for key in ("net_gex", "netGex", "total_gamma", "totalGamma",
                "net_gamma", "netGamma"):
        value = _find_number(raw, key)
        if value is not None:
            return "+" if value > 0 else ("-" if value < 0 else "0")
    return None


def _find_number(value, key):
    if isinstance(value, dict):
        if key in value:
            return safe_float(value.get(key))
        for child in value.values():
            found = _find_number(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_number(child, key)
            if found is not None:
                return found
    return None

# ================================================================
# SOURCE: demo/gex_adapter.py
# ================================================================
"""GEXMonitor adapter and raw-to-effective anchor state."""

import datetime



class GexAdapter:
    def __init__(self, http_client, config=None):
        self.http = http_client
        self.config = config or CONFIG
        self.last_fetch_ms = None
        self.last_result = None

    def fetch_latest(self):
        current_ms = now_ms()
        min_interval = int(self.config.get("gex_min_fetch_interval_ms", 0))
        if (self.last_result is not None and self.last_fetch_ms is not None
                and min_interval > 0
                and current_ms - self.last_fetch_ms < min_interval):
            cached = dict(self.last_result)
            cached["cached"] = True
            return cached

        result = self.http.get_json(
            self.config["gex_base_url"],
            params={
                "asset": self.config["asset"],
                "exchange": self.config["gex_exchange"],
                "lite": self.config["gex_lite"],
                "t": current_ms,
            },
            headers={"Referer": "https://gexmonitor.com/"},
        )
        if result["quality"] != QUALITY_OK:
            return self._remember_fetch_result(result, current_ms)
        parsed = self.parse_payload(result["data"])
        if parsed is None:
            result["quality"] = QUALITY_INVALID
            result["error"] = "gex_payload_unrecognized"
            result["data"] = None
        else:
            result["data"] = parsed
        return self._remember_fetch_result(result, current_ms)

    def _remember_fetch_result(self, result, fetch_ms):
        result["cached"] = False
        self.last_fetch_ms = fetch_ms
        self.last_result = dict(result)
        return result

    @classmethod
    def parse_payload(cls, payload):
        if payload is None:
            return None
        flip = cls._find_number(payload, [
            "flip_point", "flipPoint", "gamma_flip", "gammaFlip",
            "gex_flip", "gexFlip", "gex_flip_price", "flip",
        ])
        asset_price = cls._find_number(payload, [
            "asset_price", "assetPrice", "spot", "spot_price",
            "index_price", "underlying_price",
        ])
        direct_spring = cls._find_number(payload, [
            "spring", "slope", "gamma_slope", "hedging_slope",
            "dH_dP", "dh_dp",
        ])
        spring = direct_spring
        if spring is None:
            spring = cls._spring_from_hedging_curve(payload, flip)
        if flip is None or flip <= 0:
            return None
        source_ts_ms = cls._find_timestamp_ms(payload)
        if source_ts_ms is None:
            return None
        return {
            "flip_point": flip,
            "spring": spring if spring is not None else 0.0,
            "source_ts_ms": source_ts_ms,
            "asset_price": asset_price,
            "quality": QUALITY_OK,
            "raw_payload": payload,
        }

    @classmethod
    def _spring_from_hedging_curve(cls, payload, flip):
        curve = cls._find_first_key(payload, ("hedging_curve", "hedgingCurve"))
        if not isinstance(curve, list) or len(curve) < 2:
            return 0.0
        points = []
        for item in curve:
            if not isinstance(item, dict):
                continue
            price = cls._find_number(item, [
                "price", "price_hi", "priceHi", "strike", "x", "spot",
            ])
            hedging = cls._find_number(item, [
                "hedging_btc", "hedgingBtc", "btc", "value", "y",
                "hedging", "hedge",
            ])
            if price is not None and hedging is not None:
                points.append((price, hedging))
        points.sort(key=lambda item: item[0])
        for index in range(len(points) - 1):
            price_lo, hedge_lo = points[index]
            price_hi, hedge_hi = points[index + 1]
            if price_lo <= flip <= price_hi and price_hi != price_lo:
                return abs(hedge_hi - hedge_lo) / abs(price_hi - price_lo)
        closest = sorted(points, key=lambda item: abs(item[0] - flip))[:2]
        if len(closest) == 2 and closest[0][0] != closest[1][0]:
            return abs(closest[1][1] - closest[0][1]) / abs(
                closest[1][0] - closest[0][0])
        return 0.0

    @classmethod
    def _find_timestamp_ms(cls, value):
        keys = (
            "source_ts_ms",
            "sourceTsMs",
            "timestamp_ms",
            "timestamp",
            "time",
            "updated_at_ms",
            "updatedAt",
            "updated_at",
            "updateTime",
            "createdAt",
            "created_at",
        )
        if isinstance(value, dict):
            for key in keys:
                if key in value:
                    parsed = cls._coerce_timestamp_ms(value.get(key))
                    if parsed is not None:
                        return parsed
            for child in value.values():
                parsed = cls._find_timestamp_ms(child)
                if parsed is not None:
                    return parsed
        elif isinstance(value, list):
            for child in value:
                parsed = cls._find_timestamp_ms(child)
                if parsed is not None:
                    return parsed
        return None

    @classmethod
    def _coerce_timestamp_ms(cls, value):
        numeric = safe_float(value)
        if numeric is not None:
            if numeric <= 0:
                return None
            parsed = safe_int(numeric)
            if parsed is None:
                return None
            if parsed < 10000000000:
                parsed *= 1000
            return parsed
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.datetime.fromisoformat(text)
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp() * 1000)

    @classmethod
    def _find_first_key(cls, value, keys):
        if isinstance(value, dict):
            for key in keys:
                if key in value:
                    return value.get(key)
            for child in value.values():
                found = cls._find_first_key(child, keys)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = cls._find_first_key(child, keys)
                if found is not None:
                    return found
        return None

    @classmethod
    def _find_number(cls, value, keys):
        if isinstance(value, dict):
            for key in keys:
                if key in value:
                    parsed = safe_float(value.get(key))
                    if parsed is not None:
                        return parsed
            for child in value.values():
                parsed = cls._find_number(child, keys)
                if parsed is not None:
                    return parsed
        elif isinstance(value, list):
            for child in value:
                parsed = cls._find_number(child, keys)
                if parsed is not None:
                    return parsed
        return None


class GexAnchorState:
    def __init__(self, config=None):
        self.config = config or CONFIG
        self.raw = None
        self.effective = None
        self.pending = None
        self.observation_queue = []
        self.last_acceptance_event = None
        self.last_effective_accept_ms = None
        self.last_raw_ingest_ms = None
        self.prev_band_half = None

    def ingest(self, raw):
        if not raw:
            return self.snapshot(quality=QUALITY_MISSING,
                                 reasons=["GEX_RAW_MISSING"])
        raw = dict(raw)
        raw_flip = safe_float(raw.get("flip_point"))
        if raw_flip is None or raw_flip <= 0:
            return self.snapshot(quality=QUALITY_INVALID,
                                 reasons=["GEX_RAW_MISSING"])
        raw["flip_point"] = raw_flip
        self.raw = raw
        now = now_ms()

        velocity_rejected = self._velocity_rejected(raw, now)
        self.last_raw_ingest_ms = now
        if velocity_rejected:
            promoted = self._observe_or_promote(raw, now)
            if promoted is None:
                self.last_acceptance_event = "VELOCITY_REJECTED_OBSERVING"
                return self.snapshot(reasons=["GEX_VELOCITY_REJECTED"])
            self._open_or_extend_pending(
                promoted, now, "OBSERVATION_PROMOTED_PENDING")
            return self.snapshot(reasons=["GEX_PENDING_CONSENSUS"])

        guarded = self._spot_guarded(raw)

        if self.effective is None:
            if guarded:
                self.last_acceptance_event = "SPOT_GUARD_BLOCKED_COLD_START"
                return self.snapshot(reasons=["GEX_SPOT_GUARD_PENDING"])
            self._accept_effective(raw, now, "INITIAL")
            return self.snapshot()

        if guarded:
            self._open_or_extend_pending(
                raw, now, "SPOT_GUARD_PENDING", guarded=True)
            return self.snapshot(reasons=["GEX_SPOT_GUARD_PENDING"])

        eff_flip = safe_float(self.effective.get("flip_point"))
        band_est = self._estimate_band_half_for_acceptance(eff_flip)
        jump = abs(raw_flip - eff_flip) / band_est
        if jump < self.config["gex_accept_small_absorb_frac"]:
            self._accept_effective(raw, now, "ABSORBED_SMALL")
            return self.snapshot()

        event = "WATCHDOG_PENDING"
        if jump < self.config["gex_accept_watchdog_jump"]:
            event = "PENDING"
        self._open_or_extend_pending(
            raw, now, event, watchdog=(event == "WATCHDOG_PENDING"))
        return self.snapshot(reasons=["GEX_PENDING_CONSENSUS"])

    def _accept_effective(self, raw, now, event):
        self.effective = raw
        self.pending = None
        self.observation_queue = []
        self.last_acceptance_event = event
        self.last_effective_accept_ms = now

    def update_band_reference(self, band_half):
        value = safe_float(band_half)
        if value is not None and value > 0:
            self.prev_band_half = value

    def _estimate_band_half_for_acceptance(self, ref_price):
        if self.prev_band_half is not None and self.prev_band_half > 0:
            return self.prev_band_half
        ref_price = safe_float(ref_price)
        if ref_price is None or ref_price <= 0:
            return 1e-9
        return max(ref_price * self.config["gex_accept_bootstrap_band_pct"],
                   1e-9)

    def _spot_guarded(self, raw):
        raw_flip = safe_float(raw.get("flip_point"))
        raw_price = safe_float(raw.get("asset_price"))
        if raw_flip is None or raw_price is None or raw_price <= 0:
            return False
        gap = abs(raw_flip - raw_price)
        frac_exceeded = (
            gap / raw_price > self.config["gex_accept_spot_guard_frac"])
        sigma_limit = self.config.get("gex_accept_spot_guard_sigma")
        sigma_exceeded = False
        if sigma_limit is not None and self.prev_band_half:
            sigma_exceeded = gap / self.prev_band_half > sigma_limit
        return frac_exceeded or sigma_exceeded

    def _velocity_rejected(self, raw, now):
        if not self.effective or self.last_raw_ingest_ms is None:
            return False
        raw_flip = safe_float(raw.get("flip_point"))
        eff_flip = safe_float(self.effective.get("flip_point"))
        if raw_flip is None or eff_flip is None or eff_flip <= 0:
            return False
        elapsed_ms = now - self.last_raw_ingest_ms
        if elapsed_ms <= 0:
            return False
        elapsed_sec = elapsed_ms / 1000.0
        frac_per_sec = abs(raw_flip - eff_flip) / eff_flip / elapsed_sec
        return frac_per_sec > self.config["gex_velocity_guard_frac_per_sec"]

    def _observe_or_promote(self, raw, now):
        window = int(self.config["gex_observation_window"])
        self.observation_queue.append(dict(raw))
        if len(self.observation_queue) > window:
            self.observation_queue = self.observation_queue[-window:]
        if len(self.observation_queue) < window:
            return None

        flips = [safe_float(item.get("flip_point"))
                 for item in self.observation_queue]
        flips = [value for value in flips if value is not None and value > 0]
        if len(flips) < window:
            self.observation_queue = []
            return None
        mean_flip = sum(flips) / float(len(flips))
        std_flip = population_std(flips) or 0.0
        if mean_flip <= 0 or std_flip / mean_flip > self.config[
                "gex_observation_stddev_mean_max"]:
            self.observation_queue = []
            return None

        promoted = dict(raw)
        promoted["flip_point"] = mean_flip
        promoted["source_ts_ms"] = raw.get("source_ts_ms") or now
        promoted["observation_promoted"] = True
        self.observation_queue = []
        return promoted

    def _open_or_extend_pending(self, raw, now, event, guarded=False,
                                watchdog=False):
        candidate_tol = self.config["gex_accept_candidate_frac"]
        raw_flip = safe_float(raw.get("flip_point"))
        eff_flip = (safe_float(self.effective.get("flip_point"))
                    if self.effective else raw_flip)
        band_est = self._estimate_band_half_for_acceptance(eff_flip)

        if self.pending is None:
            self.pending = {
                "flip_point": raw_flip,
                "count": 1,
                "first_seen_ms": now,
                "last_raw": raw,
                "guarded": bool(guarded),
                "watchdog": bool(watchdog),
            }
        else:
            drift = abs(raw_flip - self.pending["flip_point"]) / band_est
            if drift <= candidate_tol:
                self.pending["count"] += 1
                self.pending["last_raw"] = raw
                self.pending["guarded"] = (
                    self.pending.get("guarded") or bool(guarded))
                self.pending["watchdog"] = (
                    self.pending.get("watchdog") or bool(watchdog))
            else:
                if self._pending_force_accept_due(now):
                    self._accept_effective(
                        self.pending["last_raw"], now, "FORCE_ACCEPTED")
                    return
                self.pending = {
                    "flip_point": raw_flip,
                    "count": 1,
                    "first_seen_ms": now,
                    "last_raw": raw,
                    "guarded": bool(guarded),
                    "watchdog": bool(watchdog),
                }

        multiplier = 1.0
        if self.pending.get("guarded"):
            multiplier = self.config["gex_accept_guard_multiplier"]
        required_count = max(
            1, int(self.config["gex_accept_consensus_count"] * multiplier))
        force_ms = int(
            self.config["gex_accept_force_accept_sec"] * 1000 * multiplier)
        elapsed_ms = now - self.pending["first_seen_ms"]
        if self.pending["count"] >= required_count:
            self._accept_effective(
                self.pending["last_raw"], now, "CONSENSUS_CONFIRMED")
        elif force_ms > 0 and elapsed_ms >= force_ms:
            self._accept_effective(
                self.pending["last_raw"], now, "FORCE_ACCEPTED")
        else:
            self.last_acceptance_event = event

    def _pending_force_accept_due(self, now):
        if not self.pending:
            return False
        multiplier = 1.0
        if self.pending.get("guarded"):
            multiplier = self.config["gex_accept_guard_multiplier"]
        force_ms = int(
            self.config["gex_accept_force_accept_sec"] * 1000 * multiplier)
        return force_ms > 0 and now - self.pending["first_seen_ms"] >= force_ms

    def snapshot(self, quality=QUALITY_OK, reasons=None):
        source = self.effective
        if not source:
            return add_schema({
                "flip_point": None,
                "raw_flip_point": (
                    self.raw.get("flip_point") if self.raw else None),
                "spring": None,
                "source_ts_ms": None,
                "asset_price": None,
                "acceptance_event": self.last_acceptance_event,
                "pending": self.pending,
                "observation_count": len(self.observation_queue),
                "quality": quality if quality != QUALITY_OK else QUALITY_MISSING,
                "reasons": reasons or ["GEX_EFFECTIVE_MISSING"],
            }, SCHEMA_GEX_ANCHOR_SNAPSHOT, self.config)
        return add_schema({
            "flip_point": source.get("flip_point"),
            "raw_flip_point": (
                self.raw.get("flip_point") if self.raw else None),
            "spring": source.get("spring"),
            "source_ts_ms": source.get("source_ts_ms"),
            "asset_price": source.get("asset_price"),
            "acceptance_event": self.last_acceptance_event,
            "pending": self.pending,
            "observation_count": len(self.observation_queue),
            "quality": quality,
            "reasons": reasons or [],
        }, SCHEMA_GEX_ANCHOR_SNAPSHOT, self.config)

# ================================================================
# SOURCE: demo/bar_assembler.py
# ================================================================
"""Aggregate Binance trades into fixed-volume bars."""

import collections



class BarAssembler:
    def __init__(self, fetch_agg_trades, config=None, normalizer=None):
        self.fetch_agg_trades = fetch_agg_trades
        self.config = config or CONFIG
        self.normalize_trade = normalizer
        self.completed_bars = collections.deque(
            maxlen=int(self.config["bar_history_size"]))
        self.last_trade_id = None
        self.bar_index = 0
        self.current_open = None
        self.current_high = None
        self.current_low = None
        self.current_close = None
        self.current_volume = 0.0
        self.current_cvd = 0.0
        self.cvd_degraded = False
        self.last_cycle_metrics = {}

    def poll(self):
        from_id = None if self.last_trade_id is None else self.last_trade_id + 1
        result = self.fetch_agg_trades(from_id=from_id)
        if result.get("quality") != QUALITY_OK:
            self.last_cycle_metrics = {
                "quality": result.get("quality"),
                "error": result.get("error"),
                "trade_count": 0,
                "bar_count": 0,
            }
            return []

        raw_rows = result.get("data") or []
        had_gap = False
        new_bars = []
        for item in raw_rows:
            trade = self.normalize_trade(item) if self.normalize_trade else item
            if not trade:
                continue
            if (self.last_trade_id is not None
                    and trade["id"] > self.last_trade_id + 1
                    and not new_bars):
                had_gap = True
            new_bars.extend(self._ingest_trade(trade))

        if had_gap and self.config.get("cvd_gap_degrade_enabled", True):
            self.cvd_degraded = True
        elif not had_gap and self.cvd_degraded:
            self.cvd_degraded = False

        self.last_cycle_metrics = {
            "quality": QUALITY_OK,
            "error": None,
            "trade_count": len(raw_rows),
            "bar_count": len(new_bars),
            "cvd_degraded": self.cvd_degraded,
        }
        return new_bars

    def poll_with_drain(self):
        all_bars = []
        start = now_ms()
        rounds = 0
        hit_limit = False
        hit_wall_time = False
        max_rounds = int(self.config["max_drain_rounds"])
        max_wall = int(self.config["max_drain_wall_time_ms"])
        limit = int(self.config["agg_trades_limit"])

        while True:
            rounds += 1
            bars = self.poll()
            all_bars.extend(bars)
            trade_count = self.last_cycle_metrics.get("trade_count", 0)
            if not self.config.get("drain_enabled", True):
                break
            if trade_count == 0 or trade_count < limit:
                break
            hit_limit = True
            if rounds >= max_rounds:
                break
            if now_ms() - start >= max_wall:
                hit_wall_time = True
                break

        self.last_cycle_metrics.update({
            "catchup_rounds": rounds,
            "wall_time_ms": now_ms() - start,
            "hit_limit": hit_limit,
            "hit_wall_time": hit_wall_time,
            "backlogged": bool(hit_limit and hit_wall_time),
            "bar_count": len(all_bars),
        })
        return all_bars

    def _ingest_trade(self, trade):
        remaining = trade["qty"]
        threshold = float(self.config["volume_bar_n"])
        completed = []
        while remaining > 0:
            if self.current_open is None:
                self.current_open = trade["price"]
                self.current_high = trade["price"]
                self.current_low = trade["price"]
            space = threshold - self.current_volume
            if space <= 0:
                completed.extend(self._complete_current_bar())
                continue
            take_qty = min(remaining, space)
            fill_ratio = take_qty / trade["qty"]
            self.current_high = max(self.current_high, trade["price"])
            self.current_low = min(self.current_low, trade["price"])
            self.current_close = trade["price"]
            self.current_volume += take_qty
            self.current_cvd += trade["signed_qty"] * fill_ratio
            self.last_trade_id = trade["id"]
            remaining -= take_qty
            if self.current_volume >= threshold:
                completed.extend(self._complete_current_bar())
        return completed

    def _complete_current_bar(self):
        if self.current_open is None:
            return []
        self.bar_index += 1
        bar = add_schema({
            "bar_index": self.bar_index,
            "open": self.current_open,
            "high": self.current_high,
            "low": self.current_low,
            "close": self.current_close,
            "total_volume": self.current_volume,
            "cvd_delta": self.current_cvd,
            "complete_ts_ms": now_ms(),
        }, SCHEMA_VOLUME_BAR, self.config)
        self.completed_bars.append(bar)
        self.current_open = None
        self.current_high = None
        self.current_low = None
        self.current_close = None
        self.current_volume = 0.0
        self.current_cvd = 0.0
        return [bar]

    def slow_std_usd(self):
        window = int(self.config["slow_std_window"])
        if len(self.completed_bars) < window:
            return None
        closes = [bar["close"] for bar in list(self.completed_bars)[-window:]]
        return detrended_std(closes)

# ================================================================
# SOURCE: demo/factors.py
# ================================================================
"""Factor helpers used by module evaluators."""

import math



def compute_band_half(std_usd, price, spring, config=None):
    config = config or CONFIG
    price = safe_float(price)
    std_usd = safe_float(std_usd)
    spring = safe_float(spring) or 0.0
    if price is None or price <= 0:
        return None, False
    if std_usd is None or std_usd <= 0:
        std_usd = price * config["band_fallback_half_pct"] / max(
            config["band_base_sigma"], 1e-9)
        spring = 0.0

    capacity = abs(spring) * std_usd / max(config["volume_bar_n"], 1e-9)
    sigma_count = (config["band_base_sigma"]
                   + config["band_max_sigma_bonus"]
                   * math.tanh(capacity / config["band_spring_midpoint"]))
    band_half = std_usd * sigma_count
    min_band = price * config["band_half_min_pct"]
    max_band = price * config["band_half_max_pct"]
    if band_half < min_band:
        return min_band, False
    if band_half > max_band:
        return max_band, True
    return band_half, False


def compute_anchor_gravity(abs_nd_values, config=None):
    config = config or CONFIG
    values = [safe_float(v) for v in abs_nd_values or []]
    values = [v for v in values if v is not None]
    warmup = len(values) < int(config["anchor_gravity_warmup"])
    if warmup:
        return None, None, True

    trim_each_side = int(config["anchor_gravity_trim_each_side"])
    sorted_values = sorted(values)
    if trim_each_side > 0 and len(sorted_values) > trim_each_side * 2:
        sorted_values = sorted_values[trim_each_side:-trim_each_side]
    mean_abs = sum(sorted_values) / float(len(sorted_values))
    score = clamp(100.0 * math.exp(-1.0 * mean_abs), 0.0, 100.0)
    return mean_abs, score, False


def anchor_gravity_label(score):
    score = safe_float(score)
    if score is None:
        return "Warming"
    if score < 30.0:
        return "Detached"
    if score < 60.0:
        return "Loose"
    if score < 90.0:
        return "Attached"
    return "Tightly Attached"


def normalized_deviation(price, flip_point, band_half):
    price = safe_float(price)
    flip_point = safe_float(flip_point)
    band_half = safe_float(band_half)
    if price is None or flip_point is None or band_half is None or band_half <= 0:
        return None
    return (price - flip_point) / band_half


def recent_momentum(bars):
    if not bars or len(bars) < 2:
        return None
    first = safe_float(bars[0].get("close"))
    last = safe_float(bars[-1].get("close"))
    if first is None or last is None or first <= 0:
        return None
    return (last - first) / first


def cvd_sum(bars):
    if not bars:
        return None
    total = 0.0
    for bar in bars:
        value = safe_float(bar.get("cvd_delta"))
        if value is not None:
            total += value
    return total


def median(values):
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def quantile(values, q):
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return vals[low]
    weight = pos - low
    return vals[low] * (1.0 - weight) + vals[high] * weight


def sign(value, eps=1e-12):
    value = safe_float(value)
    if value is None or abs(value) <= eps:
        return 0
    return 1 if value > 0 else -1


def robust_iqr_normalize(value, history, max_abs_output=0.8,
                         conservative_multiplier=3.0, min_base=1e-9):
    value = safe_float(value) or 0.0
    hist = [safe_float(x) for x in history or []]
    hist = [x for x in hist if x is not None and math.isfinite(x)]
    if len(hist) < 5:
        return clamp(value / 50.0, -max_abs_output, max_abs_output)
    abs_hist = [abs(x) for x in hist]
    q75 = quantile(abs_hist, 0.75) or 0.0
    q25 = quantile(abs_hist, 0.25) or 0.0
    iqr = q75 - q25
    base = max(q75, iqr * 2.0, min_base)
    return clamp(value / (base * conservative_multiplier),
                 -max_abs_output, max_abs_output)


def robust_distribution_normalize(current, samples, max_abs_output=1.0,
                                  min_scale=1e-7):
    current = safe_float(current) or 0.0
    clean = [safe_float(x) for x in samples or []]
    clean = [x for x in clean if x is not None and math.isfinite(x)]
    if len(clean) < 8:
        scale = max(abs(current) * 3.0, min_scale)
        normalized = current / scale if scale > 0 else 0.0
        return clamp(normalized, -max_abs_output, max_abs_output), {
            "median": 0.0,
            "iqr": 0.0,
            "mad": 0.0,
            "scale": scale,
            "sample_count": float(len(clean)),
            "fallback": 1.0,
        }

    med = median(clean) or 0.0
    q75 = quantile(clean, 0.75) or med
    q25 = quantile(clean, 0.25) or med
    iqr = q75 - q25
    mad = median([abs(x - med) for x in clean]) or 0.0
    robust_scale = max(iqr / 1.349 if iqr > 0 else 0.0,
                       mad * 1.4826,
                       min_scale)
    normalized = (current - med) / (robust_scale * 2.0)
    return clamp(normalized, -max_abs_output, max_abs_output), {
        "median": med,
        "iqr": iqr,
        "mad": mad,
        "scale": robust_scale,
        "sample_count": float(len(clean)),
        "fallback": 0.0,
    }


def compute_tmvf_profile(klines, funding_points, config=None):
    config = config or CONFIG
    interval_hours = _tmvf_interval_hours(config)
    results = {}
    for label in ("24h", "48h"):
        cfg = _tmvf_window_config(label, config)
        core = compute_tmv_core(klines, cfg, config)
        funding = compute_funding_layer(
            funding_points,
            cfg["horizon_hours"],
            int(config["tmvf_funding_interval_hours"]),
            config,
        )
        if core.get("data_ready"):
            reflexivity = funding_adjustment(
                core.get("tmv_core"), funding.get("funding_norm", 0.0),
                config)
            final = reflexivity.get("direction_protected_final")
        else:
            reflexivity = {
                "adjustment": 0.0,
                "effect": "unavailable",
                "warning": "tmv_core_unavailable",
                "core_gate": 0.0,
                "direction_protected_final": None,
            }
            final = None
        results[label] = {
            "label": label,
            "data_ready": bool(core.get("data_ready")),
            "tmv_core": core.get("tmv_core"),
            "tmv_final": final,
            "final_state": classify_tmv_state(final, config),
            "funding_adjustment": reflexivity.get("adjustment"),
            "funding_effect": reflexivity.get("effect"),
            "funding_state": funding.get("funding_state"),
            "warning": reflexivity.get("warning"),
            "core": core,
            "funding": funding,
            "reflexivity": reflexivity,
        }
    combined = combine_tmvf_horizons(results, config)
    return {
        "results": results,
        "combined": combined,
        "interval_hours": interval_hours,
        "supported_interval_hours": _config_number_list(
            config.get("tmvf_supported_interval_hours", [1, 2, 4])),
        "kline_count": len(klines or []),
        "funding_count": len(funding_points or []),
    }


def compute_tmv_core(klines, cfg, config=None):
    config = config or CONFIG
    klines = list(klines or [])
    need = (
        max(cfg["ema_slow"],
            cfg["macd_slow"] + cfg["macd_signal"],
            cfg["volume_window"],
            cfg["avg_volume_window"])
        + cfg["tmv_window"])
    if len(klines) < need:
        return {
            "label": cfg["label"],
            "horizon_hours": cfg["horizon_hours"],
            "data_ready": False,
            "kline_count": len(klines),
            "required_klines": need,
        }

    recent = klines[-max(need, cfg["tmv_window"] + 80):]
    closes = [safe_float(k.get("close")) for k in recent]
    closes = [c for c in closes if c is not None and c > 0]
    if len(closes) < need:
        return {
            "label": cfg["label"],
            "horizon_hours": cfg["horizon_hours"],
            "data_ready": False,
            "kline_count": len(closes),
            "required_klines": need,
        }

    ema_fast = ema_series(closes, cfg["ema_fast"])[-1]
    ema_slow = ema_series(closes, cfg["ema_slow"])[-1]
    last_price = closes[-1]
    ema_diff = ema_fast - ema_slow
    min_trend_threshold = last_price * config["tmvf_min_trend_pct"]
    if abs(ema_diff) < min_trend_threshold:
        trend_direction = 0
    else:
        trend_direction = 1 if ema_diff > 0 else -1
    trend_strength_pct = (
        abs(ema_diff) / last_price * 100.0 if last_price > 0 else 0.0)

    macd_hist = macd_hist_series(
        closes, cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"])
    latest_macd_hist = macd_hist[-1] if macd_hist else None
    if latest_macd_hist is None:
        return {
            "label": cfg["label"],
            "horizon_hours": cfg["horizon_hours"],
            "data_ready": False,
            "kline_count": len(closes),
            "required_klines": need,
        }
    momentum_history = [
        h * config["tmvf_momentum_multiplier"]
        for h in macd_hist[-cfg["tmv_window"]:]
        if h is not None and math.isfinite(h)
    ]
    latest_momentum_score = (
        latest_macd_hist * config["tmvf_momentum_multiplier"])
    momentum_norm = robust_iqr_normalize(
        latest_momentum_score,
        momentum_history,
        max_abs_output=config["tmvf_component_max_abs"],
    )

    volume_history = []
    start_index = max(0, len(recent) - cfg["tmv_window"])
    for idx in range(start_index, len(recent)):
        volume_history.append(volume_score_at(
            recent, idx, cfg["volume_window"], cfg["avg_volume_window"]))
    latest_volume_score = volume_score_at(
        recent, len(recent) - 1,
        cfg["volume_window"], cfg["avg_volume_window"])
    volume_norm = robust_iqr_normalize(
        latest_volume_score,
        volume_history,
        max_abs_output=config["tmvf_component_max_abs"],
    )

    tmv_raw = (
        cfg["trend_weight"] * trend_direction
        + cfg["momentum_weight"] * momentum_norm
        + cfg["volume_weight"] * volume_norm)
    tmv_core = clamp(tmv_raw, -1.0, 1.0)
    return {
        "label": cfg["label"],
        "horizon_hours": cfg["horizon_hours"],
        "data_ready": True,
        "tmv_core": tmv_core,
        "tmv_core_raw": tmv_raw,
        "state": classify_tmv_state(tmv_core, config),
        "trend_direction": trend_direction,
        "trend_strength_pct": trend_strength_pct,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_diff": ema_diff,
        "min_trend_threshold": min_trend_threshold,
        "macd_hist": latest_macd_hist,
        "momentum_score": latest_momentum_score,
        "momentum_normalized": momentum_norm,
        "volume_score": latest_volume_score,
        "volume_normalized": volume_norm,
        "price": last_price,
        "kline_open_time": recent[-1].get("open_time"),
        "component_weights": {
            "trend": cfg["trend_weight"],
            "momentum": cfg["momentum_weight"],
            "volume": cfg["volume_weight"],
        },
    }


def compute_funding_layer(funding_points, horizon_hours,
                          funding_interval_hours=8, config=None):
    config = config or CONFIG
    if not funding_points:
        return {
            "horizon_hours": horizon_hours,
            "funding_cum": 0.0,
            "funding_count": 0,
            "funding_norm": 0.0,
            "funding_state": "unavailable",
            "normalization": {},
            "funding_interval_hours": funding_interval_hours,
            "data_ready": False,
        }
    funding_cum, count, start_time, end_time = current_funding_sum(
        funding_points, horizon_hours)
    samples = rolling_funding_sums(funding_points, horizon_hours)
    norm, stats = robust_distribution_normalize(
        funding_cum, samples, max_abs_output=1.0)
    required = max(
        2, int(math.floor(horizon_hours / max(1, funding_interval_hours))) - 1)
    return {
        "horizon_hours": horizon_hours,
        "funding_cum": funding_cum,
        "funding_count": count,
        "funding_norm": norm,
        "funding_state": classify_funding_state(norm, config),
        "normalization": stats,
        "funding_interval_hours": funding_interval_hours,
        "window_start_time": start_time,
        "window_end_time": end_time,
        "data_ready": count >= required,
    }


def funding_adjustment(core, funding_norm, config=None):
    config = config or CONFIG
    core = safe_float(core) or 0.0
    funding_norm = safe_float(funding_norm) or 0.0
    d = sign(core)
    s = sign(funding_norm)
    a = abs(core)
    c = abs(funding_norm)
    neutral = config["tmvf_core_neutral_abs"]
    directional = config["tmvf_core_directional_abs"]
    crowded = config["tmvf_funding_crowded_abs"]
    core_gate = clamp((a - neutral) / max(directional - neutral, 1e-9),
                      0.0, 1.0)
    adjustment = 0.0
    effect = "neutral"
    warning = None
    if a < neutral or d == 0:
        if c >= crowded:
            effect = "crowded_without_price_confirmation"
            warning = "funding_extreme_but_price_behavior_unconfirmed"
        return {
            "adjustment": 0.0,
            "effect": effect,
            "warning": warning,
            "core_gate": core_gate,
            "direction_protected_final": core,
        }

    if s == 0 or c < config["tmvf_funding_confirm_abs"]:
        effect = "neutral"
    elif s == d and c <= crowded:
        adjustment = d * core_gate * min(0.08, 0.12 * c)
        effect = "confirming"
    elif s == d and c > crowded:
        healthy_boost = d * core_gate * 0.06
        crowding_penalty = (
            d * core_gate * 0.20 * ((c - crowded) / max(1.0 - crowded, 1e-9)))
        adjustment = healthy_boost - crowding_penalty
        effect = "extreme_overcrowded"
        if c < config["tmvf_funding_extreme_abs"]:
            effect = "overcrowded"
    elif s == -d:
        adjustment = d * core_gate * min(0.12, 0.10 * c)
        effect = "opposite_crowding_fuel"

    cap = config["tmvf_funding_adjustment_cap"]
    adjustment = clamp(adjustment, -cap, cap)
    final = clamp(core + adjustment, -1.0, 1.0)
    if sign(final) != 0 and sign(core) != 0 and sign(final) != sign(core):
        final = 0.0
        warning = warning or "funding_adjustment_blocked_by_direction_protection"
    return {
        "adjustment": adjustment,
        "effect": effect,
        "warning": warning,
        "core_gate": core_gate,
        "direction_protected_final": final,
    }


def combine_tmvf_horizons(results, config=None):
    config = config or CONFIG
    ready = {
        label: item for label, item in (results or {}).items()
        if item.get("tmv_final") is not None
    }
    if not ready:
        return {
            "data_ready": False,
            "tmv_blend": None,
            "direction_code": 0,
            "state": "unavailable",
            "window_conflict": False,
        }

    score_24 = ready.get("24h", {}).get("tmv_final")
    score_48 = ready.get("48h", {}).get("tmv_final")
    if score_24 is not None and score_48 is not None:
        sign_24 = sign(score_24, config["tmvf_core_neutral_abs"])
        sign_48 = sign(score_48, config["tmvf_core_neutral_abs"])
        conflict = sign_24 != 0 and sign_48 != 0 and sign_24 != sign_48
        weight_24 = float(config.get("tmvf_blend_24h_weight", 0.4))
        weight_48 = float(config.get("tmvf_blend_48h_weight", 0.6))
        weight_sum = max(weight_24 + weight_48, 1e-9)
        blend = (weight_24 * score_24 + weight_48 * score_48) / weight_sum
    elif score_48 is not None:
        conflict = False
        blend = score_48
    else:
        conflict = False
        blend = score_24
    return {
        "data_ready": True,
        "tmv_blend": blend,
        "direction_code": sign(blend, config["tmvf_core_neutral_abs"]),
        "state": classify_tmv_state(blend, config),
        "window_conflict": conflict,
    }


def compute_micro_flow_context(bars, config=None):
    config = config or CONFIG
    horizons = _config_number_list(config.get(
        "tmvf_micro_horizons_hours", [4, 8, 12]))
    results = {}
    for horizon in horizons:
        label = str(int(horizon)) + "h"
        results[label] = micro_flow_for_horizon(bars, horizon, config)
        if label == "4h":
            results[label]["window_role"] = "fast"
        elif label == "12h":
            results[label]["window_role"] = "slow"
        else:
            results[label]["window_role"] = "middle"
    fast = results.get("4h")
    slow = results.get("12h")
    return {
        "horizons": results,
        "fast_4h": fast,
        "slow_12h": slow,
        "combined": combine_micro_flow_horizons(results, config),
    }


def micro_flow_for_horizon(bars, horizon_hours, config=None):
    config = config or CONFIG
    bars = list(bars or [])
    if not bars:
        return {
            "horizon_hours": horizon_hours,
            "data_ready": False,
            "bar_count": 0,
            "state": "unavailable",
        }
    end_ts = bars[-1].get("complete_ts_ms")
    if end_ts is None:
        return {
            "horizon_hours": horizon_hours,
            "data_ready": False,
            "bar_count": len(bars),
            "reason": "MISSING_BAR_TIMESTAMPS",
            "state": "unavailable",
        }
    lower = end_ts - horizon_hours * 60 * 60 * 1000
    selected = [
        bar for bar in bars
        if bar.get("complete_ts_ms") is not None
        and bar.get("complete_ts_ms") > lower
    ]
    if not selected:
        return {
            "horizon_hours": horizon_hours,
            "data_ready": False,
            "bar_count": 0,
            "state": "unavailable",
        }
    first_ts = selected[0].get("complete_ts_ms")
    coverage_hours = (end_ts - first_ts) / float(60 * 60 * 1000)
    coverage = coverage_hours / max(float(horizon_hours), 1e-9)
    momentum = recent_momentum(selected)
    cvd = cvd_sum(selected)
    cvd_per_bar = None
    if cvd is not None and selected:
        cvd_per_bar = cvd / float(len(selected))
    momentum_norm = clamp(
        (momentum or 0.0) / max(config["tmvf_micro_momentum_norm"], 1e-9),
        -1.0,
        1.0,
    )
    cvd_norm = 0.0
    if cvd is not None and selected:
        cvd_norm = clamp(
            cvd / max(len(selected) * config["volume_bar_n"], 1e-9),
            -1.0,
            1.0,
        )
    score = clamp(
        config["tmvf_micro_momentum_weight"] * momentum_norm
        + config["tmvf_micro_cvd_weight"] * cvd_norm,
        -1.0,
        1.0,
    )
    data_ready = (
        len(selected) >= int(config["tmvf_micro_min_bars"])
        and coverage_hours >= config["tmvf_micro_min_coverage_hours"]
        and coverage >= config["tmvf_micro_ready_coverage_frac"])
    direction = micro_flow_direction(score, config)
    state = micro_flow_state(score, config)
    confidence = min(1.0, coverage) * min(
        1.0, len(selected) / max(float(config["tmvf_micro_min_bars"]), 1.0))
    return {
        "horizon_hours": horizon_hours,
        "data_ready": data_ready,
        "bar_count": len(selected),
        "coverage_hours": coverage_hours,
        "coverage_frac": coverage,
        "momentum": momentum,
        "momentum_return_pct": (
            momentum * 100.0 if momentum is not None else None),
        "momentum_norm": momentum_norm,
        "cvd_sum": cvd,
        "cvd_unit": "BTC",
        "cvd_per_bar": cvd_per_bar,
        "cvd_norm": cvd_norm,
        "score": score,
        "state": state,
        "confidence": confidence,
        "direction": direction,
    }


def combine_micro_flow_horizons(results, config=None):
    config = config or CONFIG
    items = []
    for item in (results or {}).values():
        if not isinstance(item, dict):
            continue
        score = safe_float(item.get("score"))
        confidence = safe_float(item.get("confidence")) or 0.0
        if score is None or confidence <= 0:
            continue
        horizon = safe_float(item.get("horizon_hours")) or 0.0
        ready_boost = 1.0 if item.get("data_ready") else 0.35
        weight = max(horizon, 1.0) * confidence * ready_boost
        items.append((score, weight, item))
    if not items:
        return {
            "data_ready": False,
            "score": None,
            "state": "unavailable",
            "direction": "neutral",
            "ready_horizons": [],
            "max_coverage_hours": 0.0,
        }
    total_weight = sum(weight for _score, weight, _item in items)
    score = sum(score * weight for score, weight, _item in items) / max(
        total_weight, 1e-9)
    ready_horizons = [
        str(int(item.get("horizon_hours"))) + "h"
        for _score, _weight, item in items
        if item.get("data_ready")
    ]
    max_coverage = max(
        safe_float(item.get("coverage_hours")) or 0.0
        for _score, _weight, item in items)
    data_ready = bool(ready_horizons)
    return {
        "data_ready": data_ready,
        "score": score,
        "state": micro_flow_state(score, config),
        "direction": micro_flow_direction(score, config),
        "ready_horizons": ready_horizons,
        "max_coverage_hours": max_coverage,
    }


def micro_flow_direction(score, config=None):
    config = config or CONFIG
    score = safe_float(score)
    if score is None:
        return "neutral"
    neutral = config["tmvf_micro_neutral_abs"]
    directional = config["tmvf_micro_directional_abs"]
    if score >= directional:
        return "bullish"
    if score >= neutral:
        return "neutral_to_bullish"
    if score <= -directional:
        return "bearish"
    if score <= -neutral:
        return "neutral_to_bearish"
    return "neutral"


def micro_flow_state(score, config=None):
    direction = micro_flow_direction(score, config)
    if direction == "bullish":
        return "micro_bullish_pressure"
    if direction == "neutral_to_bullish":
        return "micro_bullish_lean"
    if direction == "bearish":
        return "micro_bearish_pressure"
    if direction == "neutral_to_bearish":
        return "micro_bearish_lean"
    return "micro_neutral"


def compute_m_die(klines, config=None):
    config = config or CONFIG
    n_bars = int(config.get("m_die_window_bars", 15))
    clean = _clean_m_die_klines(klines)
    required = n_bars + 1
    if len(clean) < required:
        return _m_die_no_value(
            "insufficient_bars", len(clean), required, config)

    window = clean[-n_bars:]
    prev_close = safe_float(clean[-required].get("close"))
    closes = [prev_close] + [safe_float(item.get("close")) for item in window]
    if any(value is None or value <= 0 for value in closes):
        return _m_die_no_value(
            "invalid_close", len(clean), required, config)

    returns = [
        math.log(closes[index] / closes[index - 1])
        for index in range(1, len(closes))
    ]
    total_return = sum(returns)
    return_floor = float(config.get("m_die_return_floor", 0.0006))
    if total_return > return_floor:
        direction = 1
    elif total_return < -return_floor:
        direction = -1
    else:
        return _m_die_zero(
            "direction_not_clear", clean, total_return, config)

    eps = float(config.get("m_die_eps", 1e-8))
    realized_vol = _stddev(returns) * math.sqrt(float(n_bars))
    displacement_z = abs(total_return) / max(realized_vol, eps)
    d_z_score = _linear_score(
        displacement_z,
        config.get("m_die_z_start", 0.6),
        config.get("m_die_z_full", 1.8),
    )
    abs_return_score = _linear_score(
        abs(total_return),
        config.get("m_die_r_start", 0.0006),
        config.get("m_die_r_full", 0.0025),
    )
    d_final = math.sqrt(d_z_score * abs_return_score)

    path_length = sum(abs(item) for item in returns)
    path_efficiency = abs(total_return) / max(path_length, eps)
    e_score = _linear_score(
        path_efficiency,
        config.get("m_die_e_start", 0.35),
        config.get("m_die_e_full", 0.85),
    )

    micro_floor = float(config.get("m_die_micro_return_floor", 0.00005))
    valid_returns = [item for item in returns if abs(item) > micro_floor]
    valid_count = len(valid_returns)
    coverage_ratio = valid_count / float(n_bars)
    if valid_count:
        same_count = sum(1 for item in valid_returns if sign(item) == direction)
        same_direction_ratio = same_count / float(valid_count)
    else:
        same_direction_ratio = 0.5
    p_raw = same_direction_ratio * math.sqrt(coverage_ratio)
    p_final = _linear_score(
        p_raw,
        config.get("m_die_p_start", 0.45),
        config.get("m_die_p_full", 0.70),
    )

    score = clamp(0.40 * d_final + 0.40 * e_score + 0.20 * p_final,
                  0.0, 1.0)
    m_die = direction * score
    level = _m_die_level(abs(m_die))
    move_shape = _m_die_move_shape(score, coverage_ratio, e_score)
    last_time = window[-1].get("close_time") or window[-1].get("open_time")
    result = {
        "factor_name": "M-DIE",
        "factor_version": "v1.1_final",
        "interval": config.get("m_die_interval", "1m"),
        "window": "15m",
        "n_bars": n_bars,
        "rolling": True,
        "last_closed_bar_time": last_time,
        "direction": "UP" if direction > 0 else "DOWN",
        "m_die": m_die,
        "score": score,
        "level": level,
        "move_shape": move_shape,
        "label_cn": _m_die_label_cn(direction, level, move_shape),
        "components": {
            "displacement": {
                "score": d_final,
                "raw": {
                    "window_log_return": total_return,
                    "window_return_pct": math.exp(total_return) - 1.0,
                    "realized_vol": realized_vol,
                    "displacement_z": displacement_z,
                    "d_z_score": d_z_score,
                    "abs_return_score": abs_return_score,
                    "d_final": d_final,
                },
            },
            "path_efficiency": {
                "score": e_score,
                "raw": {"efficiency": path_efficiency},
            },
            "directional_persistence": {
                "score": p_final,
                "raw": {
                    "same_direction_ratio": same_direction_ratio,
                    "valid_return_count": valid_count,
                    "coverage_ratio": coverage_ratio,
                    "p_raw": p_raw,
                },
            },
        },
        "data_status": {
            "source": "api_backfill_or_live_polling",
            "bars_loaded": len(clean),
            "bars_required": required,
            "uses_closed_bars_only": True,
            "data_state": "OK",
        },
        "interpretation_cn": _m_die_interpretation_cn(
            direction, level, move_shape),
    }
    return add_schema(result, SCHEMA_MDIE, config)


def _clean_m_die_klines(klines):
    by_time = {}
    for item in klines or []:
        if not isinstance(item, dict):
            continue
        open_time = item.get("open_time")
        close_time = item.get("close_time")
        close = safe_float(item.get("close"))
        high = safe_float(item.get("high"))
        low = safe_float(item.get("low"))
        if (open_time is None or close_time is None or close is None
                or close <= 0 or high is None or low is None or high < low):
            continue
        if close_time > now_ms():
            continue
        by_time[int(open_time)] = dict(item)
    return [by_time[key] for key in sorted(by_time.keys())]


def _m_die_no_value(reason, bars_loaded, bars_required, config):
    result = {
        "factor_name": "M-DIE",
        "factor_version": "v1.1_final",
        "interval": config.get("m_die_interval", "1m"),
        "window": "15m",
        "n_bars": int(config.get("m_die_window_bars", 15)),
        "rolling": True,
        "last_closed_bar_time": None,
        "direction": "NO_DIRECTION",
        "m_die": 0.0,
        "score": 0.0,
        "level": "NO_DIRECTIONAL_MOVE",
        "move_shape": "NO_MOVE",
        "label_cn": "无明显单向变化｜无明显变化",
        "components": {},
        "data_status": {
            "source": "api_backfill_or_live_polling",
            "bars_loaded": bars_loaded,
            "bars_required": bars_required,
            "uses_closed_bars_only": True,
            "data_state": reason,
        },
        "interpretation_cn": "M-DIE数据不足或不可用。",
    }
    return add_schema(result, SCHEMA_MDIE, config)


def _m_die_zero(reason, clean, total_return, config):
    required = int(config.get("m_die_window_bars", 15)) + 1
    last = clean[-1] if clean else {}
    result = _m_die_no_value(reason, len(clean), required, config)
    result["last_closed_bar_time"] = last.get("close_time") or last.get(
        "open_time")
    result["data_status"]["data_state"] = "OK"
    result["components"] = {
        "displacement": {
            "score": 0.0,
            "raw": {
                "window_log_return": total_return,
                "window_return_pct": math.exp(total_return) - 1.0,
            },
        },
    }
    result["interpretation_cn"] = "最近15分钟未形成明确单向变化。"
    return result


def _linear_score(value, start, full):
    value = safe_float(value) or 0.0
    start = safe_float(start) or 0.0
    full = safe_float(full) or start
    return clamp((value - start) / max(full - start, 1e-12), 0.0, 1.0)


def _stddev(values):
    vals = [safe_float(item) for item in values or []]
    vals = [item for item in vals if item is not None and math.isfinite(item)]
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / float(len(vals))
    variance = sum((item - mean) ** 2 for item in vals) / float(len(vals))
    return math.sqrt(max(0.0, variance))


def _m_die_level(abs_value):
    if abs_value < 0.25:
        return "NO_DIRECTIONAL_MOVE"
    if abs_value < 0.45:
        return "MILD_DIRECTIONAL_MOVE"
    if abs_value < 0.65:
        return "CLEAR_DIRECTIONAL_MOVE"
    return "STRONG_DIRECTIONAL_MOVE"


def _m_die_move_shape(score, coverage_ratio, e_score):
    if score < 0.25:
        return "NO_MOVE"
    if coverage_ratio < 0.35 and e_score > 0.75:
        return "IMPULSE_SHIFT"
    if coverage_ratio >= 0.50 and e_score >= 0.55:
        return "DRIFT_RUN"
    return "CHOPPY_DRIFT"


def _m_die_label_cn(direction, level, move_shape):
    direction_text = "上行" if direction > 0 else "下行" if direction < 0 else ""
    level_map = {
        "NO_DIRECTIONAL_MOVE": "无明显单向变化",
        "MILD_DIRECTIONAL_MOVE": "轻度" + direction_text + "单向变化",
        "CLEAR_DIRECTIONAL_MOVE": "明显" + direction_text + "单向变化",
        "STRONG_DIRECTIONAL_MOVE": "强" + direction_text + "单向变化",
    }
    shape_map = {
        "NO_MOVE": "无明显变化",
        "DRIFT_RUN": "连续推进型",
        "IMPULSE_SHIFT": "冲击位移型",
        "CHOPPY_DRIFT": "震荡漂移型",
    }
    return level_map.get(level, level) + "｜" + shape_map.get(
        move_shape, move_shape)


def _m_die_interpretation_cn(direction, level, move_shape):
    if direction == 0 or level == "NO_DIRECTIONAL_MOVE":
        return "最近15分钟未形成明显单向变化。"
    side = "上行" if direction > 0 else "下行"
    return "最近15分钟价格出现{0}单向变化，形态为{1}。".format(
        side, _m_die_label_cn(direction, level, move_shape).split("｜")[-1])


def ema_series(values, period):
    vals = [safe_float(v) for v in values or []]
    vals = [v for v in vals if v is not None and math.isfinite(v)]
    if not vals:
        return []
    alpha = 2.0 / (float(period) + 1.0)
    out = []
    ema = vals[0]
    for value in vals:
        ema = alpha * value + (1.0 - alpha) * ema
        out.append(ema)
    return out


def macd_hist_series(values, fast, slow, signal_period):
    vals = [safe_float(v) for v in values or []]
    vals = [v for v in vals if v is not None and math.isfinite(v)]
    if not vals:
        return []
    fast_ema = ema_series(vals, fast)
    slow_ema = ema_series(vals, slow)
    macd = [fast_ema[i] - slow_ema[i] for i in range(len(vals))]
    signal_line = ema_series(macd, signal_period)
    return [macd[i] - signal_line[i] for i in range(len(macd))]


def volume_score_at(klines, end_index, volume_window, avg_volume_window=24):
    if end_index <= 0 or end_index >= len(klines):
        return 0.0
    start = max(0, end_index - volume_window + 1)
    window = klines[start:end_index + 1]
    if len(window) < 2:
        return 0.0
    avg_start = max(0, end_index - avg_volume_window + 1)
    avg_slice = klines[avg_start:end_index + 1]
    volumes = [safe_float(k.get("volume")) for k in avg_slice]
    volumes = [v for v in volumes if v is not None]
    avg_volume = sum(volumes) / max(1, len(volumes))
    if avg_volume <= 0:
        avg_volume = 1.0
    bull = 0.0
    bear = 0.0
    for i in range(1, len(window)):
        prev = window[i - 1]
        cur = window[i]
        prev_close = safe_float(prev.get("close"))
        cur_close = safe_float(cur.get("close"))
        cur_volume = safe_float(cur.get("volume")) or 0.0
        if prev_close is None or cur_close is None or prev_close <= 0:
            continue
        change = cur_close - prev_close
        change_rate = change / prev_close
        volume_weight = cur_volume / avg_volume if avg_volume > 0 else 1.0
        vwpr = change_rate * volume_weight
        if change > 0:
            bull += vwpr
        elif change < 0:
            bear += abs(vwpr)
    return bull - bear


def rolling_funding_sums(points, window_hours):
    pts = _sorted_funding_points(points)
    if not pts:
        return []
    window_ms = window_hours * 60 * 60 * 1000
    sums = []
    left = 0
    running = 0.0
    for right, point in enumerate(pts):
        running += point["funding_rate"]
        lower = point["funding_time"] - window_ms
        while left <= right and pts[left]["funding_time"] <= lower:
            running -= pts[left]["funding_rate"]
            left += 1
        if right - left + 1 >= 2:
            sums.append(running)
    return sums


def current_funding_sum(points, window_hours):
    pts = _sorted_funding_points(points)
    if not pts:
        return 0.0, 0, None, None
    end_time = pts[-1]["funding_time"]
    lower = end_time - window_hours * 60 * 60 * 1000
    selected = [
        p for p in pts
        if lower < p["funding_time"] <= end_time
    ]
    return (
        sum(p["funding_rate"] for p in selected),
        len(selected),
        selected[0]["funding_time"] if selected else None,
        end_time,
    )


def classify_funding_state(norm, config=None):
    config = config or CONFIG
    norm = safe_float(norm) or 0.0
    a = abs(norm)
    side = "long" if norm > 0 else "short" if norm < 0 else "neutral"
    if a < config["tmvf_funding_confirm_abs"]:
        return "neutral"
    if a < config["tmvf_funding_crowded_abs"]:
        return "healthy_" + side + "_bias"
    if a < config["tmvf_funding_extreme_abs"]:
        return "crowded_" + side
    return "extreme_crowded_" + side


def classify_tmv_state(value, config=None):
    config = config or CONFIG
    value = safe_float(value)
    if value is None:
        return "unavailable"
    if value >= config["tmvf_core_strong_abs"]:
        return "strong_bullish_bias"
    if value >= config["tmvf_core_directional_abs"]:
        return "bullish_bias"
    if value >= config["tmvf_core_neutral_abs"]:
        return "mild_bullish_bias"
    if value > -config["tmvf_core_neutral_abs"]:
        return "neutral"
    if value > -config["tmvf_core_directional_abs"]:
        return "mild_bearish_bias"
    if value > -config["tmvf_core_strong_abs"]:
        return "bearish_bias"
    return "strong_bearish_bias"


def _tmvf_window_config(label, config):
    prefix = "tmvf_" + label.replace("h", "h_")
    horizon = int(label.replace("h", ""))
    interval_hours = _tmvf_interval_hours(config)
    return {
        "label": label,
        "horizon_hours": horizon,
        "interval_hours": interval_hours,
        "tmv_window": _scale_hours_to_bars(horizon, interval_hours),
        "ema_fast": _scale_hours_to_bars(
            config[prefix + "ema_fast"], interval_hours),
        "ema_slow": _scale_hours_to_bars(
            config[prefix + "ema_slow"], interval_hours),
        "macd_fast": _scale_hours_to_bars(
            config[prefix + "macd_fast"], interval_hours),
        "macd_slow": _scale_hours_to_bars(
            config[prefix + "macd_slow"], interval_hours),
        "macd_signal": _scale_hours_to_bars(
            config[prefix + "macd_signal"], interval_hours),
        "volume_window": _scale_hours_to_bars(
            config[prefix + "volume_window"], interval_hours),
        "avg_volume_window": _scale_hours_to_bars(
            config["tmvf_avg_volume_window"], interval_hours),
        "trend_weight": float(config[prefix + "trend_weight"]),
        "momentum_weight": float(config[prefix + "momentum_weight"]),
        "volume_weight": float(config[prefix + "volume_weight"]),
    }


def _tmvf_interval_hours(config):
    value = safe_float(config.get("tmvf_kline_interval_hours"))
    if value is not None and value > 0:
        return value
    interval = str(config.get("tmvf_kline_interval", "1h")).strip().lower()
    if interval.endswith("h"):
        parsed = safe_float(interval[:-1])
        if parsed is not None and parsed > 0:
            return parsed
    return 1.0


def _scale_hours_to_bars(hours, interval_hours):
    value = safe_float(hours)
    if value is None or value <= 0:
        return 1
    return max(1, int(math.ceil(value / max(interval_hours, 1e-9))))


def _config_number_list(value):
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = [value]
    numbers = []
    for item in parts:
        parsed = safe_float(item)
        if parsed is not None and parsed > 0:
            numbers.append(parsed)
    return numbers


def _sorted_funding_points(points):
    out = []
    for item in points or []:
        if not isinstance(item, dict):
            continue
        funding_time = item.get("funding_time")
        if funding_time is None:
            funding_time = item.get("fundingTime")
        funding_rate = item.get("funding_rate")
        if funding_rate is None:
            funding_rate = item.get("fundingRate")
        funding_time = safe_float(funding_time)
        funding_rate = safe_float(funding_rate)
        if funding_time is None or funding_rate is None:
            continue
        out.append({
            "funding_time": int(funding_time),
            "funding_rate": funding_rate,
        })
    out.sort(key=lambda x: x["funding_time"])
    return out


def build_factor_snapshot(module_results, strategy_recommendation=None,
                          macro_pressure=None, m_die=None,
                          neutral_repair_signal=None, config=None):
    """Flatten module facts into a stable per-evaluation factor snapshot."""
    config = config or CONFIG
    by_module = {item.get("module"): item for item in module_results or []}
    anchor = by_module.get(MODULE_ANCHOR, {})
    tmvf = by_module.get(MODULE_TMVF, {})

    snapshot = {
        "anchor": _copy_facts(anchor, [
            "ready",
            "bar_index",
            "bar_close",
            "effective_flip_point",
            "raw_flip_point",
            "gex_source_ts_ms",
            "gex_freshness",
            "freshness",
            "flip_point",
            "spring",
            "std_usd",
            "band_half",
            "normalized_deviation",
            "band_clamped",
            "acceptance_event",
            "gex_acceptance_event",
            "anchor_nd_mean_abs",
            "anchor_gravity_ref_score",
            "anchor_gravity_ref_label",
            "anchor_gravity_warming",
            "anchor_gravity_window_count",
        ]),
        "flow": _copy_facts(tmvf, [
            "momentum",
            "cvd_sum",
            "normalized_deviation",
            "direction",
            "market_state",
            "tmvf_architecture",
            "tmvf_24h",
            "tmvf_48h",
            "tmvf_interval_hours",
            "tmvf_supported_interval_hours",
            "tmv_blend",
            "tmv_state",
            "window_conflict",
            "tmvf_funding_effect",
            "micro_flow_effect",
            "micro_flow",
            "kline_count",
            "funding_count",
            "last_funding_rate",
            "mark_price",
            "index_price",
        ]),
        "strategy_recommendation": _strategy_factors(
            strategy_recommendation),
        "macro_pressure": macro_pressure,
        "m_die": m_die,
        "neutral_repair_signal": neutral_repair_signal,
        "skew": None,
        "gamma_regime": None,
        "edb": None,
    }
    return add_schema(snapshot, SCHEMA_FACTOR_SNAPSHOT, config)


def _copy_facts(module_result, keys):
    facts = module_result.get("facts") if isinstance(module_result, dict) else {}
    if not isinstance(facts, dict):
        facts = {}
    return {key: facts.get(key) for key in keys}


def _strategy_factors(strategy_recommendation):
    if not strategy_recommendation:
        return None
    return {
        "signal": strategy_recommendation.get("signal"),
        "expiry_24h": strategy_recommendation.get("expiry_24h"),
        "expiry_48h": strategy_recommendation.get("expiry_48h"),
        "strategy_code": strategy_recommendation.get("strategy_code"),
        "strategy_type": strategy_recommendation.get("strategy_type"),
        "summary": strategy_recommendation.get("summary"),
        "selection_reason": strategy_recommendation.get("selection_reason"),
        "order_layer": strategy_recommendation.get("order_layer"),
    }

# ================================================================
# SOURCE: demo/modules.py
# ================================================================
"""Module evaluators for the demo decision chain."""



def module_result(module, state, facts=None, reasons=None, quality=QUALITY_OK,
                  score=None):
    return add_schema({
        "module": module,
        "state": state,
        "score": score,
        "facts": facts or {},
        "reasons": reasons or [],
        "quality": quality,
    }, SCHEMA_MODULE_RESULT)


def evaluate_external_gate(config=None, source_quality=None):
    config = config or CONFIG
    source_quality = source_quality or {}
    reasons = []
    state = STATE_CLEAR
    if config.get("read_only_demo", True):
        state = STATE_CAUTION
        reasons.append(REASON_READ_ONLY_DEMO)
    if source_quality:
        bad_count = sum(1 for v in source_quality.values()
                        if v in (
                            QUALITY_ERROR,
                            QUALITY_MISSING,
                            QUALITY_INVALID,
                        ))
        if bad_count == len(source_quality):
            return module_result(MODULE_EXTERNAL_GATE, STATE_BLOCKED,
                                 facts={"source_quality": source_quality},
                                 reasons=["ALL_SOURCES_UNAVAILABLE"],
                                 quality=QUALITY_MISSING)
    return module_result(MODULE_EXTERNAL_GATE, state,
                         facts={"source_quality": source_quality},
                         reasons=reasons)


def evaluate_anchor(gex_snapshot, current_price, std_usd, config=None,
                    latest_bar=None, nd_window=None, update_window=True):
    config = config or CONFIG
    reasons = []
    bar_index = latest_bar.get("bar_index") if isinstance(
        latest_bar, dict) else None
    bar_close = safe_float(latest_bar.get("close")) if isinstance(
        latest_bar, dict) else None
    price_ref = bar_close if bar_close is not None else safe_float(
        current_price)
    effective_flip = gex_snapshot.get("flip_point") if gex_snapshot else None
    raw_flip = gex_snapshot.get("raw_flip_point") if gex_snapshot else None
    source_ts = gex_snapshot.get("source_ts_ms") if gex_snapshot else None
    spring = gex_snapshot.get("spring") if gex_snapshot else None

    facts = {
        "ready": False,
        "bar_index": bar_index,
        "bar_close": bar_close,
        "effective_flip_point": effective_flip,
        "raw_flip_point": raw_flip,
        "gex_source_ts_ms": source_ts,
        "gex_freshness": None,
        "gex_acceptance_event": (
            gex_snapshot.get("acceptance_event") if gex_snapshot else None),
        "spring": spring,
        "std_usd": std_usd,
        "band_half": None,
        "band_clamped": False,
        "normalized_deviation": None,
        "anchor_nd_mean_abs": None,
        "anchor_gravity_ref_score": None,
        "anchor_gravity_ref_label": "Warming",
        "anchor_gravity_warming": True,
        "anchor_gravity_window_count": len(nd_window or []),
        "gravity_window_updated": False,
        "freshness": None,
        "flip_point": effective_flip,
        "acceptance_event": (
            gex_snapshot.get("acceptance_event") if gex_snapshot else None),
    }
    if not gex_snapshot or gex_snapshot.get("flip_point") is None:
        return module_result(MODULE_ANCHOR, STATE_INVALID,
                             facts=facts,
                             reasons=["ANCHOR_SOURCE_MISSING"],
                             quality=QUALITY_MISSING)

    age_ms = now_ms() - source_ts if source_ts else None
    freshness = "FRESH"
    if age_ms is None:
        freshness = "EXPIRED"
    elif age_ms >= config["gex_freshness_expired_ms"]:
        freshness = "EXPIRED"
    elif age_ms >= config["gex_freshness_stale_ms"]:
        freshness = "STALE"
    facts["gex_freshness"] = freshness
    facts["freshness"] = freshness

    band_half, band_clamped = compute_band_half(
        std_usd, price_ref, spring, config)
    nd = normalized_deviation(
        bar_close, gex_snapshot.get("flip_point"), band_half)
    facts.update({
        "band_half": band_half,
        "normalized_deviation": nd,
        "band_clamped": band_clamped,
        "ready": (
            gex_snapshot.get("flip_point") is not None
            and bar_close is not None
            and band_half is not None),
    })
    if freshness == "EXPIRED":
        return module_result(MODULE_ANCHOR, STATE_INVALID, facts=facts,
                             reasons=["ANCHOR_EXPIRED"],
                             quality=QUALITY_INVALID)
    if bar_close is None:
        return module_result(MODULE_ANCHOR, STATE_INVALID, facts=facts,
                             reasons=["ANCHOR_BAR_MISSING"],
                             quality=QUALITY_MISSING)
    if band_half is None or nd is None:
        return module_result(MODULE_ANCHOR, STATE_INVALID, facts=facts,
                             reasons=["ANCHOR_BAND_UNAVAILABLE"],
                             quality=QUALITY_INVALID)
    if nd_window is not None and update_window:
        nd_window.append(abs(nd))
        facts["gravity_window_updated"] = True
    gravity_values = nd_window if nd_window is not None else [abs(nd)]
    mean_abs, score, warming = compute_anchor_gravity(gravity_values, config)
    facts.update({
        "anchor_nd_mean_abs": mean_abs,
        "anchor_gravity_ref_score": score,
        "anchor_gravity_ref_label": anchor_gravity_label(score),
        "anchor_gravity_warming": warming,
        "anchor_gravity_window_count": len(gravity_values or []),
    })
    if freshness == "STALE":
        reasons.append("ANCHOR_STALE")
    if abs(nd) > config["anchor_weak_deviation"]:
        reasons.append("ANCHOR_DEVIATION_WIDE")
    if gex_snapshot.get("pending"):
        reasons.append("GEX_PENDING")

    state = STATE_VALID if not reasons else STATE_WEAK
    return module_result(MODULE_ANCHOR, state, facts=facts,
                         reasons=reasons,
                         quality=QUALITY_STALE if freshness == "STALE"
                         else QUALITY_OK,
                         score=score)


def evaluate_tmvf(bars, anchor_result, futures_facts=None, config=None,
                  kline_bars=None, funding_points=None):
    config = config or CONFIG
    facts = dict(futures_facts or {})
    if facts.get("last_funding_rate") is None:
        facts["last_funding_rate"] = _latest_funding_rate(funding_points)
    profile = compute_tmvf_profile(kline_bars or [], funding_points or [],
                                   config)
    micro_flow = compute_micro_flow_context(bars or [], config)
    combined = profile.get("combined", {})
    results = profile.get("results", {})
    nd = anchor_result.get("facts", {}).get("normalized_deviation")
    facts.update({
        "tmvf_architecture": "24h/48h_tmv_core_plus_bounded_funding",
        "tmvf_24h": results.get("24h"),
        "tmvf_48h": results.get("48h"),
        "tmvf_interval_hours": profile.get("interval_hours"),
        "tmvf_supported_interval_hours": profile.get("supported_interval_hours"),
        "tmv_blend": combined.get("tmv_blend"),
        "tmv_state": combined.get("state"),
        "window_conflict": combined.get("window_conflict"),
        "micro_flow": micro_flow,
        "normalized_deviation": nd,
        "kline_count": profile.get("kline_count"),
        "funding_count": profile.get("funding_count"),
    })

    reasons = []
    if not combined.get("data_ready"):
        return module_result(MODULE_TMVF, STATE_UNCLEAR,
                             facts=facts,
                             reasons=["TMVF_KLINE_WINDOW_COLD"],
                             quality=QUALITY_MISSING)

    if combined.get("window_conflict"):
        reasons.append("TMVF_WINDOW_CONFLICT")

    funding_ready = _tmvf_any_funding_ready(results)
    if not funding_ready:
        reasons.append("TMVF_FUNDING_HISTORY_MISSING")

    micro_combined = (micro_flow or {}).get("combined") or {}
    micro_ready = bool(micro_combined.get("data_ready"))
    if not micro_ready:
        reasons.append("TMVF_MICRO_FLOW_UNALIGNED")

    direction = _tmvf_direction_from_score(combined.get("tmv_blend"), config)
    if combined.get("window_conflict"):
        direction = DIRECTION_UNCLEAR
    elif config.get("tmvf_micro_flow_direction_tilt", False):
        # v0.5 de-double-count: micro_flow no longer tilts TMV direction by
        # default. Volume-bar flow now enters direction only via the EDB CVD
        # vote, so the same flow data is not counted twice.
        direction = _tmvf_apply_micro_flow_tilt(
            direction, micro_combined, reasons)

    market_state = _tmvf_market_state(direction, results, config)
    if combined.get("window_conflict"):
        market_state = MARKET_UNCLEAR

    facts["direction"] = direction
    facts["market_state"] = market_state
    facts["tmvf_funding_effect"] = _tmvf_primary_funding_effect(results)
    facts["micro_flow_effect"] = micro_combined.get("state")
    facts["momentum"] = _tmvf_micro_fact(micro_flow, "momentum")
    facts["cvd_sum"] = _tmvf_micro_fact(micro_flow, "cvd_sum")

    quality = QUALITY_STALE if "TMVF_FUNDING_HISTORY_MISSING" in reasons else QUALITY_OK
    return module_result(MODULE_TMVF, direction, facts=facts,
                         reasons=reasons, quality=quality,
                         score=combined.get("tmv_blend"))


def _tmvf_direction_from_score(score, config):
    score = safe_float(score)
    if score is None:
        return DIRECTION_UNCLEAR
    neutral = config["tmvf_core_neutral_abs"]
    directional = config["tmvf_core_directional_abs"]
    if score >= directional:
        return DIRECTION_BULLISH
    if score >= neutral:
        return DIRECTION_NEUTRAL_TO_BULLISH
    if score <= -directional:
        return DIRECTION_BEARISH
    if score <= -neutral:
        return DIRECTION_NEUTRAL_TO_BEARISH
    return DIRECTION_NEUTRAL


def _tmvf_market_state(direction, results, config):
    if direction == DIRECTION_UNCLEAR:
        return MARKET_UNCLEAR
    effects = [
        ((item or {}).get("funding_effect") or "")
        for item in (results or {}).values()
    ]
    if any(effect in ("overcrowded", "extreme_overcrowded")
           for effect in effects):
        market_state = MARKET_FUNDING_CROWDED
    elif direction in (
            DIRECTION_BULLISH,
            DIRECTION_NEUTRAL_TO_BULLISH,
            DIRECTION_NEUTRAL_TO_BEARISH,
            DIRECTION_BEARISH):
        market_state = MARKET_DIRECTIONAL_DRIFT
    else:
        market_state = MARKET_ANCHOR_MEAN_REVERSION
    return market_state


def _tmvf_apply_micro_flow_tilt(direction, micro_combined, reasons):
    micro_direction = (micro_combined or {}).get("direction")
    if not (micro_combined or {}).get("data_ready"):
        return direction
    if direction == DIRECTION_NEUTRAL:
        if micro_direction == "bullish":
            reasons.append("TMVF_MICRO_FLOW_TILT")
            return DIRECTION_NEUTRAL_TO_BULLISH
        if micro_direction == "bearish":
            reasons.append("TMVF_MICRO_FLOW_TILT")
            return DIRECTION_NEUTRAL_TO_BEARISH
        if micro_direction == "neutral_to_bullish":
            reasons.append("TMVF_MICRO_FLOW_TILT")
            return DIRECTION_NEUTRAL_TO_BULLISH
        if micro_direction == "neutral_to_bearish":
            reasons.append("TMVF_MICRO_FLOW_TILT")
            return DIRECTION_NEUTRAL_TO_BEARISH
        return direction
    if direction in (DIRECTION_BULLISH, DIRECTION_NEUTRAL_TO_BULLISH):
        if micro_direction in ("bearish", "neutral_to_bearish"):
            reasons.append("TMVF_MICRO_FLOW_CONFLICT")
    elif direction in (DIRECTION_BEARISH, DIRECTION_NEUTRAL_TO_BEARISH):
        if micro_direction in ("bullish", "neutral_to_bullish"):
            reasons.append("TMVF_MICRO_FLOW_CONFLICT")
    return direction


def _tmvf_any_funding_ready(results):
    for item in (results or {}).values():
        funding = (item or {}).get("funding") or {}
        if funding.get("data_ready"):
            return True
    return False


def _tmvf_primary_funding_effect(results):
    item = (results or {}).get("48h") or (results or {}).get("24h") or {}
    return item.get("funding_effect")


def _latest_funding_rate(funding_points):
    latest = None
    latest_time = None
    for item in funding_points or []:
        if not isinstance(item, dict):
            continue
        funding_rate = safe_float(item.get("funding_rate"))
        funding_time = safe_float(item.get("funding_time"))
        if funding_rate is None or funding_time is None:
            continue
        if latest_time is None or funding_time > latest_time:
            latest = funding_rate
            latest_time = funding_time
    return latest


def _tmvf_micro_fact(micro_flow, key):
    horizons = (micro_flow or {}).get("horizons") or {}
    for label in ("12h", "8h", "4h"):
        item = horizons.get(label) or {}
        if item.get("data_ready"):
            return item.get(key)
    combined = (micro_flow or {}).get("combined") or {}
    if key == "score":
        return combined.get("score")
    return None


# ================================================================
# SOURCE: demo/strategy.py
# ================================================================
"""Public strategy recommendation boundary for v0.4.1.

The model emits only signal, nearest 24h/48h expiries, and strategy type.
Detailed execution is handled by a separate external program.
"""

import datetime



def build_strategy_recommendation(tmvf_result=None, expiries=None,
                                  config=None, ts_ms=None, edb=None):
    """v0.5: side comes from EDB when a tradeable lean exists; otherwise the
    output is an explicitly-labelled legacy TMV-F directional PREVIEW (an
    observation, not a recommendation). This closes the loop so the top-level
    recommendation cannot silently contradict the EDB direction layer.
    """
    config = config or CONFIG
    active_ts_ms = int(ts_ms or now_ms())
    tmvf_facts = (tmvf_result or {}).get("facts") or {}
    tmvf_signal = tmvf_facts.get("direction") or DIRECTION_UNCLEAR
    edb = edb or {}
    edb_support = edb.get("support_label")
    edb_side = edb.get("side_hint")
    edb_active = (
        edb_support in ("TRADE_SUPPORT_STRONG", "TRADE_SUPPORT_WEAK")
        and edb_side in (SIDE_PUT_CREDIT_SPREAD, SIDE_CALL_CREDIT_SPREAD))
    if edb_active:
        strategy_code = edb_side
        edb_score = safe_float(edb.get("edb_score")) or 0.0
        signal = DIRECTION_BULLISH if edb_score > 0 else DIRECTION_BEARISH
        selection_reason = "EDB_DIRECTION"
        direction_source = "EDB"
    else:
        signal = tmvf_signal
        strategy_code = strategy_code_from_signal(signal)
        selection_reason = (
            "TMVF_LEGACY_PREVIEW" if strategy_code != SIDE_NONE else None)
        direction_source = "TMVF_LEGACY_PREVIEW"
    strategy_type = strategy_type_label(strategy_code)
    expiry_24h = choose_target_expiry(
        expiries, 24.0, config, active_ts_ms)
    expiry_48h = choose_target_expiry(
        expiries, 48.0, config, active_ts_ms)
    summary = _summary(expiry_24h, expiry_48h, strategy_type)
    return add_schema({
        "signal": signal,
        "strategy_code": strategy_code,
        "strategy_type": strategy_type,
        "expiry_24h": expiry_24h,
        "expiry_48h": expiry_48h,
        "summary": summary,
        "selection_reason": selection_reason,
        "direction_source": direction_source,
        "edb_lean": edb.get("lean"),
        "edb_support": edb_support,
        "edb_confidence": edb.get("confidence"),
        "market_state": tmvf_facts.get("market_state"),
        "order_layer": "external_execution_program",
        "execution_boundary": "signal_and_strategy_only",
    }, SCHEMA_STRATEGY_RECOMMENDATION, config)


def strategy_code_from_signal(signal):
    if signal in (DIRECTION_BULLISH, DIRECTION_NEUTRAL_TO_BULLISH):
        return SIDE_PUT_CREDIT_SPREAD
    if signal in (DIRECTION_BEARISH, DIRECTION_NEUTRAL_TO_BEARISH):
        return SIDE_CALL_CREDIT_SPREAD
    return SIDE_NONE


def choose_target_expiry(expiries, target_hours, config=None, ts_ms=None):
    config = config or CONFIG
    active_ts_ms = int(ts_ms or now_ms())
    target_hours = float(target_hours)
    max_days = safe_float(config.get("deribit_max_expiry_days")) or 365.0
    max_ts_ms = active_ts_ms + int(max_days * 24 * 60 * 60 * 1000)
    future_expiries = []
    for expiry in expiries or []:
        expiry_ts_ms = safe_float(expiry)
        if expiry_ts_ms is None:
            continue
        expiry_ts_ms = int(expiry_ts_ms)
        if active_ts_ms < expiry_ts_ms <= max_ts_ms:
            future_expiries.append(expiry_ts_ms)
    unique_expiries = sorted(set(future_expiries))
    if not unique_expiries:
        return {
            "target_hours": target_hours,
            "expiry": None,
            "expiration_ts_ms": None,
            "hours_to_expiry": None,
            "distance_to_target_hours": None,
            "data_state": "MISSING",
        }
    target_ts_ms = active_ts_ms + int(target_hours * 60 * 60 * 1000)
    selected = min(unique_expiries, key=lambda item: abs(item - target_ts_ms))
    hours_to_expiry = (selected - active_ts_ms) / (60 * 60 * 1000.0)
    return {
        "target_hours": target_hours,
        "expiry": format_option_expiry(selected, config.get("asset", "BTC")),
        "expiration_ts_ms": selected,
        "hours_to_expiry": hours_to_expiry,
        "distance_to_target_hours": abs(hours_to_expiry - target_hours),
        "data_state": "OK",
    }


def strategy_type_label(strategy_code):
    mapping = {
        SIDE_PUT_CREDIT_SPREAD: "Put Credit Spread",
        SIDE_CALL_CREDIT_SPREAD: "Call Credit Spread",
        SIDE_NONE: None,
    }
    return mapping.get(strategy_code, strategy_code)


def format_option_expiry(expiry_ts_ms, asset="BTC"):
    expiry = safe_float(expiry_ts_ms)
    if expiry is None or expiry <= 0:
        return None
    dt = datetime.datetime.utcfromtimestamp(expiry / 1000.0)
    month = (
        "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
        "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    )[dt.month - 1]
    return "{0}-{1}{2}{3:02d}".format(
        asset or "BTC", dt.day, month, dt.year % 100)


def _summary(expiry_24h, expiry_48h, strategy_type):
    first = (expiry_24h or {}).get("expiry") or "24h expiry missing"
    second = (expiry_48h or {}).get("expiry") or "48h expiry missing"
    label = strategy_type or "no strategy type"
    return "24h {0} / 48h {1} / {2}".format(first, second, label)

# ================================================================
# SOURCE: demo/decision.py
# ================================================================
"""Decision compression for the demo chain."""



def decide(module_results, strategy_recommendation=None, config=None):
    config = config or CONFIG
    by_module = {item["module"]: item for item in module_results}
    reject_type = REJECT_NONE
    reject_reasons = []
    decision = DECISION_TRADE

    external = by_module.get(MODULE_EXTERNAL_GATE, {})
    anchor = by_module.get(MODULE_ANCHOR, {})
    tmvf = by_module.get(MODULE_TMVF, {})

    if external.get("state") == STATE_BLOCKED:
        decision = DECISION_NO_TRADE
        reject_type = REJECT_HARD
        reject_reasons.extend(external.get("reasons", []))
    elif anchor.get("state") == STATE_INVALID:
        decision = DECISION_NO_TRADE
        reject_type = REJECT_HARD
        reject_reasons.extend(anchor.get("reasons", ["ANCHOR_INVALID"]))
    elif tmvf.get("state") == STATE_UNCLEAR:
        decision = DECISION_NO_TRADE
        reject_type = REJECT_DATA
        reject_reasons.extend(tmvf.get("reasons", ["TMVF_UNCLEAR"]))
    elif (config.get("read_only_demo", True)
          or external.get("state") == STATE_CAUTION):
        decision = DECISION_OBSERVE
        reject_type = REJECT_SOFT
        reject_reasons.extend(external.get("reasons", [REASON_READ_ONLY_DEMO]))

    side = SIDE_NONE
    if strategy_recommendation:
        side = strategy_recommendation.get("strategy_code") or SIDE_NONE
    snapshot = {
        "ts_ms": now_ms(),
        "strategy_name": config.get("strategy_name"),
        "demo_version": config.get("demo_version"),
        "schema_version": config.get("schema_version"),
        "symbol": config["asset"],
        "read_only_demo": bool(config.get("read_only_demo", True)),
        "decision": decision,
        "side": side,
        "market_state": tmvf.get("facts", {}).get("market_state",
                                                   MARKET_UNCLEAR),
        "module_states": {k: v.get("state") for k, v in by_module.items()},
        "strategy_recommendation": strategy_recommendation,
        "reject_type": reject_type,
        "reject_reasons": reject_reasons,
        "data_quality": {k: v.get("quality") for k, v in by_module.items()},
    }
    return add_schema(snapshot, SCHEMA_DECISION_SNAPSHOT, config)


def attach_runtime_facts(decision_snapshot, runtime_facts):
    decision_snapshot["runtime_facts"] = runtime_facts or {}
    return decision_snapshot


def attach_factor_snapshot(decision_snapshot, factor_snapshot):
    decision_snapshot["factor_snapshot"] = factor_snapshot or {}
    return decision_snapshot

# ================================================================
# SOURCE: demo/recorder.py
# ================================================================
"""JSONL recorder and FMZ status renderer."""

import json
import os



class JsonlRecorder:
    def __init__(self, config=None):
        self.config = config or CONFIG
        self.logs_dir = self.config.get("logs_dir", "demo/logs")
        self.enabled = True
        try:
            os.makedirs(self.logs_dir, exist_ok=True)
        except Exception:
            self.enabled = False

    def write(self, name, payload):
        if not self.enabled:
            fmz_log(name, json.dumps(payload, ensure_ascii=False))
            return False
        path = os.path.join(self.logs_dir, name + ".jsonl")
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False,
                                        sort_keys=True) + "\n")
            return True
        except Exception as error:
            fmz_log("记录写入失败", name, str(error))
            return False


def render_status(decision_snapshot, module_results, config=None):
    config = config or CONFIG
    if not config.get("log_status_enabled", True):
        return ""
    tables = build_status_tables(decision_snapshot, module_results, config)
    text = "`" + json.dumps(tables, ensure_ascii=False) + "`"
    fmz_status(text)
    return text


def build_signal_brief(decision_snapshot, module_results):
    """One-shot综述 emitted when a signal is generated. Carries the 4-step
    kernel (situation / lean / target / plan) without labelling it as such."""
    runtime = decision_snapshot.get("runtime_facts") or {}
    factors = decision_snapshot.get("factor_snapshot") or {}
    edb = factors.get("edb") or {}
    strat = factors.get("strategy_recommendation") or {}
    modules = _module_map(module_results)
    src = runtime.get("source_quality") or {}
    ev = ",".join(item.get("key") for item in (edb.get("evidence") or []))
    situation = "情况：价格 {0} / 锚 {1} / DIE+Anchor 窗口已确认；数据 B{2}/D{3}/G{4}".format(
        _fmt_price(runtime.get("current_price")),
        _cn_state(_state_of(modules, "Anchor")),
        _cn_quality(src.get("binance")), _cn_quality(src.get("deribit")),
        _cn_quality(src.get("gex")))
    lean = "倾向：{0} / 置信 {1} / 一致度 {2} / 覆盖 {3} / 证据[{4}]".format(
        _edb_lean_cn(edb.get("lean")), _fmt(edb.get("confidence")),
        _pct_text(edb.get("agreement")), _pct_text(edb.get("coverage")), ev)
    target = "目标：{0} / {1}".format(
        _fmt(strat.get("strategy_type") or "无方向"),
        _expiry_pair_text(strat))
    plan = "策略：{0} / {1}；执行层外置".format(
        _fmt(strat.get("strategy_code") or "none"),
        _support_label_cn(edb.get("support_label")))
    return " | ".join([situation, lean, target, plan])


def build_status_tables(decision_snapshot, module_results, config=None):
    config = config or CONFIG
    runtime = decision_snapshot.get("runtime_facts") or {}
    factors = decision_snapshot.get("factor_snapshot") or {}
    modules = _module_map(module_results)
    return [
        _overview_table(decision_snapshot, runtime, config),
        _edb_table(factors),
        _signal_events_table(factors.get("signal_events") or {}),
        _source_timer_table(runtime),
        _module_table(modules, factors),
        _macro_pressure_table(factors.get("macro_pressure") or {}),
    ]


def _wrap_cell_text(text, width=28, max_lines=4):
    if text is None:
        return ["-"]
    raw_lines = str(text).splitlines() or ["-"]
    lines = []
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            lines.append("")
            continue
        while len(raw) > width and len(lines) < max_lines:
            lines.append(raw[:width])
            raw = raw[width:]
        if len(lines) < max_lines:
            lines.append(raw)
        if len(lines) >= max_lines:
            break
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if len(lines) == max_lines and raw_lines:
        last = lines[-1]
        if len(last) >= width:
            lines[-1] = last[:max(0, width - 1)] + "…"
    return lines or ["-"]


def _join_cell_lines(lines, html=False):
    if lines is None:
        lines = ["-"]
    if isinstance(lines, str):
        lines = lines.splitlines()
    clean = [str(item) for item in lines if item is not None]
    if not clean:
        clean = ["-"]
    return ("<br>" if html else "\n").join(clean)


def _overview_table(decision_snapshot, runtime, config):
    reject_reasons = decision_snapshot.get("reject_reasons") or []
    audit = decision_snapshot.get("contract_audit") or {}
    return {
        "id": "overview",
        "type": "table",
        "title": _fmt(config.get("demo_version")) + " 前置信号总览",
        "cols": ["项目", "当前值", "观察含义"],
        "rows": [
            ["策略版本", config.get("demo_version"), "本轮为 FMZ 流程观察版"],
            ["运行时间", utc8_text(decision_snapshot.get("ts_ms")),
             "北京时间显示"],
            ["循环状态", "第 {0} 轮 / 已运行 {1}".format(
                _fmt(runtime.get("tick_count")),
                _fmt_age(runtime.get("uptime_ms"))), "用于判断观测是否持续"],
            ["运行模式", _cn_runtime_mode(runtime.get("runtime_mode")),
             "只读公开数据观察"],
            ["交易安全边界", "只读=" + _cn_bool(runtime.get("read_only_demo"))
             + " / 执行层=外置", "本模型不配置腿、不下单"],
            ["当前价格", _fmt_price(runtime.get("current_price"))
             + " / " + _cn_price_source(runtime.get("current_price_source")),
             "用于锚、期权链和状态观察"],
            ["最终结论", _cn_decision(decision_snapshot.get("decision")),
             _cn_reject_type(decision_snapshot.get("reject_type"))],
            ["拒绝或观察原因", _reason_list(reject_reasons),
             "No Trade 是有效输出，不是运行失败"],
            ["合约自检", "通过=" + _cn_bool(audit.get("ok"))
             + " / 错误=" + _fmt(len(audit.get("errors") or []))
             + " / 警告=" + _fmt(len(audit.get("warnings") or [])),
             "检查输出结构与只读边界"],
        ],
    }


def _source_timer_table(runtime):
    source_quality = runtime.get("source_quality") or {}
    source_details = runtime.get("source_details") or {}
    gex = source_details.get("gex") or {}
    binance = source_details.get("binance") or {}
    deribit = source_details.get("deribit") or {}
    return {
        "type": "table",
        "title": "数据源与定时任务",
        "cols": ["分区", "状态", "数量/年龄", "下次动作", "备注"],
        "rows": [
            ["Binance 行情", _cn_quality(source_quality.get("binance")),
             "成交量柱 {0} / K线 {1} / Funding {2}".format(
                 _fmt(runtime.get("completed_bar_count")),
                 _fmt(runtime.get("tmvf_kline_count")),
                 _fmt(runtime.get("tmvf_funding_count"))),
             "TMV-F 刷新 " + _fmt_age(
                 runtime.get("tmvf_next_refresh_in_ms")),
             _detail_text(binance, ("error", "tmvf_data_quality"))],
            ["成交量柱构造", "运行中",
             "已完成 {0} 根 / 当前 {1}/{2} BTC".format(
                 _fmt(runtime.get("completed_bar_count")),
                 _fmt_float(runtime.get("current_bar_volume")),
                 _fmt_float(runtime.get("volume_bar_threshold"))),
             "进度 " + _fmt_pct(runtime.get("current_bar_progress_frac")),
             "本轮成交 " + _fmt(runtime.get("last_cycle_trade_count"))
             + " 条 / 新柱 " + _fmt(
                 runtime.get("last_cycle_new_bar_count")) + " 根"],
            ["Deribit 期号发现", _cn_quality(source_quality.get("deribit")),
             "期号 {0} / 年龄 {1}".format(
                 _fmt(runtime.get("option_expiry_count")),
                 _fmt_age(runtime.get("option_expiry_age_ms"))),
             "期号刷新 " + _fmt_age(
                 runtime.get("option_expiry_next_refresh_in_ms")),
             _detail_text(deribit, ("error", "expiry_count"))],
            ["GEX 锚源", _cn_quality(source_quality.get("gex")),
             "年龄 " + _fmt_age(runtime.get("gex_fetch_age_ms")),
             "GEX刷新 " + _fmt_age(runtime.get("gex_next_fetch_in_ms")),
             _detail_text(gex, ("cached", "error"))],
            ["主循环", "运行中", "间隔 " + _fmt_age(runtime.get("loop_sleep_ms")),
             "下一轮由 FMZ Sleep 控制",
             "最大循环=" + _fmt(runtime.get("max_main_loops"))],
            ["策略图表", _chart_state(runtime.get("chart")),
             "累计写入点 " + _fmt((runtime.get("chart") or {}).get(
                 "points_added")),
             "每轮追加实时价 / 处理后中轴 / Anchor分数 / TMV-F分数 / M-DIE",
             _fmt((runtime.get("chart") or {}).get("last_error"))],
        ],
    }


def _module_table(modules, factors=None):
    factors = factors or {}
    rows = []
    order = [
        ("1", "External Gate"),
        ("2", "Anchor"),
        ("3", "TMV-F"),
    ]
    anchor_fx = factors.get("anchor") or {}
    for index, module in order:
        item = modules.get(module) or {}
        reason_cell = _reason_list(item.get("reasons") or [])
        if module == "Anchor":
            reason_cell = (
                "偏离 " + _fmt_float(anchor_fx.get("normalized_deviation"))
                + " / 分数 " + _fmt_float(
                    anchor_fx.get("anchor_gravity_ref_score"))
                + " / 中轴 " + _fmt_price(
                    anchor_fx.get("effective_flip_point"))
                + " / " + _cn_anchor_label(
                    anchor_fx.get("anchor_gravity_ref_label")))
        rows.append([
            index,
            _cn_module(module),
            _cn_state(item.get("state")),
            _cn_quality(item.get("quality")),
            reason_cell,
            _module_meaning(module, item),
        ])
    me = factors.get("m_die") or {}
    nr = factors.get("neutral_repair_signal") or {}
    edb = factors.get("edb") or {}
    skew = factors.get("skew") or {}
    ggr = factors.get("gamma_regime") or {}
    macro = factors.get("macro_pressure") or {}
    gate = edb.get("ggr_gate") or {}
    rows.append([
        "时序", "M-DIE 位移", _fmt(me.get("level")),
        _fmt((me.get("data_status") or {}).get("data_state")),
        _fmt(me.get("direction")) + " / " + _fmt_float(me.get("m_die")),
        "15m 短期单向变化触发"])
    rows.append([
        "时序", "DIE+Anchor 修复", _neutral_repair_state_cn(nr.get("state")),
        "激活=" + _cn_bool(nr.get("is_active")),
        _reason_list(nr.get("reason_codes") or []),
        "窗口择时门(决定何时，不决定方向)"])
    rows.append([
        "方向", "EDB 合成", _edb_lean_cn(edb.get("lean")),
        "置信 " + _fmt(edb.get("confidence")),
        "EDB " + _fmt_signed_num(edb.get("edb_score"), 3)
        + " / 一致度 " + _pct_text(edb.get("agreement")),
        "六证据到期窗口方向合成(权威方向层)"])
    rows.append([
        "方向", "SRD 期权偏斜", _fmt(skew.get("data_state")),
        "vote " + _fmt_signed_num(skew.get("vote"), 2),
        "RR " + _fmt_signed_num(skew.get("rr_blend"), 4),
        "期权需求面方向票"])
    rows.append([
        "门控", "GGR Gamma区制", _fmt(ggr.get("regime")),
        "门x" + _fmt_num(gate.get("multiplier"), 2)
        + ("/否决" if gate.get("veto") else ""),
        "净Gamma " + _fmt_signed_float(ggr.get("net_gamma_notional"))
        + " / 最大行权 " + _fmt_price(ggr.get("max_gamma_strike")),
        "单边卖权安全门 + 空间钉"])
    rows.append([
        "环境", "MPF 宏观", _fmt(macro.get("macro_regime")),
        _fmt(macro.get("data_status")),
        "score " + _fmt_signed_num(macro.get("macro_score"), 3),
        "多日外部环境顺/逆风"])
    return {
        "type": "table",
        "title": "主链路与因子状态",
        "cols": ["阶段", "模块/因子", "状态", "数据质量", "原因/读数", "观察含义"],
        "rows": rows,
    }


def _edb_table(factors):
    edb = (factors or {}).get("edb") or {}
    skew = (factors or {}).get("skew") or {}
    if not edb:
        return {
            "id": "edb", "type": "table", "title": "EDB 到期方向合成层",
            "cols": ["项目", "当前值", "观察含义"],
            "rows": [["状态", "未计算", "本轮无 EDB 输出"]],
        }
    precond = edb.get("precondition") or {}
    gate = edb.get("ggr_gate") or {}
    ev = {item.get("key"): item for item in (edb.get("evidence") or [])}

    def detail(key):
        return (ev.get(key) or {}).get("detail") or {}

    def vw(key):
        item = ev.get(key)
        if not item:
            return "—"
        return ("vote " + _fmt_signed_num(item.get("vote"), 2)
                + " / w " + _fmt_num(item.get("weight"), 2))

    tmv = detail("TMV")
    # CVD/MACRO rows prefer EDB's rich detail but fall back to the raw factor
    # payloads so the observable numbers (cvd_sum / 涨跌 / macro score) always
    # render even when EDB drops the vote to zero weight (window not yet ready,
    # macro blocking). Mirrors the SRD/GGR pattern just below.
    micro = ((factors or {}).get("flow") or {}).get("micro_flow") or {}
    macro_raw = (factors or {}).get("macro_pressure") or {}

    def cvd_cell(edb_key, raw_key):
        det = detail(edb_key)
        raw = micro.get(raw_key) or {}
        return {
            "cvd_sum": det.get("cvd_sum", raw.get("cvd_sum")),
            "price_return_pct": det.get(
                "price_return_pct", raw.get("momentum_return_pct")),
            "strength": det.get("strength") or raw.get("state"),
            "verdict": det.get("verdict"),
        }

    c4 = cvd_cell("CVD_4h", "fast_4h")
    c12 = cvd_cell("CVD_12h", "slow_12h")
    md = detail("MACRO")
    mac = {
        "macro_score": md.get("macro_score", macro_raw.get("macro_score")),
        "macro_regime": md.get("macro_regime") or macro_raw.get("macro_regime"),
        "components_cn": (md.get("components_cn")
                          or macro_raw.get("summary_label_cn")),
    }
    # SRD/GGR rich detail read straight from their factor payloads so the raw
    # skew/gamma numbers always render, even when their EDB vote weight is 0
    # (e.g. calm/transition regime drops the GGR spatial vote from evidence).
    srdd = skew
    ggrd = (factors or {}).get("gamma_regime") or {}
    fund = detail("FUNDING")
    flow = (factors or {}).get("flow") or {}
    strat = (factors or {}).get("strategy_recommendation") or {}
    rows = [
        ["summary", "方向合成",
         _edb_lean_cn(edb.get("lean")) + " / 置信 " + _fmt(
             edb.get("confidence")),
         "EDB " + _fmt_signed_num(edb.get("edb_score"), 3)
         + " / 一致度 " + _pct_text(edb.get("agreement"))
         + " / 覆盖 " + _pct_text(edb.get("coverage"))
         + " / 冲突 " + _conflict_cn(edb.get("conflict_level")),
         _join_cell_lines(_wrap_cell_text(edb.get("summary_cn"), 34, 4))],
        ["precondition", "DIE+Anchor 窗口",
         "激活=" + _cn_bool(precond.get("nr_active")),
         _neutral_repair_state_cn(precond.get("nr_state")),
         "时序门：窗口开才放行可交易方向"],
        ["tmv", "TMV-F 主干 " + vw("TMV"),
         "blend " + _fmt_signed_num(tmv.get("tmv_blend"), 3)
         + " / " + _cn_direction(tmv.get("direction")),
         "24h " + _fmt_float(tmv.get("tmvf_24h_final"))
         + " / 48h " + _fmt_float(tmv.get("tmvf_48h_final"))
         + " / 窗口冲突=" + _cn_bool(tmv.get("window_conflict")),
         "量价主干（1h 等时间轴趋势/动量/量）"],
        ["cvd", "主动流×价格 4h " + vw("CVD_4h") + " / 12h " + vw("CVD_12h"),
         "4h: CVD " + _fmt_signed_float(c4.get("cvd_sum"))
         + " BTC / 涨跌 " + _fmt_signed_unit_pct(c4.get("price_return_pct"))
         + " / " + _fmt(c4.get("strength")) + " / " + _fmt(c4.get("verdict")),
         "12h: CVD " + _fmt_signed_float(c12.get("cvd_sum"))
         + " BTC / 涨跌 " + _fmt_signed_unit_pct(c12.get("price_return_pct"))
         + " / " + _fmt(c12.get("strength")),
         "强度按滚动分位(自适应非固定阈)，联合价格四象限"],
        ["macro", "宏观 " + vw("MACRO"),
         "score " + _fmt_signed_num(mac.get("macro_score"), 3)
         + " / " + _fmt(mac.get("macro_regime")),
         _join_cell_lines(_wrap_cell_text(mac.get("components_cn"), 38, 4)),
         "多日环境顺/逆风，作方向票之一"],
        ["funding", "资金费率反身性 " + vw("FUNDING"),
         "rate " + _fmt_signed_pct(flow.get("last_funding_rate")),
         "verdict " + _fmt(fund.get("verdict"))
         + " / norm " + _fmt_signed_num(fund.get("funding_norm"), 2),
         "永续仓位拥挤/反身性，小权重方向票"],
        ["srd", "期权偏斜 SRD " + vw("SRD"),
         "RR " + _fmt_signed_num(srdd.get("rr_blend"), 4)
         + " / 归一 " + _fmt_signed_num(srdd.get("skew_norm_blend"), 3),
         "rr_z " + _fmt_signed_num(srdd.get("rr_z"), 2)
         + " / ΔRR " + _fmt_signed_num(srdd.get("delta_rr"), 4)
         + " / 质量 " + _fmt_num(srdd.get("vote_confidence"), 2)
         + " / 数据 " + _fmt(skew.get("data_state")),
         "25Δ风险逆转:方向用相对偏离+动量(非原始符号);RR常为负属正常"],
        ["ggr", "Gamma区制 GGR " + vw("GGR_SPATIAL"),
         _fmt(ggrd.get("regime")) + " / 强度 "
         + _fmt_num(ggrd.get("regime_strength"), 2)
         + " / 门x" + _fmt_num(gate.get("multiplier"), 2)
         + (" / 否决" if gate.get("veto") else ""),
         "净Gamma " + _fmt_signed_float(ggrd.get("net_gamma_notional"))
         + " / flip " + _fmt_price(ggrd.get("flip_point"))
         + " / 距flip " + _fmt_num(ggrd.get("distance_to_flip_pct"), 2)
         + "% / 最大Gamma行权 " + _fmt_price(ggrd.get("max_gamma_strike")),
         "首先是单边卖权安全门(负Gamma放大→砍/否决)，其次空间钉"],
        ["recommendation", "下游侧建议(信号成立后)",
         _fmt(strat.get("strategy_type") or "-")
         + " / " + _fmt(edb.get("side_hint")),
         "支持 " + _support_label_cn(edb.get("support_label"))
         + " / " + _expiry_pair_text(strat),
         "信号成立后基于EDB倾向+置信；期号在此，腿/报价/下单外置"],
    ]
    return {
        "id": "edb", "type": "table", "title": "EDB 到期方向合成层",
        "cols": ["ID", "分区", "关键输出", "辅助字段", "观察含义"],
        "rows": rows,
    }


def _edb_lean_cn(lean):
    mapping = {
        "BULLISH_STRONG": "强偏多", "BULLISH_WEAK": "弱偏多",
        "BULLISH": "偏多(窗口未开/预热)",
        "BEARISH_STRONG": "强偏空", "BEARISH_WEAK": "弱偏空",
        "BEARISH": "偏空(窗口未开/预热)",
        "NEUTRAL": "方向不明/中性",
    }
    return mapping.get(lean, lean or "-")


def _fmt_signed_num(value, digits=2):
    try:
        if value is None:
            return "-"
        return ("{:+." + str(int(digits)) + "f}").format(float(value))
    except Exception:
        return str(value)


def _fmt_num(value, digits=2):
    try:
        if value is None:
            return "-"
        return ("{:." + str(int(digits)) + "f}").format(float(value))
    except Exception:
        return "-"


def _pct_text(value):
    try:
        if value is None:
            return "-"
        return "{:.0%}".format(float(value))
    except Exception:
        return "-"


def _signal_events_table(signal_events):
    rows = []
    for item in (signal_events.get("events") or [])[:10]:
        rows.append([
            utc8_text(item.get("confirmed_time")),
            _fmt_price(item.get("price_at_confirmation")),
            "方向 " + _fmt(item.get("episode_direction"))
            + " / DIE峰值 " + _fmt_float(item.get("peak_m_die"))
            + " / 合并 " + _fmt(item.get("event_count_merged")),
            _edb_lean_cn(item.get("edb_lean"))
            + " / " + _support_label_cn(item.get("edb_support"))
            + " / 置信 " + _fmt(item.get("edb_confidence")),
            _signal_event_numeric_text(item),
            "已确认 DIE+Anchor 修复信号，供后续全量复盘观察",
        ])
    if not rows:
        rows.append([
            "-", "-", "暂无确认事件", "-", "-",
            "仅记录 NR_REPAIR_CONFIRMED 的成功信号",
        ])
    return {
        "id": "signal_events",
        "type": "table",
        "title": "信号事件",
        "cols": ["时间", "成交价", "DIE+Anchor", "倾向性结论", "数值摘要", "观察含义"],
        "rows": rows,
    }


def _signal_event_numeric_text(item):
    return (
        "TMV " + _fmt_float(item.get("tmv_blend"))
        + " / Macro " + _fmt_float(item.get("macro_score"))
        + " / 4h CVD " + _fmt_signed_float(item.get("fast_4h_cvd_btc"))
        + " BTC / 4h涨跌 " + _fmt_signed_unit_pct(
            item.get("fast_4h_return_pct"))
        + " / SRD " + _fmt_signed_float(item.get("srd_rr_blend"))
        + " / Funding " + _fmt_signed_pct(item.get("funding_rate"))
    )


def _macro_pressure_table(macro):
    components = macro.get("components") or []
    rows = [[
        "summary",
        "MPF",
        "分数 " + _fmt_float(macro.get("macro_score"))
        + " / " + _fmt(macro.get("summary_label_cn")),
        "置信度 " + _fmt_float(macro.get("macro_data_confidence"))
        + " / 状态 " + _fmt(macro.get("data_status")),
        "最近数据 " + utc8_text(macro.get("last_data_time"))
        + " / 年龄 " + _fmt_age(macro.get("data_age_ms")),
    ]]
    for item in components:
        rows.append([
            item.get("key"),
            _fmt(item.get("source_symbol"))
            + " / " + _fmt(item.get("source_status")),
            "当前 " + _fmt_float(item.get("current_close"))
            + " / 参考 " + _fmt_float(item.get("reference_close")),
            "3d变化 " + _fmt_float(item.get("change_pct_3d"))
            + "% / " + _fmt(item.get("observation_cn")),
            "分层 " + _fmt(item.get("tier_cn"))
            + " / 贡献 " + _fmt_float(item.get("component_score"))
            + " / " + _fmt(item.get("meaning_cn")),
        ])
    return {
        "id": "macro_pressure",
        "type": "table",
        "title": "宏观要素",
        "cols": ["ID", "组件", "关键输出", "辅助字段", "观察含义"],
        "rows": rows,
    }


def _neutral_repair_state_cn(value):
    mapping = {
        "NR_IDLE": "等待DIE事件",
        "NR_DISPLACEMENT_ACTIVE": "DIE位移活跃",
        "NR_WAIT_ANCHOR_DAMAGE": "等待Anchor受损",
        "NR_WAIT_ANCHOR_REPAIR": "等待Anchor修复",
        "NR_REPAIR_CANDIDATE": "修复候选",
        "NR_REPAIR_CONFIRMED": "修复确认",
        "NR_REPAIR_STALE": "修复上下文过期",
        "NR_DATA_INSUFFICIENT": "数据不足",
    }
    return mapping.get(value, _fmt(value))


def _support_label_cn(value):
    mapping = {
        "TRADE_SUPPORT_STRONG": "支持较强",
        "TRADE_SUPPORT_WEAK": "支持偏弱",
        "WAIT_CONFIRMATION": "等待确认",
        "NO_TRADE_AMBIGUOUS": "无交易-歧义",
        "NO_TRADE_BLOCKED": "无交易-阻断",
    }
    return mapping.get(value, _fmt(value))


def _conflict_cn(value):
    mapping = {
        "NONE": "无",
        "MILD": "轻微",
        "MATERIAL": "明显",
        "SEVERE": "严重",
    }
    return mapping.get(value, _fmt(value))


def _module_map(module_results):
    return {item.get("module"): item for item in module_results or []
            if isinstance(item, dict)}


def _state_of(modules, module):
    return (modules.get(module) or {}).get("state")


def _detail_text(details, keys):
    parts = []
    for key in keys:
        value = details.get(key)
        if value is None:
            continue
        if key == "cached":
            parts.append("缓存=" + _cn_bool(value))
        elif key == "error":
            parts.append("异常=" + _cn_reason(value))
        else:
            parts.append(str(key) + "=" + _fmt(value))
    return "；".join(parts) if parts else "-"


def _expiry_pair_text(strategy):
    first = strategy.get("expiry_24h") or {}
    second = strategy.get("expiry_48h") or {}
    return "24h {0} / 48h {1}".format(
        _fmt(first.get("expiry")),
        _fmt(second.get("expiry")),
    )


def _chart_state(snapshot):
    if not snapshot:
        return "-"
    if not snapshot.get("enabled"):
        return "已关闭"
    if snapshot.get("initialized"):
        return "已初始化"
    if snapshot.get("last_error"):
        return "等待中"
    return "待初始化"


def _module_meaning(module, item):
    state = item.get("state")
    if module == "External Gate":
        return "系统前置边界"
    if module == "Anchor":
        return "中性锚有效性"
    if module == "TMV-F":
        return "到期窗口倾向"
    return _fmt(state)


def _reason_list(reasons):
    if not reasons:
        return "-"
    return "；".join(_cn_reason(item) for item in reasons)


def _cn_reason(value):
    mapping = {
        "TRADER_DISABLED": "交易开关关闭",
        "RISK_BUDGET_INVALID": "风险预算无效",
        "READ_ONLY_DEMO": "只读演示模式",
        "ALL_SOURCES_UNAVAILABLE": "全部数据源不可用",
        "ANCHOR_SOURCE_MISSING": "锚源缺失",
        "ANCHOR_BAR_MISSING": "成交量柱缺失",
        "ANCHOR_EXPIRED": "锚数据过期",
        "ANCHOR_BAND_UNAVAILABLE": "锚带宽不可用",
        "ANCHOR_STALE": "锚数据偏旧",
        "ANCHOR_DEVIATION_WIDE": "价格偏离锚过宽",
        "GEX_PENDING": "GEX锚仍在确认",
        "TMVF_KLINE_WINDOW_COLD": "TMV-F K线窗口不足",
        "TMVF_FUNDING_HISTORY_MISSING": "Funding历史不足",
        "TMVF_WINDOW_CONFLICT": "24h/48h窗口冲突",
        "TMVF_MICRO_FLOW_UNALIGNED": "成交量柱微流视角未对齐",
        "TMVF_MICRO_FLOW_TILT": "微流对中性判断给出倾斜",
        "TMVF_MICRO_FLOW_CONFLICT": "微流与主方向冲突",
        "ANCHOR_INVALID": "锚无效",
        "TREND_ACCELERATION": "趋势加速",
        "NO_ORDER_PLACEMENT_IMPLEMENTED": "未实现真实下单",
        "gex_payload_unrecognized": "GEX返回暂不能识别",
    }
    return mapping.get(value, _fmt(value))


def _cn_state(value):
    mapping = {
        "Clear": "清晰",
        "Caution": "注意",
        "Blocked": "阻断",
        "Valid": "有效",
        "Weak": "偏弱",
        "Invalid": "无效",
        "Unclear": "不清晰",
    }
    return mapping.get(value, _fmt(value))


def _cn_quality(value):
    mapping = {
        "OK": "正常",
        "STALE": "偏旧",
        "MISSING": "缺失",
        "INVALID": "无效",
        "ERROR": "异常",
    }
    return mapping.get(value, _fmt(value))


def _cn_decision(value):
    mapping = {
        "Trade": "交易信号",
        "Observe": "观察",
        "No Trade": "不交易",
    }
    return mapping.get(value, _fmt(value))


def _cn_reject_type(value):
    mapping = {
        "none": "无拒绝",
        "hard_gate": "硬门控",
        "soft_gate": "软门控",
        "data_insufficient": "数据不足",
        "discretionary": "人工裁量",
    }
    return mapping.get(value, _fmt(value))


def _cn_module(value):
    mapping = {
        "External Gate": "外部门控",
        "Anchor": "Anchor 锚",
        "TMV-F": "TMV-F 倾向",
    }
    return mapping.get(value, _fmt(value))


def _cn_direction(value):
    mapping = {
        "Bullish": "偏多",
        "Neutral-to-Bullish": "中性偏多",
        "Neutral": "中性",
        "Neutral-to-Bearish": "中性偏空",
        "Bearish": "偏空",
        "Unclear": "不清晰",
    }
    return mapping.get(value, _fmt(value))


def _cn_anchor_label(value):
    mapping = {
        "Warming": "预热中",
        "Weak": "偏弱",
        "Valid": "有效",
        "Strong": "强",
        "Detached": "脱锚",
        "Loose": "松散贴合",
        "Attached": "贴合",
        "Tightly Attached": "紧密贴合",
    }
    return mapping.get(value, _fmt(value))


def _cn_runtime_mode(value):
    mapping = {
        "live_public_read_only": "公开行情只读观察",
        "offline_fixture": "离线样例",
        "offline_missing_sources": "离线无数据源",
    }
    return mapping.get(value, _fmt(value))


def _cn_price_source(value):
    mapping = {
        "binance_spot_volume_bar": "Binance现货成交量柱",
        "gex_asset_price": "GEX资产价格",
        "binance_futures_mark": "Binance合约标记价",
        "binance_futures_index": "Binance合约指数价",
        "binance_spot_depth_mid": "Binance现货盘口中间价",
        "deribit_index_price": "Deribit指数价",
        "offline_fixture": "离线样例",
    }
    return mapping.get(value, _fmt(value))


def _cn_bool(value):
    if value is True:
        return "是"
    if value is False:
        return "否"
    return _fmt(value)


def _fmt(value):
    if value is None:
        return "-"
    return str(value)


def _fmt_float(value):
    try:
        if value is None:
            return "-"
        return "{:.4f}".format(float(value))
    except Exception:
        return str(value)


def _fmt_price(value):
    try:
        if value is None:
            return "-"
        return "{:.2f}".format(float(value))
    except Exception:
        return str(value)


def _fmt_pct(value):
    try:
        if value is None:
            return "-"
        return "{:.2f}%".format(float(value) * 100.0)
    except Exception:
        return str(value)


def _fmt_signed_pct(value):
    try:
        if value is None:
            return "-"
        return "{:+.4f}%".format(float(value) * 100.0)
    except Exception:
        return str(value)


def _fmt_signed_unit_pct(value):
    try:
        if value is None:
            return "-"
        return "{:+.2f}%".format(float(value))
    except Exception:
        return str(value)


def _fmt_signed_float(value):
    try:
        if value is None:
            return "-"
        return "{:+.4f}".format(float(value))
    except Exception:
        return str(value)


def _fmt_age(value_ms):
    try:
        if value_ms is None:
            return "-"
        value = float(value_ms)
    except Exception:
        return str(value_ms)
    if value <= 0:
        return "就绪"
    seconds = value / 1000.0
    if seconds < 60:
        return "{:.0f}秒".format(seconds)
    minutes = seconds / 60.0
    if minutes < 60:
        return "{:.1f}分钟".format(minutes)
    hours = minutes / 60.0
    return "{:.1f}小时".format(hours)

# ================================================================
# SOURCE: demo/main.py
# ================================================================
"""Local multi-file demo entrypoint.

The final FMZ delivery will be merged into a single Python file. This module
keeps the demo stage readable while interfaces and module contracts settle.
"""

import collections
import math



class DemoRuntime:
    def __init__(self, config=None):
        base_config = CONFIG if config is None else config
        self.config = apply_runtime_config_overrides(base_config, globals())
        self.http = HttpClient(self.config)
        self.binance = BinanceAdapter(self.http, self.config)
        self.deribit = DeribitAdapter(self.http, self.config)
        self.gex = GexAdapter(self.http, self.config)
        self.gex_state = GexAnchorState(self.config)
        self.bars = BarAssembler(
            self.binance.fetch_spot_agg_trades,
            self.config,
            self.binance.normalize_agg_trade,
        )
        self.recorder = JsonlRecorder(self.config)
        self.option_expiries = []
        self.futures_facts = {}
        self.current_price = None
        self.current_price_source = None
        self.last_source_quality = {}
        self.last_source_details = {}
        self.last_option_expiry_refresh_ms = None
        self.anchor_nd_window = collections.deque(
            maxlen=int(self.config["anchor_gravity_window"]))
        self.last_anchor_bar_index = None
        self.tmvf_klines = []
        self.tmvf_funding_points = []
        self.last_tmvf_refresh_ms = None
        self.tmvf_data_quality = QUALITY_MISSING
        self.mdie_klines = []
        self.last_mdie_refresh_ms = None
        self.mdie_data_quality = QUALITY_MISSING
        self.macro_factor = MacroPressureFactor(self.http, self.config)
        self.last_macro_snapshot = None
        self.neutral_repair_tracker = NeutralRepairSignalTracker(self.config)
        self.signal_events = SignalEventTracker(self.config)
        self.option_instruments = []
        self.option_greeks = []
        self.last_option_greeks_refresh_ms = None
        _cvd_window = int(self.config.get("edb_cvd_strength_window", 240))
        self.cvd_hist = {
            "4h": collections.deque(maxlen=_cvd_window),
            "12h": collections.deque(maxlen=_cvd_window),
        }
        self.rr_hist = collections.deque(
            maxlen=int(self.config.get("srd_rr_baseline_window", 240)))
        self.prev_edb_score = None
        self.chart = DemoChart(self.config)
        self.start_ms = now_ms()
        self.tick_count = 0
        self.last_live_fetch_active = None
        self.last_decision_signature = None
        self.last_tick_summary_log_count = 0
        self.last_tick_error = None
        if self.config.get("startup_log_enabled", True):
            self._log_startup()

    def tick(self, live_fetch=None):
        self.tick_count += 1
        self.last_tick_error = None
        live = self.config.get("live_fetch_enabled", False)
        if live_fetch is not None:
            live = bool(live_fetch)
        self.last_live_fetch_active = live

        source_quality = {}
        self.last_source_details = {}
        if live:
            self._fetch_live_sources(source_quality)
        elif self.config.get("offline_fixture_enabled", False):
            self._seed_offline_fixture()
            source_quality = {
                "binance": QUALITY_OK,
                "deribit": QUALITY_OK,
                "gex": QUALITY_OK,
            }
            self.last_source_details = {
                "binance": {"quality": QUALITY_OK, "mode": "offline_fixture"},
                "deribit": {"quality": QUALITY_OK, "mode": "offline_fixture"},
                "gex": {"quality": QUALITY_OK, "mode": "offline_fixture"},
            }
        else:
            source_quality = {
                "binance": QUALITY_MISSING,
                "deribit": QUALITY_MISSING,
                "gex": QUALITY_MISSING,
            }
            self.last_source_details = {
                "binance": {"quality": QUALITY_MISSING, "mode": "offline"},
                "deribit": {"quality": QUALITY_MISSING, "mode": "offline"},
                "gex": {"quality": QUALITY_MISSING, "mode": "offline"},
            }
        self.last_source_quality = dict(source_quality)
        self._refresh_macro_factor(
            live, self.config.get("offline_fixture_enabled", False))

        gex_snapshot = self.gex_state.snapshot()
        external = evaluate_external_gate(self.config, source_quality)
        anchor = self._evaluate_anchor_for_completed_bars(gex_snapshot)
        tmvf = evaluate_tmvf(
            list(self.bars.completed_bars), anchor, self.futures_facts,
            self.config,
            kline_bars=self.tmvf_klines,
            funding_points=self.tmvf_funding_points)
        module_results = [
            external, anchor, tmvf,
        ]
        m_die = compute_m_die(self.mdie_klines, self.config)
        macro_pressure = self._effective_macro_snapshot()
        neutral_repair_signal = self.neutral_repair_tracker.update(
            m_die, anchor, {"current_price": self.current_price})
        factor_snapshot = build_factor_snapshot(
            module_results,
            None,
            macro_pressure,
            m_die,
            neutral_repair_signal,
            self.config)
        skew = evaluate_skew_rr(
            self.option_greeks, list(self.rr_hist), self.config)
        gamma_regime = evaluate_gamma_regime(
            gex_snapshot, self.current_price, self.option_greeks, self.config)
        edb = evaluate_edb(
            factor_snapshot.get("flow"),
            macro_pressure,
            neutral_repair_signal,
            skew,
            gamma_regime,
            {"4h": list(self.cvd_hist["4h"]),
             "12h": list(self.cvd_hist["12h"])},
            self.prev_edb_score,
            self.config)
        factor_snapshot["skew"] = skew
        factor_snapshot["gamma_regime"] = gamma_regime
        factor_snapshot["edb"] = edb
        self.prev_edb_score = edb.get("edb_score")
        self._update_edb_history(factor_snapshot.get("flow"), skew)
        strategy_recommendation = build_strategy_recommendation(
            tmvf, self.option_expiries, self.config, edb=edb)
        factor_snapshot["strategy_recommendation"] = _strategy_factors(
            strategy_recommendation)
        self.last_signal_recorded = self.signal_events.maybe_record(
            neutral_repair_signal,
            factor_snapshot,
            {"current_price": self.current_price})
        factor_snapshot["signal_events"] = self.signal_events.snapshot()
        decision_snapshot = decide(
            module_results, strategy_recommendation, self.config)
        attach_factor_snapshot(decision_snapshot, factor_snapshot)
        attach_runtime_facts(decision_snapshot, self._runtime_facts())
        contract_audit = validate_evaluation_contract(
            decision_snapshot, module_results, factor_snapshot, self.config)
        decision_snapshot["contract_audit"] = contract_audit
        self.chart.update(decision_snapshot)
        attach_runtime_facts(decision_snapshot, self._runtime_facts())
        self.recorder.write("decisions", decision_snapshot)
        self.recorder.write("snapshots", add_schema({
            "decision": decision_snapshot,
            "modules": module_results,
            "factor_snapshot": factor_snapshot,
            "contract_audit": contract_audit,
        }, SCHEMA_EVALUATION_SNAPSHOT, self.config))
        self._log_tick_summary(decision_snapshot, module_results)
        render_status(decision_snapshot, module_results, self.config)
        return decision_snapshot, module_results

    def _log_startup(self):
        fmz_log(
            "启动摘要",
            "版本=" + str(self.config.get("demo_version")),
            "模式=只读观察",
            "只读=" + str(self.config.get("read_only_demo")),
            "公开行情=" + str(self.config.get("live_fetch_enabled")),
            "最大循环=" + str(self.config.get("max_main_loops")),
            "循环间隔毫秒=" + str(self.config.get("loop_sleep_ms")),
        )

    def _log_tick_summary(self, decision_snapshot, module_results):
        if not self.config.get("tick_summary_log_enabled", True):
            return
        every = int(self.config.get("tick_summary_log_every", 1) or 1)
        if every < 1:
            every = 1
        module_states = [
            str(item.get("module")) + "=" + str(item.get("state"))
            for item in module_results or []
        ]
        signature = "|".join([
            str(decision_snapshot.get("decision")),
            str(decision_snapshot.get("side")),
            ";".join(module_states),
            ",".join(decision_snapshot.get("reject_reasons") or []),
        ])
        changed = signature != self.last_decision_signature
        should_log_regular = (
            self.tick_count - self.last_tick_summary_log_count >= every)
        if should_log_regular:
            self.last_tick_summary_log_count = self.tick_count
        if changed and self.config.get("state_change_log_enabled", True):
            fmz_log(
                "状态变化",
                "轮次=" + str(self.tick_count),
                "结论=" + str(decision_snapshot.get("decision")),
                "方向=" + str(decision_snapshot.get("side")),
                "拒绝类型=" + str(decision_snapshot.get("reject_type")),
                "原因=" + ",".join(
                    decision_snapshot.get("reject_reasons") or []),
            )
        if changed or should_log_regular:
            runtime = decision_snapshot.get("runtime_facts") or {}
            fmz_log(
                "观察摘要",
                "轮次=" + str(self.tick_count),
                "模式=" + str(runtime.get("runtime_mode")),
                "结论=" + str(decision_snapshot.get("decision")),
                "方向=" + str(decision_snapshot.get("side")),
                "价格=" + str(runtime.get("current_price")),
                "价格来源=" + str(runtime.get("current_price_source")),
                "数据源=" + str(runtime.get("source_quality")),
            )
        if getattr(self, "last_signal_recorded", False):
            fmz_log("信号综述", build_signal_brief(
                decision_snapshot, module_results))
        self.last_decision_signature = signature

    def _fetch_live_sources(self, source_quality):
        try:
            gex_result = self.gex.fetch_latest()
            source_quality["gex"] = gex_result.get("quality")
            self.last_source_details["gex"] = {
                "quality": gex_result.get("quality"),
                "cached": bool(gex_result.get("cached")),
                "error": gex_result.get("error"),
            }
            if (gex_result.get("quality") == QUALITY_OK
                    and not gex_result.get("cached")):
                self.gex_state.ingest(gex_result.get("data"))
        except Exception as error:
            self._mark_source_error(source_quality, "gex", error)

        try:
            new_bars = self.bars.poll_with_drain()
            source_quality["binance"] = self.bars.last_cycle_metrics.get(
                "quality", QUALITY_MISSING)
            if self.bars.completed_bars:
                self._set_current_price(
                    self.bars.completed_bars[-1]["close"],
                    "binance_spot_volume_bar",
                )
            elif self.gex_state.effective:
                self._set_current_price(
                    self.gex_state.effective.get("asset_price"),
                    "gex_asset_price",
                )

            premium_index = self.binance.fetch_premium_index()
            if premium_index.get("quality") == QUALITY_OK:
                premium_facts = self.binance.normalize_premium_index(
                    premium_index.get("data"))
                self.futures_facts.update(premium_facts)
                if self.current_price is None:
                    if not self._set_current_price(
                            premium_facts.get("mark_price"),
                            "binance_futures_mark"):
                        self._set_current_price(
                            premium_facts.get("index_price"),
                            "binance_futures_index")
            self._refresh_tmvf_market_data()
            self._refresh_mdie_market_data()
            if self.current_price is None:
                depth_result = self.binance.fetch_spot_depth(
                    limit=self.config["spot_depth_limit"])
                if depth_result.get("quality") == QUALITY_OK:
                    self._set_current_price(
                        self.binance.best_mid_from_depth(
                            depth_result.get("data")),
                        "binance_spot_depth_mid",
                    )
            if new_bars:
                fmz_log("新成交量柱", len(new_bars))
            self.last_source_details["binance"] = {
                "quality": source_quality.get("binance"),
                "last_cycle_metrics": dict(self.bars.last_cycle_metrics),
                "futures_facts": dict(self.futures_facts),
                "tmvf_kline_count": len(self.tmvf_klines),
                "tmvf_funding_count": len(self.tmvf_funding_points),
                "tmvf_data_quality": self.tmvf_data_quality,
                "tmvf_data_age_ms": self._tmvf_data_age_ms(),
                "m_die_kline_count": len(self.mdie_klines),
                "m_die_data_quality": self.mdie_data_quality,
                "m_die_data_age_ms": self._mdie_data_age_ms(),
            }
        except Exception as error:
            self._mark_source_error(source_quality, "binance", error)

        try:
            if self.current_price is None:
                index_result = self.deribit.get_index_price(
                    self.config["deribit_index_name"])
                if index_result.get("quality") == QUALITY_OK:
                    index_data = index_result.get("data")
                    if isinstance(index_data, dict):
                        self._set_current_price(
                            index_data.get("index_price"),
                            "deribit_index_price",
                        )
            expiry_was_stale = self._option_expiries_stale()
            if expiry_was_stale:
                self._refresh_option_expiries()
            expiry_is_stale = self._option_expiries_stale()
            if not self.option_expiries:
                source_quality["deribit"] = QUALITY_MISSING
            elif expiry_was_stale and expiry_is_stale:
                source_quality["deribit"] = QUALITY_STALE
            else:
                source_quality["deribit"] = QUALITY_OK
            self._refresh_option_greeks()
            self.last_source_details["deribit"] = {
                "quality": source_quality.get("deribit"),
                "expiry_count": len(self.option_expiries),
                "expiry_age_ms": self._option_expiry_age_ms(),
            }
        except Exception as error:
            self._mark_source_error(source_quality, "deribit", error)

    def _mark_source_error(self, source_quality, source, error):
        source_quality[source] = QUALITY_ERROR
        self.last_source_details[source] = {
            "quality": QUALITY_ERROR,
            "error": str(error),
        }
        fmz_log("数据源异常", source, str(error))

    def _set_current_price(self, value, source):
        price = safe_float(value)
        if price is None or price <= 0:
            return False
        self.current_price = price
        self.current_price_source = source
        return True

    def _runtime_facts(self):
        data_source_manifest = build_data_source_manifest(self.config)
        return add_schema({
            "tick_count": self.tick_count,
            "uptime_ms": now_ms() - self.start_ms,
            "runtime_mode": self._runtime_mode(),
            "read_only_demo": bool(self.config.get("read_only_demo", True)),
            "live_fetch_enabled": bool(
                self.config.get("live_fetch_enabled", False)),
            "last_live_fetch_active": bool(self.last_live_fetch_active),
            "loop_sleep_ms": self.config.get("loop_sleep_ms"),
            "max_main_loops": self.config.get("max_main_loops"),
            "current_price": self.current_price,
            "current_price_source": self.current_price_source,
            "completed_bar_count": len(self.bars.completed_bars),
            "volume_bar_threshold": self.config.get("volume_bar_n"),
            "current_bar_volume": self.bars.current_volume,
            "current_bar_progress_frac": self._current_bar_progress_frac(),
            "current_bar_open": self.bars.current_open,
            "current_bar_high": self.bars.current_high,
            "current_bar_low": self.bars.current_low,
            "current_bar_close": self.bars.current_close,
            "current_bar_cvd": self.bars.current_cvd,
            "last_trade_id": self.bars.last_trade_id,
            "last_cycle_trade_count": self.bars.last_cycle_metrics.get(
                "trade_count"),
            "last_cycle_new_bar_count": self.bars.last_cycle_metrics.get(
                "bar_count"),
            "tmvf_kline_count": len(self.tmvf_klines),
            "tmvf_funding_count": len(self.tmvf_funding_points),
            "tmvf_data_age_ms": self._tmvf_data_age_ms(),
            "tmvf_next_refresh_in_ms": self._tmvf_next_refresh_in_ms(),
            "m_die_kline_count": len(self.mdie_klines),
            "m_die_data_age_ms": self._mdie_data_age_ms(),
            "m_die_next_refresh_in_ms": self._mdie_next_refresh_in_ms(),
            "macro_next_refresh_in_ms": self._macro_next_refresh_in_ms(),
            "option_expiry_count": len(self.option_expiries),
            "option_expiry_age_ms": self._option_expiry_age_ms(),
            "option_expiry_next_refresh_in_ms": (
                self._option_expiry_next_refresh_in_ms()),
            "gex_fetch_age_ms": self._gex_fetch_age_ms(),
            "gex_next_fetch_in_ms": self._gex_next_fetch_in_ms(),
            "last_tick_error": self.last_tick_error,
            "data_source_manifest": {
                "schema_name": data_source_manifest.get("schema_name"),
                "source_count": data_source_manifest.get("source_count"),
            },
            "source_quality": dict(self.last_source_quality),
            "source_details": dict(self.last_source_details),
            "chart": self.chart.snapshot(),
        }, SCHEMA_RUNTIME_FACTS, self.config)

    def _runtime_mode(self):
        if self.last_live_fetch_active:
            return "live_public_read_only"
        if self.config.get("offline_fixture_enabled", False):
            return "offline_fixture"
        return "offline_missing_sources"

    def _current_bar_progress_frac(self):
        threshold = safe_float(self.config.get("volume_bar_n"))
        if threshold is None or threshold <= 0:
            return None
        current = safe_float(self.bars.current_volume) or 0.0
        return max(0.0, min(1.0, current / threshold))

    def _refresh_tmvf_market_data(self):
        if not self._tmvf_data_stale():
            return
        quality = QUALITY_OK
        klines_result = self.binance.fetch_futures_klines(
            interval=self._tmvf_kline_interval(),
            limit=self.config["tmvf_kline_limit"],
        )
        if klines_result.get("quality") == QUALITY_OK:
            klines = []
            for row in klines_result.get("data") or []:
                parsed = self.binance.normalize_kline(row)
                if parsed:
                    klines.append(parsed)
            if klines:
                self.tmvf_klines = klines
            else:
                quality = QUALITY_MISSING
        else:
            quality = klines_result.get("quality", QUALITY_ERROR)

        end_ms = now_ms()
        start_ms = (
            end_ms
            - int(self.config["tmvf_funding_lookback_days"])
            * 24 * 60 * 60 * 1000)
        funding_result = self.binance.fetch_funding_rate(
            start_time=start_ms,
            end_time=end_ms,
            limit=self.config["tmvf_funding_limit"],
        )
        if funding_result.get("quality") == QUALITY_OK:
            funding_points = []
            for row in funding_result.get("data") or []:
                parsed = self.binance.normalize_funding_point(row)
                if parsed:
                    funding_points.append(parsed)
            funding_points.sort(key=lambda item: item.get("funding_time") or 0)
            if funding_points:
                self.tmvf_funding_points = funding_points
            elif quality == QUALITY_OK:
                quality = QUALITY_STALE
        elif quality == QUALITY_OK:
            quality = funding_result.get("quality", QUALITY_STALE)

        self.last_tmvf_refresh_ms = now_ms()
        self.tmvf_data_quality = quality

    def _refresh_mdie_market_data(self):
        if not self._mdie_data_stale():
            return
        result = self.binance.fetch_futures_klines(
            interval=self.config.get("m_die_interval", "1m"),
            limit=self.config.get("m_die_kline_limit", 40),
        )
        quality = result.get("quality", QUALITY_ERROR)
        if result.get("quality") == QUALITY_OK:
            klines = []
            for row in result.get("data") or []:
                parsed = self.binance.normalize_kline(row)
                if parsed:
                    klines.append(parsed)
            if klines:
                self.mdie_klines = klines
                quality = QUALITY_OK
            else:
                quality = QUALITY_MISSING
        self.last_mdie_refresh_ms = now_ms()
        self.mdie_data_quality = quality

    def _refresh_macro_factor(self, live, offline_fixture=False):
        if not live:
            if offline_fixture and self.last_macro_snapshot is None:
                self.last_macro_snapshot = offline_macro_pressure_snapshot(
                    self.config)
            elif self.last_macro_snapshot is None:
                self.last_macro_snapshot = compute_macro_pressure(
                    [], self.config)
            return self.last_macro_snapshot
        if self.macro_factor.is_stale():
            self.last_macro_snapshot = self.macro_factor.refresh()
        return self.last_macro_snapshot

    def _effective_macro_snapshot(self):
        if self.last_macro_snapshot is not None:
            return self.last_macro_snapshot
        if self.config.get("offline_fixture_enabled", False):
            self.last_macro_snapshot = offline_macro_pressure_snapshot(
                self.config)
        else:
            self.last_macro_snapshot = compute_macro_pressure([], self.config)
        return self.last_macro_snapshot

    def _refresh_option_greeks(self):
        if not self.config.get("edb_enabled", True):
            return
        if not (self.config.get("srd_enabled", True)
                or self.config.get("ggr_enabled", True)):
            return
        if not self._option_greeks_stale():
            return
        if not self.option_instruments or self.current_price is None:
            return
        greeks = []
        for inst in self._select_near_money_instruments():
            ticker = self.deribit.get_ticker(inst.get("instrument_name"))
            if ticker.get("quality") == QUALITY_OK:
                norm = self.deribit.normalize_ticker(ticker.get("data"), inst)
                if norm and norm.get("delta") is not None:
                    greeks.append(norm)
        if greeks:
            self.option_greeks = greeks
        self.last_option_greeks_refresh_ms = now_ms()

    def _option_greeks_stale(self):
        refresh_ms = int(
            self.config.get("deribit_option_refresh_sec", 300)) * 1000
        if refresh_ms <= 0:
            return False
        if self.last_option_greeks_refresh_ms is None:
            return True
        return now_ms() - self.last_option_greeks_refresh_ms >= refresh_ms

    def _target_expiry_ts(self):
        now = now_ms()
        future = [e for e in self.option_expiries if e and e > now]
        if not future:
            return []
        targets = []
        for hours in (self.config.get("strategy_expiry_targets_hours")
                      or [24, 48]):
            goal = now + int(float(hours) * 60 * 60 * 1000)
            nearest = min(future, key=lambda e: abs(e - goal))
            if nearest not in targets:
                targets.append(nearest)
        return targets

    def _select_near_money_instruments(self):
        price = safe_float(self.current_price) or 0.0
        each = int(self.config.get("deribit_option_strikes_each_side", 8))
        selected = []
        for expiry in self._target_expiry_ts():
            same = [i for i in self.option_instruments
                    if i.get("expiration_ts_ms") == expiry
                    and i.get("strike") is not None]
            for opt_type in ("call", "put"):
                side = [i for i in same if i.get("option_type") == opt_type]
                side.sort(key=lambda i: abs((i.get("strike") or 0.0) - price))
                selected.extend(side[:each])
        return selected

    def _update_edb_history(self, flow, skew):
        micro = (flow or {}).get("micro_flow") or {}
        for label, role in (("fast_4h", "4h"), ("slow_12h", "12h")):
            cvd_norm = safe_float((micro.get(label) or {}).get("cvd_norm"))
            if cvd_norm is not None:
                self.cvd_hist[role].append(abs(cvd_norm))
        rr = safe_float((skew or {}).get("rr_blend"))
        if rr is not None:
            self.rr_hist.append(rr)

    def _tmvf_data_stale(self):
        if not self.tmvf_klines:
            return True
        refresh_ms = int(self.config.get("tmvf_refresh_sec", 300)) * 1000
        if refresh_ms <= 0:
            return False
        if self.last_tmvf_refresh_ms is None:
            return True
        return now_ms() - self.last_tmvf_refresh_ms >= refresh_ms

    def _tmvf_data_age_ms(self):
        if self.last_tmvf_refresh_ms is None:
            return None
        return now_ms() - self.last_tmvf_refresh_ms

    def _tmvf_next_refresh_in_ms(self):
        refresh_ms = int(self.config.get("tmvf_refresh_sec", 300)) * 1000
        return self._next_due_ms(self.last_tmvf_refresh_ms, refresh_ms)

    def _mdie_data_stale(self):
        if not self.mdie_klines:
            return True
        refresh_ms = int(self.config.get("m_die_refresh_sec", 60)) * 1000
        if refresh_ms <= 0:
            return True
        if self.last_mdie_refresh_ms is None:
            return True
        return now_ms() - self.last_mdie_refresh_ms >= refresh_ms

    def _mdie_data_age_ms(self):
        if self.last_mdie_refresh_ms is None:
            return None
        return now_ms() - self.last_mdie_refresh_ms

    def _mdie_next_refresh_in_ms(self):
        refresh_ms = int(self.config.get("m_die_refresh_sec", 60)) * 1000
        return self._next_due_ms(self.last_mdie_refresh_ms, refresh_ms)

    def _macro_next_refresh_in_ms(self):
        refresh_ms = int(self.config.get("macro_refresh_sec", 3600)) * 1000
        last_refresh = getattr(self.macro_factor, "last_refresh_ms", None)
        return self._next_due_ms(last_refresh, refresh_ms)

    def _tmvf_kline_interval(self):
        hours = safe_float(self.config.get("tmvf_kline_interval_hours"))
        if hours is not None and hours > 0:
            if abs(hours - int(hours)) < 1e-9:
                return str(int(hours)) + "h"
            return str(hours) + "h"
        return self.config.get("tmvf_kline_interval", "1h")

    def _option_expiries_stale(self):
        if not self.option_expiries:
            return True
        refresh_ms = int(
            self.config.get("deribit_instruments_refresh_sec", 300)) * 1000
        if refresh_ms <= 0:
            return False
        if self.last_option_expiry_refresh_ms is None:
            return True
        return now_ms() - self.last_option_expiry_refresh_ms >= refresh_ms

    def _option_expiry_age_ms(self):
        if self.last_option_expiry_refresh_ms is None:
            return None
        return now_ms() - self.last_option_expiry_refresh_ms

    def _option_expiry_next_refresh_in_ms(self):
        refresh_ms = int(
            self.config.get("deribit_instruments_refresh_sec", 300)) * 1000
        return self._next_due_ms(self.last_option_expiry_refresh_ms, refresh_ms)

    def _gex_fetch_age_ms(self):
        last_fetch = getattr(self.gex, "last_fetch_ms", None)
        if last_fetch is None:
            return None
        return now_ms() - last_fetch

    def _gex_next_fetch_in_ms(self):
        interval_ms = int(self.config.get("gex_min_fetch_interval_ms", 0))
        last_fetch = getattr(self.gex, "last_fetch_ms", None)
        return self._next_due_ms(last_fetch, interval_ms)

    @staticmethod
    def _next_due_ms(last_ms, interval_ms):
        if interval_ms <= 0:
            return 0
        if last_ms is None:
            return 0
        age = now_ms() - last_ms
        return max(0, interval_ms - age)

    def _refresh_option_expiries(self):
        instruments_result = self.deribit.get_instruments(
            self.config["deribit_currency"], kind="option")
        if instruments_result.get("quality") != QUALITY_OK:
            return
        instruments = []
        for item in instruments_result.get("data") or []:
            parsed = self.deribit.normalize_instrument(item)
            if parsed and parsed.get("is_active") and parsed.get("state") == "open":
                instruments.append(parsed)
        if instruments:
            self.option_instruments = instruments
        expiries = self._extract_option_expiries(instruments)
        if expiries:
            self.option_expiries = expiries
            self.last_option_expiry_refresh_ms = now_ms()

    def _evaluate_anchor_for_completed_bars(self, gex_snapshot):
        std_usd = self.bars.slow_std_usd()
        if not self.bars.completed_bars:
            return evaluate_anchor(
                gex_snapshot,
                self.current_price,
                std_usd,
                self.config,
                latest_bar=None,
                nd_window=self.anchor_nd_window,
                update_window=False,
            )

        pending_bars = []
        for bar in self.bars.completed_bars:
            bar_index = bar.get("bar_index")
            if (self.last_anchor_bar_index is None
                    or bar_index > self.last_anchor_bar_index):
                pending_bars.append(bar)
        if not pending_bars:
            pending_bars = [self.bars.completed_bars[-1]]

        anchor = None
        for bar in pending_bars:
            bar_index = bar.get("bar_index")
            update_window = (
                self.last_anchor_bar_index is None
                or bar_index > self.last_anchor_bar_index)
            anchor = evaluate_anchor(
                gex_snapshot,
                self.current_price,
                std_usd,
                self.config,
                latest_bar=bar,
                nd_window=self.anchor_nd_window,
                update_window=update_window,
            )
            if anchor.get("facts", {}).get("gravity_window_updated"):
                self.last_anchor_bar_index = bar_index
            band_half = anchor.get("facts", {}).get("band_half")
            self.gex_state.update_band_reference(band_half)
        return anchor

    def _extract_option_expiries(self, instruments):
        active_now = now_ms()
        expiries = []
        min_expiry_ms = active_now + int(
            self.config.get("deribit_min_expiry_hours", 0)) * 60 * 60 * 1000
        max_expiry_ms = active_now + int(
            self.config.get("deribit_max_expiry_days", 365)) * 24 * 60 * 60 * 1000
        for item in instruments or []:
            expiry = safe_float(item.get("expiration_ts_ms"))
            if expiry is None:
                continue
            expiry = int(expiry)
            if min_expiry_ms <= expiry <= max_expiry_ms:
                expiries.append(expiry)
        return sorted(set(expiries))

    def _seed_offline_fixture(self):
        """Inject deterministic data for local smoke checks without network."""
        if (self.bars.completed_bars and self.option_expiries
                and self.gex_state.effective and self.tmvf_klines
                and self.tmvf_funding_points and self.mdie_klines
                and self.last_macro_snapshot):
            return
        base_price = 100000.0
        base_ts_ms = now_ms()
        self.bars.completed_bars.clear()
        fixture_bar_count = 180
        fixture_bar_spacing_ms = 5 * 60 * 1000
        for index in range(fixture_bar_count):
            close_price = base_price + index * 5.0
            self.bars.completed_bars.append(add_schema({
                "bar_index": index + 1,
                "open": close_price - 2.0,
                "high": close_price + 6.0,
                "low": close_price - 6.0,
                "close": close_price,
                "total_volume": self.config["volume_bar_n"],
                "cvd_delta": 1.0,
                "complete_ts_ms": (
                    base_ts_ms
                    - (fixture_bar_count - index) * fixture_bar_spacing_ms),
            }, SCHEMA_VOLUME_BAR, self.config))
        self.bars.bar_index = fixture_bar_count
        self._set_current_price(
            self.bars.completed_bars[-1]["close"],
            "offline_fixture",
        )
        self.gex_state.ingest({
            "flip_point": self.current_price - 120.0,
            "spring": 0.0,
            "source_ts_ms": base_ts_ms,
            "asset_price": self.current_price,
            "quality": QUALITY_OK,
        })
        self.option_expiries = [
            base_ts_ms + 24 * 60 * 60 * 1000,
            base_ts_ms + 48 * 60 * 60 * 1000,
            base_ts_ms + 72 * 60 * 60 * 1000,
        ]
        self.last_option_expiry_refresh_ms = base_ts_ms
        self.futures_facts = {
            "last_funding_rate": 0.0001,
            "mark_price": self.current_price,
            "index_price": self.current_price,
        }
        self.tmvf_klines = []
        kline_start = base_ts_ms - 180 * 60 * 60 * 1000
        price = base_price * 0.985
        for index in range(180):
            drift = 18.0 + (index % 7 - 3) * 2.0
            open_price = price
            close_price = open_price + drift
            high = max(open_price, close_price) + 25.0
            low = min(open_price, close_price) - 25.0
            open_time = kline_start + index * 60 * 60 * 1000
            self.tmvf_klines.append({
                "open_time": open_time,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "volume": 1000.0 + (index % 12) * 25.0,
                "close_time": open_time + 60 * 60 * 1000 - 1,
            })
            price = close_price
        self.tmvf_funding_points = []
        funding_start = base_ts_ms - 35 * 24 * 60 * 60 * 1000
        for index in range(35 * 3):
            funding_time = funding_start + index * 8 * 60 * 60 * 1000
            self.tmvf_funding_points.append({
                "funding_rate": 0.00008 + (index % 9 - 4) * 0.00001,
                "funding_time": funding_time,
                "mark_price": self.current_price,
            })
        self.last_tmvf_refresh_ms = base_ts_ms
        self.tmvf_data_quality = QUALITY_OK
        self.mdie_klines = []
        mdie_start = base_ts_ms - 40 * 60 * 1000
        mdie_price = base_price * 0.998
        for index in range(40):
            open_price = mdie_price
            close_price = open_price + 8.0 + (index % 5) * 0.8
            high = max(open_price, close_price) + 3.0
            low = min(open_price, close_price) - 3.0
            open_time = mdie_start + index * 60 * 1000
            self.mdie_klines.append({
                "open_time": open_time,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "volume": 120.0 + index,
                "close_time": open_time + 60 * 1000 - 1,
            })
            mdie_price = close_price
        self.last_mdie_refresh_ms = base_ts_ms
        self.mdie_data_quality = QUALITY_OK
        self.last_macro_snapshot = offline_macro_pressure_snapshot(
            self.config, base_ts_ms)
        self.macro_factor.last_snapshot = self.last_macro_snapshot
        self.macro_factor.last_refresh_ms = base_ts_ms
        self._seed_offline_option_greeks(base_ts_ms)

    def _seed_offline_option_greeks(self, base_ts_ms):
        """Deterministic option greeks so SRD/GGR run in the offline smoke."""
        self.option_instruments = []
        self.option_greeks = []
        price = self.current_price or 100000.0
        for expiry in self.option_expiries[:2]:
            hours = (expiry - base_ts_ms) / (60 * 60 * 1000.0)
            for step in range(-5, 6):
                strike = float(int(price + step * 500))
                moneyness = (strike - price) / price
                for opt_type in ("call", "put"):
                    if opt_type == "call":
                        delta = max(0.02, min(0.98, 0.5 - moneyness * 8.0))
                        iv = 55.0 + max(0.0, moneyness * 40.0)
                    else:
                        delta = -max(0.02, min(0.98, 0.5 + moneyness * 8.0))
                        iv = 58.0 - min(0.0, moneyness * 40.0) + 3.0
                    name = "BTC-FIX-{0}-{1}".format(
                        int(strike), "C" if opt_type == "call" else "P")
                    inst = {
                        "instrument_name": name,
                        "option_type": opt_type,
                        "strike": strike,
                        "expiration_ts_ms": expiry,
                    }
                    self.option_instruments.append(inst)
                    self.option_greeks.append({
                        "instrument_name": name,
                        "option_type": opt_type,
                        "strike": strike,
                        "delta": delta,
                        "gamma": 0.0008 * math.exp(-abs(moneyness) * 30.0),
                        "mark_iv": iv,
                        "open_interest": 100.0 + 50.0 * math.exp(
                            -abs(moneyness) * 20.0),
                        "expiration_ts_ms": expiry,
                        "hours_to_expiry": hours,
                    })
        self.last_option_greeks_refresh_ms = base_ts_ms


def run(live_fetch=None, max_loops=None):
    runtime = DemoRuntime(CONFIG)
    loops = max_loops
    if loops is None:
        loops = int(runtime.config.get("max_main_loops", 1))
    count = 0
    while True:
        count += 1
        try:
            runtime.tick(live_fetch=live_fetch)
        except Exception as error:
            runtime.last_tick_error = str(error)
            fmz_log("循环异常", "轮次=" + str(count), str(error))
            fmz_status(
                "NRD " + str(runtime.config.get("demo_version"))
                + " 只读观察\n"
                + "状态：循环异常\n"
                + "轮次：" + str(count) + "\n"
                + "错误：" + str(error) + "\n"
                + "下次重试毫秒："
                + str(runtime.config.get("error_sleep_ms")))
            if not runtime.config.get("continue_on_tick_error", True):
                raise
        if loops and count >= loops:
            break
        sleep_ms = int(runtime.config.get("loop_sleep_ms", 60000))
        if runtime.last_tick_error:
            sleep_ms = int(runtime.config.get("error_sleep_ms", sleep_ms))
        fmz_sleep(sleep_ms)


def main():
    run()


def run_offline_fixture_once():
    config = dict(CONFIG)
    config["live_fetch_enabled"] = False
    config["offline_fixture_enabled"] = True
    config["max_main_loops"] = 1
    runtime = DemoRuntime(config)
    return runtime.tick(live_fetch=False)


if __name__ == "__main__":
    main()


# ===================== 整合层：SignalEvidence 导出桥（R6，only addition） =====================
# -*- coding: utf-8 -*-
"""SignalEvidence export bridge for demo v0.2.

This module is intentionally narrow: it turns an existing signal snapshot into
the only package the execution FMZ may consume. It does not expose execution
fields such as strikes, orders, ledger state, sizing, or approval data.
"""

from copy import deepcopy
from typing import Any, Dict


def _copy_mapping(source: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = source.get(key)
    if isinstance(value, dict):
        return deepcopy(value)
    return {}


def export_signal_evidence_package(
    snapshot: Dict[str, Any],
    package_id: str,
    now_ts: int,
    ttl_sec: int = 90,
) -> Dict[str, Any]:
    """Build the signal-to-execution contract package.

    The package is deliberately assembled from an allow-list. Execution-only
    fields in the input snapshot are ignored even if present.
    """
    neutral_repair = _copy_mapping(snapshot, "neutral_repair")
    anchor = _copy_mapping(snapshot, "anchor")
    edb = _copy_mapping(snapshot, "edb")
    recommendation = _copy_mapping(snapshot, "recommendation")
    data_quality = _copy_mapping(snapshot, "data_quality")
    reject_state = _copy_mapping(snapshot, "reject_state")

    return {
        "schema_name": "SignalEvidencePackage",
        "schema_version": "nrd.integration.signal.v0.1",
        "package_id": package_id,
        "created_ts": now_ts,
        "expires_ts": now_ts + ttl_sec,
        "timing_window": {
            "neutral_repair": neutral_repair,
            "anchor": anchor,
        },
        "direction_evidence": {
            "edb": edb,
        },
        "strategy_recommendation": {
            "expiry_hours": recommendation.get("expiry_hours"),
            "side_hint": recommendation.get("side_hint"),
        },
        "pre_trade_context": {
            "ggr": _copy_mapping(snapshot, "ggr"),
            "macro": _copy_mapping(snapshot, "macro"),
            "funding": _copy_mapping(snapshot, "funding"),
        },
        "data_quality": data_quality,
        "reject_state": reject_state,
    }

