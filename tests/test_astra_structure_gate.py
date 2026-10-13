"""The space structure is evaluated, never gated (2026-10-13)."""
from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_v20 as core


def gate(state, codes, net_ok):
    return {"state": state, "reason_codes": codes, "anchor": {"ok": False, "score": 20.0},
            "net_gamma": {"ok": net_ok}}


def test_anchor_codes_alone_do_not_block():
    rule = core.structure_gate(gate("STRUCTURE_BLOCKED", ["SPACE_GATE_ANCHOR_SCORE_LOW", "SPACE_GATE_ANCHOR_NOT_VALID"], True))
    assert rule["eligible"] is True and rule["blocking_codes"] == []
    assert rule["anchor_codes"] == ["SPACE_GATE_ANCHOR_NOT_VALID", "SPACE_GATE_ANCHOR_SCORE_LOW"]


def test_nothing_in_the_space_structure_blocks_and_its_codes_stay_visible():
    rule = core.structure_gate(gate("STRUCTURE_BLOCKED", ["SPACE_GATE_GGR_TRANSITION", "SPACE_GATE_ANCHOR_SCORE_LOW"], False))
    assert rule["eligible"] is True and rule["blocking_codes"] == []
    assert rule["observed_codes"] == ["SPACE_GATE_ANCHOR_SCORE_LOW", "SPACE_GATE_GGR_TRANSITION"]
    missing = core.structure_gate(None)
    assert missing["eligible"] is True and missing["producer_present"] is False


def test_the_review_catalog_uses_the_same_rule():
    snap = lambda g: {"fact_context": {"market_facts": {"space_gate": g}}}
    assert review._opening_blocked(snap(gate("STRUCTURE_BLOCKED", ["SPACE_GATE_ANCHOR_SCORE_LOW"], True))) is False
    assert review._opening_blocked(snap(gate("STRUCTURE_BLOCKED", ["SPACE_GATE_GGR_TRANSITION"], False))) is False
    assert review._opening_blocked(snap({})) is False
