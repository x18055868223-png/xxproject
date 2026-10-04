"""Data-engine integration acceptance: page reads are not collectors."""
import importlib.util
from pathlib import Path
from http.server import ThreadingHTTPServer
import json
import threading
from urllib.request import urlopen

spec = importlib.util.spec_from_file_location("v20_fixtures", Path(__file__).with_name("test_astra_workbench_v20.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def test_many_page_reads_share_one_collection_and_no_routine_ledger(tmp_path):
    wb, _ = fixtures.make_workbench(tmp_path)
    fixtures.write_fmz(wb.config.snapshots_jsonl, fixtures.native_gate())
    calls = []
    original = wb.discovery_capture
    wb.discovery_capture = lambda **kw: (calls.append(kw), original(**kw))[1]
    initial = wb.refresh(record_daily=False)
    assert len(calls) == 1
    assert not wb.config.ledger_path.exists()
    server = ThreadingHTTPServer(("127.0.0.1", 0), fixtures.workbench.make_handler(wb))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for _ in range(5):
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/workbench") as response:
                result = json.load(response)
            assert result["updated_at_ms"] == initial["updated_at_ms"]
            assert result["llm_call_count"] == 0
        assert len(calls) == 1
        assert not wb.config.ledger_path.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_cached_quote_expires_without_rewriting_frozen_values(tmp_path):
    clock = [fixtures.T]
    wb, _ = fixtures.make_workbench(tmp_path, risk_provider=fixtures.positive_risk, clock=lambda: clock[0])
    fixtures.write_fmz(wb.config.snapshots_jsonl, fixtures.native_gate())
    initial = wb.refresh(record_daily=False)
    before = [item["snapshot"] for item in initial["candidates"]]
    clock[0] += 6_000
    result = wb.cached_view()
    assert [item["snapshot"] for item in result["candidates"]] == before
    assert all(not item["review_gate"]["enabled"] for item in result["candidates"])
    assert all("cached_quote_not_current" in item["review_gate"]["reasons"] for item in result["candidates"])


def test_uninitialized_page_read_does_not_fetch_or_call_llm(tmp_path):
    wb, _ = fixtures.make_workbench(tmp_path)
    wb.discovery_capture = lambda **kw: (_ for _ in ()).throw(AssertionError("read performed network"))
    result = wb.cached_view()
    assert result["candidates"] == []
    assert result["llm_call_count"] == 0
    assert "data_collector_warming_up" in result["gaps"]


def test_supported_native_export_version_preserves_old_gate_checks(tmp_path):
    row = fixtures.write_fmz(tmp_path / "fmz.jsonl", fixtures.native_gate())
    from tools import astra_underwriting_fact_context_v20 as context
    row["decision"]["demo_version"] = "2.0.1"
    result = context.from_evaluation_snapshot(row, fixtures.T, source_hash="a" * 64)
    assert result["status"] == "current"
    row["decision"]["demo_version"] = "2.0.2"
    result = context.from_evaluation_snapshot(row, fixtures.T, source_hash="a" * 64)
    assert result["status"] == "background_only"


def test_quote_cache_keeps_real_metadata_clocks_and_stale_book_is_rejected(tmp_path, monkeypatch):
    clock = [fixtures.T]
    wb, _ = fixtures.make_workbench(tmp_path, clock=lambda: clock[0])
    names = [row['instrument_name'] for row in fixtures.instruments()][:2]
    metas = {row['instrument_name']: row for row in fixtures.instruments()}
    calls = []
    def fetch(path, params):
        calls.append(path)
        if path == 'public/get_instrument':
            return metas[params['instrument_name']]
        return {'instrument_name': params['instrument_name'], 'timestamp': clock[0],
                'state': 'open', 'index_price': 100000, 'bids': [[.007, 1]], 'asks': [[.008, 1]]}
    monkeypatch.setattr(fixtures.workbench.public_quote, 'public_transport', fetch)
    first = wb._default_quote_capture(*names, 1.0, now_ms=clock[0])
    clock[0] += 1000
    second = wb._default_quote_capture(*names, 1.0, now_ms=clock[0])
    assert len(calls) == 4
    assert second['metadata_received_ts_ms'] == first['metadata_received_ts_ms']
    assert second['market']['short_book']['timestamp'] == fixtures.T
    clock[0] += 5000
    wb._default_quote_capture(*names, 1.0, now_ms=clock[0])
    assert calls.count('public/get_instrument') == 2
    assert calls.count('public/get_order_book') == 4


def test_manual_lock_fetches_only_chosen_side_and_never_rediscovers(tmp_path):
    wb, path = fixtures.make_workbench(tmp_path, risk_provider=fixtures.positive_risk)
    fixtures.write_fmz(path, fixtures.native_gate())
    initial = wb.refresh(record_daily=False)
    selected = initial['candidates'][0]['snapshot']['candidate_id']
    wb.discovery_capture = lambda **kw: (_ for _ in ()).throw(AssertionError('manual lock rescanned chain'))
    calls = []
    original = wb.quote_capture
    def capture(short, long, quantity, **kw):
        calls.append((short, long))
        return original(short, long, quantity, **kw)
    wb.quote_capture = capture
    wb.review_candidate(selected, 'selected-only', authorization='Bearer secret')
    assert len(calls) == 1
    assert calls[0][0].endswith('-P')


def test_stale_gamma_input_cannot_keep_native_gate_current(tmp_path):
    wb, path = fixtures.make_workbench(tmp_path)
    gate = fixtures.native_gate()
    gate['net_gamma']['source_ts_ms'] = fixtures.T - 3600_000
    fixtures.write_fmz(path, gate)
    result = wb.latest_fact(fixtures.T)
    assert result['status'] == 'background_only'
    assert 'data_engine_space_input_usage_rejected' in result['gap_reasons']
