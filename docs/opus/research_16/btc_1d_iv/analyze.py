"""Re-test of research note 15 with real Deribit 1-day ATM IV (from build_iv.py) instead of DVOL.
Predictors use data at or before entry, except the IV window itself (entry +-45 min, see look-ahead note)."""
import array, bisect, csv, io, json, math, os, statistics as st, sys, zipfile
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, r"C:\Users\Xu\Documents\中性回路整合工程\.worktrees\claude-astra\tools")
import astra_session_clock_v1 as sc  # noqa: E402
from build_iv import b76, ncdf  # noqa: E402

RAW = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\intraday_vol\raw"
DVOL = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\intraday_vol\dvol_1h.json"
IVF = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\research_btc_1d_iv\btc_1d_atm_iv.json"
CACHE = os.path.join(HERE, "closes.bin")
MIN, H = 60_000, 3_600_000
T0 = int(datetime(2024, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
IVKEY = os.environ.get("IVKEY", "atm_iv")
HALVES = ("H1 24-10..25-03", "H2 25-04..25-09", "H3 25-10..26-03", "H4 26-04..26-09")


def load_closes():
    if os.path.exists(CACHE):
        a = array.array("d")
        a.frombytes(open(CACHE, "rb").read())
        return a
    n = int((datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp() * 1000 - T0) // MIN)
    a = array.array("d", [math.nan]) * n
    for name in sorted(os.listdir(RAW)):
        if name.endswith(".zip"):
            z = zipfile.ZipFile(os.path.join(RAW, name))
            for row in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), "ascii")):
                t = int(row[0])
                t = t // 1000 if t > 10 ** 14 else t
                i = (t - T0) // MIN
                if 0 <= i < n:
                    a[i] = float(row[4])
    last = math.nan
    for i in range(n):
        if math.isnan(a[i]):
            a[i] = last
        else:
            last = a[i]
    open(CACHE, "wb").write(a.tobytes())
    return a


C = load_closes()
DV = json.load(open(DVOL))["points"]
DV_T = [p[0] for p in DV]
DV_V = [p[1] for p in DV]


def px(ms):
    i = (ms - T0) // MIN
    return C[i] if 0 <= i < len(C) else math.nan


def dvol(ms):
    i = bisect.bisect_right(DV_T, ms) - 1
    return DV_V[i] if i >= 0 and ms - DV_T[i] <= 2 * H else None


def dvol_rank(ms, hours=168):
    now = dvol(ms)
    i = bisect.bisect_right(DV_T, ms)
    w = DV_V[max(0, i - hours):i]
    if now is None or len(w) < hours * 0.8:
        return None
    return sum(v < now for v in w) / len(w)


def rv(ms, hours=24):
    s, n = 0.0, 0
    for k in range(hours * 12):
        a, b = px(ms - (k + 1) * 5 * MIN), px(ms - k * 5 * MIN)
        if math.isnan(a) or math.isnan(b):
            return None
        s += math.log(b / a) ** 2
        n += 1
    return math.sqrt(s / (n * 5 / 60) * 8760) * 100


def bdelta(F, K, T, sig, call):
    s = sig * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * s * s) / s
    return ncdf(d1) if call else ncdf(d1) - 1


def smile_iv(r, m_pct):
    """Linear interpolation of the window's OTM smile (0.5% bins, median trade IV) at moneyness m (%),
    ATM point = atm_iv, flat beyond the outermost populated bin on that side; None if that side is empty."""
    pts = {0.0: r[IVKEY]}
    for b, (v, n) in r["smile"].items():
        if float(b) != 0.0:
            pts[float(b)] = v
    xs = sorted(pts)
    if m_pct <= xs[0]:
        return pts[xs[0]] if xs[0] < 0 else None
    if m_pct >= xs[-1]:
        return pts[xs[-1]] if xs[-1] > 0 else None
    j = bisect.bisect_right(xs, m_pct)
    x0, x1 = xs[j - 1], xs[j]
    return pts[x0] + (pts[x1] - pts[x0]) * (m_pct - x0) / (x1 - x0)


def vertical(F, ST, T, sig_fn, side, width=2000.0, grid=500.0, target=0.35):
    """Short |Black delta|~target strike on a 500 grid, long leg `width` further OTM, 1 BTC, coin-settled."""
    call = side == "call"
    best = None
    k0 = round(F / grid) * grid
    for j in range(-40, 41):
        K = k0 + j * grid
        sig = sig_fn(K)
        if K <= 0 or sig is None:
            continue
        dl = abs(bdelta(F, K, T, sig / 100, call))
        if best is None or abs(dl - target) < abs(best[1] - target):
            best = (K, dl)
    if best is None:
        return None
    Ks = best[0]
    Kl = Ks + width if call else Ks - width
    s1, s2 = sig_fn(Ks), sig_fn(Kl)
    if s1 is None or s2 is None:
        return None
    credit = (b76(F, Ks, T, s1 / 100, call) - b76(F, Kl, T, s2 / 100, call)) / F
    pay = (max(0, ST - Ks) - max(0, ST - Kl)) if call else (max(0, Ks - ST) - max(0, Kl - ST))
    return {"K_short": Ks, "delta": best[1], "credit_btc": credit, "pnl_btc": credit - pay / ST}


def half_of(expiry):
    d = datetime.fromisoformat(expiry)
    return HALVES[min(3, ((d.year - 2024) * 12 + d.month - 10) // 6)]


def build():
    ivrows = json.load(open(IVF, encoding="utf-8"))["rows"]
    by = {(r["expiry"], r["entry_hour_utc"]): r for r in ivrows}
    out = []
    for r in ivrows:
        expiry = int(datetime.fromisoformat(r["expiry"]).replace(hour=8, tzinfo=timezone.utc).timestamp() * 1000)
        hb = r["hours_to_expiry"]
        entry = expiry - hb * H
        x = {"expiry": r["expiry"], "hb": hb, "half": half_of(r["expiry"]), "iv": r[IVKEY], "iv_mark": r["atm_iv_mark"],
             "skew": r["skew_put_minus_call"], "atm_n": r["atm_n"]}
        s0, s1 = px(entry), px(expiry)
        twap = st.mean(px(expiry - (30 - k) * MIN) for k in range(30))   # minute closes 07:30..07:59 (close ~07:31..08:00)
        x.update(s0=s0, s1=s1, twap=twap, dvol=dvol(entry), dvol_prev=dvol(entry - 24 * H),
                 dvol_rank7=dvol_rank(entry), rv24=rv(entry, 24))
        if x["iv"] is None or math.isnan(s0) or math.isnan(s1) or math.isnan(twap):
            out.append(x)
            continue
        T = hb / 8760
        varh = sc.variance_between(entry, expiry)
        lr = math.log(s1 / s0)
        x["z"] = lr / (x["iv"] / 100 * math.sqrt(T))
        x["z_clock"] = lr / (x["iv"] / 100 * math.sqrt(varh / 8760))
        if x["dvol"]:
            x["z_dvol"] = lr / (x["dvol"] / 100 * math.sqrt(varh / 8760))
        prev = [by.get(((datetime.fromisoformat(r["expiry"]) - timedelta(days=k)).strftime("%Y-%m-%d"), r["entry_hour_utc"]))
                for k in range(1, 8)]
        pv = [p[IVKEY] for p in prev if p and p[IVKEY] is not None]
        x["iv_rank7"] = sum(v < x["iv"] for v in pv) / len(pv) if len(pv) >= 5 else None
        x["iv_chg"] = (x["iv"] - prev[0][IVKEY]) if prev[0] and prev[0][IVKEY] is not None else None
        x["iv_minus_rv24"] = x["iv"] - x["rv24"] if x["rv24"] else None
        x["dvol_chg"] = x["dvol"] - x["dvol_prev"] if x["dvol"] and x["dvol_prev"] else None
        x["dvol_minus_rv24"] = x["dvol"] - x["rv24"] if x["dvol"] and x["rv24"] else None
        x["iv_over_dvol"] = x["iv"] / x["dvol"] if x["dvol"] else None
        F, ST, sig = s0, twap, x["iv"] / 100
        strad = (b76(F, F, T, sig, True) + b76(F, F, T, sig, False)) / F
        x["straddle_credit"] = strad
        x["straddle_pnl"] = strad - abs(ST - F) / ST
        for side in ("put", "call"):
            v = vertical(F, ST, T, lambda K: x["iv"], side)
            x[f"{side}_flat"], x[f"{side}_flat_credit"], x[f"{side}_delta"] = v["pnl_btc"], v["credit_btc"], v["delta"]
            vs = vertical(F, ST, T, lambda K: smile_iv(r, (K / F - 1) * 100), side) if r["smile"] else None
            x[f"{side}_skew"] = vs["pnl_btc"] if vs else None
            x[f"{side}_skew_credit"] = vs["credit_btc"] if vs else None
        x["both_flat"] = x["put_flat"] + x["call_flat"]
        x["both_skew"] = x["put_skew"] + x["call_skew"] if x["put_skew"] is not None and x["call_skew"] is not None else None
        out.append(x)
    return out


def smed(v, nd):
    v = list(v)
    return round(st.median(v), nd) if v else None


def pnl_stats(vals):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n < 2:
        return {"n": n}
    s = sorted(vals)
    return {"n": n, "mean": round(st.mean(vals), 5), "median": round(st.median(vals), 5),
            "hit": round(sum(v > 0 for v in vals) / n, 3), "worst_decile_mean": round(st.mean(s[:max(1, n // 10)]), 5),
            "se": round(st.stdev(vals) / math.sqrt(n), 5)}


def clustered(rows, key):
    d = {}
    for r in rows:
        if r.get(key) is not None:
            d.setdefault(r["expiry"], []).append(r[key])
    m = [st.mean(v) for v in d.values()]
    allv = [v for vs in d.values() for v in vs]
    return {"n_obs": len(allv), "n_days": len(m), "mean": round(st.mean(allv), 5),
            "se_day_clustered": round(st.stdev(m) / math.sqrt(len(m)), 5)}


def vrp(rows, zkey="z"):
    z = [r[zkey] for r in rows if r.get(zkey) is not None]
    if not z:
        return {"n": 0}
    return {"n": len(z), "rv_over_iv": round(math.sqrt(sum(v * v for v in z) / len(z)), 3),
            "share_abs_z_gt1": round(sum(abs(v) > 1 for v in z) / len(z), 3),
            "median_abs_z": round(st.median(abs(v) for v in z), 3)}


def group_report(rows, zkey="z"):
    return {**vrp(rows, zkey), "put_flat": pnl_stats([r["put_flat"] for r in rows]),
            "call_flat": pnl_stats([r["call_flat"] for r in rows]), "both_flat": pnl_stats([r["both_flat"] for r in rows]),
            "both_skew": pnl_stats([r.get("both_skew") for r in rows])}


def signal_groups(rows, key, mode, zkey="z"):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if mode == "rank":
        lo, hi = 0.2, 0.8
    else:
        q = st.quantiles(vals, n=5)
        lo, hi = q[0], q[3]
    lab = lambda v: "1 low (<p20)" if v < lo else ("3 high (>=p80)" if v >= hi else "2 mid")  # noqa: E731
    g = {}
    for r in rows:
        if r.get(key) is not None:
            g.setdefault(lab(r[key]), []).append(r)
    return {"cuts": [round(lo, 3), round(hi, 3)], "groups": {k: group_report(v, zkey) for k, v in sorted(g.items())},
            "by_half": {k: {h: {**vrp([r for r in v if r["half"] == h], zkey),
                                "both_flat_mean": pnl_stats([r["both_flat"] for r in v if r["half"] == h]).get("mean")}
                            for h in HALVES} for k, v in sorted(g.items())}}


def main():
    rows = build()
    ok = [r for r in rows if r.get("z") is not None]
    rep = {"notes": {
        "pnl_units": "BTC per 1 BTC notional, no fees, Black-76 with calendar T=hours/8760, F=Binance entry close; settle=Binance 07:30-08:00 1m-close TWAP",
        "z": "z=log(S_08:00/S_entry)/(IV*sqrt(T_cal)) (primary); z_clock uses variance_between hours instead of T_cal; z_dvol = DVOL with clock (as note 15)",
        "lookahead": "IV window is entry +-45 min, so up to 45 min of post-entry trades enter the IV; z and P&L run from the entry minute"}}
    cov = {}
    for hb in (24, 18, 16):
        sub = [r for r in rows if r["hb"] == hb]
        cov[f"{hb}h"] = {"windows": len(sub), "with_atm_iv": sum(r["iv"] is not None for r in sub),
                         "usable": sum(r.get("z") is not None for r in sub),
                         "with_skew": sum(r["skew"] is not None for r in sub),
                         "median_atm_trades": st.median(r["atm_n"] for r in sub),
                         "p10_atm_trades": st.quantiles([r["atm_n"] for r in sub], n=10)[0],
                         "missing": [r["expiry"] for r in sub if r["iv"] is None]}
    rep["coverage"] = cov
    rq = {}
    for r in ok:
        if r.get("iv_over_dvol"):
            rq.setdefault(r["expiry"][:7], {}).setdefault(r["hb"], []).append(r["iv_over_dvol"])
    rep["a_iv_over_dvol_monthly_median"] = {m: {f"{hb}h": round(st.median(v), 3) for hb, v in sorted(d.items())} for m, d in sorted(rq.items())}
    rep["a_by_half"] = {h: {f"{hb}h": {
        "iv_med": smed([r["iv"] for r in ok if r["half"] == h and r["hb"] == hb], 1),
        "mark_iv_med": smed([r["iv_mark"] for r in ok if r["half"] == h and r["hb"] == hb and r["iv_mark"]], 1),
        "dvol_med": smed([r["dvol"] for r in ok if r["half"] == h and r["hb"] == hb and r["dvol"]], 1),
        "iv_over_dvol_med": smed([r["iv_over_dvol"] for r in ok if r["half"] == h and r["hb"] == hb and r.get("iv_over_dvol")], 3),
        "skew_med": smed([r["skew"] for r in ok if r["half"] == h and r["hb"] == hb and r["skew"] is not None], 2)}
        for hb in (24, 18, 16)} for h in HALVES}
    b = {}
    pk = ("straddle_pnl", "put_flat", "call_flat", "both_flat", "put_skew", "call_skew", "both_skew")
    for hb in (24, 18, 16):
        sub = [r for r in ok if r["hb"] == hb]
        b[f"{hb}h"] = {"all": {"z_direct": vrp(sub), "z_clock": vrp(sub, "z_clock"), "z_dvol_clock": vrp(sub, "z_dvol"),
                               **{k: pnl_stats([r[k] for r in sub]) for k in pk},
                               "mean_credit": {k: round(st.mean([r[k] for r in sub if r.get(k) is not None]), 5)
                                               for k in ("straddle_credit", "put_flat_credit", "call_flat_credit", "put_skew_credit", "call_skew_credit")},
                               "mean_short_delta": {s: round(st.mean(r[f"{s}_delta"] for r in sub), 3) for s in ("put", "call")}},
                       "by_half": {h: {"z_direct": vrp([r for r in sub if r["half"] == h]),
                                       "z_clock": vrp([r for r in sub if r["half"] == h], "z_clock"),
                                       **{k: pnl_stats([r[k] for r in sub if r["half"] == h]) for k in ("straddle_pnl", "put_flat", "call_flat", "both_flat", "both_skew")}}
                                   for h in HALVES}}
    b["pooled_3_entries_day_clustered"] = {k: clustered(ok, k) for k in pk}
    rep["b_vrp_pnl"] = b
    c = {}
    for hb in (24, 18, 16):
        sub = [r for r in ok if r["hb"] == hb and r.get("dvol_rank7") is not None and r.get("dvol_chg") is not None
               and r.get("iv_rank7") is not None and r.get("iv_chg") is not None and r.get("iv_minus_rv24") is not None
               and r.get("dvol_minus_rv24") is not None]
        c[f"{hb}h"] = {"n_common": len(sub),
                       "iv_rank7": signal_groups(sub, "iv_rank7", "rank"),
                       "iv_chg_vs_prev_day": signal_groups(sub, "iv_chg", "q"),
                       "iv_minus_rv24": signal_groups(sub, "iv_minus_rv24", "q"),
                       "dvol_rank7": signal_groups(sub, "dvol_rank7", "rank"),
                       "dvol_chg_24h": signal_groups(sub, "dvol_chg", "q"),
                       "dvol_minus_rv24": signal_groups(sub, "dvol_minus_rv24", "q"),
                       "dvol_rank7__z_dvol": signal_groups(sub, "dvol_rank7", "rank", "z_dvol"),
                       "dvol_chg_24h__z_dvol": signal_groups(sub, "dvol_chg", "q", "z_dvol"),
                       "dvol_minus_rv24__z_dvol": signal_groups(sub, "dvol_minus_rv24", "q", "z_dvol")}
        q_chg = st.quantiles([r["iv_chg"] for r in sub], n=5)[3]
        q_vr = st.quantiles([r["iv_minus_rv24"] for r in sub], n=5)[0]
        g = {}
        for r in sub:
            k = (r["iv_rank7"] >= 0.8) + (r["iv_chg"] >= q_chg) + (r["iv_minus_rv24"] < q_vr)
            g.setdefault(str(k), []).append(r)
        c[f"{hb}h"]["iv_signal_count"] = {k: group_report(v) for k, v in sorted(g.items())}
    rep["c_signals"] = c
    d = {}
    for hb in (24, 18, 16):
        sub = [r for r in ok if r["hb"] == hb]
        q = st.quantiles([r["iv"] for r in sub], n=5)
        g = {}
        for r in sub:
            g.setdefault(sum(r["iv"] >= c_ for c_ in q), []).append(r)
        d[f"{hb}h"] = {"cuts": [round(v, 1) for v in q],
                       "quintiles": {f"Q{k + 1}": {**vrp(v), "mean_credit_put": round(st.mean(r["put_flat_credit"] for r in v), 5),
                                                    "mean_credit_call": round(st.mean(r["call_flat_credit"] for r in v), 5),
                                                    "put_flat": pnl_stats([r["put_flat"] for r in v]),
                                                    "call_flat": pnl_stats([r["call_flat"] for r in v]),
                                                    "both_flat": pnl_stats([r["both_flat"] for r in v]),
                                                    "both_skew": pnl_stats([r.get("both_skew") for r in v])}
                                     for k, v in sorted(g.items())}}
    rep["d_iv_level_quintiles"] = d
    json.dump(rep, open(os.path.join(HERE, ("analysis_1d_iv.json" if IVKEY == "atm_iv" else f"analysis_1d_iv__{IVKEY}.json")), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    keep = ("expiry", "hb", "half", "iv", "iv_mark", "skew", "dvol", "rv24", "iv_rank7", "iv_chg", "iv_minus_rv24", "dvol_rank7",
            "dvol_chg", "s0", "s1", "twap", "z", "z_clock", "z_dvol", "straddle_pnl", "put_flat", "call_flat", "put_skew", "call_skew")
    with open(os.path.join(HERE, ("rows_1d_iv.csv" if IVKEY == "atm_iv" else f"rows_1d_iv__{IVKEY}.csv")), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(keep)
        for r in rows:
            w.writerow([r.get(k) for k in keep])
    print("rows", len(rows), "usable", len(ok))


if __name__ == "__main__":
    main()
