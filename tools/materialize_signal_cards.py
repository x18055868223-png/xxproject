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
import json
import os
from pathlib import Path
import re
import tempfile


DEFAULT_FMZ_JSONL = "/home/bitnami/fmz2/logs/storage/668422/demo/logs/signal_review.jsonl"
MANIFEST_SCHEMA = {
    "name": "signal_cards_manifest",
    "version": "1.0.0",
    "card_schema": "signal_review_card@1.0.0",
}
TRANSITION_SCHEMA_VERSION = "signal_transition_record@1.0.0"
TRANSITION_COMPUTATION_VERSION = "signal_transition_materializer@1.0.0"
TRANSITION_FIELD_REGISTRY_VERSION = "TRANSITION_FIELD_REGISTRY@1.0.0"
TRANSITION_REVIEW_SCHEMA_VERSION = "signal_transition_llm_review@1.0.0"
MATERIALITY_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
TRANSITION_FIELD_REGISTRY = (
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
        "path": "factor_cross_section.skew.vote",
        "domain": "SKEW",
        "type": "categorical",
        "role": "CONTEXT",
        "meaning": "SKEW_VOTE_CHANGE",
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
        "path": "quality.overall",
        "domain": "QUALITY",
        "type": "categorical",
        "role": "QUALITY",
        "meaning": "DATA_QUALITY_CHANGE",
    },
)
EVIDENCE_KEY_BY_DOMAIN = {
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


def materialize(source, output, max_cards=200, llm_reviews=None,
                include_synthetic=False, transition_ledger=None,
                transition_state=None, transition_reviews=None):
    source = Path(source)
    output = Path(output)
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
                if (_review_status(existing) == "OK"
                        and _review_status(review) != "OK"):
                    continue
                record["llm_review"] = review
                merged_review_count += 1
    synthetic_count = sum(1 for record in records if _is_synthetic(record))
    if not include_synthetic:
        records = [record for record in records if not _is_synthetic(record)]
    records = sorted(records, key=_sort_key, reverse=True)
    if max_cards and max_cards > 0:
        records = records[:max_cards]

    for record in records:
        _backfill_session_context(record)
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
        _write_json(cards_dir / filename, record)
        manifest_cards.append({
            "card_id": card_id,
            "confirmed_at": identity.get("confirmed_at") or record.get("created_at"),
            "symbol": identity.get("symbol") or record.get("symbol"),
            "quality": _quality(record),
            "path": rel_path,
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
            reviews[card_id] = review
    return reviews


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
        "elapsed_ms": elapsed_ms,
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


def _field_snapshot(record, entry):
    value, source = _field_value(record, entry)
    row = _evidence_row(record, entry.get("domain"))
    return {
        "value": value,
        "source": source,
        "role": _evidence_role(row, entry),
        "source_ref": row.get("source_ref") if isinstance(row, dict) else None,
    }


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
        sign_flip = bool(prev_num * curr_num < 0)
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
        rows.append({
            "card_id": identity.get("card_id") or record.get("card_id"),
            "confirmed_time_ms": _event_time_ms(record),
            "episode_id": identity.get("episode_id"),
            "lean": decision.get("lean"),
            "support_label": decision.get("support_label"),
            "macro_score": _number(_get_path(record, "factor_cross_section.macro_pressure.macro_score")),
            "funding_last_rate": _number(_get_path(record, "factor_cross_section.funding.last_rate")),
            "gamma_regime": _get_path(record, "factor_cross_section.gamma_regime.regime"),
        })
    return rows


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


def _is_synthetic(record):
    marker = _identity(record).get("is_synthetic")
    if isinstance(marker, str):
        return marker.strip().lower() in {"1", "true", "yes", "synthetic"}
    return bool(marker)


def _sanitize_legacy_display_text(value):
    if isinstance(value, dict):
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


def _auxiliary_role(key):
    return {
        "FUNDING": "FUTURES_FUNDING_CROWDING",
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
            "observed_at", "age_ms", "source_ref"),
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
            "source_ref"),
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
        rate = _first_number(detail, factor,
                             fields=("funding_norm", "last_rate",
                                     "last_funding_rate"))
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
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                    dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
        os.chmod(path, 0o644)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


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
    parser.add_argument("--max-cards", type=int, default=200,
                        help="Maximum newest cards to publish; <=0 publishes all.")
    parser.add_argument("--llm-reviews", default="",
                        help="Optional sidecar JSONL generated by gemini_signal_llm_review.py.")
    parser.add_argument("--transition-ledger", default="",
                        help="Optional private JSONL output path for materialized transition records.")
    parser.add_argument("--transition-state", default="",
                        help="Optional private JSON state output path for the transition hash chain.")
    parser.add_argument("--transition-reviews", default="",
                        help="Optional sidecar JSONL of transition LLM reviews to merge into cards.")
    parser.add_argument("--include-synthetic", action="store_true",
                        help="Include synthetic/local preview cards in the published manifest.")
    args = parser.parse_args(argv)
    result = materialize(args.source, args.output, args.max_cards,
                         llm_reviews=args.llm_reviews,
                         include_synthetic=args.include_synthetic,
                         transition_ledger=args.transition_ledger,
                         transition_state=args.transition_state,
                         transition_reviews=args.transition_reviews)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
