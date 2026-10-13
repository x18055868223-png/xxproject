"""BTC dynamic cross-asset reference (phase 1, background only).

Which external assets BTC has recently been trading with: same-direction, opposite,
weakly related, or not distinguishable on the sample. Contemporaneous co-movement of
aligned daily returns; not causation, not a forecast. At daily frequency no external
asset leads BTC (design doc §4.5), so nothing here carries direction.

One observation per US trading day, every asset at the close of the QQQ regular
session (16:00 New York, 13:00 on early-close days; DST follows the exchange bars).
Rate axes are not computed: at daily frequency they failed validation (design §5).

Design and validation: docs/astra/CROSS_ASSET_REFERENCE_DESIGN_20261013.md.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping

if __package__:
    from . import astra_map_sources_v24 as map_sources
else:  # direct CLI execution from tools/
    import astra_map_sources_v24 as map_sources

SCHEMA = "astra_cross_asset_v1@1.0.0"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
BTC_SYMBOL = "BTC-USD"
ANCHOR = "qqq"
# key, Yahoo symbol. Order is the display order.
AXES: tuple[tuple[str, str], ...] = (("qqq", "QQQ"), ("gold", "GC=F"), ("dxy", "DX-Y.NYB"))

# Chosen by the validation in the design doc (appendix B, validate.py): W=60 keeps DXY's
# sign 89% of the time over the next 20 sessions (79% at W=40) and gives the best
# out-of-sample attribution; MATERIAL_R 0.35 leaves 18% of axis readings undecided.
LONG_WINDOW = 60
SHORT_WINDOW = 20
Z_RELATION = 1.96        # 95% interval: a relation needs the interval to exclude 0
Z_WEAK = 1.645           # 90% interval: equivalence test for "weak" (TOST at 5%)
MATERIAL_R = 0.35        # |r| below this explains < ~12% of BTC variance
OVERLAP_R = 0.5          # two main axes this correlated carry overlapping information
MAX_MAIN = 2
FETCH_RANGE = "6mo"      # ~125 trading days: long window + short-window history
SESSION_MS = int(6.5 * 3600 * 1000)


# ---------------------------------------------------------------- data

def parse_chart(payload: Any) -> list[tuple[int, float]]:
    """Yahoo v8 chart -> [(bar_start_s, close)], bars with a close only."""
    try:
        result = payload["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("chart_payload_malformed") from exc
    out = []
    for ts, close in zip(stamps, closes):
        if (isinstance(ts, int) and isinstance(close, (int, float)) and not isinstance(close, bool)
                and math.isfinite(close) and close > 0):
            out.append((ts, float(close)))
    return sorted(out)


def _ny_day(ts_s: int) -> str:
    # The QQQ closing bar starts 15:30 or 12:30 New York; any of those is the same NY and UTC-5 date.
    return datetime.fromtimestamp(ts_s - 5 * 3600, tz=timezone.utc).strftime("%Y-%m-%d")


def align(bars: Mapping[str, list[tuple[int, float]]], *, bar_s: int = 3600) -> list[dict[str, Any]]:
    """One row per QQQ session: the close at the end of the last QQQ bar, every other
    asset at the close of its hourly bar ending at that moment (within one hour)."""
    last: dict[str, tuple[int, float]] = {}
    for ts, close in bars[ANCHOR]:
        if ts % 3600 == 1800:  # regular-session bars only; a stray 16:00:00 point is not a bar
            last[_ny_day(ts)] = (ts, close)
    others = [k for k in bars if k != ANCHOR]
    index = {k: sorted(bars[k]) for k in others}
    rows = []
    for day in sorted(last):
        ts, close = last[day]
        end = ts + 1800  # the last session bar is the 30-minute one ending at the close
        row = {"date": day, "close_ts_ms": end * 1000, ANCHOR: close}
        for k in others:
            value = None
            for bts, bclose in reversed(index[k]):
                bend = bts + bar_s
                if bend <= end:
                    if end - bend <= bar_s:
                        value = bclose
                    break
            row[k] = value
        if all(row[k] is not None for k in others):
            rows.append(row)
    return rows


def returns(rows: list[Mapping[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    out = []
    for a, b in zip(rows, rows[1:]):
        out.append({"date": b["date"], "close_ts_ms": b.get("close_ts_ms"),
                    **{k: math.log(b[k] / a[k]) for k in keys}})
    return out


# ---------------------------------------------------------------- statistics

def corr(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 4 or n != len(y):
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if not sxx or not syy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def interval(r: float, n: int, z: float) -> tuple[float, float]:
    f = math.atanh(max(-0.9999, min(0.9999, r)))
    s = 1 / math.sqrt(n - 3)
    return math.tanh(f - z * s), math.tanh(f + z * s)


def state(r: float | None, n: int) -> str:
    """positive / negative: 95% interval excludes 0. weak: 90% interval inside ±MATERIAL_R.
    uncertain: neither -- the sample cannot tell a relation from none."""
    if r is None or n < 10:
        return "uncertain"
    lo, hi = interval(r, n, Z_RELATION)
    if lo > 0:
        return "positive"
    if hi < 0:
        return "negative"
    lo, hi = interval(r, n, Z_WEAK)
    return "weak" if -MATERIAL_R < lo and hi < MATERIAL_R else "uncertain"


def trend(long_state: str, short_state: str) -> str:
    rel = ("positive", "negative")
    if long_state in rel:
        if short_state == long_state:
            return "stable"
        return "reversed" if short_state in rel else "fading"
    return "emerging" if short_state in rel else "none"


def ols(y: list[float], xs: list[list[float]]) -> tuple[list[float], float] | None:
    """Least squares with intercept; returns (slopes, R²). Tiny Gaussian elimination."""
    n, k = len(y), len(xs)
    if n <= k + 2:
        return None
    cols = [[1.0] * n] + xs
    m = [[sum(a * b for a, b in zip(ci, cj)) for cj in cols] for ci in cols]
    v = [sum(a * b for a, b in zip(ci, y)) for ci in cols]
    size = k + 1
    for i in range(size):
        p = max(range(i, size), key=lambda r: abs(m[r][i]))
        if abs(m[p][i]) < 1e-18:
            return None
        m[i], m[p], v[i], v[p] = m[p], m[i], v[p], v[i]
        for r in range(i + 1, size):
            f = m[r][i] / m[i][i]
            for c in range(i, size):
                m[r][c] -= f * m[i][c]
            v[r] -= f * v[i]
    beta = [0.0] * size
    for i in reversed(range(size)):
        beta[i] = (v[i] - sum(m[i][c] * beta[c] for c in range(i + 1, size))) / m[i][i]
    my = sum(y) / n
    fitted = [beta[0] + sum(beta[j + 1] * xs[j][t] for j in range(k)) for t in range(n)]
    sst = sum((a - my) ** 2 for a in y)
    sse = sum((a - f) ** 2 for a, f in zip(y, fitted))
    return beta[1:], (1 - sse / sst) if sst else 0.0


# ---------------------------------------------------------------- assessment

def _window(rows: list[Mapping[str, Any]], key: str, w: int) -> dict[str, Any]:
    part = rows[-w:]
    n = len(part)
    r = corr([p["btc"] for p in part], [p[key] for p in part])
    out: dict[str, Any] = {"n": n, "r": None if r is None else round(r, 4), "state": state(r, n)}
    if r is not None and n > 3:
        lo, hi = interval(r, n, Z_RELATION)
        out.update(lo=round(lo, 4), hi=round(hi, 4))
    return out


def assess(rets: list[Mapping[str, Any]], *, axes: tuple[str, ...] = tuple(k for k, _ in AXES)) -> dict[str, Any]:
    """Pure function of aligned daily returns (each row: btc + one key per axis)."""
    n = len(rets)
    if n < LONG_WINDOW + 1:
        return {"schema": SCHEMA, "status": "insufficient_history", "observations": n,
                "windows": {"long": LONG_WINDOW, "short": SHORT_WINDOW},
                "as_of_close_ms": rets[-1].get("close_ts_ms") if rets else None}
    out_axes = []
    for key in axes:
        lw, sw = _window(rets, key, LONG_WINDOW), _window(rets, key, SHORT_WINDOW)
        part = rets[-LONG_WINDOW:]
        fit = ols([p["btc"] for p in part], [[p[key] for p in part]])
        out_axes.append({"key": key, "long": lw, "short": sw, "trend": trend(lw["state"], sw["state"]),
                         "beta": round(fit[0][0], 4) if fit else None})
    main = sorted((a for a in out_axes if a["long"]["state"] in ("positive", "negative")),
                  key=lambda a: -abs(a["long"]["r"]))[:MAX_MAIN]
    if main:
        kind = "references"
    elif all(a["long"]["state"] == "weak" for a in out_axes):
        kind = "independent"
    else:
        kind = "uncertain"
    overlaps = []
    for i, a in enumerate(main):
        for b in main[i + 1:]:
            part = rets[-LONG_WINDOW:]
            r = corr([p[a["key"]] for p in part], [p[b["key"]] for p in part])
            if r is not None and abs(r) >= OVERLAP_R:
                overlaps.append({"axes": [a["key"], b["key"]], "r": round(r, 4)})
    part = rets[-LONG_WINDOW:]
    joint = ols([p["btc"] for p in part], [[p[k] for p in part] for k in axes])
    return {
        "schema": SCHEMA, "status": "ok", "observations": n,
        "windows": {"long": LONG_WINDOW, "short": SHORT_WINDOW},
        "as_of_date": rets[-1]["date"], "as_of_close_ms": rets[-1].get("close_ts_ms"),
        "axes": out_axes,
        "verdict": {"kind": kind, "main": [a["key"] for a in main], "overlaps": overlaps},
        "joint_r2": round(joint[1], 4) if joint else None,
        # Only the main references attribute: adding the other axes overfits (design §5 V3).
        "attribution": attribution(rets, tuple(a["key"] for a in main)) if main else None,
    }


def attribution(rets: list[Mapping[str, Any]], axes: tuple[str, ...]) -> dict[str, Any] | None:
    """Last session's BTC move split into the part the external moves explain (coefficients
    estimated on the LONG_WINDOW sessions before it, so out of sample) and BTC's own part."""
    if len(rets) < LONG_WINDOW + 1:
        return None
    last, part = rets[-1], rets[-LONG_WINDOW - 1:-1]
    fit = ols([p["btc"] for p in part], [[p[k] for p in part] for k in axes])
    if not fit:
        return None
    explained = sum(b * last[k] for b, k in zip(fit[0], axes))
    pct = lambda v: round((math.exp(v) - 1) * 100, 3)
    return {"date": last["date"], "btc_pct": pct(last["btc"]), "explained_pct": round(explained * 100, 3),
            "own_pct": round((last["btc"] - explained) * 100, 3),
            "axis_pct": {k: pct(last[k]) for k in axes}}


# ---------------------------------------------------------------- contract window

def us_sessions(start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    """US regular cash sessions (NYSE calendar) overlapping [start_ms, end_ms).
    Early-close days are treated as full sessions; the overlap is approximate there."""
    first = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).date() - timedelta(days=1)
    last = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).date()
    out = []
    for day in map_sources.nyse_trading_days(first, last):
        close = map_sources.nyse_session_close_ms(day)
        open_ = close - SESSION_MS
        lo, hi = max(open_, start_ms), min(close, end_ms)
        if hi > lo:
            out.append({"day": day.isoformat(), "open_ms": open_, "close_ms": close, "overlap_ms": hi - lo})
    return out


def session_closed(day: str, now_ms: int) -> bool:
    """A session still trading has no closing observation yet (its last bar is mid-day)."""
    return map_sources.nyse_session_close_ms(date.fromisoformat(day)) <= now_ms


def contract_window(now_ms: int, expiry_ms: int | None) -> dict[str, Any] | None:
    if not isinstance(expiry_ms, int) or expiry_ms <= now_ms:
        return None
    sessions = us_sessions(now_ms, expiry_ms)
    return {"from_ms": now_ms, "expiry_ms": expiry_ms, "covers_us_session": bool(sessions),
            "sessions": sessions, "overlap_ms": sum(s["overlap_ms"] for s in sessions),
            "basis": "research_observation"}


# ---------------------------------------------------------------- collection

def collect(get_json: Callable[[str, Mapping[str, Any]], Any], now_ms: int) -> dict[str, Any]:
    """Fetch hourly bars (4 requests) and assess. Errors become a status, never raise."""
    bars: dict[str, list[tuple[int, float]]] = {}
    symbols = {"btc": BTC_SYMBOL, **dict(AXES)}
    for key, symbol in symbols.items():
        try:
            bars[key] = parse_chart(get_json(YAHOO_CHART_URL + symbol, {
                "interval": "1h", "range": FETCH_RANGE, "includePrePost": "false"}))
        except Exception as exc:
            return {"schema": SCHEMA, "status": "fetch_failed", "computed_at_ms": now_ms,
                    "failed_symbol": symbol, "error_type": type(exc).__name__}
    rows = [r for r in align(bars) if session_closed(r["date"], now_ms)]
    result = assess(returns(rows, ("btc",) + tuple(k for k, _ in AXES)))
    result.update(computed_at_ms=now_ms, source="yahoo_chart_1h",
                  symbols=symbols)
    return result


def latest_closed_day(now_ms: int) -> str | None:
    """Newest trading date whose (calendar) close has passed. Dates, not close times: an
    early close stores 13:00 from the bars while the calendar says 16:00."""
    today = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).date()
    days = [d for d in map_sources.nyse_trading_days(today - timedelta(days=10), today)
            if map_sources.nyse_session_close_ms(d) <= now_ms]
    return max(days).isoformat() if days else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def due(previous: Mapping[str, Any] | None, now_ms: int, *, retry_ms: int = 900_000) -> bool:
    """Refresh after each US close (and on a failed or missing result, at most every retry_ms).
    Unreadable or future clocks count as missing, so a bad state file cannot stop refreshing."""
    if not isinstance(previous, Mapping):
        return True
    attempt = previous.get("last_attempt") if isinstance(previous.get("last_attempt"), Mapping) else {}
    tried = max([t for t in (_int(previous.get("computed_at_ms")), _int(attempt.get("computed_at_ms")))
                 if t is not None and t <= now_ms] or [None], key=lambda t: t or 0)
    if tried is None:
        return True
    if now_ms - tried < retry_ms:
        return False
    if previous.get("status") != "ok":
        return True
    closed = latest_closed_day(now_ms)
    as_of = previous.get("as_of_date")
    return closed is not None and (not isinstance(as_of, str) or closed > as_of)


def default_get_json(url: str, params: Mapping[str, Any]) -> Any:
    import json  # noqa: PLC0415
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    request = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed https host
        return json.loads(response.read().decode("utf-8"))
