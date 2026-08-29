from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .models import SECTION_FIELDS, SECTIONS


class PublicJsonSource:
    """Read the public GEX Monitor JSON feeds without a browser session."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.base_url.rstrip("/")
        self._last_snapshot: dict[str, Any] | None = None

    def source_url(self, section: str) -> str:
        mapping = {
            "gex_board": "/api/gex-latest",
            "gamma_exposure": "/api/gex-latest",
            "volatility": "/api/volatility-metrics",
            "flow": "/api/volatility-metrics",
        }
        path = mapping.get(section, "/api/gex-latest")
        params = {"asset": self.settings.asset}
        if path.endswith("gex-latest"):
            params.update({"exchange": "all", "lite": "true"})
        return self.base_url + path + "?" + urllib.parse.urlencode(params)

    async def fetch_snapshot(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_snapshot_sync)

    def _fetch_snapshot_sync(self) -> dict[str, Any]:
        fetched_at = datetime.now(UTC).isoformat()
        urls = {
            "gex": self.source_url("gex_board"),
            "volatility": self.base_url + "/api/volatility-metrics?" + urllib.parse.urlencode({"asset": self.settings.asset}),
            "options_chain": self.base_url + "/api/options-chain?" + urllib.parse.urlencode({"asset": self.settings.asset}),
            "price": self.base_url + "/api/price?" + urllib.parse.urlencode({"asset": self.settings.asset}),
        }
        errors: dict[str, str] = {}
        gex = self._get_json(urls["gex"], errors, "gex")
        volatility = self._get_json(urls["volatility"], errors, "volatility")
        price = self._get_json(urls["price"], errors, "price")
        options = {}
        if self.settings.options_chain_crosscheck:
            options = self._get_json(urls["options_chain"], errors, "options_chain")

        sections = self._build_sections(gex, volatility, price, options, fetched_at)
        metadata = {
            "source_mode": "public_json",
            "source_urls": urls,
            "errors": errors,
            "cross_check": self._cross_check(gex, options),
            "observed_at": fetched_at,
        }
        snapshot = {"sections": sections, "metadata": metadata}
        self._last_snapshot = snapshot
        return snapshot

    def _get_json(self, url: str, errors: dict[str, str], name: str) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "application/json,text/plain,*/*",
                "Referer": self.base_url + "/",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
                if getattr(response, "status", 200) != 200:
                    raise RuntimeError(f"http_status_{response.status}")
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
                return payload if isinstance(payload, dict) else {}
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
            errors[name] = f"{type(exc).__name__}: {str(exc)[:160]}"
            return {}

    def _build_sections(
        self,
        gex: dict[str, Any],
        volatility: dict[str, Any],
        price: dict[str, Any],
        options: dict[str, Any],
        fetched_at: str,
    ) -> dict[str, dict[str, Any]]:
        total = ((gex.get("profiles") or {}).get("total") or {})
        walls = total.get("walls") or {}
        meta = total.get("meta") or {}
        metrics = volatility.get("metrics") or {}
        spot = _number(gex.get("asset_price")) or _number(price.get("price"))
        flip = _number(gex.get("flip_point")) or _number(meta.get("flip"))
        total_gex = _number(gex.get("total_gex"))
        call_volume = _number(metrics.get("totalCallVolume"))
        put_volume = _number(metrics.get("totalPutVolume"))
        call_share = None
        if call_volume is not None and put_volume is not None and call_volume + put_volume > 0:
            call_share = call_volume / (call_volume + put_volume) * 100.0
        market_state = None
        if total_gex is not None:
            market_state = "positive_gamma" if total_gex > 0 else "negative_gamma" if total_gex < 0 else "neutral"
        elif spot is not None and flip is not None:
            market_state = "positive_gamma" if spot > flip else "negative_gamma"

        values = {
            "gex_board": {
                "total_net_gex": total_gex,
                "dvol": _number(gex.get("dvol")) or _number(metrics.get("dvol")),
                "market_state": market_state,
            },
            "gamma_exposure": {
                "n2": _strike(walls.get("n2")),
                "n1": _strike(walls.get("n1")),
                "flip_point": flip,
                "volatility_trigger": _number(meta.get("vol_trigger")),
                "spot_price": spot,
                "magnet_price": _number(meta.get("magnet_a1")) or _number(meta.get("magnet_a2")),
                "p1": _strike(walls.get("p1")),
                "p2": _strike(walls.get("p2")),
            },
            "volatility": {
                "iv_rv_ratio": _number(metrics.get("ivRvRatio")),
                "pcr": _number(metrics.get("pcrVolume")),
                "term_structure": [],
            },
            "flow": {
                "call_premium": None,
                "put_premium": None,
                "call_put_bias": f"{call_share:.1f}% Call" if call_share is not None else None,
                "put_call_ratio": _number(metrics.get("pcrVolume")),
                "abnormal_signal": None,
            },
        }
        sources = {
            "gex_board": {
                "total_net_gex": "gex-latest.total_gex",
                "dvol": "gex-latest.dvol|volatility-metrics.metrics.dvol",
                "market_state": "derived:total_gex_sign",
            },
            "gamma_exposure": {
                "n2": "gex-latest.profiles.total.walls.n2",
                "n1": "gex-latest.profiles.total.walls.n1",
                "flip_point": "gex-latest.flip_point",
                "volatility_trigger": "gex-latest.profiles.total.meta.vol_trigger",
                "spot_price": "gex-latest.asset_price|price.price",
                "magnet_price": "gex-latest.profiles.total.meta.magnet_a1|magnet_a2",
                "p1": "gex-latest.profiles.total.walls.p1",
                "p2": "gex-latest.profiles.total.walls.p2",
            },
            "volatility": {
                "iv_rv_ratio": "volatility-metrics.metrics.ivRvRatio",
                "pcr": "volatility-metrics.metrics.pcrVolume",
                "term_structure": "unavailable",
            },
            "flow": {
                "call_premium": "unavailable",
                "put_premium": "unavailable",
                "call_put_bias": "derived:totalCallVolume/(Call+Put)",
                "put_call_ratio": "volatility-metrics.metrics.pcrVolume",
                "abnormal_signal": "unavailable",
            },
        }
        result: dict[str, dict[str, Any]] = {}
        for section in SECTIONS:
            data = values[section]
            missing = [f"{section}.{field}" for field in SECTION_FIELDS[section] if data.get(field) is None or (isinstance(data.get(field), list) and not data.get(field))]
            result[section] = {
                "data": data,
                "missing_fields": missing,
                "field_status": {
                    f"{section}.{field}": {
                        "status": "missing" if f"{section}.{field}" in missing else "ok",
                        "reason": "not_available_from_public_json" if f"{section}.{field}" in missing else "public_json",
                        "source_ref": sources[section][field],
                        "derived": field in {"market_state", "call_put_bias"},
                        "observed_at": fetched_at,
                    }
                    for field in SECTION_FIELDS[section]
                },
                "fetched_at": fetched_at,
                "last_success_at": fetched_at,
                "source_url": self.source_url(section),
                "content_hash": None,
                "last_error": None,
            }
        return result

    def _cross_check(self, gex: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        total = ((gex.get("profiles") or {}).get("total") or {})
        walls = total.get("walls") or {}
        option_rows = [row for row in options.get("options", []) if isinstance(row, dict) and row.get("currency") == self.settings.asset]
        by_strike: dict[float, float] = {}
        spot = _number(options.get("spot_price"))
        for row in option_rows:
            strike = _number(row.get("strike")); gamma = _number(row.get("gamma")); oi = _number(row.get("open_interest")); multiplier = _number(row.get("contract_size")) or 1.0
            if strike is None or gamma is None or oi is None or spot is None:
                continue
            value = gamma * oi * multiplier * spot * spot * 0.01
            by_strike[strike] = by_strike.get(strike, 0.0) + (value if str(row.get("type")).upper() == "C" else -value)
        positives = sorted(by_strike.items(), key=lambda item: item[1], reverse=True)
        negatives = sorted(by_strike.items(), key=lambda item: item[1])
        return {
            "options_count": len(option_rows),
            "wall_strikes_match": {
                "p1": bool(positives and positives[0][0] == _strike(walls.get("p1"))),
                "n1": bool(negatives and negatives[0][0] == _strike(walls.get("n1"))),
            },
            "derived_top_positive_strike": positives[0][0] if positives else None,
            "derived_top_negative_strike": negatives[0][0] if negatives else None,
            "status": "ok" if option_rows else "unavailable",
        }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _strike(value: Any) -> float | None:
    if isinstance(value, dict):
        return _number(value.get("strike"))
    return _number(value)
