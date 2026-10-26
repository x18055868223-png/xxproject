"""Backtest of the busy_window_thin_cushion entry warning (docs/opus/22). Run: python docs/opus/research_22/warning_study.py

Sample and outcomes exactly as doc 17 appendix A (research_17/hours_analyze.py, IVKEY=atm_iv): 11 entry hours per
08:00 UTC expiry, |delta|~0.35 short on a 500 grid, 2,000 wide, 1 BTC, coin-settled, hold to expiry, Black-76 at the
real 1-day ATM IV; flat = pre-cost (primary), net = after the appendix-A cost model. Per side, x1e-4 BTC.

The warning is replayed with the live code (tools/astra_entry_warnings_v1.busy_window_thin_cushion and
tools/astra_session_clock_v1.reading) at the entry instant, with the contract's own expiry. distance_in_moves uses the
live definition: cushion % / (ATM IV x sqrt(hours / 8760)). Macro windows come from the historical release dates
(.artifacts/research_us_equity/us_macro_events.json: CPI, NFP 08:30 ET, FOMC 14:00 ET); PPI/GDP/PCE and pre-2026
NYSE holidays are missing from history (a US-open window is assumed on those holiday weekdays).

Variants (veto when the condition holds):
  V0 live:       busy hour now (clock >= 1.25) OR a window in [-1 h, +2 h]   AND distance < 0.5 moves
  V1 ahead:      a window starting in (0, +2 h]                                AND distance < 0.5 moves
  V2 ahead+thin: a window starting in (0, +2 h]                                AND distance < 0.25 moves
  clock:         the time condition of V0 alone, no distance test (the condition of the proposed holding note)
Path outcomes (1-minute Binance closes, so touches inside a minute are missed): touch of the short strike within 2 h of
entry and before expiry, beyond the long strike at expiry (30-minute TWAP, as the P&L).
Comparisons (standard errors clustered by expiry day: up to ~6 hours x 2 sides of one day share its path):
  (a) skip: the mean outcome of the flagged entries themselves (the veto's direct counterfactual is not trading, 0);
  (b) within entry hour, flagged vs not, precision-weighted across hours. At a fixed hour this mostly compares weekdays
      with weekends and daylight with standard time, so it is also given for weekdays only and without 12:00 (where
      "a window ahead" means daylight time or a macro day);
  (c) flagged cells minus the same day and side's 08:00 entry (secondary: at a later hour the 08:00 entry is history).
Strikes are chosen at a fixed 0.35 delta with one IV, so distance in moves is ~0.39 plus 500-grid rounding: the
distance condition cannot be tested on its own here (values < 0.25 are short-tenor rounding to a higher delta).
"""
import json, math, os, statistics as st, sys, types
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
R16 = os.path.join(ROOT, "docs", "opus", "research_16", "btc_1d_iv")
R17 = os.path.join(ROOT, "docs", "opus", "research_17")
ART = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts"
os.environ["IVKEY"] = "atm_iv"

_src_path = os.path.join(R16, "analyze.py")
_src = open(_src_path, encoding="utf-8").read()
_old = 'CACHE = os.path.join(HERE, "closes.bin")'
assert _old in _src
_src = _src.replace(_old, "CACHE = " + repr(os.path.join(ART, "research_btc_1d_iv", "closes_1m_binance.bin")))
A = types.ModuleType("analyze")
A.__file__ = _src_path
sys.modules["analyze"] = A
exec(compile(_src, _src_path, "exec"), A.__dict__)
sys.path.insert(0, R17)
import hours_analyze as HA  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "tools"))
import astra_event_calendar_v1 as cal  # noqa: E402
import astra_session_clock_v1 as sc  # noqa: E402
import astra_entry_warnings_v1 as ew  # noqa: E402

MIN, H = 60_000, 3_600_000
EV = json.load(open(os.path.join(ART, "research_us_equity", "us_macro_events.json"), encoding="utf-8"))
HIST = sorted([{"kind": k, "name_cn": cal.KIND_CN[k], "at_ms": cal.event_ms(d, "14:00" if k == "FOMC" else "08:30")}
               for k in ("FOMC", "CPI", "NFP") for d in EV[k]], key=lambda e: e["at_ms"])
FLAGS = ("V0", "V1", "V2", "clock", "busy_now", "in_progress", "ahead")


def _between(start_ms, end_ms):
    return [e for e in HIST if start_ms <= e["at_ms"] < end_ms]


sc.event_calendar.between = _between          # history instead of the forward calendar (live code otherwise)
sc.event_calendar.coverage_gaps = lambda *a, **k: []


def touch(entry, end, K, put):
    lo, hi = math.inf, -math.inf
    for i in range((entry - A.T0) // MIN, (end - A.T0) // MIN):
        v = A.C[i]
        lo, hi = min(lo, v), max(hi, v)
    return lo <= K if put else hi >= K


def variants(clock, dim):
    w = clock.get("windows") or []
    ahead = any(0 < x["in_hours"] <= ew.WINDOW_HOURS for x in w)
    in_progress = any(-1 <= x["in_hours"] <= 0 for x in w)
    busy = clock.get("hour_level") == "busy"
    live = ew.busy_window_thin_cushion(clock, dim)["triggered"]
    return {"V0": bool(live), "V1": bool(ahead and dim < 0.5), "V2": bool(ahead and dim < 0.25),
            "clock": busy or in_progress or ahead, "busy_now": busy, "in_progress": in_progress, "ahead": ahead}


def build():
    ivrows = json.load(open(HA.IVF, encoding="utf-8"))["rows"]
    hs = {h: {"short": st.median([r["hs_short_btc"] for r in ivrows if r["entry_hour_utc"] == h and r["hs_short_btc"] is not None]),
              "long": st.median([r["hs_long_btc"] for r in ivrows if r["entry_hour_utc"] == h and r["hs_long_btc"] is not None])}
          for h in HA.HOURS}
    cells = []
    for r in ivrows:
        h = r["entry_hour_utc"]
        if h not in HA.HB:
            continue
        hb = HA.HB[h]
        expiry = int(datetime.fromisoformat(r["expiry"]).replace(hour=8, tzinfo=timezone.utc).timestamp() * 1000)
        entry = expiry - hb * H
        iv = r["atm_iv"]
        s0, s1 = A.px(entry), A.px(expiry)
        if iv is None or any(math.isnan(v) for v in (s0, s1)):
            continue
        twap = st.mean(A.px(expiry - (30 - k) * MIN) for k in range(30))
        T = hb / 8760
        clock = sc.reading(entry, expiry)
        move = iv * math.sqrt(T)                          # % (live: atm x sqrt(hours/8760))
        for side, call in (("put", False), ("call", True)):
            v = HA.vert(s0, twap, T, lambda K: iv, call)
            if v is None:
                continue
            best = None                                   # the short strike HA.vert() selects (same grid, same tie-break)
            for j in range(-40, 41):
                K = round(s0 / HA.GRID) * HA.GRID + j * HA.GRID
                dl = abs(A.bdelta(s0, K, T, iv / 100, call))
                if best is None or abs(dl - 0.35) < abs(best[1] - 0.35):
                    best = (K, dl)
            Ks = best[0]
            Kl = Ks + HA.WIDTH if call else Ks - HA.WIDTH
            dim = (Ks / s0 - 1) * 100 * (1 if call else -1) / move
            cost = min(HA.FEE, HA.FEE_CAP * v["ps"]) + min(HA.FEE, HA.FEE_CAP * v["pl"]) + hs[h]["short"] + hs[h]["long"]
            cells.append({"expiry": r["expiry"], "half": A.half_of(r["expiry"]), "h": h, "side": side,
                          "weekend": clock["now_day_type"] == "weekend", "dim": dim,
                          "flat": v["pnl"] * 1e4, "net": (v["pnl"] - cost) * 1e4,
                          "touch2h": touch(entry, min(expiry, entry + 2 * H), Ks, not call),
                          "touch": touch(entry, expiry, Ks, not call),
                          "beyond_long": (twap <= Kl) if not call else (twap >= Kl),
                          **variants(clock, dim)})
    return cells


def cmse(pairs):
    """Mean and its standard error clustered by expiry day; pairs = [(day, value)]."""
    if len(pairs) < 2:
        return None
    m = st.mean(v for _, v in pairs)
    by = {}
    for d, v in pairs:
        by[d] = by.get(d, 0.0) + (v - m)
    g = len(by)
    se = math.sqrt(sum(x * x for x in by.values()) * g / max(1, g - 1)) / len(pairs)
    return {"n": len(pairs), "mean": m, "se": se, "t": m / se if se else None}


def within_hour(cells, flag, key, hours=HA.HOURS):
    """Precision-weighted mean of (flagged - not) within each entry hour that has both (>= 20 each)."""
    num = den = 0.0
    parts = {}
    for h in hours:
        a = [float(c[key]) for c in cells if c["h"] == h and c[flag]]
        b = [float(c[key]) for c in cells if c["h"] == h and not c[flag]]
        if len(a) < 20 or len(b) < 20:
            continue
        d = st.mean(a) - st.mean(b)
        se = math.sqrt(st.variance(a) / len(a) + st.variance(b) / len(b))
        if se == 0:
            continue
        parts[f"{h:02d}"] = {"n_fired": len(a), "n_not": len(b), "diff": d, "se": se}
        num += d / se ** 2
        den += 1 / se ** 2
    if not den:
        return {"parts": parts, "diff": None}
    return {"parts": parts, "diff": num / den, "se": math.sqrt(1 / den), "t": num / den * math.sqrt(den)}


def vs_08(cells, flag, key):
    base = {(c["expiry"], c["side"]): c[key] for c in cells if c["h"] == 8}
    return cmse([(c["expiry"], c[key] - base[(c["expiry"], c["side"])]) for c in cells
                 if c[flag] and c["h"] != 8 and (c["expiry"], c["side"]) in base])


def main():
    cells = build()
    ref = json.load(open(os.path.join(R17, "hours_results.json"), encoding="utf-8"))
    for h in HA.HOURS:                              # fidelity: per-hour both-sides flat mean equals research_17
        per_day = {}
        for c in cells:
            if c["h"] == h:
                per_day.setdefault(c["expiry"], []).append(c["flat"])
        v = [sum(x) for x in per_day.values() if len(x) == 2]
        assert abs(st.mean(v) - ref["per_hour"][f"{h:02d}"]["both_flat"]["mean"] * 1e4) < 1e-6, h
    weekdays = [c for c in cells if not c["weekend"]]
    no12 = tuple(h for h in HA.HOURS if h != 12)
    out = {"n_cells": len(cells), "rate_by_hour": {}, "dim_by_hour": {}, "variants": {}}
    for h in HA.HOURS:
        sub = [c for c in cells if c["h"] == h]
        wd = [c for c in sub if not c["weekend"]]
        we = [c for c in sub if c["weekend"]]
        out["rate_by_hour"][f"{h:02d}"] = {k: round(sum(c[k] for c in sub) / len(sub), 3) for k in FLAGS}
        out["rate_by_hour"][f"{h:02d}"]["V0_weekday"] = round(sum(c["V0"] for c in wd) / len(wd), 3)
        out["rate_by_hour"][f"{h:02d}"]["V0_weekend"] = round(sum(c["V0"] for c in we) / len(we), 3)
        out["rate_by_hour"][f"{h:02d}"]["clock_weekday"] = round(sum(c["clock"] for c in wd) / len(wd), 3)
        d = sorted(c["dim"] for c in sub)
        out["dim_by_hour"][f"{h:02d}"] = {"p10": round(d[len(d) // 10], 3), "median": round(st.median(d), 3),
                                          "p90": round(d[len(d) * 9 // 10], 3),
                                          "share_below_0_5": round(sum(x < 0.5 for x in d) / len(d), 3),
                                          "share_below_0_25": round(sum(x < 0.25 for x in d) / len(d), 3)}
    for flag in FLAGS:
        f = [c for c in cells if c[flag]]
        n = [c for c in cells if not c[flag]]
        row = {"share": len(f) / len(cells), "n_fired": len(f)}
        for key in ("flat", "net"):
            row[f"{key}_fired"] = cmse([(c["expiry"], c[key]) for c in f])
            row[f"{key}_not"] = cmse([(c["expiry"], c[key]) for c in n])
            row[f"{key}_within_hour"] = within_hour(cells, flag, key)
            row[f"{key}_within_hour_weekdays"] = within_hour(weekdays, flag, key)
            row[f"{key}_within_hour_no12"] = within_hour(cells, flag, key, no12)
            row[f"{key}_vs_08"] = vs_08(cells, flag, key)
        for key in ("touch2h", "touch", "beyond_long"):
            row[f"{key}_fired"] = sum(c[key] for c in f) / len(f) if f else None
            row[f"{key}_not"] = sum(c[key] for c in n) / len(n)
            row[f"{key}_within_hour"] = within_hour(cells, flag, key)
            row[f"{key}_within_hour_weekdays"] = within_hour(weekdays, flag, key)
        row["by_half_flat_vs_08"] = {hf: vs_08([c for c in cells if c["half"] == hf], flag, "flat") for hf in A.HALVES}
        row["by_half_flat_skip"] = {hf: cmse([(c["expiry"], c["flat"]) for c in f if c["half"] == hf]) for hf in A.HALVES}
        out["variants"][flag] = row
    # inside the clock condition: the closer strikes (distance < 0.5) against the rest
    inside = [c for c in cells if c["clock"]]
    for c in inside:
        c["thin"] = c["dim"] < 0.5
    out["inside_clock_thin_vs_not"] = {k: within_hour(inside, "thin", k) for k in ("flat", "touch2h", "touch", "beyond_long")}
    out["path_by_hour"] = {f"{h:02d}": {k: round(sum(c[k] for c in cells if c["h"] == h) / sum(1 for c in cells if c["h"] == h), 3)
                                        for k in ("touch2h", "touch", "beyond_long")} for h in HA.HOURS}
    json.dump(out, open(os.path.join(HERE, "warning_results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def ms(r):
        return f"{r['mean']:+.1f}±{r['se']:.1f} (t {r['t']:+.2f})" if r else "-"

    def wh(r, pp=False):
        if r["diff"] is None:
            return "-"
        k = 100 if pp else 1
        return f"{r['diff'] * k:+.1f}{'pp' if pp else ''}±{r['se'] * k:.1f} (t {r['t']:+.2f})"

    L = [f"cells (day x hour x side) = {len(cells)}; per side, x1e-4 BTC; flat = pre-cost; SE of means clustered by expiry day"]
    L.append("\nfiring rate by entry hour: V0 all (weekday / weekend) | V1 | V2 | clock all (weekday)")
    for h, r in out["rate_by_hour"].items():
        L.append(f"  {h}: V0 {r['V0']:.2f} ({r['V0_weekday']:.2f} / {r['V0_weekend']:.2f}) | V1 {r['V1']:.2f} | V2 {r['V2']:.2f} | clock {r['clock']:.2f} ({r['clock_weekday']:.2f})")
    L.append("\ndistance in moves of the 0.35-delta short leg (p10 / median / p90, share <0.5, <0.25):")
    for h, r in out["dim_by_hour"].items():
        L.append(f"  {h}: {r['p10']:.2f} / {r['median']:.2f} / {r['p90']:.2f}  {r['share_below_0_5']:.2f}  {r['share_below_0_25']:.2f}")
    for flag, r in out["variants"].items():
        L.append(f"\n[{flag}] flagged {r['n_fired']} ({r['share']:.1%})")
        for key in ("flat", "net"):
            L.append(f"  {key}: flagged (vs skip) {ms(r[f'{key}_fired'])}  not {ms(r[f'{key}_not'])}")
            L.append(f"    within hour {wh(r[f'{key}_within_hour'])} | weekdays {wh(r[f'{key}_within_hour_weekdays'])} | no 12:00 {wh(r[f'{key}_within_hour_no12'])}")
            L.append(f"    minus same-day 08:00 {ms(r[f'{key}_vs_08'])}")
        for key in ("touch2h", "touch", "beyond_long"):
            if r[f"{key}_fired"] is not None:
                L.append(f"  {key}: flagged {r[f'{key}_fired']:.3f} not {r[f'{key}_not']:.3f}  within hour {wh(r[f'{key}_within_hour'], True)}"
                         f" | weekdays {wh(r[f'{key}_within_hour_weekdays'], True)}")
        L.append("  flat by half, skip: " + " ".join(f"{hf[:2]} {v['mean']:+.1f}±{v['se']:.1f}" if v else f"{hf[:2]} -" for hf, v in r["by_half_flat_skip"].items()))
        L.append("  flat by half, vs 08: " + " ".join(f"{hf[:2]} {v['mean']:+.1f}±{v['se']:.1f}" if v else f"{hf[:2]} -" for hf, v in r["by_half_flat_vs_08"].items()))
    L.append("\ninside the clock condition, distance < 0.5 vs >= 0.5 (within hour):")
    for k, r in out["inside_clock_thin_vs_not"].items():
        L.append(f"  {k}: {wh(r, k != 'flat')}")
    L.append("\npath by entry hour (touch within 2h / touch before expiry / beyond long at expiry):")
    for h, r in out["path_by_hour"].items():
        L.append(f"  {h}: {r['touch2h']:.3f} {r['touch']:.3f} {r['beyond_long']:.3f}")
    txt = "\n".join(L)
    open(os.path.join(HERE, "warning_summary.txt"), "w", encoding="utf-8").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
