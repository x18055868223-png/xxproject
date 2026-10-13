"""Admission reads net gamma only; the flip anchor is information (2026-10-13)."""
from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_v20 as core


def gate(state, codes, net_ok):
    return {"state": state, "reason_codes": codes, "anchor": {"ok": False, "score": 20.0},
            "net_gamma": {"ok": net_ok}}


def test_anchor_codes_alone_do_not_block():
    rule = core.structure_gate(gate("STRUCTURE_BLOCKED", ["SPACE_GATE_ANCHOR_SCORE_LOW", "SPACE_GATE_ANCHOR_NOT_VALID"], True))
    assert rule["eligible"] is True and rule["blocking_codes"] == []
    assert rule["anchor_codes"] == ["SPACE_GATE_ANCHOR_NOT_VALID", "SPACE_GATE_ANCHOR_SCORE_LOW"]


def test_net_gamma_still_blocks_and_missing_gate_blocks():
    rule = core.structure_gate(gate("STRUCTURE_BLOCKED", ["SPACE_GATE_NET_GAMMA_NOT_POSITIVE", "SPACE_GATE_ANCHOR_SCORE_LOW"], False))
    assert rule["eligible"] is False and rule["blocking_codes"] == ["SPACE_GATE_NET_GAMMA_NOT_POSITIVE"]
    assert core.structure_gate(gate("STRUCTURE_BLOCKED", [], False))["blocking_codes"] == ["SPACE_GATE_NET_GAMMA_NOT_OK"]
    assert core.structure_gate(None)["eligible"] is False


def test_the_review_catalog_uses_the_same_rule():
    snap = lambda g: {"fact_context": {"market_facts": {"space_gate": g}}}
    assert review._opening_blocked(snap(gate("STRUCTURE_BLOCKED", ["SPACE_GATE_ANCHOR_SCORE_LOW"], True))) is False
    assert review._opening_blocked(snap(gate("STRUCTURE_BLOCKED", ["SPACE_GATE_GGR_TRANSITION"], False))) is True
    assert review._opening_blocked(snap({})) is False
