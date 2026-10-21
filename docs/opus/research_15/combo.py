import math, statistics, sys
exec(open("study_1d.py", encoding="utf-8").read().replace("\nmain()\n", "\n"))
for hb in (24, 16):
    rows = cycles(hb)
    chg = statistics.quantiles([r["iv_chg_24h"] for r in rows if r["iv_chg_24h"] is not None], n=5)[3]
    vrp = statistics.quantiles([r["iv"] - r["rv24"] for r in rows], n=5)[0]
    print(f"entry {hb}h before expiry; cut: DVOL rise >= {chg:.2f} pts, DVOL-RV24h <= {vrp:.2f}")
    groups = {}
    for r in rows:
        f = [r["rank7"] is not None and r["rank7"] >= 0.8, r["iv_chg_24h"] is not None and r["iv_chg_24h"] >= chg, r["iv"] - r["rv24"] <= vrp]
        groups.setdefault(sum(f), []).append(r)
        r["flags"] = f
    for k in sorted(groups):
        sub = groups[k]; z = [abs(r["z"]) for r in sub]
        touch = sum(min(r["down"], -r["up"]) <= -0.4 for r in sub) / len(sub)
        both = sum(r["down"] <= -0.4 and r["up"] >= 0.4 for r in sub) / len(sub)
        print(f"  flags={k}: n={len(sub):3d} P(|z|>1)={sum(v>1 for v in z)/len(z):.2f} median|z|={statistics.median(z):.2f} "
              f"touch(any side 0.4σ)={touch:.2f} both sides touched={both:.2f} reach_1σ_any={sum(max(-r['down'], r['up'])>=1 for r in sub)/len(sub):.2f}")
    # overlap
    n = len(rows); a = [sum(r["flags"][i] for r in rows) for i in range(3)]
    print("  flag counts", a, "pairs", [sum(r["flags"][i] and r["flags"][j] for r in rows) for i, j in ((0, 1), (0, 2), (1, 2))])
