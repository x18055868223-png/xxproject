"""Shadow-only candidate policy evaluator, audit builder, and minimal console renderer.

All policy parameters are labeled UNVALIDATED_CANDIDATE_PARAMS and decision authority is
hard-locked to SHADOW_ONLY (live_veto_enabled=False, player_integration_enabled=False).
"""

from __future__ import annotations

import math
from typing import Any

from research.gfri_v11.config import (
    CODE_VERSION,
    DEFAULT_CONFIG,
    LOCKED_DECISION_AUTHORITY,
    LOCKED_LIVE_VETO_ENABLED,
    LOCKED_PLAYER_INTEGRATION_ENABLED,
    SCHEMA_AUDIT,
    GfriConfig,
)

VALID_TOP_DECISIONS = frozenset(
    {
        "NO_VETO",
        "SHADOW_PAUSE_PUT",
        "SHADOW_PAUSE_CALL",
        "SHADOW_PAUSE_BOTH",
        "UNKNOWN",
        "WARMING_UP",
    }
)

STATUS_DISPLAY_MAP = {
    "NO_VETO": "不阻断（候选）",
    "SHADOW_PAUSE": "候选暂缓",
    "UNKNOWN": "未知",
    "WARMING_UP": "预热中",
}

UNITS_METADATA = {
    "gfri": "ratio_x",
    "hedge_flow": "USD",
    "liquidity_depth": "USD",
    "timestamps": "epoch_ms",
    "oi_lag": "hours",
}


def _default_side_state() -> dict[str, Any]:
    return {
        "active_pause": False,
        "pause_consecutive": 0,
        "release_consecutive": 0,
        "last_decision": None,
    }


def _normalize_prior_side_state(prior_state: dict[str, Any] | None, side: str) -> dict[str, Any]:
    if not isinstance(prior_state, dict):
        return _default_side_state()
    raw = prior_state.get(side)
    if not isinstance(raw, dict):
        return _default_side_state()
    return {
        "active_pause": bool(raw.get("active_pause", False)),
        "pause_consecutive": int(raw.get("pause_consecutive", 0) or 0),
        "release_consecutive": int(raw.get("release_consecutive", 0) or 0),
        "last_decision": raw.get("last_decision"),
    }


def _is_finite_number(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(float(val))


def _evaluate_side_policy(
    side_key: str,
    confirmed_cycle: dict[str, Any],
    side_near: dict[str, Any],
    side_long: dict[str, Any],
    side_prior: dict[str, Any],
    *,
    trigger_abs: float,
    release_abs: float,
    cfg: GfriConfig,
) -> dict[str, Any]:
    side_data = confirmed_cycle.get(side_key)
    data_quality = confirmed_cycle.get("data_quality", "UNKNOWN")
    confirmation_status = confirmed_cycle.get("confirmation_status", "UNKNOWN")

    if not isinstance(side_data, dict):
        side_data = {}
    side_status = side_data.get("status", "UNKNOWN")
    gfri_raw = side_data.get("gfri_confirmed")

    was_paused = bool(side_prior.get("active_pause", False))
    prior_pause_streak = int(side_prior.get("pause_consecutive", 0) or 0)
    prior_release_streak = int(side_prior.get("release_consecutive", 0) or 0)

    reasons: list[str] = []

    if (
        data_quality == "UNKNOWN"
        or side_status == "UNKNOWN"
        or not _is_finite_number(gfri_raw)
    ):
        if data_quality == "UNKNOWN":
            reasons.append("DATA_QUALITY_UNKNOWN")
        if side_status == "UNKNOWN":
            reasons.append(f"{side_key.upper()}_STATUS_UNKNOWN")
        if not _is_finite_number(gfri_raw):
            reasons.append(f"{side_key.upper()}_GFRI_MISSING")
        return {
            "side": side_key.upper(),
            "decision": "UNKNOWN",
            "gfri_confirmed": None,
            "reason_codes": reasons,
            "next_state": {
                "active_pause": was_paused,
                "pause_consecutive": 0,
                "release_consecutive": 0,
                "last_decision": "UNKNOWN",
            },
        }

    gfri = float(gfri_raw)
    if side_near.get("state") == "WARMING_UP":
        reasons.append("NEAR_HISTORY_WARMING_UP")
        return {
            "side": side_key.upper(),
            "decision": "WARMING_UP",
            "gfri_confirmed": gfri,
            "reason_codes": reasons,
            "next_state": {
                "active_pause": False,
                "pause_consecutive": 0,
                "release_consecutive": 0,
                "last_decision": "WARMING_UP",
            },
        }

    raw_is_amp = side_data.get("is_amplifying")
    is_amplifying = (gfri > 0.0) if raw_is_amp is None else bool(raw_is_amp)
    side_conf = side_data.get("confirmation_status", confirmation_status)
    single_blip = (
        bool(side_data.get("single_blip_detected", False))
        or side_conf == "SINGLE_BLIP_FILTERED"
    )
    sample_confirmed = (side_conf == "CONFIRMED") and not single_blip
    delta_ratio = side_near.get("delta_ratio")

    if isinstance(side_long, dict) and side_long.get("regime_drift_warning"):
        reasons.append("REGIME_DRIFT_ANTI_MASKING_ACTIVE")

    # Never allow long_ref low percentile to suppress or clear SHADOW_PAUSE.
    if was_paused:
        if gfri < release_abs:
            new_release_streak = prior_release_streak + 1
            if new_release_streak >= cfg.release_consecutive_cycles:
                reasons.append("HYSTERESIS_RELEASED")
                return {
                    "side": side_key.upper(),
                    "decision": "NO_VETO",
                    "gfri_confirmed": gfri,
                    "reason_codes": reasons,
                    "next_state": {
                        "active_pause": False,
                        "pause_consecutive": 0,
                        "release_consecutive": 0,
                        "last_decision": "NO_VETO",
                    },
                }
            reasons.append("HYSTERESIS_HOLD_PENDING_RELEASE")
            return {
                "side": side_key.upper(),
                "decision": "SHADOW_PAUSE",
                "gfri_confirmed": gfri,
                "reason_codes": reasons,
                "next_state": {
                    "active_pause": True,
                    "pause_consecutive": prior_pause_streak,
                    "release_consecutive": new_release_streak,
                    "last_decision": "SHADOW_PAUSE",
                },
            }

        reasons.append("HYSTERESIS_HOLD_ABOVE_RELEASE")
        new_pause_streak = (
            prior_pause_streak + 1
            if (is_amplifying and gfri >= trigger_abs and not single_blip)
            else prior_pause_streak
        )
        return {
            "side": side_key.upper(),
            "decision": "SHADOW_PAUSE",
            "gfri_confirmed": gfri,
            "reason_codes": reasons,
            "next_state": {
                "active_pause": True,
                "pause_consecutive": new_pause_streak,
                "release_consecutive": 0,
                "last_decision": "SHADOW_PAUSE",
            },
        }

    # Not previously paused: evaluate entry rules.
    if not is_amplifying or gfri <= 0.0:
        reasons.append("POSITIVE_GAMMA_DAMPENING")
        return {
            "side": side_key.upper(),
            "decision": "NO_VETO",
            "gfri_confirmed": gfri,
            "reason_codes": reasons,
            "next_state": {
                "active_pause": False,
                "pause_consecutive": 0,
                "release_consecutive": 0,
                "last_decision": "NO_VETO",
            },
        }

    if single_blip:
        reasons.append("SINGLE_BLIP_FILTERED_NO_TRIGGER")
        return {
            "side": side_key.upper(),
            "decision": "NO_VETO",
            "gfri_confirmed": gfri,
            "reason_codes": reasons,
            "next_state": {
                "active_pause": False,
                "pause_consecutive": 0,
                "release_consecutive": 0,
                "last_decision": "NO_VETO",
            },
        }

    if gfri >= trigger_abs:
        near_persistence = (
            int(side_near.get("persistence_count_trigger", 0) or 0)
            if math.isclose(trigger_abs, cfg.trigger_abs_gfri, rel_tol=0.0, abs_tol=1e-9)
            else 0
        )
        pause_streak = max(near_persistence, prior_pause_streak + 1)
    else:
        pause_streak = 0

    sustained_hit = (
        is_amplifying
        and gfri >= trigger_abs
        and pause_streak >= cfg.trigger_consecutive_cycles
    )
    deterioration_hit = (
        sample_confirmed
        and is_amplifying
        and gfri >= cfg.elevated_abs_gfri
        and delta_ratio is not None
        and _is_finite_number(delta_ratio)
        and float(delta_ratio) >= cfg.trigger_deterioration_ratio
    )

    if sustained_hit or deterioration_hit:
        if sustained_hit:
            reasons.append("SUSTAINED_HIGH_GFRI_TRIGGER")
        if deterioration_hit:
            reasons.append("RAPID_DETERIORATION_TRIGGER")
        return {
            "side": side_key.upper(),
            "decision": "SHADOW_PAUSE",
            "gfri_confirmed": gfri,
            "reason_codes": reasons,
            "next_state": {
                "active_pause": True,
                "pause_consecutive": pause_streak,
                "release_consecutive": 0,
                "last_decision": "SHADOW_PAUSE",
            },
        }

    if gfri >= trigger_abs:
        reasons.append("BELOW_CONSECUTIVE_CONFIRMATION")
    elif gfri >= cfg.elevated_abs_gfri:
        reasons.append("ELEVATED_BELOW_TRIGGER")
    else:
        reasons.append("WITHIN_CANDIDATE_THRESHOLD")

    return {
        "side": side_key.upper(),
        "decision": "NO_VETO",
        "gfri_confirmed": gfri,
        "reason_codes": reasons,
        "next_state": {
            "active_pause": False,
            "pause_consecutive": pause_streak,
            "release_consecutive": 0,
            "last_decision": "NO_VETO",
        },
    }


def _combine_decisions(
    put_decision: str,
    call_decision: str,
) -> tuple[str, str | None]:
    if put_decision == "UNKNOWN" or call_decision == "UNKNOWN":
        partial_pause_side: str | None = None
        if put_decision == "SHADOW_PAUSE" and call_decision != "SHADOW_PAUSE":
            partial_pause_side = "PUT"
        elif call_decision == "SHADOW_PAUSE" and put_decision != "SHADOW_PAUSE":
            partial_pause_side = "CALL"
        return "UNKNOWN", partial_pause_side

    if put_decision == "SHADOW_PAUSE" and call_decision == "SHADOW_PAUSE":
        return "SHADOW_PAUSE_BOTH", None
    if put_decision == "SHADOW_PAUSE":
        return "SHADOW_PAUSE_PUT", None
    if call_decision == "SHADOW_PAUSE":
        return "SHADOW_PAUSE_CALL", None

    if put_decision == "WARMING_UP" or call_decision == "WARMING_UP":
        return "WARMING_UP", None

    return "NO_VETO", None


def evaluate_shadow_policy(
    confirmed_cycle: dict[str, Any],
    near_ref: dict[str, Any],
    long_ref: dict[str, Any],
    prior_state: dict[str, Any] | None = None,
    *,
    cfg: GfriConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Evaluate shadow-only candidate veto policy for PUT and CALL with hysteresis."""
    cfg.flags.validate_locked()

    put_near = near_ref.get("put", {}) if isinstance(near_ref, dict) else {}
    call_near = near_ref.get("call", {}) if isinstance(near_ref, dict) else {}
    put_long = long_ref.get("put", {}) if isinstance(long_ref, dict) else {}
    call_long = long_ref.get("call", {}) if isinstance(long_ref, dict) else {}

    put_eval = _evaluate_side_policy(
        "put",
        confirmed_cycle,
        put_near,
        put_long,
        _normalize_prior_side_state(prior_state, "put"),
        trigger_abs=cfg.trigger_abs_gfri,
        release_abs=cfg.release_abs_gfri,
        cfg=cfg,
    )
    call_eval = _evaluate_side_policy(
        "call",
        confirmed_cycle,
        call_near,
        call_long,
        _normalize_prior_side_state(prior_state, "call"),
        trigger_abs=cfg.trigger_abs_gfri,
        release_abs=cfg.release_abs_gfri,
        cfg=cfg,
    )

    shadow_decision, partial_pause_side = _combine_decisions(
        put_eval["decision"],
        call_eval["decision"],
    )
    assert shadow_decision in VALID_TOP_DECISIONS

    prior_sens = (
        prior_state.get("sensitivity", {})
        if isinstance(prior_state, dict) and isinstance(prior_state.get("sensitivity"), dict)
        else {}
    )
    sensitivity_comparison: dict[str, Any] = {
        "param_calibration_status": "UNVALIDATED_CANDIDATE_PARAMS",
        "presets": {},
    }
    next_sens_state: dict[str, Any] = {}

    for preset_name, preset_params in cfg.sensitivity_presets.items():
        preset_prior = prior_sens.get(preset_name)
        t_abs = float(preset_params["trigger_abs_gfri"])
        r_abs = float(preset_params["release_abs_gfri"])
        p_res = _evaluate_side_policy(
            "put",
            confirmed_cycle,
            put_near,
            put_long,
            _normalize_prior_side_state(preset_prior, "put"),
            trigger_abs=t_abs,
            release_abs=r_abs,
            cfg=cfg,
        )
        c_res = _evaluate_side_policy(
            "call",
            confirmed_cycle,
            call_near,
            call_long,
            _normalize_prior_side_state(preset_prior, "call"),
            trigger_abs=t_abs,
            release_abs=r_abs,
            cfg=cfg,
        )
        p_top, p_partial = _combine_decisions(p_res["decision"], c_res["decision"])
        sensitivity_comparison["presets"][preset_name] = {
            "param_calibration_status": "UNVALIDATED_CANDIDATE_PARAMS",
            "trigger_abs_gfri": t_abs,
            "release_abs_gfri": r_abs,
            "shadow_decision": p_top,
            "put_decision": p_res["decision"],
            "call_decision": c_res["decision"],
            "partial_pause_side": p_partial,
        }
        next_sens_state[preset_name] = {
            "put": p_res["next_state"],
            "call": c_res["next_state"],
        }

    candidate_reason_codes = {
        "put": put_eval["reason_codes"],
        "call": call_eval["reason_codes"],
    }

    caveats = [
        "no_veto_does_not_imply_safe",
        "unvalidated_candidate_params_shadow_only",
    ]
    if partial_pause_side is not None:
        caveats.append(f"partial_pause_active_on_{partial_pause_side.lower()}_during_unknown")

    return {
        "policy_version": cfg.policy_version,
        "param_calibration_status": "UNVALIDATED_CANDIDATE_PARAMS",
        "decision_authority": LOCKED_DECISION_AUTHORITY,
        "live_veto_enabled": LOCKED_LIVE_VETO_ENABLED,
        "player_integration_enabled": LOCKED_PLAYER_INTEGRATION_ENABLED,
        "shadow_decision": shadow_decision,
        "put_decision": put_eval["decision"],
        "call_decision": call_eval["decision"],
        "partial_pause_side": partial_pause_side,
        "candidate_reason_codes": candidate_reason_codes,
        "no_veto_does_not_imply_safe": True,
        "caveats": caveats,
        "sensitivity_comparison": sensitivity_comparison,
        "next_policy_state": {
            "put": put_eval["next_state"],
            "call": call_eval["next_state"],
            "sensitivity": next_sens_state,
        },
    }


def _side_ratio_summary(side_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": side_data.get("status", "UNKNOWN"),
        "gfri_confirmed": side_data.get("gfri_confirmed"),
        "gfri_fast_max": side_data.get("gfri_fast_max"),
        "gfri_fast_min": side_data.get("gfri_fast_min"),
        "gfri_latest_raw": side_data.get("gfri_latest_raw"),
        "spread_ratio": side_data.get("spread_ratio"),
        "single_blip_detected": bool(side_data.get("single_blip_detected", False)),
        "valid_count": side_data.get("valid_count"),
        "sample_count": side_data.get("sample_count"),
    }


def build_audit_record(
    confirmed_cycle: dict[str, Any],
    near_ref: dict[str, Any],
    long_ref: dict[str, Any],
    policy_result: dict[str, Any],
    *,
    git_commit: str | None = None,
    cfg: GfriConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Build deterministic Section 8 audit record (`gfri_audit_v11`)."""
    cfg.flags.validate_locked()

    provenance = confirmed_cycle.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    put_data = confirmed_cycle.get("put") if isinstance(confirmed_cycle.get("put"), dict) else {}
    call_data = confirmed_cycle.get("call") if isinstance(confirmed_cycle.get("call"), dict) else {}

    raw_input_hashes = {
        "gex_sha256": provenance.get("gex_sha256"),
        "ob_sha256": provenance.get("ob_sha256"),
        "gex_snapshot_id": provenance.get("gex_snapshot_id"),
        "gex_universe_hash": provenance.get("gex_universe_hash"),
        "sample_refs": confirmed_cycle.get("sample_refs", []),
    }

    hedge_flows = {
        "down_1pct": {
            "side": "PUT",
            "raw_hedge_flow_usd": put_data.get("raw_hedge_flow_usd"),
            "signed_ratio": put_data.get("signed_ratio"),
            "is_amplifying": put_data.get("is_amplifying"),
        },
        "up_1pct": {
            "side": "CALL",
            "raw_hedge_flow_usd": call_data.get("raw_hedge_flow_usd"),
            "signed_ratio": call_data.get("signed_ratio"),
            "is_amplifying": call_data.get("is_amplifying"),
        },
    }

    order_book = {
        "put_liquidity_depth_usd": put_data.get("liquidity_depth_usd"),
        "call_liquidity_depth_usd": call_data.get("liquidity_depth_usd"),
        "put_sample_depths_usd": put_data.get("sample_depths_usd", []),
        "call_sample_depths_usd": call_data.get("sample_depths_usd", []),
        "gex_spot_usd": provenance.get("gex_spot_usd"),
        "ob_ref_price_usd": provenance.get("ob_ref_price_usd"),
        "total_open_interest": provenance.get("total_open_interest"),
        "depth_coverage": {
            k: provenance[k]
            for k in (
                "bid_levels",
                "ask_levels",
                "deribit_depth_param",
                "ob_bid_coverage_pct",
                "ob_ask_coverage_pct",
            )
            if k in provenance
        },
    }

    ratios = {
        "put": _side_ratio_summary(put_data),
        "call": _side_ratio_summary(call_data),
    }

    reasons = list(confirmed_cycle.get("reasons", []))

    return {
        "schema": SCHEMA_AUDIT,
        "code_version": CODE_VERSION,
        "git_commit": git_commit,
        "decision_authority": LOCKED_DECISION_AUTHORITY,
        "live_veto_enabled": LOCKED_LIVE_VETO_ENABLED,
        "player_integration_enabled": LOCKED_PLAYER_INTEGRATION_ENABLED,
        "param_calibration_status": "UNVALIDATED_CANDIDATE_PARAMS",
        "config_snapshot": cfg.to_audit_dict(),
        "freeze_at_ms": confirmed_cycle.get("freeze_at_ms"),
        "primary_method": confirmed_cycle.get("primary_method", "UNKNOWN"),
        "comparability": confirmed_cycle.get("comparability"),
        "raw_input_hashes": raw_input_hashes,
        "units": dict(UNITS_METADATA),
        "timestamps": confirmed_cycle.get("timestamps", {}),
        "hedge_flows": hedge_flows,
        "order_book": order_book,
        "ratios": ratios,
        "cross_venue_ref": confirmed_cycle.get("cross_venue_ref", {}),
        "research_submetrics": confirmed_cycle.get("research_submetrics", {}),
        "near_ref": near_ref,
        "long_ref": long_ref,
        "policy_version": policy_result.get("policy_version", cfg.policy_version),
        "shadow_decision": policy_result.get("shadow_decision"),
        "candidate_reason_codes": policy_result.get("candidate_reason_codes", {}),
        "no_veto_does_not_imply_safe": policy_result.get("no_veto_does_not_imply_safe", True),
        "policy_result": policy_result,
        "data_quality": confirmed_cycle.get("data_quality", "UNKNOWN"),
        "confirmation_status": confirmed_cycle.get("confirmation_status", "UNKNOWN"),
        "failure_reasons": reasons,
        "reasons": reasons,
        # Direct side mirrors so audit records stored in ShadowLedger can be re-read by history.py.
        "put": dict(put_data),
        "call": dict(call_data),
    }


def _format_gfri_value(ratio_block: dict[str, Any], hedge_block: dict[str, Any], side_decision: str) -> str:
    if side_decision == "UNKNOWN" or ratio_block.get("status") == "UNKNOWN":
        return "N/A"
    val = ratio_block.get("gfri_confirmed")
    if not _is_finite_number(val):
        return "N/A"
    num = float(val)
    is_amp = hedge_block.get("is_amplifying")
    if num <= 0.0 or is_amp is False:
        return "+0.00x (阻尼)"
    return f"+{num:.2f}x"


def _format_near_side(label: str, side_near: dict[str, Any]) -> str:
    if not isinstance(side_near, dict) or side_near.get("state") == "WARMING_UP":
        cnt = side_near.get("sample_count", 0) if isinstance(side_near, dict) else 0
        req = side_near.get("min_near_samples", 4) if isinstance(side_near, dict) else 4
        return f"{label} 预热({cnt}/{req})"
    delta = side_near.get("delta_abs")
    driver = side_near.get("driver", "STABLE")
    if _is_finite_number(delta):
        return f"{label} {float(delta):+.2f}x({driver})"
    return f"{label} N/A({driver})"


def _format_long_side(label: str, side_long: dict[str, Any]) -> str:
    if not isinstance(side_long, dict):
        return f"{label} N/A"
    text = side_long.get("display_label") or "N/A"
    if side_long.get("regime_drift_warning"):
        return f"{label} {text}(漂移警示)"
    return f"{label} {text}"


def render_minimal_console(audit_record: dict[str, Any]) -> str:
    """Render concise <=12-line Section 8 console view without Gamma-wall/Pin/underwriting prose."""
    ratios = audit_record.get("ratios", {})
    hedge_flows = audit_record.get("hedge_flows", {})
    policy_res = audit_record.get("policy_result", {})
    near_ref = audit_record.get("near_ref", {})
    long_ref = audit_record.get("long_ref", {})

    put_dec = policy_res.get("put_decision", "UNKNOWN")
    call_dec = policy_res.get("call_decision", "UNKNOWN")

    put_val_str = _format_gfri_value(
        ratios.get("put", {}),
        hedge_flows.get("down_1pct", {}),
        put_dec,
    )
    call_val_str = _format_gfri_value(
        ratios.get("call", {}),
        hedge_flows.get("up_1pct", {}),
        call_dec,
    )

    put_status_cn = STATUS_DISPLAY_MAP.get(put_dec, "未知")
    call_status_cn = STATUS_DISPLAY_MAP.get(call_dec, "未知")

    near_summary = (
        f"{_format_near_side('PUT', near_ref.get('put', {}))} | "
        f"{_format_near_side('CALL', near_ref.get('call', {}))}"
    )
    long_summary = (
        f"{_format_long_side('PUT', long_ref.get('put', {}))} | "
        f"{_format_long_side('CALL', long_ref.get('call', {}))}"
    )
    data_quality = audit_record.get("data_quality", "UNKNOWN")

    lines = [
        "GFRI v1.1                 SHADOW",
        "",
        f"PUT  下行压力  {put_val_str}",
        f"状态：{put_status_cn}",
        "",
        f"CALL 上行压力  {call_val_str}",
        f"状态：{call_status_cn}",
        "",
        f"近期变化：{near_summary}",
        f"历史位置：{long_summary}",
        f"数据质量：{data_quality}",
    ]
    return "\n".join(lines)
