"""This-expiry option structure from Deribit public data (2026-10-14).

The map's gexmonitor levels are all-tenor (magnet) or unlabelled near-term buckets; none
is the call wall, put wall or pin of the expiry a ~24h policy actually settles on. One
public request returns every BTC option's open interest and mark IV, from which this
module computes, per expiry:

- Gamma walls: the strike with the largest call (put) dollar gamma, i.e. where dealer
  hedging pressure sits. Near expiry gamma concentrates at the money, so these walls are
  pulled toward spot.
- OI walls: the strike with the largest call (put) open interest, i.e. where positions
  are stacked.
- Pin: inside the expected move to expiry, the strike with the largest total dollar gamma.

Dollar gamma per 1% move = BS gamma (mark IV, forward = the expiry's underlying price,
no rates) x open interest (BTC) x F^2 x 0.01. Sign conventions (who is long) are not
known from public data, so no net-GEX sign is claimed.

A second public request gives hourly BTC-PERPETUAL bars, from which realized volatility
over 24h and 7d is computed with explicit windows (Q2's RV vs IV row).

Pure functions plus `collect`; the workbench does the scheduling and caching.
Design: docs/astra/DECISION_PATH_DESIGN_20261014.md section 5.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

SCHEMA = "astra_expiry_gamma_v1@1.0.0"
BOOK_SUMMARY_URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
CHART_URL = "https://www.deribit.com/api/v2/public/get_tradingview_chart_data"
RV_INSTRUMENT = "BTC-PERPETUAL"
REFRESH_MS = 5 * 60_000
RETRY_MS = 60_000
HORIZON_MS = 8 * 86_400_000      # expiries kept: the next eight days
STALE_MS = 20 * 60_000           # older than this the page says the walls are stale
TOP_N = 3
HOUR_MS = 3_600_000
YEAR_HOURS = 24 * 365
_NAME = re.compile(r"^BTC-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:\.\d+)?)-([CP])$")
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def parse_name(name: Any) -> dict[str, Any] | None:
    """BTC-15OCT26-83000-C -> {code, expiry_ms (08:00 UTC), strike, kind}."""
    match = _NAME.match(str(name or ""))
    if not match or match.group(2) not in _MONTHS:
        return None
    day, month, year, strike, kind = match.groups()
    try:
        expiry = datetime(2000 + int(year), _MONTHS[month], int(day), 8, tzinfo=timezone.utc)
    except ValueError:
        return None
    return {"code": f"{int(day)}{month}{year}", "expiry_ms": int(expiry.timestamp() * 1000),
            "strike": float(strike), "kind": "call" if kind == "C" else "put"}


def expiry_code(expiry_ms: int) -> str:
    d = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)
    return f"{d.day}{list(_MONTHS)[d.month - 1]}{d.year % 100:02d}"


def bs_gamma(forward: float, strike: float, years: float, vol: float) -> float:
    """Black-Scholes gamma per unit of underlying (no rates; vol as a fraction)."""
    if forward <= 0 or strike <= 0 or years <= 0 or vol <= 0:
        return 0.0
    sd = vol * math.sqrt(years)
    d1 = (math.log(forward / strike) + 0.5 * sd * sd) / sd
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (forward * sd)


def profile(rows: Sequence[Mapping[str, Any]], *, expiry_ms: int, now_ms: int) -> dict[str, Any] | None:
    """Walls and pin of one expiry from book-summary rows (any expiry; filtered here)."""
    years = (expiry_ms - now_ms) / (YEAR_HOURS * HOUR_MS)
    if years <= 0:
        return None
    strikes: dict[float, dict[str, float]] = {}
    forwards: list[float] = []
    for row in rows:
        meta = parse_name(row.get("instrument_name"))
        if not meta or meta["expiry_ms"] != expiry_ms:
            continue
        oi, iv, fwd = _num(row.get("open_interest")), _num(row.get("mark_iv")), _num(row.get("underlying_price"))
        if fwd is not None and fwd > 0:
            forwards.append(fwd)
        if oi is None or oi < 0 or iv is None or iv <= 0 or fwd is None or fwd <= 0:
            continue
        slot = strikes.setdefault(meta["strike"], {"call_gex": 0.0, "put_gex": 0.0, "call_oi": 0.0, "put_oi": 0.0,
                                                   "call_iv": 0.0, "put_iv": 0.0})
        gex = bs_gamma(fwd, meta["strike"], years, iv / 100) * oi * fwd * fwd * 0.01
        slot[meta["kind"] + "_gex"] += gex
        slot[meta["kind"] + "_oi"] += oi
        slot[meta["kind"] + "_iv"] = iv
    if not strikes or not forwards:
        return None
    forward = sorted(forwards)[len(forwards) // 2]
    atm = min(strikes, key=lambda k: abs(k - forward))
    ivs = [v for v in (strikes[atm]["call_iv"], strikes[atm]["put_iv"]) if v > 0]
    atm_iv = sum(ivs) / len(ivs) if ivs else None
    move = forward * atm_iv / 100 * math.sqrt(years) if atm_iv else None

    def top(key: str) -> list[dict[str, float]]:
        ranked = sorted(((k, v[key]) for k, v in strikes.items() if v[key] > 0), key=lambda kv: -kv[1])
        return [{"strike": k, "value": round(v, 2)} for k, v in ranked[:TOP_N]]

    pin = None
    if move:
        inside = {k: v["call_gex"] + v["put_gex"] for k, v in strikes.items() if abs(k - forward) <= move}
        if inside and max(inside.values()) > 0:
            pin = max(inside, key=inside.get)
    first = lambda items: items[0]["strike"] if items else None  # noqa: E731
    gamma_call, gamma_put, oi_call, oi_put = top("call_gex"), top("put_gex"), top("call_oi"), top("put_oi")
    return {
        "expiry": expiry_code(expiry_ms), "expiry_ms": expiry_ms, "hours_to_expiry": round(years * YEAR_HOURS, 2),
        "forward_usd": round(forward, 2), "atm_strike": atm, "atm_iv_pct": round(atm_iv, 2) if atm_iv else None,
        "expected_move_usd": round(move, 1) if move else None,
        "walls": {"gamma_call": first(gamma_call), "gamma_put": first(gamma_put),
                  "oi_call": first(oi_call), "oi_put": first(oi_put), "pin": pin},
        "top": {"gamma_call": gamma_call, "gamma_put": gamma_put, "oi_call": oi_call, "oi_put": oi_put},
        "totals": {"call_gex_usd_1pct": round(sum(v["call_gex"] for v in strikes.values()), 2),
                   "put_gex_usd_1pct": round(sum(v["put_gex"] for v in strikes.values()), 2),
                   "call_oi_btc": round(sum(v["call_oi"] for v in strikes.values()), 1),
                   "put_oi_btc": round(sum(v["put_oi"] for v in strikes.values()), 1)},
        "strike_count": len(strikes),
        "units": {"gex": "USD per 1% move, unsigned (dealer side unknown)", "oi": "BTC"},
    }


def realized_vol(chart: Mapping[str, Any], *, now_ms: int) -> dict[str, Any] | None:
    """Annualized close-to-close RV over the last 24 and 168 closed hourly bars."""
    ticks, closes = chart.get("ticks"), chart.get("close")
    if not isinstance(ticks, list) or not isinstance(closes, list):
        return None
    bars = sorted((int(t), float(c)) for t, c in zip(ticks, closes)
                  if _num(t) is not None and _num(c) is not None and c > 0 and int(t) + HOUR_MS <= now_ms)
    out: dict[str, Any] = {"instrument": RV_INSTRUMENT, "bar": "1h close-to-close",
                           "last_bar_end_ms": bars[-1][0] + HOUR_MS if bars else None}
    for label, n in (("rv_24h_pct", 24), ("rv_7d_pct", 168)):
        window = bars[-(n + 1):]
        if len(window) < n + 1 or window[-1][0] - window[0][0] != n * HOUR_MS:
            out[label] = None  # a missing bar would silently stretch the window
            continue
        rets = [math.log(b[1] / a[1]) for a, b in zip(window, window[1:])]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        out[label] = round(math.sqrt(var * YEAR_HOURS) * 100, 2)
    return out


def collect(get_json: Callable[[str, Mapping[str, Any]], Any], now_ms: int) -> dict[str, Any]:
    """Two public requests; errors become a status, never raise."""
    result: dict[str, Any] = {"schema": SCHEMA, "computed_at_ms": now_ms, "source": "deribit_public"}
    try:
        rows = get_json(BOOK_SUMMARY_URL, {"currency": "BTC", "kind": "option"})["result"]
        if not isinstance(rows, list):
            raise ValueError("book_summary_malformed")
    except Exception as exc:
        return {**result, "status": "fetch_failed", "error_type": type(exc).__name__}
    expiries = sorted({m["expiry_ms"] for m in map(lambda r: parse_name(r.get("instrument_name")), rows)
                       if m and now_ms < m["expiry_ms"] <= now_ms + HORIZON_MS})
    profiles = {}
    for expiry in expiries:
        item = profile(rows, expiry_ms=expiry, now_ms=now_ms)
        if item is not None:
            profiles[item["expiry"]] = item
    result.update(status="ok" if profiles else "no_expiry", expiries=profiles, instrument_count=len(rows))
    try:
        chart = get_json(CHART_URL, {"instrument_name": RV_INSTRUMENT, "resolution": "60",
                                     "start_timestamp": now_ms - 8 * 86_400_000, "end_timestamp": now_ms})["result"]
        result["realized_vol"] = realized_vol(chart, now_ms=now_ms)
    except Exception as exc:
        result["realized_vol"] = {"status": "fetch_failed", "error_type": type(exc).__name__}
    return result


def due(previous: Mapping[str, Any] | None, now_ms: int) -> bool:
    if not isinstance(previous, Mapping):
        return True
    attempt = previous.get("last_attempt") if isinstance(previous.get("last_attempt"), Mapping) else {}
    times = [t for t in (previous.get("computed_at_ms"), attempt.get("computed_at_ms"))
             if isinstance(t, int) and not isinstance(t, bool) and t <= now_ms]
    if not times:
        return True
    wait = REFRESH_MS if previous.get("status") == "ok" and not attempt else RETRY_MS
    return now_ms - max(times) >= wait


def for_expiry(result: Mapping[str, Any] | None, expiry_ms: Any, *, now_ms: int) -> dict[str, Any] | None:
    """The profile of one contract expiry with its age; None when it was not collected."""
    if not isinstance(result, Mapping) or not isinstance(expiry_ms, int):
        return None
    item = (result.get("expiries") or {}).get(expiry_code(expiry_ms))
    if not isinstance(item, Mapping):
        return None
    computed = result.get("computed_at_ms")
    age = now_ms - computed if isinstance(computed, int) else None
    return {**item, "computed_at_ms": computed, "age_ms": age,
            "stale": age is None or age > STALE_MS}
