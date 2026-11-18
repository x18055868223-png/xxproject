"""Deterministic, offline responsibility evidence from frozen index samples."""

from . import astra_player_input_v1 as player

MINUTE = 60_000
WINDOWS = (5, 15, 60, 240)


def frozen_points(path, cutoff, frozen, acquired, *, synthetic=False):
    if path.get("index_name") != "btc_usd":
        raise ValueError("expected Deribit btc_usd path")
    semantics = path.get("timestamp_semantics", "unverified_minute_label")
    if semantics not in {"instant", "minute_close", "minute_open", "unverified_minute_label"}:
        raise ValueError("unsupported timestamp semantics")
    if synthetic and (acquired is not None or path.get("fetched_at_ms") is not None):
        raise ValueError("synthetic fixtures cannot carry acquisition receipts")
    if not synthetic and (semantics != "unverified_minute_label" or
                          type(acquired) is not int or not cutoff <= acquired <= frozen or
                          type(path.get("fetched_at_ms")) is not int or not 0 < path["fetched_at_ms"] <= acquired):
        raise ValueError("historical path needs a valid frozen acquisition receipt and archived clock convention")
    delay = MINUTE if semantics in {"minute_open", "unverified_minute_label"} else 0
    accepted, excluded, seen = {}, [], set()
    for row in path["points"]:
        if len(row) != 2 or type(row[0]) is not int or row[0] % MINUTE:
            raise ValueError("invalid minute label")
        label, price = row
        if (price := player.number(price)) is None or price <= 0:
            raise ValueError("invalid index price")
        if label in seen:
            raise ValueError("duplicate index minute")
        seen.add(label)
        if label + delay > cutoff:
            excluded.append({"label_ms": label, "latest_possible_ms": label + delay})
        else:
            accepted[label] = price
    return sorted(accepted.items()), {
        "timestamp_semantics": semantics,
        "market_cutoff_ms": cutoff,
        "bundle_acquired_ms": acquired,
        "path_fetched_ms": path.get("fetched_at_ms"),
        "freeze_ms": frozen,
        "availability_checked": not synthetic,
        "latest_eligible_label_ms": max(accepted, default=None),
        "excluded_after_market_cutoff": excluded,
    }


def summarize(points, contracts, cutoff):
    points = [point for point in points if cutoff - player.DAY < point[0] <= cutoff]
    if not points:
        return {"nodes": [], "events": [], "segments": [], "windows": {}}
    by_time = dict(points)
    last = points[-1][0]
    segments = [[]]
    for point in points:
        if segments[-1] and point[0] - segments[-1][-1][0] != MINUTE:
            segments.append([])
        segments[-1].append(point)

    mandatory, optional, events = {}, {}, []
    episodes = {side: [] for side in contracts}

    def mark(target, label, kind):
        target.setdefault(label, []).append(kind)

    def event(label, kind, side=None):
        events.append({"label_ms": label, "kind": kind, "side": side, "index_usd": by_time[label]})
        mark(mandatory, label, f"{side}_{kind}" if side else kind)

    mark(mandatory, points[0][0], "window_first_sample")
    mark(mandatory, last, "latest_eligible_sample")
    for idx, segment in enumerate(segments):
        if idx:
            event(segments[idx - 1][-1][0], "before_gap")
            event(segment[0][0], "after_gap")
        for side, contract in contracts.items():
            boundary = contract["short_strike_usd"]
            trace = player.responsibility_trace(segment, side, boundary)
            first_strict = next((label for label, price in segment if price != boundary), None)
            for episode in trace["episodes"]:
                entered = episode["entered_ms"]
                if entered == first_strict:
                    entered = None  # A segment beginning at a touch has no observed outside side yet.
                episodes[side].append({**episode, "entered_ms": entered})
                event(episode["observed_from_ms"], "entry" if entered is not None else "segment_starts_inside", side)
                mark(optional, episode["peak_ms"], f"{side}_deepest")
                if episode["returned_ms"] is not None:
                    event(episode["returned_ms"], "return", side)
            for label in trace["touches"]:
                event(label, "short_touch", side)
            protection = contract["long_strike_usd"]
            direction = -1 if side == "put" else 1
            previous = None
            for label, price in segment:
                beyond = direction * (price - protection) > 0
                if price == protection:
                    event(label, "protection_touch", side)
                if beyond and previous is not True:
                    event(label, "segment_starts_beyond_protection" if previous is None else "beyond_protection", side)
                elif not beyond and previous is True:
                    event(label, "returns_from_beyond_protection", side)
                previous = beyond

    windows = {}
    for minutes in WINDOWS:
        start = last - minutes * MINUTE
        actual = [(label, price) for label, price in points if start <= label <= last]
        expected = minutes + 1
        window = {"start_ms": start, "end_ms": last, "sample_count": len(actual), "expected_count": expected}
        windows[str(minutes)] = window
        if len(actual) != expected:
            continue
        low = min(actual, key=lambda p: (p[1], p[0]))
        high = max(actual, key=lambda p: (p[1], -p[0]))
        rising = falling = 0.0
        running_low = running_high = actual[0][1]
        for _, price in actual:
            rising, falling = max(rising, price - running_low), max(falling, running_high - price)
            running_low, running_high = min(running_low, price), max(running_high, price)
        window.update(start_price=by_time[start], end_price=by_time[last], delta=by_time[last] - by_time[start],
                      low=low, high=high, range_usd=high[1] - low[1],
                      max_up_usd=rising, max_down_usd=falling)
        if minutes == 5:
            mark(mandatory, start, "five_minute_start")
        if minutes == 240:
            mark(optional, low[0], "four_hour_low")
            mark(optional, high[0], "four_hour_high")
            mark(optional, start, "four_hour_start")
    # Keep adjacent extrema when the last leg reverses the four-hour net move.
    fast, slow = windows["5"], windows["240"]
    if "delta" in fast and "delta" in slow and fast["delta"] * slow["delta"] < 0:
        extreme = slow["low" if fast["delta"] > 0 else "high"]
        mark(mandatory, extreme[0], "late_reversal_reference")
        first_extreme = slow["high" if fast["delta"] > 0 else "low"]
        if first_extreme[0] < extreme[0]:
            mark(mandatory, first_extreme[0], "preceding_leg_extreme")
    for label in sorted(optional, reverse=True):
        if len(mandatory) >= 6:
            break
        if label not in mandatory:
            mandatory[label] = []
    nodes = []
    for label, tags in sorted(mandatory.items()):
        price = by_time[label]
        nodes.append({
            "label_ms": label, "index_usd": price, "reasons": tags + optional.get(label, []),
            "put_margin_usd": price - contracts["put"]["short_strike_usd"],
            "call_margin_usd": contracts["call"]["short_strike_usd"] - price,
            **{f"{side}_state": player.responsibility_zone(price, contract) for side, contract in contracts.items()},
        })
    return {"nodes": nodes, "events": sorted(events, key=lambda e: e["label_ms"]), "episodes": episodes,
            "segments": [{"first": s[0][0], "last": s[-1][0], "samples": len(s)} for s in segments],
            "windows": windows}
