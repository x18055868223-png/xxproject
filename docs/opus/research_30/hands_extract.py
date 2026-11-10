"""Case-book extraction for doc 30 (N1). Read-only over the research archive. Run on the server from the
archive root with python3, this file on stdin, stdout redirected to hands_raw.json.
One hand per expiry day and side, read at the first frozen case at or after 09:00 UTC of the day before expiry.
Only observed facts are extracted. Before the reading moment: this expiry's key strikes (Deribit OI/gamma,
unsigned) and what the index did at them in the prior 24h. After it: what the index did at the same strikes
until expiry."""
import glob, gzip, json, os, re, sys

H = 3_600_000
DAY = 24 * H


def load(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")) as f:
        return json.load(f)


def entry(case, ref):
    for layer in case["layers"].values():
        for e in layer:
            if e.get("ref") == ref:
                return e
    return {}


def at(points, ms):
    """Last index point at or before ms."""
    if not points or points[0][0] > ms:
        return None
    lo, hi = 0, len(points) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if points[mid][0] <= ms:
            lo = mid
        else:
            hi = mid - 1
    return points[lo][1]


def behaviour(points, k, start, end):
    """What the index did at level k in (start, end]: crossings, closest approach, the first crossing and the hour after it."""
    seg = [(t, p) for t, p in points if start < t <= end]
    if len(seg) < 2:
        return None
    crossings, first, prev = 0, None, seg[0][1] >= k
    for i, (t, p) in enumerate(seg):
        s = p >= k
        if s != prev:
            crossings += 1
            if first is None:
                first = i
            prev = s
    out = {"crossings": crossings, "closest_usd": round(min(abs(p - k) for _, p in seg), 1),
           "minutes_within_100usd": sum(1 for _, p in seg if abs(p - k) <= 100), "first_cross_ms": None}
    if first is not None:
        t0 = seg[first][0]
        after = [p for t, p in seg[first:] if t <= t0 + H]
        sign = 1 if seg[first][1] >= k else -1          # direction of the first crossing
        out.update(first_cross_ms=t0, first_cross_dir="up" if sign > 0 else "down",
                   beyond_60m_usd=round(max(sign * (p - k) for p in after), 1),
                   back_within_60m=any(sign * (p - k) < 0 for p in after),
                   end_beyond=bool(sign * (seg[-1][1] - k) > 0))
    return out


def main():
    all_rows = []
    for lf in sorted(glob.glob("ledger/*.jsonl")):
        for line in open(lf, encoding="utf-8"):
            try:
                all_rows.append(json.loads(line))
            except ValueError:
                pass
    hands = []
    for pf in sorted(glob.glob("paths/*.json.gz")):
        day = os.path.basename(pf)[:10]
        path = load(pf)
        expiry, points = path["expiry_ms"], path["points"]
        read_from = expiry - DAY + H                    # 09:00 UTC of the day before
        outcomes = {}
        of = "outcomes/%s.jsonl" % day
        if os.path.exists(of):
            for line in open(of, encoding="utf-8"):
                o = json.loads(line)
                outcomes[o["contract_key"]] = o
        rows = [r for r in all_rows if r.get("expiry_ms") == expiry]
        dirs = sorted(d for d in os.listdir("cases") if d <= day)[-2:]
        cases = [cf for d in dirs for cf in sorted(glob.glob("cases/%s/*.json.gz" % d))]
        for side in ("put", "call"):
            pick = None
            for cf in cases:
                if ("_%s_" % side) not in os.path.basename(cf):
                    continue
                try:
                    c = load(cf)
                except Exception:
                    continue
                w = (entry(c, "L3.expiry_walls").get("values") or {})
                if read_from <= c.get("asof_ms", 0) < expiry and w.get("expiry_ms") == expiry:
                    pick = (cf, c, w)
                    break
            if not pick:
                hands.append({"expiry_day": day, "side": side, "status": "no_case"})
                continue
            cf, c, w = pick
            t0 = c["asof_ms"]
            # the hand is the case's own contract (the ledger also tracks older contracts of the same side)
            suffix = "-P" if side == "put" else "-C"
            text = json.dumps([c.get("contract_cn"), c.get("candidate_id"), c["layers"].get("L1")], ensure_ascii=False)
            names = [n for n in dict.fromkeys(re.findall(r"BTC-\d+[A-Z]{3}\d+-\d+-[PC]", text)) if n.endswith(suffix)]
            strikes = sorted({float(n.split("-")[2]) for n in names})
            near = [r for r in rows if r.get("side") == side and abs(r["at_ms"] - t0) <= 40 * 60000
                    and len(names) >= 2 and set(r["contract_key"].split("|")) <= set(names)]
            row = min(near, key=lambda r: abs(r["at_ms"] - t0)) if near else None
            spot = at(points, t0)
            top = w.get("top") or {}
            walls = w.get("walls") or {}
            keys = {}
            for kind in ("gamma_call", "gamma_put", "oi_call", "oi_put"):
                for rank, x in enumerate(top.get(kind) or [], 1):
                    keys.setdefault(float(x["strike"]), []).append("%s#%d" % (kind, rank))
            if walls.get("pin") is not None:
                keys.setdefault(float(walls["pin"]), []).append("pin")
            gex = entry(c, "L3.gex.structure").get("values") or {}
            anchor = (entry(c, "L3.space.anchor").get("values") or {}).get("gamma_regime") or {}
            hand = {"expiry_day": day, "side": side, "status": "ok", "case": os.path.basename(cf), "read_at_ms": t0,
                    "hours_to_expiry": round((expiry - t0) / H, 2), "index_at_read": spot,
                    "index_at_expiry": at(points, expiry),
                    "expected_move_usd": w.get("expected_move_usd"), "atm_iv_pct": w.get("atm_iv_pct"),
                    "totals": w.get("totals"), "walls": walls, "top": top,
                    "third_party": {"market_state": gex.get("market_state"), "flip_point": gex.get("flip_point"),
                                    "total_net_gex": gex.get("total_net_gex"), "regime": anchor.get("regime")},
                    "case_contracts": names, "row": None, "outcome": None, "keys": []}
            if row:
                ck = row["contract_key"]
                names = ck.split("|")
                hand["row"] = {"contract_key": ck, "short_strike": float(names[0].split("-")[2]),
                               "long_strike": float(names[1].split("-")[2]),
                               "credit_after_fee_btc": row.get("credit_after_fee_btc"),
                               "mark_spread_btc": row.get("mark_spread_btc"),
                               "distance_in_moves": row.get("distance_in_moves"),
                               "session_phase": row.get("session_phase"), "states": row.get("states"),
                               "index_chg_1h_pct": row.get("index_chg_1h_pct"),
                               "index_chg_4h_pct": row.get("index_chg_4h_pct"),
                               "rv_24h_pct": row.get("rv_24h_pct"), "locked": row.get("locked")}
                o = outcomes.get(ck)
                if o:
                    hand["outcome"] = {"delivery_price_usd": o.get("delivery_price_usd"),
                                       "payout_btc": o.get("payout_btc"), "path": o.get("path")}
                keys.setdefault(hand["row"]["short_strike"], []).append("short")
                keys.setdefault(hand["row"]["long_strike"], []).append("long")
            for k in sorted(keys):
                hand["keys"].append({"strike": k, "roles": keys[k],
                                     "before_24h": behaviour(points, k, t0 - DAY, t0),
                                     "after": behaviour(points, k, t0, expiry)})
            seg = [p for t, p in points if t0 < t <= expiry]
            if seg:
                hand["after_range"] = {"max_usd": max(seg), "min_usd": min(seg)}
            hands.append(hand)
        # a late look at the same expiry: the first case at or after 4 hours before expiry
        for cf in cases:
            try:
                c = load(cf)
            except Exception:
                continue
            w = (entry(c, "L3.expiry_walls").get("values") or {})
            if expiry - 4 * H <= c.get("asof_ms", 0) < expiry and w.get("expiry_ms") == expiry:
                hands.append({"expiry_day": day, "side": "late", "status": "late", "read_at_ms": c["asof_ms"],
                              "index_at_read": at(points, c["asof_ms"]), "index_at_expiry": at(points, expiry),
                              "walls": w.get("walls"), "top": w.get("top"), "expected_move_usd": w.get("expected_move_usd")})
                break
    json.dump({"schema": "hands_raw@0.1", "hands": hands}, sys.stdout, ensure_ascii=False)


main()
