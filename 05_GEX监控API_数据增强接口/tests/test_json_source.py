from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from gexmonitorapi.config import Settings
from gexmonitorapi.json_source import PublicJsonSource


class _Response:
    status = 200

    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_public_json_maps_levels_and_volatility(monkeypatch):
    payloads = {
        "/api/gex-latest": {
            "asset_price": 77586,
            "flip_point": 77725,
            "total_gex": 97400000,
            "dvol": 37.9,
            "timestamp": "2026-06-03T08:58:00Z",
            "dvol_source_timestamp": "2026-06-03T08:56:00Z",
            "dvol_timestamp": "2026-06-03T08:57:00Z",
            "profiles": {
                "total": {
                    "walls": {
                        "p1": {"strike": 82000}, "p2": {"strike": 80000},
                        "n1": {"strike": 77500}, "n2": {"strike": 77000},
                    },
                    "meta": {
                        "magnet_a1": 78000,
                        "magnet_a2": 77500,
                        "vol_trigger": 77500,
                        "updateTime": "2026-06-03T08:55:00Z",
                    },
                }
            },
        },
        "/api/volatility-metrics": {
            "timestamp": "2026-06-03T08:54:00Z",
            "metrics": {
                "pcrVolume": 0.67, "ivRvRatio": 0.968,
                "totalCallVolume": 40452, "totalPutVolume": 26978,
            }
        },
        "/api/price": {"price": 77586, "timestamp": "2026-06-03T08:53:00Z"},
        "/api/options-chain": {
            "spot_price": 77586,
            "options": [
                {"currency": "BTC", "strike": 82000, "gamma": 1, "open_interest": 1, "contract_size": 1, "type": "C"},
                {"currency": "BTC", "strike": 77500, "gamma": 1, "open_interest": 1, "contract_size": 1, "type": "P"},
            ],
        },
    }

    def fake_urlopen(request, timeout):
        for path, payload in payloads.items():
            if path in request.full_url:
                return _Response(payload)
        raise AssertionError(request.full_url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    source = PublicJsonSource(Settings(asset="BTC", request_timeout_seconds=1))
    snapshot = asyncio.run(source.fetch_snapshot())
    board = snapshot["sections"]["gex_board"]["data"]
    gamma = snapshot["sections"]["gamma_exposure"]["data"]
    flow = snapshot["sections"]["flow"]["data"]
    assert board == {"total_net_gex": 97400000.0, "dvol": 37.9, "market_state": "positive_gamma"}
    assert gamma["flip_point"] == 77725.0
    assert gamma["p1"] == 82000.0 and gamma["n1"] == 77500.0
    assert gamma["magnet_price"] == 78000.0
    assert flow["put_call_ratio"] == 0.67
    assert snapshot["metadata"]["source_mode"] == "public_json"
    assert snapshot["metadata"]["cross_check"]["wall_strikes_match"]["p1"] is True
    assert snapshot["sections"]["gamma_exposure"]["field_status"]["gamma_exposure.p1"]["source_ref"].endswith("walls.p1")
    assert snapshot["sections"]["gamma_exposure"]["field_status"]["gamma_exposure.p1"]["observed_at"] is None
    assert snapshot["sections"]["gamma_exposure"]["field_status"]["gamma_exposure.p1"]["generated_at"]
    semantics = snapshot["metadata"]["gex_time_semantics"]
    fields = semantics["fields"]
    assert semantics["schema_version"] == "gex_time_semantics@1.0.0"
    assert fields["gex_board.total_net_gex"]["generated_at_ms"] == _ms("2026-06-03T08:58:00Z")
    assert fields["gex_board.total_net_gex"]["observed_at_ms"] is None
    assert fields["gex_board.dvol"]["observed_at_ms"] == _ms("2026-06-03T08:56:00Z")
    assert fields["gex_board.dvol"]["generated_at_ms"] == _ms("2026-06-03T08:57:00Z")
    assert fields["gamma_exposure.p1"]["generated_at_ms"] == _ms("2026-06-03T08:55:00Z")
    assert fields["gamma_exposure.flip_point"]["generated_at_ms"] == _ms("2026-06-03T08:58:00Z")
    assert fields["flow.call_put_bias"]["generated_at_ms"] == _ms("2026-06-03T08:54:00Z")


def test_public_json_uses_selected_fallback_times_and_reports_bad_time(monkeypatch):
    payloads = {
        "/api/gex-latest": {
            "total_gex": 12500000,
            "timestamp": "not-a-time",
            "profiles": {
                "total": {
                    "walls": {"p1": {"strike": 80000}},
                    "meta": {"flip": 79500, "updateTime": "2026-06-03T08:55:00Z"},
                }
            },
        },
        "/api/volatility-metrics": {
            "timestamp": "2026-06-03T08:54:00Z",
            "metrics": {"dvol": 41.2},
        },
        "/api/price": {"price": 79600, "timestamp": "2026-06-03T08:53:00Z"},
        "/api/options-chain": {},
    }

    def fake_urlopen(request, timeout):
        for path, payload in payloads.items():
            if path in request.full_url:
                return _Response(payload)
        raise AssertionError(request.full_url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    source = PublicJsonSource(Settings(asset="BTC", request_timeout_seconds=1))
    fields = asyncio.run(source.fetch_snapshot())["metadata"]["gex_time_semantics"]["fields"]

    assert fields["gamma_exposure.spot_price"]["source_ref"] == "price.price"
    assert fields["gamma_exposure.spot_price"]["generated_at_ms"] == _ms("2026-06-03T08:53:00Z")
    assert fields["gamma_exposure.flip_point"]["source_ref"] == "gex-latest.profiles.total.meta.flip"
    assert fields["gamma_exposure.flip_point"]["generated_at_ms"] == _ms("2026-06-03T08:55:00Z")
    assert fields["gex_board.dvol"]["source_ref"] == "volatility-metrics.metrics.dvol"
    assert fields["gex_board.dvol"]["generated_at_ms"] == _ms("2026-06-03T08:54:00Z")
    assert fields["gex_board.total_net_gex"]["generated_at_ms"] is None
    assert fields["gex_board.total_net_gex"]["time_errors"] == [
        "generated_at_parse_failed:gex-latest.total_gex"
    ]


def test_public_json_keeps_fetched_only_time_as_unknown_not_error(monkeypatch):
    payloads = {
        "/api/gex-latest": {"dvol": 39.5},
        "/api/volatility-metrics": {"metrics": {}},
        "/api/price": {},
        "/api/options-chain": {},
    }

    def fake_urlopen(request, timeout):
        for path, payload in payloads.items():
            if path in request.full_url:
                return _Response(payload)
        raise AssertionError(request.full_url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    source = PublicJsonSource(Settings(asset="BTC", request_timeout_seconds=1))
    fields = asyncio.run(source.fetch_snapshot())["metadata"]["gex_time_semantics"]["fields"]
    dvol = fields["gex_board.dvol"]

    assert dvol["source_ref"] == "gex-latest.dvol"
    assert dvol["observed_at_ms"] is None
    assert dvol["generated_at_ms"] is None
    assert dvol["fetched_at_ms"] is not None
    assert dvol["time_basis"] == "dvol_time_unknown"
    assert dvol["time_errors"] == []


def test_public_json_marks_primary_failures(monkeypatch):
    def fail_urlopen(request, timeout):
        raise OSError("blocked")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    source = PublicJsonSource(Settings(asset="BTC", request_timeout_seconds=1))
    snapshot = asyncio.run(source.fetch_snapshot())
    assert "gex" in snapshot["metadata"]["errors"]
    assert "gex_board.total_net_gex" in snapshot["sections"]["gex_board"]["missing_fields"]
    fields = snapshot["metadata"]["gex_time_semantics"]["fields"]
    assert fields["gex_board.total_net_gex"]["observed_at_ms"] is None
    assert fields["gex_board.total_net_gex"]["fetched_at_ms"] is None
    assert set(fields) >= {"gex_board.total_net_gex", "gamma_exposure.p1", "flow.put_call_ratio"}


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).timestamp() * 1000)


def test_source_time_requires_timezone_or_valid_epoch():
    from gexmonitorapi.json_source import _time_to_ms
    for bad in (True, -1, 0, float("nan"), "2026-09-11T12:00:00"):
        value, errors = _time_to_ms(bad, "test.source", "generated_at")
        assert value is None
        assert errors
