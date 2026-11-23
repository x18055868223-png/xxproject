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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

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
    category, detail, reasons = _classify_short_delta(
        raw, captured_ts_ms, max_age_ms=max_age_ms, max_abs=max_abs,
    )
    if category == "verified_eligible":
        return detail, []
    return None, reasons


def _classify_short_delta(
    raw: Mapping[str, Any],
    captured_ts_ms: int,
    *,
    max_age_ms: int,
    max_abs: float,
    failed_names: set[str] | frozenset[str] = frozenset(),
) -> tuple[str, dict[str, Any] | None, list[str]]:
    """Classify one structurally qualified short leg into four explicit categories:
    `verified_eligible`, `verified_above_max`, `delta_missing_or_invalid`, or `unacquired`.
    """
    name = raw.get("instrument_name")
    if (isinstance(name, str) and name in failed_names) or raw.get("_delta_capture_error"):
        return "unacquired", None, ["delta_population_capture_incomplete"]
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
    above_max = False
    if delta is not None:
        side = raw.get("option_type")
        if (side == "put" and delta >= 0) or (side == "call" and delta <= 0) or math.isclose(delta, 0.0, abs_tol=1e-12):
            reasons.append("short_delta_sign_mismatch")
        if abs(delta) > max_abs + 1e-12:
            above_max = True
            reasons.append("short_delta_abs_above_max")
    non_policy_reasons = [r for r in reasons if r != "short_delta_abs_above_max"]
    if non_policy_reasons or delta is None or observed is None:
        return "delta_missing_or_invalid", None, sorted(set(reasons))
    detail = {
        "delta": delta,
        "abs_delta": abs(delta),
        "observed_at_ms": observed,
        "source": str(raw.get("delta_source") or delta_source),
        "max_age_ms": max_age_ms,
        "max_abs": max_abs,
    }
    if above_max:
        return "verified_above_max", detail, ["short_delta_abs_above_max"]
    return "verified_eligible", detail, []


def _gap(side: str, reasons: list[str], *, expiry_ms: int | None,
         remaining_hours: float | None,
         delta_population: Mapping[str, Any] | None = None) -> dict[str, Any]:
    out = {"status": "gap", "side": side, "candidate": None, "metadata": None,
           "expiry_ms": expiry_ms, "remaining_hours": remaining_hours,
           "gap_reasons": sorted(set(reasons))}
    if delta_population is not None:
        out["delta_population"] = dict(delta_population)
    return out


def _candidate(side: str, short: Mapping[str, Any], long: Mapping[str, Any],
               quantity_btc: float, width_usd: float, index_price: float,
               remaining_hours: float, delta: Mapping[str, Any],
               delta_target_abs: float, delta_max_abs: float, *,
               delta_population: Mapping[str, Any] | None = None) -> dict[str, Any]:
    candidate = {"short_instrument": short["instrument_name"],
                 "long_instrument": long["instrument_name"],
                 "quantity_btc": quantity_btc,
                 "exit_policy": "hold_to_expiry"}
    out = {"status": "candidate", "side": side, "candidate": candidate,
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
    if delta_population is not None:
        out["delta_population"] = dict(delta_population)
    return out


def _discover_side(side: str, by_side: Mapping[str, Mapping[float, Mapping[str, Any]]],
                   *, selected_expiry_ms: int, remaining_hours: float, index_price: float,
                   quantity_btc: float, width_usd: float, captured_ts_ms: int,
                   delta_target_abs: float, delta_max_abs: float,
                   delta_max_age_ms: int,
                   failed_names: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    side_chain = by_side.get(side, {})
    empty_pop = {"total_qualified": 0, "verified_eligible": 0, "verified_above_max": 0,
                 "delta_missing_or_invalid": [], "unacquired": [], "complete": False}
    if not side_chain:
        return _gap(side, ["side_chain_missing"], expiry_ms=selected_expiry_ms,
                    remaining_hours=remaining_hours, delta_population=empty_pop)
    if side == "put":
        short_strikes = [strike for strike in side_chain if strike < index_price]
        target_long_strike = lambda strike: strike - width_usd
        missing_reason = "short_put_strict_otm_missing"
    else:
        short_strikes = [strike for strike in side_chain if strike > index_price]
        target_long_strike = lambda strike: strike + width_usd
        missing_reason = "short_call_strict_otm_missing"
    if not short_strikes:
        return _gap(side, [missing_reason], expiry_ms=selected_expiry_ms,
                    remaining_hours=remaining_hours, delta_population=empty_pop)
    rejected_reasons: list[str] = []
    unacquired: list[str] = []
    delta_missing_or_invalid: list[str] = []
    verified_above_max = 0
    choices: list[tuple[float, float, float, float, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []
    qualified_count = 0
    for short_strike in short_strikes:
        short = side_chain[short_strike]
        long_strike = target_long_strike(short_strike)
        long = side_chain.get(long_strike)
        if long is None:
            rejected_reasons.append("protection_leg_missing_exact_width")
            continue
        if quantity_btc + 1e-12 < max(short["min_trade_amount"], long["min_trade_amount"]):
            rejected_reasons.append("quantity_below_min_trade")
            continue
        qualified_count += 1
        category, delta, delta_reasons = _classify_short_delta(
            short, captured_ts_ms, max_age_ms=delta_max_age_ms, max_abs=delta_max_abs,
            failed_names=failed_names,
        )
        if category == "unacquired":
            unacquired.append(str(short["instrument_name"]))
            continue
        if category == "delta_missing_or_invalid":
            delta_missing_or_invalid.append(str(short["instrument_name"]))
            rejected_reasons.extend(delta_reasons)
            continue
        if category == "verified_above_max":
            verified_above_max += 1
            rejected_reasons.extend(delta_reasons)
            continue
        assert delta is not None
        # Tie order: closest to requested |delta|, then more conservative lower
        # |delta|, then closer-to-index strike, then numeric strike for stability.
        choices.append((abs(delta["abs_delta"] - delta_target_abs), delta["abs_delta"],
                        abs(short_strike - index_price), short_strike, short, long, delta))
    complete = (not unacquired and not delta_missing_or_invalid and qualified_count > 0)
    population = {
        "total_qualified": qualified_count,
        "verified_eligible": len(choices),
        "verified_above_max": verified_above_max,
        "delta_missing_or_invalid": sorted(delta_missing_or_invalid),
        "unacquired": sorted(unacquired),
        "complete": complete,
    }
    if unacquired:
        # Missing acquired legs make ranking across the qualified population incomplete.
        return _gap(side, ["delta_population_capture_incomplete"],
                    expiry_ms=selected_expiry_ms, remaining_hours=remaining_hours,
                    delta_population=population)
    if delta_missing_or_invalid:
        # Unknown/missing/stale/invalid delta cannot be silently dropped from ranking.
        return _gap(side, [*rejected_reasons, "delta_population_greeks_incomplete"],
                    expiry_ms=selected_expiry_ms, remaining_hours=remaining_hours,
                    delta_population=population)
    if not choices:
        return _gap(side, rejected_reasons or ["short_delta_candidate_missing"],
                    expiry_ms=selected_expiry_ms, remaining_hours=remaining_hours,
                    delta_population=population)
    _, _, _, _, short, long, delta = min(choices, key=lambda item: item[:4])
    return _candidate(side, short, long, quantity_btc, width_usd, index_price,
                      remaining_hours, delta, delta_target_abs, delta_max_abs,
                      delta_population=population)


def discover(instruments: Any, index_price: Any, captured_ts_ms: Any, *,
             quantity_btc: float = DEFAULT_QUANTITY_BTC,
             width_usd: float = DEFAULT_WIDTH_USD,
             target_hours: float = TARGET_HOURS,
             short_delta_target_abs: float = DEFAULT_SHORT_DELTA_TARGET_ABS,
             short_delta_max_abs: float = DEFAULT_SHORT_DELTA_MAX_ABS,
             delta_max_age_ms: int = DEFAULT_DELTA_MAX_AGE_MS,
             failed_names: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
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
                              delta_max_abs=delta_max, delta_max_age_ms=delta_age,
                              failed_names=failed_names),
        "call": _discover_side("call", by_side, selected_expiry_ms=selected_expiry,
                               remaining_hours=remaining_hours, index_price=index,
                               quantity_btc=quantity, width_usd=width,
                               captured_ts_ms=captured, delta_target_abs=delta_target,
                               delta_max_abs=delta_max, delta_max_age_ms=delta_age,
                               failed_names=failed_names),
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


def _ordered_short_candidates(
    instruments: list[Any],
    selected_expiry: int | None,
    *,
    captured: int,
    index_price: float,
    quantity_btc: float,
    width_usd: float,
    start_side: str = "put",
) -> tuple[list[str], set[str], dict[str, int], dict[str, list[str]]]:
    """Return side-grouped near-OTM short candidate names with alternating Put/Call priority."""
    if selected_expiry is None:
        return [], set(), {"put": 0, "call": 0}, {"put": [], "call": []}
    valid: list[dict[str, Any]] = []
    for raw in instruments:
        normalized, _ = _normalize(raw, captured)
        if normalized is not None and normalized["expiration_timestamp"] == selected_expiry:
            valid.append(normalized)
    by_side: dict[str, dict[float, dict[str, Any]]] = {"put": {}, "call": {}}
    for item in valid:
        by_side[item["option_type"]][item["strike"]] = item
    side_lists: dict[str, list[str]] = {"put": [], "call": []}
    for side in ("put", "call"):
        side_chain = by_side[side]
        if side == "put":
            short_strikes = [strike for strike in side_chain if strike < index_price]
            target_long_strike = lambda strike: strike - width_usd
        else:
            short_strikes = [strike for strike in side_chain if strike > index_price]
            target_long_strike = lambda strike: strike + width_usd
        # Nearest OTM strikes first so priority legs cover the target delta zone early.
        short_strikes.sort(key=lambda strike: (abs(strike - index_price), strike))
        for short_strike in short_strikes:
            short = side_chain[short_strike]
            long = side_chain.get(target_long_strike(short_strike))
            if long is None:
                continue
            if quantity_btc + 1e-12 < max(short["min_trade_amount"], long["min_trade_amount"]):
                continue
            side_lists[side].append(short["instrument_name"])
    first = "call" if start_side == "call" else "put"
    second = "put" if first == "call" else "call"
    # Side-grouped ordering preserves one complete surviving side under mid-round 429 aborts,
    # while alternating start_side guarantees 50/50 Put/Call priority across rounds.
    ordered = [*side_lists[first], *side_lists[second]]
    counts = {"put": len(side_lists["put"]), "call": len(side_lists["call"])}
    return ordered, set(ordered), counts, side_lists


def _delta_short_candidate_names(instruments: list[Any], selected_expiry: int | None, *,
                                 captured: int, index_price: float,
                                 quantity_btc: float, width_usd: float) -> tuple[set[str], dict[str, Any]]:
    _ordered, selected, counts, _side_lists = _ordered_short_candidates(
        instruments, selected_expiry, captured=captured,
        index_price=index_price, quantity_btc=quantity_btc, width_usd=width_usd,
    )
    return selected, counts


class DeribitPacer:
    """Process-local monotonic pacer respecting Deribit's 5 req/s (burst 25) order-book pool.

    Sustained discovery pace defaults to 2.5 req/s (400 ms) so background discovery leaves
    headroom for priority 2-leg quote pairs (80 ms spacing inside a 4 req/s sliding 1s cap)
    and co-located cron consumers on the same egress IP.
    """

    def __init__(
        self,
        *,
        min_interval_s: float = 0.40,
        priority_min_interval_s: float = 0.08,
        max_per_second: int = 4,
        cooldown_default_s: float = 10.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_interval_s = max(0.0, float(min_interval_s))
        self.priority_min_interval_s = max(0.0, float(priority_min_interval_s))
        self.max_per_second = max(1, int(max_per_second))
        self.cooldown_default_s = max(1.0, float(cooldown_default_s))
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_grant_mono: float | None = None
        self._grants: list[float] = []
        self._cooldown_until_mono: float = 0.0
        self._stats = {"total_acquired": 0, "discovery": 0, "quote": 0,
                       "rate_limit_events": 0, "maintenance_events": 0}

    def record_rate_limit(self, retry_after_ms: int | None = None) -> float:
        wait_s = (max(1.0, min(120.0, retry_after_ms / 1000.0))
                  if isinstance(retry_after_ms, (int, float)) and retry_after_ms > 0
                  else self.cooldown_default_s)
        with self._lock:
            now_m = self._monotonic()
            self._cooldown_until_mono = max(self._cooldown_until_mono, now_m + wait_s)
            self._stats["rate_limit_events"] += 1
            return self._cooldown_until_mono - now_m

    def record_maintenance(self, backoff_ms: int = 30_000) -> float:
        wait_s = max(1.0, backoff_ms / 1000.0)
        with self._lock:
            now_m = self._monotonic()
            self._cooldown_until_mono = max(self._cooldown_until_mono, now_m + wait_s)
            self._stats["maintenance_events"] += 1
            return self._cooldown_until_mono - now_m

    def cooldown_remaining_s(self) -> float:
        with self._lock:
            return max(0.0, self._cooldown_until_mono - self._monotonic())

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {**self._stats, "cooldown_remaining_s": round(max(0.0, self._cooldown_until_mono - self._monotonic()), 3)}

    def acquire(self, *, priority: str = "discovery", deadline_mono: float | None = None) -> tuple[bool, str | None]:
        with self._lock:
            now_m = self._monotonic()
            if self._cooldown_until_mono > now_m:
                if priority == "discovery" or (self._cooldown_until_mono - now_m) > 2.0:
                    return False, "rate_limit_cooldown"
                if deadline_mono is not None and self._cooldown_until_mono > deadline_mono:
                    return False, "rate_limit_cooldown"
            interval = self.priority_min_interval_s if priority in {"quote", "high"} else self.min_interval_s
            slot_m = now_m if self._last_grant_mono is None else max(now_m, self._last_grant_mono + interval)
            cutoff = slot_m - 1.0
            self._grants = [t for t in self._grants if t > cutoff]
            if len(self._grants) >= self.max_per_second:
                oldest_in_window = self._grants[-self.max_per_second]
                slot_m = max(slot_m, oldest_in_window + 1.005)
            if deadline_mono is not None and slot_m > deadline_mono:
                return False, "round_deadline_exceeded"
            wait_s = max(0.0, slot_m - now_m)
            self._last_grant_mono = slot_m
            self._grants.append(slot_m)
            cutoff2 = slot_m - 1.0
            self._grants = [t for t in self._grants if t > cutoff2]
            self._stats["total_acquired"] += 1
            bucket = "quote" if priority in {"quote", "high"} else "discovery"
            self._stats[bucket] += 1
        if wait_s > 0:
            self._sleep(wait_s)
        return True, None


def _error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    return str(code) if isinstance(code, str) and code else type(exc).__name__


def _with_public_greeks(
    instruments: list[Any],
    short_candidate_names: set[str] | Sequence[str],
    *,
    base_url: str,
    timeout: float,
    get: Callable[[str, str, Mapping[str, Any], float], dict[str, Any]],
    now: Callable[[], float],
    pacer: DeribitPacer | None = None,
    min_interval_s: float = 0.0,
    max_round_s: float = 25.0,
    retry_delay_s: float = 0.25,
    delta_cache_get: Callable[[str], Mapping[str, Any] | None] | None = None,
    delta_cache_put: Callable[[str, Mapping[str, Any]], None] | None = None,
    ordered_names: Sequence[str] | None = None,
    delta_max_age_ms: int = DEFAULT_DELTA_MAX_AGE_MS,
    delta_max_abs: float = DEFAULT_SHORT_DELTA_MAX_ABS,
    captured_ts_ms: int | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    mono_fn = monotonic if monotonic is not None else (pacer._monotonic if pacer is not None else time.monotonic)
    sleep_fn = sleep if sleep is not None else (pacer._sleep if pacer is not None else time.sleep)
    short_set = set(short_candidate_names)
    if not short_set:
        return list(instruments), {
            "order_book_received_ms": {},
            "order_book_response_hashes": {},
            "order_book_errors": {},
            "order_book_retried": [],
            "requested_count": 0,
            "requested_instruments": [],
            "wire_requests_total": 0,
            "wire_requests_initial": 0,
            "wire_requests_retry": 0,
            "cache_hits": 0,
            "cache_hit_instruments": [],
            "skipped_instruments": {},
            "rate_limit_hits": 0,
            "maintenance_hits": 0,
            "abort_reason": None,
            "retry_after_ms": None,
        }
    enriched: list[Any] = []
    by_name: dict[str, dict[str, Any]] = {}
    for raw in instruments:
        item = dict(raw) if isinstance(raw, Mapping) else raw
        enriched.append(item)
        if isinstance(item, dict):
            name = item.get("instrument_name")
            if isinstance(name, str) and name in short_set:
                by_name[name] = item

    order_list = [n for n in ordered_names if n in short_set] if ordered_names else sorted(short_set)
    receipts: dict[str, int] = {}
    hashes: dict[str, str] = {}
    errors: dict[str, str] = {}
    retried: list[str] = []
    cache_hit_instruments: list[str] = []
    skipped_instruments: dict[str, str] = {}
    wire_initial = 0
    wire_retry = 0
    rate_limit_hits = 0
    maintenance_hits = 0
    abort_reason: str | None = None
    retry_after_ms: int | None = None
    deadline_mono = mono_fn() + max_round_s if max_round_s and max_round_s > 0 else None
    last_wire_mono: list[float | None] = [None]

    def _pace() -> tuple[bool, str | None]:
        if pacer is not None:
            return pacer.acquire(priority="discovery", deadline_mono=deadline_mono)
        if min_interval_s > 0:
            now_m = mono_fn()
            target_m = now_m if last_wire_mono[0] is None else max(now_m, last_wire_mono[0] + min_interval_s)
            if deadline_mono is not None and target_m > deadline_mono:
                return False, "round_deadline_exceeded"
            if target_m > now_m:
                sleep_fn(target_m - now_m)
            last_wire_mono[0] = mono_fn()
            return True, None
        if deadline_mono is not None and mono_fn() > deadline_mono:
            return False, "round_deadline_exceeded"
        return True, None

    def _mark_remaining(start_idx: int, reason_code: str) -> None:
        for rem in wire_order[start_idx:]:
            if rem not in receipts and rem not in cache_hit_set and rem not in errors:
                errors[rem] = reason_code
                skipped_instruments[rem] = reason_code

    cache_hit_set: set[str] = set()
    if delta_cache_get is not None:
        for name in order_list:
            item = by_name.get(name)
            if item is None:
                continue
            try:
                cached = delta_cache_get(name)
            except Exception:
                cached = None
            if isinstance(cached, Mapping):
                probe = dict(item)
                probe["greeks"] = dict(cached.get("greeks") or {}) if isinstance(cached.get("greeks"), Mapping) else {}
                probe["delta_observed_at_ms"] = cached.get("delta_observed_at_ms")
                probe["delta_source"] = str(cached.get("delta_source") or "cache.greeks.delta")
                probe["delta_book_received_ms"] = cached.get("delta_book_received_ms")
                check_ts = max(
                    int(captured_ts_ms) if isinstance(captured_ts_ms, int) else 0,
                    int(probe["delta_book_received_ms"]) if isinstance(probe["delta_book_received_ms"], int) else 0,
                )
                cat, _det, _rsn = _classify_short_delta(
                    probe, check_ts, max_age_ms=delta_max_age_ms, max_abs=delta_max_abs,
                )
                if cat in {"verified_eligible", "verified_above_max"}:
                    item["greeks"] = probe["greeks"]
                    item["delta_observed_at_ms"] = probe["delta_observed_at_ms"]
                    item["delta_source"] = probe["delta_source"]
                    item["delta_book_received_ms"] = probe["delta_book_received_ms"]
                    item["delta_cache_hit"] = True
                    if isinstance(probe["delta_book_received_ms"], int):
                        receipts[name] = probe["delta_book_received_ms"]
                    if isinstance(cached.get("response_hash"), str):
                        hashes[name] = cached["response_hash"]
                    cache_hit_instruments.append(name)
                    cache_hit_set.add(name)

    wire_order = [name for name in order_list if name not in cache_hit_set]
    for idx, name in enumerate(wire_order):
        item = by_name.get(name)
        if item is None:
            continue

        aborted = False
        for attempt in range(2):
            if attempt > 0 and retry_delay_s > 0:
                if deadline_mono is not None and mono_fn() + retry_delay_s > deadline_mono:
                    errors.setdefault(name, "round_deadline_exceeded")
                    skipped_instruments[name] = "round_deadline_exceeded"
                    abort_reason = "round_deadline_exceeded"
                    _mark_remaining(idx + 1, "round_deadline_exceeded")
                    aborted = True
                    break
                sleep_fn(retry_delay_s)
            ok, stop_why = _pace()
            if not ok:
                reason = stop_why or "rate_limit_cooldown"
                errors.setdefault(name, reason)
                skipped_instruments[name] = reason
                abort_reason = reason
                _mark_remaining(idx + 1, reason)
                aborted = True
                break
            if attempt == 0:
                wire_initial += 1
            else:
                wire_retry += 1
            try:
                response = get(base_url, "/public/get_order_book",
                               {"instrument_name": name, "depth": 1}, timeout)
                result = response.get("result") if isinstance(response, Mapping) else None
                if not isinstance(result, Mapping) or result.get("instrument_name") != name:
                    raise ValueError("unexpected order book result")
                receipt_ms = (response.get("data_engine_receipt") or {}).get("retrieved_at_ms")
                if not isinstance(receipt_ms, int):
                    receipt_ms = int(now() * 1000)
                receipts[name] = receipt_ms
                hashes[name] = _hash(response)
                greeks = result.get("greeks") if isinstance(result.get("greeks"), Mapping) else {}
                item["greeks"] = dict(greeks)
                item["delta_observed_at_ms"] = result.get("timestamp")
                item["delta_source"] = "public/get_order_book.greeks.delta"
                item["delta_book_received_ms"] = receipts[name]
                errors.pop(name, None)
                if attempt:
                    retried.append(name)
                if delta_cache_put is not None:
                    check_ts = max(receipt_ms, int(captured_ts_ms) if isinstance(captured_ts_ms, int) else 0)
                    cat, _det, _rsn = _classify_short_delta(
                        item, check_ts, max_age_ms=delta_max_age_ms, max_abs=delta_max_abs,
                    )
                    if cat in {"verified_eligible", "verified_above_max"}:
                        try:
                            delta_cache_put(name, {
                                "instrument_name": name,
                                "greeks": dict(greeks),
                                "delta_observed_at_ms": item["delta_observed_at_ms"],
                                "delta_source": item["delta_source"],
                                "delta_book_received_ms": item["delta_book_received_ms"],
                                "response_hash": hashes[name],
                            })
                        except Exception:
                            pass
                break
            except Exception as exc:
                # the first failure is the cause; keep it if the retry fails too
                code = _error_code(exc)
                errors.setdefault(name, code)
                is_maint = bool(getattr(exc, "is_maintenance", False)) or code == "DERIBIT_MAINTENANCE"
                is_rl = bool(getattr(exc, "is_rate_limit", False)) or code == "DERIBIT_HTTP_429"
                if is_maint:
                    maintenance_hits += 1
                    abort_reason = "DERIBIT_MAINTENANCE"
                    if pacer is not None:
                        pacer.record_maintenance()
                    _mark_remaining(idx + 1, "DERIBIT_MAINTENANCE_SKIPPED")
                    aborted = True
                    break
                if is_rl:
                    rate_limit_hits += 1
                    abort_reason = "DERIBIT_HTTP_429"
                    exc_retry = getattr(exc, "retry_after_ms", None)
                    if isinstance(exc_retry, int) and exc_retry > 0:
                        retry_after_ms = max(retry_after_ms or 0, exc_retry)
                    if pacer is not None:
                        pacer.record_rate_limit(exc_retry)
                    _mark_remaining(idx + 1, "DERIBIT_HTTP_429_SKIPPED")
                    aborted = True
                    break
                if code in {"FETCH_BUDGET_EXHAUSTED", "FETCH_BACKOFF_ACTIVE"}:
                    abort_reason = code
                    _mark_remaining(idx + 1, code)
                    aborted = True
                    break
        if aborted:
            break

    return enriched, {
        "order_book_received_ms": receipts,
        "order_book_response_hashes": hashes,
        "order_book_errors": errors,
        "order_book_retried": sorted(retried),
        "requested_count": len(short_set),
        "requested_instruments": sorted(short_set),
        "wire_requests_total": wire_initial + wire_retry,
        "wire_requests_initial": wire_initial,
        "wire_requests_retry": wire_retry,
        "cache_hits": len(cache_hit_instruments),
        "cache_hit_instruments": sorted(cache_hit_instruments),
        "skipped_instruments": skipped_instruments,
        "rate_limit_hits": rate_limit_hits,
        "maintenance_hits": maintenance_hits,
        "abort_reason": abort_reason,
        "retry_after_ms": retry_after_ms,
    }


def _parse_retry_after_ms(headers: Any) -> int | None:
    if headers is None:
        return None
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    if not raw:
        return None
    try:
        secs = float(str(raw).strip())
    except ValueError:
        return None
    if not math.isfinite(secs) or secs <= 0:
        return None
    return max(1_000, min(120_000, int(round(secs * 1000))))


class DeribitPublicError(ValueError):
    """A Deribit public API error with the exchange's own reason, e.g. system maintenance or 429."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        retry_after_ms: int | None = None,
        error_code: int | None = None,
    ):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.status = status
        self.retry_after_ms = retry_after_ms
        self.error_code = error_code

    @property
    def is_maintenance(self) -> bool:
        return self.code == "DERIBIT_MAINTENANCE" or self.error_code == 11051

    @property
    def is_rate_limit(self) -> bool:
        return self.code == "DERIBIT_HTTP_429" or self.status == 429 or self.error_code == 10028

    @classmethod
    def from_http(cls, exc: "urllib.error.HTTPError", path: str) -> "DeribitPublicError":
        reason, error_code = None, None
        retry_after_ms = _parse_retry_after_ms(getattr(exc, "headers", None))
        try:
            body = json.loads(exc.read(4096).decode("utf-8", "replace"))
            error = body.get("error") if isinstance(body, Mapping) else None
            if isinstance(error, Mapping):
                reason, error_code = error.get("message"), error.get("code")
        except Exception:  # noqa: BLE001 - the HTTP status is still reported
            pass
        if reason == "system_maintenance" or error_code == 11051:
            code = "DERIBIT_MAINTENANCE"
        elif exc.code == 429 or error_code == 10028 or reason == "too_many_requests":
            code = "DERIBIT_HTTP_429"
        else:
            code = f"DERIBIT_HTTP_{exc.code}"
        return cls(
            code,
            f"{reason or exc.reason} ({path}, HTTP {exc.code})",
            status=exc.code,
            retry_after_ms=retry_after_ms,
            error_code=error_code if isinstance(error_code, int) else None,
        )


def _public_get(base_url: str, path: str, params: Mapping[str, Any], timeout_s: float) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{base_url.rstrip('/')}{path}?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": "astra-underwriting-discovery/2.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DeribitPublicError.from_http(exc, path) from exc
    if isinstance(payload, Mapping) and isinstance(payload.get("error"), Mapping):
        err = payload["error"]
        reason = err.get("message")
        err_code = err.get("code")
        if reason == "system_maintenance" or err_code == 11051:
            raise DeribitPublicError("DERIBIT_MAINTENANCE", f"{reason} ({path})", status=503, error_code=11051)
        if err_code == 10028 or reason == "too_many_requests":
            raise DeribitPublicError("DERIBIT_HTTP_429", f"{reason} ({path})", status=429, error_code=10028)
    if not isinstance(payload, Mapping) or "result" not in payload:
        raise ValueError(f"unexpected Deribit response from {path}")
    return dict(payload)


def capture_public_deribit_discovery(
    *,
    base_url: str = DERIBIT_BASE_URL,
    timeout_s: float = 10.0,
    captured_ts_ms: int | None = None,
    quantity_btc: float = DEFAULT_QUANTITY_BTC,
    width_usd: float = DEFAULT_WIDTH_USD,
    transport: Callable | None = None,
    pacer: DeribitPacer | None = None,
    min_interval_s: float | None = None,
    max_round_s: float = 25.0,
    retry_delay_s: float = 0.25,
    delta_cache_get: Callable[[str], Mapping[str, Any] | None] | None = None,
    delta_cache_put: Callable[[str, Mapping[str, Any]], None] | None = None,
    start_side: str = "put",
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """One-shot public capture: metadata/index plus candidate-short book greeks."""
    timeout = _number("timeout_s", timeout_s, positive=True)
    get = transport or _public_get
    effective_min_interval = (0.40 if (transport is None and pacer is None) else 0.0) if min_interval_s is None else float(min_interval_s)
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
    ordered_names, short_names, short_counts, _side_lists = _ordered_short_candidates(
        instruments, selected_expiry, captured=selection_clock_ms,
        index_price=index_price, quantity_btc=quantity, width_usd=width,
        start_side=start_side,
    )
    enriched, delta_identity = _with_public_greeks(
        instruments,
        short_names,
        base_url=base_url,
        timeout=timeout,
        get=get,
        now=time.time,
        pacer=pacer,
        min_interval_s=effective_min_interval,
        max_round_s=max_round_s,
        retry_delay_s=retry_delay_s,
        delta_cache_get=delta_cache_get,
        delta_cache_put=delta_cache_put,
        ordered_names=ordered_names,
        captured_ts_ms=selection_clock_ms,
        sleep=sleep,
    )
    order_book_receipts = list(delta_identity.get("order_book_received_ms", {}).values())
    capture_completed_ms = max([selection_clock_ms, *order_book_receipts])
    failed_names = set(delta_identity.get("order_book_errors") or {})
    discovered = discover(
        enriched,
        index_price,
        capture_completed_ms,
        quantity_btc=quantity,
        width_usd=width,
        failed_names=failed_names,
    )
    put_ok = discovered["candidates"]["put"]["status"] == "candidate"
    call_ok = discovered["candidates"]["call"]["status"] == "candidate"
    put_pop_ok = ((discovered["candidates"]["put"].get("delta_population") or {}).get("complete") is True)
    call_pop_ok = ((discovered["candidates"]["call"].get("delta_population") or {}).get("complete") is True)
    abort_reason = delta_identity.get("abort_reason")
    if abort_reason == "DERIBIT_MAINTENANCE":
        capture_state = "MAINTENANCE"
        upstream_status = "deribit_maintenance"
    else:
        if abort_reason == "DERIBIT_HTTP_429":
            upstream_status = "deribit_rate_limited"
        elif delta_identity.get("order_book_errors"):
            upstream_status = "deribit_unavailable"
        else:
            upstream_status = None
        if discovered["selected_expiry_ms"] is None:
            capture_state = "INCOMPLETE"
        elif (put_pop_ok and call_pop_ok) or (put_ok and call_ok):
            capture_state = "COMPLETE"
        elif put_ok or call_ok:
            capture_state = "PARTIAL"
        else:
            capture_state = "INCOMPLETE"

    raw_identity = {
        "endpoints": ["public/get_index_price", "public/get_instruments", "public/get_order_book"],
        "request_started_ms": request_started_ms,
        "requested_capture_ts_ms": captured_ts_ms,
        "index_received_ms": index_received_ms,
        "instrument_received_ms": instrument_received_ms,
        "selection_clock_ms": selection_clock_ms,
        "capture_completed_ms": capture_completed_ms,
        "delta_short_candidate_counts": short_counts,
        "start_side": "call" if start_side == "call" else "put",
        "capture_state": capture_state,
        "upstream_status": upstream_status,
        "retry_after_ms": delta_identity.get("retry_after_ms"),
        "wire_requests_total": 2 + int(delta_identity.get("wire_requests_total") or 0),
        "index_response_hash": _hash(index_response),
        "instrument_response_hash": _hash(instrument_response),
        "delta_capture": delta_identity,
    }
    # Preserve the actual selection inputs for day/manual freezes. No second
    # HTTP request, mark substitution, or re-fetch is used for provenance.
    fields = ("instrument_name", "kind", "base_currency", "settlement_currency",
              "quote_currency", "counter_currency", "price_index", "instrument_type",
              "state", "option_type", "expiration_timestamp", "contract_size", "strike",
              "min_trade_amount", "greeks", "delta_observed_at_ms", "delta_source", "delta_book_received_ms")
    selection_inputs = [{key: row[key] for key in fields if key in row}
                        for row in enriched if isinstance(row, Mapping)]
    return {
        **discovered,
        "capture_state": capture_state,
        "upstream_status": upstream_status,
        "data_identity": {"kind": "live_public_capture", **raw_identity},
        "selection_inputs": {"index": dict(index_result), "instruments": selection_inputs},
    }
