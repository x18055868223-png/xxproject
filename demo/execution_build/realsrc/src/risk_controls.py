# -*- coding: utf-8 -*-
"""Conservative risk, position management, attribution, and replay contracts."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


REPLAY_BUCKET_FIELDS = ["side", "dte_bucket", "vrp_gate", "budget_decision"]


def _expectation_bucket(net_sum: float) -> str:
    if net_sum > 0:
        return "POSITIVE_NET"
    if net_sum < 0:
        return "NEGATIVE_NET"
    return "FLAT_NET"


def evaluate_portfolio_budget(
    current: Dict[str, float],
    limits: Dict[str, float],
    proposed_size: float,
) -> Dict[str, Any]:
    breaches: List[str] = []
    checks = (
        ("open_positions", "max_open_positions"),
        ("short_gamma", "max_short_gamma"),
        ("short_vega", "max_short_vega"),
        ("margin_used", "max_margin"),
    )
    for current_key, limit_key in checks:
        if current.get(current_key, 0.0) > limits.get(limit_key, float("inf")):
            breaches.append("%s>%s" % (current_key, limit_key))

    blocked = bool(breaches)
    return {
        "schema_name": "PortfolioRiskBudgetPackage",
        "schema_version": "nrd.integration.portfolio_budget.v0.1",
        "status": "PLACEHOLDER",
        "decision": "BLOCK" if blocked else "ALLOW_TEST_SIZE",
        "allowed_size": 0.0 if blocked else min(float(proposed_size), 1.0),
        "breaches": breaches,
        "reason_codes": ["PORTFOLIO_BUDGET_EXCEEDED"] if blocked else ["PORTFOLIO_BUDGET_PLACEHOLDER_CONSERVATIVE"],
    }


# ---------- 投影预算真实算法（P0-6；替代上面 PLACEHOLDER：把拟建仓位计入，fail-closed）----------

def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _budget_result(decision, projected, reasons, fail_closed):
    return {
        "schema_name": "ProjectedBudgetPackage",
        "schema_version": "nrd.integration.projected_budget.v1",
        "decision": decision,
        "projected": projected,
        "fail_closed": bool(fail_closed),
        "reason_codes": reasons,
    }


def evaluate_projected_budget(proposed, current, limits):
    """把**拟建仓位**(proposed)计入当前组合(current)后与限额(limits)比较。
    proposed 任一必填项缺失 → fail closed(BLOCK)，绝不放行不完整输入。
      proposed: {short_gamma, short_vega, structure_margin, max_spread_loss,
                 hedge_margin_reserve, fee_reserve}
      current:  {open_positions, short_gamma, short_vega, margin_used}
      limits:   {max_open_positions, max_short_gamma, max_short_vega, max_margin,
                 max_spread_loss_per_trade}"""
    required = ("short_gamma", "short_vega", "structure_margin",
                "max_spread_loss", "hedge_margin_reserve", "fee_reserve")
    missing = [k for k in required if not _is_num((proposed or {}).get(k))]
    if missing:
        return _budget_result("BLOCK", {},
                              ["BUDGET_INPUT_INCOMPLETE:" + ",".join(missing)], True)
    cur = current or {}
    proj = {
        "open_positions": int(cur.get("open_positions", 0)) + 1,
        "short_gamma": float(cur.get("short_gamma", 0.0)) + abs(float(proposed["short_gamma"])),
        "short_vega": float(cur.get("short_vega", 0.0)) + abs(float(proposed["short_vega"])),
        "margin_used": (float(cur.get("margin_used", 0.0))
                        + float(proposed["structure_margin"])
                        + float(proposed["hedge_margin_reserve"])
                        + float(proposed["fee_reserve"])),
    }
    breaches = []
    for pk, lk in (("open_positions", "max_open_positions"),
                   ("short_gamma", "max_short_gamma"),
                   ("short_vega", "max_short_vega"),
                   ("margin_used", "max_margin")):
        lim = (limits or {}).get(lk)
        if _is_num(lim) and proj[pk] > lim:
            breaches.append("%s>%s" % (pk, lk))
    msl_lim = (limits or {}).get("max_spread_loss_per_trade")
    if _is_num(msl_lim) and float(proposed["max_spread_loss"]) > msl_lim:
        breaches.append("max_spread_loss>limit")
    decision = "BLOCK" if breaches else "ALLOW"
    return _budget_result(decision, proj,
                          breaches if breaches else ["PROJECTED_BUDGET_OK"], False)


# ---------- 统一动作仲裁四输出（P0-4：退出不可执行可回退对冲，避免压住风险收口）----------

def _arb(preferred, executable, blocked_reason, fallback):
    return {"schema_name": "ActionArbitration",
            "preferred_action": preferred, "executable_action": executable,
            "blocked_reason": blocked_reason, "fallback_action": fallback}


def unified_action_arbiter(s):
    """每轮输出唯一 preferred + 实际可执行 executable + blocked_reason + fallback。
    s: recovery_blocked / orphan_hedge / in_flight_order / exit_preferred / hedge_ready /
       take_profit_ready / exit_authorized / exit_executable / exit_pause_reason / hedge_executable。
    优先级：RECOVERY_BLOCKED > ORPHAN_HEDGE_EMERGENCY > MANAGE_IN_FLIGHT >
            EXIT_PREFERRED > HEDGE_READY > TAKE_PROFIT_READY > HOLD。
    P0-4：当退出类为 preferred 但未授权/无数据/预算暂停时，executable 回退到对冲(若可执行)，
    不因退出受阻而禁止必要对冲。"""
    s = s or {}
    if s.get("recovery_blocked"):
        return _arb("RECOVERY_BLOCKED", "RECOVERY_BLOCKED", None, None)
    if s.get("orphan_hedge"):
        return _arb("ORPHAN_HEDGE_EMERGENCY", "ORPHAN_HEDGE_EMERGENCY", None, None)
    if s.get("in_flight_order"):
        return _arb("MANAGE_IN_FLIGHT", "MANAGE_IN_FLIGHT", None, None)

    if s.get("exit_preferred"):
        preferred = "EXIT_PREFERRED"
    elif s.get("hedge_ready"):
        preferred = "HEDGE_READY"
    elif s.get("take_profit_ready"):
        preferred = "TAKE_PROFIT_READY"
    else:
        return _arb("HOLD", "HOLD", None, None)

    if preferred in ("EXIT_PREFERRED", "TAKE_PROFIT_READY"):
        if not s.get("exit_authorized"):
            blocked = "EXIT_NOT_AUTHORIZED"
        elif s.get("exit_pause_reason"):
            blocked = "EXIT_" + str(s["exit_pause_reason"])
        elif not s.get("exit_executable"):
            blocked = "EXIT_NOT_EXECUTABLE"
        else:
            blocked = None
        if not blocked:
            return _arb(preferred, preferred, None, None)
        if s.get("hedge_executable"):
            return _arb(preferred, "HEDGE_READY", blocked, "HEDGE_READY")
        return _arb(preferred, "HOLD", blocked, "HOLD")

    # preferred == HEDGE_READY
    if s.get("hedge_executable"):
        return _arb("HEDGE_READY", "HEDGE_READY", None, None)
    return _arb("HEDGE_READY", "HOLD", "HEDGE_NOT_EXECUTABLE", "HOLD")


def decide_position_manage(
    premium_captured_ratio: float,
    take_profit_threshold: float,
    dte_remaining: int,
    gamma_state: str,
) -> Dict[str, Any]:
    reason_codes: List[str] = []
    decision = "HOLD_REVIEW"
    if premium_captured_ratio >= take_profit_threshold:
        decision = "TAKE_PROFIT_READY"
        reason_codes.append("TAKE_PROFIT_PLACEHOLDER_EARLY")
    elif dte_remaining <= 2 and gamma_state.upper() == "HIGH":
        decision = "GAMMA_DECAY_EXIT"
        reason_codes.append("GAMMA_DECAY_PLACEHOLDER_EARLY")
    elif dte_remaining <= 4:
        decision = "TIME_EXIT_REVIEW"
        reason_codes.append("TIME_EXIT_DRYRUN_REVIEW")
    else:
        reason_codes.append("POSITION_MANAGE_PLACEHOLDER_HOLD")

    return {
        "schema_name": "PositionManageDecision",
        "schema_version": "nrd.integration.position_manage.v0.1",
        "status": "PLACEHOLDER",
        "decision": decision,
        "inputs": {
            "premium_captured_ratio": premium_captured_ratio,
            "take_profit_threshold": take_profit_threshold,
            "dte_remaining": dte_remaining,
            "gamma_state": gamma_state,
        },
        "reason_codes": reason_codes,
    }


def build_attribution(
    session_id: str,
    theta_capture: float,
    directional_pnl: float,
    iv_rv_edge_proxy: float,
    fee_cost: float,
    spread_slippage_cost: float,
    protection_cost_or_recovery: float,
    hedge_pnl: float,
    unexplained_residual: float = 0.0,
) -> Dict[str, Any]:
    net = (
        theta_capture
        + directional_pnl
        + iv_rv_edge_proxy
        - fee_cost
        - spread_slippage_cost
        + protection_cost_or_recovery
        + hedge_pnl
        + unexplained_residual
    )
    return {
        "schema_name": "AttributionPackage",
        "schema_version": "nrd.integration.attribution.v0.1",
        "status": "PLACEHOLDER",
        "session_id": session_id,
        "theta_capture": theta_capture,
        "directional_pnl": directional_pnl,
        "iv_rv_edge_proxy": iv_rv_edge_proxy,
        "fee_cost": fee_cost,
        "spread_slippage_cost": spread_slippage_cost,
        "protection_cost_or_recovery": protection_cost_or_recovery,
        "hedge_pnl": hedge_pnl,
        "unexplained_residual": unexplained_residual,
        "net_pnl_after_costs": net,
    }


def replay_expectation(attributions: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(attributions)
    net_sum = sum(float(row.get("net_pnl_after_costs", 0.0)) for row in rows)
    return {
        "schema_name": "ReplayExpectationPackage",
        "schema_version": "nrd.integration.replay_expectation.v0.1",
        "status": "OFFLINE",
        "sample_count": len(rows),
        "net_pnl_after_costs_sum": net_sum,
        "net_pnl_after_costs_mean": net_sum / len(rows) if rows else 0.0,
        "expectation_bucket": _expectation_bucket(net_sum),
    }


def _bucket_key(row: Dict[str, Any], bucket_fields: List[str]) -> str:
    return "|".join("%s=%s" % (field, row.get(field, "UNKNOWN")) for field in bucket_fields)


def replay_expectation_by_bucket(
    rows: Iterable[Dict[str, Any]],
    bucket_fields: List[str],
) -> Dict[str, Any]:
    materialized = list(rows)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in materialized:
        grouped.setdefault(_bucket_key(row, bucket_fields), []).append(row)

    buckets = []
    for key in sorted(grouped):
        bucket_rows = grouped[key]
        net_sum = sum(float(row.get("net_pnl_after_costs", 0.0)) for row in bucket_rows)
        first_row = bucket_rows[0] if bucket_rows else {}
        buckets.append({
            "bucket_key": key,
            "bucket_values": {field: first_row.get(field, "UNKNOWN") for field in bucket_fields},
            "sample_count": len(bucket_rows),
            "net_pnl_after_costs_sum": net_sum,
            "net_pnl_after_costs_mean": net_sum / len(bucket_rows) if bucket_rows else 0.0,
            "expectation_bucket": _expectation_bucket(net_sum),
        })

    net_sum = sum(float(row.get("net_pnl_after_costs", 0.0)) for row in materialized)
    return {
        "schema_name": "ReplayExpectationBucketReport",
        "schema_version": "nrd.integration.replay_expectation_buckets.v0.5",
        "status": "OFFLINE",
        "sample_count": len(materialized),
        "bucket_fields": bucket_fields,
        "net_pnl_after_costs_sum": net_sum,
        "net_pnl_after_costs_mean": net_sum / len(materialized) if materialized else 0.0,
        "buckets": buckets,
    }


def _dte_bucket(expiry_hours: Any) -> str:
    if expiry_hours is None:
        return "UNKNOWN"
    try:
        return "%sh" % int(float(expiry_hours))
    except (TypeError, ValueError):
        return str(expiry_hours)


def build_replay_context_row(execution_result: Dict[str, Any]) -> Dict[str, Any]:
    session = execution_result.get("session", {})
    locked_plan = execution_result.get("locked_plan", {})
    plan = locked_plan.get("plan", {})
    vrp_gate = execution_result.get("vrp_gate", {})
    candidate = vrp_gate.get("candidate", {})
    portfolio_budget = execution_result.get("portfolio_budget", {})
    attribution = execution_result.get("attribution", {})
    approval_intent = session.get("approval_intent", {})
    vrp_state = "PASS" if vrp_gate.get("pass") else "BLOCK"
    return {
        "schema_name": "ReplayContextRow",
        "schema_version": "nrd.integration.replay_context.v0.8",
        "session_id": session.get("session_id"),
        "signal_package_id": session.get("signal_package_id"),
        "plan_hash": locked_plan.get("plan_hash"),
        "side": plan.get("side", "UNKNOWN"),
        "expiry_hours": plan.get("expiry_hours"),
        "dte_bucket": _dte_bucket(plan.get("expiry_hours")),
        "vrp_gate": vrp_state,
        "window_vrp_gate": vrp_gate.get("window", {}).get("window_vrp_gate"),
        "candidate_vrp_gate": candidate.get("candidate_vrp_gate"),
        "candidate_vrp_edge_ccy": float(candidate.get("candidate_vrp_edge_ccy", 0.0) or 0.0),
        "budget_decision": portfolio_budget.get("decision", "UNKNOWN"),
        "approval_state": approval_intent.get("approval_state", "UNKNOWN"),
        "can_commit_order": bool(execution_result.get("can_commit_order", False)),
        "net_pnl_after_costs": float(attribution.get("net_pnl_after_costs", 0.0) or 0.0),
        "reason_codes": sorted(set(vrp_gate.get("reason_codes") or [])),
    }


def replay_expectation_from_execution_result(execution_result: Dict[str, Any]) -> Dict[str, Any]:
    row = build_replay_context_row(execution_result)
    report = replay_expectation_by_bucket([row], bucket_fields=REPLAY_BUCKET_FIELDS)
    report["source"] = "execution_result"
    report["rows"] = [row]
    return report


def replay_expectation_batch_from_execution_results(
    execution_results: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    rows = [build_replay_context_row(result) for result in execution_results]
    bucket_report = replay_expectation_by_bucket(rows, bucket_fields=REPLAY_BUCKET_FIELDS)
    bucket_report["source"] = "execution_results"
    net_sum = sum(float(row.get("net_pnl_after_costs", 0.0)) for row in rows)
    return {
        "schema_name": "ReplayExpectationBatchReport",
        "schema_version": "nrd.integration.replay_batch.v0.9",
        "source": "execution_results",
        "sample_count": len(rows),
        "net_pnl_after_costs_sum": net_sum,
        "net_pnl_after_costs_mean": net_sum / len(rows) if rows else 0.0,
        "expectation_bucket": _expectation_bucket(net_sum),
        "rows": rows,
        "bucket_report": bucket_report,
    }
