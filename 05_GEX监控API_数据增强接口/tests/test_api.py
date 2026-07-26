from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from gexmonitorapi.app import create_app
from gexmonitorapi.cache import MetricsCache
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


class RankSequenceScraper:
    def __init__(self, rows: list[dict[str, float]]) -> None:
        self.rows = rows
        self.index = -1

    async def fetch_section_text(self, section: str) -> str:
        if section == "gex_board":
            self.index += 1
        row = self.rows[self.index]
        texts = {
            "gex_board": (
                f"DVOL {row['dvol']}% TOTAL NET GEX ${row['net_gex_m']}M "
                "MM Short Gamma MARKET STATE Critical"
            ),
            "gamma_exposure": "GAMMA COMPONENTS FLIP 67,372.727 +1.0% SPOT PRICE 66,684.5",
            "volatility": f"IV/RV RATIO {row['iv_rv']}x PCR (VOLUME) {row['pcr']}",
            "flow": (
                f"CALL PREMIUM $27.7M PUT PREMIUM $51.8M "
                f"CALL / PUT TILT {row['call_share']:g}% Call P/C RATIO {row['put_call_ratio']}"
            ),
        }
        return texts[section]


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


def test_info_rank_quality_turns_ok_at_15_days_and_keeps_30d_cutoff() -> None:
    base = datetime(2026, 6, 1, 9, tzinfo=UTC)
    day_offsets = [0.0, 1.0, 14.9, 15.0, 15.1, 29.9, 31.0]
    rows = [
        {
            "net_gex_m": -10.0 - index,
            "dvol": 40.0 + index,
            "iv_rv": 1.0 + index / 100,
            "pcr": 0.8 + index / 100,
            "call_share": 45.0 + index,
            "put_call_ratio": 1.0 + index / 100,
        }
        for index in range(len(day_offsets))
    ]
    now_values = iter(
        timestamp
        for offset in day_offsets
        for timestamp in [base + timedelta(days=offset)] * 4
    )
    cache = MetricsCache(
        RankSequenceScraper(rows),
        rank_lookback_days=30,
        now=lambda: next(now_values),
    )
    app = create_app(
        Settings(
            api_token="test-token",
            enable_background_refresh=False,
            refresh_on_startup=False,
        ),
        cache=cache,
    )

    with TestClient(app) as client:
        qualities: list[tuple[float, str]] = []
        for _ in day_offsets[:-1]:
            response = client.post(
                "/v1/refresh?section=all",
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200
            payload = response.json()
            metric = payload["rank"]["metrics"]["gex_board.total_net_gex"]
            qualities.append((payload["rank"]["window"]["window_days"], metric["quality"]))

        assert qualities == [
            (0.0, "single_sample"),
            (1.0, "warming_up"),
            (14.9, "warming_up"),
            (15.0, "ok"),
            (15.1, "ok"),
            (29.9, "ok"),
        ]

        response = client.post(
            "/v1/refresh?section=all",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    payload = response.json()
    rank = payload["rank"]
    assert rank["window"]["history_retained_count"] == 7
    assert rank["window"]["sample_count"] == 6
    assert rank["window"]["window_days"] == pytest.approx(30.0)
    assert rank["metrics"]["gex_board.total_net_gex"]["sample_count"] == 6
    assert rank["metrics"]["gex_board.total_net_gex"]["quality"] == "ok"
