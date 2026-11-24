"""Deterministic, zero-lookahead ESF v0.1 (Episode Structural Fragility) research prototype.

First-principles decomposition for short-dated (16h-24h) BTC vertical credit spreads:
- Primary axis (B0/B1): Standardized buffer B_d = D_d / V_T under realized volatility scale
  V_T = sigma_RV * sqrt(T), with explicit ALREADY_BREACHED handling when D_d <= 0.
- Conditional modifier (B0/B1): Cross-venue-checked directional GFRI state within spatial
  proximity of the +-1% shock band, guarding against Deribit single-venue quote flicker.
- Scheme 1 (Selected for ESF v0.1): Two-Axis Structural Fragility (B_d, FeedbackState_d).
- Scheme 2 (Evaluated & Deferred): Continuous stress-adjusted buffer B_d^stress.

Authority: Strictly RESEARCH_ONLY (live_veto_enabled=False, player_integration_enabled=False).
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping, Sequence

from tools import astra_expiry_gamma_v1 as expiry_gamma
from tools import astra_session_clock_v1 as session_clock

ESF_VERSION = "esf_v0.1"
DECISION_AUTHORITY = "RESEARCH_ONLY"
LIVE_VETO_ENABLED = False
PLAYER_INTEGRATION_ENABLED = False
PARAM_CALIBRATION_STATUS = "UNVALIDATED_CANDIDATE_PARAMS"

YEAR_HOURS = 8760.0
HOUR_MS = 3_600_000
FIVE_MIN_MS = 300_000
DAY_MS = 24 * HOUR_MS
YEAR_MS = 365.0 * 86_400_000.0

DEFAULT_GFRI_MAX_AGE_MS = 1_200_000
DEFAULT_STRESS_LAMBDA = 0.35

# Candidate buffer band cutoffs (unvalidated candidate params, sigma units)
BUFFER_CRITICAL_MAX = 0.50
BUFFER_THIN_MAX = 1.00
BUFFER_MODERATE_MAX = 1.75

# Spatial relevance cutoffs for +-1% market-wide GFRI shock (log-distance units)
PROXIMITY_DIRECT_MAX = 0.02
PROXIMITY_MODERATE_MAX = 0.03

# Conditional GFRI feedback thresholds (unvalidated candidate params)
GFRI_ELEVATED_MIN = 3.0
GFRI_SURGE_MIN = 3.5
GFRI_SURGE_DELTA_RATIO_MIN = 1.30
FLICKER_DELTA_RATIO_MIN = 1.25
FLICKER_OKX_TO_DERIBIT_MIN = 2.50
FLICKER_OKX_GFRI_MAX = 2.00

# Fast-vol divergence alert cutoff
FAST_VOL_RATIO_MIN = 1.50
FAST_VOL_BUFFER_RATIO_MAX = 0.65

CANDIDATE_STATUS_BY_RANK = {
    4: "待校准 / 候选已越界",
    3: "待校准 / 候选极高脆弱",
    2: "待校准 / 候选高脆弱",
    1: "待校准 / 候选中脆弱",
    0: "待校准 / 候选低脆弱",
}

CANDIDATE_CODE_BY_RANK = {
    4: "CANDIDATE_ALREADY_BREACHED",
    3: "CANDIDATE_EXTREME_FRAGILITY",
    2: "CANDIDATE_HIGH_FRAGILITY",
    1: "CANDIDATE_MODERATE_FRAGILITY",
    0: "CANDIDATE_LOW_FRAGILITY",
}

FEEDBACK_CN = {
    "AMPLIFYING_SURGE": "放大激增",
    "ELEVATED_PRESSURE": "压力偏高",
    "QUOTE_FLICKER_SUSPECT": "单所闪崩存疑",
    "NORMAL_OR_LOW": "常态",
    "UNAVAILABLE": "不可用",
}

FEEDBACK_SEVERITY = {
    "AMPLIFYING_SURGE": 3,
    "ELEVATED_PRESSURE": 2,
    "NORMAL_OR_LOW": 1,
    "QUOTE_FLICKER_SUSPECT": 0,
    "UNAVAILABLE": 0,
}


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    val = float(value)
    return val if math.isfinite(val) else None


def _valid_side(side: str) -> str:
    norm = str(side or "").strip().lower()
    if norm not in ("put", "call"):
        raise ValueError(f"side must be 'put' or 'call', got {side!r}")
    return norm


def _iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def rv_relative_standard_error(sample_returns_n: int) -> float:
    """Asymptotic IID Gaussian relative SE of sample volatility: SE(sigma_hat)/sigma_hat ~= 1/sqrt(2(n-1))."""
    if not isinstance(sample_returns_n, int) or isinstance(sample_returns_n, bool) or sample_returns_n < 2:
        raise ValueError("sample_returns_n must be an integer >= 2")
    return 1.0 / math.sqrt(2.0 * (sample_returns_n - 1))


def compute_short_leg_distance(side: str, spot_usd: float, short_strike_usd: float) -> dict[str, Any]:
    """Signed log distance from current spot to the short strike (positive = OTM cushion).

    Put:  D_put  = ln(S / K_put)
    Call: D_call = ln(K_call / S)
    When D_d <= 0, explicitly flags boundary_state = 'ALREADY_BREACHED'.
    """
    norm_side = _valid_side(side)
    s = _finite_float(spot_usd)
    k = _finite_float(short_strike_usd)
    if s is None or s <= 0.0:
        raise ValueError("spot_usd must be finite and > 0")
    if k is None or k <= 0.0:
        raise ValueError("short_strike_usd must be finite and > 0")

    d_log = math.log(s / k) if norm_side == "put" else math.log(k / s)
    simple_pct = ((s - k) / s * 100.0) if norm_side == "put" else ((k - s) / s * 100.0)
    boundary_state = "ALREADY_BREACHED" if d_log <= 0.0 else "OTM_CUSHION"
    return {
        "side": norm_side,
        "spot_usd": s,
        "short_strike_usd": k,
        "distance_log": d_log,
        "distance_pct": d_log * 100.0,
        "simple_distance_pct": simple_pct,
        "boundary_state": boundary_state,
    }


def compute_remaining_horizon(asof_ms: int, expiry_ms: int) -> dict[str, float]:
    """Remaining time to expiry on BTC 24/7 calendar (YEAR_HOURS = 8760, YEAR_MS = 365*86400*1000)."""
    if isinstance(asof_ms, bool) or not isinstance(asof_ms, int):
        raise ValueError("asof_ms must be int milliseconds")
    if isinstance(expiry_ms, bool) or not isinstance(expiry_ms, int):
        raise ValueError("expiry_ms must be int milliseconds")
    rem_ms = max(0, expiry_ms - asof_ms)
    return {
        "remaining_ms": float(rem_ms),
        "remaining_hours": rem_ms / HOUR_MS,
        "remaining_years": rem_ms / YEAR_MS,
    }


def compute_vt_scale(rv_pct: float | None, remaining_years: float) -> float | None:
    """Remaining horizon realized volatility scale V_T = (rv_pct / 100) * sqrt(T)."""
    rv = _finite_float(rv_pct)
    t_yr = _finite_float(remaining_years)
    if rv is None or rv <= 0.0 or t_yr is None or t_yr < 0.0:
        return None
    return (rv / 100.0) * math.sqrt(t_yr)


def compute_standardized_buffer(distance_log: float, vt_scale: float | None) -> float | None:
    """Standardized structural cushion B_d = D_d / V_T in realized-sigma units.

    Note: B_d is a standardized geometric distance under sqrt(T) diffusive scaling,
    NOT a Gaussian survival probability Phi(-B_d) under jumps or volatility clustering.
    """
    d = _finite_float(distance_log)
    vt = _finite_float(vt_scale)
    if d is None or vt is None or vt <= 0.0:
        return None
    return d / vt


def compute_hourly_rv_no_lookahead(
    perp_1h_bars: Mapping[str, Any],
    *,
    asof_ms: int,
) -> dict[str, Any] | None:
    """Reuse tools.astra_expiry_gamma_v1.realized_vol with strict bar_end_ms <= asof_ms."""
    return expiry_gamma.realized_vol(perp_1h_bars, now_ms=int(asof_ms))


def compute_rv24_clock_adjusted(
    rv_24h_pct: float | None,
    *,
    last_bar_end_ms: int | None,
) -> float | None:
    """Adjust 24h realized vol by BTC intraday seasonal variance clock (astra_candidate_readings_v1)."""
    rv24 = _finite_float(rv_24h_pct)
    if rv24 is None or rv24 <= 0.0 or not isinstance(last_bar_end_ms, int) or isinstance(last_bar_end_ms, bool):
        return None
    factor = session_clock.variance_between(last_bar_end_ms - DAY_MS, last_bar_end_ms) / 24.0
    if factor <= 0.0:
        return None
    return rv24 / math.sqrt(factor)


def compute_5m_realized_vol(
    index_points: Sequence[Sequence[Any]],
    *,
    asof_ms: int,
    window_hours: float = 24.0,
    max_anchor_lag_ms: int = FIVE_MIN_MS,
) -> float | None:
    """Annualized 5-minute close-to-close RV (%) with strict no-lookahead (t <= asof_ms).

    Requires an exact contiguous 5-minute grid of n = int(round(window_hours * 12)) returns
    ending at the latest observation <= asof_ms (within max_anchor_lag_ms). Missing intervals
    return None rather than silently stretching multi-bar returns.
    """
    if isinstance(asof_ms, bool) or not isinstance(asof_ms, int):
        raise ValueError("asof_ms must be int milliseconds")
    n_intervals = int(round(float(window_hours) * 12.0))
    if n_intervals < 2:
        raise ValueError("window_hours must yield at least 2 five-minute intervals")

    by_ts: dict[int, float] = {}
    latest_ts: int | None = None
    for row in index_points or ():
        if not isinstance(row, Sequence) or len(row) < 2:
            continue
        t_raw, p_raw = row[0], row[1]
        if isinstance(t_raw, bool) or not isinstance(t_raw, (int, float)):
            continue
        ts = int(t_raw)
        if ts > asof_ms:
            continue
        price = _finite_float(p_raw)
        if price is None or price <= 0.0:
            continue
        by_ts[ts] = price
        if latest_ts is None or ts > latest_ts:
            latest_ts = ts

    if latest_ts is None or (asof_ms - latest_ts) > max_anchor_lag_ms:
        return None

    # Prefer canonical 5-minute clock boundaries (:00, :05, ..., :55 UTC) <= asof_ms when present,
    # matching closed-bar convention in astra_expiry_gamma_v1.realized_vol; fall back to latest_ts
    # only for synthetic grids not aligned to epoch 5m boundaries.
    canonical_5m = [ts for ts in by_ts if ts % FIVE_MIN_MS == 0 and (asof_ms - ts) <= max_anchor_lag_ms]
    anchor_ts = max(canonical_5m) if canonical_5m else latest_ts

    grid = [anchor_ts - k * FIVE_MIN_MS for k in range(n_intervals, -1, -1)]
    if not all(ts in by_ts for ts in grid):
        return None

    prices = [by_ts[ts] for ts in grid]
    rets = [math.log(b / a) for a, b in zip(prices, prices[1:])]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * (YEAR_HOURS * 12.0)) * 100.0


def classify_buffer_band(distance_log: float, buffer_sigma: float | None) -> str:
    """Primary axis structural band on D_d and B_d = D_d / V_T."""
    if distance_log <= 0.0:
        return "ALREADY_BREACHED"
    if buffer_sigma is None:
        return "UNAVAILABLE"
    if buffer_sigma < BUFFER_CRITICAL_MAX:
        return "CRITICAL"
    if buffer_sigma < BUFFER_THIN_MAX:
        return "THIN"
    if buffer_sigma < BUFFER_MODERATE_MAX:
        return "MODERATE"
    return "WIDE"


def classify_spatial_relevance(distance_log: float) -> str:
    """Spatial relevance of market-wide +-1% GFRI shock to a short strike at distance D_d."""
    if distance_log <= 0.0:
        return "ALREADY_BREACHED"
    if distance_log <= PROXIMITY_DIRECT_MAX:
        return "DIRECT_PROXIMITY"
    if distance_log <= PROXIMITY_MODERATE_MAX:
        return "MODERATE_PROXIMITY"
    return "DISTANT_FROM_1PCT_SHOCK"


def align_gfri_no_lookahead(
    gfri_records: Sequence[Mapping[str, Any]],
    asof_ms: int,
    side: str,
    *,
    max_age_ms: int = DEFAULT_GFRI_MAX_AGE_MS,
) -> dict[str, Any] | None:
    """Select the latest valid/degraded GFRI cycle with freeze_at_ms <= asof_ms within max_age_ms."""
    norm_side = _valid_side(side)
    if isinstance(asof_ms, bool) or not isinstance(asof_ms, int):
        raise ValueError("asof_ms must be int milliseconds")

    best_rec: Mapping[str, Any] | None = None
    best_ts: int = -1
    for rec in gfri_records or ():
        if not isinstance(rec, Mapping):
            continue
        frz = rec.get("freeze_at_ms")
        if isinstance(frz, bool) or not isinstance(frz, int):
            continue
        if frz > asof_ms or (asof_ms - frz) > max_age_ms:
            continue
        side_block = rec.get(norm_side)
        if not isinstance(side_block, Mapping):
            continue
        if side_block.get("status") not in ("VALID", "DEGRADED"):
            continue
        if _finite_float(side_block.get("gfri_confirmed")) is None:
            continue
        if frz > best_ts:
            best_ts = frz
            best_rec = rec

    if best_rec is None:
        return None
    return {
        "record": dict(best_rec),
        "freeze_at_ms": best_ts,
        "freeze_iso": best_rec.get("freeze_iso"),
        "age_ms": asof_ms - best_ts,
    }


def evaluate_feedback_state(
    gfri_record: Mapping[str, Any] | None,
    *,
    side: str,
    distance_log: float,
    age_ms: int | None = None,
) -> dict[str, Any]:
    """Evaluate directional Gamma feedback state with OKX cross-venue quote-flicker check."""
    norm_side = _valid_side(side)
    spatial = classify_spatial_relevance(distance_log)
    if not isinstance(gfri_record, Mapping):
        return {
            "side": norm_side,
            "feedback_state": "UNAVAILABLE",
            "feedback_cn": FEEDBACK_CN["UNAVAILABLE"],
            "gfri_confirmed": None,
            "raw_hedge_flow_usd": None,
            "liquidity_depth_usd": None,
            "near_delta_ratio": None,
            "near_driver": None,
            "okx_to_deribit_ratio": None,
            "okx_gfri_1pct": None,
            "deribit_quote_flicker_suspect": False,
            "spatial_relevance": spatial,
            "age_ms": age_ms,
            "gfri_freeze_iso": None,
        }

    sb = gfri_record.get(norm_side) if isinstance(gfri_record.get(norm_side), Mapping) else {}
    gfri_val = _finite_float(sb.get("gfri_confirmed"))
    if sb.get("status") not in ("VALID", "DEGRADED") or gfri_val is None:
        return {
            "side": norm_side,
            "feedback_state": "UNAVAILABLE",
            "feedback_cn": FEEDBACK_CN["UNAVAILABLE"],
            "gfri_confirmed": None,
            "raw_hedge_flow_usd": None,
            "liquidity_depth_usd": None,
            "near_delta_ratio": None,
            "near_driver": None,
            "okx_to_deribit_ratio": None,
            "okx_gfri_1pct": None,
            "deribit_quote_flicker_suspect": False,
            "spatial_relevance": spatial,
            "age_ms": age_ms,
            "gfri_freeze_iso": gfri_record.get("freeze_iso"),
        }

    nr = gfri_record.get("near_ref") if isinstance(gfri_record.get("near_ref"), Mapping) else {}
    delta_ratio = _finite_float(nr.get(f"delta_ratio_{norm_side}"))
    driver = nr.get(f"driver_{norm_side}")

    okx = gfri_record.get("okx_ref")
    if not isinstance(okx, Mapping):
        okx = gfri_record.get("cross_venue_ref") if isinstance(gfri_record.get("cross_venue_ref"), Mapping) else {}
    dir_key = "down" if norm_side == "put" else "up"
    okx_ratio = _finite_float(okx.get(f"ratio_okx_to_deribit_{dir_key}"))
    okx_gfri = _finite_float(okx.get(f"gfri_{dir_key}_1pct_okx"))
    okx_depth = _finite_float(okx.get(f"{dir_key}_usd"))

    # Deribit single-venue quote-pull guard:
    # Deribit +-1% depth drops sharply while OKX +-1% depth stays >= 2.5x Deribit and calm.
    flicker_suspect = bool(
        gfri_val >= GFRI_ELEVATED_MIN
        and driver == "LIQUIDITY_DROP"
        and (delta_ratio is not None and delta_ratio >= FLICKER_DELTA_RATIO_MIN)
        and (okx_ratio is not None and okx_ratio >= FLICKER_OKX_TO_DERIBIT_MIN)
        and (okx_gfri is not None and okx_gfri < FLICKER_OKX_GFRI_MAX)
    )

    if flicker_suspect:
        state = "QUOTE_FLICKER_SUSPECT"
    elif (
        gfri_val >= GFRI_SURGE_MIN
        and delta_ratio is not None
        and delta_ratio >= GFRI_SURGE_DELTA_RATIO_MIN
    ):
        state = "AMPLIFYING_SURGE"
    elif gfri_val >= GFRI_ELEVATED_MIN:
        state = "ELEVATED_PRESSURE"
    else:
        state = "NORMAL_OR_LOW"

    return {
        "side": norm_side,
        "feedback_state": state,
        "feedback_cn": FEEDBACK_CN[state],
        "gfri_confirmed": gfri_val,
        "raw_hedge_flow_usd": _finite_float(sb.get("raw_hedge_flow_usd")),
        "liquidity_depth_usd": _finite_float(sb.get("liquidity_depth_usd")),
        "near_delta_ratio": delta_ratio,
        "near_driver": driver,
        "okx_depth_usd": okx_depth,
        "okx_to_deribit_ratio": okx_ratio,
        "okx_gfri_1pct": okx_gfri,
        "deribit_quote_flicker_suspect": flicker_suspect,
        "spatial_relevance": spatial,
        "age_ms": age_ms,
        "gfri_freeze_iso": gfri_record.get("freeze_iso"),
    }


def evaluate_scheme1_two_axis(
    *,
    distance_log: float,
    buffer_sigma: float | None,
    feedback_info: Mapping[str, Any],
    buffer_rv4h_5m_sigma: float | None = None,
    rv_24h_pct: float | None = None,
    rv_4h_5m_pct: float | None = None,
) -> dict[str, Any]:
    """Selected ESF v0.1 Scheme 1: Two-Axis Structural Fragility ESF = (B_d, FeedbackState_d).

    Invariants:
    1. D_d <= 0 -> always '待校准 / 候选已越界'.
    2. 0 < B_d < 0.50 sigma -> always '待校准 / 候选极高脆弱' even when GFRI is low/unavailable.
    3. High bilateral GFRI with WIDE B_d (>= 1.75 sigma) upgrades caution by at most +1 step
       when within spatial proximity (D_d <= 3%), and +0 steps when distant (D_d > 3%).
    """
    band = classify_buffer_band(distance_log, buffer_sigma)
    fb_state = str(feedback_info.get("feedback_state") or "UNAVAILABLE")
    spatial = str(feedback_info.get("spatial_relevance") or classify_spatial_relevance(distance_log))
    in_proximity = spatial in ("DIRECT_PROXIMITY", "MODERATE_PROXIMITY")

    base_rank_map = {
        "ALREADY_BREACHED": 4,
        "CRITICAL": 3,
        "THIN": 2,
        "MODERATE": 1,
        "WIDE": 0,
    }
    if band not in base_rank_map:
        return {
            "scheme": "SCHEME_1_TWO_AXIS",
            "selected_for_v01": True,
            "buffer_band": band,
            "feedback_state": fb_state,
            "spatial_relevance": spatial,
            "base_rank": None,
            "final_rank": None,
            "conditional_upgrade_applied": False,
            "candidate_code": "CANDIDATE_UNAVAILABLE",
            "candidate_status_cn": "待校准 / 数据缺失",
            "fast_vol_alert": False,
            "lexicographic_key": None,
        }

    base_rank = base_rank_map[band]
    upgrade = 0
    if band in ("WIDE", "MODERATE") and in_proximity and fb_state in ("AMPLIFYING_SURGE", "ELEVATED_PRESSURE"):
        upgrade = 1
    elif band == "THIN" and in_proximity and fb_state == "AMPLIFYING_SURGE":
        upgrade = 1

    final_rank = min(3, base_rank + upgrade) if base_rank < 4 else 4

    fast_vol_alert = False
    if (
        distance_log > 0.0
        and buffer_sigma is not None
        and buffer_rv4h_5m_sigma is not None
        and rv_24h_pct is not None
        and rv_4h_5m_pct is not None
    ):
        fast_vol_alert = (
            rv_4h_5m_pct >= FAST_VOL_RATIO_MIN * rv_24h_pct
            and buffer_rv4h_5m_sigma <= FAST_VOL_BUFFER_RATIO_MAX * buffer_sigma
        )

    fb_sev = FEEDBACK_SEVERITY.get(fb_state, 0) if (in_proximity or band == "ALREADY_BREACHED") else 0
    b_sort = float(buffer_sigma) if buffer_sigma is not None else 0.0
    return {
        "scheme": "SCHEME_1_TWO_AXIS",
        "selected_for_v01": True,
        "buffer_band": band,
        "feedback_state": fb_state,
        "spatial_relevance": spatial,
        "base_rank": base_rank,
        "final_rank": final_rank,
        "conditional_upgrade_applied": upgrade > 0,
        "conditional_pressure_flag": bool(in_proximity and fb_state in ("AMPLIFYING_SURGE", "ELEVATED_PRESSURE")),
        "candidate_code": CANDIDATE_CODE_BY_RANK[final_rank],
        "candidate_status_cn": CANDIDATE_STATUS_BY_RANK[final_rank],
        "fast_vol_alert": fast_vol_alert,
        "lexicographic_key": (final_rank, -b_sort, fb_sev),
    }


def evaluate_scheme2_stress_buffer(
    distance_log: float,
    vt_scale: float | None,
    gfri_confirmed: float | None,
    *,
    stress_lambda: float = DEFAULT_STRESS_LAMBDA,
) -> dict[str, Any]:
    """Evaluated & Deferred Scheme 2: Continuous Stress-Adjusted Buffer B_d^stress.

    V_T^stress(d) = V_T * [1 + lambda * exp(-max(0, D_d - 0.01) / 0.01) * ln(1 + max(0, GFRI_d - 1))]
    B_d^stress    = D_d / V_T^stress(d)

    Strictly monotone in D_d (increasing), V_T (decreasing for D_d > 0), and GFRI_d (decreasing
    for D_d > 0, GFRI_d > 1). Deferred for v0.1 because lambda cannot be calibrated without
    overfitting on N=3 live-GFRI official Dossiers and raw GFRI injects Deribit 10m book flicker.
    """
    d = _finite_float(distance_log)
    vt = _finite_float(vt_scale)
    lam = _finite_float(stress_lambda)
    if lam is None or lam < 0.0:
        raise ValueError("stress_lambda must be finite and >= 0")
    if d is None or vt is None or vt <= 0.0:
        return {
            "scheme": "SCHEME_2_STRESS_ADJUSTED_BUFFER",
            "status_scope": "EVALUATED_AND_DEFERRED_FOR_V01",
            "stress_lambda": lam,
            "spatial_decay": None,
            "stress_multiplier": None,
            "vt_stress_pct": None,
            "buffer_stress_sigma": None,
        }

    g = _finite_float(gfri_confirmed)
    spatial_decay = math.exp(-max(0.0, d - 0.01) / 0.01)
    gfri_excess = max(0.0, (g if g is not None else 1.0) - 1.0)
    multiplier = 1.0 + lam * spatial_decay * math.log(1.0 + gfri_excess)
    vt_stress = vt * multiplier
    return {
        "scheme": "SCHEME_2_STRESS_ADJUSTED_BUFFER",
        "status_scope": "EVALUATED_AND_DEFERRED_FOR_V01",
        "stress_lambda": lam,
        "spatial_decay": spatial_decay,
        "stress_multiplier": multiplier,
        "vt_stress_pct": vt_stress * 100.0,
        "buffer_stress_sigma": d / vt_stress,
    }


def evaluate_side_esf(
    *,
    side: str,
    spot_usd: float,
    short_strike_usd: float,
    long_strike_usd: float | None = None,
    asof_ms: int,
    expiry_ms: int,
    rv_24h_pct: float | None,
    rv_7d_pct: float | None = None,
    last_bar_end_ms: int | None = None,
    index_points: Sequence[Sequence[Any]] | None = None,
    index_asof_ms: int | None = None,
    gfri_records: Sequence[Mapping[str, Any]] | None = None,
    gfri_asof_ms: int | None = None,
    gfri_max_age_ms: int = DEFAULT_GFRI_MAX_AGE_MS,
    short_abs_delta: float | None = None,
    short_mark_iv_pct: float | None = None,
) -> dict[str, Any]:
    """Compute complete zero-lookahead ESF v0.1 diagnostics for one vertical credit spread side."""
    dist = compute_short_leg_distance(side, spot_usd, short_strike_usd)
    horizon = compute_remaining_horizon(asof_ms, expiry_ms)
    rem_years = horizon["remaining_years"]

    vt_rv24 = compute_vt_scale(rv_24h_pct, rem_years)
    b_rv24 = compute_standardized_buffer(dist["distance_log"], vt_rv24)

    vt_rv7d = compute_vt_scale(rv_7d_pct, rem_years)
    b_rv7d = compute_standardized_buffer(dist["distance_log"], vt_rv7d)

    rv24_clk = compute_rv24_clock_adjusted(rv_24h_pct, last_bar_end_ms=last_bar_end_ms)
    vt_clk = compute_vt_scale(rv24_clk, rem_years)
    b_clk = compute_standardized_buffer(dist["distance_log"], vt_clk)

    idx_ts = int(index_asof_ms) if index_asof_ms is not None else int(asof_ms)
    rv_4h_5m = (
        compute_5m_realized_vol(index_points, asof_ms=idx_ts, window_hours=4.0)
        if index_points is not None
        else None
    )
    rv_24h_5m = (
        compute_5m_realized_vol(index_points, asof_ms=idx_ts, window_hours=24.0)
        if index_points is not None
        else None
    )
    vt_4h_5m = compute_vt_scale(rv_4h_5m, rem_years)
    b_4h_5m = compute_standardized_buffer(dist["distance_log"], vt_4h_5m)
    vt_24h_5m = compute_vt_scale(rv_24h_5m, rem_years)
    b_24h_5m = compute_standardized_buffer(dist["distance_log"], vt_24h_5m)

    align_ts = int(gfri_asof_ms) if gfri_asof_ms is not None else int(asof_ms)
    aligned = (
        align_gfri_no_lookahead(gfri_records, align_ts, dist["side"], max_age_ms=gfri_max_age_ms)
        if gfri_records is not None
        else None
    )
    fb = evaluate_feedback_state(
        aligned["record"] if aligned else None,
        side=dist["side"],
        distance_log=dist["distance_log"],
        age_ms=aligned["age_ms"] if aligned else None,
    )

    scheme1 = evaluate_scheme1_two_axis(
        distance_log=dist["distance_log"],
        buffer_sigma=b_rv24,
        feedback_info=fb,
        buffer_rv4h_5m_sigma=b_4h_5m,
        rv_24h_pct=_finite_float(rv_24h_pct),
        rv_4h_5m_pct=rv_4h_5m,
    )
    scheme2 = evaluate_scheme2_stress_buffer(
        dist["distance_log"],
        vt_rv24,
        fb["gfri_confirmed"],
    )

    iv_val = _finite_float(short_mark_iv_pct)
    rv_val = _finite_float(rv_24h_pct)
    return {
        "schema": ESF_VERSION,
        "decision_authority": DECISION_AUTHORITY,
        "live_veto_enabled": LIVE_VETO_ENABLED,
        "player_integration_enabled": PLAYER_INTEGRATION_ENABLED,
        "param_calibration_status": PARAM_CALIBRATION_STATUS,
        "side": dist["side"],
        "asof_ms": int(asof_ms),
        "expiry_ms": int(expiry_ms),
        "spot_usd": dist["spot_usd"],
        "short_strike_usd": dist["short_strike_usd"],
        "long_strike_usd": _finite_float(long_strike_usd),
        "distance_log": dist["distance_log"],
        "distance_pct": dist["distance_pct"],
        "simple_distance_pct": dist["simple_distance_pct"],
        "boundary_state": dist["boundary_state"],
        "remaining_hours": horizon["remaining_hours"],
        "remaining_years": rem_years,
        "vt_pct": vt_rv24 * 100.0 if vt_rv24 is not None else None,
        "buffer_sigma": b_rv24,
        "multi_window_rv": {
            "rv_24h_1h_pct": rv_val,
            "rv_24h_1h_se_rel": rv_relative_standard_error(24),
            "buffer_rv24_1h_sigma": b_rv24,
            "rv_7d_1h_pct": _finite_float(rv_7d_pct),
            "rv_7d_1h_se_rel": rv_relative_standard_error(168),
            "buffer_rv7d_1h_sigma": b_rv7d,
            "rv24_clock_adjusted_pct": rv24_clk,
            "buffer_rv24_clock_sigma": b_clk,
            "rv_4h_5m_pct": rv_4h_5m,
            "rv_4h_5m_se_rel": rv_relative_standard_error(48),
            "buffer_rv4h_5m_sigma": b_4h_5m,
            "rv_24h_5m_pct": rv_24h_5m,
            "rv_24h_5m_se_rel": rv_relative_standard_error(288),
            "buffer_rv24h_5m_sigma": b_24h_5m,
        },
        "option_comparators": {
            "short_abs_delta": _finite_float(short_abs_delta),
            "short_mark_iv_pct": iv_val,
            "iv_minus_rv24_vol_pts": (iv_val - rv_val) if (iv_val is not None and rv_val is not None) else None,
        },
        "feedback": fb,
        "scheme1_two_axis": scheme1,
        "scheme2_stress_buffer": scheme2,
    }


def evaluate_episode_esf(
    *,
    episode_label: str,
    freeze_ms: int,
    expiry_ms: int,
    put_side: Mapping[str, Any],
    call_side: Mapping[str, Any],
    index_points: Sequence[Sequence[Any]] | None = None,
    gfri_records: Sequence[Mapping[str, Any]] | None = None,
    gfri_max_age_ms: int = DEFAULT_GFRI_MAX_AGE_MS,
) -> dict[str, Any]:
    """Evaluate paired PUT and CALL ESF v0.1 state for a dealt Episode / Dossier."""
    sides_out: dict[str, dict[str, Any]] = {}
    for side_key, side_payload in (("put", put_side), ("call", call_side)):
        rv_block = ((side_payload.get("expiry_vol") or {}).get("realized_vol") or {})
        sel_block = side_payload.get("selection") or {}
        short_grk = side_payload.get("short_greeks") or {}
        abs_delta = _finite_float(sel_block.get("short_abs_delta"))
        if abs_delta is None and _finite_float(short_grk.get("delta")) is not None:
            abs_delta = abs(float(short_grk["delta"]))
        sides_out[side_key] = evaluate_side_esf(
            side=side_key,
            spot_usd=float(side_payload["economics"]["valuation_spot_usd"]),
            short_strike_usd=float(side_payload["contract"]["short_strike_usd"]),
            long_strike_usd=_finite_float(side_payload["contract"].get("long_strike_usd")),
            asof_ms=int(side_payload["asof_ms"]),
            expiry_ms=int(expiry_ms),
            rv_24h_pct=_finite_float(rv_block.get("rv_24h_pct")),
            rv_7d_pct=_finite_float(rv_block.get("rv_7d_pct")),
            last_bar_end_ms=rv_block.get("last_bar_end_ms"),
            index_points=index_points,
            index_asof_ms=int(freeze_ms),
            gfri_records=gfri_records,
            gfri_asof_ms=int(freeze_ms),
            gfri_max_age_ms=gfri_max_age_ms,
            short_abs_delta=abs_delta,
            short_mark_iv_pct=_finite_float(side_payload.get("short_mark_iv")),
        )

    put_spot = sides_out["put"]["spot_usd"]
    call_spot = sides_out["call"]["spot_usd"]
    ref_spot = 0.5 * (put_spot + call_spot)
    rem_hours = max(0.0, (int(expiry_ms) - int(freeze_ms)) / HOUR_MS)
    return {
        "schema": ESF_VERSION,
        "episode_label": episode_label,
        "freeze_ms": int(freeze_ms),
        "freeze_utc": _iso_utc(int(freeze_ms)),
        "expiry_ms": int(expiry_ms),
        "expiry_utc": _iso_utc(int(expiry_ms)),
        "reference_spot_usd": ref_spot,
        "remaining_hours": rem_hours,
        "decision_authority": DECISION_AUTHORITY,
        "live_veto_enabled": LIVE_VETO_ENABLED,
        "player_integration_enabled": PLAYER_INTEGRATION_ENABLED,
        "param_calibration_status": PARAM_CALIBRATION_STATUS,
        "put": sides_out["put"],
        "call": sides_out["call"],
    }


def render_esf_card(episode_esf: Mapping[str, Any]) -> str:
    """Render clean Astra reader-style ESF v0.1 card (<= 14 lines, strictly RESEARCH_ONLY)."""
    put_s = episode_esf["put"]
    call_s = episode_esf["call"]
    spot = float(episode_esf["reference_spot_usd"])
    rem_h = float(episode_esf["remaining_hours"])
    exp_utc = str(episode_esf.get("expiry_utc") or _iso_utc(int(episode_esf["expiry_ms"])))

    def _side_lines(label: str, dir_cn: str, s: Mapping[str, Any]) -> tuple[str, str]:
        sk = float(s["short_strike_usd"])
        lk = s.get("long_strike_usd")
        strike_txt = f"{sk:.0f}/{lk:.0f}" if lk is not None else f"{sk:.0f}"
        d_pct = float(s["distance_pct"])
        d_txt = f"{d_pct:+.2f}%" + (" [已越界]" if s["boundary_state"] == "ALREADY_BREACHED" else "")

        b_val = s.get("buffer_sigma")
        b_txt = f"{b_val:+.2f}σ" if b_val is not None else "N/A"
        s1 = s["scheme1_two_axis"]
        b4 = s["multi_window_rv"].get("buffer_rv4h_5m_sigma")
        if s1.get("fast_vol_alert") and b4 is not None:
            b_txt += f" (快变RV4h {b4:+.2f}σ!)"

        fb = s["feedback"]
        g_val = fb.get("gfri_confirmed")
        g_txt = f"{g_val:.2f}x ({fb['feedback_cn']})" if g_val is not None else "不可用"
        line1 = f"{label:<4s}  {strike_txt:<11s} 短腿距离 {d_txt}  |  缓冲 {b_txt}  |  {dir_cn} GFRI {g_txt}"
        line2 = f"结构状态：{s1['candidate_status_cn']}"
        return line1, line2

    p1, p2 = _side_lines("PUT", "下行", put_s)
    c1, c2 = _side_lines("CALL", "上行", call_s)

    lines = [
        f"ESF v0.1 牌局结构脆弱度           {episode_esf['decision_authority']}",
        "",
        f"标的现价：{spot:,.2f} USD  |  剩余时间：{rem_h:.2f}h (到期 {exp_utc})",
        "",
        p1,
        p2,
        "",
        c1,
        c2,
        "",
        (
            f"模型权限：{episode_esf['decision_authority']}"
            f"（live_veto={episode_esf['live_veto_enabled']}"
            f" | player_integration={episode_esf['player_integration_enabled']}"
            " | 阈值未校准）"
        ),
    ]
    return "\n".join(lines)
