import sys, math
sys.argv = [sys.argv[0], "."]
exec(open("probe.py", encoding="utf-8").read().split("rows = returns(hourly_aligned())")[0])
from datetime import datetime, timezone
def dd(path, us=True):
    out = {}
    for t, c in bars(path):
        out[datetime.fromtimestamp(t + (6*3600 if us else 0), tz=timezone.utc).strftime("%Y-%m-%d")] = c
    return out
s = {"BTC": dd("d_BTC-USD.json", False), "QQQ": dd("d_QQQ.json"), "GOLD": dd("d_GC%3DF.json"), "DXY": dd("d_DX-Y.NYB.json"), "TNX": dd("d_%5ETNX.json"), "REAL": fred("DFII10")}
rows = returns(s)
for y0, y1 in (("2017","2019"),("2020","2021"),("2022","2023"),("2024","2026")):
    sub = [r for r in rows if y0 <= r["date"][:4] <= y1]
    btc = [r["BTC"] for r in sub]
    print(y0, y1, len(sub), {k: round(corr(btc, [r[k] for r in sub]), 2) for k in AXES},
          "lead", {k: round(corr(btc[1:], [r[k] for r in sub[:-1]]), 2) for k in AXES})
W = 60
for y in range(2017, 2027):
    idx = [i for i, r in enumerate(rows) if r["date"][:4] == str(y) and i >= W]
    if not idx: continue
    btc = [r["BTC"] for r in rows]
    cnt = {k: {"+":0,"-":0,"0":0} for k in AXES}; none = 0
    for t in idx:
        st = {k: state(corr(btc[t-W:t], [r[k] for r in rows[t-W:t]]), W) for k in AXES}
        for k in AXES: cnt[k][st[k]] += 1
        none += all(v == "0" for v in st.values())
    n = len(idx)
    print(y, " ".join(f"{k}:{cnt[k]['+']*100//n}/{cnt[k]['-']*100//n}" for k in AXES), f"none-significant {none*100//n}%")
