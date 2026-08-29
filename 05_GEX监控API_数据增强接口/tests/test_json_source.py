from __future__ import annotations

import asyncio
import json

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
            "profiles": {
                "total": {
                    "walls": {
                        "p1": {"strike": 82000}, "p2": {"strike": 80000},
                        "n1": {"strike": 77500}, "n2": {"strike": 77000},
                    },
                    "meta": {"magnet_a1": 78000, "magnet_a2": 77500, "vol_trigger": 77500},
                }
            },
        },
        "/api/volatility-metrics": {
            "metrics": {
                "pcrVolume": 0.67, "ivRvRatio": 0.968,
                "totalCallVolume": 40452, "totalPutVolume": 26978,
            }
        },
        "/api/price": {"price": 77586},
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
    assert snapshot["sections"]["gamma_exposure"]["field_status"]["gamma_exposure.p1"]["observed_at"]


def test_public_json_marks_primary_failures(monkeypatch):
    def fail_urlopen(request, timeout):
        raise OSError("blocked")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    source = PublicJsonSource(Settings(asset="BTC", request_timeout_seconds=1))
    snapshot = asyncio.run(source.fetch_snapshot())
    assert "gex" in snapshot["metadata"]["errors"]
    assert "gex_board.total_net_gex" in snapshot["sections"]["gex_board"]["missing_fields"]
