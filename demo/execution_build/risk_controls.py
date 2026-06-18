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
