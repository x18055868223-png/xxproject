"""Validation of tools/astra_cross_asset_v1.py on downloaded history (design doc §5).

usage: python validate.py <data_dir>
data_dir holds h_<symbol>.json (Yahoo chart 1h, range=730d) and d_<symbol>.json
(1d, range=10y) for BTC-USD, QQQ, GC%3DF, DX-Y.NYB, as written by the probe step.
"""
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tools"))
import astra_cross_asset_v1 as xa  # noqa: E402

D = Path(sys.argv[1])
FILES = {"btc": "BTC-USD", "qqq": "QQQ", "gold": "GC%3DF", "dxy": "DX-Y.NYB"}
AX = ("qqq", "gold", "dxy")


def load(prefix):
    return {k: xa.parse_chart(json.loads((D / f"{prefix}_{v}.json").read_text(encoding="utf-8"))) for k, v in FILES.items()}


def daily_rows():
    # daily closes, BTC closes at 00:00 UTC (3-4 h after the US close): attenuated, history only
    bars = load("d")
    days = {}
    for k, series in bars.items():
        for ts, c in series:
            d = datetime.fromtimestamp(ts + (0 if k == "btc" else 6 * 3600), tz=timezone.utc).strftime("%Y-%m-%d")
            days.setdefault(d, {})[k] = c
    rows = [{"date": d, **v} for d, v in sorted(days.items()) if len(v) == 4]
    return xa.returns(rows, ("btc",) + AX)


def hourly_rows():
    return xa.returns(xa.align(load("h")), ("btc",) + AX)


def v1(rets, w, horizon=20):
    out = {}
    for k in AX:
        sig = hold = 0
        for t in range(w, len(rets) - horizon):
            st = xa.state(xa.corr([r["btc"] for r in rets[t - w:t]], [r[k] for r in rets[t - w:t]]), w)
            if st in ("positive", "negative"):
                sig += 1
                rf = xa.corr([r["btc"] for r in rets[t:t + horizon]], [r[k] for r in rets[t:t + horizon]])
                hold += (rf or 0) > 0 if st == "positive" else (rf or 0) < 0
        out[k] = f"{hold / sig:.0%} (n={sig})" if sig else "n=0"
    return out


def v3(rets, w, axes):
    sse = sst = 0.0
    for t in range(w, len(rets)):
        part = rets[t - w:t]
        fit = xa.ols([r["btc"] for r in part], [[r[k] for r in part] for k in axes])
        if not fit:
            continue
        e = rets[t]["btc"] - sum(b * rets[t][k] for b, k in zip(fit[0], axes))
        sse += e * e
        sst += rets[t]["btc"] ** 2
    return f"{1 - sse / sst:+.1%}"


def v3_main(rets, w):
    """Attribution with only the axes that are main references at that time."""
    sse = sst = 0.0
    for t in range(w + 1, len(rets)):
        saved, xa.LONG_WINDOW = xa.LONG_WINDOW, w
        axes = tuple(xa.assess(rets[:t])["verdict"]["main"])
        xa.LONG_WINDOW = saved
        pred = 0.0
        if axes:
            part = rets[t - w:t]
            fit = xa.ols([r["btc"] for r in part], [[r[k] for r in part] for k in axes])
            pred = sum(b * rets[t][k] for b, k in zip(fit[0], axes)) if fit else 0.0
        sse += (rets[t]["btc"] - pred) ** 2
        sst += rets[t]["btc"] ** 2
    return f"{1 - sse / sst:+.1%}"


def verdicts(rets, w, material):
    saved, xa.MATERIAL_R = xa.MATERIAL_R, material
    kinds, states = Counter(), Counter()
    for t in range(w + 1, len(rets) + 1):
        part = rets[t - w:t]
        for k in AX:
            states[xa.state(xa.corr([r["btc"] for r in part], [r[k] for r in part]), w)] += 1
        saved_long, xa.LONG_WINDOW = xa.LONG_WINDOW, w
        kinds[xa.assess(rets[:t])["verdict"]["kind"]] += 1
        xa.LONG_WINDOW = saved_long
    xa.MATERIAL_R = saved
    tot, stot = sum(kinds.values()), sum(states.values())
    return ({k: f"{v / tot:.0%}" for k, v in kinds.items()}, {k: f"{v / stot:.0%}" for k, v in states.items()})


h = hourly_rows()
print(f"hourly-aligned: {len(h)} returns {h[0]['date']}..{h[-1]['date']}")
for w in (40, 60):
    print(f" W={w} V1 sign-hold next 20d:", v1(h, w))
    print(f" W={w} V3 out-of-sample explained: qqq {v3(h, w, ('qqq',))}, all three {v3(h, w, AX)}, main only {v3_main(h, w)}")
for m in (0.3, 0.35, 0.4):
    kinds, states = verdicts(h, 60, m)
    print(f" W=60 material={m}: verdicts {kinds} axis states {states}")
d = daily_rows()
print(f"daily (attenuated): {len(d)} returns {d[0]['date']}..{d[-1]['date']}")
for year in ("2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026"):
    idx = [i for i, r in enumerate(d) if r["date"].startswith(year) and i >= 61]
    sub = Counter()
    for t in idx:
        sub[xa.assess(d[:t + 1])["verdict"]["kind"]] += 1
    tot = sum(sub.values())
    print(f" {year}: " + ", ".join(f"{k} {v / tot:.0%}" for k, v in sorted(sub.items())))
print("current:", json.dumps(xa.assess(h), ensure_ascii=False)[:1500])
