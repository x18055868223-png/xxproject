import json

import pytest

from tools import astra_underwriting_review_v20 as review
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_v20 as core
from test_astra_underwriting_v20 import sample


class Budget:
    def __init__(self):
        self.reservations = []

    def reserve(self, count, **kwargs):
        self.reservations.append(kwargs)
        return {"reservation_id": str(len(self.reservations))}

    def complete(self, reservation_id, status_category, usage):
        pass


def _wire_answer(snapshot, *, research_only=False):
    projection = review.build_review_input(snapshot, research_only=research_only)
    catalog = projection["claim_catalog"]
    required = projection["decision_relations"]["selection_requirements"]

    def first(role, prefix=None):
        for item in catalog[role]:
            if prefix is None or item["id"].startswith(prefix):
                return item["id"]
        return catalog[role][0]["id"]

    return {
        "response_protocol": review.RESPONSE_PROTOCOL,
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "projection_hash": projection["projection_hash"],
        "claim_catalog_hash": projection["claim_catalog_hash"],
        "applicability": "UNABLE_TO_JUDGE",
        "selected_claim_ids": list(dict.fromkeys([first("claims", "claim.contract."), *required["claim_ids"]])),
        "selected_counter_ids": list(dict.fromkeys([first("counterclaims", "counter.contract."), *required["counter_ids"]])),
        "selected_gap_ids": [first("gaps")],
        "selected_reassess_condition_ids": list(dict.fromkeys([first("reassess_conditions"), *required["reassess_condition_ids"]])),
    }


def _response(payload):
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload, ensure_ascii=False)}}],
        "usage": {"total_tokens": 99},
    }


def _point(financials, point_id):
    return next(item for item in financials["points"] if item["id"] == point_id)


def test_projection_hash_survives_sorted_ledger_roundtrip(tmp_path):
    from test_astra_workbench_v20 import make_workbench, write_fmz, native_gate, T
    wb, snapshots = make_workbench(tmp_path)
    write_fmz(snapshots, native_gate())
    snapshot = wb.refresh(now_ms=T)["candidates"][0]["snapshot"]
    restored = json.loads(json.dumps(snapshot, sort_keys=True))
    assert review.build_review_input(snapshot) == review.build_review_input(restored)


def test_put_tail_short_leg_and_breakeven_are_program_accounting():
    snapshot = core.evaluate(sample("put"))
    financials = review.build_review_input(snapshot)["deterministic_financials"]

    assert financials["btc_tail_profile"] == "unbounded_btc_tail_below_protection"
    assert financials["btc_contract_cap"] is None
    assert _point(financials, "short_strike")["payout_btc"] == pytest.approx(0.0)
    assert _point(financials, "short_strike")["static_net_btc"] == pytest.approx(0.0003625)
    assert _point(financials, "breakeven_1")["settlement_usd"] == pytest.approx(96649.64509, rel=1e-7)
    assert _point(financials, "deep_tail")["settlement_usd"] == pytest.approx(1000.0)
    assert _point(financials, "deep_tail")["payout_btc"] == pytest.approx(0.2)
    assert _point(financials, "deep_tail")["static_net_btc"] == pytest.approx(-0.1996375)


def test_call_protection_boundary_is_btc_peak_not_constant_width_cap():
    snapshot = core.evaluate(sample("call"))
    financials = review.build_review_input(snapshot)["deterministic_financials"]
    long_point = _point(financials, "long_strike")
    high_tail = _point(financials, "high_tail")

    assert financials["btc_tail_profile"] == "bounded_btc_payout_peaks_near_protection"
    assert financials["btc_contract_cap"] == pytest.approx(long_point["payout_btc"])
    assert high_tail["settlement_usd"] > long_point["settlement_usd"]
    assert high_tail["payout_btc"] < long_point["payout_btc"]


def test_wire_response_rejects_free_text_unknown_duplicates_and_wrong_role():
    snapshot = core.evaluate(sample("put"))

    free_text = _wire_answer(snapshot)
    free_text["summary_cn"] = "这里试图新增自由金融文本。"
    with pytest.raises(review.ReviewValidationError, match="frozen schema"):
        review.validate_wire_response(snapshot, free_text)

    unknown = _wire_answer(snapshot)
    unknown["selected_claim_ids"] = ["claim.not_in_catalog"]
    with pytest.raises(review.ReviewValidationError, match="bounded catalog"):
        review.validate_wire_response(snapshot, unknown)

    duplicate = _wire_answer(snapshot)
    duplicate["selected_claim_ids"] = duplicate["selected_claim_ids"] * 2
    with pytest.raises(review.ReviewValidationError, match="duplicates"):
        review.validate_wire_response(snapshot, duplicate)

    wrong_role = _wire_answer(snapshot)
    wrong_role["selected_counter_ids"] = wrong_role["selected_claim_ids"]
    with pytest.raises(review.ReviewValidationError, match="wrong role"):
        review.validate_wire_response(snapshot, wrong_role)


def test_old_freeform_canary_is_rejected_without_semantic_retry(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(sample())["snapshot"]
    old_freeform = {
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "applicability": "CONTESTED",
        "supports": [{"ref": "contract", "claim_cn": "最大损失接近价差宽度减补偿。"}],
        "counters": [],
        "gaps_cn": [],
        "reassess_if_cn": [],
        "summary_cn": "价格向短腿或下方移动时静态净额转负。",
    }
    calls = []

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        return _response(old_freeform)

    result = review.run_review(snapshot, ledger, tmp_path / "states", api_key="fake",
                               model="deepseek-flash", budget=Budget(),
                               transport=transport, research_only=True)

    assert result["status"] == "rejected"
    assert result["http_calls"] == 1
    assert len(calls) == 1


def test_legacy_research_state_is_not_replayed_as_current_protocol_or_recalled(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(sample())["snapshot"]
    state_root = tmp_path / "states" / "research_only"
    state_path = review._review_state_path(state_root, snapshot["candidate_id"], snapshot["snapshot_id"])
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "schema": "astra_underwriting_review_attempts@2.4.3",
        "candidate_id": snapshot["candidate_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "packet_hash": review._hash(core.llm_applicability_packet(snapshot)),
        "input_hash": "old-projection",
        "mode": "research_only",
        "attempts": [{"status": "HTTP_OK"}],
        "settled": True,
        "result": {"status": "RESEARCH_OK", "reviewed_snapshot_id": snapshot["snapshot_id"], "http_calls": 1},
    }), encoding="utf-8")
    calls = []

    result = review.run_review(snapshot, ledger, tmp_path / "states", api_key="fake",
                               model="deepseek-flash", budget=Budget(),
                               transport=lambda *args, **kwargs: calls.append(args),
                               research_only=True)

    assert result["status"] == "stale_protocol_review"
    assert result["legacy_status"] == "RESEARCH_OK"
    assert result["http_calls"] == 1
    assert calls == []


def test_legacy_attached_review_remains_readable_but_cannot_be_newly_attached(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    annex = ledger.record_payload(sample())
    snapshot = annex["snapshot"]
    old_review = {
        "schema": review.SCHEMA,
        "status": "OK",
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "candidate_id": snapshot["candidate_id"],
        "packet_hash": review._hash(core.llm_applicability_packet(snapshot)),
        "applicability": "UNABLE_TO_JUDGE",
        "supports": [],
        "counters": [],
        "gaps_cn": ["旧自由文本回执仅保留可读。"],
        "reassess_if_cn": [],
        "summary_cn": "旧协议回执不能升级为新协议语义通过。",
        "http_calls": 1,
    }
    store.append_jsonl(ledger.path, [store._event(store.REVIEW_EVENT, {
        "snapshot_id": snapshot["snapshot_id"],
        "review_hash": store._hash(old_review),
        "review": old_review,
    }, now_ms=2)])

    state = ledger.state()
    assert state["snapshots"][snapshot["snapshot_id"]]["review"]["review"] == old_review
    with pytest.raises(ValueError, match="complete v2 causal review"):
        ledger.attach_review(old_review, snapshot_id=snapshot["snapshot_id"])
