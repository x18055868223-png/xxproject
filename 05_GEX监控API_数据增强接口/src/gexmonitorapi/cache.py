from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import SECTION_FIELDS, SECTIONS, RefreshSection, SectionName, empty_section_data
from .parsers import parse_section

RANK_METRICS: tuple[str, ...] = (
    "gex_board.total_net_gex",
    "gex_board.dvol",
    "volatility.iv_rv_ratio",
    "volatility.pcr",
    "flow.call_share_pct",
    "flow.put_call_ratio",
)


class MetricsCache:
    def __init__(
        self,
        scraper: Any,
        *,
        cache_file: Path | None = None,
        history_file: Path | None = None,
        rank_lookback_days: int = 30,
        refresh_interval_seconds: int = 1800,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.scraper = scraper
        self.cache_file = cache_file
        self.history_file = history_file
        self.rank_lookback_days = max(1, rank_lookback_days)
        self.refresh_interval_seconds = refresh_interval_seconds
        self.now = now or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self._sections = {section: self._empty_section_state(section) for section in SECTIONS}
        self._last_payload: dict[str, Any] | None = None
        self._stale = True
        self._history: list[dict[str, Any]] = []

    async def load(self) -> None:
        if self.cache_file and self.cache_file.exists():
            try:
                self._last_payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
                self._restore_cached_payload(self._last_payload)
                self._stale = bool(self._last_payload.get("stale", True))
            except Exception:
                pass
        self._load_history()

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
            if section == "all" and not had_error:
                self._append_history_sample(self._current_history_sample())
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
        payload["rank"] = self._build_rank(payload)
        return payload

    def _current_history_sample(self) -> dict[str, Any] | None:
        fetched_at = max(
            (state.get("fetched_at") for state in self._sections.values() if state.get("fetched_at")),
            default=None,
        )
        if not fetched_at:
            return None
        payload = {
            "gex_board": self._sections["gex_board"]["data"],
            "volatility": self._sections["volatility"]["data"],
            "flow": self._sections["flow"]["data"],
        }
        return {
            "asset": "BTC",
            "fetched_at": fetched_at,
            "metrics": self._rank_metric_values(payload),
        }

    def _append_history_sample(self, sample: dict[str, Any] | None) -> None:
        if not sample:
            return
        self._history.append(sample)
        if not self.history_file:
            return
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        with self.history_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _load_history(self) -> None:
        if not self.history_file or not self.history_file.exists():
            return
        loaded: list[dict[str, Any]] = []
        try:
            for line in self.history_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, dict) and isinstance(item.get("metrics"), dict):
                    loaded.append(item)
        except Exception:
            return
        self._history = loaded

    def _restore_cached_payload(self, payload: dict[str, Any]) -> None:
        problem_status = payload.get("field_status", {})
        sections = payload.get("sections", {})
        for section in SECTIONS:
            data = payload.get(section)
            if isinstance(data, dict):
                self._sections[section]["data"] = data

            cached = sections.get(section) if isinstance(sections, dict) else None
            if isinstance(cached, dict):
                for key in ("fetched_at", "last_success_at", "last_error", "source_url", "content_hash"):
                    if key in cached:
                        self._sections[section][key] = cached[key]
                missing_fields = cached.get("missing_fields", [])
                if isinstance(missing_fields, list):
                    self._sections[section]["missing_fields"] = missing_fields

            missing = set(self._sections[section].get("missing_fields", []))
            field_status: dict[str, dict[str, str]] = {}
            for field in SECTION_FIELDS[section]:
                path = f"{section}.{field}"
                if path in missing:
                    cached_status = problem_status.get(path, {}) if isinstance(problem_status, dict) else {}
                    field_status[path] = {
                        "status": str(cached_status.get("status", "missing")),
                        "reason": str(cached_status.get("reason", "restored_from_cache")),
                    }
                else:
                    field_status[path] = {"status": "ok", "reason": "restored_from_cache"}
            self._sections[section]["field_status"] = field_status

    def _build_rank(self, payload: dict[str, Any]) -> dict[str, Any]:
        current_sample = {
            "asset": payload.get("asset", "BTC"),
            "fetched_at": payload.get("fetched_at"),
            "metrics": self._rank_metric_values(payload),
        }
        history = list(self._history)
        if current_sample["fetched_at"] and not any(
            sample.get("fetched_at") == current_sample["fetched_at"] for sample in history
        ):
            history.append(current_sample)

        timed_samples: list[tuple[datetime, dict[str, Any]]] = []
        for sample in history:
            fetched_at = self._parse_datetime(sample.get("fetched_at"))
            if fetched_at and isinstance(sample.get("metrics"), dict):
                timed_samples.append((fetched_at, sample))

        if timed_samples:
            end_at_dt = max(dt for dt, _ in timed_samples)
            cutoff = end_at_dt - timedelta(days=self.rank_lookback_days)
            window_samples = [(dt, sample) for dt, sample in timed_samples if dt >= cutoff]
            start_at_dt = min((dt for dt, _ in window_samples), default=None)
            end_at = end_at_dt.isoformat()
            start_at = start_at_dt.isoformat() if start_at_dt else None
            window_days = (
                round((end_at_dt - start_at_dt).total_seconds() / 86400, 4) if start_at_dt else 0.0
            )
        else:
            window_samples = []
            start_at = None
            end_at = None
            window_days = 0.0

        metrics: dict[str, Any] = {}
        current_metrics = current_sample["metrics"]
        for metric_path in RANK_METRICS:
            current_value = self._as_number(current_metrics.get(metric_path))
            values = [
                value
                for _, sample in window_samples
                if (value := self._as_number(sample["metrics"].get(metric_path))) is not None
            ]
            item: dict[str, Any] = {
                "value": current_value,
                "percentile": self._percentile(values, current_value),
                "rank_pct": self._percentile_pct(values, current_value),
                "sample_count": len(values),
                "quality": self._rank_quality(len(values), window_days, current_value),
            }
            if metric_path == "gex_board.total_net_gex":
                abs_values = [abs(value) for value in values]
                abs_current = abs(current_value) if current_value is not None else None
                item["abs_percentile"] = self._percentile(abs_values, abs_current)
                item["abs_rank_pct"] = self._percentile_pct(abs_values, abs_current)
            metrics[metric_path] = item

        return {
            "window": {
                "mode": "rolling_30d_or_available",
                "lookback_days": self.rank_lookback_days,
                "sample_count": len(window_samples),
                "history_retained_count": len(timed_samples),
                "start_at": start_at,
                "end_at": end_at,
                "window_days": window_days,
            },
            "metrics": metrics,
        }

    def _rank_metric_values(self, payload: dict[str, Any]) -> dict[str, float | None]:
        gex_board = payload.get("gex_board", {})
        volatility = payload.get("volatility", {})
        flow = payload.get("flow", {})
        return {
            "gex_board.total_net_gex": self._as_number(gex_board.get("total_net_gex")),
            "gex_board.dvol": self._as_number(gex_board.get("dvol")),
            "volatility.iv_rv_ratio": self._as_number(volatility.get("iv_rv_ratio")),
            "volatility.pcr": self._as_number(volatility.get("pcr")),
            "flow.call_share_pct": self._call_share_pct(flow.get("call_put_bias")),
            "flow.put_call_ratio": self._as_number(flow.get("put_call_ratio")),
        }

    def _rank_quality(self, sample_count: int, window_days: float, current_value: float | None) -> str:
        if current_value is None or sample_count == 0:
            return "missing"
        if sample_count < 2:
            return "single_sample"
        if window_days < self.rank_lookback_days:
            return "warming_up"
        return "ok"

    def _percentile(self, values: list[float], current_value: float | None) -> float | None:
        if current_value is None or not values:
            return None
        return sum(value <= current_value for value in values) / len(values)

    def _percentile_pct(self, values: list[float], current_value: float | None) -> float | None:
        percentile = self._percentile(values, current_value)
        if percentile is None:
            return None
        return percentile * 100

    def _as_number(self, value: Any) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int | float):
            return float(value)
        return None

    def _call_share_pct(self, value: Any) -> float | None:
        if not isinstance(value, str):
            return None
        match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%\s*Call", value, re.IGNORECASE)
        if not match:
            return None
        return float(match.group(1))

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

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
