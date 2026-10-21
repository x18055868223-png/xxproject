import math, statistics, sys
sys.argv = ["x"]
exec(open("study_1d.py", encoding="utf-8").read().replace("\nmain()\n", "\n"))
rows = cycles(24)
for r in rows:
    r["vrp24"] = r["iv"] - r["rv24"]
split = int(datetime(2025, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
def stat(sub):
    if len(sub) < 15: return f"n={len(sub)}"
    z = [abs(r["z"]) for r in sub]
    big = sum(v > 1 for v in z) / len(z)
    rms = math.sqrt(sum(v * v for v in z) / len(z))
    trimmed = sorted(v * v for v in z)[: int(len(z) * 0.95)]
    return f"n={len(sub):3d} rms={rms:.2f} rms_trim95={math.sqrt(sum(trimmed)/len(trimmed)):.2f} P(|z|>1)={big:.2f} median|z|={statistics.median(z):.2f}"
def show(name, key, edges, labs):
    print("==", name)
    for lab, lo, hi in zip(labs, edges[:-1], edges[1:]):
        sub = [r for r in rows if r[key] is not None and lo <= r[key] < hi]
        y1 = [r for r in sub if r["entry"] < split]; y2 = [r for r in sub if r["entry"] >= split]
        print(f"  {lab:8} all {stat(sub)}\n           Y1 {stat(y1)}\n           Y2 {stat(y2)}")
show("DVOL 7d rank", "rank7", [0, .2, .8, 1.01], ["<20%", "20-80%", ">=80%"])
qs = statistics.quantiles([r["iv_chg_24h"] for r in rows if r["iv_chg_24h"] is not None], n=5)
show("DVOL change vs 24h ago", "iv_chg_24h", [-1e9, qs[0], qs[3], 1e9], ["fall", "mid", "rise"])
qs = statistics.quantiles([r["vrp24"] for r in rows], n=5)
show("DVOL - RV24h", "vrp24", [-1e9, qs[0], qs[3], 1e9], ["RV>=IV", "mid", "IV>>RV"])
print("baseline", stat(rows))
