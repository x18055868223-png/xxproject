import json, time, urllib.request
OUT = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\intraday_vol\dvol_1h.json"
start = int(time.mktime((2024, 10, 1, 0, 0, 0, 0, 0, 0)) * 1000) - 8 * 3600_000
end = int(time.time() * 1000)
rows = {}
cur_end = end
while True:
    url = ("https://www.deribit.com/api/v2/public/get_volatility_index_data?currency=BTC"
           f"&start_timestamp={start}&end_timestamp={cur_end}&resolution=3600")
    with urllib.request.urlopen(url, timeout=30) as r:
        res = json.load(r)["result"]
    for t, o, h, l, c in res["data"]:
        rows[t] = c
    cont = res.get("continuation")
    print(len(res["data"]), cont, len(rows), flush=True)
    if not cont or cont <= start or not res["data"]:
        break
    cur_end = cont
    time.sleep(0.3)
pts = sorted(rows.items())
json.dump({"source": "deribit public/get_volatility_index_data BTC 1h close", "points": pts}, open(OUT, "w"))
print("saved", len(pts), pts[0], pts[-1])
