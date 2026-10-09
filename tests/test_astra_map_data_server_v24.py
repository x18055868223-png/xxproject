from pathlib import Path
import json

import pytest

from tools import astra_data_engine_v23 as data_engine
from tools import astra_map_data_server_v24 as server
from tools import astra_map_projection_v24 as projection
from tools import astra_map_sources_v24 as sources
from tools import astra_underwriting_fact_context_v20 as facts

T = 1_790_400_000_000


def config(tmp_path):
    return {"state_dir": str(tmp_path / "state"), "snapshots_jsonl": str(tmp_path / "absent.jsonl"),
            "kpf_manifest_path": str(tmp_path / "kpf/current.json"), "min_free_bytes": 0,
            "sources_enabled": True, "index_enabled": False}


FMZ_PRODUCTS = {
    "fmz.bridge.snapshots.v1",
    "fmz.spacegate.current.v1",
    "gex.netgamma.proxy.v1",
    "gex.effective.accepted.v1",
    "macro.risk_state.components.v1",
}


def write_live_snapshot(path: Path, *, ts: int = T - 1000) -> None:
    row = facts._synthetic_preview_snapshot(ts)
    row["decision"]["runtime_facts"]["runtime_mode"] = "live_public_read_only"
    row["decision"]["runtime_facts"]["current_price_source"] = "test_deribit_index"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def background_capture(engine, now_ms, *, transport=None, config=None):
    rows = []
    for product in config["products"]:
        scope = sources._scope_for(product, config)
        engine.ingest(product, {"test": product}, observation_end_ms=now_ms - 10_000,
                      retrieved_at_ms=None, ingested_at_ms=now_ms, scope=scope, data_state="OK")
        rows.append(engine.read(product, now_ms=now_ms, usage="background", scope=scope))
    return rows


def product_ids(manifest):
    return [row["identity"]["product_id"] for row in manifest["source_records"]]


def assert_no_record_identity_conflict(records):
    seen = {}
    for row in records:
        key = (row["identity"]["product_id"], json.dumps(row["identity"].get("scope"), sort_keys=True))
        fingerprint = json.dumps(row, ensure_ascii=False, sort_keys=True)
        assert key not in seen or seen[key] == fingerprint
        seen[key] = fingerprint


def test_rotating_capture_still_consumes_all_cached_products_and_is_readonly(tmp_path, monkeypatch):
    calls=[]
    def capture(engine, now_ms, *, transport=None, config=None):
        calls.append(config['products'])
        rows=[]
        for product in config['products']:
            scope=sources._scope_for(product, config)
            values={"test": product}
            engine.ingest(product, values, observation_end_ms=now_ms-10_000, retrieved_at_ms=None, ingested_at_ms=now_ms,
                          scope=scope, data_state="OK")
            rows.append(engine.read(product, now_ms=now_ms, usage="background", scope=scope))
        return rows
    monkeypatch.setattr(sources, 'collect_sources', capture)
    cfg=config(tmp_path)
    for index in range(3):
        result=server.collect(cfg, now_ms=T+index*300_000)
        assert calls[index]==server.FAST+server.SLOW[index]
        assert result['llm_http_calls']==result['trading_calls']==0
        assert result['completeness']['complete'] is False
        assert result['fmz']['status']!='current'
    frozen={row['identity']['product_id'] for row in result['freeze_manifest']['source_records']}
    assert sources.ETF_FLOW in frozen and sources.BRK_COST in frozen and sources.BRK_PNL in frozen
    assert (Path(cfg['state_dir'])/'previous_map.json').exists()
    assert json.loads((Path(cfg['state_dir'])/'current_map.json').read_text())['next_slow_group']==0


def test_collect_publishes_single_manifest_with_fmz_records_readable_by_consumer(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'collect_sources', background_capture)
    cfg = config(tmp_path)
    write_live_snapshot(Path(cfg["snapshots_jsonl"]))

    result = server.collect(cfg, now_ms=T)
    current_path = Path(cfg["state_dir"]) / "current_map.json"
    frozen = result["freeze_manifest"]
    inner = result["btc_map"]["source_manifest"]

    assert frozen == inner
    assert set(product_ids(frozen)).issuperset(FMZ_PRODUCTS)
    assert_no_record_identity_conflict(frozen["source_records"])
    assert result["completeness"]["groups"]["持续事实"]["complete"] is True
    assert result["llm_http_calls"] == result["trading_calls"] == 0

    qualifier = data_engine.DataEngine(tmp_path / "qualifier")
    consumer = projection.load_producer_source_engine(
        current_path,
        now_ms=result["generated_at_ms"],
        qualifier_engine=qualifier,
    )
    summary = consumer.source_summary(result["generated_at_ms"])
    assert summary["status"] == "OK"
    assert summary["legacy_exact_duplicate_manifest"] is False
    assert summary["strict_subset_manifest_compatibility"] is None


def test_consecutive_collect_keeps_outer_and_inner_manifest_identical(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'collect_sources', background_capture)
    cfg = config(tmp_path)
    snapshot_path = Path(cfg["snapshots_jsonl"])
    write_live_snapshot(snapshot_path, ts=T - 1000)
    first = server.collect(cfg, now_ms=T)

    write_live_snapshot(snapshot_path, ts=T + 300_000 - 1000)
    second = server.collect(cfg, now_ms=T + 300_000)
    current_path = Path(cfg["state_dir"]) / "current_map.json"

    assert first["freeze_manifest"] == first["btc_map"]["source_manifest"]
    assert second["freeze_manifest"] == second["btc_map"]["source_manifest"]
    assert set(product_ids(second["freeze_manifest"])).issuperset(FMZ_PRODUCTS)
    assert_no_record_identity_conflict(second["freeze_manifest"]["source_records"])
    qualifier = data_engine.DataEngine(tmp_path / "qualifier-consecutive")
    consumer = projection.load_producer_source_engine(
        current_path,
        now_ms=second["generated_at_ms"],
        qualifier_engine=qualifier,
    )
    summary = consumer.source_summary(second["generated_at_ms"])
    assert summary["legacy_exact_duplicate_manifest"] is False
    assert summary["strict_subset_manifest_compatibility"] is None


def test_fresh_cutoff_does_not_predate_actual_availability(tmp_path, monkeypatch):
    def capture(engine, now_ms, **kwargs):
        product=sources.OI_NATIVE
        scope=sources._scope_for(product,{})
        engine.ingest(product, {"open_interest_native_btc": 1}, observation_end_ms=now_ms-1000, retrieved_at_ms=None,
                      ingested_at_ms=now_ms+5000, scope=scope, data_state="OK")
        return [engine.read(product, now_ms=now_ms+5000, usage="background",scope=scope)]
    monkeypatch.setattr(sources, 'collect_sources', capture)
    result=server.collect(config(tmp_path),now_ms=T)
    assert result['generated_at_ms']==T+5000
    assert any(row['identity']['product_id']==sources.OI_NATIVE for row in result['freeze_manifest']['source_records'])
    assert not any(row['reason']=='first_seen_after_cutoff' for row in result['freeze_manifest']['excluded_records'])


def test_disk_guard_preserves_completed_snapshot(tmp_path):
    cfg=config(tmp_path)
    cfg['sources_enabled']=False
    result=server.collect(cfg,now_ms=T)
    raw=(Path(cfg['state_dir'])/'current_map.json').read_bytes()
    cfg['min_free_bytes']=2**64
    with pytest.raises(ValueError,match='MAP_DISK_FREE_GUARD'):
        server.collect(cfg,now_ms=T+300_000)
    assert (Path(cfg['state_dir'])/'current_map.json').read_bytes()==raw
    assert result['kpf']['gaps']


def test_published_state_has_bounded_size_and_no_raw_trades(tmp_path):
    cfg=config(tmp_path)
    cfg['sources_enabled']=False
    server.collect(cfg,now_ms=T)
    state=Path(cfg['state_dir'])
    assert (state/'current_map.json').stat().st_size<server.MAX_STATE_BYTES
    assert not list(state.rglob('*.csv'))
    with pytest.raises(ValueError,match='MAP_STATE_TOO_LARGE'):
        server.write_atomic(state/'large.json',{'data':'x'*server.MAX_STATE_BYTES})
    assert not (state/'large.json').exists()


def test_independent_index_can_anchor_map_without_creating_fmz_facts(tmp_path,monkeypatch):
    monkeypatch.setattr(sources,'collect_sources',lambda *args,**kwargs: [])
    calls=[]
    def get_json(url,params,**kwargs):
        calls.append((url,params))
        return {'result':{'index_price':85000.0,'timestamp':T-1000}}
    monkeypatch.setattr(sources,'_get_json',get_json)
    cfg=config(tmp_path)
    cfg['index_enabled']=True
    result=server.collect(cfg,now_ms=T)
    assert len(calls)==1
    assert result['current_market']['can_use'] is True
    assert result['current_market']['price_usd']==85000
    assert result['fmz']['status']!='current'
    assert result['completeness']['groups']['持续事实']['complete'] is False
    assert result['completeness']['complete'] is False
    stale=server.collect({**cfg,'sources_enabled':False},now_ms=T+300001)
    assert stale['current_market']['can_use'] is False
