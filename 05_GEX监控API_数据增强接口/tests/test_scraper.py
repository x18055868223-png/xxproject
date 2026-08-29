from __future__ import annotations

import pytest

from gexmonitorapi.config import Settings
from gexmonitorapi.scraper import ScraplingScraper


DASHBOARD_TEXT = " ".join([
    "净 GEX / REGIME 87M",
    "CALL WALL US$82,000",
    "PUT WALL US$77,500",
    "x" * 1300,
])


def test_dashboard_url_and_rendered_text_are_reused() -> None:
    scraper = ScraplingScraper(Settings(browser_text_cache_seconds=60))
    calls = 0

    def rendered(_url: str) -> str:
        nonlocal calls
        calls += 1
        return DASHBOARD_TEXT

    scraper._text_from_scrapling_dynamic = rendered  # type: ignore[method-assign]
    assert scraper.source_url("gex_board").endswith("/dashboard")
    assert scraper._fetch_section_text_sync("gex_board") == DASHBOARD_TEXT
    assert scraper._fetch_section_text_sync("gamma_exposure") == DASHBOARD_TEXT
    assert calls == 1


def test_login_shell_is_not_accepted_as_metrics() -> None:
    scraper = ScraplingScraper(Settings())
    login = "Checking authentication status... Login / Register " + "x" * 1300
    scraper._text_from_scrapling_dynamic = lambda _url: login  # type: ignore[method-assign]
    scraper._text_from_playwright = lambda _url: login  # type: ignore[method-assign]
    scraper._text_from_static = lambda _url: login  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="authentication_required"):
        scraper._fetch_section_text_sync("gex_board")
