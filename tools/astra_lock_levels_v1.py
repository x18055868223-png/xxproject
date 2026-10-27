"""This expiry's price structure, kept small inside a lock point (2026-10-27).

A lock's t0 and every later sample carry the levels the map shows for the contract's own
expiry, so the lock detail can say where the pin, the walls and max pain were at lock time
and where they are now. Sources, with the page's semantics (deploy/astra_workbench_v3/model.js
mapModel):

- the expiry profile (astra_expiry_gamma_v1.for_expiry): pin, dollar-Gamma walls, OI walls
  and the top dollar-Gamma strikes per side;
- gexmonitor gex_info in the snapshot's market facts: max pain of the next expiry and of the
  main expiry it picked, the all-tenor magnet; only the one whose expiry is this contract's is
  this expiry's max pain;
- the frozen BTC MAP in the snapshot: effective Gamma flip, on-chain holder cost bases, KPF
  acceptance zones.

Pure function; a missing source leaves its fields None, never a substitute.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping

SCHEMA = "lock_levels@0.1"
STALE_MS = 20 * 60_000           # astra_expiry_gamma_v1.STALE_MS
TOP_N = 3
KPF_MAX = 6
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _clock(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def expiry_token(expiry_ms: Any) -> str | None:
    """Deribit expiry code, e.g. 28OCT26 (astra_expiry_gamma_v1.expiry_code)."""
    if isinstance(expiry_ms, bool) or not isinstance(expiry_ms, int):
        return None
    d = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)
    return f"{d.day}{_MONTHS[d.month - 1]}{d.year % 100:02d}"


def _top(rows: Any) -> list[list[float]]:
    out = []
    for row in rows if isinstance(rows, list) else []:
        strike, value = _num(_map(row).get("strike")), _num(_map(row).get("value"))
        if strike is not None and value is not None:
            out.append([strike, round(value)])
    return out[:TOP_N]


def _same(a: Any, b: str | None) -> bool:
    return bool(b) and isinstance(a, str) and a.upper() == b


def compact(profile: Mapping[str, Any] | None, gex_info: Mapping[str, Any] | None,
            btc_map: Mapping[str, Any] | None, *, now_ms: int, expiry_ms: int | None = None) -> dict[str, Any]:
    """The levels block of one point. `now_ms` is the point's own clock (its age reference)."""
    prof = _map(profile)
    gex = _map(gex_info)
    walls = _map(prof.get("walls"))
    top = _map(prof.get("top"))
    token = (prof.get("expiry") if isinstance(prof.get("expiry"), str) else None) or expiry_token(expiry_ms)
    token = token.upper() if token else None

    # this expiry's max pain: the next expiry's when it is this one, else the main expiry's
    nxt = _map(gex.get("next_expiry"))
    this_pain = None
    if _same(nxt.get("expiry"), token):
        this_pain = _num(nxt.get("max_pain_price"))
    if this_pain is None and _same(gex.get("max_pain_target_expiry"), token):
        this_pain = _num(gex.get("max_pain_price"))

    pool = [r for r in _map(btc_map).get("reference_pool") or [] if isinstance(r, Mapping)]

    def pool_price(role: str) -> float | None:
        row = next((r for r in pool if r.get("role") == role), None)
        if row is None:
            return None
        price = _num(row.get("raw_price"))
        return price if price is not None else _num(row.get("display_center"))

    cost = _map(_map(_map(_map(_map(btc_map).get("background")).get("inventory")).get("metrics")).get("cost_basis"))
    costs = {"sth": _num(cost.get("cp_sth_usd")), "lth": _num(cost.get("cp_lth_usd")), "all": _num(cost.get("cp_all_usd"))}
    for key, role in (("sth", "CP_STH"), ("lth", "CP_LTH"), ("all", "CP_ALL")):
        if costs[key] is None:
            costs[key] = pool_price(role)
    kpf = []
    kpf_end = None
    for r in pool:
        if r.get("role") != "KPF_BASIN":
            continue
        lo, hi = _num(r.get("raw_low")), _num(r.get("raw_high"))
        if lo is not None and hi is not None:
            kpf.append([lo, hi])
        end = _clock(r.get("observation_end_ms"))
        if end is not None:
            kpf_end = max(end, kpf_end or end)

    computed = _clock(prof.get("computed_at_ms"))
    age = now_ms - computed if computed is not None else None
    return {
        "schema": SCHEMA,
        "expiry": token,
        "profile_present": bool(prof),
        "pin": _num(walls.get("pin")),
        "gamma_put": _num(walls.get("gamma_put")),
        "gamma_call": _num(walls.get("gamma_call")),
        "oi_put": _num(walls.get("oi_put")),
        "oi_call": _num(walls.get("oi_call")),
        "top_gamma_put": _top(top.get("gamma_put")),
        "top_gamma_call": _top(top.get("gamma_call")),
        "max_pain_this": this_pain,
        "max_pain_main": _num(gex.get("max_pain_price")),
        "max_pain_main_expiry": gex.get("max_pain_target_expiry") if isinstance(gex.get("max_pain_target_expiry"), str) else None,
        "magnet": _num(gex.get("magnet_price")),
        "flip": pool_price("EFFECTIVE_FLIP"),
        "cost_sth": costs["sth"],
        "cost_lth": costs["lth"],
        "cost_all": costs["all"],
        "cost_date": cost.get("latest_observation_date") if isinstance(cost.get("latest_observation_date"), str) else None,
        "kpf": sorted(kpf)[:KPF_MAX],
        "kpf_asof_ms": kpf_end,
        "gex_asof_ms": _clock(gex.get("fetched_at_ms")),
        "map_asof_ms": _clock(_map(btc_map).get("cutoff_at_ms")),
        "computed_at_ms": computed,
        "age_ms": age,
        "stale": bool(prof) and (age is None or age > STALE_MS),
    }


SERIES_KEYS = ("pin", "gamma_put", "gamma_call", "oi_put", "oi_call", "max_pain_this", "flip")


def series_subset(levels: Any) -> dict[str, Any] | None:
    """The few levels the per-sample process series carries."""
    if not isinstance(levels, Mapping) or levels.get("schema") != SCHEMA:
        return None
    return {key: levels.get(key) for key in SERIES_KEYS}
