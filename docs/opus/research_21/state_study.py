"""Pre-registered state study, doc 21 (docs/opus/21_本期状态收束_事先登记_v0.1.md). Run: python docs/opus/research_21/state_study.py

Sample: each 08:00 UTC expiry cycle (2024-10..2026-09). Outcomes exactly as doc 17 appendix A
(research_17/hours_analyze.py, IVKEY=atm_iv): |delta|~0.35 short, 2,000 wide, hold to expiry, Black-76 at real 1-day ATM IV;
both_flat (primary), both_net (secondary); BTC per 1 BTC notional, reported x1e-4.
States use only data before the 08:00 entry:
  S1 = RV24 / RV7  (>=1 expansion, <1 contraction)
  S2 = IV_pre - RV24 (>=0 implied above realized, <0 implied below realized)
  RV = research_16 analyze.rv (5-min log returns from Binance 1m closes, annualized x sqrt(8760 h), in %),
       anchored at entry-1min so the newest close used is the 07:59 bar close (strictly before 08:00).
  IV_pre = atm_iv_pre (median ATM trade IV of trades at or before 08:00 in the [-45min, entry] window).
       The pricing IV atm_iv uses trades up to +45 min after entry, so it is NOT used for the state (doc 21 §3).
Tests (doc 21 §4): 4 state-vs-rest Welch t on both_flat at 08:00; 4 within-state paired (16:00 - 08:00) both_flat;
pass |t|>2.50. Robustness (§4.3) evaluated only for passing tests.
Note: hours_analyze.main() is not called because it rewrites research_17 outputs; its per-row loop is mirrored
below with its own vert()/constants and checked against research_17/hours_results.json.
"""
import json, math, os, statistics as st, sys, types

HERE = os.path.dirname(os.path.abspath(__file__))
R16 = os.path.normpath(os.path.join(HERE, "..", "research_16", "btc_1d_iv"))
R17 = os.path.normpath(os.path.join(HERE, "..", "research_17"))
ART = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\research_btc_1d_iv"
os.environ["IVKEY"] = "atm_iv"

# load research_16 analyze with its closes cache redirected outside the repo tree (same data, same code otherwise)
_src_path = os.path.join(R16, "analyze.py")
_src = open(_src_path, encoding="utf-8").read()
_old = 'CACHE = os.path.join(HERE, "closes.bin")'
assert _old in _src
_src = _src.replace(_old, "CACHE = " + repr(os.path.join(ART, "closes_1m_binance.bin")))
A = types.ModuleType("analyze")
A.__file__ = _src_path
sys.modules["analyze"] = A
exec(compile(_src, _src_path, "exec"), A.__dict__)
sys.path.insert(0, R17)
import hours_analyze as HA  # noqa: E402

THRESH = 2.50
TAIL = -0.0100          # both-sides loss > 100 x1e-4 BTC
YEARS = {"Y1 24-10..25-09": A.HALVES[:2], "Y2 25-10..26-09": A.HALVES[2:]}
STATES = ("扩张|隐含高于实现", "扩张|隐含低于实现", "收敛|隐含高于实现", "收敛|隐含低于实现")


def build(hours=(8, 16)):
    """Mirror of hours_analyze.main() per-row loop, restricted to both_flat / both_net."""
    ivrows = json.load(open(HA.IVF, encoding="utf-8"))["rows"]
    hs = {h: {"short": st.median([r["hs_short_btc"] for r in ivrows if r["entry_hour_utc"] == h and r["hs_short_btc"] is not None]),
              "long": st.median([r["hs_long_btc"] for r in ivrows if r["entry_hour_utc"] == h and r["hs_long_btc"] is not None])}
          for h in hours}
    out = {}
    for r in ivrows:
        h = r["entry_hour_utc"]
        if h not in hours:
            continue
        hb = HA.HB[h]
        expiry = int(HA.datetime.fromisoformat(r["expiry"]).replace(hour=8, tzinfo=HA.timezone.utc).timestamp() * 1000)
        entry = expiry - hb * HA.H
        iv = r["atm_iv"]
        s0, s1 = A.px(entry), A.px(expiry)
        twap = st.mean(A.px(expiry - (30 - k) * HA.MIN) for k in range(30))
        x = {"expiry": r["expiry"], "h": h, "entry_ms": entry, "iv": iv, "iv_pre": r["atm_iv_pre"]}
        if iv is None or any(math.isnan(v) for v in (s0, s1, twap)):
            out[(r["expiry"], h)] = x
            continue
        T = hb / 8760
        flat = net = 0.0
        for call in (False, True):
            v = HA.vert(s0, twap, T, lambda K: iv, call)
            cost = (min(HA.FEE, HA.FEE_CAP * v["ps"]) + min(HA.FEE, HA.FEE_CAP * v["pl"]) + hs[h]["short"] + hs[h]["long"])
            flat += v["pnl"]
            net += v["pnl"] - cost
        x.update(both_flat=flat, both_net=net)
        out[(r["expiry"], h)] = x
    return out


def mse(v):
    n = len(v)
    return {"n": n, "mean": st.mean(v) if n else None, "se": st.stdev(v) / math.sqrt(n) if n > 1 else None}


def welch(a, b):
    ma, mb, va, vb = st.mean(a), st.mean(b), st.variance(a), st.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    df = (va / len(a) + vb / len(b)) ** 2 / ((va / len(a)) ** 2 / (len(a) - 1) + (vb / len(b)) ** 2 / (len(b) - 1))
    return {"diff": ma - mb, "se": se, "t": (ma - mb) / se, "df": df}


def paired(d):
    m, se = st.mean(d), st.stdev(d) / math.sqrt(len(d))
    return {"n": len(d), "mean": m, "se": se, "t": m / se}


def sgn(v):
    return (v > 0) - (v < 0)


def main():
    cells = build()
    # fidelity check vs research_17 outputs
    ref = json.load(open(os.path.join(R17, "hours_results.json"), encoding="utf-8"))
    for h in (8, 16):
        for k in ("both_flat", "both_net"):
            v = [x[k] for (e, hh), x in cells.items() if hh == h and k in x]
            rr = ref["per_hour"][f"{h:02d}"][k]
            assert len(v) == rr["n"] and abs(st.mean(v) - rr["mean"]) < 1e-12, (h, k, len(v), rr["n"], st.mean(v), rr["mean"])
    days = sorted({e for e, h in cells})
    pd_ref = ref["paired_vs_08"]["16"]["both_flat"]
    d_all = [cells[(e, 16)]["both_flat"] - cells[(e, 8)]["both_flat"] for e in days
             if "both_flat" in cells.get((e, 8), {}) and "both_flat" in cells.get((e, 16), {})]
    assert len(d_all) == pd_ref["n_days"] and abs(st.mean(d_all) - pd_ref["mean"]) < 1e-12

    rows, excl = [], {"no_08_outcome": 0, "no_iv_pre": 0, "no_rv": 0}
    for e in days:
        c8 = cells.get((e, 8))
        if not c8 or "both_flat" not in c8:
            excl["no_08_outcome"] += 1
            continue
        anchor = c8["entry_ms"] - HA.MIN          # newest close used = 07:59 bar close
        rv24, rv7 = A.rv(anchor, 24), A.rv(anchor, 168)
        if rv24 is None or rv7 is None or rv7 == 0:
            excl["no_rv"] += 1
            continue
        if c8["iv_pre"] is None:
            excl["no_iv_pre"] += 1
            continue
        s1 = "扩张" if rv24 / rv7 >= 1 else "收敛"
        s2 = "隐含高于实现" if c8["iv_pre"] - rv24 >= 0 else "隐含低于实现"
        c16 = cells.get((e, 16), {})
        rows.append({"expiry": e, "half": A.half_of(e), "rv24": rv24, "rv7": rv7, "iv_pre": c8["iv_pre"], "iv": c8["iv"],
                     "state": f"{s1}|{s2}", "s2_with_atm_iv": "隐含高于实现" if c8["iv"] - rv24 >= 0 else "隐含低于实现",
                     "f8": c8["both_flat"], "n8": c8["both_net"],
                     "f16": c16.get("both_flat"), "n16": c16.get("both_net")})

    def contrast(sub, rest, key):
        return welch([r[key] for r in sub], [r[key] for r in rest]) if len(sub) > 1 and len(rest) > 1 else None

    def pdiff(sub, key16, key8):
        d = [r[key16] - r[key8] for r in sub if r[key16] is not None]
        return paired(d) if len(d) > 1 else None

    res = {}
    for s in STATES:
        sub = [r for r in rows if r["state"] == s]
        rest = [r for r in rows if r["state"] != s]
        main_t = contrast(sub, rest, "f8")
        inter = pdiff(sub, "f16", "f8")
        x = {"n": len(sub), "both_flat": mse([r["f8"] for r in sub]), "both_net": mse([r["n8"] for r in sub]),
             "rest_both_flat": mse([r["f8"] for r in rest]), "rest_both_net": mse([r["n8"] for r in rest]),
             "main_test_flat_vs_rest": main_t, "secondary_net_vs_rest": contrast(sub, rest, "n8"),
             "tail_freq_flat_loss_gt100": sum(r["f8"] < TAIL for r in sub) / len(sub),
             "tail_freq_net_loss_gt100": sum(r["n8"] < TAIL for r in sub) / len(sub),
             "rest_tail_freq_flat_loss_gt100": sum(r["f8"] < TAIL for r in rest) / len(rest),
             "interaction_16_minus_08_flat": inter, "interaction_16_minus_08_net_descriptive": pdiff(sub, "n16", "n8"),
             "count_by_half": {h: sum(r["half"] == h for r in sub) for h in A.HALVES}}
        x["main_pass"] = abs(main_t["t"]) > THRESH
        x["inter_pass"] = abs(inter["t"]) > THRESH
        rob = {}
        if x["main_pass"]:
            sg = sgn(main_t["diff"])
            yrs = {y: contrast([r for r in sub if r["half"] in hs_], [r for r in rest if r["half"] in hs_], "f8") for y, hs_ in YEARS.items()}
            hal = {h: contrast([r for r in sub if r["half"] == h], [r for r in rest if r["half"] == h], "f8") for h in A.HALVES}
            net = x["secondary_net_vs_rest"]
            rob["main"] = {"years": yrs, "halves": hal,
                           "years_same_sign": all(v and sgn(v["diff"]) == sg for v in yrs.values()),
                           "halves_same_sign_count": sum(1 for v in hal.values() if v and sgn(v["diff"]) == sg),
                           "net_same_sign": sgn(net["diff"]) == sg}
            rob["main"]["all_pass"] = rob["main"]["years_same_sign"] and rob["main"]["halves_same_sign_count"] >= 3 and rob["main"]["net_same_sign"]
        if x["inter_pass"]:
            sg = sgn(inter["mean"])
            yrs = {y: pdiff([r for r in sub if r["half"] in hs_], "f16", "f8") for y, hs_ in YEARS.items()}
            hal = {h: pdiff([r for r in sub if r["half"] == h], "f16", "f8") for h in A.HALVES}
            net = x["interaction_16_minus_08_net_descriptive"]
            rob["interaction"] = {"years": yrs, "halves": hal,
                                  "years_same_sign": all(v and sgn(v["mean"]) == sg for v in yrs.values()),
                                  "halves_same_sign_count": sum(1 for v in hal.values() if v and sgn(v["mean"]) == sg),
                                  "net_same_sign": sgn(net["mean"]) == sg}
            rob["interaction"]["all_pass"] = (rob["interaction"]["years_same_sign"] and rob["interaction"]["halves_same_sign_count"] >= 3
                                              and rob["interaction"]["net_same_sign"])
        x["robustness"] = rob
        x["has_increment"] = any(v["all_pass"] for v in rob.values())
        res[s] = x
    verdict = "有增量" if any(v["has_increment"] for v in res.values()) else "无增量"
    rep = {"registration": "docs/opus/21_本期状态收束_事先登记_v0.1.md", "doc": __doc__, "threshold_abs_t": THRESH,
           "n_cycles_used": len(rows), "excluded": excl,
           "fidelity_check": "08/16 both_flat/both_net n and mean equal research_17/hours_results.json; paired 16-08 equal",
           "all_cycles": {"both_flat": mse([r["f8"] for r in rows]), "both_net": mse([r["n8"] for r in rows]),
                          "tail_freq_flat": sum(r["f8"] < TAIL for r in rows) / len(rows),
                          "paired_16_minus_08_flat": pdiff(rows, "f16", "f8")},
           "state_counts_by_half": {h: {s: sum(r["half"] == h and r["state"] == s for r in rows) for s in STATES} for h in A.HALVES},
           "s2_classification_agreement_iv_pre_vs_atm_iv": sum(r["state"].split("|")[1] == r["s2_with_atm_iv"] for r in rows) / len(rows),
           "states": res, "verdict": verdict,
           "rows": [{k: r[k] for k in ("expiry", "half", "state", "rv24", "rv7", "iv_pre", "iv", "f8", "n8", "f16", "n16")} for r in rows]}
    json.dump(rep, open(os.path.join(HERE, "state_results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    f = lambda v: f"{v * 1e4:+.1f}"  # noqa: E731
    L = [f"doc 21 state study  cycles used {len(rows)}  excluded {excl}  pass |t|>{THRESH}",
         f"all: both_flat {f(rep['all_cycles']['both_flat']['mean'])}±{rep['all_cycles']['both_flat']['se'] * 1e4:.1f}  "
         f"both_net {f(rep['all_cycles']['both_net']['mean'])}±{rep['all_cycles']['both_net']['se'] * 1e4:.1f}  "
         f"tail {rep['all_cycles']['tail_freq_flat']:.3f}  16-08 {f(rep['all_cycles']['paired_16_minus_08_flat']['mean'])} t={rep['all_cycles']['paired_16_minus_08_flat']['t']:+.2f}",
         "", "state | n | flat±se (vs rest diff, t) | net±se (vs rest diff, t) | tail flat/net (rest flat) | 16-08 flat±se t (n) | 16-08 net"]
    for s, x in res.items():
        m, nt, it, inn = x["main_test_flat_vs_rest"], x["secondary_net_vs_rest"], x["interaction_16_minus_08_flat"], x["interaction_16_minus_08_net_descriptive"]
        L.append(f"{s} | {x['n']} | {f(x['both_flat']['mean'])}±{x['both_flat']['se'] * 1e4:.1f} ({f(m['diff'])}, t={m['t']:+.2f}) | "
                 f"{f(x['both_net']['mean'])}±{x['both_net']['se'] * 1e4:.1f} ({f(nt['diff'])}, t={nt['t']:+.2f}) | "
                 f"{x['tail_freq_flat_loss_gt100']:.3f}/{x['tail_freq_net_loss_gt100']:.3f} ({x['rest_tail_freq_flat_loss_gt100']:.3f}) | "
                 f"{f(it['mean'])}±{it['se'] * 1e4:.1f} t={it['t']:+.2f} (n{it['n']}) | {f(inn['mean'])} t={inn['t']:+.2f}"
                 f"{'  MAIN PASS' if x['main_pass'] else ''}{'  INTER PASS' if x['inter_pass'] else ''}")
        for k, rb in x["robustness"].items():
            key = "diff" if k == "main" else "mean"
            L.append(f"   robustness[{k}]: years " + " ".join(f"{y[:2]} {f(v[key])}" if v else f"{y[:2]} n/a" for y, v in rb["years"].items())
                     + " | halves " + " ".join(f"{h[:2]} {f(v[key])}" if v else f"{h[:2]} n/a" for h, v in rb["halves"].items())
                     + f" | years_same {rb['years_same_sign']} halves_same {rb['halves_same_sign_count']}/4 net_same {rb['net_same_sign']} -> all_pass {rb['all_pass']}")
    L.append("\nstate counts by half:")
    for h, d in rep["state_counts_by_half"].items():
        L.append(f"  {h}: " + "  ".join(f"{s} {n}" for s, n in d.items()))
    L.append(f"\nS2 agreement (iv_pre vs atm_iv, not a test): {rep['s2_classification_agreement_iv_pre_vs_atm_iv']:.3f}")
    L.append(f"VERDICT (doc 21 §5): {verdict}")
    txt = "\n".join(L)
    open(os.path.join(HERE, "state_summary.txt"), "w", encoding="utf-8").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
