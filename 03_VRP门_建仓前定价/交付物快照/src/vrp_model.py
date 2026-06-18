# -*- coding: utf-8 -*-
"""VRP gate model primitives for the neutral-loop Deribit option workflow."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import erf, exp, log, sqrt
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PASS = "PASS"
BLOCK = "BLOCK"
DISTORTED_REVIEW = "DISTORTED_REVIEW"

# Stage-seal factor version. v1.1.0: honest window field naming (representative_vol_edge),
# window gate documented as a cheap vol-space pre-screen; candidate gate stays the
# authoritative currency full-burn. Gate decisions are unchanged from the v1.0 mass
# simulation, so the sealed 268k traversal and selected policy remain valid.
VRP_FACTOR_VERSION = "1.1.0"


@dataclass(frozen=True)
class ScenarioConfig:
    rv_weights: Tuple[float, float, float] = (0.45, 0.35, 0.20)
    low_percentile_threshold: float = 0.25
    high_percentile_threshold: float = 0.75
    low_percentile_multiplier: float = 1.25
    high_percentile_multiplier: float = 0.92
    cold_start_multiplier: float = 1.10
    min_history_days: int = 30
    term_backwardation_ratio: float = 1.18
    event_backwardation_ratio: float = 1.35
    min_window_vol_edge: float = 0.02
    min_candidate_edge_ccy: float = 0.0
    spread_round_trip_multiplier: float = 2.0
    option_fee_cap_ccy: float = 0.0003
    option_fee_rate: float = 0.125
    annualization_days: int = 365


@dataclass(frozen=True)
class WindowInput:
    window_id: str
    expiry: str
    dte_hours: float
    side: str
    front_anchor_iv: float
    atm_front_iv: Optional[float]
    term_reference_iv_5_10d: Optional[float]
    rv_24h: float
    rv_72h: float
    rv_7d: float
    rv_percentile: Optional[float]
    history_days: int


@dataclass(frozen=True)
class CandidateQuote:
    window_id: str
    side: str
    spot: float
    short_strike: float
    protection_strike: float
    dte_hours: float
    amount: float
    short_bid: float
    short_ask: float
    protection_bid: float
    protection_ask: float
    executable_short_iv: float
    executable_protection_iv: Optional[float]
    forward_vol_hurdle: float
    short_instrument: str = ""
    protection_instrument: str = ""
    short_delta: Optional[float] = None


# INTEGRATION-RECONCILE: when this module lands in the Deribit execution layer, the
# following primitives must bind to the layer's canonical implementations instead of
# these local copies, to avoid two divergent sources of truth:
#   normalise_iv      -> hedge_risk._normalise_iv
#   _norm_cdf         -> hedge_risk._norm_cdf
#   _option_fee       -> accounting.acct_option_fee_ccy
#   _spread_half_cost -> accounting.acct_spread_cost
# (See docs/VRP执行层整合收口契约_v1.1.md for the full mapping.) The standalone copies
# are kept byte-consistent with those formulas during the research/seal stage.
def normalise_iv(value: Optional[float]) -> Optional[float]:
    """Return IV as decimal, accepting Deribit percent-style or decimal inputs."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v > 3.0:
        return v / 100.0
    return v


def _normalise_rv(value: Optional[float]) -> Optional[float]:
    return normalise_iv(value)


def _weighted_average(values: Sequence[Optional[float]], weights: Sequence[float]) -> Optional[float]:
    total_weight = 0.0
    total = 0.0
    for value, weight in zip(values, weights):
        if value is None:
            continue
        total += value * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return total / total_weight


def forward_vol_hurdle(
    rv_24h: Optional[float],
    rv_72h: Optional[float],
    rv_7d: Optional[float],
    rv_percentile: Optional[float],
    history_days: int,
    config: ScenarioConfig,
) -> Tuple[Optional[float], Dict[str, Any]]:
    """Compute the scalar forward-vol hurdle from regime anchor and multipliers."""
    rvs = [_normalise_rv(rv_24h), _normalise_rv(rv_72h), _normalise_rv(rv_7d)]
    rv_regime_anchor = _weighted_average(rvs, config.rv_weights)
    if rv_regime_anchor is None:
        return None, {
            "rv_regime_anchor": None,
            "percentile_adjustment": None,
            "cold_start_multiplier": None,
            "reason_codes": ["RV_REGIME_ANCHOR_MISSING"],
        }

    reason_codes: List[str] = []
    percentile_adjustment = 1.0
    if rv_percentile is None:
        reason_codes.append("RV_PERCENTILE_MISSING")
    elif rv_percentile <= config.low_percentile_threshold:
        percentile_adjustment = config.low_percentile_multiplier
        reason_codes.append("RV_LOW_PERCENTILE_HURDLE_UP")
    elif rv_percentile >= config.high_percentile_threshold:
        percentile_adjustment = config.high_percentile_multiplier
        reason_codes.append("RV_HIGH_PERCENTILE_HURDLE_RELAXED")

    cold_start_multiplier = 1.0
    if history_days < config.min_history_days:
        cold_start_multiplier = config.cold_start_multiplier
        reason_codes.append("COLD_START_HURDLE_UP")

    hurdle = rv_regime_anchor * percentile_adjustment * cold_start_multiplier
    return hurdle, {
        "rv_regime_anchor": rv_regime_anchor,
        "percentile_adjustment": percentile_adjustment,
        "cold_start_multiplier": cold_start_multiplier,
        "forward_vol_hurdle": hurdle,
        "reason_codes": reason_codes,
    }


def assess_window(window: WindowInput, config: ScenarioConfig) -> Dict[str, Any]:
    """Assess a per-expiry/per-side VRP window gate.

    The window gate is a deliberately cheap VOL-SPACE pre-screen: front anchor IV vs the
    forward-vol hurdle, plus term-structure / data-quality routing. It does NOT compute a
    currency full-burn here -- that is the authoritative job of assess_candidate, and
    duplicating it upstream would only add delta->strike inversion noise. The window gate
    answers "is this expiry/side worth enumerating at all"; the candidate gate answers
    "does this concrete vertical survive full friction".
    """
    reason_codes: List[str] = []
    front_iv = normalise_iv(window.front_anchor_iv)
    atm_front_iv = normalise_iv(window.atm_front_iv)
    term_iv = normalise_iv(window.term_reference_iv_5_10d)
    hurdle, hurdle_meta = forward_vol_hurdle(
        window.rv_24h,
        window.rv_72h,
        window.rv_7d,
        window.rv_percentile,
        window.history_days,
        config,
    )
    reason_codes.extend(hurdle_meta.get("reason_codes") or [])

    gate = PASS
    front_to_term_state = "NORMAL"
    if front_iv is None or hurdle is None:
        gate = BLOCK
        reason_codes.append("WINDOW_DATA_MISSING")
    if term_iv and front_iv:
        ratio = front_iv / term_iv
        if ratio >= config.event_backwardation_ratio:
            front_to_term_state = "EVENT_DISTORTED"
            gate = DISTORTED_REVIEW
            reason_codes.append("FRONT_TERM_EVENT_DISTORTED")
        elif ratio >= config.term_backwardation_ratio:
            front_to_term_state = "STRESSED_BACKWARDATION"
            gate = DISTORTED_REVIEW
            reason_codes.append("FRONT_TERM_BACKWARDATION")
        elif ratio >= 0.96:
            front_to_term_state = "FLAT"

    # Vol-space pre-screen only (see docstring). Honest name: this is a vol-points edge,
    # not a currency full-burn. The authoritative ccy full-burn is in assess_candidate.
    representative_vol_edge = None
    if front_iv is not None and hurdle is not None:
        representative_vol_edge = front_iv - hurdle
        if gate == PASS and representative_vol_edge < config.min_window_vol_edge:
            gate = BLOCK
            reason_codes.append("WINDOW_VRP_EDGE_TOO_THIN")

    return {
        "window_id": window.window_id,
        "expiry": window.expiry,
        "dte_hours": window.dte_hours,
        "side": window.side,
        "main_anchor_delta": 0.30,
        "front_anchor_iv": front_iv,
        "atm_front_iv": atm_front_iv,
        "term_reference_iv_5_10d": term_iv,
        "front_to_term_state": front_to_term_state,
        "rv_regime_anchor": hurdle_meta.get("rv_regime_anchor"),
        "rv_lookback_n_days": window.history_days,
        "rv_percentile": window.rv_percentile,
        "percentile_adjustment": hurdle_meta.get("percentile_adjustment"),
        "cold_start_multiplier": hurdle_meta.get("cold_start_multiplier"),
        "forward_vol_hurdle": hurdle,
        "representative_vol_edge": representative_vol_edge,
        "window_vrp_gate": gate,
        "reason_codes": sorted(set(reason_codes)),
    }


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def black_scholes_price_usd(
    option_type: str,
    spot: float,
    strike: float,
    dte_hours: float,
    sigma: float,
    annualization_days: int = 365,
) -> float:
    """European option price in USD; zero-rate approximation for short crypto DTE."""
    if spot <= 0 or strike <= 0 or dte_hours <= 0 or sigma <= 0:
        return 0.0
    t = max(dte_hours / (24.0 * annualization_days), 1e-9)
    vol_sqrt_t = sigma * sqrt(t)
    if vol_sqrt_t <= 0:
        return 0.0
    d1 = (log(spot / strike) + 0.5 * sigma * sigma * t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    if option_type == "call":
        return max(0.0, spot * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return max(0.0, strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1))


def _option_type_for_side(side: str) -> str:
    if side == "SHORT_CALL":
        return "call"
    if side == "SHORT_PUT":
        return "put"
    raise ValueError(f"Unsupported side: {side}")


def _option_fee(price_ccy: float, amount: float, config: ScenarioConfig) -> float:
    return min(config.option_fee_cap_ccy, config.option_fee_rate * max(price_ccy, 0.0)) * amount


def _spread_half_cost(bid: float, ask: float, amount: float) -> float:
    if bid is None or ask is None or ask < bid:
        return 0.0
    return (ask - bid) / 2.0 * amount


def assess_candidate(candidate: CandidateQuote, config: ScenarioConfig) -> Dict[str, Any]:
    """Assess a concrete vertical candidate after a passing window gate."""
    reason_codes: List[str] = []
    option_type = _option_type_for_side(candidate.side)
    hurdle = normalise_iv(candidate.forward_vol_hurdle)
    short_iv = normalise_iv(candidate.executable_short_iv)
    protection_iv = normalise_iv(candidate.executable_protection_iv)
    if protection_iv is None:
        protection_iv = hurdle

    executable_net_credit = (candidate.short_bid - candidate.protection_ask) * candidate.amount
    short_hurdle = black_scholes_price_usd(
        option_type,
        candidate.spot,
        candidate.short_strike,
        candidate.dte_hours,
        hurdle or 0.0,
        config.annualization_days,
    ) / candidate.spot
    protection_hurdle = black_scholes_price_usd(
        option_type,
        candidate.spot,
        candidate.protection_strike,
        candidate.dte_hours,
        hurdle or 0.0,
        config.annualization_days,
    ) / candidate.spot
    hurdle_net_credit = (short_hurdle - protection_hurdle) * candidate.amount

    entry_exit_fees = (
        2.0 * _option_fee(candidate.short_bid, candidate.amount, config)
        + 2.0 * _option_fee(candidate.protection_ask, candidate.amount, config)
    )
    spread_reserve = config.spread_round_trip_multiplier * (
        _spread_half_cost(candidate.short_bid, candidate.short_ask, candidate.amount)
        + _spread_half_cost(candidate.protection_bid, candidate.protection_ask, candidate.amount)
    )
    full_round_trip_friction = entry_exit_fees + spread_reserve
    candidate_edge = executable_net_credit - hurdle_net_credit - full_round_trip_friction

    gate = PASS
    if short_iv is None or hurdle is None:
        gate = BLOCK
        reason_codes.append("CANDIDATE_IV_OR_HURDLE_MISSING")
    if candidate_edge <= config.min_candidate_edge_ccy:
        gate = BLOCK
        reason_codes.append("CANDIDATE_FULL_BURN_EDGE_TOO_THIN")

    return {
        "window_id": candidate.window_id,
        "instrument_pair": {
            "short": candidate.short_instrument,
            "protection": candidate.protection_instrument,
        },
        "side": candidate.side,
        "short_delta": candidate.short_delta,
        "short_dte_hours": candidate.dte_hours,
        "width": abs(candidate.protection_strike - candidate.short_strike),
        "executable_short_iv": short_iv,
        "executable_protection_iv": protection_iv,
        "forward_vol_hurdle": hurdle,
        "vertical_net_credit_at_executable_quotes": executable_net_credit,
        "vertical_net_credit_at_forward_vol_hurdle": hurdle_net_credit,
        "full_round_trip_friction": full_round_trip_friction,
        "candidate_vrp_edge_ccy": candidate_edge,
        "candidate_vrp_gate": gate,
        "vrp_residual_score": max(0.0, candidate_edge),
        "reason_codes": sorted(set(reason_codes)),
        "raw_candidate": asdict(candidate),
    }


def eligible_windows(windows: Iterable[Dict[str, Any]]) -> List[str]:
    """Return pass-through expiries; VRP filters windows but does not choose between them."""
    return [str(w["expiry"]) for w in windows if w.get("window_vrp_gate") == PASS]


def _as_float(value: Optional[Any]) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def gex_info_cross_check(
    window_assessment: Dict[str, Any],
    gex_info: Optional[Dict[str, Any]],
    config: ScenarioConfig,
) -> Dict[str, Any]:
    """Display-only cross-check of a VRP window against gexmonitorapi /v1/info.

    VRP's own Deribit-IV + self-computed-RV full-burn stays AUTHORITATIVE: this
    NEVER changes the hurdle or the gate (note applied_to_gate is always False),
    so the sealed window/candidate decisions are untouched. It only corroborates
    "are options rich?" (iv_rv_ratio vs VRP's own vol-edge) and the term-structure
    backwardation routing, and surfaces the API iv_rv_ratio as a weak external
    prior during cold start. With gex_info missing it returns
    gex_info_available=False and contributes nothing.
    """
    reason_codes: List[str] = []
    info = gex_info if isinstance(gex_info, dict) else None

    rep_edge = window_assessment.get("representative_vol_edge")
    vrp_iv_rich = rep_edge > 0.0 if isinstance(rep_edge, (int, float)) else None

    iv_rv_ratio: Optional[float] = None
    api_iv_rich: Optional[bool] = None
    iv_rv_agree: Optional[bool] = None
    api_front_back_ratio: Optional[float] = None
    term_structure_agree: Optional[bool] = None
    cold_start_prior: Optional[float] = None
    dvol: Optional[float] = None

    if info is None:
        reason_codes.append("GEX_INFO_UNAVAILABLE")
    else:
        dvol = _as_float(info.get("dvol"))
        iv_rv_ratio = _as_float(info.get("iv_rv_ratio"))
        if iv_rv_ratio is not None:
            api_iv_rich = iv_rv_ratio > 1.0
            if vrp_iv_rich is not None:
                iv_rv_agree = api_iv_rich == vrp_iv_rich
                if not iv_rv_agree:
                    reason_codes.append("GEX_INFO_IV_RV_DISAGREES")
            cold_mult = window_assessment.get("cold_start_multiplier")
            if cold_mult is not None and cold_mult != 1.0:
                cold_start_prior = iv_rv_ratio
                reason_codes.append("GEX_INFO_COLD_START_PRIOR_AVAILABLE")
        atm_ivs = [
            _as_float((item or {}).get("atm_iv"))
            for item in (info.get("term_structure") or [])
        ]
        atm_ivs = [value for value in atm_ivs if value is not None and value > 0]
        if len(atm_ivs) >= 2:
            api_front_back_ratio = atm_ivs[0] / atm_ivs[-1]
            api_backwardation = (
                api_front_back_ratio >= config.term_backwardation_ratio)
            vrp_backwardation = window_assessment.get("front_to_term_state") in (
                "STRESSED_BACKWARDATION", "EVENT_DISTORTED")
            term_structure_agree = api_backwardation == vrp_backwardation
            if not term_structure_agree:
                reason_codes.append("GEX_INFO_TERM_STRUCTURE_DISAGREES")

    return {
        "gex_info_available": info is not None,
        "applied_to_gate": False,
        "iv_rv_ratio": iv_rv_ratio,
        "dvol": dvol,
        "api_iv_rich": api_iv_rich,
        "vrp_iv_rich": vrp_iv_rich,
        "iv_rv_agree": iv_rv_agree,
        "api_front_back_iv_ratio": api_front_back_ratio,
        "term_structure_agree": term_structure_agree,
        "cold_start_external_prior_iv_rv": cold_start_prior,
        "reason_codes": sorted(set(reason_codes)),
    }
