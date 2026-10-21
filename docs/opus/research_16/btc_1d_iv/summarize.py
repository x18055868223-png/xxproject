"""Print compact tables from analysis_1d_iv.json."""
import json, os

R = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), __import__("sys").argv[1] if len(__import__("sys").argv) > 1 else "analysis_1d_iv.json"), encoding="utf-8"))
f = lambda v: "-" if v is None else (f"{v*1e4:+.1f}" if isinstance(v, float) else str(v))  # noqa: E731  bp of 1 BTC

print("COVERAGE", {k: {kk: (len(vv) if kk == "missing" else vv) for kk, vv in v.items()} for k, v in R["coverage"].items()})
print("missing", {k: v["missing"] for k, v in R["coverage"].items()})
print("\nA by half")
for h, d in R["a_by_half"].items():
    print(h, d)
for hb in ("24h", "18h", "16h"):
    a = R["b_vrp_pnl"][hb]["all"]
    print(f"\nB {hb} zdirect {a['z_direct']} zclock {a['z_clock']} zdvol {a['z_dvol_clock']}")
    print("  credit", a["mean_credit"], "delta", a["mean_short_delta"])
    for k in ("straddle_pnl", "put_flat", "call_flat", "both_flat", "put_skew", "call_skew", "both_skew"):
        s = a[k]
        print(f"  {k:13s} n={s['n']} mean={f(s['mean'])} med={f(s['median'])} hit={s['hit']} worst10={f(s['worst_decile_mean'])} se={f(s['se'])}")
    for h, d in R["b_vrp_pnl"][hb]["by_half"].items():
        print(f"  {h}: n={d['z_direct']['n']} rv/iv={d['z_direct'].get('rv_over_iv')} clk={d['z_clock'].get('rv_over_iv')} |z|>1={d['z_direct'].get('share_abs_z_gt1')}"
              f" strad={f(d['straddle_pnl'].get('mean'))} put={f(d['put_flat'].get('mean'))} call={f(d['call_flat'].get('mean'))}"
              f" both={f(d['both_flat'].get('mean'))}±{f(d['both_flat'].get('se'))} bothskew={f(d['both_skew'].get('mean'))}")
print("\npooled day-clustered", {k: (f(v["mean"]), f(v["se_day_clustered"]), v["n_days"]) for k, v in R["b_vrp_pnl"]["pooled_3_entries_day_clustered"].items()})
for hb in ("24h", "18h", "16h"):
    c = R["c_signals"][hb]
    print(f"\nC {hb} n_common={c['n_common']}")
    for sig in ("iv_rank7", "iv_chg_vs_prev_day", "iv_minus_rv24", "dvol_rank7", "dvol_chg_24h", "dvol_minus_rv24",
                "dvol_rank7__z_dvol", "dvol_chg_24h__z_dvol", "dvol_minus_rv24__z_dvol"):
        g = c[sig]
        print(f" {sig} cuts={g['cuts']}")
        for lab, s in g["groups"].items():
            print(f"   {lab:15s} n={s['n']} rv/iv={s['rv_over_iv']} |z|>1={s['share_abs_z_gt1']} put={f(s['put_flat']['mean'])} call={f(s['call_flat']['mean'])}"
                  f" both={f(s['both_flat']['mean'])}±{f(s['both_flat']['se'])} hit={s['both_flat']['hit']} worst10={f(s['both_flat']['worst_decile_mean'])}"
                  f" | halves |z|>1: " + " ".join(f"{v.get('share_abs_z_gt1')}({v['n']})" for v in g["by_half"][lab].values())
                  + " both: " + " ".join(f(v.get("both_flat_mean")) for v in g["by_half"][lab].values()))
    print(" count:")
    for k, s in c["iv_signal_count"].items():
        print(f"   {k} n={s['n']} rv/iv={s['rv_over_iv']} |z|>1={s['share_abs_z_gt1']} both={f(s['both_flat']['mean'])}±{f(s['both_flat']['se'])}")
for hb in ("24h", "18h", "16h"):
    d = R["d_iv_level_quintiles"][hb]
    print(f"\nD {hb} cuts {d['cuts']}")
    for q, s in d["quintiles"].items():
        print(f"  {q} n={s['n']} rv/iv={s['rv_over_iv']} |z|>1={s['share_abs_z_gt1']} credit p/c={f(s['mean_credit_put'])}/{f(s['mean_credit_call'])}"
              f" put={f(s['put_flat']['mean'])} call={f(s['call_flat']['mean'])} both={f(s['both_flat']['mean'])}±{f(s['both_flat']['se'])}"
              f" skew={f(s['both_skew'].get('mean'))} worst10={f(s['both_flat']['worst_decile_mean'])}")
