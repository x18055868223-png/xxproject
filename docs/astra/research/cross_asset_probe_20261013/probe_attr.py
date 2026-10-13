# V3 out-of-sample: beta estimated on past W explains how much of the next day's BTC move (same-day asset move known)
import sys
sys.argv=[sys.argv[0],"."]
exec(open("probe.py", encoding="utf-8").read().split("rows = returns(hourly_aligned())")[0])
rows = returns(hourly_aligned()); btc=[r["BTC"] for r in rows]; n=len(rows)
for W in (20,40,60):
    out=[]
    for k in AXES:
        a=[r[k] for r in rows]; sse=sst=0; sse_sig=sst_sig=0
        for t in range(W,n):
            x=a[t-W:t]; y=btc[t-W:t]; mx=sum(x)/W; my=sum(y)/W
            sxx=sum((v-mx)**2 for v in x); b=sum((u-mx)*(v-my) for u,v in zip(x,y))/sxx if sxx else 0
            e=btc[t]-b*a[t]; sse+=e*e; sst+=btc[t]**2
            if state(corr(y,x),W)!="0": sse_sig+=e*e; sst_sig+=btc[t]**2
        out.append(f"{k}:{1-sse/sst:+.1%}(sig-only {1-sse_sig/max(sst_sig,1e-12):+.1%})")
    print(W," ".join(out))
