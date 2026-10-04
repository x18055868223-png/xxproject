import copy
import json

from tools import astra_workbench_v20 as wb_core
from tools import astra_map_sources_v24 as sources
from tools import astra_underwriting_review_v20 as review
from tests import test_astra_workbench_v20 as fx
from tests import test_astra_source_consumer_v244 as producer_fx


def test_legacy_success_is_readable_without_current_qualification_or_rewrite(tmp_path):
    wb, _ = fx.make_workbench(tmp_path)
    old = {"status": "OK", "snapshot_id": "old", "review": {
        "status": "OK", "summary_cn": "historical wording", "http_calls": 1}}
    wb._locked_state_update(lambda state: state.setdefault("manual_review", {}).update({
        "last_result": copy.deepcopy(old),
        "idempotency": {"old-click": {"result": copy.deepcopy(old)}},
    }))
    raw = wb.config.state_path.read_bytes()
    for result in [wb.manual_review_state(fx.T)["last_result"], wb._idempotency_result("old-click")]:
        assert result["status"] == "stale_protocol_review"
        assert result["current_protocol_qualified"] is False
        assert result["review"]["summary_cn"] == "historical wording"
        assert result["review"]["status"] == "stale_protocol_review"
    compact = wb_core._compact_review({"status": "attached", "review": old["review"]})
    assert compact["status"] == "stale_protocol_review"
    assert compact["summary_cn"] == "historical wording"
    assert wb.config.state_path.read_bytes() == raw


def test_producer_health_describes_consumed_records_and_clears_private_fallback(tmp_path, monkeypatch):
    path, producer = producer_fx._write_producer_current(tmp_path)
    wb = producer_fx._make_env_workbench(tmp_path, monkeypatch, path)
    view = wb.refresh(now_ms=fx.T, record_daily=False)
    frozen = next(row for row in producer["freeze_manifest"]["source_records"]
                  if row["identity"]["product_id"] == sources.ETF_FLOW)
    for current in [view, wb.cached_view()]:
        health = current["data_health"]
        assert health["map_source_health_schema"] == "astra_producer_source_health@2.4.4"
        row = health["products"][sources.ETF_FLOW]
        assert row["status"] not in {"MISSING", "NOT_CONNECTED"}
        assert row["current"][0]["record_id"] == frozen["record_id"]
        assert row["current"][0]["first_seen_at_ms"] == frozen["time"]["first_seen_at_ms"]
        assert row["current"][0]["usage_decisions"]
    # A former collector cache cannot silently take authority when the producer fails.
    wb.data_engine.ingest(sources.ETF_FLOW, {"metrics": {"flow_3d_usd_mn": 999}},
                         observation_end_ms=fx.T - 1000, retrieved_at_ms=fx.T,
                         source_revision="private-shadow", scope=frozen["identity"]["scope"])
    path.write_text("{}", encoding="utf-8")
    failed = wb.cached_view()
    assert failed["data_health"]["map_source_consumer"]["status"] == "ERROR"
    assert failed["data_health"]["products"][sources.ETF_FLOW]["current"] == []
    assert "producer_map_source_unqualified" in failed["gaps"]
    path.write_text(json.dumps(producer), encoding="utf-8")
    recovered = wb.cached_view()
    assert recovered["data_health"]["map_source_consumer"]["status"] == "OK"
    assert "producer_map_source_unqualified" not in recovered["gaps"]
    assert recovered["candidates"][0]["snapshot"] == view["candidates"][0]["snapshot"]
    path.write_bytes(b"\xff")
    unreadable = wb.cached_view()
    assert unreadable["data_health"]["map_source_consumer"]["error"]["code"] == "PRODUCER_CURRENT_INVALID_JSON"
    assert unreadable["candidates"][0]["snapshot"]["liability"]["payout_table"]


def test_producer_changes_during_quote_do_not_mix_map_or_erase_contract(tmp_path, monkeypatch):
    path, _ = producer_fx._write_producer_current(tmp_path)
    wb = producer_fx._make_env_workbench(tmp_path, monkeypatch, path)
    def quote(*args, **kwargs):
        path.write_text("{}", encoding="utf-8")
        return fx.fake_quote_capture(*args, **kwargs)
    wb.quote_capture = quote
    view = wb.refresh(now_ms=fx.T, record_daily=False)
    for item in view["candidates"]:
        assert item["status"] == "snapshot"
        snapshot = item["snapshot"]
        assert snapshot["liability"]["payout_table"]
        assert snapshot["data_identity"]["btc_map"]["source_consumer"]["status"] == "ERROR"
        assert not any(ref.startswith("map.") for ref in review.build_review_input(snapshot)["allowed_refs"])
