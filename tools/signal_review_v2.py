#!/usr/bin/env python3
"""Pure helpers for Astra single-pass signal evidence reviews.

This module does not call the model.  It builds the bounded request payload,
normalizes one model response, derives local action states, and publishes a
stable summary hash for downstream materializers.
"""

from __future__ import annotations

import copy
import datetime as _dt
from decimal import Decimal
import hashlib
import json
import math
import re
from typing import Any


DEFAULT_MODEL = "deepseek-v4-flash"
PROVIDER = "deepseek"
LEGACY_OUTPUT_SCHEMA_VERSION = "signal_llm_review@2.0.0"
OUTPUT_SCHEMA_VERSION_2_1 = "signal_llm_review@2.1.0"
OUTPUT_SCHEMA_VERSION = "signal_llm_review@2.2.0"
LEGACY_PROMPT_VERSION = "signal_llm_review_prompt@2.0.0"
PROMPT_VERSION_2_0_1 = "signal_llm_review_prompt@2.0.1"
PROMPT_VERSION_2_1_0 = "signal_llm_review_prompt@2.1.0"
PROMPT_VERSION = "signal_llm_review_prompt@2.2.0"
MAIN_PROMPT_VERSION = PROMPT_VERSION
REVIEW_MODE = "single_evidence_v2"
LEGACY_PACKET_SCHEMA_VERSION = "signal_evidence_packet@2.0.0"
PACKET_SCHEMA_VERSION = "signal_evidence_packet@2.1.0"
SUMMARY_SCHEMA_VERSION = "signal_evidence_summary@2.2.0"
DISPLAY_PROJECTION_VERSION = "2.2.0"
PRICE_BIAS_SCHEMA_VERSION = "price_bias@1.0.0"
ADVISORY_GUIDANCE_SCHEMA_VERSION = "advisory_guidance@1.0.0"

SIDE_KEYS = ("put_credit", "call_credit")
SIDE_FIT_THESES = {
    "put_credit": "put_downside_containment",
    "call_credit": "call_upside_containment",
}
GRADES = ("D", "C", "B", "A", "S")
SIDE_STATUS = ("RATED", "UNRATED")
REVIEW_STATUS = ("OK", "PARTIAL", "ERROR")
ACCEPTED_OUTPUT_SCHEMA_VERSIONS = (
    LEGACY_OUTPUT_SCHEMA_VERSION,
    OUTPUT_SCHEMA_VERSION_2_1,
    OUTPUT_SCHEMA_VERSION,
)
ACCEPTED_PROMPT_VERSIONS = (
    LEGACY_PROMPT_VERSION,
    PROMPT_VERSION_2_0_1,
    PROMPT_VERSION_2_1_0,
    PROMPT_VERSION,
)
ACCEPTED_PACKET_SCHEMA_VERSIONS = (LEGACY_PACKET_SCHEMA_VERSION, PACKET_SCHEMA_VERSION)
PRICE_BIASES = ("BULLISH", "BEARISH", "NEUTRAL", "MIXED", "UNDETERMINED")
PRICE_BIAS_STATUS = ("ASSESSED", "UNAVAILABLE")
EVIDENCE_ROLES = ("supports_fit", "counters_fit", "context_only")
COMPARISON_SIDES = ("put_credit", "call_credit", "tie", "not_comparable")
COMPARISON_STATUS = ("ASSESSED", "UNAVAILABLE")

LEGACY_MODEL_SIDE_FIELDS = {
    "grade",
    "basis_cn",
    "market_counter_cn",
    "alternative_cn",
    "next_observation_cn",
    "invalid_if_cn",
    "evidence_refs",
    "counter_evidence_refs",
    "unresolved_conditions_cn",
}
MODEL_SIDE_FIELDS_V21 = {
    "grade",
    "basis_cn",
    "mechanism_cn",
    "market_counter_cn",
    "alternative_cn",
    "next_observation_cn",
    "strengthen_if_cn",
    "weaken_if_cn",
    "evidence_roles",
    "unresolved_conditions_cn",
}
MODEL_SIDE_FIELDS = {
    "grade",
    "basis_cn",
    "mechanism",
    "primary_counter_ref",
    "alternative_cn",
    "next_observation_cn",
    "strengthen_if_cn",
    "weaken_if_cn",
    "evidence_roles",
    "unresolved_conditions_cn",
}
MODEL_SIDE_ROLE_FIELDS = {"ref", "role", "claim_cn"}
MODEL_SIDE_MECHANISM_FIELDS = {"summary_cn", "refs"}
MODEL_PRICE_BIAS_FIELDS = {
    "bias",
    "basis_cn",
    "counter_cn",
    "invalid_if_cn",
    "evidence_refs",
    "counter_evidence_refs",
}
MODEL_COMPARISON_FIELDS = {
    "relative_side",
    "basis_cn",
    "evidence_refs",
    "flip_if_cn",
}
MODEL_ADVISORY_GUIDANCE_FIELDS = {
    "summary_cn",
    "tradeoffs_cn",
    "evidence_refs",
    "outlooks",
}
MODEL_GUIDANCE_OUTLOOK_FIELDS = {
    "horizon_hours",
    "scenario_cn",
    "watch_cn",
    "evidence_refs",
}

_CONSTRAINT_WORD_RE = re.compile(
    r"(空间|约束|结构|锚|墙|边界|区间|位置|距离|反转|Gamma|GEX|"
    r"不利侧|侵入|传导|吸收|路径质量|价格表现|迁移|支撑|阻力)",
    re.IGNORECASE,
)
_PRICE_BIAS_SIGNAL_RE = re.compile(
    r"(压力|响应|主动|成交|买卖|量价|价格表现|路径|推进|传导|吸收|"
    r"宏观|资金|费率|Funding|TMV|CVD|上行|下行|偏多|偏空|中性|混合)",
    re.IGNORECASE,
)
_MACHINE_TEXT_PATTERNS = (
    re.compile(r"\b[a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+){1,}\b"),
    re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b"),
    re.compile(
        r"\b(?:schema|hash|source_ref|field|enum|confidence|durability|"
        r"execution_allowed|trade_allowed|support_label|decision_matrix)\b",
        re.IGNORECASE,
    ),
    re.compile(r"[A-Za-z]:\\[^,\]\}\n\r\t ]+"),
)
_INTERNAL_IDENTIFIER_RE = re.compile(
    r"\b(?:put_credit|call_credit|supports_fit|counters_fit|context_only|"
    r"put_downside_containment|call_upside_containment|fit_thesis|"
    r"evidence_roles|side_comparison|side_evidence_ratings)\b"
)
_FORBIDDEN_TRADE_TEXT_RE = re.compile(
    r"(?:行权价|履约价|strike|挂单价|限价|仓位|杠杆)\s*[:：为=]?\s*\d"
    r"|(?:立即|直接|自动|现在|建议)\s*(?:下单|开仓|建仓|成交)"
    r"|(?:允许|授权|批准)\s*(?:执行交易|自动交易|下单)"
    r"|(?:卖出|买入).{0,10}(?:具体行权价|具体履约价)",
    re.IGNORECASE,
)
_PROBABILITY_TEXT_RE = re.compile(
    r"(胜率|概率|后验\s*%|posterior|win\s*rate|收益概率|"
    r"\b\d{1,3}\s*%\s*(?:胜率|概率|后验|posterior))",
    re.IGNORECASE,
)
_FUTURE_PROOF_RE = re.compile(
    r"(未来|后续|30\s*分钟后|60\s*分钟后|之后).{0,18}"
    r"(证明|已经证明|确认本次|保证|必然|一定)",
)


class EvidenceFormatError(ValueError):
    """Recoverable response-shape problem for the runtime retry controller."""


def _resolve_prompt_version(
    payload: dict[str, Any] | None,
    *,
    prompt_version: str | None,
    require_price_bias: bool,
) -> str:
    if prompt_version is not None:
        if prompt_version not in ACCEPTED_PROMPT_VERSIONS:
            raise EvidenceFormatError("invalid prompt_version")
        return prompt_version
    return PROMPT_VERSION


def _is_latest_prompt(prompt_version: str | None) -> bool:
    return prompt_version == PROMPT_VERSION


def _is_latest_review(review: dict[str, Any]) -> bool:
    return _is_latest_prompt(_as_dict(review).get("prompt_version"))


def _review_protocol_for_prompt(prompt_version: str | None) -> str:
    if prompt_version == PROMPT_VERSION:
        return "2.2"
    if prompt_version == PROMPT_VERSION_2_1_0:
        return "2.1"
    if prompt_version in (LEGACY_PROMPT_VERSION, PROMPT_VERSION_2_0_1):
        return "2.0"
    raise EvidenceFormatError("invalid prompt_version")


def _prompt_uses_roles(prompt_version: str | None) -> bool:
    return _review_protocol_for_prompt(prompt_version) in ("2.1", "2.2")


def _prompt_uses_comparison(prompt_version: str | None) -> bool:
    return _prompt_uses_roles(prompt_version)


def _prompt_uses_guidance(prompt_version: str | None) -> bool:
    return _review_protocol_for_prompt(prompt_version) == "2.2"


def _prompt_requires_price_bias(prompt_version: str | None) -> bool:
    return prompt_version in (PROMPT_VERSION_2_0_1, PROMPT_VERSION_2_1_0, PROMPT_VERSION)


def _output_schema_for_prompt(prompt_version: str | None) -> str:
    if prompt_version == PROMPT_VERSION:
        return OUTPUT_SCHEMA_VERSION
    if prompt_version == PROMPT_VERSION_2_1_0:
        return OUTPUT_SCHEMA_VERSION_2_1
    if prompt_version in (LEGACY_PROMPT_VERSION, PROMPT_VERSION_2_0_1):
        return LEGACY_OUTPUT_SCHEMA_VERSION
    raise EvidenceFormatError("invalid prompt_version")


def build_request(packet: dict[str, Any], model: str, recovery: bool = False) -> dict[str, Any]:
    """Build one Chat Completions request for the v2 evidence review."""

    canonical_packet = _canonical_packet(packet)
    high_reasoning = not recovery
    mode_line = (
        "这是一次格式恢复请求：只修复 JSON 结构，不能改变事实、补造引用或追求更高等级。"
        if recovery
        else "这是一次常规单次综合评审。"
    )
    system_prompt = (
        "你是 Astra 信号审计的单次综合评审器。你的目标是帮助交易员理解当前市场"
        "怎样约束价格、价格当前偏多/偏空/中性还是混合、Put/Call 信用价差哪一侧的"
        "信号环境更有依据、是否值得人工研究或准备，以及什么变化会"
        "改变判断。评级 D/C/B/A/S 表示总体证据等级，不表示胜率、收益概率、交易许可或价格"
        "会单向移动的强弱。末日垂直信用价差首先关注空间约束、不利侧侵入、压力与"
        "价格响应的关系，再解释倾向性。Put 侧固定评审下行侵入风险是否受到可解释"
        "约束，Call 侧固定评审上行侵入风险是否受到可解释约束。你可以使用定性"
        "贝叶斯式证据更新来比较支持解释和竞争解释，但"
        "不得输出未经校准的概率、胜率或后验百分比。S 不要求新增来源；它只能表示当前"
        "事实之间的关系让关键替代解释更难成立。B 级可以提出人工研究或准备建议，"
        "A/S 只表示论证更强，不能写成自动执行授权；A 不要求完美承接，也不要求未来"
        "压力持续已经被证明。"
    )
    user_prompt = (
        f"{mode_line}\n\n"
        "只返回一个 JSON 对象，且顶层只能包含 side_evidence_ratings、price_bias、"
        "side_comparison 和 advisory_guidance。两侧必须是 put_credit 和 call_credit。"
        "put_credit 的固定评审对象是下行侵入风险是否受到可解释约束；"
        "call_credit 的固定评审对象是上行侵入风险是否受到可解释约束。"
        "每侧只填写 grade、basis_cn、mechanism、primary_counter_ref、"
        "alternative_cn、next_observation_cn、strengthen_if_cn、weaken_if_cn、"
        "evidence_roles、unresolved_conditions_cn。basis_cn 是等级依据；"
        "mechanism 是对象，只包含 summary_cn 和 refs，用简短语言说明该侧适配机制及成立程度。"
        "primary_counter_ref 必须是该侧 counters_fit 中最主要反证的 ref，若没有主要反证则为 null，"
        "不要另写 market_counter_cn。"
        "职责分工：mechanism 只解释哪项结构、价格响应或传导关系正在限制本侧不利侵入；"
        "basis_cn 只说明为什么落在该等级以及主要薄弱处，不要复述 mechanism；"
        "evidence_roles.claim_cn 要写具体当前事实对本侧适配的作用，反证不要写成不能升级交易认可。"
        "未接入候选报价、执行权限和交易许可这类通用提醒集中放在 advisory_guidance 或 unresolved_conditions_cn，"
        "不要在每段主体里重复。"
        "price_bias 只填写 bias、basis_cn、counter_cn、invalid_if_cn、"
        "evidence_refs、counter_evidence_refs；bias 只能是 BULLISH、BEARISH、"
        "NEUTRAL、MIXED 或 UNDETERMINED。side_comparison 只填写 relative_side、"
        "basis_cn、evidence_refs、flip_if_cn；relative_side 只能是 put_credit、"
        "call_credit、tie 或 not_comparable。advisory_guidance 只填写 summary_cn、"
        "tradeoffs_cn、evidence_refs、outlooks。summary_cn 必须直接说明相对更值得研究的侧别、"
        "关键适配机制和当下主要问题；如果没有相对研究侧，要明确说明为什么无法比较。"
        "tradeoffs_cn 要围绕本卡事实说明空间与补偿的具体取舍，避免重复常识。"
        "outlooks 可给 4 小时近端情景、24 小时背景情景、两项都给或不给；缺少可引用事实时"
        "不要输出泛泛占位情景。每项只包含 horizon_hours、scenario_cn、watch_cn、evidence_refs。\n\n"
        "论证顺序：先说明结构与来源可信度，再说明本侧不利压力，再说明同窗价格响应，"
        "再区分近端窗口与 4h/12h 较长窗口的差异，随后给出竞争解释，最后给出两侧"
        "等级与建议。已发生的反对事实、缺少证据、竞争机制和未来条件要分开表达；"
        "未知不是反对，未来可能变化也不能独自成为限级理由。\n\n"
        "引用规则：evidence_roles 中每项只填写 ref、role、claim_cn；role 只能是 "
        "supports_fit、counters_fit 或 context_only。ref、price_bias 引用、"
        "side_comparison 引用都只能使用输入 facts 中的 id；"
        "事实必须 usable=true 且 observed_at_ms 不晚于 identity.as_of_ms。未知或非投票"
        "事实不是自动反对。Funding 为非计票观察时只能作为背景，不要把温和正负值写成"
        "独立方向支持或反对。本侧不利压力或不利推进必须保留为 counters_fit；若你认为"
        "存在缓冲或承接，只能把相应的结构事实、价格响应事实或传导关系另列为 supports_fit，"
        "不能把不利推进本身直接写成支持。OHLC 代理不能证明路径先后顺序，未来行情不能作为本卡升级"
        "依据。同一事实可以在两侧承担不同角色，但必须说明区别，不能当作多份独立确认。\n\n"
        "建议边界：advisory_guidance 可以说明本卡是否值得研究哪侧、准备时应权衡什么、"
        "以及后续条件如何影响 Put 或 Call 的适配判断；outlooks 每项都要按“基于已有事实的条件"
        "→ 哪一侧受到何种影响 → 需要用什么观察重判”的顺序写。不得预设统一 Delta、行权价、距离、权利金门槛，"
        "不得虚构合约、报价或净补偿。没有报价时可说明补偿尚未评估，也可提醒更远空间"
        "可能牺牲净补偿，但不能声称某个候选值得成交。\n\n"
        "中文输出规则：所有中文字段只能写交易员可读的市场事实、推理和反证；不得写原始"
        "字段路径、内部枚举、schema/hash、公式、权重、执行许可、下单参数、具体行权价、"
        "具体报价、仓位、胜率、概率、后验百分比或未来路径证明。\n\n"
        "等级口径：D=有效事实总体反对该侧适配解释；C=可以判断但无明显支持优势；"
        "B=已有值得关注的支持且竞争解释仍有实质分量；A=整体论证较强，适配解释更有依据；"
        "S=整体支持很强，关键替代解释更难成立；null=必要证据或有效判断缺失。\n\n"
        "价格方向口径：price_bias 是对标的价格倾向的独立结论，必须明确偏多、偏空、"
        "中性、混合或无法判断；它不能替代两侧价差证据等级，也不能改变本地行动边界。"
        "方向判断要先解释空间约束和不利侧推进，再解释倾向性；方向压力强不等于垂直"
        "信用价差更适配。看跌依据不能自动成为 Put 信用价差适配依据；上涨使 Put 下行"
        "风险减弱，也不能被写成 Put 适配失效。上行侵入增强或价格靠近上方 Call 墙，"
        "通常削弱 Call 信用价差适配；除非另有上冲受阻、回落或压力传导弱化等响应证据，"
        "不能因此宣称 Call 相对变优。\n\n"
        "两侧比较口径：side_comparison 只比较两侧当前证据谁更有依据。两侧同为 B 也"
        "可以给出相对侧别；相对有利不等于达到 A，也不影响本地准备状态。任何一侧缺少"
        "有效评级时，relative_side 填 not_comparable。比较不能和字母顺序明显矛盾，"
        "也不能因为一侧无效就宣布另一侧胜出。\n\n"
        "side_comparison.flip_if_cn 必须写明什么适配变化会让另一侧相对变优；不要把"
        "当前优势继续扩大写成比较反转条件。\n\n"
        "字段类型：grade为单个字母或null；说明字段必须是字符串；mechanism 必须是对象，"
        "summary_cn 为字符串，refs 为字符串数组；primary_counter_ref 为字符串或 null；strengthen_if_cn、"
        "weaken_if_cn、evidence_roles、unresolved_conditions_cn必须是数组，没有条目时返回[]。"
        "price_bias 的三个说明字段必须是字符串，两个引用字段必须是字符串数组。"
        "side_comparison 的 evidence_refs 必须是字符串数组。advisory_guidance 的 tradeoffs_cn、"
        "evidence_refs、outlooks 必须是数组。不要将数组合成一段字符串。\n"
        "事实口径：宏观逆风刻度正值为风险资产逆风、负值为顺风；不把负数当下跌方向。"
        "墙位、锚带只是结构参照，距离本身不能证明承接；墙、翻转点与 Pin 不能只因"
        "距离接近就视为等价约束。越过翻转点不单独证明全局净Gamma变号；Gamma 过渡区"
        "也不能仅凭正净Gamma认定抑制波动。传导较弱只能提示可能承接，不能写成已观测"
        "隐藏订单吸收。明确区分成交窗口和价格窗口，终点变化不能证明期间持续单向推进。\n\n"
        "输入事实包：\n"
        f"{json.dumps(canonical_packet, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
    )
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "thinking": {"type": "enabled" if high_reasoning else "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": 32768,
        "_local_prompt_version": PROMPT_VERSION,
        "_local_review_mode": REVIEW_MODE,
        "_local_call_profile": "single_evidence_v2_recovery" if recovery else REVIEW_MODE,
        "_local_packet_hash": _packet_hash(canonical_packet),
        "_local_json_schema": response_schema(),
    }
    if high_reasoning:
        request["reasoning_effort"] = "high"
    return request


def build_review(
    card: dict[str, Any],
    payload: dict[str, Any],
    packet: dict[str, Any],
    model: str = DEFAULT_MODEL,
    reviewed_at: str | None = None,
    *,
    require_price_bias: bool = False,
    prompt_version: str | None = None,
) -> dict[str, Any]:
    """Normalize one model payload into the persisted v2 review object."""

    canonical_packet = _canonical_packet(packet)
    resolved_prompt_version = _resolve_prompt_version(
        payload,
        prompt_version=prompt_version,
        require_price_bias=require_price_bias,
    )
    protocol = _review_protocol_for_prompt(resolved_prompt_version)
    uses_roles = _prompt_uses_roles(resolved_prompt_version)
    uses_comparison = _prompt_uses_comparison(resolved_prompt_version)
    uses_guidance = _prompt_uses_guidance(resolved_prompt_version)
    raw_ratings, raw_price_bias, raw_comparison, raw_guidance = _extract_model_payload(
        payload,
        protocol=protocol,
    )
    fact_index = _fact_index(canonical_packet)
    needs_price_bias = _prompt_requires_price_bias(resolved_prompt_version)
    raw_sides = [raw_ratings.get(key) for key in SIDE_KEYS]
    if _shared_array_shape_error(raw_sides, fact_index, latest=uses_roles):
        # A shared structural output error may recover once. Fabricated refs and
        # side-local semantic errors remain final local validation results.
        raise EvidenceFormatError("side condition fields must be arrays")
    sides = {
        side_key: _normalize_side(
            side_key,
            raw_ratings.get(side_key),
            fact_index=fact_index,
            as_of_ms=_packet_as_of_ms(canonical_packet),
            protocol=protocol,
        )
        for side_key in SIDE_KEYS
    }
    price_bias = (
        _normalize_price_bias(
            raw_price_bias,
            fact_index=fact_index,
            as_of_ms=_packet_as_of_ms(canonical_packet),
        )
        if raw_price_bias is not None
        else _default_price_bias(
            validation_reasons_cn=["本次模型回复缺少价格方向结论，方向复核暂未完成。"]
        )
        if needs_price_bias
        else None
    )
    side_comparison = (
        _normalize_side_comparison(
            raw_comparison,
            sides=sides,
            fact_index=fact_index,
            as_of_ms=_packet_as_of_ms(canonical_packet),
        )
        if uses_comparison
        else _legacy_side_comparison()
    )
    guidance = (
        _normalize_advisory_guidance(
            raw_guidance,
            fact_index=fact_index,
            as_of_ms=_packet_as_of_ms(canonical_packet),
        )
        if uses_guidance
        else None
    )
    action_state = _build_local_action_state(card, sides)
    validation_reasons = [
        reason
        for side in SIDE_KEYS
        for reason in sides[side]["validation_reasons_cn"]
    ]
    if price_bias is not None:
        validation_reasons.extend(price_bias["validation_reasons_cn"])
    if uses_comparison:
        validation_reasons.extend(side_comparison["validation_reasons_cn"])
    if guidance is not None:
        validation_reasons.extend(guidance["validation_reasons_cn"])
    rated_count = sum(sides[key]["status"] == "RATED" for key in SIDE_KEYS)
    status = "OK" if rated_count == 2 else "PARTIAL" if rated_count else "ERROR"
    if price_bias is not None and price_bias["status"] != "ASSESSED" and rated_count:
        status = "PARTIAL"
    if uses_comparison and side_comparison["validation_reasons_cn"] and rated_count:
        status = "PARTIAL"
    if guidance is not None and guidance["status"] != "ASSESSED" and rated_count:
        status = "PARTIAL"
    advisory = {
        "side_evidence_ratings": sides,
        "local_action_state": action_state,
        "source_boundary": _source_boundary(sides, action_state, card=card),
        "action_summary_cn": _action_summary(sides, action_state),
        "market_facts": _clone(canonical_packet["facts"]),
        "quote_boundary_cn": (
            "本层只评估信号环境；候选两腿、报价、净补偿、费用、退出条件与风控"
            "需要在人工交易准备环节另行确认。"
        ),
        "validation": {
            "status": status,
            "validation_reasons_cn": validation_reasons,
            "assessment_hash": None,
        },
    }
    if price_bias is not None:
        advisory["price_bias"] = price_bias
    if uses_comparison:
        advisory["side_comparison"] = side_comparison
    if guidance is not None:
        advisory["advisory_guidance"] = guidance
    advisory["validation"]["assessment_hash"] = _assessment_hash(advisory)
    return {
        "schema_version": _output_schema_for_prompt(resolved_prompt_version),
        "prompt_version": resolved_prompt_version,
        "review_mode": REVIEW_MODE,
        "status": status,
        "model": model,
        "provider": PROVIDER,
        "reviewed_at": reviewed_at or _now_iso(),
        "input_packet_hash": _packet_hash(canonical_packet),
        "evidence_context": _evidence_context(canonical_packet),
        "integrated_trade_advisory": advisory,
    }


def build_error_review(
    card: dict[str, Any],
    packet: dict[str, Any],
    reason_cn: str,
    model: str = DEFAULT_MODEL,
    reviewed_at: str | None = None,
    *,
    require_price_bias: bool = False,
    prompt_version: str | None = None,
) -> dict[str, Any]:
    """Build a fail-closed v2 review while preserving the market fact copy."""

    canonical_packet = _canonical_packet(packet)
    display_reason = "暂未完成有效综合评审，保留本卡市场事实。"
    safe_reason = _clean_text(reason_cn, fallback=display_reason, limit=240)
    resolved_prompt_version = _resolve_prompt_version(
        None,
        prompt_version=prompt_version,
        require_price_bias=require_price_bias,
    )
    protocol = _review_protocol_for_prompt(resolved_prompt_version)
    uses_roles = _prompt_uses_roles(resolved_prompt_version)
    uses_comparison = _prompt_uses_comparison(resolved_prompt_version)
    uses_guidance = _prompt_uses_guidance(resolved_prompt_version)
    needs_price_bias = _prompt_requires_price_bias(resolved_prompt_version)
    sides = {
        side_key: _default_side(
            side_key=side_key,
            protocol=protocol,
            status="UNRATED",
            grade=None,
            basis_cn=display_reason,
            validation_reasons_cn=[safe_reason],
        )
        for side_key in SIDE_KEYS
    }
    action_state = _build_local_action_state(card, sides)
    advisory = {
        "side_evidence_ratings": sides,
        "local_action_state": action_state,
        "source_boundary": _source_boundary(sides, action_state, card=card),
        "action_summary_cn": "本卡暂未完成有效综合评级，先保留市场事实并等待重新评审。",
        "market_facts": _clone(canonical_packet["facts"]),
        "quote_boundary_cn": (
            "本层只评估信号环境；候选两腿、报价、净补偿、费用、退出条件与风控"
            "需要在人工交易准备环节另行确认。"
        ),
        "validation": {
            "status": "ERROR",
            "validation_reasons_cn": [safe_reason],
            "assessment_hash": None,
        },
    }
    if needs_price_bias:
        advisory["price_bias"] = _default_price_bias(validation_reasons_cn=[safe_reason])
    if uses_comparison:
        advisory["side_comparison"] = _default_side_comparison(
            ["本卡未完成有效综合评审，相对比较不可用。"]
        )
    if uses_guidance:
        advisory["advisory_guidance"] = _default_advisory_guidance(
            ["本卡未完成有效综合评审，建议说明暂不可用。"]
        )
    advisory["validation"]["assessment_hash"] = _assessment_hash(advisory)
    return {
        "schema_version": _output_schema_for_prompt(resolved_prompt_version),
        "prompt_version": resolved_prompt_version,
        "review_mode": REVIEW_MODE,
        "status": "ERROR",
        "model": model,
        "provider": PROVIDER,
        "reviewed_at": reviewed_at or _now_iso(),
        "input_packet_hash": _packet_hash(canonical_packet),
        "evidence_context": _evidence_context(canonical_packet),
        "integrated_trade_advisory": advisory,
    }


def build_summary(review: dict[str, Any], card: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the compact materializer summary for a validated v2 review."""

    validation = (
        revalidate_review(card, review)
        if card is not None
        else validate_persisted_review(review)
    )
    advisory = _as_dict(review.get("integrated_trade_advisory"))
    sides = _as_dict(advisory.get("side_evidence_ratings"))
    context = _as_dict(review.get("evidence_context"))
    identity = _as_dict(context.get("identity"))
    action_state = _clone(advisory.get("local_action_state"))
    comparison = _summary_side_comparison(
        advisory.get("side_comparison"),
        has_comparison=_prompt_uses_comparison(_as_dict(review).get("prompt_version")),
    )
    display_action_state = _display_action_state(sides, action_state, card=card)
    source_boundary = _summary_source_boundary(
        advisory.get("source_boundary"),
        sides=sides,
        action_state=action_state,
        display_action_state=display_action_state,
        card=card,
    )
    guidance = (
        _summary_advisory_guidance(advisory.get("advisory_guidance"))
        if _prompt_uses_guidance(_as_dict(review).get("prompt_version"))
        else None
    )
    primary_action_summary = (
        str(_as_dict(guidance).get("summary_cn") or "")
        if guidance is not None and _as_dict(guidance).get("status") == "ASSESSED"
        else str(advisory.get("action_summary_cn") or "")
    )
    review_prompt_version = _as_dict(review).get("prompt_version")
    review_protocol = _review_protocol_for_prompt(review_prompt_version)
    has_native_guidance = guidance is not None
    summary = {
        "schema": SUMMARY_SCHEMA_VERSION,
        "review_schema_version": review.get("schema_version"),
        "review_prompt_version": review_prompt_version,
        "review_protocol": review_protocol,
        "has_advisory_guidance": has_native_guidance,
        "put_credit": _summary_side(sides.get("put_credit")),
        "call_credit": _summary_side(sides.get("call_credit")),
        "local_action_state": action_state,
        "source_boundary": source_boundary,
        "action_summary_cn": primary_action_summary,
        "display_projection_version": DISPLAY_PROJECTION_VERSION,
        "display_action_state": display_action_state,
        "display_action_summary_cn": (
            primary_action_summary
            if guidance is not None and _as_dict(guidance).get("status") == "ASSESSED"
            else _display_action_summary(sides, display_action_state, comparison)
        ),
        "source_record_hash": identity.get("source_record_hash"),
        "market_snapshot": _market_snapshot(advisory.get("market_facts")),
        "side_comparison": comparison,
        "as_of_ms": identity.get("as_of_ms"),
        "input_packet_hash": review.get("input_packet_hash"),
        "assessment_hash": validation["assessment_hash"],
    }
    if "price_bias" in advisory:
        summary["price_bias"] = _summary_price_bias(advisory.get("price_bias"))
    if guidance is not None:
        summary["advisory_guidance"] = guidance
    summary["display_projection_hash"] = _display_projection_hash(summary)
    return summary


def validate_persisted_review(review: dict[str, Any], *, recheck_claims=True) -> dict[str, Any]:
    """Validate a stored v2 review without needing the source card.

    The caller can still perform stronger checks against the source record hash
    and recompute the local action state from the current card.
    """

    if not isinstance(review, dict):
        raise EvidenceFormatError("review must be object")
    if review.get("schema_version") not in ACCEPTED_OUTPUT_SCHEMA_VERSIONS:
        raise EvidenceFormatError("invalid review schema_version")
    prompt_version = review.get("prompt_version")
    if prompt_version not in ACCEPTED_PROMPT_VERSIONS:
        raise EvidenceFormatError("invalid prompt_version")
    if review.get("schema_version") != _output_schema_for_prompt(prompt_version):
        raise EvidenceFormatError("schema_version and prompt_version mismatch")
    if review.get("review_mode") != REVIEW_MODE:
        raise EvidenceFormatError("invalid review_mode")
    if review.get("status") not in REVIEW_STATUS:
        raise EvidenceFormatError("invalid review status")
    context = _as_dict(review.get("evidence_context"))
    advisory = _as_dict(review.get("integrated_trade_advisory"))
    if not context or not advisory:
        raise EvidenceFormatError("review missing evidence_context or advisory")
    if context.get("schema") not in ACCEPTED_PACKET_SCHEMA_VERSIONS:
        raise EvidenceFormatError("invalid evidence packet schema")
    rebuilt_packet = {
        "schema": context.get("schema") or PACKET_SCHEMA_VERSION,
        "identity": _clone(context.get("identity")),
        "facts": _clone(advisory.get("market_facts") or []),
        "limitations_cn": _clone(context.get("limitations_cn") or []),
    }
    _canonical_packet(rebuilt_packet)
    if review.get("input_packet_hash") != _packet_hash(rebuilt_packet):
        raise EvidenceFormatError("input_packet_hash mismatch")
    validation = _as_dict(advisory.get("validation"))
    if validation.get("status") != review.get("status"):
        raise EvidenceFormatError("validation status mismatch")
    assessment_hash = validation.get("assessment_hash")
    if assessment_hash != _assessment_hash(advisory):
        raise EvidenceFormatError("assessment_hash mismatch")
    sides = _as_dict(advisory.get("side_evidence_ratings"))
    if set(sides) != set(SIDE_KEYS):
        raise EvidenceFormatError("side_evidence_ratings shape mismatch")
    fact_index = _fact_index(rebuilt_packet)
    as_of_ms = _packet_as_of_ms(rebuilt_packet)
    protocol = _review_protocol_for_prompt(prompt_version)
    uses_comparison = _prompt_uses_comparison(prompt_version)
    uses_guidance = _prompt_uses_guidance(prompt_version)
    for side_key in SIDE_KEYS:
        side = _as_dict(sides.get(side_key))
        if side.get("status") not in SIDE_STATUS:
            raise EvidenceFormatError(f"{side_key} status invalid")
        grade = side.get("grade")
        if side.get("status") == "RATED" and grade not in GRADES:
            raise EvidenceFormatError(f"{side_key} grade invalid")
        if side.get("status") == "UNRATED" and grade is not None:
            raise EvidenceFormatError(f"{side_key} unrated grade must be null")
        _validate_persisted_side(side_key, side, fact_index, as_of_ms=as_of_ms,
                                 recheck_claims=recheck_claims,
                                 protocol=protocol)
    has_price_bias = "price_bias" in advisory
    if _prompt_requires_price_bias(prompt_version) and not has_price_bias:
        raise EvidenceFormatError("price_bias missing for prompt")
    if has_price_bias:
        price_bias = _as_dict(advisory.get("price_bias"))
        _validate_persisted_price_bias(
            price_bias,
            fact_index,
            as_of_ms=as_of_ms,
            recheck_claims=recheck_claims,
        )
        if price_bias.get("status") != "ASSESSED" and review.get("status") == "OK":
            raise EvidenceFormatError("unavailable price_bias cannot be OK")
    if uses_comparison:
        comparison = _as_dict(advisory.get("side_comparison"))
        _validate_persisted_side_comparison(
            comparison,
            sides=sides,
            fact_index=fact_index,
            as_of_ms=as_of_ms,
            recheck_claims=recheck_claims,
        )
        if comparison.get("validation_reasons_cn") and review.get("status") == "OK":
            raise EvidenceFormatError("invalid side_comparison cannot be OK")
    elif "side_comparison" in advisory:
        raise EvidenceFormatError("legacy review must not contain side_comparison")
    if uses_guidance:
        guidance = _as_dict(advisory.get("advisory_guidance"))
        _validate_persisted_advisory_guidance(
            guidance,
            fact_index,
            as_of_ms=as_of_ms,
            recheck_claims=recheck_claims,
        )
        if guidance.get("status") != "ASSESSED" and review.get("status") == "OK":
            raise EvidenceFormatError("unavailable advisory guidance cannot be OK")
        _validate_source_boundary(_as_dict(advisory.get("source_boundary")))
    elif "advisory_guidance" in advisory:
        raise EvidenceFormatError("legacy review must not contain advisory_guidance")
    return {
        "ok": True,
        "assessment_hash": assessment_hash,
        "input_packet_hash": review.get("input_packet_hash"),
    }


def revalidate_review(card: dict[str, Any], review: dict[str, Any], *, recheck_claims=True) -> dict[str, Any]:
    """Validate persisted review against the current source card identity."""

    result = validate_persisted_review(review, recheck_claims=recheck_claims)
    context_identity = _as_dict(_as_dict(review.get("evidence_context")).get("identity"))
    card_identity = _as_dict(_as_dict(card).get("identity"))
    if context_identity.get("card_id") != card_identity.get("card_id"):
        raise EvidenceFormatError("card_id mismatch")
    for key in ("symbol", "strategy_version"):
        if context_identity.get(key) != card_identity.get(key):
            raise EvidenceFormatError(f"{key} mismatch")
    card_as_of_ms = (
        card_identity.get("as_of_ms")
        if card_identity.get("as_of_ms") is not None
        else card_identity.get("confirmed_time_ms")
    )
    if context_identity.get("as_of_ms") != card_as_of_ms:
        raise EvidenceFormatError("as_of_ms mismatch")
    review_hash = context_identity.get("source_record_hash")
    card_hash = _source_record_hash(card)
    if bool(review_hash) != bool(card_hash):
        raise EvidenceFormatError("source_record_hash presence mismatch")
    if review_hash and review_hash != card_hash:
        raise EvidenceFormatError("source_record_hash mismatch")
    advisory = _as_dict(review.get("integrated_trade_advisory"))
    sides = _as_dict(advisory.get("side_evidence_ratings"))
    expected_action = _build_local_action_state(card, sides)
    if advisory.get("local_action_state") != expected_action:
        raise EvidenceFormatError("local_action_state mismatch")
    if _prompt_uses_guidance(_as_dict(review).get("prompt_version")):
        expected_boundary = _source_boundary(sides, expected_action, card=card)
        if advisory.get("source_boundary") != expected_boundary:
            raise EvidenceFormatError("source_boundary mismatch")
    result["source_identity_ok"] = True
    result["local_action_state_ok"] = True
    return result


def supported_review_protocol(review: dict[str, Any]) -> str | None:
    """Return the supported review protocol family, or None for unknown input."""
    try:
        schema_version = _as_dict(review).get("schema_version")
        prompt_version = _as_dict(review).get("prompt_version")
        if schema_version not in ACCEPTED_OUTPUT_SCHEMA_VERSIONS:
            return None
        if prompt_version not in ACCEPTED_PROMPT_VERSIONS:
            return None
        if schema_version != _output_schema_for_prompt(prompt_version):
            return None
        return _review_protocol_for_prompt(prompt_version)
    except Exception:
        return None


def is_supported_review_protocol(review: dict[str, Any]) -> bool:
    return supported_review_protocol(review) is not None


def model_side_fields_for_review(review: dict[str, Any]) -> list[str]:
    """Return the model-authored side fields for a persisted review protocol."""

    protocol = supported_review_protocol(review)
    if protocol == "2.2":
        return sorted(MODEL_SIDE_FIELDS)
    if protocol == "2.1":
        return sorted(MODEL_SIDE_FIELDS_V21)
    if protocol == "2.0":
        return sorted(LEGACY_MODEL_SIDE_FIELDS)
    return []


def model_payload_from_review(review: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the model-authored payload shape from a persisted review."""

    protocol = supported_review_protocol(review)
    if protocol is None:
        raise EvidenceFormatError("unsupported review protocol")
    advisory = _as_dict(_as_dict(review).get("integrated_trade_advisory"))
    sides = _as_dict(advisory.get("side_evidence_ratings"))
    side_fields = set(model_side_fields_for_review(review))
    payload: dict[str, Any] = {"side_evidence_ratings": {}}
    for side_key in SIDE_KEYS:
        side = _as_dict(sides.get(side_key))
        payload["side_evidence_ratings"][side_key] = {}
        for field in sorted(side_fields):
            payload["side_evidence_ratings"][side_key][field] = _clone(side.get(field))
    if "price_bias" in advisory:
        price_bias = _as_dict(advisory.get("price_bias"))
        payload["price_bias"] = {}
        for field in sorted(MODEL_PRICE_BIAS_FIELDS):
            payload["price_bias"][field] = _clone(price_bias.get(field))
    if protocol in ("2.1", "2.2"):
        comparison = _as_dict(advisory.get("side_comparison"))
        payload["side_comparison"] = {}
        for field in sorted(MODEL_COMPARISON_FIELDS):
            payload["side_comparison"][field] = _clone(comparison.get(field))
    if protocol == "2.2":
        guidance = _as_dict(advisory.get("advisory_guidance"))
        payload["advisory_guidance"] = {}
        for field in sorted(MODEL_ADVISORY_GUIDANCE_FIELDS):
            payload["advisory_guidance"][field] = _clone(guidance.get(field))
    return payload


def response_schema() -> dict[str, Any]:
    role_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(MODEL_SIDE_ROLE_FIELDS),
        "properties": {
            "ref": {"type": "string"},
            "role": {"enum": list(EVIDENCE_ROLES)},
            "claim_cn": {"type": "string"},
        },
    }
    mechanism_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(MODEL_SIDE_MECHANISM_FIELDS),
        "properties": {
            "summary_cn": {"type": "string"},
            "refs": {"type": "array", "items": {"type": "string"}},
        },
    }
    side_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(MODEL_SIDE_FIELDS),
        "properties": {
            "grade": {"enum": ["D", "C", "B", "A", "S", None]},
            "basis_cn": {"type": "string"},
            "mechanism": mechanism_schema,
            "primary_counter_ref": {"type": ["string", "null"]},
            "alternative_cn": {"type": "string"},
            "next_observation_cn": {"type": "string"},
            "strengthen_if_cn": {"type": "array", "items": {"type": "string"}},
            "weaken_if_cn": {"type": "array", "items": {"type": "string"}},
            "evidence_roles": {"type": "array", "items": role_schema},
            "unresolved_conditions_cn": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }
    price_bias_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(MODEL_PRICE_BIAS_FIELDS),
        "properties": {
            "bias": {"enum": list(PRICE_BIASES)},
            "basis_cn": {"type": "string"},
            "counter_cn": {"type": "string"},
            "invalid_if_cn": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "counter_evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
    }
    comparison_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(MODEL_COMPARISON_FIELDS),
        "properties": {
            "relative_side": {"enum": list(COMPARISON_SIDES)},
            "basis_cn": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "flip_if_cn": {"type": "string"},
        },
    }
    outlook_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(MODEL_GUIDANCE_OUTLOOK_FIELDS),
        "properties": {
            "horizon_hours": {"enum": [4, 24]},
            "scenario_cn": {"type": "string"},
            "watch_cn": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
    }
    guidance_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(MODEL_ADVISORY_GUIDANCE_FIELDS),
        "properties": {
            "summary_cn": {"type": "string"},
            "tradeoffs_cn": {"type": "array", "items": {"type": "string"}},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "outlooks": {"type": "array", "items": outlook_schema},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["side_evidence_ratings", "price_bias", "side_comparison", "advisory_guidance"],
        "properties": {
            "side_evidence_ratings": {
                "type": "object",
                "additionalProperties": False,
                "required": list(SIDE_KEYS),
                "properties": {side_key: side_schema for side_key in SIDE_KEYS},
            },
            "price_bias": price_bias_schema,
            "side_comparison": comparison_schema,
            "advisory_guidance": guidance_schema,
        },
    }


def _extract_model_payload(
    payload: dict[str, Any],
    *,
    protocol: str,
) -> tuple[dict[str, Any], Any, Any, Any]:
    if not isinstance(payload, dict):
        raise EvidenceFormatError("model payload must be object")
    allowed = {"side_evidence_ratings", "price_bias"}
    if protocol in ("2.1", "2.2"):
        allowed.add("side_comparison")
    if protocol == "2.2":
        allowed.add("advisory_guidance")
    if not set(payload).issubset(allowed):
        raise EvidenceFormatError("model payload top-level shape invalid")
    ratings = payload.get("side_evidence_ratings")
    if not isinstance(ratings, dict):
        raise EvidenceFormatError("side_evidence_ratings must be object")
    if not any(side in ratings for side in SIDE_KEYS):
        raise EvidenceFormatError("side_evidence_ratings has no known sides")
    return (
        ratings,
        payload.get("price_bias"),
        payload.get("side_comparison"),
        payload.get("advisory_guidance"),
    )


def _shared_array_shape_error(
    raw_sides: list[Any],
    fact_index: dict[str, dict[str, Any]],
    *,
    latest: bool,
) -> bool:
    if not all(isinstance(side, dict) for side in raw_sides):
        return False
    array_fields = (
        ("strengthen_if_cn", "weaken_if_cn", "evidence_roles", "unresolved_conditions_cn")
        if latest
        else ("unresolved_conditions_cn",)
    )
    if not all(any(field in side and not isinstance(side.get(field), list)
                   for field in array_fields) for side in raw_sides):
        return False
    if latest:
        refs = [
            role.get("ref")
            for side in raw_sides
            for role in (side.get("evidence_roles") if isinstance(side.get("evidence_roles"), list) else [])
            if isinstance(role, dict)
        ]
    else:
        refs = [
            ref
            for side in raw_sides
            for field in ("evidence_refs", "counter_evidence_refs")
            for ref in (side.get(field) if isinstance(side.get(field), list) else [])
        ]
    return all(isinstance(ref, str) and ref in fact_index for ref in refs)


def _normalize_side(
    side_key: str,
    raw_side: Any,
    *,
    fact_index: dict[str, dict[str, Any]],
    as_of_ms: int | float,
    protocol: str = "2.0",
) -> dict[str, Any]:
    if protocol == "2.2":
        return _normalize_v22_side(
            side_key,
            raw_side,
            fact_index=fact_index,
            as_of_ms=as_of_ms,
        )
    if protocol == "2.1":
        return _normalize_v21_side(
            side_key,
            raw_side,
            fact_index=fact_index,
            as_of_ms=as_of_ms,
        )
    return _normalize_legacy_side(
        side_key,
        raw_side,
        fact_index=fact_index,
        as_of_ms=as_of_ms,
    )


def _normalize_legacy_side(
    side_key: str,
    raw_side: Any,
    *,
    fact_index: dict[str, dict[str, Any]],
    as_of_ms: int | float,
) -> dict[str, Any]:
    if not isinstance(raw_side, dict):
        return _default_side(
            validation_reasons_cn=[_side_label(side_key) + "缺少有效评级对象。"]
        )

    reasons: list[str] = []
    if set(raw_side) != LEGACY_MODEL_SIDE_FIELDS:
        reasons.append(_side_label(side_key) + "评级说明结构尚不完整。")

    grade = raw_side.get("grade")
    if grade is not None and grade not in GRADES:
        reasons.append(_side_label(side_key) + "等级不是 D/C/B/A/S 或未评级。")
        grade = None

    text_fields = {
        "basis_cn": _clean_text(raw_side.get("basis_cn"), fallback="本侧说明不足。"),
        "market_counter_cn": _clean_text(
            raw_side.get("market_counter_cn"), fallback="主要反证暂未形成有效说明。"
        ),
        "alternative_cn": _clean_text(
            raw_side.get("alternative_cn"), fallback="竞争解释暂未形成有效说明。"
        ),
        "next_observation_cn": _clean_text(
            raw_side.get("next_observation_cn"), fallback="继续观察关键市场事实变化。"
        ),
        "invalid_if_cn": _clean_text(
            raw_side.get("invalid_if_cn"), fallback="关键事实失效时需要重新评级。"
        ),
    }

    for field_name, text in text_fields.items():
        if not isinstance(raw_side.get(field_name), str) or not raw_side[field_name].strip():
            reasons.append(_side_label(side_key) + "缺少必要的可读说明。")
        issue = _human_text_issue(text)
        if issue:
            reasons.append(_side_label(side_key) + issue)
            text_fields[field_name] = "该项说明含有不可展示内容，暂不作为有效评级依据。"
    reasons.extend(_side_label(side_key) + reason for reason in
                   _fact_assertion_issues(text_fields, fact_index))

    unresolved, unresolved_ok = _clean_text_list(
        raw_side.get("unresolved_conditions_cn"), limit=8, fallback_item=""
    )
    if not unresolved_ok:
        reasons.append(_side_label(side_key) + "未解条件不是可读列表。")
    safe_unresolved = []
    for item in unresolved:
        issue = _human_text_issue(item)
        if issue:
            reasons.append(_side_label(side_key) + issue)
        else:
            safe_unresolved.append(item)

    evidence_refs, evidence_ok, evidence_reasons = _valid_refs(
        raw_side.get("evidence_refs"), fact_index, as_of_ms=as_of_ms
    )
    counter_refs, counter_ok, counter_reasons = _valid_refs(
        raw_side.get("counter_evidence_refs"), fact_index, as_of_ms=as_of_ms
    )
    if not evidence_ok:
        reasons.append(_side_label(side_key) + "支持引用不是可读列表。")
    if not counter_ok:
        reasons.append(_side_label(side_key) + "反证引用不是可读列表。")
    reasons.extend(_side_label(side_key) + reason for reason in evidence_reasons)
    reasons.extend(_side_label(side_key) + reason for reason in counter_reasons)

    if grade is not None and not evidence_refs and not counter_refs:
        reasons.append(_side_label(side_key) + "缺少可核验事实引用。")
    if grade in ("B", "A", "S") and not evidence_refs:
        reasons.append(_side_label(side_key) + "支持等级缺少可核验支持事实。")
    if grade in ("A", "S") and not _has_constraint_argument(
        text_fields, evidence_refs, counter_refs, fact_index
    ):
        reasons.append(_side_label(side_key) + "A/S 不能只由方向动量构成，缺少空间约束或压力响应论证。")

    status = "RATED" if grade is not None and not reasons else "UNRATED"
    return {
        "status": status,
        "grade": grade if status == "RATED" else None,
        "basis_cn": text_fields["basis_cn"],
        "market_counter_cn": text_fields["market_counter_cn"],
        "alternative_cn": text_fields["alternative_cn"],
        "next_observation_cn": text_fields["next_observation_cn"],
        "invalid_if_cn": text_fields["invalid_if_cn"],
        "evidence_refs": evidence_refs,
        "counter_evidence_refs": counter_refs,
        "unresolved_conditions_cn": safe_unresolved,
        "validation_reasons_cn": reasons,
    }


def _normalize_v21_side(
    side_key: str,
    raw_side: Any,
    *,
    fact_index: dict[str, dict[str, Any]],
    as_of_ms: int | float,
) -> dict[str, Any]:
    if not isinstance(raw_side, dict):
        return _default_side(
            side_key=side_key,
            protocol="2.1",
            validation_reasons_cn=[_side_label(side_key) + "缺少有效评级对象。"],
        )

    reasons: list[str] = []
    if set(raw_side) != MODEL_SIDE_FIELDS_V21:
        reasons.append(_side_label(side_key) + "评级说明结构尚不完整。")

    grade = raw_side.get("grade")
    if grade is not None and grade not in GRADES:
        reasons.append(_side_label(side_key) + "等级不是 D/C/B/A/S 或未评级。")
        grade = None

    text_fields = {
        "basis_cn": _clean_text(raw_side.get("basis_cn"), fallback="本侧等级依据不足。"),
        "mechanism_cn": _clean_text(raw_side.get("mechanism_cn"), fallback="本侧适配机制暂未形成有效说明。"),
        "market_counter_cn": _clean_text(
            raw_side.get("market_counter_cn"), fallback="主要反证暂未形成有效说明。"
        ),
        "alternative_cn": _clean_text(
            raw_side.get("alternative_cn"), fallback="竞争解释暂未形成有效说明。"
        ),
        "next_observation_cn": _clean_text(
            raw_side.get("next_observation_cn"), fallback="继续观察关键市场事实变化。"
        ),
    }
    for field_name, text in text_fields.items():
        if not isinstance(raw_side.get(field_name), str) or not raw_side[field_name].strip():
            reasons.append(_side_label(side_key) + "缺少必要的可读说明。")
        issue = _v21_human_text_issue(text)
        if issue:
            reasons.append(_side_label(side_key) + issue)
            text_fields[field_name] = "该项说明含有不可展示内容，暂不作为有效评级依据。"
    reasons.extend(_side_label(side_key) + reason for reason in
                   _fact_assertion_issues(text_fields, fact_index))

    strengthen, strengthen_ok = _clean_human_text_list(
        raw_side.get("strengthen_if_cn"),
        limit=6,
        fallback_item="补齐关键观察后重新判断。",
    )
    if not strengthen_ok:
        reasons.append(_side_label(side_key) + "增强条件不是可读列表。")
    weaken, weaken_ok = _clean_human_text_list(
        raw_side.get("weaken_if_cn"),
        limit=6,
        fallback_item="关键事实转弱时需要重新评级。",
    )
    if not weaken_ok:
        reasons.append(_side_label(side_key) + "削弱条件不是可读列表。")
    unresolved, unresolved_ok = _clean_human_text_list(
        raw_side.get("unresolved_conditions_cn"),
        limit=8,
        fallback_item="仍有未解条件需要观察。",
    )
    if not unresolved_ok:
        reasons.append(_side_label(side_key) + "未解条件不是可读列表。")

    roles, role_refs, role_reasons = _normalize_evidence_roles(
        side_key,
        raw_side.get("evidence_roles"),
        fact_index=fact_index,
        as_of_ms=as_of_ms,
    )
    reasons.extend(role_reasons)
    evidence_refs = role_refs["supports_fit"]
    counter_refs = role_refs["counters_fit"]
    context_refs = role_refs["context_only"]

    if grade is not None and not roles:
        reasons.append(_side_label(side_key) + "缺少可核验事实引用。")
    if grade in ("B", "A", "S") and not evidence_refs:
        reasons.append(_side_label(side_key) + "支持等级缺少可核验支持事实。")
    if grade in ("A", "S") and not _has_constraint_argument(
        text_fields, evidence_refs, counter_refs, fact_index
    ):
        reasons.append(_side_label(side_key) + "A/S 不能只由方向动量构成，缺少空间约束或压力响应论证。")

    status = "RATED" if grade is not None and not reasons else "UNRATED"
    return {
        "status": status,
        "grade": grade if status == "RATED" else None,
        "fit_thesis": SIDE_FIT_THESES[side_key],
        "basis_cn": text_fields["basis_cn"],
        "mechanism_cn": text_fields["mechanism_cn"],
        "market_counter_cn": text_fields["market_counter_cn"],
        "alternative_cn": text_fields["alternative_cn"],
        "next_observation_cn": text_fields["next_observation_cn"],
        "strengthen_if_cn": strengthen,
        "weaken_if_cn": weaken,
        "evidence_roles": roles if status == "RATED" else [],
        "evidence_refs": evidence_refs if status == "RATED" else [],
        "counter_evidence_refs": counter_refs if status == "RATED" else [],
        "context_evidence_refs": context_refs if status == "RATED" else [],
        "unresolved_conditions_cn": unresolved,
        "validation_reasons_cn": reasons,
    }


def _normalize_v22_side(
    side_key: str,
    raw_side: Any,
    *,
    fact_index: dict[str, dict[str, Any]],
    as_of_ms: int | float,
) -> dict[str, Any]:
    if not isinstance(raw_side, dict):
        return _default_side(
            side_key=side_key,
            protocol="2.2",
            validation_reasons_cn=[_side_label(side_key) + "缺少有效评级对象。"],
        )

    reasons: list[str] = []
    if set(raw_side) != MODEL_SIDE_FIELDS:
        reasons.append(_side_label(side_key) + "评级说明结构尚不完整。")

    grade = raw_side.get("grade")
    if grade is not None and grade not in GRADES:
        reasons.append(_side_label(side_key) + "等级不是 D/C/B/A/S 或未评级。")
        grade = None

    text_fields = {
        "basis_cn": _clean_text(raw_side.get("basis_cn"), fallback="本侧等级依据不足。"),
        "alternative_cn": _clean_text(
            raw_side.get("alternative_cn"), fallback="竞争解释暂未形成有效说明。"
        ),
        "next_observation_cn": _clean_text(
            raw_side.get("next_observation_cn"), fallback="继续观察关键市场事实变化。"
        ),
    }
    for field_name, text in text_fields.items():
        if not isinstance(raw_side.get(field_name), str) or not raw_side[field_name].strip():
            reasons.append(_side_label(side_key) + "缺少必要的可读说明。")
        issue = _v21_human_text_issue(text)
        if issue:
            reasons.append(_side_label(side_key) + issue)
            text_fields[field_name] = "该项说明含有不可展示内容，暂不作为有效评级依据。"
    reasons.extend(_side_label(side_key) + reason for reason in
                   _fact_assertion_issues(text_fields, fact_index))

    mechanism, mechanism_ref_reasons = _normalize_mechanism(
        raw_side.get("mechanism"),
        fact_index=fact_index,
        as_of_ms=as_of_ms,
    )
    reasons.extend(_side_label(side_key) + reason for reason in mechanism_ref_reasons)

    strengthen, strengthen_ok = _clean_human_text_list(
        raw_side.get("strengthen_if_cn"),
        limit=6,
        fallback_item="补齐关键观察后重新判断。",
    )
    if not strengthen_ok:
        reasons.append(_side_label(side_key) + "增强条件不是可读列表。")
    weaken, weaken_ok = _clean_human_text_list(
        raw_side.get("weaken_if_cn"),
        limit=6,
        fallback_item="关键事实转弱时需要重新评级。",
    )
    if not weaken_ok:
        reasons.append(_side_label(side_key) + "削弱条件不是可读列表。")
    unresolved, unresolved_ok = _clean_human_text_list(
        raw_side.get("unresolved_conditions_cn"),
        limit=8,
        fallback_item="仍有未解条件需要观察。",
    )
    if not unresolved_ok:
        reasons.append(_side_label(side_key) + "未解条件不是可读列表。")

    roles, role_refs, role_reasons = _normalize_evidence_roles(
        side_key,
        raw_side.get("evidence_roles"),
        fact_index=fact_index,
        as_of_ms=as_of_ms,
    )
    reasons.extend(role_reasons)
    evidence_refs = role_refs["supports_fit"]
    counter_refs = role_refs["counters_fit"]
    context_refs = role_refs["context_only"]

    primary_counter_ref, primary_counter_reason = _normalize_primary_counter_ref(
        raw_side.get("primary_counter_ref"),
        counter_refs,
    )
    if primary_counter_reason:
        reasons.append(_side_label(side_key) + primary_counter_reason)
    market_counter_cn = _market_counter_from_roles(roles, primary_counter_ref)

    if grade is not None and not roles:
        reasons.append(_side_label(side_key) + "缺少可核验事实引用。")
    if grade in ("B", "A", "S") and not evidence_refs:
        reasons.append(_side_label(side_key) + "支持等级缺少可核验支持事实。")
    if grade in ("A", "S") and not _has_v22_mechanism_argument(
        mechanism,
        evidence_refs,
        fact_index,
    ):
        reasons.append(_side_label(side_key) + "A/S 需要机制引用落在支持角色中的结构或响应事实上。")

    status = "RATED" if grade is not None and not reasons else "UNRATED"
    if status != "RATED":
        roles = []
        evidence_refs = []
        counter_refs = []
        context_refs = []
        primary_counter_ref = None
        market_counter_cn = "主要反证暂未形成有效说明。"
    return {
        "status": status,
        "grade": grade if status == "RATED" else None,
        "fit_thesis": SIDE_FIT_THESES[side_key],
        "basis_cn": text_fields["basis_cn"],
        "mechanism": mechanism,
        "primary_counter_ref": primary_counter_ref,
        "market_counter_cn": market_counter_cn,
        "alternative_cn": text_fields["alternative_cn"],
        "next_observation_cn": text_fields["next_observation_cn"],
        "strengthen_if_cn": strengthen,
        "weaken_if_cn": weaken,
        "evidence_roles": roles,
        "evidence_refs": evidence_refs,
        "counter_evidence_refs": counter_refs,
        "context_evidence_refs": context_refs,
        "unresolved_conditions_cn": unresolved,
        "validation_reasons_cn": reasons,
    }


def _normalize_mechanism(
    raw_mechanism: Any,
    *,
    fact_index: dict[str, dict[str, Any]],
    as_of_ms: int | float,
) -> tuple[dict[str, Any], list[str]]:
    reasons: list[str] = []
    if not isinstance(raw_mechanism, dict):
        return {
            "summary_cn": "本侧适配机制暂未形成有效说明。",
            "refs": [],
        }, ["适配机制不是可读对象。"]
    if set(raw_mechanism) != MODEL_SIDE_MECHANISM_FIELDS:
        reasons.append("适配机制结构尚不完整。")
    summary = _clean_text(
        raw_mechanism.get("summary_cn"),
        fallback="本侧适配机制暂未形成有效说明。",
    )
    if not isinstance(raw_mechanism.get("summary_cn"), str) or not raw_mechanism["summary_cn"].strip():
        reasons.append("适配机制缺少可读说明。")
    issue = _v21_human_text_issue(summary)
    if issue:
        reasons.append(issue)
        summary = "该项说明含有不可展示内容，暂不作为有效机制依据。"
    refs, refs_ok, ref_reasons = _valid_refs(
        raw_mechanism.get("refs"), fact_index, as_of_ms=as_of_ms
    )
    if not refs_ok:
        reasons.append("适配机制引用不是可读列表。")
    reasons.extend(ref_reasons)
    reasons.extend(_fact_assertion_issues({"summary_cn": summary}, fact_index))
    return {"summary_cn": summary, "refs": refs}, reasons


def _normalize_primary_counter_ref(
    raw_ref: Any,
    counter_refs: list[str],
) -> tuple[str | None, str]:
    if raw_ref is None:
        return None, ""
    if not isinstance(raw_ref, str) or not raw_ref.strip():
        return None, "主要反证引用必须为空或可读事实引用。"
    ref = raw_ref.strip()
    if ref not in counter_refs:
        return None, "主要反证引用必须来自该侧反证角色。"
    return ref, ""


def _market_counter_from_roles(
    roles: list[dict[str, str]],
    primary_counter_ref: str | None,
) -> str:
    if primary_counter_ref is None:
        return "主要反证暂未形成有效说明。"
    for role in roles:
        if role.get("role") == "counters_fit" and role.get("ref") == primary_counter_ref:
            claim = _clean_text(role.get("claim_cn"), fallback="")
            if claim and not _v21_human_text_issue(claim):
                return claim
    return "主要反证暂未形成有效说明。"


def _has_v22_mechanism_argument(
    mechanism: dict[str, Any],
    evidence_refs: list[str],
    fact_index: dict[str, dict[str, Any]],
) -> bool:
    support_refs = set(evidence_refs)
    mechanism_refs = mechanism.get("refs") if isinstance(mechanism, dict) else None
    if not isinstance(mechanism_refs, list) or not mechanism_refs:
        return False
    return any(
        ref in support_refs and _fact_can_explain_fit(fact_index.get(ref) or {})
        for ref in mechanism_refs
    )


def _fact_can_explain_fit(fact: dict[str, Any]) -> bool:
    topic = str(fact.get("topic") or "").lower()
    fact_id = str(fact.get("id") or "").lower()
    source_group = str(fact.get("source_group") or "").lower()
    topic_tokens = (
        "structure",
        "spatial",
        "position",
        "anchor",
        "wall",
        "gex",
        "gamma",
        "response",
        "pressure",
        "near_term",
    )
    id_prefixes = (
        "structure.",
        "side.",
        "pressure.",
        "market.",
        "near_term.",
        "options.",
    )
    source_tokens = ("gex", "ggr", "option", "gamma", "price", "flow")
    return (
        any(token in topic for token in topic_tokens)
        or any(fact_id.startswith(prefix) for prefix in id_prefixes)
        or any(token in source_group for token in source_tokens)
    )


def _normalize_price_bias(
    raw_bias: Any,
    *,
    fact_index: dict[str, dict[str, Any]],
    as_of_ms: int | float,
) -> dict[str, Any]:
    if not isinstance(raw_bias, dict):
        return _default_price_bias(
            validation_reasons_cn=["价格方向缺少有效复核对象。"]
        )

    reasons: list[str] = []
    if set(raw_bias) != MODEL_PRICE_BIAS_FIELDS:
        reasons.append("价格方向说明结构尚不完整。")

    bias = str(raw_bias.get("bias") or "").upper()
    if bias not in PRICE_BIASES:
        reasons.append("价格方向不是偏多、偏空、中性、混合或无法判断。")
        bias = "UNDETERMINED"

    text_fields = {
        "basis_cn": _clean_text(raw_bias.get("basis_cn"), fallback="价格方向依据暂未形成有效说明。"),
        "counter_cn": _clean_text(raw_bias.get("counter_cn"), fallback="价格方向反证暂未形成有效说明。"),
        "invalid_if_cn": _clean_text(raw_bias.get("invalid_if_cn"), fallback="关键事实变化时需要重新判断价格方向。"),
    }
    for field_name, text in text_fields.items():
        if not isinstance(raw_bias.get(field_name), str) or not raw_bias[field_name].strip():
            reasons.append("价格方向缺少必要的可读说明。")
        issue = _human_text_issue(text)
        if issue:
            reasons.append("价格方向" + issue)
            text_fields[field_name] = "该项说明含有不可展示内容，暂不作为有效方向依据。"
    reasons.extend("价格方向" + reason for reason in _fact_assertion_issues(text_fields, fact_index))

    evidence_refs, evidence_ok, evidence_reasons = _valid_refs(
        raw_bias.get("evidence_refs"), fact_index, as_of_ms=as_of_ms
    )
    counter_refs, counter_ok, counter_reasons = _valid_refs(
        raw_bias.get("counter_evidence_refs"), fact_index, as_of_ms=as_of_ms
    )
    if not evidence_ok:
        reasons.append("价格方向支持引用不是可读列表。")
    if not counter_ok:
        reasons.append("价格方向反证引用不是可读列表。")
    reasons.extend("价格方向" + reason for reason in evidence_reasons)
    reasons.extend("价格方向" + reason for reason in counter_reasons)

    if bias != "UNDETERMINED" and not evidence_refs and not counter_refs:
        reasons.append("价格方向缺少可核验事实引用。")
    if bias != "UNDETERMINED" and not _has_price_bias_signal_argument(
        text_fields, evidence_refs, counter_refs, fact_index
    ):
        reasons.append("价格方向不能只由墙位、距离或净Gamma符号构成，缺少压力、响应或方向背景依据。")

    status = "ASSESSED" if not reasons else "UNAVAILABLE"
    return {
        "schema": PRICE_BIAS_SCHEMA_VERSION,
        "status": status,
        "bias": bias if status == "ASSESSED" else "UNDETERMINED",
        "basis_cn": text_fields["basis_cn"],
        "counter_cn": text_fields["counter_cn"],
        "invalid_if_cn": text_fields["invalid_if_cn"],
        "evidence_refs": evidence_refs,
        "counter_evidence_refs": counter_refs,
        "validation_reasons_cn": reasons,
    }


def _normalize_side_comparison(
    raw_comparison: Any,
    *,
    sides: dict[str, dict[str, Any]],
    fact_index: dict[str, dict[str, Any]],
    as_of_ms: int | float,
) -> dict[str, Any]:
    if not isinstance(raw_comparison, dict):
        return _default_side_comparison(["两侧比较缺少有效对象，相对比较未采纳。"])

    reasons: list[str] = []
    if set(raw_comparison) != MODEL_COMPARISON_FIELDS:
        reasons.append("两侧比较结构尚不完整。")

    relative_side = str(raw_comparison.get("relative_side") or "").strip()
    if relative_side not in COMPARISON_SIDES:
        reasons.append("两侧比较结果不是可读侧别。")
        relative_side = "not_comparable"
    basis_cn = _clean_text(raw_comparison.get("basis_cn"), fallback="相对比较暂未形成有效说明。")
    flip_if_cn = _clean_text(raw_comparison.get("flip_if_cn"), fallback="补齐有效比较后重新判断。")
    for raw_value, text in (
        (raw_comparison.get("basis_cn"), basis_cn),
        (raw_comparison.get("flip_if_cn"), flip_if_cn),
    ):
        if not isinstance(raw_value, str) or not raw_value.strip():
            reasons.append("两侧比较缺少必要的可读说明。")
        issue = _v21_human_text_issue(text)
        if issue:
            reasons.append("两侧比较" + issue)
            basis_cn = "相对比较说明含有不可展示内容，暂未采纳。"
            flip_if_cn = "补齐有效比较后重新判断。"
            break

    reasons.extend(
        "两侧比较" + reason
        for reason in _fact_assertion_issues(
            {"basis_cn": basis_cn, "flip_if_cn": flip_if_cn},
            fact_index,
        )
    )

    evidence_refs, refs_ok, ref_reasons = _valid_refs(
        raw_comparison.get("evidence_refs"), fact_index, as_of_ms=as_of_ms
    )
    if not refs_ok:
        reasons.append("两侧比较引用不是可读列表。")
    reasons.extend("两侧比较" + reason for reason in ref_reasons)

    if relative_side != "not_comparable" and not evidence_refs:
        reasons.append("两侧比较缺少可核验事实引用。")

    side_statuses = {key: _as_dict(sides.get(key)).get("status") for key in SIDE_KEYS}
    if any(status != "RATED" for status in side_statuses.values()):
        if relative_side != "not_comparable":
            reasons.append("存在未评级侧，不能据此宣布另一侧更有依据。")
        relative_side = "not_comparable"

    order_issue = _comparison_order_issue(relative_side, sides)
    if order_issue:
        reasons.append(order_issue)
        relative_side = "not_comparable"

    status = (
        "ASSESSED"
        if relative_side in ("put_credit", "call_credit", "tie") and not reasons
        else "UNAVAILABLE"
    )
    if status == "UNAVAILABLE" and not reasons and relative_side == "not_comparable":
        basis_cn = basis_cn or "本卡两侧证据暂不具备有效相对比较。"
    return {
        "status": status,
        "relative_side": relative_side if status == "ASSESSED" else "not_comparable",
        "basis_cn": basis_cn,
        "evidence_refs": evidence_refs if status == "ASSESSED" else [],
        "flip_if_cn": flip_if_cn,
        "validation_reasons_cn": reasons,
    }


def _normalize_advisory_guidance(
    raw_guidance: Any,
    *,
    fact_index: dict[str, dict[str, Any]],
    as_of_ms: int | float,
) -> dict[str, Any]:
    if not isinstance(raw_guidance, dict):
        return _default_advisory_guidance(["建议说明缺少有效对象。"])

    reasons: list[str] = []
    if set(raw_guidance) != MODEL_ADVISORY_GUIDANCE_FIELDS:
        reasons.append("建议说明结构尚不完整。")

    summary_cn = _clean_text(
        raw_guidance.get("summary_cn"),
        fallback="本卡建议暂未形成有效说明。",
        limit=420,
    )
    if not isinstance(raw_guidance.get("summary_cn"), str) or not raw_guidance["summary_cn"].strip():
        reasons.append("建议说明缺少可读摘要。")
    issue = _human_text_issue(summary_cn)
    if issue:
        reasons.append("建议说明" + issue)
        summary_cn = "该项说明含有不可展示内容，暂不作为有效建议依据。"
    reasons.extend("建议说明" + reason for reason in _fact_assertion_issues(
        {"summary_cn": summary_cn},
        fact_index,
    ))

    tradeoffs_cn, tradeoffs_ok = _clean_guidance_text_list(
        raw_guidance.get("tradeoffs_cn"),
        limit=6,
        fallback_item="准备时需要权衡空间与补偿。",
    )
    if not tradeoffs_ok:
        reasons.append("建议取舍不是可读列表。")

    evidence_refs, refs_ok, ref_reasons = _valid_refs(
        raw_guidance.get("evidence_refs"), fact_index, as_of_ms=as_of_ms
    )
    if not refs_ok:
        reasons.append("建议引用不是可读列表。")
    reasons.extend("建议说明" + reason for reason in ref_reasons)

    outlooks, outlook_reasons = _normalize_guidance_outlooks(
        raw_guidance.get("outlooks"),
        fact_index=fact_index,
        as_of_ms=as_of_ms,
    )
    reasons.extend(outlook_reasons)

    status = "ASSESSED" if not reasons else "UNAVAILABLE"
    return {
        "schema": ADVISORY_GUIDANCE_SCHEMA_VERSION,
        "status": status,
        "summary_cn": summary_cn,
        "tradeoffs_cn": tradeoffs_cn,
        "evidence_refs": evidence_refs if status == "ASSESSED" else [],
        "outlooks": outlooks,
        "validation_reasons_cn": reasons,
    }


def _normalize_guidance_outlooks(
    raw_outlooks: Any,
    *,
    fact_index: dict[str, dict[str, Any]],
    as_of_ms: int | float,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(raw_outlooks, list):
        return [], ["建议情景不是可读列表。"]
    outlooks: list[dict[str, Any]] = []
    reasons: list[str] = []
    seen_horizons: set[int] = set()
    for raw in raw_outlooks[:4]:
        if not isinstance(raw, dict):
            reasons.append("存在不可读建议情景。")
            continue
        if set(raw) != MODEL_GUIDANCE_OUTLOOK_FIELDS:
            reasons.append("建议情景结构尚不完整。")
        horizon = raw.get("horizon_hours")
        if horizon not in (4, 24):
            reasons.append("建议情景窗口只能是四小时或二十四小时。")
            continue
        scenario_cn = _clean_text(
            raw.get("scenario_cn"),
            fallback="该窗口情景暂未形成有效说明。",
            limit=320,
        )
        watch_cn = _clean_text(
            raw.get("watch_cn"),
            fallback="继续观察关键市场事实变化。",
            limit=320,
        )
        if not isinstance(raw.get("scenario_cn"), str) or not raw["scenario_cn"].strip():
            reasons.append("建议情景缺少可读说明。")
            continue
        if not isinstance(raw.get("watch_cn"), str) or not raw["watch_cn"].strip():
            reasons.append("建议情景缺少可读观察条件。")
            continue
        if _human_text_issue(scenario_cn) or _human_text_issue(watch_cn):
            reasons.append("建议情景说明包含不可展示内容。")
            continue
        assertion_reasons = _fact_assertion_issues(
            {"scenario_cn": scenario_cn, "watch_cn": watch_cn},
            fact_index,
        )
        if assertion_reasons:
            reasons.extend("建议情景" + reason for reason in assertion_reasons)
            continue
        refs, refs_ok, ref_reasons = _valid_refs(
            raw.get("evidence_refs"), fact_index, as_of_ms=as_of_ms
        )
        if not refs_ok:
            reasons.append("建议情景引用不是可读列表。")
            continue
        if ref_reasons:
            reasons.extend("建议情景" + reason for reason in ref_reasons)
            continue
        if horizon in seen_horizons:
            reasons.append("建议情景窗口重复。")
            continue
        seen_horizons.add(horizon)
        outlooks.append({
            "horizon_hours": horizon,
            "scenario_cn": scenario_cn,
            "watch_cn": watch_cn,
            "evidence_refs": refs,
        })
    return outlooks, reasons


def _clean_guidance_text_list(
    value: Any,
    *,
    limit: int,
    fallback_item: str,
) -> tuple[list[str], bool]:
    items, ok = _clean_text_list(value, limit=limit, fallback_item=fallback_item)
    if not ok:
        return items, False
    safe_items: list[str] = []
    for item in items:
        if _human_text_issue(item):
            return safe_items, False
        safe_items.append(item)
    return safe_items, True


def _default_advisory_guidance(
    validation_reasons_cn: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": ADVISORY_GUIDANCE_SCHEMA_VERSION,
        "status": "UNAVAILABLE",
        "summary_cn": "本卡建议暂未形成有效说明。",
        "tradeoffs_cn": [],
        "evidence_refs": [],
        "outlooks": [],
        "validation_reasons_cn": list(validation_reasons_cn or []),
    }


def _comparison_order_issue(
    relative_side: str,
    sides: dict[str, dict[str, Any]],
) -> str:
    if relative_side == "not_comparable":
        return ""
    put_grade = _as_dict(sides.get("put_credit")).get("grade")
    call_grade = _as_dict(sides.get("call_credit")).get("grade")
    if put_grade not in GRADES or call_grade not in GRADES:
        return "两侧比较依赖的等级不完整。"
    put_rank = GRADES.index(put_grade)
    call_rank = GRADES.index(call_grade)
    if relative_side == "put_credit" and put_rank < call_rank:
        return "两侧比较与等级顺序矛盾，相对比较未采纳。"
    if relative_side == "call_credit" and call_rank < put_rank:
        return "两侧比较与等级顺序矛盾，相对比较未采纳。"
    if relative_side == "tie" and put_rank != call_rank:
        return "两侧等级不同，不能写成并列。"
    return ""


def _default_side_comparison(
    validation_reasons_cn: list[str] | None = None,
    *,
    basis_cn: str = "相对比较暂未采纳。",
) -> dict[str, Any]:
    return {
        "status": "UNAVAILABLE",
        "relative_side": "not_comparable",
        "basis_cn": basis_cn,
        "evidence_refs": [],
        "flip_if_cn": "补齐有效两侧比较后重新判断。",
        "validation_reasons_cn": list(validation_reasons_cn or []),
    }


def _legacy_side_comparison() -> dict[str, Any]:
    return _default_side_comparison(
        [],
        basis_cn="旧版未提供两侧比较。",
    )


def _normalize_evidence_roles(
    side_key: str,
    raw_roles: Any,
    *,
    fact_index: dict[str, dict[str, Any]],
    as_of_ms: int | float,
) -> tuple[list[dict[str, str]], dict[str, list[str]], list[str]]:
    reasons: list[str] = []
    if not isinstance(raw_roles, list):
        return [], {role: [] for role in EVIDENCE_ROLES}, [
            _side_label(side_key) + "证据角色不是可读列表。"
        ]

    roles: list[dict[str, str]] = []
    refs_by_role: dict[str, list[str]] = {role: [] for role in EVIDENCE_ROLES}
    seen_roles = set()
    seen_refs: dict[str, set[str]] = {role: set() for role in EVIDENCE_ROLES}
    for raw in raw_roles:
        if not isinstance(raw, dict):
            reasons.append(_side_label(side_key) + "存在不可读证据角色。")
            continue
        if set(raw) != MODEL_SIDE_ROLE_FIELDS:
            reasons.append(_side_label(side_key) + "证据角色结构不完整。")
        ref = str(raw.get("ref") or "").strip()
        role = str(raw.get("role") or "").strip()
        claim = _clean_text(raw.get("claim_cn"), fallback="本项证据作用未形成有效说明。")
        if role not in EVIDENCE_ROLES:
            reasons.append(_side_label(side_key) + "证据角色只能是支持、反证或背景。")
            continue
        fact = fact_index.get(ref)
        if not fact:
            reasons.append(_side_label(side_key) + "存在无法核验的事实引用。")
            continue
        usable, reason = _fact_is_usable(fact, as_of_ms=as_of_ms)
        if not usable:
            reasons.append(_side_label(side_key) + reason)
            continue
        issue = _v21_human_text_issue(claim)
        if not isinstance(raw.get("claim_cn"), str) or not raw.get("claim_cn", "").strip():
            reasons.append(_side_label(side_key) + "证据角色缺少可读说明。")
            continue
        if issue:
            reasons.append(_side_label(side_key) + issue)
            continue
        role_issue = _evidence_role_issue(side_key, role, fact, fact_index)
        if role_issue:
            reasons.append(_side_label(side_key) + role_issue)
            continue
        role_key = (ref, role, claim)
        if role_key in seen_roles:
            continue
        seen_roles.add(role_key)
        roles.append({"ref": ref, "role": role, "claim_cn": claim})
        if ref not in seen_refs[role]:
            refs_by_role[role].append(ref)
            seen_refs[role].add(ref)
    return roles, refs_by_role, reasons


def _evidence_role_issue(
    side_key: str,
    role: str,
    fact: dict[str, Any],
    fact_index: dict[str, dict[str, Any]],
) -> str:
    if _funding_is_nonvoting(fact, fact_index) and role != "context_only":
        return "非计票资金费率只能作为背景，不能当作该侧独立支持或反证。"
    if role == "supports_fit" and _fact_is_direct_adverse_pressure(side_key, fact):
        return "本侧不利推进或方向压力不能直接作为该侧适配支持。"
    return ""


def _funding_is_nonvoting(
    fact: dict[str, Any],
    fact_index: dict[str, dict[str, Any]],
) -> bool:
    if str(fact.get("source_group") or "").upper() != "FUNDING":
        return False
    vote_fact = _as_dict(fact_index.get("pressure.funding.vote_role"))
    text = " ".join(str(vote_fact.get(key) or "") for key in ("value", "summary_cn"))
    return "非计票" in text


def _fact_is_direct_adverse_pressure(side_key: str, fact: dict[str, Any]) -> bool:
    fact_id = str(fact.get("id") or "")
    value = str(fact.get("value") or "")
    summary = str(fact.get("summary_cn") or "")
    topic = str(fact.get("topic") or "")
    mitigated = any(word in summary for word in ("受限", "暂未", "没有", "未见", "不能", "不足以"))
    if side_key == "put_credit":
        if fact_id == "side.put.adverse_progress" and value == "下行推进":
            return True
        if topic == "adverse_pressure" and value == "下行":
            return True
        return not mitigated and (
            "显示下行推进" in summary or "向 Put 信用价差的不利方向推进" in summary
        )
    if fact_id == "side.call.adverse_progress" and value == "上行推进":
        return True
    if topic == "adverse_pressure" and value == "上行":
        return True
    return not mitigated and (
        "显示上行推进" in summary or "向 Call 信用价差的不利方向推进" in summary
    )


def _clean_human_text_list(
    value: Any,
    *,
    limit: int,
    fallback_item: str,
) -> tuple[list[str], bool]:
    items, ok = _clean_text_list(value, limit=limit, fallback_item=fallback_item)
    if not ok:
        return items, False
    safe_items: list[str] = []
    for item in items:
        if _v21_human_text_issue(item):
            return safe_items, False
        safe_items.append(item)
    return safe_items, True


def _valid_refs(
    value: Any,
    fact_index: dict[str, dict[str, Any]],
    *,
    as_of_ms: int | float,
) -> tuple[list[str], bool, list[str]]:
    if value is None:
        return [], False, []
    if not isinstance(value, list):
        return [], False, []
    refs: list[str] = []
    reasons: list[str] = []
    seen = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            reasons.append("存在不可读事实引用。")
            continue
        ref = item.strip()
        fact = fact_index.get(ref)
        if not fact:
            reasons.append("存在无法核验的事实引用。")
            continue
        usable, reason = _fact_is_usable(fact, as_of_ms=as_of_ms)
        if not usable:
            reasons.append(reason)
            continue
        if ref not in seen:
            refs.append(ref)
            seen.add(ref)
    return refs, True, reasons


def _fact_assertion_issues(text_fields, fact_index):
    """Check narrow explicit assertions, not the truth of free-form reasoning."""
    text = " ".join(
        value for value in text_fields.values() if isinstance(value, str)
    )
    reasons = []
    # The v2 packet has point-count/endpoint/efficiency summaries, not a proven
    # all-window monotonicity or maximum adverse excursion fact. Even an exact
    # point source label alone does not establish the claimed full path.
    for clause in re.split(r"[；;。\n]", text):
        subject = re.search(r"价格|行情", clause)
        if not subject:
            continue
        prefix = clause[:subject.start()]
        if (re.search(r"若|如果|一旦|可能", prefix)
                or re.search(r"(?:不能|无法|不应|不足以|未能)(?:确认|证明|断言|得出|说明).*$", prefix)):
            continue
        if (re.search(r"始终|一直|全程|整个窗口|整个期间|从未|持续(?:向上|向下|上行|下行)", clause)
                and re.search(r"向上|向下|上行|下行|上涨|下跌|反弹", clause)):
            reasons.append("说明把窗口汇总变化当作全程价格路径，本卡缺少支持该主张的逐点证据。")
            break
    price = _as_dict(fact_index.get("market.price.current")).get("value")
    if isinstance(price, (int, float)):
        for match in re.finditer(r"(?:现价|当前价格|标的价格)\s*(?:为|是|：|:)?\s*([0-9][0-9,.]*)", text):
            try:
                observed = float(match.group(1).replace(",", "").rstrip("."))
            except ValueError:
                continue
            if abs(observed - price) > max(0.01, abs(price) * 0.00001):
                reasons.append("说明中的现价与本卡事实不一致。")
                break
    gamma = _as_dict(fact_index.get("structure.gex.net_gamma_notional_usd")).get("value")
    if isinstance(gamma, (int, float)) and gamma:
        for match in re.finditer(r"(?:净\s*(?:Gamma|GEX)|总Gamma敞口)\s*(?:为|是|呈)\s*(正|负)", text, re.I):
            if (match.group(1) == "正") != (gamma > 0):
                reasons.append("说明中的净期权敞口符号与本卡事实不一致。")
    return reasons


def _fact_is_usable(fact: dict[str, Any], *, as_of_ms: int | float) -> tuple[bool, str]:
    label = _clean_text(fact.get("label_cn"), fallback="该事实")
    if fact.get("usable") is not True:
        return False, label + "当前不可用，不能作为本侧引用。"
    observed_at_ms = fact.get("observed_at_ms")
    if not isinstance(observed_at_ms, (int, float)):
        return False, label + "缺少可核验观察时点，不能作为本侧引用。"
    if observed_at_ms > as_of_ms:
        return False, label + "晚于本卡评级时点，不能作为当前证据。"
    return True, ""


def _has_constraint_argument(
    text_fields: dict[str, str],
    evidence_refs: list[str],
    counter_refs: list[str],
    fact_index: dict[str, dict[str, Any]],
) -> bool:
    text_chunks = [
        value for value in text_fields.values() if isinstance(value, str)
    ]
    fact_chunks = []
    for ref in evidence_refs + counter_refs:
        fact = fact_index.get(ref) or {}
        fact_chunks.extend(
            str(fact.get(key) or "")
            for key in ("topic", "label_cn", "summary_cn", "source_group")
        )
    return (
        bool(_CONSTRAINT_WORD_RE.search(" ".join(text_chunks)))
        and bool(_CONSTRAINT_WORD_RE.search(" ".join(fact_chunks)))
    )


def _has_price_bias_signal_argument(
    text_fields: dict[str, str],
    evidence_refs: list[str],
    counter_refs: list[str],
    fact_index: dict[str, dict[str, Any]],
) -> bool:
    text = " ".join(str(value or "") for value in text_fields.values())
    fact_chunks = []
    for ref in evidence_refs + counter_refs:
        fact = fact_index.get(ref) or {}
        fact_chunks.extend(
            str(fact.get(key) or "")
            for key in ("topic", "label_cn", "summary_cn", "source_group")
        )
    return (
        bool(_PRICE_BIAS_SIGNAL_RE.search(text))
        and bool(_PRICE_BIAS_SIGNAL_RE.search(" ".join(fact_chunks)))
    )


def _build_local_action_state(
    card: dict[str, Any],
    sides: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    context = _legacy_context(card)
    return {
        side_key: _action_for_side(side_key, side, context)
        for side_key, side in sides.items()
    }


def _action_for_side(
    side_key: str,
    side: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    label = _side_label(side_key)
    if side.get("status") != "RATED":
        return {
            "state": "UNRATED",
            "label_cn": "暂未评级",
            "reasons_cn": [label + "暂未形成有效证据等级。"],
        }
    grade = side.get("grade")
    if context["blocked"]:
        return {
            "state": "BLOCKED",
            "label_cn": "本地阻断",
            "reasons_cn": ["旧有阻断或硬否决仍然存在，不能进入人工准备。"],
        }
    if context["expired"]:
        return {
            "state": "BLOCKED",
            "label_cn": "窗口失效",
            "reasons_cn": ["信号窗口或事件状态已经失效，需要等待新卡。"],
        }
    if grade == "D":
        return {
            "state": "AVOID",
            "label_cn": "本轮回避",
            "reasons_cn": [label + "有效事实总体反对当前适配解释。"],
        }
    if context["waiting"]:
        return {
            "state": "WAIT",
            "label_cn": "等待确认",
            "reasons_cn": ["旧有等待条件或窗口尚未打开，证据等级只作为关注依据。"],
        }
    if grade in ("A", "S") and not _direction_allows_side(side_key, context["direction"]):
        return {
            "state": "WAIT",
            "label_cn": "侧别待确认",
            "reasons_cn": [label + "证据较强，但原信号侧别边界尚未支持该侧准备。"],
        }
    if grade in ("A", "S"):
        return {
            "state": "PREPARE",
            "label_cn": "人工准备",
            "reasons_cn": [
                label + "达到信号层准入；候选两腿、报价、净补偿和风控仍需确认。"
            ],
        }
    if grade == "B":
        return {
            "state": "WATCH",
            "label_cn": "启动关注",
            "reasons_cn": [label + "已有值得跟踪的支持，但竞争解释仍有实质分量。"],
        }
    return {
        "state": "WATCH",
        "label_cn": "普通观察",
        "reasons_cn": [label + "可以判断，但没有明显支持优势。"],
    }


def _action_summary(
    sides: dict[str, dict[str, Any]],
    action_state: dict[str, dict[str, Any]],
) -> str:
    prepare = [
        side_key
        for side_key in SIDE_KEYS
        if _as_dict(action_state.get(side_key)).get("state") == "PREPARE"
    ]
    if len(prepare) == 1:
        side_key = prepare[0]
        grade = sides[side_key].get("grade")
        return (
            f"{_side_label(side_key)}证据等级为 {grade}，本地边界允许进入人工交易准备；"
            "具体两腿、报价、净补偿和退出条件仍需随后确认。"
        )
    if len(prepare) == 2:
        return (
            "Put 与 Call 两侧都达到信号层准备条件，但没有单一优先侧；只能进入人工对比，"
            "不代表双侧同时交易。"
        )
    states = {_as_dict(action_state.get(side_key)).get("state") for side_key in SIDE_KEYS}
    if "BLOCKED" in states:
        return "本卡仍受本地阻断或失效条件约束，不能进入人工交易准备。"
    if "WAIT" in states:
        return "本卡存在等待条件或侧别边界限制，先保留为观察与人工复核。"
    if "UNRATED" in states and len(states) > 1:
        descriptions = [f"{_side_label(side_key)}{_as_dict(action_state.get(side_key)).get('label_cn', '暂未评级')}"
                        for side_key in SIDE_KEYS]
        return "当前不能进入人工准备；" + "，".join(descriptions) + "。"
    if "WATCH" in states:
        descriptions = [
            _side_action_phrase(
                side_key,
                _as_dict(sides.get(side_key)),
                _as_dict(action_state.get(side_key)),
            )
            for side_key in SIDE_KEYS
        ]
        return "当前没有一侧进入人工准备；" + "，".join(descriptions) + "。"
    if "AVOID" in states:
        return "当前没有一侧进入人工准备；D 级侧按本轮回避处理。"
    return "本卡暂未形成有效综合证据等级，保留市场事实等待重新评审。"


def _legacy_context(card: dict[str, Any]) -> dict[str, Any]:
    card = _as_dict(card)
    decision = _as_dict(card.get("decision"))
    matrix = _as_dict(card.get("decision_matrix"))
    blocking = _as_dict(card.get("blocking"))
    window = _as_dict(card.get("signal_window"))
    direction = str(matrix.get("direction") or decision.get("lean") or "").upper()
    decision_state = str(matrix.get("decision_state") or "").upper()
    support_label = str(decision.get("support_label") or "").upper()
    soft_gates = blocking.get("soft_gates") if isinstance(blocking.get("soft_gates"), list) else []
    neutral_window = _as_dict(window.get("neutral_repair"))
    nr_state = " ".join(str(v or "").upper() for v in (
        window.get("nr_state"), window.get("state"), neutral_window.get("state")))
    boundary_values = {decision_state, support_label,
                       str(decision.get("support_pre_gate") or "").upper(),
                       str(blocking.get("block_kind") or "").upper()}
    macro = _as_dict(_as_dict(card.get("factor_cross_section")).get("macro_pressure"))
    blocked = (
        blocking.get("has_block") is True
        or bool(blocking.get("hard_veto"))
        or blocking.get("hard_block") is True or blocking.get("hard_blocked") is True
        or _as_dict(macro.get("macro_shock")).get("block") is True
        or any("BLOCK" in value or value.startswith("NO_TRADE")
               or value in {"HARD", "SOFT_GATE"} for value in boundary_values)
    )
    expired = any(word in nr_state for word in ("EXPIRED", "STALE", "INVALID", "ENDED", "TIMEOUT", "FAILED"))
    waiting = (
        any("WAIT" in value or value == "UNABLE_TO_JUDGE" for value in boundary_values)
        or bool(soft_gates)
        or (window.get("is_active") is False and not expired)
        or (neutral_window.get("is_active") is False and not expired)
    )
    return {
        "direction": direction,
        "blocked": blocked,
        "expired": expired,
        "waiting": waiting,
    }


def _source_record_hash(card: dict[str, Any]) -> Any:
    card = _as_dict(card)
    identity = _as_dict(card.get("identity"))
    if identity.get("source_record_hash"):
        return identity.get("source_record_hash")
    integrity = _as_dict(card.get("producer_integrity"))
    if integrity.get("record_hash"):
        return integrity.get("record_hash")
    return _as_dict(card.get("integrity")).get("record_hash")


def _direction_allows_side(side_key: str, direction: str) -> bool:
    if "NEUTRAL" in direction or direction in {"RANGE", "FLAT"}:
        return True
    if not any(word in direction for word in ("BULL", "BEAR", "UP", "DOWN", "LONG", "SHORT")):
        return False
    if side_key == "put_credit":
        return not any(word in direction for word in ("BEAR", "DOWN", "SHORT"))
    return not any(word in direction for word in ("BULL", "UP", "LONG"))


def _canonical_packet(packet: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(packet, dict):
        raise EvidenceFormatError("packet must be object")
    identity = packet.get("identity")
    facts = packet.get("facts")
    if not isinstance(identity, dict):
        raise EvidenceFormatError("packet.identity must be object")
    if not isinstance(facts, list):
        raise EvidenceFormatError("packet.facts must be list")
    if not isinstance(identity.get("as_of_ms"), (int, float)):
        raise EvidenceFormatError("packet.identity.as_of_ms must be numeric")
    for fact in facts:
        if not isinstance(fact, dict) or not str(fact.get("id") or "").strip():
            raise EvidenceFormatError("packet facts must be objects with id")
    schema = packet.get("schema") or packet.get("schema_version") or PACKET_SCHEMA_VERSION
    if schema not in ACCEPTED_PACKET_SCHEMA_VERSIONS:
        raise EvidenceFormatError("invalid packet schema")
    return {
        "schema": schema,
        "identity": _clone(identity),
        "facts": _clone(facts),
        "limitations_cn": _clone(packet.get("limitations_cn") or []),
    }


def _packet_as_of_ms(packet: dict[str, Any]) -> int | float:
    return _as_dict(packet.get("identity")).get("as_of_ms")


def _fact_index(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(fact.get("id")).strip(): fact
        for fact in packet.get("facts", [])
        if isinstance(fact, dict) and str(fact.get("id") or "").strip()
    }


def _evidence_context(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": packet.get("schema") or PACKET_SCHEMA_VERSION,
        "identity": _clone(packet.get("identity")),
        "limitations_cn": _clone(packet.get("limitations_cn") or []),
    }


def _packet_hash(packet: dict[str, Any]) -> str:
    return _sha256_json(_canonical_packet(packet))


def _assessment_hash(advisory: dict[str, Any]) -> str:
    clone = _clone(advisory)
    validation = clone.get("validation")
    if isinstance(validation, dict):
        validation.pop("assessment_hash", None)
    return "sha256:" + hashlib.sha256(_browser_canonical_json(clone).encode("utf-8")).hexdigest()


def _browser_canonical_json(value: Any) -> str:
    """Match the browser's sorted JSON, including integer-valued floats.

    Market descriptors are finite JSON numbers. Python's default 1.0/e-07
    spellings must not invalidate the same values parsed by a browser.
    """
    if value is None or isinstance(value, (str, bool)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        if isinstance(value, int) and abs(value) > 9007199254740991:
            value = float(value)
        if not math.isfinite(value):
            raise EvidenceFormatError("non-finite market value")
        if value == 0:
            return "0"
        if isinstance(value, int):
            return str(value)
        text = repr(value)
        if 1e-6 <= abs(value) < 1e21:
            text = format(Decimal(text), "f")
            return text.rstrip("0").rstrip(".") if "." in text else text
        mantissa, exponent = text.lower().split("e")
        mantissa = mantissa.rstrip("0").rstrip(".") if "." in mantissa else mantissa
        exp = int(exponent)
        return mantissa + "e" + ("+" if exp >= 0 else "-") + str(abs(exp))
    if isinstance(value, list):
        return "[" + ",".join(_browser_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" +
                              _browser_canonical_json(value[key]) for key in sorted(value)) + "}"
    raise EvidenceFormatError("payload is not JSON serializable")


def _sha256_json(payload: Any) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError as exc:
        raise EvidenceFormatError("payload is not JSON serializable") from exc
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _summary_side(value: Any) -> dict[str, Any]:
    side = _as_dict(value)
    return {
        "grade": side.get("grade"),
        "status": side.get("status"),
        "basis_cn": str(side.get("basis_cn") or ""),
    }


def _summary_price_bias(value: Any) -> dict[str, Any]:
    bias = _as_dict(value)
    return {
        "schema": bias.get("schema"),
        "status": bias.get("status"),
        "bias": bias.get("bias"),
        "bias_cn": _price_bias_label(bias.get("bias")),
        "basis_cn": str(bias.get("basis_cn") or ""),
    }


def _summary_advisory_guidance(value: Any) -> dict[str, Any]:
    guidance = _as_dict(value)
    if not guidance:
        return _default_advisory_guidance(["建议说明缺失。"])
    return {
        "schema": guidance.get("schema"),
        "status": guidance.get("status"),
        "summary_cn": str(guidance.get("summary_cn") or ""),
        "tradeoffs_cn": _clone(guidance.get("tradeoffs_cn") or []),
        "evidence_refs": _clone(guidance.get("evidence_refs") or []),
        "outlooks": _clone(guidance.get("outlooks") or []),
        "validation_reasons_cn": _clone(guidance.get("validation_reasons_cn") or []),
    }


def _summary_side_comparison(value: Any, *, has_comparison: bool) -> dict[str, Any]:
    if not has_comparison:
        return _legacy_side_comparison()
    comparison = _as_dict(value)
    if not comparison:
        return _default_side_comparison(["两侧比较缺失，相对比较未采纳。"])
    validation_reasons = _clone(comparison.get("validation_reasons_cn") or [])
    status = comparison.get("status")
    relative_side = comparison.get("relative_side")
    if status == "UNAVAILABLE" and validation_reasons:
        return {
            "status": "UNAVAILABLE",
            "relative_side": "not_comparable",
            "relative_side_cn": _relative_side_label("not_comparable"),
            "basis_cn": "相对比较未采纳：" + "；".join(str(item) for item in validation_reasons[:2]),
            "evidence_refs": [],
            "flip_if_cn": "补齐有效两侧比较后重新判断。",
            "validation_reasons_cn": validation_reasons,
        }
    return {
        "status": status,
        "relative_side": relative_side,
        "relative_side_cn": _relative_side_label(relative_side),
        "basis_cn": str(comparison.get("basis_cn") or ""),
        "evidence_refs": _clone(comparison.get("evidence_refs") or []),
        "flip_if_cn": str(comparison.get("flip_if_cn") or ""),
        "validation_reasons_cn": validation_reasons,
    }


def _source_boundary(
    sides: dict[str, Any],
    action_state: Any,
    *,
    card: dict[str, Any] | None,
) -> dict[str, Any]:
    del sides, action_state
    if card is None:
        return _source_boundary_default()
    return _source_boundary_from_card(card)


def _summary_source_boundary(
    value: Any,
    *,
    sides: dict[str, Any],
    action_state: Any,
    display_action_state: dict[str, dict[str, Any]],
    card: dict[str, Any] | None,
) -> dict[str, Any]:
    boundary = _as_dict(value)
    try:
        _validate_source_boundary(boundary)
        return _clone(boundary)
    except EvidenceFormatError:
        if card is not None:
            return _source_boundary(sides, action_state, card=card)
        del sides, action_state, display_action_state
        return _source_boundary_default()


def _source_boundary_default() -> dict[str, Any]:
    sides = {
        side_key: {
            "state": "INFO",
            "label_cn": "资料未提供",
            "reasons_cn": ["源卡资料未提供，无法核验原信号窗口或风险边界。"],
        }
        for side_key in SIDE_KEYS
    }
    return {
        "label_cn": "原信号窗口与风险",
        "summary_cn": "源卡资料未提供，无法核验原信号窗口或风险边界。此处只说明源端边界，不代表新的执行许可。",
        "sides": sides,
    }


def _source_boundary_from_card(card: dict[str, Any]) -> dict[str, Any]:
    card = _as_dict(card)
    context = _legacy_context(card)
    global_state, global_label, global_reasons = _source_boundary_global_state(card, context)
    readonly_reason = _source_readonly_reason(card)
    if readonly_reason:
        global_reasons = list(global_reasons) + [readonly_reason]
    boundary_sides: dict[str, dict[str, Any]] = {}
    summary_reasons: list[str] = []
    for side_key in SIDE_KEYS:
        side_state = global_state
        side_label = global_label
        side_reasons = list(global_reasons)
        side_reason = _source_side_boundary_reason(side_key, context)
        if side_reason:
            side_reasons.append(side_reason)
            if side_state != "BLOCKED":
                side_state = "WAIT"
                side_label = "侧别边界"
        cleaned_reasons = _dedupe_clean_reasons(side_reasons)
        summary_reasons.extend(reason for reason in cleaned_reasons if reason not in summary_reasons)
        boundary_sides[side_key] = {
            "state": side_state,
            "label_cn": side_label,
            "reasons_cn": cleaned_reasons,
        }
    summary = "；".join(summary_reasons[:2]) if summary_reasons else "原信号未列出额外等待或阻断。"
    return {
        "label_cn": "原信号窗口与风险",
        "summary_cn": summary + " 此处只说明源端边界，不代表新的执行许可。",
        "sides": boundary_sides,
    }


def _source_boundary_global_state(
    card: dict[str, Any],
    context: dict[str, Any],
) -> tuple[str, str, list[str]]:
    if context["expired"]:
        return "BLOCKED", "窗口失效", ["信号窗口或事件状态已经失效，需要等待新卡。"]
    hard_reason = _source_hard_reason(card)
    if hard_reason:
        return "BLOCKED", "本地硬风险", [hard_reason]
    wait_reason = _source_wait_reason(card, context)
    if wait_reason:
        return "WAIT", "等待条件", [wait_reason]
    return "INFO", "无额外原边界", ["原信号未列出额外等待或阻断。"]


def _source_hard_reason(card: dict[str, Any]) -> str:
    blocking = _as_dict(card.get("blocking"))
    macro = _as_dict(_as_dict(card.get("factor_cross_section")).get("macro_pressure"))
    hard_veto = blocking.get("hard_veto")
    macro_shock = _as_dict(macro.get("macro_shock"))
    decision = _as_dict(card.get("decision"))
    matrix = _as_dict(card.get("decision_matrix"))
    boundary_values = [
        str(matrix.get("decision_state") or "").upper(),
        str(decision.get("support_label") or "").upper(),
        str(decision.get("support_pre_gate") or "").upper(),
        str(blocking.get("block_kind") or "").upper(),
    ]
    blocking_active = (
        _source_active_hard_veto(hard_veto)
        or blocking.get("hard_block") is True
        or blocking.get("hard_blocked") is True
        or any(_source_hard_boundary_value(value) for value in boundary_values)
    )
    macro_active = macro_shock.get("block") is True
    if blocking_active:
        for value in (
            _as_dict(hard_veto).get("reason_cn"),
            blocking.get("reason_cn"),
            blocking.get("block_reason_cn"),
        ):
            text = _clean_text(value, fallback="")
            if text and not _human_text_issue(text):
                return "存在本地硬风险：" + text
    if macro_active:
        for value in (
            _as_dict(macro_shock.get("reason")).get("reason_cn"),
            macro_shock.get("reason_cn"),
        ):
            text = _clean_text(value, fallback="")
            if text and not _human_text_issue(text):
                return "存在本地硬风险：" + text
    if (
        blocking_active
        or macro_active
    ):
        return "存在本地硬风险，需等待新卡复核。"
    return ""


def _source_wait_reason(card: dict[str, Any], context: dict[str, Any]) -> str:
    del context
    window = _as_dict(card.get("signal_window"))
    neutral_window = _as_dict(window.get("neutral_repair"))
    if window.get("is_active") is False or neutral_window.get("is_active") is False:
        return "信号窗口尚未打开，当前只记录源端等待条件。"
    blocking = _as_dict(card.get("blocking"))
    for gate in _source_active_soft_gates(blocking):
        text = _clean_text(_as_dict(gate).get("reason_cn"), fallback="")
        if text and not _human_text_issue(text):
            return "存在源端等待条件：" + text
    if _source_wait_boundary_active(card):
        return "源端存在等待条件或窗口边界，需要后续确认。"
    unknown_block_reason = _source_unknown_block_reason(card)
    if unknown_block_reason:
        return unknown_block_reason
    return ""


def _source_hard_boundary_value(value: str) -> bool:
    if not value:
        return False
    return (
        value == "HARD"
        or "HARD_VETO" in value
        or "HARD_BLOCK" in value
        or "HARD RISK" in value
        or "硬" in value
    )


def _source_active_hard_veto(value: Any) -> bool:
    veto = _as_dict(value)
    if not veto:
        return False
    if _source_gate_inactive(veto):
        return False
    active_keys = ("active", "is_active", "triggered", "block", "blocked", "hard_block")
    if any(veto.get(key) is True for key in active_keys):
        return True
    state = str(veto.get("state") or veto.get("status") or veto.get("kind") or "").upper()
    if any(word in state for word in ("ACTIVE", "TRIGGER", "BLOCK", "VETO", "HARD")):
        return True
    return True


def _source_active_soft_gates(blocking: dict[str, Any]) -> list[dict[str, Any]]:
    raw_gates = blocking.get("soft_gates") if isinstance(blocking.get("soft_gates"), list) else []
    active: list[dict[str, Any]] = []
    for raw_gate in raw_gates:
        gate = _as_dict(raw_gate)
        if gate and not _source_gate_inactive(gate):
            active.append(gate)
    return active


def _source_gate_inactive(gate: dict[str, Any]) -> bool:
    for key in ("active", "is_active", "triggered", "block", "blocked", "waiting"):
        if gate.get(key) is False:
            return True
    state = str(gate.get("state") or gate.get("status") or "").upper()
    return state in {
        "INACTIVE",
        "DISABLED",
        "CLEARED",
        "CLEAR",
        "PASS",
        "PASSED",
        "OK",
        "OFF",
        "NOT_TRIGGERED",
        "NOT_ACTIVE",
    }


def _source_wait_boundary_active(card: dict[str, Any]) -> bool:
    decision = _as_dict(card.get("decision"))
    matrix = _as_dict(card.get("decision_matrix"))
    blocking = _as_dict(card.get("blocking"))
    boundary_values = [
        str(matrix.get("decision_state") or "").upper(),
        str(decision.get("support_label") or "").upper(),
        str(decision.get("support_pre_gate") or "").upper(),
        str(blocking.get("block_kind") or "").upper(),
    ]
    return any("WAIT" in value or value == "UNABLE_TO_JUDGE" or value == "SOFT_GATE" for value in boundary_values)


def _source_unknown_block_reason(card: dict[str, Any]) -> str:
    blocking = _as_dict(card.get("blocking"))
    if blocking.get("has_block") is not True:
        return ""
    if (
        _source_active_hard_veto(blocking.get("hard_veto"))
        or blocking.get("hard_block") is True
        or blocking.get("hard_blocked") is True
    ):
        return ""
    if _source_wait_boundary_active(card):
        return ""
    return "源端存在阻断汇总标记，但阻断类型未明，需要人工核验源卡边界。"


def _source_readonly_reason(card: dict[str, Any]) -> str:
    matrix = _as_dict(card.get("decision_matrix"))
    if matrix.get("execution_allowed") is False:
        return "本卡为只读审计记录，执行开关关闭；这是运行权限背景，不评价市场适配。"
    return ""


def _source_side_boundary_reason(side_key: str, context: dict[str, Any]) -> str:
    direction = str(context.get("direction") or "").upper()
    if not direction or _direction_allows_side(side_key, direction):
        return ""
    if side_key == "put_credit":
        return "原信号方向偏空，Put 信用价差与源端侧别边界不匹配。"
    return "原信号方向偏多，Call 信用价差与源端侧别边界不匹配。"


def _dedupe_clean_reasons(values: list[str]) -> list[str]:
    reasons: list[str] = []
    for value in values:
        text = _clean_text(value, fallback="")
        if text and not _human_text_issue(text) and text not in reasons:
            reasons.append(text)
    return reasons or ["原信号未列出额外等待或阻断。"]


def _validate_source_boundary(boundary: dict[str, Any]) -> None:
    if set(boundary) != {"label_cn", "summary_cn", "sides"}:
        raise EvidenceFormatError("source_boundary shape invalid")
    for field_name in ("label_cn", "summary_cn"):
        text = boundary.get(field_name)
        if not isinstance(text, str) or not text.strip() or _human_text_issue(text):
            raise EvidenceFormatError("source_boundary text invalid")
    sides = _as_dict(boundary.get("sides"))
    if set(sides) != set(SIDE_KEYS):
        raise EvidenceFormatError("source_boundary sides invalid")
    for side_key in SIDE_KEYS:
        side = _as_dict(sides.get(side_key))
        if set(side) != {"state", "label_cn", "reasons_cn"}:
            raise EvidenceFormatError("source_boundary side invalid")
        if not isinstance(side.get("state"), str) or not side.get("state"):
            raise EvidenceFormatError("source_boundary side state invalid")
        if not isinstance(side.get("label_cn"), str) or _human_text_issue(side.get("label_cn")):
            raise EvidenceFormatError("source_boundary side label invalid")
        reasons = side.get("reasons_cn")
        if not isinstance(reasons, list) or any(
            not isinstance(item, str) or not item.strip() or _human_text_issue(item)
            for item in reasons
        ):
            raise EvidenceFormatError("source_boundary side reasons invalid")


def _market_snapshot(facts: Any) -> dict[str, Any]:
    if not isinstance(facts, list):
        return {
            "status": "UNAVAILABLE",
            "price": None,
            "unit": None,
            "observed_at_ms": None,
            "reason_cn": "本卡没有可读价格事实。",
        }
    price_fact = next(
        (fact for fact in facts if isinstance(fact, dict)
         and fact.get("id") == "market.price.current"),
        None,
    )
    if not price_fact:
        return {
            "status": "UNAVAILABLE",
            "price": None,
            "unit": None,
            "observed_at_ms": None,
            "reason_cn": "本卡未提供卡片时点价格。",
        }
    value = price_fact.get("value")
    unit = price_fact.get("unit")
    observed_at_ms = price_fact.get("observed_at_ms")
    if (
        price_fact.get("usable") is True
        and isinstance(value, (int, float))
        and isinstance(unit, str)
        and unit.strip()
        and isinstance(observed_at_ms, (int, float))
    ):
        return {
            "status": "AVAILABLE",
            "price": value,
            "unit": unit,
            "observed_at_ms": observed_at_ms,
        }
    return {
        "status": "UNAVAILABLE",
        "price": None,
        "unit": unit if isinstance(unit, str) and unit.strip() else None,
        "observed_at_ms": observed_at_ms if isinstance(observed_at_ms, (int, float)) else None,
        "reason_cn": "卡片时点价格缺失或不可用，不使用实时行情回填。",
    }


def _display_action_state(
    sides: dict[str, Any],
    action_state: Any,
    *,
    card: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    actions = _as_dict(action_state)
    return {
        side_key: _display_action_for_side(
            side_key,
            _as_dict(sides.get(side_key)),
            _as_dict(actions.get(side_key)),
            card=card,
        )
        for side_key in SIDE_KEYS
    }


def _display_action_for_side(
    side_key: str,
    side: dict[str, Any],
    action: dict[str, Any],
    *,
    card: dict[str, Any] | None,
) -> dict[str, Any]:
    state = str(action.get("state") or "UNRATED")
    label_cn = str(action.get("label_cn") or "暂未评级")
    reasons = _clean_display_reasons(action.get("reasons_cn"))
    if card is not None:
        if state == "BLOCKED":
            reasons = [_card_block_or_expiry_reason(card)]
        elif state == "WAIT":
            reasons = [_card_wait_reason(card)]
    return {
        "state": state,
        "label_cn": label_cn,
        "grade": side.get("grade"),
        "side_label_cn": _side_label(side_key),
        "reasons_cn": reasons,
    }


def _display_action_summary(
    sides: dict[str, Any],
    action_state: Any,
    comparison: dict[str, Any],
) -> str:
    actions = _as_dict(action_state)
    prepare = [
        side_key
        for side_key in SIDE_KEYS
        if _as_dict(actions.get(side_key)).get("state") == "PREPARE"
    ]
    if len(prepare) == 1:
        side_key = prepare[0]
        grade = _as_dict(sides.get(side_key)).get("grade")
        return (
            f"{_side_label(side_key)}{grade} 级达到信号层准入，可进入人工交易准备；"
            "候选两腿、报价、净补偿和退出条件仍需确认。"
        )
    if len(prepare) == 2:
        return "两侧都达到人工准备条件；需要人工二选一复核，不代表双侧同时交易。"
    reason_text = " ".join(
        " ".join(str(item) for item in _as_dict(actions.get(side_key)).get("reasons_cn", []))
        for side_key in SIDE_KEYS
    )
    if "窗口尚未打开" in reason_text:
        return "本卡信号窗口尚未打开，证据等级只作为关注依据，暂不能进入人工交易准备。"
    if "已经失效" in reason_text:
        return "本卡信号窗口或事件状态已经失效，需要等待新卡，暂不能进入人工交易准备。"
    if "本地硬否决" in reason_text or "阻断" in reason_text:
        return "本卡仍受本地阻断约束，不能进入人工交易准备。"
    phrases = [
        _side_action_phrase(side_key, _as_dict(sides.get(side_key)), _as_dict(actions.get(side_key)))
        for side_key in SIDE_KEYS
    ]
    text = "当前没有一侧进入人工准备；" + "，".join(phrases) + "。"
    if _as_dict(comparison).get("status") == "ASSESSED":
        text += "相对比较：" + _relative_side_label(comparison.get("relative_side")) + "。"
    return text


def _side_action_phrase(side_key: str, side: dict[str, Any], action: dict[str, Any]) -> str:
    grade = side.get("grade")
    grade_text = f"{grade} 级" if grade in GRADES else "未评级"
    label = str(action.get("label_cn") or "暂未评级")
    if grade not in GRADES and label == "暂未评级":
        return f"{_side_label(side_key)}暂未评级"
    return f"{_side_label(side_key)}{grade_text}{label}"


def _display_projection_hash(summary: dict[str, Any]) -> str:
    clone = _clone(summary)
    clone.pop("display_projection_hash", None)
    return "sha256:" + hashlib.sha256(_browser_canonical_json(clone).encode("utf-8")).hexdigest()


def _price_bias_label(value: Any) -> str:
    return {
        "BULLISH": "偏多",
        "BEARISH": "偏空",
        "NEUTRAL": "中性",
        "MIXED": "分歧",
        "UNDETERMINED": "无法判断",
    }.get(value, "无法判断")


def _relative_side_label(value: Any) -> str:
    return {
        "put_credit": "Put 侧相对更有依据",
        "call_credit": "Call 侧相对更有依据",
        "tie": "两侧接近，无单一优先侧",
        "not_comparable": "相对比较未采纳",
    }.get(value, "相对比较未采纳")


def _clean_display_reasons(value: Any) -> list[str]:
    if not isinstance(value, list):
        return ["当前行动原因暂未形成有效说明。"]
    reasons = []
    for item in value:
        text = _clean_text(item, fallback="")
        if text and not _human_text_issue(text):
            reasons.append(text)
    return reasons or ["当前行动原因暂未形成有效说明。"]


def _card_block_or_expiry_reason(card: dict[str, Any]) -> str:
    context = _legacy_context(card)
    if context["expired"]:
        return "信号窗口或事件状态已经失效，需要等待新卡。"
    window = _as_dict(_as_dict(card).get("signal_window"))
    neutral_window = _as_dict(window.get("neutral_repair"))
    if window.get("is_active") is False or neutral_window.get("is_active") is False:
        return "信号窗口尚未打开，证据等级只作为关注依据。"
    blocking = _as_dict(_as_dict(card).get("blocking"))
    for value in (
        _as_dict(blocking.get("hard_veto")).get("reason_cn"),
        blocking.get("reason_cn"),
        blocking.get("block_reason_cn"),
    ):
        text = _clean_text(value, fallback="")
        if text and not _human_text_issue(text):
            return "存在本地硬否决：" + text
    return "旧有阻断或硬否决仍然存在，不能进入人工准备。"


def _card_wait_reason(card: dict[str, Any]) -> str:
    window = _as_dict(_as_dict(card).get("signal_window"))
    neutral_window = _as_dict(window.get("neutral_repair"))
    if window.get("is_active") is False or neutral_window.get("is_active") is False:
        return "信号窗口尚未打开，证据等级只作为关注依据。"
    blocking = _as_dict(_as_dict(card).get("blocking"))
    soft_gates = blocking.get("soft_gates") if isinstance(blocking.get("soft_gates"), list) else []
    for gate in soft_gates:
        text = _clean_text(_as_dict(gate).get("reason_cn"), fallback="")
        if text and not _human_text_issue(text):
            return "存在等待条件：" + text
    return "旧有等待条件或侧别边界限制仍在，先保留为观察与人工复核。"


def _validate_persisted_side(
    side_key: str,
    side: dict[str, Any],
    fact_index: dict[str, dict[str, Any]],
    *,
    as_of_ms: int | float,
    recheck_claims=True,
    protocol: str = "2.0",
) -> None:
    if protocol == "2.2":
        _validate_persisted_v22_side(
            side_key,
            side,
            fact_index,
            as_of_ms=as_of_ms,
            recheck_claims=recheck_claims,
        )
        return
    if protocol == "2.1":
        _validate_persisted_v21_side(
            side_key,
            side,
            fact_index,
            as_of_ms=as_of_ms,
            recheck_claims=recheck_claims,
        )
        return
    if protocol != "2.0":
        raise EvidenceFormatError(f"{side_key} persisted side protocol invalid")
    expected = LEGACY_MODEL_SIDE_FIELDS | {"status", "validation_reasons_cn"}
    if set(side) != expected:
        raise EvidenceFormatError(f"{side_key} persisted side shape invalid")
    for field_name in (
        "basis_cn",
        "market_counter_cn",
        "alternative_cn",
        "next_observation_cn",
        "invalid_if_cn",
    ):
        text = side.get(field_name)
        if not isinstance(text, str) or _human_text_issue(text):
            raise EvidenceFormatError(f"{side_key} human text invalid")
    unresolved = side.get("unresolved_conditions_cn")
    if not isinstance(unresolved, list) or any(
        not isinstance(item, str) or _human_text_issue(item) for item in unresolved
    ):
        raise EvidenceFormatError(f"{side_key} unresolved conditions invalid")
    evidence_refs, evidence_ok, evidence_reasons = _valid_refs(
        side.get("evidence_refs"), fact_index, as_of_ms=as_of_ms
    )
    counter_refs, counter_ok, counter_reasons = _valid_refs(
        side.get("counter_evidence_refs"), fact_index, as_of_ms=as_of_ms
    )
    if (
        not evidence_ok
        or not counter_ok
        or evidence_reasons
        or counter_reasons
        or evidence_refs != side.get("evidence_refs")
        or counter_refs != side.get("counter_evidence_refs")
    ):
        raise EvidenceFormatError(f"{side_key} evidence refs invalid")
    grade = side.get("grade")
    if side.get("status") == "RATED":
        if recheck_claims and _fact_assertion_issues(side, fact_index):
            raise EvidenceFormatError(f"{side_key} stated fact contradicts source")
        if grade is not None and not evidence_refs and not counter_refs:
            raise EvidenceFormatError(f"{side_key} rated side missing refs")
        if grade in ("B", "A", "S") and not evidence_refs:
            raise EvidenceFormatError(f"{side_key} support grade missing evidence")
        if grade in ("A", "S") and not _has_constraint_argument(
            {
                "basis_cn": side.get("basis_cn", ""),
                "market_counter_cn": side.get("market_counter_cn", ""),
                "alternative_cn": side.get("alternative_cn", ""),
            },
            evidence_refs,
            counter_refs,
            fact_index,
        ):
            raise EvidenceFormatError(f"{side_key} A/S constraint argument invalid")


def _validate_persisted_v21_side(
    side_key: str,
    side: dict[str, Any],
    fact_index: dict[str, dict[str, Any]],
    *,
    as_of_ms: int | float,
    recheck_claims=True,
) -> None:
    expected = (
        MODEL_SIDE_FIELDS_V21
        | {
            "status",
            "fit_thesis",
            "validation_reasons_cn",
            "evidence_refs",
            "counter_evidence_refs",
            "context_evidence_refs",
        }
    )
    if set(side) != expected:
        raise EvidenceFormatError(f"{side_key} persisted side shape invalid")
    if side.get("fit_thesis") != SIDE_FIT_THESES[side_key]:
        raise EvidenceFormatError(f"{side_key} fit_thesis invalid")
    for field_name in (
        "basis_cn",
        "mechanism_cn",
        "market_counter_cn",
        "alternative_cn",
        "next_observation_cn",
    ):
        text = side.get(field_name)
        if (
            not isinstance(text, str)
            or (recheck_claims and _v21_human_text_issue(text))
            or (not recheck_claims and _human_text_issue(text))
        ):
            raise EvidenceFormatError(f"{side_key} human text invalid")
    for field_name in ("strengthen_if_cn", "weaken_if_cn", "unresolved_conditions_cn"):
        values = side.get(field_name)
        if not isinstance(values, list) or any(
            not isinstance(item, str)
            or (recheck_claims and _v21_human_text_issue(item))
            or (not recheck_claims and _human_text_issue(item))
            for item in values
        ):
            raise EvidenceFormatError(f"{side_key} {field_name} invalid")
    validation_reasons = side.get("validation_reasons_cn")
    if not isinstance(validation_reasons, list) or any(
        not isinstance(item, str)
        or (recheck_claims and _v21_human_text_issue(item))
        or (not recheck_claims and _human_text_issue(item))
        for item in validation_reasons
    ):
        raise EvidenceFormatError(f"{side_key} validation reasons invalid")
    roles, role_refs, role_reasons = _normalize_evidence_roles(
        side_key,
        side.get("evidence_roles"),
        fact_index=fact_index,
        as_of_ms=as_of_ms,
    )
    if role_reasons or roles != side.get("evidence_roles"):
        raise EvidenceFormatError(f"{side_key} evidence roles invalid")
    if role_refs["supports_fit"] != side.get("evidence_refs"):
        raise EvidenceFormatError(f"{side_key} support refs mismatch")
    if role_refs["counters_fit"] != side.get("counter_evidence_refs"):
        raise EvidenceFormatError(f"{side_key} counter refs mismatch")
    if role_refs["context_only"] != side.get("context_evidence_refs"):
        raise EvidenceFormatError(f"{side_key} context refs mismatch")
    grade = side.get("grade")
    if side.get("status") == "RATED":
        if validation_reasons:
            raise EvidenceFormatError(f"{side_key} rated side has validation reasons")
        if recheck_claims and _fact_assertion_issues(side, fact_index):
            raise EvidenceFormatError(f"{side_key} stated fact contradicts source")
        if grade is not None and not roles:
            raise EvidenceFormatError(f"{side_key} rated side missing refs")
        if grade in ("B", "A", "S") and not side.get("evidence_refs"):
            raise EvidenceFormatError(f"{side_key} support grade missing evidence")
        if grade in ("A", "S") and not _has_constraint_argument(
            {
                "basis_cn": side.get("basis_cn", ""),
                "mechanism_cn": side.get("mechanism_cn", ""),
                "market_counter_cn": side.get("market_counter_cn", ""),
                "alternative_cn": side.get("alternative_cn", ""),
            },
            side.get("evidence_refs"),
            side.get("counter_evidence_refs"),
            fact_index,
        ):
            raise EvidenceFormatError(f"{side_key} A/S constraint argument invalid")


def _validate_persisted_v22_side(
    side_key: str,
    side: dict[str, Any],
    fact_index: dict[str, dict[str, Any]],
    *,
    as_of_ms: int | float,
    recheck_claims=True,
) -> None:
    expected = (
        MODEL_SIDE_FIELDS
        | {
            "status",
            "fit_thesis",
            "market_counter_cn",
            "validation_reasons_cn",
            "evidence_refs",
            "counter_evidence_refs",
            "context_evidence_refs",
        }
    )
    if set(side) != expected:
        raise EvidenceFormatError(f"{side_key} persisted side shape invalid")
    if side.get("fit_thesis") != SIDE_FIT_THESES[side_key]:
        raise EvidenceFormatError(f"{side_key} fit_thesis invalid")
    for field_name in (
        "basis_cn",
        "market_counter_cn",
        "alternative_cn",
        "next_observation_cn",
    ):
        text = side.get(field_name)
        if (
            not isinstance(text, str)
            or (recheck_claims and _v21_human_text_issue(text))
            or (not recheck_claims and _human_text_issue(text))
        ):
            raise EvidenceFormatError(f"{side_key} human text invalid")
    mechanism, mechanism_reasons = _normalize_mechanism(
        side.get("mechanism"),
        fact_index=fact_index,
        as_of_ms=as_of_ms,
    )
    if mechanism_reasons or mechanism != side.get("mechanism"):
        raise EvidenceFormatError(f"{side_key} mechanism invalid")
    for field_name in ("strengthen_if_cn", "weaken_if_cn", "unresolved_conditions_cn"):
        values = side.get(field_name)
        if not isinstance(values, list) or any(
            not isinstance(item, str)
            or (recheck_claims and _v21_human_text_issue(item))
            or (not recheck_claims and _human_text_issue(item))
            for item in values
        ):
            raise EvidenceFormatError(f"{side_key} {field_name} invalid")
    validation_reasons = side.get("validation_reasons_cn")
    if not isinstance(validation_reasons, list) or any(
        not isinstance(item, str)
        or (recheck_claims and _v21_human_text_issue(item))
        or (not recheck_claims and _human_text_issue(item))
        for item in validation_reasons
    ):
        raise EvidenceFormatError(f"{side_key} validation reasons invalid")
    roles, role_refs, role_reasons = _normalize_evidence_roles(
        side_key,
        side.get("evidence_roles"),
        fact_index=fact_index,
        as_of_ms=as_of_ms,
    )
    if role_reasons or roles != side.get("evidence_roles"):
        raise EvidenceFormatError(f"{side_key} evidence roles invalid")
    if role_refs["supports_fit"] != side.get("evidence_refs"):
        raise EvidenceFormatError(f"{side_key} support refs mismatch")
    if role_refs["counters_fit"] != side.get("counter_evidence_refs"):
        raise EvidenceFormatError(f"{side_key} counter refs mismatch")
    if role_refs["context_only"] != side.get("context_evidence_refs"):
        raise EvidenceFormatError(f"{side_key} context refs mismatch")
    primary_counter_ref, primary_reason = _normalize_primary_counter_ref(
        side.get("primary_counter_ref"),
        side.get("counter_evidence_refs"),
    )
    if primary_reason or primary_counter_ref != side.get("primary_counter_ref"):
        raise EvidenceFormatError(f"{side_key} primary counter invalid")
    if _market_counter_from_roles(roles, primary_counter_ref) != side.get("market_counter_cn"):
        raise EvidenceFormatError(f"{side_key} market counter derivation invalid")
    grade = side.get("grade")
    if side.get("status") == "RATED":
        if validation_reasons:
            raise EvidenceFormatError(f"{side_key} rated side has validation reasons")
        if recheck_claims and _fact_assertion_issues(side, fact_index):
            raise EvidenceFormatError(f"{side_key} stated fact contradicts source")
        if grade is not None and not roles:
            raise EvidenceFormatError(f"{side_key} rated side missing refs")
        if grade in ("B", "A", "S") and not side.get("evidence_refs"):
            raise EvidenceFormatError(f"{side_key} support grade missing evidence")
        if grade in ("A", "S") and not _has_v22_mechanism_argument(
            mechanism,
            side.get("evidence_refs"),
            fact_index,
        ):
            raise EvidenceFormatError(f"{side_key} A/S mechanism argument invalid")


def _validate_persisted_side_comparison(
    comparison: dict[str, Any],
    *,
    sides: dict[str, dict[str, Any]],
    fact_index: dict[str, dict[str, Any]],
    as_of_ms: int | float,
    recheck_claims=True,
) -> None:
    expected = MODEL_COMPARISON_FIELDS | {"status", "validation_reasons_cn"}
    if set(comparison) != expected:
        raise EvidenceFormatError("side_comparison shape invalid")
    if comparison.get("status") not in COMPARISON_STATUS:
        raise EvidenceFormatError("side_comparison status invalid")
    if comparison.get("relative_side") not in COMPARISON_SIDES:
        raise EvidenceFormatError("side_comparison relative_side invalid")
    for field_name in ("basis_cn", "flip_if_cn"):
        text = comparison.get(field_name)
        if not isinstance(text, str):
            raise EvidenceFormatError("side_comparison human text invalid")
        if recheck_claims and _v21_human_text_issue(text):
            raise EvidenceFormatError("side_comparison human text invalid")
    validation_reasons = comparison.get("validation_reasons_cn")
    if not isinstance(validation_reasons, list):
        raise EvidenceFormatError("side_comparison validation reasons invalid")
    if recheck_claims and any(
        not isinstance(item, str) or _v21_human_text_issue(item) for item in validation_reasons
    ):
        raise EvidenceFormatError("side_comparison validation reasons invalid")
    if not recheck_claims and any(not isinstance(item, str) for item in validation_reasons):
        raise EvidenceFormatError("side_comparison validation reasons invalid")
    refs, refs_ok, ref_reasons = _valid_refs(
        comparison.get("evidence_refs"), fact_index, as_of_ms=as_of_ms
    )
    if not refs_ok or ref_reasons or refs != comparison.get("evidence_refs"):
        raise EvidenceFormatError("side_comparison refs invalid")
    assertion_reasons = (
        [
            "两侧比较" + reason
            for reason in _fact_assertion_issues(
                {
                    "basis_cn": comparison.get("basis_cn", ""),
                    "flip_if_cn": comparison.get("flip_if_cn", ""),
                },
                fact_index,
            )
        ]
        if recheck_claims
        else []
    )
    if comparison.get("status") == "ASSESSED":
        if assertion_reasons:
            raise EvidenceFormatError("side_comparison stated fact contradicts source")
        if validation_reasons:
            raise EvidenceFormatError("assessed side_comparison has validation reasons")
        if comparison.get("relative_side") == "not_comparable":
            raise EvidenceFormatError("assessed side_comparison cannot be not_comparable")
        if not refs:
            raise EvidenceFormatError("assessed side_comparison missing refs")
        issue = _comparison_order_issue(comparison.get("relative_side"), sides)
        if issue:
            raise EvidenceFormatError("side_comparison order invalid")
    elif assertion_reasons and any(reason not in validation_reasons for reason in assertion_reasons):
        raise EvidenceFormatError("side_comparison validation reasons incomplete")


def _validate_persisted_advisory_guidance(
    guidance: dict[str, Any],
    fact_index: dict[str, dict[str, Any]],
    *,
    as_of_ms: int | float,
    recheck_claims=True,
) -> None:
    expected = MODEL_ADVISORY_GUIDANCE_FIELDS | {
        "schema",
        "status",
        "validation_reasons_cn",
    }
    if set(guidance) != expected:
        raise EvidenceFormatError("advisory_guidance shape invalid")
    if guidance.get("schema") != ADVISORY_GUIDANCE_SCHEMA_VERSION:
        raise EvidenceFormatError("advisory_guidance schema invalid")
    if guidance.get("status") not in ("ASSESSED", "UNAVAILABLE"):
        raise EvidenceFormatError("advisory_guidance status invalid")
    normalized = _normalize_advisory_guidance(
        {
            "summary_cn": guidance.get("summary_cn"),
            "tradeoffs_cn": guidance.get("tradeoffs_cn"),
            "evidence_refs": guidance.get("evidence_refs"),
            "outlooks": guidance.get("outlooks"),
        },
        fact_index=fact_index,
        as_of_ms=as_of_ms,
    )
    if recheck_claims:
        normalized_without_status = {
            key: normalized.get(key)
            for key in MODEL_ADVISORY_GUIDANCE_FIELDS
        }
        guidance_without_status = {
            key: guidance.get(key)
            for key in MODEL_ADVISORY_GUIDANCE_FIELDS
        }
        if normalized_without_status != guidance_without_status:
            raise EvidenceFormatError("advisory_guidance content invalid")
    validation_reasons = guidance.get("validation_reasons_cn")
    if not isinstance(validation_reasons, list) or any(
        not isinstance(item, str) or _human_text_issue(item) for item in validation_reasons
    ):
        raise EvidenceFormatError("advisory_guidance validation reasons invalid")
    if guidance.get("status") == "ASSESSED":
        if validation_reasons:
            raise EvidenceFormatError("assessed advisory_guidance has validation reasons")
        if normalized.get("status") != "ASSESSED":
            raise EvidenceFormatError("advisory_guidance assessed content invalid")
    elif not validation_reasons:
        raise EvidenceFormatError("unavailable advisory_guidance missing reasons")


def _validate_persisted_price_bias(
    price_bias: dict[str, Any],
    fact_index: dict[str, dict[str, Any]],
    *,
    as_of_ms: int | float,
    recheck_claims=True,
) -> None:
    expected = MODEL_PRICE_BIAS_FIELDS | {
        "schema",
        "status",
        "validation_reasons_cn",
    }
    if set(price_bias) != expected:
        raise EvidenceFormatError("price_bias shape invalid")
    if price_bias.get("schema") != PRICE_BIAS_SCHEMA_VERSION:
        raise EvidenceFormatError("price_bias schema invalid")
    if price_bias.get("status") not in PRICE_BIAS_STATUS:
        raise EvidenceFormatError("price_bias status invalid")
    bias = price_bias.get("bias")
    if bias not in PRICE_BIASES:
        raise EvidenceFormatError("price_bias bias invalid")
    if price_bias.get("status") == "UNAVAILABLE" and bias != "UNDETERMINED":
        raise EvidenceFormatError("unavailable price_bias must be undetermined")
    for field_name in ("basis_cn", "counter_cn", "invalid_if_cn"):
        text = price_bias.get(field_name)
        if not isinstance(text, str) or _human_text_issue(text):
            raise EvidenceFormatError("price_bias human text invalid")
    validation_reasons = price_bias.get("validation_reasons_cn")
    if not isinstance(validation_reasons, list) or any(
        not isinstance(item, str) or _human_text_issue(item)
        for item in validation_reasons
    ):
        raise EvidenceFormatError("price_bias validation reasons invalid")
    evidence_refs, evidence_ok, evidence_reasons = _valid_refs(
        price_bias.get("evidence_refs"), fact_index, as_of_ms=as_of_ms
    )
    counter_refs, counter_ok, counter_reasons = _valid_refs(
        price_bias.get("counter_evidence_refs"), fact_index, as_of_ms=as_of_ms
    )
    if (
        not evidence_ok
        or not counter_ok
        or evidence_reasons
        or counter_reasons
        or evidence_refs != price_bias.get("evidence_refs")
        or counter_refs != price_bias.get("counter_evidence_refs")
    ):
        raise EvidenceFormatError("price_bias evidence refs invalid")
    if price_bias.get("status") == "ASSESSED":
        if validation_reasons:
            raise EvidenceFormatError("assessed price_bias has validation reasons")
        if recheck_claims and _fact_assertion_issues(price_bias, fact_index):
            raise EvidenceFormatError("price_bias stated fact contradicts source")
        if bias != "UNDETERMINED" and not evidence_refs and not counter_refs:
            raise EvidenceFormatError("assessed price_bias missing refs")
        if bias != "UNDETERMINED" and not _has_price_bias_signal_argument(
            {
                "basis_cn": price_bias.get("basis_cn", ""),
                "counter_cn": price_bias.get("counter_cn", ""),
                "invalid_if_cn": price_bias.get("invalid_if_cn", ""),
            },
            evidence_refs,
            counter_refs,
            fact_index,
        ):
            raise EvidenceFormatError("price_bias signal argument invalid")


def _default_side(
    *,
    side_key: str | None = None,
    protocol: str = "2.0",
    status: str = "UNRATED",
    grade: str | None = None,
    basis_cn: str = "本侧暂未形成有效证据等级。",
    validation_reasons_cn: list[str] | None = None,
) -> dict[str, Any]:
    if protocol == "2.2":
        expected_thesis = SIDE_FIT_THESES.get(str(side_key or ""), "")
        return {
            "status": status,
            "grade": grade,
            "fit_thesis": expected_thesis,
            "basis_cn": basis_cn,
            "mechanism": {
                "summary_cn": "本侧适配机制暂未形成有效说明。",
                "refs": [],
            },
            "primary_counter_ref": None,
            "market_counter_cn": "主要反证暂未形成有效说明。",
            "alternative_cn": "竞争解释暂未形成有效说明。",
            "next_observation_cn": "继续观察关键市场事实变化。",
            "strengthen_if_cn": [],
            "weaken_if_cn": ["补齐有效评级后重新判断。"],
            "evidence_roles": [],
            "evidence_refs": [],
            "counter_evidence_refs": [],
            "context_evidence_refs": [],
            "unresolved_conditions_cn": [],
            "validation_reasons_cn": list(validation_reasons_cn or []),
        }
    if protocol == "2.1":
        expected_thesis = SIDE_FIT_THESES.get(str(side_key or ""), "")
        return {
            "status": status,
            "grade": grade,
            "fit_thesis": expected_thesis,
            "basis_cn": basis_cn,
            "mechanism_cn": "本侧适配机制暂未形成有效说明。",
            "market_counter_cn": "主要反证暂未形成有效说明。",
            "alternative_cn": "竞争解释暂未形成有效说明。",
            "next_observation_cn": "继续观察关键市场事实变化。",
            "strengthen_if_cn": [],
            "weaken_if_cn": ["补齐有效评级后重新判断。"],
            "evidence_roles": [],
            "evidence_refs": [],
            "counter_evidence_refs": [],
            "context_evidence_refs": [],
            "unresolved_conditions_cn": [],
            "validation_reasons_cn": list(validation_reasons_cn or []),
        }
    return {
        "status": status,
        "grade": grade,
        "basis_cn": basis_cn,
        "market_counter_cn": "主要反证暂未形成有效说明。",
        "alternative_cn": "竞争解释暂未形成有效说明。",
        "next_observation_cn": "继续观察关键市场事实变化。",
        "invalid_if_cn": "关键事实失效时需要重新评级。",
        "evidence_refs": [],
        "counter_evidence_refs": [],
        "unresolved_conditions_cn": [],
        "validation_reasons_cn": list(validation_reasons_cn or []),
    }


def _default_price_bias(
    *,
    validation_reasons_cn: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": PRICE_BIAS_SCHEMA_VERSION,
        "status": "UNAVAILABLE",
        "bias": "UNDETERMINED",
        "basis_cn": "价格方向复核暂未完成。",
        "counter_cn": "价格方向反证暂未形成有效说明。",
        "invalid_if_cn": "补齐有效方向复核后重新判断。",
        "evidence_refs": [],
        "counter_evidence_refs": [],
        "validation_reasons_cn": list(validation_reasons_cn or []),
    }


def _clean_text(value: Any, *, fallback: str, limit: int = 360) -> str:
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.strip().split())
    if not text:
        return fallback
    return text[:limit]


def _clean_text_list(
    value: Any,
    *,
    limit: int,
    fallback_item: str,
) -> tuple[list[str], bool]:
    if value is None:
        return [], False
    if not isinstance(value, list):
        return [], False
    items: list[str] = []
    for item in value[:limit]:
        if not isinstance(item, str) or not item.strip():
            return items, False
        cleaned = _clean_text(item, fallback=fallback_item)
        if cleaned:
            items.append(cleaned)
    return items, True


def _human_text_issue(text: str) -> str:
    for pattern in _MACHINE_TEXT_PATTERNS:
        if pattern.search(text):
            return "说明包含原始字段、内部枚举或机器标识。"
    if _FORBIDDEN_TRADE_TEXT_RE.search(text):
        return "说明包含本层不评价的交易合约或执行参数。"
    if _PROBABILITY_TEXT_RE.search(text):
        return "说明把评级写成了未经校准的概率或胜率。"
    if _FUTURE_PROOF_RE.search(text):
        return "说明把未来路径当作本卡评级证明。"
    return ""


def _v21_human_text_issue(text: str) -> str:
    issue = _human_text_issue(text)
    if issue:
        return issue
    if _INTERNAL_IDENTIFIER_RE.search(text):
        return "说明包含内部侧别、角色或命题标识。"
    return ""


def _side_label(side_key: str) -> str:
    return "Put 侧" if side_key == "put_credit" else "Call 侧"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")
