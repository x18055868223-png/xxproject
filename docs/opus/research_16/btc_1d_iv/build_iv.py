"""Per expiry day x entry time: 1-day ATM IV and an OTM skew proxy from cached Deribit trades.
ATM: trades with |K/index-1| <= 1.5% within +-45 min of entry (both C and P; trade's own index_price).
  atm_iv          = median trade IV (primary)
  atm_iv_wmed     = amount-weighted median trade IV
  atm_iv_mark     = median IV implied by the trade's mark_price (Black-76, F = trade index, calendar T)
Skew: OTM puts with K/index-1 in [-2.5%,-1.5%] vs OTM calls in [+1.5%,+2.5%] (median trade IV each).
Smile: median OTM trade IV per 0.5%-moneyness bin (-6%..+6%), for skew-priced verticals.
"""
import csv, gzip, json, math, os, statistics as st
from datetime import datetime, timezone

RAW = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\research_btc_1d_iv\raw"
OUT = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\research_btc_1d_iv"
YEAR_MS = 365 * 86400_000


def ncdf(x):
    return 0.5 * math.erfc(-x / math.sqrt(2))


def b76(F, K, T, sig, call):
    if T <= 0 or sig <= 0:
        return max(0.0, (F - K) if call else (K - F))
    s = sig * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * s * s) / s
    d2 = d1 - s
    return F * ncdf(d1) - K * ncdf(d2) if call else K * ncdf(-d2) - F * ncdf(-d1)


def implied(price_usd, F, K, T, call):
    lo, hi = 0.01, 5.0
    if price_usd <= b76(F, K, T, lo, call) or price_usd >= b76(F, K, T, hi, call):
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if b76(F, K, T, mid, call) < price_usd:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def wmed(pairs):
    pairs = sorted(pairs)
    tot = sum(w for _, w in pairs)
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= tot / 2:
            return v


def main():
    rows = []
    for name in sorted(os.listdir(RAW)):
        if not name.endswith(".json.gz"):
            continue
        d = json.load(gzip.open(os.path.join(RAW, name), "rt", encoding="utf-8"))
        ymd, hh = name[:8], int(name[9:11])
        expiry = datetime.strptime(ymd, "%Y%m%d").replace(hour=8, tzinfo=timezone.utc)
        exp_ms = int(expiry.timestamp() * 1000)
        atm, puts, calls, marks, smile, pre = [], [], [], [], {}, []
        centre = d["window_ms"][0] + 45 * 60000
        strikes_atm = set()
        idx = []
        for t in d["trades"]:
            if t["iv"] is None or t["index_price"] is None:
                continue
            _, _, k, cp = t["instrument_name"].split("-")
            K = float(k)
            S = t["index_price"]
            idx.append(S)
            m = K / S - 1
            iv = t["iv"]
            call = cp == "C"
            if abs(m) <= 0.015:
                atm.append((iv, t["amount"] or 0))
                if t["timestamp"] <= centre:
                    pre.append(iv)
                strikes_atm.add(int(K))
                T = (exp_ms - t["timestamp"]) / YEAR_MS
                if t.get("mark_price"):
                    v = implied(t["mark_price"] * S, S, K, T, call)
                    if v:
                        marks.append(v * 100)
            if not call and -0.025 <= m <= -0.015:
                puts.append(iv)
            if call and 0.015 <= m <= 0.025:
                calls.append(iv)
            otm = (call and m >= -0.0025) or (not call and m <= 0.0025)
            if otm and abs(m) <= 0.06:
                b = round(m / 0.005) * 0.5   # bin centre in %
                smile.setdefault(b, []).append(iv)
        row = {"expiry": expiry.strftime("%Y-%m-%d"), "entry_hour_utc": hh,
               "entry_utc": d["entry_utc"], "hours_to_expiry": 24 - (hh - 8),
               "n_trades_expiry": len(d["trades"]), "n_all_option_trades_window": d["all_option_trades"],
               "index_median": round(st.median(idx), 2) if idx else None,
               "atm_n": len(atm), "atm_strikes": sorted(strikes_atm),
               "atm_iv": round(st.median(v for v, _ in atm), 2) if len(atm) >= 3 else None,
               "atm_iv_wmed": round(wmed(atm), 2) if len(atm) >= 3 else None,
               "atm_iv_pre": round(st.median(pre), 2) if len(pre) >= 3 else None, "atm_n_pre": len(pre),
               "atm_iv_mark": round(st.median(marks), 2) if len(marks) >= 3 else None,
               "put_wing_n": len(puts), "call_wing_n": len(calls),
               "put_wing_iv": round(st.median(puts), 2) if len(puts) >= 2 else None,
               "call_wing_iv": round(st.median(calls), 2) if len(calls) >= 2 else None,
               "smile": {f"{b:+.1f}": [round(st.median(v), 2), len(v)] for b, v in sorted(smile.items())},
               "method": "median trade IV, |K/index-1|<=1.5%, +-45min, min 3 trades"}
        row["skew_put_minus_call"] = (round(row["put_wing_iv"] - row["call_wing_iv"], 2)
                                      if row["put_wing_iv"] is not None and row["call_wing_iv"] is not None else None)
        rows.append(row)
    json.dump({"source": "Deribit history API get_last_trades_by_currency_and_time (BTC options), next 08:00 UTC daily expiry only",
               "rows": rows}, open(os.path.join(OUT, "btc_1d_atm_iv.json"), "w", encoding="utf-8"), indent=0)
    cols = [k for k in rows[0] if k != "smile"]
    with open(os.path.join(OUT, "btc_1d_atm_iv.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([" ".join(map(str, r[c])) if isinstance(r[c], list) else r[c] for c in cols])
    ok = sum(r["atm_iv"] is not None for r in rows)
    print(len(rows), "windows;", ok, "with ATM IV")


if __name__ == "__main__":
    main()
