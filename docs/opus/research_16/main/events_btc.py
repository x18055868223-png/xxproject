import json, math, statistics
exec(open("funding_survival.py", encoding="utf-8").read().split("FUND = ")[0])
ev = json.load(open(r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\research_us_equity\us_macro_events.json", encoding="utf-8"))
from datetime import date, timedelta
def days(k):
    out = set()
    for d in ev.get(k) or []:
        s = d if isinstance(d, str) else d.get("date")
        if s: out.add(date.fromisoformat(s[:10]))
    return out
E = {k: days(k) for k in ("FOMC", "CPI", "NFP")}
rows = cycles(24)
def cyc_event(r):
    d = datetime.fromtimestamp(r["expiry"] / 1000, tz=timezone.utc).date() - timedelta(days=1)
    return [k for k, s in E.items() if d in s]
def st(sub):
    z = [abs(r["z"]) for r in sub]
    if not z: return {"n": 0}
    return {"n": len(z), "rms": round(math.sqrt(sum(v*v for v in z)/len(z)), 3), "|z|>1": round(sum(v > 1 for v in z)/len(z), 3),
            "median|z|": round(statistics.median(z), 3), "touch_any_1σ": round(sum(max(-r["down"], r["up"]) >= 1 for r in sub)/len(sub), 3)}
res = {k: st([r for r in rows if k in cyc_event(r)]) for k in E}
res["any"] = st([r for r in rows if cyc_event(r)])
res["none_weekday"] = st([r for r in rows if not cyc_event(r) and r["dow_expiry"] in (1, 2, 3, 4)])
res["all"] = st(rows)
print({k: len(v) for k, v in E.items()})
print(json.dumps(res, ensure_ascii=False, indent=1))
