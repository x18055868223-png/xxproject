#!/usr/bin/env python3
"""Download market inputs for the Astra lightweight signal study.

The tool is intentionally narrow: it builds reproducible offline files for the
research pipeline and does not touch production services, sidecars, schemas, or
trading code.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Iterable


BINANCE_MONTHLY = (
    "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/"
    "BTCUSDT-1m-{year:04d}-{month:02d}.zip"
)
BINANCE_DAILY = (
    "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1m/"
    "BTCUSDT-1m-{year:04d}-{month:02d}-{day:02d}.zip"
)
BINANCE_REST = "https://api.binance.com/api/v3/klines"
DERIBIT_HISTORY_INSTRUMENTS = (
    "https://history.deribit.com/api/v2/public/get_instruments"
    "?currency=BTC&kind=option&expired=true"
)
DERIBIT_LIVE_INSTRUMENTS = (
    "https://www.deribit.com/api/v2/public/get_instruments"
    "?currency=BTC&kind=option&expired=false"
)
DERIBIT_DELIVERY_PRICES = (
    "https://www.deribit.com/api/v2/public/get_delivery_prices"
    "?index_name=btc_usd&count={count}&offset={offset}"
)
USER_AGENT = "astra-light-study-data/1.0"
MS_PER_MINUTE = 60_000
MS_PER_DAY = 86_400_000


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).isoformat()


def iso_date_to_ms(date_s: str) -> int:
    return int(
        dt.datetime.strptime(date_s, "%Y-%m-%d")
        .replace(tzinfo=dt.UTC, hour=8)
        .timestamp()
        * 1000
    )


def normalize_timestamp_ms(value: Any) -> int:
    if value is None or value == "":
        raise ValueError("missing timestamp")
    raw = int(float(value))
    # Binance archives switched to microsecond timestamps. REST remains ms.
    while abs(raw) >= 100_000_000_000_000:
        raw //= 1000
    return raw


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def request_bytes(url: str, timeout: int = 60) -> tuple[bytes, dict[str, Any]]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        meta = {
            "url": url,
            "status": getattr(resp, "status", None),
            "headers": dict(resp.headers.items()),
            "fetched_at_utc": utc_now_iso(),
            "length": len(data),
            "sha256": sha256_bytes(data),
        }
    return data, meta


def cache_name(url: str, suffix: str) -> str:
    parsed = urllib.parse.urlparse(url)
    base = Path(parsed.path).name or "response"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", base)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    if not safe.endswith(suffix):
        safe += suffix
    return f"{digest}_{safe}"


def cache_get(url: str, cache_dir: Path, suffix: str, timeout: int = 60) -> tuple[bytes, dict[str, Any]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    body_path = cache_dir / cache_name(url, suffix)
    meta_path = body_path.with_suffix(body_path.suffix + ".meta.json")
    if body_path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta = dict(meta)
        meta["cache_hit"] = True
        return body_path.read_bytes(), meta
    data, meta = request_bytes(url, timeout=timeout)
    body_path.write_bytes(data)
    meta["cache_path"] = str(body_path)
    meta["cache_hit"] = False
    write_json(meta_path, meta)
    return data, meta


def try_cache_get(
    url: str, cache_dir: Path, suffix: str, timeout: int = 60
) -> tuple[bytes | None, dict[str, Any]]:
    try:
        return cache_get(url, cache_dir, suffix, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return None, {
            "url": url,
            "status": exc.code,
            "error": f"HTTPError: {exc.reason}",
            "fetched_at_utc": utc_now_iso(),
        }
    except Exception as exc:  # network/cache errors should be visible in the manifest
        return None, {
            "url": url,
            "status": None,
            "error": f"{type(exc).__name__}: {exc}",
            "fetched_at_utc": utc_now_iso(),
        }


def get_checksum(url: str, cache_dir: Path) -> dict[str, Any] | None:
    checksum_url = url + ".CHECKSUM"
    data, meta = try_cache_get(checksum_url, cache_dir, ".CHECKSUM", timeout=30)
    if data is None:
        meta["available"] = False
        return meta
    text = data.decode("utf-8", errors="replace").strip()
    parts = text.split()
    expected = parts[0] if parts else None
    meta["available"] = True
    meta["checksum_text"] = text[:200]
    meta["expected_sha256"] = expected
    return meta


def parse_binance_zip(data: bytes, source_url: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        csv_names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"no CSV file in {source_url}")
        with zf.open(csv_names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.reader(text)
            for row in reader:
                if not row:
                    continue
                if row[0].lower() in {"open_time", "open time"}:
                    continue
                if len(row) < 7:
                    continue
                open_ms = normalize_timestamp_ms(row[0])
                close_ms = normalize_timestamp_ms(row[6])
                rows.append(
                    {
                        "open_time_ms": str(open_ms),
                        "open": row[1],
                        "high": row[2],
                        "low": row[3],
                        "close": row[4],
                        "close_time_ms": str(close_ms),
                        "volume": row[5],
                    }
                )
    return rows


def parse_binance_rest(data: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in data:
        if len(row) < 7:
            continue
        rows.append(
            {
                "open_time_ms": str(normalize_timestamp_ms(row[0])),
                "open": str(row[1]),
                "high": str(row[2]),
                "low": str(row[3]),
                "close": str(row[4]),
                "close_time_ms": str(normalize_timestamp_ms(row[6])),
                "volume": str(row[5]),
            }
        )
    return rows


def month_iter(start: dt.date, end: dt.date) -> Iterable[tuple[int, int]]:
    cur = dt.date(start.year, start.month, 1)
    last = dt.date(end.year, end.month, 1)
    while cur <= last:
        yield cur.year, cur.month
        if cur.month == 12:
            cur = dt.date(cur.year + 1, 1, 1)
        else:
            cur = dt.date(cur.year, cur.month + 1, 1)


def day_iter(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    cur = start
    while cur <= end:
        yield cur
        cur += dt.timedelta(days=1)


def last_closed_open_ms(end_ms: int) -> int:
    return ((end_ms - 59_999) // MS_PER_MINUTE) * MS_PER_MINUTE


def find_missing_ranges(present: set[int], start_ms: int, end_open_ms: int) -> list[tuple[int, int]]:
    missing: list[tuple[int, int]] = []
    cur_start: int | None = None
    t = (start_ms // MS_PER_MINUTE) * MS_PER_MINUTE
    while t <= end_open_ms:
        if t not in present:
            if cur_start is None:
                cur_start = t
        elif cur_start is not None:
            missing.append((cur_start, t - MS_PER_MINUTE))
            cur_start = None
        t += MS_PER_MINUTE
    if cur_start is not None:
        missing.append((cur_start, end_open_ms))
    return missing


def expected_delivery_dates(start_ms: int, end_ms: int) -> list[str]:
    start_date = dt.datetime.fromtimestamp(start_ms / 1000, dt.UTC).date()
    end_date = dt.datetime.fromtimestamp(end_ms / 1000, dt.UTC).date()
    out: list[str] = []
    cur = start_date
    while cur <= end_date:
        delivery_ms = int(dt.datetime(cur.year, cur.month, cur.day, 8, tzinfo=dt.UTC).timestamp() * 1000)
        if delivery_ms <= end_ms:
            out.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    return out


def rest_klines(
    start_open_ms: int, end_open_ms: int, cache_dir: Path
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    rows: list[dict[str, str]] = []
    sources: list[dict[str, Any]] = []
    cur = start_open_ms
    while cur <= end_open_ms:
        # Binance accepts endTime in ms. Request up to 1000 one-minute bars.
        chunk_end = min(end_open_ms + 59_999, cur + 1000 * MS_PER_MINUTE - 1)
        query = urllib.parse.urlencode(
            {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "startTime": cur,
                "endTime": chunk_end,
                "limit": 1000,
            }
        )
        url = f"{BINANCE_REST}?{query}"
        data_b, meta = cache_get(url, cache_dir, ".json", timeout=30)
        parsed = json.loads(data_b.decode("utf-8"))
        parsed_rows = parse_binance_rest(parsed)
        rows.extend(parsed_rows)
        meta["record_count"] = len(parsed_rows)
        meta["range_start_ms"] = cur
        meta["range_end_ms"] = chunk_end
        sources.append(meta)
        if not parsed_rows:
            cur += 1000 * MS_PER_MINUTE
        else:
            cur = int(parsed_rows[-1]["open_time_ms"]) + MS_PER_MINUTE
        time.sleep(0.05)
    return rows, sources


def add_rows(
    row_map: dict[int, dict[str, str]],
    rows: Iterable[dict[str, str]],
    start_ms: int,
    end_ms: int,
    source_label: str,
    conflicts: list[dict[str, Any]],
) -> int:
    count = 0
    for row in rows:
        open_ms = int(row["open_time_ms"])
        close_ms = int(row["close_time_ms"])
        if open_ms < start_ms or close_ms > end_ms:
            continue
        existing = row_map.get(open_ms)
        if existing is not None:
            if any(existing[k] != row[k] for k in ("open", "high", "low", "close", "volume")):
                conflicts.append({"open_time_ms": open_ms, "kept": "first", "new_source": source_label})
            continue
        row_map[open_ms] = row
        count += 1
    return count


def build_binance(output_dir: Path, start_ms: int, end_ms: int) -> dict[str, Any]:
    cache_dir = output_dir / "cache" / "binance"
    row_map: dict[int, dict[str, str]] = {}
    archive_sources: list[dict[str, Any]] = []
    rest_sources: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    start_date = dt.datetime.fromtimestamp(start_ms / 1000, dt.UTC).date()
    end_date = dt.datetime.fromtimestamp(end_ms / 1000, dt.UTC).date()
    end_month = dt.date(end_date.year, end_date.month, 1)

    for year, month in month_iter(start_date, end_date):
        month_date = dt.date(year, month, 1)
        if month_date >= end_month:
            continue
        url = BINANCE_MONTHLY.format(year=year, month=month)
        data, meta = try_cache_get(url, cache_dir, ".zip", timeout=120)
        source = dict(meta)
        source["kind"] = "monthly_archive"
        if data is not None:
            checksum = get_checksum(url, cache_dir)
            source["external_checksum"] = checksum
            if checksum and checksum.get("expected_sha256"):
                source["external_checksum_match"] = (
                    checksum.get("expected_sha256") == source.get("sha256")
                )
            parsed_rows = parse_binance_zip(data, url)
            added = add_rows(row_map, parsed_rows, start_ms, end_ms, url, conflicts)
            source["record_count_raw"] = len(parsed_rows)
            source["record_count_used"] = added
        archive_sources.append(source)

    end_month_start = dt.date(end_date.year, end_date.month, 1)
    daily_start = max(start_date, end_month_start)
    daily_end = end_date
    for day in day_iter(daily_start, daily_end):
        url = BINANCE_DAILY.format(year=day.year, month=day.month, day=day.day)
        data, meta = try_cache_get(url, cache_dir, ".zip", timeout=90)
        source = dict(meta)
        source["kind"] = "daily_archive"
        if data is not None:
            checksum = get_checksum(url, cache_dir)
            source["external_checksum"] = checksum
            if checksum and checksum.get("expected_sha256"):
                source["external_checksum_match"] = (
                    checksum.get("expected_sha256") == source.get("sha256")
                )
            parsed_rows = parse_binance_zip(data, url)
            added = add_rows(row_map, parsed_rows, start_ms, end_ms, url, conflicts)
            source["record_count_raw"] = len(parsed_rows)
            source["record_count_used"] = added
        archive_sources.append(source)

    end_open = last_closed_open_ms(end_ms)
    gaps_before = find_missing_ranges(set(row_map), start_ms, end_open)
    for gap_start, gap_end in gaps_before:
        rows, sources = rest_klines(gap_start, gap_end, cache_dir)
        rest_sources.extend(sources)
        add_rows(row_map, rows, start_ms, end_ms, "binance_rest", conflicts)

    gaps_after = find_missing_ranges(set(row_map), start_ms, end_open)
    rows_sorted = [row_map[k] for k in sorted(row_map)]
    output_path = output_dir / "klines.csv"
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "open_time_ms",
                "open",
                "high",
                "low",
                "close",
                "close_time_ms",
                "volume",
            ],
        )
        writer.writeheader()
        writer.writerows(rows_sorted)

    return {
        "output": str(output_path),
        "sha256": sha256_file(output_path),
        "record_count": len(rows_sorted),
        "range_open_time_ms": [int(rows_sorted[0]["open_time_ms"]), int(rows_sorted[-1]["open_time_ms"])]
        if rows_sorted
        else None,
        "range_open_time_utc": [ms_to_iso(int(rows_sorted[0]["open_time_ms"])), ms_to_iso(int(rows_sorted[-1]["open_time_ms"]))]
        if rows_sorted
        else None,
        "expected_start_ms": start_ms,
        "expected_last_closed_open_ms": end_open,
        "expected_minutes": ((end_open - start_ms) // MS_PER_MINUTE + 1) if end_open >= start_ms else 0,
        "missing_ranges_before_rest": [
            {"start_ms": a, "end_ms": b, "start_utc": ms_to_iso(a), "end_utc": ms_to_iso(b)}
            for a, b in gaps_before
        ],
        "missing_ranges_after_rest": [
            {"start_ms": a, "end_ms": b, "start_utc": ms_to_iso(a), "end_utc": ms_to_iso(b)}
            for a, b in gaps_after
        ],
        "archive_sources": archive_sources,
        "rest_sources": rest_sources,
        "duplicate_conflicts": conflicts[:50],
        "duplicate_conflict_count": len(conflicts),
    }


def deribit_json(url: str, cache_dir: Path, timeout: int = 90) -> tuple[Any, dict[str, Any]]:
    data, meta = cache_get(url, cache_dir, ".json", timeout=timeout)
    payload = json.loads(data.decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(f"Deribit error for {url}: {payload['error']}")
    return payload.get("result"), meta


def instrument_key(item: dict[str, Any]) -> str:
    return str(item.get("instrument_name") or item.get("instrument_id"))


def filter_instruments(items: Iterable[dict[str, Any]], start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in items:
        try:
            expiration = normalize_timestamp_ms(item.get("expiration_timestamp"))
            creation = normalize_timestamp_ms(item.get("creation_timestamp"))
        except Exception:
            continue
        if expiration < start_ms or expiration > end_ms:
            continue
        if creation > end_ms:
            continue
        if item.get("kind") != "option":
            continue
        if item.get("settlement_currency") not in (None, "BTC"):
            continue
        if item.get("base_currency") not in (None, "BTC"):
            continue
        out = dict(item)
        out["creation_timestamp"] = creation
        out["expiration_timestamp"] = expiration
        if "strike" in out and out["strike"] is not None:
            out["strike"] = float(out["strike"])
        if "contract_size" in out and out["contract_size"] is not None:
            try:
                out["contract_size"] = float(out["contract_size"])
            except Exception:
                pass
        filtered.append(out)
    filtered.sort(
        key=lambda x: (
            int(x.get("expiration_timestamp") or 0),
            float(x.get("strike") or 0.0),
            str(x.get("option_type") or ""),
            str(x.get("instrument_name") or ""),
        )
    )
    return filtered


def build_deribit_instruments(output_dir: Path, start_ms: int, end_ms: int) -> dict[str, Any]:
    cache_dir = output_dir / "cache" / "deribit"
    expired_result, expired_meta = deribit_json(DERIBIT_HISTORY_INSTRUMENTS, cache_dir, timeout=180)
    live_result, live_meta = deribit_json(DERIBIT_LIVE_INSTRUMENTS, cache_dir, timeout=90)
    if not isinstance(expired_result, list):
        raise TypeError("Deribit historical get_instruments result is not a list")
    if not isinstance(live_result, list):
        raise TypeError("Deribit live get_instruments result is not a list")

    merged: dict[str, dict[str, Any]] = {}
    for item in expired_result:
        merged[instrument_key(item)] = item
    for item in live_result:
        merged[instrument_key(item)] = item

    filtered = filter_instruments(merged.values(), start_ms, end_ms)
    output_path = output_dir / "instruments.json"
    write_json(output_path, filtered)

    expirations = sorted({int(item["expiration_timestamp"]) for item in filtered})
    expiration_dates = sorted({ms_to_iso(ms)[:10] for ms in expirations if ms_to_iso(ms)})
    expected_dates = expected_delivery_dates(start_ms, end_ms)
    missing_expiration_dates = [date_s for date_s in expected_dates if date_s not in expiration_dates]

    raw_expirations: list[int] = []
    raw_creations: list[int] = []
    for item in expired_result:
        try:
            raw_expirations.append(normalize_timestamp_ms(item.get("expiration_timestamp")))
        except Exception:
            pass
        try:
            raw_creations.append(normalize_timestamp_ms(item.get("creation_timestamp")))
        except Exception:
            pass
    raw_expiration_range = [min(raw_expirations), max(raw_expirations)] if raw_expirations else None
    raw_creation_range = [min(raw_creations), max(raw_creations)] if raw_creations else None
    expected_expiry_ms = iso_date_to_ms(expected_dates[-1]) if expected_dates else None
    raw_covers_study = bool(
        raw_expiration_range
        and raw_expiration_range[0] <= start_ms
        and expected_expiry_ms is not None
        and raw_expiration_range[1] >= expected_expiry_ms
    )
    unpaginated_list_shape = isinstance(expired_result, list)
    exact_selection_supported = bool(
        unpaginated_list_shape
        and raw_covers_study
        and not missing_expiration_dates
        and filtered
    )
    return {
        "output": str(output_path),
        "sha256": sha256_file(output_path),
        "record_count": len(filtered),
        "expiration_count": len(expirations),
        "expiration_range_ms": [expirations[0], expirations[-1]] if expirations else None,
        "expiration_range_utc": [ms_to_iso(expirations[0]), ms_to_iso(expirations[-1])] if expirations else None,
        "study_expiration_dates_expected": expected_dates,
        "study_expiration_dates_present": expiration_dates,
        "missing_study_expiration_dates": missing_expiration_dates,
        "historical_endpoint_shape": {
            "result_type": "list",
            "pagination_cursor_observed": False,
            "raw_expired_record_count": len(expired_result),
            "raw_expiration_range_ms": raw_expiration_range,
            "raw_expiration_range_utc": [ms_to_iso(raw_expiration_range[0]), ms_to_iso(raw_expiration_range[1])]
            if raw_expiration_range
            else None,
            "raw_creation_range_ms": raw_creation_range,
            "raw_creation_range_utc": [ms_to_iso(raw_creation_range[0]), ms_to_iso(raw_creation_range[1])]
            if raw_creation_range
            else None,
            "raw_covers_study_expiries": raw_covers_study,
            "latest_expected_expiry_ms": expected_expiry_ms,
            "latest_expected_expiry_utc": ms_to_iso(expected_expiry_ms),
        },
        "complete_enough_for_exact_option_selection": exact_selection_supported,
        "expired_source": {
            "url": DERIBIT_HISTORY_INSTRUMENTS,
            "raw_record_count": len(expired_result),
            "meta": expired_meta,
        },
        "live_source": {
            "url": DERIBIT_LIVE_INSTRUMENTS,
            "raw_record_count": len(live_result),
            "meta": live_meta,
        },
        "completeness_note": (
            "Filtered from Deribit public historical BTC option instrument list plus live list. "
            "The historical endpoint returned one unpaginated list; this manifest records the raw expiration range "
            "and the per-delivery-date coverage used for this study. "
            "Selection can use creation_timestamp and expiration_timestamp to avoid future-listed strikes. "
            "No strikes are inferred from naming patterns."
        ),
    }


def build_deribit_delivery_prices(output_dir: Path, start_ms: int, end_ms: int) -> dict[str, Any]:
    cache_dir = output_dir / "cache" / "deribit"
    start_date = dt.datetime.fromtimestamp(start_ms / 1000, dt.UTC).date()
    end_date = dt.datetime.fromtimestamp(end_ms / 1000, dt.UTC).date()
    count = max(120, (end_date - start_date).days + 10)
    offset = 0
    all_items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []

    while True:
        url = DERIBIT_DELIVERY_PRICES.format(count=count, offset=offset)
        result, meta = deribit_json(url, cache_dir, timeout=60)
        if not isinstance(result, dict):
            raise TypeError("Deribit delivery result is not an object")
        batch = result.get("data") or []
        if not isinstance(batch, list):
            raise TypeError("Deribit delivery data is not a list")
        sources.append({"url": url, "record_count": len(batch), "meta": meta})
        all_items.extend(batch)
        if not batch:
            break
        oldest_date = min(item.get("date", "9999-99-99") for item in batch)
        if oldest_date <= start_date.isoformat():
            break
        offset += len(batch)
        if offset > 5000:
            break
        time.sleep(0.05)

    dedup: dict[str, dict[str, Any]] = {}
    cutoff_date = end_date.isoformat()
    for item in all_items:
        date_s = item.get("date")
        if not date_s:
            continue
        if date_s < start_date.isoformat() or date_s > cutoff_date:
            continue
        delivery_ms = iso_date_to_ms(date_s)
        if delivery_ms > end_ms:
            continue
        dedup[date_s] = {
            "date": date_s,
            "delivery_price": item.get("delivery_price"),
        }

    prices = [dedup[k] for k in sorted(dedup)]
    output_path = output_dir / "delivery_prices.json"
    write_json(output_path, prices)

    expected_dates = expected_delivery_dates(start_ms, end_ms)
    missing = [date_s for date_s in expected_dates if date_s not in dedup]

    return {
        "output": str(output_path),
        "sha256": sha256_file(output_path),
        "record_count": len(prices),
        "date_range": [prices[0]["date"], prices[-1]["date"]] if prices else None,
        "missing_dates": missing,
        "sources": sources,
        "completeness_note": (
            "Delivery prices are Deribit btc_usd official daily delivery prices at the expiry date. "
            "Dates whose 08:00 UTC delivery is after the freeze cutoff are excluded."
        ),
    }


def build_manifest(
    output_dir: Path,
    start_ms: int,
    end_ms: int,
    binance: dict[str, Any],
    instruments: dict[str, Any],
    deliveries: dict[str, Any],
) -> dict[str, Any]:
    manifest = {
        "generated_at_utc": utc_now_iso(),
        "tool": "tools/astra_light_study_data.py",
        "tool_version": "1.0.0",
        "scope": {
            "symbol": "BTCUSDT",
            "index_name": "btc_usd",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start_utc": ms_to_iso(start_ms),
            "end_utc": ms_to_iso(end_ms),
            "last_closed_kline_open_ms": last_closed_open_ms(end_ms),
            "last_closed_kline_open_utc": ms_to_iso(last_closed_open_ms(end_ms)),
            "freeze_note": "Rows after the supplied end-ms cutoff are excluded; unsettled delivery dates are not treated as delivered.",
        },
        "outputs": {
            "klines": binance,
            "instruments": instruments,
            "delivery_prices": deliveries,
        },
        "overall_completeness": {
            "klines_gap_count_after_rest": len(binance.get("missing_ranges_after_rest") or []),
            "delivery_missing_count": len(deliveries.get("missing_dates") or []),
            "instrument_source_complete_enough": instruments.get(
                "complete_enough_for_exact_option_selection", False
            ),
            "exact_option_selection_supported": instruments.get(
                "complete_enough_for_exact_option_selection", False
            ),
        },
    }
    manifest_path = output_dir / "data_manifest.json"
    write_json(manifest_path, manifest)
    checksums_path = output_dir / "SHA256SUMS.txt"
    checksum_lines = []
    for path in (
        output_dir / "klines.csv",
        output_dir / "instruments.json",
        output_dir / "delivery_prices.json",
        manifest_path,
    ):
        checksum_lines.append(f"{sha256_file(path)}  {path.name}\n")
    checksums_path.write_text("".join(checksum_lines), encoding="utf-8", newline="\n")
    manifest["outputs"]["checksums"] = {
        "output": str(checksums_path),
        "note": "Includes the data_manifest.json hash without embedding that hash inside the manifest itself.",
    }
    write_json(manifest_path, manifest)
    # Rebuild the checksum file after adding its pointer to the manifest.
    checksum_lines = []
    for path in (
        output_dir / "klines.csv",
        output_dir / "instruments.json",
        output_dir / "delivery_prices.json",
        manifest_path,
    ):
        checksum_lines.append(f"{sha256_file(path)}  {path.name}\n")
    checksums_path.write_text("".join(checksum_lines), encoding="utf-8", newline="\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Absolute output directory")
    parser.add_argument("--start-ms", required=True, type=int)
    parser.add_argument("--end-ms", required=True, type=int)
    args = parser.parse_args()

    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        raise SystemExit("--output must be an absolute path")
    output_dir.mkdir(parents=True, exist_ok=True)

    start_ms = (args.start_ms // MS_PER_MINUTE) * MS_PER_MINUTE
    end_ms = args.end_ms
    if end_ms <= start_ms:
        raise SystemExit("--end-ms must be after --start-ms")
    if last_closed_open_ms(end_ms) < start_ms:
        raise SystemExit("no fully closed one-minute bar in the requested range")

    binance = build_binance(output_dir, start_ms, end_ms)
    instruments = build_deribit_instruments(output_dir, start_ms, end_ms)
    deliveries = build_deribit_delivery_prices(output_dir, start_ms, end_ms)
    manifest = build_manifest(output_dir, start_ms, end_ms, binance, instruments, deliveries)

    summary = {
        "klines": {
            "records": binance.get("record_count"),
            "gaps_after_rest": len(binance.get("missing_ranges_after_rest") or []),
            "sha256": binance.get("sha256"),
        },
        "instruments": {
            "records": instruments.get("record_count"),
            "expirations": instruments.get("expiration_count"),
            "sha256": instruments.get("sha256"),
        },
        "delivery_prices": {
            "records": deliveries.get("record_count"),
            "missing_dates": len(deliveries.get("missing_dates") or []),
            "sha256": deliveries.get("sha256"),
        },
        "checksums": manifest["outputs"]["checksums"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
