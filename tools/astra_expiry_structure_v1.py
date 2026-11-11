"""This-expiry structure and per-strike trade flow for the research archive (docs/opus/32 step 1, M3 + M4).

Two records, both direct observations, kept until each expiry settles:

- structure snapshot: every expiry within the next 56 hours, each strike's open interest, mark IV,
  24h volume and mark price (one public book-summary request covers all expiries), plus the
  workbench's own profile() of that expiry so walls and pin use the same definition as the page.
  Every archive tick in the last 6 hours before an expiry, every 30 minutes before that.
- strike flow: the trades published since the previous tick, summed per expiry, strike and
  call/put: aggressor buys and sells ("direction" is the taker's side), block trades and combo
  legs kept apart. One row per expiry per tick, empty rows included, so a missing row means the
  archive did not look, never that nothing traded.

Facts only: who is the market maker is not known, so the fields say aggressor buy / sell and
nothing more. Missing values stay None; nothing is estimated.

Deribit public/get_last_trades_by_currency_and_time, checked with live requests on 2026-11-11:
result {"trades", "has_more"}; count defaults to 10, at most 1000; no continuation token, the next
page starts at the last trade's timestamp (inclusive, so trades are de-duplicated by trade_id);
history reaches back about 24 hours only. A longer outage is a gap in the record, not refetched.

Pure functions except that fetch_trades calls the page function it is given.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

try:
    from . import astra_expiry_gamma_v1 as expiry_gamma
    from . import astra_research_scorecards_v1 as research_scorecards
except ImportError:  # script-style import
    import astra_expiry_gamma_v1 as expiry_gamma
    import astra_research_scorecards_v1 as research_scorecards

SNAPSHOT_SCHEMA = "structure_snapshot@0.1"
FLOW_SCHEMA = "strike_flow@0.1"
HOUR_MS = 3_600_000
# expiries recorded: now < expiry <= now + 56h. Round 1 sits about 30 h before expiry; its 24 h
# windows need data from 54 h out, plus the 40 min snapshot tolerance (docs/opus/33 §7.10).
# Daily expiries are listed 96 h ahead (measured 2026-11-11), so the data exists.
WINDOW_MS = 56 * HOUR_MS
LATE_MS = 6 * HOUR_MS               # inside this, a snapshot every tick
EARLY_EVERY_MS = 30 * 60_000        # before it, one every 30 minutes
JITTER_MS = 60_000                  # timer slack: a tick 29.5 minutes after the last still counts
PAGE_COUNT = 1000                   # the endpoint's maximum
MAX_PAGES = 10
HISTORY_MS = 24 * HOUR_MS           # how far back the trades endpoint reaches (observed)
OVERLAP_MS = 5 * 60_000             # each fetch re-reads this much; trade ids de-duplicate it
GAP_MS = research_scorecards.ENTRY_LOOKBACK_MS   # 40 min: the same sampling tolerance as the scorecards
EDGE_MS = 15 * 60_000               # a flow sum may start or end this far inside its window (one tick)
MAX_FETCH_S = 20.0                  # one tick's trade fetch stops here (truncated); the next tick continues
REQUEST_TIMEOUT_S = 8               # per request, for the two collection endpoints


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _r(value: float) -> float:
    return round(value, 4)


# ---------------------------------------------------------------- structure snapshot

def strike_table(rows: Sequence[Mapping[str, Any]], *, expiry_ms: int) -> list[dict[str, Any]]:
    """Each strike of one expiry: call/put open interest (BTC), mark IV (%), 24h volume (BTC) and
    mark price (BTC). Every row with a parseable name is kept; a missing number stays None (profile()
    instead drops rows without IV, so its totals can be smaller than this table's)."""
    table: dict[float, dict[str, Any]] = {}
    for row in rows:
        meta = expiry_gamma.parse_name(row.get("instrument_name"))
        if not meta or meta["expiry_ms"] != expiry_ms:
            continue
        slot = table.setdefault(meta["strike"], {"k": meta["strike"], "c_oi": None, "p_oi": None, "c_iv": None,
                                                 "p_iv": None, "c_vol24h": None, "p_vol24h": None,
                                                 "c_mark": None, "p_mark": None})
        p = "c_" if meta["kind"] == "call" else "p_"
        slot[p + "oi"] = _num(row.get("open_interest"))
        slot[p + "iv"] = _num(row.get("mark_iv"))
        slot[p + "vol24h"] = _num(row.get("volume"))
        slot[p + "mark"] = _num(row.get("mark_price"))
    return [table[k] for k in sorted(table)]


def expiries_in_window(names: Iterable[Any], now_ms: int, *, after_ms: int | None = None) -> list[int]:
    """Expiries (ms) among instrument names with after < expiry <= now + 56h, ascending; `after`
    defaults to now. The flow passes its cursor, so an expiry that settled since the last tick still
    gets its last minutes."""
    low = now_ms if after_ms is None else after_ms
    out = set()
    for name in names:
        meta = expiry_gamma.parse_name(name)
        if meta and low < meta["expiry_ms"] <= now_ms + WINDOW_MS:
            out.add(meta["expiry_ms"])
    return sorted(out)


def snapshot_due(last_at_ms: int | None, expiry_ms: int, now_ms: int) -> bool:
    if expiry_ms - now_ms <= LATE_MS or last_at_ms is None:
        return True
    return now_ms - last_at_ms >= EARLY_EVERY_MS - JITTER_MS


def snapshot(rows: Sequence[Mapping[str, Any]], *, expiry_ms: int, now_ms: int) -> dict[str, Any] | None:
    """One structure_snapshot@0.1 line for one expiry; None when the expiry has no rows."""
    strikes = strike_table(rows, expiry_ms=expiry_ms)
    if not strikes:
        return None
    prof = expiry_gamma.profile(rows, expiry_ms=expiry_ms, now_ms=now_ms) or {}
    return {"schema": SNAPSHOT_SCHEMA, "at_ms": now_ms, "expiry_ms": expiry_ms,
            "expiry": expiry_gamma.expiry_code(expiry_ms),
            "forward_usd": prof.get("forward_usd"), "atm_iv_pct": prof.get("atm_iv_pct"),
            "expected_move_usd": prof.get("expected_move_usd"),
            "hours_to_expiry": round((expiry_ms - now_ms) / HOUR_MS, 2),
            "strikes": strikes,
            "profile": {k: prof.get(k) for k in ("walls", "top", "totals")} if prof else None}


# ---------------------------------------------------------------- trades

def flow_window(cursor_ms: int | None, now_ms: int) -> tuple[int, int | None]:
    """(fetch from, unrecorded-gap start). A cursor older than the endpoint's history leaves a gap
    from the cursor to the oldest reachable time; it is reported, not filled."""
    reach = now_ms - HISTORY_MS + OVERLAP_MS
    start = cursor_ms - OVERLAP_MS
    return (reach, cursor_ms) if start < reach else (start, None)


_REQUIRED = ("trade_id", "timestamp", "instrument_name", "direction", "amount")


def _valid_trade(t: Any) -> bool:
    return (isinstance(t, Mapping) and all(t.get(k) is not None for k in _REQUIRED)
            and isinstance(t["timestamp"], int) and t["direction"] in ("buy", "sell")
            and _num(t["amount"]) is not None and expiry_gamma.parse_name(t["instrument_name"]) is not None)


def fetch_trades(page: Callable[[int, int, int], Any], from_ms: int, to_ms: int,
                 seen_ids: Iterable[str] = ()) -> dict[str, Any]:
    """All trades in [from_ms, to_ms], oldest first, through `page(start_ms, end_ms, count)` (the
    endpoint's result). De-duplicated by trade_id against `seen_ids` and across pages. At most
    MAX_PAGES pages or MAX_FETCH_S seconds; if more remain, `truncated` and `covered_to_ms` is the
    last trade read. `skipped` counts malformed trades as read (the overlap re-reads them).
    A malformed page raises (the caller keeps its cursor); a malformed trade is skipped and counted.
    `tail_ids`: every id read at or after covered_to_ms - OVERLAP_MS, which the next fetch reads again."""
    seen = {str(i) for i in seen_ids}
    trades: list[dict[str, Any]] = []
    read: list[tuple[str, int]] = []
    skipped, pages, start, truncated, covered = 0, 0, from_ms, False, to_ms
    began = time.monotonic()
    while True:
        result = page(start, to_ms, PAGE_COUNT)
        batch = result.get("trades") if isinstance(result, Mapping) else None
        if not isinstance(batch, list) or not isinstance(result.get("has_more"), bool):
            raise ValueError("trades page malformed")
        pages += 1
        last_ts = None
        for t in batch:
            if not _valid_trade(t):
                skipped += 1
                continue
            last_ts = t["timestamp"]
            tid = str(t["trade_id"])
            read.append((tid, last_ts))
            if tid in seen:
                continue
            seen.add(tid)
            trades.append(dict(t))
        if not result["has_more"]:
            break
        if last_ts is None or last_ts <= start and len(batch) >= PAGE_COUNT:
            truncated, covered = True, (last_ts if last_ts is not None else start)   # cannot move forward
            break
        if pages >= MAX_PAGES or time.monotonic() - began >= MAX_FETCH_S:
            truncated, covered = True, last_ts
            break
        start = last_ts
    trades.sort(key=lambda t: (t["timestamp"], str(t["trade_id"])))
    return {"trades": trades, "truncated": truncated, "covered_to_ms": covered, "pages": pages,
            "skipped": skipped, "tail_ids": sorted({i for i, ts in read if ts >= covered - OVERLAP_MS})}


def trade_class(t: Mapping[str, Any]) -> str:
    """block: negotiated off-book and reported; combo: one leg of a multi-leg order (its direction
    belongs to the whole combo); plain: a single-leg order-book trade."""
    if t.get("block_trade_id"):
        return "block"
    if t.get("combo_id") or t.get("combo_trade_id"):
        return "combo"
    return "plain"


def aggregate(trades: Iterable[Mapping[str, Any]], expiries: Iterable[int]) -> dict[int, list[dict[str, Any]]]:
    """Per expiry (only those given), per strike and call/put: aggressor buy / sell BTC of plain
    trades and their count, block and combo legs apart, and the largest single trade."""
    wanted = set(expiries)
    cells: dict[int, dict[tuple[float, str], dict[str, float]]] = {e: {} for e in wanted}
    for t in trades:
        meta = expiry_gamma.parse_name(t.get("instrument_name"))
        if not meta or meta["expiry_ms"] not in wanted:
            continue
        cell = cells[meta["expiry_ms"]].setdefault((meta["strike"], meta["kind"]), {
            "buy_btc": 0.0, "sell_btc": 0.0, "n": 0, "block_buy_btc": 0.0, "block_sell_btc": 0.0, "n_block": 0,
            "combo_buy_btc": 0.0, "combo_sell_btc": 0.0, "n_combo": 0, "max_trade_btc": 0.0})
        amount, cls = float(t["amount"]), trade_class(t)
        prefix = "" if cls == "plain" else cls + "_"
        cell[prefix + t["direction"] + "_btc"] += amount
        cell["n" if cls == "plain" else "n_" + cls] += 1
        cell["max_trade_btc"] = max(cell["max_trade_btc"], amount)
    return {e: [{"k": k, "kind": kind, **{f: (_r(v) if isinstance(v, float) else v) for f, v in c.items()}}
                for (k, kind), c in sorted(cells[e].items())] for e in sorted(cells)}


def flow_row(expiry_ms: int, rows: list[dict[str, Any]], *, from_ms: int, to_ms: int, truncated: bool,
             gap_from_ms: int | None, skipped: int) -> dict[str, Any]:
    return {"schema": FLOW_SCHEMA, "from_ms": from_ms, "to_ms": to_ms, "expiry_ms": expiry_ms,
            "expiry": expiry_gamma.expiry_code(expiry_ms), "truncated": truncated,
            "gap_from_ms": gap_from_ms, "skipped_trades": skipped, "rows": rows}


# ---------------------------------------------------------------- readings from the records

def _at_or_before(snapshots: Sequence[Mapping[str, Any]], at_ms: int) -> Mapping[str, Any] | None:
    best = None
    for s in snapshots:
        t = s.get("at_ms")
        if isinstance(t, int) and t <= at_ms and (best is None or t > best["at_ms"]):
            best = s
    return best if best is not None and at_ms - best["at_ms"] <= GAP_MS else None


def _oi_at(snap: Mapping[str, Any], k: float) -> tuple[float | None, float | None]:
    for s in snap.get("strikes") or []:
        if s.get("k") == k:
            return _num(s.get("c_oi")), _num(s.get("p_oi"))
    return None, None


def oi_change(snapshots: Sequence[Mapping[str, Any]], k: float, at_ms: int, back_ms: int) -> dict[str, Any]:
    """Call / put open interest change at strike k over (at_ms - back_ms, at_ms], from the latest
    snapshot at or before each end. An end without a snapshot within 40 minutes is a gap: None."""
    now, then = _at_or_before(snapshots, at_ms), _at_or_before(snapshots, at_ms - back_ms)
    if now is None or then is None:
        return {"c": None, "p": None, "gap": True}
    (c1, p1), (c0, p0) = _oi_at(now, k), _oi_at(then, k)
    diff = lambda a, b: _r(a - b) if a is not None and b is not None else None  # noqa: E731
    return {"c": diff(c1, c0), "p": diff(p1, p0), "gap": False}


def flow_sum(flow_rows: Sequence[Mapping[str, Any]], k: float, from_ms: int, to_ms: int) -> dict[str, Any]:
    """Aggressor buy / sell (and block, combo) BTC at strike k from the flow rows whose window ends in
    (from_ms, to_ms]. `gap` when those rows do not match the window: a missing tick, an outage beyond
    the endpoint's history, a row starting well before the window (its older trades would be counted)
    or rows that do not join end to start (overlap counts twice); `truncated` when any of them was."""
    rows = sorted((r for r in flow_rows if isinstance(r.get("to_ms"), int) and from_ms < r["to_ms"] <= to_ms),
                  key=lambda r: r["from_ms"])
    out = {f"{c}_{x}": 0.0 for c in ("c", "p") for x in ("buy", "sell", "block_buy", "block_sell",
                                                         "combo_buy", "combo_sell")}
    out.update(n_c=0, n_p=0)
    gap = (not rows or abs(rows[0]["from_ms"] - from_ms) > EDGE_MS or rows[-1]["to_ms"] < to_ms - EDGE_MS
           or any(b["from_ms"] != a["to_ms"] for a, b in zip(rows, rows[1:]))
           or any(r.get("gap_from_ms") is not None for r in rows))
    for r in rows:
        for cell in r.get("rows") or []:
            if cell.get("k") != k:
                continue
            c = "c" if cell.get("kind") == "call" else "p"
            for x in ("buy", "sell", "block_buy", "block_sell", "combo_buy", "combo_sell"):
                out[f"{c}_{x}"] += float(cell.get(x + "_btc") or 0.0)
            out["n_" + c] += int(cell.get("n") or 0)
    out = {f: (_r(v) if isinstance(v, float) else v) for f, v in out.items()}
    out["truncated"] = any(bool(r.get("truncated")) for r in rows)
    out["gap"] = bool(gap)
    return out
