#!/usr/bin/env python3
"""Point-in-time market data helpers for Astra joint research.

This module is research-only. It parses frozen Binance one-minute archives,
builds closed-bar feature snapshots, and can write partitioned Parquet outputs
for downstream model work. It does not import or change the FMZ strategy.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import zipfile
from collections import deque
from pathlib import Path
from typing import Any, Iterable

from astra_joint_contract import FEATURE_GROUPS


MINUTE_MS = 60_000
HOUR_MS = 3_600_000
PARTITION_SCHEMA = "astra_joint_market_partitions@1.0.0"
FEATURE_SCHEMA = "astra_joint_market_features@1.0.0"

BINANCE_COLUMNS = (
    "open_time_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time_ms",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
)


def normalize_timestamp_ms(value: Any) -> int:
    if value is None or value == "" or isinstance(value, bool):
        raise ValueError("missing timestamp")
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid timestamp") from exc
    if not math.isfinite(numeric):
        raise ValueError("invalid timestamp")
    raw = int(numeric)
    # Binance spot archives switched to microsecond timestamps in newer files.
    while abs(raw) >= 100_000_000_000_000:
        raw //= 1000
    if 0 < abs(raw) < 10_000_000_000:
        raw *= 1000
    return raw


def finite(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_date(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).date().isoformat()


def ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).isoformat()


def entry_open_ms(as_of_ms: int) -> int:
    return (int(as_of_ms) // MINUTE_MS) * MINUTE_MS + MINUTE_MS


def _coerce_timestamp_int(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric):
        return None
    return int(numeric)


def is_complete_minute_bar(row: Any, *, as_of_ms: int | None = None) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("forming_open_only") or row.get("path_fields_usable") is False:
        return False
    open_ms = _coerce_timestamp_int(row.get("open_time_ms"))
    close_ms = _coerce_timestamp_int(row.get("close_time_ms"))
    if open_ms is None or close_ms is None:
        return False
    if open_ms % MINUTE_MS != 0:
        return False
    if close_ms - open_ms != MINUTE_MS - 1:
        return False
    if as_of_ms is not None:
        as_of = _coerce_timestamp_int(as_of_ms)
        if as_of is None or close_ms >= as_of:
            return False
    return True


def normalize_kline(row: Any, source: str | None = None) -> dict[str, Any] | None:
    """Normalize a Binance kline row from CSV dicts, lists, or FMZ-style dicts."""
    if isinstance(row, (list, tuple)):
        raw = {name: row[index] for index, name in enumerate(BINANCE_COLUMNS[: len(row)])}
    elif isinstance(row, dict):
        raw = dict(row)
        if raw.get("forming_open_only") or raw.get("path_fields_usable") is False:
            return None
    else:
        return None

    def pick(*names: str) -> Any:
        for name in names:
            if name in raw:
                return raw[name]
            spaced = name.replace("_", " ")
            if spaced in raw:
                return raw[spaced]
            titled = spaced.title()
            if titled in raw:
                return raw[titled]
        return None

    try:
        open_ms = normalize_timestamp_ms(pick("open_time_ms", "open_time", "Open time"))
        close_ms = normalize_timestamp_ms(pick("close_time_ms", "close_time", "Close time"))
    except (TypeError, ValueError):
        return None
    if not is_complete_minute_bar({"open_time_ms": open_ms, "close_time_ms": close_ms}):
        return None

    open_price = finite(pick("open", "Open"))
    high = finite(pick("high", "High"))
    low = finite(pick("low", "Low"))
    close = finite(pick("close", "Close"))
    volume = finite(pick("volume", "Volume"))
    if (
        open_price is None
        or high is None
        or low is None
        or close is None
        or volume is None
        or open_price <= 0
        or high < low
        or close_ms < open_ms
    ):
        return None

    normalized = {
        "open_time_ms": open_ms,
        "open_time": open_ms,
        "close_time_ms": close_ms,
        "close_time": close_ms,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "quote_volume": finite(pick("quote_volume", "quote_asset_volume", "Quote asset volume")),
        "trade_count": finite(pick("trade_count", "number_of_trades", "Number of trades")),
        "taker_buy_base_volume": finite(
            pick("taker_buy_base_volume", "taker_buy_base_asset_volume", "Taker buy base asset volume")
        ),
        "taker_buy_quote_volume": finite(
            pick("taker_buy_quote_volume", "taker_buy_quote_asset_volume", "Taker buy quote asset volume")
        ),
        "date_utc": utc_date(open_ms),
    }
    if source:
        normalized["source"] = source
    return normalized


def read_binance_klines(path: Path) -> list[dict[str, Any]]:
    rows = list(iter_binance_klines(path))
    rows.sort(key=lambda item: item["open_time_ms"])
    return rows


def iter_binance_klines(path: Path) -> Iterable[dict[str, Any]]:
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not names:
                raise ValueError(f"no CSV member in {path}")
            with archive.open(names[0]) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                yield from _iter_binance_csv_rows(text, str(path))
    else:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            yield from _iter_binance_csv_rows(stream, str(path))


def _read_binance_csv_rows(stream: Iterable[str], source: str) -> list[dict[str, Any]]:
    return list(_iter_binance_csv_rows(stream, source))


def _iter_binance_csv_rows(stream: Iterable[str], source: str) -> Iterable[dict[str, Any]]:
    reader = csv.reader(stream)
    for raw_row in reader:
        if not raw_row:
            continue
        if str(raw_row[0]).strip().lower() in {"open_time", "open time", "open_time_ms"}:
            continue
        row = normalize_kline(raw_row, source=source)
        if row is not None:
            yield row


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    os.replace(tmp, path)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_partitioned_parquet(
    rows: list[dict[str, Any]],
    output_dir: Path,
    *,
    partition_field: str = "date_utc",
    compression: str = "snappy",
) -> dict[str, Any]:
    """Write normalized rows to partitioned Parquet files.

    The heavy parquet dependencies are imported only here so the pure event
    tests can run with the standard library.
    """
    if not rows:
        raise ValueError("no rows supplied")
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("pandas and a parquet engine are required for parquet export") from exc

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    if partition_field not in df.columns:
        raise KeyError(f"partition field missing: {partition_field}")

    partitions: list[dict[str, Any]] = []
    for key, group in df.groupby(partition_field, dropna=False, sort=True):
        label = str(key) if key == key else "unknown"
        part_dir = output_dir / f"{partition_field}={label}"
        part_dir.mkdir(parents=True, exist_ok=True)
        file_path = part_dir / "part-000.parquet"
        try:
            group.to_parquet(file_path, index=False, compression=compression)
        except Exception as exc:  # pragma: no cover - engine dependent
            raise RuntimeError("failed to write parquet; install pyarrow or fastparquet") from exc
        partitions.append(
            {
                "partition": label,
                "path": str(file_path),
                "rows": int(len(group)),
                "sha256": sha256_file(file_path),
            }
        )

    manifest = {
        "schema": PARTITION_SCHEMA,
        "partition_field": partition_field,
        "compression": compression,
        "row_count": len(rows),
        "partitions": partitions,
    }
    write_json(output_dir / "parquet_manifest.json", manifest)
    return manifest


def contiguous_gaps(rows: list[dict[str, Any]]) -> list[dict[str, int]]:
    gaps: list[dict[str, int]] = []
    sorted_rows = sorted(rows, key=lambda item: item["open_time_ms"])
    for left, right in zip(sorted_rows, sorted_rows[1:]):
        expected = int(left["open_time_ms"]) + MINUTE_MS
        actual = int(right["open_time_ms"])
        if actual != expected:
            gaps.append(
                {
                    "after_open_ms": int(left["open_time_ms"]),
                    "expected_next_open_ms": expected,
                    "actual_next_open_ms": actual,
                    "missing_minutes": max(0, int((actual - expected) // MINUTE_MS)),
                }
            )
    return gaps


class HistoricalBars:
    """Closed one-minute bars with point-in-time feature snapshots."""

    def __init__(self, rows: Iterable[Any]):
        dedup: dict[int, dict[str, Any]] = {}
        for raw in rows:
            row = normalize_kline(raw)
            if row is not None:
                dedup.setdefault(row["open_time_ms"], row)
        self.rows = [dedup[key] for key in sorted(dedup)]
        self.open_times = [int(row["open_time_ms"]) for row in self.rows]
        self.close_times = [int(row["close_time_ms"]) for row in self.rows]
        self.by_open = {int(row["open_time_ms"]): row for row in self.rows}

    def row_at(self, open_ms: int) -> dict[str, Any] | None:
        return self.by_open.get(int(open_ms))

    def open_at(self, open_ms: int) -> float | None:
        row = self.row_at(open_ms)
        return row.get("open") if row else None

    def closed_window(self, as_of_ms: int, minutes: int) -> list[dict[str, Any]] | None:
        as_of = int(as_of_ms)
        end = bisect.bisect_left(self.close_times, as_of)
        if end < minutes:
            return None
        window = self.rows[end - minutes : end]
        if as_of - int(window[-1]["close_time_ms"]) >= MINUTE_MS:
            return None
        return window if _is_complete_contiguous_window(window, as_of) else None

    def range_by_open(self, start_open_ms: int, end_open_ms: int) -> list[dict[str, Any]] | None:
        if end_open_ms < start_open_ms:
            return []
        result = []
        for open_ms in range(int(start_open_ms), int(end_open_ms) + MINUTE_MS, MINUTE_MS):
            row = self.row_at(open_ms)
            if row is None:
                return None
            result.append(row)
        return result

    def last_close_at_or_before(self, as_of_ms: int) -> float | None:
        index = bisect.bisect_left(self.close_times, int(as_of_ms)) - 1
        if index < 0:
            return None
        return self.rows[index]["close"]

    def feature_snapshot(
        self,
        as_of_ms: int,
        *,
        shock_as_of_ms: int | None = None,
        shock_direction: int | None = None,
        shock_magnitude: float | None = None,
        elapsed_from_shock_min: float | None = None,
    ) -> dict[str, Any]:
        features = {name: None for name in FEATURE_GROUPS["joint"]}
        features.update(
            {
                "feature_schema": FEATURE_SCHEMA,
                "feature_as_of_ms": int(as_of_ms),
                "feature_as_of_utc": ms_to_iso(int(as_of_ms)),
                "dte_hours": None,
                "short_distance_fraction": None,
                "width_fraction": None,
                "side_sign": None,
                "hour_sin": _hour_sin(int(as_of_ms)),
                "hour_cos": _hour_cos(int(as_of_ms)),
                "shock_magnitude": shock_magnitude,
                "elapsed_from_shock_min": elapsed_from_shock_min,
                "window_status": {},
            }
        )

        summaries = {minutes: self.window_summary(as_of_ms, minutes) for minutes in (15, 30, 240, 720, 1440)}
        for minutes, summary in summaries.items():
            suffix = str(minutes)
            features["window_status"][suffix] = summary["status"]
            if summary["status"] != "available":
                continue
            if f"ret_{suffix}" in features:
                features[f"ret_{suffix}"] = summary["return_fraction"]
            if f"vol_{suffix}" in features:
                features[f"vol_{suffix}"] = summary["realized_vol"]
            if f"net_flow_{suffix}" in features:
                features[f"net_flow_{suffix}"] = summary["net_flow_ratio"]
            if minutes in (15, 30):
                features[f"vwap_deviation_{suffix}"] = summary["vwap_deviation"]
                features[f"range_position_{suffix}"] = summary["range_position"]
                features[f"efficiency_{suffix}"] = summary["efficiency"]
                features[f"pressure_response_{suffix}"] = summary["pressure_response"]
            if minutes == 15:
                features["vwap_migration_15"] = self.vwap_migration(as_of_ms, 15)
                features["range_expansion_15"] = self.range_expansion(as_of_ms, 15)

        moves = self.moves_since(as_of_ms, shock_as_of_ms, shock_direction) if shock_as_of_ms is not None else {}
        features.update(moves)
        if "adverse_move_since_shock" in moves:
            features["adverse_move_since_shock"] = moves["adverse_move_since_shock"]
            features["favorable_move_since_shock"] = moves["favorable_move_since_shock"]
        return features

    def window_summary(self, as_of_ms: int, minutes: int) -> dict[str, Any]:
        rows = self.closed_window(as_of_ms, minutes)
        if rows is None:
            return {"status": "insufficient_or_gap", "minutes": minutes}
        return _summarize_rows(rows, minutes)

    def vwap_migration(self, as_of_ms: int, minutes: int) -> float | None:
        current = self.closed_window(as_of_ms, minutes)
        if current is None:
            return None
        previous_end = int(current[0]["open_time_ms"])
        previous = self.closed_window(previous_end, minutes)
        if previous is None:
            return None
        current_vwap = _vwap(current)
        previous_vwap = _vwap(previous)
        if current_vwap is None or previous_vwap is None or previous_vwap <= 0:
            return None
        return current_vwap / previous_vwap - 1.0

    def range_expansion(self, as_of_ms: int, minutes: int) -> float | None:
        current = self.closed_window(as_of_ms, minutes)
        if current is None:
            return None
        previous_end = int(current[0]["open_time_ms"])
        previous = self.closed_window(previous_end, minutes)
        if previous is None:
            return None
        current_range = _range_fraction(current)
        previous_range = _range_fraction(previous)
        if current_range is None or previous_range is None or previous_range <= 0:
            return None
        return current_range / previous_range - 1.0

    def moves_since(
        self,
        as_of_ms: int,
        shock_as_of_ms: int | None,
        shock_direction: int | None,
    ) -> dict[str, Any]:
        if shock_as_of_ms is None:
            return {}
        shock_index = bisect.bisect_right(self.close_times, int(shock_as_of_ms)) - 1
        if shock_index < 0:
            return {}
        start_open = int(self.rows[shock_index]["open_time_ms"])
        current_index = bisect.bisect_right(self.close_times, int(as_of_ms)) - 1
        if current_index < 0:
            return {}
        end_open = int(self.rows[current_index]["open_time_ms"])
        shock_price = self.last_close_at_or_before(shock_as_of_ms)
        if shock_price is None or shock_price <= 0:
            return {}
        return {
            "shock_price": shock_price,
            **self._moves_between(start_open, end_open, shock_price, shock_direction),
        }

    def _moves_between(
        self,
        start_open_ms: int,
        end_open_ms: int,
        shock_price: float,
        shock_direction: int | None,
    ) -> dict[str, Any]:
        rows = self.range_by_open(start_open_ms, end_open_ms)
        if not rows:
            return {}
        high = max(row["high"] for row in rows)
        low = min(row["low"] for row in rows)
        up = max(high - shock_price, 0.0) / shock_price
        down = max(shock_price - low, 0.0) / shock_price
        if shock_direction and shock_direction < 0:
            favorable, adverse = down, up
        else:
            favorable, adverse = up, down
        return {
            "up_move_since_shock": up,
            "down_move_since_shock": down,
            "put_adverse_move_since_shock": down,
            "call_adverse_move_since_shock": up,
            "adverse_move_since_shock": adverse,
            "favorable_move_since_shock": favorable,
        }


class RollingHistoricalBars(HistoricalBars):
    """Bounded closed-bar store for streaming event replay.

    It presents the same feature methods as HistoricalBars while retaining only
    a rolling 1441-minute buffer by default.
    """

    def __init__(self, max_minutes: int = 1441):
        self.max_minutes = int(max_minutes)
        self.rows: deque[dict[str, Any]] = deque(maxlen=self.max_minutes)
        self.close_times: deque[int] = deque(maxlen=self.max_minutes)
        self.by_open: dict[int, dict[str, Any]] = {}

    def __len__(self) -> int:
        return len(self.rows)

    def clear(self) -> None:
        self.rows.clear()
        self.close_times.clear()
        self.by_open.clear()

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "schema": "astra_joint_rolling_historical_bars@1.0.0",
            "max_minutes": self.max_minutes,
            "rows": list(self.rows),
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "RollingHistoricalBars":
        if not isinstance(snapshot, dict) or snapshot.get("schema") != "astra_joint_rolling_historical_bars@1.0.0":
            raise ValueError("unsupported rolling bars snapshot")
        store = cls(max_minutes=int(snapshot.get("max_minutes") or 1441))
        for row in snapshot.get("rows") or []:
            store.append(row)
        return store

    def append(self, raw: Any) -> dict[str, Any] | None:
        row = normalize_kline(raw)
        if row is None:
            return None
        open_ms = int(row["open_time_ms"])
        if open_ms in self.by_open:
            for index, existing in enumerate(self.rows):
                if int(existing["open_time_ms"]) == open_ms:
                    self.rows[index] = row
                    self.close_times[index] = int(row["close_time_ms"])
                    break
            self.by_open[open_ms] = row
            return row
        if len(self.rows) == self.max_minutes:
            old = self.rows[0]
            self.by_open.pop(int(old["open_time_ms"]), None)
        self.rows.append(row)
        self.close_times.append(int(row["close_time_ms"]))
        self.by_open[open_ms] = row
        return row

    def row_at(self, open_ms: int) -> dict[str, Any] | None:
        return self.by_open.get(int(open_ms))

    def closed_window(self, as_of_ms: int, minutes: int) -> list[dict[str, Any]] | None:
        rows = list(self.rows)
        close_times = list(self.close_times)
        as_of = int(as_of_ms)
        end = bisect.bisect_left(close_times, as_of)
        if end < minutes:
            return None
        window = rows[end - minutes : end]
        if as_of - int(window[-1]["close_time_ms"]) >= MINUTE_MS:
            return None
        return window if _is_complete_contiguous_window(window, as_of) else None

    def range_by_open(self, start_open_ms: int, end_open_ms: int) -> list[dict[str, Any]] | None:
        if end_open_ms < start_open_ms:
            return []
        result = []
        for open_ms in range(int(start_open_ms), int(end_open_ms) + MINUTE_MS, MINUTE_MS):
            row = self.by_open.get(open_ms)
            if row is None:
                return None
            result.append(row)
        return result

    def last_close_at_or_before(self, as_of_ms: int) -> float | None:
        rows = list(self.rows)
        close_times = list(self.close_times)
        index = bisect.bisect_left(close_times, int(as_of_ms)) - 1
        if index < 0:
            return None
        return rows[index]["close"]

    def moves_since(
        self,
        as_of_ms: int,
        shock_as_of_ms: int | None,
        shock_direction: int | None,
    ) -> dict[str, Any]:
        if shock_as_of_ms is None:
            return {}
        rows = list(self.rows)
        close_times = list(self.close_times)
        shock_index = bisect.bisect_right(close_times, int(shock_as_of_ms)) - 1
        current_index = bisect.bisect_right(close_times, int(as_of_ms)) - 1
        if shock_index < 0 or current_index < 0:
            return {}
        start_open = int(rows[shock_index]["open_time_ms"])
        end_open = int(rows[current_index]["open_time_ms"])
        shock_price = rows[shock_index]["close"]
        if shock_price is None or shock_price <= 0:
            return {}
        return {
            "shock_price": shock_price,
            **self._moves_between(start_open, end_open, shock_price, shock_direction),
        }


def _is_contiguous(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    for left, right in zip(rows, rows[1:]):
        if int(right["open_time_ms"]) - int(left["open_time_ms"]) != MINUTE_MS:
            return False
    return True


def _is_complete_contiguous_window(rows: list[dict[str, Any]], as_of_ms: int) -> bool:
    return _is_contiguous(rows) and all(is_complete_minute_bar(row, as_of_ms=as_of_ms) for row in rows)


def _summarize_rows(rows: list[dict[str, Any]], minutes: int) -> dict[str, Any]:
    first = rows[0]["open"]
    last = rows[-1]["close"]
    high = max(row["high"] for row in rows)
    low = min(row["low"] for row in rows)
    path = _path_length(rows)
    return_fraction = last / first - 1.0 if first > 0 else None
    range_fraction = _range_fraction(rows)
    net_flow_ratio = _net_flow_ratio(rows)
    vwap = _vwap(rows)
    return {
        "status": "available",
        "minutes": minutes,
        "first_open_ms": rows[0]["open_time_ms"],
        "last_close_ms": rows[-1]["close_time_ms"],
        "first_open": first,
        "last_close": last,
        "high": high,
        "low": low,
        "return_fraction": return_fraction,
        "realized_vol": _stddev(_log_returns(rows)) * math.sqrt(float(len(rows))),
        "range_fraction": range_fraction,
        "range_position": (last - low) / (high - low) if high > low else None,
        "efficiency": abs(last - first) / path if path and path > 0 else 0.0,
        "net_flow_ratio": net_flow_ratio,
        "vwap": vwap,
        "vwap_deviation": (last / vwap - 1.0) if vwap and vwap > 0 else None,
        "pressure_response": _pressure_response(return_fraction, range_fraction, net_flow_ratio),
    }


def _log_returns(rows: list[dict[str, Any]]) -> list[float]:
    prices = [rows[0]["open"]] + [row["close"] for row in rows]
    return [math.log(right / left) for left, right in zip(prices, prices[1:]) if left > 0 and right > 0]


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / float(len(values))
    variance = sum((value - mean) ** 2 for value in values) / float(len(values))
    return math.sqrt(max(0.0, variance))


def _path_length(rows: list[dict[str, Any]]) -> float:
    prices = [rows[0]["open"]] + [row["close"] for row in rows]
    return sum(abs(right - left) for left, right in zip(prices, prices[1:]))


def _range_fraction(rows: list[dict[str, Any]]) -> float | None:
    first = rows[0]["open"]
    if first <= 0:
        return None
    high = max(row["high"] for row in rows)
    low = min(row["low"] for row in rows)
    return (high - low) / first


def _net_flow_ratio(rows: list[dict[str, Any]]) -> float | None:
    volumes: list[float] = []
    taker_buys: list[float] = []
    for row in rows:
        volume = row.get("volume")
        taker_buy = row.get("taker_buy_base_volume")
        if (
            not isinstance(volume, (int, float))
            or not math.isfinite(float(volume))
            or not isinstance(taker_buy, (int, float))
            or not math.isfinite(float(taker_buy))
        ):
            return None
        volume_f = float(volume)
        taker_buy_f = float(taker_buy)
        if volume_f < 0 or taker_buy_f < 0 or taker_buy_f > volume_f + 1e-9:
            return None
        volumes.append(volume_f)
        taker_buys.append(taker_buy_f)
    volume = sum(volumes)
    taker_buy = sum(taker_buys)
    if volume <= 0:
        return None
    return (2.0 * taker_buy - volume) / volume


def _vwap(rows: list[dict[str, Any]]) -> float | None:
    quote_volumes: list[float] = []
    base_volumes: list[float] = []
    for row in rows:
        quote_volume = row.get("quote_volume")
        base_volume = row.get("volume")
        if (
            not isinstance(quote_volume, (int, float))
            or not math.isfinite(float(quote_volume))
            or not isinstance(base_volume, (int, float))
            or not math.isfinite(float(base_volume))
        ):
            return None
        quote_volume_f = float(quote_volume)
        base_volume_f = float(base_volume)
        if quote_volume_f < 0 or base_volume_f < 0:
            return None
        quote_volumes.append(quote_volume_f)
        base_volumes.append(base_volume_f)
    quote_volume = sum(quote_volumes)
    base_volume = sum(base_volumes)
    if quote_volume > 0 and base_volume > 0:
        return quote_volume / base_volume
    return None


def _pressure_response(ret: float | None, range_fraction: float | None, net_flow: float | None) -> float | None:
    if ret is None or range_fraction is None or range_fraction <= 0 or net_flow is None:
        return None
    directional_efficiency = max(-1.0, min(1.0, ret / range_fraction))
    return net_flow * directional_efficiency


def _hour_fraction(ms: int) -> float:
    value = dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC)
    return (value.hour * 60 + value.minute + value.second / 60.0) / (24.0 * 60.0)


def _hour_sin(ms: int) -> float:
    return math.sin(2.0 * math.pi * _hour_fraction(ms))


def _hour_cos(ms: int) -> float:
    return math.cos(2.0 * math.pi * _hour_fraction(ms))


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    convert = sub.add_parser("convert-binance", help="Normalize one or more Binance CSV/ZIP files.")
    convert.add_argument("--input", nargs="+", type=Path, required=True)
    convert.add_argument("--output-dir", type=Path, required=True)
    convert.add_argument("--csv-copy", type=Path)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for path in args.input:
        rows.extend(read_binance_klines(path))
    rows.sort(key=lambda item: item["open_time_ms"])
    if args.csv_copy:
        write_csv(args.csv_copy, rows)
    manifest = write_partitioned_parquet(rows, args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
