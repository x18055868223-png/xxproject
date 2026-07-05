# -*- coding: utf-8 -*-
"""Manual audit gate context helpers."""
import hashlib
import json

SCHEMA_NAME = "ManualExecutionContext"
SCHEMA_VERSION = "nrd.execution.manual_context.v1"


def _hash(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _pair(value, default=(0, 0)):
    try:
        a, b = value
        return a, b
    except Exception:
        return default


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def manual_context_hash(ctx):
    ctx = ctx or {}
    material = {
        "schema_name": ctx.get("schema_name"),
        "schema_version": ctx.get("schema_version"),
        "context_id": ctx.get("context_id"),
        "created_ts_ms": ctx.get("created_ts_ms"),
        "expires_ts_ms": ctx.get("expires_ts_ms"),
        "operator_decision": ctx.get("operator_decision"),
        "direction_bias": ctx.get("direction_bias"),
        "audit_reference": ctx.get("audit_reference") or {},
        "planning_scope": ctx.get("planning_scope") or {},
        "risk_policy": ctx.get("risk_policy") or {},
    }
    return _hash(material)


def manual_config_signature(planning_allowed, direction_bias, dte_hours, delta_range,
                            width_range, amount, audit_card_id, audit_note, ttl_min,
                            risk_policy=None):
    dte_min, dte_max = _pair(dte_hours)
    delta_min, delta_max = _pair(delta_range)
    width_min, width_max = _pair(width_range)
    return _hash({
        "planning_allowed": bool(planning_allowed),
        "direction_bias": direction_bias,
        "dte_hours": [dte_min, dte_max],
        "short_delta": [delta_min, delta_max],
        "protection_width": [width_min, width_max],
        "amount": amount,
        "audit_card_id": str(audit_card_id or "").strip(),
        "audit_note": str(audit_note or "").strip(),
        "ttl_min": ttl_min,
        "risk_policy": risk_policy or {},
    })


def build_manual_context(now_ms, planning_allowed, direction_bias, dte_hours, delta_range,
                         width_range, amount, audit_card_id, audit_note, ttl_min,
                         risk_policy=None):
    dte_min, dte_max = _pair(dte_hours)
    delta_min, delta_max = _pair(delta_range)
    width_min, width_max = _pair(width_range)
    sig = manual_config_signature(
        planning_allowed, direction_bias, dte_hours, delta_range, width_range,
        amount, audit_card_id, audit_note, ttl_min, risk_policy)
    ttl_ms = int((ttl_min or 0) * 60 * 1000) if _num(ttl_min) else 0
    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "context_id": "manual-%s" % sig[:12],
        "config_signature": sig,
        "created_ts_ms": now_ms,
        "expires_ts_ms": now_ms + ttl_ms,
        "operator_decision": "APPROVE_PLANNING" if planning_allowed else "WAIT_AUDIT_GATE",
        "direction_bias": direction_bias,
        "audit_reference": {
            "card_id": str(audit_card_id or "").strip(),
            "operator_notes": str(audit_note or "").strip(),
        },
        "planning_scope": {
            "dte_hours_min": dte_min,
            "dte_hours_max": dte_max,
            "short_delta_min": delta_min,
            "short_delta_max": delta_max,
            "protection_width_min": width_min,
            "protection_width_max": width_max,
            "amount": amount,
        },
        "risk_policy": risk_policy or {},
    }


def validate_manual_context(ctx, now_ms):
    errors = []
    ctx = ctx or {}
    scope = ctx.get("planning_scope") or {}
    audit = ctx.get("audit_reference") or {}
    if not ctx:
        errors.append("MANUAL_CONTEXT_MISSING")
    if ctx.get("operator_decision") != "APPROVE_PLANNING":
        errors.append("PLANNING_NOT_APPROVED")
    if ctx.get("direction_bias") not in ("SHORT_CALL", "SHORT_PUT"):
        errors.append("DIRECTION_BIAS_INVALID")
    if not str(audit.get("card_id") or "").strip():
        errors.append("AUDIT_REFERENCE_MISSING")
    if not (_num(scope.get("dte_hours_min")) and _num(scope.get("dte_hours_max"))
            and scope["dte_hours_min"] < scope["dte_hours_max"]):
        errors.append("DTE_RANGE_INVALID")
    if not (_num(scope.get("short_delta_min")) and _num(scope.get("short_delta_max"))
            and 0 < scope["short_delta_min"] < scope["short_delta_max"] < 1):
        errors.append("SHORT_DELTA_RANGE_INVALID")
    if not (_num(scope.get("protection_width_min")) and _num(scope.get("protection_width_max"))
            and scope["protection_width_min"] <= scope["protection_width_max"]):
        errors.append("PROTECTION_WIDTH_RANGE_INVALID")
    if not (_num(scope.get("amount")) and scope["amount"] > 0):
        errors.append("ORDER_AMOUNT_INVALID")
    exp = ctx.get("expires_ts_ms")
    if not _num(exp) or exp <= now_ms:
        errors.append("MANUAL_CONTEXT_EXPIRED")
    return {"valid": not errors, "errors": errors}
