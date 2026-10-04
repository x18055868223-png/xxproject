"""Deterministic read-only candidate discovery for Astra underwriting v2.0.

The discovery layer only turns a Deribit BTC option chain and one btc_usd index
price into at most two explicit vertical definitions. It does not fetch books,
choose a side, price risk, call an LLM, or grant execution permission.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

SCHEMA = "astra_underwriting_discovery@2.0.0"
DERIBIT_BASE_URL = "https://www.deribit.com/api/v2"
TARGET_HOURS = 24.0
DEFAULT_WIDTH_USD = 2_000.0
DEFAULT_QUANTITY_BTC = 1.0
DEFAULT_SHORT_DELTA_TARGET_ABS = 0.35
DEFAULT_SHORT_DELTA_MAX_ABS = 0.5
DEFAULT_DELTA_MAX_AGE_MS = 300_000

_NAME_RE = re.compile(r"BTC-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:\.\d+)?)-([PC])")
_MONTHS = {name: index for index, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _number(name: str, value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} must be {'positive and ' if positive else ''}finite")
    return number


def _integer(name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    number = _number(name, value)
    if not number.is_integer():
        raise ValueError(f"{name} must be an integer")
    return int(number)


def _named_expiry_ms(name: str) -> tuple[int, float, str] | None:
    match = _NAME_RE.fullmatch(name)
    if match is None or match.group(2) not in _MONTHS:
        return None
    expiry = datetime(2000 + int(match.group(3)), _MONTHS[match.group(2)],
                      int(match.group(1)), 8, tzinfo=timezone.utc)
    side = "put" if match.group(5) == "P" else "call"
    return int(expiry.timestamp() * 1000), float(match.group(4)), side


def _reject(raw: Any, reasons: list[str]) -> dict[str, Any]:
    name = raw.get("instrument_name") if isinstance(raw, Mapping) else None
    return {"instrument_name": name, "reasons": sorted(set(reasons))}


def _normalize(raw: Any, captured_ts_ms: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    reasons: list[str] = []
    if not isinstance(raw, Mapping):
        return None, {"instrument_name": None, "reasons": ["instrument_not_object"]}
    name = str(raw.get("instrument_name") or "")
    parsed = _named_expiry_ms(name)
    if parsed is None:
        reasons.append("instrument_name_invalid")
        named_expiry = strike_from_name = side_from_name = None
    else:
        named_expiry, strike_from_name, side_from_name = parsed
    if raw.get("kind") != "option":
        reasons.append("kind_not_option")
    if raw.get("base_currency") != "BTC":
        reasons.append("base_not_btc")
    if raw.get("settlement_currency") != "BTC" or raw.get("quote_currency") != "BTC":
        reasons.append("not_btc_settled_inverse")
    if raw.get("counter_currency") != "USD" or raw.get("price_index") != "btc_usd":
        reasons.append("not_btc_usd_indexed")
    if raw.get("instrument_type") not in (None, "reversed"):
        reasons.append("instrument_type_not_inverse")
    if raw.get("state") != "open":
        reasons.append("state_not_open")
    option_type = raw.get("option_type")
    if option_type not in {"put", "call"}:
        reasons.append("option_type_invalid")
    try:
        expiry = _integer("expiration_timestamp", raw.get("expiration_timestamp"))
        if expiry <= captured_ts_ms:
            reasons.append("expired")
    except ValueError:
        expiry = None
        reasons.append("expiration_timestamp_invalid")
    try:
        strike = _number("strike", raw.get("strike"), positive=True)
    except ValueError:
        strike = None
        reasons.append("strike_invalid")
    try:
        contract_size = _number("contract_size", raw.get("contract_size"), positive=True)
        if not math.isclose(contract_size, 1.0, rel_tol=0, abs_tol=1e-12):
            reasons.append("contract_size_unsupported")
    except ValueError:
        contract_size = None
        reasons.append("contract_size_invalid")
    try:
        min_trade = _number("min_trade_amount", raw.get("min_trade_amount"), positive=True)
    except ValueError:
        min_trade = None
        reasons.append("min_trade_amount_invalid")
    if parsed is not None:
        if expiry is not None and expiry != named_expiry:
            reasons.append("name_expiry_mismatch")
        if strike is not None and not math.isclose(strike, strike_from_name, rel_tol=0, abs_tol=1e-9):
            reasons.append("name_strike_mismatch")
        if option_type in {"put", "call"} and option_type != side_from_name:
            reasons.append("name_option_type_mismatch")
    if reasons:
        return None, _reject(raw, reasons)
    assert expiry is not None and strike is not None and contract_size is not None and min_trade is not None
    normalized = dict(raw)
    normalized.update({"instrument_name": name, "kind": "option", "base_currency": "BTC",
                       "settlement_currency": "BTC", "quote_currency": "BTC",
                       "counter_currency": "USD", "price_index": "btc_usd",
                       "instrument_type": raw.get("instrument_type"),
                       "option_type": option_type, "state": "open",
                       "expiration_timestamp": expiry, "contract_size": contract_size,
                       "strike": strike, "min_trade_amount": min_trade})
    return normalized, None


def _delta_timestamp(raw: Mapping[str, Any]) -> int | None:
    for key in ("delta_observed_at_ms", "greeks_observed_at_ms", "greeks_ts_ms",
                "greeks_epoch_ms", "greeks_updated_at_ms", "timestamp"):
        value = raw.get(key)
        if value is None:
            continue
        return _integer(key, value)
    return None


def _short_delta(raw: Mapping[str, Any], captured_ts_ms: int, *,
                 max_age_ms: int, max_abs: float) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate one producer/public-market delta. Price distance is never a fallback."""
    reasons: list[str] = []
    delta_source = "instrument.delta"
    raw_delta = raw.get("delta")
    greeks = raw.get("greeks") if isinstance(raw.get("greeks"), Mapping) else None
    if raw_delta is None and greeks is not None:
        raw_delta = greeks.get("delta")
        delta_source = "greeks.delta"
    try:
        delta = _number("delta", raw_delta)
    except ValueError:
        reasons.append("short_delta_missing_or_invalid")
        delta = None
    try:
        observed = _delta_timestamp(raw)
    except ValueError:
        observed = None
        reasons.append("short_delta_timestamp_invalid")
    if observed is None:
        reasons.append("short_delta_time_missing")
    elif observed > captured_ts_ms or captured_ts_ms - observed > max_age_ms:
        reasons.append("short_delta_stale_or_future")
    if delta is not None:
        side = raw.get("option_type")
        if (side == "put" and delta >= 0) or (side == "call" and delta <= 0) or math.isclose(delta, 0.0, abs_tol=1e-12):
            reasons.append("short_delta_sign_mismatch")
        if abs(delta) > max_abs + 1e-12:
            reasons.append("short_delta_abs_above_max")
    if reasons or delta is None or observed is None:
        return None, sorted(set(reasons))
    return {"delta": delta, "abs_delta": abs(delta), "observed_at_ms": observed,
            "source": str(raw.get("delta_source") or delta_source),
            "max_age_ms": max_age_ms, "max_abs": max_abs}, []


def _gap(side: str, reasons: list[str], *, expiry_ms: int | None,
         remaining_hours: float | None) -> dict[str, Any]:
    return {"status": "gap", "side": side, "candidate": None, "metadata": None,
            "expiry_ms": expiry_ms, "remaining_hours": remaining_hours,
            "gap_reasons": sorted(set(reasons))}


def _candidate(side: str, short: Mapping[str, Any], long: Mapping[str, Any],
               quantity_btc: float, width_usd: float, index_price: float,
               remaining_hours: float, delta: Mapping[str, Any],
               delta_target_abs: float, delta_max_abs: float) -> dict[str, Any]:
    candidate = {"short_instrument": short["instrument_name"],
                 "long_instrument": long["instrument_name"],
                 "quantity_btc": quantity_btc,
                 "exit_policy": "hold_to_expiry"}
    return {"status": "candidate", "side": side, "candidate": candidate,
            "metadata": {"short": dict(short), "long": dict(long)},
            "expiry_ms": short["expiration_timestamp"],
            "remaining_hours": remaining_hours,
            "short_strike_usd": short["strike"],
            "long_strike_usd": long["strike"],
            "width_usd": width_usd,
            "strict_otm_reference_price_usd": index_price,
            "short_delta": delta["delta"],
            "short_abs_delta": delta["abs_delta"],
            "short_delta_observed_at_ms": delta["observed_at_ms"],
            "short_delta_source": delta["source"],
            "short_delta_target_abs": delta_target_abs,
            "short_delta_max_abs": delta_max_abs,
            "selection_basis": "short_abs_delta_closest_to_target_with_exact_width_and_abs_delta_lte_max",
            "gap_reasons": [],
            "execution_allowed": False}


def _discover_side(side: str, by_side: Mapping[str, Mapping[float, Mapping[str, Any]]],
                   *, selected_expiry_ms: int, remaining_hours: float, index_price: float,
                   quantity_btc: float, width_usd: float, captured_ts_ms: int,
                   delta_target_abs: float, delta_max_abs: float,
                   delta_max_age_ms: int) -> dict[str, Any]:
    side_chain = by_side.get(side, {})
    if not side_chain:
        return _gap(side, ["side_chain_missing"], expiry_ms=selected_expiry_ms, remaining_hours=remaining_hours)
    if side == "put":
        short_strikes = [strike for strike in side_chain if strike < index_price]
        target_long_strike = lambda strike: strike - width_usd
        missing_reason = "short_put_strict_otm_missing"
    else:
        short_strikes = [strike for strike in side_chain if strike > index_price]
        target_long_strike = lambda strike: strike + width_usd
        missing_reason = "short_call_strict_otm_missing"
    if not short_strikes:
        return _gap(side, [missing_reason], expiry_ms=selected_expiry_ms, remaining_hours=remaining_hours)
    rejected_reasons: list[str] = []
    choices: list[tuple[float, float, float, float, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []
    for short_strike in short_strikes:
        short = side_chain[short_strike]
        delta, delta_reasons = _short_delta(
            short, captured_ts_ms, max_age_ms=delta_max_age_ms, max_abs=delta_max_abs)
        if delta_reasons or delta is None:
            rejected_reasons.extend(delta_reasons)
            continue
        long_strike = target_long_strike(short_strike)
        long = side_chain.get(long_strike)
        if long is None:
            rejected_reasons.append("protection_leg_missing_exact_width")
            continue
        if quantity_btc + 1e-12 < max(short["min_trade_amount"], long["min_trade_amount"]):
            rejected_reasons.append("quantity_below_min_trade")
            continue
        # Tie order: closest to requested |delta|, then more conservative lower
        # |delta|, then closer-to-index strike, then numeric strike for stability.
        choices.append((abs(delta["abs_delta"] - delta_target_abs), delta["abs_delta"],
                        abs(short_strike - index_price), short_strike, short, long, delta))
    if not choices:
        return _gap(side, rejected_reasons or ["short_delta_candidate_missing"],
                    expiry_ms=selected_expiry_ms, remaining_hours=remaining_hours)
    _, _, _, _, short, long, delta = min(choices, key=lambda item: item[:4])
    return _candidate(side, short, long, quantity_btc, width_usd, index_price,
                      remaining_hours, delta, delta_target_abs, delta_max_abs)


def discover(instruments: Any, index_price: Any, captured_ts_ms: Any, *,
             quantity_btc: float = DEFAULT_QUANTITY_BTC,
             width_usd: float = DEFAULT_WIDTH_USD,
             target_hours: float = TARGET_HOURS,
             short_delta_target_abs: float = DEFAULT_SHORT_DELTA_TARGET_ABS,
             short_delta_max_abs: float = DEFAULT_SHORT_DELTA_MAX_ABS,
             delta_max_age_ms: int = DEFAULT_DELTA_MAX_AGE_MS) -> dict[str, Any]:
    """Discover the current Put and Call candidate definitions without choosing a side."""
    if not isinstance(instruments, list):
        raise ValueError("instruments must be a list")
    captured = _integer("captured_ts_ms", captured_ts_ms)
    index = _number("index_price", index_price, positive=True)
    quantity = _number("quantity_btc", quantity_btc, positive=True)
    width = _number("width_usd", width_usd, positive=True)
    target_ms = _number("target_hours", target_hours, positive=True) * 3_600_000
    delta_target = _number("short_delta_target_abs", short_delta_target_abs, positive=True)
    delta_max = _number("short_delta_max_abs", short_delta_max_abs, positive=True)
    delta_age = _integer("delta_max_age_ms", delta_max_age_ms)
    if delta_target > delta_max:
        raise ValueError("short delta target cannot exceed maximum")
    if delta_max > 1.0 or delta_age <= 0:
        raise ValueError("delta maximum and freshness must be bounded")
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for raw in instruments:
        normalized, rejected = _normalize(raw, captured)
        if normalized is None:
            assert rejected is not None
            invalid.append(rejected)
        else:
            valid.append(normalized)
    expiries = sorted({item["expiration_timestamp"] for item in valid})
    if not expiries:
        body = {"captured_ts_ms": captured, "index_price_usd": index,
                "target_hours": target_hours, "quantity_btc": quantity,
                "width_usd": width, "short_delta_target_abs": delta_target,
                "short_delta_max_abs": delta_max, "delta_max_age_ms": delta_age,
                "selected_expiry_ms": None,
                "remaining_hours": None, "entry_mode": "parallel_read_only_no_side_selection",
                "candidates": {"put": _gap("put", ["expiry_missing"], expiry_ms=None, remaining_hours=None),
                               "call": _gap("call", ["expiry_missing"], expiry_ms=None, remaining_hours=None)},
                "gap_reasons": ["expiry_missing"],
                "input_summary": {"instrument_count": len(instruments), "valid_count": 0,
                                  "invalid_count": len(invalid), "invalid_instruments": invalid[:20]}}
        return {"schema": SCHEMA, "discovery_id": _hash(body), **body}
    selected_expiry = min(expiries, key=lambda expiry: (abs((expiry - captured) - target_ms), expiry))
    remaining_hours = (selected_expiry - captured) / 3_600_000
    by_side: dict[str, dict[float, dict[str, Any]]] = {"put": {}, "call": {}}
    for item in valid:
        if item["expiration_timestamp"] == selected_expiry:
            by_side[item["option_type"]][item["strike"]] = item
    candidates = {
        "put": _discover_side("put", by_side, selected_expiry_ms=selected_expiry,
                              remaining_hours=remaining_hours, index_price=index,
                              quantity_btc=quantity, width_usd=width,
                              captured_ts_ms=captured, delta_target_abs=delta_target,
                              delta_max_abs=delta_max, delta_max_age_ms=delta_age),
        "call": _discover_side("call", by_side, selected_expiry_ms=selected_expiry,
                               remaining_hours=remaining_hours, index_price=index,
                               quantity_btc=quantity, width_usd=width,
                               captured_ts_ms=captured, delta_target_abs=delta_target,
                               delta_max_abs=delta_max, delta_max_age_ms=delta_age),
    }
    body = {"captured_ts_ms": captured, "index_price_usd": index,
            "target_hours": target_hours, "quantity_btc": quantity,
            "width_usd": width, "selected_expiry_ms": selected_expiry,
            "short_delta_target_abs": delta_target,
            "short_delta_max_abs": delta_max,
            "delta_max_age_ms": delta_age,
            "remaining_hours": remaining_hours,
            "entry_mode": "parallel_read_only_no_side_selection",
            "candidates": candidates,
            "input_summary": {"instrument_count": len(instruments), "valid_count": len(valid),
                              "invalid_count": len(invalid), "invalid_instruments": invalid[:20]},
            "gap_reasons": sorted({reason for candidate in candidates.values()
                                   for reason in candidate["gap_reasons"]})}
    return {"schema": SCHEMA, "discovery_id": _hash(body), **body}


def _selected_expiry(instruments: list[Mapping[str, Any]], captured: int,
                     target_hours: float) -> int | None:
    valid: list[dict[str, Any]] = []
    for raw in instruments:
        normalized, _ = _normalize(raw, captured)
        if normalized is not None:
            valid.append(normalized)
    expiries = sorted({item["expiration_timestamp"] for item in valid})
    if not expiries:
        return None
    target_ms = target_hours * 3_600_000
    return min(expiries, key=lambda expiry: (abs((expiry - captured) - target_ms), expiry))


def _delta_short_candidate_names(instruments: list[Any], selected_expiry: int | None, *,
                                 captured: int, index_price: float,
                                 quantity_btc: float, width_usd: float) -> tuple[set[str], dict[str, Any]]:
    if selected_expiry is None:
        return set(), {"put": 0, "call": 0}
    valid: list[dict[str, Any]] = []
    for raw in instruments:
        normalized, _ = _normalize(raw, captured)
        if normalized is not None and normalized["expiration_timestamp"] == selected_expiry:
            valid.append(normalized)
    by_side: dict[str, dict[float, dict[str, Any]]] = {"put": {}, "call": {}}
    for item in valid:
        by_side[item["option_type"]][item["strike"]] = item
    selected: set[str] = set()
    counts = {"put": 0, "call": 0}
    for side in ("put", "call"):
        side_chain = by_side[side]
        if side == "put":
            short_strikes = [strike for strike in side_chain if strike < index_price]
            target_long_strike = lambda strike: strike - width_usd
        else:
            short_strikes = [strike for strike in side_chain if strike > index_price]
            target_long_strike = lambda strike: strike + width_usd
        for short_strike in short_strikes:
            short = side_chain[short_strike]
            long = side_chain.get(target_long_strike(short_strike))
            if long is None:
                continue
            if quantity_btc + 1e-12 < max(short["min_trade_amount"], long["min_trade_amount"]):
                continue
            selected.add(short["instrument_name"])
            counts[side] += 1
    return selected, counts


def _with_public_greeks(instruments: list[Any], short_candidate_names: set[str], *,
                        base_url: str, timeout: float,
                        get: Callable[[str, str, Mapping[str, Any], float], dict[str, Any]],
                        now: Callable[[], float]) -> tuple[list[Any], dict[str, Any]]:
    if not short_candidate_names:
        return list(instruments), {"order_book_received_ms": {}, "order_book_response_hashes": {},
                                   "requested_count": 0, "requested_instruments": []}
    enriched: list[Any] = []
    receipts: dict[str, int] = {}
    hashes: dict[str, str] = {}
    errors: dict[str, str] = {}
    for raw in instruments:
        item = dict(raw) if isinstance(raw, Mapping) else raw
        if isinstance(item, dict):
            name = item.get("instrument_name")
            if isinstance(name, str) and name in short_candidate_names:
                try:
                    response = get(base_url, "/public/get_order_book",
                                   {"instrument_name": name, "depth": 1}, timeout)
                    receipts[name] = (response.get("data_engine_receipt") or {}).get("retrieved_at_ms") or int(now() * 1000)
                    result = response.get("result") if isinstance(response, Mapping) else None
                    if not isinstance(result, Mapping) or result.get("instrument_name") != name:
                        raise ValueError("unexpected order book result")
                    hashes[name] = _hash(response)
                    greeks = result.get("greeks") if isinstance(result.get("greeks"), Mapping) else {}
                    item["greeks"] = dict(greeks)
                    item["delta_observed_at_ms"] = result.get("timestamp")
                    item["delta_source"] = "public/get_order_book.greeks.delta"
                    item["delta_book_received_ms"] = receipts[name]
                except Exception as exc:
                    errors[name] = type(exc).__name__
        enriched.append(item)
    return enriched, {"order_book_received_ms": receipts,
                      "order_book_response_hashes": hashes,
                      "order_book_errors": errors,
                      "requested_count": len(short_candidate_names),
                      "requested_instruments": sorted(short_candidate_names)}


def _public_get(base_url: str, path: str, params: Mapping[str, Any], timeout_s: float) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{base_url.rstrip('/')}{path}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "astra-underwriting-discovery/2.0"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping) or "result" not in payload:
        raise ValueError(f"unexpected Deribit response from {path}")
    return dict(payload)


def capture_public_deribit_discovery(*, base_url: str = DERIBIT_BASE_URL,
                                     timeout_s: float = 10.0,
                                     captured_ts_ms: int | None = None,
                                     quantity_btc: float = DEFAULT_QUANTITY_BTC,
                                     width_usd: float = DEFAULT_WIDTH_USD,
                                     transport: Callable | None = None) -> dict[str, Any]:
    """One-shot public capture: metadata/index plus candidate-short book greeks."""
    timeout = _number("timeout_s", timeout_s, positive=True)
    get = transport or _public_get
    request_started_ms = (_integer("captured_ts_ms", captured_ts_ms)
                          if captured_ts_ms is not None else int(time.time() * 1000))
    index_response = get(base_url, "/public/get_index_price", {"index_name": "btc_usd"}, timeout)
    index_received_ms = (index_response.get("data_engine_receipt") or {}).get("retrieved_at_ms") or int(time.time() * 1000)
    instrument_response = get(base_url, "/public/get_instruments",
                                      {"currency": "BTC", "kind": "option", "expired": "false"}, timeout)
    instrument_received_ms = (instrument_response.get("data_engine_receipt") or {}).get("retrieved_at_ms") or int(time.time() * 1000)
    selection_clock_ms = max(request_started_ms, index_received_ms, instrument_received_ms)
    index_result = index_response["result"]
    instruments = instrument_response["result"]
    if not isinstance(index_result, Mapping) or not isinstance(instruments, list):
        raise ValueError("unexpected Deribit result shape")
    index_price = _number("index_price", index_result.get("index_price"), positive=True)
    quantity = _number("quantity_btc", quantity_btc, positive=True)
    width = _number("width_usd", width_usd, positive=True)
    selected_expiry = _selected_expiry(instruments, selection_clock_ms, TARGET_HOURS)
    short_names, short_counts = _delta_short_candidate_names(
        instruments, selected_expiry, captured=selection_clock_ms,
        index_price=index_price, quantity_btc=quantity, width_usd=width)
    enriched, delta_identity = _with_public_greeks(
        instruments, short_names, base_url=base_url, timeout=timeout,
        get=get, now=time.time)
    order_book_receipts = list(delta_identity.get("order_book_received_ms", {}).values())
    capture_completed_ms = max([selection_clock_ms, *order_book_receipts])
    discovered = discover(enriched, index_price, capture_completed_ms,
                          quantity_btc=quantity, width_usd=width)
    # A transport/budget failure is a hole in the ranking population, not a
    # reason to silently choose from the successfully fetched subset.
    failed_names = set(delta_identity.get("order_book_errors") or {})
    for side, suffix in (("put", "-P"), ("call", "-C")):
        if any(name.endswith(suffix) for name in failed_names):
            discovered["candidates"][side] = _gap(side, ["delta_population_capture_incomplete"],
                expiry_ms=discovered["selected_expiry_ms"], remaining_hours=discovered["remaining_hours"])
    discovered["gap_reasons"] = sorted({reason for item in discovered["candidates"].values()
                                        for reason in item["gap_reasons"]})
    discovered["discovery_id"] = _hash({k: v for k, v in discovered.items()
                                       if k not in {"schema", "discovery_id"}})
    raw_identity = {"endpoints": ["public/get_index_price", "public/get_instruments", "public/get_order_book"],
                    "request_started_ms": request_started_ms,
                    "requested_capture_ts_ms": captured_ts_ms,
                    "index_received_ms": index_received_ms,
                    "instrument_received_ms": instrument_received_ms,
                    "selection_clock_ms": selection_clock_ms,
                    "capture_completed_ms": capture_completed_ms,
                    "delta_short_candidate_counts": short_counts,
                    "index_response_hash": _hash(index_response),
                    "instrument_response_hash": _hash(instrument_response),
                    "delta_capture": delta_identity}
    # Preserve the actual selection inputs for day/manual freezes. No second
    # HTTP request, mark substitution, or re-fetch is used for provenance.
    fields = ("instrument_name", "kind", "base_currency", "settlement_currency",
              "quote_currency", "counter_currency", "price_index", "instrument_type",
              "state", "option_type", "expiration_timestamp", "contract_size", "strike",
              "min_trade_amount", "greeks", "delta_observed_at_ms", "delta_source", "delta_book_received_ms")
    selection_inputs = [{key: row[key] for key in fields if key in row}
                        for row in enriched if isinstance(row, Mapping)]
    return {**discovered, "data_identity": {"kind": "live_public_capture", **raw_identity},
            "selection_inputs": {"index": dict(index_result), "instruments": selection_inputs}}
