import csv, math, statistics, array
from datetime import datetime, timezone
C = array.array("d"); C.frombytes(open("closes.bin", "rb").read())
T0 = int(datetime(2024, 10, 1, tzinfo=timezone.utc).timestamp() * 1000); MIN = 60000; H = 3600000
rows = list(csv.DictReader(open(r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\research_btc_1d_iv\btc_1d_atm_iv_allhours.csv")))
print(rows[0].keys())
by = {}
for r in rows:
    if r["atm_iv"] not in ("", "nan"):
        by.setdefault(int(r["hours_to_expiry"]), []).append(float(r["atm_iv"]))
print("median IV by hours-left", {h: round(statistics.median(v), 1) for h, v in sorted(by.items(), reverse=True)})
# realized variance share from h hours before expiry (sum of squared 1m log returns), averaged over expiries
days = sorted({r["expiry"] for r in rows})
tot = {h: 0.0 for h in by}
full = 0.0
for d in days:
    e = int(datetime.fromisoformat(d).replace(hour=8, tzinfo=timezone.utc).timestamp() * 1000)
    i1 = (e - T0) // MIN
    if i1 >= len(C) or (e - 24 * H - T0) // MIN < 1: continue
    def rv(hh):
        i0 = (e - hh * H - T0) // MIN
        return sum(math.log(C[i + 1] / C[i]) ** 2 for i in range(i0, i1))
    f = rv(24); full += f
    for h in tot: tot[h] += rv(h)
print("realized variance share", {h: round(tot[h] / full, 2) for h in sorted(tot, reverse=True)})
