# -*- coding: utf-8 -*-
"""Deribit public-data snapshot helpers for VRP simulation."""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from vrp_model import normalise_iv


API_BASE = "https://www.deribit.com/api/v2"


def public_get(method: str, **params: Any) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"{API_BASE}/public/{method}"
    if query:
        url += "?" + query
    with urllib.request.urlopen(url, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(payload["error"])
    return payload.get("result")


def iso_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def expiry_label(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enrich_instrument(inst: Dict[str, Any], ticker: Dict[str, Any], now_ms: int) -> Dict[str, Any]:
    greeks = ticker.get("greeks") or {}
    expiration_timestamp = int(inst["expiration_timestamp"])
    dte_hours = max(0.0, (expiration_timestamp - now_ms) / 3_600_000.0)
    delta = greeks.get("delta")
    gamma = greeks.get("gamma")
    return {
        "instrument_name": inst.get("instrument_name"),
        "expiry": expiry_label(expiration_timestamp),
        "expiration_timestamp": expiration_timestamp,
        "dte_hours": dte_hours,
        "strike": float(inst.get("strike") or 0.0),
        "option_type": inst.get("option_type"),
        "mark_price": ticker.get("mark_price"),
        "best_bid_price": ticker.get("best_bid_price"),
        "best_ask_price": ticker.get("best_ask_price"),
        "bid_iv": ticker.get("bid_iv"),
        "ask_iv": ticker.get("ask_iv"),
        "mark_iv": ticker.get("mark_iv"),
        "underlying_price": ticker.get("underlying_price") or ticker.get("index_price"),
        "delta": delta,
        "abs_delta": abs(delta) if isinstance(delta, (int, float)) else None,
        "gamma": gamma,
        "open_interest": ticker.get("open_interest"),
        "volume": (ticker.get("stats") or {}).get("volume"),
        "timestamp": ticker.get("timestamp"),
        "raw_ticker_state": ticker.get("state"),
    }


def select_expiry_bands(
    rows: Iterable[Dict[str, Any]],
    short_hours: Tuple[float, float] = (24.0, 72.0),
    term_days: Tuple[float, float] = (5.0, 10.0),
) -> Tuple[List[str], List[str]]:
    short: List[str] = []
    term: List[str] = []
    fallback_terms: List[Tuple[float, str]] = []
    term_hours = (term_days[0] * 24.0, term_days[1] * 24.0)
    for row in rows:
        expiry = str(row["expiry"])
        dte = float(row["dte_hours"])
        if short_hours[0] <= dte <= short_hours[1] and expiry not in short:
            short.append(expiry)
        if term_hours[0] <= dte <= term_hours[1] and expiry not in term:
            term.append(expiry)
        if dte > short_hours[1] and expiry not in [x[1] for x in fallback_terms]:
            fallback_terms.append((dte, expiry))
    if not term and fallback_terms:
        fallback_terms.sort(key=lambda x: x[0])
        term.append(fallback_terms[0][1])
    return sorted(short), sorted(term)


def rv_percentile(history: Sequence[float], current: Optional[float]) -> Optional[float]:
    vals = [float(v) for v in history if v is not None]
    if not vals or current is None:
        return None
    c = float(current)
    return sum(1 for v in vals if v <= c) / len(vals)


def annualized_realized_vol_from_closes(
    closes: Sequence[float],
    bars_per_day: int = 24,
    annualization_days: int = 365,
) -> Optional[float]:
    prices = [float(x) for x in closes if x and float(x) > 0]
    if len(prices) < 3:
        return None
    returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(bars_per_day * annualization_days)


def rv_context_from_chart_closes(ticks: Sequence[int], closes: Sequence[float]) -> Dict[str, Any]:
    close_values = [float(c) for c in closes if c and float(c) > 0]
    if not close_values:
        return {}
    trailing_24 = annualized_realized_vol_from_closes(close_values[-25:])
    trailing_72 = annualized_realized_vol_from_closes(close_values[-73:])
    trailing_7d = annualized_realized_vol_from_closes(close_values[-169:])

    rolling_24: List[float] = []
    for i in range(25, len(close_values) + 1):
        rv = annualized_realized_vol_from_closes(close_values[i - 25 : i])
        if rv is not None:
            rolling_24.append(rv)
    current = rolling_24[-1] if rolling_24 else trailing_24
    history_days = int(len(close_values) / 24)
    return {
        "rv_24h": trailing_24,
        "rv_72h": trailing_72,
        "rv_7d": trailing_7d,
        "rv_percentile_90d": rv_percentile(rolling_24[-2160:], current),
        "history_days": history_days,
        "chart_points": len(close_values),
        "source": "DERIBIT_BTC_PERPETUAL_1H_CLOSES",
    }


def parse_historical_vol(result: Sequence[Any]) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    seen = set()
    for item in result or []:
        if isinstance(item, dict) and "value" in item:
            item = item["value"]
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        ts = int(item[0])
        if ts in seen:
            continue
        seen.add(ts)
        vol = normalise_iv(float(item[1]))
        if vol is None:
            continue
        parsed.append({"timestamp": ts, "iso": iso_from_ms(ts), "vol": vol})
    parsed.sort(key=lambda x: x["timestamp"])
    return parsed


def rv_windows_from_deribit_hv(hv: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not hv:
        return {
            "rv_24h": None,
            "rv_72h": None,
            "rv_7d": None,
            "rv_percentile_90d": None,
            "history_days": 0,
        }
    vols = [x["vol"] for x in hv]
    current = vols[-1]
    # Deribit historical volatility is hourly, so trailing buckets are simple tails.
    rv_24h = sum(vols[-24:]) / min(len(vols), 24)
    rv_72h = sum(vols[-72:]) / min(len(vols), 72)
    rv_7d = sum(vols[-168:]) / min(len(vols), 168)
    lookback = vols[-2160:]  # 90 days of hourly points when available.
    return {
        "rv_24h": rv_24h,
        "rv_72h": rv_72h,
        "rv_7d": rv_7d,
        "rv_percentile_90d": rv_percentile(lookback, current),
        "history_days": int(len(lookback) / 24),
        "hv_points": len(hv),
    }


def _basic_instrument_rows(instruments: Iterable[Dict[str, Any]], now_ms: int) -> List[Dict[str, Any]]:
    rows = []
    for inst in instruments:
        exp = int(inst.get("expiration_timestamp") or 0)
        rows.append(
            {
                "instrument_name": inst.get("instrument_name"),
                "expiry": expiry_label(exp),
                "expiration_timestamp": exp,
                "dte_hours": max(0.0, (exp - now_ms) / 3_600_000.0),
                "strike": inst.get("strike"),
                "option_type": inst.get("option_type"),
                "raw": inst,
            }
        )
    return rows


def fetch_deribit_snapshot(currency: str = "BTC", max_tickers: int = 260) -> Dict[str, Any]:
    now_ms = int(public_get("get_time"))
    index_name = f"{currency.lower()}_usd"
    index = public_get("get_index_price", index_name=index_name)
    instruments = public_get("get_instruments", currency=currency, kind="option", expired="false")
    basic_rows = _basic_instrument_rows(instruments, now_ms)
    short_expiries, term_expiries = select_expiry_bands(basic_rows)
    wanted_expiries = set(short_expiries + term_expiries)
    wanted = [r for r in basic_rows if r["expiry"] in wanted_expiries]
    wanted.sort(key=lambda r: (r["expiration_timestamp"], r["strike"], r["option_type"]))
    if max_tickers and len(wanted) > max_tickers:
        wanted = wanted[:max_tickers]

    option_rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    by_name = {inst["instrument_name"]: inst for inst in instruments}
    for row in wanted:
        name = str(row["instrument_name"])
        try:
            ticker = public_get("ticker", instrument_name=name)
            option_rows.append(enrich_instrument(by_name[name], ticker, now_ms))
            time.sleep(0.03)
        except Exception as exc:  # noqa: BLE001 - capture public API gaps in snapshot.
            errors.append({"instrument_name": name, "error": str(exc)})

    chart_rv: Dict[str, Any] = {}
    chart = None
    try:
        start_ms = now_ms - 90 * 24 * 3_600_000
        chart = public_get(
            "get_tradingview_chart_data",
            instrument_name=f"{currency}-PERPETUAL",
            start_timestamp=start_ms,
            end_timestamp=now_ms,
            resolution="60",
        )
        if isinstance(chart, dict) and chart.get("status") == "ok":
            chart_rv = rv_context_from_chart_closes(chart.get("ticks") or [], chart.get("close") or [])
    except Exception as exc:  # noqa: BLE001 - RV proxy can fall back to Deribit HV endpoint.
        errors.append({"instrument_name": f"{currency}-PERPETUAL", "error": "chart_rv:" + str(exc)})

    hv_raw = public_get("get_historical_volatility", currency=currency)
    hv = parse_historical_vol(hv_raw)
    hv_rv = rv_windows_from_deribit_hv(hv)
    rv = chart_rv or hv_rv

    return {
        "schema_name": "DeribitVrpMarketSnapshot",
        "schema_version": "nrd.integration.vrp.deribit_snapshot.v1.0",
        "currency": currency,
        "generated_at_ms": now_ms,
        "generated_at": iso_from_ms(now_ms),
        "index_price": index.get("index_price") if isinstance(index, dict) else None,
        "estimated_delivery_price": index.get("estimated_delivery_price") if isinstance(index, dict) else None,
        "short_expiries": short_expiries,
        "term_expiries": term_expiries,
        "option_rows": option_rows,
        "historical_volatility": hv,
        "perpetual_chart_1h": chart,
        "rv_context": rv,
        "rv_context_hv_crosscheck": hv_rv,
        "fetch_errors": errors,
        "instrument_count": len(instruments),
        "ticker_count": len(option_rows),
    }


def write_snapshot(snapshot: Dict[str, Any], output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    stamp = datetime.fromtimestamp(snapshot["generated_at_ms"] / 1000.0, tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"deribit_{snapshot['currency'].lower()}_snapshot_{stamp}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2)
    return path
