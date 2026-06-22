#!/usr/bin/env python3
"""Generate sidecar LLM reviews for signal audit cards with Gemini.

This tool is intentionally outside the FMZ signal loop. It reads the local
signal_review.jsonl output, asks Gemini for an audit-only review, and writes a
small sidecar JSONL that the static materializer can merge into card JSON.
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request


DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_REVIEWS = "signal_llm_reviews.jsonl"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OUTPUT_SCHEMA_VERSION = "signal_llm_review@1.3.0"
PROMPT_VERSION = "gemini_signal_review_prompt@1.3.0"
PACKET_VERSION = "signal_llm_review_packet@1.0.0"
BLIND_PACKET_VERSION = "signal_llm_blind_theoretical_packet@1.1.0"

FACTOR_KEYS = (
    "tmvf",
    "micro_flow",
    "macro_pressure",
    "gamma_regime",
    "gex_info",
    "skew",
    "funding",
)
SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|apikey|authorization|"
    r"bearer|cookie|account|balance|position|order|private|credential)",
    re.IGNORECASE,
)
SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\[^,\]\}\n\r\t ]+"),
    re.compile(r"/(?:home|opt|var|etc|root|Users)/[^,\]\}\n\r\t ]+"),
)
REQUIRED_REVIEW_FIELDS = (
    "summary_cn",
    "agreement_with_system",
    "caution_level",
    "theoretical_active_view",
    "gamma_regime_lens",
    "main_supporting_factors",
    "main_risks_or_conflicts",
    "operator_focus",
    "invalid_if",
    "not_trading_advice",
)
REQUIRED_BLIND_FIELDS = ("theoretical_active_view", "gamma_regime_lens")
THEORETICAL_ACTIVE_BIASES = {
    "BULLISH_LEAN",
    "BEARISH_LEAN",
    "NEUTRAL_OR_RANGE",
    "MIXED_UNCLEAR",
    "UNABLE_TO_JUDGE",
}
THEORETICAL_ACTIVE_CONVICTIONS = {"LOW", "MEDIUM", "HIGH"}
GAMMA_LENS_REGIMES = {
    "LONG_GAMMA_STABILIZING",
    "SHORT_GAMMA_AMPLIFYING",
    "NEAR_FLIP_UNSTABLE",
    "UNKNOWN",
}
GAMMA_LENS_EXTREMITIES = {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}
GAMMA_LENS_EFFECTS = {"NEUTRALIZE", "LOWER", "NEUTRAL", "UNKNOWN"}


def build_review_packet(card):
    identity = _as_dict(card.get("identity"))
    factor = _as_dict(card.get("factor_cross_section"))
    selected_factors = {
        key: _safe_copy(factor.get(key))
        for key in FACTOR_KEYS
        if key in factor
    }
    packet = {
        "schema": {
            "name": PACKET_VERSION,
            "source_card_schema": _safe_copy(card.get("schema")),
        },
        "identity": _safe_copy({
            "card_id": identity.get("card_id") or card.get("card_id"),
            "symbol": identity.get("symbol") or card.get("symbol"),
            "confirmed_at": identity.get("confirmed_at") or card.get("created_at"),
            "strategy_name": identity.get("strategy_name"),
            "strategy_version": identity.get("strategy_version"),
        }),
        "market_context": _safe_copy(card.get("market_context")),
        "decision": _safe_copy(card.get("decision")),
        "signal_window": _safe_copy(card.get("signal_window")),
        "reasoning": _safe_copy(card.get("reasoning")),
        "conflict": _safe_copy(card.get("conflict")),
        "blocking": _safe_copy(card.get("blocking")),
        "quality": _safe_copy(card.get("quality")),
        "factor_cross_section": selected_factors,
        "field_glossary": {
            "confidence": "证据质量刻度，不是胜率或收益概率。",
            "evidence_strength": "程序化证据强度，反映信号截面证据充分程度。",
            "conflict.ratio": "有效反向证据占比，越高越需要谨慎解释。",
            "gex_info.rank": "GEX Monitor 历史窗口内的百分位，warming_up 表示样本仍不足。",
            "trade_allowed": "系统交易许可字段，LLM 不得修改或推导执行动作。",
            "theoretical_active_view": (
                "LLM 基于给定截面做出的理论主动倾向参考，不是系统信号、下单指令或门控。"
            ),
        },
        "guardrails": {
            "role": "AUDIT_ADVISORY_ONLY",
            "do_not_change_system_decision": True,
            "do_not_recompute_weights": True,
            "do_not_use_external_market_data": True,
            "not_trading_advice": True,
        },
    }
    return _safe_copy(packet)


def build_blind_theoretical_packet(packet):
    source = _as_dict(packet)
    factor = _as_dict(source.get("factor_cross_section"))
    selected_factors = {
        key: _safe_copy(factor.get(key))
        for key in FACTOR_KEYS
        if key in factor
    }
    identity = _as_dict(source.get("identity"))
    blind = {
        "schema": {
            "name": BLIND_PACKET_VERSION,
            "source_packet_schema": _safe_copy(_as_dict(source.get("schema")).get("name")),
            "strict_blind_input": True,
            "independence_mode": "two_call_first_pass_true_blind",
        },
        "identity": _safe_copy({
            "card_id": identity.get("card_id"),
            "symbol": identity.get("symbol"),
            "confirmed_at": identity.get("confirmed_at"),
            "strategy_name": identity.get("strategy_name"),
            "strategy_version": identity.get("strategy_version"),
        }),
        "market_context": _safe_copy(source.get("market_context")),
        "quality": _safe_copy(source.get("quality")),
        "factor_cross_section": selected_factors,
        "field_glossary": {
            "theoretical_active_view": (
                "只基于本盲读包形成的理论倾向参考；不是系统信号、不是交易许可。"
            ),
            "gamma_regime_lens": (
                "Gamma 体制只分析分布、尾部和反身性风险；不能给竞争性方向。"
            ),
            "gex_info.rank": "GEX Monitor 历史窗口内百分位；warming_up 表示样本仍不足。",
        },
        "guardrails": {
            "role": "BLIND_THEORETICAL_READING",
            "do_not_infer_system_conclusion": True,
            "do_not_recompute_weights": True,
            "do_not_use_external_market_data": True,
            "not_trading_advice": True,
        },
    }
    return _safe_copy(blind)


def build_blind_prompt(packet):
    blind_packet = build_blind_theoretical_packet(packet)
    return (
        "你是交易信号审计复核员，只做审计增强，不是交易执行系统。\n"
        "这是两次调用真盲审的第一阶段。你只能依据 BLIND_THEORETICAL_PACKET "
        "形成 theoretical_active_view 与 gamma_regime_lens，输出中文 JSON。\n"
        "你看不到系统结论、窗口、阻断、交易许可或 EDB 结论，"
        "不得猜测这些系统结论。\n\n"
        "边界：\n"
        "1. 不得重算模型权重，不得用单一因子覆盖系统结论。\n"
        "2. 不得编造外部实时行情、盘口、新闻或未提供的数据。\n"
        "3. 先检查数据质量、缺失字段、rank 冷启动，再解释理论倾向。\n"
        "4. 不得给出开仓、平仓、仓位、杠杆、止损止盈、下单价格等交易执行建议。\n"
        "5. 你必须输出 theoretical_active_view：这是基于市场微结构、量价、Gamma/GEX、"
        "宏观、偏斜、资金费率等理论关系的主动倾向参考；它不是系统信号，不改变系统结论、"
        "阻断或交易许可。\n"
        "6. theoretical_active_view 可以给出倾向，但必须言之有物：至少引用两类给定因子，"
        "同时列出反证或不确定性；证据不足时选择 MIXED_UNCLEAR 或 UNABLE_TO_JUDGE。\n"
        "7. 你必须输出 gamma_regime_lens：它只分析 Gamma 体制对分布、尾部和反身性的"
        "风险叠加，绝不产出竞争性方向；只能降低/中和方向倾向的把握度，不能翻转方向。\n"
        "8. 正 Gamma → 预期波动压制、均值回归、向 pin/call/put 大额行权价钉住；"
        "负 Gamma → 预期波动放大、助涨助跌、两个方向都可能剧烈反身。\n"
        "9. 极端负 Gamma + 已有方向倾向时，必须提示催化剂导致反向挤压/剧烈反转的尾部风险，"
        "并点名 flip / 对侧 wall / pin 等关键位。\n"
        "10. 如果 flip/GEX 缺失、rank warming_up 或符号假设不稳，gamma_regime_lens.regime 选 UNKNOWN，"
        "不得臆断体制。\n"
        "11. GEX 符号在加密市场是持仓假设，不是测得事实；必须在 positioning_assumption_cn 中说明。\n"
        "12. 不得把 theoretical_active_view 或 gamma_regime_lens 写成开仓建议、收益预测或对系统模型的重算。\n"
        "13. 只输出 JSON，不要 markdown，不要额外解释。\n\n"
        "theoretical_active_view 字段要求：\n"
        "- bias 枚举：BULLISH_LEAN / BEARISH_LEAN / NEUTRAL_OR_RANGE / MIXED_UNCLEAR / UNABLE_TO_JUDGE。\n"
        "- conviction 枚举：LOW / MEDIUM / HIGH，表示该参考视角的定性把握度，不是胜率。\n"
        "- basis_cn: 用一句中文解释理论倾向，必须包含依据与不确定性。\n"
        "- key_drivers: 支持该理论倾向的关键因子。\n"
        "- counter_evidence: 反向证据、缺失数据或冷启动问题。\n"
        "- boundary_cn: 固定说明该判断只作审计参考，不改变系统信号、门控、置信或交易许可。\n\n"
        "gamma_regime_lens 字段要求：\n"
        "- regime 枚举：LONG_GAMMA_STABILIZING / SHORT_GAMMA_AMPLIFYING / NEAR_FLIP_UNSTABLE / UNKNOWN。\n"
        "- regime_extremity 枚举：LOW / MEDIUM / HIGH / UNKNOWN，主要依据 |GEX| 的 abs_rank_pct、regime_strength 与数据质量。\n"
        "- dynamics_cn: 说明当前 Gamma 体制预期动力学，例如压制钉住、放大反身或 flip 附近切换。\n"
        "- dominant_tail_risk_cn: 说明最主要尾部风险，不得写成交易方向。\n"
        "- conviction_effect_on_directional_view 枚举：NEUTRALIZE / LOWER / NEUTRAL / UNKNOWN；只降不升。\n"
        "- key_levels: 写入 flip/call_wall/put_wall/pin 等关键位，缺失则用 null。\n"
        "- positioning_assumption_cn: 显式声明 GEX 符号/做市商持仓假设及加密市场不确定性。\n"
        "- data_quality_cn: 说明 GEX/flip/rank 冷启动或缺失带来的可靠性限制。\n"
        "- lens_is_risk_overlay_not_direction: 必须为 true。\n\n"
        "BLIND_THEORETICAL_PACKET JSON：\n"
        f"{json.dumps(blind_packet, ensure_ascii=False, sort_keys=True)}"
    )


def build_full_review_prompt(packet, blind_payload):
    blind_payload = _validate_blind_payload(blind_payload)
    return (
        "你是交易信号审计复核员，只做审计增强，不是交易执行系统。\n"
        "这是两次调用真盲审的第二阶段。BLIND_REVIEW_RESULT 是第一阶段在看不到"
        "系统结论时形成的理论主动视角；你现在可以读取 FULL_AUDIT_PACKET 做复核，"
        "但不得修改系统 decision、置信度、EDB、blocking、trade_allowed 或下一步动作。\n\n"
        "边界：\n"
        "1. decision 是程序化系统结论；必须作为只读事实。\n"
        "2. confidence 是证据质量刻度，不是胜率、收益概率或可交易概率。\n"
        "3. 不得重算模型权重，不得用单一因子覆盖系统结论。\n"
        "4. 不得编造外部实时行情、盘口、新闻或未提供的数据。\n"
        "5. 输出应帮助人工审计：说明支持项、风险冲突、下一步观察重点和复核失效条件。\n"
        "6. 不得给出开仓、平仓、仓位、杠杆、止损止盈、下单价格等交易执行建议。\n"
        "7. 保留 BLIND_REVIEW_RESULT 的 theoretical_active_view 与 gamma_regime_lens 边界："
        "它们只作审计参考，不改变系统信号、门控、置信或交易许可。\n"
        "8. 只输出 JSON，不要 markdown，不要额外解释。\n\n"
        "字段含义摘要：\n"
        "- market_context: 当前价格、报价币种和价格来源。\n"
        "- decision: 程序化系统结论；必须作为只读事实。\n"
        "- reasoning.evidence: 因子证据账本，含同向/反向贡献和排除原因。\n"
        "- conflict: 同向与反向证据冲突情况。\n"
        "- blocking: 硬/软门控及解除条件。\n"
        "- factor_cross_section: TMV、微观流、宏观压力、Gamma/GEX、rank、偏斜和资金费率截面。\n\n"
        "BLIND_REVIEW_RESULT JSON：\n"
        f"{json.dumps(blind_payload, ensure_ascii=False, sort_keys=True)}\n\n"
        "FULL_AUDIT_PACKET JSON：\n"
        f"{json.dumps(packet, ensure_ascii=False, sort_keys=True)}"
    )


def build_prompt(packet):
    blind_payload = {
        "theoretical_active_view": _default_theoretical_active_view(
            "兼容路径未执行第一阶段盲审。"),
        "gamma_regime_lens": _default_gamma_regime_lens(
            "兼容路径未执行第一阶段盲审。"),
    }
    return build_full_review_prompt(packet, blind_payload)


def review_response_schema():
    text_item = {"type": "string", "minLength": 1, "maxLength": 260}
    key_levels_schema = {
        "type": "object",
        "properties": {
            "flip": {"type": "number"},
            "call_wall": {"type": "number"},
            "put_wall": {"type": "number"},
            "pin": {"type": "number"},
        },
    }
    active_view_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "bias": {
                "type": "string",
                "enum": sorted(THEORETICAL_ACTIVE_BIASES),
                "description": "理论主动倾向参考，不是系统信号或交易方向。",
            },
            "conviction": {
                "type": "string",
                "enum": sorted(THEORETICAL_ACTIVE_CONVICTIONS),
                "description": "该参考视角的定性把握度，不是胜率。",
            },
            "basis_cn": {
                "type": "string",
                "description": "一句中文说明理论倾向、依据和不确定性。",
                "minLength": 1,
                "maxLength": 420,
            },
            "key_drivers": {
                "type": "array",
                "items": text_item,
                "minItems": 1,
                "maxItems": 5,
            },
            "counter_evidence": {
                "type": "array",
                "items": text_item,
                "minItems": 1,
                "maxItems": 5,
            },
            "boundary_cn": {
                "type": "string",
                "description": "说明该判断只作审计参考，不改变系统信号、门控或交易许可。",
                "minLength": 1,
                "maxLength": 260,
            },
        },
        "required": [
            "bias",
            "conviction",
            "basis_cn",
            "key_drivers",
            "counter_evidence",
            "boundary_cn",
        ],
    }
    gamma_lens_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "regime": {
                "type": "string",
                "enum": sorted(GAMMA_LENS_REGIMES),
                "description": "Gamma 体制，不是方向信号。",
            },
            "regime_extremity": {
                "type": "string",
                "enum": sorted(GAMMA_LENS_EXTREMITIES),
                "description": "体制极端度，主要依据 |GEX| 分位和数据质量。",
            },
            "dynamics_cn": {
                "type": "string",
                "description": "该 Gamma 体制下的分布/波动动力学。",
                "minLength": 1,
                "maxLength": 420,
            },
            "dominant_tail_risk_cn": {
                "type": "string",
                "description": "主要尾部风险，不得写成交易方向。",
                "minLength": 1,
                "maxLength": 420,
            },
            "conviction_effect_on_directional_view": {
                "type": "string",
                "enum": sorted(GAMMA_LENS_EFFECTS),
                "description": "对理论倾向把握度的影响，只降不升。",
            },
            "key_levels": key_levels_schema,
            "positioning_assumption_cn": {
                "type": "string",
                "description": "GEX 符号/做市商持仓假设说明。",
                "minLength": 1,
                "maxLength": 360,
            },
            "data_quality_cn": {
                "type": "string",
                "description": "GEX/flip/rank 冷启动或缺失的可靠性说明。",
                "minLength": 1,
                "maxLength": 360,
            },
            "lens_is_risk_overlay_not_direction": {
                "type": "boolean",
                "description": "必须为 true。",
            },
        },
        "required": [
            "regime",
            "regime_extremity",
            "dynamics_cn",
            "dominant_tail_risk_cn",
            "conviction_effect_on_directional_view",
            "key_levels",
            "positioning_assumption_cn",
            "data_quality_cn",
            "lens_is_risk_overlay_not_direction",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary_cn": {
                "type": "string",
                "description": "一句到两句话的中文综合复核结论，必须区分系统结论与复核意见。",
                "minLength": 1,
                "maxLength": 420,
            },
            "agreement_with_system": {
                "type": "string",
                "enum": ["SUPPORT", "PARTIAL_SUPPORT", "DO_NOT_SUPPORT", "UNABLE_TO_JUDGE"],
                "description": "LLM 复核意见与系统结论的一致程度。",
            },
            "caution_level": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH"],
                "description": "对本次复核意见本身的谨慎程度，不是交易风险等级。",
            },
            "theoretical_active_view": active_view_schema,
            "gamma_regime_lens": gamma_lens_schema,
            "main_supporting_factors": {
                "type": "array",
                "items": text_item,
                "minItems": 0,
                "maxItems": 5,
            },
            "main_risks_or_conflicts": {
                "type": "array",
                "items": text_item,
                "minItems": 0,
                "maxItems": 5,
            },
            "operator_focus": {
                "type": "array",
                "items": text_item,
                "minItems": 0,
                "maxItems": 5,
            },
            "invalid_if": {
                "type": "array",
                "items": text_item,
                "minItems": 0,
                "maxItems": 5,
            },
            "data_quality_note": {
                "type": "string",
                "description": "数据质量、缺失项、rank 冷启动或置信未校准说明。",
                "minLength": 0,
                "maxLength": 320,
            },
            "not_trading_advice": {
                "type": "boolean",
                "description": "必须为 true。",
            },
        },
        "required": list(REQUIRED_REVIEW_FIELDS),
    }


def blind_response_schema():
    full = review_response_schema()
    props = full["properties"]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "theoretical_active_view": props["theoretical_active_view"],
            "gamma_regime_lens": props["gamma_regime_lens"],
        },
        "required": list(REQUIRED_BLIND_FIELDS),
    }


def build_gemini_request(prompt, model=DEFAULT_MODEL, schema=None):
    del model
    schema = schema or review_response_schema()
    return {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}],
        }],
        "generationConfig": {
            "temperature": 0.2,
            "topP": 0.85,
            "responseMimeType": "application/json",
            "responseSchema": _strip_schema_for_legacy(schema),
        },
    }


def call_gemini(api_key, model, request_body, timeout=60):
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required")
    try:
        return _post_gemini(api_key, model, request_body, timeout)
    except RuntimeError as exc:
        if "responseFormat" not in str(exc) and "response_format" not in str(exc):
            raise
        return _post_gemini(api_key, model, _legacy_gemini_request(request_body), timeout)


def resolve_gemini_api_keys(api_key=None, fallback_api_key=None, environ=None):
    environ = environ if environ is not None else os.environ

    def first_present(names):
        for name in names:
            value = environ.get(name)
            if value:
                return value
        return None

    primary = api_key or first_present((
        "GEMINI_3_5_FLASH_API_KEY",
        "GEMINI_FLASH_API_KEY",
        "GEMINI_LOW_COST_API_KEY",
        "GEMINI_API_KEY",
    ))
    fallback = fallback_api_key or first_present((
        "GEMINI_PAID_API_KEY",
        "GEMINI_FALLBACK_API_KEY",
        "GEMINI_PRO_API_KEY",
    ))
    chain = []
    if primary:
        chain.append(("low_cost", primary))
    if fallback and fallback != primary:
        chain.append(("paid_fallback", fallback))
    return chain


def _call_gemini_with_key_chain(key_chain, model, request_body, timeout,
                                caller=call_gemini):
    if not key_chain:
        raise RuntimeError(
            "Gemini API key is required; set GEMINI_3_5_FLASH_API_KEY "
            "(or GEMINI_API_KEY) and optional GEMINI_PAID_API_KEY fallback.")
    failures = []
    for tier, key in key_chain:
        try:
            return caller(key, model, request_body, timeout)
        except Exception as exc:
            failures.append(tier + ": " + _redact_sensitive_text(str(exc))[:180])
    raise RuntimeError("Gemini call failed for all key tiers: "
                       + " | ".join(failures))


def _post_gemini(api_key, model, request_body, timeout):
    url = GEMINI_ENDPOINT.format(model=model)
    body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini HTTP {exc.code}: {detail}") from exc


def _legacy_gemini_request(request_body):
    converted = dict(request_body)
    generation = dict(converted.get("generationConfig") or {})
    response_format = _as_dict(_as_dict(generation.pop("responseFormat")).get("text"))
    if response_format:
        generation["responseMimeType"] = response_format.get("mimeType", "application/json")
        generation["responseSchema"] = _strip_schema_for_legacy(
            response_format.get("schema") or {})
    converted["generationConfig"] = generation
    return converted


def _strip_schema_for_legacy(schema):
    if isinstance(schema, dict):
        allowed = {
            "type", "properties", "required", "items", "enum",
            "description", "nullable",
        }
        result = {}
        for key, value in schema.items():
            if key not in allowed:
                continue
            if key == "properties" and isinstance(value, dict):
                result[key] = {
                    name: _strip_schema_for_legacy(child)
                    for name, child in value.items()
                }
            else:
                result[key] = _strip_schema_for_legacy(value)
        return result
    if isinstance(schema, list):
        return [_strip_schema_for_legacy(item) for item in schema]
    return schema


def parse_gemini_response(response):
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Gemini response missing candidates")
    parts = _as_dict(_as_dict(candidates[0]).get("content")).get("parts")
    if not isinstance(parts, list):
        raise ValueError("Gemini response missing content.parts")
    text = "".join(str(_as_dict(part).get("text") or "") for part in parts).strip()
    if not text:
        raise ValueError("Gemini response text is empty")
    return json.loads(_strip_json_fence(text))


def build_llm_review(card, payload, model=DEFAULT_MODEL, reviewed_at=None,
                     blind_payload=None):
    payload = _validate_model_payload(payload)
    blind_payload = _validate_blind_payload(blind_payload) if blind_payload else None
    packet = build_review_packet(card)
    blind_packet = build_blind_theoretical_packet(packet)
    reviewed_at = reviewed_at or _now_iso()
    active_view = ((blind_payload or {}).get("theoretical_active_view")
                   if blind_payload else payload["theoretical_active_view"])
    gamma_lens = ((blind_payload or {}).get("gamma_regime_lens")
                  if blind_payload else payload["gamma_regime_lens"])
    review = {
        "schema": OUTPUT_SCHEMA_VERSION,
        "status": "OK",
        "provider": "gemini",
        "model": model,
        "reviewed_at": reviewed_at,
        "prompt_version": PROMPT_VERSION,
        "input_packet_hash": _sha256_json(packet),
        "blind_input_packet_hash": _sha256_json(blind_packet),
        "summary_cn": payload["summary_cn"],
        "agreement_with_system": payload["agreement_with_system"],
        "caution_level": _policy_caution(payload["caution_level"], packet),
        "theoretical_active_view": _normalize_theoretical_active_view(
            active_view, derived_blind=bool(blind_payload)),
        "gamma_regime_lens": _normalize_gamma_regime_lens(
            gamma_lens),
        "main_supporting_factors": _trim_list(payload["main_supporting_factors"]),
        "main_risks_or_conflicts": _trim_list(payload["main_risks_or_conflicts"]),
        "operator_focus": _trim_list(payload["operator_focus"]),
        "invalid_if": _trim_list(payload["invalid_if"]),
        "data_quality_note": str(payload.get("data_quality_note") or ""),
        "not_trading_advice": True,
    }
    return review


def generate_reviews(source, reviews_output, api_key=None, fallback_api_key=None,
                     model=DEFAULT_MODEL, limit=20, include_synthetic=False, timeout=60,
                     call_gemini=call_gemini, reviewed_at=None):
    source = Path(source)
    reviews_output = Path(reviews_output)
    cards = _read_jsonl(source)
    cards = _dedupe_cards(cards)
    cards = sorted(cards, key=_card_sort_key, reverse=True)
    done = _read_review_card_ids(reviews_output)
    key_chain = resolve_gemini_api_keys(api_key, fallback_api_key)
    written = 0
    attempted = 0
    skipped = 0
    errors = 0
    for card in cards:
        if limit and attempted >= limit:
            break
        card_id = _card_id(card)
        if not card_id:
            skipped += 1
            continue
        if card_id in done:
            skipped += 1
            continue
        if _is_synthetic(card) and not include_synthetic:
            skipped += 1
            continue
        attempted += 1
        try:
            packet = build_review_packet(card)
            blind_prompt = build_blind_prompt(packet)
            blind_request = build_gemini_request(
                blind_prompt, model=model, schema=blind_response_schema())
            blind_response = _call_gemini_with_key_chain(
                key_chain, model, blind_request, timeout, call_gemini)
            blind_payload = _validate_blind_payload(
                parse_gemini_response(blind_response))

            prompt = build_full_review_prompt(packet, blind_payload)
            request_body = build_gemini_request(
                prompt, model=model, schema=review_response_schema())
            raw_response = _call_gemini_with_key_chain(
                key_chain, model, request_body, timeout, call_gemini)
            payload = parse_gemini_response(raw_response)
            review = build_llm_review(card, payload, model=model,
                                      blind_payload=blind_payload,
                                      reviewed_at=reviewed_at)
            _append_jsonl(reviews_output, {
                "card_id": card_id,
                "symbol": _as_dict(card.get("identity")).get("symbol") or card.get("symbol"),
                "confirmed_at": _as_dict(card.get("identity")).get("confirmed_at") or card.get("created_at"),
                "llm_review": review,
            })
            done.add(card_id)
            written += 1
        except Exception as exc:  # keep sidecar script soft-fail per card
            errors += 1
            safe_error = _redact_sensitive_text(str(exc))[:220]
            _append_jsonl(reviews_output, {
                "card_id": card_id,
                "llm_review": {
                    "schema": OUTPUT_SCHEMA_VERSION,
                    "status": "ERROR",
                    "provider": "gemini",
                    "model": model,
                    "reviewed_at": reviewed_at or _now_iso(),
                    "prompt_version": PROMPT_VERSION,
                    "summary_cn": "LLM 复核生成失败，保留系统审计卡原始结论。",
                    "agreement_with_system": "UNABLE_TO_JUDGE",
                    "caution_level": "HIGH",
                    "theoretical_active_view": _default_theoretical_active_view(
                        "LLM 调用或解析失败，无法形成理论主动倾向参考。"),
                    "gamma_regime_lens": _default_gamma_regime_lens(
                        "LLM 调用或解析失败，无法形成 Gamma 体制风险叠加分析。"),
                    "main_supporting_factors": [],
                    "main_risks_or_conflicts": ["LLM 调用或解析失败：" + safe_error],
                    "operator_focus": ["仅依据系统审计卡继续人工复核。"],
                    "invalid_if": [],
                    "data_quality_note": "",
                    "not_trading_advice": True,
                },
            })
    return {
        "source": str(source),
        "reviews_output": str(reviews_output),
        "written_reviews": written,
        "attempted_cards": attempted,
        "skipped_cards": skipped,
        "errors": errors,
        "model": model,
        "api_key_tiers": [tier for tier, _ in key_chain],
    }


def _validate_blind_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("blind model output must be object")
    missing = [key for key in REQUIRED_BLIND_FIELDS if key not in payload]
    if missing:
        raise ValueError("blind model output missing fields: " + ", ".join(missing))
    _validate_theoretical_active_view(payload.get("theoretical_active_view"))
    _validate_gamma_regime_lens(payload.get("gamma_regime_lens"))
    return payload


def _validate_model_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("model output must be object")
    missing = [key for key in REQUIRED_REVIEW_FIELDS if key not in payload]
    if missing:
        raise ValueError("model output missing fields: " + ", ".join(missing))
    if payload.get("agreement_with_system") not in {
        "SUPPORT", "PARTIAL_SUPPORT", "DO_NOT_SUPPORT", "UNABLE_TO_JUDGE",
    }:
        raise ValueError("invalid agreement_with_system")
    if payload.get("caution_level") not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError("invalid caution_level")
    _validate_theoretical_active_view(payload.get("theoretical_active_view"))
    _validate_gamma_regime_lens(payload.get("gamma_regime_lens"))
    for key in ("main_supporting_factors", "main_risks_or_conflicts",
                "operator_focus", "invalid_if"):
        if not isinstance(payload.get(key), list):
            raise ValueError(key + " must be list")
    return payload


def _validate_theoretical_active_view(view):
    if not isinstance(view, dict):
        raise ValueError("theoretical_active_view must be object")
    missing = [
        key for key in ("bias", "conviction", "basis_cn", "key_drivers",
                       "counter_evidence", "boundary_cn")
        if key not in view
    ]
    if missing:
        raise ValueError("theoretical_active_view missing fields: "
                         + ", ".join(missing))
    if str(view.get("bias") or "").upper() not in THEORETICAL_ACTIVE_BIASES:
        raise ValueError("invalid theoretical_active_view.bias")
    if str(view.get("conviction") or "").upper() not in THEORETICAL_ACTIVE_CONVICTIONS:
        raise ValueError("invalid theoretical_active_view.conviction")
    if not isinstance(view.get("key_drivers"), list):
        raise ValueError("theoretical_active_view.key_drivers must be list")
    if not isinstance(view.get("counter_evidence"), list):
        raise ValueError("theoretical_active_view.counter_evidence must be list")


def _normalize_theoretical_active_view(view, derived_blind=False):
    view = _as_dict(view)
    bias = str(view.get("bias") or "UNABLE_TO_JUDGE").upper()
    conviction = str(view.get("conviction") or "LOW").upper()
    if bias not in THEORETICAL_ACTIVE_BIASES:
        bias = "UNABLE_TO_JUDGE"
    if conviction not in THEORETICAL_ACTIVE_CONVICTIONS:
        conviction = "LOW"
    return {
        "bias": bias,
        "conviction": conviction,
        "basis_cn": str(view.get("basis_cn") or "")[:420],
        "key_drivers": _trim_list(view.get("key_drivers"), limit=5),
        "counter_evidence": _trim_list(view.get("counter_evidence"), limit=5),
        "boundary_cn": str(view.get("boundary_cn") or (
            "该判断只作审计参考，不改变系统信号、门控、置信或交易许可。"
        ))[:260],
        "derived_blind": bool(derived_blind),
        "is_not_a_signal": True,
        "validation_status": "UNVALIDATED",
    }


def _default_theoretical_active_view(reason):
    return {
        "bias": "UNABLE_TO_JUDGE",
        "conviction": "LOW",
        "basis_cn": reason,
        "key_drivers": [],
        "counter_evidence": [reason],
        "boundary_cn": "该判断只作审计参考，不改变系统信号、门控、置信或交易许可。",
        "derived_blind": False,
        "is_not_a_signal": True,
        "validation_status": "UNVALIDATED",
    }


def _validate_gamma_regime_lens(lens):
    if not isinstance(lens, dict):
        raise ValueError("gamma_regime_lens must be object")
    missing = [
        key for key in (
            "regime",
            "regime_extremity",
            "dynamics_cn",
            "dominant_tail_risk_cn",
            "conviction_effect_on_directional_view",
            "key_levels",
            "positioning_assumption_cn",
            "data_quality_cn",
            "lens_is_risk_overlay_not_direction",
        )
        if key not in lens
    ]
    if missing:
        raise ValueError("gamma_regime_lens missing fields: " + ", ".join(missing))
    if str(lens.get("regime") or "").upper() not in GAMMA_LENS_REGIMES:
        raise ValueError("invalid gamma_regime_lens.regime")
    if str(lens.get("regime_extremity") or "").upper() not in GAMMA_LENS_EXTREMITIES:
        raise ValueError("invalid gamma_regime_lens.regime_extremity")
    effect = str(lens.get("conviction_effect_on_directional_view") or "").upper()
    if effect not in GAMMA_LENS_EFFECTS:
        raise ValueError("invalid gamma_regime_lens.conviction_effect_on_directional_view")
    if not isinstance(lens.get("key_levels"), dict):
        raise ValueError("gamma_regime_lens.key_levels must be object")
    if lens.get("lens_is_risk_overlay_not_direction") is not True:
        raise ValueError("gamma_regime_lens must be risk overlay, not direction")


def _normalize_gamma_regime_lens(lens):
    lens = _as_dict(lens)
    regime = str(lens.get("regime") or "UNKNOWN").upper()
    extremity = str(lens.get("regime_extremity") or "UNKNOWN").upper()
    effect = str(lens.get("conviction_effect_on_directional_view") or "UNKNOWN").upper()
    if regime not in GAMMA_LENS_REGIMES:
        regime = "UNKNOWN"
    if extremity not in GAMMA_LENS_EXTREMITIES:
        extremity = "UNKNOWN"
    if effect not in GAMMA_LENS_EFFECTS:
        effect = "UNKNOWN"
    levels = _as_dict(lens.get("key_levels"))
    return {
        "regime": regime,
        "regime_extremity": extremity,
        "dynamics_cn": str(lens.get("dynamics_cn") or "")[:420],
        "dominant_tail_risk_cn": str(lens.get("dominant_tail_risk_cn") or "")[:420],
        "conviction_effect_on_directional_view": effect,
        "key_levels": {
            "flip": _number_or_none(levels.get("flip")),
            "call_wall": _number_or_none(levels.get("call_wall")),
            "put_wall": _number_or_none(levels.get("put_wall")),
            "pin": _number_or_none(levels.get("pin")),
        },
        "positioning_assumption_cn": str(lens.get("positioning_assumption_cn") or "")[:360],
        "data_quality_cn": str(lens.get("data_quality_cn") or "")[:360],
        "lens_is_risk_overlay_not_direction": True,
    }


def _default_gamma_regime_lens(reason):
    return {
        "regime": "UNKNOWN",
        "regime_extremity": "UNKNOWN",
        "dynamics_cn": reason,
        "dominant_tail_risk_cn": reason,
        "conviction_effect_on_directional_view": "UNKNOWN",
        "key_levels": {},
        "positioning_assumption_cn": "无法从当前 LLM 输出形成可靠的 GEX 持仓符号假设。",
        "data_quality_cn": reason,
        "lens_is_risk_overlay_not_direction": True,
    }


def _policy_caution(caution, packet):
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    floor = "LOW"
    conflict = _as_dict(packet.get("conflict"))
    if str(conflict.get("level") or "").upper() in {"MATERIAL", "HIGH", "SEVERE"}:
        floor = "MEDIUM"
    quality = _as_dict(packet.get("quality"))
    if quality.get("overall") not in (None, "OK"):
        floor = "MEDIUM"
    rank = _as_dict(_as_dict(_as_dict(packet.get("factor_cross_section")).get("gex_info")).get("rank"))
    metrics = _as_dict(rank.get("metrics"))
    if any(str(_as_dict(metric).get("quality") or "").lower() == "warming_up"
           for metric in metrics.values()):
        floor = "MEDIUM"
    return caution if order.get(caution, 0) >= order[floor] else floor


def _safe_copy(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if SENSITIVE_KEY_RE.search(str(key)):
                continue
            result[str(key)] = _safe_copy(item)
        return result
    if isinstance(value, list):
        return [_safe_copy(item) for item in value[:120]]
    if isinstance(value, str):
        redacted = value
        for pattern in SENSITIVE_TEXT_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted[:4000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:4000]


def _trim_list(items, limit=5):
    return [str(item)[:260] for item in list(items or [])[:limit]]


def _number_or_none(value):
    if isinstance(value, bool) or value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _redact_sensitive_text(value):
    redacted = str(value or "")
    for pattern in SENSITIVE_TEXT_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    redacted = re.sub(r"GEMINI_API_KEY", "[REDACTED_ENV]", redacted, flags=re.IGNORECASE)
    redacted = re.sub(r"x-goog-api-key", "[REDACTED_HEADER]", redacted, flags=re.IGNORECASE)
    redacted = re.sub(r"Bearer\s+\S+", "[REDACTED_BEARER]", redacted, flags=re.IGNORECASE)
    return redacted


def _read_jsonl(path):
    path = Path(path)
    records = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def _read_review_card_ids(path):
    done = set()
    for value in _read_jsonl(path):
        card_id = value.get("card_id") or _card_id(value)
        review = _as_dict(value.get("llm_review"))
        if card_id and review.get("status") == "OK":
            done.add(card_id)
    return done


def _dedupe_cards(cards):
    by_id = {}
    for card in cards:
        card_id = _card_id(card)
        if card_id:
            by_id[card_id] = card
    return list(by_id.values())


def _card_id(card):
    return _as_dict(card.get("identity")).get("card_id") or card.get("card_id")


def _card_sort_key(card):
    identity = _as_dict(card.get("identity"))
    return (
        _timestamp_sort_value(
            identity.get("confirmed_time_ms") or card.get("confirmed_time_ms"))
        or _timestamp_sort_value(
            identity.get("confirmed_at") or card.get("created_at")),
        _card_id(card) or "",
    )


def _timestamp_sort_value(value):
    if isinstance(value, bool) or value in ("", None):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = _dt.datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            return parsed.timestamp() * 1000.0
        except ValueError:
            return 0.0
    return 0.0


def _is_synthetic(card):
    return bool(_as_dict(card.get("identity")).get("is_synthetic") or card.get("is_synthetic"))


def _append_jsonl(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")) + "\n")


def _sha256_json(payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_json_fence(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate Gemini LLM audit reviews for signal_review.jsonl.")
    parser.add_argument("--source", required=True,
                        help="Path to signal_review.jsonl.")
    parser.add_argument("--reviews-output", default=DEFAULT_REVIEWS,
                        help="Sidecar JSONL path for LLM reviews.")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="Gemini model name.")
    parser.add_argument(
        "--api-key",
        default=None,
        help=("Low-cost Gemini key for the default first attempt. Env order: "
              "GEMINI_3_5_FLASH_API_KEY, GEMINI_FLASH_API_KEY, "
              "GEMINI_LOW_COST_API_KEY, GEMINI_API_KEY."))
    parser.add_argument(
        "--fallback-api-key",
        default=None,
        help=("Fallback paid-tier Gemini key. Env order: GEMINI_PAID_API_KEY, "
              "GEMINI_FALLBACK_API_KEY, GEMINI_PRO_API_KEY."))
    parser.add_argument("--limit", type=int, default=5,
                        help="Maximum new cards to review in this run.")
    parser.add_argument("--timeout", type=int, default=60,
                        help="HTTP timeout seconds.")
    parser.add_argument("--include-synthetic", action="store_true",
                        help="Allow synthetic/local fixture cards for preview testing.")
    args = parser.parse_args(argv)
    result = generate_reviews(
        args.source,
        args.reviews_output,
        api_key=args.api_key,
        fallback_api_key=args.fallback_api_key,
        model=args.model,
        limit=args.limit,
        include_synthetic=args.include_synthetic,
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result["errors"] and not result["written_reviews"] else 0


if __name__ == "__main__":
    sys.exit(main())
