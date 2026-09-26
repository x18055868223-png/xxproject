import hashlib
import json
import threading
import time

import pytest

from tools import astra_data_engine_v23 as data_engine_module
from tools.astra_data_engine_v23 import DataEngine, DataEngineError


T = 1_790_323_200_000


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def manifest_hash(manifest):
    body = dict(manifest)
    body.pop("manifest_hash", None)
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def engine(tmp_path):
    return DataEngine(tmp_path / "state")


def minimal_registry(product_id="flat.test.product.v1"):
    return {
        "schema": "astra_data_product_registry@2.3.0",
        "products": [
            {
                "product_id": product_id,
                "provider_id": "flat_provider",
                "collector_id": "flat_collector",
                "primary_collector": "server",
                "connection_status": "REGISTERED_ADAPTER",
                "definition_version": "flat.test@1.0.0",
                "unit": "value",
                "usage_policies": {"default": {"ttl_ms": 300000, "allowed_data_states": ["OK"]}},
            }
        ],
    }


def budget_registry():
    def product(product_id, limit):
        return {
            "product_id": product_id,
            "provider_id": "shared_provider",
            "collector_id": product_id + ".collector",
            "primary_collector": "server",
            "connection_status": "REGISTERED_ADAPTER",
            "definition_version": product_id + "@1.0.0",
            "unit": "value",
            "usage_policies": {"default": {"ttl_ms": 300000, "allowed_data_states": ["OK"]}, "fetch": {"ttl_ms": 300000, "allowed_data_states": ["OK"]}},
            "request_policy": {"budget_per_minute": limit, "retry_backoff_ms": [10000, 30000]},
        }

    return {
        "schema": "astra_data_product_registry@2.3.0",
        "products": [
            product("budget.small.v1", 1),
            product("budget.large.v1", 3),
        ],
    }


def write_registry(path, registry):
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    return path


def quote_values(price=100000, *, matched=True):
    return {
        "short_leg": {"instrument": "BTC-26SEP26-100000-C", "bid": 0.010, "observed_at_ms": T},
        "long_leg": {"instrument": "BTC-26SEP26-102000-C", "ask": 0.004, "observed_at_ms": T if matched else T - 10_000},
        "quantity_btc": 1.0,
        "index_price_usd": price,
        "leg_time_match": matched,
    }


def test_registry_contains_required_v23_products_and_planned_sources(tmp_path):
    e = engine(tmp_path)
    required = {
        "fmz.bridge.snapshots.v1",
        "fmz.spacegate.current.v1",
        "gex.raw.board.v1",
        "gex.effective.accepted.v1",
        "gex.pending.candidate.v1",
        "gex.netgamma.proxy.v1",
        "binance.spot.btcusdt.trade.v1",
        "binance.spot.btcusdt.book_ticker.v1",
        "binance.spot.btcusdt.kline_1m.v1",
        "binance.um.btcusdt.kline_1m.v1",
        "binance.um.btcusdt.funding.settled.v1",
        "binance.um.btcusdt.funding.predicted.v1",
        "binance.um.btcusdt.oi.native.v1",
        "binance.um.btcusdt.oi.usd_notional.v1",
        "deribit.btc.option.universe.v1",
        "deribit.btc.discovery.current.v1",
        "deribit.btc.option.instrument.v1",
        "deribit.btc.index.v1",
        "deribit.btc.option.greeks_oi.v1",
        "deribit.btc.option.order_book.v1",
        "deribit.btc.vertical.two_leg_book.v1",
        "deribit.btc.option.fee_schedule.v1",
        "macro.risk_state.components.v1",
        "risk.astra.qualified_model.domain.v1",
        "risk.astra.frozen_mu.reference.v1",
        "btc.etf.flow.daily.v1",
        "btc.brk.cost_basis.v1",
        "btc.brk.realized_pnl.daily.v1",
        "cex.usdt.borrow_rate.v1",
        "btc.kpf.map.readonly.v1",
    }
    assert required.issubset(e.products)
    assert e.products["fmz.spacegate.current.v1"]["connection_status"] == "REGISTERED_ADAPTER"
    assert e.products["btc.etf.flow.daily.v1"]["connection_status"] == "REGISTERED_ADAPTER"
    assert e.products["macro.dxy.close.v1"]["connection_status"] == "PLANNED_NOT_CONNECTED"
    assert e.products["macro.usd.broad.close.v1"]["asset"] == "DTWEXBGS"
    assert e.products["deribit.btc.vertical.two_leg_book.v1"]["usage_policies"]["candidate_quote"]["ttl_ms"] == 5000
    assert e.products["deribit.btc.option.order_book.v1"]["usage_policies"]["fetch"]["ttl_ms"] == 5000
    assert e.products["deribit.btc.option.universe.v1"]["usage_policies"]["fetch"]["ttl_ms"] == 300000
    assert e.products["fmz.spacegate.current.v1"]["usage_policies"]["default"]["ttl_ms"] == 300000
    assert "TTL values" in e.registry["ttl_policy_note"]


def test_default_registry_path_supports_flat_installer_layout(tmp_path, monkeypatch):
    flat_dir = tmp_path / "signal-audit-tools"
    flat_dir.mkdir()
    (flat_dir / "data_product_registry_v23.json").write_text(
        json.dumps(minimal_registry(), ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(data_engine_module, "__file__", str(flat_dir / "astra_data_engine_v23.py"))
    e = DataEngine(tmp_path / "state")
    assert e.registry_path == flat_dir / "data_product_registry_v23.json"
    assert "flat.test.product.v1" in e.products


def test_default_registry_missing_fails_closed_in_flat_layout(tmp_path, monkeypatch):
    flat_dir = tmp_path / "signal-audit-tools"
    flat_dir.mkdir()
    monkeypatch.setattr(data_engine_module, "__file__", str(flat_dir / "astra_data_engine_v23.py"))
    with pytest.raises(DataEngineError) as err:
        DataEngine(tmp_path / "state")
    assert err.value.code == "REGISTRY_MISSING"


def test_empty_or_invalid_registry_fails_closed(tmp_path):
    empty = tmp_path / "empty_registry.json"
    empty.write_text(json.dumps({"schema": "astra_data_product_registry@2.3.0", "products": []}), encoding="utf-8")
    with pytest.raises(DataEngineError) as empty_err:
        DataEngine(tmp_path / "state_empty", registry_path=empty)
    assert empty_err.value.code == "REGISTRY_EMPTY"
    invalid = tmp_path / "invalid_registry.json"
    invalid.write_text("{not-json", encoding="utf-8")
    with pytest.raises(DataEngineError) as invalid_err:
        DataEngine(tmp_path / "state_invalid", registry_path=invalid)
    assert invalid_err.value.code == "REGISTRY_INVALID"


def test_fmz_bridge_allows_unknown_external_retrieval_with_explicit_ingest_time(tmp_path):
    e = engine(tmp_path)
    rec = e.ingest(
        "fmz.spacegate.current.v1",
        {"state": "STRUCTURE_ELIGIBLE"},
        observation_end_ms=T - 1000,
        retrieved_at_ms=None,
        ingested_at_ms=T,
    )
    assert rec["time"]["retrieved_at_ms"] is None
    assert rec["time"]["ingested_at_ms"] == T
    assert rec["time"]["first_seen_at_ms"] == T
    assert e.read("fmz.spacegate.current.v1", now_ms=T + 1000, usage="workbench.current")["usage_decision"]["can_use"]


def test_read_current_usage_rejects_record_first_seen_after_now(tmp_path):
    e = engine(tmp_path)
    e.ingest(
        "fmz.spacegate.current.v1",
        {"state": "STRUCTURE_ELIGIBLE"},
        observation_end_ms=T,
        retrieved_at_ms=None,
        ingested_at_ms=T + 10_000,
    )
    current = e.read("fmz.spacegate.current.v1", now_ms=T, usage="workbench.current")
    health = e.read("fmz.spacegate.current.v1", now_ms=T, usage="health")
    assert current["usage_decision"]["status"] == "FIRST_SEEN_AFTER_NOW"
    assert current["usage_decision"]["can_use"] is False
    assert health["usage_decision"]["can_use"] is True


def test_d03_invalid_payload_is_not_zero_filled_or_usable(tmp_path):
    e = engine(tmp_path)
    rec = e.ingest(
        "deribit.btc.vertical.two_leg_book.v1",
        {"short_leg": {"bid": None}, "long_leg": {"ask": 0.004}},
        observation_end_ms=T,
        retrieved_at_ms=T,
        data_state="INVALID",
        reason_codes=["FIELD_MISSING"],
        scope={"candidate": "call"},
    )
    read = e.read("deribit.btc.vertical.two_leg_book.v1", now_ms=T + 1000, usage="candidate_quote", scope={"candidate": "call"})
    assert read["record_id"] == rec["record_id"]
    assert read["usage_decision"]["can_use"] is False
    assert read["usage_decision"]["status"] == "INVALID"
    assert read["content"]["values"]["short_leg"]["bid"] is None


def test_d04_same_payload_preserves_first_seen_and_stales_by_usage_ttl(tmp_path):
    e = engine(tmp_path)
    first = e.ingest(
        "deribit.btc.vertical.two_leg_book.v1",
        quote_values(),
        observation_end_ms=T,
        retrieved_at_ms=T,
        scope={"candidate": "put"},
    )
    again = e.ingest(
        "deribit.btc.vertical.two_leg_book.v1",
        quote_values(),
        observation_end_ms=T,
        retrieved_at_ms=T + 600_000,
        scope={"candidate": "put"},
    )
    assert again["record_id"] == first["record_id"]
    assert again["time"]["first_seen_at_ms"] == T
    assert again["time"]["observation_end_ms"] == T
    assert again["time"]["retrieved_at_ms"] == T
    stale = e.read("deribit.btc.vertical.two_leg_book.v1", now_ms=T + 6000, usage="candidate_quote", scope={"candidate": "put"})
    assert stale["usage_decision"]["status"] == "STALE"
    assert stale["usage_decision"]["can_use"] is False


def test_d05_eligible_cache_prevents_duplicate_loader_call(tmp_path):
    e = engine(tmp_path)
    e.ingest(
        "deribit.btc.vertical.two_leg_book.v1",
        quote_values(),
        observation_end_ms=T,
        retrieved_at_ms=T,
        scope={"candidate": "put"},
    )
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        raise AssertionError("loader should not be called while cache is eligible")

    rec = e.fetch("deribit.btc.vertical.two_leg_book.v1", loader, now_ms=T + 1000, scope={"candidate": "put"})
    assert rec["fetch_decision"]["status"] == "CACHE_HIT"
    assert calls["n"] == 0


def test_d10_time_mismatched_two_leg_book_fails_candidate_usage(tmp_path):
    e = engine(tmp_path)
    e.ingest(
        "deribit.btc.vertical.two_leg_book.v1",
        quote_values(matched=False),
        observation_end_ms=T,
        retrieved_at_ms=T,
        data_state="INVALID",
        reason_codes=["LEG_TIME_MISMATCH"],
        scope={"candidate": "call"},
    )
    read = e.read("deribit.btc.vertical.two_leg_book.v1", now_ms=T + 1000, usage="candidate_quote", scope={"candidate": "call"})
    assert read["usage_decision"]["can_use"] is False
    assert "DATA_STATE_INVALID" in read["usage_decision"]["reason_codes"]
    assert read["content"]["values"]["leg_time_match"] is False


def test_d12_future_source_time_is_rejected(tmp_path):
    e = engine(tmp_path)
    with pytest.raises(DataEngineError) as err:
        e.ingest(
            "fmz.spacegate.current.v1",
            {"state": "STRUCTURE_ELIGIBLE"},
            observation_end_ms=T + 10_000,
            retrieved_at_ms=None,
            ingested_at_ms=T,
        )
    assert err.value.code == "SOURCE_TIME_IN_FUTURE"


def test_d14_late_old_value_does_not_rollback_and_revision_is_new_version(tmp_path):
    e = engine(tmp_path)
    first = e.ingest(
        "macro.risk_state.components.v1",
        {"score": 0.1},
        observation_end_ms=T,
        retrieved_at_ms=T,
    )
    late_old = e.ingest(
        "macro.risk_state.components.v1",
        {"score": -0.8},
        observation_end_ms=T - 1000,
        retrieved_at_ms=T + 1000,
    )
    current = e.read("macro.risk_state.components.v1", now_ms=T + 2000, usage="macro_layer")
    assert late_old["collector"]["accepted_as_current"] is False
    assert current["record_id"] == first["record_id"]
    revision = e.ingest(
        "macro.risk_state.components.v1",
        {"score": 0.2},
        observation_end_ms=T,
        retrieved_at_ms=T + 2000,
        source_revision="rev-2",
        parent_record_ids=[first["record_id"]],
    )
    assert revision["record_id"] != first["record_id"]
    assert e.read("macro.risk_state.components.v1", now_ms=T + 3000, usage="macro_layer")["record_id"] == revision["record_id"]


def test_d17_singleflight_collapses_concurrent_fetches(tmp_path):
    e = engine(tmp_path)
    calls = {"n": 0}
    lock = threading.Lock()

    def loader():
        with lock:
            calls["n"] += 1
        time.sleep(0.05)
        return {"values": quote_values(), "observation_end_ms": T, "retrieved_at_ms": T}

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(e.fetch("deribit.btc.vertical.two_leg_book.v1", loader, now_ms=T, scope={"candidate": "put"})))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls["n"] == 1
    assert len(results) == 4
    assert {item["record_id"] for item in results} == {results[0]["record_id"]}


def test_d18_fetch_failure_enters_finite_backoff_without_second_loader_call(tmp_path):
    e = engine(tmp_path)
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        raise RuntimeError("temporary transport failure")

    with pytest.raises(DataEngineError) as first_err:
        e.fetch("deribit.btc.index.v1", loader, now_ms=T, scope={"index": "btc_usd"})
    assert first_err.value.code == "FETCH_ERROR"
    with pytest.raises(DataEngineError) as second_err:
        e.fetch("deribit.btc.index.v1", loader, now_ms=T + 1000, scope={"index": "btc_usd"})
    assert second_err.value.code == "FETCH_BACKOFF_ACTIVE"
    assert calls["n"] == 1


def test_provider_budget_uses_provider_max_and_product_budget_stays_independent(tmp_path):
    registry_path = write_registry(tmp_path / "budget_registry.json", budget_registry())
    e = DataEngine(tmp_path / "state", registry_path=registry_path)
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return {"values": {"call": calls["n"]}, "observation_end_ms": T, "retrieved_at_ms": T}

    e.fetch("budget.small.v1", loader, now_ms=T, scope={"scope": "small-1"})
    e.fetch("budget.large.v1", loader, now_ms=T, scope={"scope": "large-1"})
    e.fetch("budget.large.v1", loader, now_ms=T, scope={"scope": "large-2"})
    with pytest.raises(DataEngineError) as provider_err:
        e.fetch("budget.large.v1", loader, now_ms=T, scope={"scope": "large-3"})
    assert provider_err.value.code == "FETCH_BUDGET_EXHAUSTED"
    assert provider_err.value.detail["level"] == "provider"
    assert calls["n"] == 3

    e2 = DataEngine(tmp_path / "state2", registry_path=registry_path)
    e2.fetch("budget.small.v1", loader, now_ms=T, scope={"scope": "small-1"})
    with pytest.raises(DataEngineError) as product_err:
        e2.fetch("budget.small.v1", loader, now_ms=T, scope={"scope": "small-2"})
    assert product_err.value.code == "FETCH_BUDGET_EXHAUSTED"
    assert product_err.value.detail["level"] == "product"


def test_repeated_scope_cache_hit_does_not_consume_fetch_budget(tmp_path):
    registry_path = write_registry(tmp_path / "budget_registry.json", budget_registry())
    e = DataEngine(tmp_path / "state", registry_path=registry_path)
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return {"values": {"call": calls["n"]}, "observation_end_ms": T, "retrieved_at_ms": T}

    first = e.fetch("budget.small.v1", loader, now_ms=T, scope={"scope": "same"})
    second = e.fetch("budget.small.v1", loader, now_ms=T + 1000, scope={"scope": "same"})
    assert first["record_id"] == second["record_id"]
    assert second["fetch_decision"]["status"] == "CACHE_HIT"
    assert calls["n"] == 1
    with pytest.raises(DataEngineError) as product_err:
        e.fetch("budget.small.v1", loader, now_ms=T + 1000, scope={"scope": "different"})
    assert product_err.value.detail["level"] == "product"


def test_fetch_loader_must_provide_observation_time(tmp_path):
    e = engine(tmp_path)

    def loader():
        return {"values": {"index_price": 100000}, "retrieved_at_ms": T}

    with pytest.raises(DataEngineError) as err:
        e.fetch("deribit.btc.index.v1", loader, now_ms=T, scope={"index": "btc_usd"})
    assert err.value.code == "LOADER_RESULT_INVALID"


def test_d20_old_health_green_expires(tmp_path):
    e = engine(tmp_path)
    e.ingest(
        "fmz.spacegate.current.v1",
        {"state": "STRUCTURE_ELIGIBLE"},
        observation_end_ms=T,
        retrieved_at_ms=None,
        ingested_at_ms=T,
    )
    health = e.health(T + 700_000)
    product = health["products"]["fmz.spacegate.current.v1"]
    assert product["current"][0]["usage_decision"]["status"] == "STALE"
    assert product["status"] == "STALE"


def test_health_reports_business_usage_stale_even_when_health_ttl_still_valid(tmp_path):
    e = engine(tmp_path)
    e.ingest(
        "deribit.btc.vertical.two_leg_book.v1",
        quote_values(),
        observation_end_ms=T,
        retrieved_at_ms=T,
        scope={"candidate": "put"},
    )
    product = e.health(T + 6000)["products"]["deribit.btc.vertical.two_leg_book.v1"]
    current = product["current"][0]
    assert current["usage_decision"]["can_use"] is True
    assert current["usage_decisions"]["candidate_quote"]["status"] == "STALE"
    assert product["status"] == "USAGE_LIMITED"


def test_d21_freeze_excludes_records_first_seen_after_cutoff_and_hashes_manifest(tmp_path):
    e = engine(tmp_path)
    before = e.ingest(
        "deribit.btc.index.v1",
        {"index_price": 100000},
        observation_end_ms=T - 1000,
        retrieved_at_ms=T - 500,
        scope={"index": "btc_usd"},
    )
    after = e.ingest(
        "macro.risk_state.components.v1",
        {"score": 0.2},
        observation_end_ms=T + 1000,
        retrieved_at_ms=T + 1000,
        scope={"macro": "snapshot"},
    )
    manifest = e.freeze([before, after], cutoff_at_ms=T, candidate_id="candidate-1")
    assert [item["record_id"] for item in manifest["source_records"]] == [before["record_id"]]
    assert manifest["excluded_records"][0]["record_id"] == after["record_id"]
    assert manifest["excluded_records"][0]["reason"] == "first_seen_after_cutoff"
    assert manifest["manifest_hash"] == manifest_hash(manifest)
    assert manifest["source_records"][0]["content"]["values"]["index_price"] == 100000
    assert "checked_at_ms" not in manifest["source_records"][0]["quality"]
    assert "served_at_ms" not in manifest["source_records"][0]["usage_qualification"]


def test_freeze_manifest_stable_after_repeated_same_payload_check(tmp_path):
    e = engine(tmp_path)
    e.ingest(
        "deribit.btc.vertical.two_leg_book.v1",
        quote_values(),
        observation_end_ms=T,
        retrieved_at_ms=T,
        scope={"candidate": "put"},
    )
    before_read = e.read("deribit.btc.vertical.two_leg_book.v1", now_ms=T + 1000, usage="candidate_quote", scope={"candidate": "put"})
    before = e.freeze([before_read], cutoff_at_ms=T + 1000, candidate_id="put-1")
    e.ingest(
        "deribit.btc.vertical.two_leg_book.v1",
        quote_values(),
        observation_end_ms=T,
        retrieved_at_ms=T + 2000,
        scope={"candidate": "put"},
    )
    after_read = e.read("deribit.btc.vertical.two_leg_book.v1", now_ms=T + 1000, usage="candidate_quote", scope={"candidate": "put"})
    after = e.freeze([after_read], cutoff_at_ms=T + 1000, candidate_id="put-1")
    assert after["manifest_hash"] == before["manifest_hash"]
    assert after["source_records"] == before["source_records"]


def test_historical_import_is_visible_but_not_current_or_freezable(tmp_path):
    e = engine(tmp_path)
    rec = e.ingest(
        "macro.risk_state.components.v1",
        {"score": 0.2},
        observation_end_ms=T - 1000,
        retrieved_at_ms=None,
        ingested_at_ms=T,
        historical_import=True,
    )
    current = e.read("macro.risk_state.components.v1", now_ms=T + 1000, usage="manual_review_packet")
    history = e.read("macro.risk_state.components.v1", now_ms=T + 1000, usage="history")
    manifest = e.freeze([rec], cutoff_at_ms=T + 1000)
    assert current["usage_decision"]["status"] == "HISTORICAL_IMPORT_ONLY"
    assert history["usage_decision"]["can_use"] is True
    assert manifest["source_records"] == []
    assert manifest["excluded_records"][0]["reason"] == "historical_import_not_current"


def test_d23_secret_fields_and_values_are_rejected_without_persistence(tmp_path):
    e = engine(tmp_path)
    with pytest.raises(DataEngineError) as key_err:
        e.ingest(
            "fmz.spacegate.current.v1",
            {"api_key": "redacted"},
            observation_end_ms=T,
            retrieved_at_ms=T,
        )
    assert key_err.value.code == "SECRET_FIELD_REJECTED"
    with pytest.raises(DataEngineError) as value_err:
        e.ingest(
            "fmz.spacegate.current.v1",
            {"header": "Bearer abcdefghijklmnop"},
            observation_end_ms=T,
            retrieved_at_ms=T,
        )
    assert value_err.value.code == "SECRET_VALUE_REJECTED"
    state_file = tmp_path / "state" / "data_engine_state_v23.json"
    assert not state_file.exists()


def test_d26_restart_preserves_current_record_and_freeze_evidence(tmp_path):
    first = engine(tmp_path)
    rec = first.ingest(
        "risk.astra.frozen_mu.reference.v1",
        {"mu_btc": 0.001, "model_hash": "a" * 64},
        observation_end_ms=T,
        retrieved_at_ms=T,
        scope={"candidate_id": "abc"},
    )
    restarted = engine(tmp_path)
    read = restarted.read("risk.astra.frozen_mu.reference.v1", now_ms=T + 1000, usage="underwriting_accounting", scope={"candidate_id": "abc"})
    manifest = restarted.freeze([read], cutoff_at_ms=T + 1000, candidate_id="abc")
    assert read["record_id"] == rec["record_id"]
    assert manifest["source_records"][0]["record_id"] == rec["record_id"]
    assert manifest["source_records"][0]["content"]["values"]["mu_btc"] == 0.001
