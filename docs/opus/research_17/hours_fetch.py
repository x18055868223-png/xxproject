"""Fetch Deribit BTC option trades (public history API) in +-45 min windows around 08:00/14:00/16:00 UTC
on the day before each 08:00 UTC daily expiry; keep only trades of the next daily expiry. Cached per window."""
import gzip, json, os, sys, time, random, urllib.request, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

OUT = r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\research_btc_1d_iv\raw"
API = "https://history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time"
HALF = 45 * 60000
ENTRY_HOURS = tuple(int(h) for h in os.environ.get("ENTRY_HOURS", "8,14,16").split(","))


def code(d):
    return f"{d.day}{d.strftime('%b').upper()}{d.strftime('%y')}"


def get(url):
    for k in range(8):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            w = min(60, 2 ** k) + random.random()
            print("retry", k, e, url[-80:], file=sys.stderr, flush=True)
            time.sleep(w)
    raise RuntimeError("failed " + url)


def fetch_window(expiry, hour):
    path = os.path.join(OUT, f"{expiry:%Y%m%d}_{hour:02d}.json.gz")
    if os.path.exists(path):
        return path, None
    hb = ((8 - hour) % 24) or 24          # hours before the 08:00 expiry
    ent = expiry - timedelta(hours=hb)
    c = int(ent.timestamp() * 1000)
    start, end = c - HALF, c + HALF
    tag = "-" + code(expiry) + "-"
    seen, keep, pages, total = set(), [], 0, 0
    while True:
        d = get(f"{API}?currency=BTC&kind=option&start_timestamp={start}&end_timestamp={end}&count=1000&sorting=asc")
        r = d["result"]
        tr = r["trades"]
        pages += 1
        time.sleep(0.15)
        for t in tr:
            if t["trade_id"] in seen:
                continue
            seen.add(t["trade_id"])
            total += 1
            if tag in t["instrument_name"]:
                keep.append({k: t.get(k) for k in ("timestamp", "instrument_name", "iv", "index_price", "price",
                                                    "mark_price", "direction", "amount", "trade_id", "block_trade_id")})
        if not r.get("has_more") or not tr:
            break
        nxt = tr[-1]["timestamp"]
        if nxt <= start:  # >1000 trades in one ms: cannot paginate further
            print("stuck", path, file=sys.stderr)
            break
        start = nxt
    rec = {"expiry": code(expiry), "entry_utc": ent.isoformat(), "window_ms": [c - HALF, c + HALF], "pages": pages,
           "all_option_trades": total, "trades": keep}
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(rec, fh)
    os.replace(tmp, path)
    return path, (pages, total, len(keep))


def main():
    a = datetime.fromisoformat(sys.argv[1]).replace(hour=8, tzinfo=timezone.utc)
    b = datetime.fromisoformat(sys.argv[2]).replace(hour=8, tzinfo=timezone.utc)
    jobs = []
    d = a
    while d <= b:
        for h in ENTRY_HOURS:
            jobs.append((d, h))
        d += timedelta(days=1)
    n = [0]
    lock = threading.Lock()

    def run(j):
        p, info = fetch_window(*j)
        with lock:
            n[0] += 1
            if info or n[0] % 50 == 0:
                print(n[0], len(jobs), os.path.basename(p), info, flush=True)
    with ThreadPoolExecutor(int(sys.argv[3]) if len(sys.argv) > 3 else 4) as ex:
        list(ex.map(run, jobs))


if __name__ == "__main__":
    main()
