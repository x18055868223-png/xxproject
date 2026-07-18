#!/usr/bin/env python3
"""Runtime entrypoint for Gemini signal reviews.

The core reviewer remains the source of all schema, policy, and fail-closed
validation. This entrypoint applies only two bounded repairs before the core
validator runs:

1. mechanically impossible ``source_alignment`` enum combinations;
2. explicitly prohibitive execution-language phrases such as "不构成开仓依据"
   that contain blocked action words but do not provide an execution instruction.

Positive or ambiguous execution language, recommendation conflicts, hard blocks,
waiting-state upgrades, invalid evidence, authorization, and all remaining
policy checks stay fail-closed in the core reviewer.
"""

import copy
import json
import re
import sys

import gemini_signal_llm_review as core


ENTRY_VERSION = "gemini_signal_review_entry@1.0.1"
PROMPT_VERSION = "gemini_signal_review_prompt@1.4.6"
_ALLOWED_ALIGNMENTS = set(core.ADVISORY_SOURCE_ALIGNMENTS)
_RECOGNIZED_DIRECTIONS = {"BULLISH", "BEARISH", "NEUTRAL"}

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


def build_prompt(packet, blind_payload=None):
    """Append exact enum and human-language mappings to the core prompt."""
    return (
        _ORIGINAL_BUILD_PROMPT(packet, blind_payload)
        + _ALIGNMENT_PROMPT_RULES
        + _EXECUTION_LANGUAGE_PROMPT_RULES
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


def build_llm_review(card, payload, model=core.DEFAULT_MODEL, reviewed_at=None,
                     derived_blind=True, llm_call_count=2,
                     llm_call_routes=None):
    """Apply bounded repairs, then delegate every validation to the core."""
    packet = core.build_review_packet(card)
    repaired_payload, alignment_trace = _repair_source_alignment(payload, packet)
    repaired_payload, execution_trace = _repair_prohibitive_execution_language(
        repaired_payload
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
