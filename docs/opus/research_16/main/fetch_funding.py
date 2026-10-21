import csv, io, json, urllib.request, zipfile
OUT = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\intraday_vol\funding_8h.json"
rows = []
months = [(y, m) for y in (2024, 2025, 2026) for m in range(1, 13) if (y, m) >= (2024, 9) and (y, m) <= (2026, 9)]
for y, m in months:
    url = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{y}-{m:02d}.zip"
    try:
        z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(url, timeout=60).read()))
    except Exception as e:
        print("miss", y, m, e); continue
    for row in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), "utf-8")):
        if not row[0].isdigit(): continue
        rows.append((int(row[0]), float(row[2])))
rows.sort()
json.dump({"source": "data.binance.vision futures/um fundingRate BTCUSDT (calc_time, last_funding_rate)", "points": rows}, open(OUT, "w"))
print("saved", len(rows), rows[0], rows[-1])
