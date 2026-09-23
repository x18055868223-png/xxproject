#!/usr/bin/env python3
"""Astra joint-research v1.1 natural-card data foundation.

The module is deliberately stdlib-only at the top level.  It reads the frozen
v1 Binance/Deribit caches, builds all-market point-in-time observations for
natural NR research, and writes a separate v1.1 dataset without mutating the old
research artifacts.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from astra_joint_data import (  # noqa: E402
    HOUR_MS,
    MINUTE_MS,
    HistoricalBars,
    RollingHistoricalBars,
    ms_to_iso,
    normalize_timestamp_ms,
)
from astra_joint_dataset import payout, select_legs  # noqa: E402
from astra_joint_sources import digest  # noqa: E402
from astra_joint_v11_contract import (  # noqa: E402
    CANDIDATE_FIELDS,
    CANDIDATE_SCHEMA,
    COMMON_FEATURE_GROUPS,
    CSV_COMPAT_IGNORED_COLUMNS,
    DATASET_MANIFEST_SCHEMA,
    MARKET_OBSERVATION_SCHEMA,
    MODEL_INPUT_SCHEMA,
    NATURAL_FEATURE_SCHEMA,
    PROTOCOL_SEAL_SCHEMA,
    PROTOCOL_V11,
    SOURCE_QUALITY_SCHEMA,
)


BJT = dt.timezone(dt.timedelta(hours=8))
DAY_MS = 86_400_000
EPSILON = 1e-12
DEFAULT_SOURCE_ROOT = Path(r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\astra-joint-v1-20260914")
DEFAULT_OUTPUT_ROOT = Path(r"C:\Users\Xu\Documents\中性回路整合工程\.artifacts\astra-joint-v11-20260915")


def stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, value: Any, *, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8")
    if immutable and path.exists():
        if path.read_bytes().rstrip(b"\r\n") != encoded:
            raise ValueError(f"immutable artifact already exists with different content: {path}")
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(encoded + b"\n")
    os.replace(tmp, path)


def parse_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def utc_ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(dt.datetime(year, month, day, hour, minute, tzinfo=dt.UTC).timestamp() * 1000)


def protocol_hash(protocol: Mapping[str, Any] | None = None) -> str:
    return stable_hash(protocol or PROTOCOL_V11)


def expiry_ms(entry_ms: int) -> int:
    at = dt.datetime.fromtimestamp(int(entry_ms) / 1000, tz=dt.UTC)
    expiry = at.replace(hour=int(PROTOCOL_V11["expiry_hour_utc"]), minute=0, second=0, microsecond=0)
    if int(expiry.timestamp() * 1000) <= int(entry_ms):
        expiry += dt.timedelta(days=1)
    return int(expiry.timestamp() * 1000)


def delivery_date(expiry: int) -> str:
    return dt.datetime.fromtimestamp(int(expiry) / 1000, tz=dt.UTC).date().isoformat()


def historical_usage_role(expiry: int) -> str:
    year = dt.datetime.fromtimestamp(int(expiry) / 1000, tz=dt.UTC).year
    if year <= 2021:
        return "rolling_source_history"
    if 2022 <= year <= 2025:
        return "rolling_development_validation"
    return "interface_and_domain_check"


def split_for_expiry(expiry: int) -> str:
    year = dt.datetime.fromtimestamp(int(expiry) / 1000, tz=dt.UTC).year
    if year in (2020, 2021):
        return "train_pool"
    if year in (2022, 2023, 2024, 2025):
        return "rolling_validation_pool"
    return "development"


def _side_name(side: str) -> str:
    text = str(side).strip().lower()
    if text in {"put", "put_credit", "put_credit_spread"}:
        return "put"
    if text in {"call", "call_credit", "call_credit_spread"}:
        return "call"
    raise ValueError(f"unsupported side: {side}")


def _side_sign(side: str) -> float:
    return -1.0 if _side_name(side) == "put" else 1.0


def _strike(leg: Mapping[str, Any] | float | int) -> float:
    if isinstance(leg, Mapping):
        value = parse_float(leg.get("strike"))
    else:
        value = parse_float(leg)
    if value is None:
        raise ValueError("leg strike is required")
    return value


def _feature_value(snapshot: Mapping[str, Any], name: str) -> float | None:
    return parse_float(snapshot.get(name))


def common_features(as_of_ms: int, um_bars: HistoricalBars | RollingHistoricalBars) -> dict[str, Any]:
    """Return a common natural-card feature snapshot from closed UM bars only.

    Signature for other workers:
      common_features(as_of_ms: int, um_bars: HistoricalBars | RollingHistoricalBars) -> dict
    """

    snapshot = um_bars.feature_snapshot(int(as_of_ms))
    last_price = um_bars.last_close_at_or_before(int(as_of_ms))
    result = {
        "schema": NATURAL_FEATURE_SCHEMA,
        "feature_as_of_ms": int(as_of_ms),
        "feature_as_of_utc": ms_to_iso(int(as_of_ms)),
        "last_closed_price": last_price,
        "last_closed_time_ms": _last_closed_time_at_or_before(um_bars, int(as_of_ms)),
        "window_status": dict(snapshot.get("window_status") or {}),
        "feature_names": list(COMMON_FEATURE_GROUPS["joint"]),
    }
    for name in (
        "ret_15",
        "ret_30",
        "ret_240",
        "ret_720",
        "ret_1440",
        "vol_15",
        "vol_30",
        "vol_240",
        "vol_720",
        "vol_1440",
        "net_flow_15",
        "net_flow_30",
        "net_flow_240",
        "hour_sin",
        "hour_cos",
        "vwap_deviation_15",
        "vwap_deviation_30",
        "vwap_migration_15",
        "range_position_15",
        "range_position_30",
        "range_expansion_15",
        "efficiency_15",
        "efficiency_30",
        "pressure_response_15",
        "pressure_response_30",
    ):
        result[name] = snapshot.get(name)
    return result


def side_features(
    snapshot: Mapping[str, Any],
    side: str,
    price: float,
    short: Mapping[str, Any] | float,
    long: Mapping[str, Any] | float,
    dte_hours: float,
) -> dict[str, Any]:
    """Project one common snapshot onto a Put/Call reference spread.

    Signature for other workers:
      side_features(snapshot, side, price, short, long, dte_hours) -> dict
    """

    side_text = _side_name(side)
    sign = _side_sign(side_text)
    price_f = float(price)
    if price_f <= 0:
        raise ValueError("price must be positive")
    short_strike = _strike(short)
    long_strike = _strike(long)
    width = abs(short_strike - long_strike)
    result = {
        "dte_hours": float(dte_hours),
        "short_distance_fraction": abs(short_strike - price_f) / price_f,
        "width_fraction": width / price_f,
        "side_sign": sign,
    }
    for name in COMMON_FEATURE_GROUPS["joint"]:
        if name in result:
            continue
        result[name] = snapshot.get(name)

    for minutes in (15, 30, 240, 720, 1440):
        ret = _feature_value(snapshot, f"ret_{minutes}")
        result[f"adverse_return_{minutes}"] = sign * ret if ret is not None else None
    for minutes in (15, 30, 240):
        flow = _feature_value(snapshot, f"net_flow_{minutes}")
        result[f"adverse_flow_{minutes}"] = sign * flow if flow is not None else None
    for minutes in (15, 30):
        position = _feature_value(snapshot, f"range_position_{minutes}")
        if position is None:
            result[f"adverse_range_room_{minutes}"] = None
            result[f"favorable_range_room_{minutes}"] = None
        elif side_text == "put":
            result[f"adverse_range_room_{minutes}"] = position
            result[f"favorable_range_room_{minutes}"] = 1.0 - position
        else:
            result[f"adverse_range_room_{minutes}"] = 1.0 - position
            result[f"favorable_range_room_{minutes}"] = position
        ret = _feature_value(snapshot, f"ret_{minutes}")
        flow = _feature_value(snapshot, f"net_flow_{minutes}")
        range_width = _range_width_proxy(snapshot, minutes)
        if ret is None or flow is None or range_width is None:
            result[f"adverse_flow_response_{minutes}"] = None
            result[f"favorable_flow_response_{minutes}"] = None
        else:
            ret_norm = max(-1.0, min(1.0, ret / max(range_width, EPSILON)))
            adverse_price = max(0.0, sign * ret_norm)
            adverse_flow = max(0.0, sign * flow)
            favorable_price = max(0.0, -sign * ret_norm)
            favorable_flow = max(0.0, -sign * flow)
            result[f"adverse_flow_response_{minutes}"] = adverse_price * adverse_flow
            result[f"favorable_flow_response_{minutes}"] = favorable_price * favorable_flow
    return {name: result.get(name) for name in COMMON_FEATURE_GROUPS["joint"]}


def reference_spot_close(
    as_of_ms: int,
    spot_bars: HistoricalBars | RollingHistoricalBars,
) -> dict[str, Any] | None:
    window = spot_bars.closed_window(int(as_of_ms), 1)
    if window is None:
        return None
    if int(window[-1]["close_time_ms"]) - int(window[-1]["open_time_ms"]) != MINUTE_MS - 1:
        return None
    price = spot_bars.last_close_at_or_before(int(as_of_ms))
    observed = _last_closed_time_at_or_before(spot_bars, int(as_of_ms))
    if price is None or observed is None:
        return None
    return {
        "price": float(price),
        "price_source": "Binance BTCUSDT spot last fully closed 1m close at or before observation",
        "price_observation_ms": int(observed),
        "price_observation_utc": ms_to_iso(int(observed)),
    }


def iter_clock_observations(
    source_root: str | Path,
    *,
    step_minutes: int = 30,
    start_ms: int | None = None,
    as_of_upper_ms: int | None = None,
    quality_dir: str | Path | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield all-market point-in-time observations for natural-card research."""

    if step_minutes <= 0:
        raise ValueError("step_minutes must be positive")
    source_root = Path(source_root)
    quality = SourceQualityWriter(Path(quality_dir)) if quality_dir is not None else None
    spot_rows = _iter_market_rows(source_root, "spot", quality)
    um_rows = _iter_market_rows(source_root, "um", quality)
    spot = _next_or_none(spot_rows)
    um = _next_or_none(um_rows)
    spot_bars = RollingHistoricalBars(max_minutes=1441)
    um_bars = RollingHistoricalBars(max_minutes=1441)
    step_ms = int(step_minutes) * MINUTE_MS
    pair_counts: Counter[str] = Counter()
    completed = False
    try:
        while spot is not None and um is not None:
            spot_open = int(spot["open_time_ms"])
            um_open = int(um["open_time_ms"])
            if spot_open == um_open:
                spot_bars.append(spot)
                um_bars.append(um)
                as_of_ms = max(int(spot["close_time_ms"]), int(um["close_time_ms"])) + 1
                pair_counts["paired_minutes"] += 1
                if start_ms is not None and as_of_ms < int(start_ms):
                    spot = _next_or_none(spot_rows)
                    um = _next_or_none(um_rows)
                    continue
                if as_of_upper_ms is not None and as_of_ms > int(as_of_upper_ms):
                    completed = True
                    break
                if as_of_ms % step_ms == 0:
                    snapshot = common_features(as_of_ms, um_bars)
                    price_ref = reference_spot_close(as_of_ms, spot_bars)
                    observation = {
                        "schema": MARKET_OBSERVATION_SCHEMA,
                        "event_family": "natural_all_market_clock",
                        "observation_kind": f"clock_{step_minutes}m",
                        "observation_id": f"natural-clock-{step_minutes}m:{as_of_ms}",
                        "as_of_ms": as_of_ms,
                        "as_of_utc": ms_to_iso(as_of_ms),
                        "entry_ms": as_of_ms,
                        "entry_utc": ms_to_iso(as_of_ms),
                        "entry_price": price_ref["price"] if price_ref else None,
                        "price_source": price_ref["price_source"] if price_ref else None,
                        "price_observation_ms": price_ref["price_observation_ms"] if price_ref else None,
                        "price_observation_utc": price_ref["price_observation_utc"] if price_ref else None,
                        "spot_source_file": spot.get("source"),
                        "um_source_file": um.get("source"),
                        "spot_open_time_ms": spot_open,
                        "um_open_time_ms": um_open,
                        "source_observation_hash": stable_hash(
                            {
                                "step_minutes": step_minutes,
                                "as_of_ms": as_of_ms,
                                "spot_open_time_ms": spot_open,
                                "spot_close": spot.get("close"),
                                "um_open_time_ms": um_open,
                                "um_close": um.get("close"),
                                "features": snapshot,
                            }
                        ),
                    }
                    observation.update(snapshot)
                    yield observation
                spot = _next_or_none(spot_rows)
                um = _next_or_none(um_rows)
            elif spot_open < um_open:
                pair_counts["spot_without_um"] += 1
                if quality is not None:
                    quality.write_pair_gap("spot_without_um", spot_open, um_open)
                spot = _next_or_none(spot_rows)
            else:
                pair_counts["um_without_spot"] += 1
                if quality is not None:
                    quality.write_pair_gap("um_without_spot", spot_open, um_open)
                um = _next_or_none(um_rows)
        else:
            completed = True
    finally:
        if quality is not None:
            quality.close(extra_counts={"pairing": dict(pair_counts), "step_minutes": int(step_minutes)}, complete=completed)


def build_revision_dataset(
    source_root: str | Path,
    output_root: str | Path,
    *,
    step_minutes: int = 30,
    widths: Sequence[float] | None = None,
    as_of_upper_ms: int | None = None,
    include_outcomes: bool = True,
) -> dict[str, Any]:
    """Build v1.1 per-year model-input CSVs from read-only v1 caches."""

    source_root = Path(source_root)
    output_root = Path(output_root)
    widths = tuple(float(item) for item in (widths or PROTOCOL_V11["widths"]))
    run_dir = output_root / f"step{int(step_minutes)}"
    quality_dir = run_dir / "source_quality"
    decisions_dir = run_dir / "decisions"
    model_dir = run_dir / "model_input"
    observations_path = decisions_dir / "market_observations.jsonl"
    gaps_path = decisions_dir / "candidate_gaps.jsonl"
    protocol_path = output_root / "protocol_v11.json"
    seal_path = output_root / "protocol_seal.json"
    output_root.mkdir(parents=True, exist_ok=True)
    if protocol_path.exists() and json.loads(protocol_path.read_text(encoding="utf-8")) != PROTOCOL_V11:
        if list(output_root.glob("step*/revision_dataset_manifest.json")):
            raise ValueError(f"sealed protocol differs from current v1.1 contract: {protocol_path}")
        write_json(protocol_path, PROTOCOL_V11)
    else:
        write_json(protocol_path, PROTOCOL_V11, immutable=True)
    seal_payload = {
        "schema": PROTOCOL_SEAL_SCHEMA,
        "protocol_sha256": digest(protocol_path),
        "sealed_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "source_root": str(source_root),
        "outcomes_computed_before_protocol_seal": False,
        "production_changed": False,
    }
    if seal_path.exists():
        existing_seal = json.loads(seal_path.read_text(encoding="utf-8"))
        mismatch = [
            key
            for key in ("schema", "protocol_sha256", "source_root", "outcomes_computed_before_protocol_seal")
            if existing_seal.get(key) != seal_payload.get(key)
        ]
        if mismatch:
            if list(output_root.glob("step*/revision_dataset_manifest.json")):
                raise ValueError(f"protocol seal mismatch for {mismatch}: {seal_path}")
            write_json(seal_path, seal_payload)
    else:
        write_json(seal_path, seal_payload, immutable=True)

    contracts, option_source_sha = load_contracts(source_root)
    delivery_prices, delivery_source_sha = load_delivery_prices(source_root)
    decisions_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)
    writers: dict[tuple[str, int], tuple[Any, csv.DictWriter, Path, Path]] = {}
    counts: Counter[str] = Counter()
    years: set[int] = set()
    file_hashes: dict[str, str] = {}
    obs_partial = observations_path.with_suffix(observations_path.suffix + ".partial")
    gaps_partial = gaps_path.with_suffix(gaps_path.suffix + ".partial")
    success = False
    with obs_partial.open("w", encoding="utf-8", newline="\n") as obs_stream, gaps_partial.open(
        "w", encoding="utf-8", newline="\n"
    ) as gap_stream:
        try:
            for observation in iter_clock_observations(
                source_root,
                step_minutes=step_minutes,
                as_of_upper_ms=as_of_upper_ms,
                quality_dir=quality_dir,
            ):
                counts["observations"] += 1
                obs_stream.write(json.dumps(observation, ensure_ascii=False, sort_keys=True, allow_nan=False))
                obs_stream.write("\n")
                entry_ms = int(observation["entry_ms"])
                expiry = expiry_ms(entry_ms)
                dte_hours = (expiry - entry_ms) / float(HOUR_MS)
                if not (
                    float(PROTOCOL_V11["ordinary_dte_min_exclusive_hours"])
                    < dte_hours
                    <= float(PROTOCOL_V11["ordinary_dte_max_inclusive_hours"])
                ):
                    counts["dte_excluded"] += 1
                    continue
                price = parse_float(observation.get("entry_price"))
                if price is None or price <= 0:
                    _write_gap(gap_stream, observation, "entry_price_unavailable")
                    counts["entry_price_unavailable"] += 1
                    continue
                expiry_contracts = contracts.get(expiry) or []
                if not expiry_contracts:
                    _write_gap(gap_stream, observation, "actual_expiry_contracts_unavailable", expiry_ms=expiry)
                    counts["actual_expiry_contracts_unavailable"] += 1
                    continue
                delivery = delivery_date(expiry)
                settlement = delivery_prices.get(delivery)
                year = dt.datetime.fromtimestamp(expiry / 1000, tz=dt.UTC).year
                years.add(year)
                for side in ("put", "call"):
                    for target_width in widths:
                        selected = select_legs(expiry_contracts, side, price, target_width, entry_ms)
                        if selected is None:
                            _write_gap(
                                gap_stream,
                                observation,
                                "actual_legs_unavailable",
                                side=side,
                                target_width=target_width,
                                expiry_ms=expiry,
                            )
                            counts["actual_legs_unavailable"] += 1
                            continue
                        short, long = selected
                        row = _candidate_row(
                            observation,
                            side,
                            target_width,
                            short,
                            long,
                            price,
                            expiry,
                            dte_hours,
                            option_source_sha,
                            delivery_source_sha,
                            settlement if include_outcomes else None,
                        )
                        key = ("candidates", year)
                        writer = _writer_for(writers, decisions_dir, key, CANDIDATE_FIELDS)
                        writer.writerow(_csv_ready(row))
                        key_joined = ("model_rows", year)
                        joined_writer = _writer_for(writers, model_dir, key_joined, CANDIDATE_FIELDS)
                        joined_writer.writerow(_csv_ready(row))
                        counts[f"rows_{year}"] += 1
            success = True
        finally:
            for stream, _writer, _partial, _final in writers.values():
                stream.close()
    if success:
        os.replace(obs_partial, observations_path)
        os.replace(gaps_partial, gaps_path)
        for _stream, _writer, partial_path, final_path in writers.values():
            os.replace(partial_path, final_path)
    for path in sorted(decisions_dir.glob("*.csv")) + sorted(model_dir.glob("*.csv")):
        file_hashes[str(path.relative_to(run_dir).as_posix())] = digest(path)
    file_hashes[str(observations_path.relative_to(run_dir).as_posix())] = digest(observations_path)
    file_hashes[str(gaps_path.relative_to(run_dir).as_posix())] = digest(gaps_path)
    quality_manifest = quality_dir / "source_quality_manifest.json"
    if quality_manifest.exists():
        file_hashes[str(quality_manifest.relative_to(run_dir).as_posix())] = digest(quality_manifest)
    manifest = {
        "schema": DATASET_MANIFEST_SCHEMA,
        "model_input_schema": MODEL_INPUT_SCHEMA,
        "candidate_schema": CANDIDATE_SCHEMA,
        "model_features": {key: list(value) for key, value in COMMON_FEATURE_GROUPS.items()},
        "csv_compat_ignored_columns": list(CSV_COMPAT_IGNORED_COLUMNS),
        "protocol_sha256": digest(protocol_path),
        "source_root": str(source_root),
        "output_root": str(output_root),
        "step_minutes": int(step_minutes),
        "widths": list(widths),
        "include_outcomes": bool(include_outcomes),
        "created_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "counts": dict(sorted(counts.items())),
        "years": sorted(years),
        "files": file_hashes,
        "interfaces": {
            "common_features": "common_features(as_of_ms, um_bars)",
            "side_features": "side_features(snapshot, side, price, short, long, dte_hours)",
            "statistical_sorting": PROTOCOL_V11["model_targets"]["statistical_sorting"],
            "forward_quote": PROTOCOL_V11["forward_quote"],
        },
        "boundaries": {
            "old_research_mutated": False,
            "production_changed": False,
            "fmz_changed": False,
            "llm_called": False,
            "training_started": False,
        },
    }
    write_json(run_dir / "revision_dataset_manifest.json", manifest)
    return manifest


def load_contracts(source_root: str | Path) -> tuple[dict[int, list[dict[str, Any]]], str]:
    path = Path(source_root) / "raw" / "deribit" / "instruments.json"
    if not path.exists():
        raise FileNotFoundError(path)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for raw in json.loads(path.read_text(encoding="utf-8")):
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "option").lower()
        if kind != "option":
            continue
        option_type = str(raw.get("option_type") or "").lower()
        if option_type not in {"put", "call"}:
            continue
        expiry = raw.get("expiration_timestamp")
        strike = parse_float(raw.get("strike"))
        creation = raw.get("creation_timestamp")
        if expiry is None or strike is None or creation is None:
            continue
        item = dict(raw)
        item["option_type"] = option_type
        item["expiration_timestamp"] = int(expiry)
        item["creation_timestamp"] = int(creation)
        item["strike"] = float(strike)
        grouped[int(expiry)].append(item)
    return grouped, digest(path)


def load_delivery_prices(source_root: str | Path) -> tuple[dict[str, float], str | None]:
    path = Path(source_root) / "raw" / "deribit" / "delivery_prices.json"
    if not path.exists():
        return {}, None
    rows = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("date"):
            price = parse_float(row.get("delivery_price"))
            if price is not None and price > 0:
                result[str(row["date"])] = price
    return result, digest(path)


class SourceQualityWriter:
    def __init__(self, folder: Path | None):
        self.folder = folder
        self.counts: Counter[str] = Counter()
        self.source_summaries: list[dict[str, Any]] = []
        self.rejections = None
        self.gaps = None
        self.pair_gaps = None
        self._targets: list[tuple[Path, Path]] = []
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            self.rejections = self._open_partial(folder / "rejections.jsonl")
            self.gaps = self._open_partial(folder / "gaps.jsonl")
            self.pair_gaps = self._open_partial(folder / "pair_gaps.jsonl")

    def _open_partial(self, final_path: Path) -> Any:
        partial_path = final_path.with_suffix(final_path.suffix + ".partial")
        self._targets.append((partial_path, final_path))
        return partial_path.open("w", encoding="utf-8", newline="\n")

    def source_summary(self, item: dict[str, Any]) -> None:
        self.source_summaries.append(item)
        for key in ("raw_rows", "accepted_rows", "rejected_rows", "zero_volume_rows", "missing_taker_buy_rows"):
            self.counts[key] += int(item.get(key) or 0)

    def reject(self, feed: str, source: Path, line_no: int, reason: str, raw: Sequence[Any]) -> None:
        self.counts[f"reject:{reason}"] += 1
        if self.rejections is not None:
            self.rejections.write(
                json.dumps(
                    {
                        "schema": SOURCE_QUALITY_SCHEMA,
                        "feed": feed,
                        "source_file": str(source),
                        "line": line_no,
                        "reason": reason,
                        "raw_preview": list(raw[:4]),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )

    def gap(self, feed: str, source: Path, previous_open_ms: int, current_open_ms: int) -> None:
        self.counts["market_gaps"] += 1
        if self.gaps is not None:
            self.gaps.write(
                json.dumps(
                    {
                        "schema": SOURCE_QUALITY_SCHEMA,
                        "feed": feed,
                        "source_file": str(source),
                        "previous_open_ms": int(previous_open_ms),
                        "current_open_ms": int(current_open_ms),
                        "missing_minutes": max(0, (int(current_open_ms) - int(previous_open_ms)) // MINUTE_MS - 1),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )

    def write_pair_gap(self, reason: str, spot_open_ms: int, um_open_ms: int) -> None:
        self.counts[f"pair:{reason}"] += 1
        if self.pair_gaps is not None:
            self.pair_gaps.write(
                json.dumps(
                    {
                        "schema": SOURCE_QUALITY_SCHEMA,
                        "reason": reason,
                        "spot_open_ms": int(spot_open_ms),
                        "um_open_ms": int(um_open_ms),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            )

    def close(self, extra_counts: dict[str, Any] | None = None, *, complete: bool = True) -> None:
        for stream in (self.rejections, self.gaps, self.pair_gaps):
            if stream is not None:
                stream.close()
        if self.folder is None:
            return
        if not complete:
            return
        for partial_path, final_path in self._targets:
            os.replace(partial_path, final_path)
        payload = {
            "schema": SOURCE_QUALITY_SCHEMA,
            "created_at_utc": dt.datetime.now(dt.UTC).isoformat(),
            "counts": dict(sorted(self.counts.items())),
            "extra_counts": extra_counts or {},
            "sources": self.source_summaries,
        }
        write_json(self.folder / "source_quality_manifest.json", payload)


def _iter_market_rows(source_root: Path, feed: str, quality: SourceQualityWriter | None) -> Iterator[dict[str, Any]]:
    folder = source_root / "raw" / "binance" / feed
    if not folder.exists():
        raise FileNotFoundError(folder)
    previous_open: int | None = None
    for path in _market_archive_paths(folder):
        file_counts = Counter()
        first_open = None
        last_open = None
        source_sha = _source_sha(path)
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"expected one csv member in {path}")
            with archive.open(members[0]) as raw_stream:
                text = io.TextIOWrapper(raw_stream, encoding="utf-8-sig", newline="")
                reader = csv.reader(text)
                for line_no, raw in enumerate(reader, start=1):
                    if not raw:
                        continue
                    if str(raw[0]).strip().lower() in {"open_time", "open time", "open_time_ms"}:
                        continue
                    file_counts["raw_rows"] += 1
                    row, reason = _normalize_binance_raw(raw, str(path))
                    if row is None:
                        file_counts["rejected_rows"] += 1
                        if quality is not None:
                            quality.reject(feed, path, line_no, reason or "invalid_row", raw)
                        continue
                    file_counts["accepted_rows"] += 1
                    if float(row.get("volume") or 0.0) == 0.0:
                        file_counts["zero_volume_rows"] += 1
                    if row.get("taker_buy_base_volume") is None:
                        file_counts["missing_taker_buy_rows"] += 1
                    open_ms = int(row["open_time_ms"])
                    if previous_open is not None and open_ms != previous_open + MINUTE_MS:
                        if quality is not None:
                            quality.gap(feed, path, previous_open, open_ms)
                    previous_open = open_ms
                    first_open = open_ms if first_open is None else first_open
                    last_open = open_ms
                    yield row
        if quality is not None:
            quality.source_summary(
                {
                    "schema": SOURCE_QUALITY_SCHEMA,
                    "feed": feed,
                    "source_file": str(path),
                    "sha256": source_sha,
                    "raw_rows": int(file_counts["raw_rows"]),
                    "accepted_rows": int(file_counts["accepted_rows"]),
                    "rejected_rows": int(file_counts["rejected_rows"]),
                    "zero_volume_rows": int(file_counts["zero_volume_rows"]),
                    "missing_taker_buy_rows": int(file_counts["missing_taker_buy_rows"]),
                    "first_open_ms": first_open,
                    "last_open_ms": last_open,
                    "first_open_utc": ms_to_iso(first_open) if first_open is not None else None,
                    "last_open_utc": ms_to_iso(last_open) if last_open is not None else None,
                    "time_identity": "Binance closed 1m kline; archive download time is not market observation time",
                }
            )


def _market_archive_paths(folder: Path) -> list[Path]:
    monthly = sorted((folder / "monthly").glob("BTCUSDT-1m-????-??.zip")) if (folder / "monthly").exists() else []
    daily = sorted((folder / "daily").glob("BTCUSDT-1m-????-??-??.zip")) if (folder / "daily").exists() else []
    return [*monthly, *daily]


def _normalize_binance_raw(raw: Sequence[Any], source: str) -> tuple[dict[str, Any] | None, str | None]:
    if len(raw) < 7:
        return None, "missing_columns"
    try:
        open_ms = normalize_timestamp_ms(raw[0])
        close_ms = normalize_timestamp_ms(raw[6])
    except Exception:
        return None, "invalid_timestamp"
    open_price = parse_float(raw[1])
    high = parse_float(raw[2])
    low = parse_float(raw[3])
    close = parse_float(raw[4])
    volume = parse_float(raw[5])
    if open_price is None or high is None or low is None or close is None or volume is None:
        return None, "invalid_price_or_volume"
    if open_price <= 0 or high < low or close_ms < open_ms or volume < 0:
        return None, "invalid_ohlcv"
    quote_volume = parse_float(raw[7]) if len(raw) > 7 else None
    trade_count = parse_float(raw[8]) if len(raw) > 8 else None
    taker_buy_base = parse_float(raw[9]) if len(raw) > 9 else None
    taker_buy_quote = parse_float(raw[10]) if len(raw) > 10 else None
    if taker_buy_base is not None and (taker_buy_base < 0 or taker_buy_base > volume + 1e-9):
        return None, "invalid_taker_buy_volume"
    return (
        {
            "open_time_ms": int(open_ms),
            "open_time": int(open_ms),
            "open": float(open_price),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": float(volume),
            "close_time_ms": int(close_ms),
            "close_time": int(close_ms),
            "quote_volume": quote_volume,
            "trade_count": trade_count,
            "taker_buy_base_volume": taker_buy_base,
            "taker_buy_quote_volume": taker_buy_quote,
            "source": source,
            "date_utc": dt.datetime.fromtimestamp(int(open_ms) / 1000, tz=dt.UTC).date().isoformat(),
        },
        None,
    )


def _source_sha(path: Path) -> str:
    receipt = path.with_name(path.name + ".receipt.json")
    if receipt.exists():
        try:
            value = json.loads(receipt.read_text(encoding="utf-8")).get("sha256")
            if value:
                return str(value)
        except Exception:
            pass
    return digest(path)


def _candidate_row(
    observation: Mapping[str, Any],
    side: str,
    target_width: float,
    short: Mapping[str, Any],
    long: Mapping[str, Any],
    price: float,
    expiry: int,
    dte_hours: float,
    option_source_sha: str,
    delivery_source_sha: str | None,
    settlement: float | None,
) -> dict[str, Any]:
    side_text = _side_name(side)
    actual_width = abs(float(short["strike"]) - float(long["strike"]))
    feature_row = side_features(observation, side_text, price, short, long, dte_hours)
    delivery = delivery_date(expiry)
    identity = {
        "observation_id": observation["observation_id"],
        "side": side_text,
        "target_width": float(target_width),
        "actual_width": actual_width,
        "short_strike": float(short["strike"]),
        "long_strike": float(long["strike"]),
        "entry_ms": int(observation["entry_ms"]),
        "expiry_ms": int(expiry),
        "source_observation_hash": observation["source_observation_hash"],
    }
    preoutcome_hash = stable_hash({**identity, "features": feature_row})
    settlement_price = parse_float(settlement)
    payout_btc = None
    loss_normalized = None
    short_breached = None
    protection_breached = None
    outcome_status = "missing_official_delivery"
    if settlement_price is not None and settlement_price > 0:
        payout_btc = payout(side_text, float(short["strike"]), float(long["strike"]), settlement_price)
        loss_normalized = payout_btc / (actual_width / float(price)) if actual_width > 0 else None
        if side_text == "put":
            short_breached = settlement_price < float(short["strike"])
            protection_breached = settlement_price < float(long["strike"])
        else:
            short_breached = settlement_price > float(short["strike"])
            protection_breached = settlement_price > float(long["strike"])
        outcome_status = "settled"
    year = dt.datetime.fromtimestamp(int(expiry) / 1000, tz=dt.UTC).year
    row = {
        "schema": MODEL_INPUT_SCHEMA,
        "row_id": stable_hash(identity),
        "observation_id": observation["observation_id"],
        "event_family": observation["event_family"],
        "observation_kind": observation["observation_kind"],
        "as_of_ms": int(observation["as_of_ms"]),
        "as_of_utc": observation.get("as_of_utc"),
        "entry_ms": int(observation["entry_ms"]),
        "entry_utc": observation.get("entry_utc"),
        "expiry_ms": int(expiry),
        "expiry_utc": ms_to_iso(int(expiry)),
        "delivery_date": delivery,
        "delivery_year": year,
        "split": split_for_expiry(expiry),
        "historical_usage_role": historical_usage_role(expiry),
        "side": side_text,
        "target_width": float(target_width),
        "actual_width": actual_width,
        "short_strike": float(short["strike"]),
        "long_strike": float(long["strike"]),
        "short_name": short.get("instrument_name"),
        "long_name": long.get("instrument_name"),
        "short_creation_ms": int(short["creation_timestamp"]),
        "long_creation_ms": int(long["creation_timestamp"]),
        "entry_price": float(price),
        "price_source": observation.get("price_source"),
        "price_observation_ms": observation.get("price_observation_ms"),
        "price_observation_utc": observation.get("price_observation_utc"),
        "spot_source_file": observation.get("spot_source_file"),
        "um_source_file": observation.get("um_source_file"),
        "option_source_sha256": option_source_sha,
        "delivery_source_sha256": delivery_source_sha,
        "source_observation_hash": observation["source_observation_hash"],
        "preoutcome_row_hash": preoutcome_hash,
        **feature_row,
        "settlement_price": settlement_price,
        "payout_btc": payout_btc,
        "loss_normalized": loss_normalized,
        "short_leg_breached": short_breached,
        "protection_leg_breached": protection_breached,
        "outcome_status": outcome_status,
    }
    return {field: row.get(field) for field in CANDIDATE_FIELDS}


def _range_width_proxy(snapshot: Mapping[str, Any], minutes: int) -> float | None:
    ret = abs(_feature_value(snapshot, f"ret_{minutes}") or 0.0)
    efficiency = _feature_value(snapshot, f"efficiency_{minutes}")
    if efficiency is None or efficiency <= 0:
        return None
    return max(ret / max(efficiency, EPSILON), EPSILON)


def _last_closed_time_at_or_before(
    bars: HistoricalBars | RollingHistoricalBars,
    as_of_ms: int,
) -> int | None:
    close_times = list(getattr(bars, "close_times", []))
    if not close_times:
        return None
    index = _bisect_right(close_times, int(as_of_ms)) - 1
    if index < 0:
        return None
    return int(close_times[index])


def _bisect_right(values: Sequence[int], target: int) -> int:
    lo = 0
    hi = len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if int(values[mid]) <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _next_or_none(iterator: Iterator[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        return next(iterator)
    except StopIteration:
        return None


def _writer_for(
    writers: dict[tuple[str, int], tuple[Any, csv.DictWriter, Path, Path]],
    folder: Path,
    key: tuple[str, int],
    fields: Sequence[str],
) -> csv.DictWriter:
    if key in writers:
        return writers[key][1]
    stem, year = key
    path = folder / f"{stem}-{year}.csv"
    partial_path = path.with_suffix(path.suffix + ".partial")
    stream = partial_path.open("w", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore")
    writer.writeheader()
    writers[key] = (stream, writer, partial_path, path)
    return writer


def _csv_ready(row: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in row.items():
        if isinstance(value, (dict, list)):
            result[key] = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        elif isinstance(value, bool):
            result[key] = "true" if value else "false"
        elif value is None:
            result[key] = ""
        else:
            result[key] = value
    return result


def _write_gap(stream: Any, observation: Mapping[str, Any], reason: str, **extra: Any) -> None:
    stream.write(
        json.dumps(
            {
                "schema": CANDIDATE_SCHEMA,
                "observation_id": observation.get("observation_id"),
                "as_of_ms": observation.get("as_of_ms"),
                "reason": reason,
                **extra,
            },
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--step-minutes", type=int, nargs="+", default=[30])
    parser.add_argument("--as-of-upper-ms", type=int)
    parser.add_argument("--no-outcomes", action="store_true")
    args = parser.parse_args(argv)
    results = []
    for step in args.step_minutes:
        results.append(
            build_revision_dataset(
                args.source_root,
                args.output_root,
                step_minutes=step,
                as_of_upper_ms=args.as_of_upper_ms,
                include_outcomes=not args.no_outcomes,
            )
        )
    print(json.dumps(results, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
