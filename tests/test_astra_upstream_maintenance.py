"""When Deribit is under maintenance the page says so, instead of an exception type."""
import io
import urllib.error

import pytest

from tools import astra_underwriting_discovery_v20 as discovery
from tools import astra_workbench_v20 as module
from test_astra_locked_followup import setup

MAINTENANCE = b'{"jsonrpc":"2.0","error":{"message":"system_maintenance","code":11051}}'


def http_error(status=503, body=MAINTENANCE):
    return urllib.error.HTTPError("https://www.deribit.com/api/v2/public/get_index_price", status,
                                  "Service Unavailable", {}, io.BytesIO(body))


def test_a_maintenance_response_is_named_by_the_exchanges_own_reason(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise http_error()

    monkeypatch.setattr(discovery.urllib.request, "urlopen", refuse)
    with pytest.raises(discovery.DeribitPublicError) as raised:
        discovery._public_get("https://www.deribit.com/api/v2", "/public/get_index_price", {}, 1.0)
    assert raised.value.code == "DERIBIT_MAINTENANCE" and raised.value.status == 503
    other = discovery.DeribitPublicError.from_http(http_error(502, b"not json"), "/x")
    assert other.code == "DERIBIT_HTTP_502"
    assert module._upstream_code(other) == "deribit_unavailable"
    assert module._upstream_code(ValueError("plain")) is None


def test_candidates_carry_the_upstream_reason_through_both_cache_layers(tmp_path, monkeypatch):
    wb, _, clock, _ = setup(tmp_path)
    wb.discovery_capture = wb._default_discovery_capture

    def maintenance(**_kwargs):
        raise discovery.DeribitPublicError("DERIBIT_MAINTENANCE", "system_maintenance (/public/get_index_price, HTTP 503)",
                                           status=503)

    monkeypatch.setattr(module.discovery, "capture_public_deribit_discovery", maintenance)
    for _ in range(2):  # the second refresh meets the data engine's retry backoff
        clock[0] += 60_000
        view = wb.refresh(now_ms=clock[0], record_daily=False)
        assert "upstream_deribit_maintenance" in view["gaps"]
        for item in view["candidates"]:
            assert item["status"] == "error"
            assert item["review_gate"]["reasons"] == ["discovery_refresh_error", "upstream_deribit_maintenance"]
            assert item["upstream"]["code"] == "deribit_maintenance"
            assert item["error_type"] and item["error_type"] not in item["review_gate"]["reasons"]
