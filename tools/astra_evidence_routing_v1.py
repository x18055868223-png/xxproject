"""Standalone shadow Evidence Routing v1.

The module consumes only retained underwriting snapshot and Review Evidence
fields. It does not call providers, read raw source bytes, recompute financial
values, grant permission, or attach itself to composer/product code.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

if __package__:
    from . import astra_underwriting_v20 as _contract_core
else:
    import astra_underwriting_v20 as _contract_core


SCHEMA = "evidence_routing@1.0.0"
VALIDATION_SCHEMA = "evidence_routing_validation@1.0.0"
ROUTING_RULE_VERSION = "evidence_routing_rules@1.0.0"

PROBLEM_DOMAINS = {
    "RESPONSIBILITY_SPACE", "TIMING_WINDOW", "FEEDBACK_STABILITY",
    "DIRECTIONAL_PRESSURE", "PATH_DYNAMICS", "FLOW_REFLEXIVITY",
    "OPTION_STRUCTURE", "VOLATILITY_PRICING", "COMPENSATION",
    "EXOGENOUS_DISTURBANCE", "QUALIFICATION_DATA", "AUDIT_LINEAGE",
}
SYSTEM_ROLES = {"STATE", "FEEDBACK", "DISTURBANCE", "CONSTRAINT", "COMPENSATION", "QUALIFICATION", "AUDIT"}
LOOPS = {"BULLISH_LOOP", "NEUTRAL_STABILIZING_LOOP", "BEARISH_LOOP", "CROSS_LOOP", "OBSERVATION_ONLY"}
LOOP_RELATIONS = {"SUPPORT", "CONTRADICT", "MODULATE", "CONSTRAIN", "INCONCLUSIVE"}
DEPENDENCY_GROUPS = {
    "PRICE_LOCATION", "PRICE_PATH", "FLOW", "OPTION_SPACE", "OPTION_DEMAND",
    "VOLATILITY_STRUCTURE", "MACRO_RISK", "COMPENSATION", "DATA_QUALITY",
    "RISK_REFERENCE",
}
HORIZONS = {"INSTANT", "MINUTES", "15M", "1H", "INTRADAY", "EXPIRY_MATCHED", "MULTIDAY", "SLOW_BACKGROUND", "UNKNOWN"}
CANDIDATE_RELEVANCE = {"DIRECT", "CONTEXTUAL", "WEAK", "NOT_APPLICABLE"}
INFORMATION_INCREMENTS = {
    "NOVEL", "CONFIRMING_SAME_BLOCK", "COUNTER_EVIDENCE", "CONTEXT_ONLY",
    "QUALIFICATION_ONLY", "REDUNDANT", "MISSING", "INCONCLUSIVE",
}
Q_ROLES = {"PRIMARY", "SUPPORT", "COUNTER", "LIMITATION", "REASSESS", "NONE"}
QUALIFICATION_STATUSES = {"QUALIFIED", "PARTIAL", "UNQUALIFIED", "MISSING", "SYNTHETIC", "BLOCKED"}
FRESHNESS_STATUSES = {"CURRENT", "STALE", "UNKNOWN", "MISSING", "FUTURE"}
INDEPENDENCE_STATUSES = {"INDEPENDENT", "PARTIALLY_DEPENDENT", "SAME_BLOCK", "UNKNOWN"}

DOMAIN_LABELS_CN = {
    "RESPONSIBILITY_SPACE": "责任位置",
    "TIMING_WINDOW": "时间窗口",
    "FEEDBACK_STABILITY": "反馈稳定性",
    "DIRECTIONAL_PRESSURE": "方向压力",
    "PATH_DYNAMICS": "路径动态",
    "FLOW_REFLEXIVITY": "流量反馈",
    "OPTION_STRUCTURE": "期权结构",
    "VOLATILITY_PRICING": "波动率定价",
    "COMPENSATION": "补偿",
    "EXOGENOUS_DISTURBANCE": "外部扰动",
    "QUALIFICATION_DATA": "资料资格",
    "AUDIT_LINEAGE": "审计血缘",
}


class RoutingValidationError(ValueError):
    """Raised when a supplied routing package is not replay-equivalent."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    """Same canonical hash contract used by astra_underwriting_v20._hash."""
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _m(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _lst(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _p(root: Any, *keys: str) -> Any:
    cur = root
    for key in keys:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _up(value: Any) -> str:
    return str(value or "").upper()


PATH_CLASSIFICATION_CN = {
    "single_direction_adverse_pressure": "同一保单窗口内，价格持续向不利责任区靠近。",
    "single_direction_favorable_relief": "同一保单窗口内，价格持续远离不利责任区。",
    "repeated_cross_zone": "同一保单窗口内，价格反复跨越责任区；末端位置不能概括这段路径。",
    "persistent_same_zone": "同一保单窗口内，价格主要停留在同一责任区；未形成单向缓解或承压路径。",
    "gap": "同一保单连续窗口尚不足，当前归因只使用截面事实。",
}


def _fmt_btc(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.8f}BTC"
    return None


def _fmt_usd(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,.2f}美元"
    return None


def _fmt_pct(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.2f}%"
    return None


def _fmt_signed_pct(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:+.2f}%"
    return None


def _fmt_signed_btc(value: Any, *, noun: str) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    direction = "增加" if value > 0 else "减少" if value < 0 else "不变"
    return f"{noun}{direction}{abs(value):.8f}BTC" if value else f"{noun}不变"


def _fmt_price_list(values: Any) -> str | None:
    rows = [item for item in _lst(values) if isinstance(item, (int, float)) and not isinstance(item, bool)]
    if not rows:
        return None
    return "、".join(_fmt_usd(item) or str(item) for item in rows[:3])


def _fee_covers_cn(values: Any) -> str:
    labels = {"entry": "进场", "delivery": "到期交割"}
    rows = [labels.get(str(item), str(item)) for item in _lst(values) if item]
    return "、".join(rows) if rows else "未列明"


def _flow_cn(direction: str) -> str:
    return "主动买入" if direction == "UP" else "主动卖出" if direction == "DOWN" else "主动流方向未知"


def _tmv_cn(direction: str) -> str:
    return "向上" if direction == "UP" else "向下" if direction == "DOWN" else str(direction or "未知")


def _side_cn(side: str) -> str:
    return "Put" if side == "PUT" else "Call" if side == "CALL" else "本单"


def _horizons_cn(value: Any) -> str:
    if isinstance(value, Mapping):
        rows = list(value.keys())
    else:
        rows = _lst(value)
    rows = [str(row) for row in rows if row]
    return "/".join(rows) if rows else "已覆盖窗口"


def _window_ms_cn(value: Any) -> str:
    if value == 900000:
        return "15分钟"
    if value == 3600000:
        return "1小时"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minutes = value / 60000
        return f"{minutes:g}分钟" if minutes else "0分钟"
    return "未知窗口"


def _coverage_reason_cn(qualification: Mapping[str, Any], coverage: Mapping[str, Any], same_status: str) -> str:
    status = _up(qualification.get("status") or same_status)
    kind = _up(qualification.get("kind"))
    real_status = _up(qualification.get("real_window_status"))
    windows = [_m(row) for row in _lst(coverage.get("windows")) if isinstance(row, Mapping)]
    labels = "/".join(_window_ms_cn(row.get("requested_window_ms")) for row in windows) or "同候选"
    native_points = sum(int(row.get("native_qualified_points") or 0) for row in windows)
    qualified_points = sum(int(row.get("qualified_points") or 0) for row in windows)
    window_statuses = sorted({str(row.get("status")) for row in windows if row.get("status")})
    evidence_kinds = sorted({str(row.get("evidence_kind")) for row in windows if row.get("evidence_kind")})

    if status == "SYNTHETIC_WINDOW_COVERED" or "SYNTHETIC" in kind:
        base = f"{labels}窗口为模拟覆盖（SYNTHETIC_WINDOW_COVERED）"
    elif status == "NATIVE_WINDOW_QUALIFIED" or native_points:
        base = f"{labels}窗口为原生覆盖（NATIVE_WINDOW_QUALIFIED）"
    elif status in {"INSUFFICIENT_WINDOW", "INSUFFICIENT"} or real_status == "REAL_WINDOW_BLOCKED":
        base = f"{labels}窗口不足（INSUFFICIENT_WINDOW）"
    elif status == "OBSERVED":
        base = f"{labels}窗口已观察（OBSERVED）"
    else:
        base = f"{labels}窗口状态为{status or 'UNKNOWN'}"

    detail = []
    if qualified_points or native_points:
        detail.append(f"合格点{qualified_points}、原生合格点{native_points}")
    if window_statuses:
        detail.append("窗口枚举" + "/".join(window_statuses))
    if evidence_kinds:
        detail.append("证据类型" + "/".join(evidence_kinds))
    suffix = "；" + "，".join(detail) if detail else ""
    return f"{base}{suffix}。"


def _rel(loop: str, relation: str, mechanism_id: str) -> dict[str, str]:
    if loop not in LOOPS or relation not in LOOP_RELATIONS or not mechanism_id:
        raise ValueError("invalid_loop_relation")
    return {"loop": loop, "relation": relation, "mechanism_id": mechanism_id}


def _hz(source: str, method: str, ref: str | None = None) -> dict[str, str]:
    if source not in HORIZONS or method not in HORIZONS:
        raise ValueError("invalid_horizon")
    out = {"source": source, "method": method}
    if ref:
        out["source_window_ref"] = ref
    return out


def _binding(snapshot: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    market = _m(snapshot.get("market"))
    return {
        "candidate_id": snapshot.get("candidate_id"),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_hash": _hash(snapshot),
        "evidence_schema": evidence.get("schema"),
        "decision_asof_ms": evidence.get("decision_asof_ms") or market.get("valuation_ts_ms"),
        "source_kind": _p(snapshot, "data_identity", "kind"),
        "synthetic_identity": _p(snapshot, "data_identity", "kind") == "synthetic_fixture",
        "routing_rule_version": ROUTING_RULE_VERSION,
    }


def _unit_id(binding: Mapping[str, Any], asset: str, ref: str) -> str:
    digest = _hash({
        "candidate_id": binding.get("candidate_id"),
        "snapshot_hash": binding.get("snapshot_hash"),
        "asset": asset,
        "ref": ref,
        "rule": ROUTING_RULE_VERSION,
    })[:16]
    safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in asset).strip("_")
    return f"eu.{safe}.{digest}"


def _window_status(evidence: Mapping[str, Any]) -> str | None:
    return (
        _p(evidence, "same_candidate_window", "qualification", "status")
        or _p(evidence, "same_candidate_window", "status")
    )


def _wpem(evidence: Mapping[str, Any], *parts: str) -> dict[str, Any]:
    path = _m(_p(evidence, "same_candidate_window", "LevelDeltaPath", "path"))
    return {
        "level": "level" if "level" in parts else None,
        "delta": "delta" if "delta" in parts else None,
        "path": path.get("status"),
        "path_classification": path.get("classification"),
        "window_status": _window_status(evidence),
        "source_window_evidence_hash": _p(evidence, "same_candidate_window", "source_window_evidence_hash"),
    }


def _source_status(value: Any) -> tuple[str, str]:
    state = _up(value)
    if state in {"OK", "FRESH", "VALID", "QUALIFIED", "CURRENT", "USABLE", "SUPPORTED", "OBSERVED", "NATIVE_WINDOW_QUALIFIED"}:
        return "QUALIFIED", "CURRENT"
    if state in {"SYNTHETIC_WINDOW_COVERED", "SYNTHETIC_CONTRACT_PASSED"}:
        return "SYNTHETIC", "CURRENT"
    if state in {"PARTIAL", "BACKGROUND_ONLY", "INSUFFICIENT", "INSUFFICIENT_WINDOW"}:
        return "PARTIAL", "UNKNOWN"
    if state in {"STALE", "NOT_CURRENT"}:
        return "UNQUALIFIED", "STALE"
    if state in {"MISSING", ""}:
        return "MISSING", "MISSING"
    if "BLOCK" in state or state in {"INVALID", "UNUSABLE", "ERROR"}:
        return "BLOCKED", "UNKNOWN"
    return "PARTIAL", "UNKNOWN"


def _add(
    units: list[dict[str, Any]],
    binding: Mapping[str, Any],
    *,
    source_asset: str,
    source_ref: str,
    asof: Any,
    problem_domains: Sequence[str],
    primary_problem_domain: str,
    system_role: str,
    loop_relations: Sequence[Mapping[str, str]],
    dependency_group: str,
    independence_status: str,
    horizon: Mapping[str, Any],
    candidate_relevance: str,
    information_increment: str,
    q1_role: str,
    q2_role: str,
    qualification_status: str,
    freshness_status: str,
    reason_cn: str,
    trace_refs: Sequence[str],
    q1_reason_cn: str | None = None,
    q2_reason_cn: str | None = None,
    asof_basis: str | None = None,
    source_observed_ms: Any = None,
    source_clock_status: str | None = None,
    wpem_binding: Mapping[str, Any] | None = None,
    dependency_parent_refs: Sequence[str] = (),
    derived_from: Sequence[str] = (),
) -> dict[str, Any]:
    if primary_problem_domain not in PROBLEM_DOMAINS or any(d not in PROBLEM_DOMAINS for d in problem_domains):
        raise ValueError("invalid_problem_domain")
    if system_role not in SYSTEM_ROLES or dependency_group not in DEPENDENCY_GROUPS:
        raise ValueError("invalid_role_or_dependency")
    if independence_status not in INDEPENDENCE_STATUSES:
        raise ValueError("invalid_independence_status")
    if candidate_relevance not in CANDIDATE_RELEVANCE or information_increment not in INFORMATION_INCREMENTS:
        raise ValueError("invalid_relevance_or_increment")
    if q1_role not in Q_ROLES or q2_role not in Q_ROLES:
        raise ValueError("invalid_q_role")
    if qualification_status not in QUALIFICATION_STATUSES or freshness_status not in FRESHNESS_STATUSES:
        raise ValueError("invalid_qualification")
    unit = {
        "evidence_id": _unit_id(binding, source_asset, source_ref),
        "source_asset": source_asset,
        "source_ref": source_ref,
        "asof": asof,
        "retained_snapshot_asof_ms": binding.get("decision_asof_ms"),
        "asof_basis": asof_basis or ("SOURCE_CLOCK" if asof is not None else "UNKNOWN"),
        "source_observed_ms": source_observed_ms if source_observed_ms is not None else (asof if asof is not None and (asof_basis in {None, "SOURCE_CLOCK"}) else None),
        "source_clock_status": source_clock_status or ("RETAINED" if asof is not None else "NOT_RETAINED"),
        "problem_domains": list(dict.fromkeys(problem_domains)),
        "primary_problem_domain": primary_problem_domain,
        "system_role": system_role,
        "loop_relations": [dict(row) for row in loop_relations],
        "dependency_group": dependency_group,
        "dependency_parent_refs": list(dependency_parent_refs),
        "derived_from": list(derived_from),
        "independence_status": independence_status,
        "horizon": dict(horizon),
        "candidate_relevance": candidate_relevance,
        "information_increment": information_increment,
        "q1_role": q1_role,
        "q2_role": q2_role,
        "qualification_status": qualification_status,
        "freshness_status": freshness_status,
        "source_qualification": {"status": qualification_status, "freshness_status": freshness_status},
        "wpem_binding": dict(wpem_binding or {}),
        "reason_cn": reason_cn,
        "q1_reason_cn": q1_reason_cn if q1_reason_cn is not None else (None if q1_role == "NONE" else reason_cn),
        "q2_reason_cn": q2_reason_cn if q2_reason_cn is not None else (None if q2_role == "NONE" else reason_cn),
        "trace_refs": list(trace_refs),
        "binding": {
            "candidate_id": binding.get("candidate_id"),
            "snapshot_id": binding.get("snapshot_id"),
            "snapshot_hash": binding.get("snapshot_hash"),
            "decision_asof_ms": binding.get("decision_asof_ms"),
        },
    }
    units.append(unit)
    return unit


def _side(snapshot: Mapping[str, Any]) -> str:
    return _up(_p(snapshot, "contract", "side"))


def _flow_direction(value: Any) -> str | None:
    direction = _up(value)
    if direction in {"UP", "BULLISH", "BUY", "BUYING", "LONG"}:
        return "UP"
    if direction in {"DOWN", "BEARISH", "SELL", "SELLING", "SHORT"}:
        return "DOWN"
    return None


def _flow_roles(side: str, direction: str, horizons: Any) -> tuple[str, str, str, str, str]:
    adverse = (side == "PUT" and direction == "DOWN") or (side == "CALL" and direction == "UP")
    window = _horizons_cn(horizons)
    flow = _flow_cn(direction)
    side_label = _side_cn(side)
    if adverse:
        return (
            "COUNTER",
            "COUNTER",
            f"{window} {flow}与{side_label}短腿责任扩大的方向一致。",
            f"{window} {flow}推向{side_label}短腿责任扩大的方向，应作为Q1反证。",
            f"Q2接受该主动流背景时，需要把不利路径压力与既有补偿和风险参考一起权衡。",
        )
    return (
        "SUPPORT",
        "SUPPORT",
        f"{window} {flow}没有指向{side_label}短腿责任扩大的方向。",
        f"{window} {flow}没有直接推向{side_label}短腿责任扩大。",
        "Q2可把该流向作为背景支持，仍需与补偿、费用和风险参考分开判断。",
    )


def _quality_ok(*values: Any) -> bool:
    return any(_source_status(value)[0] == "QUALIFIED" for value in values if value is not None)


def _ready_horizons_ready(value: Any) -> bool:
    if isinstance(value, Mapping):
        rows = list(value.values())
        return bool(rows) and all(_m(row).get("data_ready") is True or _m(row).get("ready") is True for row in rows)
    if isinstance(value, list):
        if not value:
            return False
        if all(isinstance(row, str) for row in value):
            return True
        return all(_m(row).get("data_ready") is True or _m(row).get("ready") is True for row in value)
    return False


def _snapshot_units(units: list[dict[str, Any]], binding: Mapping[str, Any], snapshot: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
    market = _m(snapshot.get("market"))
    contract = _m(snapshot.get("contract"))
    liability = _m(snapshot.get("liability"))
    economics = _m(snapshot.get("economics"))
    fee = _m(snapshot.get("fee"))
    risk = _m(snapshot.get("risk"))
    fact = _m(snapshot.get("fact_context"))
    facts = _m(fact.get("market_facts"))

    if market.get("reference_price_usd") is not None:
        _add(units, binding, source_asset="current_reference_price", source_ref="snapshot.market.reference_price_usd",
             asof=market.get("valuation_ts_ms"), problem_domains=["RESPONSIBILITY_SPACE"],
             primary_problem_domain="RESPONSIBILITY_SPACE", system_role="STATE",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "price_state_not_direction")],
             dependency_group="PRICE_LOCATION", independence_status="INDEPENDENT",
             horizon=_hz("INSTANT", "INSTANT", "snapshot.market.valuation_ts_ms"),
             candidate_relevance="DIRECT", information_increment="NOVEL", q1_role="PRIMARY", q2_role="SUPPORT",
             qualification_status="QUALIFIED", freshness_status="CURRENT", wpem_binding=_wpem(evidence, "level", "delta", "path"),
             reason_cn=(
                 f"当前参考价{_fmt_usd(market.get('reference_price_usd')) or market.get('reference_price_usd')}，"
                 f"短腿{_fmt_usd(contract.get('short_strike_usd')) or contract.get('short_strike_usd')}、"
                 f"保护腿{_fmt_usd(contract.get('long_strike_usd')) or contract.get('long_strike_usd')}，用于定位本单责任区。"
             ),
             trace_refs=["snapshot.market.reference_price_usd", "same_candidate_window.LevelDeltaPath.path"])

    if liability:
        _add(units, binding, source_asset="responsibility_distance", source_ref="snapshot.liability.signed_distance_to_short_pct",
             asof=market.get("valuation_ts_ms"), problem_domains=["RESPONSIBILITY_SPACE", "COMPENSATION"],
             primary_problem_domain="RESPONSIBILITY_SPACE", system_role="STATE",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "liability_geometry_not_forecast")],
             dependency_group="PRICE_LOCATION", independence_status="PARTIALLY_DEPENDENT", derived_from=["current_reference_price"],
             horizon=_hz("INSTANT", "EXPIRY_MATCHED", "snapshot.contract.expiry_ms"),
             candidate_relevance="DIRECT", information_increment="NOVEL", q1_role="PRIMARY", q2_role="SUPPORT",
             qualification_status="QUALIFIED", freshness_status="CURRENT", wpem_binding=_wpem(evidence, "level", "delta", "path"),
             reason_cn=(
                 f"距短腿责任边界{_fmt_signed_pct(_contract_core.responsibility_distance_pct(snapshot, 'short')) or '未知'}，"
                 f"距保护腿{_fmt_signed_pct(_contract_core.responsibility_distance_pct(snapshot, 'long')) or '未知'}"
                 f"（正值表示仍在该边界外），"
                 f"盈亏平衡约{_fmt_price_list(liability.get('breakeven_usd')) or '未形成'}。"
             ),
             trace_refs=["snapshot.liability", "same_candidate_window.LevelDeltaPath.responsibility"])

    quote_ok = economics.get("visible_credit_btc") is not None
    _add(units, binding, source_asset="visible_credit", source_ref="snapshot.economics.visible_credit_btc",
         asof=market.get("valuation_ts_ms"), problem_domains=["COMPENSATION", "VOLATILITY_PRICING"],
         primary_problem_domain="COMPENSATION", system_role="COMPENSATION",
         loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "quote_compensation_not_loop")],
         dependency_group="COMPENSATION", independence_status="INDEPENDENT",
         horizon=_hz("INSTANT", "EXPIRY_MATCHED", "snapshot.market.quote.exchange_times_ms"),
         candidate_relevance="DIRECT", information_increment="NOVEL" if quote_ok else "MISSING",
         q1_role="NONE" if quote_ok else "LIMITATION", q2_role="PRIMARY" if quote_ok else "LIMITATION",
         qualification_status="QUALIFIED" if quote_ok else "MISSING", freshness_status="CURRENT" if quote_ok else "MISSING",
         wpem_binding=_wpem(evidence, "level", "delta"),
         reason_cn=f"两腿可见权利金{_fmt_btc(economics.get('visible_credit_btc')) or '缺失'}，是Q2补偿账入口。",
         trace_refs=["snapshot.market.quote.exchange_times_ms", "quote_fee_deterministic_responsibility.visible_credit_btc"])

    fee_ok = fee.get("amount_btc") is not None
    _add(units, binding, source_asset="fee", source_ref="snapshot.fee.amount_btc",
         asof=market.get("valuation_ts_ms"), problem_domains=["COMPENSATION", "QUALIFICATION_DATA"],
         primary_problem_domain="COMPENSATION", system_role="COMPENSATION",
         loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "fee_cost_not_direction")],
         dependency_group="COMPENSATION", independence_status="SAME_BLOCK",
         horizon=_hz("EXPIRY_MATCHED", "EXPIRY_MATCHED", "snapshot.fee.covers"),
         candidate_relevance="DIRECT", information_increment="NOVEL" if fee_ok else "MISSING",
         q1_role="NONE" if fee_ok else "LIMITATION", q2_role="PRIMARY" if fee_ok else "LIMITATION",
         qualification_status="QUALIFIED" if fee_ok else "MISSING", freshness_status="CURRENT" if fee_ok else "MISSING",
         wpem_binding=_wpem(evidence, "level"), reason_cn=f"持有到期费用{_fmt_btc(fee.get('amount_btc')) or '缺失'}，覆盖{_fee_covers_cn(fee.get('covers'))}，修正Q2净补偿。",
         trace_refs=["snapshot.fee", "quote_fee_deterministic_responsibility.fee_btc"])

    risk_ok = _up(risk.get("status")) == "SUPPORTED" and economics.get("mu_ref_btc") is not None and economics.get("reference_margin_btc") is not None
    _add(units, binding, source_asset="risk_reference", source_ref="snapshot.risk",
         asof=risk.get("input_ts_ms"), problem_domains=["COMPENSATION", "QUALIFICATION_DATA"],
         primary_problem_domain="COMPENSATION", system_role="QUALIFICATION",
         loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "risk_reference_no_market_loop")],
         dependency_group="RISK_REFERENCE", independence_status="INDEPENDENT",
         horizon=_hz("EXPIRY_MATCHED", "EXPIRY_MATCHED", "snapshot.risk.horizon_hours"),
         candidate_relevance="DIRECT" if risk_ok else "NOT_APPLICABLE", information_increment="NOVEL" if risk_ok else "MISSING",
         q1_role="NONE" if risk_ok else "LIMITATION", q2_role="PRIMARY" if risk_ok else "LIMITATION",
         qualification_status="QUALIFIED" if risk_ok else "MISSING", freshness_status="CURRENT" if risk_ok else "MISSING",
         wpem_binding=_wpem(evidence, "level"), reason_cn=(
             f"风险参考已支持：μ={_fmt_btc(economics.get('mu_ref_btc')) or '缺失'}，参考余量={_fmt_btc(economics.get('reference_margin_btc')) or '缺失'}。"
             if risk_ok else
             "风险参考缺失或未支持，μ/M保持不可用。"
         ),
         trace_refs=["snapshot.risk", "risk_reference_status"])

    _fact_units(units, binding, snapshot, evidence, fact, facts)
    _map_units(units, binding, snapshot, evidence)
    _evidence_units(units, binding, snapshot, evidence)
    _missing_units(units, binding, facts)


def _map_ref(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    identity = _m(snapshot.get("data_identity"))
    if isinstance(identity.get("btc_map"), Mapping):
        return _m(identity.get("btc_map")), "snapshot.data_identity.btc_map"
    if isinstance(snapshot.get("btc_map"), Mapping):
        return _m(snapshot.get("btc_map")), "snapshot.btc_map"
    if isinstance(snapshot.get("map"), Mapping):
        return _m(snapshot.get("map")), "snapshot.map"
    return {}, "snapshot.data_identity.btc_map"


def _map_source_status(row: Mapping[str, Any]) -> tuple[str, str]:
    usage = _m(row.get("usage_decision") or row.get("usage_qualification"))
    status = usage.get("status") or row.get("quality") or row.get("data_state")
    return _source_status(status)


def _map_units(units: list[dict[str, Any]], binding: Mapping[str, Any], snapshot: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
    btc_map, root_ref = _map_ref(snapshot)
    if not btc_map:
        return
    decision_asof = binding.get("decision_asof_ms")
    refs = [row for row in _lst(btc_map.get("reference_pool")) if isinstance(row, Mapping)]
    background = _m(btc_map.get("background"))
    manifest = _m(btc_map.get("source_manifest"))

    if refs:
        families = ",".join(sorted({str(row.get("family") or row.get("source_family") or "UNKNOWN") for row in refs})[:4])
        q_rows = [_map_source_status(row)[0] for row in refs]
        qualified = any(status == "QUALIFIED" for status in q_rows)
        _add(units, binding, source_asset="map_reference_pool", source_ref=f"{root_ref}.reference_pool",
             asof=btc_map.get("cutoff_at_ms") or decision_asof,
             problem_domains=["RESPONSIBILITY_SPACE", "QUALIFICATION_DATA", "AUDIT_LINEAGE"],
             primary_problem_domain="RESPONSIBILITY_SPACE", system_role="STATE",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "map_reference_context_not_loop")],
             dependency_group="PRICE_LOCATION", independence_status="PARTIALLY_DEPENDENT",
             horizon=_hz("SLOW_BACKGROUND", "INTRADAY", f"{root_ref}.reference_pool"),
             candidate_relevance="CONTEXTUAL", information_increment="CONTEXT_ONLY",
             q1_role="SUPPORT" if qualified else "LIMITATION", q2_role="NONE",
             qualification_status="PARTIAL" if qualified else "UNQUALIFIED", freshness_status="UNKNOWN",
             wpem_binding=_wpem(evidence, "level"),
             reason_cn=f"MAP reference_pool保留{families or 'UNKNOWN'}等坐标引用，只作空间背景，不新增执行许可或分数。",
             trace_refs=[f"{root_ref}.reference_pool"])

    kpf_refs = [
        row for row in refs
        if "KPF" in _up(row.get("family")) or "KPF" in _up(row.get("source_family")) or "KPF" in _up(row.get("reference_id"))
        or _up(row.get("coordinate_qualification")) == "NOMINAL_ONLY" and "TRADED" in _up(row.get("family"))
    ]
    if kpf_refs:
        _add(units, binding, source_asset="kpf_nominal_reference", source_ref=f"{root_ref}.reference_pool[family=TRADED_ACCEPTANCE|KPF]",
             asof=btc_map.get("cutoff_at_ms") or decision_asof,
             problem_domains=["RESPONSIBILITY_SPACE", "QUALIFICATION_DATA"],
             primary_problem_domain="RESPONSIBILITY_SPACE", system_role="QUALIFICATION",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "kpf_nominal_only_context")],
             dependency_group="PRICE_LOCATION", independence_status="SAME_BLOCK",
             horizon=_hz("SLOW_BACKGROUND", "SLOW_BACKGROUND", f"{root_ref}.reference_pool.coordinate_qualification"),
             candidate_relevance="CONTEXTUAL", information_increment="INCONCLUSIVE",
             q1_role="LIMITATION", q2_role="NONE",
             qualification_status="PARTIAL", freshness_status="UNKNOWN", wpem_binding=_wpem(evidence, "level"),
             reason_cn="KPF/历史接受区只以NOMINAL_ONLY进入MAP背景，不能当作当前Deribit合约可比证据。",
             trace_refs=[f"{root_ref}.reference_pool", f"{root_ref}.source_manifest"])

    if background:
        for key in ["allocation", "inventory", "financing", "external_conditions"]:
            if key not in background:
                continue
            row = _m(background.get(key))
            q_status, fresh = _map_source_status(row)
            _add(units, binding, source_asset=f"map_background_{key}", source_ref=f"{root_ref}.background.{key}",
                 asof=btc_map.get("cutoff_at_ms") or decision_asof,
                 problem_domains=["EXOGENOUS_DISTURBANCE", "QUALIFICATION_DATA"],
                 primary_problem_domain="EXOGENOUS_DISTURBANCE", system_role="STATE",
                 loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "map_background_context_not_loop")],
                 dependency_group="MACRO_RISK", independence_status="PARTIALLY_DEPENDENT",
                 horizon=_hz("SLOW_BACKGROUND", "SLOW_BACKGROUND", f"{root_ref}.background.{key}"),
                 candidate_relevance="CONTEXTUAL", information_increment="CONTEXT_ONLY" if q_status == "QUALIFIED" else "INCONCLUSIVE",
                 q1_role="SUPPORT" if q_status == "QUALIFIED" else "LIMITATION", q2_role="NONE",
                 qualification_status=q_status if q_status != "MISSING" else "PARTIAL", freshness_status=fresh,
                 wpem_binding=_wpem(evidence),
                 reason_cn=f"MAP慢源背景「{key}」只保留背景状态，不能作为当前方向票或补偿输入。",
                 trace_refs=[f"{root_ref}.background.{key}"])

    if manifest:
        _add(units, binding, source_asset="map_source_manifest", source_ref=f"{root_ref}.source_manifest",
             asof=btc_map.get("cutoff_at_ms") or decision_asof,
             problem_domains=["AUDIT_LINEAGE", "QUALIFICATION_DATA"], primary_problem_domain="AUDIT_LINEAGE",
             system_role="AUDIT", loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "map_manifest_audit_only")],
             dependency_group="DATA_QUALITY", independence_status="UNKNOWN",
             horizon=_hz("INSTANT", "INSTANT", f"{root_ref}.source_manifest"),
             candidate_relevance="CONTEXTUAL", information_increment="QUALIFICATION_ONLY",
             q1_role="SUPPORT", q2_role="NONE", qualification_status="QUALIFIED", freshness_status="CURRENT",
             wpem_binding=_wpem(evidence), reason_cn="MAP source_manifest只提供来源血缘，不能把背景源升级为当前合约证据。",
             trace_refs=[f"{root_ref}.source_manifest"])


def _fact_units(units: list[dict[str, Any]], binding: Mapping[str, Any], snapshot: Mapping[str, Any], evidence: Mapping[str, Any], fact: Mapping[str, Any], facts: Mapping[str, Any]) -> None:
    side = _side(snapshot)
    decision_asof = binding.get("decision_asof_ms")
    space = _m(facts.get("space_context"))
    anchor = _m(facts.get("anchor") or space.get("anchor"))
    gate = _m(facts.get("space_gate") or {"state": space.get("space_gate_state")})
    gamma = _m(facts.get("gamma_regime") or space.get("gamma_regime"))
    gex_info = _m(space.get("gex_info"))
    volume = _m(facts.get("volume_price"))
    micro = _m(volume.get("micro_flow"))
    combined = _m(micro.get("combined"))
    macro = _m(facts.get("macro_pressure"))
    skew = _m(facts.get("skew"))
    neutral_repair = _m(facts.get("neutral_repair"))
    edb = _m(facts.get("edb"))

    if anchor:
        anchor_clock = anchor.get("gex_source_ts_ms")
        anchor_quality = anchor.get("gex_freshness") or anchor.get("data_state") or anchor.get("quality") or anchor.get("status")
        ready = (anchor.get("ready") is True or anchor.get("effective_flip_point") is not None) and _quality_ok(anchor_quality)
        flip_text = f"有效翻转位{_fmt_usd(anchor.get('effective_flip_point')) or anchor.get('effective_flip_point')}" if anchor.get("effective_flip_point") is not None else "有效翻转位缺失"
        anchor_reason = (
            f"冻结来源报告Anchor状态合格，{flip_text}，归入同源中性稳定反馈。"
            if ready else
            f"Anchor状态未合格或{flip_text}，不能支持中性回路。"
        )
        _add(units, binding, source_asset="anchor_effective_flip", source_ref="fact_context.market_facts.anchor",
             asof=anchor_clock or fact.get("snapshot_ts_ms"),
             asof_basis="SOURCE_CLOCK" if anchor_clock is not None else "PRODUCER_SNAPSHOT",
             source_observed_ms=anchor_clock,
             source_clock_status="RETAINED" if anchor_clock is not None else "NOT_RETAINED",
             problem_domains=["FEEDBACK_STABILITY", "OPTION_STRUCTURE", "RESPONSIBILITY_SPACE"],
             primary_problem_domain="FEEDBACK_STABILITY", system_role="FEEDBACK",
             loop_relations=[_rel("NEUTRAL_STABILIZING_LOOP", "SUPPORT" if ready else "INCONCLUSIVE", "anchor_attachment_retained_state")],
             dependency_group="OPTION_SPACE", independence_status="SAME_BLOCK",
             horizon=_hz("MINUTES", "INTRADAY", "fact_context.market_facts.anchor.gex_source_ts_ms"),
             candidate_relevance="CONTEXTUAL", information_increment="CONFIRMING_SAME_BLOCK" if ready else "INCONCLUSIVE",
             q1_role="SUPPORT" if ready else "LIMITATION", q2_role="NONE",
             qualification_status="QUALIFIED" if ready else "PARTIAL", freshness_status="CURRENT" if ready else "UNKNOWN",
             wpem_binding=_wpem(evidence, "level"), reason_cn=anchor_reason,
             trace_refs=["fact_context.market_facts.anchor", "same_candidate_window.LevelDeltaPath.level.anchor_gex_wall_group"])

    if gamma or gate:
        gamma_clock = gate.get("observed_at_ms") or _p(gate, "net_gamma", "source_ts_ms")
        regime = _up(gamma.get("regime"))
        state = _up(gate.get("state"))
        gamma_quality = gamma.get("data_state") or gamma.get("quality") or gate.get("data_state") or _p(gate, "net_gamma", "quality")
        gate_fresh = gate.get("freshness") or _p(gate, "net_gamma", "freshness") or _p(gate, "net_gamma", "status")
        gate_valid = _p(gate, "net_gamma", "valid") is True
        qualified_gamma = _quality_ok(gamma_quality, gate_fresh) or gate_valid
        positive_pin = qualified_gamma and (regime in {"POSITIVE_GAMMA_PINNING", "PINNING", "PINNING_REGIME"} or state == "STRUCTURE_ELIGIBLE")
        amplifying = qualified_gamma and regime in {"NEGATIVE_GAMMA_AMPLIFYING", "NEGATIVE_GAMMA", "AMPLIFYING"}
        rels: list[dict[str, str]] = []
        if positive_pin:
            rels.append(_rel("NEUTRAL_STABILIZING_LOOP", "SUPPORT", "qualified_ggr_pinning"))
        if amplifying:
            rels += [
                _rel("NEUTRAL_STABILIZING_LOOP", "CONTRADICT", "ggr_amplifying_contradicts_stability"),
                _rel("CROSS_LOOP", "MODULATE", "ggr_amplifying_cross_loop_modulator"),
            ]
        if not rels:
            rels.append(_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "ggr_state_not_enumerated"))
        _add(units, binding, source_asset="space_gate_gamma_regime", source_ref="fact_context.market_facts.gamma_regime",
             asof=gamma_clock or fact.get("snapshot_ts_ms"),
             asof_basis="SOURCE_CLOCK" if gamma_clock is not None else "PRODUCER_SNAPSHOT",
             source_observed_ms=gamma_clock,
             source_clock_status="RETAINED" if gamma_clock is not None else "NOT_RETAINED",
             problem_domains=["FEEDBACK_STABILITY", "OPTION_STRUCTURE", "QUALIFICATION_DATA"],
             primary_problem_domain="FEEDBACK_STABILITY", system_role="CONSTRAINT",
             loop_relations=rels, dependency_group="OPTION_SPACE", dependency_parent_refs=["anchor_effective_flip"],
             independence_status="SAME_BLOCK", horizon=_hz("MINUTES", "INTRADAY", "fact_context.market_facts.space_gate.observed_at_ms"),
             candidate_relevance="CONTEXTUAL", information_increment="CONFIRMING_SAME_BLOCK" if positive_pin else "COUNTER_EVIDENCE" if amplifying else "INCONCLUSIVE",
             q1_role="SUPPORT" if positive_pin else "COUNTER" if amplifying else "LIMITATION", q2_role="NONE",
             qualification_status="QUALIFIED" if qualified_gamma else "PARTIAL",
             freshness_status="CURRENT" if qualified_gamma else "UNKNOWN", wpem_binding=_wpem(evidence, "level"),
             reason_cn=(
                 "GGR现有枚举为正Gamma钉住，质量合格，支持中性稳定结构。"
                 if positive_pin else
                 "GGR现有枚举为负Gamma放大，质量合格，会削弱中性稳定结构。"
                 if amplifying else
                 "GGR/SpaceGate未给出可用稳定机制，不能支持回路。"
             ),
             trace_refs=["fact_context.market_facts.space_gate", "fact_context.market_facts.gamma_regime"])

    if gex_info:
        gex_state = _up(gex_info.get("quality") or gex_info.get("data_state") or gex_info.get("availability"))
        gex_q, gex_fresh = _source_status(gex_state)
        if not gex_state:
            gex_q, gex_fresh = "PARTIAL", "UNKNOWN"
        _add(units, binding, source_asset="gamma_walls_flip", source_ref="fact_context.market_facts.space_context.gex_info",
             asof=gex_info.get("fetched_at_ms"),
             problem_domains=["OPTION_STRUCTURE", "RESPONSIBILITY_SPACE", "FEEDBACK_STABILITY"],
             primary_problem_domain="OPTION_STRUCTURE", system_role="CONSTRAINT",
             loop_relations=[_rel("NEUTRAL_STABILIZING_LOOP", "INCONCLUSIVE", "walls_same_option_space_context")],
             dependency_group="OPTION_SPACE", dependency_parent_refs=["anchor_effective_flip", "space_gate_gamma_regime"],
             independence_status="SAME_BLOCK", horizon=_hz("MINUTES", "INTRADAY", "fact_context.market_facts.space_context.gex_info.fetched_at_ms"),
             candidate_relevance="CONTEXTUAL", information_increment="CONFIRMING_SAME_BLOCK" if gex_q == "QUALIFIED" else "INCONCLUSIVE",
             q1_role="SUPPORT" if gex_q == "QUALIFIED" else "LIMITATION", q2_role="NONE",
             qualification_status=gex_q, freshness_status=gex_fresh, wpem_binding=_wpem(evidence, "level"),
             reason_cn=(
                 f"墙位引用：翻转位{_fmt_usd(gex_info.get('flip_point')) or '缺失'}，"
                 f"支撑墙{_fmt_price_list(gex_info.get('support_walls')) or '缺失'}，"
                 f"压力墙{_fmt_price_list(gex_info.get('resistance_walls')) or '缺失'}；与Anchor/GEX同源归组。"
             ),
             trace_refs=["fact_context.market_facts.space_context.gex_info"])

    if volume:
        if volume.get("tmvf_24h") or volume.get("tmvf_48h") or volume.get("tmv_blend") is not None or volume.get("direction"):
            tmv_dir = _flow_direction(volume.get("direction")) or _up(volume.get("direction")) or "UNKNOWN"
            tmv_source_clock = (
                _p(volume, "tmvf_24h", "core", "kline_open_time")
                or _p(volume, "tmvf_48h", "core", "kline_open_time")
                or _p(volume, "tmvf_24h", "kline_open_time")
                or _p(volume, "tmvf_48h", "kline_open_time")
            )
            _add(units, binding, source_asset="tmv_price_path", source_ref="fact_context.market_facts.volume_price",
                 asof=tmv_source_clock or fact.get("snapshot_ts_ms"), problem_domains=["PATH_DYNAMICS", "DIRECTIONAL_PRESSURE"],
                 primary_problem_domain="PATH_DYNAMICS", system_role="STATE",
                 loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "tmv_price_path_not_second_flow_vote")],
                 dependency_group="PRICE_PATH", independence_status="PARTIALLY_DEPENDENT",
                 horizon=_hz("MULTIDAY", "INTRADAY", "fact_context.market_facts.volume_price.tmvf_24h_48h"),
                 candidate_relevance="CONTEXTUAL", information_increment="CONTEXT_ONLY", q1_role="NONE", q2_role="NONE",
                 qualification_status="QUALIFIED", freshness_status="CURRENT", wpem_binding=_wpem(evidence, "path"),
                 asof_basis="SOURCE_CLOCK" if tmv_source_clock is not None else "PRODUCER_SNAPSHOT",
                 source_observed_ms=tmv_source_clock,
                 source_clock_status="RETAINED" if tmv_source_clock is not None else "NOT_RETAINED",
                 reason_cn=f"TMV 24h/48h方向{_tmv_cn(tmv_dir)}，作为多日价格路径背景，不作为第二个主动流投票。",
                 trace_refs=["fact_context.market_facts.volume_price.tmvf_24h", "fact_context.market_facts.volume_price.tmvf_48h"])

        direction = _flow_direction(combined.get("direction"))
        ready = combined.get("data_ready") is True and _ready_horizons_ready(combined.get("ready_horizons"))
        flow_source_clock = combined.get("window_end_time") or combined.get("source_ts_ms")
        if direction and ready:
            loop = "BULLISH_LOOP" if direction == "UP" else "BEARISH_LOOP"
            q1, q2, reason, q1_reason, q2_reason = _flow_roles(side, direction, combined.get("ready_horizons"))
            _add(units, binding, source_asset="active_flow_cvd", source_ref="fact_context.market_facts.volume_price.micro_flow.combined",
                 asof=flow_source_clock or fact.get("snapshot_ts_ms"), problem_domains=["FLOW_REFLEXIVITY", "DIRECTIONAL_PRESSURE"],
                 primary_problem_domain="FLOW_REFLEXIVITY", system_role="FEEDBACK",
                 loop_relations=[_rel(loop, "SUPPORT", "covered_active_flow_direction")],
                 dependency_group="FLOW", dependency_parent_refs=["tmv_price_path"], independence_status="PARTIALLY_DEPENDENT",
                 horizon=_hz("INTRADAY", "INTRADAY", "fact_context.market_facts.volume_price.micro_flow.combined.ready_horizons"),
                 candidate_relevance="CONTEXTUAL", information_increment="COUNTER_EVIDENCE" if q1 == "COUNTER" else "NOVEL",
                 q1_role=q1, q2_role=q2, qualification_status="QUALIFIED", freshness_status="CURRENT",
                 wpem_binding=_wpem(evidence, "path"), reason_cn=reason,
                 q1_reason_cn=q1_reason, q2_reason_cn=q2_reason,
                 asof_basis="SOURCE_CLOCK" if flow_source_clock is not None else "PRODUCER_SNAPSHOT",
                 source_observed_ms=flow_source_clock,
                 source_clock_status="RETAINED" if flow_source_clock is not None else "NOT_RETAINED",
                 trace_refs=["fact_context.market_facts.volume_price.micro_flow.combined"])
        elif combined:
            _add(units, binding, source_asset="active_flow_cvd_unqualified", source_ref="fact_context.market_facts.volume_price.micro_flow.combined",
                 asof=flow_source_clock or fact.get("snapshot_ts_ms"), problem_domains=["FLOW_REFLEXIVITY", "QUALIFICATION_DATA"],
                 primary_problem_domain="FLOW_REFLEXIVITY", system_role="QUALIFICATION",
                 loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "flow_missing_covered_direction")],
                 dependency_group="FLOW", independence_status="UNKNOWN", horizon=_hz("UNKNOWN", "INTRADAY"),
                 candidate_relevance="CONTEXTUAL", information_increment="INCONCLUSIVE", q1_role="LIMITATION", q2_role="NONE",
                 qualification_status="PARTIAL", freshness_status="UNKNOWN", wpem_binding=_wpem(evidence, "path"),
                 asof_basis="SOURCE_CLOCK" if flow_source_clock is not None else "PRODUCER_SNAPSHOT",
                 source_observed_ms=flow_source_clock,
                 source_clock_status="RETAINED" if flow_source_clock is not None else "NOT_RETAINED",
                 reason_cn="主动流缺少合格覆盖或明确枚举方向，不能支持回路。",
                 trace_refs=["fact_context.market_facts.volume_price.micro_flow.combined"])

        if volume.get("funding") is not None:
            funding_source_clock = _p(volume, "funding", "window_end_time") or _p(volume, "funding", "tmvf_24h", "window_end_time")
            _add(units, binding, source_asset="funding_context", source_ref="fact_context.market_facts.volume_price.funding",
                 asof=funding_source_clock or fact.get("snapshot_ts_ms"), problem_domains=["FLOW_REFLEXIVITY", "COMPENSATION"],
                 primary_problem_domain="FLOW_REFLEXIVITY", system_role="STATE",
                 loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "funding_sign_not_mechanism")],
                 dependency_group="FLOW", independence_status="PARTIALLY_DEPENDENT",
                 horizon=_hz("INTRADAY", "SLOW_BACKGROUND", "fact_context.market_facts.volume_price.funding"),
                 candidate_relevance="CONTEXTUAL", information_increment="CONTEXT_ONLY", q1_role="NONE", q2_role="NONE",
                 qualification_status="QUALIFIED", freshness_status="CURRENT", wpem_binding=_wpem(evidence),
                 asof_basis="SOURCE_CLOCK" if funding_source_clock is not None else "PRODUCER_SNAPSHOT",
                 source_observed_ms=funding_source_clock,
                 source_clock_status="RETAINED" if funding_source_clock is not None else "NOT_RETAINED",
                 reason_cn="Funding保留为资金费/拥挤背景，不能按符号自动给方向。",
                 trace_refs=["fact_context.market_facts.volume_price.funding"])

    if skew:
        ok = _up(skew.get("data_state")) == "OK"
        _add(units, binding, source_asset="srd_skew_aggregate", source_ref="fact_context.market_facts.skew",
             asof=skew.get("greeks_epoch_ms") or decision_asof,
             problem_domains=["OPTION_STRUCTURE", "VOLATILITY_PRICING"], primary_problem_domain="OPTION_STRUCTURE",
             system_role="FEEDBACK" if ok else "QUALIFICATION",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "skew_sign_not_direction_mechanism")],
             dependency_group="OPTION_DEMAND", independence_status="PARTIALLY_DEPENDENT",
             horizon=_hz("INTRADAY", "INTRADAY", "fact_context.market_facts.skew.greeks_epoch_ms"),
             candidate_relevance="CONTEXTUAL" if ok else "WEAK", information_increment="CONTEXT_ONLY" if ok else "MISSING",
             q1_role="NONE" if ok else "LIMITATION", q2_role="NONE",
             qualification_status="QUALIFIED" if ok else "PARTIAL", freshness_status="CURRENT" if ok else "UNKNOWN",
             wpem_binding=_wpem(evidence), reason_cn="SRD/skew聚合可作为期权需求背景，但不按skew符号制造方向回路。",
             trace_refs=["fact_context.market_facts.skew"])

    if neutral_repair:
        nr_state = _up(neutral_repair.get("state"))
        fact_q, fact_fresh = _source_status(fact.get("status"))
        if fact_q == "MISSING":
            fact_q, fact_fresh = "PARTIAL", "UNKNOWN"
        active = neutral_repair.get("is_active") is True
        _add(units, binding, source_asset="neutral_repair_state", source_ref="fact_context.market_facts.neutral_repair",
             asof=fact.get("snapshot_ts_ms") or decision_asof,
             problem_domains=["TIMING_WINDOW", "PATH_DYNAMICS", "FEEDBACK_STABILITY"],
             primary_problem_domain="TIMING_WINDOW", system_role="STATE",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "neutral_repair_temporal_state_only")],
             dependency_group="PRICE_PATH", independence_status="PARTIALLY_DEPENDENT",
             horizon=_hz("INTRADAY", "INTRADAY", "fact_context.market_facts.neutral_repair.state"),
             candidate_relevance="CONTEXTUAL", information_increment="CONTEXT_ONLY",
             q1_role="SUPPORT" if fact_q in {"QUALIFIED", "SYNTHETIC"} else "LIMITATION", q2_role="NONE",
             qualification_status=fact_q if fact_q != "MISSING" else "PARTIAL", freshness_status=fact_fresh,
             wpem_binding=_wpem(evidence, "path"),
             reason_cn=f"NeutralRepair状态{nr_state or 'UNKNOWN'}，active={active}，只描述修复/位移窗口，不作为方向票。",
             trace_refs=["fact_context.market_facts.neutral_repair", "same_candidate_window.LevelDeltaPath.path"])

    if edb:
        _add(units, binding, source_asset="edb_legacy_derived_signal", source_ref="fact_context.market_facts.edb",
             asof=fact.get("snapshot_ts_ms") or decision_asof,
             problem_domains=["FLOW_REFLEXIVITY", "AUDIT_LINEAGE", "QUALIFICATION_DATA"],
             primary_problem_domain="AUDIT_LINEAGE", system_role="AUDIT",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "edb_derived_no_second_support")],
             dependency_group="FLOW", independence_status="SAME_BLOCK",
             derived_from=["active_flow_cvd", "tmv_price_path", "macro_pressure"],
             horizon=_hz("INTRADAY", "INTRADAY", "fact_context.market_facts.edb"),
             candidate_relevance="WEAK", information_increment="REDUNDANT",
             q1_role="NONE", q2_role="NONE", qualification_status="PARTIAL", freshness_status="UNKNOWN",
             wpem_binding=_wpem(evidence),
             reason_cn="EDB旧派生信号只保留血缘，不能作为独立第二票或回路支持。",
             trace_refs=["fact_context.market_facts.edb"])

    if macro:
        shock = _m(macro.get("macro_shock"))
        block = shock.get("block") is True
        macro_state = _up(macro.get("data_status"))
        macro_q, macro_fresh = _source_status(macro_state)
        if not macro_state:
            macro_q, macro_fresh = "PARTIAL", "UNKNOWN"
        macro_qualified = macro_q == "QUALIFIED"
        if macro_qualified:
            macro_rels = [_rel("CROSS_LOOP", "CONSTRAIN" if block else "MODULATE", "macro_cross_loop_disturbance")]
            macro_increment = "COUNTER_EVIDENCE" if block else "CONTEXT_ONLY"
            macro_q1 = "LIMITATION" if block else "SUPPORT"
            macro_q2 = "LIMITATION" if block else "NONE"
            macro_role = "CONSTRAINT" if block else "DISTURBANCE"
            macro_relevance = "CONTEXTUAL"
            macro_reason = "宏观扰动默认调制跨回路，不自动变成强空头票。"
        else:
            macro_rels = [_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "macro_unqualified_no_loop")]
            macro_increment = "INCONCLUSIVE"
            macro_q1 = "LIMITATION"
            macro_q2 = "NONE"
            macro_role = "QUALIFICATION"
            macro_relevance = "WEAK"
            macro_reason = "宏观状态未合格，保留为资料限制，不进入跨回路调制。"
        _add(units, binding, source_asset="macro_pressure", source_ref="fact_context.market_facts.macro_pressure",
             asof=macro.get("last_data_time"),
             problem_domains=["EXOGENOUS_DISTURBANCE", "QUALIFICATION_DATA"], primary_problem_domain="EXOGENOUS_DISTURBANCE",
             system_role=macro_role,
             loop_relations=macro_rels,
             dependency_group="MACRO_RISK", independence_status="INDEPENDENT",
             horizon=_hz("SLOW_BACKGROUND", "SLOW_BACKGROUND", "fact_context.market_facts.macro_pressure.last_data_time"),
             candidate_relevance=macro_relevance, information_increment=macro_increment,
             q1_role=macro_q1, q2_role=macro_q2,
             qualification_status=macro_q, freshness_status=macro_fresh,
             wpem_binding=_wpem(evidence), reason_cn=macro_reason,
             trace_refs=["fact_context.market_facts.macro_pressure"])

    if fact.get("source_quality") is not None or facts.get("source_trace") is not None:
        status, fresh = _source_status(_p(facts, "source_trace", "data_state") or fact.get("status"))
        _add(units, binding, source_asset="source_quality", source_ref="snapshot.fact_context.source_quality",
             asof=fact.get("snapshot_ts_ms"), problem_domains=["QUALIFICATION_DATA", "AUDIT_LINEAGE"],
             primary_problem_domain="QUALIFICATION_DATA", system_role="QUALIFICATION",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "source_quality_not_market_loop")],
             dependency_group="DATA_QUALITY", independence_status="UNKNOWN",
             horizon=_hz("INSTANT", "INSTANT", "snapshot.fact_context.snapshot_ts_ms"),
             candidate_relevance="DIRECT", information_increment="QUALIFICATION_ONLY",
             q1_role="NONE" if status == "QUALIFIED" else "LIMITATION",
             q2_role="NONE" if status == "QUALIFIED" else "LIMITATION",
             qualification_status=status, freshness_status=fresh, wpem_binding=_wpem(evidence),
             reason_cn="Source quality只限定资料可用性，不构成市场方向。",
             trace_refs=["snapshot.fact_context.source_quality", "fact_context.market_facts.source_trace"])


def _evidence_units(units: list[dict[str, Any]], binding: Mapping[str, Any], snapshot: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
    same = _m(evidence.get("same_candidate_window"))
    ldp = _m(same.get("LevelDeltaPath"))
    path = _m(ldp.get("path"))
    delta = _m(ldp.get("delta"))
    qualification = _m(same.get("qualification"))
    coverage = _m(same.get("coverage_summary"))
    method_breaks = _m(same.get("source_method_breaks"))
    window_status = _up(qualification.get("status") or same.get("status"))
    path_observed = _up(path.get("status")) == "PATH_OBSERVED"
    q_status = "SYNTHETIC" if "SYNTHETIC" in _up(qualification.get("kind")) or "SYNTHETIC" in window_status else "QUALIFIED" if path_observed else "PARTIAL"
    path_classification = path.get("classification") or path.get("status")
    path_text = PATH_CLASSIFICATION_CN.get(str(path_classification), PATH_CLASSIFICATION_CN["gap"])

    if same:
        _add(units, binding, source_asset="wpem_window_path", source_ref="same_candidate_window.LevelDeltaPath.path",
             asof=evidence.get("decision_asof_ms"), problem_domains=["TIMING_WINDOW", "PATH_DYNAMICS", "QUALIFICATION_DATA"],
             primary_problem_domain="PATH_DYNAMICS", system_role="QUALIFICATION",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "wpem_path_temporal_dimension")],
             dependency_group="PRICE_PATH", independence_status="PARTIALLY_DEPENDENT", derived_from=["current_reference_price", "responsibility_distance"],
             horizon=_hz("15M", "1H", "same_candidate_window.coverage_summary.windows"),
             candidate_relevance="DIRECT", information_increment="NOVEL" if path_observed else "QUALIFICATION_ONLY",
             q1_role="PRIMARY" if path_observed else "LIMITATION", q2_role="SUPPORT" if path_observed else "LIMITATION",
             qualification_status=q_status, freshness_status="CURRENT" if path_observed else "UNKNOWN",
             wpem_binding=_wpem(evidence, "level", "delta", "path"),
             reason_cn=path_text,
             trace_refs=["same_candidate_window.LevelDeltaPath", "same_candidate_window.coverage_summary"])

        if delta.get("changes"):
            # A credit or margin move is observed change, not automatic worsening:
            # thinner credit can accompany falling risk (time decay, distance).
            changes = _m(delta.get("changes"))
            parts = []
            credit_text = _fmt_signed_btc(changes.get("visible_credit_btc_delta"), noun="可收信用")
            margin_text = _fmt_signed_btc(changes.get("M_ref_btc_delta"), noun="参考余量")
            if credit_text:
                parts.append(credit_text)
            if margin_text:
                parts.append(margin_text)
            delta_text = "，".join(parts) if parts else "同窗补偿变化尚不可同口径比较"
            _add(units, binding, source_asset="wpem_delta", source_ref="same_candidate_window.LevelDeltaPath.delta.changes",
                 asof=evidence.get("decision_asof_ms"), problem_domains=["PATH_DYNAMICS", "COMPENSATION"],
                 primary_problem_domain="PATH_DYNAMICS", system_role="STATE",
                 loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "wpem_delta_not_loop_score")],
                 dependency_group="PRICE_PATH", independence_status="PARTIALLY_DEPENDENT", derived_from=["wpem_window_path", "visible_credit"],
                 horizon=_hz("15M", "1H", "same_candidate_window.LevelDeltaPath.delta"),
                 candidate_relevance="DIRECT", information_increment="NOVEL",
                 q1_role="NONE", q2_role="REASSESS",
                 qualification_status=q_status, freshness_status="CURRENT" if path_observed else "UNKNOWN", wpem_binding=_wpem(evidence, "delta"),
                 reason_cn=f"同候选窗口内，{delta_text}；补偿账变化需与责任位置一并重新权衡。",
                 q1_reason_cn=None,
                 q2_reason_cn=f"同候选窗口内，{delta_text}；Q2按当前补偿账重新权衡。",
                 trace_refs=["same_candidate_window.LevelDeltaPath.delta.changes"])

        _add(units, binding, source_asset="wpem_coverage", source_ref="same_candidate_window.coverage_summary",
             asof=evidence.get("decision_asof_ms"), problem_domains=["TIMING_WINDOW", "QUALIFICATION_DATA"],
             primary_problem_domain="TIMING_WINDOW", system_role="QUALIFICATION",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "coverage_is_data_completeness_only")],
             dependency_group="DATA_QUALITY", independence_status="UNKNOWN",
             horizon=_hz("15M", "1H", "same_candidate_window.coverage_summary.windows"),
             candidate_relevance="DIRECT", information_increment="QUALIFICATION_ONLY",
             q1_role="NONE" if path_observed else "LIMITATION", q2_role="NONE" if path_observed else "LIMITATION",
             qualification_status=q_status, freshness_status="CURRENT" if coverage.get("windows") else "UNKNOWN", wpem_binding=_wpem(evidence, "path"),
             reason_cn=_coverage_reason_cn(qualification, coverage, same.get("status")),
             trace_refs=["same_candidate_window.coverage_summary.policy", "same_candidate_window.qualification"])

        for flag, asset, text in (
            ("method_break_observed", "wpem_method_break", "WPEM检测到取样方法断裂，需要重新评估同候选路径。"),
            ("candidate_rotation_observed", "wpem_candidate_rotation", "WPEM检测到候选轮换，旧路径不能外推到当前候选。"),
        ):
            if method_breaks.get(flag) is True:
                _add(units, binding, source_asset=asset, source_ref=f"same_candidate_window.source_method_breaks.{flag}",
                     asof=evidence.get("decision_asof_ms"), problem_domains=["TIMING_WINDOW", "QUALIFICATION_DATA", "AUDIT_LINEAGE"],
                     primary_problem_domain="QUALIFICATION_DATA", system_role="QUALIFICATION",
                     loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "wpem_source_method_break_reassess")],
                     dependency_group="DATA_QUALITY", independence_status="UNKNOWN",
                     horizon=_hz("15M", "1H", "same_candidate_window.source_method_breaks"),
                     candidate_relevance="DIRECT", information_increment="INCONCLUSIVE",
                     q1_role="REASSESS", q2_role="REASSESS", qualification_status="BLOCKED", freshness_status="UNKNOWN",
                     wpem_binding=_wpem(evidence, "path"), reason_cn=text,
                     trace_refs=["same_candidate_window.source_method_breaks"])

    review_risk = _m(evidence.get("risk_reference_status") or evidence.get("risk_status"))
    if review_risk:
        terminal = _up(review_risk.get("terminal_status") or review_risk.get("status"))
        qualified = "QUALIFIED" in terminal and "NOT" not in terminal and "MISSING" not in terminal
        _add(units, binding, source_asset="review_risk_status", source_ref="risk_reference_status",
             asof=evidence.get("decision_asof_ms"), problem_domains=["QUALIFICATION_DATA", "COMPENSATION"],
             primary_problem_domain="QUALIFICATION_DATA", system_role="QUALIFICATION",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "risk_status_not_loop")],
             dependency_group="RISK_REFERENCE", independence_status="SAME_BLOCK",
             horizon=_hz("EXPIRY_MATCHED", "EXPIRY_MATCHED", "risk_reference_status"),
             candidate_relevance="DIRECT" if qualified else "NOT_APPLICABLE",
             information_increment="QUALIFICATION_ONLY" if qualified else "MISSING",
             q1_role="NONE" if qualified else "LIMITATION", q2_role="PRIMARY" if qualified else "LIMITATION",
             qualification_status="QUALIFIED" if qualified else "MISSING", freshness_status="CURRENT" if qualified else "MISSING",
             wpem_binding=_wpem(evidence), reason_cn="Review risk status只说明风险参考是否可用，不能补造μ/M或市场反证。",
             trace_refs=["risk_reference_status"])

    _add(units, binding, source_asset="review_evidence_lineage", source_ref="source_bindings",
         asof=evidence.get("decision_asof_ms"), problem_domains=["AUDIT_LINEAGE", "QUALIFICATION_DATA"],
         primary_problem_domain="AUDIT_LINEAGE", system_role="AUDIT",
         loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "review_packet_lineage_audit_only")],
         dependency_group="DATA_QUALITY", independence_status="UNKNOWN",
         horizon=_hz("INSTANT", "INSTANT", "source_bindings"), candidate_relevance="DIRECT",
         information_increment="QUALIFICATION_ONLY", q1_role="NONE", q2_role="NONE",
         qualification_status="QUALIFIED", freshness_status="CURRENT", wpem_binding=_wpem(evidence),
         reason_cn="Review Evidence来源绑定用于重放审计，不直接形成Q1/Q2观点。",
         trace_refs=["source_bindings", "same_candidate_window.source_window_evidence_hash"])

    rv = _m(evidence.get("review_validity"))
    if rv:
        _add(units, binding, source_asset="review_validity", source_ref="review_validity",
             asof=rv.get("quote_timestamp_ms"), problem_domains=["AUDIT_LINEAGE", "QUALIFICATION_DATA"],
             primary_problem_domain="AUDIT_LINEAGE", system_role="AUDIT",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "review_binding_audit_only")],
             dependency_group="DATA_QUALITY", independence_status="UNKNOWN",
             horizon=_hz("INSTANT", "EXPIRY_MATCHED", "review_validity.valid_until_ms"),
             candidate_relevance="DIRECT", information_increment="QUALIFICATION_ONLY", q1_role="REASSESS", q2_role="REASSESS",
             qualification_status="QUALIFIED", freshness_status="CURRENT", wpem_binding=_wpem(evidence),
             reason_cn="Review validity只约束当前回执是否仍绑定同一报价、风险和窗口证据。",
             trace_refs=["review_validity"])

    if evidence.get("current_cross_section"):
        _add(units, binding, source_asset="current_cross_section", source_ref="current_cross_section",
             asof=evidence.get("decision_asof_ms"), problem_domains=["COMPENSATION", "QUALIFICATION_DATA"],
             primary_problem_domain="COMPENSATION", system_role="STATE",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "peer_rows_do_not_fill_path")],
             dependency_group="COMPENSATION", independence_status="UNKNOWN",
             horizon=_hz("INSTANT", "INSTANT", "current_cross_section"),
             candidate_relevance="CONTEXTUAL", information_increment="CONTEXT_ONLY", q1_role="NONE", q2_role="SUPPORT",
             qualification_status="PARTIAL", freshness_status="UNKNOWN", wpem_binding=_wpem(evidence),
             reason_cn="Cross-section只作同截面对照，不能填补同候选路径或EV。",
             trace_refs=["current_cross_section.asof_join_rule", "current_cross_section.rows"])


def _missing_units(units: list[dict[str, Any]], binding: Mapping[str, Any], facts: Mapping[str, Any]) -> None:
    skew = _m(facts.get("skew"))
    space = _m(facts.get("space_context"))
    gex_info = _m(space.get("gex_info"))
    gamma = _m(facts.get("gamma_regime") or space.get("gamma_regime"))
    missing: list[tuple[str, str, str, str, str, str]] = []
    if not isinstance(skew.get("per_expiry"), Mapping):
        missing.append(("term_structure", "factor_cross_section.skew.per_expiry", "OPTION_STRUCTURE", "OPTION_DEMAND", "LIMITATION", "期限结构未进入当前已冻结事实。"))
    if not any(_p(skew, "per_expiry", key, "atm_iv") is not None for key in ("24h", "48h")):
        missing.append(("iv_atm", "factor_cross_section.skew.per_expiry.*.atm_iv", "VOLATILITY_PRICING", "VOLATILITY_STRUCTURE", "NONE", "IV未进入当前已冻结事实。"))
    missing.append(("rv_realized", "realized_volatility", "VOLATILITY_PRICING", "VOLATILITY_STRUCTURE", "NONE", "BTC RV未在当前已冻结事实中发现。"))
    if gex_info.get("dvol") is None:
        missing.append(("dvol", "factor_cross_section.gex_info.dvol", "VOLATILITY_PRICING", "VOLATILITY_STRUCTURE", "NONE", "DVOL未进入当前fact_context投影。"))
    if gamma.get("max_gamma_oi_share") is None and not isinstance(skew.get("option_oi"), Mapping):
        missing.append(("raw_option_oi", "deribit.option.open_interest", "OPTION_STRUCTURE", "OPTION_DEMAND", "LIMITATION", "原始Deribit option OI未作为当前路由事实保留。"))

    for asset, ref, domain, group, q1_role, reason in missing:
        _add(units, binding, source_asset=asset, source_ref=ref, asof=None,
             problem_domains=[domain, "QUALIFICATION_DATA"], primary_problem_domain=domain, system_role="QUALIFICATION",
             loop_relations=[_rel("OBSERVATION_ONLY", "INCONCLUSIVE", "missing_retained_route")],
             dependency_group=group, independence_status="UNKNOWN", horizon=_hz("UNKNOWN", "UNKNOWN"),
             candidate_relevance="NOT_APPLICABLE", information_increment="MISSING",
             q1_role=q1_role, q2_role="LIMITATION" if domain == "VOLATILITY_PRICING" else "NONE",
             qualification_status="MISSING", freshness_status="MISSING", wpem_binding={},
             reason_cn=reason, trace_refs=[ref])


def _has_rel(unit: Mapping[str, Any], relation: str) -> bool:
    return any(row.get("loop") != "OBSERVATION_ONLY" and row.get("relation") == relation for row in _lst(unit.get("loop_relations")))


def _representative(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    def key(row: Mapping[str, Any]) -> tuple[int, str]:
        if row.get("q1_role") == "COUNTER" or row.get("q2_role") == "COUNTER" or row.get("information_increment") == "COUNTER_EVIDENCE":
            rank = 0
        elif row.get("q1_role") == "PRIMARY" or row.get("q2_role") == "PRIMARY" or row.get("information_increment") == "NOVEL":
            rank = 1
        elif row.get("information_increment") == "QUALIFICATION_ONLY":
            rank = 2
        elif row.get("information_increment") == "MISSING":
            rank = 3
        else:
            rank = 4
        return rank, str(row.get("evidence_id"))
    return sorted(rows, key=key)[0]


def _dependency_blocks(units: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for unit in units:
        grouped.setdefault(str(unit["dependency_group"]), []).append(unit)
    blocks = []
    for group in sorted(grouped):
        rows = grouped[group]
        support = [row["evidence_id"] for row in rows if _has_rel(row, "SUPPORT")]
        counter = [row["evidence_id"] for row in rows if _has_rel(row, "CONTRADICT") or row.get("q1_role") == "COUNTER" or row.get("q2_role") == "COUNTER" or row.get("information_increment") == "COUNTER_EVIDENCE"]
        limitation = [row["evidence_id"] for row in rows if row.get("q1_role") == "LIMITATION" or row.get("q2_role") == "LIMITATION" or row.get("information_increment") in {"MISSING", "QUALIFICATION_ONLY"}]
        blocks.append({
            "block_id": "block." + group.lower(),
            "dependency_group": group,
            "evidence_ids": [row["evidence_id"] for row in rows],
            "representative_id": _representative(rows)["evidence_id"],
            "support_ids": support[:2],
            "counter_ids": counter[:2],
            "limitation_ids": limitation[:2],
            "facet_policy": "up_to_two_per_facet_no_independence_count",
            "independence_statuses": sorted(set(str(row.get("independence_status")) for row in rows)),
        })
    return blocks


def _block_id(blocks: Sequence[Mapping[str, Any]], unit: Mapping[str, Any]) -> str:
    for block in blocks:
        if block.get("dependency_group") == unit.get("dependency_group"):
            return str(block["block_id"])
    return "block.unknown"


PROBLEM_DOMAIN_ASSET_PRIORITY = {
    "RESPONSIBILITY_SPACE": ["responsibility_distance", "current_reference_price", "map_reference_pool"],
    "TIMING_WINDOW": ["wpem_window_path", "wpem_coverage", "neutral_repair_state", "wpem_method_break", "wpem_candidate_rotation"],
    "FEEDBACK_STABILITY": ["space_gate_gamma_regime", "anchor_effective_flip", "neutral_repair_state", "gamma_walls_flip"],
    "DIRECTIONAL_PRESSURE": ["active_flow_cvd", "tmv_price_path", "active_flow_cvd_unqualified"],
    "PATH_DYNAMICS": ["wpem_window_path", "tmv_price_path", "neutral_repair_state", "wpem_delta"],
    "FLOW_REFLEXIVITY": ["active_flow_cvd", "active_flow_cvd_unqualified", "funding_context", "edb_legacy_derived_signal"],
    "OPTION_STRUCTURE": ["space_gate_gamma_regime", "anchor_effective_flip", "gamma_walls_flip", "srd_skew_aggregate"],
    "VOLATILITY_PRICING": ["iv_atm", "dvol", "rv_realized", "srd_skew_aggregate", "visible_credit"],
    "COMPENSATION": ["visible_credit", "fee", "risk_reference", "wpem_delta"],
    "EXOGENOUS_DISTURBANCE": ["macro_pressure", "map_background_external_conditions", "map_background_financing"],
    "QUALIFICATION_DATA": ["wpem_method_break", "wpem_candidate_rotation", "wpem_coverage", "source_quality", "review_risk_status", "risk_reference"],
    "AUDIT_LINEAGE": ["review_validity", "review_evidence_lineage", "map_source_manifest", "edb_legacy_derived_signal"],
}


def _problem_representative(domain: str, domain_units: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    primary = [] if domain == "QUALIFICATION_DATA" else [unit for unit in domain_units if unit.get("primary_problem_domain") == domain]
    pool = primary or list(domain_units)
    priority = PROBLEM_DOMAIN_ASSET_PRIORITY.get(domain, [])

    def key(unit: Mapping[str, Any]) -> tuple[int, int, int, str]:
        asset = str(unit.get("source_asset"))
        asset_rank = priority.index(asset) if asset in priority else len(priority) + 1
        limit_rank = 0 if unit.get("q1_role") in {"REASSESS", "LIMITATION"} or unit.get("q2_role") in {"REASSESS", "LIMITATION"} else 1
        direct_rank = 0 if unit.get("candidate_relevance") == "DIRECT" else 1
        return asset_rank, limit_rank, direct_rank, str(unit.get("evidence_id"))

    return sorted(pool, key=key)[0]


def _problem_map(units: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for domain in sorted({domain for unit in units for domain in _lst(unit.get("problem_domains"))}):
        domain_units = [unit for unit in units if domain in _lst(unit.get("problem_domains"))]
        representative = _problem_representative(domain, domain_units)
        has_direct = any(unit.get("candidate_relevance") == "DIRECT" for unit in domain_units)
        has_missing = any(unit.get("information_increment") == "MISSING" for unit in domain_units)
        rows.append({
            "domain": domain,
            "label_cn": DOMAIN_LABELS_CN.get(domain, domain),
            "evidence_ids": [unit["evidence_id"] for unit in domain_units],
            "representative_id": representative["evidence_id"],
            "summary_cn": representative["reason_cn"],
            "coverage_status": "DIRECT_WITH_GAPS" if has_direct and has_missing else "DIRECT" if has_direct else "GAPPED" if has_missing else "CONTEXT_OR_QUALIFICATION",
            "limitations": list(dict.fromkeys(
                unit["reason_cn"] for unit in domain_units
                if unit.get("information_increment") == "MISSING" or unit.get("q1_role") == "LIMITATION" or unit.get("q2_role") == "LIMITATION"
            ))[:4],
        })
    return rows


def _uniq_limitations(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out, seen = [], set()
    for item in items:
        key = (item.get("block_id"), item.get("evidence_id"), item.get("reason_cn"))
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(item))
    return out[:6]


def _loop_digest(units: Sequence[Mapping[str, Any]], blocks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    group_to_block = {block["dependency_group"]: block["block_id"] for block in blocks}

    def one(loop: str) -> dict[str, Any]:
        supports, counters, limitations = [], [], []
        for unit in units:
            block = group_to_block.get(unit.get("dependency_group"))
            rels = [row for row in _lst(unit.get("loop_relations")) if row.get("loop") == loop]
            if any(row.get("relation") == "SUPPORT" for row in rels):
                supports.append(block)
            if any(row.get("relation") in {"CONTRADICT", "CONSTRAIN"} for row in rels):
                counters.append(block)
            if rels and (
                any(row.get("relation") == "CONSTRAIN" for row in rels)
                or unit.get("q1_role") == "LIMITATION"
                or unit.get("q2_role") == "LIMITATION"
                or unit.get("information_increment") == "MISSING"
            ):
                limitations.append({"block_id": block, "evidence_id": unit["evidence_id"], "reason_cn": unit["reason_cn"]})
        return {
            "support_blocks": list(dict.fromkeys(block for block in supports if block)),
            "counter_blocks": list(dict.fromkeys(block for block in counters if block)),
            "limitations": _uniq_limitations(row for row in limitations if row.get("block_id")),
        }

    cross = []
    for unit in units:
        for rel in _lst(unit.get("loop_relations")):
            if rel.get("loop") == "CROSS_LOOP":
                cross.append({
                    "block_id": group_to_block.get(unit.get("dependency_group")),
                    "evidence_id": unit["evidence_id"],
                    "relation": rel.get("relation"),
                    "reason_cn": unit["reason_cn"],
                })
    unresolved = [
        {"evidence_id": unit["evidence_id"], "reason_cn": unit["reason_cn"]}
        for unit in units
        if unit.get("information_increment") in {"INCONCLUSIVE", "MISSING"}
    ][:8]
    bullish = one("BULLISH_LOOP")
    neutral = one("NEUTRAL_STABILIZING_LOOP")
    bearish = one("BEARISH_LOOP")
    if neutral["support_blocks"] and bearish["support_blocks"]:
        cross.insert(0, {
            "block_id": "cross.neutral_bearish_coexist",
            "evidence_id": None,
            "relation": "MODULATE",
            "reason_cn": "中性稳定块与主动流空头块同时存在；这里只提示两类机制并存，不生成胜负裁决。",
            "support_blocks": {
                "neutral_stabilizing_loop": neutral["support_blocks"],
                "bearish_loop": bearish["support_blocks"],
            },
        })
    if neutral["support_blocks"] and bullish["support_blocks"]:
        cross.insert(0, {
            "block_id": "cross.neutral_bullish_coexist",
            "evidence_id": None,
            "relation": "MODULATE",
            "reason_cn": "中性稳定块与主动流多头块同时存在；这里只提示两类机制并存，不生成胜负裁决。",
            "support_blocks": {
                "neutral_stabilizing_loop": neutral["support_blocks"],
                "bullish_loop": bullish["support_blocks"],
            },
        })
    return {
        "bullish_loop": bullish,
        "neutral_stabilizing_loop": neutral,
        "bearish_loop": bearish,
        "cross_loop_conflicts": cross[:6],
        "unresolved": unresolved,
    }


def _selection_entry(bucket: str, unit: Mapping[str, Any], block_id: str) -> dict[str, Any]:
    prefix = {"claims": "claim", "counters": "counter", "gaps": "gap", "reassess": "reassess"}[bucket]
    return {
        "id": f"{prefix}.{unit['evidence_id'].replace('eu.', '')}",
        "ref": unit["source_ref"],
        "claim_cn": unit["reason_cn"],
        "text_cn": unit["reason_cn"],
        "q1_reason_cn": unit.get("q1_reason_cn"),
        "q2_reason_cn": unit.get("q2_reason_cn"),
        "block_id": block_id,
        "evidence_ids": [unit["evidence_id"]],
        "q1_role": unit["q1_role"],
        "q2_role": unit["q2_role"],
    }


def _selection_summary(units: Sequence[Mapping[str, Any]], blocks: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"claims": [], "counters": [], "gaps": [], "reassess": []}
    by_bucket: dict[str, list[Mapping[str, Any]]] = {key: [] for key in out}
    for unit in units:
        if unit.get("q1_role") == "REASSESS" or unit.get("q2_role") == "REASSESS":
            bucket = "reassess"
        elif unit.get("information_increment") == "MISSING" or unit.get("q1_role") == "LIMITATION" or unit.get("q2_role") == "LIMITATION":
            bucket = "gaps"
        elif unit.get("q1_role") == "COUNTER" or unit.get("q2_role") == "COUNTER" or unit.get("information_increment") == "COUNTER_EVIDENCE" or _has_rel(unit, "CONTRADICT"):
            bucket = "counters"
        elif unit.get("q1_role") in {"PRIMARY", "SUPPORT"} or unit.get("q2_role") in {"PRIMARY", "SUPPORT"}:
            bucket = "claims"
        else:
            bucket = None
        if bucket:
            by_bucket[bucket].append(unit)

    bucket_rank = {"reassess": 0, "counters": 1, "gaps": 2, "claims": 3}
    role_rank = {"PRIMARY": 0, "COUNTER": 1, "LIMITATION": 2, "REASSESS": 3, "SUPPORT": 4, "NONE": 5}

    def priority(bucket: str, unit: Mapping[str, Any]) -> tuple[int, int, int, str]:
        rel_priority = 0 if _has_rel(unit, "CONTRADICT") else 1 if _has_rel(unit, "SUPPORT") else 2
        unit_role_rank = min(role_rank.get(str(unit.get("q1_role")), 9), role_rank.get(str(unit.get("q2_role")), 9))
        return bucket_rank[bucket], unit_role_rank, rel_priority, str(unit.get("evidence_id"))

    for bucket, rows in by_bucket.items():
        seen: set[tuple[str, str, str]] = set()
        for unit in sorted(rows, key=lambda row: priority(bucket, row)):
            block_id = _block_id(blocks, unit)
            key = (block_id, str(unit.get("q1_role")), str(unit.get("q2_role")))
            if key in seen:
                continue
            seen.add(key)
            out[bucket].append(_selection_entry(bucket, unit, block_id))
            if len(out[bucket]) >= 6:
                break
    return out


def _limitations(binding: Mapping[str, Any], units: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    if binding.get("synthetic_identity"):
        rows.append({"id": "synthetic_identity_retained", "text_cn": "当前snapshot保留synthetic_fixture身份；routing可重放但不能获得native资格。", "evidence_ids": []})
    source_kind = str(binding.get("source_kind") or "")
    if source_kind and source_kind not in {"live_public_capture", "native_current_capture"}:
        rows.append({"id": "non_native_current_source_kind", "text_cn": f"当前source_kind={source_kind}，routing不得将其升级为native/current实盘资格。", "evidence_ids": []})
    for unit in units:
        if unit.get("information_increment") == "MISSING" or unit.get("qualification_status") in {"MISSING", "BLOCKED"}:
            rows.append({"id": "limitation." + unit["evidence_id"].replace("eu.", ""), "text_cn": unit["reason_cn"], "evidence_ids": [unit["evidence_id"]]})
    unique, seen = [], set()
    for row in rows:
        if row["id"] not in seen:
            seen.add(row["id"])
            unique.append(row)
    return unique[:12]


def _validate_units(units: Sequence[Mapping[str, Any]]) -> None:
    for unit in units:
        if unit.get("system_role") not in SYSTEM_ROLES or unit.get("system_role") == "CONTEXT":
            raise ValueError("invalid_unit_system_role")
        if unit.get("primary_problem_domain") not in PROBLEM_DOMAINS:
            raise ValueError("invalid_unit_problem_domain")
        if unit.get("dependency_group") not in DEPENDENCY_GROUPS:
            raise ValueError("invalid_unit_dependency_group")
        if unit.get("information_increment") not in INFORMATION_INCREMENTS:
            raise ValueError("invalid_unit_information_increment")
        if unit.get("q1_role") not in Q_ROLES or unit.get("q2_role") not in Q_ROLES:
            raise ValueError("invalid_unit_q_role")
        horizon = _m(unit.get("horizon"))
        if horizon.get("source") not in HORIZONS or horizon.get("method") not in HORIZONS:
            raise ValueError("invalid_unit_horizon")
        for rel in _lst(unit.get("loop_relations")):
            if rel.get("loop") not in LOOPS or rel.get("relation") not in LOOP_RELATIONS or not rel.get("mechanism_id"):
                raise ValueError("invalid_unit_loop_relation")


def build_routing(snapshot: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Build and seal the routing package."""
    if not isinstance(snapshot, Mapping) or not isinstance(evidence, Mapping):
        raise ValueError("snapshot_and_evidence_must_be_mappings")
    binding = _binding(snapshot, evidence)
    units: list[dict[str, Any]] = []
    _snapshot_units(units, binding, snapshot, evidence)
    units.sort(key=lambda row: row["evidence_id"])
    _validate_units(units)
    blocks = _dependency_blocks(units)
    body = {
        "schema": SCHEMA,
        "binding": binding,
        "units": units,
        "dependency_blocks": blocks,
        "problem_map": _problem_map(units),
        "loop_digest": _loop_digest(units, blocks),
        "selection_summary": _selection_summary(units, blocks),
        "limitations": _limitations(binding, units),
    }
    body["routing_hash"] = _hash(body)
    return body


def validate_routing(snapshot: Mapping[str, Any], evidence: Mapping[str, Any], routing: Mapping[str, Any]) -> dict[str, Any]:
    """Replay build_routing and fail closed if any field changed."""
    if not isinstance(routing, Mapping):
        raise RoutingValidationError("routing_must_be_mapping")
    if routing.get("schema") != SCHEMA:
        raise RoutingValidationError("routing_schema_mismatch")
    rebuilt = build_routing(snapshot, evidence)
    if dict(routing) != rebuilt:
        raise RoutingValidationError("routing_rebuild_mismatch")
    return {
        "schema": VALIDATION_SCHEMA,
        "status": "VALID",
        "routing_hash": rebuilt["routing_hash"],
        "unit_count": len(rebuilt["units"]),
        "dependency_block_ids": [block["block_id"] for block in rebuilt["dependency_blocks"]],
    }
