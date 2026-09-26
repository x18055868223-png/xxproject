"""BTC MAP v2.4 pure computation helpers.

This module does not fetch data, call an LLM, rank trades, or grant execution
permission.  It turns already-qualified engine records, market references,
observations, and one optional underwriting snapshot into a versioned
``btc_map@1`` object for the workbench and manual review packet.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Mapping

if __package__:
    from .astra_commercial_math_v17 import inverse_spread_payout, static_cashflow
    from .astra_map_presentation_v241 import build_presentation
else:  # pragma: no cover - direct CLI execution from tools/
    from astra_commercial_math_v17 import inverse_spread_payout, static_cashflow
    from astra_map_presentation_v241 import build_presentation


SCHEMA = "btc_map@1"
RULE_VERSION = "astra_map_rules@2.4.0"

BACKGROUND_KEYS = ("allocation", "inventory", "financing", "external_conditions")
REFERENCE_FAMILIES = {"OPTION_STRUCTURE", "TRADED_ACCEPTANCE", "ONCHAIN_COST", "CONTRACT_LIABILITY"}
OPTION_ROLE_ORDER = ("PUT_REFERENCE", "CALL_REFERENCE", "EFFECTIVE_FLIP")
GOOD_QUALITY = {"OK", "PARTIAL", "VALID", "CURRENT"}
BAD_QUALITY = {"STALE", "INVALID", "MISSING", "ERROR", "PLANNED_NOT_CONNECTED"}
COMPARABLE_QUALIFICATIONS = {"COMPARABLE", "SAME_BASIS", "QUALIFIED_CONVERSION", "QUALIFIED"}
NOMINAL_QUALIFICATIONS = {"NOMINAL_ONLY", "GLOBAL_ONLY"}
MAX_RESPONSE_EVENTS = 80


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _copy(value: Any) -> Any:
    return deepcopy(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int_ms(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not number.is_integer() or number < 0:
        return None
    return int(number)


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _source_ids(value: Mapping[str, Any]) -> list[str]:
    raw = value.get("source_record_ids", value.get("source_record_id"))
    ids = [str(item) for item in _as_list(raw) if item not in (None, "")]
    if not ids and isinstance(value.get("source_record"), Mapping):
        record_id = value["source_record"].get("record_id")
        if record_id:
            ids.append(str(record_id))
    return sorted(dict.fromkeys(ids))


def _raw_geometry(ref: Mapping[str, Any]) -> str:
    geometry = _upper(ref.get("geometry"))
    if geometry in {"POINT", "OBSERVED_ZONE", "DISPLAY_ONLY"}:
        return geometry
    if _finite(ref.get("raw_low")) is not None and _finite(ref.get("raw_high")) is not None:
        return "OBSERVED_ZONE"
    if isinstance(ref.get("raw_zone"), (list, tuple)) and len(ref["raw_zone"]) == 2:
        return "OBSERVED_ZONE"
    if _finite(ref.get("raw_price")) is not None:
        return "POINT"
    return "DISPLAY_ONLY"


def _zone_from(value: Mapping[str, Any], *, mapped: bool = False) -> tuple[float, float] | None:
    if mapped:
        zone = value.get("mapped_zone", value.get("mapped_price_or_zone"))
        if isinstance(zone, Mapping):
            low = _finite(zone.get("low", zone.get("raw_low")))
            high = _finite(zone.get("high", zone.get("raw_high")))
        elif isinstance(zone, (list, tuple)) and len(zone) == 2:
            low, high = _finite(zone[0]), _finite(zone[1])
        else:
            price = _finite(value.get("mapped_price"))
            if price is None:
                price = _finite(value.get("mapped_price_or_zone"))
            low = high = price
    else:
        if isinstance(value.get("raw_zone"), (list, tuple)) and len(value["raw_zone"]) == 2:
            low, high = _finite(value["raw_zone"][0]), _finite(value["raw_zone"][1])
        else:
            low = _finite(value.get("raw_low"))
            high = _finite(value.get("raw_high"))
            if low is None or high is None:
                price = _finite(value.get("raw_price"))
                low = high = price
    if low is None or high is None:
        return None
    return (min(low, high), max(low, high))


def _display_center(ref: Mapping[str, Any]) -> float | None:
    own = _finite(ref.get("display_center"))
    if own is not None:
        return own
    zone = _zone_from(ref)
    if zone is not None:
        return (zone[0] + zone[1]) / 2.0
    band = ref.get("display_band")
    if isinstance(band, (list, tuple)) and len(band) == 2:
        low, high = _finite(band[0]), _finite(band[1])
        if low is not None and high is not None:
            return (low + high) / 2.0
    return None


def _has_source_clock(ref: Mapping[str, Any]) -> bool:
    return any(
        _int_ms(ref.get(key)) is not None
        for key in ("known_at_ms", "known_at", "observation_end_ms", "observation_end", "active_from_ms", "active_from")
    )


def _has_registered_mapping(ref: Mapping[str, Any]) -> bool:
    return any(ref.get(key) for key in ("mapping_version", "conversion_version", "mapping_policy_version"))


def _native_deribit_btc_usd(ref: Mapping[str, Any]) -> bool:
    quote = _upper(ref.get("quote_currency"))
    market = _upper(ref.get("market"))
    basis = _upper(ref.get("price_basis"))
    return (
        quote == "USD"
        and "DERIBIT" in market
        and ("BTC_USD" in basis or "BTC-USD" in basis or "INDEX" in basis)
        and _has_source_clock(ref)
    )


def _coordinate_qualification(ref: Mapping[str, Any], geometry: str) -> str:
    if geometry == "DISPLAY_ONLY":
        return "DISPLAY_ONLY"
    explicit = _upper(ref.get("coordinate_qualification") or ref.get("mapping_quality"))
    quote = _upper(ref.get("quote_currency"))
    basis = _upper(ref.get("price_basis"))
    if quote == "USDT" or "USDT" in basis:
        return "NOMINAL_ONLY"
    if explicit in NOMINAL_QUALIFICATIONS:
        return "NOMINAL_ONLY"
    if explicit in COMPARABLE_QUALIFICATIONS:
        if not basis or not _has_source_clock(ref):
            return "UNKNOWN"
        if _native_deribit_btc_usd(ref) or _has_registered_mapping(ref):
            return "COMPARABLE"
        return "UNKNOWN"
    if explicit in {"DISPLAY_ONLY", "UNKNOWN", "NOT_COMPARABLE"}:
        return explicit
    return "UNKNOWN"


def _usage_can_use(ref: Mapping[str, Any], now_ms: int) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    usage = ref.get("usage_decision")
    if not isinstance(usage, Mapping) or usage.get("can_use") is not True:
        reasons.append(str((usage or {}).get("status") or "usage_rejected"))
    quality = _upper(ref.get("quality", ref.get("data_state")))
    if not quality:
        reasons.append("quality_missing")
    if quality in BAD_QUALITY:
        reasons.append(quality.lower())
    if quality and quality not in GOOD_QUALITY and quality not in BAD_QUALITY:
        reasons.append("quality_unknown")
    known_at = _int_ms(ref.get("known_at_ms", ref.get("known_at")))
    active_from = _int_ms(ref.get("active_from_ms", ref.get("active_from")))
    if known_at is not None and known_at > now_ms:
        reasons.append("known_after_cutoff")
    if active_from is not None and active_from > now_ms:
        reasons.append("active_after_cutoff")
    observed = _int_ms(ref.get("observation_end_ms"))
    if observed is not None and observed > now_ms:
        reasons.append("observation_after_cutoff")
    return not reasons, sorted(set(reasons))


def _normalize_reference(raw: Mapping[str, Any], now_ms: int) -> dict[str, Any]:
    ref = dict(raw)
    family = _upper(ref.get("family"))
    if family not in REFERENCE_FAMILIES:
        family = "TRADED_ACCEPTANCE" if _upper(ref.get("source_type")) == "KPF" else family or "UNKNOWN"
    role = _upper(ref.get("role"))
    geometry = _raw_geometry(ref)
    reference_id = str(ref.get("reference_id") or "")
    source_record_ids = _source_ids(ref)
    id_basis = {
        "family": family,
        "role": role,
        "geometry": geometry,
        "raw_zone": _zone_from(ref),
        "market": ref.get("market"),
        "price_basis": ref.get("price_basis"),
        "expiry_scope": ref.get("expiry_scope"),
        "source_record_ids": source_record_ids,
        "source_family": ref.get("source_family", ref.get("provider_id")),
    }
    if not reference_id:
        reference_id = "ref_" + _hash(id_basis)[:16]
    revision_id = str(ref.get("revision_id") or ref.get("version") or _hash({
        **id_basis,
        "active_from": ref.get("active_from_ms", ref.get("active_from")),
        "observation_end": ref.get("observation_end_ms", ref.get("observation_end")),
    })[:16])
    coordinate_qualification = _coordinate_qualification(ref, geometry)
    usable, rejection_reasons = _usage_can_use(ref, now_ms)
    out = {
        "reference_id": reference_id,
        "revision_id": revision_id,
        "family": family,
        "role": role,
        "source_family": str(ref.get("source_family") or ref.get("provider_id") or family),
        "source_record_ids": source_record_ids,
        "geometry": geometry,
        "raw_price": _finite(ref.get("raw_price")),
        "raw_low": None,
        "raw_high": None,
        "display_center": _display_center(ref),
        "display_band": _copy(ref.get("display_band")),
        "market": ref.get("market"),
        "quote_currency": ref.get("quote_currency"),
        "price_basis": ref.get("price_basis"),
        "expiry_scope": ref.get("expiry_scope"),
        "observation_end_ms": _int_ms(ref.get("observation_end_ms", ref.get("observation_end"))),
        "known_at_ms": _int_ms(ref.get("known_at_ms", ref.get("known_at"))),
        "active_from_ms": _int_ms(ref.get("active_from_ms", ref.get("active_from"))),
        "quality": ref.get("quality", ref.get("data_state")),
        "usage_decision": _copy(ref.get("usage_decision")),
        "mapped_price": _finite(ref.get("mapped_price")),
        "mapped_zone": _copy(ref.get("mapped_zone")),
        "mapping_policy": ref.get("mapping_policy"),
        "mapping_quality": ref.get("mapping_quality"),
        "coordinate_qualification": coordinate_qualification,
        "eligible_for_display": usable,
        "display_rejection_reasons": rejection_reasons,
        "limitations": list(ref.get("limitations") or []),
    }
    zone = _zone_from(ref)
    if zone is not None:
        out["raw_low"], out["raw_high"] = zone
    if geometry == "DISPLAY_ONLY" and "display_only_no_strict_geometry" not in out["limitations"]:
        out["limitations"].append("display_only_no_strict_geometry")
    if coordinate_qualification in NOMINAL_QUALIFICATIONS and "nominal_only_not_contract_comparable" not in out["limitations"]:
        out["limitations"].append("nominal_only_not_contract_comparable")
    if family == "TRADED_ACCEPTANCE" and geometry != "OBSERVED_ZONE" and "acceptance_basin_unknown" not in out["limitations"]:
        out["limitations"].append("acceptance_basin_unknown")
    return out


def _current_price(snapshot: Mapping[str, Any] | None, current_price_usd: float | None = None) -> float | None:
    current = _finite(current_price_usd)
    if current is not None:
        return current
    if not isinstance(snapshot, Mapping):
        return None
    market = snapshot.get("market") if isinstance(snapshot.get("market"), Mapping) else {}
    price = _finite(market.get("reference_price_usd"))
    if price is not None:
        return price
    econ = snapshot.get("economics") if isinstance(snapshot.get("economics"), Mapping) else {}
    return _finite(econ.get("valuation_spot_usd"))


def _expiry_key(snapshot: Mapping[str, Any] | None) -> Any:
    if not isinstance(snapshot, Mapping):
        return None
    contract = snapshot.get("contract") if isinstance(snapshot.get("contract"), Mapping) else {}
    return contract.get("expiry_ms") or contract.get("expiry_at") or contract.get("expiry_at_ms")


def _option_ref_sort_key(
    ref: Mapping[str, Any],
    snapshot: Mapping[str, Any] | None,
    current_price_usd: float | None = None,
) -> tuple[int, float, str]:
    expiry = _expiry_key(snapshot)
    exact_expiry = ref.get("expiry_scope") in {expiry, str(expiry), "candidate_expiry", "selected_expiry"}
    center = _display_center(ref)
    current = _current_price(snapshot, current_price_usd)
    distance = abs(center - current) if center is not None and current is not None else float("inf")
    return (0 if exact_expiry else 1, distance, str(ref["reference_id"]))


def _distance_to_zone(ref: Mapping[str, Any], current: float | None) -> tuple[int, float, str]:
    zone = _coordinate_zone(ref, allow_nominal=False)
    if current is None or zone is None:
        center = _display_center(ref)
        return (1, abs(center - current) if center is not None and current is not None else float("inf"),
                str(ref["reference_id"]))
    low, high = zone
    if low <= current <= high:
        return (0, 0.0, str(ref["reference_id"]))
    return (1, min(abs(current - low), abs(current - high)), str(ref["reference_id"]))


def _select_references(
    pool: list[dict[str, Any]],
    snapshot: Mapping[str, Any] | None,
    current_price_usd: float | None = None,
) -> list[str]:
    eligible = [ref for ref in pool if ref.get("eligible_for_display")]
    selected: list[str] = []
    for role in OPTION_ROLE_ORDER:
        candidates = [ref for ref in eligible if ref["family"] == "OPTION_STRUCTURE" and ref["role"] == role]
        if candidates:
            selected.append(sorted(candidates, key=lambda ref: _option_ref_sort_key(ref, snapshot, current_price_usd))[0]["reference_id"])
    current = _current_price(snapshot, current_price_usd)
    traded = [ref for ref in eligible if ref["family"] == "TRADED_ACCEPTANCE"]
    current_zones = [ref for ref in traded if (_coordinate_zone(ref, allow_nominal=False) is not None
                     and _coordinate_zone(ref, allow_nominal=False)[0] <= current <= _coordinate_zone(ref, allow_nominal=False)[1])
                     ] if current is not None else []
    selected_traded: list[dict[str, Any]] = []
    if current_zones:
        selected_traded.append(sorted(current_zones, key=lambda ref: _distance_to_zone(ref, current))[0])
    for ref in sorted(traded, key=lambda item: _distance_to_zone(item, current)):
        if ref["reference_id"] not in {item["reference_id"] for item in selected_traded}:
            selected_traded.append(ref)
        if len(selected_traded) >= 2:
            break
    selected.extend(ref["reference_id"] for ref in selected_traded[:2])
    cp_sth = [ref for ref in eligible if ref["family"] == "ONCHAIN_COST" and ref["role"] == "CP_STH"]
    if cp_sth:
        selected.append(sorted(cp_sth, key=lambda ref: (ref.get("known_at_ms") or 0, ref["reference_id"]), reverse=True)[0]["reference_id"])
    return list(dict.fromkeys(selected))[:6]


def _coordinate_zone(ref: Mapping[str, Any], *, allow_nominal: bool) -> tuple[float, float] | None:
    qualification = ref.get("coordinate_qualification")
    if qualification == "DISPLAY_ONLY":
        return None
    if qualification not in COMPARABLE_QUALIFICATIONS and not (allow_nominal and qualification in NOMINAL_QUALIFICATIONS):
        return None
    mapped = _zone_from(ref, mapped=True)
    if mapped is not None:
        return mapped
    raw = _zone_from(ref)
    return raw


def _position_for_zone(zone: tuple[float, float], price: float, *, point: bool = False) -> str:
    low, high = zone
    if point or math.isclose(low, high, rel_tol=0.0, abs_tol=1e-12):
        target = (low + high) / 2.0
        if math.isclose(price, target, rel_tol=0.0, abs_tol=1e-12):
            return "AT"
        return "BELOW" if price < target else "ABOVE"
    if price < low:
        return "BELOW"
    if price > high:
        return "ABOVE"
    return "INSIDE"


def _event_type(prev: str | None, current: str, geometry: str) -> str:
    if prev is None or prev == current:
        return "POSITION_OBSERVED"
    if geometry == "POINT":
        return "POINT_SIDE_CHANGE"
    if prev in {"BELOW", "ABOVE"} and current == "INSIDE":
        return "ENTERED_REFERENCE"
    if prev == "INSIDE" and current in {"BELOW", "ABOVE"}:
        return "LEFT_REFERENCE"
    if {prev, current} == {"BELOW", "ABOVE"}:
        return "SAMPLED_CROSSING_BETWEEN_SAMPLES"
    return "POSITION_CHANGED"


def _normalize_observation(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reference_id": str(raw.get("reference_id") or ""),
        "revision_id": str(raw.get("revision_id") or ""),
        "observed_at_ms": _int_ms(raw.get("observed_at_ms", raw.get("event_at_ms"))),
        "known_at_ms": _int_ms(raw.get("known_at_ms", raw.get("retrieved_at_ms"))),
        "window_start_ms": _int_ms(raw.get("window_start_ms")),
        "window_end_ms": _int_ms(raw.get("window_end_ms")),
        "price": _finite(raw.get("price")),
        "open": _finite(raw.get("open")),
        "high": _finite(raw.get("high")),
        "low": _finite(raw.get("low")),
        "close": _finite(raw.get("close")),
        "method": _upper(raw.get("method") or raw.get("observation_method") or "PRICE_SAMPLE"),
        "coverage": _upper(raw.get("coverage") or "UNKNOWN"),
        "price_source": raw.get("price_source"),
        "market": raw.get("market"),
        "price_basis": raw.get("price_basis"),
        "active_flow_delta": _finite(raw.get("active_flow_delta")),
        "net_price_change": _finite(raw.get("net_price_change")),
    }


def _observation_basis_matches(ref: Mapping[str, Any], obs: Mapping[str, Any]) -> bool:
    for key in ("market", "price_basis"):
        obs_value = _upper(obs.get(key))
        ref_value = _upper(ref.get(key))
        if not obs_value or not ref_value or obs_value != ref_value:
            return False
    return True


def _build_response_events(
    pool: list[dict[str, Any]],
    observations: Any,
    prior_state: Mapping[str, Any] | None,
    now_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    refs_by_id = {ref["reference_id"]: ref for ref in pool}
    state = _copy(prior_state) if isinstance(prior_state, Mapping) else {}
    prior_segments = state.get("segments") if isinstance(state.get("segments"), Mapping) else {}
    events = list(state.get("events") or [])
    segments: dict[str, Any] = {}
    for ref in pool:
        prior = prior_segments.get(ref["reference_id"]) if isinstance(prior_segments, Mapping) else None
        if isinstance(prior, Mapping) and prior.get("revision_id") != ref["revision_id"]:
            events.append({
                "event_type": "REFERENCE_REVISION_CHANGED",
                "reference_id": ref["reference_id"],
                "previous_revision_id": prior.get("revision_id"),
                "revision_id": ref["revision_id"],
                "event_at_ms": ref.get("known_at_ms"),
                "reason": "reference_moved_not_price_break",
            })
        segments[ref["reference_id"]] = {
            "reference_id": ref["reference_id"],
            "revision_id": ref["revision_id"],
            "last_position": prior.get("last_position") if isinstance(prior, Mapping) and prior.get("revision_id") == ref["revision_id"] else None,
            "last_event_at_ms": prior.get("last_event_at_ms") if isinstance(prior, Mapping) and prior.get("revision_id") == ref["revision_id"] else None,
        }
    normalized = [_normalize_observation(item) for item in _as_list(observations) if isinstance(item, Mapping)]
    normalized.sort(key=lambda item: (item.get("known_at_ms") or item.get("observed_at_ms") or 0, item["reference_id"]))
    for obs in normalized:
        if obs["known_at_ms"] is not None and obs["known_at_ms"] > now_ms:
            continue
        ref = refs_by_id.get(obs["reference_id"])
        if ref is None:
            events.append({"event_type": "OBSERVATION_REFERENCE_UNKNOWN", **obs})
            continue
        if obs["revision_id"] and obs["revision_id"] != ref["revision_id"]:
            events.append({"event_type": "OBSERVATION_REVISION_MISMATCH", **obs})
            continue
        if not ref.get("eligible_for_display"):
            events.append({"event_type": "REFERENCE_NOT_QUALIFIED_FOR_RESPONSE", **obs,
                           "reason": "reference_usage_not_qualified"})
            continue
        if not _observation_basis_matches(ref, obs):
            events.append({"event_type": "OBSERVATION_PRICE_BASIS_MISMATCH", **obs,
                           "reason": "observation_market_or_basis_differs_from_reference"})
            continue
        zone = _coordinate_zone(ref, allow_nominal=False)
        if zone is None:
            events.append({"event_type": "REFERENCE_NOT_STRICTLY_OBSERVABLE", **obs})
            continue
        event_at = obs.get("observed_at_ms") or obs.get("window_end_ms") or obs.get("known_at_ms")
        if event_at is not None and event_at > now_ms:
            continue
        earliest = max(ref.get("known_at_ms") or 0, ref.get("active_from_ms") or 0)
        window_start = obs.get("window_start_ms") if obs["method"] in {"OHLC", "KLINE", "CANDLE"} else event_at
        if event_at is None or (window_start is not None and window_start < earliest) or event_at < earliest:
            events.append({"event_type": "POST_HOC_BACKGROUND_ONLY", **obs,
                           "reason": "observation_before_reference_known_and_active"})
            continue
        last_event_at = segments[ref["reference_id"]].get("last_event_at_ms")
        if last_event_at is not None and event_at <= last_event_at:
            continue
        if obs["coverage"] in {"GAP", "UNKNOWN", "DISCONNECTED"}:
            events.append({"event_type": "INTERVAL_UNKNOWN", **obs,
                           "reason": "price_source_gap_no_continuous_path"})
            segments[ref["reference_id"]]["last_position"] = "UNKNOWN"
            segments[ref["reference_id"]]["last_event_at_ms"] = event_at
            continue
        geometry = ref["geometry"]
        if obs["method"] in {"OHLC", "KLINE", "CANDLE"} and obs["high"] is not None and obs["low"] is not None:
            if obs["high"] >= zone[0] and obs["low"] <= zone[1]:
                events.append({"event_type": "RANGE_INTERSECTS", **obs,
                               "current_position": None,
                               "reason": "ohlc_range_intersects_without_intrabar_sequence"})
            if obs["close"] is None:
                continue
            price = obs["close"]
            method_event = "CLOSE_SIDE_CHANGE"
        else:
            price = obs["price"] if obs["price"] is not None else obs["close"]
            method_event = None
        if price is None:
            continue
        position = _position_for_zone(zone, price, point=geometry == "POINT")
        prior_position = segments[ref["reference_id"]].get("last_position")
        typ = method_event if method_event and prior_position not in (None, position) else _event_type(prior_position, position, geometry)
        event = {"event_type": typ, **obs, "price": price,
                 "current_position": position, "previous_position": prior_position,
                 "reference_revision": ref["revision_id"]}
        # A repeated side is a current sample, not a new semantic response.
        if position != prior_position:
            events.append(event)
        segments[ref["reference_id"]]["last_position"] = position
        segments[ref["reference_id"]]["last_event_at_ms"] = event_at
    events = events[-MAX_RESPONSE_EVENTS:]
    return events, {
        "schema": "btc_map_response_state@1",
        "rule_version": RULE_VERSION,
        "updated_at_ms": now_ms,
        "segments": segments,
        "events": events,
        "event_limit": MAX_RESPONSE_EVENTS,
    }


def _co_location(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, left in enumerate(pool):
        for right in pool[index + 1:]:
            if left["family"] == "CONTRACT_LIABILITY" or right["family"] == "CONTRACT_LIABILITY":
                evidence = "contract_line_not_market_evidence"
            elif left.get("source_family") == right.get("source_family"):
                evidence = "same_source_family_not_independent"
            else:
                evidence = "separate_source_family"
            if left["coordinate_qualification"] == "DISPLAY_ONLY" or right["coordinate_qualification"] == "DISPLAY_ONLY":
                out.append({"left_id": left["reference_id"], "right_id": right["reference_id"],
                            "relation": "NOT_STRICT", "reason": "display_only_geometry",
                            "independent_evidence": evidence == "separate_source_family"})
                continue
            if left["coordinate_qualification"] not in COMPARABLE_QUALIFICATIONS or right["coordinate_qualification"] not in COMPARABLE_QUALIFICATIONS:
                out.append({"left_id": left["reference_id"], "right_id": right["reference_id"],
                            "relation": "NOT_COMPARABLE", "reason": "coordinate_basis_not_qualified",
                            "independent_evidence": evidence == "separate_source_family"})
                continue
            lz, rz = _coordinate_zone(left, allow_nominal=False), _coordinate_zone(right, allow_nominal=False)
            if lz is None or rz is None:
                continue
            low, high = max(lz[0], rz[0]), min(lz[1], rz[1])
            if low < high:
                relation = "OVERLAPS"
            elif math.isclose(low, high, rel_tol=0.0, abs_tol=1e-12):
                relation = "BOUNDARY_TOUCH"
            else:
                relation = "SEPARATE"
            out.append({"left_id": left["reference_id"], "right_id": right["reference_id"],
                        "relation": relation, "intersection": [low, high] if relation != "SEPARATE" else None,
                        "raw_distance": 0.0 if relation != "SEPARATE" else min(abs(lz[0] - rz[1]), abs(rz[0] - lz[1])),
                        "evidence_note": evidence,
                        "independent_evidence": evidence == "separate_source_family"})
    return out


def _contract_from_snapshot(snapshot: Mapping[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(snapshot, Mapping):
        return None, ["candidate_not_selected"]
    contract = snapshot.get("contract") if isinstance(snapshot.get("contract"), Mapping) else {}
    side = str(contract.get("side") or contract.get("put_or_call") or "").lower()
    ks = _finite(contract.get("short_strike_usd", contract.get("Ks")))
    kl = _finite(contract.get("long_strike_usd", contract.get("Kl")))
    quantity = _finite(contract.get("quantity_btc", contract.get("quantity")))
    reasons: list[str] = []
    if side not in {"put", "call"} or ks is None or kl is None or quantity is None:
        return None, ["unsupported_or_incomplete_contract"]
    if min(ks, kl, quantity) <= 0:
        return None, ["contract_values_must_be_positive"]
    if side == "put" and not ks > kl:
        reasons.append("put_requires_short_above_long")
    if side == "call" and not ks < kl:
        reasons.append("call_requires_short_below_long")
    settlement = contract.get("settlement_currency", "BTC")
    quote = contract.get("quote_currency", "BTC")
    if settlement != "BTC" or quote not in {"BTC", None}:
        reasons.append("only_btc_inverse_supported")
    if contract.get("contract_size", 1.0) not in {1, 1.0, None}:
        reasons.append("contract_multiplier_not_supported")
    if reasons:
        return None, sorted(set(reasons))
    return {
        "side": side,
        "short_strike_usd": ks,
        "long_strike_usd": kl,
        "quantity_btc": quantity,
        "expiry_ms": contract.get("expiry_ms", contract.get("expiry_at_ms")),
        "candidate_id": snapshot.get("candidate_id", contract.get("candidate_id")),
        "candidate_version": snapshot.get("snapshot_id", contract.get("candidate_version")),
    }, []


def _near_be(snapshot: Mapping[str, Any] | None, contract: Mapping[str, Any]) -> float | None:
    if not isinstance(snapshot, Mapping):
        return None
    liability = snapshot.get("liability") if isinstance(snapshot.get("liability"), Mapping) else {}
    roots = [_finite(item) for item in _as_list(liability.get("breakeven_usd"))]
    roots = [item for item in roots if item is not None]
    if contract["side"] == "put":
        lo, hi = contract["long_strike_usd"], contract["short_strike_usd"]
    else:
        lo, hi = contract["short_strike_usd"], contract["long_strike_usd"]
    inside = [root for root in roots if lo <= root <= hi]
    if inside:
        return sorted(inside, key=lambda value: abs(value - contract["short_strike_usd"]))[0]
    return None


def _relation_label(contract: Mapping[str, Any], price: float, be: float | None) -> str:
    ks, kl, side = contract["short_strike_usd"], contract["long_strike_usd"], contract["side"]
    if side == "put":
        if price >= ks:
            base = "before_payout"
        elif price > kl:
            base = "partial_payout"
        else:
            base = "beyond_protection"
    else:
        if price <= ks:
            base = "before_payout"
        elif price < kl:
            base = "partial_payout"
        else:
            base = "beyond_protection"
    if be is not None and math.isclose(price, be, rel_tol=0.0, abs_tol=1e-8):
        return base + "_at_near_be"
    return base


def _points_for_zone(zone: tuple[float, float], contract: Mapping[str, Any], be: float | None) -> list[float]:
    low, high = zone
    points = [low, high]
    for point in (contract["short_strike_usd"], contract["long_strike_usd"], be):
        if point is not None and low <= point <= high:
            points.append(float(point))
    return sorted(dict.fromkeys(points))


def _range(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {"min_btc": min(values), "max_btc": max(values)}


def _cost_scope(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    fee = snapshot.get("fee") if isinstance(snapshot.get("fee"), Mapping) else {}
    covers = sorted(str(item) for item in _as_list(fee.get("covers")))
    if "entry" in covers and "delivery" in covers:
        title = "hold_to_expiry_static_result_with_listed_fees"
    elif covers:
        title = "_".join(covers) + "_only_static_result_not_full_actual_net"
    else:
        title = "fee_scope_unknown_static_result_unavailable"
    return {"title": title, "covers": covers, "fee_basis": fee.get("basis")}


def _liability_row(
    ref: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    contract: Mapping[str, Any],
    current: float | None,
    *,
    allow_nominal: bool,
) -> dict[str, Any]:
    zone = _coordinate_zone(ref, allow_nominal=allow_nominal)
    be = _near_be(snapshot, contract)
    qualification = ref.get("coordinate_qualification")
    row = {
        "region_id": ref["reference_id"],
        "version": ref["revision_id"],
        "family": ref["family"],
        "role": ref["role"],
        "coordinate_qualification": qualification,
        "geometry": ref["geometry"],
        "position_vs_current": None,
        "relation_to_short_long_be": None,
        "distance_to_short_usd": None,
        "distance_to_near_be_usd": None,
        "terminal_payout_range": None,
        "static_net_range": None,
        "cost_scope": _cost_scope(snapshot),
        "response_facts": [],
        "source_cutoffs": {"known_at_ms": ref.get("known_at_ms"), "observation_end_ms": ref.get("observation_end_ms")},
        "limitations": list(ref.get("limitations") or []),
        "terminal_scenario_basis": None,
    }
    if not ref.get("eligible_for_display"):
        row["limitations"].append("reference_not_qualified_for_policy_link")
        return row
    if zone is None:
        row["limitations"].append("no_contract_comparable_coordinate")
        return row
    low, high = zone
    if current is not None:
        if high < current:
            row["position_vs_current"] = "BELOW_CURRENT"
        elif low > current:
            row["position_vs_current"] = "ABOVE_CURRENT"
        else:
            row["position_vs_current"] = "CONTAINS_CURRENT"
    row["distance_to_short_usd"] = [low - contract["short_strike_usd"], high - contract["short_strike_usd"]]
    row["distance_to_near_be_usd"] = None if be is None else [low - be, high - be]
    labels = []
    for point in _points_for_zone(zone, contract, be):
        label = _relation_label(contract, point, be)
        labels.append(label)
        if label.endswith("_at_near_be"):
            labels.append(label.removesuffix("_at_near_be"))
    row["relation_to_short_long_be"] = sorted(dict.fromkeys(labels))
    if be is not None and low <= be <= high:
        row["contains_near_breakeven"] = True
    points = _points_for_zone(zone, contract, be)
    payout_values = [
        inverse_spread_payout(contract["side"], contract["short_strike_usd"], contract["long_strike_usd"],
                              point, contract["quantity_btc"])
        for point in points
    ]
    row["terminal_payout_range"] = _range(payout_values)
    row["terminal_payout_points"] = [
        {"settlement_usd": point, "payout_btc": payout}
        for point, payout in zip(points, payout_values)
    ]
    fee = snapshot.get("fee") if isinstance(snapshot.get("fee"), Mapping) else {}
    econ = snapshot.get("economics") if isinstance(snapshot.get("economics"), Mapping) else {}
    credit = _finite(econ.get("visible_credit_btc", econ.get("premium_spread_btc")))
    fee_btc = _finite(fee.get("amount_btc"))
    if credit is not None and fee_btc is not None:
        static_values = [static_cashflow(credit, payout, fee_btc)["static_net_btc"] for payout in payout_values]
        row["static_net_range"] = _range([item for item in static_values if item is not None])
    else:
        row["limitations"].append("static_net_unavailable_fee_or_credit_missing")
    if qualification in NOMINAL_QUALIFICATIONS:
        row["position_vs_current"] = None
        row["relation_to_short_long_be"] = None
        row["distance_to_short_usd"] = None
        row["distance_to_near_be_usd"] = None
        row.pop("contains_near_breakeven", None)
        row["terminal_scenario_basis"] = "nominal_number_if_official_settlement_equaled_it"
        row["limitations"].append("nominal_scenario_not_precise_contract_mapping")
    else:
        row["terminal_scenario_basis"] = "official_settlement_inside_contract_comparable_coordinate"
    return row


def _row_priority(row: Mapping[str, Any], contract: Mapping[str, Any], current: float | None) -> tuple[int, float, str]:
    relation = set(row.get("relation_to_short_long_be") or [])
    position = row.get("position_vs_current")
    if current is None:
        distance = float("inf")
    else:
        dist = row.get("distance_to_short_usd")
        distance = min(abs(item) for item in dist) if isinstance(dist, list) and dist else float("inf")
    adverse = ((contract["side"] == "put" and position == "BELOW_CURRENT")
               or (contract["side"] == "call" and position == "ABOVE_CURRENT"))
    if adverse and "before_payout" in relation:
        bucket = 0
    elif any(item.startswith("partial_payout") for item in relation) or row.get("contains_near_breakeven"):
        bucket = 1
    elif "beyond_protection" in relation:
        bucket = 2
    else:
        bucket = 3
    return (bucket, distance, str(row.get("region_id")))


def _policy_link(
    snapshot: Mapping[str, Any] | None,
    pool: list[dict[str, Any]],
    events: list[dict[str, Any]],
    current_price_usd: float | None = None,
) -> dict[str, Any]:
    contract, reasons = _contract_from_snapshot(snapshot)
    if contract is None:
        return {
            "candidate_id": None,
            "candidate_version": None,
            "valuation_version": None,
            "region_liability_rows": [],
            "region_liability_full_rows": [],
            "account_risk_not_covered": sorted(set(reasons)),
        }
    assert snapshot is not None
    current = _current_price(snapshot, current_price_usd)
    full_rows = []
    events_by_ref: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        events_by_ref.setdefault(str(event.get("reference_id") or ""), []).append(event)
    for ref in pool:
        if ref["family"] == "CONTRACT_LIABILITY":
            continue
        row = _liability_row(ref, snapshot, contract, current, allow_nominal=True)
        row["response_facts"] = events_by_ref.get(ref["reference_id"], [])[-5:]
        full_rows.append(row)
    comparable_rows = [
        row for row in full_rows
        if row["coordinate_qualification"] in COMPARABLE_QUALIFICATIONS
        and row.get("terminal_payout_range") is not None
    ]
    rows = sorted(comparable_rows, key=lambda row: _row_priority(row, contract, current))[:3]
    if len(rows) < 3:
        for row in sorted(full_rows, key=lambda item: (item["coordinate_qualification"] not in NOMINAL_QUALIFICATIONS, item["region_id"])):
            if row not in rows:
                rows.append(row)
            if len(rows) >= 3:
                break
    return {
        "candidate_id": contract["candidate_id"],
        "candidate_version": contract["candidate_version"],
        "valuation_version": snapshot.get("snapshot_id"),
        "contract": {
            "side": contract["side"],
            "short_strike_usd": contract["short_strike_usd"],
            "long_strike_usd": contract["long_strike_usd"],
            "quantity_btc": contract["quantity_btc"],
            "expiry_ms": contract["expiry_ms"],
        },
        "mu_ref_btc": (snapshot.get("economics") or {}).get("mu_ref_btc") if isinstance(snapshot.get("economics"), Mapping) else None,
        "M_ref_btc": (snapshot.get("economics") or {}).get("reference_margin_btc") if isinstance(snapshot.get("economics"), Mapping) else None,
        "region_liability_rows": rows[:3],
        "region_liability_full_rows": full_rows,
        "account_risk_not_covered": [
            "early_exit_cashflow",
            "slippage",
            "margin_liquidation",
            "hedge_cashflow",
            "touch_is_not_terminal_settlement",
        ],
    }


def _background(background: Any) -> dict[str, Any]:
    source = background if isinstance(background, Mapping) else {}
    out = {}
    for key in BACKGROUND_KEYS:
        row = source.get(key) if isinstance(source.get(key), Mapping) else {}
        out[key] = {
            "status": row.get("status", "missing"),
            "summary": row.get("summary"),
            "summary_cn": row.get("summary_cn"),
            "metrics": _copy(row.get("metrics")),
            "cutoff_at_ms": _int_ms(row.get("cutoff_at_ms")),
            "missing": _copy(row.get("missing")),
            "fact_state": row.get("fact_state"),
            "facts": _copy(row.get("facts", [])),
            "source_record_ids": _source_ids(row),
            "known_at_ms": _int_ms(row.get("known_at_ms", row.get("known_at"))),
            "active_from_ms": _int_ms(row.get("active_from_ms", row.get("active_from"))),
            "limitations": list(row.get("limitations") or ([] if row else ["background_row_missing"])),
        }
    out["facts_changed_since"] = _copy(source.get("facts_changed_since", []))
    out["missing_or_conflicting"] = _copy(source.get("missing_or_conflicting", []))
    return out


def _extract_records(value: Any) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        if value.get("schema") == "astra_data_record@2.3.0":
            records.append(value)
        for key in ("data_record", "source_record"):
            if isinstance(value.get(key), Mapping):
                records.extend(_extract_records(value[key]))
        if isinstance(value.get("source_records"), list):
            for item in value["source_records"]:
                records.extend(_extract_records(item))
    elif isinstance(value, list):
        for item in value:
            records.extend(_extract_records(item))
    return records


def _source_manifest(engine: Any, now_ms: int, snapshot: Mapping[str, Any] | None, values: list[Any]) -> dict[str, Any]:
    for value in values:
        if isinstance(value, Mapping) and isinstance(value.get("source_manifest"), Mapping):
            supplied = value["source_manifest"]
            candidate = snapshot.get("candidate_id") if isinstance(snapshot, Mapping) else None
            if (supplied.get("cutoff_at_ms") != now_ms or supplied.get("candidate_id") != candidate
                    or supplied.get("manifest_hash") != _hash({k: v for k, v in supplied.items() if k != "manifest_hash"})):
                raise ValueError("supplied MAP source manifest is not the same valid freeze")
            return _copy(supplied)
    records: list[Mapping[str, Any]] = []
    for value in values:
        records.extend(_extract_records(value))
    candidate_id = snapshot.get("candidate_id") if isinstance(snapshot, Mapping) else None
    if records and engine is not None and hasattr(engine, "freeze"):
        return engine.freeze(records, cutoff_at_ms=now_ms, candidate_id=candidate_id)
    source_refs: list[dict[str, Any]] = []
    for record in records:
        if record.get("record_id"):
            source_refs.append({"record_id": record.get("record_id")})
    for value in values:
        if isinstance(value, Mapping):
            ids = _source_ids(value)
            source_refs.extend({"record_id": item} for item in ids)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    source_refs.extend({"record_id": rid} for rid in _source_ids(item))
    body = {
        "schema": "btc_map_source_manifest@1",
        "cutoff_at_ms": now_ms,
        "candidate_id": candidate_id,
        "source_records": sorted(source_refs, key=lambda item: item.get("record_id") or ""),
        "note": "caller_supplied_records_without_engine_freeze" if source_refs else "no_source_records_supplied",
    }
    body["manifest_hash"] = _hash(body)
    return body


def build_map(
    engine: Any,
    *,
    now_ms: int,
    snapshot: Mapping[str, Any] | None = None,
    background: Mapping[str, Any] | None = None,
    references: list[Mapping[str, Any]] | None = None,
    observations: list[Mapping[str, Any]] | None = None,
    prior_state: Mapping[str, Any] | None = None,
    current_price_usd: float | None = None,
    current_price_basis: str | None = None,
    presentation_mode: str | None = None,
) -> dict[str, Any]:
    """Build a frozen BTC MAP object from already-provided inputs.

    ``engine`` is optional and is used only to freeze caller-supplied
    ``astra_data_record@2.3.0`` records when present.  This function never
    calls ``fetch`` or performs network I/O.
    """
    cutoff = _int_ms(now_ms)
    if cutoff is None:
        raise ValueError("now_ms must be an integer millisecond timestamp")
    current = _current_price(snapshot, current_price_usd)
    if current_price_basis is None and isinstance(snapshot, Mapping):
        market = snapshot.get("market") if isinstance(snapshot.get("market"), Mapping) else {}
        current_price_basis = market.get("reference_source") or market.get("price_basis")
    pool = [_normalize_reference(ref, cutoff) for ref in _as_list(references) if isinstance(ref, Mapping)]
    contract, _ = _contract_from_snapshot(snapshot)
    if contract is not None:
        contract_points = [("SHORT_LEG", contract["short_strike_usd"]),
                           ("PROTECTIVE_LEG", contract["long_strike_usd"]),
                           ("NEAR_BREAKEVEN", _near_be(snapshot, contract))]
        for role, price in contract_points:
            if price is None:
                continue
            pool.append(_normalize_reference({
                "reference_id": "contract_" + role.lower(),
                "revision_id": snapshot.get("snapshot_id"), "family": "CONTRACT_LIABILITY",
                "role": role, "source_family": "contract", "geometry": "POINT", "raw_price": price,
                "market": "Deribit", "quote_currency": "USD", "price_basis": "DERIBIT_BTC_USD_INDEX",
                "expiry_scope": contract["expiry_ms"], "known_at_ms": cutoff, "active_from_ms": cutoff,
                "observation_end_ms": cutoff,
                "quality": "OK", "usage_decision": {"can_use": True},
                "coordinate_qualification": "COMPARABLE",
                "limitations": ["contract_line_not_independent_market_confirmation"],
            }, cutoff))
    display_reference_ids = _select_references(pool, snapshot, current)
    for ref in pool:
        ref["selected_for_display"] = ref["reference_id"] in display_reference_ids
    response_events, response_state = _build_response_events(pool, observations, prior_state, cutoff)
    body = {
        "schema": SCHEMA,
        "rule_version": RULE_VERSION,
        "cutoff_at_ms": cutoff,
        "current_price_usd": current,
        "price_basis": current_price_basis,
        "price_identity": {
            "current_price_usd": current,
            "price_basis": current_price_basis,
            "source": "explicit_argument" if _finite(current_price_usd) is not None else "snapshot" if snapshot else None,
        },
        "source_manifest": _source_manifest(engine, cutoff, snapshot, [background, references, observations]),
        "background": _background(background),
        "reference_pool": pool,
        "display_reference_ids": display_reference_ids,
        "co_location": _co_location(pool),
        "response_events": response_events,
        "response_state": response_state,
        "policy_link": _policy_link(snapshot, pool, response_events, current),
        "interpretation_mode": "FACTS_UNTIL_MANUAL_REVIEW",
        "automation": {"llm_calls": 0, "http_fetches": 0, "execution_permission_changed": False},
    }
    body["presentation"] = build_presentation(body, snapshot,
        presentation_mode=presentation_mode or ("LIVE" if engine is not None else None))
    body["map_id"] = _hash(body)
    return body
