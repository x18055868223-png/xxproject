"""Intraday exit study, step 2 of docs/opus/12: closing a short daily vertical at a ledger
instant versus holding it to expiry, and how the daily options' IV moves through the day.

A derived file (scorecards/intraday_exit.json), rebuilt with the scorecards; read-only on
the archive; no trading rule is made here (intraday_exit_study@0.1).

- Decision instants: ledger rows of settled contracts, thinned to the first row per
  contract per 30-minute slot (the ledger writes more often near expiry and on changes,
  which would otherwise overweight those moments).
- Closing at t = buy the short leg back at its ask and sell the long leg at its bid, plus
  the taker fee per leg (min(0.0003, 12.5% x price)); a long leg with no bid is sold for 0.
  Holding = paying the settlement payout. close_advantage = payout - close cost (> 0:
  closing would have cost less than holding). A row whose short ask is missing or zero has
  no close quote and is counted, not scored. The mark-based cost (no fees) is the cost of a
  patient exit.
- Groups: UTC hour, hours to expiry, the variance-clock factor of the time left (doc 12
  hourly ratios rescaled to the all-days mean: > 1 means the time left is busier than an
  average hour), distance in expected moves. For 08:00 dailies the first three are nearly
  the same axis and are not independent evidence.
- IV clock: within each contract, the change of log short-leg mark IV between consecutive
  rows at most 40 minutes apart, per hour elapsed, attributed to the UTC hour where the
  interval starts and averaged. Fixed-strike IV also moves with spot, so this is a drift
  description, not a pure clock.
Rows of one contract share a settlement and contracts of one day share the market, so
every group reports rows, contracts and days, with medians and contract-weighted means;
with few days everything is descriptive only.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

STUDY = "intraday_exit_study@0.1"
SCHEMA = "intraday_exit_study_card@0.1"
TAKER_FEE_BTC = 0.0003
TAKER_FEE_PRICE_CAP = 0.125
MIN_DAYS_FOR_READING = 20
SLOT_MS = 30 * 60_000
IV_PAIR_MAX_GAP_MS = 40 * 60_000
RECENT_SAMPLE_DAYS = 3
HOUR_MS = 3_600_000
# doc 12 (Binance BTCUSDT 1m, 2024-10..2026-09): hourly variance ratio to the weekday mean and to
# the weekend mean; weekday variance is 1.193x and weekend 0.515x the all-days mean
CLOCK_WEEKDAY = (1.081, 1.087, 0.852, 0.643, 0.544, 0.516, 0.556, 0.612, 0.637, 0.585, 0.519, 0.581,
                 0.871, 1.521, 2.357, 2.093, 1.423, 1.376, 1.213, 1.065, 1.153, 1.162, 0.972, 0.582)
CLOCK_WEEKEND = (0.871, 0.889, 0.828, 0.634, 0.705, 0.718, 0.581, 0.636, 0.672, 0.627, 0.56, 0.613,
                 0.758, 0.917, 1.233, 1.206, 1.355, 1.152, 1.426, 1.064, 0.97, 1.266, 2.335, 1.983)
WEEKDAY_SCALE, WEEKEND_SCALE = 1.193, 0.515
BUCKETS = {
    "hours_to_expiry": [(0, 3, "0-3h"), (3, 6, "3-6h"), (6, 12, "6-12h"), (12, 18, "12-18h"), (18, 24, "18-24h"),
                        (24, 1e9, "24h+")],
    "clock_factor": [(0, 0.8, "<0.8"), (0.8, 1.0, "0.8-1.0"), (1.0, 1.2, "1.0-1.2"), (1.2, 1e9, ">1.2")],
    "distance_in_moves": [(-1e9, 0, "<0 (ITM)"), (0, 0.25, "0-0.25"), (0.25, 0.5, "0.25-0.5"), (0.5, 1.0, "0.5-1"),
                          (1.0, 1e9, ">1")],
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _m(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _utc(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)


def _day(ms: int) -> str:
    return _utc(ms).strftime("%Y-%m-%d")


def _lines(path: Path) -> list[dict[str, Any]]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line) if line.strip() else None
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def clock_factor(start_ms: int, end_ms: int) -> float | None:
    """Average variance ratio to the all-days mean over [start, end), minute-weighted."""
    if end_ms <= start_ms:
        return None
    total, weight, t = 0.0, 0.0, start_ms
    while t < end_ms:
        moment = _utc(t)
        step = min((t // HOUR_MS + 1) * HOUR_MS, end_ms) - t
        weekend = moment.weekday() >= 5
        ratio = (CLOCK_WEEKEND[moment.hour] * WEEKEND_SCALE) if weekend else (CLOCK_WEEKDAY[moment.hour] * WEEKDAY_SCALE)
        total += ratio * step
        weight += step
        t += step
    return total / weight


def _fee(price: float) -> float:
    return min(TAKER_FEE_BTC, TAKER_FEE_PRICE_CAP * price)


def close_costs(row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    """(taker close cost, mark close cost) in BTC for 1 BTC: buy the short back, sell the long.
    None when the short has no positive ask or the long leg is absent."""
    short, long = row.get("short"), row.get("long")
    if not isinstance(short, Mapping) or not isinstance(long, Mapping):
        return None, None
    ask, bid = _num(short.get("best_ask_price")), _num(long.get("best_bid_price"))
    taker = None
    if ask is not None and ask > 0:
        sold = bid if bid is not None and bid > 0 else 0.0     # no bid: the long is sold for nothing
        taker = ask - sold + _fee(ask) + (_fee(sold) if sold > 0 else 0.0)
    sm, lm = _num(short.get("mark_price")), _num(long.get("mark_price"))
    return taker, (sm - lm if sm is not None and lm is not None else None)


def _bucket(name: str, value: float | None) -> str | None:
    if value is None:
        return None
    return next((label for lo, hi, label in BUCKETS[name] if lo <= value < hi), None)


def thin(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """First row per contract per 30-minute slot."""
    seen, out = set(), []
    for row in sorted(rows, key=lambda r: (r["contract_key"], int(r["at_ms"]))):
        slot = (row["contract_key"], int(row["at_ms"]) // SLOT_MS)
        if slot not in seen:
            seen.add(slot)
            out.append(row)
    return out


def samples(ledger: Iterable[Mapping[str, Any]], outcomes: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in thin(ledger):
        outcome = outcomes.get(row["contract_key"])
        expiry = (outcome or {}).get("expiry_ms")
        payout = _num((outcome or {}).get("payout_btc"))
        if not isinstance(expiry, int) or payout is None or int(row["at_ms"]) >= expiry:
            continue
        at = int(row["at_ms"])
        taker, mark = close_costs(row)
        out.append({
            "contract_key": row["contract_key"], "expiry_day": _day(expiry), "at_ms": at,
            "hour_utc": _utc(at).hour, "hours_to_expiry": (expiry - at) / HOUR_MS, "side": row.get("side"),
            "displayed": row.get("displayed"), "close_quote": taker is not None,
            "close_cost_btc": taker, "close_cost_mark_btc": mark, "payout_btc": payout,
            "close_advantage_btc": payout - taker if taker is not None else None,
            "close_advantage_mark_btc": payout - mark if mark is not None else None,
            "clock_factor": clock_factor(at, expiry), "distance_in_moves": _num(row.get("distance_in_moves")),
            "short_mark_iv": _num(_m(row.get("short")).get("mark_iv")),
        })
    return out


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _summary(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r["close_advantage_btc"] is not None]
    adv = [r["close_advantage_btc"] for r in scored]
    per_contract: dict[str, list[float]] = {}
    for r in scored:
        per_contract.setdefault(r["contract_key"], []).append(r["close_advantage_btc"])
    adv_mark = [r["close_advantage_mark_btc"] for r in rows if r["close_advantage_mark_btc"] is not None]
    return {"rows": len(rows), "rows_without_close_quote": len(rows) - len(scored),
            "contracts": len({r["contract_key"] for r in rows}), "days": len({r["expiry_day"] for r in rows}),
            "mean_close_advantage_btc": sum(adv) / len(adv) if adv else None,
            "median_close_advantage_btc": _median(adv),
            "contract_weighted_mean_close_advantage_btc":
                sum(sum(v) / len(v) for v in per_contract.values()) / len(per_contract) if per_contract else None,
            "share_close_better": sum(1 for a in adv if a > 0) / len(adv) if adv else None,
            "max_close_advantage_btc": max(adv) if adv else None,     # holding cost this much more than closing
            "min_close_advantage_btc": min(adv) if adv else None,     # closing cost this much more than holding
            "mean_close_advantage_mark_btc": sum(adv_mark) / len(adv_mark) if adv_mark else None}


def _group(rows: list[Mapping[str, Any]], key) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        label = key(r)
        if label is not None:
            groups.setdefault(str(label), []).append(r)
    return {label: _summary(members) for label, members in sorted(groups.items())}


def iv_clock(ledger: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Mean change of log short-leg mark IV per hour elapsed, by the UTC hour it happened in."""
    by_contract: dict[str, list[tuple[int, float]]] = {}
    for row in ledger:
        iv = _num(_m(row.get("short")).get("mark_iv"))
        if iv and iv > 0:
            by_contract.setdefault(row["contract_key"], []).append((int(row["at_ms"]), iv))
    acc: dict[str, list[float]] = {}
    for points in by_contract.values():
        points.sort()
        for (t0, a), (t1, b) in zip(points, points[1:]):
            if 0 < t1 - t0 <= IV_PAIR_MAX_GAP_MS:
                slot = acc.setdefault(f"{_utc(t0).hour:02d}", [0.0, 0.0, 0])
                slot[0] += math.log(b / a)
                slot[1] += (t1 - t0) / HOUR_MS
                slot[2] += 1
    return {"by_hour_utc": {h: {"log_iv_change_per_hour": s / w if w else None, "pairs": n}
                            for h, (s, w, n) in sorted(acc.items())},
            "contracts": len(by_contract)}


def build(root: str | Path, *, now_ms: int) -> dict[str, Any]:
    root = Path(root)
    outcomes: dict[str, Mapping[str, Any]] = {}
    for path in sorted((root / "outcomes").glob("*.jsonl")):
        for o in _lines(path):
            if o.get("status") == "settled" and isinstance(o.get("expiry_ms"), int) and o.get("contract_key"):
                outcomes[o["contract_key"]] = o
    # only the ledger days that can hold rows of settled contracts (expiry day and the two before)
    days = set()
    for o in outcomes.values():
        for back in (0, 1, 2):
            days.add(_day(o["expiry_ms"] - back * 24 * HOUR_MS))
    ledger = [r for d in sorted(days) for r in _lines(root / "ledger" / f"{d}.jsonl")
              if isinstance(r.get("at_ms"), int) and r.get("contract_key") in outcomes]
    rows = samples(ledger, outcomes)
    overall = _summary(rows)
    recent_days = sorted({r["expiry_day"] for r in rows})[-RECENT_SAMPLE_DAYS:]
    return {"schema": SCHEMA, "study": STUDY, "built_at_ms": now_ms, "derived": True,
            "note_cn": "每个账本时点（每合约每 30 分钟取第一行）：此刻按对手价平仓（含费）对比持有到期赔付；"
                       "close_advantage > 0 表示平掉更省，相等算持有更好。同一合约各行共享一次交割，同一天的合约共享行情；"
                       "days 少时只作描述，并看中位数与按合约加权的均值。clock_factor 为剩余时段相对全天平均的方差倍数"
                       "（12 号文档，已把工作日与周末换算到同一刻度）；对 08:00 到期的合约，小时、距到期与 clock_factor"
                       "几乎是同一条轴，不是独立证据。mark 口径不含费用。",
            "enough_days": overall["days"] >= MIN_DAYS_FOR_READING, "overall": overall,
            "by": {"hour_utc": _group(rows, lambda r: f"{r['hour_utc']:02d}"),
                   "hours_to_expiry": _group(rows, lambda r: _bucket("hours_to_expiry", r["hours_to_expiry"])),
                   "clock_factor": _group(rows, lambda r: _bucket("clock_factor", r["clock_factor"])),
                   "distance_in_moves": _group(rows, lambda r: _bucket("distance_in_moves", r["distance_in_moves"]))},
            "iv_clock": iv_clock(ledger),
            "recent_samples": [r for r in rows if r["expiry_day"] in recent_days]}
