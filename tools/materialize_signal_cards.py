#!/usr/bin/env python3
"""Materialize signal_review.jsonl into the finalized static frontend layout.

Output layout:
  <output>/signal_cards/index.json
  <output>/signal_cards/<card_id>.json
  <output>/signal_cards/fallback.js
"""

import argparse
from collections import deque
import datetime as _dt
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
from signal_fact_semantics import (  # noqa: E402
    build_funding_semantics,
    ensure_card_fact_semantics,
    validate_funding_semantics,
)


DEFAULT_FMZ_JSONL = "/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl"
MANIFEST_SCHEMA = {
    "name": "signal_cards_manifest",
    "version": "1.0.0",
    "card_schema": "signal_review_card@1.0.0",
}
TRANSITION_SCHEMA_VERSION = "signal_transition_record@1.0.0"
TRANSITION_COMPUTATION_VERSION = "signal_transition_materializer@1.0.0"
TRANSITION_FIELD_REGISTRY_VERSION = "TRANSITION_FIELD_REGISTRY@1.0.0"
TRANSITION_REVIEW_SCHEMA_VERSION = "signal_transition_llm_review@1.2.4"
MATERIALITY_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
SIGNAL_EVIDENCE_REVIEW_SCHEMA_VERSION = "signal_llm_review@2.0.0"
SIGNAL_RATING_SCHEMA_VERSION = "signal_rating@1.0.0"
SIGNAL_RATING_SCOPE = "side_environment_v1"
SIGNAL_RATING_CLAIM_STATUSES = frozenset({
    "SUPPORTED",
    "CONFLICTED",
    "OPPOSED",
    "INSUFFICIENT",
})
SIGNAL_COMFORT_SCHEMA_VERSION = "signal_comfort_ratings@1.0.0"
SIGNAL_COMFORT_SCOPE = "signal_side_admission"
SIGNAL_COMFORT_GRADES = ("D", "C", "B", "A", "S")
SIGNAL_COMFORT_GRADE_RANK = {grade: index for index, grade in enumerate(SIGNAL_COMFORT_GRADES)}
SIGNAL_COMFORT_ADMISSION_GRADES = frozenset({"A", "S"})
SIGNAL_COMFORT_FOCUS_SIDES = frozenset({"put_credit", "call_credit", "tie", "none"})
_SIGNAL_COMFORT_CORE = None


class SourceTailValidationError(RuntimeError):
    pass


TRANSITION_DOMAIN_ORDER = (
    "TMV", "MACRO", "FUNDING", "SKEW", "GAMMA", "P_C_RATIO",
    "CONFLICT", "DECISION", "QUALITY",
)
TRANSITION_SKELETON_SPECS = (
    {
        "domain": "TMV",
        "source_ref": "factor_cross_section.tmvf",
        "fields": (
            ("direction", ("factor_cross_section.tmvf.direction",)),
            ("tmv_blend", ("factor_cross_section.tmvf.tmv_blend",)),
            ("tmvf_24h_final", (
                "factor_cross_section.tmvf.tmvf_24h.tmv_final",
                "factor_cross_section.tmvf.tmvf_24h.final",
                "factor_cross_section.tmvf.tmvf_24h_final",
                "tmvf_24h_final",
            )),
            ("tmvf_48h_final", (
                "factor_cross_section.tmvf.tmvf_48h.tmv_final",
                "factor_cross_section.tmvf.tmvf_48h.final",
                "factor_cross_section.tmvf.tmvf_48h_final",
                "tmvf_48h_final",
            )),
            ("window_conflict", ("factor_cross_section.tmvf.window_conflict",)),
        ),
    },
    {
        "domain": "MACRO",
        "source_ref": "factor_cross_section.macro_pressure",
        "fields": (
            ("macro_score", (
                "factor_cross_section.macro_pressure.macro_score",
                "factor_cross_section.macro_pressure.score",
                "macro_score",
                "score",
            )),
            ("macro_regime", (
                "factor_cross_section.macro_pressure.macro_regime",
                "factor_cross_section.macro_pressure.regime",
                "macro_regime",
                "regime",
            )),
            ("volq_scoring_bps", (
                "factor_cross_section.macro_pressure.components.VOLQ.scoring_bps",
                "components.VOLQ.scoring_bps",
            )),
            ("dxy_scoring_bps", (
                "factor_cross_section.macro_pressure.components.DXY.scoring_bps",
                "components.DXY.scoring_bps",
            )),
            ("us10y_scoring_bps", (
                "factor_cross_section.macro_pressure.components.US10Y.scoring_bps",
                "components.US10Y.scoring_bps",
            )),
            ("macro_shock_state", (
                "factor_cross_section.macro_pressure.macro_shock.state",
                "macro_shock.state",
            )),
            ("macro_shock_block", (
                "factor_cross_section.macro_pressure.macro_shock.block",
                "macro_shock.block",
            )),
        ),
    },
    {
        "domain": "FUNDING",
        "source_ref": "factor_cross_section.funding",
        "fields": (
            ("last_rate", (
                "factor_cross_section.funding.last_rate",
                "factor_cross_section.funding.last_funding_rate",
                "last_rate",
                "last_funding_rate",
            )),
            ("funding_norm", (
                "factor_cross_section.funding.funding_norm",
                "factor_cross_section.tmvf.tmvf_48h.funding.funding_norm",
                "funding_norm",
            )),
            ("funding_state", (
                "factor_cross_section.funding.funding_state",
                "factor_cross_section.tmvf.tmvf_48h.funding_state",
                "funding_state",
            )),
            ("effect", (
                "factor_cross_section.funding.effect",
                "factor_cross_section.funding.tmvf_funding_effect",
                "effect",
                "tmvf_funding_effect",
            )),
        ),
    },
    {
        "domain": "SKEW",
        "source_ref": "factor_cross_section.skew",
        "fields": (
            ("vote", ("factor_cross_section.skew.vote", "vote")),
            ("rr_blend", ("factor_cross_section.skew.rr_blend", "rr_blend")),
            ("rr_25d", ("factor_cross_section.skew.rr_25d", "rr_25d")),
            ("skew_norm_blend", (
                "factor_cross_section.skew.skew_norm_blend",
                "skew_norm_blend",
            )),
        ),
    },
    {
        "domain": "GAMMA",
        "source_ref": "factor_cross_section.gex_info",
        "fields": (
            ("regime", (
                "factor_cross_section.gex_info.market_state",
                "factor_cross_section.gamma_regime.regime",
                "regime",
            )),
            ("net_gamma_notional_usd", (
                "factor_cross_section.gex_info.net_gamma_notional_usd",
                "factor_cross_section.gex_info.total_net_gex",
                "factor_cross_section.gex_info.net_gamma_notional",
                "factor_cross_section.gamma_regime.net_gamma_notional_usd",
                "factor_cross_section.gamma_regime.net_gamma_notional",
                "net_gamma_notional_usd",
                "net_gamma_notional",
            )),
            ("distance_to_flip_pct", (
                "factor_cross_section.gamma_regime.distance_to_flip_pct",
                "distance_to_flip_pct",
            )),
            ("distance_to_pin_pct", (
                "factor_cross_section.gamma_regime.distance_to_pin_pct",
                "factor_cross_section.gamma_regime.pin.distance_to_pin_pct",
                "distance_to_pin_pct",
            )),
        ),
    },
    {
        "domain": "P_C_RATIO",
        "source_ref": "factor_cross_section.gex_info",
        "fields": (
            ("put_call_ratio", (
                "factor_cross_section.gex_info.put_call_ratio",
                "factor_cross_section.gex_info.pc_ratio",
                "factor_cross_section.gex_info.pcr",
                "factor_cross_section.gex_info.call_put_ratio",
            )),
        ),
    },
    {
        "domain": "CONFLICT",
        "source_ref": "conflict",
        "fields": (
            ("ratio", ("conflict.ratio",)),
            ("level", ("conflict.level",)),
            ("aligned_keys", ("conflict.aligned_keys",)),
            ("dissent_keys", ("conflict.dissent_keys",)),
        ),
    },
    {
        "domain": "DECISION",
        "source_ref": "decision",
        "fields": (
            ("lean", ("decision.lean",)),
            ("support_label", ("decision.support_label",)),
            ("confidence", ("decision.confidence",)),
            ("decision_state", ("decision_matrix.decision_state",)),
        ),
    },
    {
        "domain": "QUALITY",
        "source_ref": "quality",
        "fields": (
            ("overall", ("quality.overall",)),
            ("missing_field_count", ("quality.missing_field_count",)),
        ),
    },
)
TRANSITION_FIELD_REGISTRY = (
    {
        "path": "factor_cross_section.tmvf.tmv_blend",
        "domain": "TMV",
        "type": "continuous",
        "role": "DIRECTION_OWNER",
        "absolute_floor": 0.10,
        "critical_floor": 0.30,
        "higher_meaning": "TMV_BULLISH_PRESSURE_RISE",
    },
    {
        "path": "factor_cross_section.tmvf.tmvf_24h.tmv_final",
        "domain": "TMV",
        "type": "continuous",
        "role": "DIRECTION_OWNER",
        "absolute_floor": 0.10,
        "higher_meaning": "TMV_24H_PRESSURE_RISE",
    },
    {
        "path": "factor_cross_section.tmvf.tmvf_48h.tmv_final",
        "domain": "TMV",
        "type": "continuous",
        "role": "DIRECTION_OWNER",
        "absolute_floor": 0.10,
        "higher_meaning": "TMV_48H_PRESSURE_RISE",
    },
    {
        "path": "factor_cross_section.tmvf.window_conflict",
        "domain": "TMV",
        "type": "categorical",
        "role": "DIRECTION_OWNER",
        "meaning": "TMV_WINDOW_CONFLICT_CHANGE",
    },
    {
        "path": "decision.lean",
        "domain": "DECISION",
        "type": "categorical",
        "role": "DECISION",
        "meaning": "DIRECTION_CHANGE",
    },
    {
        "path": "decision.support_label",
        "domain": "DECISION",
        "type": "categorical",
        "role": "DECISION",
        "meaning": "SUPPORT_CHANGE",
    },
    {
        "path": "decision.confidence",
        "domain": "DECISION",
        "type": "continuous",
        "role": "DECISION",
        "absolute_floor": 10.0,
        "meaning": "CONFIDENCE_CHANGE",
    },
    {
        "path": "factor_cross_section.macro_pressure.macro_score",
        "domain": "MACRO",
        "type": "continuous",
        "role": "CONTEXT",
        "absolute_floor": 0.08,
        "critical_floor": 0.30,
        "higher_meaning": "MORE_RISK_HEADWIND",
    },
    {
        "path": "factor_cross_section.macro_pressure.macro_regime",
        "domain": "MACRO",
        "type": "categorical",
        "role": "CONTEXT",
        "meaning": "MACRO_REGIME_CHANGE",
    },
    {
        "path": "factor_cross_section.macro_pressure.components.VOLQ.scoring_bps",
        "domain": "MACRO",
        "type": "continuous",
        "role": "CONTEXT",
        "absolute_floor": 100.0,
        "higher_meaning": "VOLATILITY_PRESSURE_RISE",
        "unit": "bps",
    },
    {
        "path": "factor_cross_section.macro_pressure.components.DXY.scoring_bps",
        "domain": "MACRO",
        "type": "continuous",
        "role": "CONTEXT",
        "absolute_floor": 5.0,
        "higher_meaning": "DXY_PRESSURE_RISE",
        "unit": "bps",
    },
    {
        "path": "factor_cross_section.macro_pressure.components.US10Y.scoring_bps",
        "domain": "MACRO",
        "type": "continuous",
        "role": "CONTEXT",
        "absolute_floor": 5.0,
        "higher_meaning": "RATE_PRESSURE_RISE",
        "unit": "bps",
    },
    {
        "path": "factor_cross_section.macro_pressure.macro_shock.state",
        "domain": "MACRO",
        "type": "categorical",
        "role": "GATE_ONLY",
        "meaning": "MACRO_SHOCK_GATE_STATE_CHANGE",
    },
    {
        "path": "factor_cross_section.macro_pressure.macro_shock.block",
        "domain": "MACRO",
        "type": "categorical",
        "role": "GATE_ONLY",
        "meaning": "MACRO_SHOCK_GATE_BLOCK_CHANGE",
    },
    {
        "path": "factor_cross_section.funding.last_rate",
        "domain": "FUNDING",
        "type": "continuous",
        "role": "CONTEXT",
        "absolute_floor": 0.00002,
        "higher_meaning": "FUNDING_CROWDING_UP",
    },
    {
        "path": "factor_cross_section.funding.funding_state",
        "domain": "FUNDING",
        "type": "categorical",
        "role": "CONTEXT",
        "meaning": "FUNDING_STATE_CHANGE",
    },
    {
        "path": "factor_cross_section.gamma_regime.net_gamma_notional_usd",
        "domain": "GAMMA",
        "type": "continuous",
        "role": "GATE_ONLY",
        "absolute_floor": 5000000.0,
        "higher_meaning": "NET_GAMMA_RISE",
        "unit": "usd_notional",
    },
    {
        "path": "factor_cross_section.gamma_regime.regime",
        "domain": "GAMMA",
        "type": "categorical",
        "role": "GATE_ONLY",
        "meaning": "GAMMA_REGIME_SHIFT",
    },
    {
        "path": "factor_cross_section.gamma_regime.distance_to_flip_pct",
        "domain": "GAMMA",
        "type": "continuous",
        "role": "GATE_ONLY",
        "absolute_floor": 0.25,
        "higher_meaning": "FARTHER_FROM_FLIP",
        "unit": "pct_points",
    },
    {
        "path": "factor_cross_section.gamma_regime.distance_to_pin_pct",
        "domain": "GAMMA",
        "type": "continuous",
        "role": "GATE_ONLY",
        "absolute_floor": 0.25,
        "higher_meaning": "FARTHER_FROM_PIN",
        "unit": "pct_points",
    },
    {
        "path": "factor_cross_section.gex_info.put_call_ratio",
        "domain": "P_C_RATIO",
        "type": "continuous",
        "role": "CONTEXT",
        "absolute_floor": 0.15,
        "critical_floor": 0.35,
        "higher_meaning": "PUT_CALL_RATIO_RISE",
        "sign_flip_applicable": False,
    },
    {
        "path": "factor_cross_section.skew.vote",
        "domain": "SKEW",
        "type": "categorical",
        "role": "CONTEXT",
        "meaning": "SKEW_VOTE_CHANGE",
    },
    {
        "path": "factor_cross_section.skew.rr_blend",
        "domain": "SKEW",
        "type": "continuous",
        "role": "CONTEXT",
        "absolute_floor": 0.01,
        "higher_meaning": "RR_BLEND_RISE",
    },
    {
        "path": "factor_cross_section.skew.rr_25d",
        "domain": "SKEW",
        "type": "continuous",
        "role": "CONTEXT",
        "absolute_floor": 0.01,
        "higher_meaning": "CALL_SKEW_RISE",
    },
    {
        "path": "conflict.ratio",
        "domain": "CONFLICT",
        "type": "continuous",
        "role": "QUALITY",
        "absolute_floor": 0.10,
        "critical_floor": 0.30,
        "higher_meaning": "CONFLICT_RATIO_RISE",
        "sign_flip_applicable": False,
    },
    {
        "path": "conflict.level",
        "domain": "CONFLICT",
        "type": "categorical",
        "role": "QUALITY",
        "meaning": "CONFLICT_LEVEL_CHANGE",
    },
    {
        "path": "quality.overall",
        "domain": "QUALITY",
        "type": "categorical",
        "role": "QUALITY",
        "meaning": "DATA_QUALITY_CHANGE",
    },
)
EVIDENCE_KEY_BY_DOMAIN = {
    "TMV": "TMV",
    "MACRO": "MACRO",
    "FUNDING": "FUNDING",
    "GAMMA": "GGR_SPATIAL",
    "SKEW": "SRD",
}
LEGACY_CONFIDENCE_REMINDER_RE = re.compile(r"(置信度?\s*[0-9]+)\s*未校准")
SESSION_VALIDATION_BASIS = {
    "bar_interval": "5m",
    "calibration_state": "MARKET_PRIOR_VALIDATED_NOT_SIGNAL_CALIBRATED",
    "confidence_policy": "DO_NOT_MULTIPLY_CONFIDENCE",
    "coverage_ratio": 1.0,
    "data_range": "2023-04-17 -> 2026-04-16",
    "headline_horizon_min": 60,
    "method": "KLINE_PROXY_PREMISE_REWRITE_RATE",
    "research_grade": "MARKET_PRIOR_VALIDATED",
    "sample_bars": 315363,
    "source_document": "结论档案_各时段信号耐久度_2023-2026_v1",
    "symbol": "BTC_USDT",
}
SESSION_PREMISE_CONTEXTS = {
    "POST_US_DEADZONE": {
        "clock_window": "04:00-08:00", "start_min": 240, "end_min": 480,
        "backtest_delta_pp": 0.09, "theory_zone": "LOW", "base_zone": "LOW",
        "effective_zone": "NEUTRAL_CONSERVATIVE", "display_label": "中性保守",
        "premise_durability": "NEUTRAL_CONSERVATIVE", "liquidity_depth": "THIN",
        "catalyst_exposure": "TAIL_SPIKE_RISK",
        "adjustment_direction": "NEUTRAL_CONSERVATIVE", "evidence_level": "NEUTRAL",
        "axis": "A_THIN_TAIL_RISK",
        "operator_hint_cn": "保持中性保守；等待长窗/边界覆盖复核。",
        "rationale_cn": "04:00-08:00 UTC+8 在 60m 口径下仅 +0.09pp，未显示稳定脆性；但薄盘尾部插针属于均值口径难覆盖的尾部风险，本版本保持中性保守，不据此升耐久，也不改写 confidence。",
    },
    "ASIA_MORNING": {
        "clock_window": "08:00-11:30", "start_min": 480, "end_min": 690,
        "backtest_delta_pp": 0.02, "theory_zone": "MEDIUM", "base_zone": "MEDIUM",
        "effective_zone": "NEUTRAL", "display_label": "中性",
        "premise_durability": "NEUTRAL", "liquidity_depth": "MEDIUM",
        "catalyst_exposure": "NORMAL", "adjustment_direction": "NEUTRAL",
        "evidence_level": "NEUTRAL", "axis": "REGIONAL_LIQUIDITY",
        "operator_hint_cn": "保持中性观察；不区分、不乘进 confidence。",
        "rationale_cn": "08:00-11:30 UTC+8 三年 K 线代理复合重写率仅 +0.02pp，无可落地差异；保持中性提示，不区分、不乘进 confidence。",
    },
    "ASIA_AFTERNOON_LULL": {
        "clock_window": "11:30-15:00", "start_min": 690, "end_min": 900,
        "backtest_delta_pp": -2.51, "theory_zone": "LOW", "base_zone": "LOW",
        "effective_zone": "NEUTRAL_CONSERVATIVE", "display_label": "60m耐久但暂不升档",
        "premise_durability": "NEUTRAL_CONSERVATIVE", "liquidity_depth": "THIN",
        "catalyst_exposure": "DISTANT_EU_US_COVERAGE",
        "adjustment_direction": "NEUTRAL_CONSERVATIVE",
        "evidence_level": "CONFIRMED_60M_LOCAL",
        "axis": "A_THIN_B_DISTANT_COVERAGE",
        "operator_hint_cn": "60m 局部耐久但不放松跨时段覆盖防护。",
        "rationale_cn": "11:30-15:00 UTC+8 在 60m 局部口径下更耐久（-2.51pp，92%一致），但该结论捕捉的是薄盘安静，不覆盖数小时后欧美主导流动性重写；本版本保持理论保守，等待 120/240m 长窗或边界覆盖复核后再决定，切勿据 60m 结果放松防护或改写 confidence。",
    },
    "LONDON_EARLY": {
        "clock_window": "15:00-18:00", "start_min": 900, "end_min": 1080,
        "backtest_delta_pp": -1.37, "theory_zone": "MEDIUM",
        "base_zone": "MEDIUM", "effective_zone": "NEUTRAL",
        "display_label": "中性/观察", "premise_durability": "NEUTRAL",
        "liquidity_depth": "MODERATE", "catalyst_exposure": "PRE_US_AHEAD",
        "adjustment_direction": "NEUTRAL", "evidence_level": "TENTATIVE",
        "axis": "EU_LIQUIDITY_US_AHEAD",
        "operator_hint_cn": "偏耐久但未确认，保持中性观察。",
        "rationale_cn": "15:00-18:00 UTC+8 欧洲早盘补流动性，60m 代理显示 -1.37pp 偏耐久但不稳；维持中性观察，不据此改写 confidence。",
    },
    "PRE_US_TRAPDOOR": {
        "clock_window": "18:00-21:30", "start_min": 1080, "end_min": 1290,
        "backtest_delta_pp": 5.31, "theory_zone": "LOW", "base_zone": "LOW",
        "effective_zone": "LOWER_DURABILITY_CONFIRMED",
        "display_label": "降耐久/要求确认",
        "premise_durability": "LOWER_DURABILITY_CONFIRMED",
        "liquidity_depth": "PRE_US_TRANSITION",
        "catalyst_exposure": "NEAR_US_DATA_AND_OPEN",
        "adjustment_direction": "DECREASE", "evidence_level": "CONFIRMED",
        "axis": "B_NEAR_US_DATA_AND_OPEN",
        "operator_hint_cn": "弱信号应等美盘开后再确认。",
        "rationale_cn": "18:00-21:30 UTC+8 是美盘前数据/开盘活板门；三年 BTC 5m K 线代理显示复合重写率 +5.31pp、12/12 季度一致，是唯一强确认的脆性窗口。弱信号应等美盘开后再确认；本层只降低前提耐久度提示，不改写 confidence。",
    },
    "US_OPEN_TURBULENCE": {
        "clock_window": "21:30-23:00", "start_min": 1290, "end_min": 1380,
        "backtest_delta_pp": 1.49, "theory_zone": "MEDIUM",
        "base_zone": "MEDIUM", "effective_zone": "NEUTRAL_CONSERVATIVE",
        "display_label": "开盘湍流/暂不升档",
        "premise_durability": "NEUTRAL_CONSERVATIVE",
        "liquidity_depth": "DEEP_BUT_TURBULENT",
        "catalyst_exposure": "US_OPEN_REPRICING",
        "adjustment_direction": "NEUTRAL_CONSERVATIVE",
        "evidence_level": "TENTATIVE", "axis": "B_US_OPEN_TURBULENCE",
        "operator_hint_cn": "开盘再定价阶段，避免过早升 HIGH。",
        "rationale_cn": "21:30-23:00 UTC+8 为纽约开盘湍流阶段，60m 代理显示 +1.49pp 偏脆但不稳；本版本保持中性保守，避免过早升高前提耐久度，不改写 confidence。",
    },
    "US_DEEP_POST_CATALYST": {
        "clock_window": "23:00-04:00", "start_min": 1380, "end_min": 1440,
        "backtest_delta_pp": -1.49, "theory_zone": "HIGH", "base_zone": "HIGH",
        "effective_zone": "RAISE_DURABILITY_TENTATIVE",
        "display_label": "升耐久（中等信心）",
        "premise_durability": "RAISE_DURABILITY_TENTATIVE",
        "liquidity_depth": "DEEP", "catalyst_exposure": "POST_CATALYST",
        "adjustment_direction": "INCREASE", "evidence_level": "TENTATIVE",
        "axis": "A_DEEP_LIQUIDITY_AND_POST_CATALYST",
        "operator_hint_cn": "可中等提高前提耐久度，但仍保持审计提示口径。",
        "rationale_cn": "23:00-04:00 UTC+8 属美盘深流动性/催化剂已消化窗口；三年 K 线代理显示复合重写率 -1.49pp，方向与理论一致但仍属暂定，因此只作为中等幅度提高前提耐久度的人工提示，不改写 confidence。",
    },
}


def materialize(source, output, max_cards=15, llm_reviews=None,
                include_synthetic=False, transition_ledger=None,
                transition_state=None, transition_reviews=None,
                require_valid_source_tail=False):
    source = Path(source)
    output = Path(output)
    if require_valid_source_tail:
        _validate_source_tail(source)
    cards_dir = output / "signal_cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    _chmod_public_dir(output)
    _chmod_public_dir(cards_dir)

    tail_limit = _read_tail_limit(max_cards)
    records, skipped = _read_jsonl(source, max_records=tail_limit)
    records = _dedupe_by_card_id(records)
    review_map = _read_llm_reviews(llm_reviews, max_records=tail_limit)
    transition_review_map = _read_transition_reviews(transition_reviews,
                                                     max_records=tail_limit)
    merged_review_count = 0
    if review_map:
        for record in records:
            card_id = _identity(record).get("card_id") or record.get("card_id")
            review = review_map.get(card_id)
            if review:
                existing = record.get("llm_review")
                selected = _select_llm_review(
                    existing,
                    review,
                    keep_same_protocol_ok=True,
                )
                if selected is existing:
                    continue
                record["llm_review"] = selected
                merged_review_count += 1
    synthetic_count = sum(1 for record in records if _is_synthetic(record))
    if not include_synthetic:
        records = [record for record in records if not _is_synthetic(record)]
    expected_v2_packets = {}
    prior_by_symbol = {}
    for current in sorted(records, key=_sort_key):
        symbol = str(_identity(current).get("symbol") or current.get("symbol"))
        previous = prior_by_symbol.get(symbol)
        if _uses_evidence_review_path(current):
            from signal_evidence_v2 import build_evidence_packet
            transition = _transition_record(previous, current, [previous, current], None) if previous else None
            expected_v2_packets[_identity(current).get("card_id")] = build_evidence_packet(current, previous, transition)
        prior_by_symbol[symbol] = current
    records = sorted(records, key=_sort_key, reverse=True)
    if max_cards and max_cards > 0:
        records = records[:max_cards]

    for record in records:
        if _is_unsupported_evidence_review(record):
            _unsupported_evidence_review(record)
        if _is_evidence_v2(record):
            _validate_evidence_v2(record, expected_v2_packets.get(_identity(record).get("card_id")))
        canonical = ensure_card_fact_semantics(
            record,
            compat_source="materializer:legacy_card_funding_semantics_v1",
        )
        record.clear()
        record.update(canonical)
        _backfill_session_context(record)
        _backfill_signal_durability(record)
        _enrich_auxiliary_evidence(record)
        _sanitize_legacy_display_text(record)

    transitions = _build_transition_records(records, transition_review_map)
    if transition_ledger:
        _write_jsonl(transition_ledger, transitions)
    if transition_state:
        _write_transition_state(transition_state, transitions)
    _write_trajectory_files(output, records, transitions)

    manifest_cards = []
    expected_card_files = set()
    for record in records:
        identity = _identity(record)
        card_id = identity.get("card_id") or record.get("card_id")
        filename = _filename_for_card(card_id)
        expected_card_files.add(filename)
        rel_path = "signal_cards/" + filename
        if _is_evidence_v2(record):
            from signal_review_v2 import build_summary
            record["signal_evidence_summary"] = build_summary(record["llm_review"])
            record.pop("signal_comfort_summary", None)
        else:
            record["signal_comfort_summary"] = _signal_comfort_summary(record)
        _write_json(cards_dir / filename, record)
        manifest_cards.append({
            "card_id": card_id,
            "confirmed_at": identity.get("confirmed_at") or record.get("created_at"),
            "symbol": identity.get("symbol") or record.get("symbol"),
            "quality": _quality(record),
            "path": rel_path,
            "summary": _manifest_card_summary(record),
        })

    manifest = {
        "schema": dict(MANIFEST_SCHEMA),
        "generated_at": _now_iso(),
        "cards": manifest_cards,
    }
    _prune_stale_card_json(cards_dir, expected_card_files)
    _write_json(cards_dir / "index.json", manifest)
    _write_fallback(cards_dir / "fallback.js", records)
    return {
        "source": str(source),
        "output": str(output),
        "written_cards": len(records),
        "skipped_lines": skipped,
        "manifest": str(cards_dir / "index.json"),
        "fallback": str(cards_dir / "fallback.js"),
        "llm_reviews": str(llm_reviews) if llm_reviews else "",
        "merged_review_count": merged_review_count,
        "filtered_synthetic_count": 0 if include_synthetic else synthetic_count,
        "include_synthetic": bool(include_synthetic),
        "transition_records": len(transitions),
        "transition_ledger": str(transition_ledger) if transition_ledger else "",
        "transition_reviews": str(transition_reviews) if transition_reviews else "",
    }


def _read_tail_limit(max_cards):
    if not max_cards or max_cards <= 0:
        return None
    return max(500, max_cards * 5)


def _read_jsonl(source, max_records=None, require_identity=True):
    if max_records and max_records > 0:
        records = deque(maxlen=max_records)
    else:
        records = []
    skipped = 0
    if not source.exists():
        return [], skipped
    with source.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if (isinstance(value, dict)
                    and (not require_identity
                         or _identity(value).get("card_id"))):
                records.append(value)
            else:
                skipped += 1
    return list(records), skipped


def _validate_source_tail(source):
    if not source.exists():
        raise SourceTailValidationError(
            "source JSONL does not exist: {}".format(source))
    last_text = None
    last_line_number = 0
    try:
        with source.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                text = line.strip()
                if text:
                    last_text = text
                    last_line_number = line_number
    except UnicodeDecodeError as exc:
        raise SourceTailValidationError(
            "source JSONL is not valid UTF-8: {}".format(source)) from exc
    if last_text is None:
        raise SourceTailValidationError(
            "source JSONL has no non-empty records: {}".format(source))
    try:
        value = json.loads(last_text)
    except json.JSONDecodeError as exc:
        raise SourceTailValidationError(
            "last non-empty source line {} in {} is not valid JSON: {}".format(
                last_line_number, source, exc.msg)) from exc
    if not isinstance(value, dict):
        raise SourceTailValidationError(
            "last non-empty source line {} in {} is not a JSON object".format(
                last_line_number, source))
    identity = value.get("identity")
    if not isinstance(identity, dict) or not identity.get("card_id"):
        raise SourceTailValidationError(
            "last non-empty source line {} in {} is missing identity.card_id".format(
                last_line_number, source))


def _dedupe_by_card_id(records):
    by_id = {}
    for record in records:
        by_id[_identity(record).get("card_id")] = record
    return list(by_id.values())


def _read_llm_reviews(path, max_records=None):
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        return {}
    reviews = {}
    records, _skipped = _read_jsonl(path, max_records=max_records,
                                    require_identity=False)
    for value in records:
        card_id = value.get("card_id") or _identity(value).get("card_id")
        review = value.get("llm_review")
        if card_id and isinstance(review, dict):
            reviews[card_id] = _select_llm_review(reviews.get(card_id), review)
    return reviews


def _select_llm_review(existing, candidate, *, keep_same_protocol_ok=False):
    """Prefer the current evidence protocol over legacy review rows."""

    if not isinstance(candidate, dict):
        return existing
    if not isinstance(existing, dict):
        return candidate
    existing_rank = _llm_review_protocol_rank(existing)
    candidate_rank = _llm_review_protocol_rank(candidate)
    if candidate_rank > existing_rank:
        return candidate
    if candidate_rank < existing_rank:
        return existing
    if (keep_same_protocol_ok
            and _review_status(existing) == "OK"
            and _review_status(candidate) != "OK"):
        return existing
    return candidate


def _llm_review_protocol_rank(review):
    return 2 if _is_evidence_review_protocol(review) else 1


def _is_evidence_review_protocol(review):
    review = _dict(review)
    schema = str(review.get("schema_version") or "")
    if schema == SIGNAL_EVIDENCE_REVIEW_SCHEMA_VERSION:
        return True
    if "side_evidence_ratings" in _dict(review.get("integrated_trade_advisory")):
        return True
    prefix = "signal_llm_review@"
    if not schema.startswith(prefix):
        return False
    match = re.match(r"^signal_llm_review@(\d+)(?:\.|$)", schema)
    if not match:
        return True
    return int(match.group(1)) >= 2


def _read_transition_reviews(path, max_records=None):
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        return {}
    reviews = {}
    records, _skipped = _read_jsonl(path, max_records=max_records,
                                    require_identity=False)
    for value in records:
        transition_id = value.get("transition_id")
        review = value.get("transition_llm_review")
        if transition_id and isinstance(review, dict):
            reviews[transition_id] = review
    return reviews


def _build_transition_records(records, review_map=None):
    review_map = review_map or {}
    by_symbol_previous = {}
    by_symbol_history = {}
    transitions = []
    previous_transition_hash = None
    for current in sorted(records, key=_sort_key):
        identity = _identity(current)
        symbol = str(identity.get("symbol") or current.get("symbol") or "UNKNOWN")
        history = by_symbol_history.setdefault(symbol, [])
        previous = by_symbol_previous.get(symbol)
        if previous:
            transition = _transition_record(
                previous,
                current,
                history + [current],
                previous_transition_hash,
            )
            previous_transition_hash = transition.get("record_hash")
            review = review_map.get(transition.get("transition_id"))
            current["transition_context"] = _transition_context_for_card(transition)
            if isinstance(review, dict):
                current["transition_llm_review"] = review
            transitions.append(transition)
        history.append(current)
        if len(history) > 64:
            del history[:-64]
        by_symbol_previous[symbol] = current
    return transitions


def _transition_record(previous, current, history, previous_transition_hash):
    prev_identity = _identity(previous)
    curr_identity = _identity(current)
    symbol = curr_identity.get("symbol") or current.get("symbol") or prev_identity.get("symbol")
    previous_card_id = prev_identity.get("card_id") or previous.get("card_id")
    current_card_id = curr_identity.get("card_id") or current.get("card_id")
    previous_ts_ms = _event_time_ms(previous)
    current_ts_ms = _event_time_ms(current)
    previous_event_type = prev_identity.get("event_type")
    current_event_type = curr_identity.get("event_type")
    previous_tags = list(prev_identity.get("tags") or [])
    current_tags = list(curr_identity.get("tags") or [])
    current_round = _dict(current.get("analysis_round"))
    fixed_time_snapshot = (
        current_event_type == "FIXED_ANALYSIS_ROUND"
        or "FIXED_ROUND_ANALYSIS" in current_tags
        or bool(current_round)
    )
    event_context = {
        "transition_nature": (
            "FIXED_TIME_SNAPSHOT_DIFF"
            if fixed_time_snapshot else "SIGNAL_EVENT_TRANSITION"
        ),
        "fixed_time_snapshot_diff": bool(fixed_time_snapshot),
        "previous": {
            "event_type": previous_event_type,
            "tags": previous_tags,
            "analysis_round": _dict(previous.get("analysis_round")),
        },
        "current": {
            "event_type": current_event_type,
            "tags": current_tags,
            "analysis_round": current_round,
        },
    }
    previous_anchor = _producer_anchor(previous)
    current_anchor = _producer_anchor(current)
    compat_anchor = (
        previous_anchor.get("compat_backfill_applied")
        or current_anchor.get("compat_backfill_applied")
    )
    elapsed_ms = None
    if previous_ts_ms and current_ts_ms:
        elapsed_ms = int(current_ts_ms - previous_ts_ms)
    comparison_quality = _comparison_quality(elapsed_ms)
    changes = _transition_changes(previous, current, elapsed_ms)
    top_changes = _top_material_changes(changes)
    core_skeleton = _core_skeleton(previous, current, elapsed_ms,
                                   comparison_quality)
    domain_summaries = _domain_change_summaries(changes)
    core_transition_display = _core_transition_display(
        core_skeleton, domain_summaries, top_changes)
    raw_change_groups = _raw_change_groups(changes)
    flags = _transition_flags(previous, current, top_changes)
    materiality_score = _materiality_score(top_changes, flags)
    transition_id = _transition_id(
        symbol, previous_card_id, current_card_id,
        _producer_record_hash(previous), _producer_record_hash(current))
    record = {
        "schema_name": "SignalTransitionRecord",
        "schema_version": TRANSITION_SCHEMA_VERSION,
        "computation_version": TRANSITION_COMPUTATION_VERSION,
        "field_registry_version": TRANSITION_FIELD_REGISTRY_VERSION,
        "audit_scope": "AUDIT_ONLY",
        "transition_id": transition_id,
        "symbol": symbol,
        "previous_card_id": previous_card_id,
        "current_card_id": current_card_id,
        "previous_ts_ms": previous_ts_ms,
        "current_ts_ms": current_ts_ms,
        "previous_strategy_version": prev_identity.get("strategy_version"),
        "current_strategy_version": curr_identity.get("strategy_version"),
        "previous_card_schema": _source_schema_fingerprint(previous),
        "current_card_schema": _source_schema_fingerprint(current),
        "elapsed_ms": elapsed_ms,
        "previous_event_type": previous_event_type,
        "current_event_type": current_event_type,
        "previous_tags": previous_tags,
        "current_tags": current_tags,
        "event_context": event_context,
        "comparison_quality": comparison_quality,
        "producer_anchor": {
            "previous": previous_anchor,
            "current": current_anchor,
        },
        "compat_backfill_applied": bool(compat_anchor),
        "compat_backfill_source": (
            "materializer_transition_producer_anchor_compat_v1"
            if compat_anchor else None
        ),
        "compat_source_fields": _compat_source_fields(
            previous_anchor, current_anchor),
        "producer_record_hashes": {
            "previous": _producer_record_hash(previous),
            "current": _producer_record_hash(current),
        },
        "relation": {
            "immediate_predecessor": True,
            "same_episode": _episode_id(previous) == _episode_id(current),
            "comparison_quality": comparison_quality,
            "comparison_limitations": _comparison_limitations(
                previous, current, elapsed_ms, comparison_quality),
        },
        "decision_transition": _decision_transition(previous, current),
        "core_skeleton": core_skeleton,
        "core_transition_display": core_transition_display,
        "domain_change_summaries": domain_summaries,
        "raw_change_groups": raw_change_groups,
        "top_material_changes": top_changes,
        "recent_5_trajectory": _recent_trajectory(history, limit=5),
        "baseline_24h": _baseline_24h(history, current_ts_ms),
        "episode_anchor": _episode_anchor(history, current),
        "trajectory": _trajectory_summary(history),
        "domain_states": _domain_states(top_changes),
        "cross_domain_flags": flags,
        "materiality_score": materiality_score,
        "llm_review_required": bool(flags and materiality_score >= 25.0),
        "hash_chain": {
            "algorithm": "sha256",
            "canonicalization": "json_sort_keys_compact",
            "previous_transition_hash": previous_transition_hash,
            "basis": [
                "producer_integrity.record_hash.previous",
                "producer_integrity.record_hash.current",
                "transition_canonical_json",
            ],
        },
    }
    record["record_hash"] = _transition_hash(record, previous_transition_hash)
    return record


def _transition_context_for_card(transition):
    context = dict(transition)
    return context


def _transition_id(symbol, previous_card_id, current_card_id,
                   previous_record_hash, current_record_hash):
    seed = {
        "symbol": symbol,
        "previous_card_id": previous_card_id,
        "current_card_id": current_card_id,
        "previous_record_hash": previous_record_hash,
        "current_record_hash": current_record_hash,
        "field_registry_version": TRANSITION_FIELD_REGISTRY_VERSION,
    }
    return "tr-" + _sha256_json(seed)[7:23]


def _transition_hash(record, previous_transition_hash):
    payload = dict(record)
    payload.pop("record_hash", None)
    seed = {
        "previous_transition_hash": previous_transition_hash,
        "transition": payload,
    }
    return _sha256_json(seed)


def _sha256_json(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _transition_changes(previous, current, elapsed_ms):
    changes = []
    for entry in TRANSITION_FIELD_REGISTRY:
        before = _field_snapshot(previous, entry)
        after = _field_snapshot(current, entry)
        change = _compare_transition_field(entry, before, after, elapsed_ms)
        if change:
            changes.append(change)
    return changes


def _core_skeleton(previous, current, elapsed_ms, comparison_quality):
    prev_identity = _identity(previous)
    curr_identity = _identity(current)
    return {
        "schema_version": "transition_core_skeleton@1.0.0",
        "timeline": {
            "previous_card_id": prev_identity.get("card_id") or previous.get("card_id"),
            "current_card_id": curr_identity.get("card_id") or current.get("card_id"),
            "previous_short_id": prev_identity.get("short_id"),
            "current_short_id": curr_identity.get("short_id"),
            "previous_ts_ms": _event_time_ms(previous),
            "current_ts_ms": _event_time_ms(current),
            "elapsed_ms": elapsed_ms,
            "comparison_quality": comparison_quality,
        },
        "domains": [
            _skeleton_domain(previous, current, spec)
            for spec in TRANSITION_SKELETON_SPECS
        ],
    }


def _skeleton_domain(previous, current, spec):
    domain = spec.get("domain")
    source_ref = spec.get("source_ref")
    return {
        "domain": domain,
        "previous": _skeleton_values(previous, domain, spec.get("fields") or ()),
        "current": _skeleton_values(current, domain, spec.get("fields") or ()),
        "source_refs": _unique_values([
            source_ref,
            _source_ref_for_domain(previous, domain, source_ref),
            _source_ref_for_domain(current, domain, source_ref),
        ]),
    }


def _skeleton_values(record, domain, fields):
    values = {}
    for name, paths in fields:
        values[name] = _first_transition_value(record, domain, paths)
    return values


def _core_transition_display(core_skeleton, domain_summaries, top_changes):
    skeleton_by_domain = {
        str(item.get("domain") or "").upper(): item
        for item in _list(_dict(core_skeleton).get("domains"))
        if isinstance(item, dict)
    }
    summary_by_domain = {
        str(item.get("domain") or "").upper(): item
        for item in _list(domain_summaries)
        if isinstance(item, dict)
    }
    change_by_domain = {}
    for item in _list(top_changes):
        if isinstance(item, dict):
            change_by_domain.setdefault(str(item.get("domain") or "").upper(), item)
    rows = []
    for domain in TRANSITION_DOMAIN_ORDER:
        skeleton = _dict(skeleton_by_domain.get(domain))
        if not skeleton:
            continue
        previous = _dict(skeleton.get("previous"))
        current = _dict(skeleton.get("current"))
        value_key = _display_value_key(domain, previous, current)
        prev_value = previous.get(value_key) if value_key else None
        curr_value = current.get(value_key) if value_key else None
        if value_key is None:
            value_key = "state"
        summary = _dict(summary_by_domain.get(domain))
        change = _dict(change_by_domain.get(domain))
        grade = _grade_cn(summary.get("materiality") or change.get("materiality"))
        source_note = _display_source_note(domain, value_key, prev_value, curr_value)
        rows.append({
            "domain": domain,
            "title_cn": _display_title_cn(domain),
            "value_key": _display_public_value_key(domain, value_key, prev_value, curr_value),
            "previous_display": _display_value_text(domain, value_key, prev_value),
            "current_display": _display_value_text(domain, value_key, curr_value),
            "delta_display": _display_delta_text(domain, value_key, prev_value, curr_value),
            "meaning_cn": _display_meaning_cn(domain, value_key, prev_value, curr_value),
            "grade_cn": grade,
            "source_note": source_note,
        })
    return rows


def _display_value_key(domain, previous, current):
    preferences = {
        "TMV": ("tmv_blend", "tmvf_24h_final", "tmvf_48h_final", "direction"),
        "MACRO": ("macro_score", "macro_shock_state", "macro_regime"),
        "FUNDING": ("last_rate", "last_funding_rate", "funding_state",
                    "funding_norm", "effect"),
        "SKEW": ("rr_25d", "rr_blend", "skew_norm_blend", "vote"),
        "GAMMA": ("net_gamma_notional_usd", "distance_to_flip_pct",
                  "distance_to_pin_pct", "regime"),
        "P_C_RATIO": ("put_call_ratio",),
        "CONFLICT": ("ratio", "level"),
        "DECISION": ("confidence", "lean", "support_label", "decision_state"),
        "QUALITY": ("overall", "missing_field_count"),
    }.get(domain, ())
    for key in preferences:
        if previous.get(key) not in (None, "") or current.get(key) not in (None, ""):
            return key
    keys = list(previous.keys()) + [key for key in current if key not in previous]
    for key in keys:
        if previous.get(key) not in (None, "") or current.get(key) not in (None, ""):
            return key
    return None


def _display_public_value_key(domain, key, previous, current):
    if domain == "GAMMA" and key in {"net_gamma_notional_usd", "net_gamma_notional"}:
        values = [_number(previous), _number(current)]
        numeric = [value for value in values if value is not None]
        if numeric and max(abs(value) for value in numeric) < 1000:
            return "net_gamma_metric"
    return key


def _display_title_cn(domain):
    return {
        "TMV": "TMV（量价路径）",
        "MACRO": "宏观（利率/美元/波动率）",
        "FUNDING": "Funding（期货资金费率）",
        "SKEW": "期权偏斜（Skew）",
        "GAMMA": "Gamma（净 Gamma）",
        "P_C_RATIO": "P/C（期权需求）",
        "CONFLICT": "冲突（信号分歧）",
        "DECISION": "决策（状态/置信）",
        "QUALITY": "数据质量（完整性）",
    }.get(domain, domain or "其他")


def _grade_cn(materiality):
    return {
        "CRITICAL": "关键",
        "HIGH": "高",
        "MEDIUM": "中",
        "LOW": "低",
        "VERY_LOW": "很低",
        "NONE": "无",
        "UNKNOWN": "未定",
        None: "未定",
    }.get(materiality, str(materiality))


def _display_value_text(domain, key, value):
    if value in (None, ""):
        return "缺失"
    numeric = _number(value)
    if numeric is None:
        return _enum_cn(value)
    if domain == "FUNDING" and key in {"last_rate", "last_funding_rate"}:
        return _trim_number(numeric * 100.0, 6) + "%"
    if domain == "CONFLICT" and key == "ratio":
        return _trim_number(numeric * 100.0, 1) + "%"
    if domain == "GAMMA" and key in {"net_gamma_notional_usd", "net_gamma_notional"}:
        if abs(numeric) < 1000:
            return _trim_number(numeric, 4)
        return _usd_notional_text(numeric)
    if key in {"distance_to_flip_pct", "distance_to_pin_pct"}:
        return _trim_number(numeric, 2) + "%"
    return _trim_number(numeric, 4)


def _display_delta_text(domain, key, previous, current):
    prev_num = _number(previous)
    curr_num = _number(current)
    if prev_num is None or curr_num is None:
        return "缺失"
    return _display_value_text(domain, key, curr_num - prev_num)


def _display_source_note(domain, key, previous, current):
    if domain == "FUNDING" and key in {"last_rate", "last_funding_rate"}:
        return "原始 last_rate"
    if domain == "GAMMA" and key in {"net_gamma_notional_usd", "net_gamma_notional"}:
        values = [_number(previous), _number(current)]
        numeric = [value for value in values if value is not None]
        if numeric and max(abs(value) for value in numeric) < 1000:
            return "旧卡兼容推导：按 Gamma 指标显示，不伪装为 USD 名义额"
        return "净 Gamma USD 名义额"
    if domain == "P_C_RATIO":
        return "GEX put_call_ratio"
    return "materializer display-only"


def _display_meaning_cn(domain, key, previous, current):
    prev_num = _number(previous)
    curr_num = _number(current)
    rising = prev_num is not None and curr_num is not None and curr_num > prev_num
    falling = prev_num is not None and curr_num is not None and curr_num < prev_num
    if domain == "TMV":
        if prev_num is not None and curr_num is not None and prev_num >= 0 > curr_num:
            return "量价路径由正转负，方向骨架转弱。"
        return "量价路径转弱。" if falling else "量价路径改善或维持。"
    if domain == "MACRO":
        return "宏观逆风压力上升，更多是风险背景约束。" if rising else "宏观逆风压力回落或维持。"
    if domain == "FUNDING":
        return build_funding_semantics(
            curr_num,
            source="materializer:transition_core_funding_rate",
            compat_backfill_applied=True,
        )["canonical_text_cn"]
    if domain == "SKEW":
        return "期权偏斜压力加深，保护需求或下行尾部定价更重。" if falling else "期权偏斜压力缓和。"
    if domain == "GAMMA":
        values = [value for value in (prev_num, curr_num) if value is not None]
        compat = bool(values and max(abs(value) for value in values) < 1000)
        prefix = "旧卡兼容推导的 Gamma 指标" if compat else "净 Gamma 名义敞口"
        return prefix + ("走弱，空间约束略加深。" if falling else "回升或维持，空间约束未继续加深。")
    if domain == "P_C_RATIO":
        if rising:
            return "期权保护需求上升，说明防护/看跌需求更重；这不是方向交易结论。"
        return "期权保护需求从高位略回落，但仍需结合绝对水平判断，不构成方向反转。"
    if domain == "CONFLICT":
        return "信号分歧升高，说明多维证据一致性下降。" if rising else "信号分歧缓和。"
    if domain == "DECISION":
        return "决策置信下降，说明审计证据收缩，不代表胜率变化。" if falling else "决策状态维持或改善。"
    if domain == "QUALITY":
        return "数据质量状态，用于解释审计可读性和比较可信度。"
    return "关键状态变化。"


def _trim_number(value, digits):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) < 0.5 * (10 ** -digits):
        number = 0.0
    text = ("{0:." + str(digits) + "f}").format(number)
    return text.rstrip("0").rstrip(".") or "0"


def _usd_notional_text(value):
    sign = "-" if value < 0 else ""
    amount = abs(float(value))
    if amount >= 1_000_000_000:
        return sign + "$" + _trim_number(amount / 1_000_000_000, 2) + "B"
    if amount >= 1_000_000:
        return sign + "$" + _trim_number(amount / 1_000_000, 2) + "M"
    if amount >= 1_000:
        return sign + "$" + _trim_number(amount / 1_000, 2) + "K"
    return sign + "$" + _trim_number(amount, 2)


def _enum_cn(value):
    text = str(value)
    return {
        "NEUTRAL": "中性",
        "BULLISH": "偏多",
        "BEARISH": "偏空",
        "BULLISH_STRONG": "强偏多",
        "BEARISH_STRONG": "强偏空",
        "NO_TRADE_BLOCKED": "无交易/阻断",
        "WAIT_CONFIRMATION": "等待确认",
        "TRADE_SUPPORT_STRONG": "强交易支持",
        "TRADE_SUPPORT_WEAK": "弱交易支持",
        "BLOCK": "阻断",
        "WATCH": "观察",
        "CLEAR": "清除",
        "Mild Headwind": "轻度逆风",
        "Headwind": "逆风",
        "TRANSITION": "过渡区",
        "POSITIVE_GAMMA_PINNING": "正 Gamma 钉住",
        "NEGATIVE_GAMMA": "负 Gamma",
        "OK": "正常",
        "MATERIAL": "实质分歧",
        "LOW": "低",
    }.get(text, text)


def _first_transition_value(record, domain, paths):
    for path in paths:
        value = _get_path(record, path)
        if value not in (None, ""):
            return value
    row = _evidence_row(record, domain)
    for source_name in ("raw_values", "detail"):
        source = row.get(source_name) if isinstance(row, dict) else None
        for path in paths:
            value = _raw_value_for_path(source, path)
            if value not in (None, ""):
                return value
    return None


def _source_ref_for_domain(record, domain, default_ref):
    row = _evidence_row(record, domain)
    if isinstance(row, dict) and row.get("source_ref"):
        return row.get("source_ref")
    if default_ref:
        value = _get_path(record, default_ref + ".source_ref")
        if value:
            return value
    return default_ref


def _field_snapshot(record, entry):
    value, source = _field_value(record, entry)
    row = _evidence_row(record, entry.get("domain"))
    return {
        "value": value,
        "source": source,
        "role": _evidence_role(row, entry),
        "source_ref": (
            row.get("source_ref") if isinstance(row, dict) and row.get("source_ref")
            else _source_ref_for_path(record, entry.get("path"))
        ),
    }


def _source_ref_for_path(record, dotted_path):
    parts = str(dotted_path or "").split(".")
    for length in range(len(parts) - 1, 0, -1):
        parent = ".".join(parts[:length])
        value = _get_path(record, parent + ".source_ref")
        if value:
            return value
    if parts:
        return ".".join(parts[:-1]) if len(parts) > 1 else parts[0]
    return None


def _field_value(record, entry):
    value = _get_path(record, entry.get("path"))
    if value not in (None, ""):
        return value, "canonical"
    row = _evidence_row(record, entry.get("domain"))
    if row:
        raw_values = row.get("raw_values")
        value = _raw_value_for_path(raw_values, entry.get("path"))
        if value not in (None, ""):
            return value, "reasoning.evidence.raw_values"
        detail = row.get("detail")
        value = _raw_value_for_path(detail, entry.get("path"))
        if value not in (None, ""):
            return value, "reasoning.evidence.detail"
    return None, "missing"


def _get_path(value, dotted_path):
    if not dotted_path:
        return None
    current = value
    for part in str(dotted_path).split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current.get(part)
            continue
        if isinstance(current, list):
            current = _select_keyed_item(current, part)
            if current is None:
                return None
            continue
        return None
    return current


def _select_keyed_item(items, key):
    wanted = str(key).upper()
    for item in items:
        if isinstance(item, dict) and str(item.get("key") or "").upper() == wanted:
            return item
    return None


def _raw_value_for_path(values, dotted_path):
    values = _dict(values)
    if not values:
        return None
    compact = _compact_raw_key(dotted_path)
    if compact in values:
        return values.get(compact)
    leaf = str(dotted_path or "").split(".")[-1]
    if leaf in values:
        return values.get(leaf)
    parts = str(dotted_path or "").split(".")
    if len(parts) >= 3 and parts[-2].isupper():
        component = _select_keyed_item(values.get("components") or [], parts[-2])
        if isinstance(component, dict):
            return component.get(leaf)
    if "component_scores" in values and len(parts) >= 3 and parts[-2].isupper():
        component_scores = _dict(values.get("component_scores"))
        component = _dict(component_scores.get(parts[-2]))
        if leaf in component:
            return component.get(leaf)
    return None


def _compact_raw_key(dotted_path):
    parts = str(dotted_path or "").split(".")
    if len(parts) >= 2:
        return "_".join(parts[-2:])
    return parts[0] if parts else ""


def _evidence_row(record, domain):
    key = EVIDENCE_KEY_BY_DOMAIN.get(str(domain or "").upper())
    if not key:
        return {}
    rows = _dict(record.get("reasoning")).get("evidence")
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, dict) and str(row.get("key") or "").upper() == key:
            return row
    return {}


def _evidence_role(row, entry):
    if isinstance(row, dict):
        status = str(row.get("participation_status") or "").upper()
        if status:
            return status
    return entry.get("role")


def _compare_transition_field(entry, before, after, elapsed_ms):
    previous_value = before.get("value")
    current_value = after.get("value")
    if previous_value in (None, "") and current_value in (None, ""):
        return None
    if entry.get("type") == "continuous":
        return _compare_continuous(entry, before, after, elapsed_ms)
    return _compare_categorical(entry, before, after, elapsed_ms)


def _compare_continuous(entry, before, after, elapsed_ms):
    prev_num = _number(before.get("value"))
    curr_num = _number(after.get("value"))
    changed = prev_num is not None and curr_num is not None and prev_num != curr_num
    missing_changed = (prev_num is None) != (curr_num is None)
    if not changed and not missing_changed:
        return None
    delta_abs = None
    delta_relative = None
    sign_before = _sign_label(prev_num)
    sign_after = _sign_label(curr_num)
    sign_flip = False
    if prev_num is not None and curr_num is not None:
        delta_abs = round(curr_num - prev_num, 10)
        if abs(prev_num) > 1e-12:
            delta_relative = round((curr_num - prev_num) / abs(prev_num), 10)
        sign_flip = bool(
            entry.get("sign_flip_applicable", True)
            and prev_num * curr_num < 0
        )
    materiality = _continuous_materiality(entry, prev_num, curr_num, delta_abs,
                                          sign_flip, missing_changed)
    if materiality == "NONE":
        return None
    return {
        "domain": entry.get("domain"),
        "field": entry.get("path"),
        "previous": before.get("value"),
        "current": after.get("value"),
        "delta_abs": delta_abs,
        "delta_relative": delta_relative,
        "sign_before": sign_before,
        "sign_after": sign_after,
        "sign_flip": sign_flip,
        "elapsed_ms": elapsed_ms,
        "role_before": before.get("role"),
        "role_after": after.get("role"),
        "materiality": materiality,
        "meaning": _continuous_meaning(entry, delta_abs, sign_flip),
        "source_priority": [before.get("source"), after.get("source")],
        "source_ref": after.get("source_ref") or before.get("source_ref"),
    }


def _compare_categorical(entry, before, after, elapsed_ms):
    previous_value = before.get("value")
    current_value = after.get("value")
    if str(previous_value) == str(current_value):
        return None
    materiality = "HIGH" if previous_value not in (None, "") and current_value not in (None, "") else "MEDIUM"
    return {
        "domain": entry.get("domain"),
        "field": entry.get("path"),
        "previous": previous_value,
        "current": current_value,
        "delta_abs": None,
        "delta_relative": None,
        "sign_before": None,
        "sign_after": None,
        "sign_flip": False,
        "elapsed_ms": elapsed_ms,
        "role_before": before.get("role"),
        "role_after": after.get("role"),
        "materiality": materiality,
        "meaning": entry.get("meaning") or "CATEGORY_CHANGE",
        "source_priority": [before.get("source"), after.get("source")],
        "source_ref": after.get("source_ref") or before.get("source_ref"),
    }


def _continuous_materiality(entry, prev_num, curr_num, delta_abs, sign_flip,
                            missing_changed):
    if missing_changed:
        return "MEDIUM"
    if delta_abs is None:
        return "NONE"
    abs_delta = abs(delta_abs)
    floor = float(entry.get("absolute_floor") or 0.0)
    critical = float(entry.get("critical_floor") or max(floor * 3.0, floor))
    if critical and abs_delta >= critical:
        return "CRITICAL"
    if sign_flip and floor and abs_delta >= floor:
        return "HIGH"
    if floor and abs_delta >= floor:
        return "HIGH"
    if abs_delta > 0:
        return "LOW"
    return "NONE"


def _continuous_meaning(entry, delta_abs, sign_flip):
    if sign_flip:
        return "RISK_HEADWIND_SIGN_FLIP"
    if delta_abs is None:
        return "VALUE_AVAILABILITY_CHANGE"
    if delta_abs > 0 and entry.get("higher_meaning"):
        return entry.get("higher_meaning")
    if delta_abs < 0 and entry.get("higher_meaning"):
        return "LOWER_" + str(entry.get("higher_meaning"))
    return entry.get("meaning") or "VALUE_CHANGE"


def _sign_label(value):
    value = _number(value)
    if value is None:
        return None
    if value > 0:
        return "POSITIVE"
    if value < 0:
        return "NEGATIVE"
    return "ZERO"


def _top_material_changes(changes, limit=8):
    material = [change for change in changes
                if MATERIALITY_RANK.get(change.get("materiality"), 0) > 0]
    return sorted(
        material,
        key=lambda item: (
            -MATERIALITY_RANK.get(item.get("materiality"), 0),
            0 if item.get("sign_flip") else 1,
            0 if item.get("delta_abs") is not None else 1,
            str(item.get("domain") or ""),
            str(item.get("field") or ""),
        ),
    )[:limit]


def _domain_change_summaries(changes):
    summaries = []
    grouped = _group_changes_by_domain(changes)
    for domain in TRANSITION_DOMAIN_ORDER:
        items = grouped.get(domain) or []
        if not items:
            continue
        strongest = _strongest_change(items)
        summaries.append({
            "domain": domain,
            "materiality": strongest.get("materiality") if strongest else "NONE",
            "meaning": strongest.get("meaning") if strongest else None,
            "raw_change_count": len(items),
            "primary_fields": [item.get("field") for item in items[:4]
                               if item.get("field")],
            "source_refs": _unique_values(item.get("source_ref") for item in items),
            "role_transition": _domain_role_transition(items),
            "previous": _domain_value_summary(items, "previous"),
            "current": _domain_value_summary(items, "current"),
            "children": [_raw_change_child(item) for item in items],
        })
    for domain, items in sorted(grouped.items()):
        if domain in TRANSITION_DOMAIN_ORDER or not items:
            continue
        strongest = _strongest_change(items)
        summaries.append({
            "domain": domain,
            "materiality": strongest.get("materiality") if strongest else "NONE",
            "meaning": strongest.get("meaning") if strongest else None,
            "raw_change_count": len(items),
            "primary_fields": [item.get("field") for item in items[:4]
                               if item.get("field")],
            "source_refs": _unique_values(item.get("source_ref") for item in items),
            "role_transition": _domain_role_transition(items),
            "previous": _domain_value_summary(items, "previous"),
            "current": _domain_value_summary(items, "current"),
            "children": [_raw_change_child(item) for item in items],
        })
    return summaries


def _raw_change_groups(changes):
    grouped = _group_changes_by_domain(changes)
    groups = []
    for domain in list(TRANSITION_DOMAIN_ORDER) + sorted(
            domain for domain in grouped if domain not in TRANSITION_DOMAIN_ORDER):
        items = grouped.get(domain) or []
        if not items:
            continue
        strongest = _strongest_change(items)
        groups.append({
            "domain": domain,
            "materiality": strongest.get("materiality") if strongest else "NONE",
            "raw_change_count": len(items),
            "source_refs": _unique_values(item.get("source_ref") for item in items),
            "children": [_raw_change_child(item) for item in items],
        })
    return groups


def _group_changes_by_domain(changes):
    grouped = {}
    material = [change for change in changes
                if MATERIALITY_RANK.get(change.get("materiality"), 0) > 0]
    for change in sorted(material, key=_change_sort_key):
        domain = str(change.get("domain") or "OTHER").upper()
        grouped.setdefault(domain, []).append(change)
    return grouped


def _change_sort_key(item):
    return (
        -MATERIALITY_RANK.get(item.get("materiality"), 0),
        0 if item.get("sign_flip") else 1,
        0 if item.get("delta_abs") is not None else 1,
        str(item.get("field") or ""),
    )


def _strongest_change(items):
    if not items:
        return None
    return sorted(items, key=_change_sort_key)[0]


def _domain_role_transition(items):
    before = _unique_values(item.get("role_before") for item in items)
    after = _unique_values(item.get("role_after") for item in items)
    return {
        "before": before[0] if len(before) == 1 else before,
        "after": after[0] if len(after) == 1 else after,
    }


def _domain_value_summary(items, side):
    values = {}
    for item in items[:6]:
        field = _field_leaf(item.get("field"))
        if field:
            values[field] = item.get(side)
    return values


def _raw_change_child(change):
    return {
        "domain": change.get("domain"),
        "field": change.get("field"),
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
        "source_priority": change.get("source_priority"),
    }


def _field_leaf(field):
    parts = str(field or "").split(".")
    if len(parts) >= 3 and parts[-2].isupper():
        return parts[-2].lower() + "_" + parts[-1]
    return parts[-1] if parts else ""


def _unique_values(values):
    result = []
    for value in values:
        if value in (None, ""):
            continue
        if value not in result:
            result.append(value)
    return result


def _transition_flags(previous, current, top_changes):
    flags = []
    decision = _decision_transition(previous, current)
    if decision.get("block_entered") or (
            str(decision.get("support_before") or "").startswith("TRADE_SUPPORT")
            and str(decision.get("support_after") or "").startswith("NO_TRADE")):
        flags.append("DECISION_SUPPORT_COLLAPSE")
    domains_high = {
        change.get("domain")
        for change in top_changes
        if MATERIALITY_RANK.get(change.get("materiality"), 0) >= MATERIALITY_RANK["HIGH"]
    }
    macro_changes = [
        change for change in top_changes
        if change.get("domain") == "MACRO"
    ]
    if any(change.get("materiality") == "CRITICAL"
           or abs(_number(change.get("delta_abs")) or 0.0) >= 0.2
           for change in macro_changes):
        flags.append("MACRO_SHOCK")
    if "FUNDING" in domains_high:
        flags.append("FUNDING_CROWDING_ESCALATION")
    if "GAMMA" in domains_high:
        flags.append("GAMMA_REGIME_SHIFT")
    if "SKEW" in domains_high:
        flags.append("SKEW_REVERSAL")
    non_decision_domains = {domain for domain in domains_high
                            if domain not in {"DECISION", "QUALITY"}}
    if len(non_decision_domains) >= 2 or (
            "DECISION_SUPPORT_COLLAPSE" in flags and "MACRO_SHOCK" in flags):
        flags.append("MULTI_DOMAIN_RISK_DETERIORATION")
    return flags


def _decision_transition(previous, current):
    prev_decision = _dict(previous.get("decision"))
    curr_decision = _dict(current.get("decision"))
    prev_blocking = _dict(previous.get("blocking"))
    curr_blocking = _dict(current.get("blocking"))
    before_block = bool(prev_blocking.get("has_block")
                        or _dict(previous.get("decision_matrix")).get("decision_state") == "BLOCKED")
    after_block = bool(curr_blocking.get("has_block")
                       or _dict(current.get("decision_matrix")).get("decision_state") == "BLOCKED")
    return {
        "lean_before": prev_decision.get("lean"),
        "lean_after": curr_decision.get("lean"),
        "support_before": prev_decision.get("support_label"),
        "support_after": curr_decision.get("support_label"),
        "confidence_before": prev_decision.get("confidence"),
        "confidence_after": curr_decision.get("confidence"),
        "block_before": before_block,
        "block_after": after_block,
        "block_entered": bool(after_block and not before_block),
        "blocking_reason_after": _dict(curr_blocking.get("hard_veto")).get("veto_reason")
        or curr_blocking.get("block_kind"),
    }


def _materiality_score(top_changes, flags):
    score = 0.0
    weights = {"LOW": 3.0, "MEDIUM": 8.0, "HIGH": 16.0, "CRITICAL": 28.0}
    for change in top_changes:
        score += weights.get(change.get("materiality"), 0.0)
    score += 7.0 * len(flags)
    return min(100.0, round(score, 2))


def _comparison_quality(elapsed_ms):
    if elapsed_ms is None or elapsed_ms < 0:
        return "VERY_LOW"
    minutes = elapsed_ms / 60000.0
    if minutes <= 90:
        return "HIGH"
    if minutes <= 360:
        return "MEDIUM"
    if minutes <= 1440:
        return "LOW"
    return "VERY_LOW"


def _comparison_limitations(previous, current, elapsed_ms, comparison_quality):
    limitations = []
    if comparison_quality in {"LOW", "VERY_LOW"}:
        limitations.append("SPARSE_EVENT_GAP")
    if elapsed_ms is not None and elapsed_ms > 6 * 60 * 60 * 1000:
        limitations.append("EVENT_GAP_OVER_6H")
    if elapsed_ms is not None and elapsed_ms > 24 * 60 * 60 * 1000:
        limitations.append("EVENT_GAP_OVER_24H")
    if _identity(previous).get("strategy_version") != _identity(current).get("strategy_version"):
        limitations.append("DIFFERENT_STRATEGY_VERSION")
    if _dict(previous.get("schema")).get("version") != _dict(current.get("schema")).get("version"):
        limitations.append("DIFFERENT_CARD_SCHEMA_VERSION")
    return limitations


def _recent_trajectory(history, limit=5):
    rows = []
    for record in history[-limit:]:
        identity = _identity(record)
        decision = _dict(record.get("decision"))
        skeleton = _recent_skeleton_values(record)
        rows.append({
            "card_id": identity.get("card_id") or record.get("card_id"),
            "confirmed_time_ms": _event_time_ms(record),
            "episode_id": identity.get("episode_id"),
            "lean": decision.get("lean"),
            "support_label": decision.get("support_label"),
            "macro_score": _number(_get_path(record, "factor_cross_section.macro_pressure.macro_score")),
            "funding_last_rate": _number(_get_path(record, "factor_cross_section.funding.last_rate")),
            "gamma_regime": _get_path(record, "factor_cross_section.gamma_regime.regime"),
            **skeleton,
        })
    return rows


def _recent_skeleton_values(record):
    return {
        "tmv_blend": _number(_first_transition_value(
            record, "TMV", ("factor_cross_section.tmvf.tmv_blend", "tmv_blend"))),
        "tmvf_24h_final": _number(_first_transition_value(
            record, "TMV", (
                "factor_cross_section.tmvf.tmvf_24h.tmv_final",
                "factor_cross_section.tmvf.tmvf_24h.final",
                "tmvf_24h_final",
            ))),
        "tmvf_48h_final": _number(_first_transition_value(
            record, "TMV", (
                "factor_cross_section.tmvf.tmvf_48h.tmv_final",
                "factor_cross_section.tmvf.tmvf_48h.final",
                "tmvf_48h_final",
            ))),
        "net_gamma_notional_usd": _number(_first_transition_value(
            record, "GAMMA", (
                "factor_cross_section.gex_info.net_gamma_notional_usd",
                "factor_cross_section.gex_info.total_net_gex",
                "factor_cross_section.gex_info.net_gamma_notional",
                "factor_cross_section.gamma_regime.net_gamma_notional_usd",
                "factor_cross_section.gamma_regime.net_gamma_notional",
            ))),
        "put_call_ratio": _number(_first_transition_value(
            record, "P_C_RATIO", (
                "factor_cross_section.gex_info.put_call_ratio",
                "factor_cross_section.gex_info.pc_ratio",
                "factor_cross_section.gex_info.pcr",
            ))),
        "conflict_ratio": _number(_get_path(record, "conflict.ratio")),
        "skew_vote": _first_transition_value(
            record, "SKEW", ("factor_cross_section.skew.vote", "vote")),
        "skew_rr_blend": _number(_first_transition_value(
            record, "SKEW", ("factor_cross_section.skew.rr_blend", "rr_blend"))),
    }


def _baseline_24h(history, current_ts_ms):
    if not current_ts_ms:
        return {"available": False, "reason": "NO_CURRENT_EVENT_TIME"}
    window_start = current_ts_ms - 24 * 60 * 60 * 1000
    candidates = [record for record in history
                  if (_event_time_ms(record) or 0) >= window_start
                  and (_event_time_ms(record) or 0) <= current_ts_ms]
    if not candidates:
        return {"available": False, "reason": "NO_CARD_IN_24H_WINDOW"}
    baseline = candidates[0]
    identity = _identity(baseline)
    return {
        "available": True,
        "card_id": identity.get("card_id") or baseline.get("card_id"),
        "elapsed_ms": int(current_ts_ms - (_event_time_ms(baseline) or current_ts_ms)),
        "event_count": len(candidates),
    }


def _episode_anchor(history, current):
    current_episode = _episode_id(current)
    if not current_episode:
        return {"available": False, "reason": "NO_EPISODE_ID"}
    for record in history:
        if _episode_id(record) == current_episode:
            identity = _identity(record)
            return {
                "available": True,
                "episode_id": current_episode,
                "card_id": identity.get("card_id") or record.get("card_id"),
                "elapsed_ms": int((_event_time_ms(current) or 0) - (_event_time_ms(record) or 0)),
            }
    return {"available": False, "reason": "NO_SAME_EPISODE_ANCHOR"}


def _trajectory_summary(history):
    recent = _recent_trajectory(history, limit=5)
    macro_values = [_number(item.get("macro_score")) for item in recent
                    if _number(item.get("macro_score")) is not None]
    funding_values = [_number(item.get("funding_last_rate")) for item in recent
                      if _number(item.get("funding_last_rate")) is not None]
    gamma_regimes = [item.get("gamma_regime") for item in recent
                     if item.get("gamma_regime")]
    return {
        "recent_event_count": len(recent),
        "macro_direction": _direction_from_values(macro_values,
                                                  high_label="DETERIORATING",
                                                  low_label="EASING"),
        "funding_direction": _direction_from_values(funding_values,
                                                    high_label="CROWDING_UP",
                                                    low_label="CROWDING_DOWN"),
        "gamma_last_regime": gamma_regimes[-1] if gamma_regimes else None,
    }


def _direction_from_values(values, high_label, low_label):
    if len(values) < 2:
        return "INSUFFICIENT_HISTORY"
    delta = values[-1] - values[0]
    if delta > 0:
        return high_label
    if delta < 0:
        return low_label
    return "UNCHANGED"


def _domain_states(top_changes):
    states = {}
    for domain in ("MACRO", "FUNDING", "GAMMA", "SKEW", "DECISION", "QUALITY"):
        domain_changes = [change for change in top_changes
                          if change.get("domain") == domain]
        if not domain_changes:
            continue
        strongest = max(domain_changes,
                        key=lambda item: MATERIALITY_RANK.get(item.get("materiality"), 0))
        if domain == "MACRO" and MATERIALITY_RANK.get(strongest.get("materiality"), 0) >= MATERIALITY_RANK["HIGH"]:
            states[domain] = "SHOCK"
        elif domain == "FUNDING":
            states[domain] = "RISING_NON_VOTING" if (strongest.get("role_after") == "NON_VOTING") else "CHANGED"
        elif domain == "GAMMA":
            states[domain] = "STRUCTURE_SHIFT"
        elif domain == "SKEW":
            states[domain] = "VOTE_SHIFT"
        elif domain == "DECISION":
            states[domain] = "SYSTEM_STATE_CHANGED"
        else:
            states[domain] = "CHANGED"
    return states


def _event_time_ms(record):
    identity = _identity(record)
    for value in (
            _dict(_dict(record.get("provenance")).get("transition_audit_source")).get("event_time_ms"),
            identity.get("confirmed_time_ms"),
            record.get("confirmed_time_ms"),
            identity.get("confirmed_at"),
            record.get("created_at")):
        parsed = _timestamp_sort_value(value)
        if parsed:
            return int(parsed)
    return None


def _episode_id(record):
    return _identity(record).get("episode_id") or record.get("episode_id")


def _producer_anchor(record):
    anchor = _dict(_dict(record.get("provenance")).get("transition_audit_source"))
    identity = _identity(record)
    event_time_ms = anchor.get("event_time_ms")
    source_fields = []
    if event_time_ms in (None, ""):
        event_time_ms = identity.get("confirmed_time_ms") or record.get("confirmed_time_ms")
        source_fields.append("identity.confirmed_time_ms")
    if event_time_ms in (None, ""):
        event_time_ms = identity.get("confirmed_at") or record.get("created_at")
        source_fields.append("identity.confirmed_at")
    native = (
        anchor.get("schema_name") == "SignalTransitionProducerAnchor"
        and anchor.get("schema_version") == "1.0.0"
        and anchor.get("audit_scope") == "AUDIT_ONLY"
        and anchor.get("event_time_basis") == "identity.confirmed_time_ms"
        and anchor.get("transition_computation_owner") == "MATERIALIZER_DERIVED"
        and anchor.get("event_time_ms") not in (None, "")
    )
    return {
        "native": bool(native),
        "schema_name": anchor.get("schema_name"),
        "schema_version": anchor.get("schema_version"),
        "audit_scope": anchor.get("audit_scope"),
        "event_time_ms": event_time_ms,
        "event_time_basis": anchor.get("event_time_basis")
        or (";".join(source_fields) if source_fields else None),
        "transition_computation_owner": anchor.get("transition_computation_owner"),
        "compat_backfill_applied": not native,
        "compat_backfill_source": None if native else "materializer_transition_producer_anchor_compat_v1",
        "compat_source_fields": source_fields,
    }


def _compat_source_fields(*anchors):
    fields = []
    for anchor in anchors:
        for field in anchor.get("compat_source_fields") or []:
            if field not in fields:
                fields.append(field)
    return fields


def _producer_record_hash(record):
    return _dict(record.get("integrity")).get("record_hash")


def _write_jsonl(path, rows):
    text = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")) + "\n"
        for row in rows
    )
    _atomic_write_text(path, text)


def _write_transition_state(path, transitions):
    last = transitions[-1] if transitions else {}
    _write_json(path, {
        "schema_name": "SignalTransitionState",
        "schema_version": "signal_transition_state@1.0.0",
        "computation_version": TRANSITION_COMPUTATION_VERSION,
        "updated_at": _now_iso(),
        "transition_count": len(transitions),
        "last_transition_id": last.get("transition_id"),
        "last_current_card_id": last.get("current_card_id"),
        "last_transition_hash": last.get("record_hash"),
    })


def _write_trajectory_files(output, records, transitions):
    trajectory_dir = Path(output) / "signal_cards" / "trajectory"
    trajectory_dir.mkdir(parents=True, exist_ok=True)
    _chmod_public_dir(trajectory_dir)
    by_symbol_records = {}
    for record in sorted(records, key=_sort_key):
        identity = _identity(record)
        symbol = str(identity.get("symbol") or record.get("symbol") or "UNKNOWN")
        by_symbol_records.setdefault(symbol, []).append(record)
    by_symbol_transitions = {}
    for transition in transitions:
        by_symbol_transitions.setdefault(
            str(transition.get("symbol") or "UNKNOWN"), []).append(transition)
    expected = set()
    for symbol, symbol_records in by_symbol_records.items():
        filename = _filename_for_symbol(symbol)
        expected.add(filename)
        _write_json(trajectory_dir / filename, {
            "schema_name": "SignalTrajectory",
            "schema_version": "signal_trajectory@1.0.0",
            "audit_scope": "AUDIT_ONLY",
            "generated_at": _now_iso(),
            "symbol": symbol,
            "event_count": len(symbol_records),
            "events": _recent_trajectory(symbol_records, limit=len(symbol_records)),
            "recent_transitions": [
                {
                    "transition_id": transition.get("transition_id"),
                    "current_card_id": transition.get("current_card_id"),
                    "elapsed_ms": transition.get("elapsed_ms"),
                    "comparison_quality": transition.get("comparison_quality"),
                    "cross_domain_flags": transition.get("cross_domain_flags"),
                    "materiality_score": transition.get("materiality_score"),
                    "record_hash": transition.get("record_hash"),
                }
                for transition in by_symbol_transitions.get(symbol, [])[-5:]
            ],
        })
    for path in trajectory_dir.glob("*.json"):
        if path.name not in expected:
            path.unlink()


def _filename_for_symbol(symbol):
    safe = re.sub(r"[^A-Za-z0-9_.+@=-]+", "_", str(symbol or "").strip())
    safe = safe.strip("._")
    return (safe or "UNKNOWN") + ".json"


def _review_status(review):
    if not isinstance(review, dict):
        return ""
    return str(review.get("status") or "").upper()


def _identity(record):
    identity = record.get("identity")
    return identity if isinstance(identity, dict) else {}


def _quality(record):
    quality = record.get("quality")
    if isinstance(quality, dict):
        return quality.get("overall")
    return quality


def _manifest_card_summary(record):
    """Return deterministic list/filter fields without publishing card detail."""
    identity = _identity(record)
    decision = _dict(record.get("decision"))
    display_layers = _dict(record.get("display_layers"))
    durability = _dict(record.get("signal_durability"))
    comfort = _dict(durability.get("comfort_window"))
    transition = _dict(record.get("transition_context"))
    relation = _dict(transition.get("relation"))
    review = _dict(record.get("llm_review"))
    if _is_evidence_v2(record):
        from signal_review_v2 import build_summary
        return {
            "identity": {key: identity.get(key) for key in (
                "card_id", "short_id", "confirmed_at", "symbol", "strategy_name",
                "event_type", "tags", "is_synthetic") if identity.get(key) is not None},
            "llm_review_status": review.get("status"),
            "signal_evidence_summary": build_summary(review),
        }
    summary = {
        "identity": {
            key: identity.get(key)
            for key in (
                "card_id", "short_id", "confirmed_at", "symbol",
                "strategy_name", "event_type", "tags", "is_synthetic",
            )
            if identity.get(key) not in (None, "", [])
        },
        "decision": {
            key: decision.get(key)
            for key in ("lean", "support_label", "confidence")
            if decision.get(key) is not None
        },
        "quality": {"overall": _quality(record)},
        "display_layers": {"headline": display_layers.get("headline")},
        "signal_durability": {
            "headline_score": durability.get("headline_score"),
            "headline_state": durability.get("headline_state"),
            "comfort_window": {
                key: comfort.get(key)
                for key in ("tag", "time_window", "window", "clock_window")
                if comfort.get(key) not in (None, "")
            },
        },
        "transition_context": {
            "comparison_quality": transition.get("comparison_quality"),
            "cross_domain_flags": list(
                transition.get("cross_domain_flags") or []),
            "relation": {
                "comparison_quality": relation.get("comparison_quality"),
            },
        } if transition else {},
        "llm_review_status": _review_status(review) or "MISSING",
    }
    signal_rating_summary = _signal_rating_summary(record)
    if signal_rating_summary:
        summary["signal_rating_summary"] = signal_rating_summary
    if "signal_comfort_summary" in record:
        signal_comfort_summary = record.get("signal_comfort_summary")
    else:
        signal_comfort_summary = _signal_comfort_summary(record)
    if signal_comfort_summary:
        summary["signal_comfort_summary"] = signal_comfort_summary
    return summary


def _source_schema_fingerprint(record):
    schema = _dict(record.get("schema"))
    if schema:
        text = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    return record.get("schema_version")


def _is_evidence_v2(record):
    return _dict(record.get("llm_review")).get("schema_version") == SIGNAL_EVIDENCE_REVIEW_SCHEMA_VERSION


def _uses_evidence_review_path(record):
    return _is_evidence_review_protocol(_dict(record.get("llm_review")))


def _is_unsupported_evidence_review(record):
    review = _dict(record.get("llm_review"))
    return _is_evidence_review_protocol(review) and review.get("schema_version") != SIGNAL_EVIDENCE_REVIEW_SCHEMA_VERSION


def _unsupported_evidence_review(record):
    from signal_evidence_v2 import build_evidence_packet
    from signal_review_v2 import build_error_review, revalidate_review

    original_review = _json_clone(_dict(record.get("llm_review")))
    packet = build_evidence_packet(record)
    record["invalid_llm_review_archive"] = original_review
    record["llm_review"] = build_error_review(
        record,
        packet,
        "这张卡的综合评审格式尚未受当前版本支持，暂未完成有效评级。",
        model=original_review.get("model"),
        reviewed_at=original_review.get("reviewed_at"),
        require_price_bias=True,
    )
    record["llm_review"]["materializer_revalidation"] = "unsupported_review_schema"
    revalidate_review(record, record["llm_review"])


def _validate_evidence_v2(record, expected_packet=None):
    """Validate against raw source before display compatibility enrichments."""
    from signal_evidence_v2 import build_evidence_packet
    from signal_review_v2 import (
        MODEL_PRICE_BIAS_FIELDS,
        MODEL_SIDE_FIELDS,
        PROMPT_VERSION as REVIEW_V2_PROMPT_VERSION,
        _assessment_hash,
        build_error_review,
        build_review,
        revalidate_review,
        _fact_assertion_issues,
        _valid_refs,
    )
    review = record["llm_review"]
    packet = build_evidence_packet(record)

    def requires_price_bias(candidate):
        advisory = _dict(candidate.get("integrated_trade_advisory"))
        return (
            candidate.get("prompt_version") == REVIEW_V2_PROMPT_VERSION
            or "price_bias" in advisory
        )

    def review_prompt(candidate):
        return candidate.get("prompt_version")

    def model_payload_from(candidate):
        advisory = _dict(candidate.get("integrated_trade_advisory"))
        payload = {"side_evidence_ratings": {
            key: {field: side.get(field) for field in MODEL_SIDE_FIELDS}
            for key, side in _dict(advisory.get("side_evidence_ratings")).items()
        }}
        price_bias = _dict(advisory.get("price_bias"))
        if price_bias.get("status") == "ASSESSED":
            payload["price_bias"] = {
                field: price_bias.get(field) for field in MODEL_PRICE_BIAS_FIELDS
            }
        return payload

    def preserved_unavailable_price_bias(candidate):
        price_bias = _dict(
            _dict(candidate.get("integrated_trade_advisory")).get("price_bias")
        )
        if price_bias.get("status") == "UNAVAILABLE":
            return _json_clone(price_bias)
        return None

    def restore_unavailable_price_bias(candidate, preserved):
        if not preserved:
            return candidate
        advisory = _dict(candidate.get("integrated_trade_advisory"))
        if not advisory:
            return candidate
        preserved = _json_clone(preserved)
        facts = {fact["id"]: fact for fact in advisory["market_facts"]}
        as_of_ms = candidate["evidence_context"]["identity"]["as_of_ms"]
        removed_refs = False
        for field in ("evidence_refs", "counter_evidence_refs"):
            refs, _, _ = _valid_refs(preserved[field], facts, as_of_ms=as_of_ms)
            removed_refs = removed_refs or refs != preserved[field]
            preserved[field] = refs
        if removed_refs:
            reason = "原方向引用的部分事实已不可核验，仅保留可用引用。"
            if reason not in preserved["validation_reasons_cn"]:
                preserved["validation_reasons_cn"].append(reason)
        advisory["price_bias"] = preserved
        sides = _dict(advisory.get("side_evidence_ratings"))
        rated_count = sum(
            1 for side in sides.values()
            if _dict(side).get("status") == "RATED"
        )
        status = "PARTIAL" if rated_count else "ERROR"
        candidate["status"] = status
        validation = _dict(advisory.get("validation"))
        validation["status"] = status
        validation["validation_reasons_cn"] = [
            reason
            for side in sides.values()
            for reason in _dict(side).get("validation_reasons_cn", [])
        ] + list(preserved.get("validation_reasons_cn") or [])
        validation["assessment_hash"] = None
        validation["assessment_hash"] = _assessment_hash(advisory)
        return candidate

    try:
        # Check integrity, shape, references and permissions before isolating a
        # newly detected side-local claim error. No model request is made here.
        revalidate_review(record, review, recheck_claims=False)
        if review["evidence_context"]["identity"] != packet["identity"]:
            raise ValueError("source identity mismatch")
        # Current facts must be reproducible; frozen prior-card differences were
        # separately checked by the packet builder at assessment time.
        expected = {f["id"]: f for f in packet["facts"] if f.get("topic") != "change_context"}
        actual = {f["id"]: f for f in review["integrated_trade_advisory"]["market_facts"]
                  if f.get("topic") != "change_context"}
        if actual != expected:
            raise ValueError("current source facts mismatch")
        used_changes = [f for f in review["integrated_trade_advisory"]["market_facts"]
                        if f.get("topic") == "change_context"]
        if any(f.get("usable") for f in used_changes):
            expected_changes = [f for f in (expected_packet or packet)["facts"]
                                if f.get("topic") == "change_context"]
            if used_changes != expected_changes:
                record["invalid_llm_review_archive"] = review
                invalid_ids = {f["id"] for f in used_changes}
                payload = model_payload_from(review)
                # Preserve unaffected sides; references to unverifiable changes
                # become side-local validation errors against the current packet.
                for side in payload["side_evidence_ratings"].values():
                    if invalid_ids.intersection(side.get("evidence_refs", []) + side.get("counter_evidence_refs", [])):
                        side["grade"] = None
                        side["unresolved_conditions_cn"] = list(side.get("unresolved_conditions_cn") or []) + ["前后变化缺少可核验的对应资料。"]
                preserved_bias = preserved_unavailable_price_bias(review)
                record["llm_review"] = restore_unavailable_price_bias({**review, **build_review(
                    record, payload, packet, model=review.get("model"),
                    reviewed_at=review.get("reviewed_at"),
                    require_price_bias=requires_price_bias(review),
                    prompt_version=review_prompt(review))}, preserved_bias)
                record["llm_review"]["materializer_revalidation"] = "change_source_unavailable"
        checked = record["llm_review"]
        advisory = checked["integrated_trade_advisory"]
        facts = {fact["id"]: fact for fact in advisory["market_facts"]}
        if any(side.get("status") == "RATED" and _fact_assertion_issues(side, facts)
               for side in advisory["side_evidence_ratings"].values()):
            record.setdefault("invalid_llm_review_archive", review)
            frozen_packet = {**checked["evidence_context"], "facts": advisory["market_facts"]}
            payload = model_payload_from(checked)
            preserved_bias = preserved_unavailable_price_bias(checked)
            record["llm_review"] = restore_unavailable_price_bias({**checked, **build_review(
                record, payload, frozen_packet, model=checked.get("model"),
                reviewed_at=checked.get("reviewed_at"),
                require_price_bias=requires_price_bias(checked),
                prompt_version=review_prompt(checked))}, preserved_bias)
            record["llm_review"]["materializer_revalidation"] = "claim_scope_unavailable"
        checked = record["llm_review"]
        advisory = checked["integrated_trade_advisory"]
        facts = {fact["id"]: fact for fact in advisory["market_facts"]}
        price_bias = _dict(advisory.get("price_bias"))
        if (price_bias.get("status") == "ASSESSED"
                and _fact_assertion_issues(price_bias, facts)):
            record.setdefault("invalid_llm_review_archive", review)
            frozen_packet = {**checked["evidence_context"], "facts": advisory["market_facts"]}
            record["llm_review"] = {**checked, **build_review(
                record, model_payload_from(checked), frozen_packet,
                model=checked.get("model"), reviewed_at=checked.get("reviewed_at"),
                require_price_bias=requires_price_bias(checked),
                prompt_version=review_prompt(checked))}
            record["llm_review"]["materializer_revalidation"] = "claim_scope_unavailable"
        revalidate_review(record, record["llm_review"])
    except (ValueError, KeyError, TypeError):
        record["invalid_llm_review_archive"] = review
        record["llm_review"] = build_error_review(
            record, packet, "评级资料与本卡来源不一致，暂未完成有效评级。",
            model=review.get("model"), reviewed_at=review.get("reviewed_at"),
            require_price_bias=requires_price_bias(review),
            prompt_version=review_prompt(review))


def _signal_rating_summary(record):
    rating = _dict(record.get("signal_rating"))
    if not rating:
        return None
    if rating.get("schema") != SIGNAL_RATING_SCHEMA_VERSION:
        return None
    if rating.get("rating_scope") != SIGNAL_RATING_SCOPE:
        return None
    if rating.get("candidate_quote_economics") != "not_evaluated":
        return None
    as_of_ms = _number(rating.get("as_of_ms"))
    if as_of_ms is None or as_of_ms <= 0 or not math.isfinite(as_of_ms):
        return None
    claims = _dict(rating.get("claims"))
    structure = _signal_rating_claim_summary(claims.get("structure"))
    put_pressure = _signal_rating_claim_summary(claims.get("put_pressure"))
    call_pressure = _signal_rating_claim_summary(claims.get("call_pressure"))
    if not (structure and put_pressure and call_pressure):
        return None
    return {
        "schema": rating.get("schema"),
        "rating_scope": rating.get("rating_scope"),
        "as_of_ms": rating.get("as_of_ms"),
        "structure": structure,
        "put_pressure": put_pressure,
        "call_pressure": call_pressure,
    }


def _signal_rating_claim_summary(claim):
    claim = _dict(claim)
    status = str(claim.get("status") or "").upper()
    summary_cn = claim.get("summary_cn")
    if status not in SIGNAL_RATING_CLAIM_STATUSES:
        return None
    if not isinstance(summary_cn, str) or not summary_cn.strip():
        return None
    if not isinstance(claim.get("required_inputs"), list):
        return None
    support = claim.get("support")
    opposition = claim.get("opposition")
    unknowns = claim.get("unknowns")
    if not all(isinstance(value, list)
               for value in (support, opposition, unknowns)):
        return None
    if not all(_signal_rating_basis_item_valid(item)
               for item in support + opposition):
        return None
    if not all(_signal_rating_unknown_item_valid(item) for item in unknowns):
        return None
    if status == "SUPPORTED" and (not support or opposition):
        return None
    if status == "CONFLICTED" and (not support or not opposition):
        return None
    if status == "OPPOSED" and (not opposition or support):
        return None
    return {
        "status": status,
        "summary_cn": summary_cn.strip(),
    }


def _signal_rating_basis_item_valid(item):
    item = _dict(item)
    return all(
        isinstance(item.get(key), str) and bool(item.get(key).strip())
        for key in ("source_ref", "source_group", "basis_cn")
    )


def _signal_rating_unknown_item_valid(item):
    item = _dict(item)
    return (
        isinstance(item.get("source_ref"), str)
        and bool(item.get("source_ref").strip())
        and isinstance(item.get("reason_cn"), str)
        and bool(item.get("reason_cn").strip())
    )


def _signal_comfort_summary(record):
    review = _dict(record.get("llm_review"))
    if _review_status(review) != "OK":
        return None
    advisory = _dict(review.get("integrated_trade_advisory"))
    comfort = _dict(advisory.get("side_comfort_ratings"))
    if not comfort:
        return None
    if not _signal_comfort_core_consistent(record, advisory, comfort):
        return None
    if comfort.get("schema") != SIGNAL_COMFORT_SCHEMA_VERSION:
        return None
    if comfort.get("rating_scope") != SIGNAL_COMFORT_SCOPE:
        return None
    if comfort.get("candidate_quote_economics") != "not_evaluated":
        return None
    as_of_ms = _number(comfort.get("as_of_ms"))
    if as_of_ms is None or as_of_ms <= 0 or not math.isfinite(as_of_ms):
        return None
    native_rating = _signal_rating_summary(record)
    if not native_rating:
        return None
    native_as_of_ms = _number(native_rating.get("as_of_ms"))
    if native_as_of_ms != as_of_ms:
        return None

    put = _signal_comfort_side_summary(comfort.get("put_credit"))
    call = _signal_comfort_side_summary(comfort.get("call_credit"))
    if put is None or call is None:
        return None
    if not _signal_comfort_admission_consistent(
            record, advisory, native_rating, "put_credit", put):
        return None
    if not _signal_comfort_admission_consistent(
            record, advisory, native_rating, "call_credit", call):
        return None
    headline = _signal_comfort_headline_summary(
        comfort.get("headline"), put, call)
    if headline is None:
        return None
    return {
        "schema": comfort.get("schema"),
        "rating_scope": comfort.get("rating_scope"),
        "candidate_quote_economics": comfort.get("candidate_quote_economics"),
        "as_of_ms": comfort.get("as_of_ms"),
        "headline": headline,
        "put_credit": put,
        "call_credit": call,
    }


def _signal_comfort_side_summary(side):
    side = _dict(side)
    status = str(side.get("status") or "").upper()
    if status not in {"RATED", "UNRATED"}:
        return None
    model_grade = _signal_comfort_grade(side.get("model_grade"))
    final_grade = _signal_comfort_grade(side.get("final_grade"))
    if status == "RATED":
        if final_grade is None or model_grade is None:
            return None
        if (SIGNAL_COMFORT_GRADE_RANK[final_grade]
                > SIGNAL_COMFORT_GRADE_RANK[model_grade]):
            return None
    elif final_grade is not None:
        return None
    if side.get("model_grade") not in (None, "") and model_grade is None:
        return None
    text_fields = (
        "basis_cn",
        "counter_evidence_cn",
        "next_observation_cn",
    )
    for field in text_fields:
        value = side.get(field)
        if not isinstance(value, str) or not value.strip():
            return None
    list_fields = (
        "unresolved_conditions_cn",
        "evidence_refs",
        "counter_evidence_refs",
        "cap_reasons_cn",
        "s_upgrade_evidence_refs",
    )
    for field in list_fields:
        value = side.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return None
    if status == "RATED":
        evidence_refs = [
            item for item in side.get("evidence_refs", [])
            if isinstance(item, str) and item.strip()
        ]
        counter_evidence_refs = [
            item for item in side.get("counter_evidence_refs", [])
            if isinstance(item, str) and item.strip()
        ]
        if final_grade in {"B", "A", "S"} and not evidence_refs:
            return None
        if final_grade in {"C", "D"} and not (
                evidence_refs or counter_evidence_refs):
            return None
    if final_grade == "S":
        basis = side.get("s_upgrade_basis_cn")
        if (not isinstance(basis, str) or not basis.strip()
                or not side.get("s_upgrade_evidence_refs")):
            return None
    elif side.get("s_upgrade_basis_cn") not in (None, ""):
        if not isinstance(side.get("s_upgrade_basis_cn"), str):
            return None
    return {
        "status": status,
        "final_grade": final_grade,
        "basis_cn": side["basis_cn"].strip(),
        "cap_reasons_cn": list(side.get("cap_reasons_cn") or []),
    }


def _signal_comfort_core_module():
    global _SIGNAL_COMFORT_CORE
    if _SIGNAL_COMFORT_CORE is None:
        _SIGNAL_COMFORT_CORE = importlib.import_module("signal_llm_review")
    return _SIGNAL_COMFORT_CORE


def _signal_comfort_core_consistent(record, advisory, comfort):
    try:
        core = _signal_comfort_core_module()
        packet = core.build_review_packet(_json_clone(record))
        core._validate_side_comfort_ratings(comfort, packet)
        expected = core._finalize_side_comfort_ratings(
            _signal_comfort_raw_model_shape(comfort), packet, advisory)
    except Exception:
        return False
    return _signal_comfort_finalized_shape_matches(comfort, expected)


def _signal_comfort_raw_model_shape(comfort):
    comfort = _dict(comfort)
    return {
        side_key: _signal_comfort_raw_side(
            _dict(comfort.get(side_key)))
        for side_key in ("put_credit", "call_credit")
    }


def _signal_comfort_raw_side(side):
    model_grade = _signal_comfort_grade(side.get("model_grade"))
    return {
        "grade": model_grade or "UNRATED",
        "basis_cn": side.get("basis_cn"),
        "counter_evidence_cn": side.get("counter_evidence_cn"),
        "unresolved_conditions_cn": list(
            side.get("unresolved_conditions_cn") or []),
        "next_observation_cn": side.get("next_observation_cn"),
        "evidence_refs": list(side.get("evidence_refs") or []),
        "counter_evidence_refs": list(
            side.get("counter_evidence_refs") or []),
        "s_upgrade_basis_cn": side.get("s_upgrade_basis_cn") or "",
        "s_upgrade_evidence_refs": list(
            side.get("s_upgrade_evidence_refs") or []),
    }


def _signal_comfort_finalized_shape_matches(comfort, expected):
    comfort = _dict(comfort)
    expected = _dict(expected)
    for side_key in ("put_credit", "call_credit"):
        side = _dict(comfort.get(side_key))
        expected_side = _dict(expected.get(side_key))
        if side.get("status") != expected_side.get("status"):
            return False
        if side.get("final_grade") != expected_side.get("final_grade"):
            return False
    headline = _dict(comfort.get("headline"))
    expected_headline = _dict(expected.get("headline"))
    return (
        headline.get("final_grade") == expected_headline.get("final_grade")
        and headline.get("focus_side") == expected_headline.get("focus_side")
    )


def _json_clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def _signal_comfort_admission_consistent(
        record, advisory, native_rating, side_key, side):
    final_grade = side.get("final_grade")
    if (_signal_comfort_record_has_explicit_window_failure(record)
            and final_grade in SIGNAL_COMFORT_GRADE_RANK
            and final_grade != "D"):
        return False
    if final_grade not in SIGNAL_COMFORT_ADMISSION_GRADES:
        return True
    if _signal_comfort_record_has_any_producer_block(record):
        return False
    if _signal_comfort_record_requires_confirmation(record):
        return False
    if _signal_comfort_record_has_window_not_open_or_invalid(record):
        return False
    if not _signal_comfort_side_allows_admission(side_key, advisory, record):
        return False
    quality = _dict(record.get("quality"))
    if quality.get("overall") not in (None, "OK"):
        return False
    if not _signal_comfort_native_allows_admission(native_rating, side_key):
        return False
    return True


def _signal_comfort_record_has_any_producer_block(record):
    if _signal_comfort_record_has_producer_hard_block(record):
        return True
    decision = _dict(record.get("decision"))
    matrix = _dict(record.get("decision_matrix"))
    blocking = _dict(record.get("blocking"))
    values = {
        str(decision.get("support_label") or "").upper(),
        str(decision.get("support_pre_gate") or "").upper(),
        str(matrix.get("decision_state") or "").upper(),
        str(blocking.get("block_kind") or "").upper(),
    }
    if values & {"BLOCKED", "NO_TRADE_BLOCKED", "BLOCK", "SOFT_GATE"}:
        return True
    return bool(blocking.get("has_block") or blocking.get("soft_gates"))


def _signal_comfort_record_has_producer_hard_block(record):
    decision = _dict(record.get("decision"))
    matrix = _dict(record.get("decision_matrix"))
    blocking = _dict(record.get("blocking"))
    support_values = {
        str(decision.get("support_label") or "").upper(),
        str(decision.get("support_pre_gate") or "").upper(),
        str(matrix.get("decision_state") or "").upper(),
    }
    if "NO_TRADE_BLOCKED" in support_values:
        return True
    if str(blocking.get("block_kind") or "").upper() == "HARD":
        return True
    if blocking.get("hard_block") is True or blocking.get("hard_blocked") is True:
        return True
    if blocking.get("hard_veto") not in (None, {}, [], False):
        return True
    macro = _dict(_dict(record.get("factor_cross_section")).get("macro_pressure"))
    if _dict(macro.get("macro_shock")).get("block") is True:
        return True
    return False


def _signal_comfort_record_requires_confirmation(record):
    decision = _dict(record.get("decision"))
    matrix = _dict(record.get("decision_matrix"))
    return bool({
        "WAIT_CONFIRMATION",
        "WAIT_FOR_CONFIRMATION",
        "WAITING_CONFIRMATION",
        "PENDING_CONFIRMATION",
    } & {
        str(decision.get("support_label") or "").upper(),
        str(decision.get("support_pre_gate") or "").upper(),
        str(matrix.get("decision_state") or "").upper(),
    })


def _signal_comfort_record_has_window_not_open_or_invalid(record):
    if _signal_comfort_record_has_explicit_window_failure(record):
        return True
    window = _dict(record.get("signal_window"))
    if not window:
        return False
    if "is_active" in window and window.get("is_active") is False:
        return True
    neutral = _dict(window.get("neutral_repair"))
    if "is_active" in neutral and neutral.get("is_active") is False:
        return True
    return False


def _signal_comfort_record_has_explicit_window_failure(record):
    window = _dict(record.get("signal_window"))
    if not window:
        return False
    state_values = {
        str(window.get("nr_state") or "").upper(),
        str(window.get("state") or "").upper(),
        str(_dict(window.get("neutral_repair")).get("state") or "").upper(),
    }
    if any(any(token in state for token in (
            "STALE", "EXPIRED", "TIMEOUT", "INVALID", "FAILED"))
            for state in state_values if state):
        return True
    return False


def _signal_comfort_side_allows_admission(side_key, advisory, record):
    recommendation = str(_dict(advisory).get("recommendation") or "").upper()
    direction = _signal_comfort_producer_direction(record)
    if recommendation not in {
            "SELL_PUT_SPREAD_REVIEW", "SELL_CALL_SPREAD_REVIEW",
            "NEUTRAL_SINGLE_SIDE_REVIEW"}:
        return False
    if recommendation == "SELL_PUT_SPREAD_REVIEW" and side_key != "put_credit":
        return False
    if recommendation == "SELL_CALL_SPREAD_REVIEW" and side_key != "call_credit":
        return False
    if direction == "BULLISH" and side_key == "call_credit":
        return False
    if direction == "BEARISH" and side_key == "put_credit":
        return False
    if direction == "NEUTRAL" and recommendation != "NEUTRAL_SINGLE_SIDE_REVIEW":
        return False
    return True


def _signal_comfort_producer_direction(record):
    decision = _dict(record.get("decision"))
    matrix = _dict(record.get("decision_matrix"))
    text = str(decision.get("lean") or matrix.get("direction") or "").upper()
    if "BULLISH" in text or text in {"UP", "LONG"}:
        return "BULLISH"
    if "BEARISH" in text or text in {"DOWN", "SHORT"}:
        return "BEARISH"
    if "NEUTRAL" in text or text in {"RANGE", "FLAT"}:
        return "NEUTRAL"
    return "UNKNOWN"


def _signal_comfort_native_allows_admission(native_rating, side_key):
    if _dict(native_rating.get("structure")).get("status") == "OPPOSED":
        return False
    side_claim = "put_pressure" if side_key == "put_credit" else "call_pressure"
    if _dict(native_rating.get(side_claim)).get("status") == "OPPOSED":
        return False
    return True


def _signal_comfort_headline_summary(headline, put, call):
    headline = _dict(headline)
    final_grade = _signal_comfort_grade(headline.get("final_grade"))
    if headline.get("final_grade") not in (None, "") and final_grade is None:
        return None
    focus_side = str(headline.get("focus_side") or "")
    if focus_side not in SIGNAL_COMFORT_FOCUS_SIDES:
        return None
    action_cn = headline.get("action_cn")
    if not isinstance(action_cn, str) or not action_cn.strip():
        return None
    expected_grade, expected_focus = _signal_comfort_expected_headline(put, call)
    if final_grade != expected_grade or focus_side != expected_focus:
        return None
    return {
        "final_grade": final_grade,
        "focus_side": focus_side,
        "action_cn": action_cn.strip(),
    }


def _signal_comfort_expected_headline(put, call):
    side_grades = []
    for side_name, side in (("put_credit", put), ("call_credit", call)):
        grade = side.get("final_grade")
        if grade in SIGNAL_COMFORT_GRADE_RANK:
            side_grades.append((side_name, grade, SIGNAL_COMFORT_GRADE_RANK[grade]))
    if not side_grades:
        return None, "none"
    best_rank = max(rank for _side_name, _grade, rank in side_grades)
    best = [
        (side_name, grade)
        for side_name, grade, rank in side_grades
        if rank == best_rank
    ]
    grade = best[0][1]
    focus = best[0][0] if len(best) == 1 else "tie"
    return grade, focus


def _signal_comfort_grade(value):
    if isinstance(value, str):
        grade = value.strip().upper()
        if grade in SIGNAL_COMFORT_GRADE_RANK:
            return grade
    return None


def _is_synthetic(record):
    marker = _identity(record).get("is_synthetic")
    if isinstance(marker, str):
        return marker.strip().lower() in {"1", "true", "yes", "synthetic"}
    return bool(marker)


def _sanitize_legacy_display_text(value):
    if isinstance(value, dict):
        if value.get("schema_version") == "signal_llm_review@2.0.0":
            return value
        for key, item in list(value.items()):
            value[key] = _sanitize_legacy_display_text(item)
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _sanitize_legacy_display_text(item)
        return value
    if isinstance(value, str):
        return LEGACY_CONFIDENCE_REMINDER_RE.sub(r"\1", value)
    return value


def _backfill_session_context(record):
    if not isinstance(record, dict):
        return record
    signal_window = record.get("signal_window")
    if not isinstance(signal_window, dict):
        return record
    ctx = signal_window.get("session_context")
    if not isinstance(ctx, dict) or not ctx:
        return record
    code = str(ctx.get("rationale_code") or "").strip()
    template = SESSION_PREMISE_CONTEXTS.get(code)
    if not template:
        return record
    needs_backfill = (
        ctx.get("schema_name") != "SignalSessionPremiseDurabilityContext"
        or ctx.get("clock_window") in (None, "")
        or ctx.get("backtest_delta_pp") in (None, "")
        or not isinstance(ctx.get("validation_basis"), dict)
    )
    if not needs_backfill:
        _ensure_decision_matrix_temporal(record, ctx)
        return record

    original_schema = ctx.get("schema") or ctx.get("schema_name")
    preserved = {
        "dst_mode": ctx.get("dst_mode"),
        "london_dst_mode": ctx.get("london_dst_mode"),
        "utc8_time": ctx.get("utc8_time"),
        "is_weekend": ctx.get("is_weekend"),
        "weekend_adjustment": ctx.get("weekend_adjustment"),
        "event_blackout": ctx.get("event_blackout"),
        "affects_confidence": ctx.get("affects_confidence"),
        "affects_blocking": ctx.get("affects_blocking"),
        "affects_trade_allowed": ctx.get("affects_trade_allowed"),
        "transition": ctx.get("transition"),
    }
    ctx.clear()
    ctx.update(template)
    ctx.update({
        "schema": "SignalSessionPremiseDurabilityContext@1.0.0",
        "schema_name": "SignalSessionPremiseDurabilityContext",
        "schema_version": "1.0.0",
        "rationale_code": code,
        "boundary_buffer_min": 0,
        "buffer_policy": "DIRECT_UTC8_SUMMER_BUCKET_MAPPING",
        "calibration_state": "MARKET_PRIOR_VALIDATED_NOT_SIGNAL_CALIBRATED",
        "confidence_policy": "DO_NOT_MULTIPLY_CONFIDENCE",
        "confidence_multiplier": 1.0,
        "validation_basis": dict(SESSION_VALIDATION_BASIS),
        "compat_backfill_applied": True,
        "compat_backfill_source": "materializer_session_context_v1",
        "compat_source_schema": original_schema,
    })
    for key, value in preserved.items():
        if value not in (None, ""):
            ctx[key] = value
    if not isinstance(ctx.get("event_blackout"), dict):
        ctx["event_blackout"] = {"active": False}
    if not isinstance(ctx.get("weekend_adjustment"), dict):
        ctx["weekend_adjustment"] = {"applied": False}
    if not isinstance(ctx.get("transition"), dict):
        ctx["transition"] = {
            "active": False,
            "boundary": str(template.get("clock_window", "")).split("-")[0] or None,
            "minutes_from_boundary": None,
            "policy": "DISPLAY_ONLY_NO_CONFIDENCE_CHANGE",
        }
    for key in ("affects_confidence", "affects_blocking", "affects_trade_allowed"):
        ctx[key] = False
    _ensure_decision_matrix_temporal(record, ctx)
    return record


def _ensure_decision_matrix_temporal(record, ctx):
    matrix = record.get("decision_matrix")
    if not isinstance(matrix, dict):
        matrix = {"schema_name": "SignalDecisionMatrix"}
        record["decision_matrix"] = matrix
    matrix["temporal_durability"] = ctx.get("premise_durability")
    if not matrix.get("window"):
        matrix["window"] = "CONFIRMED"
    if not matrix.get("audit_dissent"):
        matrix["audit_dissent"] = "PENDING_LLM"
    if not matrix.get("direction"):
        matrix["direction"] = _dict(record.get("decision")).get("lean")
    return matrix


def _backfill_signal_durability(record):
    if not isinstance(record, dict):
        return record
    durability = record.get("signal_durability")
    if isinstance(durability, dict) and durability:
        missing = _signal_durability_native_missing_fields(durability)
        if missing:
            durability.setdefault("compat_backfill_applied", True)
            durability.setdefault(
                "compat_backfill_source",
                "materializer_signal_durability_partial_v1")
            durability.setdefault("compat_source_fields", ["signal_durability"])
            durability["compat_missing_native_fields"] = missing
        _ensure_signal_durability_defaults(durability)
        return record
    if durability not in (None, {}, ""):
        return record

    aliases = {}
    for key in (
            "comfort_window",
            "price_anchor_durability",
            "temporal_session",
            "session_context",
            "durability_layer_scores",
            "durability_reason_codes",
            "durability_data_gaps"):
        value = record.get(key)
        if value not in (None, "", [], {}):
            aliases[key] = value
    signal_window = record.get("signal_window")
    if isinstance(signal_window, dict):
        ctx = signal_window.get("session_context")
        if isinstance(ctx, dict) and ctx:
            aliases.setdefault("session_context", ctx)

    if not any(key in aliases for key in (
            "comfort_window", "price_anchor_durability")):
        return record

    wrapper = {
        "schema_name": "SignalDurabilityLayer",
        "schema_version": "nrd.signal.durability_layer.v1",
        "audit_scope": "AUDIT_ONLY",
        "score_semantics": "STRUCTURE_HEALTH_INDEX_NOT_PROBABILITY",
        "confidence_policy": "DO_NOT_MULTIPLY_CONFIDENCE",
        "policy": _signal_durability_policy(),
        "compat_backfill_applied": True,
        "compat_backfill_source": "materializer_signal_durability_alias_v1",
        "compat_source_fields": sorted(aliases),
    }
    field_map = {
        "comfort_window": "comfort_window",
        "price_anchor_durability": "price_anchor_durability",
        "temporal_session": "temporal_session",
        "session_context": "session_context",
        "durability_layer_scores": "layer_scores",
        "durability_reason_codes": "reason_codes",
        "durability_data_gaps": "data_gaps",
    }
    for source_key, target_key in field_map.items():
        if source_key in aliases:
            wrapper[target_key] = aliases[source_key]
    anchor = wrapper.get("price_anchor_durability")
    if isinstance(anchor, dict):
        _ensure_price_anchor_durability_defaults(anchor)
        score = _first_present(
            anchor.get("durability_score"), anchor.get("headline_score"),
            anchor.get("score"))
        state = _first_present(
            anchor.get("durability_state"), anchor.get("headline_state"),
            anchor.get("state"))
        if score not in (None, ""):
            wrapper["headline_score"] = score
            wrapper["score"] = score
        if state not in (None, ""):
            wrapper["headline_state"] = state
            wrapper["state"] = state
        if isinstance(anchor.get("layer_scores"), dict):
            wrapper["layer_scores"] = anchor.get("layer_scores")
    record["signal_durability"] = wrapper
    return record


def _first_present(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _signal_durability_native_missing_fields(durability):
    missing = []
    if durability.get("schema_name") != "SignalDurabilityLayer":
        missing.append("schema_name")
    if durability.get("schema_version") != "nrd.signal.durability_layer.v1":
        missing.append("schema_version")
    if durability.get("audit_scope") != "AUDIT_ONLY":
        missing.append("audit_scope")
    if durability.get("headline_score") in (None, ""):
        missing.append("headline_score")
    if durability.get("headline_state") in (None, ""):
        missing.append("headline_state")
    comfort = durability.get("comfort_window")
    if not isinstance(comfort, dict) or comfort.get("tag") in (None, ""):
        missing.append("comfort_window.tag")
    anchor = durability.get("price_anchor_durability")
    if not isinstance(anchor, dict):
        missing.append("price_anchor_durability")
        return missing
    if anchor.get("schema_name") != "SignalPriceAnchorDurability":
        missing.append("price_anchor_durability.schema_name")
    if anchor.get("durability_score") in (None, ""):
        missing.append("price_anchor_durability.durability_score")
    if anchor.get("durability_state") in (None, ""):
        missing.append("price_anchor_durability.durability_state")
    if not isinstance(anchor.get("layer_scores"), dict):
        missing.append("price_anchor_durability.layer_scores")
    return missing


def _ensure_signal_durability_defaults(durability):
    durability.setdefault("schema_name", "SignalDurabilityLayer")
    durability.setdefault("schema_version", "nrd.signal.durability_layer.v1")
    durability.setdefault("audit_scope", "AUDIT_ONLY")
    durability.setdefault("score_semantics",
                          "STRUCTURE_HEALTH_INDEX_NOT_PROBABILITY")
    durability.setdefault("confidence_policy", "DO_NOT_MULTIPLY_CONFIDENCE")
    durability.setdefault("policy", _signal_durability_policy())
    anchor = durability.get("price_anchor_durability")
    if isinstance(anchor, dict):
        _ensure_price_anchor_durability_defaults(anchor)
        score = _first_present(
            durability.get("headline_score"), anchor.get("durability_score"),
            anchor.get("headline_score"), anchor.get("score"))
        state = _first_present(
            durability.get("headline_state"), anchor.get("durability_state"),
            anchor.get("headline_state"), anchor.get("state"))
        if score not in (None, ""):
            durability.setdefault("headline_score", score)
            durability.setdefault("score", score)
        if state not in (None, ""):
            durability.setdefault("headline_state", state)
            durability.setdefault("state", state)
        if isinstance(anchor.get("layer_scores"), dict):
            durability.setdefault("layer_scores", anchor.get("layer_scores"))


def _signal_durability_policy():
    return {
        "not_direction_factor": True,
        "not_execution_gate": True,
        "not_confidence_multiplier": True,
        "display_only_for_manual_audit": True,
    }


def _ensure_price_anchor_durability_defaults(anchor):
    anchor.setdefault("schema_name", "SignalPriceAnchorDurability")
    anchor.setdefault("schema_version", "nrd.signal.price_anchor_durability.v1")
    anchor.setdefault("audit_scope", "AUDIT_ONLY")
    anchor.setdefault("score_method", "LAYERED_HEURISTIC_V1")
    anchor.setdefault("score_semantics",
                      "STRUCTURE_HEALTH_INDEX_NOT_PROBABILITY")
    anchor.setdefault("score_calibration", "HEURISTIC_UNCALIBRATED")
    anchor.setdefault("policy", _signal_durability_policy())
    score = _first_present(anchor.get("durability_score"),
                           anchor.get("headline_score"), anchor.get("score"))
    state = _first_present(anchor.get("durability_state"),
                           anchor.get("headline_state"), anchor.get("state"))
    if score not in (None, ""):
        anchor.setdefault("durability_score", score)
        anchor.setdefault("headline_score", score)
        anchor.setdefault("score", score)
    if state not in (None, ""):
        anchor.setdefault("durability_state", state)
        anchor.setdefault("headline_state", state)
        anchor.setdefault("state", state)


def _enrich_auxiliary_evidence(record):
    if not isinstance(record, dict):
        return record
    reasoning = record.get("reasoning")
    if not isinstance(reasoning, dict):
        return record
    rows = reasoning.get("evidence")
    if not isinstance(rows, list):
        return record
    cross = record.get("factor_cross_section")
    if not isinstance(cross, dict):
        cross = {}
    for row in rows:
        if isinstance(row, dict):
            _enrich_auxiliary_evidence_row(row, cross)
    return record


def _enrich_auxiliary_evidence_row(row, cross):
    key = str(row.get("key") or "").upper()
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    factor = _factor_for_evidence(key, cross)
    if key == "FUNDING":
        semantics = _dict(factor.get("canonical_funding_semantics"))
        if validate_funding_semantics(semantics):
            row["canonical_funding_semantics"] = semantics
            detail["canonical_funding_semantics"] = semantics
            row["detail"] = detail
    raw_values = _auxiliary_raw_values(key, detail, factor)
    if raw_values:
        existing = row.get("raw_values")
        if isinstance(existing, dict):
            for name, value in raw_values.items():
                if existing.get(name) in (None, ""):
                    existing[name] = value
        else:
            row["raw_values"] = raw_values
    role = _auxiliary_role(key)
    if role and not row.get("auxiliary_role"):
        row["auxiliary_role"] = role
    lean = _auxiliary_lean(key, row, detail, factor)
    if lean and not row.get("auxiliary_lean"):
        row["auxiliary_lean"] = lean
    return row


def _factor_for_evidence(key, cross):
    if key == "FUNDING":
        factor = dict(_dict(cross.get("funding")))
        tmvf = _dict(cross.get("tmvf"))
        tmvf_48h = _dict(tmvf.get("tmvf_48h"))
        funding_48h = _dict(tmvf_48h.get("funding"))
        for name, value in funding_48h.items():
            if factor.get(name) in (None, ""):
                factor[name] = value
        if factor.get("funding_state") in (None, ""):
            factor["funding_state"] = tmvf_48h.get("funding_state")
        if factor.get("last_rate") in (None, ""):
            factor["last_rate"] = factor.get("last_funding_rate")
        if factor.get("effect") in (None, ""):
            factor["effect"] = factor.get("tmvf_funding_effect")
        return factor
    if key == "SRD":
        return _dict(cross.get("skew"))
    if key == "GGR_SPATIAL":
        return _dict(cross.get("gamma_regime"))
    if key == "TMV":
        return _dict(cross.get("tmvf"))
    if key == "FLOW_CONFIRM":
        return _dict(cross.get("micro_flow"))
    if key == "MACRO":
        return _dict(cross.get("macro_pressure"))
    if key in ("CVD_4H", "CVD_12H"):
        micro = _dict(cross.get("micro_flow"))
        return _dict(micro.get("fast_4h" if key == "CVD_4H" else "slow_12h"))
    return {}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _auxiliary_role(key):
    return {
        "FUNDING": "FUTURES_FUNDING_SEMANTICS",
        "SRD": "OPTION_SKEW_DIRECTION",
        "GGR_SPATIAL": "OPTION_GAMMA_STRUCTURE",
        "TMV": "DIRECTION_OWNER",
        "FLOW_CONFIRM": "FLOW_CONFIRMATION",
        "MACRO": "MACRO_CONTEXT",
        "CVD_4H": "FLOW_CONFIRM_COMPONENT",
        "CVD_12H": "FLOW_CONFIRM_COMPONENT",
    }.get(key)


def _auxiliary_raw_values(key, detail, factor):
    fields = {
        "FUNDING": (
            "last_rate", "last_funding_rate", "funding_norm", "funding_cum",
            "funding_count", "funding_state", "effect", "tmvf_funding_effect",
            "verdict", "hard_warning", "history_points", "rate_unit",
            "observed_at", "age_ms", "source_ref",
            "canonical_funding_semantics", "canonical_text_cn"),
        "SRD": (
            "vote", "rr_blend", "rr_25d", "delta_rr", "rr_z",
            "skew_norm_blend", "skew_slope", "term_slope", "vote_confidence",
            "target_expiry_hours", "expiry_count", "data_status",
            "data_state", "observed_at", "age_ms", "source_ref"),
        "GGR_SPATIAL": (
            "regime", "regime_strength", "confidence_multiplier", "veto",
            "veto_reason", "net_gamma_notional_usd", "net_gamma_notional",
            "flip_point", "distance_to_flip_pct", "pin_strike",
            "distance_to_pin_pct", "pin_pull_direction", "max_gamma_strike",
            "call_wall", "put_wall", "market_state", "observed_at",
            "age_ms", "source_ref"),
        "FLOW_CONFIRM": (
            "agreement", "absorption_state", "combined_vote",
            "combined_weight", "data_quality"),
        "CVD_4H": (
            "verdict", "cvd_norm", "cvd_sum", "price_return_pct",
            "strength", "strength_pctl", "data_ready", "vote", "weight"),
        "CVD_12H": (
            "verdict", "cvd_norm", "cvd_sum", "price_return_pct",
            "strength", "strength_pctl", "data_ready", "vote", "weight"),
        "MACRO": (
            "macro_score", "score", "macro_regime", "regime", "verdict",
            "data_status", "data_confidence", "macro_data_confidence",
            "components", "component_scores", "macro_components_cn",
            "macro_shock", "legacy_blocking_flags", "blocking_flags",
            "reason_codes", "source_ref"),
    }.get(key, tuple(detail.keys()))
    raw = {}
    for field in fields:
        value = detail.get(field)
        if (value is None or value == "") and field in factor:
            value = factor.get(field)
        if value is not None and value != "":
            raw[field] = value
    if key == "GGR_SPATIAL":
        pin = _dict(factor.get("pin"))
        for source, target in (
                ("pin_strike", "pin_strike"),
                ("distance_to_pin_pct", "distance_to_pin_pct"),
                ("pin_pull_direction", "pin_pull_direction")):
            if target not in raw and pin.get(source) is not None:
                raw[target] = pin.get(source)
    if key == "FUNDING":
        if "last_rate" not in raw and raw.get("last_funding_rate") is not None:
            raw["last_rate"] = raw.get("last_funding_rate")
        if "effect" not in raw and raw.get("tmvf_funding_effect") is not None:
            raw["effect"] = raw.get("tmvf_funding_effect")
    return raw


def _auxiliary_lean(key, row, detail, factor):
    if key == "FUNDING":
        semantics = _dict(
            detail.get("canonical_funding_semantics")
            or factor.get("canonical_funding_semantics"))
        if validate_funding_semantics(semantics):
            if semantics.get("edb_vote_allowed") is not True:
                return "NEUTRAL"
            rate = _number(semantics.get("raw_funding_rate"))
            return _signed_lean(-rate if rate is not None else None)
        rate = _first_number(
            detail, factor, fields=("last_rate", "last_funding_rate"))
        return _signed_lean(-rate if rate is not None else None)
    if key == "SRD":
        return _signed_lean(_first_number(row, detail, factor, fields=("vote",)))
    if key == "GGR_SPATIAL":
        if factor.get("veto"):
            return "RISK_CONSTRAINT"
        regime = str(factor.get("regime") or detail.get("regime") or "").upper()
        if "NEGATIVE" in regime or "AMPLIFY" in regime:
            return "RISK_CONSTRAINT"
        if "POSITIVE" in regime or "PINNING" in regime:
            return "SUPPORTIVE"
        multiplier = _first_number(factor, detail,
                                   fields=("confidence_multiplier",))
        if multiplier is not None:
            if multiplier > 1.0:
                return "SUPPORTIVE"
            if multiplier < 1.0:
                return "CONSTRAINT"
        return "NEUTRAL"
    if key == "MACRO":
        vote = _first_number(row, detail, factor, fields=("vote",))
        if vote is not None:
            return _signed_lean(vote)
        score = _first_number(detail, factor, fields=("macro_score", "score"))
        return _signed_lean(-score if score is not None else None)
    return _signed_lean(_first_number(row, detail, factor, fields=("vote",)))


def _first_number(*objects, fields):
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        for field in fields:
            value = _number(obj.get(field))
            if value is not None:
                return value
    return None


def _number(value):
    if isinstance(value, bool) or value in ("", None):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _signed_lean(value):
    value = _number(value)
    if value is None:
        return None
    if value > 0:
        return "BULLISH"
    if value < 0:
        return "BEARISH"
    return "NEUTRAL"


def _sort_key(record):
    identity = _identity(record)
    ms = identity.get("confirmed_time_ms") or record.get("confirmed_time_ms")
    timestamp = _timestamp_sort_value(ms)
    if timestamp == 0.0:
        timestamp = _timestamp_sort_value(
            identity.get("confirmed_at") or record.get("created_at"))
    return (timestamp, identity.get("card_id") or "")


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


def _filename_for_card(card_id):
    safe = re.sub(r"[^A-Za-z0-9_.+@=-]+", "_", str(card_id or "").strip())
    safe = safe.strip("._")
    if not safe:
        safe = "card"
    return safe + ".json"


def _prune_stale_card_json(cards_dir, expected_card_files):
    expected = set(expected_card_files)
    for path in cards_dir.glob("*.json"):
        if path.name == "index.json" or path.name in expected:
            continue
        path.unlink()


def _write_json(path, payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    _atomic_write_text(path, text + "\n")


def _write_fallback(path, records):
    text = ("window.SIGNAL_CARD_FIXTURES = "
            + json.dumps(records, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
            + ";\n")
    _atomic_write_text(path, text)


def _atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod_public_dir(path.parent)
    encoded = text.encode("utf-8")
    try:
        if path.read_bytes() == encoded:
            os.chmod(path, 0o644)
            return
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                    dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
        os.chmod(path, 0o644)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _fsync_directory(path):
    try:
        directory_fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def _chmod_public_dir(path):
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build signal_cards/ data for the finalized signal audit page.")
    parser.add_argument("--source", default=DEFAULT_FMZ_JSONL,
                        help="Path to FMZ signal_review.jsonl.")
    parser.add_argument("--output", required=True,
                        help="Static frontend root containing index.html/app.js.")
    parser.add_argument("--max-cards", type=int, default=15,
                        help="Maximum newest cards to publish; <=0 publishes all.")
    parser.add_argument("--llm-reviews", default="",
                        help="Optional sidecar JSONL generated by signal_llm_review.py.")
    parser.add_argument("--transition-ledger", default="",
                        help="Optional private JSONL output path for materialized transition records.")
    parser.add_argument("--transition-state", default="",
                        help="Optional private JSON state output path for the transition hash chain.")
    parser.add_argument("--transition-reviews", default="",
                        help="Optional sidecar JSONL of transition LLM reviews to merge into cards.")
    parser.add_argument("--include-synthetic", action="store_true",
                        help="Include synthetic/local preview cards in the published manifest.")
    parser.add_argument("--require-valid-source-tail", action="store_true",
                        help=("Fail before writing output if the last non-empty "
                              "source line is not a JSON object with identity.card_id."))
    args = parser.parse_args(argv)
    try:
        result = materialize(args.source, args.output, args.max_cards,
                             llm_reviews=args.llm_reviews,
                             include_synthetic=args.include_synthetic,
                             transition_ledger=args.transition_ledger,
                             transition_state=args.transition_state,
                             transition_reviews=args.transition_reviews,
                             require_valid_source_tail=args.require_valid_source_tail)
    except SourceTailValidationError as exc:
        parser.exit(2, "materialize_signal_cards: ERROR: {}\n".format(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
