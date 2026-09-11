"""Deterministic display projection for comparable signal-card changes.

This module deliberately stays outside the LLM packet and persisted review
hashes.  It reads the strict v2.1.1 evidence facts that already exist, checks
the current/previous card transition, and emits a compact row projection for
the frontend to render.
"""

from __future__ import annotations

import math
from typing import Any

try:  # pragma: no cover - import path differs between direct scripts/package use.
    from signal_evidence_v2 import (
        PACKET_SCHEMA_VERSION,
        _as_ms,
        _dict,
        _finite_number,
        _get_path,
        _transition_matches,
        build_evidence_packet,
    )
except ImportError:  # pragma: no cover
    from .signal_evidence_v2 import (
        PACKET_SCHEMA_VERSION,
        _as_ms,
        _dict,
        _finite_number,
        _get_path,
        _transition_matches,
        build_evidence_packet,
    )


CHANGE_DISPLAY_KEYS = (
    "key",
    "label_cn",
    "previous",
    "current",
    "delta",
    "unit",
    "usable",
    "summary_cn",
    "gap_cn",
    "source_refs",
)


_SPECS = (
    {
        "key": "tmv_blend",
        "label_cn": "TMV 量价主干",
        "fact_ids": ("pressure.tmv.blend",),
        "raw_paths": ("factor_cross_section.tmvf",),
        "kind": "number",
        "meaning": "只表示两张卡的量价主干刻度差，不等于胜率变化。",
    },
    {
        "key": "net_gamma_notional_usd",
        "label_cn": "净 Gamma 名义规模",
        "fact_ids": ("structure.gex.net_gamma_notional_usd",),
        "raw_paths": ("factor_cross_section.gex_info",),
        "kind": "number",
        "required_source_ref": "factor_cross_section.gex_info",
        "forbid_source_ref": "factor_cross_section.gamma_regime",
        "meaning": "只记录看板净 Gamma 名义规模差，不推断约束增强或减弱。",
    },
    {
        "key": "active_flow",
        "label_cn": "主动流",
        "fact_ids": (
            "pressure.near_term.15m.net_active_volume",
            "pressure.near_term.30m.net_active_volume",
            "pressure.cvd.combined_direction",
            "pressure.cvd.fast_4h.cvd_norm",
        ),
        "raw_paths": (
            "near_term_market_context.windows.15m",
            "near_term_market_context.windows.30m",
            "factor_cross_section.micro_flow.combined",
            "factor_cross_section.micro_flow.fast_4h",
        ),
        "kind": "auto",
        "meaning": "主动流变化只说明对应窗口的买卖压力，不与价格直接相减。",
    },
    {
        "key": "macro_score",
        "label_cn": "宏观逆风刻度",
        "fact_ids": ("pressure.macro.score",),
        "raw_paths": ("factor_cross_section.macro_pressure",),
        "kind": "number",
        "meaning": "正值偏风险资产逆风，负值偏顺风；这里保留原刻度差。",
    },
    {
        "key": "funding_raw_rate",
        "label_cn": "资金费率背景",
        "fact_ids": ("pressure.funding.raw_rate",),
        "raw_paths": ("factor_cross_section.funding",),
        "kind": "number",
        "meaning": "资金费率按原始小数记录；温和费率仍只是背景。",
    },
    {
        "key": "pin_strike",
        "label_cn": "Pin 或最大 Gamma 行权价",
        "fact_ids": ("structure.gamma.pin_strike",),
        "raw_paths": (
            "factor_cross_section.gex_info",
            "factor_cross_section.gamma_regime",
            "factor_cross_section.gamma_regime.pin",
        ),
        "kind": "number",
        "meaning": "结构位迁移与价格移动分开记录，不自动解释为更安全。",
    },
)


def build_change_display(record: dict[str, Any] | None,
                         previous: dict[str, Any] | None,
                         transition: dict[str, Any] | None) -> dict[str, Any]:
    """Build rows for the frontend's key-change skeleton.

    The caller owns the outer assessment/source/as-of/hash wrapper.  This
    function returns only deterministic display rows and Chinese reasons.
    """
    current_card = _dict(record)
    previous_card = _dict(previous)
    transition_record = _dict(transition)

    valid, reasons = _transition_matches(
        current_card,
        previous_card,
        transition_record,
        use_comparable_schema=True,
    )
    if not valid:
        return {"rows": [], "reasons_cn": _unique_strings(reasons)}

    current_packet = build_evidence_packet(
        current_card,
        previous_card,
        transition_record,
        packet_schema=PACKET_SCHEMA_VERSION,
    )
    previous_packet = build_evidence_packet(
        previous_card,
        packet_schema=PACKET_SCHEMA_VERSION,
    )
    if (current_packet.get("schema") != PACKET_SCHEMA_VERSION
            or previous_packet.get("schema") != PACKET_SCHEMA_VERSION):
        return {
            "rows": [],
            "reasons_cn": ["市场事实包不是严格 2.1.1，变化展示不计算。"],
        }

    current_facts = _facts_by_id(current_packet)
    previous_facts = _facts_by_id(previous_packet)
    rows = [
        _build_row(spec, current_card, previous_card,
                   current_packet, previous_packet,
                   current_facts, previous_facts)
        for spec in _SPECS
    ]
    reasons_cn = [
        "前后卡身份、来源哈希、版本、时间与可比资料结构已通过核验。",
        "以下变化只表示两张卡时点差，不代表完整行情路径。",
    ]
    return {"rows": rows, "reasons_cn": reasons_cn}


def _build_row(spec: dict[str, Any],
               current_card: dict[str, Any],
               previous_card: dict[str, Any],
               current_packet: dict[str, Any],
               previous_packet: dict[str, Any],
               current_facts: dict[str, dict[str, Any]],
               previous_facts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    selected = _select_fact_pair(spec, current_facts, previous_facts)
    fact_id = selected["fact_id"]
    current_fact = selected["current"]
    previous_fact = selected["previous"]
    previous_value = _fact_value(previous_fact)
    current_value = _fact_value(current_fact)
    unit = _resolve_unit(previous_fact, current_fact)
    source_refs = _source_refs(fact_id, previous_fact, current_fact)

    gap_reasons = []
    if previous_fact is None:
        gap_reasons.append("前卡缺少该事实，不能补造前值。")
    if current_fact is None:
        gap_reasons.append("当前卡缺少该事实，不能补造当前值。")

    previous_ok, previous_reason = _fact_usable_for_card(
        previous_fact, previous_packet)
    current_ok, current_reason = _fact_usable_for_card(
        current_fact, current_packet)
    if previous_reason:
        gap_reasons.append("前卡" + previous_reason)
    if current_reason:
        gap_reasons.append("当前卡" + current_reason)

    comparable_ok, comparable_reason = _facts_comparable(
        spec, previous_fact, current_fact)
    if comparable_reason:
        gap_reasons.append(comparable_reason)

    raw_ok, raw_reason = _raw_sources_comparable(
        spec, previous_card, current_card, fact_id)
    if raw_reason:
        gap_reasons.append(raw_reason)

    source_ok, source_reason = _required_source_check(
        spec, previous_fact, current_fact)
    if source_reason:
        gap_reasons.append(source_reason)

    can_compare = bool(previous_fact and current_fact and previous_ok
                       and current_ok and comparable_ok and raw_ok and source_ok)
    delta = None
    if can_compare:
        delta = _delta_for(previous_value, current_value, spec.get("kind"))
        if spec.get("kind") == "number" and delta is None:
            can_compare = False
            gap_reasons.append("前后值不是可比较数字，不能计算变化。")

    gap_cn = "；".join(_unique_strings(gap_reasons))
    summary_cn = (
        _summary_for(spec, previous_value, current_value, delta, unit)
        if can_compare else
        (gap_cn or "该行暂不能形成可核验变化。")
    )
    return _ordered_row({
        "key": spec["key"],
        "label_cn": ({
            "pressure.near_term.15m.net_active_volume": "近端15分钟净主动量",
            "pressure.near_term.30m.net_active_volume": "近端30分钟净主动量",
            "pressure.cvd.combined_direction": "主动流综合方向",
        }.get(fact_id, spec["label_cn"])),
        "previous": previous_value,
        "current": current_value,
        "delta": delta if can_compare else None,
        "unit": unit,
        "usable": can_compare,
        "summary_cn": summary_cn,
        "gap_cn": "" if can_compare else gap_cn,
        "source_refs": source_refs,
    })


def _select_fact_pair(spec, current_facts, previous_facts):
    fallback = {"fact_id": spec["fact_ids"][0], "current": None, "previous": None}
    for fact_id in spec["fact_ids"]:
        current = current_facts.get(fact_id)
        previous = previous_facts.get(fact_id)
        if current or previous:
            candidate = {"fact_id": fact_id, "current": current, "previous": previous}
            if current and previous and current.get("usable") and previous.get("usable"):
                return candidate
            fallback = candidate
    return fallback


def _facts_by_id(packet):
    return {
        str(item.get("id")): item
        for item in packet.get("facts", [])
        if isinstance(item, dict) and item.get("id") not in (None, "")
    }


def _fact_value(fact):
    return None if fact is None else fact.get("value")


def _fact_usable_for_card(fact, packet):
    if fact is None:
        return False, ""
    if fact.get("usable") is not True:
        return False, "事实不可用。"
    as_of = _as_ms(_dict(packet.get("identity")).get("as_of_ms"))
    available = _as_ms(fact.get("available_at_ms"))
    if available is None:
        return False, "缺少可得时间。"
    if as_of is not None and available > as_of:
        return False, "事实可得时间晚于卡片时点。"
    for label, value in _known_fact_times(fact):
        parsed = _as_ms(value)
        if parsed is not None and as_of is not None and parsed > as_of:
            return False, f"{label}晚于卡片时点。"
    provenance = _dict(fact.get("provenance"))
    if not provenance:
        return False, "缺少来源说明。"
    if not provenance.get("selected_source") or not provenance.get("method"):
        return False, "来源或方法未说明。"
    if provenance.get("time_errors"):
        return False, "来源时间存在错误。"
    return True, ""


def _known_fact_times(fact):
    yield "观测时间", fact.get("observed_at_ms")
    provenance = _dict(fact.get("provenance"))
    for key, label in (
            ("observed_at_ms", "来源观测时间"),
            ("generated_at_ms", "上游结果时间"),
            ("fetched_at_ms", "抓取时间"),
            ("recorded_at_ms", "卡片记录时间")):
        yield label, provenance.get(key)


def _facts_comparable(spec, previous_fact, current_fact):
    if previous_fact is None or current_fact is None:
        return False, ""
    if _fact_unit(previous_fact) != _fact_unit(current_fact):
        return False, "前后单位不同，本项不做变化判断。"
    if _fact_window(previous_fact) != _fact_window(current_fact):
        return False, "前后观察窗口不同，本项不做变化判断。"
    prev_prov = _dict(previous_fact.get("provenance"))
    curr_prov = _dict(current_fact.get("provenance"))
    for key, label in (("selected_source", "来源"), ("method", "方法")):
        prev = prev_prov.get(key)
        curr = curr_prov.get(key)
        if prev and curr and prev != curr:
            return False, f"前后{label}不同，本项不做变化判断。"
    return True, ""


def _raw_sources_comparable(spec, previous_card, current_card, fact_id):
    prev_sig = _raw_signature(previous_card, spec.get("raw_paths", ()), fact_id)
    curr_sig = _raw_signature(current_card, spec.get("raw_paths", ()), fact_id)
    keys = ("source_ref", "source", "provider", "method", "window")
    if not any(prev_sig.get(key) not in (None, "") for key in keys) and not any(
            curr_sig.get(key) not in (None, "") for key in keys):
        return True, ""
    for key in keys:
        prev = prev_sig.get(key)
        curr = curr_sig.get(key)
        if (prev not in (None, "") or curr not in (None, "")) and prev != curr:
            return False, "前后原始来源身份或窗口不同，本项不做变化判断。"
    return True, ""


def _raw_signature(card, raw_paths, fact_id):
    for path in _paths_for_fact(raw_paths, fact_id):
        node = _dict(_get_path(card, path))
        if not node:
            continue
        return {
            "source_ref": _first_present(
                node, "source_ref", "source_id", "data_source"),
            "source": _first_present(node, "source", "provider_name"),
            "provider": _first_present(node, "provider", "exchange"),
            "method": _first_present(node, "method", "calc_method"),
            "window": _window_identity(node),
        }
    return {}


def _paths_for_fact(raw_paths, fact_id):
    if fact_id == "pressure.near_term.15m.net_active_volume":
        return ("near_term_market_context.windows.15m",)
    if fact_id == "pressure.near_term.30m.net_active_volume":
        return ("near_term_market_context.windows.30m",)
    if fact_id == "pressure.cvd.combined_direction":
        return ("factor_cross_section.micro_flow.combined",)
    if fact_id.startswith("pressure.cvd.fast_4h"):
        return ("factor_cross_section.micro_flow.fast_4h",)
    return raw_paths or ()


def _window_identity(node):
    source = _dict(node)
    for key in ("window", "time_window", "clock_window", "horizon_hours",
                "interval_hours", "target_expiry_hours"):
        value = source.get(key)
        if value not in (None, "", {}, []):
            return _stable_scalar(value)
    return None


def _first_present(node, *keys):
    for key in keys:
        value = _dict(node).get(key)
        if value not in (None, "", {}, []):
            return _stable_scalar(value)
    return None


def _stable_scalar(value):
    if isinstance(value, dict):
        return tuple(sorted((str(k), _stable_scalar(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_stable_scalar(item) for item in value)
    return str(value)


def _required_source_check(spec, previous_fact, current_fact):
    required = spec.get("required_source_ref")
    forbidden = spec.get("forbid_source_ref")
    if not required and not forbidden:
        return True, ""
    for fact in (previous_fact, current_fact):
        if fact is None:
            continue
        source_refs = set(str(item) for item in fact.get("source_refs", []))
        selected = str(_dict(fact.get("provenance")).get("selected_source") or "")
        if required and required not in source_refs and required not in selected:
            return False, "该项未使用要求的原生来源，本项不做变化判断。"
        if forbidden and (forbidden in source_refs or forbidden in selected):
            return False, "该项混入不允许的替代来源，本项不做变化判断。"
    return True, ""


def _delta_for(previous, current, kind):
    prev_num = _finite_number(previous)
    curr_num = _finite_number(current)
    if kind == "number" or (prev_num is not None and curr_num is not None):
        if prev_num is None or curr_num is None:
            return None
        return _round(curr_num - prev_num)
    return None


def _summary_for(spec, previous, current, delta, unit):
    if delta is None:
        return "方向类别未变，不计算幅度差。" if previous == current else "方向类别发生变化，不计算幅度差。"
    direction = "未变" if delta == 0 else "上移" if delta > 0 else "下移"
    if spec['key'] == 'tmv_blend':
        return f"量价刻度{direction}；正负两侧分别对应上行与下行倾向，不能直接换成价差评级。"
    if spec['key'] == 'net_gamma_notional_usd':
        return f"净名义值{direction}；仍需结合翻转位置与价格响应解释波动反馈。"
    if spec['key'] == 'funding_raw_rate':
        return f"费率{direction}；温和费率仍只作背景，不能独立确认价格方向。"
    if spec['key'] == 'active_flow':
        return f"净主动量{direction}；需与对应窗口的价格响应一起核对。"
    if spec['key'] == 'pin_strike':
        return f"Pin 点位{direction}；这与现价靠近或远离 Pin 是两件事。"
    return f"原刻度{direction}。{spec['meaning']}"


def _resolve_unit(previous_fact, current_fact):
    prev_unit = _fact_unit(previous_fact)
    curr_unit = _fact_unit(current_fact)
    if prev_unit == curr_unit:
        return prev_unit
    return curr_unit if prev_unit in (None, "") else prev_unit


def _fact_unit(fact):
    if fact is None:
        return None
    unit = fact.get("unit")
    return None if unit in (None, "") else str(unit)


def _fact_window(fact):
    if fact is None:
        return None
    window = fact.get("window")
    return None if window in (None, "") else str(window)


def _round(value):
    number = _finite_number(value)
    if number is None:
        return None
    if not math.isfinite(number):
        return None
    return round(number, 8)


def _source_refs(fact_id, previous_fact, current_fact):
    refs = [fact_id]
    for fact in (previous_fact, current_fact):
        if fact:
            refs.extend(fact.get("source_refs", []))
    refs.append("transition_context")
    return _unique_strings(refs)


def _unique_strings(values):
    out = []
    for value in values or []:
        if value in (None, ""):
            continue
        text = str(value)
        if text not in out:
            out.append(text)
    return out


def _ordered_row(row):
    return {key: row.get(key) for key in CHANGE_DISPLAY_KEYS}
