"""Offline light-study analysis for Astra signal samples.

The module is intentionally pure: callers provide already-frozen signal samples,
price candles, option instruments, and official delivery prices.  No network,
server, LLM, or production state is touched here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from math import ceil, floor, isfinite
from typing import Any


MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
DELIVERY_HOUR_UTC = 8
TARGET_WIDTHS = (1500.0, 2000.0, 2500.0)
CREDIT_FRACTIONS = (0.05, 0.10, 0.20)
VALID_DIRECTIONS = {"BULLISH", "BEARISH", "NEUTRAL"}
SIDE_PUT = "put_credit"
SIDE_CALL = "call_credit"


def analyze(
    samples: list[dict[str, Any]],
    klines: list[dict[str, Any]],
    instruments: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
    cutoff_ms: int,
) -> dict[str, Any]:
    """Build spread, path, exclusion, and summary rows for the light study."""
    cutoff = _as_ms(cutoff_ms)
    kline_by_open = _index_klines(klines)
    instruments_by_expiry = _index_instruments(instruments)
    delivery_by_date = _index_deliveries(deliveries)

    spread_rows: list[dict[str, Any]] = []
    path_rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    seen_cards: set[str] = set()

    for index, raw_sample in enumerate(samples):
        sample = dict(raw_sample or {})
        card_id = str(sample.get("card_id") or f"sample_{index}")
        if card_id in seen_cards:
            exclusions.append(_exclusion(sample, "duplicate_card", "card_id repeated"))
            continue
        seen_cards.add(card_id)

        direction = str(sample.get("direction") or "").upper()
        if direction not in VALID_DIRECTIONS:
            exclusions.append(_exclusion(sample, "invalid_direction", "direction is not BULLISH/BEARISH/NEUTRAL"))
            continue

        event_ms = _as_ms(sample.get("event_time_ms"))
        if event_ms is None:
            exclusions.append(_exclusion(sample, "missing_event_time", "event_time_ms missing or invalid"))
            continue

        entry_ms = _entry_ms(event_ms)
        entry_kline = kline_by_open.get(entry_ms)
        if entry_kline is None:
            exclusions.append(_exclusion(sample, "missing_entry_kline", "next-minute entry candle is missing", entry_ms=entry_ms))
            continue

        entry_price = _num(entry_kline.get("open"))
        if entry_price is None or entry_price <= 0:
            exclusions.append(_exclusion(sample, "invalid_entry_price", "entry candle open is missing or invalid", entry_ms=entry_ms))
            continue

        expiry_ms = _next_delivery_ms(entry_ms)
        dte_ms = expiry_ms - entry_ms
        if dte_ms <= 0 or dte_ms > DAY_MS:
            exclusions.append(_exclusion(sample, "invalid_expiry_window", "expiry must be after entry and within 24h", entry_ms=entry_ms, expiry_ms=expiry_ms))
            continue
        if expiry_ms > cutoff:
            exclusions.append(_exclusion(sample, "not_matured", "expiry is after cutoff", entry_ms=entry_ms, expiry_ms=expiry_ms))
            continue

        delivery_date = _utc_date(expiry_ms)
        delivery_price = delivery_by_date.get(delivery_date)
        if delivery_price is None or delivery_price <= 0:
            exclusions.append(_exclusion(sample, "missing_delivery", "official delivery price missing or invalid", entry_ms=entry_ms, expiry_ms=expiry_ms, delivery_date=delivery_date))
            continue

        eligible = _eligible_instruments(instruments_by_expiry.get(expiry_ms, []), entry_ms)
        selected_sides = _sides_for_direction(direction)
        any_side_row = False
        for side, relation in selected_sides:
            short_leg = _select_short_leg(eligible, side, entry_price)
            if short_leg is None:
                exclusions.append(_exclusion(sample, "missing_short_leg", "strictly OTM short leg is not available", side=side, entry_ms=entry_ms, expiry_ms=expiry_ms))
                continue
            side_had_width = False
            for target_width in TARGET_WIDTHS:
                long_leg = _select_long_leg(eligible, side, short_leg["strike"], target_width)
                if long_leg is None:
                    exclusions.append(_exclusion(sample, "missing_long_leg", "farther protective long leg is not available", side=side, target_width=target_width, entry_ms=entry_ms, expiry_ms=expiry_ms))
                    continue
                actual_width = _actual_width(side, short_leg["strike"], long_leg["strike"])
                if actual_width <= 0:
                    exclusions.append(_exclusion(sample, "invalid_width", "selected spread width is not positive", side=side, target_width=target_width, entry_ms=entry_ms, expiry_ms=expiry_ms))
                    continue
                side_had_width = True
                any_side_row = True
                spread_intrinsic_usd = _spread_intrinsic_usd(
                    side,
                    short_leg["strike"],
                    long_leg["strike"],
                    delivery_price,
                )
                payout_btc = spread_intrinsic_usd / delivery_price
                otm = (
                    delivery_price <= short_leg["strike"]
                    if side == SIDE_CALL
                    else delivery_price >= short_leg["strike"]
                )
                payout_category = _payout_category(spread_intrinsic_usd, actual_width)
                for credit_fraction in CREDIT_FRACTIONS:
                    net_credit_btc = credit_fraction * actual_width / entry_price
                    net_pnl_btc = net_credit_btc - payout_btc
                    spread_rows.append(_spread_row(
                        sample=sample,
                        side=side,
                        relation=relation,
                        entry_ms=entry_ms,
                        entry_price=entry_price,
                        expiry_ms=expiry_ms,
                        delivery_date=delivery_date,
                        delivery_price=delivery_price,
                        dte_ms=dte_ms,
                        short_leg=short_leg,
                        long_leg=long_leg,
                        target_width=target_width,
                        actual_width=actual_width,
                        credit_fraction=credit_fraction,
                        net_credit_btc=net_credit_btc,
                        spread_intrinsic_usd=spread_intrinsic_usd,
                        payout_btc=payout_btc,
                        net_pnl_btc=net_pnl_btc,
                        otm=otm,
                        payout_category=payout_category,
                    ))
            if not side_had_width:
                exclusions.append(_exclusion(sample, "side_has_no_complete_spread", "no target width could be formed for this side", side=side, entry_ms=entry_ms, expiry_ms=expiry_ms))
        if any_side_row:
            path_rows.append(_path_row(sample, kline_by_open, entry_ms, entry_price, expiry_ms))

    summaries = _build_summaries(spread_rows, len(seen_cards), exclusions)
    return {
        "spread_rows": spread_rows,
        "path_rows": path_rows,
        "exclusions": exclusions,
        "summaries": summaries,
    }


def _entry_ms(event_ms: int) -> int:
    return floor(event_ms / MINUTE_MS) * MINUTE_MS + MINUTE_MS


def _next_delivery_ms(entry_ms: int) -> int:
    dt = datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc)
    candidate = dt.replace(hour=DELIVERY_HOUR_UTC, minute=0, second=0, microsecond=0)
    if candidate.timestamp() * 1000 <= entry_ms:
        candidate = candidate + timedelta(days=1)
    return int(candidate.timestamp() * 1000)


def _utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _index_klines(klines: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for raw in klines or []:
        row = dict(raw or {})
        open_ms = _as_ms(row.get("open_time_ms"))
        if open_ms is None:
            continue
        normalized = {
            "open_time_ms": open_ms,
            "open": _num(row.get("open")),
            "high": _num(row.get("high")),
            "low": _num(row.get("low")),
            "close": _num(row.get("close")),
            "close_time_ms": _as_ms(row.get("close_time_ms")),
            "volume": _num(row.get("volume")),
        }
        result[open_ms] = normalized
    return result


def _index_instruments(instruments: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw in instruments or []:
        row = dict(raw or {})
        expiry_ms = _as_ms(row.get("expiration_timestamp"))
        creation_ms = _as_ms(row.get("creation_timestamp"))
        strike = _num(row.get("strike"))
        contract_size = _num(row.get("contract_size"))
        instrument_name = row.get("instrument_name")
        option_type = _option_type(row.get("option_type"))
        settlement = str(row.get("settlement_currency") or "").upper()
        if expiry_ms is None or creation_ms is None or strike is None or strike <= 0:
            continue
        if not isinstance(instrument_name, str) or not instrument_name.strip():
            continue
        if contract_size is None or abs(contract_size - 1.0) > 1e-12:
            continue
        if option_type not in {"call", "put"} or settlement != "BTC":
            continue
        result[expiry_ms].append({
            "instrument_name": instrument_name,
            "creation_timestamp": creation_ms,
            "expiration_timestamp": expiry_ms,
            "strike": strike,
            "option_type": option_type,
            "settlement_currency": settlement,
            "contract_size": contract_size,
        })
    for expiry_ms, rows in result.items():
        rows.sort(key=lambda item: (item["option_type"], item["strike"], str(item.get("instrument_name") or "")))
    return dict(result)


def _index_deliveries(deliveries: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw in deliveries or []:
        row = dict(raw or {})
        date = row.get("date")
        price = _num(row.get("delivery_price"))
        if isinstance(date, str) and price is not None and price > 0:
            result[date] = price
    return result


def _eligible_instruments(instruments: list[dict[str, Any]], entry_ms: int) -> list[dict[str, Any]]:
    return [row for row in instruments if row["creation_timestamp"] <= entry_ms]


def _sides_for_direction(direction: str) -> list[tuple[str, str]]:
    if direction == "BULLISH":
        return [(SIDE_PUT, "directional"), (SIDE_CALL, "opposite")]
    if direction == "BEARISH":
        return [(SIDE_CALL, "directional"), (SIDE_PUT, "opposite")]
    return [(SIDE_PUT, "neutral_put"), (SIDE_CALL, "neutral_call")]


def _select_short_leg(instruments: list[dict[str, Any]], side: str, entry_price: float) -> dict[str, Any] | None:
    option_type = "put" if side == SIDE_PUT else "call"
    rows = [row for row in instruments if row["option_type"] == option_type]
    if side == SIDE_PUT:
        candidates = [row for row in rows if row["strike"] < entry_price]
        return max(candidates, key=lambda item: item["strike"], default=None)
    candidates = [row for row in rows if row["strike"] > entry_price]
    return min(candidates, key=lambda item: item["strike"], default=None)


def _select_long_leg(
    instruments: list[dict[str, Any]],
    side: str,
    short_strike: float,
    target_width: float,
) -> dict[str, Any] | None:
    option_type = "put" if side == SIDE_PUT else "call"
    rows = [row for row in instruments if row["option_type"] == option_type]
    if side == SIDE_PUT:
        candidates = [row for row in rows if row["strike"] < short_strike]
        return min(
            candidates,
            key=lambda item: (abs((short_strike - item["strike"]) - target_width), short_strike - item["strike"]),
            default=None,
        )
    candidates = [row for row in rows if row["strike"] > short_strike]
    return min(
        candidates,
        key=lambda item: (abs((item["strike"] - short_strike) - target_width), item["strike"] - short_strike),
        default=None,
    )


def _actual_width(side: str, short_strike: float, long_strike: float) -> float:
    if side == SIDE_PUT:
        return short_strike - long_strike
    return long_strike - short_strike


def _spread_intrinsic_usd(
    side: str,
    short_strike: float,
    long_strike: float,
    settlement: float,
) -> float:
    if side == SIDE_CALL:
        return max(settlement - short_strike, 0.0) - max(settlement - long_strike, 0.0)
    return max(short_strike - settlement, 0.0) - max(long_strike - settlement, 0.0)


def _payout_category(spread_intrinsic_usd: float, actual_width: float) -> str:
    eps = 1e-9
    if spread_intrinsic_usd <= eps:
        return "zero"
    if spread_intrinsic_usd >= actual_width - eps:
        return "full_width"
    return "partial"


def _spread_row(**kwargs: Any) -> dict[str, Any]:
    sample = kwargs["sample"]
    row = {
        "card_id": sample.get("card_id"),
        "episode_id": sample.get("episode_id"),
        "version": sample.get("version"),
        "direction": sample.get("direction"),
        "anchor_position": sample.get("anchor_position", "unknown"),
        "gamma_regime": sample.get("gamma_regime", "unknown"),
        "flow_alignment": sample.get("flow_alignment", "neutral_or_unknown"),
        "side": kwargs["side"],
        "relation": kwargs["relation"],
        "entry_basis": "Binance BTCUSDT spot 1m next-minute open",
        "entry_ms": kwargs["entry_ms"],
        "entry_price": kwargs["entry_price"],
        "sample_card_price": sample.get("card_price"),
        "expiry_ms": kwargs["expiry_ms"],
        "delivery_date": kwargs["delivery_date"],
        "delivery_price": kwargs["delivery_price"],
        "dte_hours": kwargs["dte_ms"] / HOUR_MS,
        "dte_bucket": _dte_bucket(kwargs["dte_ms"] / HOUR_MS),
        "short_instrument": kwargs["short_leg"].get("instrument_name"),
        "long_instrument": kwargs["long_leg"].get("instrument_name"),
        "short_strike": kwargs["short_leg"]["strike"],
        "long_strike": kwargs["long_leg"]["strike"],
        "target_width": kwargs["target_width"],
        "actual_width": kwargs["actual_width"],
        "credit_fraction": kwargs["credit_fraction"],
        "net_credit_btc": kwargs["net_credit_btc"],
        "spread_intrinsic_usd": kwargs["spread_intrinsic_usd"],
        "payout_btc": kwargs["payout_btc"],
        "net_pnl_btc": kwargs["net_pnl_btc"],
        "result": "win" if kwargs["net_pnl_btc"] > 0 else ("loss" if kwargs["net_pnl_btc"] < 0 else "tie"),
        "short_otm_at_expiry": kwargs["otm"],
        "payout_category": kwargs["payout_category"],
        "quantity_basis": "1 BTC inverse option group",
        "quote_basis": "synthetic net credit fraction of USD width converted at entry price; not historical quote",
    }
    return row


def _path_row(
    sample: dict[str, Any],
    kline_by_open: dict[int, dict[str, Any]],
    entry_ms: int,
    entry_price: float,
    expiry_ms: int,
) -> dict[str, Any]:
    expected = max(0, int((expiry_ms - entry_ms) // MINUTE_MS))
    observed_rows = []
    valid_rows = []
    for open_ms in range(entry_ms, expiry_ms, MINUTE_MS):
        row = kline_by_open.get(open_ms)
        if row is None:
            continue
        observed_rows.append(row)
        if _path_kline_valid(row, open_ms, expiry_ms):
            valid_rows.append(row)
    lows = [row["low"] for row in valid_rows]
    highs = [row["high"] for row in valid_rows]
    closes = [row["close"] for row in valid_rows]
    min_low = min(lows) if lows else None
    max_high = max(highs) if highs else None
    last_close = closes[-1] if closes else None
    valid_minutes = len(valid_rows)
    invalid_minutes = len(observed_rows) - valid_minutes
    missing = expected - valid_minutes
    put_adverse = max(entry_price - min_low, 0.0) if min_low is not None else None
    call_adverse = max(max_high - entry_price, 0.0) if max_high is not None else None
    return {
        "card_id": sample.get("card_id"),
        "episode_id": sample.get("episode_id"),
        "direction": sample.get("direction"),
        "entry_ms": entry_ms,
        "entry_price": entry_price,
        "expiry_ms": expiry_ms,
        "expected_minutes": expected,
        "observed_minutes": len(observed_rows),
        "valid_minutes": valid_minutes,
        "invalid_minutes": invalid_minutes,
        "missing_minutes": missing,
        "path_complete": missing == 0,
        "min_low": min_low,
        "max_high": max_high,
        "last_close_before_expiry": last_close,
        "put_max_adverse_usd": put_adverse,
        "put_max_adverse_pct": put_adverse / entry_price if put_adverse is not None and entry_price else None,
        "call_max_adverse_usd": call_adverse,
        "call_max_adverse_pct": call_adverse / entry_price if call_adverse is not None and entry_price else None,
        "basis": "observed Binance BTCUSDT spot 1m candles from entry to expiry; touch is diagnostic only",
    }


def _path_kline_valid(row: dict[str, Any], open_ms: int, expiry_ms: int) -> bool:
    high = _num(row.get("high"))
    low = _num(row.get("low"))
    close = _num(row.get("close"))
    close_ms = _as_ms(row.get("close_time_ms"))
    if high is None or low is None or close is None or close_ms is None:
        return False
    if close_ms > open_ms + MINUTE_MS - 1:
        return False
    if close_ms >= expiry_ms:
        return False
    return True


def _build_summaries(
    spread_rows: list[dict[str, Any]],
    unique_input_cards: int,
    exclusions: list[dict[str, Any]],
) -> dict[str, Any]:
    summaries = {
        "input": {
            "unique_input_cards": unique_input_cards,
            "spread_rows": len(spread_rows),
            "excluded_items": len(exclusions),
        },
        "overall": [],
        "directional_vs_opposite": [],
        "neutral_sides": [],
        "dte_buckets": [],
        "versions": [],
        "anchor_position": [],
        "gamma_regime": [],
        "flow_alignment": [],
        "paired_comparison": {},
    }
    summaries["overall"] = _group_metrics(spread_rows, ("side", "relation", "target_width", "credit_fraction"))
    summaries["directional_vs_opposite"] = _group_metrics(
        [row for row in spread_rows if row["relation"] in {"directional", "opposite"}],
        ("direction", "relation", "side", "target_width", "credit_fraction"),
    )
    summaries["neutral_sides"] = _group_metrics(
        [row for row in spread_rows if row["direction"] == "NEUTRAL"],
        ("relation", "side", "target_width", "credit_fraction"),
    )
    summaries["dte_buckets"] = _group_metrics(
        spread_rows,
        ("dte_bucket", "direction", "relation", "side", "target_width", "credit_fraction"),
    )
    summaries["versions"] = _group_metrics(
        spread_rows,
        ("version", "direction", "relation", "side", "target_width", "credit_fraction"),
    )
    summaries["anchor_position"] = _group_metrics(
        spread_rows,
        ("anchor_position", "direction", "relation", "side", "target_width", "credit_fraction"),
    )
    summaries["gamma_regime"] = _group_metrics(
        spread_rows,
        ("gamma_regime", "direction", "relation", "side", "target_width", "credit_fraction"),
    )
    summaries["flow_alignment"] = _group_metrics(
        spread_rows,
        ("flow_alignment", "direction", "relation", "side", "target_width", "credit_fraction"),
    )
    summaries["paired_comparison"] = _paired_comparison(spread_rows)
    return summaries


def _group_metrics(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(row.get(key) for key in keys)].append(row)
    result = []
    for key_values, group_rows in sorted(buckets.items(), key=lambda item: tuple("" if value is None else str(value) for value in item[0])):
        labels = {key: value for key, value in zip(keys, key_values)}
        result.append({**labels, **_metrics(group_rows)})
    return result


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_signal = {}
    for row in rows:
        key = (row["card_id"], row["side"], row["relation"])
        by_signal.setdefault(key, row)
    selected = list(by_signal.values())
    n = len(selected)
    if not selected:
        return {
            "n": 0,
            "win_rate": None,
            "tie_rate": None,
            "otm_rate": None,
            "full_width_rate": None,
            "avg_win_btc": None,
            "avg_abs_loss_btc": None,
            "payoff_ratio": None,
            "tail_top_5pct_payout_btc": None,
            "tail_top_5pct_payout_to_total_credit": None,
            "total_credit_btc": None,
        }
    wins = [row["net_pnl_btc"] for row in selected if row["net_pnl_btc"] > 0]
    losses = [row["net_pnl_btc"] for row in selected if row["net_pnl_btc"] < 0]
    ties = [row for row in selected if row["net_pnl_btc"] == 0]
    total_credit = sum(row["net_credit_btc"] for row in selected)
    payouts = sorted((row["payout_btc"] for row in selected), reverse=True)
    top_n = max(1, ceil(n * 0.05))
    top_payout = sum(payouts[:top_n])
    avg_win = sum(wins) / len(wins) if wins else None
    avg_abs_loss = abs(sum(losses) / len(losses)) if losses else None
    return {
        "n": n,
        "win_rate": len(wins) / n,
        "tie_rate": len(ties) / n,
        "otm_rate": sum(1 for row in selected if row["short_otm_at_expiry"]) / n,
        "full_width_rate": sum(1 for row in selected if row["payout_category"] == "full_width") / n,
        "avg_win_btc": avg_win,
        "avg_abs_loss_btc": avg_abs_loss,
        "payoff_ratio": (avg_win / avg_abs_loss if avg_win is not None and avg_abs_loss else None),
        "tail_top_5pct_payout_btc": top_payout,
        "tail_top_5pct_payout_to_total_credit": (top_payout / total_credit if total_credit > 0 else None),
        "total_credit_btc": total_credit,
    }


def _paired_comparison(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    eligible = [row for row in rows if row.get("relation") in {"directional", "opposite"}]
    return {
        "bullish_put_vs_call": _paired_group(
            [row for row in eligible if row.get("direction") == "BULLISH"],
            "put_credit",
            "call_credit",
        ),
        "bearish_call_vs_put": _paired_group(
            [row for row in eligible if row.get("direction") == "BEARISH"],
            "call_credit",
            "put_credit",
        ),
        "pooled_directional_vs_opposite": _paired_group(
            eligible,
            None,
            None,
        ),
    }


def _paired_group(
    rows: list[dict[str, Any]],
    preferred_side: str | None,
    opposite_side: str | None,
) -> list[dict[str, Any]]:
    buckets: dict[tuple[float, float], dict[str, dict[tuple[Any, ...], dict[str, Any]]]] = defaultdict(
        lambda: {"preferred": {}, "opposite": {}}
    )
    for row in rows:
        key = (row["target_width"], row["credit_fraction"])
        card_key = (row["card_id"], row["direction"])
        is_preferred = (
            row.get("side") == preferred_side
            if preferred_side
            else row.get("relation") == "directional"
        )
        is_opposite = (
            row.get("side") == opposite_side
            if opposite_side
            else row.get("relation") == "opposite"
        )
        if is_preferred:
            buckets[key]["preferred"][card_key] = row
        elif is_opposite:
            buckets[key]["opposite"][card_key] = row

    result = []
    for (target_width, credit_fraction), bucket in sorted(buckets.items()):
        pair_rows = []
        for card_key, preferred in bucket["preferred"].items():
            opposite = bucket["opposite"].get(card_key)
            if opposite is not None:
                pair_rows.append((preferred, opposite))
        result.append(_paired_metrics(target_width, credit_fraction, pair_rows))
    return result


def _paired_metrics(
    target_width: float,
    credit_fraction: float,
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    pnl_wins = pnl_ties = pnl_losses = 0
    otm_wins = otm_ties = otm_losses = 0
    diffs = []
    for preferred, opposite in pairs:
        pnl_diff = preferred["net_pnl_btc"] - opposite["net_pnl_btc"]
        diffs.append(pnl_diff)
        if pnl_diff > 1e-12:
            pnl_wins += 1
        elif pnl_diff < -1e-12:
            pnl_losses += 1
        else:
            pnl_ties += 1

        preferred_otm = bool(preferred["short_otm_at_expiry"])
        opposite_otm = bool(opposite["short_otm_at_expiry"])
        if preferred_otm and not opposite_otm:
            otm_wins += 1
        elif opposite_otm and not preferred_otm:
            otm_losses += 1
        else:
            otm_ties += 1

    n = len(pairs)
    return {
        "target_width": target_width,
        "credit_fraction": credit_fraction,
        "n": n,
        "net_pnl_preferred_wins": pnl_wins,
        "net_pnl_ties": pnl_ties,
        "net_pnl_preferred_losses": pnl_losses,
        "otm_preferred_wins": otm_wins,
        "otm_ties": otm_ties,
        "otm_preferred_losses": otm_losses,
        "avg_net_pnl_diff_btc": (sum(diffs) / n if n else None),
    }


def _dte_bucket(dte_hours: float) -> str:
    if 0 < dte_hours <= 4:
        return "(0,4]"
    if 4 < dte_hours <= 8:
        return "(4,8]"
    if 8 < dte_hours <= 16:
        return "(8,16]"
    if 16 < dte_hours <= 24:
        return "(16,24]"
    return "out_of_range"


def _exclusion(sample: dict[str, Any], reason: str, message: str, **extra: Any) -> dict[str, Any]:
    row = {
        "card_id": (sample or {}).get("card_id"),
        "episode_id": (sample or {}).get("episode_id"),
        "direction": (sample or {}).get("direction"),
        "version": (sample or {}).get("version"),
        "reason": reason,
        "message": message,
    }
    row.update(extra)
    return row


def _as_ms(value: Any) -> int | None:
    number = _num(value)
    if number is None:
        return None
    if number > 10_000_000_000_000_000:
        number = number / 1000
    elif number > 10_000_000_000_000:
        number = number / 1000
    elif number < 10_000_000_000:
        number = number * 1000
    return int(number)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _option_type(value: Any) -> str:
    raw = str(value or "").lower()
    if raw in {"call", "c"}:
        return "call"
    if raw in {"put", "p"}:
        return "put"
    return raw
