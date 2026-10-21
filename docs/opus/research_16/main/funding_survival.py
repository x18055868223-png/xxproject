"""BTC 1-day cycles: (1) funding crowding vs next-day direction and tails; (2) how much touch risk is
left when the short leg has not been touched by a given hour (the intraday exit question)."""
import bisect
import json
import math
import statistics

SRC = r"C:\Users\Xu\Documents\中性回路整合工程\.worktrees\claude-astra\docs\opus\research_15\study_1d.py"
code = open(SRC, encoding="utf-8").read().replace("\nmain()\n", "\n")
code = code.replace('CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "closes.bin")',
                    'CACHE = r"C:\\Users\\Xu\\AppData\\Local\\Temp\\claude\\C--Users-Xu-Documents---------\\5555fd0f-0352-4a36-ae3e-fdfdff77f94a\\scratchpad\\research\\closes.bin"')
code = code.replace('os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tools")',
                    'r"C:\\Users\\Xu\\Documents\\中性回路整合工程\\.worktrees\\claude-astra\\tools"')
exec(code)

FUND = json.load(open(r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\intraday_vol\funding_8h.json"))["points"]
FT = [p[0] for p in FUND]
FV = [p[1] for p in FUND]


def funding(ms, n=1):
    i = bisect.bisect_right(FT, ms)
    vals = FV[max(0, i - n):i]
    return sum(vals) / len(vals) if len(vals) == n else None


def describe(sub):
    if len(sub) < 20:
        return {"n": len(sub)}
    z = [r["z"] for r in sub]
    return {"n": len(sub), "mean_z": round(statistics.mean(z), 3), "up_share": round(sum(v > 0 for v in z) / len(z), 3),
            "rms_z": round(math.sqrt(sum(v * v for v in z) / len(z)), 3),
            "down_tail_(min<=-1σ)": round(sum(r["down"] <= -1 for r in sub) / len(sub), 3),
            "up_tail_(max>=1σ)": round(sum(r["up"] >= 1 for r in sub) / len(sub), 3),
            "put_settle_beyond_0.4σ": round(sum(r["z"] <= -0.4 for r in sub) / len(sub), 3),
            "call_settle_beyond_0.4σ": round(sum(r["z"] >= 0.4 for r in sub) / len(sub), 3)}


out = {}
rows = cycles(24)
for r in rows:
    r["f_last"] = funding(r["entry"], 1)
    r["f_3d"] = funding(r["entry"], 9)
vals = sorted(r["f_3d"] for r in rows if r["f_3d"] is not None)
q = statistics.quantiles(vals, n=10)
out["funding_3d_mean_deciles_cuts"] = [round(c * 1e4, 3) for c in q]   # in basis points per 8h
groups = {"负或零（空头付费）": lambda f: f <= 0, "最低 10%": lambda f: f <= q[0], "中间 80%": lambda f: q[0] < f < q[8],
          "最高 10%（多头拥挤）": lambda f: f >= q[8], "最高 20%": lambda f: f >= q[7]}
out["funding_3d"] = {k: describe([r for r in rows if r["f_3d"] is not None and g(r["f_3d"])]) for k, g in groups.items()}
out["funding_3d"]["全部"] = describe(rows)
half = int(datetime(2025, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
out["funding_3d_by_half"] = {f"{k} {lab}": describe([r for r in rows if r["f_3d"] is not None and g(r["f_3d"]) and sel(r)])
                             for k, g in (("最高 20%", groups["最高 20%"]), ("最低 10%", groups["最低 10%"]))
                             for lab, sel in (("Y1", lambda r: r["entry"] < half), ("Y2", lambda r: r["entry"] >= half))}

# (2) survival: entry 08:00 UTC (24h), short at 0.4σ each side
M = 0.4
checkpoints = [12, 14, 16, 18, 20, 22, 0, 2, 4, 6]
surv = {}
for side in ("put", "call"):
    res = {}
    for hh in checkpoints:
        hours_in = (hh - 8) % 24
        alive = beyond_after = touch_after = 0
        for r in rows:
            seg = r["seg"]
            lvl = seg[0] * math.exp((-M if side == "put" else M) * r["sig"])
            hit = lambda p: p <= lvl if side == "put" else p >= lvl  # noqa: E731
            cut = hours_in * 60
            if any(hit(p) for p in seg[:cut + 1]):
                continue
            alive += 1
            if any(hit(p) for p in seg[cut + 1:]):
                touch_after += 1
            if hit(seg[-1]):
                beyond_after += 1
        expiry = rows[0]["expiry"]
        share = sc.variance_between(expiry - (24 - hours_in) * H, expiry) / sc.variance_between(expiry - 24 * H, expiry)
        res[f"{hh:02d}:00 UTC 仍未触及"] = {"alive": alive, "之后触及": round(touch_after / alive, 3),
                                         "到期越过": round(beyond_after / alive, 3),
                                         "剩余方差占比（时钟，按首个交割日）": round(share, 3)}
    surv[side] = res
out["survival_after_untouched_08utc_entry"] = surv

# first-touch hour distribution
hist = {}
for side in ("put", "call"):
    hcount = {}
    for r in rows:
        seg = r["seg"]
        lvl = seg[0] * math.exp((-M if side == "put" else M) * r["sig"])
        i = next((i for i, p in enumerate(seg) if (p <= lvl if side == "put" else p >= lvl)), None)
        if i is None:
            continue
        hour = (8 + i // 60) % 24
        hcount[hour] = hcount.get(hour, 0) + 1
    hist[side] = {f"{h:02d}": hcount.get(h, 0) for h in [(8 + k) % 24 for k in range(24)]}
out["first_touch_hour_utc"] = hist
for r in rows:
    r.pop("seg", None)
json.dump(out, open(r"C:\Users\Xu\AppData\Local\Temp\claude\C--Users-Xu-Documents---------\5555fd0f-0352-4a36-ae3e-fdfdff77f94a\scratchpad\research\me\funding_survival.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
