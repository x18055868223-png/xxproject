"""Decision reduction: every Q1/Q2 item lands in exactly one place; what is left is at
most three questions the system has no standing to answer; nothing is weighed or concluded."""
from copy import deepcopy
import json

from tools import astra_decision_reduction_v1 as reduction
from tools import astra_workbench_reading_v1 as reading
from test_astra_locked_followup import setup


def candidate(side="put", increments=("CONTEXT_ONLY", "INCONCLUSIVE", "NOVEL", "CONFIRMING_SAME_BLOCK"),
              flow="bullish", credit=0.0021, gate_open=False):
    short_k, long_k = (82_500.0, 80_500.0) if side == "put" else (84_000.0, 86_000.0)
    items = [{"id": f"context.i{n}", "evidence_id": f"eu.item_{n}", "label_cn": f"条目{n}",
              "information_increment": inc, "readings": {"combined": {"direction": flow}}}
             for n, inc in enumerate(increments)]
    return {
        "side": side,
        "review_gate": {"enabled": False, "reasons": ([] if gate_open else ["space_gate_not_eligible", "structure_gate_not_eligible"])
                        + ["fee_missing_or_incomplete"]},
        "space_gate": {"eligible": gate_open, "gap_reasons": [] if gate_open else ["SPACE_GATE_ANCHOR_NOT_VALID"]},
        "discovery": {"short_abs_delta": 0.36},
        "current_questions": {
            "research_context": {"items": items},
            "q1": {"routing_evidence_ids": ["eu.item_2", "eu.anchor_effective_flip.x"], "routing_observations_cn": ["…"]},
            "q2": {"routing_evidence_ids": ["eu.item_2"], "routing_observations_cn": ["…"]},
            "audit": {"risk": {"production_M_ref_btc": None}},
        },
        "reading_guidance": {"notes": [{"kind": k, "text_cn": "…"} for k in
                                       ("liability_direction", "compensation_gap", "risk_reference_gap")]},
        "wpem_summary": {"window_status": "INSUFFICIENT_WINDOW", "path_classification": "gap"},
        "snapshot": {
            "contract": {"side": side, "short_strike_usd": short_k, "long_strike_usd": long_k},
            "market": {"reference_price_usd": 83_400.0},
            "economics": {"visible_credit_btc": credit, "net_credit_after_entry_fee_btc": None if credit is None else credit - 0.0004,
                          "net_credit_after_entry_fee_usd_at_spot": None if credit is None else (credit - 0.0004) * 83_400},
            "fee": {"entry_fee_btc": None if credit is None else 0.0004},
            "risk": {"mu_btc": None},
            "liability": {"usd_intrinsic_cap": 2000.0, "protection_at_usd": long_k, "payout_table": [
                {"settlement_usd": long_k, "payout_btc": 2000.0 / long_k},
                {"settlement_usd": long_k / 2 if side == "put" else long_k * 2,
                 "payout_btc": 4000.0 / long_k if side == "put" else 1000.0 / long_k}]},
        },
    }


def test_every_input_lands_in_exactly_one_place():
    out = reduction.reduce(candidate())
    cov = out["coverage"]
    assert cov["inputs"] == cov["folded"] + cov["evidence"] + cov["unclassified"]
    assert cov["unclassified"] == 0 and cov["evidence"] == 1
    codes = {row["code"] for row in out["folded"]}
    assert {"rule_gate", "shown_at_number", "non_voting_context", "no_direction", "same_block_repeat",
            "in_boundary", "in_question_premise", "restates_evidence", "no_path_signal"} <= codes


def test_at_most_three_questions_in_fixed_order_and_no_tail_question_for_call():
    assert [q["id"] for q in reduction.reduce(candidate())["questions"]] == ["R1", "R2", "R3"]
    call = reduction.reduce(candidate(side="call"))
    assert [q["id"] for q in call["questions"]] == ["R1", "R2"]
    assert call["questions"][1]["tail_cn"]


def test_no_conclusion_words_and_no_llm():
    out = reduction.reduce(candidate())
    text = json.dumps(out, ensure_ascii=False)
    for word in ("值得", "不值得", "建议", "总体偏多", "总体偏空", "应该承保", "不要承保"):
        assert word not in text
    assert out["llm_call_count"] == 0 and out["execution_allowed"] is False


def test_opposite_directions_are_kept_side_by_side_not_netted():
    c = candidate(increments=("NOVEL", "COUNTER_EVIDENCE"))
    c["current_questions"]["research_context"]["items"][1]["readings"] = {"direction": "bearish"}
    r1 = reduction.reduce(c)["questions"][0]
    assert r1["conflict"] is True
    assert sorted(e["relation"] for e in r1["evidence"]) == ["adverse", "favorable"]


def test_missing_credit_is_named_and_the_block_still_renders():
    r2 = reduction.reduce(candidate(credit=None))["questions"][1]
    assert r2["numbers"]["net_credit_btc"] is None and "可见信用" in r2["numbers"]["missing_cn"]
    assert "μ_ref" in r2["premise_cn"]


def test_rule_state_follows_the_gates():
    assert reduction.reduce(candidate())["rule_state"]["status"] == "blocked"
    assert reduction.reduce(candidate(gate_open=True))["rule_state"]["status"] == "open"


def test_input_order_does_not_change_the_result():
    a = candidate(increments=("CONTEXT_ONLY", "NOVEL", "INCONCLUSIVE"))
    b = deepcopy(a)
    b["current_questions"]["research_context"]["items"].reverse()
    norm = lambda out: {**out, "folded": sorted((r["code"], sorted(r["refs"])) for r in out["folded"])}
    assert norm(reduction.reduce(a))["questions"] == norm(reduction.reduce(b))["questions"]
    assert norm(reduction.reduce(a))["folded"] == norm(reduction.reduce(b))["folded"]


def test_an_unknown_label_is_left_to_the_user_not_folded():
    c = candidate(increments=("SOMETHING_NEW",))
    c["current_questions"]["q1"]["routing_evidence_ids"] = ["eu.item_0"]
    c["current_questions"]["q2"]["routing_evidence_ids"] = ["eu.item_0"]
    out = reduction.reduce(c)
    # the item itself is shown to the user as is; its Q1/Q2 restatement need not be repeated
    assert [u["ref"] for u in out["unclassified"]] == ["eu.item_0"]
    # a Q1/Q2 line about evidence the reduction has never seen is not folded
    c["current_questions"]["q1"]["routing_evidence_ids"] = ["eu.unseen"]
    assert "q1.routing" in [u["ref"] for u in reduction.reduce(c)["unclassified"]]
    assert out["coverage"]["inputs"] == sum(out["coverage"][k] for k in ("folded", "evidence", "unclassified"))


def test_the_page_view_carries_the_reduction(tmp_path):
    wb, _, _, _ = setup(tmp_path)
    page = reading.workbench_page(wb.cached_view(reading_only=True))
    assert page["candidates"]
    for item in page["candidates"]:
        assert item["decision_reduction"]["schema"] == reduction.SCHEMA
        assert "error" not in item["decision_reduction"]


def test_review_gate_reasons_go_by_explicit_list_and_unknown_ones_stay_with_the_user():
    c = candidate()
    c["review_gate"]["reasons"] += ["manual_review_cooldown_active", "something_unforeseen"]
    out = reduction.reduce(c)
    codes = {row["code"]: row["refs"] for row in out["folded"]}
    assert "review_gate.manual_review_cooldown_active" in codes["review_service_state"]
    assert [u["code"] for u in out["unclassified"]] == ["something_unforeseen"]


def test_every_guidance_kind_has_a_place_and_no_raw_kind_is_shown():
    c = candidate()
    kinds = ("liability_direction", "liability_gap", "compensation_gap", "compensation_insufficient",
             "compensation_reference", "risk_reference_gap", "risk_reference", "edb_gap", "edb_direction_gap",
             "edb_conflict", "edb_same_source", "identity", "identity_gap")
    c["reading_guidance"]["notes"] = [{"kind": k, "text_cn": "…"} for k in kinds]
    out = reduction.reduce(c)
    for u in out["unclassified"]:
        assert u["label_cn"] and u["label_cn"] not in kinds
    assert {u["ref"] for u in out["unclassified"]} == {"reading_guidance.liability_gap", "reading_guidance.identity"}
    assert "净信用不为正：权利金差不够覆盖入场费" in out["questions"][1]["flags_cn"]


def test_counter_evidence_without_a_price_direction_is_named_as_such():
    c = candidate(increments=("COUNTER_EVIDENCE",))
    c["current_questions"]["research_context"]["items"][0]["readings"] = {"gamma_regime": "NEGATIVE"}
    ev = reduction.reduce(c)["questions"][0]["evidence"]
    assert [e["relation"] for e in ev] == ["counter_non_directional"]


def test_evidence_order_does_not_depend_on_input_order():
    a = candidate(increments=("NOVEL", "COUNTER_EVIDENCE"))
    b = deepcopy(a)
    b["current_questions"]["research_context"]["items"].reverse()
    assert reduction.reduce(a)["questions"][0]["evidence"] == reduction.reduce(b)["questions"][0]["evidence"]


def test_loss_at_protection_uses_the_payout_rows_all_fee_net():
    c = candidate()
    c["snapshot"]["liability"]["payout_table"][0]["static_net_btc"] = -0.0231
    assert reduction.reduce(c)["questions"][1]["numbers"]["loss_at_protection_btc"] == 0.0231
