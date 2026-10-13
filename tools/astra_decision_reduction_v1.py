"""Decision reduction: what is left for the user once Q1/Q2 are read.

Deterministic and read-only. Every program-side Q1/Q2 item lands in exactly one place:
the rule state, a fold with a checkable reason code, a remaining question, or the
"unclassified" list shown to the user as is. Nothing is weighed, ranked, netted or
concluded; no number is made up (each comes from a field of the candidate).
Design: docs/astra/DECISION_REDUCTION_DESIGN_20261013.md
"""
from __future__ import annotations

from typing import Any, Mapping

if __package__:
    from . import astra_underwriting_review_v20 as review_core
    from . import astra_underwriting_v20 as core
else:
    import astra_underwriting_review_v20 as review_core
    import astra_underwriting_v20 as core

SCHEMA = "astra_decision_reduction@1.1.0"

RULE_GATE_REASONS = {"space_gate_not_eligible", "structure_gate_not_eligible"}
# Review-gate reasons by explicit list; anything else stays with the user.
DATA_GAP_REASONS = {
    "cached_quote_not_current", "quote_invalid_or_stale", "fee_missing_or_incomplete",
    "data_engine_quote_usage_rejected", "data_engine_required_source_usage_rejected",
    "snapshot_missing", "snapshot_not_reviewable", "snapshot_time_missing",
    "candidate_gate_version_changed_during_refresh", "wpem_evidence_unavailable",
}
REVIEW_SERVICE_REASONS = {
    "manual_review_service_not_configured", "manual_review_budget_exhausted", "manual_review_cooldown_active",
    "manual_review_inflight", "llm_not_configured",
}
PREMISE_REASONS = {"m_ref_invalid", "m_ref_non_positive"}
# reading_guidance note kind -> (question, code); unknown kinds stay with the user
GUIDANCE_FOLD = {
    "liability_direction": ("R1", "in_boundary"),
    "compensation_gap": ("R2", "in_question_premise"),
    "compensation_reference": ("R2", "in_question_premise"),
    "compensation_insufficient": ("R2", "in_question_premise"),
    "risk_reference_gap": ("R2", "in_question_premise"),
    "risk_reference": ("R2", "in_question_premise"),
    "edb_conflict": ("R1", "evidence"),
    "edb_same_source": ("R1", "evidence"),
    "edb_gap": ("R1", "no_direction"),
    "edb_direction_gap": ("R1", "no_direction"),
    "identity_gap": ("-", "shown_at_number"),
}
GUIDANCE_LABEL_CN = {
    "liability_gap": "合约侧别缺失",
    "identity": "数据身份提示（模拟或背景口径）",
}
# research item information_increment -> fold reason
INCREMENT_FOLD = {
    "CONTEXT_ONLY": "non_voting_context",
    "CONFIRMING_SAME_BLOCK": "same_block_repeat",
    "REDUNDANT": "same_block_repeat",
    "INCONCLUSIVE": "no_direction",
    "MISSING": "no_direction",
    "QUALIFICATION_ONLY": "qualification_only",
}
# increments that carry a direction for this side: kept as evidence under R1
INCREMENT_EVIDENCE = {"NOVEL", "COUNTER_EVIDENCE"}
# evidence id prefixes that are the space gate itself
RULE_EVIDENCE_PREFIXES = ("eu.anchor_", "eu.space_gate")
FOLD_CN = {
    "rule_gate": "已在规则状态中",
    "shown_at_number": "数据缺口，只在受影响的数字处标注",
    "in_boundary": "并入问题 1 的分界",
    "in_question_premise": "转为问题 2 的前提",
    "non_voting_context": "背景，不投票",
    "same_block_repeat": "同源重复确认",
    "no_direction": "无方向结论或缺数据",
    "qualification_only": "只用于来源资格，不提供方向",
    "restates_evidence": "复述已归类的证据",
    "no_path_signal": "同窗路径不足，不参与",
    "review_service_state": "复核服务状态，与承保判断无关",
}
OUTSIDE_MODEL_CN = (
    "到期前已知事件（宏观数据、监管、交易所公告）",
    "交易所与结算风险",
    "你自己的整体仓位与承受度",
)
RELATION_CN = {"adverse": "靠近短腿", "favorable": "远离短腿", "neutral": "中性", "unknown": "方向不明",
               "counter_non_directional": "反证（无价格方向）"}


def _m(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _relation(side: str, direction: Any) -> str:
    return review_core._direction_relation(side, review_core._normalized_direction(direction))


def _item_direction(item: Mapping[str, Any]) -> Any:
    readings = _m(item.get("readings"))
    return readings.get("direction") or _m(readings.get("combined")).get("direction")


DIRECTION_CN = {"UP": "偏多", "DOWN": "偏空", "NEUTRAL": "中性"}


def _reading_cn(item: Mapping[str, Any]) -> str | None:
    """The reading as the producer gave it: direction, plus the windows it holds over."""
    readings = _m(item.get("readings"))
    direction = DIRECTION_CN.get(review_core._normalized_direction(_item_direction(item)))
    horizons = _m(readings.get("combined")).get("ready_horizons") or readings.get("ready_horizons")
    windows = "/".join(str(h) for h in horizons) if isinstance(horizons, list) and horizons else None
    if direction and windows:
        return f"{windows} {direction}"
    return direction


def _distance(side: str, spot: Any, boundary: Any, kind: str) -> float | None:
    found = core.signed_boundary_distance(side, spot, boundary, boundary_kind=kind)
    return None if found is None else found["signed_distance_pct"]


def reduce(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce one displayed candidate; never raises on missing fields."""
    side = str(candidate.get("side") or "").lower()
    snapshot = _m(candidate.get("snapshot"))
    contract = _m(snapshot.get("contract"))
    liability = _m(snapshot.get("liability"))
    economics = _m(snapshot.get("economics"))
    market = _m(snapshot.get("market"))
    questions = _m(candidate.get("current_questions"))
    folded: dict[str, list[str]] = {}
    unclassified: list[dict[str, Any]] = []
    inputs = 0

    def fold(code: str, ref: str) -> None:
        folded.setdefault(code, []).append(ref)

    # 1. rule state
    gate_reasons = list(_m(candidate.get("review_gate")).get("reasons") or [])
    space_gate = _m(candidate.get("space_gate"))
    rule_reasons = sorted({r for r in gate_reasons if r in RULE_GATE_REASONS}
                          | ({"space_gate_not_eligible"} if space_gate.get("eligible") is False else set()))
    premise_flags: list[str] = []
    for reason in gate_reasons:
        inputs += 1
        ref = "review_gate." + reason
        if reason in RULE_GATE_REASONS:
            fold("rule_gate", ref)
        elif reason in DATA_GAP_REASONS:
            fold("shown_at_number", ref)
        elif reason in REVIEW_SERVICE_REASONS:
            fold("review_service_state", ref)
        elif reason in PREMISE_REASONS:
            fold("in_question_premise", ref)
            premise_flags.append(reason)
        else:
            unclassified.append({"ref": ref, "code": reason, "label_cn": None})
    rule_state = {
        "status": "blocked" if rule_reasons else "open",
        "reasons": rule_reasons,
        "gate_gap_reasons": list(space_gate.get("gap_reasons") or []),
    }

    # 2. evidence for R1 and the folds
    evidence: list[dict[str, Any]] = []
    known_ids: set[str] = set()
    for item in _m(questions.get("research_context")).get("items") or []:
        if not isinstance(item, Mapping):
            continue
        inputs += 1
        ref = str(item.get("evidence_id") or item.get("id") or "")
        known_ids.add(ref)
        increment = str(item.get("information_increment") or "")
        if increment in INCREMENT_FOLD:
            fold(INCREMENT_FOLD[increment], ref)
        elif increment in INCREMENT_EVIDENCE:
            relation = _relation(side, _item_direction(item))
            if increment == "COUNTER_EVIDENCE" and relation in {"unknown", "neutral"}:
                relation = "counter_non_directional"
            evidence.append({"ref": ref, "label_cn": item.get("label_cn") or "未命名来源", "relation": relation,
                             "relation_cn": RELATION_CN.get(relation, "方向不明"), "source": "research_context",
                             "detail_cn": _reading_cn(item)})
        else:
            unclassified.append({"ref": ref, "label_cn": item.get("label_cn") or "未命名来源", "text_cn": None})
    for note in _m(candidate.get("reading_guidance")).get("notes") or []:
        if not isinstance(note, Mapping):
            continue
        inputs += 1
        kind = str(note.get("kind") or "")
        ref = "reading_guidance." + kind
        target = GUIDANCE_FOLD.get(kind)
        if target is None:
            unclassified.append({"ref": ref, "label_cn": GUIDANCE_LABEL_CN.get(kind, "程序提示"), "text_cn": note.get("text_cn")})
        elif target[1] == "evidence":
            edb = review_core._edb_relation(snapshot)
            relation = edb.get("relation") or "unknown"
            lean = review_core._edb_lean_cn(edb.get("lean"))
            evidence.append({"ref": ref, "label_cn": "EDB 倾向（定性）", "relation": relation,
                             "relation_cn": RELATION_CN.get(relation, "方向不明"), "source": "reading_guidance",
                             "detail_cn": lean if lean != "未知" else DIRECTION_CN.get(str(edb.get("direction") or ""))})
        else:
            fold(target[1], ref)
            if kind == "compensation_insufficient":
                premise_flags.append(kind)
    for key in ("q1", "q2"):
        block = _m(questions.get(key))
        if not block.get("routing_observations_cn"):
            continue
        inputs += 1
        ids = [str(x) for x in block.get("routing_evidence_ids") or []]
        if ids and all(i in known_ids or i.startswith(RULE_EVIDENCE_PREFIXES) for i in ids):
            fold("restates_evidence", key + ".routing")
        else:
            unclassified.append({"ref": key + ".routing", "label_cn": "Q1" if key == "q1" else "Q2",
                                 "text_cn": "；".join(block.get("routing_observations_cn") or [])})
    wpem = _m(candidate.get("wpem_summary"))
    if wpem:
        inputs += 1
        path_class = wpem.get("path_classification")
        if wpem.get("window_status") == "INSUFFICIENT_WINDOW" or not isinstance(path_class, str) or path_class == "gap":
            fold("no_path_signal", "wpem_summary")
        else:
            unclassified.append({"ref": "wpem_summary", "label_cn": "同窗路径", "text_cn": wpem.get("path_cn")})
    evidence.sort(key=lambda e: e["ref"])  # input order must not change the output
    relations = {e["relation"] for e in evidence}
    conflict = "adverse" in relations and "favorable" in relations

    # 3. what is left for the user; every number comes from a field of the candidate
    spot = _num(market.get("reference_price_usd")) or _num(economics.get("valuation_spot_usd"))
    usd = (lambda v: v * spot if v is not None and spot else None)
    short_k, long_k = _num(contract.get("short_strike_usd")), _num(contract.get("long_strike_usd"))
    quantity = _num(contract.get("quantity_btc")) or 1.0
    payout = [row for row in liability.get("payout_table") or [] if isinstance(row, Mapping)]
    protection = _num(liability.get("protection_at_usd")) or long_k
    protection_row = next((r for r in payout if _num(r.get("settlement_usd")) == protection), {})
    at_protection = _num(protection_row.get("payout_btc"))
    deepest = min(payout, key=lambda r: _num(r.get("settlement_usd")) or float("inf")) if payout else None
    m_ref = _num(economics.get("reference_margin_btc"))
    credit = _num(economics.get("visible_credit_btc"))
    net_all_fees = _num(economics.get("net_credit_after_fee_btc"))
    fees = (credit - net_all_fees) if credit is not None and net_all_fees is not None else None
    marks = _m(candidate.get("market_marks"))
    short_mark, long_mark = _num(marks.get("short_mark_btc")), _num(marks.get("long_mark_btc"))
    # The spread's mark value is the market's own estimate of what it pays at expiry.
    market_payout = (short_mark - long_mark) * quantity if short_mark is not None and long_mark is not None else None
    spread_cost = (credit - market_payout) if credit is not None and market_payout is not None else None
    edge = (net_all_fees - market_payout) if net_all_fees is not None and market_payout is not None else None
    missing = [label for label, value in (("可见信用", credit), ("手续费", fees), ("两腿标记价", market_payout))
               if value is None]
    flags = ((["净信用不为正：权利金差不够覆盖入场费"] if "compensation_insufficient" in premise_flags else [])
             + (["程序参照 M_ref 不为正或无效"] if {"m_ref_invalid", "m_ref_non_positive"} & set(premise_flags) else []))

    r1 = {
        "id": "R1", "kind": "judgment", "key": "market_price",
        "title_cn": "补偿对比市场估价",
        "numbers": {
            "credit_btc": credit, "fees_btc": fees, "net_btc": net_all_fees, "net_usd": usd(net_all_fees),
            "market_payout_btc": market_payout, "market_payout_usd": usd(market_payout),
            "spread_cost_btc": spread_cost, "spread_cost_usd": usd(spread_cost),
            "fees_usd": usd(fees), "edge_btc": edge, "edge_usd": usd(edge),
            "marks_observed_ms": marks.get("observed_ms"), "missing_cn": missing,
        },
        "stance": None if edge is None else ("below_market" if edge < 0 else "at_or_above_market"),
        "flags_cn": flags,
        "m_ref_btc": m_ref,
        "why_user_cn": ("市场的估价已经包含了公开信息；系统没有独立的风险参照（μ_ref 无生产者），"
                        "所以补偿够不够，只取决于你是否比市场更有把握。"),
    }
    r2 = {
        "id": "R2", "kind": "belief", "key": "your_reason",
        "title_cn": "你比市场更有把握的理由",
        "boundary": {
            "spot_usd": spot, "short_strike_usd": short_k,
            "short_distance_pct": _distance(side, spot, short_k, "short"),
            "protection_distance_pct": _distance(side, spot, long_k, "protection"),
            "short_abs_delta": _num(_m(candidate.get("discovery")).get("short_abs_delta")),
        },
        "evidence": evidence,
        "conflict": conflict,
        "why_user_cn": "这些读数没有校准概率，项目不给概率；系统看到的也不等于全部重要因素。",
    }
    # The payout row's static net already includes every fee; fall back to net after all fees.
    static_net = _num(protection_row.get("static_net_btc"))
    loss_at_protection = (-static_net if static_net is not None
                          else (at_protection - net_all_fees) if at_protection is not None and net_all_fees is not None else None)
    r3 = {
        "id": "R3", "kind": "preference", "key": "worst_case",
        "title_cn": "能否接受最坏情况",
        "numbers": {"protection_usd": protection, "loss_at_protection_btc": loss_at_protection,
                    "loss_at_protection_usd": usd(loss_at_protection),
                    "payout_at_protection_btc": at_protection,
                    "deep_usd": _num(_m(deepest).get("settlement_usd")) if side == "put" else None,
                    "payout_at_deep_btc": _num(_m(deepest).get("payout_btc")) if side == "put" else None},
        "tail": "grows_in_btc" if side == "put" else "capped",
        "why_user_cn": "这是承受度偏好，不是事实问题。",
    }
    remaining = [r1, r2, r3]

    folded_rows = [{"code": code, "label_cn": FOLD_CN.get(code, code), "count": len(refs), "refs": refs}
                   for code, refs in folded.items()]
    return {
        "schema": SCHEMA,
        "rule_state": rule_state,
        "questions": remaining,
        "folded": folded_rows,
        "unclassified": unclassified,
        "outside_model_cn": list(OUTSIDE_MODEL_CN),
        "coverage": {"inputs": inputs, "folded": sum(len(r) for r in folded.values()),
                     "evidence": len(evidence), "unclassified": len(unclassified)},
        "llm_call_count": 0,
        "execution_allowed": False,
    }
