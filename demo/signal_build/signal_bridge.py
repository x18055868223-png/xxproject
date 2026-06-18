# -*- coding: utf-8 -*-
"""SignalEvidence export bridge for demo v0.2.

This module is intentionally narrow: it turns an existing signal snapshot into
the only package the execution FMZ may consume. It does not expose execution
fields such as strikes, orders, ledger state, sizing, or approval data.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


def _copy_mapping(source: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = source.get(key)
    if isinstance(value, dict):
        return deepcopy(value)
    return {}


def export_signal_evidence_package(
    snapshot: Dict[str, Any],
    package_id: str,
    now_ts: int,
    ttl_sec: int = 90,
) -> Dict[str, Any]:
    """Build the signal-to-execution contract package.

    The package is deliberately assembled from an allow-list. Execution-only
    fields in the input snapshot are ignored even if present.
    """
    neutral_repair = _copy_mapping(snapshot, "neutral_repair")
    anchor = _copy_mapping(snapshot, "anchor")
    edb = _copy_mapping(snapshot, "edb")
    recommendation = _copy_mapping(snapshot, "recommendation")
    data_quality = _copy_mapping(snapshot, "data_quality")
    reject_state = _copy_mapping(snapshot, "reject_state")
    # Signal Review Card DIGEST only (card_id + conclusion + conflict/block
    # summary). The full factor cross-section stays in signal_review.jsonl; the
    # contract package stays narrow and clean.
    signal_review = _copy_mapping(snapshot, "signal_review")

    return {
        "schema_name": "SignalEvidencePackage",
        "schema_version": "nrd.integration.signal.v0.1",
        "package_id": package_id,
        "created_ts": now_ts,
        "expires_ts": now_ts + ttl_sec,
        "timing_window": {
            "neutral_repair": neutral_repair,
            "anchor": anchor,
        },
        "direction_evidence": {
            "edb": edb,
        },
        "signal_review": signal_review,
        "strategy_recommendation": {
            "expiry_hours": recommendation.get("expiry_hours"),
            "side_hint": recommendation.get("side_hint"),
        },
        "pre_trade_context": {
            "ggr": _copy_mapping(snapshot, "ggr"),
            "macro": _copy_mapping(snapshot, "macro"),
            "funding": _copy_mapping(snapshot, "funding"),
        },
        "data_quality": data_quality,
        "reject_state": reject_state,
    }

