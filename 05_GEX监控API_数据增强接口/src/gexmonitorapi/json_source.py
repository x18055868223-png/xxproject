from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .models import GEX_TIME_SEMANTICS_SCHEMA, SECTION_FIELDS, SECTIONS


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
            "observed_at": None,
            "fetched_at": fetched_at,
            "latest_attempt_at": fetched_at,
            "gex_time_semantics": _flatten_time_semantics(sections),
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
        gex_fetch = fetched_at if gex else None
        volatility_fetch = fetched_at if volatility else None
        price_fetch = fetched_at if price else None
        gex_generated_at = gex.get("timestamp")
        profile_generated_at = meta.get("updateTime")
        volatility_generated_at = volatility.get("timestamp")
        price_generated_at = price.get("timestamp")

        gex_spot = _number(gex.get("asset_price"))
        price_spot = _number(price.get("price"))
        if gex_spot is not None:
            spot = gex_spot
            spot_semantics = _time_semantics(
                "gex-latest.asset_price",
                fetched_at=gex_fetch,
                generated_at=gex_generated_at,
                time_basis="upstream_result_time",
            )
        elif price_spot is not None:
            spot = price_spot
            spot_semantics = _time_semantics(
                "price.price",
                fetched_at=price_fetch,
                generated_at=price_generated_at,
                time_basis="price_endpoint_time",
            )
        else:
            spot = None
            spot_semantics = _time_semantics(
                "gex-latest.asset_price|price.price",
                fetched_at=gex_fetch or price_fetch,
                generated_at=gex_generated_at or price_generated_at,
                time_basis="source_field_missing",
            )

        top_flip = _number(gex.get("flip_point"))
        meta_flip = _number(meta.get("flip"))
        if top_flip is not None:
            flip = top_flip
            flip_semantics = _time_semantics(
                "gex-latest.flip_point",
                fetched_at=gex_fetch,
                generated_at=gex_generated_at,
                time_basis="upstream_result_time",
            )
        elif meta_flip is not None:
            flip = meta_flip
            flip_semantics = _time_semantics(
                "gex-latest.profiles.total.meta.flip",
                fetched_at=gex_fetch,
                generated_at=profile_generated_at,
                time_basis="profile_group_update_time",
            )
        else:
            flip = None
            flip_semantics = _time_semantics(
                "gex-latest.flip_point|gex-latest.profiles.total.meta.flip",
                fetched_at=gex_fetch,
                generated_at=gex_generated_at or profile_generated_at,
                time_basis="source_field_missing",
            )

        total_gex = _number(gex.get("total_gex"))
        total_gex_semantics = _time_semantics(
            "gex-latest.total_gex",
            fetched_at=gex_fetch if total_gex is not None else None,
            generated_at=gex_generated_at,
            time_basis="upstream_result_time" if total_gex is not None else "source_field_missing",
        )
        gex_dvol = _number(gex.get("dvol"))
        metrics_dvol = _number(metrics.get("dvol"))
        if gex_dvol is not None:
            dvol = gex_dvol
            dvol_semantics = _time_semantics(
                "gex-latest.dvol",
                fetched_at=gex_fetch,
                observed_at=gex.get("dvol_source_timestamp"),
                generated_at=gex.get("dvol_timestamp"),
                time_basis=(
                    "dvol_native_time"
                    if gex.get("dvol_source_timestamp") or gex.get("dvol_timestamp")
                    else "dvol_time_unknown"
                ),
            )
        elif metrics_dvol is not None:
            dvol = metrics_dvol
            dvol_semantics = _time_semantics(
                "volatility-metrics.metrics.dvol",
                fetched_at=volatility_fetch,
                generated_at=volatility_generated_at,
                time_basis="volatility_metrics_timestamp",
            )
        else:
            dvol = None
            dvol_semantics = _time_semantics(
                "gex-latest.dvol|volatility-metrics.metrics.dvol",
                fetched_at=gex_fetch or volatility_fetch,
                generated_at=gex.get("dvol_timestamp") or volatility_generated_at,
                observed_at=gex.get("dvol_source_timestamp"),
                time_basis="source_field_missing",
            )
        call_volume = _number(metrics.get("totalCallVolume"))
        put_volume = _number(metrics.get("totalPutVolume"))
        call_share = None
        if call_volume is not None and put_volume is not None and call_volume + put_volume > 0:
            call_share = call_volume / (call_volume + put_volume) * 100.0
        market_state = None
        if total_gex is not None:
            market_state = "positive_gamma" if total_gex > 0 else "negative_gamma" if total_gex < 0 else "neutral"
            market_state_semantics = _derived_time_semantics(
                "derived:total_gex_sign",
                [total_gex_semantics],
                "derived_from_total_gex",
            )
        elif spot is not None and flip is not None:
            market_state = "positive_gamma" if spot > flip else "negative_gamma"
            market_state_semantics = _derived_time_semantics(
                "derived:spot_price_vs_flip_point",
                [spot_semantics, flip_semantics],
                "derived_from_spot_and_flip",
            )
        else:
            market_state_semantics = _time_semantics(
                "derived:market_state",
                fetched_at=None,
                time_basis="source_field_missing",
            )

        magnet_a1 = _number(meta.get("magnet_a1"))
        magnet_a2 = _number(meta.get("magnet_a2"))
        if magnet_a1 is not None:
            magnet_price = magnet_a1
            magnet_source = "gex-latest.profiles.total.meta.magnet_a1"
        elif magnet_a2 is not None:
            magnet_price = magnet_a2
            magnet_source = "gex-latest.profiles.total.meta.magnet_a2"
        else:
            magnet_price = None
            magnet_source = "gex-latest.profiles.total.meta.magnet_a1|magnet_a2"

        profile_semantics: dict[str, dict[str, Any]] = {
            "n2": _time_semantics(
                "gex-latest.profiles.total.walls.n2",
                fetched_at=gex_fetch if _strike(walls.get("n2")) is not None else None,
                generated_at=profile_generated_at,
                time_basis="profile_group_update_time" if _strike(walls.get("n2")) is not None else "source_field_missing",
            ),
            "n1": _time_semantics(
                "gex-latest.profiles.total.walls.n1",
                fetched_at=gex_fetch if _strike(walls.get("n1")) is not None else None,
                generated_at=profile_generated_at,
                time_basis="profile_group_update_time" if _strike(walls.get("n1")) is not None else "source_field_missing",
            ),
            "volatility_trigger": _time_semantics(
                "gex-latest.profiles.total.meta.vol_trigger",
                fetched_at=gex_fetch if _number(meta.get("vol_trigger")) is not None else None,
                generated_at=profile_generated_at,
                time_basis=(
                    "profile_group_update_time"
                    if _number(meta.get("vol_trigger")) is not None
                    else "source_field_missing"
                ),
            ),
            "magnet_price": _time_semantics(
                magnet_source,
                fetched_at=gex_fetch if magnet_price is not None else None,
                generated_at=profile_generated_at,
                time_basis="profile_group_update_time" if magnet_price is not None else "source_field_missing",
            ),
            "p1": _time_semantics(
                "gex-latest.profiles.total.walls.p1",
                fetched_at=gex_fetch if _strike(walls.get("p1")) is not None else None,
                generated_at=profile_generated_at,
                time_basis="profile_group_update_time" if _strike(walls.get("p1")) is not None else "source_field_missing",
            ),
            "p2": _time_semantics(
                "gex-latest.profiles.total.walls.p2",
                fetched_at=gex_fetch if _strike(walls.get("p2")) is not None else None,
                generated_at=profile_generated_at,
                time_basis="profile_group_update_time" if _strike(walls.get("p2")) is not None else "source_field_missing",
            ),
        }

        volatility_metric_semantics = {
            "iv_rv_ratio": _time_semantics(
                "volatility-metrics.metrics.ivRvRatio",
                fetched_at=volatility_fetch if _number(metrics.get("ivRvRatio")) is not None else None,
                generated_at=volatility_generated_at,
                time_basis=(
                    "volatility_metrics_timestamp"
                    if _number(metrics.get("ivRvRatio")) is not None
                    else "source_field_missing"
                ),
            ),
            "pcr": _time_semantics(
                "volatility-metrics.metrics.pcrVolume",
                fetched_at=volatility_fetch if _number(metrics.get("pcrVolume")) is not None else None,
                generated_at=volatility_generated_at,
                time_basis=(
                    "volatility_metrics_timestamp"
                    if _number(metrics.get("pcrVolume")) is not None
                    else "source_field_missing"
                ),
            ),
        }
        call_put_bias_semantics = _time_semantics(
            "derived:totalCallVolume/(totalCallVolume+totalPutVolume)",
            fetched_at=volatility_fetch if call_share is not None else None,
            generated_at=volatility_generated_at,
            time_basis="derived_from_volatility_metrics" if call_share is not None else "source_field_missing",
        )

        values = {
            "gex_board": {
                "total_net_gex": total_gex,
                "dvol": dvol,
                "market_state": market_state,
            },
            "gamma_exposure": {
                "n2": _strike(walls.get("n2")),
                "n1": _strike(walls.get("n1")),
                "flip_point": flip,
                "volatility_trigger": _number(meta.get("vol_trigger")),
                "spot_price": spot,
                "magnet_price": magnet_price,
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
        time_semantics = {
            "gex_board": {
                "total_net_gex": total_gex_semantics,
                "dvol": dvol_semantics,
                "market_state": market_state_semantics,
            },
            "gamma_exposure": {
                "n2": profile_semantics["n2"],
                "n1": profile_semantics["n1"],
                "flip_point": flip_semantics,
                "volatility_trigger": profile_semantics["volatility_trigger"],
                "spot_price": spot_semantics,
                "magnet_price": profile_semantics["magnet_price"],
                "p1": profile_semantics["p1"],
                "p2": profile_semantics["p2"],
            },
            "volatility": {
                "iv_rv_ratio": volatility_metric_semantics["iv_rv_ratio"],
                "pcr": volatility_metric_semantics["pcr"],
                "term_structure": _time_semantics(
                    "unavailable",
                    fetched_at=None,
                    time_basis="unavailable",
                ),
            },
            "flow": {
                "call_premium": _time_semantics(
                    "unavailable",
                    fetched_at=None,
                    time_basis="unavailable",
                ),
                "put_premium": _time_semantics(
                    "unavailable",
                    fetched_at=None,
                    time_basis="unavailable",
                ),
                "call_put_bias": call_put_bias_semantics,
                "put_call_ratio": _time_semantics(
                    "volatility-metrics.metrics.pcrVolume",
                    fetched_at=volatility_fetch if _number(metrics.get("pcrVolume")) is not None else None,
                    generated_at=volatility_generated_at,
                    time_basis=(
                        "volatility_metrics_timestamp"
                        if _number(metrics.get("pcrVolume")) is not None
                        else "source_field_missing"
                    ),
                ),
                "abnormal_signal": _time_semantics(
                    "unavailable",
                    fetched_at=None,
                    time_basis="unavailable",
                ),
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
                        "source_ref": time_semantics[section][field]["source_ref"],
                        "derived": field in {"market_state", "call_put_bias"},
                        "observed_at": _iso_from_ms(time_semantics[section][field]["observed_at_ms"]),
                        "generated_at": _iso_from_ms(time_semantics[section][field]["generated_at_ms"]),
                        "time_basis": time_semantics[section][field]["time_basis"],
                    }
                    for field in SECTION_FIELDS[section]
                },
                "gex_time_semantics": {
                    f"{section}.{field}": time_semantics[section][field]
                    for field in SECTION_FIELDS[section]
                },
                "fetched_at": fetched_at,
                "latest_attempt_at": fetched_at,
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


def _flatten_time_semantics(sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {}
    for state in sections.values():
        semantics = state.get("gex_time_semantics")
        if isinstance(semantics, dict):
            for path, item in semantics.items():
                if isinstance(item, dict):
                    fields[path] = item
    return {"schema_version": GEX_TIME_SEMANTICS_SCHEMA, "fields": fields}


def _time_semantics(
    source_ref: str,
    *,
    fetched_at: Any = None,
    observed_at: Any = None,
    generated_at: Any = None,
    time_basis: str,
) -> dict[str, Any]:
    observed_ms, observed_errors = _time_to_ms(observed_at, source_ref, "observed_at")
    generated_ms, generated_errors = _time_to_ms(generated_at, source_ref, "generated_at")
    fetched_ms, fetched_errors = _time_to_ms(fetched_at, source_ref, "fetched_at")
    return {
        "source_ref": source_ref,
        "observed_at_ms": observed_ms,
        "generated_at_ms": generated_ms,
        "fetched_at_ms": fetched_ms,
        "time_basis": time_basis,
        "time_errors": observed_errors + generated_errors + fetched_errors,
    }


def _derived_time_semantics(
    source_ref: str,
    dependencies: list[dict[str, Any]],
    time_basis: str,
) -> dict[str, Any]:
    observed_values = [item.get("observed_at_ms") for item in dependencies if item.get("observed_at_ms") is not None]
    generated_values = [item.get("generated_at_ms") for item in dependencies if item.get("generated_at_ms") is not None]
    fetched_values = [item.get("fetched_at_ms") for item in dependencies if item.get("fetched_at_ms") is not None]
    errors: list[str] = []
    for item in dependencies:
        for error in item.get("time_errors") or []:
            if error not in errors:
                errors.append(error)
    return {
        "source_ref": source_ref,
        "observed_at_ms": max(observed_values) if len(observed_values) == len(dependencies) else None,
        "generated_at_ms": max(generated_values) if generated_values else None,
        "fetched_at_ms": max(fetched_values) if fetched_values else None,
        "time_basis": time_basis,
        "time_errors": errors,
    }


def _time_to_ms(value: Any, source_ref: str, label: str) -> tuple[int | None, list[str]]:
    if value is None or value == "":
        return None, []
    parsed: datetime | None = None
    if isinstance(value, int | float) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        try:
            parsed = datetime.fromtimestamp(timestamp, UTC)
        except (OSError, OverflowError, ValueError):
            parsed = None
    elif isinstance(value, str):
        text = value.strip()
        try:
            if re.fullmatch(r"\d+(?:\.\d+)?", text):
                timestamp = float(text)
                if timestamp > 10_000_000_000:
                    timestamp /= 1000.0
                parsed = datetime.fromtimestamp(timestamp, UTC)
            else:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (OSError, OverflowError, ValueError):
            parsed = None
    if parsed is None:
        return None, [f"{label}_parse_failed:{source_ref}"]
    if parsed.tzinfo is None:
        return None, [f"{label}_timezone_unknown:{source_ref}"]
    if parsed.timestamp() <= 0:
        return None, [f"{label}_parse_failed:{source_ref}"]
    return int(parsed.timestamp() * 1000), []


def _iso_from_ms(value: Any) -> str | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return datetime.fromtimestamp(float(value) / 1000.0, UTC).isoformat()
