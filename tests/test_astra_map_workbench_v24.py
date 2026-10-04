import json
from copy import deepcopy
import threading

from tests import test_astra_workbench_v20 as fx
from tools import astra_map_projection_v24 as map_projection
from tools import astra_map_sources_v24 as map_sources
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


def _configure_map(wb, capture, *, products=None):
    wb.config.map_sources_enabled = True
    wb.config.map_source_config = {"products": list(products or PRODUCTS)}
    wb.map_source_capture = capture


def _seed_product(engine, product, values, *, now_ms, config, obs_ms=None, data_state="OK", scope="AUTO"):
    if scope == "AUTO":
        scope = map_sources._scope_for(product, config or {})
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


def seed_complete_sources(calls):
    def capture(*, engine, now_ms, config):
        calls.append(now_ms)
        obs_rows = [{"date": f"2026-09-{day:02d}", "value": 100.0 + day} for day in range(1, 23)]
        _seed_product(
            engine,
            "gex.effective.accepted.v1",
            {"effective_flip_point": 99000.0, "ready": True, "gex_source_ts_ms": now_ms - 10_000},
            now_ms=now_ms,
            config=config,
            scope=None,
        )
        _seed_product(engine, map_sources.ETF_FLOW,
                      {"metrics": {"summary_cn": "ETF配置为净流入。", "flow_3d_usd_mn": 120.0}},
                      now_ms=now_ms, config=config)
        _seed_product(engine, map_sources.BRK_COST,
                      {"cp_sth_usd": 88000.0,
                       "series": {"cp_sth": {"points": [{"date": "2026-09-24", "value": 88000.0}]}}},
                      now_ms=now_ms, config=config)
        _seed_product(engine, map_sources.BRK_PNL,
                      {"metrics": {"loss_share_14d": 0.22}}, now_ms=now_ms, config=config)
        _seed_product(engine, map_sources.OI_NATIVE,
                      {"open_interest_native_btc": 12345.0}, now_ms=now_ms, config=config)
        _seed_product(engine, map_sources.FUNDING_SETTLED,
                      {"metrics": {"settled_funding_rate_sum": 0.0012}}, now_ms=now_ms, config=config)
        _seed_product(engine, map_sources.BORROW_RATE,
                      {"rate": {"simple_apr_pct": 5.2}}, now_ms=now_ms, config=config)
        _seed_product(engine, map_sources.MACRO_USD_BROAD,
                      {"observations": obs_rows}, now_ms=now_ms, config=config)
        _seed_product(engine, map_sources.MACRO_US10Y_NOMINAL,
                      {"observations": [{**row, "value": 4.0 + index * 0.01}
                                        for index, row in enumerate(obs_rows)]},
                      now_ms=now_ms, config=config)
        _seed_product(engine, map_sources.MACRO_US10Y_REAL,
                      {"observations": [{**row, "value": 1.7 + index * 0.01}
                                        for index, row in enumerate(obs_rows)]},
                      now_ms=now_ms, config=config)
    return capture


def _candidate_maps(view):
    return [item["snapshot"]["data_identity"]["btc_map"] for item in view["candidates"]]


def _source_values(manifest, product_id):
    for record in manifest.get("source_records") or []:
        identity = record.get("identity") or {}
        if identity.get("product_id") == product_id:
            return (record.get("content") or {}).get("values") or {}
    return None


def _source_record(manifest, product_id):
    for record in manifest.get("source_records") or []:
        identity = record.get("identity") or {}
        if identity.get("product_id") == product_id:
            return record
    return None


def test_refresh_builds_global_and_candidate_maps_from_cached_sources_without_llm(tmp_path):
    calls = []
    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk)
    _configure_map(wb, seed_complete_sources(calls))
    fx.write_fmz(snapshots, fx.native_gate())

    view = wb.refresh(now_ms=fx.T, record_daily=False)

    assert calls == [fx.T]
    assert view["llm_call_count"] == 0
    assert view["space_gate"]["state"] == "STRUCTURE_ELIGIBLE"
    assert view["btc_map"]["schema"] == "btc_map@1"
    assert view["btc_map"]["policy_link"]["candidate_id"] is None
    assert view["btc_map"]["automation"] == {"llm_calls": 0, "http_fetches": 0, "execution_permission_changed": False}
    assert {"allocation", "inventory", "financing", "external_conditions"}.issubset(view["btc_map"]["background"])
    for name in ("allocation", "inventory", "financing", "external_conditions"):
        row = view["btc_map"]["background"][name]
        assert row["fact_state"] == "usable"
        assert isinstance(row["metrics"], dict) and row["metrics"]
    assert _source_values(view["btc_map"]["source_manifest"], map_sources.BRK_COST)["cp_sth_usd"] == 88000.0

    maps = _candidate_maps(view)
    assert len({m["map_id"] for m in maps}) == 2
    assert {m["policy_link"]["contract"]["side"] for m in maps} == {"put", "call"}
    for item, mapping in zip(view["candidates"], maps):
        snapshot = item["snapshot"]
        assert snapshot["execution_allowed"] is False
        assert snapshot["economics"]["premium_spread_btc"] == snapshot["economics"]["visible_credit_btc"]
        assert mapping["source_manifest"]["candidate_id"] == snapshot["candidate_id"]
        assert mapping["policy_link"]["candidate_id"] == snapshot["candidate_id"]
        assert mapping["policy_link"]["valuation_version"] != snapshot["snapshot_id"]
        assert len(mapping["policy_link"]["region_liability_rows"]) >= 1
        assert {row["region_id"] for row in mapping["policy_link"]["region_liability_rows"]}.issuperset(
            {"gex-effective-flip", "brk-cp-sth"}
        )
        assert _source_values(mapping["source_manifest"], "gex.effective.accepted.v1")["effective_flip_point"] == 99000.0

    cached = wb.cached_view()
    assert cached["btc_map"]["map_id"] == view["btc_map"]["map_id"]
    assert calls == [fx.T]


def test_manual_review_reuses_same_projection_and_request_references_map(tmp_path):
    source_calls = []
    llm_requests = []

    def transport(api_key, model, request, timeout, endpoint=None):
        llm_requests.append(request)
        content = request["messages"][1]["content"]
        assert "map.policy" in content
        assert "map.background.inventory" in content
        assert "map.reference.gex-effective-flip" in content
        packet = json.loads(content.split("冻结候选：", 1)[1])
        snapshot = wb.ledger.state()["snapshots"][packet["snapshot_id"]]["snapshot"]
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(fx.good_review(snapshot), ensure_ascii=False)}}]}

    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk, transport=transport)
    _configure_map(wb, seed_complete_sources(source_calls))
    fx.write_fmz(snapshots, fx.native_gate())
    view = wb.refresh(now_ms=fx.T, record_daily=False)
    candidate_id = view["candidates"][0]["snapshot"]["candidate_id"]

    result = wb.review_candidate(candidate_id, "map-review-1", authorization="Bearer secret")

    assert result["status"] == "OK"
    assert result["llm_call_count"] == 1
    assert len(llm_requests) == 1
    assert source_calls == [fx.T]
    reviewed = wb.ledger.state()["snapshots"][result["snapshot_id"]]["snapshot"]
    reviewed_map = reviewed["data_identity"]["btc_map"]
    initial_map = view["candidates"][0]["snapshot"]["data_identity"]["btc_map"]
    assert reviewed_map["policy_link"]["candidate_id"] == candidate_id
    assert reviewed_map["source_manifest"]["candidate_id"] == candidate_id
    assert [row["record_id"] for row in reviewed_map["source_manifest"]["source_records"]] == [
        row["record_id"] for row in initial_map["source_manifest"]["source_records"]
    ]


def test_failed_space_gate_keeps_review_and_execution_disabled_even_with_map(tmp_path):
    calls = []
    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk)
    _configure_map(wb, seed_complete_sources(calls))
    fx.write_fmz(snapshots, fx.native_gate("STRUCTURE_BLOCKED"))

    view = wb.refresh(now_ms=fx.T, record_daily=False)

    assert calls == [fx.T]
    assert view["space_gate"]["eligible"] is False
    assert view["btc_map"]["display_reference_ids"]
    for item in view["candidates"]:
        snapshot = item["snapshot"]
        assert snapshot["data_identity"]["btc_map"]["policy_link"]["region_liability_rows"]
        assert snapshot["execution_allowed"] is False
        assert item["review_gate"]["enabled"] is False
        assert "structure_gate_not_eligible" in item["review_gate"]["reasons"]


def test_stale_source_degrades_without_promoting_cp_reference(tmp_path):
    calls = []

    def capture(*, engine, now_ms, config):
        calls.append(now_ms)
        _seed_product(
            engine,
            "gex.effective.accepted.v1",
            {"effective_flip_point": 99000.0, "ready": True, "gex_source_ts_ms": now_ms - 10_000},
            now_ms=now_ms,
            config=config,
            scope=None,
        )
        stale = now_ms - 172_800_001
        _seed_product(engine, map_sources.BRK_COST, {"cp_sth_usd": 88000.0},
                      now_ms=now_ms, config=config, obs_ms=stale)

    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk)
    _configure_map(wb, capture, products=[map_sources.BRK_COST])
    fx.write_fmz(snapshots, fx.native_gate())

    view = wb.refresh(now_ms=fx.T, record_daily=False)

    assert calls == [fx.T]
    assert "cp_sth_unavailable" in view["gaps"]
    assert {ref["reference_id"] for ref in view["btc_map"]["reference_pool"]} == {"gex-effective-flip"}
    for mapping in _candidate_maps(view):
        row_ids = {row["region_id"] for row in mapping["policy_link"]["region_liability_rows"]}
        assert row_ids == {"gex-effective-flip"}
        stale_record = _source_record(mapping["source_manifest"], map_sources.BRK_COST)
        assert (stale_record["content"]["values"] or {})["cp_sth_usd"] == 88000.0
        assert stale_record["usage_qualification"]["can_use"] is False


def test_after_cutoff_record_does_not_become_reference(tmp_path):
    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk)
    fx.write_fmz(snapshots, fx.native_gate())
    future_seen = fx.T + 600_001
    wb.data_engine.ingest(
        "gex.effective.accepted.v1",
        {"effective_flip_point": 99000.0, "ready": True, "gex_source_ts_ms": fx.T - 1000},
        observation_end_ms=fx.T - 1000,
        retrieved_at_ms=None,
        ingested_at_ms=future_seen,
        source_revision="future-gex",
    )
    fact = wb.latest_fact(fx.T)

    projection = map_projection.build_projection(
        wb.data_engine,
        now_ms=fx.T,
        fmz_fact=fact,
        source_records=[],
        config={},
        prior_state={},
    )

    assert "gex_effective_flip_unavailable" in projection["gaps"]
    assert projection["references"] == []
    assert projection["btc_map"]["display_reference_ids"] == []


def test_daily_snapshot_freezes_the_same_map_objects(tmp_path):
    calls = []
    daily_now = fx.T + 7 * 3_600_000
    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk, clock=lambda: daily_now)

    def fresh_discovery(*, now_ms):
        rows = fx.instruments()
        for row in rows:
            row["delta_observed_at_ms"] = now_ms - 1000
        return fx.discovery.discover(rows, 100000, now_ms, quantity_btc=1.0, width_usd=2000.0)

    wb.discovery_capture = fresh_discovery
    _configure_map(wb, seed_complete_sources(calls))
    fx.write_fmz(snapshots, fx.native_gate(ts=daily_now - 1000), now=daily_now)

    view = wb.refresh(now_ms=daily_now, record_daily=True)
    daily = view["daily_snapshots"][-1]

    assert calls == [daily_now]
    assert daily["btc_map"]["map_id"] == view["btc_map"]["map_id"]
    assert daily["btc_map_manifests"]
    assert {item["btc_map"]["map_id"] for item in daily["candidates"]} == {
        item["snapshot"]["data_identity"]["btc_map"]["map_id"] for item in view["candidates"]
    }


def test_projection_observations_do_not_relabel_binance_spot_as_deribit():
    refs = [{
        "reference_id": "kpf-zone",
        "revision_id": "v1",
        "family": "TRADED_ACCEPTANCE",
        "role": "KPF_BASIN",
        "geometry": "OBSERVED_ZONE",
        "raw_low": 99900.0,
        "raw_high": 100100.0,
        "market": "Deribit",
        "quote_currency": "USD",
        "price_basis": "DERIBIT_BTC_USD_INDEX",
        "quality": "OK",
        "usage_decision": {"can_use": True},
        "known_at_ms": fx.T,
        "active_from_ms": fx.T,
        "coordinate_qualification": "COMPARABLE",
    }]
    fmz_fact = {"current_price_usd": 100000.0, "current_price_source": "binance_spot", "price_observed_ms": fx.T}

    assert map_projection.build_observations(fmz_fact, refs) == []


def test_kpf_local_zone_without_explicit_basis_clock_or_usage_is_not_strict(tmp_path):
    snapshot_path = tmp_path / "kpf_snapshot.json"
    audit_path = tmp_path / "kpf_audit.json"
    debug_path = tmp_path / "kpf_debug.json"
    snapshot_path.write_text(json.dumps({"version": "kpf-v1"}), encoding="utf-8")
    audit_path.write_text(json.dumps({"version": "kpf-v1"}), encoding="utf-8")
    debug_path.write_text(json.dumps({
        "version": "kpf-v1",
        "zones": [{"region_id": "kpf-local", "raw_basin_low": 99900.0, "raw_basin_high": 100100.0}],
    }), encoding="utf-8")
    wb, _snapshots = fx.make_workbench(tmp_path / "wb")

    projection = map_projection.build_projection(
        wb.data_engine,
        now_ms=fx.T,
        fmz_fact={"current_price_usd": 100000.0, "current_price_source": "deribit_index", "price_observed_ms": fx.T},
        source_records=[],
        config={
            "kpf_snapshot_path": str(snapshot_path),
            "kpf_audit_path": str(audit_path),
            "kpf_debug_path": str(debug_path),
        },
        prior_state={},
    )

    # Projection is a reader: paths do not authorize implicit file collection.
    assert projection["btc_map"]["reference_pool"] == []
    assert projection["btc_map"]["display_reference_ids"] == []
    assert projection["btc_map"]["response_events"] == []
    assert "kpf_cached_record_unavailable" in projection["gaps"]


def test_local_kpf_collection_only_occurs_on_refresh(tmp_path, monkeypatch):
    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk)
    fx.write_fmz(snapshots, fx.native_gate())
    calls = []
    def collector(engine, now_ms, config):
        calls.append(now_ms)
        return {"record": None, "gaps": ["kpf_same_version_artifacts_missing"], "artifact_hashes": {}}
    monkeypatch.setattr(map_projection, "collect_kpf_artifacts", collector)
    wb.refresh(now_ms=fx.T, record_daily=False)
    assert calls == [fx.T]
    wb.cached_view()
    wb.cached_view()
    assert calls == [fx.T]


def test_i02_cooldown_source_failure_is_visible_without_http_or_new_review(tmp_path):
    clock = [fx.T]
    calls, llm_calls = [], []
    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk,
        clock=lambda: clock[0], transport=lambda *a, **k: llm_calls.append(1))
    _configure_map(wb, seed_complete_sources(calls))
    fx.write_fmz(snapshots, fx.native_gate())
    before = wb.refresh(now_ms=fx.T, record_daily=False)
    frozen = deepcopy(before["candidates"][0]["snapshot"]["data_identity"]["btc_map"])
    wb._locked_state_update(lambda state: state["manual_review"].update(last_started_ts_ms=fx.T))
    clock[0] = fx.T + 1
    _seed_product(wb.data_engine, map_sources.BRK_COST, {"cp_sth_usd": 88000}, now_ms=clock[0],
                  obs_ms=clock[0], config=wb.config.map_source_config, data_state="INVALID")
    view = wb.cached_view()
    assert view["manual_review_state"]["cooldown_remaining_ms"] > 0
    assert view["btc_map"]["background"]["inventory"]["fact_state"] != "usable"
    assert "brk-cp-sth" not in view["btc_map"]["display_reference_ids"]
    assert view["candidates"][0]["btc_map_current_usage"]["status"] == "REQUIRES_RECHECK"
    assert view["candidates"][0]["snapshot"]["data_identity"]["btc_map"] == frozen
    assert calls == [fx.T] and llm_calls == []
    clock[0] += 172_800_001
    stale = wb.cached_view()
    assert stale["btc_map"]["background"]["allocation"]["fact_state"] != "usable"
    assert calls == [fx.T] and llm_calls == []


def test_i03_async_receipt_keeps_old_map_when_current_quote_changes(tmp_path):
    entered, release = threading.Event(), threading.Event()
    clock, output, errors, requests = [fx.T], [], [], []

    def transport(api_key, model, request, timeout, endpoint=None):
        requests.append(request)
        packet = json.loads(request["messages"][1]["content"].split("冻结候选：", 1)[1])
        snap = wb.ledger.state()["snapshots"][packet["snapshot_id"]]["snapshot"]
        entered.set()
        assert release.wait(5)
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(fx.good_review(snap), ensure_ascii=False)}}]}

    calls = []
    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk,
                                    clock=lambda: clock[0], transport=transport)
    _configure_map(wb, seed_complete_sources(calls))
    fx.write_fmz(snapshots, fx.native_gate())
    before = wb.refresh(now_ms=fx.T, record_daily=False)
    old = before["candidates"][0]["snapshot"]
    def run():
        try:
            output.append(wb.review_candidate(old["candidate_id"], "map-async-once", authorization="Bearer secret"))
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        clock[0] = fx.T + 1000
        original_capture = wb.quote_capture
        def changed_capture(*args, **kwargs):
            payload = original_capture(*args, **kwargs)
            payload["market"]["short_book"]["bids"][0][0] += .001
            return payload
        wb.quote_capture = changed_capture
        current = wb.refresh(now_ms=clock[0], record_daily=False)
        assert current["candidates"][0]["snapshot"]["snapshot_id"] != old["snapshot_id"]
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and errors == []
    assert len(requests) == 1 and output[0]["snapshot_id"] == old["snapshot_id"]
    latest = wb.cached_view()
    assert latest["candidates"][0]["snapshot"] == current["candidates"][0]["snapshot"]
    assert latest["candidates"][0]["btc_map_review_delta"]["same_snapshot"] is False
    assert latest["candidates"][0]["btc_map_review_delta"]["differences_cn"]
    assert old["snapshot_id"] in wb.ledger.state()["snapshots"]


def test_i04_daily_navigation_keeps_fifteen_and_does_not_delete_map_archive(tmp_path):
    calls = []
    wb, snapshots = fx.make_workbench(tmp_path, risk_provider=fx.positive_risk)
    _configure_map(wb, seed_complete_sources(calls))
    fx.write_fmz(snapshots, fx.native_gate())
    view = wb.refresh(now_ms=fx.T, record_daily=False)
    for day in range(1, 21):
        workbench._append_jsonl(wb.config.daily_snapshots_path, {
            "schema": workbench.DAILY_SNAPSHOT_SCHEMA, "snapshot_id": str(day),
            "bjt_day": f"2026-09-{day:02d}", "btc_map": view["btc_map"]})
    original = wb.config.daily_snapshots_path.read_bytes()
    assert len(wb.recent_daily_snapshots()) == 15
    assert wb.config.daily_snapshots_path.read_bytes() == original
    archived = [json.loads(row) for row in original.decode('utf-8').splitlines()]
    assert len(archived) == 20 and archived[0]["btc_map"] == view["btc_map"]
