# -*- coding: utf-8 -*-
"""
Post-entry hedge risk evaluator.

The module is deliberately pure: it produces PositionRiskPackage and optional
dry-run HedgeIntentPackage, but it never places orders or mutates the option
ledger. It uses EDB as the aggregate signal input and keeps GGR as a boundary
and persistence modifier, not as a probability predictor.
"""
import math


SCHEMA_NAME = "PositionRiskPackage"
SCHEMA_VERSION = "nrd.integration.position_risk.v0.4"

STATE_NORMAL = "NORMAL"
STATE_WATCH = "WATCH"
STATE_EXIT_PREFERRED = "EXIT_PREFERRED"
STATE_HEDGE_READY = "HEDGE_READY"
STATE_HEDGE_ACTIVE = "HEDGE_ACTIVE"
STATE_MANUAL_REVIEW = "MANUAL_REVIEW"

EXECUTION_DRY_INTENT_ONLY = "DRY_INTENT_ONLY"

PERSISTENCE_LOW = "LOW"
PERSISTENCE_MEDIUM = "MEDIUM"
PERSISTENCE_HIGH = "HIGH"

SIDE_SHORT_CALL = "SHORT_CALL"
SIDE_SHORT_PUT = "SHORT_PUT"

BUY_HEDGE = "BUY_FUTURE_OR_PERP"
SELL_HEDGE = "SELL_FUTURE_OR_PERP"

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _safe_float(v):
    try:
        if v is None:
            return None
        out = float(v)
        if not math.isfinite(out):
            return None
        return out
    except Exception:
        return None


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _normalise_iv(iv):
    vol = _safe_float(iv)
    if vol is None or vol <= 0:
        return None
    # Accept either decimal IV (0.7) or percent IV (70).
    return vol / 100.0 if vol > 3.0 else vol


def _is_short_call(direction_bias):
    return direction_bias == SIDE_SHORT_CALL


def _breached(direction_bias, price, boundary):
    if _is_short_call(direction_bias):
        return price >= boundary
    return price <= boundary


def boundary_distance_pct(direction_bias, price, loss_boundary):
    price, boundary = _safe_float(price), _safe_float(loss_boundary)
    if price is None or price <= 0 or boundary is None or boundary <= 0:
        return None
    if _is_short_call(direction_bias):
        return (boundary - price) / price * 100.0
    return (price - boundary) / price * 100.0


def estimate_touch_probability(direction_bias, price, loss_boundary,
                               dte_hours, iv=None, short_delta=None):
    """Estimate first-touch probability to the loss boundary before expiry.

    This is a risk-control estimate, not a real-world win-rate claim. IV-based
    output uses the driftless lognormal barrier approximation. Delta fallback is
    intentionally conservative and marked low-confidence by callers.
    """
    price = _safe_float(price)
    boundary = _safe_float(loss_boundary)
    dte = _safe_float(dte_hours)
    if price is None or price <= 0 or boundary is None or boundary <= 0:
        return 0.0
    if _breached(direction_bias, price, boundary):
        return 1.0
    if dte is None or dte <= 0:
        return 0.0

    vol = _normalise_iv(iv)
    if vol is not None:
        t_years = dte / (24.0 * 365.0)
        sigma_t = vol * math.sqrt(max(t_years, 1e-12))
        if sigma_t <= 1e-12:
            return 0.0
        if _is_short_call(direction_bias):
            distance = math.log(boundary / price)
        else:
            distance = math.log(price / boundary)
        if distance <= 0:
            return 1.0
        z = distance / sigma_t
        return _clamp(2.0 * (1.0 - _norm_cdf(z)), 0.0, 0.98)

    delta = _safe_float(short_delta)
    if delta is None:
        return 0.0
    return _clamp(abs(delta) * 1.8, 0.0, 0.95)


def _probability_confidence(iv):
    return "HIGH" if _normalise_iv(iv) is not None else "LOW"


def build_entry_risk_anchor(direction_bias, entry_price, entry_dte_hours,
                            entry_short_delta, entry_short_gamma, entry_iv,
                            entry_loss_boundary, entry_edb_side="",
                            entry_gamma_regime="",
                            entry_vrp_window_id="", entry_forward_vol_hurdle=None,
                            entry_candidate_vrp_edge_ccy=None,
                            entry_executable_short_iv=None, entry_vrp_reason_codes=None):
    p = estimate_touch_probability(
        direction_bias, entry_price, entry_loss_boundary, entry_dte_hours,
        entry_iv, entry_short_delta)
    return {
        "entry_price": entry_price,
        "entry_dte_hours": entry_dte_hours,
        "entry_short_delta": entry_short_delta,
        "entry_short_gamma": entry_short_gamma,
        "entry_iv": entry_iv,
        "entry_loss_boundary": entry_loss_boundary,
        "entry_touch_probability": p,
        "entry_probability_confidence": _probability_confidence(entry_iv),
        "entry_boundary_distance_pct": boundary_distance_pct(
            direction_bias, entry_price, entry_loss_boundary),
        "entry_edb_side": entry_edb_side,
        "entry_gamma_regime": entry_gamma_regime,
        # R4：VRP 入场血缘（与对冲共 IV/vol 基线；对冲只读此血缘、不反向重做 VRP）
        "entry_vrp_window_id": entry_vrp_window_id,
        "entry_forward_vol_hurdle": entry_forward_vol_hurdle,
        "entry_candidate_vrp_edge_ccy": entry_candidate_vrp_edge_ccy,
        "entry_executable_short_iv": entry_executable_short_iv,
        "entry_vrp_reason_codes": entry_vrp_reason_codes or [],
    }


def _recent_slope(current_probability, recent_history, now_ms,
                  recent_window_ms=30 * 60 * 1000):
    now = _safe_float(now_ms)
    if now is None:
        return 0.0
    usable = []
    for item in recent_history or []:
        ts = _safe_float((item or {}).get("ts_ms"))
        p = _safe_float((item or {}).get("touch_probability"))
        if ts is None or p is None:
            continue
        age = now - ts
        if 0 <= age <= recent_window_ms:
            usable.append((ts, p))
    if not usable:
        return 0.0
    ts, p0 = sorted(usable, key=lambda x: x[0])[0]
    hours = max((now - ts) / (60.0 * 60.0 * 1000.0), 1e-9)
    return (current_probability - p0) / hours


def _tail_exposure_acceleration(direction_bias, current_price, loss_boundary,
                                short_delta, short_gamma, entry_anchor):
    if _breached(direction_bias, _safe_float(current_price) or 0.0,
                 _safe_float(loss_boundary) or 0.0):
        return PERSISTENCE_HIGH
    delta = abs(_safe_float(short_delta) or 0.0)
    gamma = abs(_safe_float(short_gamma) or 0.0)
    entry_gamma = abs(_safe_float(
        (entry_anchor or {}).get("entry_short_gamma")) or 0.0)
    gamma_ratio = gamma / entry_gamma if entry_gamma > 0 else 0.0
    if delta >= 0.70 or gamma_ratio >= 2.0:
        return PERSISTENCE_HIGH
    if delta >= 0.50 or gamma_ratio >= 1.4:
        return PERSISTENCE_MEDIUM
    return PERSISTENCE_LOW


def _edb_adverse(direction_bias, edb):
    edb = edb or {}
    confidence = _safe_float(edb.get("confidence")) or 0.0
    coverage = _safe_float(edb.get("coverage"))
    if coverage is None:
        coverage = 1.0 if confidence >= 50 else 0.0
    if confidence < 50 or coverage < 0.50:
        return False
    lean = str(edb.get("lean") or edb.get("side_hint") or "").upper()
    if _is_short_call(direction_bias):
        return lean in ("BULLISH", "UP", "LONG", "SHORT_PUT", "PUT_CREDIT_SPREAD")
    return lean in ("BEARISH", "DOWN", "SHORT", "SHORT_CALL", "CALL_CREDIT_SPREAD")


def _ggr_adverse(gamma_regime):
    ggr = gamma_regime or {}
    if bool(ggr.get("veto")):
        return True
    regime = str(ggr.get("regime") or "").upper()
    dist = _safe_float(ggr.get("distance_to_flip_pct"))
    if regime == "NEGATIVE_GAMMA_AMPLIFYING":
        return dist is None or abs(dist) <= 1.0
    gate = str(((ggr.get("ggr_gate") or {}).get("regime")) or "").upper()
    return gate == "NEGATIVE_GAMMA_AMPLIFYING"


def persistence_score(direction_bias, edb=None, gamma_regime=None):
    """整合 Phase1：持续性两项制 {EDB_ADVERSE, GGR_ADVERSE}（删 KPF buffer）。
    重标定 0→LOW / 1→MEDIUM / 2→HIGH。EDB 为唯一方向证据入口、GGR 为负 Gamma 例外修正。"""
    confirmations = []
    if _edb_adverse(direction_bias, edb):
        confirmations.append("EDB_ADVERSE")
    if _ggr_adverse(gamma_regime):
        confirmations.append("GGR_ADVERSE")
    count = len(confirmations)
    if count >= 2:
        score = PERSISTENCE_HIGH
    elif count == 1:
        score = PERSISTENCE_MEDIUM
    else:
        score = PERSISTENCE_LOW
    return score, confirmations


def _friction_score(value):
    text = str(value or "").upper()
    if text in ("EXTREME", "VERY_HIGH", "BLOCKED"):
        return 4
    if text in ("HIGH", "POOR", "WIDE", "EXPENSIVE"):
        return 3
    if text in ("MEDIUM", "FAIR", "NORMAL"):
        return 2
    if text in ("LOW", "GOOD", "OK", "CHEAP"):
        return 1
    return 2


def exit_vs_hedge_friction(exit_friction):
    data = exit_friction or {}
    option_score = _friction_score(data.get("option_exit_friction"))
    hedge_score = _friction_score(data.get("future_hedge_friction"))
    hedge_preferred = option_score - hedge_score >= 1 and option_score >= 3
    return {
        "option_exit_friction": data.get("option_exit_friction"),
        "future_hedge_friction": data.get("future_hedge_friction"),
        "option_exit_score": option_score,
        "future_hedge_score": hedge_score,
        "hedge_friction_advantage": hedge_preferred,
    }


def _state_from_inputs(probability_now, drift, slope, persistence, friction,
                       existing_hedge=False):
    if existing_hedge:
        return STATE_HEDGE_ACTIVE
    risk_elevated = (
        probability_now >= 0.50 or drift >= 0.10 or slope >= 0.20)
    if not risk_elevated:
        return STATE_NORMAL
    risk_severe = (
        probability_now >= 0.65 or drift >= 0.20 or slope >= 0.30)
    if (risk_severe and persistence in (PERSISTENCE_MEDIUM, PERSISTENCE_HIGH)
            and friction.get("hedge_friction_advantage")):
        return STATE_HEDGE_READY
    if not friction.get("hedge_friction_advantage"):
        return STATE_EXIT_PREFERRED
    return STATE_WATCH


def _hedge_side(direction_bias):
    return BUY_HEDGE if _is_short_call(direction_bias) else SELL_HEDGE


def _hedge_size_mode(probability_now, drift, tail_acceleration,
                     persistence, breached):
    if breached or probability_now >= 0.80:
        return "FULL", 0.90
    if tail_acceleration == PERSISTENCE_HIGH and persistence == PERSISTENCE_HIGH:
        return "FULL", 0.90
    if probability_now >= 0.60 or drift >= 0.25 or persistence == PERSISTENCE_HIGH:
        return "MEDIUM", 0.60
    return "LIGHT", 0.30


def _make_hedge_intent(position_id, direction_bias, probability_now, drift,
                       tail_acceleration, persistence, breached):
    mode, ratio = _hedge_size_mode(
        probability_now, drift, tail_acceleration, persistence, breached)
    return {
        "schema_name": "HedgeIntentPackage",
        "schema_version": "nrd.integration.hedge_intent.v0.1",
        "position_id": position_id,
        "hedge_side": _hedge_side(direction_bias),
        "hedge_size_mode": mode,
        "target_delta_reduction_ratio": ratio,
        "hedge_instrument": "BTC-PERPETUAL",          # v2 固定工具（删 UNBOUND/Binance 场所选择）
        "hedge_venue": "DERIBIT",
        "execution_mode": EXECUTION_DRY_INTENT_ONLY,
        "reason_codes": ["DRY_INTENT_ONLY", "TAIL_RISK_HEDGE_READY"],
    }


def _manual_review_package(position_id, entry_anchor, reason):
    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "position_id": position_id,
        "entry_risk_anchor": entry_anchor or {},
        "current_risk": {
            "touch_probability_now": 0.0,
            "touch_probability_drift": 0.0,
            "recent_deterioration_slope": 0.0,
            "tail_exposure_acceleration": PERSISTENCE_LOW,
            "persistence": PERSISTENCE_LOW,
        },
        "exit_vs_hedge_friction": {},
        "tail_risk_state": STATE_MANUAL_REVIEW,
        "hedge_intent": None,
        "reason_codes": [reason],
    }


def evaluate_position_risk(position_id, direction_bias, entry_risk_anchor,
                           current_price, dte_hours, short_delta,
                           short_gamma, iv, loss_boundary, edb=None,
                           gamma_regime=None,
                           exit_friction=None, recent_history=None,
                           now_ms=None, existing_hedge=False):
    if direction_bias not in (SIDE_SHORT_CALL, SIDE_SHORT_PUT):
        return _manual_review_package(
            position_id, entry_risk_anchor, "INVALID_DIRECTION_BIAS")
    if not entry_risk_anchor or "entry_touch_probability" not in entry_risk_anchor:
        return _manual_review_package(
            position_id, entry_risk_anchor, "MISSING_ENTRY_RISK_ANCHOR")

    p_now = estimate_touch_probability(
        direction_bias, current_price, loss_boundary, dte_hours, iv,
        short_delta)
    p_entry = _safe_float(entry_risk_anchor.get("entry_touch_probability")) or 0.0
    drift = p_now - p_entry
    slope = _recent_slope(p_now, recent_history, now_ms)
    tail_acc = _tail_exposure_acceleration(
        direction_bias, current_price, loss_boundary, short_delta,
        short_gamma, entry_risk_anchor)
    persistence, confirmations = persistence_score(
        direction_bias, edb, gamma_regime)
    friction = exit_vs_hedge_friction(exit_friction)
    state = _state_from_inputs(
        p_now, drift, slope, persistence, friction, existing_hedge)
    breached = _breached(
        direction_bias, _safe_float(current_price) or 0.0,
        _safe_float(loss_boundary) or 0.0)
    hedge_intent = None
    if state == STATE_HEDGE_READY:
        hedge_intent = _make_hedge_intent(
            position_id, direction_bias, p_now, drift, tail_acc, persistence,
            breached)

    reason_codes = list(confirmations)
    if drift > 0:
        reason_codes.append("TOUCH_PROBABILITY_DRIFT_POSITIVE")
    if slope > 0:
        reason_codes.append("RECENT_DETERIORATION_SLOPE_POSITIVE")
    if friction.get("hedge_friction_advantage"):
        reason_codes.append("HEDGE_FRICTION_ADVANTAGE")

    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "position_id": position_id,
        "entry_risk_anchor": entry_risk_anchor,
        "current_risk": {
            "touch_probability_now": p_now,
            "touch_probability_drift": drift,
            "recent_deterioration_slope": slope,
            "tail_exposure_acceleration": tail_acc,
            "persistence": persistence,
        },
        "exit_vs_hedge_friction": friction,
        "tail_risk_state": state,
        "hedge_intent": hedge_intent,
        "reason_codes": reason_codes,
    }
