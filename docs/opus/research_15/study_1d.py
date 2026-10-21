"""1-day BTC credit-spread desk: what two years of prices say about when the short leg is at risk.

Data: Binance spot BTCUSDT 1m closes (2024-10 .. 2026-09) and Deribit DVOL 1h closes (30-day IV index,
a proxy for how expensive options were; its level is above 1-day IV, so only comparisons between
groups are read, never absolute edge). Cycles expire 08:00 UTC like Deribit dailies.
"""
import array
import bisect
import csv
import io
import json
import math
import os
import statistics
import sys
import zipfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tools"))
import astra_session_clock_v1 as sc  # noqa: E402

RAW = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\intraday_vol\raw"
DVOL = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\intraday_vol\dvol_1h.json"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "closes.bin")   # rebuilt from RAW on first run
MIN = 60_000
H = 3_600_000
T0 = int(datetime(2024, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)


def load_closes():
    if os.path.exists(CACHE):
        a = array.array("d")
        with open(CACHE, "rb") as fh:
            a.frombytes(fh.read())
        return a
    n = int((datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000 - T0) // MIN)
    a = array.array("d", [math.nan]) * n
    for name in sorted(os.listdir(RAW)):
        if not name.endswith(".zip"):
            continue
        z = zipfile.ZipFile(os.path.join(RAW, name))
        for row in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), "ascii")):
            t = int(row[0])
            t = t // 1000 if t > 10 ** 14 else t
            i = (t - T0) // MIN
            if 0 <= i < n:
                a[i] = float(row[4])
    last = math.nan
    for i in range(n):                      # forward-fill the rare missing minute
        if math.isnan(a[i]):
            a[i] = last
        else:
            last = a[i]
    with open(CACHE, "wb") as fh:
        fh.write(a.tobytes())
    return a


C = load_closes()
DV = json.load(open(DVOL))["points"]
DV_T = [p[0] for p in DV]
DV_V = [p[1] for p in DV]


def px(ms):
    return C[(ms - T0) // MIN]


def dvol(ms):
    i = bisect.bisect_right(DV_T, ms) - 1
    return DV_V[i] if i >= 0 and ms - DV_T[i] <= 2 * H else None


def dvol_rank(ms, hours):
    """Percentile of DVOL now within the trailing window (0..1)."""
    now = dvol(ms)
    i = bisect.bisect_right(DV_T, ms)
    window = DV_V[max(0, i - hours):i]
    if now is None or len(window) < hours * 0.8:
        return None
    return sum(1 for v in window if v < now) / len(window)


def rv(ms, hours):
    """Annualized realized vol of 5-minute log returns over the trailing hours."""
    s = 0.0
    n = 0
    for k in range(hours * 12):
        a, b = px(ms - (k + 1) * 5 * MIN), px(ms - k * 5 * MIN)
        s += math.log(b / a) ** 2
        n += 1
    return math.sqrt(s / (n * 5 / 60) * 8760) * 100


def path(entry, expiry):
    i0, i1 = (entry - T0) // MIN, (expiry - T0) // MIN
    seg = C[i0:i1 + 1]
    return seg


def cycles(entry_hours_before):
    out = []
    day = datetime(2024, 10, 9, 8, tzinfo=timezone.utc)   # 8 days of warm-up for the trailing windows
    end = datetime(2026, 9, 30, 8, tzinfo=timezone.utc)
    while day <= end:
        expiry = int(day.timestamp() * 1000)
        entry = expiry - entry_hours_before * H
        iv = dvol(entry)
        if iv:
            var_hours = sc.variance_between(entry, expiry)
            sig = iv / 100 * math.sqrt(var_hours / 8760)
            seg = path(entry, expiry)
            s0, s1 = seg[0], seg[-1]
            out.append({
                "expiry": expiry, "entry": entry, "dow_expiry": day.weekday(), "s0": s0, "s1": s1, "sig": sig,
                "iv": iv, "z": math.log(s1 / s0) / sig, "up": math.log(max(seg) / s0) / sig,
                "down": math.log(min(seg) / s0) / sig, "seg": seg,
                "rank7": dvol_rank(entry, 168), "rank14": dvol_rank(entry, 336),
                "iv_chg_24h": (iv - dvol(entry - 24 * H)) if dvol(entry - 24 * H) else None,
                "rv24": rv(entry, 24), "rv7d": rv(entry, 168),
                "ret24": math.log(s0 / px(entry - 24 * H)),
            })
        day = datetime.fromtimestamp(day.timestamp() + 86400, tz=timezone.utc)
    return out


def summary(rows, m=0.4):
    """Short leg at m sigma out on each side: touch and settle-beyond rates; realized/implied."""
    n = len(rows)
    if not n:
        return None
    ratio = math.sqrt(sum(r["z"] ** 2 for r in rows) / n)
    put_t = sum(r["down"] <= -m for r in rows) / n
    call_t = sum(r["up"] >= m for r in rows) / n
    put_s = sum(r["z"] <= -m for r in rows) / n
    call_s = sum(r["z"] >= m for r in rows) / n
    return {"n": n, "rv_over_iv": round(ratio, 3), "touch_put": round(put_t, 3), "touch_call": round(call_t, 3),
            "settle_put": round(put_s, 3), "settle_call": round(call_s, 3)}


def buckets(rows, key, edges, labels):
    res = {}
    for lab, lo, hi in zip(labels, edges[:-1], edges[1:]):
        sub = [r for r in rows if r[key] is not None and lo <= r[key] < hi]
        res[lab] = summary(sub)
    return res


def main():
    report = {}
    by_entry = {}
    for hb in (24, 18, 16, 12, 6):
        rows = cycles(hb)
        by_entry[hb] = rows
        report[f"entry_{hb}h_before"] = summary(rows)
    rows = by_entry[24]
    # what was known at entry
    for hb in (24, 18):
        rows = by_entry[hb]
        report[f"h{hb}_iv_rank7"] = buckets(rows, "rank7", [0, .2, .4, .6, .8, 1.01], ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"])
        report[f"h{hb}_iv_rank14"] = buckets(rows, "rank14", [0, .2, .4, .6, .8, 1.01], ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"])
        for r in rows:
            r["vrp7"] = r["iv"] - r["rv7d"]
            r["vrp24"] = r["iv"] - r["rv24"]
        q = lambda k: sorted(r[k] for r in rows)  # noqa: E731
        for k in ("vrp7", "vrp24", "iv_chg_24h"):
            vals = [r[k] for r in rows if r[k] is not None]
            qs = statistics.quantiles(vals, n=5)
            report[f"h{hb}_{k}_quintiles"] = {"cuts": [round(c, 2) for c in qs],
                                              "buckets": buckets(rows, k, [-1e9] + qs + [1e9], ["Q1 低", "Q2", "Q3", "Q4", "Q5 高"])}
        report[f"h{hb}_weekend_expiry"] = {"Sat/Sun/Mon 到期": summary([r for r in rows if r["dow_expiry"] in (5, 6, 0)]),
                                          "Tue-Fri 到期": summary([r for r in rows if r["dow_expiry"] in (1, 2, 3, 4)])}
        # trend: does the prior 24h move continue? (sign agreement), and which side gets touched
        up = [r for r in rows if r["ret24"] > 0.01]
        dn = [r for r in rows if r["ret24"] < -0.01]
        flat = [r for r in rows if -0.01 <= r["ret24"] <= 0.01]
        report[f"h{hb}_prior24h_move"] = {"涨超1%": summary(up), "跌超1%": summary(dn), "±1%内": summary(flat),
                                         "continuation_rate_big_moves": round(
                                             sum((r["z"] > 0) == (r["ret24"] > 0) for r in up + dn) / max(1, len(up + dn)), 3)}
    # once the short leg is touched: how often does it still settle beyond, by hours left at the touch
    for hb in (24, 16):
        rows = by_entry[hb]
        for side in ("put", "call"):
            m = 0.4
            stat = {}
            for r in rows:
                seg = r["seg"]
                s0 = seg[0]
                lvl = s0 * math.exp((-m if side == "put" else m) * r["sig"])
                hit = next((i for i, p in enumerate(seg) if (p <= lvl if side == "put" else p >= lvl)), None)
                if hit is None:
                    continue
                left = (len(seg) - 1 - hit) / 60
                b = "touch with >12h left" if left > 12 else "6-12h left" if left > 6 else "2-6h left" if left > 2 else "<2h left"
                beyond = (seg[-1] <= lvl) if side == "put" else (seg[-1] >= lvl)
                # how far beyond at settle, in the same sigma units, and whether it reached a leg 2,000 USD further
                far = s0 * math.exp((-m if side == "put" else m) * r["sig"]) + (-2000 if side == "put" else 2000)
                reach_long = any((p <= far if side == "put" else p >= far) for p in seg[hit:])
                st = stat.setdefault(b, {"touched": 0, "settled_beyond": 0, "reached_long": 0})
                st["touched"] += 1
                st["settled_beyond"] += beyond
                st["reached_long"] += reach_long
            for st in stat.values():
                st["settle_beyond_rate"] = round(st["settled_beyond"] / st["touched"], 3)
                st["reach_long_rate"] = round(st["reached_long"] / st["touched"], 3)
            report[f"h{hb}_{side}_after_touch"] = stat
    for rows in by_entry.values():
        for r in rows:
            r.pop("seg", None)
    json.dump(report, open(os.path.join(os.path.dirname(CACHE), "study_1d.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))


main()
