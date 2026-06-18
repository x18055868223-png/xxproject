from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from gexmonitorapi.app import create_app
from gexmonitorapi.config import Settings


class FakeCache:
    def __init__(self) -> None:
        self.refreshed_section: str | None = None

    async def get_info(self) -> dict:
        return {
            "asset": "BTC",
            "fetched_at": datetime(2026, 6, 3, 9, tzinfo=UTC).isoformat(),
            "stale": False,
            "availability": "ready",
            "gex_board": {
                "total_net_gex": -62730587.7,
                "dvol": 43.1,
                "market_state": "negative_gamma",
            },
            "gamma_exposure": {
                "n2": None,
                "n1": None,
                "flip_point": 67388.83,
                "volatility_trigger": None,
                "spot_price": 66950.91,
                "magnet_price": None,
                "p1": None,
                "p2": None,
            },
            "volatility": {"iv_rv_ratio": None, "pcr": None, "term_structure": []},
            "flow": {
                "call_premium": None,
                "put_premium": None,
                "call_put_bias": None,
                "put_call_ratio": None,
                "abnormal_signal": None,
            },
            "missing_fields": ["gamma_exposure.n2"],
            "field_status": {
                "gamma_exposure.n2": {
                    "status": "missing",
                    "reason": "not_found_in_rendered_page",
                }
            },
            "sections": {},
        }

    async def refresh(self, section: str = "all") -> dict:
        self.refreshed_section = section
        return await self.get_info()


def build_client() -> tuple[TestClient, FakeCache]:
    cache = FakeCache()
    app = create_app(Settings(api_token="test-token"), cache=cache)
    return TestClient(app), cache


def test_health_is_public() -> None:
    client, _cache = build_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_info_requires_bearer_token() -> None:
    client, _cache = build_client()

    assert client.get("/v1/info").status_code == 401
    assert client.get("/v1/info", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_info_returns_clean_metric_dictionaries() -> None:
    client, _cache = build_client()

    response = client.get("/v1/info", headers={"Authorization": "Bearer test-token"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["asset"] == "BTC"
    assert payload["gex_board"]["total_net_gex"] == -62730587.7
    assert payload["gamma_exposure"]["flip_point"] == 67388.83
    assert payload["missing_fields"] == ["gamma_exposure.n2"]
    assert payload["field_status"]["gamma_exposure.n2"]["status"] == "missing"


def test_refresh_accepts_section_and_returns_info() -> None:
    client, cache = build_client()

    response = client.post(
        "/v1/refresh?section=gamma_exposure",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert cache.refreshed_section == "gamma_exposure"
    assert response.json()["asset"] == "BTC"


def test_refresh_rejects_unknown_section() -> None:
    client, _cache = build_client()

    response = client.post(
        "/v1/refresh?section=unknown",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 422

