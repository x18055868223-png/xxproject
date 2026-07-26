"""Deterministic cross-layer semantic facts for signal audit consumers.

This module never computes a signal direction or execution permission.  It
only turns producer-native mechanical values into one auditable interpretation.
"""

from copy import deepcopy
import math
import re


FUNDING_CROWDING_THRESHOLD_ABS = 0.0001
GEX_RANK_OK_MIN_WINDOW_DAYS = 15.0


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _funding_pct_text(rate):
    if rate is None:
        return "-"
    return "{:+.4f}%".format(rate * 100.0)


def build_funding_semantics(
        raw_rate,
        *,
        funding_norm=None,
        effect=None,
        funding_state=None,
        source="materializer:factor_cross_section.funding.last_rate",
        compat_backfill_applied=False):
    """Build the canonical Funding interpretation from the raw decimal rate."""
    raw = _finite_number(raw_rate)
    norm = _finite_number(funding_norm)
    threshold = FUNDING_CROWDING_THRESHOLD_ABS
    result = {
        "schema_name": "FundingCanonicalSemantics",
        "schema_version": "nrd.signal.funding_semantics.v1.0.0",
        "raw_funding_rate": raw,
        "raw_funding_rate_pct": None if raw is None else raw * 100.0,
        "crowding_threshold_abs": threshold,
        "crowding_threshold_pct": threshold * 100.0,
        "raw_source": "last_funding_rate",
        "source": source,
        "compat_backfill_applied": bool(compat_backfill_applied),
        "diagnostic_only": {
            "funding_norm": norm,
            "effect": effect,
            "funding_state": funding_state,
            "norm_effect_overrides_raw": False,
        },
    }
    if raw is None:
        result.update({
            "semantic_code": "UNABLE_TO_JUDGE",
            "raw_available": False,
            "fee_bias": "UNKNOWN",
            "fee_bias_cn": "无法判定",
            "crowding_state": "UNABLE_TO_JUDGE",
            "is_crowded": False,
            "reflexivity_state": "UNABLE_TO_JUDGE",
            "reflexivity_importance": "UNABLE_TO_JUDGE",
            "edb_participation": "NON_VOTING",
            "edb_vote_allowed": False,
            "canonical_text_cn": (
                "资金费率缺失：无法判定多空费率倾向；不得由 funding_norm/effect "
                "反推拥挤或反身性；EDB 不计票。"),
        })
        return result

    if raw == 0:
        bias, bias_cn, code = "NEUTRAL", "中性", "NEUTRAL_FUNDING"
    elif raw > 0:
        bias = "LONG_FEE_BIAS"
        bias_cn = "温和多头费率倾向"
        code = "TEMPERATE_LONG_FUNDING"
    else:
        bias = "SHORT_FEE_BIAS"
        bias_cn = "温和空头费率倾向"
        code = "TEMPERATE_SHORT_FUNDING"

    if abs(raw) <= threshold:
        result.update({
            "semantic_code": code,
            "raw_available": True,
            "fee_bias": bias,
            "fee_bias_cn": bias_cn,
            "crowding_state": "NOT_CROWDED",
            "is_crowded": False,
            "reflexivity_state": "NOISE",
            "reflexivity_importance": "NOISE",
            "edb_participation": "NON_VOTING",
            "edb_vote_allowed": False,
            "canonical_text_cn": (
                "资金费率 {rate}：{bias}；未超过 ±0.0100% 拥挤阈值；"
                "反身性影响可忽略；EDB 不计票。").format(
                    rate=_funding_pct_text(raw), bias=bias_cn),
        })
        return result

    if raw > 0:
        bias, bias_cn = "LONG_FEE_BIAS", "多头费率拥挤"
        crowding, code = "CROWDED_LONGS", "CROWDED_LONG_FUNDING"
    else:
        bias, bias_cn = "SHORT_FEE_BIAS", "空头费率拥挤"
        crowding, code = "CROWDED_SHORTS", "CROWDED_SHORT_FUNDING"
    result.update({
        "semantic_code": code,
        "raw_available": True,
        "fee_bias": bias,
        "fee_bias_cn": bias_cn,
        "crowding_state": crowding,
        "is_crowded": True,
        "reflexivity_state": "CANDIDATE",
        "reflexivity_importance": "CANDIDATE",
        "edb_participation": "VOTING_CANDIDATE",
        "edb_vote_allowed": True,
        "canonical_text_cn": (
            "资金费率 {rate}：{bias}；已超过 ±0.0100% 拥挤阈值；"
            "反身性仅作为受限候选，须结合归一化历史与价格行为计票。").format(
                rate=_funding_pct_text(raw), bias=bias_cn),
    })
    return result


def _dict(value):
    return value if isinstance(value, dict) else {}


def _first_number(*values):
    for value in values:
        number = _finite_number(value)
        if number is not None:
            return number
    return None


def validate_funding_semantics(value):
    semantics = _dict(value)
    required = {
        "schema_name", "schema_version", "raw_funding_rate",
        "raw_funding_rate_pct", "crowding_threshold_abs",
        "crowding_threshold_pct", "semantic_code", "raw_available",
        "fee_bias", "fee_bias_cn", "crowding_state", "is_crowded",
        "reflexivity_importance", "edb_participation",
        "edb_vote_allowed", "canonical_text_cn", "source",
        "compat_backfill_applied", "diagnostic_only",
    }
    if not required.issubset(semantics):
        return False
    raw = _finite_number(semantics.get("raw_funding_rate"))
    if raw is None:
        return (
            semantics.get("raw_available") is False
            and semantics.get("semantic_code") == "UNABLE_TO_JUDGE"
            and semantics.get("fee_bias") == "UNKNOWN"
            and semantics.get("crowding_state") == "UNABLE_TO_JUDGE"
            and semantics.get("is_crowded") is False
            and semantics.get("reflexivity_importance") == "UNABLE_TO_JUDGE"
            and semantics.get("edb_participation") == "NON_VOTING"
            and semantics.get("edb_vote_allowed") is False
            and bool(str(semantics.get("canonical_text_cn") or "").strip())
        )
    if abs(raw) <= FUNDING_CROWDING_THRESHOLD_ABS:
        expected_code = (
            "NEUTRAL_FUNDING" if raw == 0
            else ("TEMPERATE_LONG_FUNDING"
                  if raw > 0 else "TEMPERATE_SHORT_FUNDING"))
        return (
            semantics.get("raw_available") is True
            and semantics.get("semantic_code") == expected_code
            and semantics.get("is_crowded") is False
            and semantics.get("crowding_state") == "NOT_CROWDED"
            and semantics.get("reflexivity_importance") == "NOISE"
            and semantics.get("edb_participation") == "NON_VOTING"
            and semantics.get("edb_vote_allowed") is False
            and bool(str(semantics.get("canonical_text_cn") or "").strip())
        )
    expected_code = (
        "CROWDED_LONG_FUNDING" if raw > 0 else "CROWDED_SHORT_FUNDING")
    expected_crowding = "CROWDED_LONGS" if raw > 0 else "CROWDED_SHORTS"
    return (
        semantics.get("raw_available") is True
        and semantics.get("semantic_code") == expected_code
        and semantics.get("crowding_state") == expected_crowding
        and semantics.get("is_crowded") is True
        and semantics.get("reflexivity_importance") == "CANDIDATE"
        and semantics.get("edb_participation") == "VOTING_CANDIDATE"
        and semantics.get("edb_vote_allowed") is True
        and bool(str(semantics.get("canonical_text_cn") or "").strip())
    )


def ensure_card_fact_semantics(card, *, compat_source):
    """Return a copy with canonical Funding and legacy GEX quality backfills."""
    card = deepcopy(_dict(card))
    factor = card.setdefault("factor_cross_section", {})
    funding = factor.setdefault("funding", {})
    native = _dict(funding.get("canonical_funding_semantics"))
    if validate_funding_semantics(native):
        semantics = native
    else:
        tmvf = _dict(factor.get("tmvf"))
        tmvf_48h = _dict(tmvf.get("tmvf_48h"))
        funding_48h = _dict(tmvf_48h.get("funding"))
        raw = _first_number(
            funding.get("last_rate"),
            funding.get("last_funding_rate"),
            funding_48h.get("last_funding_rate"),
            funding_48h.get("last_rate"),
        )
        semantics = build_funding_semantics(
            raw,
            funding_norm=_first_number(
                funding.get("funding_norm"), funding_48h.get("funding_norm")),
            effect=funding.get("effect") or funding.get("tmvf_funding_effect"),
            funding_state=(
                funding.get("funding_state") or tmvf_48h.get("funding_state")),
            source=compat_source,
            compat_backfill_applied=True,
        )
        funding["canonical_funding_semantics"] = semantics
        funding["canonical_text_cn"] = semantics["canonical_text_cn"]
        funding["compat_backfill_applied"] = True
        funding["compat_backfill_source"] = compat_source

    rank = _dict(_dict(factor.get("gex_info")).get("rank"))
    window = _dict(rank.get("window"))
    window_days = _finite_number(window.get("window_days"))
    metrics = _dict(rank.get("metrics"))
    if window_days is not None and window_days >= GEX_RANK_OK_MIN_WINDOW_DAYS:
        changed = False
        for metric in metrics.values():
            if (isinstance(metric, dict)
                    and str(metric.get("quality") or "").lower() == "warming_up"):
                metric["quality"] = "ok"
                changed = True
        if changed:
            rank["compat_backfill_applied"] = True
            rank["compat_backfill_source"] = (
                "legacy_rank_window_days_ge_15_to_quality_ok")
    return card


FUNDING_CONFLICT_PATTERNS = (
    re.compile(r"(多头|空头)(极度|过度|严重)?拥挤"),
    re.compile(r"极度拥挤|过度拥挤"),
    re.compile(r"反身性(风险|挤压|升温|燃料)"),
    re.compile(r"(逼空|逼多|挤压风险)"),
)


def funding_text_conflicts(text, semantics):
    if not validate_funding_semantics(semantics):
        return False
    if semantics.get("is_crowded") is True:
        return False
    joined = str(text or "")
    return any(pattern.search(joined) for pattern in FUNDING_CONFLICT_PATTERNS)


RAW_HUMAN_LEAK_RE = re.compile(
    r"(?:^|[{,'\"\s])(?:funding_state|last_rate|last_funding_rate|"
    r"funding_norm|tmvf_funding_effect|effect)\s*['\"]?\s*[:=]"
    r"|[-+]?\d+(?:\.\d+)?e[-+]?\d+",
    re.IGNORECASE,
)


def has_raw_human_leak(text):
    return bool(RAW_HUMAN_LEAK_RE.search(str(text or "")))
