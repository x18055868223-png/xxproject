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
OUTPUT_SCHEMA_VERSION = "signal_llm_review@2.0.0"
LEGACY_PROMPT_VERSION = "signal_llm_review_prompt@2.0.0"
PROMPT_VERSION = "signal_llm_review_prompt@2.0.1"
MAIN_PROMPT_VERSION = PROMPT_VERSION
REVIEW_MODE = "single_evidence_v2"
PACKET_SCHEMA_VERSION = "signal_evidence_packet@2.0.0"
SUMMARY_SCHEMA_VERSION = "signal_evidence_summary@2.0.0"
PRICE_BIAS_SCHEMA_VERSION = "price_bias@1.0.0"

SIDE_KEYS = ("put_credit", "call_credit")
GRADES = ("D", "C", "B", "A", "S")
SIDE_STATUS = ("RATED", "UNRATED")
REVIEW_STATUS = ("OK", "PARTIAL", "ERROR")
ACCEPTED_PROMPT_VERSIONS = (LEGACY_PROMPT_VERSION, PROMPT_VERSION)
PRICE_BIASES = ("BULLISH", "BEARISH", "NEUTRAL", "MIXED", "UNDETERMINED")
PRICE_BIAS_STATUS = ("ASSESSED", "UNAVAILABLE")

MODEL_SIDE_FIELDS = {
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
MODEL_PRICE_BIAS_FIELDS = {
    "bias",
    "basis_cn",
    "counter_cn",
    "invalid_if_cn",
    "evidence_refs",
    "counter_evidence_refs",
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
        "信号环境更有依据，以及什么变化会"
        "推翻判断。评级 D/C/B/A/S 表示总体证据等级，不表示胜率、收益概率或价格"
        "会单向移动的强弱。末日垂直信用价差首先关注空间约束、不利侧侵入、压力与"
        "价格响应的关系，再解释倾向性。Put 侧关注下行侵入压力，Call 侧关注上行"
        "侵入压力。你可以使用定性贝叶斯式证据更新来比较支持解释和竞争解释，但"
        "不得输出未经校准的概率、胜率或后验百分比。S 不要求新增来源；它只能表示当前"
        "事实之间的关系让关键替代解释更难成立。"
    )
    user_prompt = (
        f"{mode_line}\n\n"
        "只返回一个 JSON 对象，且顶层只能包含 side_evidence_ratings 和 price_bias。"
        "两侧必须是 put_credit 和 call_credit。每侧只填写 grade、basis_cn、"
        "market_counter_cn、alternative_cn、next_observation_cn、invalid_if_cn、"
        "evidence_refs、counter_evidence_refs、unresolved_conditions_cn。"
        "price_bias 只填写 bias、basis_cn、counter_cn、invalid_if_cn、"
        "evidence_refs、counter_evidence_refs；bias 只能是 BULLISH、BEARISH、"
        "NEUTRAL、MIXED 或 UNDETERMINED。\n\n"
        "引用规则：evidence_refs 与 counter_evidence_refs 只能使用输入 facts 中的 id；"
        "事实必须 usable=true 且 observed_at_ms 不晚于 identity.as_of_ms。未知或非投票"
        "事实不是自动反对。OHLC 代理不能证明路径先后顺序，未来行情不能作为本卡升级"
        "依据。\n\n"
        "中文输出规则：所有中文字段只能写交易员可读的市场事实、推理和反证；不得写原始"
        "字段路径、内部枚举、schema/hash、公式、权重、执行许可、下单参数、具体行权价、"
        "具体报价、仓位、胜率、概率、后验百分比或未来路径证明。\n\n"
        "等级口径：D=有效事实总体反对该侧适配解释；C=可以判断但无明显支持优势；"
        "B=已有值得关注的支持且竞争解释仍有实质分量；A=整体论证较强，适配解释更有依据；"
        "S=整体支持很强，关键替代解释更难成立；null=必要证据或有效判断缺失。\n\n"
        "价格方向口径：price_bias 是对标的价格倾向的独立结论，必须明确偏多、偏空、"
        "中性、混合或无法判断；它不能替代两侧价差证据等级，也不能改变本地行动边界。"
        "方向判断要先解释空间约束和不利侧推进，再解释倾向性；方向压力强不等于垂直"
        "信用价差更适配。\n\n"
        "字段类型：grade为单个字母或null；五个说明字段必须是字符串；evidence_refs、"
        "counter_evidence_refs、unresolved_conditions_cn必须是字符串数组，没有条目时返回[]。"
        "price_bias 的三个说明字段必须是字符串，两个引用字段必须是字符串数组。"
        "不要将未解条件数组合成一段字符串。\n"
        "事实口径：宏观逆风刻度正值为风险资产逆风、负值为顺风；不把负数当下跌方向。"
        "墙位、锚带只是结构参照，距离本身不能证明承接；越过翻转点不单独证明全局净Gamma变号。"
        "明确区分成交窗口和价格窗口，终点变化不能证明期间持续单向推进。\n\n"
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
    raw_ratings, raw_price_bias = _extract_model_payload(payload)
    fact_index = _fact_index(canonical_packet)
    needs_price_bias = require_price_bias or prompt_version == PROMPT_VERSION
    raw_sides = [raw_ratings.get(key) for key in SIDE_KEYS]
    if all(isinstance(side, dict) and not isinstance(side.get("unresolved_conditions_cn"), list)
           for side in raw_sides):
        refs = [ref for side in raw_sides for field in ("evidence_refs", "counter_evidence_refs")
                for ref in (side.get(field) if isinstance(side.get(field), list) else [])]
        # A shared structural output error may recover once. Fabricated refs
        # remain semantic errors and never obtain a new model attempt.
        if all(isinstance(ref, str) and ref in fact_index for ref in refs):
            raise EvidenceFormatError("unresolved conditions must be arrays")
    sides = {
        side_key: _normalize_side(
            side_key,
            raw_ratings.get(side_key),
            fact_index=fact_index,
            as_of_ms=_packet_as_of_ms(canonical_packet),
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
    action_state = _build_local_action_state(card, sides)
    validation_reasons = [
        reason
        for side in SIDE_KEYS
        for reason in sides[side]["validation_reasons_cn"]
    ]
    if price_bias is not None:
        validation_reasons.extend(price_bias["validation_reasons_cn"])
    rated_count = sum(sides[key]["status"] == "RATED" for key in SIDE_KEYS)
    status = "OK" if rated_count == 2 else "PARTIAL" if rated_count else "ERROR"
    if price_bias is not None and price_bias["status"] != "ASSESSED" and rated_count:
        status = "PARTIAL"
    advisory = {
        "side_evidence_ratings": sides,
        "local_action_state": action_state,
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
    persisted_prompt_version = prompt_version or (
        PROMPT_VERSION if price_bias is not None else LEGACY_PROMPT_VERSION
    )
    advisory["validation"]["assessment_hash"] = _assessment_hash(advisory)
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "prompt_version": persisted_prompt_version,
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
    needs_price_bias = require_price_bias or prompt_version == PROMPT_VERSION
    sides = {
        side_key: _default_side(
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
    persisted_prompt_version = prompt_version or (
        PROMPT_VERSION if needs_price_bias else LEGACY_PROMPT_VERSION
    )
    advisory["validation"]["assessment_hash"] = _assessment_hash(advisory)
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "prompt_version": persisted_prompt_version,
        "review_mode": REVIEW_MODE,
        "status": "ERROR",
        "model": model,
        "provider": PROVIDER,
        "reviewed_at": reviewed_at or _now_iso(),
        "input_packet_hash": _packet_hash(canonical_packet),
        "evidence_context": _evidence_context(canonical_packet),
        "integrated_trade_advisory": advisory,
    }


def build_summary(review: dict[str, Any]) -> dict[str, Any]:
    """Build the compact materializer summary for a validated v2 review."""

    validation = validate_persisted_review(review)
    advisory = _as_dict(review.get("integrated_trade_advisory"))
    sides = _as_dict(advisory.get("side_evidence_ratings"))
    context = _as_dict(review.get("evidence_context"))
    identity = _as_dict(context.get("identity"))
    summary = {
        "schema": SUMMARY_SCHEMA_VERSION,
        "put_credit": _summary_side(sides.get("put_credit")),
        "call_credit": _summary_side(sides.get("call_credit")),
        "local_action_state": _clone(advisory.get("local_action_state")),
        "action_summary_cn": str(advisory.get("action_summary_cn") or ""),
        "as_of_ms": identity.get("as_of_ms"),
        "input_packet_hash": review.get("input_packet_hash"),
        "assessment_hash": validation["assessment_hash"],
    }
    if "price_bias" in advisory:
        summary["price_bias"] = _summary_price_bias(advisory.get("price_bias"))
    return summary


def validate_persisted_review(review: dict[str, Any], *, recheck_claims=True) -> dict[str, Any]:
    """Validate a stored v2 review without needing the source card.

    The caller can still perform stronger checks against the source record hash
    and recompute the local action state from the current card.
    """

    if not isinstance(review, dict):
        raise EvidenceFormatError("review must be object")
    if review.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise EvidenceFormatError("invalid review schema_version")
    prompt_version = review.get("prompt_version")
    if prompt_version not in ACCEPTED_PROMPT_VERSIONS:
        raise EvidenceFormatError("invalid prompt_version")
    if review.get("review_mode") != REVIEW_MODE:
        raise EvidenceFormatError("invalid review_mode")
    if review.get("status") not in REVIEW_STATUS:
        raise EvidenceFormatError("invalid review status")
    context = _as_dict(review.get("evidence_context"))
    advisory = _as_dict(review.get("integrated_trade_advisory"))
    if not context or not advisory:
        raise EvidenceFormatError("review missing evidence_context or advisory")
    if context.get("schema") != PACKET_SCHEMA_VERSION:
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
                                 recheck_claims=recheck_claims)
    has_price_bias = "price_bias" in advisory
    if prompt_version == PROMPT_VERSION and not has_price_bias:
        raise EvidenceFormatError("price_bias missing for prompt 2.0.1")
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
    result["source_identity_ok"] = True
    result["local_action_state_ok"] = True
    return result


def response_schema() -> dict[str, Any]:
    side_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(MODEL_SIDE_FIELDS),
        "properties": {
            "grade": {"enum": ["D", "C", "B", "A", "S", None]},
            "basis_cn": {"type": "string"},
            "market_counter_cn": {"type": "string"},
            "alternative_cn": {"type": "string"},
            "next_observation_cn": {"type": "string"},
            "invalid_if_cn": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "counter_evidence_refs": {"type": "array", "items": {"type": "string"}},
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
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["side_evidence_ratings", "price_bias"],
        "properties": {
            "side_evidence_ratings": {
                "type": "object",
                "additionalProperties": False,
                "required": list(SIDE_KEYS),
                "properties": {side_key: side_schema for side_key in SIDE_KEYS},
            },
            "price_bias": price_bias_schema,
        },
    }


def _extract_model_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    if not isinstance(payload, dict):
        raise EvidenceFormatError("model payload must be object")
    if not set(payload).issubset({"side_evidence_ratings", "price_bias"}):
        raise EvidenceFormatError("model payload top-level shape invalid")
    ratings = payload.get("side_evidence_ratings")
    if not isinstance(ratings, dict):
        raise EvidenceFormatError("side_evidence_ratings must be object")
    if not any(side in ratings for side in SIDE_KEYS):
        raise EvidenceFormatError("side_evidence_ratings has no known sides")
    return ratings, payload.get("price_bias")


def _normalize_side(
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
    if set(raw_side) != MODEL_SIDE_FIELDS:
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
        text_fields.get(key, "")
        for key in ("basis_cn", "market_counter_cn", "counter_cn")
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
        text_fields.get("basis_cn", ""),
        text_fields.get("market_counter_cn", ""),
        text_fields.get("alternative_cn", ""),
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
        return "当前没有一侧进入人工准备；B 级侧启动关注，C 级侧作为普通观察。"
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
    if schema != PACKET_SCHEMA_VERSION:
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
        "basis_cn": str(bias.get("basis_cn") or ""),
    }


def _validate_persisted_side(
    side_key: str,
    side: dict[str, Any],
    fact_index: dict[str, dict[str, Any]],
    *,
    as_of_ms: int | float,
    recheck_claims=True,
) -> None:
    expected = MODEL_SIDE_FIELDS | {"status", "validation_reasons_cn"}
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
    status: str = "UNRATED",
    grade: str | None = None,
    basis_cn: str = "本侧暂未形成有效证据等级。",
    validation_reasons_cn: list[str] | None = None,
) -> dict[str, Any]:
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


def _side_label(side_key: str) -> str:
    return "Put 侧" if side_key == "put_credit" else "Call 侧"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")
