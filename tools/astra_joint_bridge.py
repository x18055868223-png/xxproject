"""Optional, fail-isolated bridge; never launches inference or an LLM request."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from astra_joint_projection import build_joint_projection, validate_assessment

BRIDGE_SCHEMA = "astra_joint_review_context@1.0.0"
BRIDGE_SCHEMA_V11 = "astra_joint_review_context@1.1.0"


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _finite_ms(value: Any, field: str) -> int:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{field} must be finite epoch milliseconds")
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return int(value)


def _quote_checked_times(assessment: dict[str, Any]) -> list[int]:
    result: list[int] = []
    for side in ("put", "call"):
        quote = ((assessment.get("sides") or {}).get(side) or {}).get("quote") or {}
        if isinstance(quote, dict) and quote.get("status") == "available" and quote.get("checked_at_ms") is not None:
            result.append(_finite_ms(quote.get("checked_at_ms"), f"{side}.quote.checked_at_ms"))
    return result


def _validate_available_time(item: dict[str, Any], available_before_ms: int | None = None) -> int:
    available_at_ms = _finite_ms(item.get("available_at_ms"), "available_at_ms")
    bound = _now_ms() if available_before_ms is None else _finite_ms(available_before_ms, "available_before_ms")
    if available_at_ms > bound:
        raise ValueError("joint assessment is not available before the requested freeze time")
    return available_at_ms


def _validate_quote_freeze(assessment: dict[str, Any], available_at_ms: int) -> None:
    for checked_at_ms in _quote_checked_times(assessment):
        if checked_at_ms > available_at_ms:
            raise ValueError("quote checked_at_ms must not be later than available_at_ms")


def load_registry(path):
    if not path:
        return {}
    result = {}
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            key = str(row.get("card_id") or "")
            if not key:
                raise ValueError("joint registry card identity missing")
            if key in result and result[key] != row:
                raise ValueError("conflicting joint registry identity")
            result[key] = row
    return result


def context_for_card(card, registry, available_before_ms=None):
    cid = str((card.get("identity") or {}).get("card_id") or card.get("card_id") or "")
    item = registry.get(cid)
    if not item:
        return None
    try:
        available_at_ms = _validate_available_time(item, available_before_ms)
        assessment = validate_assessment(item.get("assessment"), card)
        _validate_quote_freeze(assessment, available_at_ms)
        if assessment["status"] != "available":
            return None
        return {
            "schema": BRIDGE_SCHEMA_V11 if assessment.get("schema") == "astra_statistical_assessment@1.1.0" else BRIDGE_SCHEMA,
            "available_at_ms": available_at_ms,
            "assessment": assessment,
            "purpose": "frozen conditional payout estimate; not a market fact, net win probability, grade, or permission",
        }
    except (ValueError, KeyError, TypeError):
        # An unavailable research estimate never erases valid market evidence.
        return None


def validate_context(context, card=None):
    if context is None:
        return None
    if not isinstance(context, dict) or context.get("schema") not in (BRIDGE_SCHEMA, BRIDGE_SCHEMA_V11):
        raise ValueError("joint context protocol unsupported")
    result = copy.deepcopy(context)
    available_at_ms = _finite_ms(result.get("available_at_ms"), "available_at_ms")
    result["assessment"] = validate_assessment(result.get("assessment"), card)
    expected = BRIDGE_SCHEMA_V11 if result["assessment"].get("schema") == "astra_statistical_assessment@1.1.0" else BRIDGE_SCHEMA
    if result["schema"] != expected:
        raise ValueError("joint context and assessment versions disagree")
    _validate_quote_freeze(result["assessment"], available_at_ms)
    return result


def prompt_context(context):
    from signal_review_joint import statistical_ranking
    valid = validate_context(context)
    if valid is None:
        return None
    assessment = valid["assessment"]
    def compact_side(side):
        result = {key: copy.deepcopy(value) for key, value in side.items()
                  if key in {'status', 'reason_cn', 'probability_positive', 'conditional_positive_loss',
                             'expected_loss_normalized', 'expected_payout_btc', 'quote',
                             'scope_cn', 'uncertainty_cn', 'tail_probability', 'tail_probability_status', 'tail_probability_note_cn'}}
        reference = side.get('reference') or {}
        result['reference'] = {key: reference[key] for key in
            ('short_strike','long_strike','width','entry_price','expiry_ms','price_observed_at_ms','price_basis_cn')
            if key in reference}
        result['input_support'] = {key: value for key, value in (side.get('input_support') or {}).items()
                                   if key in ('status','missing_features','out_of_range_features','unseen_missing_features','range_status','required_missing_features','qualified','warning_cn')}
        return result
    return {
        "schema": valid["schema"],
        "available_at_ms": valid["available_at_ms"],
        "assessment_hash": assessment["assessment_hash"],
        "statistical_preference": statistical_ranking(valid),
        "risk_order_basis_cn": "按参考宽度入场折算价值归一后的期望赔付比较；较低者是风险排序，不是净收益排序。同宽结构是主要比较组。",
        "decision_evidence_cn": "当前统计模型的历史均值误差改善尚未转化为优于简单几何基线的选侧结果。联合复核也没有已证明的额外增量；自然卡净胜率、净结果与尾损改善仍待实际前向验证。不得将排序或意见一致表述为已证明的交易优势。",
        "provenance": assessment["provenance"],
        "sides": {
            name: compact_side(side)
            for name, side in assessment['sides'].items()
        },
        "scope_cn": assessment.get("scope_cn") or assessment.get("scope"),
        "uncertainty": assessment.get("uncertainty"),
        "instruction_cn": "这是一份冻结的研究估计。解释其与当前机制的相合或分歧；不要修改数值、生成概率、把它计为独立市场事实或改变证据等级。缺报价时只能比较赔付风险。新假设作为后续研究建议，不临场更改模型。",
    }


def attach_projection(record, registry):
    cid = str((record.get("identity") or {}).get("card_id") or record.get("card_id") or "")
    item = registry.get(cid)
    if not item:
        return False
    try:
        available_at_ms = _validate_available_time(item)
        assessment = validate_assessment(item.get("assessment"), record)
        _validate_quote_freeze(assessment, available_at_ms)
        projection = build_joint_projection(assessment, record)
    except (ValueError, KeyError, TypeError):
        return False
    record.pop("astra_joint_display", None)
    record.pop("astra_joint_summary", None)
    record["joint_research_detail"] = projection["detail"]
    record["joint_research_summary"] = projection["summary"]
    frozen = (record.get("llm_review") or {}).get("statistical_context")
    record["joint_research_review_included"] = bool(
        frozen and (frozen.get("assessment") or {}).get("assessment_hash") == assessment.get("assessment_hash")
    )
    return True
