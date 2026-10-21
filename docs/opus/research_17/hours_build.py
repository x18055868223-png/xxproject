"""All 11 entry hours: 1-day ATM IV per (expiry, entry hour) from cached Deribit trades + effective half-spread.
Main estimate (unchanged method): median trade IV, |K/index-1|<=1.5%, +-45 min, >=3 trades.
Also: atm_iv_pre (trades at or before entry only), atm_iv_mark (mark-price-inverted, same strikes),
fallback atm_iv_fb = mark-inverted median over |m|<=3% (only reported as coverage; never used in the main tables).
Half-spread proxy per trade (non-block, with mark): buyer-initiated price-mark, seller-initiated mark-price (BTC),
pooled in two OTM zones: short-leg zone 0.25-2.0% OTM and long-leg zone 2.0-5.0% OTM.
"""
import csv, gzip, json, os, statistics as st, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_iv import implied  # noqa: E402

RAW = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\research_btc_1d_iv\raw"
OUT = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\research_btc_1d_iv"
YEAR_MS = 365 * 86400_000


def med(v, nd=2, k=3):
    return round(st.median(v), nd) if len(v) >= k else None


def main():
    rows = []
    for name in sorted(os.listdir(RAW)):
        if not name.endswith(".json.gz"):
            continue
        d = json.load(gzip.open(os.path.join(RAW, name), "rt", encoding="utf-8"))
        ymd, hh = name[:8], int(name[9:11])
        hb = ((8 - hh) % 24) or 24
        expiry = datetime.strptime(ymd, "%Y%m%d").replace(hour=8, tzinfo=timezone.utc)
        exp_ms = int(expiry.timestamp() * 1000)
        centre = d["window_ms"][0] + 45 * 60000
        atm, pre, marks, fb, smile = [], [], [], [], {}
        sp_short, sp_long, sp_short_rel = [], [], []
        for t in d["trades"]:
            if t["iv"] is None or t["index_price"] is None:
                continue
            _, _, k, cp = t["instrument_name"].split("-")
            K, S, call = float(k), t["index_price"], cp == "C"
            m = K / S - 1
            iv = t["iv"]
            T = (exp_ms - t["timestamp"]) / YEAR_MS
            if abs(m) <= 0.015:
                atm.append(iv)
                if t["timestamp"] <= centre:
                    pre.append(iv)
            if abs(m) <= 0.03 and t.get("mark_price"):
                v = implied(t["mark_price"] * S, S, K, T, call)
                if v:
                    fb.append(v * 100)
                    if abs(m) <= 0.015:
                        marks.append(v * 100)
            otm_m = m if call else -m          # >0 means OTM
            if -0.0025 <= otm_m and abs(m) <= 0.06:
                b = round(m / 0.005) * 0.5
                smile.setdefault(b, []).append(iv)
            if t.get("mark_price") and not t.get("block_trade_id") and t["price"] is not None:
                sgn = (t["price"] - t["mark_price"]) if t["direction"] == "buy" else (t["mark_price"] - t["price"])
                if 0.0025 <= otm_m < 0.02:
                    sp_short.append(sgn)
                    sp_short_rel.append(sgn / t["mark_price"])
                elif 0.02 <= otm_m <= 0.05:
                    sp_long.append(sgn)
        rows.append({"expiry": expiry.strftime("%Y-%m-%d"), "entry_hour_utc": hh, "hours_to_expiry": hb,
                     "n_trades_expiry": len(d["trades"]), "atm_n": len(atm), "atm_n_pre": len(pre),
                     "atm_iv": med(atm), "atm_iv_pre": med(pre), "atm_iv_mark": med(marks), "atm_iv_fb_mark3pct": med(fb),
                     "smile": {f"{b:+.1f}": [round(st.median(v), 2), len(v)] for b, v in sorted(smile.items())},
                     "hs_short_btc": med(sp_short, 6, 1), "hs_short_n": len(sp_short),
                     "hs_short_rel": med(sp_short_rel, 4, 1),
                     "hs_long_btc": med(sp_long, 6, 1), "hs_long_n": len(sp_long)})
    json.dump({"method": "median trade IV |m|<=1.5% +-45min >=3 trades; half-spread proxy = signed trade price - mark by aggressor side",
               "rows": rows}, open(os.path.join(OUT, "btc_1d_atm_iv_allhours.json"), "w", encoding="utf-8"))
    cols = [c for c in rows[0] if c != "smile"]
    with open(os.path.join(OUT, "btc_1d_atm_iv_allhours.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])
    by = {}
    for r in rows:
        by.setdefault(r["entry_hour_utc"], []).append(r)
    for h in sorted(by, key=lambda h: -(((8 - h) % 24) or 24)):
        v = by[h]
        print(f"{h:02d}:00 windows={len(v)} atm_iv={sum(r['atm_iv'] is not None for r in v)} pre={sum(r['atm_iv_pre'] is not None for r in v)}"
              f" fb={sum(r['atm_iv_fb_mark3pct'] is not None for r in v)} med_atm_n={st.median(r['atm_n'] for r in v)}")


if __name__ == "__main__":
    main()
