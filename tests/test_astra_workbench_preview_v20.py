"""The local normal-state preview must never become a live underwriting path."""

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tools import astra_workbench_preview_v20 as preview
from tools.astra_commercial_math_v17 import inverse_spread_payout


def test_preview_conversion_provenance_remains_explicitly_synthetic():
    view = preview.build_preview(1790423202147)
    assert view["preview"]["kind"] == "synthetic_fixture"
    for mapping in [view["btc_map"]] + [item["btc_map"] for item in view["candidates"]]:
        assert mapping["presentation"]["mode"] == "FIXTURE"
        records = mapping["source_manifest"]["source_records"]
        assert records and all(record["collector"]["synthetic_fixture"] is True for record in records)
        assert all(record["usage_qualification"]["simulation_only"] is True for record in records)
        allocation = next(row for row in mapping["presentation"]["background_rows"]
                          if row["key"] == "allocation")
        metrics = allocation["primary_metrics"]
        folded = [item for item in metrics if "名义折合" in item.get("label", "")]
        assert folded and all(item["conversion_qualified"] is False for item in folded)
        assert all("不能折算" in item["value"] for item in folded)
        cost = next(ref for ref in mapping["reference_pool"] if ref["role"] == "CP_STH")
        assert cost["market"] == "BITVIEW"
        assert cost["price_basis"] == "ONCHAIN_COST_CP_STH"
        assert cost["coordinate_qualification"] == "NOMINAL_ONLY"
    assert view["llm_call_count"] == 0
    assert view["preview"]["trading_permission"] is False


def test_fixture_is_explicitly_synthetic_and_internally_consistent():
    view = preview.build_preview(1_790_323_200_000)
    assert view["preview"]["kind"] == "synthetic_fixture"
    assert view["preview"]["scenario_id"] == "W1"
    assert {item["id"] for item in view["preview"]["scenarios"]} >= {"W1", "W2", "W3", "W4", "W5", "MISSING_MU"}
    assert view["preview"]["llm_http_calls"] == 0
    assert view["preview"]["trading_permission"] is False
    assert view["market_context"]["status"] == "synthetic_fixture"
    assert view["space_gate"]["source"] == "simulated_space_gate"
    assert {card["side"] for card in view["candidates"]} == {"put", "call"}
    assert view["btc_map"]["policy_link"]["candidate_id"] is None
    assert len(view["btc_map"]["display_reference_ids"]) == 6
    assert len(view["btc_map"]["reference_pool"]) == 7
    later = preview.build_preview(1_790_323_201_000)
    assert later["candidates"][0]["snapshot"]["snapshot_id"] != view["candidates"][0]["snapshot"]["snapshot_id"]
    w3 = preview.build_preview(1_790_323_200_000, scenario_id="W3")
    w5 = preview.build_preview(1_790_323_200_000, scenario_id="W5")
    missing = preview.build_preview(1_790_323_200_000, scenario_id="MISSING_MU")
    assert w3["candidates"][0]["snapshot"]["status"] == "reference_insufficient"
    assert w3["candidates"][0]["snapshot"]["economics"]["reference_margin_btc"] < 0
    assert w5["space_gate"]["state"] == "STRUCTURE_BLOCKED"
    assert w5["candidates"][0]["review_gate"]["enabled"] is True
    assert w5["candidates"][0]["review_gate"]["production_attach_allowed"] is False
    assert missing["candidates"][0]["snapshot"]["status"] == "information_gap"
    assert missing["candidates"][0]["snapshot"]["economics"]["mu_ref_btc"] is None
    for card in view["candidates"]:
        snap = card["snapshot"]
        assert card["btc_map"]["policy_link"]["candidate_id"] == snap["candidate_id"]
        assert card["btc_map"]["policy_link"]["valuation_version"] != snap["snapshot_id"]
        assert snap["data_identity"]["btc_map"]["map_id"] == card["btc_map"]["map_id"]
        assert snap["data_identity"]["kind"] == "synthetic_fixture"
        assert snap["contract"]["quantity_btc"] == 0.1
        assert snap["contract"]["width_usd"] == 2000
        econ = snap["economics"]
        assert abs(econ["premium_spread_btc"] - econ["short_premium_btc"]
                   + econ["long_premium_btc"]) < 1e-12
        assert abs(econ["net_credit_after_entry_fee_btc"]
                   - econ["premium_spread_btc"] + econ["theoretical_two_leg_fee_btc"]) < 1e-12
        for be in snap["liability"]["breakeven_usd"]:
            payout = inverse_spread_payout(snap["contract"]["side"], snap["contract"]["short_strike_usd"],
                snap["contract"]["long_strike_usd"], be, snap["contract"]["quantity_btc"])
            assert abs(econ["premium_spread_btc"] - snap["fee"]["amount_btc"] - payout) < 1e-12


def test_preview_wire_stub_uses_required_bounded_ids():
    for scenario_id, expected in {
        "W3": {
            "counter": "counter.compensation.nonpositive_margin",
            "reassess": "reassess.compensation_reprice",
        },
        "MISSING_MU": {
            "gap": "gap.compensation.mu_reference_missing",
            "reassess": "reassess.risk_reference_available",
        },
    }.items():
        view = preview.build_preview(1_790_323_200_000, scenario_id=scenario_id)
        wire, normalized = preview._wire_response(view["candidates"][0]["snapshot"], scenario_id)

        if "counter" in expected:
            assert expected["counter"] in wire["selected_counter_ids"]
        if "gap" in expected:
            assert expected["gap"] in wire["selected_gap_ids"]
        assert expected["reassess"] in wire["selected_reassess_condition_ids"]
        assert normalized["status"] == "OK"
        assert normalized["http_calls"] == 0


def test_preview_scenarios_do_not_preseed_q2_answer_text():
    forbidden = ("q2_cn", "等待机会成本", "小于负 M", "RISK_OVER_CREDIT", "THIN_CREDIT")
    assert {item["flow"] for item in preview.SCENARIOS.values()} <= {"UP", "DOWN", "NEUTRAL"}
    for scenario_id in preview.SCENARIOS:
        raw = json.dumps({
            "scenario": preview.SCENARIOS[scenario_id],
            "context": preview._context_for_scenario(1_790_323_200_000, scenario_id),
        }, ensure_ascii=False)
        for text in forbidden:
            assert text not in raw


def test_loopback_preview_receipt_is_simulated_and_never_needs_token(monkeypatch):
    monkeypatch.setattr(preview.time, "sleep", lambda seconds: None)
    server = preview.PreviewServer(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(root + "/api/workbench?scenario=W2", timeout=3) as response:
            view = json.load(response)
        assert view["preview"]["kind"] == "synthetic_fixture"
        assert view["preview"]["scenario_id"] == "W2"
        candidate_id = view["candidates"][0]["snapshot"]["candidate_id"]
        frozen_snapshot_id = view["candidates"][0]["snapshot"]["snapshot_id"]
        frozen_map_id = view["candidates"][0]["snapshot"]["data_identity"]["btc_map"]["map_id"]
        with urlopen(root + "/api/workbench?scenario=W2", timeout=3) as response:
            refreshed = json.load(response)
        assert refreshed["candidates"][0]["snapshot"]["snapshot_id"] == frozen_snapshot_id
        request = Request(root + "/api/workbench/review",
                          data=json.dumps({"candidate_id": candidate_id,
                                           "scenario": "W2",
                                           "snapshot_id": frozen_snapshot_id,
                                           "idempotency_key": "demo-once"}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=3) as response:
            result = json.load(response)
        assert result["status"] == "SIMULATED"
        assert result["side"] == view["candidates"][0]["side"]
        assert result["snapshot_id"] == frozen_snapshot_id
        assert result["map_id"] == frozen_map_id
        assert result["wire_response"]["reviewed_snapshot_id"] == frozen_snapshot_id
        assert result["llm_call_count"] == 0
        assert result["http_calls"] == 0
        assert result["review"]["status"] == "OK"
        assert result["review"]["supports"] and result["review"]["counters"]
        with urlopen(root + "/api/workbench/review-state", timeout=3) as response:
            receipt = json.load(response)["manual_review_state"]
        assert receipt["inflight"] is None
        assert receipt["last_result"] == result
        with urlopen(root + "/api/workbench?scenario=W2", timeout=3) as response:
            rebound = json.load(response)
        assert rebound["candidates"][0]["review"]["status"] == "SIMULATED"
        assert rebound["candidates"][0]["review"]["reviewed_at_ms"] == result["reviewed_at_ms"]
        assert rebound["candidates"][0]["btc_map_review_delta"]["same_snapshot"] is True
        assert rebound["candidates"][1]["review"]["status"] == "not_attached"
        with urlopen(root + "/api/workbench?scenario=W5", timeout=3) as response:
            changed = json.load(response)
        same_candidate = next(item for item in changed["candidates"]
                              if item["snapshot"]["candidate_id"] == candidate_id)
        assert same_candidate["review"]["status"] == "not_attached"
        assert same_candidate["btc_map_review_delta"]["same_snapshot"] is False
        assert same_candidate["btc_map_review_delta"]["differences_cn"]
        stale = Request(root + "/api/workbench/review",
                        data=json.dumps({"candidate_id": candidate_id, "scenario": "W5",
                                         "snapshot_id": frozen_snapshot_id,
                                         "idempotency_key": "stale"}).encode(),
                        headers={"Content-Type": "application/json"}, method="POST")
        try:
            urlopen(stale, timeout=3)
            raise AssertionError("stale snapshot should fail")
        except HTTPError as exc:
            assert exc.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
