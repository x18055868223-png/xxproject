"""MAP cadence, original-account invariants and immutable review binding."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from tools.astra_data_engine_v23 import DataEngine
from tools import astra_underwriting_v20 as core
from tools import astra_underwriting_review_v20 as review
from tools import astra_map_v24 as maps

spec = importlib.util.spec_from_file_location("map_wb_fixtures", Path(__file__).with_name("test_astra_workbench_v20.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def test_daily_check_cadence_never_renews_economic_clock(tmp_path):
    product = {"product_id": "daily.test", "provider_id": "public",
               "connection_status": "REGISTERED_ADAPTER",
               "request_policy": {"min_fetch_interval_ms": 6000, "budget_per_minute": 2},
               "usage_policies": {"default": {"ttl_ms": 1000, "allowed_data_states": ["OK"]},
                                  "fetch": {"ttl_ms": 0, "allowed_data_states": ["OK"]}}}
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema": "astra_data_product_registry@2.3.0", "products": [product]}), encoding="utf-8")
    engine = DataEngine(tmp_path / "engine", registry)
    calls = []
    def load():
        calls.append(1)
        return {"values": {"value": 42}, "observation_end_ms": fixtures.T, "retrieved_at_ms": fixtures.T}
    first = engine.fetch("daily.test", load, now_ms=fixtures.T)
    budget = json.loads(engine.state_path.read_text(encoding="utf-8"))["fetch_budget"]
    again = engine.fetch("daily.test", load, now_ms=fixtures.T + 2000)
    assert calls == [1]
    assert again["time"]["first_seen_at_ms"] == first["time"]["first_seen_at_ms"]
    assert again["time"]["observation_end_ms"] == fixtures.T
    assert not engine.read("daily.test", now_ms=fixtures.T + 2000, usage="background")["usage_decision"]["can_use"]
    assert json.loads(engine.state_path.read_text(encoding="utf-8"))["fetch_budget"] == budget
    engine.reserve_request("daily.test", now_ms=fixtures.T)
    charged = json.loads(engine.state_path.read_text(encoding="utf-8"))["fetch_budget"]
    assert sum(charged["product"].values()) == 2
    with pytest.raises(Exception, match="budget exhausted"):
        engine.reserve_request("daily.test", now_ms=fixtures.T)
    assert engine.read("daily.test", now_ms=fixtures.T, usage="background")["time"]["first_seen_at_ms"] == first["time"]["first_seen_at_ms"]


def test_optional_map_binds_review_without_changing_old_account(tmp_path):
    wb, path = fixtures.make_workbench(tmp_path, risk_provider=fixtures.positive_risk)
    fixtures.write_fmz(path, fixtures.native_gate())
    old = wb.refresh(record_daily=False)["candidates"][0]["snapshot"]
    # Workbench may already attach a MAP; recover the original account fixture.
    old = deepcopy(old)
    old["data_identity"].pop("btc_map", None)
    old["snapshot_id"] = core._hash({key: old[key] for key in (
        "contract", "fact_context", "data_identity", "market", "fee", "risk", "economics",
        "liability", "status", "gap_reasons", "execution_allowed")})
    mapping = maps.build_map(None, now_ms=old["market"]["valuation_ts_ms"], snapshot=old)
    bound = core.attach_btc_map(old, mapping)
    assert bound["snapshot_id"] != old["snapshot_id"]
    for key in ("contract", "market", "fee", "risk", "economics", "liability", "status", "execution_allowed"):
        assert bound[key] == old[key]
    assert "btc_map" not in old["data_identity"]
    assert core.llm_applicability_packet(bound)["data_identity"]["btc_map"] == mapping
    changed = deepcopy(bound)
    changed["data_identity"]["btc_map"]["map_id"] = "b" * 64
    with pytest.raises(ValueError, match="changed since freeze"):
        core.llm_applicability_packet(changed)
    mismatch = deepcopy(mapping)
    mismatch["policy_link"]["candidate_id"] = "other-contract"
    with pytest.raises(ValueError, match="exact candidate"):
        core.attach_btc_map(old, mismatch)
    tampered = deepcopy(mapping)
    tampered["map_id"] = "b" * 64
    with pytest.raises(ValueError, match="content hash"):
        core.attach_btc_map(old, tampered)
    wrong_manifest = deepcopy(mapping)
    wrong_manifest["source_manifest"]["candidate_id"] = "wrong-policy"
    wrong_manifest["source_manifest"]["manifest_hash"] = core._hash({
        k: v for k, v in wrong_manifest["source_manifest"].items() if k != "manifest_hash"})
    wrong_manifest["map_id"] = core._hash({k: v for k, v in wrong_manifest.items() if k != "map_id"})
    with pytest.raises(ValueError, match="source manifest"):
        core.attach_btc_map(old, wrong_manifest)
    request = review.build_request(bound, "fixture-model")
    assert "竞争解释" in request["messages"][1]["content"]
    assert request["_local_snapshot_id"] == bound["snapshot_id"]
    assert "map.policy" not in review._allowed_refs(core.llm_applicability_packet(bound))
    unfrozen = maps.build_map(None, now_ms=old["market"]["valuation_ts_ms"], snapshot=old, references=[{
        "reference_id": "unfrozen-local-zone", "source_record_ids": ["unfrozen-source"],
        "family": "TRADED_ACCEPTANCE", "role": "KPF_BASIN", "geometry": "OBSERVED_ZONE",
        "raw_low": 96000, "raw_high": 97000, "market": "Deribit", "quote_currency": "USD",
        "price_basis": "DERIBIT_BTC_USD_INDEX", "coordinate_qualification": "COMPARABLE",
        "quality": "OK", "known_at_ms": old["market"]["valuation_ts_ms"],
        "active_from_ms": old["market"]["valuation_ts_ms"], "usage_decision": {"can_use": True}}])
    unfrozen_bound = core.attach_btc_map(old, unfrozen)
    assert unfrozen["policy_link"]["region_liability_rows"]
    assert "map.policy" not in review._allowed_refs(core.llm_applicability_packet(unfrozen_bound))
