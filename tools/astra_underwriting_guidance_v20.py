"""Read-only Chinese guidance for one Astra v2.0 underwriting snapshot.

This module deliberately interprets already-frozen ledger fields.  It does not
recalculate probabilities, change gates, call an LLM, or grant trade permission.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

SCHEMA = "astra_underwriting_reading_guidance@2.0.0"
MAX_NOTES = 6
EDB_MEANINGFUL_FIELDS = {
    "edb_score", "edb_score_raw", "agreement", "coverage", "confidence",
    "calibration_state", "lean", "lean_pre_gate", "side_hint",
    "side_hint_pre_gate", "support_label", "support_pre_gate",
    "next_action", "next_action_pre_gate", "conflict_level", "ggr_gate",
    "veto_reason", "confidence_decomposition", "summary_cn", "evidence",
    "direction", "nr_state",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _path(value: Any, *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm(value: Any) -> str:
    return _text(value).lower()


def _has_synthetic_marker(*values: Any) -> bool:
    return any("synthetic" in _norm(value) or "simulated" in _norm(value) for value in values)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _side_from_hint(value: Any) -> str | None:
    upper = _text(value).upper()
    if not upper:
        return None
    if "PUT" in upper:
        return "put"
    if "CALL" in upper:
        return "call"
    return None


def _lean_direction(value: Any) -> str | None:
    upper = _text(value).upper()
    if not upper:
        return None
    if upper in {"NEUTRAL", "FLAT", "BALANCED", "MIXED", "NONE"}:
        return "neutral"
    if "BULL" in upper or "LONG" in upper or "UP" in upper or "CALL" in upper:
        return "bullish"
    if "BEAR" in upper or "SHORT" in upper or "DOWN" in upper or "PUT" in upper:
        return "bearish"
    return "unknown"


def _lean_cn(value: Any) -> str:
    direction = _lean_direction(value)
    if direction == "bullish":
        return "偏多"
    if direction == "bearish":
        return "偏空"
    if direction == "neutral":
        return "中性"
    return "未识别"


def _side_hint_cn(value: Any) -> str:
    side = _side_from_hint(value)
    if side == "put":
        return "Put 侧"
    if side == "call":
        return "Call 侧"
    if _lean_direction(value) == "neutral":
        return "中性"
    return "未识别"


def _conflict_cn(value: Any) -> str:
    upper = _text(value).upper()
    if not upper:
        return "未提供"
    if upper in {"NONE", "NO", "0"}:
        return "无"
    if upper == "MILD":
        return "轻微"
    if upper == "LOW":
        return "低"
    if upper == "MATERIAL":
        return "明显"
    if upper in {"MEDIUM", "MID", "MIDDLE", "MODERATE"}:
        return "中"
    if upper == "SEVERE":
        return "严重"
    if upper in {"HIGH", "HARD", "STRONG"}:
        return "高"
    return "未识别"


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _has_meaningful_edb(edb: Mapping[str, Any], reasoning: Mapping[str, Any]) -> bool:
    for source in (edb, reasoning):
        for key in EDB_MEANINGFUL_FIELDS:
            if _has_value(source.get(key)):
                return True
        reason_codes = source.get("reason_codes")
        if isinstance(reason_codes, list) and reason_codes:
            return True
    return False


def _guidance_status(snapshot: Mapping[str, Any], fact_context: Mapping[str, Any]) -> str:
    if not snapshot:
        return "gap"
    data_identity = _mapping(snapshot.get("data_identity"))
    if _norm(data_identity.get("kind")) == "historical_replay":
        return "background"
    if _has_synthetic_marker(
        data_identity.get("kind"),
        _path(snapshot, "market", "reference_source"),
        _path(snapshot, "risk", "source_kind"),
        fact_context.get("status"),
        _path(fact_context, "source_trace", "space_gate_provenance"),
    ):
        return "synthetic"
    fact_status = _norm(fact_context.get("status"))
    if fact_status == "current":
        return "current"
    if fact_status in {"background_only", "background", "stale"}:
        return "background"
    return "gap"


def _append_note(notes: list[dict[str, str]], kind: str, ref: str, text_cn: str) -> None:
    if len(notes) >= MAX_NOTES:
        return
    notes.append({"kind": kind, "ref": ref, "text_cn": text_cn})


def _identity_note(status: str) -> tuple[str, str, str] | None:
    if status == "synthetic":
        return (
            "identity",
            "snapshot.data_identity",
            "这张卡是模拟/预览身份，只能用于阅读工作台与字段链路，不能当作生产事实或经济裁定。",
        )
    if status == "background":
        return (
            "identity",
            "fact_context.status",
            "这张卡依赖背景或旧版事实，适合保留作参考账，不应作为当前 SpaceGate 原生准入事实。",
        )
    if status == "gap":
        return (
            "identity_gap",
            "fact_context.status",
            "当前事实或候选快照存在缺口；本提示只能指出缺口，不能补造方向、概率或执行许可。",
        )
    return None


def _edb_note(snapshot: Mapping[str, Any], fact_context: Mapping[str, Any]) -> tuple[str, str, str]:
    market_facts = _mapping(fact_context.get("market_facts"))
    edb = _mapping(market_facts.get("edb"))
    reasoning = _mapping(market_facts.get("edb_reasoning"))
    side = _norm(_path(snapshot, "contract", "side") or snapshot.get("side"))
    lean = _text(reasoning.get("lean") or edb.get("lean") or edb.get("direction"))
    side_hint = _text(reasoning.get("side_hint") or edb.get("side_hint"))
    conflict_level = _text(reasoning.get("conflict_level") or edb.get("conflict_level"))
    hinted_side = _side_from_hint(side_hint)
    has_edb = _has_meaningful_edb(edb, reasoning)
    lean_direction = _lean_direction(lean)
    neutral = any(direction == "neutral" for direction in (lean_direction, _lean_direction(side_hint)))
    conflict_label = _conflict_cn(conflict_level)
    conflict = bool(conflict_level and conflict_label not in {"无", "未提供"})
    if hinted_side and side and hinted_side != side:
        conflict = True
    if not has_edb:
        return (
            "edb_gap",
            "fact_context.market_facts.edb_reasoning",
            "EDB 原方向缺失，不能作为这张保单的确认来源；缺失时不得默认跟随原方向。",
        )
    if not lean and not side_hint:
        return (
            "edb_direction_gap",
            "fact_context.market_facts.edb_reasoning",
            "EDB 字段存在但原方向缺失，不能用资金/价格流向补成 EDB 确认；不得默认跟随原方向或自动选侧。",
        )
    fragments = []
    if lean:
        fragments.append(f"价格倾向：{_lean_cn(lean)}")
    if side_hint:
        fragments.append(f"源侧别：{_side_hint_cn(side_hint)}")
    if conflict_level:
        fragments.append(f"冲突强度：{conflict_label}")
    prefix = "；".join(fragments) or "EDB 已提供但方向字段不足"
    adverse = ""
    if side == "call" and lean_direction == "bullish":
        adverse = "价格倾向偏多对本 Call 信用价差不利，需复核。"
    elif side == "put" and lean_direction == "bearish":
        adverse = "价格倾向偏空对本 Put 信用价差不利，需复核。"
    if neutral or conflict:
        return (
            "edb_conflict",
            "fact_context.market_facts.edb_reasoning",
            f"EDB/原方向为中性或存在冲突（{prefix}）；它仍是同源市场意见，不构成独立确认，也不默认跟随。{adverse}价格倾向与源侧别并列展示，不自动选侧。",
        )
    return (
        "edb_same_source",
        "fact_context.market_facts.edb_reasoning",
        f"EDB 原方向（{prefix}）只作为同源市场意见读取，不构成独立确认；若与保单责任或空间门矛盾，需要人工复核。{adverse}价格倾向与源侧别并列展示，不自动选侧。",
    )


def build_reading_guidance(snapshot: Any, fact_context: Any | None = None) -> dict[str, Any]:
    """Build bounded read-only guidance for one already-frozen candidate.

    The returned notes are semantic labels for the existing card.  They never
    create probabilities, EV, gates, LLM conclusions, or execution permission.
    """

    snap = _mapping(snapshot)
    fact = _mapping(fact_context if fact_context is not None
                    else snap.get("fmz_fact_context") or snap.get("fact_context"))
    status = _guidance_status(snap, fact)
    notes: list[dict[str, str]] = []

    identity = _identity_note(status)
    if identity is not None:
        _append_note(notes, *identity)

    side = _norm(_path(snap, "contract", "side") or snap.get("side"))
    if side == "call":
        _append_note(
            notes,
            "liability_direction",
            "snapshot.contract.side",
            "Call 信用价差的主要不利方向是上涨；交割价高于卖出腿才开始合同赔付，卖出腿至保护腿之间赔付增加，保护腿附近 BTC 赔付达峰，保护腿外随交割价继续上涨而下降，不声称赔付持续单调增加；静态净亏看冻结盈亏平衡点。",
        )
    elif side == "put":
        _append_note(
            notes,
            "liability_direction",
            "snapshot.contract.side",
            "Put 信用价差的主要不利方向是下跌；交割价低于卖出腿才开始合同赔付，静态净亏看冻结盈亏平衡点；保护腿下方没有固定 BTC 赔付上限，深尾仍按原币本位账读取。",
        )
    else:
        _append_note(notes, "liability_gap", "snapshot.contract.side",
                     "合约侧别缺失，无法判断这张保单的主要不利方向。")

    economics = _mapping(snap.get("economics"))
    entry_credit = _number(economics.get("net_credit_after_entry_fee_btc"))
    if entry_credit is None:
        _append_note(notes, "compensation_gap", "snapshot.economics.net_credit_after_entry_fee_btc",
                     "净信用字段缺失，不能判断权利金差扣两腿理论入场费后的补偿是否足够。")
    elif entry_credit <= 0:
        _append_note(notes, "compensation_insufficient", "snapshot.economics.net_credit_after_entry_fee_btc",
                     "净信用非正，当前权利金补偿不足；顺序分腿盘口只是估值，不代表已成交。")
    else:
        _append_note(notes, "compensation_reference", "snapshot.economics.net_credit_after_entry_fee_btc",
                     "净信用只解释服务端原字段：权利金差扣两腿理论入场费后的当前账面补偿，顺序分腿盘口未成交。")

    risk = _mapping(snap.get("risk"))
    mu_ref = _number(economics.get("mu_ref_btc"))
    margin = _number(economics.get("reference_margin_btc"))
    data_identity = _mapping(snap.get("data_identity"))
    synthetic_risk = _has_synthetic_marker(
        data_identity.get("kind"),
        risk.get("source_kind"),
        risk.get("status"),
    )
    risk_value_readable = mu_ref is not None and (
        risk.get("status") == "supported" or synthetic_risk
    )
    if not risk_value_readable:
        _append_note(notes, "risk_reference_gap", "snapshot.risk",
                     "冻结风险均值缺失或未获支持，不能按 EV、μ_ref 或 M_ref 对候选排序。")
    else:
        ref = "snapshot.economics.reference_margin_btc"
        prefix = ("模拟 μ_ref/M_ref 已提供，非真实模型资格；"
                  if synthetic_risk else "冻结 μ_ref/M_ref 只解释服务端原值：")
        if margin is None:
            _append_note(notes, "risk_reference_gap", ref,
                         prefix + "μ_ref 可读但 M_ref 缺失，不能把它扩展成完整 EV 排序。")
        elif margin > 0:
            _append_note(notes, "risk_reference", ref,
                         prefix + "M_ref 为正，表示既有全费用口径当前有参考余量；正余量不证明 EV 或经济 edge，本提示不复算、不改阈值。")
        elif margin == 0:
            _append_note(notes, "risk_reference", ref,
                         prefix + "M_ref 为零，表示既有全费用口径没有参考余量；本提示不复算、不改阈值。")
        else:
            _append_note(notes, "risk_reference", ref,
                         prefix + "M_ref 为负，表示按原账本参考补偿不足；本提示不复算、不改阈值。")

    _append_note(notes, *_edb_note(snap, fact))

    return {
        "schema": SCHEMA,
        "status": status,
        "scope": "read_only_candidate_interpretation",
        "notes": notes,
        "llm_call_count": 0,
        "execution_allowed": False,
    }
