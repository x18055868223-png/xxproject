# -*- coding: utf-8 -*-
"""Standalone test for the SignalEvidencePackage signal_review digest.

Run: python test_signal_bridge.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from signal_bridge import export_signal_evidence_package


def test_signal_review_digest_passthrough_and_narrow():
    snapshot = {
        "edb": {"edb_score": -0.71, "support_label": "NO_TRADE_BLOCKED"},
        "signal_review": {
            "card_id": "d67f", "lean": "NEUTRAL",
            "support_label": "NO_TRADE_BLOCKED", "side_hint": "none",
            "confidence": 0, "conflict_ratio": 0.04, "conflict_level": "NONE",
            "has_block": True, "block_kind": "HARD_VETO",
            "veto_reason": "MACRO_BLOCKING",
        },
        # execution-only / heavy fields must never leak through the allow-list
        "short_strike": 60000,
        "order_intent": [{"leg": 1}],
        "ledger": {"y": 2},
        "factor_cross_section": {"macro": "huge-blob"},
    }
    pkg = export_signal_evidence_package(
        snapshot, "pkg-1", now_ts=1000, ttl_sec=90)

    review = pkg["signal_review"]
    assert review["card_id"] == "d67f", review
    assert review["veto_reason"] == "MACRO_BLOCKING", review
    assert review["has_block"] is True, review
    assert "factor_cross_section" not in review, review

    blob = repr(pkg)
    for forbidden in ("short_strike", "order_intent", "ledger",
                      "factor_cross_section"):
        assert forbidden not in blob, forbidden
    print("test_signal_bridge: PASS")


if __name__ == "__main__":
    test_signal_review_digest_passthrough_and_narrow()
