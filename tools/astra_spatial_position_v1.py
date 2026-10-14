"""Spatial position: where one side's short leg sits in the terrain toward its liability.

A pure function of a frozen candidate snapshot (its own gamma walls, flip point and KPF
zones), so any recorded snapshot can be re-read later against its settlement. It does
not predict direction and makes no score: along this side's adverse direction it lists
what price meets between spot and the short leg, in order, and names the first thing
met. Families are kept apart:

- Gamma (dealer hedging; minutes to expiry, matching a ~24h policy) decides the state.
  The first structure met on the way to the short leg decides it: a positive-gamma wall
  or crossing the flip into positive gamma is a buffer (relatively favourable for this
  policy); a negative-gamma wall or crossing into negative gamma accelerates (relatively
  unfavourable). With nothing on the way, the side of the flip the short leg sits on
  decides. Stale or missing data is "unknown".
- KPF (traded-volume zones; days to weeks) is described only and never changes the state.
- On-chain cost bases are not used: weeks-to-months levels do not match the horizon.

Nothing here is validated against settlements yet; every result says so.
Design: docs/astra/SPATIAL_POSITION_DESIGN_20261013.md
"""
from __future__ import annotations

import math
from typing import Any, Mapping

SCHEMA = "astra_spatial_position@1.0.0"
AT_SHORT_TOLERANCE = 0.001  # a structure within 0.1% of the short strike counts as at the short leg
STATE_CN = {"favorable": "相对有利", "unfavorable": "相对不利", "unknown": "不明"}
KIND_CN = {"buffer": "缓冲", "accelerate": "加速"}


def _m(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _usd(value: float) -> str:
    return f"{value:,.0f}"


def _walls(gex_info: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Near-term per-strike walls with their gamma sign; one per strike (largest |GEX| wins)."""
    labels = {"weekly": "周度", "zero_dte": "0DTE"}
    by_strike: dict[float, dict[str, Any]] = {}
    for bucket, label in labels.items():
        for key, wall in (_m(_m(_m(gex_info.get("near_term_walls")).get(bucket)).get("walls"))).items():
            strike, gex = _num(_m(wall).get("strike")), _num(_m(wall).get("gex_usd"))
            if strike is None or gex is None or gex == 0:
                continue
            prior = by_strike.get(strike)
            if prior is None or abs(gex) > abs(prior["gex_usd"]):
                by_strike[strike] = {"price": strike, "gex_usd": gex, "bucket": label, "key": key}
    return sorted(by_strike.values(), key=lambda w: w["price"])


def _kpf_zones(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    pool = _m(_m(snapshot.get("data_identity")).get("btc_map")).get("reference_pool") or []
    zones = []
    for row in pool:
        row = _m(row)
        lo, hi = _num(row.get("raw_low")), _num(row.get("raw_high"))
        if row.get("role") == "KPF_BASIN" and lo is not None and hi is not None and hi >= lo:
            zones.append({"low": lo, "high": hi, "observation_end_ms": row.get("observation_end_ms")})
    return sorted(zones, key=lambda z: z["low"])


def assess(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    """Spatial position of one candidate's short leg; never raises on missing fields."""
    snapshot = _m(snapshot)
    contract = _m(snapshot.get("contract"))
    side = str(contract.get("side") or "").lower()
    spot = _num(_m(snapshot.get("market")).get("reference_price_usd"))
    short_k, long_k = _num(contract.get("short_strike_usd")), _num(contract.get("long_strike_usd"))
    space = _m(_m(_m(snapshot.get("fact_context")).get("market_facts")).get("space_context"))
    gex_info = _m(space.get("gex_info"))
    anchor = _m(space.get("anchor"))
    base = {"schema": SCHEMA, "side": side, "validated": False,
            "validation_cn": "未经交割数据验证，只描述地形，不参与任何门槛", "llm_call_count": 0}
    if side not in {"put", "call"} or spot is None or short_k is None:
        return {**base, "state": "unknown", "state_cn": STATE_CN["unknown"],
                "reason_cn": "合同或现价缺失，无法定位短腿。", "gamma": None, "kpf": None}

    adverse = -1.0 if side == "put" else 1.0
    toward = lambda price: adverse * (price - spot)  # > 0: on the way from spot toward the liability
    reach = toward(short_k)
    tolerance = short_k * AT_SHORT_TOLERANCE
    in_corridor = lambda price: 0 < toward(price) <= reach + tolerance
    direction_cn = "下跌" if side == "put" else "上涨"

    # expected move to expiry, as a scale for the reader (not used in the state)
    expiry, valuation = _num(contract.get("expiry_ms")), _num(_m(snapshot.get("market")).get("valuation_ts_ms"))
    iv = _num(gex_info.get("atm_iv"))
    hours = (expiry - valuation) / 3.6e6 if expiry and valuation else None
    move_pct = iv * math.sqrt(hours / 8760) if iv and hours and hours > 0 else None
    distance_pct = reach / spot * 100

    # ── Gamma family ──────────────────────────────────
    flip = _num(anchor.get("effective_flip_point"))
    if flip is None:
        flip = _num(gex_info.get("flip_point"))
    freshness = anchor.get("gex_freshness") or anchor.get("freshness")
    fresh = freshness == "FRESH" and gex_info.get("stale") is not True
    events: list[dict[str, Any]] = []
    for wall in _walls(gex_info):
        if in_corridor(wall["price"]):
            kind = "buffer" if wall["gex_usd"] > 0 else "accelerate"
            sign = "正" if wall["gex_usd"] > 0 else "负"
            at_short = abs(wall["price"] - short_k) <= tolerance
            events.append({"price": wall["price"], "kind": kind, "source": "wall",
                           "label_cn": f"{wall['bucket']} {sign} Gamma 墙 {_usd(wall['price'])}"
                                       + ("（在短腿处）" if at_short else ""),
                           "gex_usd": wall["gex_usd"]})
    if flip is not None and in_corridor(flip):
        # Above the flip dealers are long gamma, below it short gamma.
        beyond_positive = adverse > 0  # moving up past the flip enters positive gamma
        events.append({"price": flip, "kind": "buffer" if beyond_positive else "accelerate", "source": "flip",
                       "label_cn": f"Flip {_usd(flip)}，之后进入{'正' if beyond_positive else '负'} Gamma 区"})
    events.sort(key=lambda e: toward(e["price"]))
    regime_at_short = None if flip is None else ("positive" if short_k > flip else "negative")
    regime_cn = {"positive": "正 Gamma 区（对冲逆势，倾向钉住）", "negative": "负 Gamma 区（对冲顺势，倾向放大）"}

    if reach <= 0:
        state, reason = "unfavorable", f"现价已越过短腿 {_usd(short_k)}，处在责任区内。"
    elif not fresh:
        state, reason = "unknown", "Gamma 数据不新鲜，不判断。"
    elif events:
        first = events[0]
        state = "favorable" if first["kind"] == "buffer" else "unfavorable"
        label = str(first["label_cn"])
        # a space between Chinese and a leading Latin word or number ("先遇到 Flip 83,458")
        reason = (f"从现价{direction_cn}到短腿 {_usd(short_k)}，先遇到{' ' if label[:1].isascii() else ''}{label}"
                  f"（{KIND_CN[first['kind']]}）。")
    elif regime_at_short is not None:
        state = "favorable" if regime_at_short == "positive" else "unfavorable"
        reason = (f"现价到短腿 {_usd(short_k)} 之间没有 Gamma 结构；短腿位于 Flip {_usd(flip)} "
                  f"{'上方' if regime_at_short == 'positive' else '下方'}的{regime_cn[regime_at_short]}。")
    else:
        state, reason = "unknown", "没有 Flip 点，通往短腿的路上也没有 Gamma 结构。"

    gamma = {
        "fresh": fresh, "freshness": freshness, "flip_usd": flip,
        "regime_at_short": regime_at_short,
        "regime_at_short_cn": regime_cn.get(regime_at_short) if regime_at_short else None,
        "events": events,
        "observed_ms": _num(anchor.get("gex_source_ts_ms")) or _num(gex_info.get("fetched_at_ms")),
    }

    # ── KPF family: described only ─────────────────────
    zones = _kpf_zones(snapshot)
    at_zone = next((z for z in zones if z["low"] <= short_k <= z["high"]), None)
    # zones overlapping the way from spot to the short leg, in the order price meets them
    on_way = sorted([z for z in zones if min(toward(z["low"]), toward(z["high"])) < reach
                     and max(toward(z["low"]), toward(z["high"])) > 0],
                    key=lambda z: min(toward(z["low"]), toward(z["high"])))
    spot_in = next((z for z in zones if z["low"] <= spot <= z["high"]), None)
    if not zones:
        kpf_cn, kpf_tag = "没有 KPF 成交区数据。", None
    else:
        zone_cn = lambda z: f"{_usd(z['low'])}–{_usd(z['high'])}"
        before = [z for z in on_way if z is not at_zone]
        parts = ["通往短腿先经过成交密集区 " + "、".join(zone_cn(z) for z in before)] if before else []
        if at_zone is not None and spot_in is at_zone:
            parts.append(f"现价与短腿在同一个成交密集区 {zone_cn(at_zone)} 内")
        elif at_zone is not None:
            entry = at_zone["high"] if side == "put" else at_zone["low"]
            parts.append(f"在 {_usd(entry)} 进入成交密集区 {zone_cn(at_zone)}，短腿就在其中")
        elif not before:
            parts.append("通往短腿的路上没有成交密集区（成交稀疏），短腿也不在密集区内")
        else:
            parts.append("短腿不在成交密集区内")
        kpf_cn = "；".join(parts) + "。"
        kpf_tag = "dense_on_way" if on_way else "sparse_on_way"
    kpf = {"zones_on_way": on_way, "short_in_zone": at_zone, "tag": kpf_tag, "description_cn": kpf_cn,
           "observation_end_ms": max((z["observation_end_ms"] for z in zones
                                      if isinstance(z.get("observation_end_ms"), int)), default=None),
           "affects_state": False}

    return {**base, "state": state, "state_cn": STATE_CN[state], "reason_cn": reason,
            "short_strike_usd": short_k, "spot_usd": spot, "distance_pct": distance_pct,
            "expected_move_pct": move_pct,
            "distance_in_moves": (distance_pct / move_pct) if move_pct else None,
            "gamma": gamma, "kpf": kpf,
            "excluded_cn": "链上成本（持有者成本线）不进入：周到月尺度，与约 24 小时保单不匹配。"}
