from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import SECTIONS, RefreshSection, SectionName, empty_section_data
from .parsers import parse_section


class MetricsCache:
    def __init__(
        self,
        scraper: Any,
        *,
        cache_file: Path | None = None,
        refresh_interval_seconds: int = 600,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.scraper = scraper
        self.cache_file = cache_file
        self.refresh_interval_seconds = refresh_interval_seconds
        self.now = now or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self._sections = {section: self._empty_section_state(section) for section in SECTIONS}
        self._last_payload: dict[str, Any] | None = None
        self._stale = True

    async def load(self) -> None:
        if not self.cache_file or not self.cache_file.exists():
            return
        try:
            self._last_payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
            for section in SECTIONS:
                cached = self._last_payload.get("sections", {}).get(section)
                if cached:
                    self._sections[section].update(cached)
            self._stale = bool(self._last_payload.get("stale", True))
        except Exception:
            return

    async def get_info(self) -> dict[str, Any]:
        async with self._lock:
            return self._build_payload()

    async def refresh(self, section: RefreshSection = "all") -> dict[str, Any]:
        targets = SECTIONS if section == "all" else (section,)
        async with self._lock:
            had_error = False
            for target in targets:
                try:
                    await self._refresh_one(target)
                except Exception as exc:
                    had_error = True
                    self._sections[target]["last_error"] = str(exc)
            self._stale = had_error
            payload = self._build_payload()
            self._last_payload = payload
            self._save(payload)
            return payload

    async def refresh_periodically(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.refresh("all")
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.refresh_interval_seconds)
            except TimeoutError:
                continue

    async def _refresh_one(self, section: SectionName) -> None:
        text = await self.scraper.fetch_section_text(section)
        parsed = parse_section(section, text)
        fetched_at = self.now().isoformat()
        source_url = self._source_url(section)
        self._sections[section].update(
            {
                "data": parsed.data,
                "missing_fields": parsed.missing_fields,
                "field_status": parsed.field_status,
                "fetched_at": fetched_at,
                "last_success_at": fetched_at,
                "source_url": source_url,
                "raw_excerpt": text[:1000],
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "last_error": None,
            }
        )

    def _build_payload(self) -> dict[str, Any]:
        missing_fields: list[str] = []
        field_status: dict[str, dict[str, str]] = {}
        for state in self._sections.values():
            missing_fields.extend(state["missing_fields"])
            # Only surface fields that need attention; "ok" entries are pure noise.
            for path, status in state["field_status"].items():
                if status.get("status") != "ok":
                    field_status[path] = status

        latest_fetch = max(
            (state.get("fetched_at") for state in self._sections.values() if state.get("fetched_at")),
            default=None,
        )
        any_success = any(state.get("last_success_at") for state in self._sections.values())
        any_error = any(state.get("last_error") for state in self._sections.values())
        if not any_success:
            availability = "missing"
        elif missing_fields or any_error:
            availability = "partial"
        else:
            availability = "ready"

        payload = {
            "asset": "BTC",
            "fetched_at": latest_fetch,
            "stale": self._stale,
            "availability": availability,
            "gex_board": self._sections["gex_board"]["data"],
            "gamma_exposure": self._sections["gamma_exposure"]["data"],
            "volatility": self._sections["volatility"]["data"],
            "flow": self._sections["flow"]["data"],
            "missing_fields": sorted(set(missing_fields)),
            "field_status": field_status,
            "sections": self._public_section_states(),
        }
        return payload

    def _public_section_states(self) -> dict[str, dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for section, state in self._sections.items():
            states[section] = {
                "fetched_at": state.get("fetched_at"),
                "last_success_at": state.get("last_success_at"),
                "last_error": state.get("last_error"),
                "source_url": state.get("source_url"),
                "content_hash": state.get("content_hash"),
                "missing_fields": state.get("missing_fields", []),
            }
        return states

    def _empty_section_state(self, section: SectionName) -> dict[str, Any]:
        data = empty_section_data(section)
        missing = [f"{section}.{field}" for field in data]
        return {
            "data": data,
            "missing_fields": missing,
            "field_status": {
                path: {"status": "missing", "reason": "not_yet_fetched"} for path in missing
            },
            "fetched_at": None,
            "last_success_at": None,
            "source_url": self._source_url(section),
            "raw_excerpt": "",
            "content_hash": None,
            "last_error": None,
        }

    def _source_url(self, section: SectionName) -> str | None:
        if hasattr(self.scraper, "source_url"):
            return self.scraper.source_url(section)
        return None

    def _save(self, payload: dict[str, Any]) -> None:
        if not self.cache_file:
            return
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

