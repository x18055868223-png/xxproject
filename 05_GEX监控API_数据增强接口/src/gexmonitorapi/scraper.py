from __future__ import annotations

import asyncio
import re
from typing import Any

from .config import Settings
from .models import SECTION_TABS, SectionName

# The unrendered SSR shell is ~860 chars and only shows the "加载中" placeholder;
# any genuinely rendered analytics tab is several KB of text.
_MIN_RENDERED_CHARS = 1200


class ScraplingScraper:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def fetch_section_text(self, section: SectionName) -> str:
        return await asyncio.to_thread(self._fetch_section_text_sync, section)

    def source_url(self, section: SectionName) -> str:
        asset = self.settings.asset.lower()
        tab = SECTION_TABS[section]
        return f"{self.settings.base_url.rstrip('/')}/{asset}/options/analytics?tab={tab}"

    def _fetch_section_text_sync(self, section: SectionName) -> str:
        url = self.source_url(section)
        # Primary: Scrapling DynamicFetcher (used on the server). Fallback: bare
        # Playwright (Scrapling's persistent-context mode can fail to spawn on some
        # hosts). Last resort: static fetch, which usually only yields the shell.
        strategies = (
            ("scrapling_dynamic", self._text_from_scrapling_dynamic),
            ("playwright", self._text_from_playwright),
            ("static", self._text_from_static),
        )
        errors: list[str] = []
        for name, strategy in strategies:
            try:
                text = strategy(url)
            except Exception as exc:  # noqa: BLE001 - record and try the next strategy
                errors.append(f"{name}={type(exc).__name__}: {str(exc)[:120]}")
                continue
            if text and len(text) >= _MIN_RENDERED_CHARS:
                return text
            errors.append(f"{name}=loading_placeholder({len(text or '')} chars)")
        raise RuntimeError("no rendered content: " + "; ".join(errors))

    def _text_from_scrapling_dynamic(self, url: str) -> str:
        from scrapling.fetchers import DynamicFetcher

        kwargs: dict[str, Any] = {
            "headless": True,
            "load_dom": True,
            "network_idle": self.settings.browser_network_idle,
            "timeout": self.settings.request_timeout_seconds * 1000,
            "disable_resources": self.settings.browser_disable_resources,
        }
        flags = ["--disable-dev-shm-usage", "--disable-gpu"]
        if self.settings.browser_no_sandbox:
            # Required for headless Chromium as non-root on locked-down hosts.
            flags.append("--no-sandbox")
        kwargs["extra_flags"] = flags
        if self.settings.browser_wait_ms > 0:
            # Fixed settle time for the websocket-fed panels to render.
            kwargs["wait"] = self.settings.browser_wait_ms
        return self._page_text(DynamicFetcher.fetch(url, **kwargs))

    def _text_from_playwright(self, url: str) -> str:
        from playwright.sync_api import sync_playwright

        flags = ["--disable-dev-shm-usage", "--disable-gpu"]
        if self.settings.browser_no_sandbox:
            flags.append("--no-sandbox")
        launch_args: dict[str, Any] = {"headless": True, "args": flags}
        timeout_ms = self.settings.request_timeout_seconds * 1000
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**launch_args)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if self.settings.browser_wait_ms > 0:
                    page.wait_for_timeout(self.settings.browser_wait_ms)
                return _clean_text(page.inner_text("body"))
            finally:
                browser.close()

    def _text_from_static(self, url: str) -> str:
        from scrapling.fetchers import Fetcher

        page = Fetcher.get(url, stealthy_headers=True, timeout=self.settings.request_timeout_seconds)
        return self._page_text(page)

    def _page_text(self, page: Any) -> str:
        # Scrapling's get_all_text ignores <script>/<style> by default, so it yields
        # visible content instead of the page's inlined React/Next bootstrap code.
        getter = getattr(page, "get_all_text", None)
        if callable(getter):
            try:
                value = getter()
                if isinstance(value, str) and value.strip():
                    return _clean_text(value)
            except Exception:
                pass
        if hasattr(page, "css"):
            try:
                values = page.css("body *::text").getall()
                text = " ".join(str(value) for value in values)
                return _clean_text(text)
            except Exception:
                pass
        for attr in ("text", "body", "content"):
            value = getattr(page, attr, None)
            if callable(value):
                try:
                    value = value()
                except Exception:
                    value = None
            if isinstance(value, str) and value.strip():
                return _clean_text(_strip_tags(value))
        return _clean_text(str(page))


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()
