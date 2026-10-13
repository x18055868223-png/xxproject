"""Cross-asset relation probe (pure python). Not production code.

Aligned sample: one observation per US trading day, every asset at ~16:00 New York
(the QQQ closing hourly bar; other assets: their bar ending at the same time).
"""
import csv, json, math, sys
from datetime import datetime, timezone

D = sys.argv[1] if len(sys.argv) > 1 else "."
AX = {"QQQ": "QQQ", "GOLD": "GC%3DF", "DXY": "DX-Y.NYB", "TNX": "%5ETNX"}


def bars(path):
    r = json.load(open(f"{D}/{path}", encoding="utf-8"))["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    return [(t, c) for t, c in zip(r["timestamp"], q["close"]) if c is not None]


def fred(name):
    out = {}
    for row in csv.DictReader(open(f"{D}/{name}.csv", encoding="utf-8")):
        v = row[name]
        if v not in ("", "."):
            out[row["observation_date"]] = float(v)
    return out


def day(ts):  # New York date approximated with UTC-4/-5 -> use UTC-4.5 midpoint is wrong; use -5h
    return datetime.fromtimestamp(ts - 5 * 3600, tz=timezone.utc).strftime("%Y-%m-%d")


def hourly_aligned():
    qqq = bars("h_QQQ.json")
    last = {}
    for t, c in qqq:
        last[day(t)] = (t, c)
    series = {"QQQ": {d: c for d, (t, c) in last.items()}}
    for k in ("BTC", "GOLD", "DXY", "TNX"):
        src = bars("h_BTC-USD.json" if k == "BTC" else f"h_{AX[k]}.json")
        idx = {}
        for t, c in src:
            idx[t] = c
        ts = sorted(idx)
        out = {}
        j = 0
        for d, (tq, _) in sorted(last.items()):
            end = tq + 1800  # QQQ last bar starts 15:30, closes 16:00
            while j + 1 < len(ts) and ts[j + 1] + 3600 <= end:
                j += 1
            if ts[j] + 3600 <= end and end - (ts[j] + 3600) <= 3600:
                out[d] = idx[ts[j]]
        series[k] = out
    series["REAL"] = fred("DFII10")
    return series


def returns(series):
    dates = sorted(set.intersection(*[set(v) for v in series.values()]))
    rows = []
    for a, b in zip(dates, dates[1:]):
        r = {"date": b}
        for k, s in series.items():
            if k in ("TNX", "REAL"):
                r[k] = (s[b] - s[a]) * 100  # bp
            else:
                r[k] = math.log(s[b] / s[a])
        rows.append(r)
    return rows


def corr(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else 0.0


def ci(r, n, z=1.96):
    f = math.atanh(max(-0.999, min(0.999, r)))
    s = 1 / math.sqrt(n - 3)
    return math.tanh(f - z * s), math.tanh(f + z * s)


def state(r, n):
    lo, hi = ci(r, n)
    return "+" if lo > 0 else "-" if hi < 0 else "0"


AXES = ["QQQ", "GOLD", "DXY", "TNX", "REAL"]


def report(rows, label):
    n = len(rows)
    print(f"\n=== {label}: {n} aligned returns, {rows[0]['date']} .. {rows[-1]['date']}")
    btc = [r["BTC"] for r in rows]
    print("full-period corr:", {k: round(corr(btc, [r[k] for r in rows]), 3) for k in AXES})
    print("lead (asset_t -> btc_t+1):", {k: round(corr(btc[1:], [r[k] for r in rows[:-1]]), 3) for k in AXES})
    print("lag  (btc_t -> asset_t+1):", {k: round(corr([r[k] for r in rows[1:]], btc[:-1]), 3) for k in AXES})
    for W in (20, 40, 60):
        print(f"-- window {W}")
        for k in AXES:
            a = [r[k] for r in rows]
            past, fut, agree, sig = [], [], 0, 0
            states = {"+": 0, "-": 0, "0": 0}
            H = 20
            for t in range(W, n - H):
                rp = corr(btc[t - W:t], a[t - W:t])
                rf = corr(btc[t:t + H], a[t:t + H])
                past.append(rp); fut.append(rf)
                s = state(rp, W)
                states[s] += 1
                if s != "0":
                    sig += 1
                    agree += (rf > 0) == (s == "+")
            cur = corr(btc[-W:], a[-W:])
            lo, hi = ci(cur, W)
            tot = sum(states.values())
            print(f"  {k:5s} now r={cur:+.2f} [{lo:+.2f},{hi:+.2f}]  share +/-/0 = "
                  f"{states['+']/tot:.0%}/{states['-']/tot:.0%}/{states['0']/tot:.0%}  "
                  f"persist corr(past,next20)={corr(past, fut):+.2f}  sign-hold when sig={agree/max(sig,1):.0%} (n={sig})")
        # independence: no axis significant
        indep = 0; tot = 0
        for t in range(W, n + 1):
            tot += 1
            if all(state(corr(btc[t - W:t], [r[k] for r in rows[t - W:t]]), W) == "0" for k in AXES):
                indep += 1
        print(f"  share of windows with no significant axis: {indep/tot:.0%}")
        print("  recent states (every 10 days):",
              " ".join("".join(state(corr(btc[t - W:t], [r[k] for r in rows[t - W:t]]), W) for k in AXES)
                       for t in range(max(W, n - 200), n + 1, 10)), "(order", AXES, ")")


rows = returns(hourly_aligned())
report(rows, "hourly-aligned 16:00 NY")
