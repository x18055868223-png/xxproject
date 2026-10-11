from __future__ import annotations

import asyncio
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from .config import Settings
from .models import (
    GEX_TIME_SEMANTICS_SCHEMA,
    LIST_FIELDS,
    OPTIONAL_FIELDS,
    SECTION_FIELDS,
    SECTION_SOURCE,
    SECTIONS,
    is_empty_value,
    is_missing,
)

_WHALE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("net_flow_usd", "netFlow", "number"),
    ("buy_volume_usd", "buyVolume", "number"),
    ("sell_volume_usd", "sellVolume", "number"),
    ("execution_net_flow_usd", "executionNetFlow", "number"),
    ("total_volume_usd", "totalVolume", "number"),
    ("trade_count", "tradeCount", "int"),
    ("call_count", "callCount", "int"),
    ("put_count", "putCount", "int"),
    ("buy_call_count", "buyCallCount", "int"),
    ("sell_call_count", "sellCallCount", "int"),
    ("buy_put_count", "buyPutCount", "int"),
    ("sell_put_count", "sellPutCount", "int"),
    ("lead_expiry", "leadExpiry", "text"),
    ("active_strike", "activeStrike", "number"),
)
_NEXT_EXPIRY_TEXT = ("expiry", "expires_at")
_NEXT_EXPIRY_NUMBERS = (
    "hours_to_expiry",
    "put_call_ratio_oi",
    "max_pain_price",
    "spot_to_max_pain_pct",
    "call_oi",
    "put_oi",
    "notional_usd",
    "expiry_oi_share_pct",
)


class PublicJsonSource:
    """Read the public GEX Monitor JSON feeds without a browser session.

    Per refresh: /api/gex-latest (lite) for GEX levels and profiles,
    /api/dashboard-bundle for volatility / flow / expiry context, and the
    optional /api/options-chain wall cross-check.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.base_url.rstrip("/")
        self._last_snapshot: dict[str, Any] | None = None

    def _url(self, name: str) -> str:
        params = {"asset": self.settings.asset}
        if name == "gex":
            params.update({"exchange": "all", "lite": "true"})
            path = "/api/gex-latest"
        elif name == "bundle":
            path = "/api/dashboard-bundle"
        else:
            path = "/api/options-chain"
        return self.base_url + path + "?" + urllib.parse.urlencode(params)

    def source_url(self, section: str) -> str:
        return self._url(SECTION_SOURCE.get(section, "gex"))  # type: ignore[arg-type]

    async def fetch_snapshot(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_snapshot_sync)

    def _fetch_snapshot_sync(self) -> dict[str, Any]:
        fetched_at = datetime.now(UTC).isoformat()
        urls = {name: self._url(name) for name in ("gex", "bundle", "options_chain")}
        errors: dict[str, str] = {}
        gex = self._get_json(urls["gex"], errors, "gex")
        gex_problem = _gex_payload_problem(gex) if gex else None
        if gex_problem:
            # A 200 body that is not usable data keeps the last good gex sections.
            errors["gex"] = gex_problem
            gex = {}
        bundle = self._get_json(urls["bundle"], errors, "bundle")
        options = {}
        if self.settings.options_chain_crosscheck:
            options = self._get_json(urls["options_chain"], errors, "options_chain")

        sections = self._build_sections(gex, bundle, fetched_at)
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
        bundle: dict[str, Any],
        fetched_at: str,
    ) -> dict[str, dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {section: {} for section in SECTIONS}
        semantics: dict[str, dict[str, dict[str, Any]]] = {section: {} for section in SECTIONS}

        def put(
            section: str,
            field: str,
            value: Any,
            source_ref: str,
            *,
            fetched: str | None,
            basis: str,
            generated: Any = None,
            observed: Any = None,
        ) -> dict[str, Any]:
            present = not is_empty_value(value)
            values[section][field] = value
            item = _time_semantics(
                source_ref,
                fetched_at=fetched if present else None,
                observed_at=observed,
                generated_at=generated,
                time_basis=basis if present else "source_field_missing",
            )
            semantics[section][field] = item
            return item

        # ---- gex-latest (lite) -------------------------------------------
        gex_fetch = fetched_at if gex else None
        gex_time = gex.get("timestamp")
        profiles = _dict(gex.get("profiles"))
        total = _dict(profiles.get("total"))
        walls = _dict(total.get("walls"))
        meta = _dict(total.get("meta"))
        profile_time = meta.get("updateTime")

        # ---- dashboard-bundle --------------------------------------------
        bundle_fetch = fetched_at if bundle else None
        meta_sections = _dict(_dict(bundle.get("meta")).get("sections"))

        def bundle_time(name: str, *, meta_name: str | None = None, own: Any = None) -> tuple[Any, str]:
            # Per-section time only; the bundle-level data_timestamp is the
            # OLDEST section and must never be attributed to a single field.
            data_timestamp = _dict(meta_sections.get(meta_name or name)).get("data_timestamp")
            if data_timestamp:
                return data_timestamp, "bundle_section_data_timestamp"
            own = own if own is not None else _dict(bundle.get(name)).get("timestamp")
            return own, "bundle_section_timestamp"

        price_time, price_basis = bundle_time("price")
        vol_metrics = _dict(bundle.get("volMetrics"))
        vol_values = _dict(vol_metrics.get("metrics"))
        vol_time, vol_basis = bundle_time("volMetrics", meta_name="volatility")

        # gex_board ----------------------------------------------------------
        total_gex = _number(gex.get("total_gex"))
        total_gex_semantics = put(
            "gex_board", "total_net_gex", total_gex, "gex-latest.total_gex",
            fetched=gex_fetch, generated=gex_time, basis="upstream_result_time",
        )
        gex_dvol = _number(gex.get("dvol"))
        bundle_dvol = _number(_dict(bundle.get("dvol")).get("value"))
        index_dvol = _number(vol_values.get("dvolIndex"))
        if gex_dvol is not None:
            native = gex.get("dvol_source_timestamp") or gex.get("dvol_timestamp")
            put(
                "gex_board", "dvol", gex_dvol, "gex-latest.dvol", fetched=gex_fetch,
                observed=gex.get("dvol_source_timestamp"), generated=gex.get("dvol_timestamp"),
                basis="dvol_native_time" if native else "dvol_time_unknown",
            )
        elif bundle_dvol is not None:
            put("gex_board", "dvol", bundle_dvol, "dashboard-bundle.dvol.value",
                fetched=bundle_fetch, basis="dvol_time_unknown")
        elif index_dvol is not None:
            put("gex_board", "dvol", index_dvol, "dashboard-bundle.volMetrics.metrics.dvolIndex",
                fetched=bundle_fetch, generated=vol_time, basis=vol_basis)
        else:
            put("gex_board", "dvol", None, "gex-latest.dvol|dashboard-bundle.dvol.value",
                fetched=None, basis="source_field_missing")

        # gamma_exposure -----------------------------------------------------
        gex_spot = _number(gex.get("asset_price"))
        bundle_spot = _number(_dict(bundle.get("price")).get("price"))
        if gex_spot is not None:
            spot = gex_spot
            spot_semantics = put("gamma_exposure", "spot_price", spot, "gex-latest.asset_price",
                                 fetched=gex_fetch, generated=gex_time, basis="upstream_result_time")
        else:
            spot = bundle_spot
            spot_semantics = put("gamma_exposure", "spot_price", spot, "dashboard-bundle.price.price",
                                 fetched=bundle_fetch, generated=price_time, basis=price_basis)

        top_flip = _number(gex.get("flip_point"))
        if top_flip is not None:
            flip = top_flip
            flip_semantics = put("gamma_exposure", "flip_point", flip, "gex-latest.flip_point",
                                 fetched=gex_fetch, generated=gex_time, basis="upstream_result_time")
        else:
            flip = _number(meta.get("flip"))
            flip_semantics = put("gamma_exposure", "flip_point", flip, "gex-latest.profiles.total.meta.flip",
                                 fetched=gex_fetch, generated=profile_time, basis="profile_group_update_time")

        for field in ("n2", "n1", "p1", "p2"):
            put("gamma_exposure", field, _strike(walls.get(field)), f"gex-latest.profiles.total.walls.{field}",
                fetched=gex_fetch, generated=profile_time, basis="profile_group_update_time")
        put("gamma_exposure", "volatility_trigger", _number(meta.get("vol_trigger")),
            "gex-latest.profiles.total.meta.vol_trigger",
            fetched=gex_fetch, generated=profile_time, basis="profile_group_update_time")
        magnet_price, magnet_key = _magnet(meta)
        put("gamma_exposure", "magnet_price", magnet_price, f"gex-latest.profiles.total.meta.{magnet_key}",
            fetched=gex_fetch, generated=profile_time, basis="profile_group_update_time")
        put("gamma_exposure", "hedging_flows", _hedging_flows(gex.get("hedging_flows")), "gex-latest.hedging_flows",
            fetched=gex_fetch, generated=gex_time, basis="upstream_result_time")

        if total_gex is not None:
            market_state = "positive_gamma" if total_gex > 0 else "negative_gamma" if total_gex < 0 else "neutral"
            market_semantics = _derived_time_semantics(
                "derived:total_gex_sign", [total_gex_semantics], "derived_from_total_gex"
            )
        elif spot is not None and flip is not None:
            market_state = "positive_gamma" if spot > flip else "negative_gamma"
            market_semantics = _derived_time_semantics(
                "derived:spot_price_vs_flip_point", [spot_semantics, flip_semantics], "derived_from_spot_and_flip"
            )
        else:
            market_state = None
            market_semantics = _time_semantics("derived:market_state", time_basis="source_field_missing")
        values["gex_board"]["market_state"] = market_state
        semantics["gex_board"]["market_state"] = market_semantics

        # gamma_profiles (bucket meta.flip is a copy of total: not exposed) ---
        for field, bucket in (("zero_dte", "0dte"), ("weekly", "weekly")):
            profile = _dict(profiles.get(bucket))
            put("gamma_profiles", field, _profile(profile), f"gex-latest.profiles.{bucket}",
                fetched=gex_fetch, generated=_dict(profile.get("meta")).get("updateTime") or gex_time,
                basis="profile_group_update_time")

        # volatility ---------------------------------------------------------
        pcr_section = _dict(bundle.get("pcr"))
        pcr_rows = [row for row in pcr_section.get("data") or [] if isinstance(row, dict)]
        pcr_row = max(pcr_rows, key=_row_time_ms) if pcr_rows else {}
        pcr_time, pcr_basis = bundle_time("pcr")
        pcr_ref = "dashboard-bundle.pcr.data[latest]"
        pcr_volume = _number(pcr_row.get("pcr_volume"))
        put("volatility", "pcr", pcr_volume, f"{pcr_ref}.pcr_volume",
            fetched=bundle_fetch, generated=pcr_time, basis=pcr_basis)
        put("volatility", "pcr_oi", _number(pcr_row.get("pcr_oi")), f"{pcr_ref}.pcr_oi",
            fetched=bundle_fetch, generated=pcr_time, basis=pcr_basis)

        rr_section = _dict(bundle.get("rrSkewSummary"))
        rr_rows = [row for row in rr_section.get("rows") or [] if isinstance(row, dict)]
        exact_skew = {
            row["expiry"]: _number(row.get("rr"))
            for row in rr_rows
            if row.get("methodology") == "exact_expiry"
            and row.get("availability", "ready") == "ready"
            and isinstance(row.get("expiry"), str)
        }
        term_time, term_basis = bundle_time("ivTerm")
        term_structure = [
            {
                "expiry": row["expiry"],
                "days_to_expiry": _number(row.get("days_to_expiry")),
                "atm_iv": _number(row.get("atm_iv")),
                "skew_25d": exact_skew.get(row["expiry"]),
            }
            for row in _dict(bundle.get("ivTerm")).get("data") or []
            if isinstance(row, dict) and isinstance(row.get("expiry"), str)
        ]
        put("volatility", "term_structure", term_structure,
            "dashboard-bundle.ivTerm.data+rrSkewSummary.rows[exact_expiry].rr",
            fetched=bundle_fetch, generated=term_time, basis=term_basis)
        put("volatility", "atm_iv", _number(vol_values.get("atmIV")), "dashboard-bundle.volMetrics.metrics.atmIV",
            fetched=bundle_fetch, generated=vol_time, basis=vol_basis)
        put("volatility", "iv_percentile", _number(vol_values.get("ivPercentile")),
            "dashboard-bundle.volMetrics.metrics.ivPercentile",
            fetched=bundle_fetch, generated=vol_time, basis=vol_basis)
        rr_time, rr_basis = bundle_time("rrSkewSummary")
        rr_skew = [
            {
                "tenor": row.get("shortLabel"),
                "days": _number(row.get("daysToExpiry")),
                "expiry": row.get("expiry") if isinstance(row.get("expiry"), str) else None,
                "call_25d_iv": _number(row.get("call25dIV")),
                "put_25d_iv": _number(row.get("put25dIV")),
                "rr": _number(row.get("rr")),
                "chg_1d": _number(row.get("chg1d")),
                "chg_1w": _number(row.get("chg1w")),
                "chg_1m": _number(row.get("chg1m")),
                "methodology": row.get("methodology"),
            }
            for row in rr_rows
        ]
        put("volatility", "rr_skew", rr_skew, "dashboard-bundle.rrSkewSummary.rows",
            fetched=bundle_fetch, generated=rr_time, basis=rr_basis)

        # flow ---------------------------------------------------------------
        # put_call_ratio is an alias of volatility.pcr (volume PCR).
        put("flow", "put_call_ratio", pcr_volume, f"{pcr_ref}.pcr_volume",
            fetched=bundle_fetch, generated=pcr_time, basis=pcr_basis)
        for field, key in (
            ("call_volume", "total_call_volume"),
            ("put_volume", "total_put_volume"),
            ("call_oi", "total_call_oi"),
            ("put_oi", "total_put_oi"),
        ):
            put("flow", field, _number(pcr_row.get(key)), f"{pcr_ref}.{key}",
                fetched=bundle_fetch, generated=pcr_time, basis=pcr_basis)
        whale = _dict(bundle.get("whaleFlowSummary"))
        for field, key in (("whale_1h", "summary1h"), ("whale_24h", "summary24h")):
            put("flow", field, _whale(whale.get(key)), f"dashboard-bundle.whaleFlowSummary.{key}",
                fetched=bundle_fetch, observed=whale.get("latest_trade_timestamp"),
                generated=whale.get("timestamp"), basis="whale_summary_time")

        # expiry -------------------------------------------------------------
        max_pain = _dict(_dict(bundle.get("maxPain")).get("latest"))
        max_pain_time, max_pain_basis = bundle_time("maxPain", own=max_pain.get("timestamp"))
        put("expiry", "max_pain_price", _number(max_pain.get("max_pain_price")),
            "dashboard-bundle.maxPain.latest.max_pain_price",
            fetched=bundle_fetch, generated=max_pain_time, basis=max_pain_basis)
        target = max_pain.get("target_expiry")
        put("expiry", "max_pain_target_expiry", target if isinstance(target, str) and target else None,
            "dashboard-bundle.maxPain.latest.target_expiry",
            fetched=bundle_fetch, generated=max_pain_time, basis=max_pain_basis)
        summary_time, summary_basis = bundle_time("expirySummary")
        put("expiry", "next_expiry", _next_expiry(_dict(bundle.get("expirySummary")).get("data")),
            "dashboard-bundle.expirySummary.data",
            fetched=bundle_fetch, generated=summary_time, basis=summary_basis)

        result: dict[str, dict[str, Any]] = {}
        for section in SECTIONS:
            data: dict[str, Any] = {}
            section_semantics: dict[str, dict[str, Any]] = {}
            missing: list[str] = []
            statuses: dict[str, dict[str, Any]] = {}
            for field in SECTION_FIELDS[section]:
                path = f"{section}.{field}"
                value = values[section].get(field, [] if field in LIST_FIELDS else None)
                item = semantics[section].get(field) or _time_semantics(
                    "unavailable", time_basis="source_field_missing"
                )
                data[field] = value
                section_semantics[path] = item
                if is_missing(section, field, value):
                    missing.append(path)
                    status, reason = "missing", "not_available_from_public_json"
                elif path in OPTIONAL_FIELDS and is_empty_value(value):
                    status, reason = "optional_missing", "optional_field_absent"
                else:
                    status, reason = "ok", "public_json"
                statuses[path] = {
                    "status": status,
                    "reason": reason,
                    "source_ref": item["source_ref"],
                    "derived": field == "market_state",
                    "observed_at": _iso_from_ms(item["observed_at_ms"]),
                    "generated_at": _iso_from_ms(item["generated_at_ms"]),
                    "time_basis": item["time_basis"],
                }
            result[section] = {
                "data": data,
                "missing_fields": missing,
                "field_status": statuses,
                "gex_time_semantics": section_semantics,
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


def _gex_payload_problem(gex: dict[str, Any]) -> str | None:
    if gex.get("stale") is True:
        return "upstream_stale"
    if _number(gex.get("total_gex")) is None:
        return "invalid_payload: total_gex missing"
    if not gex.get("timestamp"):
        return "invalid_payload: timestamp missing"
    return None


def _row_time_ms(row: dict[str, Any]) -> int:
    ms, _errors = _time_to_ms(row.get("timestamp"), "dashboard-bundle.pcr.data", "timestamp")
    return ms if ms is not None else -1


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


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and math.isfinite(number) else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _magnet(meta: dict[str, Any]) -> tuple[float | None, str]:
    for key in ("magnet_a1", "magnet_a2"):
        value = _number(meta.get(key))
        if value is not None:
            return value, key
    return None, "magnet_a1|magnet_a2"


def _hedging_flows(raw: Any) -> dict[str, Any] | None:
    raw = _dict(raw)
    total_gamma = _number(raw.get("total_gamma"))
    scenarios = [
        {"price_change_pct": _number(row.get("price_change_pct")), "hedging_usd": _number(row.get("hedging_usd"))}
        for row in raw.get("scenarios") or []
        if isinstance(row, dict)
    ]
    if total_gamma is None and not scenarios:
        return None
    return {"total_gamma_usd": total_gamma, "scenarios": scenarios}


def _profile(profile: dict[str, Any]) -> dict[str, Any] | None:
    walls = _dict(profile.get("walls"))
    meta = _dict(profile.get("meta"))
    if not walls and not meta:
        return None
    wall_items = {
        key: {
            "strike": _strike(walls.get(key)),
            "gex_usd": _number(_dict(walls.get(key)).get("value")),
            "distance_pct": _number(_dict(walls.get(key)).get("distance")),
        }
        if isinstance(walls.get(key), dict)
        else None
        for key in ("p1", "p2", "n1", "n2")
    }
    return {
        **{key: _strike(walls.get(key)) for key in ("p1", "p2", "n1", "n2")},
        "walls": wall_items,
        "volatility_trigger": _number(meta.get("vol_trigger")),
        "magnet_price": _magnet(meta)[0],
    }


def _whale(raw: Any) -> dict[str, Any] | None:
    # A window with zero trades is a valid observation (all zeros), not missing.
    if not isinstance(raw, dict) or not raw:
        return None
    convert = {"number": _number, "int": _int, "text": _text}
    return {name: convert[kind](raw.get(key)) for name, key, kind in _WHALE_FIELDS}


def _next_expiry(raw: Any) -> dict[str, Any] | None:
    raw = _dict(raw)
    if not _text(raw.get("expiry")):
        return None
    result: dict[str, Any] = {key: _text(raw.get(key)) for key in _NEXT_EXPIRY_TEXT}
    result.update({key: _number(raw.get(key)) for key in _NEXT_EXPIRY_NUMBERS})
    return result
