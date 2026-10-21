"""Entry-hour scan for the 08:00 UTC daily vertical (|delta|~0.35 short, 2,000 wide, 1 BTC, coin-settled, hold to expiry).
IVKEY=atm_iv (default) or atm_iv_pre (pre-entry trades only). Output hours_results[__atm_iv_pre].json.
P&L in BTC per 1 BTC notional; reported x1e-4 BTC in summaries.
Cost model (ASSUMPTIONS): entry fee 0.0003 BTC per leg capped at 12.5% of that leg's price; no delivery fee;
half-spread per leg = that entry hour's median effective half-spread (signed trade price - mark by aggressor side,
non-block trades) in the short-leg zone (0.25-2% OTM) and long-leg zone (2-5% OTM); held to expiry, so no exit cost.
"""
import json, math, os, statistics as st, sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
IVKEY = os.environ.get("IVKEY", "atm_iv")
os.environ["IVKEY"] = IVKEY
import analyze as A  # noqa: E402  (closes, px, bdelta, smile_iv, b76)
sys.path.insert(1, r"C:\Users\Xu\Documents\中性回路整合工程\.worktrees\claude-astra\tools")
import astra_session_clock_v1 as sc  # noqa: E402

IVF = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\research_btc_1d_iv\btc_1d_atm_iv_allhours.json"
H, MIN = 3_600_000, 60_000
HOURS = (8, 10, 12, 14, 16, 18, 20, 22, 0, 2, 4)
HB = {h: ((8 - h) % 24) or 24 for h in HOURS}
FEE, FEE_CAP, WIDTH, GRID = 0.0003, 0.125, 2000.0, 500.0
GROUPS = {"A 24h (08)": (8,), "B 16-22h (10,12,14,16)": (10, 12, 14, 16), "C 10-14h (18,20,22)": (18, 20, 22),
          "D 4-8h (00,02,04)": (0, 2, 4)}


def SM(v):
    v = list(v)
    return st.mean(v) if v else float("nan")


def SE(v):
    v = list(v)
    return st.stdev(v) / math.sqrt(len(v)) if len(v) > 1 else float("nan")


def vert(F, ST, T, sig_fn, call):
    best = None
    k0 = round(F / GRID) * GRID
    for j in range(-40, 41):
        K = k0 + j * GRID
        s = sig_fn(K)
        if K <= 0 or s is None:
            continue
        dl = abs(A.bdelta(F, K, T, s / 100, call))
        if best is None or abs(dl - 0.35) < abs(best[1] - 0.35):
            best = (K, dl)
    if best is None:
        return None
    Ks = best[0]
    Kl = Ks + WIDTH if call else Ks - WIDTH
    s1, s2 = sig_fn(Ks), sig_fn(Kl)
    if s1 is None or s2 is None:
        return None
    ps = A.b76(F, Ks, T, s1 / 100, call) / F
    pl = A.b76(F, Kl, T, s2 / 100, call) / F
    pay = (max(0, ST - Ks) - max(0, ST - Kl)) if call else (max(0, Ks - ST) - max(0, Kl - ST))
    return {"ps": ps, "pl": pl, "credit": ps - pl, "pnl": ps - pl - pay / ST, "maxloss": WIDTH / F - (ps - pl)}


def main():
    ivrows = json.load(open(IVF, encoding="utf-8"))["rows"]
    # per-hour effective half-spread (median of window medians), BTC
    hs = {}
    for h in HOURS:
        v = [r for r in ivrows if r["entry_hour_utc"] == h]
        hs[h] = {"short": st.median([r["hs_short_btc"] for r in v if r["hs_short_btc"] is not None]),
                 "long": st.median([r["hs_long_btc"] for r in v if r["hs_long_btc"] is not None]),
                 "short_rel_to_mark": st.median([r["hs_short_rel"] for r in v if r["hs_short_rel"] is not None]),
                 "windows_short": sum(r["hs_short_btc"] is not None for r in v),
                 "windows_long": sum(r["hs_long_btc"] is not None for r in v)}
    rows = []
    for r in ivrows:
        h = r["entry_hour_utc"]
        if h not in HB:
            continue
        hb = HB[h]
        expiry = int(datetime.fromisoformat(r["expiry"]).replace(hour=8, tzinfo=timezone.utc).timestamp() * 1000)
        entry = expiry - hb * H
        x = {"expiry": r["expiry"], "h": h, "hb": hb, "half": A.half_of(r["expiry"]), "iv": r[IVKEY]}
        s0, s1 = A.px(entry), A.px(expiry)
        twap = st.mean(A.px(expiry - (30 - k) * MIN) for k in range(30))
        if x["iv"] is None or any(math.isnan(v) for v in (s0, s1, twap)):
            rows.append(x)
            continue
        T = hb / 8760
        lr = math.log(s1 / s0)
        x.update(s0=s0, lr=lr, z=lr / (x["iv"] / 100 * math.sqrt(T)), clock_var=sc.variance_between(entry, expiry))
        res = {}
        for side, call in (("put", False), ("call", True)):
            v = vert(s0, twap, T, lambda K: x["iv"], call)
            vs = vert(s0, twap, T, lambda K: A.smile_iv(r, (K / s0 - 1) * 100), call) if r["smile"] else None
            cost = (min(FEE, FEE_CAP * v["ps"]) + min(FEE, FEE_CAP * v["pl"]) + hs[h]["short"] + hs[h]["long"])
            tick = lambda pr: 0.00025 if pr >= 0.005 else 0.00005   # half of an assumed Deribit tick (0.0005 >=0.005 BTC, else 0.0001)
            cost_cons = (min(FEE, FEE_CAP * v["ps"]) + min(FEE, FEE_CAP * v["pl"]) + max(hs[h]["short"], tick(v["ps"]))
                         + max(hs[h]["long"], tick(v["pl"])))
            x[f"{side}_net_cons"] = v["pnl"] - cost_cons
            x[f"{side}_cost_cons"] = cost_cons
            res[side] = v
            x[f"{side}_flat"] = v["pnl"]
            x[f"{side}_credit"] = v["credit"]
            x[f"{side}_maxloss"] = v["maxloss"]
            x[f"{side}_cost"] = cost
            x[f"{side}_net"] = v["pnl"] - cost
            x[f"{side}_skew"] = vs["pnl"] if vs else None
            x[f"{side}_skew_net"] = (vs["pnl"] - (min(FEE, FEE_CAP * vs["ps"]) + min(FEE, FEE_CAP * vs["pl"]) + hs[h]["short"] + hs[h]["long"])) if vs else None
        for k in ("flat", "credit", "maxloss", "cost", "net", "net_cons", "cost_cons"):
            x[f"both_{k}"] = x[f"put_{k}"] + x[f"call_{k}"]
        x["both_skew"] = x["put_skew"] + x["call_skew"] if x["put_skew"] is not None and x["call_skew"] is not None else None
        x["both_skew_net"] = x["put_skew_net"] + x["call_skew_net"] if x["put_skew_net"] is not None and x["call_skew_net"] is not None else None
        rows.append(x)
    ok = [r for r in rows if r.get("z") is not None]
    byh = {h: [r for r in ok if r["h"] == h] for h in HOURS}
    cell = {(r["expiry"], r["h"]): r for r in ok}
    days = sorted({r["expiry"] for r in ok})
    rep = {"ivkey": IVKEY, "assumptions": __doc__, "half_spread_by_hour_btc": {f"{h:02d}": hs[h] for h in HOURS},
           "coverage": {f"{h:02d}": {"windows": sum(r["entry_hour_utc"] == h for r in ivrows),
                                     "with_iv": sum(r["entry_hour_utc"] == h and r[IVKEY] is not None for r in ivrows),
                                     "usable": len(byh[h]),
                                     "fallback_mark3pct_available_when_missing": sum(r["entry_hour_utc"] == h and r[IVKEY] is None and r["atm_iv_fb_mark3pct"] is not None for r in ivrows),
                                     "median_atm_trades": st.median(r["atm_n"] for r in ivrows if r["entry_hour_utc"] == h),
                                     "p10_atm_trades": st.quantiles([r["atm_n"] for r in ivrows if r["entry_hour_utc"] == h], n=10)[0]}
                        for h in HOURS}}
    # 2. per-hour table
    per = {}
    for h in HOURS:
        v = byh[h]
        z = [r["z"] for r in v]
        def S(k):
            vals = [r[k] for r in v if r.get(k) is not None]
            s = sorted(vals)
            return {"n": len(vals), "mean": st.mean(vals), "se": st.stdev(vals) / math.sqrt(len(vals)), "median": st.median(vals),
                    "hit": sum(t > 0 for t in vals) / len(vals), "worst_decile": st.mean(s[:len(s) // 10])}
        per[f"{h:02d}"] = {"hb": HB[h], "n": len(v), "rv_over_iv": math.sqrt(st.mean(t * t for t in z)),
                           "share_abs_z_gt1": sum(abs(t) > 1 for t in z) / len(z),
                           "median_iv": st.median(r["iv"] for r in v),
                           **{k: S(k) for k in ("both_flat", "both_skew", "put_flat", "call_flat", "both_net", "both_skew_net", "both_net_cons")},
                           "mean_cost_cons_both": st.mean(r["both_cost_cons"] for r in v),
                           "mean_credit_both": st.mean(r["both_credit"] for r in v),
                           "mean_cost_both": st.mean(r["both_cost"] for r in v),
                           "pnl_per_credit": st.mean(r["both_flat"] for r in v) / st.mean(r["both_credit"] for r in v),
                           "pnl_per_maxloss": st.mean(r["both_flat"] for r in v) / st.mean(r["both_maxloss"] for r in v),
                           "net_per_maxloss": st.mean(r["both_net"] for r in v) / st.mean(r["both_maxloss"] for r in v),
                           "net_cons_per_maxloss": st.mean(r["both_net_cons"] for r in v) / st.mean(r["both_maxloss"] for r in v),
                           "by_half": {hf: {"n": len([r for r in v if r["half"] == hf]),
                                            "both_flat": SM([r["both_flat"] for r in v if r["half"] == hf]),
                                            "both_net": SM([r["both_net"] for r in v if r["half"] == hf]),
                                            "rv_over_iv": math.sqrt(SM([r["z"] ** 2 for r in v if r["half"] == hf]))}
                                       for hf in A.HALVES}}
    rep["per_hour"] = per
    # 3a. paired differences vs 08:00 (same expiry day)
    def paired(fa, fb, key):
        d = []
        for day in days:
            a, b = fa(day, key), fb(day, key)
            if a is not None and b is not None:
                d.append(a - b)
        return {"n_days": len(d), "mean": st.mean(d), "se": st.stdev(d) / math.sqrt(len(d)),
                "t": st.mean(d) / (st.stdev(d) / math.sqrt(len(d)))}
    one = lambda h: (lambda day, key: (cell.get((day, h)) or {}).get(key))  # noqa: E731

    def grp(hs_):
        def f(day, key):
            v = [cell[(day, h)][key] for h in hs_ if (day, h) in cell and cell[(day, h)].get(key) is not None]
            return st.mean(v) if len(v) == len(hs_) else None   # require all hours in the group (balanced)
        return f
    keys = ("both_flat", "both_skew", "both_net", "both_skew_net", "both_net_cons")
    rep["paired_vs_08"] = {f"{h:02d}": {k: paired(one(h), one(8), k) for k in keys} for h in HOURS if h != 8}
    G = {k: grp(v) for k, v in GROUPS.items()}
    gk = list(GROUPS)
    rep["group_contrasts"] = {f"{a} minus {b}": {k: paired(G[a], G[b], k) for k in keys}
                              for a, b in ((gk[1], gk[0]), (gk[2], gk[0]), (gk[3], gk[0]), (gk[3], gk[1]), (gk[2], gk[1]))}
    rep["group_contrasts_by_half"] = {}
    for a, b in ((gk[1], gk[0]), (gk[2], gk[0]), (gk[3], gk[0]), (gk[3], gk[1])):
        out = {}
        for hf in A.HALVES:
            d = [G[a](day, "both_net") - G[b](day, "both_net") for day in days
                 if A.half_of(day) == hf and G[a](day, "both_net") is not None and G[b](day, "both_net") is not None]
            out[hf] = {"n": len(d), "mean": SM(d), "se": SE(d)}
        rep["group_contrasts_by_half"][f"{a} minus {b} (both_net)"] = out
    # trend: within-day OLS slope of pnl on hours-before-expiry (per 1 hour), averaged across days with >=9 hours
    for key in ("both_flat", "both_net"):
        sl = []
        for day in days:
            pts = [(HB[h], cell[(day, h)][key]) for h in HOURS if (day, h) in cell]
            if len(pts) >= 9:
                mx = st.mean(p for p, _ in pts); my = st.mean(q for _, q in pts)
                sl.append(sum((p - mx) * (q - my) for p, q in pts) / sum((p - mx) ** 2 for p, _ in pts))
        rep[f"trend_slope_per_hour_before_expiry__{key}"] = {"n_days": len(sl), "mean": st.mean(sl), "se": st.stdev(sl) / math.sqrt(len(sl))}
    rep["multiple_comparisons_note"] = ("10 paired hour-vs-08 tests: Bonferroni 5% family-wise needs |t|>2.81; "
                                        "4-5 pre-chosen group contrasts need |t|>2.5-2.6.")
    # 5. mechanism: IV and implied vs realized remaining move by hour
    full = {r["expiry"]: r for r in byh[8]}
    mech = {}
    for h in HOURS:
        v = byh[h]
        pr = [r for r in v if r["expiry"] in full]
        mech[f"{h:02d}"] = {
            "hb": HB[h], "median_iv": st.median(r["iv"] for r in v),
            "median_iv_ratio_vs_same_day_08": st.median(r["iv"] / full[r["expiry"]]["iv"] for r in pr),
            "implied_move_pct_median": st.median(r["iv"] / 100 * math.sqrt(HB[h] / 8760) * 100 for r in v),
            "realized_move_pct_rms": math.sqrt(st.mean(r["lr"] ** 2 for r in v)) * 100,
            "implied_var_share_of_08": st.mean((r["iv"] ** 2 * HB[h]) for r in pr) / st.mean(full[r["expiry"]]["iv"] ** 2 * 24 for r in pr),
            "realized_var_share_of_08": st.mean(r["lr"] ** 2 for r in pr) / st.mean(full[r["expiry"]]["lr"] ** 2 for r in pr),
            "clock_var_share_of_08": st.mean(r["clock_var"] for r in pr) / st.mean(full[r["expiry"]]["clock_var"] for r in pr),
            "calendar_share": HB[h] / 24}
    rep["mechanism"] = mech
    suf = "" if IVKEY == "atm_iv" else f"__{IVKEY}"
    json.dump(rep, open(os.path.join(HERE, f"hours_results{suf}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # summary text
    L = []
    f4 = lambda v: f"{v * 1e4:+.1f}"  # noqa: E731
    L.append(f"IVKEY={IVKEY}")
    L.append("hour hb  n   cov  medIV rv/iv |z|>1 | both_flat±se  skew   put   call | credit cost | net±se  skew_net | hit  worst10 | /credit /maxloss net/maxloss")
    for h in HOURS:
        p, c = per[f"{h:02d}"], rep["coverage"][f"{h:02d}"]
        L.append(f"{h:02d}  {p['hb']:2d} {p['n']:3d} {c['with_iv']:3d}/{c['windows']} {p['median_iv']:5.1f} {p['rv_over_iv']:.3f} {p['share_abs_z_gt1']:.3f} | "
                 f"{f4(p['both_flat']['mean'])}±{p['both_flat']['se'] * 1e4:.1f} {f4(p['both_skew']['mean'])} {f4(p['put_flat']['mean'])} {f4(p['call_flat']['mean'])} | "
                 f"{p['mean_credit_both'] * 1e4:.1f} {p['mean_cost_both'] * 1e4:.1f} | {f4(p['both_net']['mean'])}±{p['both_net']['se'] * 1e4:.1f} {f4(p['both_skew_net']['mean'])} | "
                 f"{p['both_flat']['hit']:.3f} {f4(p['both_flat']['worst_decile'])} | {p['pnl_per_credit']:+.3f} {p['pnl_per_maxloss']:+.4f} {p['net_per_maxloss']:+.4f}")
    L.append("\nconservative cost (half-spread >= half an assumed tick): hour cost_cons net_cons±se net_cons/maxloss")
    for h in HOURS:
        p = per[f"{h:02d}"]
        L.append(f"  {h:02d} {p['mean_cost_cons_both'] * 1e4:.1f} {f4(p['both_net_cons']['mean'])}±{p['both_net_cons']['se'] * 1e4:.1f} {p['net_cons_per_maxloss']:+.4f}")
    L.append("\nhalf-spread (BTC x1e-4) short/long zone, short rel to mark:")
    L.append("  " + "  ".join(f"{h:02d}:{hs[h]['short'] * 1e4:.1f}/{hs[h]['long'] * 1e4:.1f}/{hs[h]['short_rel_to_mark']:.3f}" for h in HOURS))
    L.append("\npaired vs 08:00 (x1e-4): flat | skew | net | skew_net | net_cons  (mean±se, t)")
    for h, d in rep["paired_vs_08"].items():
        L.append(f"  {h} n={d['both_flat']['n_days']} " + " | ".join(f"{f4(d[k]['mean'])}±{d[k]['se'] * 1e4:.1f} (t={d[k]['t']:+.2f})" for k in keys))
    L.append("\ngroup contrasts (balanced days):")
    for k, d in rep["group_contrasts"].items():
        L.append(f"  {k}: n={d['both_flat']['n_days']} " + " | ".join(f"{kk} {f4(d[kk]['mean'])}±{d[kk]['se'] * 1e4:.1f} t={d[kk]['t']:+.2f}" for kk in keys))
    L.append("\ngroup contrasts by half (both_net):")
    for k, d in rep["group_contrasts_by_half"].items():
        L.append(f"  {k}: " + " ".join(f"{hf[:2]} {f4(v['mean'])}±{v['se'] * 1e4:.1f}(n{v['n']})" for hf, v in d.items()))
    for key in ("both_flat", "both_net"):
        t = rep[f"trend_slope_per_hour_before_expiry__{key}"]
        L.append(f"trend slope {key}: {t['mean'] * 1e4:+.3f}±{t['se'] * 1e4:.3f} per hour-before-expiry (n_days {t['n_days']}, t={t['mean'] / t['se']:+.2f})")
    L.append("\nper-hour by half (both_flat / both_net / rv_iv):")
    for h in HOURS:
        L.append(f"  {h:02d} " + " ".join(f"{hf[:2]} {f4(v['both_flat'])}/{f4(v['both_net'])}/{v['rv_over_iv']:.2f}" for hf, v in per[f"{h:02d}"]["by_half"].items()))
    L.append("\nmechanism: hour hb medIV IV/IV08 impl_move% real_move%(rms) impl_var_share real_var_share clock_var_share calendar_share")
    for h in HOURS:
        m = mech[f"{h:02d}"]
        L.append(f"  {h:02d} {m['hb']:2d} {m['median_iv']:5.1f} {m['median_iv_ratio_vs_same_day_08']:.3f} {m['implied_move_pct_median']:.2f} {m['realized_move_pct_rms']:.2f} "
                 f"{m['implied_var_share_of_08']:.3f} {m['realized_var_share_of_08']:.3f} {m['clock_var_share_of_08']:.3f} {m['calendar_share']:.3f}")
    txt = "\n".join(L)
    open(os.path.join(HERE, f"hours_summary{suf}.txt"), "w", encoding="utf-8").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
