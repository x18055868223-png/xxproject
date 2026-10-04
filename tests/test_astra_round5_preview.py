from copy import deepcopy
import json
import threading
from urllib.request import urlopen

from tools import astra_workbench_preview_v20 as preview


T = 1_790_323_200_000


def _receipt(card: dict, scenario_id: str, key: str, reviewed_at: int) -> dict:
    wire, normalized = preview._wire_response(card["snapshot"], scenario_id)
    snapshot = card["snapshot"]
    return {
        "schema": preview.SCHEMA,
        "status": "SIMULATED",
        "scenario_id": scenario_id,
        "candidate_id": snapshot["candidate_id"],
        "side": card["side"],
        "snapshot_id": snapshot["snapshot_id"],
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "map_id": snapshot["data_identity"]["btc_map"]["map_id"],
        "idempotency_key": key,
        "reviewed_at_ms": reviewed_at,
        "llm_call_count": 0,
        "http_calls": 0,
        "wire_response": wire,
        "review": normalized,
    }


def test_preview_rebinds_both_candidate_receipts_from_existing_idempotency() -> None:
    base = preview.build_preview(T, scenario_id="W1")
    by_side = {card["side"]: card for card in base["candidates"]}
    put_receipt = _receipt(by_side["put"], "W1", "put-once", T + 1_000)
    call_receipt = _receipt(by_side["call"], "W1", "call-once", T + 2_000)
    manual_state = {
        "inflight": None,
        "last_result": call_receipt,
        "cooldown_remaining_ms": 0,
        "daily_http_budget": None,
    }

    rebound = preview.build_preview(
        T,
        manual_review_state=manual_state,
        scenario_id="W1",
        review_receipts=[put_receipt, call_receipt],
    )
    rebound_by_side = {card["side"]: card for card in rebound["candidates"]}

    assert rebound["manual_review_state"]["last_result"]["idempotency_key"] == "call-once"
    assert "idempotency" not in rebound["manual_review_state"]
    assert rebound_by_side["put"]["review"]["status"] == "SIMULATED"
    assert rebound_by_side["put"]["review"]["reviewed_snapshot_id"] == put_receipt["snapshot_id"]
    assert rebound_by_side["put"]["review"]["reviewed_at_ms"] == put_receipt["reviewed_at_ms"]
    assert rebound_by_side["put"]["review"]["http_calls"] == 0
    assert rebound_by_side["call"]["review"]["status"] == "SIMULATED"
    assert rebound_by_side["call"]["review"]["reviewed_snapshot_id"] == call_receipt["snapshot_id"]
    assert rebound_by_side["call"]["review"]["http_calls"] == 0


def test_preview_prefers_exact_snapshot_and_map_over_newer_same_candidate_receipt() -> None:
    w1 = preview.build_preview(T, scenario_id="W1")
    w1_by_side = {card["side"]: card for card in w1["candidates"]}
    w4 = preview.build_preview(T, scenario_id="W4")
    w4_call = next(card for card in w4["candidates"] if card["side"] == "call")
    w1_put_receipt = _receipt(w1_by_side["put"], "W1", "w1-put", T + 1_000)
    w1_call_receipt = _receipt(w1_by_side["call"], "W1", "w1-call", T + 2_000)
    w4_call_receipt = _receipt(w4_call, "W4", "w4-call", T + 3_000)
    assert w4_call_receipt["candidate_id"] == w1_call_receipt["candidate_id"]
    assert w4_call_receipt["snapshot_id"] != w1_call_receipt["snapshot_id"]
    assert w4_call_receipt["map_id"] != w1_call_receipt["map_id"]

    rebound = preview.build_preview(
        T,
        manual_review_state={"inflight": None, "last_result": w4_call_receipt},
        scenario_id="W1",
        review_receipts=[w1_put_receipt, w1_call_receipt, w4_call_receipt],
    )
    rebound_by_side = {card["side"]: card for card in rebound["candidates"]}

    assert rebound_by_side["put"]["review"]["reviewed_snapshot_id"] == w1_put_receipt["snapshot_id"]
    assert rebound_by_side["put"]["review"]["reviewed_at_ms"] == w1_put_receipt["reviewed_at_ms"]
    assert rebound_by_side["call"]["review"]["reviewed_snapshot_id"] == w1_call_receipt["snapshot_id"]
    assert rebound_by_side["call"]["review"]["reviewed_at_ms"] == w1_call_receipt["reviewed_at_ms"]
    assert rebound_by_side["call"]["review"]["reviewed_at_ms"] != w4_call_receipt["reviewed_at_ms"]
    assert rebound["manual_review_state"]["last_result"]["idempotency_key"] == "w4-call"


def test_preview_refuses_invalid_exact_wire_instead_of_falling_back_to_stale_receipt() -> None:
    w1 = preview.build_preview(T, scenario_id="W1")
    w1_call = next(card for card in w1["candidates"] if card["side"] == "call")
    w4 = preview.build_preview(T, scenario_id="W4")
    w4_call = next(card for card in w4["candidates"] if card["side"] == "call")
    bad_exact = _receipt(w1_call, "W1", "bad-w1-call", T + 1_000)
    bad_exact["wire_response"] = {}
    stale = _receipt(w4_call, "W4", "w4-call", T + 2_000)

    rebound = preview.build_preview(
        T,
        manual_review_state={"inflight": None, "last_result": stale},
        scenario_id="W1",
        review_receipts=[bad_exact, stale],
    )
    call = next(card for card in rebound["candidates"] if card["side"] == "call")

    assert call["review"]["status"] == "not_attached"
    assert call["btc_map_review_delta"]["same_snapshot"] is True


def test_preview_http_view_reuses_idempotency_receipts_without_mutating_shared_state(monkeypatch) -> None:
    monkeypatch.setattr(preview, "_now_ms", lambda: T)
    server = preview.PreviewServer(0)
    base = preview.build_preview(T, scenario_id="W1")
    by_side = {card["side"]: card for card in base["candidates"]}
    put_receipt = _receipt(by_side["put"], "W1", "http-put", T + 1_000)
    call_receipt = _receipt(by_side["call"], "W1", "http-call", T + 2_000)
    with server.lock:
        server.idempotency["http-put"] = deepcopy(put_receipt)
        server.idempotency["http-call"] = deepcopy(call_receipt)
        server.review_state["last_result"] = deepcopy(call_receipt)
        before_idempotency = deepcopy(server.idempotency)
        before_review_state = deepcopy(server.review_state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/workbench?scenario=W1", timeout=3) as response:
            view = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    with server.lock:
        after_idempotency = deepcopy(server.idempotency)
        after_review_state = deepcopy(server.review_state)
    by_side = {card["side"]: card for card in view["candidates"]}

    assert by_side["put"]["review"]["status"] == "SIMULATED"
    assert by_side["put"]["review"]["reviewed_snapshot_id"] == put_receipt["snapshot_id"]
    assert by_side["call"]["review"]["status"] == "SIMULATED"
    assert by_side["call"]["review"]["reviewed_snapshot_id"] == call_receipt["snapshot_id"]
    assert "idempotency" not in view["manual_review_state"]
    assert after_idempotency == before_idempotency
    assert after_review_state == before_review_state


def test_preview_marks_old_candidate_receipt_stale_after_scenario_change() -> None:
    base = preview.build_preview(T, scenario_id="W1")
    put = next(card for card in base["candidates"] if card["side"] == "put")
    put_receipt = _receipt(put, "W1", "put-once", T + 1_000)

    changed = preview.build_preview(
        T,
        manual_review_state={
            "inflight": None,
            "last_result": put_receipt,
        },
        scenario_id="W5",
        review_receipts=[put_receipt],
    )
    same_candidate = next(
        card for card in changed["candidates"]
        if card["snapshot"]["candidate_id"] == put_receipt["candidate_id"]
    )

    assert same_candidate["review"]["status"] == "not_attached"
    assert same_candidate["btc_map_review_delta"]["same_snapshot"] is False
    assert same_candidate["btc_map_review_delta"]["differences_cn"]


def test_preview_server_keeps_candidate_expiry_on_frozen_clock_after_twelve_hours(monkeypatch) -> None:
    monkeypatch.setattr(preview, "_now_ms", lambda: T)
    server = preview.PreviewServer(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(preview, "_now_ms", lambda: T + 12 * 3_600_000)
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/workbench?scenario=W1", timeout=3) as response:
            view = json.load(response)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert view["market_context"]["age_ms"] == 12 * 3_600_000
    for card in view["candidates"]:
        frozen_remaining = card["snapshot"]["contract"]["expiry_ms"] - T
        live_remaining = max(0, card["snapshot"]["contract"]["expiry_ms"] - (T + 12 * 3_600_000))
        frozen_hours = frozen_remaining / 3_600_000
        live_hours = live_remaining / 3_600_000
        assert card["remaining_to_expiry_ms"] == frozen_remaining
        assert card["remaining_to_expiry_ms"] != live_remaining
        assert card["btc_map"]["cutoff_at_ms"] == T
        assert card["btc_map"]["source_manifest"]["cutoff_at_ms"] == T
        _, normalized = preview._wire_response(card["snapshot"], "W1")
        expected = f"{frozen_hours:.1f}小时"
        unexpected = f"{live_hours:.1f}小时"
        assert normalized["q1"]["summary_cn"].count(expected) == 1
        assert expected in normalized["q2"]["waiting_cost_cn"]
        assert unexpected not in normalized["q1"]["summary_cn"]
        assert unexpected not in normalized["q2"]["waiting_cost_cn"]
