from copy import deepcopy

from tools.astra_map_readonly_preview_v242 import build_readonly_view


def capture():
    return {
        "llm_http_calls": 0, "trading_calls": 0,
        "completeness": {"groups": {"背景底价": {"products": [{
            "product_id": "deribit.btc.index.map_context.v1", "usage": "background",
            "can_use": True, "status": "USABLE", "observation_end_ms": 1000,
            "first_seen_at_ms": 1001,
        }]}}},
        "btc_map": {
            "schema": "btc_map@1", "map_id": "original-server-map", "cutoff_at_ms": 1010,
            "current_price_usd": 84000, "price_basis": "DERIBIT_BTC_USD_INDEX",
            "background": {}, "reference_pool": [], "policy_link": {},
            "source_manifest": {"source_records": [{
                "record_id": "index-record", "identity": {"product_id": "deribit.btc.index.map_context.v1"},
                "time": {"observation_end_ms": 1000, "first_seen_at_ms": 1001},
                "usage_qualification": {"can_use": True, "status": "USABLE", "ttl_ms": 300000},
            }]},
        },
        "map_gaps": ["gex_effective_flip_unavailable"],
    }


def test_independent_server_background_never_becomes_fmz_or_policy_authority():
    original = capture()
    before = deepcopy(original)
    view = build_readonly_view(original)
    assert original == before
    assert view["btc_map"]["current_price_usd"] == 84000
    assert view["market_context"]["current_price_usd"] is None
    assert view["market_context"]["status"] == "missing"
    assert view["candidates"] == []
    assert view["space_gate"]["status"] == "STRUCTURE_BLOCKED"
    assert view["preview"]["kind"] == "historical_replay"
    assert view["btc_map"]["presentation"]["mode"] == "REPLAY"
    assert view["llm_call_count"] == 0
    assert view["preview"]["trading_permission"] is False
    assert view["preview"]["source_map_id"] == "original-server-map"
    assert view["btc_map"]["map_id"] != "original-server-map"
    assert view["updated_at_ms"] == 1010
    assert "gex_effective_flip_unavailable" in view["btc_map"]["map_gaps"]
    assert any("GEX" in item["text_cn"] for item in view["btc_map"]["presentation"]["decision_brief"]["conflicts"])


def test_health_reports_captured_source_qualification_and_does_not_refresh_clocks():
    view = build_readonly_view(capture())
    health = view["data_health"]
    product = health["products"]["deribit.btc.index.map_context.v1"]
    assert health["generated_at_ms"] == 1010
    assert health["historical_replay"] is True
    assert product["status"] == "USABLE"
    assert product["current"][0]["time"]["observation_end_ms"] == 1000
    assert product["current"][0]["usage_decision"]["ttl_ms"] == 300000
