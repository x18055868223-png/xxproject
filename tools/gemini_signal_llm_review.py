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
import socket
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
TRANSITION_OUTPUT_SCHEMA_VERSION = "signal_transition_llm_review@1.2.4"
TRANSITION_PROMPT_VERSION = "gemini_signal_transition_review_prompt@1.2.4"
TRANSITION_PACKET_VERSION = "SignalTransitionReviewPacket@1.1.1"
TRANSITION_EVIDENCE_CATALOG_VERSION = "transition_evidence_catalog@1.0.0"
TRANSITION_RAW_FIELD_LEAK_PATTERNS = (
    ("factor_cross_section", re.compile(r"\bfactor_cross_section(?:\.[A-Za-z0-9_]+)*")),
    ("macro_pressure.components", re.compile(r"\bmacro_pressure\.components(?:\.[A-Za-z0-9_]+)*")),
    ("source_ref", re.compile(r"\bsource_ref\b")),
    ("primary_fields", re.compile(r"\bprimary_fields\b")),
    ("dotted_field_path", re.compile(r"\b[a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+){2,}\b")),
    ("主要字段", re.compile(r"主要字段")),
    ("来源", re.compile(r"来源[:：]")),
    ("核心前后值已入包", re.compile(r"核心前后值已入包")),
    ("原始变化", re.compile(r"原始变化\s*\d*\s*项?")),
)

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
    re.compile(r"AQ\.[0-9A-Za-z_\-]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\[^,\]\}\n\r\t ]+"),
    re.compile(r"/(?:home|opt|var|etc|root|Users)/[^,\]\}\n\r\t ]+"),
)
RETRYABLE_GEMINI_HTTP_CODES = {429, 500, 502, 503, 504}
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
TRANSITION_TRAJECTORY_STATES = {
    "DETERIORATING",
    "IMPROVING",
    "MIXED",
    "STABLE",
    "INSUFFICIENT_HISTORY",
    "UNKNOWN",
}
TRANSITION_CONTINUITY_STATES = {
    "CONTINUING",
    "NEUTRALIZED",
    "REVERSING",
    "BLOCKED",
    "UNKNOWN",
}
TRANSITION_EVIDENCE_STATUSES = {
    "SUFFICIENT",
    "PARTIAL",
    "NOT_COMPARABLE",
    "MISSING",
}
TRANSITION_DIRECTIONAL_ROLES = {
    "RISK_CONSTRAINT",
    "SUPPORT",
    "NEUTRAL_OR_EASING",
    "MIXED",
    "UNDETERMINED",
}
TRANSITION_MAGNITUDE_VERDICTS = {
    "changes_judgment",
    "background_only",
    "indeterminate",
}
TRANSITION_AUDIT_ATTENTION_EFFECTS = {
    "SHIFT_FOCUS",
    "REINFORCE_VIEW",
    "WEAKEN_VIEW",
    "BACKGROUND_ONLY",
    "UNDETERMINED",
}
TRANSITION_EPISTEMIC_STATUSES = {
    "OBSERVED",
    "SUPPORTED_INFERENCE",
    "HYPOTHESIS",
    "NOT_ASSESSABLE",
}
TRANSITION_CROSS_FACTOR_RELATIONS = {
    "REINFORCING",
    "OFFSETTING",
    "CO_MOVEMENT",
    "CONSTRAINT_INTERACTION",
}
TRANSITION_EFFECT_TARGETS = {
    "DIRECTIONAL_SKELETON",
    "GATE_OR_BLOCKING",
    "VOLATILITY_SPACE",
    "CROWDING_OR_LEVERAGE",
    "OPTION_DEMAND",
    "SIGNAL_COHERENCE",
    "DATA_RELIABILITY",
    "CROSS_FACTOR_STATE",
    "UNKNOWN",
}
TRANSITION_EXPLANATION_RELATIONS = {
    "CONSISTENT_WITH",
    "CO_MOVEMENT",
    "REINFORCING",
    "OFFSETTING",
    "CONSTRAINT_INTERACTION",
    "ALTERNATIVE_EXPLANATION",
}
TRANSITION_BLIND_MODES = {
    "single_call_evidence_first",
    "two_call_strict",
}


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
            "derived_blind": True,
            "independence_mode": "two_call_blind_first",
            "limitation_cn": (
                "本包用于第一次独立盲读调用；不包含系统结论、证据账本、冲突账本或门控结论。"
            ),
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
        "你是交易信号审计复核员。现在是第一次独立盲读调用。\n"
        "你只能基于 BLIND_THEORETICAL_PACKET 给出 theoretical_active_view 与 gamma_regime_lens，"
        "不能推断或提及系统 decision、reasoning、conflict、blocking 或 trade_allowed。\n"
        "只输出 JSON，不要 markdown，不要额外解释。\n\n"
        "边界：\n"
        "1. 不得使用外部市场数据，不得编造未提供的数据。\n"
        "2. 不得给出开仓、平仓、仓位、杠杆、止损止盈、下单价格等交易执行建议。\n"
        "3. 你必须输出 theoretical_active_view：这是基于市场微结构、量价、Gamma/GEX、"
        "宏观、偏斜、资金费率等理论关系的主动倾向参考；它不是系统信号，不改变 decision、"
        "blocking 或 trade_allowed。\n"
        "4. theoretical_active_view 可以给出倾向，但必须言之有物：至少引用两类给定因子，"
        "同时列出反证或不确定性；证据不足时选择 MIXED_UNCLEAR 或 UNABLE_TO_JUDGE。\n"
        "5. 你必须输出 gamma_regime_lens：它只分析 Gamma 体制对分布、尾部和反身性的"
        "风险叠加，绝不产出竞争性方向；只能降低/中和方向倾向的把握度，不能翻转方向。\n"
        "6. 正 Gamma → 预期波动压制、均值回归、向 pin/call/put 大额行权价钉住；"
        "负 Gamma → 预期波动放大、助涨助跌、两个方向都可能剧烈反身。\n"
        "7. 极端负 Gamma + 已有倾向时，必须提示催化剂导致反向挤压/剧烈反转的尾部风险，"
        "并点名 flip / 对侧 wall / pin 等关键位。\n"
        "8. 如果 flip/GEX 缺失、rank warming_up 或符号假设不稳，gamma_regime_lens.regime 选 UNKNOWN，"
        "不得臆断体制。\n"
        "9. GEX 符号在加密市场是持仓假设，不是测得事实；必须在 positioning_assumption_cn 中说明。\n\n"
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


def build_prompt(packet, blind_payload=None):
    if not blind_payload:
        blind_payload = {
            "theoretical_active_view": _default_theoretical_active_view(
                "盲读结果缺失，仅保留完整审计复核。"),
            "gamma_regime_lens": _default_gamma_regime_lens(
                "盲读结果缺失，仅保留完整审计复核。"),
        }
    blind_payload = _validate_blind_payload(blind_payload)
    return (
        "你是交易信号审计复核员，只做审计增强，不是交易执行系统。\n"
        "现在是第二次复核调用。第一次盲读结果已经给出，不能被完整审计包重写；"
        "你只能基于 FULL_AUDIT_PACKET 判断系统结论是否与盲读视角、证据账本和门控一致。\n"
        "请输出中文 JSON。\n\n"
        "边界：\n"
        "1. 系统信号结论已经由 decision 给出，你不得改变方向、置信度、EDB、blocking、trade_allowed 或下一步动作。\n"
        "2. confidence 是证据质量刻度，不是胜率、收益概率或可交易概率。\n"
        "3. 不得重算模型权重，不得用单一因子覆盖系统结论。\n"
        "4. 不得编造外部实时行情、盘口、新闻或未提供的数据。\n"
        "5. 先检查数据质量、缺失字段、冲突比例、rank 冷启动，再解释方向。\n"
        "6. 输出应帮助人工审计：说明支持项、风险冲突、下一步观察重点和复核失效条件。\n"
        "7. 不得给出开仓、平仓、仓位、杠杆、止损止盈、下单价格等交易执行建议。\n"
        "8. theoretical_active_view 与 gamma_regime_lens 必须沿用 BLIND_REVIEW_RESULT，"
        "它们是第一次调用产生的真盲读结果。\n"
        "9. 只输出 JSON，不要 markdown，不要额外解释。\n\n"
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


def build_gemini_request(prompt, model=DEFAULT_MODEL):
    del model
    return {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}],
        }],
        "generationConfig": {
            "temperature": 0.2,
            "topP": 0.85,
            "responseMimeType": "application/json",
            "responseSchema": _strip_schema_for_legacy(review_response_schema()),
        },
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
        "required": ["theoretical_active_view", "gamma_regime_lens"],
    }


def build_blind_gemini_request(prompt, model=DEFAULT_MODEL):
    del model
    return {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}],
        }],
        "generationConfig": {
            "temperature": 0.2,
            "topP": 0.85,
            "responseMimeType": "application/json",
            "responseSchema": _strip_schema_for_legacy(blind_response_schema()),
        },
    }


def build_transition_review_packet(transition):
    transition = _as_dict(transition)
    relation = _as_dict(transition.get("relation"))
    packet = {
        "schema": {
            "name": TRANSITION_PACKET_VERSION,
            "source_schema": transition.get("schema_version"),
        },
        "identity": _safe_copy({
            "transition_id": transition.get("transition_id"),
            "symbol": transition.get("symbol"),
            "previous_card_id": transition.get("previous_card_id"),
            "current_card_id": transition.get("current_card_id"),
            "previous_ts_ms": transition.get("previous_ts_ms"),
            "current_ts_ms": transition.get("current_ts_ms"),
            "elapsed_ms": transition.get("elapsed_ms"),
        }),
        "comparison": _safe_copy({
            "comparison_quality": (
                transition.get("comparison_quality")
                or relation.get("comparison_quality")
            ),
            "comparison_limitations": relation.get("comparison_limitations") or [],
            "same_episode": relation.get("same_episode"),
        }),
        "decision_transition": _safe_copy(
            _transition_decision_packet(transition.get("decision_transition"))),
        "core_skeleton": _safe_copy(transition.get("core_skeleton")),
        "core_transition_display": _safe_copy([
            _transition_display_row_packet(item)
            for item in list(transition.get("core_transition_display") or [])[:9]
            if isinstance(item, dict)
        ]),
        "domain_change_summaries": _safe_copy([
            _transition_domain_summary_packet(item)
            for item in list(transition.get("domain_change_summaries") or [])[:8]
            if isinstance(item, dict)
        ]),
        "top_material_changes": _safe_copy([
            _transition_change_packet(item)
            for item in list(transition.get("top_material_changes") or [])[:8]
            if isinstance(item, dict)
        ]),
        "recent_5_trajectory": _safe_copy(
            transition.get("recent_5_trajectory")
            or _as_dict(transition.get("trajectory")).get("recent_5")
            or []),
        "baseline_24h": _safe_copy(transition.get("baseline_24h")),
        "episode_anchor": _safe_copy(transition.get("episode_anchor")),
        "trajectory": _safe_copy(transition.get("trajectory")),
        "domain_states": _safe_copy(transition.get("domain_states")),
        "cross_domain_flags": _safe_copy(transition.get("cross_domain_flags") or []),
        "materiality_score": transition.get("materiality_score"),
        "field_glossary": {
            "delta_abs": "程序已计算的当前值减上一值；LLM 只能解释，不得重算。",
            "comparison_quality": "由两张卡的时间间隔确定：<=90m HIGH, <=6h MEDIUM, <=24h LOW, >24h VERY_LOW。",
            "materiality": "程序化阈值给出的变化材料性，不是交易强度。",
            "role_before_role_after": "保留 NON_VOTING / EXCLUDED / GATE_ONLY 等原始角色，仅用于审计。",
        },
        "guardrails": {
            "role": "AUDIT_ADVISORY_ONLY",
            "only_explain_program_delta": True,
            "do_not_recompute_delta_or_weights": True,
            "do_not_use_external_market_data": True,
            "distinguish_correlation_from_causality": True,
            "no_trading_instruction": True,
            "not_trading_advice": True,
        },
    }
    packet["evidence_catalog"] = _build_transition_evidence_catalog(packet)
    packet["evidence_catalog_schema_version"] = TRANSITION_EVIDENCE_CATALOG_VERSION
    packet["evidence_catalog_source_packet_version"] = TRANSITION_PACKET_VERSION
    packet["evidence_catalog_hash"] = _sha256_json(packet["evidence_catalog"])
    packet["evidence_ref_policy"] = {
        "preferred": "Use evidence_catalog[].id values in evidence_refs.",
        "compatibility": "JSON Pointer refs remain accepted for legacy sidecars.",
        "system_assertions_are_not_evidence": True,
        "observed_changes_require_substantive_evidence": (
            "field_glossary may clarify units but cannot be the only evidence."),
    }
    return _safe_copy(packet)


def _build_transition_evidence_catalog(packet):
    rows = []
    seen = set()

    def add(row_id, pointer, domain, kind, label_cn, value_summary_cn):
        row_id = _transition_evidence_id(row_id)
        if not row_id or row_id in seen:
            return
        seen.add(row_id)
        rows.append({
            "id": row_id,
            "pointer": pointer,
            "domain": str(domain or "")[:60],
            "kind": str(kind or "")[:60],
            "label_cn": str(label_cn or "")[:120],
            "value_summary_cn": str(value_summary_cn or "")[:300],
        })

    for index, item in enumerate(list(packet.get("domain_change_summaries") or [])[:8]):
        item = _as_dict(item)
        domain = str(item.get("domain") or "DOMAIN")
        fields = ", ".join(str(value) for value in list(item.get("primary_fields") or [])[:3])
        summary = f"{domain} 领域变化摘要已入包"
        if item.get("raw_change_count") is not None:
            summary += f"，变化项数：{item.get('raw_change_count', 0)}"
        add(f"EV_DOMAIN_{domain}", f"/domain_change_summaries/{index}",
            domain, "domain_change_summary", f"{domain} 领域变化摘要", summary)

    for index, item in enumerate(list(packet.get("core_transition_display") or [])[:9]):
        item = _as_dict(item)
        domain = str(item.get("domain") or "DISPLAY")
        value_key = str(item.get("value_key") or "VALUE")
        summary = (
            f"{item.get('title_cn') or domain}："
            f"{item.get('previous_display', '缺失')} -> {item.get('current_display', '缺失')}"
        )
        if item.get("delta_display"):
            summary += f"，变化 {item.get('delta_display')}"
        if item.get("source_note"):
            summary += f"，口径 {item.get('source_note')}"
        add(f"EV_DISPLAY_{domain}_{value_key}", f"/core_transition_display/{index}",
            domain, "display_value", item.get("title_cn") or domain, summary)

    core = _as_dict(packet.get("core_skeleton"))
    for index, item in enumerate(list(core.get("domains") or [])[:12]):
        item = _as_dict(item)
        domain = str(item.get("domain") or "CORE")
        summary = f"{domain} 核心前后值可用于中文事实说明"
        add(f"EV_CORE_{domain}", f"/core_skeleton/domains/{index}",
            domain, "core_skeleton", f"{domain} 核心骨架", summary)

    add("EV_COMPARISON_QUALITY", "/comparison", "QUALITY",
        "comparison_quality", "比较质量", "两张卡的间隔、可比性与限制条件。")
    add("EV_FIELD_GLOSSARY", "/field_glossary", "QUALITY",
        "field_glossary", "字段语义契约", "单位、材料性、角色和 delta 的解释边界。")
    return rows


def _transition_evidence_id(value):
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").upper()).strip("_")
    if not text:
        return ""
    return text if text.startswith("EV_") else "EV_" + text


def build_transition_blind_delta_packet(packet):
    source = _as_dict(packet)
    display_rows = []
    for item in list(source.get("core_transition_display") or [])[:9]:
        item = _as_dict(item)
        display_rows.append({
            "domain": item.get("domain"),
            "title_cn": item.get("title_cn"),
            "value_key": item.get("value_key"),
            "previous_display": item.get("previous_display"),
            "current_display": item.get("current_display"),
            "delta_display": item.get("delta_display"),
            "source_note": item.get("source_note"),
        })
    return _safe_copy({
        "schema": {
            "name": "TRANSITION_BLIND_DELTA_PACKET@0.1.0",
            "source_packet_schema": _as_dict(source.get("schema")).get("name"),
            "derived_blind": True,
            "independence_mode": "transition_delta_blind_first",
            "limitation_cn": (
                "本包用于实验性 transition 第一次盲读；不包含系统决策、阻断、材料性排序或已生成含义。"
            ),
        },
        "identity": source.get("identity"),
        "comparison": source.get("comparison"),
        "core_skeleton": source.get("core_skeleton"),
        "core_transition_display": display_rows,
        "domain_change_summaries": [
            {
                "domain": _as_dict(item).get("domain"),
                "raw_change_count": _as_dict(item).get("raw_change_count"),
                "primary_fields": _as_dict(item).get("primary_fields"),
                "source_refs": _as_dict(item).get("source_refs"),
                "previous": _as_dict(item).get("previous"),
                "current": _as_dict(item).get("current"),
            }
            for item in list(source.get("domain_change_summaries") or [])[:8]
        ],
        "recent_5_trajectory": source.get("recent_5_trajectory"),
        "baseline_24h": source.get("baseline_24h"),
        "episode_anchor": source.get("episode_anchor"),
        "trajectory": source.get("trajectory"),
        "domain_states": source.get("domain_states"),
        "field_glossary": source.get("field_glossary"),
        "evidence_catalog": source.get("evidence_catalog"),
        "evidence_ref_policy": source.get("evidence_ref_policy"),
        "guardrails": source.get("guardrails"),
    })


def build_transition_blind_prompt(packet):
    blind_packet = build_transition_blind_delta_packet(packet)
    return (
        "你是状态转移审计第一次盲读复核员。你只能基于 TRANSITION_BLIND_DELTA_PACKET "
        "解释相邻审计卡之间的原始 delta，不得推断系统 decision、blocking、materiality "
        "或 trade_allowed，不得输出交易建议。\n"
        "请输出结构化 JSON，重点给出每个 domain 的独立读数、倾向、幅度充分性和证据路径；"
        "evidence_refs 优先使用 evidence_catalog 中的 evidence_id；candidate_explanations "
        "只能写 causal_status=UNVERIFIED 的非确定性解释。这些读数只是实验性独立观察，不改变系统结论。\n\n"
        "TRANSITION_BLIND_DELTA_PACKET JSON：\n"
        f"{json.dumps(blind_packet, ensure_ascii=False, sort_keys=True)}"
    )


def transition_blind_response_schema():
    return transition_response_schema()


def build_transition_blind_gemini_request(prompt, model=DEFAULT_MODEL):
    del model
    return {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}],
        }],
        "generationConfig": {
            "temperature": 0,
            "topP": 0.85,
            "responseMimeType": "application/json",
            "responseSchema": _strip_schema_for_legacy(transition_blind_response_schema()),
        },
    }


def build_transition_reconciliation_prompt(packet, blind_payload):
    reconciliation_packet = {
        "schema": {
            "name": "TRANSITION_RECONCILIATION_PACKET@0.1.0",
            "blind_result_is_immutable": True,
        },
        "blind_result": _safe_copy(blind_payload),
        "full_packet": _transition_prompt_packet(packet),
    }
    return (
        "你是状态转移审计第二次对照复核员。输入包含不可改写的 "
        "TRANSITION_BLIND_READ_RESULT 与完整 SignalTransitionReviewPacket。"
        "你的任务只是对照盲读结果与系统标签、材料性、阻断和 flags 的一致性或张力，"
        "补充人工核验方案与失效条件。不得输出 observed_changes 或任何新的 finding；"
        "Call 1 的 observed_changes 是最终事实解释的唯一来源。\n"
        "请输出符合 reconciliation-only response schema 的 JSON，并额外可写 blind_consistency 与 "
        "blind_differences_cn。不得输出交易建议，不得把张力写成系统错误或执行信号。\n\n"
        "TRANSITION_RECONCILIATION_PACKET JSON：\n"
        f"{json.dumps(reconciliation_packet, ensure_ascii=False)}"
    )


def _transition_decision_packet(decision):
    decision = _as_dict(decision)
    return {
        "lean_before": decision.get("lean_before"),
        "lean_after": decision.get("lean_after"),
        "support_before": decision.get("support_before"),
        "support_after": decision.get("support_after"),
        "confidence_before": decision.get("confidence_before"),
        "confidence_after": decision.get("confidence_after"),
        "block_before": decision.get("block_before"),
        "block_after": decision.get("block_after"),
        "block_entered": decision.get("block_entered"),
        "blocking_reason_after": decision.get("blocking_reason_after"),
    }


def _transition_change_packet(change):
    return {
        "domain": change.get("domain"),
        "field": _transition_public_field(change.get("field")),
        "previous": change.get("previous"),
        "current": change.get("current"),
        "delta_abs": change.get("delta_abs"),
        "delta_relative": change.get("delta_relative"),
        "sign_before": change.get("sign_before"),
        "sign_after": change.get("sign_after"),
        "sign_flip": change.get("sign_flip"),
        "role_before": change.get("role_before"),
        "role_after": change.get("role_after"),
        "materiality": change.get("materiality"),
        "meaning": change.get("meaning"),
        "source_ref": change.get("source_ref"),
    }


def _transition_display_row_packet(row):
    return {
        "domain": row.get("domain"),
        "title_cn": row.get("title_cn"),
        "value_key": row.get("value_key"),
        "previous_display": row.get("previous_display"),
        "current_display": row.get("current_display"),
        "delta_display": row.get("delta_display"),
        "meaning_cn": row.get("meaning_cn"),
        "grade_cn": row.get("grade_cn"),
        "source_note": row.get("source_note"),
    }


def _transition_domain_summary_packet(summary):
    children = [
        _transition_change_packet(item)
        for item in list(summary.get("children") or [])[:8]
        if isinstance(item, dict)
    ]
    return {
        "domain": summary.get("domain"),
        "materiality": summary.get("materiality"),
        "meaning": summary.get("meaning"),
        "raw_change_count": summary.get("raw_change_count"),
        "primary_fields": [
            _transition_public_field(field)
            for field in list(summary.get("primary_fields") or [])[:6]
        ],
        "source_refs": summary.get("source_refs") or [],
        "role_transition": summary.get("role_transition"),
        "previous": summary.get("previous"),
        "current": summary.get("current"),
        "children": children,
    }


def _transition_public_field(field):
    text = str(field or "")
    if text.startswith("factor_cross_section."):
        text = text[len("factor_cross_section."):]
    return text


def _transition_prompt_packet(packet):
    packet = _as_dict(packet)
    display_evidence = []
    display_meanings = []
    for item in list(packet.get("core_transition_display") or [])[:9]:
        item = _as_dict(item)
        display_evidence.append({
            key: item.get(key)
            for key in (
                "domain",
                "title_cn",
                "value_key",
                "previous_display",
                "current_display",
                "delta_display",
                "source_note",
            )
            if key in item
        })
        if item.get("meaning_cn"):
            display_meanings.append({
                "domain": item.get("domain"),
                "title_cn": item.get("title_cn"),
                "meaning_cn": item.get("meaning_cn"),
            })
    return {
        "schema": packet.get("schema"),
        "identity": packet.get("identity"),
        "guardrails": packet.get("guardrails"),
        "EVIDENCE": {
            "comparison": packet.get("comparison"),
            "core_skeleton": packet.get("core_skeleton"),
            "core_transition_display_values": display_evidence,
            "core_domain_coverage_required": sorted(_transition_required_core_domains(packet)),
            "domain_change_summaries": packet.get("domain_change_summaries"),
            "top_material_changes": packet.get("top_material_changes"),
            "recent_5_trajectory": packet.get("recent_5_trajectory"),
            "baseline_24h": packet.get("baseline_24h"),
            "episode_anchor": packet.get("episode_anchor"),
            "trajectory": packet.get("trajectory"),
            "domain_states": packet.get("domain_states"),
            "field_glossary": packet.get("field_glossary"),
            "evidence_catalog": packet.get("evidence_catalog"),
            "evidence_catalog_schema_version": packet.get("evidence_catalog_schema_version"),
            "evidence_catalog_hash": packet.get("evidence_catalog_hash"),
            "evidence_ref_policy": packet.get("evidence_ref_policy"),
        },
        "SYSTEM_ASSERTIONS": {
            "decision_transition": packet.get("decision_transition"),
            "cross_domain_flags": packet.get("cross_domain_flags"),
            "materiality_score": packet.get("materiality_score"),
            "display_meaning_notes": display_meanings,
        },
    }


def build_transition_review_prompt(packet):
    prompt_packet = _transition_prompt_packet(packet)
    return (
        "你是信号审计变化链复核员。你只解释程序已经计算出的 transition delta，"
        "不得重算字段、权重、置信度、材料性、decision、blocking 或 trade_allowed。\n"
        "严格边界：不得使用外部行情，不得把相关性等于因果，"
        "不得输出交易建议、仓位建议、下单建议、止损止盈、对冲或执行层动作。"
        "你的角色是审计旁路认知增强：把分散字段综合为可判断的市场状态路径，"
        "给出可追溯的倾向性解释与人工审计关注方案。\n\n"
        "输入字段分为两类：EVIDENCE 是原始前后值、确定性 delta、单位、比较质量、"
        "字段语义和 evidence_catalog；SYSTEM_ASSERTIONS 是 decision、confidence、blocking、"
        "materiality、cross_domain_flags、展示层 meaning 和系统摘要。形成 observed_changes 时，"
        "SYSTEM_ASSERTIONS 不能作为 evidence_refs，也不能作为事实或倾向的唯一依据；它们只能用于最终一致性对照。"
        "若某项结论只能由 SYSTEM_ASSERTIONS 支持，该项必须写为 UNDETERMINED，并说明缺少独立证据。\n\n"
        "推理顺序（单次 evidence-first，非真盲审）：形成每个 domain 的解释时，先仅基于 "
        "EVIDENCE 中的 core_skeleton、core_transition_display_values、domain_change_summaries、"
        "field_glossary、comparison_quality、evidence_catalog 和原始 delta 形成独立读数；写完独立读数后，"
        "才参考 SYSTEM_ASSERTIONS 做一致性对照。materiality 只用于排序，绝不作为结论。若独立读数与系统 "
        "decision_transition 指向不一致，必须在 cross_factor_interactions 或 "
        "cross_factor_assessments 中如实记录张力，不得向系统结论靠拢。\n\n"
        "impact_cn 必须覆盖的影响轴（缺一即容易成为低信息量复述）：每条 observed_change "
        "必须从以下轴中选择适用项作答，禁止只重述数值："
        "1) 方向骨架关系：支撑还是削弱当前 TMV/TMVF 方向骨架；"
        "2) 门控关系：是否跨过/退出冲击门、宏观硬阻断或其他阈值，使背景扰动升级为主动约束或反之；"
        "3) 幅度充分性：幅度是否足以改变人工审计关注重点，并写入 magnitude_verdict；"
        "4) 跨域关系：是否与其他 domain 共振、冲突、抵消或约束，并指出联合含义。\n\n"
        "证据引用：优先使用 evidence_catalog 中的 evidence_id（例如 EV_DOMAIN_MACRO），"
        "不要让模型自行拼写数组 JSON Pointer；历史兼容时可使用真实存在的 JSON Pointer。"
        "evidence_refs 必须指向 EVIDENCE，不得只指向 SYSTEM_ASSERTIONS。"
        "field_glossary 只能辅助单位与语义，不得作为 observed_changes 的唯一证据。\n\n"
        "人读字段约束：transition_summary_cn、fact_cn、impact_cn、tendency_cn、cross_factor_assessments、"
        "operator_checks 和 invalid_if 不得输出 factor_cross_section、macro_pressure.components、source_ref、"
        "primary_fields、主要字段、来源、核心前后值已入包或任何 dotted field path；这些机器溯源只允许留在 evidence_refs 和原始元数据。"
        "请把事实写成中文的实时事实、实际影响和倾向性，不要把字段清单当作结论。\n\n"
        "核心骨架覆盖：综合论证必须覆盖输入中存在的 TMV、宏观、Funding、Skew、Gamma/GEX、P/C。"
        "稳定或缺失的维度可以放在 cross_factor_assessments 中说明为背景或不可评估，不要伪造成 observed_change；"
        "但不能只讨论 MACRO/P/C 而遗漏现行骨架的其他关键维度。请查看 EVIDENCE.core_domain_coverage_required，"
        "并确保这些 domain 全部出现在 observed_changes.domain 或 cross_factor_assessments.domains 中。\n\n"
        "observed_changes 每项必须包含：domain、effect_target、fact_cn、impact_cn、tendency_cn、"
        "evidence_refs、evidence_status、directional_role、magnitude_verdict、"
        "audit_attention_effect、epistemic_status。fact_cn 只写 packet 明示的客观数值、"
        "状态或缺失情况，不加入原因、评价和材料性语言。impact_cn 写包内证据支持的审计含义，"
        "不是已证实因果。effect_target 说明作用对象，例如方向骨架、门控/阻断、波动空间、拥挤杠杆、"
        "期权需求、信号一致性、数据可靠性或跨因子状态。tendency_cn 写市场状态压力方向，例如“风险约束/压制”、"
        "“支撑”、“中性/缓和”，不是价格预测或操作方向。\n\n"
        "domain 语义规则：MACRO 必须将 DXY、US10Y、VOLQ 等子项聚合为一条，除非是数据质量异常；"
        "Funding 必须区分真实 last_rate/last_funding_rate 与 funding_norm 归一化指标，"
        "真实资金费率必须写成百分比，不得输出 7.117e-05 这类科学计数法；低于 0.01% 阈值的正资金费率只能写为温和多头倾向，不得写成拥挤升温；"
        "归一化指标不得写成真实资金费率；P/C 是非负比率，禁止写“正负符号翻转”，"
        "只能解释保护需求或相对期权需求变化；Gamma/GEX 只解释波动放大、钉住或空间约束，"
        "不得直接写成方向信号；若是净 Gamma USD 名义额必须使用 core_transition_display_values 中的展示口径，历史兼容指标不得伪装成 USD 名义额；"
        "字段缺失、单位不明或口径不可比时，evidence_status 写 PARTIAL/NOT_COMPARABLE/MISSING，"
        "magnitude_verdict 写 indeterminate，epistemic_status 写 NOT_ASSESSABLE，不得编造影响。\n\n"
        "人工审计方案：operator_focus 保留简短中文观察重点；operator_checks 输出 2 至 4 项结构化核验任务，"
        "每项包含 focus_cn、why_cn、strengthens_if_cn、weakens_if_cn、evidence_refs。"
        "只允许使用核对、观察、确认、比较、验证等审计动词；invalid_if 只能写状态/数据条件，"
        "不得写价位、仓位或执行触发器。\n\n"
        "中文表达约束：结论句不得直接复用 raw enum；NEUTRAL 写成“中性”，"
        "MACRO_BLOCKING 写成“宏观硬阻断”，MACRO_SHOCK_BLOCKING 写成“宏观冲击门阻断”，"
        "Headwind 写成“逆风”。禁止使用“评估为关键变化”“被评估为高材料性变化”"
        "“材料性变化”或只说“关键/高”这类无实际审计含义的套话。"
        "candidate_explanations 只允许写非确定性解释关系，causal_status 固定为 UNVERIFIED；"
        "不得输出 HIGH 因果置信，也不得把共同出现写成已证实因果。candidate_explanations 不得编写 packet 未提供的"
        "外部宏观环境、外部宏观事件、宏观流动性、金融条件、风险偏好、宏观经济数据、货币政策、新闻、地缘政治或盘中事件原因；若需要解释，只能写“包内证据可能同源或共振，需人工核对”。"
        "如果做不到这个边界，candidate_explanations 必须输出空数组 []。严禁写“宏观环境变化”“外部宏观事件”“短期资金流向”“导致/触发阻断”这类外部归因或确定性因果句，并避免使用导致、触发、引发、造成、证明这类确定性因果动词。"
        "所有中文可读字段都必须遵守此边界，包括 transition_summary_cn、observed_changes、cross_factor_assessments、candidate_explanations、anomaly_assessment、operator_checks、operator_focus 和 invalid_if。"
        "表达阻断时只能写“宏观冲击状态由观察转为阻断”“宏观冲击阻断已激活”，不得写“美债/美元触发阻断”或“宏观因素导致阻断”。alternative_explanations_cn 也必须遵守同一边界，无法给出包内解释时填空数组。\n\n"
        "transition_summary_cn 最多两句：第一句概括状态路径和主要约束/支撑，"
        "第二句说明是否改变人工关注重点及原因。只输出符合 response schema 的 JSON。\n\n"
        "SignalTransitionReviewPacket:\n"
        + json.dumps(prompt_packet, ensure_ascii=False)
    )


def transition_response_schema():
    text_item = {
        "type": "string",
        "minLength": 0,
        "maxLength": 360,
    }
    refs_schema = {
        "type": "array",
        "maxItems": 8,
        "items": {"type": "string"},
    }
    operator_check_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "focus_cn": text_item,
            "why_cn": text_item,
            "strengthens_if_cn": text_item,
            "weakens_if_cn": text_item,
            "evidence_refs": refs_schema,
        },
        "required": [
            "focus_cn",
            "why_cn",
            "strengthens_if_cn",
            "weakens_if_cn",
            "evidence_refs",
        ],
    }
    cross_factor_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "domains": {
                "type": "array",
                "maxItems": 5,
                "items": {"type": "string"},
            },
            "relation": {
                "type": "string",
                "enum": sorted(TRANSITION_CROSS_FACTOR_RELATIONS),
            },
            "assessment_cn": text_item,
            "evidence_refs": refs_schema,
        },
        "required": ["domains", "relation", "assessment_cn", "evidence_refs"],
    }
    candidate_explanation_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "explanation_cn": text_item,
            "relation": {
                "type": "string",
                "enum": sorted(TRANSITION_EXPLANATION_RELATIONS),
            },
            "supporting_evidence_refs": refs_schema,
            "alternative_explanations_cn": {
                "type": "array",
                "maxItems": 5,
                "items": text_item,
            },
            "causal_status": {
                "type": "string",
                "enum": ["UNVERIFIED"],
            },
        },
        "required": [
            "explanation_cn",
            "relation",
            "supporting_evidence_refs",
            "alternative_explanations_cn",
            "causal_status",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "transition_summary_cn": {
                "type": "string",
                "minLength": 1,
                "maxLength": 520,
            },
            "trajectory_state": {
                "type": "string",
                "enum": sorted(TRANSITION_TRAJECTORY_STATES),
            },
            "signal_continuity": {
                "type": "string",
                "enum": sorted(TRANSITION_CONTINUITY_STATES),
            },
            "observed_changes": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "domain": {"type": "string"},
                        "effect_target": {
                            "type": "string",
                            "enum": sorted(TRANSITION_EFFECT_TARGETS),
                        },
                        "fact_cn": text_item,
                        "impact_cn": text_item,
                        "tendency_cn": text_item,
                        "evidence_refs": refs_schema,
                        "evidence_status": {
                            "type": "string",
                            "enum": sorted(TRANSITION_EVIDENCE_STATUSES),
                        },
                        "directional_role": {
                            "type": "string",
                            "enum": sorted(TRANSITION_DIRECTIONAL_ROLES),
                        },
                        "magnitude_verdict": {
                            "type": "string",
                            "enum": sorted(TRANSITION_MAGNITUDE_VERDICTS),
                        },
                        "audit_attention_effect": {
                            "type": "string",
                            "enum": sorted(TRANSITION_AUDIT_ATTENTION_EFFECTS),
                        },
                        "epistemic_status": {
                            "type": "string",
                            "enum": sorted(TRANSITION_EPISTEMIC_STATUSES),
                        },
                        "materiality": {"type": "string"},
                    },
                    "required": [
                        "domain",
                        "effect_target",
                        "fact_cn",
                        "impact_cn",
                        "tendency_cn",
                        "evidence_refs",
                        "evidence_status",
                        "directional_role",
                        "magnitude_verdict",
                        "audit_attention_effect",
                        "epistemic_status",
                    ],
                },
            },
            "cross_factor_interactions": {
                "type": "array",
                "maxItems": 5,
                "items": text_item,
            },
            "cross_factor_assessments": {
                "type": "array",
                "maxItems": 5,
                "items": cross_factor_schema,
            },
            "candidate_causal_hypotheses": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "hypothesis_cn": text_item,
                        "supporting_fact_ids": {
                            "type": "array",
                            "maxItems": 8,
                            "items": {"type": "string"},
                        },
                        "alternative_explanations_cn": {
                            "type": "array",
                            "maxItems": 5,
                            "items": text_item,
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["LOW", "MEDIUM", "HIGH"],
                        },
                    },
                    "required": [
                        "hypothesis_cn",
                        "supporting_fact_ids",
                        "alternative_explanations_cn",
                        "confidence",
                    ],
                },
            },
            "candidate_explanations": {
                "type": "array",
                "maxItems": 3,
                "items": candidate_explanation_schema,
            },
            "anomaly_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": [
                            "NORMAL_DELTA",
                            "REGIME_SHIFT",
                            "DATA_QUALITY_WARNING",
                            "INSUFFICIENT_COMPARABILITY",
                        ],
                    },
                    "basis_cn": text_item,
                },
                "required": ["state", "basis_cn"],
            },
            "operator_focus": {
                "type": "array",
                "maxItems": 5,
                "items": text_item,
            },
            "invalid_if": {
                "type": "array",
                "maxItems": 5,
                "items": text_item,
            },
            "operator_checks": {
                "type": "array",
                "maxItems": 4,
                "items": operator_check_schema,
            },
            "language_guard": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "distinguishes_observation_from_causality": {"type": "boolean"},
                    "no_external_data": {"type": "boolean"},
                    "no_trading_instruction": {"type": "boolean"},
                },
                "required": [
                    "distinguishes_observation_from_causality",
                    "no_external_data",
                    "no_trading_instruction",
                ],
            },
            "blind_consistency": {
                "type": "string",
                "maxLength": 80,
            },
            "blind_differences_cn": {
                "type": "array",
                "maxItems": 5,
                "items": text_item,
            },
        },
        "required": [
            "transition_summary_cn",
            "trajectory_state",
            "signal_continuity",
            "observed_changes",
            "cross_factor_interactions",
            "cross_factor_assessments",
            "candidate_explanations",
            "anomaly_assessment",
            "operator_focus",
            "invalid_if",
            "operator_checks",
            "language_guard",
        ],
    }


def build_transition_gemini_request(prompt, model=DEFAULT_MODEL):
    del model
    return {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}],
        }],
        "generationConfig": {
            "temperature": 0,
            "topP": 0.85,
            "responseMimeType": "application/json",
            "responseSchema": _strip_schema_for_legacy(transition_response_schema()),
        },
    }


def transition_reconciliation_response_schema():
    text_item = {
        "type": "string",
        "minLength": 0,
        "maxLength": 360,
    }
    refs_schema = {
        "type": "array",
        "maxItems": 8,
        "items": {"type": "string"},
    }
    operator_check_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "focus_cn": text_item,
            "why_cn": text_item,
            "strengthens_if_cn": text_item,
            "weakens_if_cn": text_item,
            "evidence_refs": refs_schema,
        },
        "required": [
            "focus_cn",
            "why_cn",
            "strengthens_if_cn",
            "weakens_if_cn",
            "evidence_refs",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "transition_summary_cn": {
                "type": "string",
                "minLength": 1,
                "maxLength": 520,
            },
            "cross_factor_interactions": {
                "type": "array",
                "maxItems": 5,
                "items": text_item,
            },
            "operator_focus": {
                "type": "array",
                "maxItems": 5,
                "items": text_item,
            },
            "invalid_if": {
                "type": "array",
                "maxItems": 5,
                "items": text_item,
            },
            "operator_checks": {
                "type": "array",
                "maxItems": 4,
                "items": operator_check_schema,
            },
            "language_guard": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "distinguishes_observation_from_causality": {"type": "boolean"},
                    "no_external_data": {"type": "boolean"},
                    "no_trading_instruction": {"type": "boolean"},
                },
                "required": [
                    "distinguishes_observation_from_causality",
                    "no_external_data",
                    "no_trading_instruction",
                ],
            },
            "not_trading_advice": {"type": "boolean"},
            "blind_consistency": {
                "type": "string",
                "maxLength": 80,
            },
            "blind_differences_cn": {
                "type": "array",
                "maxItems": 5,
                "items": text_item,
            },
        },
        "required": [
            "transition_summary_cn",
            "cross_factor_interactions",
            "operator_focus",
            "invalid_if",
            "operator_checks",
            "language_guard",
            "not_trading_advice",
            "blind_consistency",
            "blind_differences_cn",
        ],
    }


def build_transition_reconciliation_gemini_request(prompt, model=DEFAULT_MODEL):
    del model
    return {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}],
        }],
        "generationConfig": {
            "temperature": 0,
            "topP": 0.85,
            "responseMimeType": "application/json",
            "responseSchema": _strip_schema_for_legacy(
                transition_reconciliation_response_schema()),
        },
    }


def _transition_reconciliation_allowed_keys():
    return set(transition_reconciliation_response_schema()["properties"].keys())


class GeminiApiError(RuntimeError):
    def __init__(self, status_code, detail):
        self.status_code = int(status_code)
        self.detail = detail
        super().__init__(f"Gemini HTTP {self.status_code}: {detail}")


def call_gemini(api_key, model, request_body, timeout=60, fallback_api_key=None):
    attempts = []
    if api_key:
        attempts.append(("channel1", api_key))
    if fallback_api_key and fallback_api_key != api_key:
        attempts.append(("channel2", fallback_api_key))
    if not attempts:
        raise RuntimeError("GEMINI_CHANNEL1_API_KEY or GEMINI_CHANNEL2_API_KEY is required")

    last_exc = None
    attempted_routes = []
    for idx, (route, key) in enumerate(attempts):
        attempted_routes.append(route)
        try:
            response = _call_gemini_single_key(key, model, request_body, timeout)
            if isinstance(response, dict):
                response.setdefault("_api_key_route", route)
            return response
        except Exception as exc:
            setattr(exc, "api_key_routes", list(attempted_routes))
            last_exc = exc
            has_next = idx + 1 < len(attempts)
            if not has_next or not _is_retryable_gemini_error(exc):
                raise
    raise last_exc


def _call_gemini_single_key(api_key, model, request_body, timeout):
    try:
        return _post_gemini(api_key, model, request_body, timeout)
    except RuntimeError as exc:
        if "responseFormat" not in str(exc) and "response_format" not in str(exc):
            raise
        return _post_gemini(api_key, model, _legacy_gemini_request(request_body), timeout)


def _is_retryable_gemini_error(exc):
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        return isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
    status_code = getattr(exc, "status_code", None)
    if status_code in RETRYABLE_GEMINI_HTTP_CODES:
        return True
    text = str(exc)
    return any(f"HTTP {code}" in text for code in RETRYABLE_GEMINI_HTTP_CODES)


def _invoke_call_gemini(call_fn, api_key, model, request_body, timeout, fallback_api_key):
    if fallback_api_key:
        return call_fn(api_key, model, request_body, timeout,
                       fallback_api_key=fallback_api_key)
    return call_fn(api_key, model, request_body, timeout)


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
        raise GeminiApiError(exc.code, detail) from exc


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
                     derived_blind=True, llm_call_count=2,
                     llm_call_routes=None):
    payload = _validate_model_payload(payload)
    packet = build_review_packet(card)
    reviewed_at = reviewed_at or _now_iso()
    review = {
        "schema": OUTPUT_SCHEMA_VERSION,
        "status": "OK",
        "provider": "gemini",
        "model": model,
        "reviewed_at": reviewed_at,
        "prompt_version": PROMPT_VERSION,
        "blind_review_mode": "two_call_strict" if derived_blind else "compatibility",
        "llm_call_count": int(llm_call_count),
        "api_key_route": _summarize_call_routes(llm_call_routes),
        "llm_call_routes": list(llm_call_routes or []),
        "input_packet_hash": _sha256_json(packet),
        "blind_packet_hash": _sha256_json(build_blind_theoretical_packet(packet)),
        "summary_cn": payload["summary_cn"],
        "agreement_with_system": payload["agreement_with_system"],
        "caution_level": _policy_caution(payload["caution_level"], packet),
        "theoretical_active_view": _normalize_theoretical_active_view(
            payload["theoretical_active_view"], derived_blind=derived_blind),
        "gamma_regime_lens": _normalize_gamma_regime_lens(
            payload["gamma_regime_lens"]),
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
    written = 0
    skipped = 0
    errors = 0
    attempted = 0
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
            blind_request = build_blind_gemini_request(blind_prompt, model=model)
            blind_raw_response = _invoke_call_gemini(
                call_gemini, api_key, model, blind_request, timeout,
                fallback_api_key)
            blind_payload = _validate_blind_payload(
                parse_gemini_response(blind_raw_response))
            prompt = build_prompt(packet, blind_payload)
            request_body = build_gemini_request(prompt, model=model)
            raw_response = _invoke_call_gemini(
                call_gemini, api_key, model, request_body, timeout,
                fallback_api_key)
            payload = parse_gemini_response(raw_response)
            payload["theoretical_active_view"] = blind_payload["theoretical_active_view"]
            payload["gamma_regime_lens"] = blind_payload["gamma_regime_lens"]
            review = build_llm_review(card, payload, model=model,
                                      reviewed_at=reviewed_at,
                                      derived_blind=True,
                                      llm_call_count=2,
                                      llm_call_routes=[
                                          _api_key_route(blind_raw_response),
                                          _api_key_route(raw_response),
                                      ])
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
            error_routes = _exception_call_routes(exc)
            _append_jsonl(reviews_output, {
                "card_id": card_id,
                "llm_review": {
                    "schema": OUTPUT_SCHEMA_VERSION,
                    "status": "ERROR",
                    "provider": "gemini",
                    "model": model,
                    "reviewed_at": reviewed_at or _now_iso(),
                    "prompt_version": PROMPT_VERSION,
                    "api_key_route": _summarize_call_routes(error_routes),
                    "llm_call_routes": error_routes,
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
        "attempted_cards": attempted,
        "written_reviews": written,
        "skipped_cards": skipped,
        "errors": errors,
        "model": model,
    }


def build_transition_llm_review(transition, payload, model=DEFAULT_MODEL,
                                reviewed_at=None, llm_call_routes=None,
                                blind_review_mode="single_call_evidence_first",
                                llm_call_count=1, blind_packet_hash=None,
                                blind_result_hash=None, blind_consistency=None,
                                blind_differences_cn=None):
    payload = _validate_transition_payload(payload)
    packet = build_transition_review_packet(transition)
    reviewed_at = reviewed_at or _now_iso()
    language_guard = _as_dict(payload.get("language_guard"))
    language_guard = {
        "distinguishes_observation_from_causality": bool(
            language_guard.get("distinguishes_observation_from_causality")),
        "no_external_data": bool(language_guard.get("no_external_data")),
        "no_trading_instruction": True,
    }
    review = {
        "schema_name": "SignalTransitionLlmReview",
        "schema_version": TRANSITION_OUTPUT_SCHEMA_VERSION,
        "status": "OK",
        "provider": "gemini",
        "model": model,
        "reviewed_at": reviewed_at,
        "prompt_version": TRANSITION_PROMPT_VERSION,
        "api_key_route": _summarize_call_routes(llm_call_routes),
        "llm_call_routes": list(llm_call_routes or []),
        "input_packet_hash": _sha256_json(packet),
        "evidence_catalog_schema_version": packet.get("evidence_catalog_schema_version"),
        "evidence_catalog_hash": packet.get("evidence_catalog_hash"),
        "blind_review_mode": blind_review_mode,
        "llm_call_count": llm_call_count,
        "transition_summary_cn": _sanitize_transition_cn(
            str(payload.get("transition_summary_cn") or ""))[:520],
        "trajectory_state": _transition_enum(
            payload.get("trajectory_state"), TRANSITION_TRAJECTORY_STATES),
        "signal_continuity": _transition_enum(
            payload.get("signal_continuity"), TRANSITION_CONTINUITY_STATES),
        "observed_changes": _normalize_observed_changes(
            payload.get("observed_changes"), packet),
        "cross_factor_interactions": _trim_list(
            _sanitize_transition_list(payload.get("cross_factor_interactions")),
            limit=5),
        "cross_factor_assessments": _normalize_cross_factor_assessments(
            payload.get("cross_factor_assessments"), packet),
        "candidate_explanations": _normalize_candidate_explanations(
            payload.get("candidate_explanations"), packet),
        "candidate_causal_hypotheses": [],
        "anomaly_assessment": _normalize_anomaly_assessment(
            payload.get("anomaly_assessment")),
        "operator_focus": _trim_list(
            _sanitize_transition_list(payload.get("operator_focus")), limit=5),
        "invalid_if": _trim_list(
            _sanitize_transition_list(payload.get("invalid_if")), limit=5),
        "operator_checks": _normalize_operator_checks(
            payload.get("operator_checks"), packet),
        "language_guard": language_guard,
        "not_trading_advice": True,
    }
    if blind_packet_hash:
        review["blind_packet_hash"] = blind_packet_hash
    if blind_result_hash:
        review["blind_result_hash"] = blind_result_hash
    if blind_consistency:
        review["blind_consistency"] = str(blind_consistency)[:80]
    if blind_differences_cn:
        review["blind_differences_cn"] = _trim_list(
            _sanitize_transition_list(blind_differences_cn), limit=5)
    review["policy_validation"] = _transition_policy_validation(review, packet)
    return review


def _merge_transition_blind_and_reconciliation_payload(blind_payload, reconciliation_payload):
    blind_payload = _as_dict(blind_payload)
    reconciliation_payload = _as_dict(reconciliation_payload)
    allowed_reconciliation_keys = _transition_reconciliation_allowed_keys()
    reconciliation_payload = {
        key: _safe_copy(value)
        for key, value in reconciliation_payload.items()
        if key in allowed_reconciliation_keys
    }
    merged = dict(reconciliation_payload)
    for key in ("trajectory_state", "signal_continuity", "observed_changes"):
        merged[key] = _safe_copy(blind_payload.get(key))
    for key in ("transition_summary_cn", "cross_factor_interactions",
                "cross_factor_assessments", "candidate_explanations",
                "anomaly_assessment", "operator_focus", "invalid_if",
                "operator_checks", "language_guard"):
        if key not in merged:
            merged[key] = _safe_copy(blind_payload.get(key))
    return merged


def generate_transition_reviews(ledger, reviews_output, api_key=None,
                                fallback_api_key=None, model=DEFAULT_MODEL,
                                limit=20, timeout=60, call_gemini=call_gemini,
                                reviewed_at=None,
                                transition_blind_mode="single_call_evidence_first"):
    if transition_blind_mode not in TRANSITION_BLIND_MODES:
        raise ValueError("invalid transition_blind_mode")
    ledger = Path(ledger)
    reviews_output = Path(reviews_output)
    transitions = _read_jsonl(ledger)
    transitions = [
        item for item in transitions
        if item.get("transition_id") and item.get("llm_review_required") is True
    ]
    transitions = sorted(transitions, key=_transition_sort_key, reverse=True)
    done = _read_transition_review_ids(reviews_output)
    written = 0
    skipped = 0
    errors = 0
    attempted = 0
    for transition in transitions:
        if limit and attempted >= limit:
            break
        transition_id = transition.get("transition_id")
        if transition_id in done:
            skipped += 1
            continue
        attempted += 1
        try:
            packet = build_transition_review_packet(transition)
            if transition_blind_mode == "two_call_strict":
                blind_packet = build_transition_blind_delta_packet(packet)
                blind_prompt = build_transition_blind_prompt(packet)
                blind_request = build_transition_blind_gemini_request(
                    blind_prompt, model=model)
                blind_raw_response = _invoke_call_gemini(
                    call_gemini, api_key, model, blind_request, timeout,
                    fallback_api_key)
                blind_payload = _validate_transition_payload(
                    parse_gemini_response(blind_raw_response))
                reconciliation_prompt = build_transition_reconciliation_prompt(
                    packet, blind_payload)
                reconciliation_request = build_transition_reconciliation_gemini_request(
                    reconciliation_prompt, model=model)
                reconciliation_raw_response = _invoke_call_gemini(
                    call_gemini, api_key, model, reconciliation_request,
                    timeout, fallback_api_key)
                reconciliation_payload = _validate_transition_reconciliation_payload(
                    parse_gemini_response(reconciliation_raw_response))
                payload = _merge_transition_blind_and_reconciliation_payload(
                    blind_payload, reconciliation_payload)
                review = build_transition_llm_review(
                    transition,
                    payload,
                    model=model,
                    reviewed_at=reviewed_at,
                    llm_call_routes=[
                        _api_key_route(blind_raw_response),
                        _api_key_route(reconciliation_raw_response),
                    ],
                    blind_review_mode="transition_two_call_strict",
                    llm_call_count=2,
                    blind_packet_hash=_sha256_json(blind_packet),
                    blind_result_hash=_sha256_json(blind_payload),
                    blind_consistency=reconciliation_payload.get("blind_consistency"),
                    blind_differences_cn=reconciliation_payload.get("blind_differences_cn"),
                )
            else:
                prompt = build_transition_review_prompt(packet)
                request_body = build_transition_gemini_request(prompt, model=model)
                raw_response = _invoke_call_gemini(
                    call_gemini, api_key, model, request_body, timeout,
                    fallback_api_key)
                payload = parse_gemini_response(raw_response)
                review = build_transition_llm_review(
                    transition,
                    payload,
                    model=model,
                    reviewed_at=reviewed_at,
                    llm_call_routes=[_api_key_route(raw_response)],
                )
            _append_jsonl(reviews_output, {
                "transition_id": transition_id,
                "current_card_id": transition.get("current_card_id"),
                "symbol": transition.get("symbol"),
                "current_ts_ms": transition.get("current_ts_ms"),
                "transition_llm_review": review,
            })
            done.add(transition_id)
            written += 1
        except Exception as exc:  # keep transition sidecar soft-fail per record
            errors += 1
            safe_error = _redact_sensitive_text(str(exc))[:220]
            error_routes = _exception_call_routes(exc)
            _append_jsonl(reviews_output, {
                "transition_id": transition_id,
                "current_card_id": transition.get("current_card_id"),
                "symbol": transition.get("symbol"),
                "transition_llm_review": _transition_error_review(
                    model, reviewed_at, safe_error, error_routes,
                    transition_blind_mode),
            })
    return {
        "ledger": str(ledger),
        "reviews_output": str(reviews_output),
        "attempted_transitions": attempted,
        "written_reviews": written,
        "skipped_transitions": skipped,
        "errors": errors,
        "model": model,
        "transition_blind_mode": transition_blind_mode,
    }


def _transition_error_review(model, reviewed_at, safe_error, error_routes,
                             transition_blind_mode="single_call_evidence_first"):
    review_mode = (
        "transition_two_call_strict"
        if transition_blind_mode == "two_call_strict"
        else "single_call_evidence_first"
    )
    return {
        "schema_name": "SignalTransitionLlmReview",
        "schema_version": TRANSITION_OUTPUT_SCHEMA_VERSION,
        "status": "ERROR",
        "provider": "gemini",
        "model": model,
        "reviewed_at": reviewed_at or _now_iso(),
        "prompt_version": TRANSITION_PROMPT_VERSION,
        "blind_review_mode": review_mode,
        "llm_call_count": 2 if transition_blind_mode == "two_call_strict" else 1,
        "api_key_route": _summarize_call_routes(error_routes),
        "llm_call_routes": error_routes,
        "transition_summary_cn": "LLM 变化链解释生成失败，保留程序化 transition ledger 结论。",
        "trajectory_state": "UNKNOWN",
        "signal_continuity": "UNKNOWN",
        "observed_changes": [],
        "cross_factor_interactions": [],
        "cross_factor_assessments": [],
        "candidate_explanations": [],
        "candidate_causal_hypotheses": [],
        "anomaly_assessment": {
            "state": "DATA_QUALITY_WARNING",
            "basis_cn": "LLM 调用或解析失败：" + safe_error,
        },
        "operator_focus": ["仅依据程序化变化链继续人工复核。"],
        "invalid_if": [],
        "operator_checks": [],
        "language_guard": {
            "distinguishes_observation_from_causality": True,
            "no_external_data": True,
            "no_trading_instruction": True,
        },
        "not_trading_advice": True,
        "policy_validation": {
            "passed": False,
            "no_external_data": True,
            "no_trading_instruction": True,
            "distinguishes_observation_from_causality": True,
            "not_trading_advice": True,
            "observed_changes_have_fact_impact_tendency": False,
            "no_materiality_boilerplate": True,
            "raw_enum_leaks": [],
            "trading_instruction_terms": [],
            "unit_mislabel_terms": [],
            "materiality_boilerplate_terms": [],
            "invalid_evidence_refs": [],
            "causal_overclaim_terms": [],
            "issue_codes": ["llm_generation_error"],
            "severity": "ERROR",
            "render_state": "DEGRADED_LLM_TEXT",
        },
    }


def _validate_transition_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("transition model output must be object")
    missing = [
        key for key in transition_response_schema()["required"]
        if key not in payload
    ]
    if missing:
        raise ValueError("transition model output missing fields: "
                         + ", ".join(missing))
    if str(payload.get("trajectory_state") or "").upper() not in TRANSITION_TRAJECTORY_STATES:
        raise ValueError("invalid trajectory_state")
    if str(payload.get("signal_continuity") or "").upper() not in TRANSITION_CONTINUITY_STATES:
        raise ValueError("invalid signal_continuity")
    if not isinstance(payload.get("observed_changes"), list):
        raise ValueError("observed_changes must be list")
    if not isinstance(payload.get("cross_factor_interactions"), list):
        raise ValueError("cross_factor_interactions must be list")
    if not isinstance(payload.get("cross_factor_assessments"), list):
        raise ValueError("cross_factor_assessments must be list")
    if not isinstance(payload.get("candidate_explanations"), list):
        raise ValueError("candidate_explanations must be list")
    if not isinstance(payload.get("operator_focus"), list):
        raise ValueError("operator_focus must be list")
    if not isinstance(payload.get("invalid_if"), list):
        raise ValueError("invalid_if must be list")
    if not isinstance(payload.get("operator_checks"), list):
        raise ValueError("operator_checks must be list")
    # language_guard self-reports (no_external_data / no_trading_instruction /
    # distinguishes_observation_from_causality) are recorded as advisory metadata,
    # not enforced as a gate: transition LLM review is an audit bypass reference and
    # does not change confidence/factors/release. Only structural/format integrity is
    # validated here so the frontend audit page renders correctly.
    return payload


def _validate_transition_reconciliation_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("transition reconciliation output must be object")
    if "observed_changes" in payload:
        raise ValueError("transition reconciliation output must not include observed_changes")
    allowed_keys = _transition_reconciliation_allowed_keys()
    unexpected = sorted(str(key) for key in payload.keys() if key not in allowed_keys)
    if unexpected:
        raise ValueError("transition reconciliation output unexpected fields: "
                         + ", ".join(unexpected))
    missing = [
        key for key in transition_reconciliation_response_schema()["required"]
        if key not in payload
    ]
    if missing:
        raise ValueError("transition reconciliation output missing fields: "
                         + ", ".join(missing))
    if not isinstance(payload.get("cross_factor_interactions"), list):
        raise ValueError("cross_factor_interactions must be list")
    if not isinstance(payload.get("operator_focus"), list):
        raise ValueError("operator_focus must be list")
    if not isinstance(payload.get("invalid_if"), list):
        raise ValueError("invalid_if must be list")
    if not isinstance(payload.get("operator_checks"), list):
        raise ValueError("operator_checks must be list")
    guard = _as_dict(payload.get("language_guard"))
    if guard.get("distinguishes_observation_from_causality") is not True:
        raise ValueError("transition reconciliation must distinguish observation from causality")
    if guard.get("no_external_data") is not True:
        raise ValueError("transition reconciliation must not use external data")
    if guard.get("no_trading_instruction") is not True:
        raise ValueError("transition reconciliation must forbid trading instruction")
    if payload.get("not_trading_advice") is not True:
        raise ValueError("transition reconciliation must be marked not trading advice")
    return payload


def _transition_enum(value, allowed):
    text = str(value or "UNKNOWN").upper()
    return text if text in allowed else "UNKNOWN"


def _transition_lower_enum(value, allowed, fallback):
    text = str(value or fallback).lower()
    return text if text in allowed else fallback


def _normalize_observed_changes(items, packet=None):
    rows = []
    for item in list(items or [])[:8]:
        item = _as_dict(item)
        refs = _normalize_evidence_refs(item.get("evidence_refs"), packet, limit=8)
        fact_result = _derive_transition_fact_cn(
            item.get("domain"), refs["valid"], packet,
            str(item.get("fact_cn") or ""),
            return_issues=True)
        if isinstance(fact_result, tuple):
            fact_cn, normalization_issues = fact_result
        else:
            fact_cn = fact_result
            normalization_issues = []
        impact_cn = _sanitize_transition_impact_cn(
            str(item.get("impact_cn") or item.get("meaning_cn") or fact_cn))
        tendency_cn = _sanitize_transition_tendency_cn(
            item.get("domain"), item.get("tendency_cn"), impact_cn or fact_cn)
        evidence_status = _transition_enum(
            item.get("evidence_status"), TRANSITION_EVIDENCE_STATUSES)
        if evidence_status == "UNKNOWN":
            evidence_status = "PARTIAL" if refs["valid"] else "MISSING"
        effect_target = _transition_enum(
            item.get("effect_target"), TRANSITION_EFFECT_TARGETS)
        if effect_target == "UNKNOWN":
            effect_target = _effect_target_from_domain(item.get("domain"))
        directional_role = _transition_enum(
            item.get("directional_role"), TRANSITION_DIRECTIONAL_ROLES)
        if directional_role == "UNKNOWN":
            directional_role = _directional_role_from_tendency(tendency_cn)
        magnitude = _transition_lower_enum(
            item.get("magnitude_verdict"),
            TRANSITION_MAGNITUDE_VERDICTS,
            "indeterminate")
        audit_attention = _transition_enum(
            item.get("audit_attention_effect"),
            TRANSITION_AUDIT_ATTENTION_EFFECTS)
        if audit_attention == "UNKNOWN":
            audit_attention = "UNDETERMINED"
        epistemic = _transition_enum(
            item.get("epistemic_status"), TRANSITION_EPISTEMIC_STATUSES)
        if epistemic == "UNKNOWN":
            epistemic = "SUPPORTED_INFERENCE" if impact_cn else "NOT_ASSESSABLE"
        rows.append({
            "domain": str(item.get("domain") or "")[:80],
            "effect_target": effect_target,
            "fact_cn": fact_cn[:240],
            "impact_cn": impact_cn[:260],
            "tendency_cn": tendency_cn[:80],
            "evidence_refs": refs["valid"],
            "_invalid_evidence_refs": refs["invalid"],
            "_normalization_issues": normalization_issues,
            "evidence_status": evidence_status,
            "directional_role": directional_role,
            "magnitude_verdict": magnitude,
            "audit_attention_effect": audit_attention,
            "epistemic_status": epistemic,
            "materiality": str(item.get("materiality") or "")[:40],
        })
    return rows


def _sanitize_transition_list(items):
    return [_sanitize_transition_cn(str(item or "")) for item in list(items or [])]


def _sanitize_transition_cn(text):
    replacements = (
        ("Mild Headwind", "轻度逆风"),
        ("Strong Headwind", "强逆风"),
        ("MACRO_SHOCK_GATE_BLOCK", "宏观冲击门阻断状态"),
        ("MACRO_SHOCK_GATE_STATE", "宏观冲击门状态"),
        ("MACRO_SHOCK_BLOCKING", "宏观冲击门阻断"),
        ("MACRO_SHOCK", "宏观冲击"),
        ("MACRO_BLOCKING", "宏观硬阻断"),
        ("MACRO Headwind", "宏观逆风"),
        ("MACRO", "宏观"),
        ("Neutral", "中性"),
        ("Mild", "轻度"),
        ("Strong", "强"),
        ("Headwind", "逆风"),
        ("WAIT_CONFIRMATION", "等待确认"),
        ("POSITIVE_GAMMA_PINNING", "正 Gamma 钉住"),
        ("NEGATIVE_GAMMA", "负 Gamma"),
        ("BULLISH", "偏多"),
        ("BEARISH", "偏空"),
        ("Bullish", "偏多"),
        ("Bearish", "偏空"),
        ("CRITICAL", "关键"),
        ("HIGH", "高"),
        ("BLOCKED", "已被阻断"),
        ("WATCH", "观察"),
        ("BLOCK", "阻断"),
        ("CLEAR", "清除"),
        ("NONE", "无"),
        ("NEUTRAL", "中性"),
        ("FUNDING", "资金费率"),
        ("SKEW_REVERSAL", "偏斜反转"),
        ("TRANSITION", "过渡区"),
        ("P_C_RATIO", "P/C 比例"),
        ("macro_shock.state", "宏观冲击门状态"),
        ("发生正负符号翻转", "出现结构变化"),
        ("不构成拥挤升温", "未达到拥挤阈值"),
        ("不代表拥挤升温", "未达到拥挤阈值"),
        ("不是拥挤升温", "未达到拥挤阈值"),
    )
    result = str(text or "")
    for old, new in replacements:
        result = result.replace(old, new)
    for old, new in (
            ("关键（关键）", "关键"),
            ("高（高）", "高"),
            ("观察（观察）", "观察"),
            ("阻断（阻断）", "阻断"),
            ("等待确认（等待确认）", "等待确认")):
        result = result.replace(old, new)
    return result


def _sanitize_transition_fact_cn(text):
    result = _strip_materiality_boilerplate(_sanitize_transition_cn(text))
    return result


def _derive_transition_fact_cn(domain, refs, packet, fallback, return_issues=False):
    domain_label = str(domain or "").upper() or "UNKNOWN"
    fallback_text = _sanitize_transition_fact_cn(fallback)
    issues = _transition_human_numeric_issues(domain_label, fallback_text)
    if (fallback_text and not _transition_text_has_raw_field_leak(fallback_text)
            and not issues):
        return (fallback_text, []) if return_issues else fallback_text
    summaries = []
    for ref in list(refs or [])[:3]:
        summary = _transition_safe_ref_summary(ref, packet)
        if summary:
            summaries.append(summary)
    if not summaries and issues:
        summary = _transition_display_summary_for_domain(domain_label, packet)
        if summary:
            summaries.append(summary)
    if summaries:
        fact = _sanitize_transition_fact_cn(
            f"{domain_label}：" + "；".join(summaries))
        return (fact, issues) if return_issues else fact
    return (fallback_text, issues) if return_issues else fallback_text


def _transition_human_numeric_issues(domain, text):
    text = str(text or "")
    issues = []
    if re.search(r"[-+]?\d+(?:\.\d+)?[eE][-+]?\d+", text):
        issues.append("scientific_notation_in_human_text")
        issues.append("numeric_display_mismatch")
    domain = _transition_core_domain_alias(domain)
    if domain == "GAMMA" and re.search(r"[-+]?(?:0?\.\d+|[1-9]\d{0,2}(?:\.\d+)?)\s*(?:USD|美元)", text, flags=re.IGNORECASE):
        issues.append("gamma_usd_unit_misread")
        issues.append("numeric_display_mismatch")
    if domain == "FUNDING" and re.search(r"(?<![%\w])0\.0{3,}\d+", text):
        issues.append("funding_rate_percent_misread")
        issues.append("numeric_display_mismatch")
    return sorted(set(issues))


def _transition_display_summary_for_domain(domain, packet):
    domain = _transition_core_domain_alias(domain)
    for item in list(_as_dict(packet).get("core_transition_display") or []):
        item = _as_dict(item)
        if _transition_core_domain_alias(item.get("domain")) != domain:
            continue
        title = item.get("title_cn") or item.get("domain") or domain
        previous = item.get("previous_display")
        current = item.get("current_display")
        if previous is None and current is None:
            continue
        summary = f"{title}：{_compact_transition_value(previous)} -> {_compact_transition_value(current)}"
        meaning = item.get("meaning_cn")
        if meaning:
            summary += f"，{_compact_transition_value(meaning)}"
        return summary
    for item in list(_as_dict(_as_dict(packet).get("core_skeleton")).get("domains") or []):
        item = _as_dict(item)
        if _transition_core_domain_alias(item.get("domain")) != domain:
            continue
        previous, current = _transition_core_display_pair_from_skeleton(domain, item)
        if previous is None and current is None:
            continue
        title = _transition_domain_title_cn(domain)
        summary = f"{title}：{_compact_transition_value(previous)} -> {_compact_transition_value(current)}"
        meaning = _transition_core_skeleton_meaning_cn(domain, item)
        if meaning:
            summary += f"，{meaning}"
        return summary
    return ""


def _transition_core_display_pair_from_skeleton(domain, item):
    previous = _as_dict(_as_dict(item).get("previous"))
    current = _as_dict(_as_dict(item).get("current"))
    keys = {
        "FUNDING": ("last_rate", "last_funding_rate"),
        "GAMMA": ("net_gamma_notional_usd", "net_gamma_notional"),
    }.get(domain, ())
    for key in keys:
        if key in previous or key in current:
            return (
                _transition_domain_value_text(domain, key, previous.get(key)),
                _transition_domain_value_text(domain, key, current.get(key)),
            )
    return None, None


def _transition_domain_value_text(domain, key, value):
    number = _transition_number(value)
    if number is None:
        return _compact_transition_value(value)
    if domain == "FUNDING" and key in {"last_rate", "last_funding_rate"}:
        return _transition_trim_number(number * 100.0, 6) + "%"
    if domain == "GAMMA" and key in {"net_gamma_notional_usd", "net_gamma_notional"}:
        if abs(number) < 1000:
            return _transition_trim_number(number, 4)
        return _transition_usd_notional_text(number)
    return _transition_trim_number(number, 4)


def _transition_domain_title_cn(domain):
    return {
        "FUNDING": "Funding（期货资金费率）",
        "GAMMA": "Gamma（净 Gamma）",
    }.get(domain, domain)


def _transition_core_skeleton_meaning_cn(domain, item):
    previous = _as_dict(_as_dict(item).get("previous"))
    current = _as_dict(_as_dict(item).get("current"))
    if domain == "FUNDING":
        current_rate = _transition_number(
            current.get("last_rate", current.get("last_funding_rate")))
        if current_rate is not None and current_rate > 0 and current_rate < 0.0001:
            return "资金费率低于 0.01% 阈值，当前为温和多头倾向。"
    if domain == "GAMMA":
        values = [
            _transition_number(previous.get("net_gamma_notional_usd", previous.get("net_gamma_notional"))),
            _transition_number(current.get("net_gamma_notional_usd", current.get("net_gamma_notional"))),
        ]
        numeric = [value for value in values if value is not None]
        if numeric and max(abs(value) for value in numeric) < 1000:
            return "旧卡兼容推导的 Gamma 指标，不伪装为 USD 名义额。"
        return "净 Gamma USD 名义额，用于解释波动空间与空间约束，不是方向信号。"
    return ""


def _transition_trim_number(value, digits):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) < 0.5 * (10 ** -digits):
        number = 0.0
    text = ("{0:." + str(digits) + "f}").format(number)
    return text.rstrip("0").rstrip(".") or "0"


def _transition_usd_notional_text(value):
    sign = "-" if value < 0 else ""
    amount = abs(float(value))
    if amount >= 1_000_000_000:
        return sign + "$" + _transition_trim_number(amount / 1_000_000_000, 2) + "B"
    if amount >= 1_000_000:
        return sign + "$" + _transition_trim_number(amount / 1_000_000, 2) + "M"
    if amount >= 1_000:
        return sign + "$" + _transition_trim_number(amount / 1_000, 2) + "K"
    return sign + "$" + _transition_trim_number(amount, 2)


def _transition_safe_ref_summary(ref, packet):
    packet = _as_dict(packet)
    catalog_item = _transition_catalog_item(packet, ref)
    pointer = catalog_item.get("pointer") if catalog_item else ref
    value = _json_pointer_value(packet, pointer)
    if isinstance(value, dict):
        domain = value.get("domain")
        title = value.get("title_cn") or domain
        previous = value.get("previous_display") or value.get("previous")
        current = value.get("current_display") or value.get("current")
        if title and (previous is not None or current is not None):
            summary = f"{title}：{_compact_transition_value(previous)} -> {_compact_transition_value(current)}"
            meaning = value.get("meaning_cn")
            if meaning:
                summary += f"，{_compact_transition_value(meaning)}"
            return summary
        if domain:
            return f"{domain} 结构化证据可用于审计说明"
    if catalog_item:
        summary = str(catalog_item.get("value_summary_cn") or catalog_item.get("label_cn") or "")
        if not _transition_text_has_raw_field_leak(summary):
            return summary
    if value is not None:
        summary = _compact_transition_value(value)
        if not _transition_text_has_raw_field_leak(summary):
            return summary
    return ""


def _transition_text_has_raw_field_leak(text):
    return bool(_transition_raw_field_leak_terms(text))


def _transition_raw_field_leak_terms(text):
    found = []
    value = str(text or "")
    for label, pattern in TRANSITION_RAW_FIELD_LEAK_PATTERNS:
        if pattern.search(value):
            found.append(label)
    return sorted(set(found))


def _compact_transition_value(value):
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text[:160]


def _sanitize_transition_impact_cn(text):
    result = _strip_materiality_boilerplate(_sanitize_transition_cn(text))
    if result and not result.endswith(("。", "！", "？")):
        result += "。"
    return result


def _strip_materiality_boilerplate(text):
    result = str(text or "")
    for phrase in (
            "被评估为关键变化",
            "被评估为高材料性变化",
            "被评估为中材料性变化",
            "被评估为低材料性变化",
            "评估为关键变化",
            "评估为高材料性变化",
            "高材料性变化",
            "关键变化",
            "材料性变化",
            "材料性"):
        result = result.replace(phrase, "")
    result = result.replace("，，", "，").replace("。。", "。")
    result = result.replace("，。", "。").replace("；。", "。")
    return result.strip(" ；，。")


def _sanitize_transition_tendency_cn(domain, value, fallback_text):
    result = _strip_materiality_boilerplate(
        _sanitize_transition_cn(str(value or ""))).strip(" ；，。")
    if result:
        return result
    text = str(fallback_text or "")
    domain = str(domain or "").upper()
    if domain in ("MACRO", "GAMMA") and any(
            key in text for key in ("上升", "加深", "恶化", "转弱", "压力")):
        return "利空/风险约束"
    if domain in ("TMV", "TMVF") and any(
            key in text for key in ("转弱", "下降", "降至", "走弱")):
        return "偏空"
    if domain in ("TMV", "TMVF") and any(
            key in text for key in ("改善", "上升", "回升")):
        return "偏多/支撑"
    if domain == "FUNDING" and any(
            key in text for key in ("回落", "转负", "消失", "缓和")):
        return "中性/拥挤缓和"
    if domain in ("P_C_RATIO", "P/C") and any(
            key in text for key in ("回落", "下降", "降低")):
        return "中性/保护需求缓和"
    if domain == "CONFLICT" and any(
            key in text for key in ("上升", "升高", "扩大")):
        return "利空/分歧升高"
    if domain == "CONFLICT" and any(
            key in text for key in ("回落", "下降", "缓和")):
        return "中性/分歧缓和"
    if any(key in text for key in ("风险", "压制", "阻断", "逆风")):
        return "利空/风险约束"
    if any(key in text for key in ("支撑", "改善", "缓和")):
        return "利多/支撑" if "支撑" in text else "中性/缓和"
    return "中性/需结合其他维度"


def _normalize_candidate_explanations(items, packet=None):
    rows = []
    for item in list(items or [])[:3]:
        item = _as_dict(item)
        relation = _transition_enum(
            item.get("relation"), TRANSITION_EXPLANATION_RELATIONS)
        if relation == "UNKNOWN":
            relation = "CONSISTENT_WITH"
        refs = _normalize_evidence_refs(
            item.get("supporting_evidence_refs") or item.get("evidence_refs"),
            packet,
            limit=8)
        rows.append({
            "explanation_cn": _sanitize_transition_cn(
                str(item.get("explanation_cn")
                    or item.get("hypothesis_cn")
                    or ""))[:360],
            "relation": relation,
            "supporting_evidence_refs": refs["valid"],
            "_invalid_evidence_refs": refs["invalid"],
            "alternative_explanations_cn": _trim_list(
                _sanitize_transition_list(item.get("alternative_explanations_cn")),
                limit=5),
            "causal_status": "UNVERIFIED",
        })
    return rows


def _normalize_causal_hypotheses(items):
    rows = []
    for item in list(items or [])[:3]:
        item = _as_dict(item)
        confidence = str(item.get("confidence") or "LOW").upper()
        if confidence not in {"LOW", "MEDIUM", "HIGH"}:
            confidence = "LOW"
        rows.append({
            "hypothesis_cn": _sanitize_transition_cn(
                str(item.get("hypothesis_cn") or ""))[:360],
            "supporting_fact_ids": _trim_list(
                item.get("supporting_fact_ids"), limit=8),
            "alternative_explanations_cn": _trim_list(
                _sanitize_transition_list(item.get("alternative_explanations_cn")),
                limit=5),
            "confidence": confidence,
        })
    return rows


def _normalize_anomaly_assessment(value):
    value = _as_dict(value)
    state = str(value.get("state") or "NORMAL_DELTA").upper()
    if state not in {
            "NORMAL_DELTA",
            "REGIME_SHIFT",
            "DATA_QUALITY_WARNING",
            "INSUFFICIENT_COMPARABILITY",
    }:
        state = "NORMAL_DELTA"
    return {
        "state": state,
        "basis_cn": _sanitize_transition_cn(
            str(value.get("basis_cn") or ""))[:360],
    }


def _normalize_cross_factor_assessments(items, packet=None):
    rows = []
    for item in list(items or [])[:5]:
        item = _as_dict(item)
        relation = _transition_enum(
            item.get("relation"), TRANSITION_CROSS_FACTOR_RELATIONS)
        if relation == "UNKNOWN":
            relation = "CO_MOVEMENT"
        refs = _normalize_evidence_refs(item.get("evidence_refs"), packet, limit=8)
        domains = [
            str(value or "")[:40]
            for value in list(item.get("domains") or [])[:5]
            if str(value or "").strip()
        ]
        rows.append({
            "domains": domains,
            "relation": relation,
            "assessment_cn": _sanitize_transition_cn(
                str(item.get("assessment_cn") or ""))[:360],
            "evidence_refs": refs["valid"],
            "_invalid_evidence_refs": refs["invalid"],
        })
    return rows


def _normalize_operator_checks(items, packet=None):
    rows = []
    for item in list(items or [])[:4]:
        item = _as_dict(item)
        refs = _normalize_evidence_refs(item.get("evidence_refs"), packet, limit=8)
        rows.append({
            "focus_cn": _sanitize_transition_cn(str(item.get("focus_cn") or ""))[:240],
            "why_cn": _sanitize_transition_cn(str(item.get("why_cn") or ""))[:260],
            "strengthens_if_cn": _sanitize_transition_cn(
                str(item.get("strengthens_if_cn") or ""))[:260],
            "weakens_if_cn": _sanitize_transition_cn(
                str(item.get("weakens_if_cn") or ""))[:260],
            "evidence_refs": refs["valid"],
            "_invalid_evidence_refs": refs["invalid"],
        })
    return rows


def _directional_role_from_tendency(text):
    value = str(text or "")
    if any(key in value for key in ("风险", "压制", "逆风", "利空", "约束")):
        return "RISK_CONSTRAINT"
    if any(key in value for key in ("支撑", "改善", "利多", "偏多")):
        return "SUPPORT"
    if any(key in value for key in ("缓和", "中性", "回落")):
        return "NEUTRAL_OR_EASING"
    if any(key in value for key in ("混合", "分歧")):
        return "MIXED"
    return "UNDETERMINED"


def _effect_target_from_domain(domain):
    domain = str(domain or "").upper()
    if domain in {"TMV", "TMVF", "DECISION"}:
        return "DIRECTIONAL_SKELETON"
    if domain == "MACRO":
        return "GATE_OR_BLOCKING"
    if domain in {"GAMMA", "GEX", "GAMMA_GEX"}:
        return "VOLATILITY_SPACE"
    if domain == "FUNDING":
        return "CROWDING_OR_LEVERAGE"
    if domain in {"P_C_RATIO", "P/C", "SKEW"}:
        return "OPTION_DEMAND"
    if domain == "CONFLICT":
        return "SIGNAL_COHERENCE"
    if domain == "QUALITY":
        return "DATA_RELIABILITY"
    return "UNKNOWN"


def _normalize_evidence_refs(items, packet=None, limit=8):
    valid = []
    invalid = []
    for item in list(items or [])[:limit]:
        ref = str(item or "").strip()
        if not ref:
            continue
        if packet is None or _transition_evidence_ref_exists(packet, ref):
            valid.append(ref[:220])
        else:
            invalid.append(ref[:220])
    return {"valid": valid, "invalid": invalid}


def _transition_evidence_ref_exists(packet, ref):
    if _transition_catalog_item(packet, ref):
        return True
    return _is_transition_evidence_pointer(ref) and _json_pointer_exists(packet, ref)


def _is_transition_evidence_pointer(ref):
    if not isinstance(ref, str) or not ref.startswith("/"):
        return False
    root = ref.strip("/").split("/", 1)[0].replace("~1", "/").replace("~0", "~")
    return root in {
        "comparison",
        "core_skeleton",
        "core_transition_display",
        "domain_change_summaries",
        "top_material_changes",
        "recent_5_trajectory",
        "baseline_24h",
        "episode_anchor",
        "trajectory",
        "domain_states",
        "field_glossary",
    }


def _transition_ref_is_substantive_evidence(packet, ref):
    item = _transition_catalog_item(packet, ref)
    if item:
        return str(item.get("kind") or "").lower() not in {"field_glossary"}
    if not _is_transition_evidence_pointer(ref):
        return False
    root = ref.strip("/").split("/", 1)[0].replace("~1", "/").replace("~0", "~")
    return root not in {"field_glossary"}


def _is_transition_system_assertion_pointer(ref):
    if not isinstance(ref, str) or not ref.startswith("/"):
        return False
    root = ref.strip("/").split("/", 1)[0].replace("~1", "/").replace("~0", "~")
    return root in {
        "SYSTEM_ASSERTIONS",
        "identity",
        "decision_transition",
        "cross_domain_flags",
        "materiality_score",
        "guardrails",
        "schema",
    }


def _transition_catalog_item(packet, ref):
    ref = str(ref or "").strip()
    if not ref:
        return None
    normalized = _transition_evidence_id(ref)
    for item in list(_as_dict(packet).get("evidence_catalog") or []):
        item = _as_dict(item)
        if item.get("id") in {ref, normalized}:
            return item
    return None


def _json_pointer_exists(value, pointer):
    return _json_pointer_value(value, pointer) is not None


def _json_pointer_value(value, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return None
    current = value
    for raw_part in pointer.strip("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                index = int(part)
            except ValueError:
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _transition_policy_validation(review, packet):
    text_items = list(_transition_human_text_fields(review))
    joined = "\n".join(text_items)
    raw_enum_terms = [
        term for term in (
            "NEUTRAL",
            "MACRO_BLOCKING",
            "MACRO_SHOCK_BLOCKING",
            "WAIT_CONFIRMATION",
            "Mild Headwind",
            "Strong Headwind",
            "Headwind",
            "BULLISH",
            "BEARISH",
        )
        if term in joined
    ]
    trading_terms = [
        term for term in (
            "买入",
            "卖出",
            "做多",
            "做空",
            "开仓",
            "平仓",
            "加仓",
            "减仓",
            "止损",
            "止盈",
            "下单",
            "仓位",
            "入场",
            "出场",
        )
        if term in joined
    ]
    for label, pattern in (
            ("对冲执行建议", r"(建议|进行|建立|执行|采取)[^。；\n]{0,12}对冲|对冲[^。；\n]{0,12}(仓位|交易|操作|执行)"),
            ("杠杆执行建议", r"(加|使用|提高|降低|调整)[^。；\n]{0,8}杠杆|杠杆[^。；\n]{0,8}(开仓|交易|下单|仓位|操作|执行)"),
    ):
        if re.search(pattern, joined):
            trading_terms.append(label)
    materiality_terms = []
    for label, pattern in (
            ("评估为关键变化", r"评估为[^。；\n]{0,12}(关键变化|材料性|高材料性)"),
            ("材料性套话", r"(材料性变化|高材料性|关键变化)"),
    ):
        if re.search(pattern, joined):
            materiality_terms.append(label)
    unit_terms = []
    for label, pattern in (
            ("评分_bps混写", r"评分[^。；\n]{0,20}bps"),
            ("归一化_USD混写", r"归一化[^。；\n]{0,30}(USD|美元|名义额)"),
            ("P/C符号翻转", r"P/C[^。；\n]{0,30}符号翻转|正负符号翻转"),
            ("Gamma零百万", r"(^|[^0-9A-Za-z])-?0M([^0-9A-Za-z]|$)")):
        if re.search(pattern, joined, flags=re.IGNORECASE):
            unit_terms.append(label)
    normalization_issues = _collect_transition_normalization_issues(review)
    unit_terms.extend(normalization_issues)
    causal_overclaim_terms = []
    for label, pattern in (
            ("确定性因果", r"(导致|触发|引发|造成|证明)[^。；\n]{0,40}(价格|市场|下跌|上涨|趋势|风险资产|阻断|门阻断|冲击|约束|压制|风险偏好)"),
    ):
        if re.search(pattern, joined):
            causal_overclaim_terms.append(label)
    external_data_terms = []
    for label, pattern in (
            ("外部宏观事件", r"(外部宏观|宏观事件|宏观流动性|流动性收紧|金融条件|风险偏好|短期资金流向|资金流向|宏观经济数据|经济数据|货币政策|政策预期|地缘政治|新闻|盘中事件|避险资金|央行|美联储|CPI|非农)"),
    ):
        if re.search(pattern, joined, flags=re.IGNORECASE):
            external_data_terms.append(label)
    raw_field_path_terms = _transition_raw_field_leak_terms(joined)
    invalid_refs = _collect_invalid_transition_refs(review)
    system_assertion_refs = sorted(
        ref for ref in invalid_refs if _is_transition_system_assertion_pointer(ref))
    missing_evidence_refs = _collect_missing_transition_evidence_refs(review, packet)
    direction_conflicts = _collect_transition_direction_conflicts(review, packet)
    missing_core_domains = _transition_missing_core_domain_coverage(review, packet)
    missing_observed_changes = not list(review.get("observed_changes") or [])
    issue_codes = []
    if missing_observed_changes:
        issue_codes.append("missing_observed_changes")
    if raw_enum_terms:
        issue_codes.append("raw_enum_leak")
    if trading_terms:
        issue_codes.append("trading_instruction")
    if materiality_terms:
        issue_codes.append("materiality_boilerplate")
    if unit_terms:
        issue_codes.append("unit_semantic_mislabel")
    if invalid_refs:
        issue_codes.append("invalid_evidence_ref")
    if system_assertion_refs:
        issue_codes.append("system_assertion_evidence_ref")
    if missing_evidence_refs:
        issue_codes.append("missing_evidence_ref")
    if causal_overclaim_terms:
        issue_codes.append("causal_overclaim")
    if external_data_terms:
        issue_codes.append("external_data_claim")
    if raw_field_path_terms:
        issue_codes.append("raw_field_path_leak")
    if missing_core_domains:
        issue_codes.append("missing_core_domain_coverage")
    if direction_conflicts:
        issue_codes.append("fact_impact_direction_conflict")
    issue_codes.extend(normalization_issues)
    issue_codes.extend(_transition_state_matrix_issues(review))
    issue_codes = sorted(set(issue_codes))
    guard = _as_dict(review.get("language_guard"))
    observed_complete = all(
        str(item.get("fact_cn") or "").strip()
        and str(item.get("impact_cn") or "").strip()
        and str(item.get("tendency_cn") or "").strip()
        for item in list(review.get("observed_changes") or [])
    )
    result = {
        "passed": False,
        "no_external_data": guard.get("no_external_data") is True and not external_data_terms,
        "no_trading_instruction": (
            guard.get("no_trading_instruction") is True and not trading_terms),
        "distinguishes_observation_from_causality": (
            guard.get("distinguishes_observation_from_causality") is True),
        "not_trading_advice": bool(review.get("not_trading_advice")) and not trading_terms,
        "observed_changes_have_fact_impact_tendency": observed_complete,
        "no_materiality_boilerplate": not materiality_terms,
        "missing_observed_changes": missing_observed_changes,
        "raw_enum_leaks": raw_enum_terms,
        "trading_instruction_terms": trading_terms,
        "unit_mislabel_terms": unit_terms,
        "normalization_issue_terms": normalization_issues,
        "materiality_boilerplate_terms": materiality_terms,
        "invalid_evidence_refs": invalid_refs,
        "system_assertion_evidence_refs": system_assertion_refs,
        "missing_evidence_refs": missing_evidence_refs,
        "fact_impact_direction_conflicts": direction_conflicts,
        "causal_overclaim_terms": causal_overclaim_terms,
        "external_data_terms": external_data_terms,
        "raw_field_path_terms": raw_field_path_terms,
        "missing_core_domain_coverage": missing_core_domains,
        "issue_codes": issue_codes,
    }
    blocking_unit_terms = [
        term for term in unit_terms if term not in normalization_issues
    ]
    # Content-expression issues (trading language, causal/external attribution, raw
    # enum or field-path leakage, unit mislabel, materiality boilerplate, fact/impact
    # direction tension) are recorded as advisory metadata only and no longer gate
    # display: the transition LLM review is an audit-bypass reference and does not
    # change confidence/factors/release. Only structural/format problems that would
    # break the audit page or its evidence traceability still degrade the render so
    # the frontend keeps showing correctly.
    structural_block = bool(
        missing_observed_changes
        or not result["observed_changes_have_fact_impact_tendency"]
        or invalid_refs
        or missing_evidence_refs
        or missing_core_domains
        or "incompatible_epistemic_state" in issue_codes
        or "partial_evidence_changes_judgment" in issue_codes
        or "sufficient_evidence_understated" in issue_codes
        or "system_assertion_observed_change" in issue_codes
        or "invalid_effect_target_for_domain" in issue_codes
    )
    if structural_block:
        severity = "ERROR"
        render_state = "DEGRADED_LLM_TEXT"
    else:
        severity = "OK"
        render_state = "DISPLAY_LLM_TEXT"
    result["severity"] = severity
    result["render_state"] = render_state
    result["passed"] = not structural_block
    _strip_transition_private_validation_fields(review)
    return result


def _transition_state_matrix_issues(review):
    issues = []
    for item in list(_as_dict(review).get("observed_changes") or []):
        item = _as_dict(item)
        status = str(item.get("evidence_status") or "").upper()
        magnitude = str(item.get("magnitude_verdict") or "").lower()
        directional = str(item.get("directional_role") or "").upper()
        epistemic = str(item.get("epistemic_status") or "").upper()
        if status in {"NOT_COMPARABLE", "MISSING"}:
            if (magnitude == "changes_judgment"
                    or directional not in {"UNDETERMINED", "MIXED"}
                    or epistemic != "NOT_ASSESSABLE"):
                issues.append("incompatible_epistemic_state")
        if status == "PARTIAL" and magnitude == "changes_judgment":
            issues.append("partial_evidence_changes_judgment")
        if status == "SUFFICIENT" and (
                magnitude == "indeterminate"
                or directional == "UNDETERMINED"
                or str(item.get("audit_attention_effect") or "").upper() == "UNDETERMINED"):
            issues.append("sufficient_evidence_understated")
        issues.extend(_transition_domain_target_issues(item))
    return issues


def _collect_transition_normalization_issues(value):
    issues = []
    if isinstance(value, dict):
        issues.extend(value.get("_normalization_issues") or [])
        for item in value.values():
            issues.extend(_collect_transition_normalization_issues(item))
    elif isinstance(value, list):
        for item in value:
            issues.extend(_collect_transition_normalization_issues(item))
    return sorted(set(str(item)[:120] for item in issues if item))


def _transition_domain_target_issues(item):
    domain = str(_as_dict(item).get("domain") or "").upper()
    target = str(_as_dict(item).get("effect_target") or "").upper()
    if domain == "DECISION":
        return ["system_assertion_observed_change"]
    allowed = {
        "MACRO": {"RISK_ASSET_ENVIRONMENT", "GATE_OR_BLOCKING", "CROSS_FACTOR_STATE", "DATA_RELIABILITY"},
        "TMV": {"DIRECTIONAL_SKELETON", "SIGNAL_COHERENCE", "CROSS_FACTOR_STATE", "DATA_RELIABILITY"},
        "TMVF": {"DIRECTIONAL_SKELETON", "SIGNAL_COHERENCE", "CROSS_FACTOR_STATE", "DATA_RELIABILITY"},
        "FUNDING": {"CROWDING_OR_LEVERAGE", "DIRECTIONAL_SKELETON", "CROSS_FACTOR_STATE", "DATA_RELIABILITY"},
        "GAMMA": {"VOLATILITY_SPACE", "DATA_RELIABILITY", "CROSS_FACTOR_STATE"},
        "GEX": {"VOLATILITY_SPACE", "DATA_RELIABILITY", "CROSS_FACTOR_STATE"},
        "GAMMA_GEX": {"VOLATILITY_SPACE", "DATA_RELIABILITY", "CROSS_FACTOR_STATE"},
        "P_C_RATIO": {"OPTION_DEMAND", "RISK_ASSET_ENVIRONMENT", "CROSS_FACTOR_STATE", "DATA_RELIABILITY"},
        "P/C": {"OPTION_DEMAND", "RISK_ASSET_ENVIRONMENT", "CROSS_FACTOR_STATE", "DATA_RELIABILITY"},
        "SKEW": {"OPTION_DEMAND", "RISK_ASSET_ENVIRONMENT", "SIGNAL_COHERENCE", "CROSS_FACTOR_STATE", "DATA_RELIABILITY"},
        "CONFLICT": {"SIGNAL_COHERENCE", "CROSS_FACTOR_STATE", "DATA_RELIABILITY"},
        "QUALITY": {"DATA_RELIABILITY"},
    }
    if domain in allowed and target not in allowed[domain]:
        return ["invalid_effect_target_for_domain"]
    return []


def _transition_missing_core_domain_coverage(review, packet):
    required = _transition_required_core_domains(packet)
    if not required:
        return []
    covered = set()
    review = _as_dict(review)
    for item in list(review.get("observed_changes") or []):
        item = _as_dict(item)
        covered.add(_transition_core_domain_alias(item.get("domain")))
    for item in list(review.get("cross_factor_assessments") or []):
        item = _as_dict(item)
        for domain in list(item.get("domains") or []):
            covered.add(_transition_core_domain_alias(domain))
    covered.update(_transition_domains_from_summary(review.get("transition_summary_cn")))
    covered.discard("")
    return sorted(required - covered)


def _transition_required_core_domains(packet):
    wanted = {"TMV", "MACRO", "FUNDING", "SKEW", "GAMMA", "P_C_RATIO"}
    domains = set()
    packet = _as_dict(packet)
    for item in list(_as_dict(packet.get("core_skeleton")).get("domains") or []):
        domain = _transition_core_domain_alias(_as_dict(item).get("domain"))
        if domain in wanted:
            domains.add(domain)
    for item in list(packet.get("core_transition_display") or []):
        domain = _transition_core_domain_alias(_as_dict(item).get("domain"))
        if domain in wanted:
            domains.add(domain)
    return domains


def _transition_domains_from_ref(packet, ref):
    domains = set()
    catalog_item = _transition_catalog_item(packet, ref)
    if catalog_item:
        domains.add(_transition_core_domain_alias(catalog_item.get("domain")))
        pointer = catalog_item.get("pointer")
    else:
        pointer = ref
    value = _json_pointer_value(packet, pointer)
    if isinstance(value, dict):
        domains.add(_transition_core_domain_alias(value.get("domain")))
    domains.discard("")
    return domains


def _transition_domains_from_summary(summary):
    text = str(summary or "")
    if not text:
        return set()
    patterns = {
        "TMV": r"\bTMV\b|量价|动能",
        "MACRO": r"宏观|美债|美元|利率",
        "FUNDING": r"Funding|资金费率|杠杆|拥挤",
        "SKEW": r"Skew|偏斜|期权偏斜",
        "GAMMA": r"Gamma|GEX|净\s*Gamma",
        "P_C_RATIO": r"P/C|P_C_RATIO|Put/Call|看跌看涨|期权保护|保护需求",
    }
    return {
        domain
        for domain, pattern in patterns.items()
        if re.search(pattern, text, flags=re.IGNORECASE)
    }


def _transition_core_domain_alias(domain):
    value = re.sub(r"[^A-Z0-9]+", "_", str(domain or "").upper()).strip("_")
    aliases = {
        "TMVF": "TMV",
        "P_C": "P_C_RATIO",
        "PC_RATIO": "P_C_RATIO",
        "PCR": "P_C_RATIO",
        "PUT_CALL": "P_C_RATIO",
        "PUT_CALL_RATIO": "P_C_RATIO",
        "GEX": "GAMMA",
        "GAMMA_GEX": "GAMMA",
        "GGR": "GAMMA",
        "GGR_SPATIAL": "GAMMA",
    }
    return aliases.get(value, value)


def _collect_missing_transition_evidence_refs(review, packet):
    missing = []
    for index, item in enumerate(list(_as_dict(review).get("observed_changes") or [])):
        item = _as_dict(item)
        refs = [ref for ref in list(item.get("evidence_refs") or []) if ref]
        substantive_refs = [
            ref for ref in refs
            if _transition_ref_is_substantive_evidence(packet, ref)
        ]
        if not substantive_refs:
            domain = str(item.get("domain") or f"observed_changes[{index}]")
            missing.append(domain[:120])
    return sorted(set(missing))


def _collect_transition_direction_conflicts(review, packet):
    conflicts = []
    for index, item in enumerate(list(_as_dict(review).get("observed_changes") or [])):
        item = _as_dict(item)
        domain = str(item.get("domain") or "").upper()
        text = " ".join(str(item.get(key) or "") for key in (
            "impact_cn", "tendency_cn"))
        directional = str(item.get("directional_role") or "").upper()
        for ref in list(item.get("evidence_refs") or [])[:8]:
            delta = _transition_ref_numeric_delta(packet, ref)
            if not delta:
                continue
            ref_domain, sign = delta
            effective_domain = domain or ref_domain
            if _transition_direction_conflicts(effective_domain, sign, text, directional):
                conflicts.append(f"{effective_domain}:{ref}")
                break
    return sorted(set(str(value)[:220] for value in conflicts))


def _transition_ref_numeric_delta(packet, ref):
    packet = _as_dict(packet)
    catalog_item = _transition_catalog_item(packet, ref)
    pointer = catalog_item.get("pointer") if catalog_item else ref
    value = _json_pointer_value(packet, pointer)
    if not isinstance(value, dict):
        return None
    domain = str(value.get("domain") or _as_dict(catalog_item).get("domain") or "").upper()
    delta = _transition_numeric_delta(value.get("previous"), value.get("current"))
    if delta is None:
        delta = _transition_numeric_delta(
            value.get("previous_display"), value.get("current_display"))
    if delta is None or abs(delta) < 1e-12:
        return None
    return domain, 1 if delta > 0 else -1


def _transition_numeric_delta(previous, current):
    if isinstance(previous, dict) and isinstance(current, dict):
        deltas = []
        for key in sorted(set(previous) & set(current)):
            prev = _transition_number(previous.get(key))
            curr = _transition_number(current.get(key))
            if prev is not None and curr is not None:
                deltas.append(curr - prev)
        if deltas:
            return sum(deltas) / len(deltas)
        return None
    prev = _transition_number(previous)
    curr = _transition_number(current)
    if prev is None or curr is None:
        return None
    return curr - prev


def _transition_number(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _transition_direction_conflicts(domain, sign, text, directional):
    domain = str(domain or "").upper()
    text = str(text or "")
    directional = str(directional or "").upper()
    easing_words = r"(回落|缓和|减弱|降低|消退|改善|形成支撑|提供支撑|支撑增强)"
    pressure_words = r"(上升|升温|加深|恶化|扩大|增强|压力|约束|压制)"
    if domain == "MACRO":
        if sign > 0:
            return (directional in {"SUPPORT", "NEUTRAL_OR_EASING"}
                    or re.search(easing_words, text) is not None)
        return (directional == "RISK_CONSTRAINT"
                or re.search(pressure_words, text) is not None)
    if domain in {"TMV", "TMVF"}:
        if sign > 0:
            return (directional == "RISK_CONSTRAINT"
                    or re.search(r"(转弱|走弱|下降|支撑失效|压制)", text) is not None)
        return (directional in {"SUPPORT", "NEUTRAL_OR_EASING"}
                or re.search(r"(改善|支撑增强|回升|偏多)", text) is not None)
    if domain == "FUNDING":
        if sign > 0:
            return re.search(r"(回落|转负|消失|缓和|减弱)", text) is not None
        return re.search(r"(上升|升温|增强|拥挤.*(升温|增强|扩大)|付费压力.*扩大)", text) is not None
    if domain in {"P_C_RATIO", "P/C", "SKEW"}:
        if sign > 0:
            return re.search(r"(回落|缓和|降低)", text) is not None
        return re.search(r"(升温|增强|上升)", text) is not None
    if domain == "CONFLICT":
        if sign > 0:
            return re.search(r"(分歧.*缓和|一致性.*增强|收敛)", text) is not None
        return re.search(r"(分歧.*升高|冲突.*扩大)", text) is not None
    return False


def _transition_human_text_fields(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).startswith("_"):
                continue
            if key in {
                    "input_packet_hash",
                    "blind_packet_hash",
                    "blind_result_hash",
                    "record_hash",
                    "api_key_route",
                    "llm_call_routes",
                    "model",
                    "provider",
                    "schema_name",
                    "schema_version",
                    "prompt_version",
                    "status",
                    "trajectory_state",
                    "signal_continuity",
                    "blind_review_mode",
                    "blind_consistency",
                    "language_guard",
                    "policy_validation",
                    "evidence_refs",
                    "supporting_evidence_refs",
                    "supporting_fact_ids",
                    "domains",
                    "domain",
                    "relation",
                    "effect_target",
                    "evidence_status",
                    "directional_role",
                    "magnitude_verdict",
                    "audit_attention_effect",
                    "epistemic_status",
                    "causal_status",
                    "materiality",
            }:
                continue
            yield from _transition_human_text_fields(item)
    elif isinstance(value, list):
        for item in value:
            yield from _transition_human_text_fields(item)
    elif isinstance(value, str):
        yield value


def _collect_invalid_transition_refs(value):
    invalid = []
    if isinstance(value, dict):
        invalid.extend(value.get("_invalid_evidence_refs") or [])
        for item in value.values():
            invalid.extend(_collect_invalid_transition_refs(item))
    elif isinstance(value, list):
        for item in value:
            invalid.extend(_collect_invalid_transition_refs(item))
    return sorted(set(str(item)[:220] for item in invalid if item))


def _strip_transition_private_validation_fields(value):
    if isinstance(value, dict):
        value.pop("_invalid_evidence_refs", None)
        value.pop("_normalization_issues", None)
        for item in value.values():
            _strip_transition_private_validation_fields(item)
    elif isinstance(value, list):
        for item in value:
            _strip_transition_private_validation_fields(item)


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


def _validate_blind_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("blind model output must be object")
    if "theoretical_active_view" not in payload:
        raise ValueError("blind model output missing theoretical_active_view")
    if "gamma_regime_lens" not in payload:
        raise ValueError("blind model output missing gamma_regime_lens")
    _validate_theoretical_active_view(payload.get("theoretical_active_view"))
    _validate_gamma_regime_lens(payload.get("gamma_regime_lens"))
    return {
        "theoretical_active_view": payload["theoretical_active_view"],
        "gamma_regime_lens": payload["gamma_regime_lens"],
    }


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
    if str(conflict.get("level") or "").upper() in {"MATERIAL", "HIGH"}:
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
    redacted = re.sub(r"GEMINI_CHANNEL[12]_API_KEY", "[REDACTED_ENV]", redacted, flags=re.IGNORECASE)
    redacted = re.sub(r"GEMINI_(PAID|FALLBACK)_API_KEY", "[REDACTED_ENV]", redacted, flags=re.IGNORECASE)
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


def _read_transition_review_ids(path):
    done = set()
    for value in _read_jsonl(path):
        transition_id = value.get("transition_id")
        review = _as_dict(value.get("transition_llm_review"))
        if transition_id and review.get("status") == "OK":
            done.add(transition_id)
    return done


def _dedupe_cards(cards):
    by_id = {}
    for card in cards:
        card_id = _card_id(card)
        if card_id:
            by_id[card_id] = card
    return list(by_id.values())


def _card_id(card):
    card_id = _as_dict(card.get("identity")).get("card_id") or card.get("card_id")
    return str(card_id) if card_id is not None else None


def _timestamp_sort_value(value):
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if abs(number) > 100000000000 else number
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        number = float(text)
        return number / 1000.0 if abs(number) > 100000000000 else number
    except ValueError:
        pass
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _card_sort_key(card):
    identity = _as_dict(card.get("identity"))
    return (
        _timestamp_sort_value(
            identity.get("confirmed_time_ms")
            or card.get("confirmed_time_ms")
            or identity.get("confirmed_at")
            or card.get("created_at")),
        _card_id(card) or "",
    )


def _transition_sort_key(transition):
    transition_id = transition.get("transition_id")
    return (
        _timestamp_sort_value(
            transition.get("current_ts_ms")
            or transition.get("current_time_ms")
            or transition.get("current_confirmed_at")),
        str(transition_id) if transition_id is not None else "",
    )


def _is_synthetic(card):
    return bool(_as_dict(card.get("identity")).get("is_synthetic") or card.get("is_synthetic"))


def _api_key_route(response):
    route = _as_dict(response).get("_api_key_route")
    return route if route in {"channel1", "channel2"} else "unknown"


def _exception_call_routes(exc):
    routes = getattr(exc, "api_key_routes", [])
    return [route for route in list(routes or []) if route in {"channel1", "channel2"}]


def _summarize_call_routes(routes):
    clean = [route for route in list(routes or []) if route in {"channel1", "channel2"}]
    if not clean:
        return "unknown"
    if all(route == "channel1" for route in clean):
        return "channel1"
    if all(route == "channel2" for route in clean):
        return "channel2"
    return "mixed"


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


def _env_first(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate Gemini LLM audit reviews for signal_review.jsonl.")
    parser.add_argument("--mode", choices=("card", "transition", "both"),
                        default="card",
                        help="Review card sidecar, transition sidecar, or both.")
    parser.add_argument("--source", default="",
                        help="Path to signal_review.jsonl.")
    parser.add_argument("--reviews-output", default=DEFAULT_REVIEWS,
                        help="Sidecar JSONL path for LLM reviews.")
    parser.add_argument("--transition-ledger", default="",
                        help="Path to materialized signal_transition_ledger.jsonl.")
    parser.add_argument("--transition-reviews-output",
                        default="signal_transition_llm_reviews.jsonl",
                        help="Sidecar JSONL path for transition LLM reviews.")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="Gemini model name.")
    parser.add_argument("--channel1-api-key",
                        default=_env_first("GEMINI_CHANNEL1_API_KEY"),
                        help="Channel 1 Gemini key. Prefer low-cost/free tier.")
    parser.add_argument("--channel2-api-key",
                        default=_env_first("GEMINI_CHANNEL2_API_KEY"),
                        help="Channel 2 Gemini key. Prefer paid fallback tier.")
    parser.add_argument("--limit", type=int, default=5,
                        help="Maximum new cards to review in this run.")
    parser.add_argument("--transition-limit", type=int, default=5,
                        help="Maximum new transition records to review in this run.")
    parser.add_argument("--transition-blind-mode",
                        choices=sorted(TRANSITION_BLIND_MODES),
                        default="single_call_evidence_first",
                        help="Transition review mode. Default keeps one-call evidence-first control; two_call_strict is experimental.")
    parser.add_argument("--timeout", type=int, default=60,
                        help="HTTP timeout seconds.")
    parser.add_argument("--include-synthetic", action="store_true",
                        help="Allow synthetic/local fixture cards for preview testing.")
    args = parser.parse_args(argv)
    result = {"mode": args.mode}
    exit_code = 0
    if args.mode in {"card", "both"}:
        if not args.source:
            parser.error("--source is required for card or both mode")
        card_result = generate_reviews(
            args.source,
            args.reviews_output,
            api_key=args.channel1_api_key,
            fallback_api_key=args.channel2_api_key,
            model=args.model,
            limit=args.limit,
            include_synthetic=args.include_synthetic,
            timeout=args.timeout,
        )
        result["card"] = card_result
        if card_result["errors"] and not card_result["written_reviews"]:
            exit_code = 1
    if args.mode in {"transition", "both"}:
        if not args.transition_ledger:
            parser.error("--transition-ledger is required for transition or both mode")
        transition_result = generate_transition_reviews(
            args.transition_ledger,
            args.transition_reviews_output,
            api_key=args.channel1_api_key,
            fallback_api_key=args.channel2_api_key,
            model=args.model,
            limit=args.transition_limit,
            timeout=args.timeout,
            transition_blind_mode=args.transition_blind_mode,
        )
        result["transition"] = transition_result
        if (transition_result["errors"]
                and not transition_result["written_reviews"]
                and transition_result["attempted_transitions"]):
            exit_code = 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
