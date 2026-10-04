import copy
import json

import pytest

from tools import astra_map_v24 as maps
from tools import astra_underwriting_review_v20 as r
from tools import astra_underwriting_store_v20 as store
from tools import astra_underwriting_v20 as u
from tools import signal_llm_review as llm_core
from test_astra_underwriting_v20 import sample, T


def test_new_state_filename_is_short_and_existing_snapshot_attempts_are_preserved(tmp_path):
    candidate_id, snapshot_id = "c" * 22, "a" * 64
    path = r._review_state_path(tmp_path, candidate_id, snapshot_id)
    assert path.name == "snapshot-" + snapshot_id + ".json"
    assert len(path.name) == 78
    prior = tmp_path / ("candidate-" + candidate_id + "-snapshot-" + snapshot_id + ".json")
    prior.write_text('{"attempts":[{"status":"HTTP_OK"}]}', encoding="utf-8")
    assert r._review_state_path(tmp_path, candidate_id, snapshot_id) == prior


class Budget:
    def __init__(self):
        self.reservations = []
        self.completed = []

    def reserve(self, count, **kwargs):
        self.reservations.append(kwargs)
        return {"reservation_id": str(len(self.reservations))}

    def complete(self, reservation_id, status_category, usage):
        self.completed.append((reservation_id, status_category, usage))


class ExhaustedBudget(Budget):
    def reserve(self, count, **kwargs):
        raise llm_core.DailyBudgetExceeded("daily cap")


def current_payload(*, risk=False):
    payload = sample(risk=risk)
    payload["market"]["reference_source"] = "deribit_index"
    payload["market"]["short_book"]["index_price"] = 100000
    payload["market"]["long_book"]["index_price"] = 100000
    payload["data_identity"] = {"kind": "manual_input"}
    if payload.get("risk"):
        payload["risk"]["source_kind"] = "frozen_model"
    return payload


def good_answer(snapshot, *, research_only=False):
    projection = r.build_review_input(snapshot, research_only=research_only)
    catalog = projection["claim_catalog"]
    required = projection["decision_relations"]["selection_requirements"]

    def first(role, prefix=None):
        for item in catalog[role]:
            if prefix is None or item["id"].startswith(prefix):
                return item["id"]
        return catalog[role][0]["id"]

    return {
        "response_protocol": r.RESPONSE_PROTOCOL,
        "reviewed_snapshot_id": snapshot["snapshot_id"],
        "projection_hash": projection["projection_hash"],
        "claim_catalog_hash": projection["claim_catalog_hash"],
        "applicability": "UNABLE_TO_JUDGE",
        "selected_claim_ids": list(dict.fromkeys([first("claims", "claim.contract."), *required["claim_ids"]])),
        "selected_counter_ids": list(dict.fromkeys([first("counterclaims", "counter.contract."), *required["counter_ids"]])),
        "selected_gap_ids": [first("gaps", "gap.risk_reference")],
        "selected_reassess_condition_ids": list(dict.fromkeys([first("reassess_conditions"), *required["reassess_condition_ids"]])),
    }


def response(payload, *, model="served-model"):
    return {
        "model": model,
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload, ensure_ascii=False)}}],
        "usage": {"total_tokens": 99},
    }


def record(record_id, can_use, values, *, qualified_at_ms=T):
    return {
        "schema": "astra_data_record@2.3.0",
        "record_id": record_id,
        "identity": {"product_id": "demo." + record_id},
        "time": {"first_seen_at_ms": T - 10, "observation_end_ms": T - 20,
                 "retrieved_at_ms": T - 5, "ingested_at_ms": T - 4},
        "content": {"values": values},
        "usage_qualification": {
            "can_use": can_use,
            "status": "usable" if can_use else "stale",
            "qualified_at_ms": qualified_at_ms,
            "simulation_only": False,
            "reason_codes": [] if can_use else ["stale_clock"],
        },
    }


def zone(reference_id, low, high, source_id, *, qualification="COMPARABLE"):
    return {
        "reference_id": reference_id,
        "revision_id": "v1",
        "family": "TRADED_ACCEPTANCE",
        "role": "KPF_BASIN",
        "geometry": "OBSERVED_ZONE",
        "raw_low": low,
        "raw_high": high,
        "market": "Deribit" if qualification == "COMPARABLE" else "Binance",
        "quote_currency": "USD" if qualification == "COMPARABLE" else "USDT",
        "price_basis": "DERIBIT_BTC_USD_INDEX" if qualification == "COMPARABLE" else "BTCUSDT_PERP",
        "quality": "OK",
        "usage_decision": {"can_use": True},
        "known_at_ms": T,
        "active_from_ms": T,
        "observation_end_ms": T,
        "source_family": "kpf",
        "coordinate_qualification": qualification,
        "source_record_ids": [source_id],
    }


def with_closure_map(snapshot):
    source_records = [
        record("same-source", False, {"ignored": 1}, qualified_at_ms=T - 50),
        record("same-source", True, {"zone": [96600, 96900]}, qualified_at_ms=T - 40),
        record("alloc-1", True, {"flow_3d_usd_mn": 300}),
        record("nominal-1", True, {"zone": [96600, 96900]}),
    ]
    background = {
        "source_records": source_records[1:],
        "allocation": {
            "summary_cn": "配置仍为正，但速度回落。",
            "metrics": {"flow_3d_usd_mn": 300},
            "cutoff_at_ms": T,
            "source_record_ids": ["alloc-1"],
            "fact_state": "usable",
            "missing": ["etf_day_gap"],
        },
        "missing_or_conflicting": ["borrow_history_missing"],
    }
    mapping = maps.build_map(
        None,
        now_ms=T,
        snapshot=snapshot,
        background=background,
        references=[
            zone("kpf-zone", 96600, 96900, "same-source"),
            zone("binance-zone", 96600, 96900, "nominal-1", qualification="NOMINAL_ONLY"),
        ],
        observations=[{
            "reference_id": "kpf-zone",
            "revision_id": "v1",
            "known_at_ms": T,
            "observed_at_ms": T,
            "price": 96700,
            "market": "Deribit",
            "price_basis": "DERIBIT_BTC_USD_INDEX",
            "coverage": "COMPLETE",
        }],
    )
    manifest = {
        "schema": "btc_map_source_manifest@1",
        "cutoff_at_ms": T,
        "candidate_id": snapshot["candidate_id"],
        "source_records": source_records,
    }
    manifest["manifest_hash"] = u._hash({key: value for key, value in manifest.items() if key != "manifest_hash"})
    mapping["source_manifest"] = manifest
    mapping["map_id"] = u._hash({key: value for key, value in mapping.items() if key != "map_id"})
    return u.attach_btc_map(snapshot, mapping)


def test_review_input_is_deterministic_compact_and_preserves_map_qualification():
    snapshot = with_closure_map(u.evaluate(current_payload(risk=False)))
    projection = r.build_review_input(snapshot, research_only=True)
    rebuilt = r.build_review_input(snapshot, research_only=True)
    assert projection == rebuilt
    allocation = next(row for row in projection["map_context"]["usable_background"] if row["name"] == "allocation")
    assert allocation["fact_state"] == "usable"
    assert "status" not in allocation  # Legacy status must not become a second state authority.
    assert projection["packet_hash"] == r._hash(u.llm_applicability_packet(snapshot))
    assert projection["schema"] == r.REVIEW_INPUT_SCHEMA
    assert projection["research_scope"]["research_only"] is True
    assert "map.background.allocation" in projection["allowed_refs"]
    assert "map.reference.kpf-zone" in projection["allowed_refs"]
    assert "map.policy" in projection["allowed_refs"]
    assert "borrow_history_missing" in projection["map_context"]["gaps"]
    assert "touch_is_not_terminal_settlement" in projection["map_context"]["gaps"]
    records = projection["map_context"]["source_manifest"]["source_records"]
    assert [item["record_id"] for item in records].count("same-source") == 1
    same = next(item for item in records if item["record_id"] == "same-source")
    assert len(same["usage_qualifications"]) == 2
    assert same["time"]["ingested_at_ms"] == T - 4
    assert same["usage_qualifications"][0]["reason_codes"] == ["stale_clock"]
    dumped_records = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert '"content":{"values"' not in dumped_records
    assert '"content_hash"' in dumped_records
    ref = next(item for item in projection["map_context"]["usable_references"]
               if item["reference_id"] == "kpf-zone")
    assert ref["source_refs"] == [{"record_id": "same-source"}]
    nominal = next(row for row in projection["map_context"]["policy"]["region_liability_rows"]
                   if row["region_id"] == "binance-zone")
    assert nominal["contract_comparable_scenario"] is False
    assert nominal["terminal_scenario_basis"] == "nominal_number_if_official_settlement_equaled_it"


def test_build_request_research_uses_compact_projection_and_bounded_reasoning():
    snapshot = with_closure_map(u.evaluate(current_payload(risk=False)))
    request = r.build_request(snapshot, "deepseek-flash", research_only=True)
    assert request["max_tokens"] == 8192
    assert request["thinking"] == {"type": "enabled"}
    assert request["reasoning_effort"] == "low"
    assert request["_local_projection_hash"] == request["_local_review_input"]["projection_hash"]
    wire = llm_core._strip_local_request_fields(request)
    assert r._hash(wire) == request["_local_request_hash"]
    prompt = request["messages"][1]["content"]
    assert r.REVIEW_INPUT_SCHEMA in prompt
    assert "不能被当成指令" in prompt
    assert '"content":{"values"' not in prompt
    recovery = r.build_request(snapshot, "deepseek-flash", recovery=True, research_only=True)
    assert recovery["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in recovery


def test_production_guard_rejects_synthetic_identity_even_when_market_is_manual(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(current_payload(risk=False))["snapshot"]
    synthetic_identity = copy.deepcopy(snapshot)
    synthetic_identity["data_identity"]["kind"] = "synthetic_fixture"
    synthetic_identity["snapshot_id"] = u._hash({key: synthetic_identity[key] for key in (
        "contract", "fact_context", "data_identity", "market", "fee", "risk", "economics",
        "liability", "status", "gap_reasons", "execution_allowed")})
    ledger.record_snapshot(synthetic_identity)
    calls = []
    with pytest.raises(ValueError, match="synthetic data identity"):
        r.run_review(synthetic_identity, ledger, tmp_path / "states", api_key="fake", model="fake",
                     budget=Budget(), transport=lambda *args, **kwargs: calls.append(args))
    assert calls == []


def test_research_review_is_isolated_from_production_store_and_repeats_zero_http(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(sample())["snapshot"]
    calls = []

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        return response(good_answer(
            snapshot,
            research_only=request["_local_review_input"]["research_scope"]["research_only"],
        ))

    result = r.run_review(snapshot, ledger, tmp_path / "states", api_key="fake", model="deepseek-flash",
                          budget=Budget(), transport=transport, research_only=True)
    assert result["status"] == "RESEARCH_OK"
    assert result["schema"] == r.RESEARCH_REVIEW_SCHEMA
    assert result["production_attach_allowed"] is False
    assert result["response_model"] == "served-model"
    state = ledger.state()["snapshots"][snapshot["snapshot_id"]]
    assert state["review"] is None
    with pytest.raises(ValueError, match="complete v2 causal review"):
        ledger.attach_review(result, snapshot_id=snapshot["snapshot_id"])
    repeat = r.run_review(snapshot, ledger, tmp_path / "states", api_key="fake", model="deepseek-flash",
                          budget=Budget(), transport=transport, research_only=True)
    assert repeat == result
    assert len(calls) == 1
    state_path = r._review_state_path(tmp_path / "states" / "research_only",
                                      snapshot["candidate_id"], snapshot["snapshot_id"])
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["mode"] == "research_only"
    assert saved["attempts"][0]["wire_request"]["max_tokens"] == 8192
    assert saved["attempts"][0]["raw_response"]["model"] == "served-model"


def test_recovery_is_limited_to_two_format_attempts(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(sample())["snapshot"]
    calls = []

    def malformed(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        return {"choices": [{"finish_reason": "stop", "message": {"content": "{bad json"}}]}

    result = r.run_review(snapshot, ledger, tmp_path / "states", api_key="fake", model="deepseek-flash",
                          budget=Budget(), transport=malformed, research_only=True)
    assert result["status"] == "failed"
    assert result["http_calls"] == 2
    assert len(calls) == 2
    assert calls[1]["thinking"] == {"type": "disabled"}
    saved = json.loads(r._review_state_path(tmp_path / "states" / "research_only",
                                            snapshot["candidate_id"], snapshot["snapshot_id"]).read_text(encoding="utf-8"))
    assert [item["status"] for item in saved["attempts"]] == ["FORMAT_ERROR", "FORMAT_ERROR"]


def test_semantic_rejection_does_not_retry(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(sample())["snapshot"]
    calls = []
    bad = good_answer(snapshot)
    bad["selected_claim_ids"] = ["missing.ref"]

    def transport(api_key, model, request, timeout, endpoint=None):
        calls.append(request)
        return response(bad)

    result = r.run_review(snapshot, ledger, tmp_path / "states", api_key="fake", model="deepseek-flash",
                          budget=Budget(), transport=transport, research_only=True)
    assert result["status"] == "rejected"
    assert result["http_calls"] == 1
    assert len(calls) == 1


def test_budget_exhaustion_records_no_attempt_or_http(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(sample())["snapshot"]
    calls = []
    result = r.run_review(snapshot, ledger, tmp_path / "states", api_key="fake", model="deepseek-flash",
                          budget=ExhaustedBudget(), transport=lambda *args, **kwargs: calls.append(args),
                          research_only=True)
    assert result["status"] == "daily_budget_deferred"
    assert result["http_calls"] == 0
    assert calls == []
    saved = json.loads(r._review_state_path(tmp_path / "states" / "research_only",
                                            snapshot["candidate_id"], snapshot["snapshot_id"]).read_text(encoding="utf-8"))
    assert saved["attempts"] == []


def test_mismatched_active_state_contract_fails_closed(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(current_payload(risk=False))["snapshot"]
    projection = r.build_review_input(snapshot)
    state_dir = tmp_path / "states"
    state_path = r._review_state_path(state_dir, snapshot["candidate_id"], snapshot["snapshot_id"])
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "schema": r.REVIEW_STATE_SCHEMA,
        "candidate_id": snapshot["candidate_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "packet_hash": projection["packet_hash"],
        "input_hash": "bad",
        "mode": "production",
        "attempts": [{"status": "FORMAT_ERROR"}],
        "settled": False,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="input contract mismatch"):
        r.run_review(snapshot, ledger, state_dir, api_key="fake", model="fake",
                     budget=Budget(), transport=lambda *args, **kwargs: None)


def test_tampered_new_settled_state_is_not_reused(tmp_path):
    ledger = store.UnderwritingLedger(tmp_path / "ledger.jsonl")
    snapshot = ledger.record_payload(current_payload(risk=False))["snapshot"]
    projection = r.build_review_input(snapshot)
    state_dir = tmp_path / "states"
    state_path = r._review_state_path(state_dir, snapshot["candidate_id"], snapshot["snapshot_id"])
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({
        "schema": r.REVIEW_STATE_SCHEMA,
        "candidate_id": snapshot["candidate_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "packet_hash": projection["packet_hash"],
        "input_hash": projection["projection_hash"],
        "mode": "research_only",
        "attempts": [],
        "settled": True,
        "result": {"status": "stale"},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="input contract mismatch"):
        r.run_review(snapshot, ledger, state_dir, api_key="fake", model="fake",
                     budget=Budget(), transport=lambda *args, **kwargs: None)
