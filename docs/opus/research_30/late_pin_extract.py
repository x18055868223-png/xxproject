"""Late look at this expiry's pin, from ledger rows (doc 30, N1). Read-only; run on the server from the archive
root with this file on stdin. For each expiry: the first ledger row at or after 12h, 4h and 1h before expiry that
carries a pin reading, and the delivery-time index from the stored path."""
import glob, gzip, json, os, sys

H = 3_600_000
rows = []
for lf in sorted(glob.glob("ledger/*.jsonl")):
    for line in open(lf, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("pin_from_index_pct") is not None and r.get("index_usd"):
            rows.append(r)
out = []
for pf in sorted(glob.glob("paths/*.json.gz")):
    with gzip.open(pf, "rt", encoding="utf-8") as f:
        path = json.load(f)
    expiry, points = path["expiry_ms"], path["points"]
    settle = [p for t, p in points if t <= expiry][-1]
    last30 = [p for t, p in points if expiry - 30 * 60000 < t <= expiry]
    rec = {"expiry_day": os.path.basename(pf)[:10], "index_at_expiry": settle,
           "last30_mean": sum(last30) / len(last30) if last30 else None, "looks": {}}
    mine = sorted((r for r in rows if r.get("expiry_ms") == expiry), key=lambda r: r["at_ms"])
    for hours in (12, 4, 1):
        pick = next((r for r in mine if expiry - hours * H <= r["at_ms"] < expiry), None)
        if pick:
            rec["looks"][str(hours)] = {"at_ms": pick["at_ms"], "index_usd": pick["index_usd"],
                                        "pin_usd": round(pick["index_usd"] * (1 + pick["pin_from_index_pct"] / 100)),
                                        "expected_move_pct": pick.get("expected_move_pct")}
    out.append(rec)
json.dump(out, sys.stdout)
