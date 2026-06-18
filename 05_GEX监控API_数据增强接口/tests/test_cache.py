from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gexmonitorapi.cache import MetricsCache


class StaticScraper:
    async def fetch_section_text(self, section: str) -> str:
        texts = {
            "gex_board": "DVOL 42.9% TOTAL NET GEX $-67M MM Short Gamma MARKET STATE Critical",
            # Deliberately partial: flip/spot present, n1/n2/etc. missing.
            "gamma_exposure": "GAMMA COMPONENTS FLIP 67,372.727 +1.0% SPOT PRICE 66,684.5",
            "volatility": "IV/RV RATIO 1.26x PCR (VOLUME) 0.93",
            "flow": "CALL PREMIUM $27.7M PUT PREMIUM $51.8M P/C RATIO 1.88",
        }
        return texts[section]


class FailingScraper:
    async def fetch_section_text(self, section: str) -> str:
        raise RuntimeError(f"fetch failed for {section}")


@pytest.mark.asyncio
async def test_refresh_builds_info_payload_and_missing_fields() -> None:
    cache = MetricsCache(StaticScraper(), now=lambda: datetime(2026, 6, 3, 9, tzinfo=UTC))

    payload = await cache.refresh("all")

    assert payload["asset"] == "BTC"
    assert payload["availability"] == "partial"
    assert payload["stale"] is False
    assert payload["gex_board"]["total_net_gex"] == -67000000.0
    assert "gamma_exposure.n2" in payload["missing_fields"]
    assert payload["field_status"]["gamma_exposure.n2"]["status"] == "missing"
    # field_status only surfaces problem fields; "ok" entries are dropped as noise.
    assert all(s["status"] != "ok" for s in payload["field_status"].values())
    assert "gex_board.total_net_gex" not in payload["field_status"]
    # raw_excerpt debug blob is no longer exposed in the response.
    assert "raw_excerpt" not in payload["sections"]["gex_board"]


@pytest.mark.asyncio
async def test_refresh_failure_keeps_previous_cache_and_marks_stale() -> None:
    cache = MetricsCache(StaticScraper(), now=lambda: datetime(2026, 6, 3, 9, tzinfo=UTC))
    await cache.refresh("all")
    cache.scraper = FailingScraper()

    payload = await cache.refresh("all")

    assert payload["stale"] is True
    assert payload["availability"] == "partial"
    assert payload["gex_board"]["total_net_gex"] == -67000000.0
    assert payload["sections"]["gex_board"]["last_error"] == "fetch failed for gex_board"

