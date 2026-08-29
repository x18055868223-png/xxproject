from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from .config import Settings
from .models import SectionName

# The unrendered SSR shell is ~860 chars and only shows the "加载中" placeholder;
# any genuinely rendered analytics tab is several KB of text.
_MIN_RENDERED_CHARS = 1200
_DASHBOARD_MARKERS = ("NET GEX", "净 GEX", "CALL WALL", "PUT WALL", "看跌/看涨比")


class ScraplingScraper:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cached_text: str | None = None
        self._cache_expires_at = 0.0

    async def fetch_section_text(self, section: SectionName) -> str:
        return await asyncio.to_thread(self._fetch_section_text_sync, section)

    def source_url(self, section: SectionName) -> str:
        del section
        return f"{self.settings.base_url.rstrip('/')}/dashboard"

    def _fetch_section_text_sync(self, section: SectionName) -> str:
        if self._cached_text and time.monotonic() < self._cache_expires_at:
            return self._cached_text
        url = self.source_url(section)
        # Primary: Scrapling DynamicFetcher (used on the server). Fallback: bare
        # Playwright (Scrapling's persistent-context mode can fail to spawn on some
        # hosts). Last resort: static fetch, which usually only yields the shell.
        if self.settings.browser_storage_state_file:
            strategies = (("playwright", self._text_from_playwright),)
        else:
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
                detail = str(exc)
                if "localhost:3000" in detail or "login" in detail.lower():
                    errors.append(f"{name}=authentication_required")
                else:
                    errors.append(f"{name}={type(exc).__name__}: {detail[:120]}")
                continue
            auth_error = _authentication_error(text)
            if auth_error:
                errors.append(f"{name}={auth_error}")
                continue
            if text and len(text) >= _MIN_RENDERED_CHARS and _has_dashboard_metrics(text):
                self._cached_text = text
                self._cache_expires_at = time.monotonic() + max(
                    0, self.settings.browser_text_cache_seconds
                )
                return text
            errors.append(f"{name}=no_dashboard_metrics({len(text or '')} chars)")
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
                context_args: dict[str, Any] = {}
                storage_state = self.settings.browser_storage_state_file
                if storage_state:
                    if not storage_state.is_file():
                        raise RuntimeError(f"storage_state_file_not_found: {storage_state}")
                    context_args["storage_state"] = str(storage_state)
                context = browser.new_context(**context_args)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if self.settings.browser_wait_ms > 0:
                    page.wait_for_timeout(self.settings.browser_wait_ms)
                return _clean_text(page.inner_text("body", timeout=min(timeout_ms, 5000)))
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


def _authentication_error(text: str) -> str | None:
    low = (text or "").lower()
    if "checking authentication status" in low:
        return "authentication_required"
    if ("login / register" in low or "登录 / 注册" in text) and not _has_dashboard_metrics(text):
        return "authentication_required"
    return None


def _has_dashboard_metrics(text: str) -> bool:
    upper = (text or "").upper()
    return sum(marker.upper() in upper for marker in _DASHBOARD_MARKERS) >= 2
