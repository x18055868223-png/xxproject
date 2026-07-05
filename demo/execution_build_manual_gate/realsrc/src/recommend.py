# -*- coding: utf-8 -*-
"""Recommendation library, approval snapshots, and precommit checks.

This manual-gate fork binds plan approval to a human-provided manual context
instead of manual package lineage. The functions stay pure so the small local
test runner can validate the approval contract without FMZ state.
"""
import base64
import hashlib
import json

QUALIFIED = "QUALIFIED"
RELIEF_BUCKET = 10
FEASIBILITY_BUCKET = 10
DEFAULT_CONFIG_HASH = "manual-gate-default-config-v1"


def _h(*parts):
    s = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _stable_json_hash(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _b32(hexstr, length):
    raw = base64.b32encode(bytes.fromhex(hexstr)).decode("ascii").rstrip("=")
    return raw[:length]


def _relief_bucket(ratio):
    if not isinstance(ratio, (int, float)):
        return "NA"
    return int(ratio * RELIEF_BUCKET)


def _feasibility_bucket(score):
    if not isinstance(score, (int, float)):
        return "NA"
    return int(max(0.0, min(100.0, score)) // FEASIBILITY_BUCKET)


def _range(scope, min_key, max_key, range_key=None):
    if range_key and range_key in scope:
        value = scope.get(range_key)
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return [value[0], value[1]]
        return value
    return [scope.get(min_key), scope.get(max_key)]


def _audit_reference(manual_context):
    ref = (manual_context or {}).get("audit_reference") or {}
    if not isinstance(ref, dict):
        return {}
    return ref


def _audit_card_id(manual_context):
    ref = _audit_reference(manual_context)
    return (ref.get("card_id") or ref.get("audit_card_id")
            or (manual_context or {}).get("audit_card_id"))


def _operator_note(manual_context):
    ref = _audit_reference(manual_context)
    return ((manual_context or {}).get("operator_note")
            or ref.get("operator_note")
            or ref.get("operator_notes")
            or "")


def _manual_context_id(manual_context):
    return ((manual_context or {}).get("context_id")
            or (manual_context or {}).get("manual_context_id"))


def _config_hash(manual_context, config_hash=None):
    return (config_hash
            or (manual_context or {}).get("config_hash")
            or DEFAULT_CONFIG_HASH)


def manual_context_hash(ctx):
    """Hash only stable material manual-planning fields."""
    ctx = ctx or {}
    planning = ctx.get("planning_scope") or {}
    risk_policy = ctx.get("risk_policy") or {}
    if not isinstance(planning, dict):
        planning = {}
    if not isinstance(risk_policy, dict):
        risk_policy = {}
    material = {
        "context_id": _manual_context_id(ctx),
        "direction_bias": ctx.get("direction_bias"),
        "planning_dte_range": _range(planning, "dte_hours_min", "dte_hours_max",
                                      "dte_hours_range"),
        "delta_range": (_range(planning, "short_delta_min", "short_delta_max",
                               "short_delta_range")
                        if ("short_delta_min" in planning
                            or "short_delta_max" in planning
                            or "short_delta_range" in planning)
                        else _range(planning, "delta_min", "delta_max",
                                    "delta_range")),
        "protection_width_range": _range(planning, "protection_width_min",
                                          "protection_width_max",
                                          "protection_width_range"),
        "amount": planning.get("amount", ctx.get("amount")),
        "audit_card_id": _audit_card_id(ctx),
        "risk_policy": risk_policy,
        "expires_ts_ms": ctx.get("expires_ts_ms"),
    }
    return _stable_json_hash(material)


def side_of(short_instrument):
    s = str(short_instrument or "")
    if s.endswith("-C"):
        return "CALL"
    if s.endswith("-P"):
        return "PUT"
    return "UNK"


def strategy_code(side, expiry_label, short_strike, long_strike):
    return "VCS|%s|%s|%s|%s" % (side, expiry_label, short_strike, long_strike)


def quality_code(manual_ctx_hash, relief_ratio, vrp_state, budget_decision,
                 execution_feasibility_score=None, config_hash=None):
    """Frozen quality code bound to manual context and economic/safety buckets."""
    return _h(manual_ctx_hash, _relief_bucket(relief_ratio), vrp_state,
              budget_decision, _feasibility_bucket(execution_feasibility_score),
              "cfg:%s" % (config_hash or DEFAULT_CONFIG_HASH))[:8]


def plan_hash(strategy_code_str, quality_code_str, side,
              short_instrument, long_instrument, amount):
    return _h(strategy_code_str, quality_code_str, side,
              short_instrument, long_instrument, amount)[:16]


def confirm_code(session_id, strategy_code_str, quality_code_str, plan_hash_str, length=4):
    return _b32(_h(session_id, strategy_code_str, quality_code_str, plan_hash_str), length)


def build_approval_snapshot(candidate, session_id, manual_context, refresh_seq, now_ts,
                            config_hash=None):
    candidate = candidate or {}
    manual_context = manual_context or {}
    ctx_hash = manual_context_hash(manual_context)
    ctx_id = _manual_context_id(manual_context)
    cfg_hash = _config_hash(manual_context, config_hash)
    short_inst = candidate.get("short_instrument") or ""
    long_inst = candidate.get("protection_instrument") or ""
    side = side_of(short_inst)
    vrp_state = candidate.get("vrp_state") or candidate.get("vrp_gate")
    budget_decision = candidate.get("budget_decision")
    sc = strategy_code(side, candidate.get("short_expiry_label"),
                       candidate.get("short_strike"), candidate.get("protection_strike"))
    qc = quality_code(ctx_hash, candidate.get("margin_relief_ratio"),
                      vrp_state, budget_decision,
                      execution_feasibility_score=candidate.get("execution_feasibility_score"),
                      config_hash=cfg_hash)
    ph = plan_hash(sc, qc, side, short_inst, long_inst, candidate.get("amount"))
    approval_id = _h("approval", session_id, ctx_id, ctx_hash, ph, cfg_hash)[:16]
    cc = confirm_code(session_id, sc, qc, ph)
    return {
        "schema_name": "PlanApprovalSnapshot",
        "schema_version": "nrd.execution.plan_approval.v1",
        "approval_id": approval_id,
        "session_id": session_id,
        "manual_context_id": ctx_id,
        "manual_context_hash": ctx_hash,
        "audit_card_id": _audit_card_id(manual_context),
        "operator_note": _operator_note(manual_context),
        "direction_bias": manual_context.get("direction_bias"),
        "config_hash": cfg_hash,
        "refresh_seq": refresh_seq,
        "plan_id": candidate.get("id"),
        "side": side,
        "strategy_code": sc,
        "quality_code": qc,
        "plan_hash": ph,
        "confirm_code": cc,
        "recommendation_state": QUALIFIED if candidate.get("qualified", True) else "REJECTED",
        "short_instrument": short_inst,
        "long_instrument": long_inst,
        "short_strike": candidate.get("short_strike"),
        "long_strike": candidate.get("protection_strike"),
        "short_expiry": candidate.get("short_expiry"),
        "short_dte_hours": candidate.get("short_dte_hours"),
        "short_delta": candidate.get("short_delta"),
        "breakeven": candidate.get("breakeven"),
        "amount": candidate.get("amount"),
        "entry_net_credit_after_costs": candidate.get("net_credit_effective"),
        "max_loss": candidate.get("max_loss"),
        "margin_relief_ratio": candidate.get("margin_relief_ratio"),
        "execution_feasibility_grade": candidate.get("execution_feasibility_grade"),
        "execution_feasibility_score": candidate.get("execution_feasibility_score"),
        "execution_feasibility_score_norm": candidate.get("execution_feasibility_score_norm"),
        "execution_feasibility_warnings": candidate.get("execution_feasibility_warnings") or [],
        "frozen_ts": now_ts,
        "summary": "%s Δ%s 宽%s" % (side, candidate.get("short_delta"), candidate.get("width")),
    }


def ensure_unique_confirm_codes(snaps, session_id, max_len=8):
    length = 4
    while length <= max_len:
        seen = {}
        for s in snaps:
            cc = confirm_code(session_id, s["strategy_code"], s["quality_code"],
                              s["plan_hash"], length)
            seen.setdefault(cc, []).append(s)
        if all(len(v) == 1 for v in seen.values()):
            break
        length += 1
    length = min(length, max_len)
    for s in snaps:
        s["confirm_code"] = confirm_code(session_id, s["strategy_code"],
                                         s["quality_code"], s["plan_hash"], length)
    return snaps


def build_recommendation_library(menu, session_id, manual_context, refresh_seq, now_ts,
                                 config_hash=None):
    manual_context = manual_context or {}
    ctx_hash = manual_context_hash(manual_context)
    ctx_id = _manual_context_id(manual_context)
    cfg_hash = _config_hash(manual_context, config_hash)
    snaps = [build_approval_snapshot(c, session_id, manual_context, refresh_seq, now_ts,
                                     config_hash=cfg_hash)
             for c in (menu or [])]
    ensure_unique_confirm_codes(snaps, session_id)
    return {
        "schema_name": "VerticalRecommendationLibrary",
        "schema_version": "nrd.execution.recommendation_library.v1",
        "session_id": session_id,
        "manual_context_id": ctx_id,
        "manual_context_hash": ctx_hash,
        "audit_card_id": _audit_card_id(manual_context),
        "operator_note": _operator_note(manual_context),
        "direction_bias": manual_context.get("direction_bias"),
        "config_hash": cfg_hash,
        "refresh_seq": refresh_seq,
        "generated_ts": now_ts,
        "recommendations": snaps,
    }


def resolve_confirm_code(library, code):
    code = str(code or "").strip().upper()
    if not code:
        return None
    for s in (library or {}).get("recommendations", []):
        if str(s.get("confirm_code", "")).upper() == code:
            if s.get("recommendation_state") == QUALIFIED:
                return s
    return None


def precommit_recheck(locked_snapshot, current_library, live_checks):
    reasons = []
    match = next((s for s in (current_library or {}).get("recommendations", [])
                  if s.get("strategy_code") == locked_snapshot.get("strategy_code")
                  and s.get("recommendation_state") == QUALIFIED), None)
    if not match:
        reasons.append("STRATEGY_NO_LONGER_QUALIFIED_IN_LIBRARY")
    elif match.get("plan_hash") != locked_snapshot.get("plan_hash"):
        reasons.append("PLAN_HASH_DRIFTED_BEYOND_TOLERANCE")
    for k, ok in (live_checks or {}).items():
        if not ok:
            reasons.append("LIVE_CHECK_FAILED:" + str(k))
    return {"passed": not reasons, "reasons": reasons}


PRECOMMIT_CHECKS = (
    "manual_context_valid", "same_manual_context", "approval_not_expired",
    "locked_plan_hash_match", "locked_quality_code_match", "vertical_only",
    "vrp_rechecked", "spm_rechecked", "quotes_rechecked",
    "entry_net_credit_after_costs_positive", "projected_budget_passed",
    "ledger_reconciled", "no_unknown_orders", "spread_ok",
    "execution_feasibility_rechecked",
)


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def evaluate_precommit_checks(locked, current_library, live):
    locked = locked or {}
    live = live or {}
    match = next((s for s in (current_library or {}).get("recommendations", [])
                  if s.get("strategy_code") == locked.get("strategy_code")
                  and s.get("recommendation_state") == QUALIFIED), None)
    locked_ctx_hash = locked.get("manual_context_hash")
    live_ctx_hash = live.get("manual_context_hash")
    c = {
        "manual_context_valid": bool(live.get("manual_context_valid")),
        "same_manual_context": bool(locked_ctx_hash) and locked_ctx_hash == live_ctx_hash,
        "approval_not_expired": live.get("approval_not_expired") is True,
        "locked_plan_hash_match": bool(match) and match.get("plan_hash") == locked.get("plan_hash"),
        "locked_quality_code_match": bool(match) and match.get("quality_code") == locked.get("quality_code"),
        "vertical_only": locked.get("side") in ("CALL", "PUT") and bool(live.get("same_expiry")),
        "vrp_rechecked": live.get("vrp_pass") is True,
        "spm_rechecked": (_is_num(live.get("spm_relief")) and _is_num(live.get("min_relief"))
                          and live["spm_relief"] >= live["min_relief"]),
        "quotes_rechecked": bool(live.get("quotes_fresh")),
        "entry_net_credit_after_costs_positive": (_is_num(live.get("net_credit_after_costs"))
                                                  and live["net_credit_after_costs"] > 0),
        "projected_budget_passed": live.get("projected_budget_decision") == "ALLOW",
        "ledger_reconciled": bool(live.get("ledger_reconciled")),
        "no_unknown_orders": bool(live.get("no_unknown_orders")),
        "spread_ok": bool(live.get("spread_ok")),
        "execution_feasibility_rechecked": (
            (live.get("execution_feasibility_live") or {}).get("hard_gate_passed") is True),
    }
    failed = [k for k in PRECOMMIT_CHECKS if not c.get(k)]
    return {"checks": c, "passed": not failed, "failed": failed}
