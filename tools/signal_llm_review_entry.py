#!/usr/bin/env python3
"""Runtime entrypoint for provider-neutral signal LLM reviews.

The core reviewer remains the source of all schema, policy, and fail-closed
validation. This entrypoint applies a small audited set of bounded repairs
before the core validator runs:

1. mechanically impossible ``source_alignment`` enum combinations;
2. explicitly prohibitive execution-language phrases such as "不构成开仓依据"
   that contain blocked action words but do not provide an execution instruction.
3. valid recommendations that conflict with a producer hard block, but only
   after the advisory human text is already free of execution-language patterns.
4. known raw field assignments in human text, incomplete assessment objects
   narrowed to ``UNABLE_TO_JUDGE``, and packet-derived wall-distance context.

Positive or ambiguous execution language, invalid recommendation enums,
waiting-state upgrades, invalid evidence, authorization, and all remaining
policy checks stay fail-closed in the core reviewer. The hard-block repair only
narrows a recognized recommendation to the producer's deterministic safe boundary.
"""

import copy
import json
import re
import sys

import signal_llm_review as core


ENTRY_VERSION = "signal_llm_review_entry@1.1.6"
PROMPT_VERSION = "signal_llm_review_prompt@1.5.5"
_ALLOWED_ALIGNMENTS = set(core.ADVISORY_SOURCE_ALIGNMENTS)
_RECOGNIZED_DIRECTIONS = {"BULLISH", "BEARISH", "NEUTRAL"}
_HARD_BLOCK_SAFE_RECOMMENDATIONS = {"NO_TRADE", "UNABLE_TO_JUDGE"}

_ORIGINAL_BUILD_PROMPT = core.build_prompt
_ORIGINAL_BUILD_LLM_REVIEW = core.build_llm_review

_ALIGNMENT_PROMPT_RULES = (
    "\n\nsource_alignment 精确映射（必须逐项执行）：\n"
    "- 只有 producer decision.lean 与盲读 theoretical_active_view.bias 映射后的方向均可识别，"
    "且完全相同，才可填写 ALIGNED。\n"
    "- producer 与盲读分别为明确偏多和明确偏空时，必须填写 DIVERGENT。\n"
    "- 一方为中性、区间或 MIXED_UNCLEAR，且不存在明确多空对立时，填写 PARTIALLY_ALIGNED。\n"
    "- 盲读为 UNABLE_TO_JUDGE，或 producer 方向无法识别时，填写 UNABLE_TO_JUDGE。\n"
    "- 不得把 recommendation 相同、都选择等待、或部分证据相似误写为 ALIGNED。"
)

_EXECUTION_LANGUAGE_PROMPT_RULES = (
    "\n\n人读文案执行词禁用规则（所有 *_cn、premise_cn、invalid_if 均适用）：\n"
    "- 即使是否定、免责声明或风险边界，也不要出现‘开仓、平仓、下单、入场、出场、加仓、减仓、止损、止盈’等词。\n"
    "- ‘不构成开仓依据/不建议下单/暂不入场’统一改写为‘不构成交易执行依据’或‘暂不进入交易复核’。\n"
    "- ‘无需平仓/不建议减仓’统一改写为‘不涉及持仓处置’。\n"
    "- ‘不设置止损止盈’统一改写为‘不涉及退出参数’。\n"
    "- 正向或含糊的执行指令仍属禁止内容，不得用改写规避。"
)

_HUMAN_OUTPUT_PROMPT_RULES = (
    "\n\n最高辅助决策自然语言规则（所有人读字段适用）：\n"
    "- 禁止输出字段路径、KEY=VALUE、JSON 布尔值或机器枚举串；把系统事实直接翻译成普通中文。\n"
    "- 例如不得写 decision_matrix.window=CONFIRMED、lean=NEUTRAL、"
    "blocking=SOFT_GATE WAIT_CONFIRMATION；应分别表述为确认窗口已成立、系统方向保持中性、"
    "当前仍处于等待确认的软门控。\n"
    "- 最高解释可以比较现价到上方看涨墙和下方看跌墙的相对距离，但只能作为空间约束说明，"
    "不得据此单独改变方向、建议或交易许可。\n"
    "- 输出前逐项检查必需对象和人读文本；缺少依据时使用无法判断，不得省略字段。"
)

# These patterns only match explicitly prohibitive/boundary language. They do
# not match positive or ambiguous instructions, which therefore continue to
# fail the core execution-language validator.
_PROHIBITIVE_EXECUTION_PATTERNS = (
    (
        re.compile(
            r"(?:不构成|并不构成|不能构成|尚不构成)\s*"
            r"(?:开仓|下单|入场|加仓|减仓|平仓|出场|止损|止盈)\s*"
            r"(?:条件|依据|信号|建议|指令|许可)?"
        ),
        "不构成交易执行依据",
    ),
    (
        re.compile(
            r"(?:不具备|尚不具备|未具备|缺乏)\s*"
            r"(?:开仓|下单|入场|加仓)\s*"
            r"(?:条件|依据|信号|许可)?"
        ),
        "不具备交易执行条件",
    ),
    (
        re.compile(
            r"(?:不代表|并不代表|不能视为|不得视为|不等于|并非)\s*"
            r"(?:可以|允许|应当|需要|适合)?\s*"
            r"(?:开仓|下单|入场|加仓)"
        ),
        "不代表可进入交易执行",
    ),
    (
        re.compile(
            r"(?:暂不|尚不|不再|无需|无须|不可|不能|不得|不应|不宜|不建议|"
            r"避免|禁止|拒绝)\s*(?:直接|立即|贸然|进行|采取|给出|用于)?\s*"
            r"(?:开仓|下单|入场|加仓)"
        ),
        "暂不进入交易复核",
    ),
    (
        re.compile(
            r"(?:暂不|尚不|不再|无需|无须|不可|不能|不得|不应|不宜|不建议|"
            r"避免|禁止|拒绝)\s*(?:直接|立即|贸然|进行|采取|给出|用于)?\s*"
            r"(?:平仓|出场|减仓)"
        ),
        "不涉及持仓处置",
    ),
    (
        re.compile(
            r"(?:暂不|尚不|不再|无需|无须|不可|不能|不得|不应|不宜|不建议|"
            r"避免|禁止|拒绝)\s*(?:直接|立即|贸然|进行|采取|给出|用于)?\s*"
            r"(?:止损|止盈)"
        ),
        "不涉及退出参数",
    ),
    (
        re.compile(
            r"(?:开仓|下单|入场|加仓)\s*"
            r"(?:条件|依据|信号|许可)\s*"
            r"(?:不足|缺失|未满足|尚未形成|不成立)"
        ),
        "交易执行条件不足",
    ),
)


def build_prompt(packet, blind_payload=None, empty_content_retry_count=0):
    """Append exact enum and human-language mappings to the core prompt."""
    return (
        _ORIGINAL_BUILD_PROMPT(
            packet,
            blind_payload,
            empty_content_retry_count=empty_content_retry_count,
        )
        + _ALIGNMENT_PROMPT_RULES
        + _EXECUTION_LANGUAGE_PROMPT_RULES
        + _HUMAN_OUTPUT_PROMPT_RULES
    )


def _alignment_context(payload, packet):
    view = core._as_dict(core._as_dict(payload).get("theoretical_active_view"))
    blind_bias = str(view.get("bias") or "").upper()
    blind_direction = core._advisory_direction(blind_bias)
    producer_lean = core._as_dict(core._as_dict(packet).get("decision")).get("lean")
    producer_direction = core._advisory_direction(producer_lean)
    return blind_bias, blind_direction, str(producer_lean or ""), producer_direction


def _repair_source_alignment(payload, packet):
    """Repair only enum combinations that the core validator proves impossible."""
    repaired_payload = copy.deepcopy(payload)
    advisory = core._as_dict(repaired_payload.get("integrated_trade_advisory"))
    claimed = str(advisory.get("source_alignment") or "").upper()
    blind_bias, blind_direction, producer_lean, producer_direction = (
        _alignment_context(repaired_payload, packet)
    )
    final = claimed
    reason = "NONE"

    # Missing or invalid enum values remain fail-closed in the core validator.
    if claimed in _ALLOWED_ALIGNMENTS:
        direct_opposition = {
            producer_direction,
            blind_direction,
        } == {"BULLISH", "BEARISH"}
        aligned_pair = (
            producer_direction in _RECOGNIZED_DIRECTIONS
            and producer_direction == blind_direction
        )

        if blind_bias == "UNABLE_TO_JUDGE" and claimed != "UNABLE_TO_JUDGE":
            final = "UNABLE_TO_JUDGE"
            reason = "BLIND_UNABLE_REQUIRES_UNABLE_ALIGNMENT"
        elif producer_direction == "UNKNOWN" and claimed == "ALIGNED":
            final = "UNABLE_TO_JUDGE"
            reason = "UNKNOWN_PRODUCER_CANNOT_BE_ALIGNED"
        elif direct_opposition and claimed != "DIVERGENT":
            final = "DIVERGENT"
            reason = "DIRECT_DIRECTION_OPPOSITION_REQUIRES_DIVERGENT"
        elif claimed == "ALIGNED" and not aligned_pair:
            final = "PARTIALLY_ALIGNED"
            reason = "ALIGNED_REQUIRES_EXACT_RECOGNIZED_DIRECTION_MATCH"

    repaired = final != claimed
    if repaired:
        advisory = dict(advisory)
        advisory["source_alignment"] = final
        repaired_payload["integrated_trade_advisory"] = advisory

    trace = {
        "entry_version": ENTRY_VERSION,
        "repair_applied": repaired,
        "repair_reason": reason,
        "claimed": claimed,
        "final": final,
        "blind_bias": blind_bias,
        "blind_direction": blind_direction,
        "producer_lean": producer_lean,
        "producer_direction": producer_direction,
    }
    return repaired_payload, trace


def _rewrite_prohibitive_execution_text(value):
    if not isinstance(value, str) or not value:
        return value, 0

    rewritten = value
    count = 0
    for pattern, replacement in _PROHIBITIVE_EXECUTION_PATTERNS:
        rewritten, substitutions = pattern.subn(replacement, rewritten)
        count += substitutions
    return rewritten, count


def _repair_prohibitive_execution_language(payload):
    """Rewrite only explicit prohibitions/boundaries, never positive instructions."""
    repaired_payload = copy.deepcopy(payload)
    advisory = core._as_dict(repaired_payload.get("integrated_trade_advisory"))
    if not advisory:
        return repaired_payload, {
            "repair_applied": False,
            "repair_count": 0,
            "repair_fields": [],
        }

    advisory = copy.deepcopy(advisory)
    fields = []
    total = 0

    def repair_field(container, key, path):
        nonlocal total
        value = container.get(key)
        rewritten, substitutions = _rewrite_prohibitive_execution_text(value)
        if substitutions:
            container[key] = rewritten
            total += substitutions
            fields.append(path)

    for field_name in (
        "final_conclusion_cn",
        "cross_loop_rationale_cn",
        "side_basis_cn",
        "dominant_conflict_cn",
        "next_observation_cn",
    ):
        repair_field(advisory, field_name, field_name)

    for object_name in (
        "containment_assessment",
        "premium_selling_fit",
        "session_advisory",
    ):
        child = core._as_dict(advisory.get(object_name))
        if child:
            child = dict(child)
            repair_field(child, "basis_cn", object_name + ".basis_cn")
            advisory[object_name] = child

    premises = []
    for index, item in enumerate(advisory.get("key_premises") or []):
        item = dict(core._as_dict(item))
        repair_field(
            item,
            "premise_cn",
            "key_premises[" + str(index) + "].premise_cn",
        )
        premises.append(item)
    if isinstance(advisory.get("key_premises"), list):
        advisory["key_premises"] = premises

    invalid_if = []
    for index, item in enumerate(advisory.get("invalid_if") or []):
        rewritten, substitutions = _rewrite_prohibitive_execution_text(item)
        if substitutions:
            total += substitutions
            fields.append("invalid_if[" + str(index) + "]")
        invalid_if.append(rewritten)
    if isinstance(advisory.get("invalid_if"), list):
        advisory["invalid_if"] = invalid_if

    repaired_payload["integrated_trade_advisory"] = advisory
    return repaired_payload, {
        "repair_applied": bool(total),
        "repair_count": total,
        "repair_fields": sorted(set(fields)),
    }


_HUMAN_CODE_REPLACEMENTS = {
    "SELL_PUT_SPREAD_REVIEW": "看跌期权价差结构复核",
    "SELL_CALL_SPREAD_REVIEW": "看涨期权价差结构复核",
    "NEUTRAL_SINGLE_SIDE_REVIEW": "中性单侧结构复核",
    "WAIT_FOR_CONFIRMATION": "等待确认",
    "NO_TRADE": "不交易",
    "UNABLE_TO_JUDGE": "无法判断",
    "ESTABLISHED": "已成立",
    "INCOMPLETE": "不完整",
    "FAILED": "未成立",
    "FIT": "适配",
    "CONDITIONAL": "有条件适配",
    "NOT_FIT": "不适配",
    "ALIGNED": "一致",
    "CAUTION": "谨慎",
    "TIME_ONLY": "仅时间维度",
    "UNKNOWN": "未知",
    "NONE": "无",
    "INFO": "提示",
    "HIGH": "高",
    "PARTIALLY_ALIGNED": "部分一致",
    "DIVERGENT": "背离",
    "trade_allowed": "交易许可字段",
    "execution_allowed": "执行许可字段",
    "source_alignment": "来源一致性",
    "recommendation": "辅助建议",
    "audit_only": "仅审计",
    "trade_authorization": "交易授权",
    "evidence_refs": "证据引用",
    "WAIT_CONFIRMATION": "等待确认",
}

_HUMAN_ASSIGNMENT_REPLACEMENTS = (
    (
        re.compile(
            r"(?<![A-Za-z0-9_])(?:decision_matrix\.)?window\s*=\s*CONFIRMED",
            re.IGNORECASE,
        ),
        "系统确认窗口已成立",
        "decision_window_confirmed",
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_])(?:decision\.|producer_)?lean\s*=\s*NEUTRAL",
            re.IGNORECASE,
        ),
        "系统方向保持中性",
        "decision_lean_neutral",
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_])(?:blocking(?:\.(?:level|state))?)\s*=\s*SOFT_GATE"
            r"(?:[\s,，;/]*WAIT(?:_FOR)?_CONFIRMATION)?",
            re.IGNORECASE,
        ),
        "当前仍处于等待确认的软门控",
        "blocking_soft_gate_wait",
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9_])(?:decision\.)?trade_allowed\s*=\s*false|"
            r"(?<![A-Za-z0-9_])(?:decision_matrix\.)?execution_allowed\s*=\s*false",
            re.IGNORECASE,
        ),
        "当前未开放交易执行许可",
        "execution_permission_false",
    ),
)


def _rewrite_human_codes(value):
    if not isinstance(value, str) or not value:
        return value, 0, []
    rewritten = value
    total = 0
    repaired_tokens = []
    for pattern, replacement, label in _HUMAN_ASSIGNMENT_REPLACEMENTS:
        rewritten, substitutions = pattern.subn(replacement, rewritten)
        if substitutions:
            total += substitutions
            repaired_tokens.append(label)
    for token in sorted(_HUMAN_CODE_REPLACEMENTS, key=len, reverse=True):
        pattern = re.compile(
            r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])",
            re.IGNORECASE,
        )
        rewritten, substitutions = pattern.subn(
            _HUMAN_CODE_REPLACEMENTS[token], rewritten)
        if substitutions:
            total += substitutions
            repaired_tokens.append(token)
    return rewritten, total, repaired_tokens


def _repair_incomplete_advisory_assessments(payload):
    """Replace only incomplete assessment objects with a fail-closed value."""
    repaired_payload = copy.deepcopy(payload)
    advisory = core._as_dict(repaired_payload.get("integrated_trade_advisory"))
    if not advisory:
        return repaired_payload, {
            "repair_applied": False,
            "repair_fields": [],
        }

    advisory = copy.deepcopy(advisory)
    repaired_fields = []
    contracts = (
        (
            "containment_assessment",
            core.ADVISORY_CONTAINMENT_STATES,
            "当前输出未完整提供中性接管评估，因此安全降级为无法判断。",
        ),
        (
            "premium_selling_fit",
            core.ADVISORY_PREMIUM_FIT_STATES,
            "当前输出未完整提供卖方结构适配依据，因此安全降级为无法判断。",
        ),
    )
    for field_name, allowed_states, basis_cn in contracts:
        assessment = core._as_dict(advisory.get(field_name))
        valid = (
            set(assessment) == {"state", "basis_cn"}
            and str(assessment.get("state") or "").upper() in allowed_states
            and isinstance(assessment.get("basis_cn"), str)
            and bool(assessment["basis_cn"].strip())
        )
        if valid:
            continue
        # Only missing/incomplete shapes are mechanically repairable. An
        # explicit but invalid state or an unexpected key remains a validator
        # failure instead of being silently rewritten.
        if assessment and (
                not set(assessment).issubset({"state", "basis_cn"})
                or (
                    assessment.get("state") not in (None, "")
                    and str(assessment.get("state")).upper()
                    not in allowed_states
                )):
            continue
        advisory[field_name] = {
            "state": "UNABLE_TO_JUDGE",
            "basis_cn": basis_cn,
        }
        repaired_fields.append(field_name)

    repaired_payload["integrated_trade_advisory"] = advisory
    return repaired_payload, {
        "repair_applied": bool(repaired_fields),
        "repair_fields": repaired_fields,
    }


def _wall_level(packet, name):
    factor = core._as_dict(core._as_dict(packet).get("factor_cross_section"))
    gex = core._as_dict(factor.get("gex_info"))
    gamma = core._as_dict(factor.get("gamma_regime"))
    value = core._number_or_none(gex.get(name))
    return value if value is not None else core._number_or_none(gamma.get(name))


def _wall_position_sentence(packet):
    price = core._number_or_none(
        core._as_dict(core._as_dict(packet).get("market_context")).get("price")
    )
    call_wall = _wall_level(packet, "call_wall")
    put_wall = _wall_level(packet, "put_wall")
    if (
            price is None or price <= 0
            or call_wall is None or put_wall is None
            or call_wall <= 0 or put_wall <= 0
            or call_wall <= put_wall):
        return "", {}

    call_distance = abs(call_wall - price) / price * 100
    put_distance = abs(price - put_wall) / price * 100
    if put_wall <= price <= call_wall:
        nearer = "下方看跌墙" if put_distance < call_distance else "上方看涨墙"
        position = "位于两道墙之间，位置更靠近" + nearer
    elif price < put_wall:
        position = "位于下方看跌墙之下"
    else:
        position = "位于上方看涨墙之上"
    sentence = (
        f"卡内现价{price:,.2f}{position}；距上方看涨墙{call_wall:,.2f}约"
        f"{call_distance:.2f}%，距下方看跌墙{put_wall:,.2f}约{put_distance:.2f}%。"
        "该相对位置只用于解释上下空间约束，不单独决定方向或辅助建议。"
    )
    return sentence, {
        "price": price,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "call_wall_distance_pct": round(call_distance, 4),
        "put_wall_distance_pct": round(put_distance, 4),
    }


def _append_wall_position_explanation(payload, packet):
    repaired_payload = copy.deepcopy(payload)
    advisory = core._as_dict(repaired_payload.get("integrated_trade_advisory"))
    sentence, facts = _wall_position_sentence(packet)
    if not advisory or not sentence:
        return repaired_payload, {
            "applied": False,
            "facts": facts,
        }
    advisory = copy.deepcopy(advisory)
    current = str(advisory.get("cross_loop_rationale_cn") or "").strip()
    if sentence in current:
        return repaired_payload, {
            "applied": False,
            "facts": facts,
        }
    keep = max(0, 519 - len(sentence))
    prefix = current[:keep].rstrip("，,；;。 ")
    advisory["cross_loop_rationale_cn"] = (
        (prefix + "。" if prefix else "") + sentence
    )[:520]
    repaired_payload["integrated_trade_advisory"] = advisory
    return repaired_payload, {
        "applied": True,
        "facts": facts,
    }


def _repair_advisory_human_codes(payload):
    """Humanize raw advisory codes only in fields owned by human text."""
    repaired_payload = copy.deepcopy(payload)
    advisory = core._as_dict(repaired_payload.get("integrated_trade_advisory"))
    if not advisory:
        return repaired_payload, {
            "repair_applied": False,
            "repair_count": 0,
            "repair_fields": [],
            "repair_tokens": [],
        }

    advisory = copy.deepcopy(advisory)
    fields = []
    tokens = []
    total = 0

    def repair_field(container, key, path):
        nonlocal total
        value = container.get(key)
        if not isinstance(value, str):
            return
        rewritten, substitutions, repaired_tokens = _rewrite_human_codes(value)
        if substitutions:
            container[key] = rewritten
            total += substitutions
            fields.append(path)
            tokens.extend(repaired_tokens)

    for field_name in (
        "final_conclusion_cn",
        "cross_loop_rationale_cn",
        "side_basis_cn",
        "dominant_conflict_cn",
        "next_observation_cn",
    ):
        repair_field(advisory, field_name, field_name)

    for object_name in (
        "containment_assessment",
        "premium_selling_fit",
        "session_advisory",
    ):
        child = core._as_dict(advisory.get(object_name))
        if child:
            child = dict(child)
            repair_field(child, "basis_cn", object_name + ".basis_cn")
            advisory[object_name] = child

    premises = []
    for index, item in enumerate(advisory.get("key_premises") or []):
        item = dict(core._as_dict(item))
        repair_field(
            item,
            "premise_cn",
            "key_premises[" + str(index) + "].premise_cn",
        )
        premises.append(item)
    if isinstance(advisory.get("key_premises"), list):
        advisory["key_premises"] = premises

    invalid_if = []
    for index, item in enumerate(advisory.get("invalid_if") or []):
        rewritten, substitutions, repaired_tokens = _rewrite_human_codes(item)
        if substitutions:
            total += substitutions
            fields.append("invalid_if[" + str(index) + "]")
            tokens.extend(repaired_tokens)
        invalid_if.append(rewritten)
    if isinstance(advisory.get("invalid_if"), list):
        advisory["invalid_if"] = invalid_if

    repaired_payload["integrated_trade_advisory"] = advisory
    return repaired_payload, {
        "repair_applied": bool(total),
        "repair_count": total,
        "repair_fields": sorted(set(fields)),
        "repair_tokens": sorted(set(tokens)),
    }


def _repair_misclassified_observed_levels(payload, packet):
    """Downgrade unmatched observed levels to explicitly model-estimated levels."""
    repaired_payload = copy.deepcopy(payload)
    advisory = core._as_dict(repaired_payload.get("integrated_trade_advisory"))
    report = core._as_dict(advisory.get("future_24h_bayesian_report"))
    levels = report.get("key_levels")
    if not isinstance(levels, list):
        return repaired_payload, {
            "repair_applied": False,
            "repair_count": 0,
            "repair_indexes": [],
        }

    packet_values = core._packet_numeric_values(packet or {})
    repaired_levels = []
    repaired_indexes = []
    for index, raw_item in enumerate(levels):
        item = dict(core._as_dict(raw_item))
        price = core._number_or_none(item.get("price"))
        matched = (
            price is not None
            and any(
                abs(price - observed) <= max(1e-9, abs(observed) * 1e-12)
                for observed in packet_values
            )
        )
        if item.get("source_type") == "PACKET_OBSERVED" and not matched:
            item["source_type"] = "MODEL_ESTIMATED"
            basis = str(item.get("basis_cn") or "").strip()
            if "模型估算观察位" not in basis:
                item["basis_cn"] = (
                    "模型估算观察位：" + basis
                    if basis
                    else "模型估算观察位：该点位未在输入卡中逐值出现。"
                )
            repaired_indexes.append(index)
        repaired_levels.append(item)

    if not repaired_indexes:
        return repaired_payload, {
            "repair_applied": False,
            "repair_count": 0,
            "repair_indexes": [],
        }

    report = dict(report)
    report["key_levels"] = repaired_levels
    advisory = dict(advisory)
    advisory["future_24h_bayesian_report"] = report
    repaired_payload["integrated_trade_advisory"] = advisory
    return repaired_payload, {
        "repair_applied": True,
        "repair_count": len(repaired_indexes),
        "repair_indexes": repaired_indexes,
    }


_HARD_BLOCK_TEXT = {
    "final_conclusion_cn": "生产端硬性阻断仍然有效，本轮仅能保留为不交易观察结论。",
    "cross_loop_rationale_cn": (
        "跨回路证据必须先服从生产端阻断，结构适配或等待观察都不能覆盖该边界。"
    ),
    "side_basis_cn": "方向侧判断在阻断解除前只作为审计背景，不形成交易侧复核。",
    "dominant_conflict_cn": (
        "主导冲突是生产端阻断仍未解除，其他结构条件不能越过该边界。"
    ),
    "next_observation_cn": (
        "下一步只观察阻断来源是否解除，以及关键证据是否重新形成一致。"
    ),
}
_HARD_BLOCK_INVALID_IF = [
    "生产端阻断解除且新证据重新生成后，需要重新评估本轮审计结论。",
]
_HARD_BLOCK_CONTAINMENT_BASIS = (
    "中性接管状态按原始评估保留；但生产端阻断优先，当前只用于风险封存说明。"
)
_HARD_BLOCK_PREMIUM_BASIS = (
    "权利金结构适配度按原始评估保留；但生产端阻断优先，不能推进结构复核。"
)


def _advisory_execution_terms(advisory):
    text = core._integrated_advisory_human_text(advisory)
    return sorted(
        label for label, pattern in core.ADVISORY_EXECUTION_TEXT_PATTERNS
        if pattern.search(text)
    )


def _hard_block_shape_is_valid_enough_to_repair(advisory):
    if not isinstance(advisory, dict):
        return False
    for field_name in _HARD_BLOCK_TEXT:
        if not isinstance(advisory.get(field_name), str) or not advisory[field_name].strip():
            return False
    invalid_if = advisory.get("invalid_if")
    if not isinstance(invalid_if, list) or not (1 <= len(invalid_if) <= 3):
        return False
    if any(not isinstance(item, str) or not item.strip() for item in invalid_if):
        return False
    containment = core._as_dict(advisory.get("containment_assessment"))
    premium_fit = core._as_dict(advisory.get("premium_selling_fit"))
    if set(containment) != {"state", "basis_cn"}:
        return False
    if set(premium_fit) != {"state", "basis_cn"}:
        return False
    if not isinstance(containment.get("basis_cn"), str) or not containment["basis_cn"].strip():
        return False
    if not isinstance(premium_fit.get("basis_cn"), str) or not premium_fit["basis_cn"].strip():
        return False
    return True


def _repair_hard_block_recommendation(payload, packet):
    """Force only valid non-safe recommendations to NO_TRADE under hard block."""
    repaired_payload = copy.deepcopy(payload)
    advisory = core._as_dict(repaired_payload.get("integrated_trade_advisory"))
    claimed = str(advisory.get("recommendation") or "").upper()
    final = claimed
    reason = "NONE"
    execution_terms = []

    if not core._packet_has_producer_hard_block(packet):
        reason = "NO_PRODUCER_HARD_BLOCK"
    elif claimed not in core.ADVISORY_RECOMMENDATIONS:
        reason = "INVALID_RECOMMENDATION_FAIL_CLOSED"
    elif claimed in _HARD_BLOCK_SAFE_RECOMMENDATIONS:
        reason = "ALREADY_HARD_BLOCK_COMPATIBLE"
    else:
        execution_terms = _advisory_execution_terms(advisory)
        if execution_terms:
            reason = "ORIGINAL_EXECUTION_LANGUAGE_REMAINS_FAIL_CLOSED"
        elif not _hard_block_shape_is_valid_enough_to_repair(advisory):
            reason = "ADVISORY_SHAPE_INCOMPLETE_FAIL_CLOSED"
        else:
            advisory = copy.deepcopy(advisory)
            advisory["recommendation"] = "NO_TRADE"
            for field_name, text in _HARD_BLOCK_TEXT.items():
                advisory[field_name] = text
            advisory["invalid_if"] = list(_HARD_BLOCK_INVALID_IF)

            containment = dict(core._as_dict(advisory.get("containment_assessment")))
            containment["basis_cn"] = _HARD_BLOCK_CONTAINMENT_BASIS
            advisory["containment_assessment"] = containment

            premium_fit = dict(core._as_dict(advisory.get("premium_selling_fit")))
            premium_fit["basis_cn"] = _HARD_BLOCK_PREMIUM_BASIS
            advisory["premium_selling_fit"] = premium_fit

            repaired_payload["integrated_trade_advisory"] = advisory
            final = "NO_TRADE"
            reason = "PRODUCER_HARD_BLOCK_FORCES_NO_TRADE"

    trace = {
        "entry_version": ENTRY_VERSION,
        "repair_applied": final != claimed,
        "repair_reason": reason,
        "claimed": claimed,
        "final": final,
        "execution_language_terms": execution_terms,
    }
    return repaired_payload, trace


def _repair_unsafe_structure_recommendation(payload, packet):
    """Narrow structurally impossible recognized recommendations to NO_TRADE."""
    repaired_payload = copy.deepcopy(payload)
    advisory = core._as_dict(repaired_payload.get("integrated_trade_advisory"))
    claimed = str(advisory.get("recommendation") or "").upper()
    final = claimed
    reasons = []

    containment = core._as_dict(advisory.get("containment_assessment"))
    premium_fit = core._as_dict(advisory.get("premium_selling_fit"))
    containment_state = str(containment.get("state") or "").upper()
    premium_state = str(premium_fit.get("state") or "").upper()

    if claimed in core.ADVISORY_SPREAD_RECOMMENDATIONS:
        if core._packet_requires_confirmation(packet):
            reasons.append("PACKET_REQUIRES_CONFIRMATION")
        if containment_state != "ESTABLISHED":
            reasons.append("CONTAINMENT_NOT_ESTABLISHED")
        if premium_state not in {"FIT", "CONDITIONAL"}:
            reasons.append("PREMIUM_STRUCTURE_NOT_FIT")

        blind_bias, blind_direction, _, producer_direction = (
            _alignment_context(repaired_payload, packet)
        )
        if claimed == "SELL_PUT_SPREAD_REVIEW" and (
                producer_direction != "BULLISH"
                or blind_direction == "BEARISH"):
            reasons.append("PUT_SPREAD_DIRECTION_MISMATCH")
        elif claimed == "SELL_CALL_SPREAD_REVIEW" and (
                producer_direction != "BEARISH"
                or blind_direction == "BULLISH"):
            reasons.append("CALL_SPREAD_DIRECTION_MISMATCH")
        elif claimed == "NEUTRAL_SINGLE_SIDE_REVIEW" and (
                producer_direction != "NEUTRAL"
                or blind_bias == "UNABLE_TO_JUDGE"):
            reasons.append("NEUTRAL_STRUCTURE_DIRECTION_MISMATCH")

        future = core._as_dict(advisory.get("future_24h_bayesian_report"))
        base_case = str(future.get("base_case") or "").upper()
        if ((base_case == "UP" and (
                producer_direction == "BEARISH" or blind_direction == "BEARISH"))
                or (base_case == "DOWN" and (
                    producer_direction == "BULLISH" or blind_direction == "BULLISH"))):
            reasons.append("FUTURE_DIRECTIONAL_CONFLICT")
    elif (claimed == "UNABLE_TO_JUDGE"
          and containment_state != "UNABLE_TO_JUDGE"
          and premium_state != "UNABLE_TO_JUDGE"):
        reasons.append("UNABLE_RECOMMENDATION_WITHOUT_UNABLE_ASSESSMENT")

    if reasons:
        advisory = dict(advisory)
        advisory["recommendation"] = "NO_TRADE"
        repaired_payload["integrated_trade_advisory"] = advisory
        final = "NO_TRADE"

    return repaired_payload, {
        "entry_version": ENTRY_VERSION,
        "repair_applied": final != claimed,
        "repair_reasons": sorted(set(reasons)),
        "claimed": claimed,
        "final": final,
    }


def build_llm_review(card, payload, model=core.DEFAULT_MODEL, reviewed_at=None,
                     derived_blind=True, llm_call_count=2,
                     llm_call_routes=None):
    """Apply bounded repairs, then delegate every validation to the core."""
    packet = core.build_review_packet(card)
    repaired_payload, alignment_trace = _repair_source_alignment(payload, packet)
    repaired_payload, execution_trace = _repair_prohibitive_execution_language(
        repaired_payload
    )
    repaired_payload, assessment_trace = _repair_incomplete_advisory_assessments(
        repaired_payload
    )
    repaired_payload, hard_block_trace = _repair_hard_block_recommendation(
        repaired_payload, packet
    )
    repaired_payload, structure_trace = _repair_unsafe_structure_recommendation(
        repaired_payload, packet
    )
    repaired_payload, key_level_trace = _repair_misclassified_observed_levels(
        repaired_payload, packet
    )
    repaired_payload, human_code_trace = _repair_advisory_human_codes(
        repaired_payload
    )
    repaired_payload, wall_position_trace = _append_wall_position_explanation(
        repaired_payload, packet
    )
    review = _ORIGINAL_BUILD_LLM_REVIEW(
        card,
        repaired_payload,
        model=model,
        reviewed_at=reviewed_at,
        derived_blind=derived_blind,
        llm_call_count=llm_call_count,
        llm_call_routes=llm_call_routes,
    )
    policy = core._as_dict(
        core._as_dict(review.get("integrated_trade_advisory")).get(
            "policy_validation"
        )
    )
    policy.update({
        "source_alignment_repair_applied": alignment_trace["repair_applied"],
        "source_alignment_repair_reason": alignment_trace["repair_reason"],
        "source_alignment_claimed": alignment_trace["claimed"],
        "source_alignment_final": alignment_trace["final"],
        "source_alignment_blind_direction": alignment_trace["blind_direction"],
        "source_alignment_producer_direction": alignment_trace["producer_direction"],
        "prohibitive_execution_language_repair_applied": execution_trace[
            "repair_applied"
        ],
        "prohibitive_execution_language_repair_count": execution_trace[
            "repair_count"
        ],
        "prohibitive_execution_language_repair_fields": execution_trace[
            "repair_fields"
        ],
        "hard_block_recommendation_repair_applied": hard_block_trace[
            "repair_applied"
        ],
        "hard_block_recommendation_repair_reason": hard_block_trace[
            "repair_reason"
        ],
        "unsafe_structure_recommendation_repair_applied": structure_trace[
            "repair_applied"
        ],
        "unsafe_structure_recommendation_repair_reasons": structure_trace[
            "repair_reasons"
        ],
        "hard_block_recommendation_claimed": hard_block_trace["claimed"],
        "hard_block_recommendation_final": hard_block_trace["final"],
        "hard_block_recommendation_entry_version": hard_block_trace[
            "entry_version"
        ],
        "human_code_repair_applied": human_code_trace["repair_applied"],
        "human_code_repair_count": human_code_trace["repair_count"],
        "human_code_repair_fields": human_code_trace["repair_fields"],
        "human_code_repair_tokens": human_code_trace["repair_tokens"],
        "assessment_completion_repair_applied": assessment_trace[
            "repair_applied"
        ],
        "assessment_completion_repair_fields": assessment_trace[
            "repair_fields"
        ],
        "wall_position_explanation_applied": wall_position_trace["applied"],
        "wall_position_facts": wall_position_trace["facts"],
        "key_level_source_repair_applied": key_level_trace["repair_applied"],
        "key_level_source_repair_count": key_level_trace["repair_count"],
        "key_level_source_repair_indexes": key_level_trace["repair_indexes"],
        "alignment_entry_version": ENTRY_VERSION,
    })
    review["integrated_trade_advisory"]["policy_validation"] = policy

    if alignment_trace["repair_applied"]:
        print(
            json.dumps(
                {"event": "SOURCE_ALIGNMENT_REPAIRED", **alignment_trace},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    if execution_trace["repair_applied"]:
        print(
            json.dumps(
                {
                    "event": "PROHIBITIVE_EXECUTION_LANGUAGE_REPAIRED",
                    "entry_version": ENTRY_VERSION,
                    **execution_trace,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    if hard_block_trace["repair_applied"]:
        print(
            json.dumps(
                {
                    "event": "PRODUCER_HARD_BLOCK_RECOMMENDATION_REPAIRED",
                    **hard_block_trace,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    if structure_trace["repair_applied"]:
        print(
            json.dumps(
                {"event": "UNSAFE_STRUCTURE_RECOMMENDATION_REPAIRED",
                 **structure_trace},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    if human_code_trace["repair_applied"]:
        print(
            json.dumps(
                {
                    "event": "ADVISORY_HUMAN_CODES_REPAIRED",
                    "entry_version": ENTRY_VERSION,
                    **human_code_trace,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    if assessment_trace["repair_applied"]:
        print(
            json.dumps(
                {
                    "event": "ADVISORY_ASSESSMENT_COMPLETED_FAIL_CLOSED",
                    "entry_version": ENTRY_VERSION,
                    **assessment_trace,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    if wall_position_trace["applied"]:
        print(
            json.dumps(
                {
                    "event": "ADVISORY_WALL_POSITION_EXPLANATION_APPENDED",
                    "entry_version": ENTRY_VERSION,
                    **wall_position_trace,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    if key_level_trace["repair_applied"]:
        print(
            json.dumps(
                {
                    "event": "KEY_LEVEL_SOURCE_DOWNGRADED",
                    "entry_version": ENTRY_VERSION,
                    **key_level_trace,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    return review


# Patch only the two card-review entrypoints used by core.main/generate_reviews.
# Transition review functions and all core validators remain untouched.
core.PROMPT_VERSION = PROMPT_VERSION
core.build_prompt = build_prompt
core.build_llm_review = build_llm_review


def main(argv=None):
    return core.main(argv)


if __name__ == "__main__":
    sys.exit(main())
