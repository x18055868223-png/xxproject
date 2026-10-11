from __future__ import annotations

import copy
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def bundle_fixture() -> dict:
    return copy.deepcopy(load_fixture("dashboard_bundle_btc_20261011.json"))


def gex_fixture() -> dict:
    return copy.deepcopy(load_fixture("gex_latest_lite_btc_20261011.json"))


class FakeResponse:
    status = 200

    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def install_payloads(monkeypatch, payloads: dict) -> None:
    """Serve payloads by URL path. A value that is an Exception is raised;
    any URL not in the map (e.g. retired /api/volatility-metrics or /api/price)
    fails the test with AssertionError, which _get_json does not swallow."""

    def fake_urlopen(request, timeout):
        for path, payload in payloads.items():
            if path in request.full_url:
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)
        raise AssertionError(f"unexpected upstream URL: {request.full_url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def fixture_payloads(bundle: dict | None = None, gex: dict | None = None) -> dict:
    return {
        "/api/dashboard-bundle": bundle if bundle is not None else bundle_fixture(),
        "/api/gex-latest": gex if gex is not None else gex_fixture(),
        "/api/options-chain": {},
    }
