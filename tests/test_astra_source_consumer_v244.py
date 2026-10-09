import json
from copy import deepcopy
from pathlib import Path

import pytest

from tests import test_astra_workbench_v20 as fx
from tools import astra_data_engine_v23 as data_engine
from tools import astra_map_projection_v24 as map_projection
from tools import astra_map_sources_v24 as map_sources
from tools import astra_map_v24 as map_core
from tools import astra_workbench_v20 as workbench


PRODUCTS = [
    map_sources.ETF_FLOW,
    map_sources.BRK_COST,
    map_sources.BRK_PNL,
    map_sources.OI_NATIVE,
    map_sources.FUNDING_SETTLED,
    map_sources.BORROW_RATE,
    map_sources.MACRO_USD_BROAD,
    map_sources.MACRO_US10Y_NOMINAL,
    map_sources.MACRO_US10Y_REAL,
]


def _seed(engine, product, values, *, now_ms, config, obs_ms=None, data_state="OK", scope="AUTO"):
    scope = map_sources._scope_for(product, config or {}) if scope == "AUTO" else scope
    obs = now_ms - 10_000 if obs_ms is None else obs_ms
    engine.ingest(
        product,
        values,
        observation_end_ms=obs,
        retrieved_at_ms=None,
        ingested_at_ms=now_ms,
        source_revision=f"{product}-{obs}",
        data_state=data_state,
        scope=scope,
    )


def _seed_background(engine, *, now_ms, config, complete=True):
    _seed(
        engine,
        "gex.effective.accepted.v1",
        {"effective_flip_point": 99000.0, "ready": True, "gex_source_ts_ms": now_ms - 10_000},
        now_ms=now_ms,
        config=config,
        scope=None,
    )
    obs_rows = [{"date": f"2026-09-{day:02d}", "value": 100.0 + day} for day in range(1, 23)]
    _seed(engine, map_sources.ETF_FLOW, {"metrics": {"summary_cn": "ETF配置为净流入。", "flow_3d_usd_mn": 120.0}}, now_ms=now_ms, config=config)
    if not complete:
        return
    _seed(engine, map_sources.BRK_COST, {"cp_sth_usd": 88000.0}, now_ms=now_ms, config=config)
    _seed(engine, map_sources.BRK_PNL, {"metrics": {"loss_share_14d": 0.22}}, now_ms=now_ms, config=config)
    _seed(engine, map_sources.OI_NATIVE, {"open_interest_native_btc": 12345.0}, now_ms=now_ms, config=config)
    _seed(engine, map_sources.FUNDING_SETTLED, {"metrics": {"settled_funding_rate_sum": 0.0012}}, now_ms=now_ms, config=config)
    _seed(engine, map_sources.BORROW_RATE, {"rate": {"simple_apr_pct": 5.2}}, now_ms=now_ms, config=config)
    _seed(engine, map_sources.MACRO_USD_BROAD, {"observations": obs_rows}, now_ms=now_ms, config=config)
    _seed(
        engine,
        map_sources.MACRO_US10Y_NOMINAL,
        {"observations": [{**row, "value": 4.0 + index * 0.01} for index, row in enumerate(obs_rows)]},
        now_ms=now_ms,
        config=config,
    )
    _seed(
        engine,
        map_sources.MACRO_US10Y_REAL,
        {"observations": [{**row, "value": 1.7 + index * 0.01} for index, row in enumerate(obs_rows)]},
        now_ms=now_ms,
        config=config,
    )


def _write_producer_current(tmp_path, *, now_ms=fx.T, complete=True, mutate=None):
    producer_state = tmp_path / "producer"
    engine = data_engine.DataEngine(producer_state / "data_engine")
    cfg = {"products": PRODUCTS}
    _seed_background(engine, now_ms=now_ms, config=cfg, complete=complete)
    fmz_fact = {"status": "current", "current_price_usd": 100000.0, "current_price_source": "deribit_index", "price_observed_ms": now_ms - 1_000}
    records = map_projection.read_cached_sources(engine, now_ms, cfg)
    mapping = map_projection.build_projection(engine, now_ms=now_ms, fmz_fact=fmz_fact, source_records=records, config=cfg, prior_state={})
    gex = engine.read("gex.effective.accepted.v1", now_ms=now_ms, usage="space_gate")
    current = {
        "schema": map_projection.PRODUCER_CURRENT_SCHEMA,
        "generated_at_ms": now_ms,
        "freeze_manifest": engine.freeze(records + [gex], cutoff_at_ms=now_ms),
        "btc_map": mapping["btc_map"],
        "projection_state": map_projection.persistable_state(mapping["btc_map"], {}),
        "llm_http_calls": 0,
        "trading_calls": 0,
    }
    if mutate is not None:
        mutate(current)
    path = tmp_path / "current_map.json"
    path.write_text(json.dumps(current, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path, current


def _make_env_workbench(tmp_path, monkeypatch, current_path, *, now_ms=fx.T, sources_enabled="0", max_age_ms=600000):
    snapshots = tmp_path / "consumer" / "snapshots.jsonl"
    fx.write_fmz(snapshots, fx.native_gate(ts=now_ms - 1000), now=now_ms)
    usage_ledger = tmp_path / "consumer" / "usage.json"
    usage_ledger.write_text(json.dumps({"date_bjt": "2026-09-25", "http_calls_used": 0}), encoding="utf-8")
    monkeypatch.setenv("ASTRA_WORKBENCH_SNAPSHOTS_JSONL", str(snapshots))
    monkeypatch.setenv("ASTRA_WORKBENCH_STATE_DIR", str(tmp_path / "consumer" / "state"))
    monkeypatch.setenv("ASTRA_WORKBENCH_FRONTEND_DIR", str(tmp_path))
    monkeypatch.setenv("ASTRA_WORKBENCH_TOKEN", "secret")
    monkeypatch.setenv("ASTRA_WORKBENCH_LLM_API_KEY", "key")
    monkeypatch.setenv("ASTRA_WORKBENCH_USAGE_LEDGER", str(usage_ledger))
    monkeypatch.setenv("ASTRA_MAP_SOURCE_MODE", "producer_manifest")
    monkeypatch.setenv("ASTRA_MAP_PRODUCER_CURRENT_PATH", str(current_path))
    monkeypatch.setenv("ASTRA_MAP_PRODUCER_MAX_AGE_MS", str(max_age_ms))
    monkeypatch.setenv("ASTRA_MAP_SOURCES_ENABLED", sources_enabled)
    cfg = workbench.config_from_env()
    return workbench.AstraWorkbench(
        cfg,
        discovery_capture=fx.fake_discovery,
        quote_capture=fx.fake_quote_capture,
        risk_provider=fx.positive_risk,
        clock_ms=lambda: now_ms,
    )


def test_standard_config_consumes_producer_manifest_without_slow_http_or_shared_state(tmp_path, monkeypatch):
    current_path, producer = _write_producer_current(tmp_path)
    slow_calls = []
    monkeypatch.setattr(map_projection.map_sources, "collect_sources", lambda *a, **k: slow_calls.append(1))
    wb = _make_env_workbench(tmp_path, monkeypatch, current_path)

    view = wb.refresh(now_ms=fx.T, record_daily=False)
    cached = wb.cached_view()

    consumer = view["btc_map"]["source_consumer"]
    assert consumer["status"] == "OK"
    assert consumer["producer_manifest_hash"] == producer["freeze_manifest"]["manifest_hash"]
    assert consumer["required_background_complete"] is True
    assert view["btc_map"]["background"]["allocation"]["fact_state"] == "usable"
    assert cached["btc_map_current_usage"]["status"] == "UNCHANGED"
    assert slow_calls == []
    private_state = json.loads((wb.config.state_dir / "data_engine" / data_engine.STATE_FILE).read_text(encoding="utf-8"))
    private_products = {key.split("|", 1)[0] for key in private_state.get("current", {})}
    assert map_sources.ETF_FLOW not in private_products
    assert map_sources.BRK_COST not in private_products


def test_consumer_mode_rejects_slow_collection_switch_conflict(tmp_path, monkeypatch):
    current_path, _producer = _write_producer_current(tmp_path)
    slow_calls = []
    monkeypatch.setattr(map_projection.map_sources, "collect_sources", lambda *a, **k: slow_calls.append(1))
    wb = _make_env_workbench(tmp_path, monkeypatch, current_path, sources_enabled="1")

    with pytest.raises(map_projection.SourceConsumerError) as err:
        wb.refresh(now_ms=fx.T, record_daily=False)

    assert err.value.code == "PRODUCER_SOURCE_SLOW_COLLECTION_CONFLICT"
    assert slow_calls == []


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda body: body["freeze_manifest"].update(source_records=[]), "PRODUCER_FREEZE_HASH_MISMATCH"),
        (lambda body: body.update(generated_at_ms=fx.T - 600_001), "PRODUCER_CURRENT_STALE"),
        (lambda body: _future_cutoff(body), "PRODUCER_FREEZE_CUTOFF_IN_FUTURE"),
        (lambda body: _conflicting_duplicate_record_id(body), "PRODUCER_SOURCE_RECORD_CONFLICT"),
        (lambda body: _conflicting_duplicate_product_scope(body), "PRODUCER_SOURCE_RECORD_CONFLICT"),
        (lambda body: _nested_source_manifest_missing_hash(body), "PRODUCER_BTC_MAP_SOURCE_HASH_MISSING"),
        (lambda body: _nested_source_manifest_cutoff_conflict(body), "PRODUCER_BTC_MAP_SOURCE_MANIFEST_MISMATCH"),
        (lambda body: _nested_source_manifest_record_conflict(body), "PRODUCER_BTC_MAP_SOURCE_MANIFEST_MISMATCH"),
    ],
)
def test_producer_manifest_integrity_failures_do_not_fallback_or_drop_contract_accounting(tmp_path, monkeypatch, mutate, expected_code):
    current_path, _producer = _write_producer_current(tmp_path, mutate=mutate)
    slow_calls = []
    monkeypatch.setattr(map_projection.map_sources, "collect_sources", lambda *a, **k: slow_calls.append(1))
    wb = _make_env_workbench(tmp_path, monkeypatch, current_path)

    view = wb.refresh(now_ms=fx.T, record_daily=False)

    assert slow_calls == []
    assert view["btc_map"]["source_consumer"]["status"] == "ERROR"
    assert view["btc_map"]["source_consumer"]["error"]["code"] == expected_code
    assert view["btc_map"]["presentation"]["mode"] == "LIVE"
    assert view["llm_call_count"] == 0
    assert "producer_map_source_unqualified" in view["gaps"]
    for item in view["candidates"]:
        assert item["status"] == "snapshot"
        assert item["snapshot"]["liability"]["payout_table"]
        assert "producer_map_source_unqualified" not in item["review_gate"]["reasons"]


def test_missing_producer_file_reports_source_gap_without_slow_fallback(tmp_path, monkeypatch):
    current_path = tmp_path / "missing_current_map.json"
    slow_calls = []
    monkeypatch.setattr(map_projection.map_sources, "collect_sources", lambda *a, **k: slow_calls.append(1))
    wb = _make_env_workbench(tmp_path, monkeypatch, current_path)

    view = wb.refresh(now_ms=fx.T, record_daily=False)

    assert slow_calls == []
    assert view["btc_map"]["source_consumer"]["error"]["code"] == "PRODUCER_CURRENT_MISSING"
    assert view["candidates"][0]["snapshot"]["economics"]["mu_ref_btc"] == 0.001
    assert view["candidates"][0]["review_gate"]["enabled"] is True
    cached = wb.cached_view()
    assert cached["btc_map_current_usage"]["status"] == "ERROR"
    assert cached["btc_map_current_usage"]["error"]["code"] == "PRODUCER_CURRENT_MISSING"


def test_producer_current_file_size_limit_fails_before_json_parse(tmp_path):
    current_path = tmp_path / "current_map.json"
    current_path.write_text("{" + (" " * (map_projection.MAX_PRODUCER_CURRENT_BYTES + 1)), encoding="utf-8")
    qualifier = data_engine.DataEngine(tmp_path / "qualifier")

    with pytest.raises(map_projection.SourceConsumerError) as err:
        map_projection.load_producer_source_engine(
            current_path,
            now_ms=fx.T,
            qualifier_engine=qualifier,
        )

    assert err.value.code == "PRODUCER_CURRENT_TOO_LARGE"


def test_producer_health_reuses_data_engine_health_shape(tmp_path):
    current_path, _producer = _write_producer_current(tmp_path)
    qualifier = data_engine.DataEngine(tmp_path / "qualifier")

    engine = map_projection.load_producer_source_engine(current_path, now_ms=fx.T, qualifier_engine=qualifier)
    health = engine.health(fx.T)

    product = health["products"][map_sources.ETF_FLOW]
    assert product["provider_id"]
    assert product["current"][0]["record_id"]
    assert "usage_decisions" in product["current"][0]
    assert product["current"][0]["usage_decision"]["status"] == "USABLE"


def test_saved_real_capture_consumes_at_original_clock(tmp_path):
    capture = Path(__file__).parent / "fixtures/wpem_rc/astra-map-v242-20260926/server_current_map_capture.json"
    if not capture.exists():
        pytest.skip("saved real producer capture is not present in this checkout")
    payload = json.loads(capture.read_text(encoding="utf-8-sig"))
    qualifier = data_engine.DataEngine(tmp_path / "qualifier")

    engine = map_projection.load_producer_source_engine(
        capture,
        now_ms=payload["generated_at_ms"],
        qualifier_engine=qualifier,
    )

    assert engine.source_summary(payload["generated_at_ms"])["status"] == "OK"
    assert engine.manifest_hash == payload["freeze_manifest"]["manifest_hash"]
    assert map_projection.read_cached_sources(engine, payload["generated_at_ms"], {"products": PRODUCTS})


def test_partial_producer_background_stays_visible_without_blocking_review_gate(tmp_path, monkeypatch):
    current_path, _producer = _write_producer_current(tmp_path, complete=False)
    wb = _make_env_workbench(tmp_path, monkeypatch, current_path)

    view = wb.refresh(now_ms=fx.T, record_daily=False)

    assert view["btc_map"]["source_consumer"]["required_background_complete"] is False
    assert view["btc_map"]["background"]["inventory"]["fact_state"] != "usable"
    assert "producer_map_background_incomplete" in view["gaps"]
    assert all("producer_map_background_incomplete" not in item["review_gate"]["reasons"] for item in view["candidates"])
    assert all(item["review_gate"]["enabled"] is True for item in view["candidates"])


def test_native_space_gate_still_hard_blocks_when_optional_background_missing(tmp_path, monkeypatch):
    current_path, _producer = _write_producer_current(tmp_path, complete=False)
    wb = _make_env_workbench(tmp_path, monkeypatch, current_path)
    fx.write_fmz(wb.config.snapshots_jsonl, fx.native_gate("STRUCTURE_BLOCKED"))

    view = wb.refresh(now_ms=fx.T, record_daily=False)

    assert "producer_map_background_incomplete" in view["gaps"]
    for item in view["candidates"]:
        assert item["review_gate"]["enabled"] is False
        assert "structure_gate_not_eligible" in item["review_gate"]["reasons"]
        assert "producer_map_background_incomplete" not in item["review_gate"]["reasons"]


def test_legacy_status_is_normalized_to_fact_state_at_map_boundary():
    mapping = map_core.build_map(
        None,
        now_ms=fx.T,
        background={
            "allocation": {
                "status": "OK",
                "summary_cn": "旧status输入。",
                "metrics": {},
                "missing": [],
            }
        },
    )

    row = mapping["background"]["allocation"]
    assert row["fact_state"] == "usable"
    assert row["status"] == "OK"


def _future_cutoff(body):
    manifest = deepcopy(body["freeze_manifest"])
    manifest["cutoff_at_ms"] = fx.T + 10_000
    manifest["manifest_hash"] = data_engine._hash({key: value for key, value in manifest.items() if key != "manifest_hash"})
    body["freeze_manifest"] = manifest


def _conflicting_duplicate_record_id(body):
    manifest = deepcopy(body["freeze_manifest"])
    row = deepcopy(manifest["source_records"][0])
    row["content"]["values"]["duplicate_probe"] = "changed"
    manifest["source_records"].append(row)
    manifest["manifest_hash"] = data_engine._hash({key: value for key, value in manifest.items() if key != "manifest_hash"})
    body["freeze_manifest"] = manifest
    body["btc_map"]["source_manifest"] = deepcopy(manifest)


def _conflicting_duplicate_product_scope(body):
    manifest = deepcopy(body["freeze_manifest"])
    row = deepcopy(manifest["source_records"][0])
    row["record_id"] = str(row["record_id"]) + ":shadow"
    row["content"]["values"]["duplicate_probe"] = "shadow"
    manifest["source_records"].append(row)
    manifest["manifest_hash"] = data_engine._hash({key: value for key, value in manifest.items() if key != "manifest_hash"})
    body["freeze_manifest"] = manifest
    body["btc_map"]["source_manifest"] = deepcopy(manifest)


def _nested_source_manifest_missing_hash(body):
    btc_map = deepcopy(body["btc_map"])
    source_manifest = deepcopy(btc_map["source_manifest"])
    source_manifest.pop("manifest_hash", None)
    btc_map["source_manifest"] = source_manifest
    body["btc_map"] = btc_map


def _nested_source_manifest_cutoff_conflict(body):
    btc_map = deepcopy(body["btc_map"])
    source_manifest = deepcopy(btc_map["source_manifest"])
    source_manifest["cutoff_at_ms"] = fx.T - 1
    source_manifest["manifest_hash"] = data_engine._hash({key: value for key, value in source_manifest.items() if key != "manifest_hash"})
    btc_map["source_manifest"] = source_manifest
    body["btc_map"] = btc_map


def _nested_source_manifest_record_conflict(body):
    btc_map = deepcopy(body["btc_map"])
    source_manifest = deepcopy(btc_map["source_manifest"])
    source_manifest["source_records"] = source_manifest["source_records"][:-1]
    source_manifest["manifest_hash"] = data_engine._hash({key: value for key, value in source_manifest.items() if key != "manifest_hash"})
    btc_map["source_manifest"] = source_manifest
    body["btc_map"] = btc_map
