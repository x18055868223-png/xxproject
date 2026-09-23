"""Prerecorded 4h market regime view for Astra v1.6 diagnostics.

API and schema:
- ``regime_boundaries(as_of_ms)`` returns the 49 UTC 5-minute boundary
  timestamps used for one REGIME_4H_V0 observation.
- ``required_minute_open_times(as_of_ms)`` returns the 241 Binance-style 1m
  open timestamps needed to prove the 49 boundary closes and the 48 complete
  intervening 5m bars.
- ``build_regime_4h_v0(minute_rows, as_of_ms, source_identity=...)`` returns a
  single dict with ``as_of_ms, regime, R4, V4, E4, Z4, RV_ANN, status_reason``.
- ``build_regimes_4h_v0(...)`` deduplicates repeated market times so Put/Call
  rows at the same as-of consume one market state.
- ``load_um_minute_parquet(path, source_identity=...)`` validates a normalized
  UM 1m Parquet schema before handing rows to the pure calculator.
- ``build_from_facts(facts_dir, asof_values)`` reads only the needed UM fact
  partitions, builds one indexed minute cache, and returns a pandas DataFrame.

Minute rows must provide ``open_time_ms``, ``close_time_ms`` and ``close``.
Optional row source fields, when present, must identify Binance UM futures.
The calculator does not train, fit thresholds, read labels, reuse ``vol_240``,
or calculate historical performance.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
from typing import Any


MINUTE_MS = 60_000
FIVE_MINUTE_MS = 5 * MINUTE_MS
RETURN_COUNT = 48
BOUNDARY_COUNT = RETURN_COUNT + 1
REGIME_SCHEMA = "REGIME_4H_V0"
OUTPUT_COLUMNS = ("as_of_ms", "regime", "R4", "V4", "E4", "Z4", "RV_ANN", "rv4_annualized", "status_reason")
REQUIRED_MINUTE_COLUMNS = ("open_time_ms", "close_time_ms", "close")
SOURCE_COLUMNS = ("source", "source_file", "feed", "market", "market_type", "price_source")
OPTIONAL_MINUTE_COLUMNS = (*SOURCE_COLUMNS, "available_at_ms", "source_sha256")
UM_SOURCE_ALIASES = frozenset(
    {
        "um",
        "um_1m",
        "um_futures",
        "binance_um",
        "binance_um_1m",
        "binance_usdm",
        "binance_usdm_1m",
        "binance_usdm_futures",
    }
)
SPOT_SOURCE_ALIASES = frozenset({"spot", "spot_1m", "binance_spot", "binance_spot_1m"})


class _MinuteIndex:
    def __init__(self, rows_by_open: dict[int, Mapping[str, Any]], open_times: list[int], duplicate_opens: set[int]):
        self.rows_by_open = rows_by_open
        self.open_times = open_times
        self.duplicate_opens = duplicate_opens

    @classmethod
    def from_rows(cls, minute_rows: Iterable[Mapping[str, Any]]) -> "_MinuteIndex":
        rows_by_open: dict[int, Mapping[str, Any]] = {}
        duplicate_opens: set[int] = set()
        for row in minute_rows:
            open_ms = _int_ms(row.get("open_time_ms"))
            if open_ms is None:
                continue
            if open_ms in rows_by_open:
                duplicate_opens.add(open_ms)
                continue
            rows_by_open[open_ms] = row
        return cls(rows_by_open, sorted(rows_by_open), duplicate_opens)

    def required_rows(self, needed_opens: Sequence[int]) -> tuple[list[Mapping[str, Any]], str | None]:
        if not needed_opens:
            return [], "missing_minute"
        lo = bisect_left(self.open_times, needed_opens[0])
        hi = bisect_right(self.open_times, needed_opens[-1])
        if hi - lo < len(needed_opens):
            return [], "missing_minute"
        if any(open_ms in self.duplicate_opens for open_ms in needed_opens):
            return [], "duplicate_minute"
        rows: list[Mapping[str, Any]] = []
        for open_ms in needed_opens:
            row = self.rows_by_open.get(open_ms)
            if row is None:
                return [], "missing_minute"
            rows.append(row)
        return rows, None


def regime_boundaries(as_of_ms: int) -> list[int]:
    """Return the 49 fixed UTC 5-minute boundary timestamps for ``as_of_ms``."""

    value = _int_ms(as_of_ms)
    if value is None:
        raise ValueError("as_of_ms must be an integer millisecond timestamp")
    end_boundary = (value // FIVE_MINUTE_MS) * FIVE_MINUTE_MS
    start_boundary = end_boundary - RETURN_COUNT * FIVE_MINUTE_MS
    return [start_boundary + i * FIVE_MINUTE_MS for i in range(BOUNDARY_COUNT)]


def required_minute_open_times(as_of_ms: int) -> list[int]:
    """Return all 1m open timestamps required by one REGIME_4H_V0 view."""

    boundaries = regime_boundaries(as_of_ms)
    first_open = boundaries[0] - MINUTE_MS
    last_open = boundaries[-1] - MINUTE_MS
    return list(range(first_open, last_open + MINUTE_MS, MINUTE_MS))


def build_regime_4h_v0(
    minute_rows: Iterable[Mapping[str, Any]],
    as_of_ms: int,
    *,
    source_identity: str,
) -> dict[str, Any]:
    """Build one preregistered 4h UM regime observation.

    ``source_identity`` is required so callers cannot silently pass spot or an
    unlabelled price stream as the UM state source.
    """

    return _build_regime_from_index(_MinuteIndex.from_rows(minute_rows), as_of_ms, source_identity=source_identity)


def _build_regime_from_index(
    minute_index: _MinuteIndex,
    as_of_ms: int,
    *,
    source_identity: str,
) -> dict[str, Any]:
    asof = _int_ms(as_of_ms)
    if asof is None or asof <= 0:
        return _unknown(as_of_ms, "invalid_as_of_ms")
    if not _is_um_source(source_identity):
        return _unknown(asof, "source_not_um")

    try:
        boundaries = regime_boundaries(asof)
        needed_opens = required_minute_open_times(asof)
    except ValueError:
        return _unknown(asof, "invalid_as_of_ms")

    rows, missing_reason = minute_index.required_rows(needed_opens)
    if missing_reason is not None:
        return _unknown(asof, missing_reason)

    closes_by_open: dict[int, float] = {}
    for open_ms, row in zip(needed_opens, rows):
        if not _row_source_is_um(row):
            return _unknown(asof, "source_not_um")
        available_at_ms = row.get("available_at_ms")
        if available_at_ms is not None:
            available = _int_ms(available_at_ms)
            if available is None:
                return _unknown(asof, "invalid_available_at_ms")
            if available > asof:
                return _unknown(asof, "available_after_asof")
        close_ms = _int_ms(row.get("close_time_ms"))
        if close_ms != open_ms + MINUTE_MS - 1:
            return _unknown(asof, "invalid_minute_shape")
        if close_ms >= asof:
            return _unknown(asof, "minute_not_closed_before_asof")
        close = _finite_float(row.get("close"))
        if close is None or close <= 0.0:
            return _unknown(asof, "non_positive_close")
        closes_by_open[open_ms] = close

    boundary_closes = [closes_by_open[boundary - MINUTE_MS] for boundary in boundaries]
    returns = [math.log(boundary_closes[i] / boundary_closes[i - 1]) for i in range(1, len(boundary_closes))]
    if len(returns) != RETURN_COUNT or any(not math.isfinite(value) for value in returns):
        return _unknown(asof, "invalid_return")

    r4 = math.fsum(returns)
    sum_abs = math.fsum(abs(value) for value in returns)
    sum_sq = math.fsum(value * value for value in returns)
    v4 = math.sqrt(sum_sq)
    if sum_abs <= 0.0 or v4 <= 0.0:
        return _unknown(asof, "zero_return_denominator")

    e4 = r4 / sum_abs
    z4 = r4 / v4
    rv_ann = v4 * math.sqrt(365 * 24 / 4)
    if not all(math.isfinite(value) for value in (r4, v4, e4, z4, rv_ann)):
        return _unknown(asof, "invalid_metric")

    return {
        "as_of_ms": asof,
        "regime": classify_regime(e4, z4),
        "R4": r4,
        "V4": v4,
        "E4": e4,
        "Z4": z4,
        "RV_ANN": rv_ann,
        "rv4_annualized": rv_ann,
        "status_reason": "ok",
    }


def build_regimes_4h_v0(
    minute_rows: Iterable[Mapping[str, Any]],
    as_of_ms_values: Iterable[int],
    *,
    source_identity: str,
) -> list[dict[str, Any]]:
    """Build one state per distinct market as-of time in first-seen order."""

    minute_index = _MinuteIndex.from_rows(minute_rows)
    seen: set[int] = set()
    results: list[dict[str, Any]] = []
    for raw_asof in as_of_ms_values:
        asof = _int_ms(raw_asof)
        key = asof if asof is not None else raw_asof
        if key in seen:
            continue
        seen.add(key)
        results.append(_build_regime_from_index(minute_index, raw_asof, source_identity=source_identity))
    return results


def classify_regime(e4: float, z4: float) -> str:
    """Classify valid E4/Z4 metrics into the fixed REGIME_4H_V0 buckets."""

    if e4 >= 0.30 and z4 >= 1.0:
        return "UP"
    if e4 <= -0.30 and z4 <= -1.0:
        return "DOWN"
    if abs(e4) <= 0.15 and abs(z4) <= 0.75:
        return "RANGE"
    return "MIXED"


def validate_um_minute_schema(frame: Any, *, source_identity: str) -> Any:
    """Validate a normalized UM 1m frame before REGIME_4H_V0 consumption."""

    columns = tuple(str(col) for col in getattr(frame, "columns", ()))
    _validate_column_names(columns, source_identity=source_identity)
    for column in SOURCE_COLUMNS:
        if column not in columns:
            continue
        values = getattr(frame[column], "dropna")().unique().tolist()
        bad = [value for value in values if _source_token(value) and _source_identity(value) != "um"]
        if bad:
            raise ValueError(f"{column} contains non-UM source identity")
    return frame


def load_um_minute_parquet(path: str | Path, *, source_identity: str) -> Any:
    """Read a normalized UM 1m Parquet file after schema/source validation."""

    parquet_path = Path(path)
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]

        schema = pq.read_schema(parquet_path)
        _validate_column_names(tuple(schema.names), source_identity=source_identity)
        columns = [column for column in (*REQUIRED_MINUTE_COLUMNS, *OPTIONAL_MINUTE_COLUMNS) if column in schema.names]
        import pandas as pd  # type: ignore[import-not-found]

        frame = pd.read_parquet(parquet_path, columns=columns)
    except ImportError:
        import pandas as pd  # type: ignore[import-not-found]

        frame = pd.read_parquet(parquet_path)
    return validate_um_minute_schema(frame, source_identity=source_identity)


def build_from_facts(facts_dir: str | Path, asof_values: Iterable[int]) -> Any:
    """Build REGIME_4H_V0 rows from normalized UM facts without scoring labels."""

    import pandas as pd  # type: ignore[import-not-found]

    unique_asofs: list[Any] = []
    seen: set[Any] = set()
    valid_asofs: list[int] = []
    for raw_asof in asof_values:
        asof = _int_ms(raw_asof)
        key = asof if asof is not None else raw_asof
        if key in seen:
            continue
        seen.add(key)
        unique_asofs.append(raw_asof)
        if asof is not None and asof > 0:
            valid_asofs.append(asof)

    source_hashes: dict[str, str] = {}
    raw_source_hashes: list[str] = []
    files_read: list[str] = []
    records: list[dict[str, Any]] = []
    if valid_asofs:
        min_open = min(required_minute_open_times(asof)[0] for asof in valid_asofs)
        max_open = max(required_minute_open_times(asof)[-1] for asof in valid_asofs)
        facts, meta = _load_fact_window(Path(facts_dir), min_open, max_open)
        source_hashes = meta["source_hashes"]
        raw_source_hashes = meta["raw_source_hashes"]
        files_read = meta["files_read"]
        records = facts.to_dict("records")

    result = pd.DataFrame(build_regimes_4h_v0(records, unique_asofs, source_identity="binance_um_1m"))
    if "RV_ANN" in result.columns and "rv4_annualized" not in result.columns:
        result["rv4_annualized"] = result["RV_ANN"]
    result.attrs["schema"] = REGIME_SCHEMA
    result.attrs["source_hashes"] = source_hashes
    result.attrs["raw_source_hashes"] = raw_source_hashes
    result.attrs["files_read"] = files_read
    return result


def _unknown(as_of_ms: Any, reason: str) -> dict[str, Any]:
    asof = _int_ms(as_of_ms)
    return {
        "as_of_ms": asof if asof is not None else as_of_ms,
        "regime": "UNKNOWN",
        "R4": None,
        "V4": None,
        "E4": None,
        "Z4": None,
        "RV_ANN": None,
        "rv4_annualized": None,
        "status_reason": reason,
    }


def _validate_column_names(columns: Sequence[str], *, source_identity: str) -> None:
    missing = [column for column in REQUIRED_MINUTE_COLUMNS if column not in columns]
    if missing:
        raise ValueError(f"missing required UM 1m columns: {', '.join(missing)}")
    if not _is_um_source(source_identity):
        raise ValueError("source_identity must identify Binance UM 1m data")


def _row_source_is_um(row: Mapping[str, Any]) -> bool:
    saw_source_value = False
    saw_um_value = False
    for column in SOURCE_COLUMNS:
        if column not in row:
            continue
        token = _source_token(row.get(column))
        if not token:
            continue
        saw_source_value = True
        identity = _source_identity(token)
        if identity != "um":
            return False
        saw_um_value = True
    return (not saw_source_value) or saw_um_value


def _is_um_source(value: Any) -> bool:
    return _source_identity(value) == "um"


def _source_identity(value: Any) -> str | None:
    token = _source_token(value)
    if token is None:
        return None
    if token in UM_SOURCE_ALIASES:
        return "um"
    if token in SPOT_SOURCE_ALIASES:
        return "spot"
    path = token.replace("\\", "/").replace(":", "/")
    parts = [part for part in path.split("/") if part]
    if "binance" in parts and "spot" in parts:
        return "spot"
    if "binance" in parts and "um" in parts and "spot" not in parts:
        return "um"
    return None


def _source_token(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if not token or token == "nan":
        return None
    return token


def _int_ms(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            return None
        return int(value)
    try:
        text = str(value).strip()
    except Exception:
        return None
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number) or number != int(number):
        return None
    return int(number)


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _load_fact_window(facts_dir: Path, min_open_ms: int, max_open_ms: int) -> tuple[Any, dict[str, Any]]:
    import pandas as pd  # type: ignore[import-not-found]

    files = _choose_fact_files(facts_dir, min_open_ms, max_open_ms)
    frames: list[Any] = []
    for path in files:
        frame = load_um_minute_parquet(path, source_identity="binance_um_1m")
        open_ms = pd.to_numeric(frame["open_time_ms"], errors="coerce")
        mask = (open_ms >= min_open_ms) & (open_ms <= max_open_ms)
        frames.append(frame.loc[mask].copy())
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.sort_values("open_time_ms", kind="mergesort").reset_index(drop=True)
    else:
        combined = pd.DataFrame(columns=list(REQUIRED_MINUTE_COLUMNS))
    source_hashes = {str(path): _sha256_file(path) for path in files}
    raw_source_hashes: list[str] = []
    if "source_sha256" in combined.columns:
        raw_source_hashes = sorted(str(value) for value in combined["source_sha256"].dropna().unique().tolist())
    return combined, {
        "source_hashes": source_hashes,
        "raw_source_hashes": raw_source_hashes,
        "files_read": [str(path) for path in files],
    }


def _choose_fact_files(facts_dir: Path, min_open_ms: int, max_open_ms: int) -> list[Path]:
    market_dir = _resolve_um_facts_dir(facts_dir)
    if not market_dir.exists():
        return []
    days = _utc_days_between(min_open_ms, max_open_ms)
    result: set[Path] = set()
    for month in sorted({day[:7] for day in days}):
        monthly = market_dir / f"{month}.parquet"
        if monthly.exists():
            result.add(monthly)
            continue
        for day in days:
            if day[:7] != month:
                continue
            daily = market_dir / f"{day}.parquet"
            if daily.exists():
                result.add(daily)
    return sorted(result)


def _resolve_um_facts_dir(facts_dir: Path) -> Path:
    if (facts_dir / "um").exists():
        return facts_dir / "um"
    if (facts_dir / "facts" / "um").exists():
        return facts_dir / "facts" / "um"
    return facts_dir


def _utc_days_between(min_ms: int, max_ms: int) -> list[str]:
    start = datetime.fromtimestamp(min_ms / 1000.0, tz=timezone.utc).date()
    end = datetime.fromtimestamp(max_ms / 1000.0, tz=timezone.utc).date()
    days: list[str] = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
