"""Build the v1.3 same-side outward-structure payout label ledger.

Research-only: this module reads frozen v1.1 model rows and archived Deribit
contract metadata. It never trains, calls an API, mutates production files, or
infers historical executable credit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ModuleNotFoundError as exc:  # pragma: no cover - exercised by CLI environment
    raise SystemExit("pandas is required; run with the frozen research virtualenv") from exc


SCHEMA = "astra_entry_structure_pairs@1.0.0"
PRIMARY_WIDTH = 2000.0
EPS = 1e-9
FATAL_EPS = 1e-7
SOURCE_MANIFEST_NAME = "source_manifest.json"
INTEGRITY_GAP_REASONS = {
    "invalid_entry_expiry_or_width",
    "original_short_row_fields_invalid",
    "original_long_row_fields_invalid",
    "original_short_instrument_row_mismatch",
    "original_long_instrument_row_mismatch",
    "original_short_instrument_identity_mismatch",
    "original_long_instrument_identity_mismatch",
    "original_short_created_after_entry",
    "original_long_created_after_entry",
    "original_width_mismatch",
    "original_leg_attributes_mismatch",
}
ATTR_KEYS = (
    "kind",
    "option_type",
    "expiration_timestamp",
    "contract_size",
    "base_currency",
    "quote_currency",
    "counter_currency",
    "settlement_currency",
    "price_index",
)
MODEL_REQUIRED = (
    "row_id",
    "observation_id",
    "as_of_ms",
    "as_of_utc",
    "entry_ms",
    "entry_utc",
    "expiry_ms",
    "expiry_utc",
    "delivery_date",
    "delivery_year",
    "side",
    "target_width",
    "actual_width",
    "short_strike",
    "long_strike",
    "short_name",
    "long_name",
    "short_creation_ms",
    "long_creation_ms",
    "entry_price",
    "dte_hours",
    "short_distance_fraction",
    "width_fraction",
    "vol_240",
    "settlement_price",
    "payout_btc",
    "loss_normalized",
    "short_leg_breached",
    "protection_leg_breached",
    "outcome_status",
)
SOURCE_COLUMNS = (
    "option_source_sha256",
    "delivery_source_sha256",
    "source_observation_hash",
    "preoutcome_row_hash",
    "spot_source_file",
    "um_source_file",
    "price_source",
    "price_observation_ms",
    "price_observation_utc",
)
FEATURE_COLUMNS = (
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
    "adverse_return_15",
    "adverse_return_30",
    "adverse_return_240",
    "adverse_flow_15",
    "adverse_flow_30",
    "adverse_flow_240",
)


def _unique_columns(columns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(columns))


PAIR_COLUMNS = _unique_columns((
    "row_id",
    "observation_id",
    "side",
    "side_credit",
    "as_of_ms",
    "as_of_utc",
    "entry_ms",
    "entry_utc",
    "expiry_ms",
    "expiry_utc",
    "delivery_date",
    "delivery_year",
    "actual_width",
    "entry_price",
    "dte_hours",
    "short_strike",
    "long_strike",
    "outward_short_strike",
    "outward_long_strike",
    "shift",
    "distance_to_width",
    "shift_to_width",
    "short_distance_fraction",
    "outward_short_distance_fraction",
    "width_fraction",
    "vol_240",
    "short_name",
    "long_name",
    "outward_short_name",
    "outward_long_name",
    "short_creation_ms",
    "long_creation_ms",
    "outward_short_creation_ms",
    "outward_long_creation_ms",
    "settlement_price",
    "official_delivery_price",
    "payout_btc",
    "outward_payout_btc",
    "loss_normalized",
    "outward_loss_normalized",
    "normalization_denominator_btc",
    "delta_btc",
    "delta_normalized",
    "short_leg_breached",
    "protection_leg_breached",
    "outward_short_leg_breached",
    "outward_protection_leg_breached",
    "both_safe",
    "both_protection_leg_breached",
    "payout_category",
    "outward_payout_category",
    *SOURCE_COLUMNS,
    *FEATURE_COLUMNS,
))
GAP_COLUMNS = (
    "row_id",
    "observation_id",
    "side",
    "as_of_ms",
    "entry_ms",
    "expiry_ms",
    "delivery_date",
    "delivery_year",
    "actual_width",
    "entry_price",
    "dte_hours",
    "short_strike",
    "long_strike",
    "short_name",
    "long_name",
    "vol_240",
    "reason",
    "detail",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(_jsonable(value), stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    os.replace(tmp, path)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if hasattr(value, "item"):
        return _jsonable(value.item())
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _num(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _int_ms(value: Any) -> int | None:
    number = _num(value)
    if number is None:
        return None
    return int(number)


def _same(left: Any, right: Any, eps: float = EPS) -> bool:
    a = _num(left)
    b = _num(right)
    if a is None or b is None:
        return False
    return abs(a - b) <= eps


def _utc_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _side(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"put", "put_credit"}:
        return "put"
    if text in {"call", "call_credit"}:
        return "call"
    raise ValueError(f"unknown side {value!r}")


def _side_credit(side: str) -> str:
    return f"{side}_credit"


def _credit_width(side: str, short_strike: float, long_strike: float) -> float:
    if side == "put":
        return short_strike - long_strike
    if side == "call":
        return long_strike - short_strike
    raise ValueError(f"unknown side {side!r}")


def _spread_intrinsic_usd(side: str, short_strike: float, long_strike: float, settlement: float) -> float:
    if side == "put":
        return max(short_strike - settlement, 0.0) - max(long_strike - settlement, 0.0)
    if side == "call":
        return max(settlement - short_strike, 0.0) - max(settlement - long_strike, 0.0)
    raise ValueError(f"unknown side {side!r}")


def _payout_btc(side: str, short_strike: float, long_strike: float, settlement: float) -> float:
    if settlement <= 0:
        raise ValueError("settlement must be positive")
    payout = _spread_intrinsic_usd(side, short_strike, long_strike, settlement) / settlement
    if payout < -FATAL_EPS:
        raise ValueError("negative spread payout")
    return payout


def _tail_flags(side: str, short_strike: float, long_strike: float, settlement: float) -> tuple[bool, bool, str]:
    intrinsic = _spread_intrinsic_usd(side, short_strike, long_strike, settlement)
    width = _credit_width(side, short_strike, long_strike)
    if side == "put":
        short_breached = settlement < short_strike
        protection_breached = settlement < long_strike
    else:
        short_breached = settlement > short_strike
        protection_breached = settlement > long_strike
    if intrinsic <= EPS:
        category = "zero"
    elif intrinsic >= width - EPS:
        category = "full_width"
    else:
        category = "partial"
    return short_breached, protection_breached, category


def _normalize_contract(raw: dict[str, Any]) -> dict[str, Any] | None:
    expiry = _int_ms(raw.get("expiration_timestamp"))
    created = _int_ms(raw.get("creation_timestamp"))
    strike = _num(raw.get("strike"))
    name = raw.get("instrument_name")
    option_type = str(raw.get("option_type") or "").lower()
    if expiry is None or created is None or strike is None or not isinstance(name, str) or not name.strip():
        return None
    if option_type not in {"put", "call"}:
        return None
    row = dict(raw)
    row["instrument_name"] = name
    row["expiration_timestamp"] = expiry
    row["creation_timestamp"] = created
    row["strike"] = float(strike)
    row["option_type"] = option_type
    row["kind"] = str(row.get("kind") or "option").lower()
    row["contract_size"] = _num(row.get("contract_size")) if row.get("contract_size") is not None else 1.0
    for key in ("base_currency", "quote_currency", "counter_currency", "settlement_currency", "price_index"):
        if row.get(key) is not None:
            row[key] = str(row[key]).upper() if key.endswith("currency") else str(row[key]).lower()
    return row


def load_instruments(path: Path) -> tuple[dict[str, dict[str, Any]], dict[tuple[int, str], list[dict[str, Any]]]]:
    by_name: dict[str, dict[str, Any]] = {}
    by_expiry_side: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in load_json(path):
        row = _normalize_contract(dict(raw or {}))
        if row is None:
            continue
        by_name[row["instrument_name"]] = row
        by_expiry_side[(row["expiration_timestamp"], row["option_type"])].append(row)
    for rows in by_expiry_side.values():
        rows.sort(key=lambda item: (item["strike"], item["creation_timestamp"], item["instrument_name"]))
    return by_name, dict(by_expiry_side)


def load_deliveries(path: Path) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw in load_json(path):
        date = raw.get("date")
        price = _num(raw.get("delivery_price"))
        if isinstance(date, str) and price is not None and price > 0:
            result[date] = float(price)
    return result


def _attrs_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    for key in ATTR_KEYS:
        a = left.get(key)
        b = right.get(key)
        if key == "contract_size":
            if not _same(a if a is not None else 1.0, b if b is not None else 1.0):
                return False
        elif a != b:
            return False
    return True


def _leg_from_row(row: dict[str, Any], contracts: dict[str, dict[str, Any]], prefix: str, side: str) -> dict[str, Any] | str:
    name = row.get(f"{prefix}_name")
    contract = contracts.get(str(name))
    if contract is None:
        return f"original_{prefix}_instrument_missing"
    strike = _num(row.get(f"{prefix}_strike"))
    created = _int_ms(row.get(f"{prefix}_creation_ms"))
    expiry = _int_ms(row.get("expiry_ms"))
    if strike is None or created is None or expiry is None:
        return f"original_{prefix}_row_fields_invalid"
    if not _same(contract["strike"], strike) or contract["creation_timestamp"] != created:
        return f"original_{prefix}_instrument_row_mismatch"
    if contract["expiration_timestamp"] != expiry or contract["option_type"] != side:
        return f"original_{prefix}_instrument_identity_mismatch"
    if contract["creation_timestamp"] > _int_ms(row.get("entry_ms")):
        return f"original_{prefix}_created_after_entry"
    return contract


def _find_exact_strike(rows: list[dict[str, Any]], strike: float, template: dict[str, Any], entry_ms: int) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row["creation_timestamp"] <= entry_ms and _same(row["strike"], strike) and _attrs_match(row, template)
    ]


def find_outward_pair(
    row: dict[str, Any],
    contracts: dict[str, dict[str, Any]],
    by_expiry_side: dict[tuple[int, str], list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    side = _side(row.get("side"))
    entry_ms = _int_ms(row.get("entry_ms"))
    expiry_ms = _int_ms(row.get("expiry_ms"))
    actual_width = _num(row.get("actual_width"))
    if entry_ms is None or expiry_ms is None or actual_width is None or actual_width <= 0:
        return None, None, "invalid_entry_expiry_or_width"
    short_leg = _leg_from_row(row, contracts, "short", side)
    if isinstance(short_leg, str):
        return None, None, short_leg
    long_leg = _leg_from_row(row, contracts, "long", side)
    if isinstance(long_leg, str):
        return None, None, long_leg
    if not _attrs_match(short_leg, long_leg):
        return None, None, "original_leg_attributes_mismatch"
    original_width = _credit_width(side, short_leg["strike"], long_leg["strike"])
    if not _same(original_width, actual_width):
        return None, None, "original_width_mismatch"

    rows = by_expiry_side.get((expiry_ms, side), [])
    short_candidates = [
        candidate
        for candidate in rows
        if candidate["creation_timestamp"] <= entry_ms and _attrs_match(candidate, short_leg)
    ]
    if side == "put":
        short_candidates = [candidate for candidate in short_candidates if candidate["strike"] < short_leg["strike"] - EPS]
        short_candidates.sort(key=lambda item: (short_leg["strike"] - item["strike"], item["creation_timestamp"], item["instrument_name"]))
    else:
        short_candidates = [candidate for candidate in short_candidates if candidate["strike"] > short_leg["strike"] + EPS]
        short_candidates.sort(key=lambda item: (item["strike"] - short_leg["strike"], item["creation_timestamp"], item["instrument_name"]))
    if not short_candidates:
        return None, None, "no_strictly_outward_short"

    for candidate_short in short_candidates:
        wanted_long = candidate_short["strike"] - actual_width if side == "put" else candidate_short["strike"] + actual_width
        long_candidates = _find_exact_strike(rows, wanted_long, long_leg, entry_ms)
        if long_candidates:
            long_candidates.sort(key=lambda item: (item["creation_timestamp"], item["instrument_name"]))
            return candidate_short, long_candidates[0], None
    return None, None, "no_outward_short_with_exact_same_width_protection"


def _fatal_if_source_mismatch(row: dict[str, Any], official_delivery: float) -> None:
    row_settlement = _num(row.get("settlement_price"))
    if row_settlement is None or abs(row_settlement - official_delivery) > FATAL_EPS:
        raise ValueError(f"official settlement mismatch for row_id={row.get('row_id')}")
    side = _side(row.get("side"))
    short_strike = _num(row.get("short_strike"))
    long_strike = _num(row.get("long_strike"))
    expected_payout = _num(row.get("payout_btc"))
    expected_loss = _num(row.get("loss_normalized"))
    actual_width = _num(row.get("actual_width"))
    entry_price = _num(row.get("entry_price"))
    if None in (short_strike, long_strike, expected_payout, expected_loss, actual_width, entry_price):
        raise ValueError(f"missing payout fields for row_id={row.get('row_id')}")
    payout = _payout_btc(side, float(short_strike), float(long_strike), official_delivery)
    if abs(payout - float(expected_payout)) > FATAL_EPS:
        raise ValueError(f"original payout mismatch for row_id={row.get('row_id')}")
    denominator = float(actual_width) / float(entry_price)
    if denominator <= 0 or abs(payout / denominator - float(expected_loss)) > FATAL_EPS:
        raise ValueError(f"original loss normalization mismatch for row_id={row.get('row_id')}")


def _gap_row(row: dict[str, Any], reason: str, detail: str | None = None) -> dict[str, Any]:
    return {column: row.get(column) for column in GAP_COLUMNS if column in row} | {
        "reason": reason,
        "detail": detail or "",
    }


def build_pair_row(
    row: dict[str, Any],
    outward_short: dict[str, Any],
    outward_long: dict[str, Any],
    official_delivery: float,
) -> dict[str, Any]:
    side = _side(row.get("side"))
    entry_price = float(row["entry_price"])
    actual_width = float(row["actual_width"])
    short_strike = float(row["short_strike"])
    long_strike = float(row["long_strike"])
    outward_short_strike = float(outward_short["strike"])
    outward_long_strike = float(outward_long["strike"])
    original_width = _credit_width(side, short_strike, long_strike)
    outward_width = _credit_width(side, outward_short_strike, outward_long_strike)
    if not _same(original_width, actual_width) or not _same(outward_width, actual_width):
        raise ValueError(f"paired width mismatch for row_id={row.get('row_id')}")
    payout_a = _payout_btc(side, short_strike, long_strike, official_delivery)
    payout_b = _payout_btc(side, outward_short_strike, outward_long_strike, official_delivery)
    delta = payout_a - payout_b
    if delta < -FATAL_EPS:
        raise ValueError(f"negative outward saving for row_id={row.get('row_id')}")
    denominator = actual_width / entry_price
    a_short_breached, a_protection_breached, a_category = _tail_flags(side, short_strike, long_strike, official_delivery)
    b_short_breached, b_protection_breached, b_category = _tail_flags(side, outward_short_strike, outward_long_strike, official_delivery)
    shift = abs(outward_short_strike - short_strike)
    result = {
        "row_id": row.get("row_id"),
        "observation_id": row.get("observation_id"),
        "side": side,
        "side_credit": _side_credit(side),
        "as_of_ms": _int_ms(row.get("as_of_ms")),
        "as_of_utc": row.get("as_of_utc"),
        "entry_ms": _int_ms(row.get("entry_ms")),
        "entry_utc": row.get("entry_utc"),
        "expiry_ms": _int_ms(row.get("expiry_ms")),
        "expiry_utc": row.get("expiry_utc"),
        "delivery_date": row.get("delivery_date"),
        "delivery_year": _int_ms(row.get("delivery_year")),
        "actual_width": actual_width,
        "entry_price": entry_price,
        "dte_hours": float(row["dte_hours"]),
        "short_strike": short_strike,
        "long_strike": long_strike,
        "outward_short_strike": outward_short_strike,
        "outward_long_strike": outward_long_strike,
        "shift": shift,
        "distance_to_width": abs(entry_price - short_strike) / actual_width,
        "shift_to_width": shift / actual_width,
        "short_distance_fraction": float(row["short_distance_fraction"]),
        "outward_short_distance_fraction": abs(outward_short_strike / entry_price - 1.0),
        "width_fraction": float(row["width_fraction"]),
        "vol_240": float(row["vol_240"]),
        "short_name": row.get("short_name"),
        "long_name": row.get("long_name"),
        "outward_short_name": outward_short["instrument_name"],
        "outward_long_name": outward_long["instrument_name"],
        "short_creation_ms": _int_ms(row.get("short_creation_ms")),
        "long_creation_ms": _int_ms(row.get("long_creation_ms")),
        "outward_short_creation_ms": outward_short["creation_timestamp"],
        "outward_long_creation_ms": outward_long["creation_timestamp"],
        "settlement_price": float(row["settlement_price"]),
        "official_delivery_price": official_delivery,
        "payout_btc": payout_a,
        "outward_payout_btc": payout_b,
        "loss_normalized": payout_a / denominator,
        "outward_loss_normalized": payout_b / denominator,
        "normalization_denominator_btc": denominator,
        "delta_btc": delta,
        "delta_normalized": delta / denominator,
        "short_leg_breached": a_short_breached,
        "protection_leg_breached": a_protection_breached,
        "outward_short_leg_breached": b_short_breached,
        "outward_protection_leg_breached": b_protection_breached,
        "both_safe": payout_a <= EPS and payout_b <= EPS,
        "both_protection_leg_breached": a_protection_breached and b_protection_breached,
        "payout_category": a_category,
        "outward_payout_category": b_category,
    }
    for column in SOURCE_COLUMNS + FEATURE_COLUMNS:
        result[column] = row.get(column)
    return result


def read_model_rows(research_v11: Path, years: list[int]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    source_files: dict[str, dict[str, Any]] = {}
    model_dir = research_v11 / "step30" / "model_input"
    use_columns = set(MODEL_REQUIRED) | set(SOURCE_COLUMNS) | set(FEATURE_COLUMNS)
    for year in years:
        path = model_dir / f"model_rows-{year}.csv"
        if not path.exists():
            raise FileNotFoundError(path)
        source_files[str(path)] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
        frame = pd.read_csv(path, usecols=lambda column: column in use_columns, low_memory=False)
        missing = [column for column in MODEL_REQUIRED if column not in frame.columns]
        if missing:
            raise ValueError(f"{path.name} missing columns: {missing}")
        primary = frame[
            (frame["target_width"].astype(float).sub(PRIMARY_WIDTH).abs() <= EPS)
            & (frame["outcome_status"].astype(str).str.lower() == "settled")
        ].copy()
        rows.extend(primary.to_dict("records"))
    return rows, source_files


def _source_manifest_path(output: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    candidate = output.parent / SOURCE_MANIFEST_NAME
    if not candidate.exists():
        raise ValueError(f"missing required source manifest: {candidate}")
    return candidate


def _verify_source_manifest(source_files: dict[str, dict[str, Any]], source_manifest: Path) -> dict[str, Any]:
    manifest = load_json(source_manifest)
    files = manifest.get("files", {})
    missing: list[str] = []
    mismatches: list[dict[str, Any]] = []
    for path, stats in source_files.items():
        expected = files.get(path)
        if expected is None:
            missing.append(path)
            continue
        if expected.get("sha256") != stats.get("sha256") or int(expected.get("bytes", -1)) != int(stats.get("bytes", -2)):
            mismatches.append({"path": path, "expected": expected, "actual": stats})
    if missing or mismatches:
        raise ValueError(f"source manifest mismatch: missing={len(missing)} mismatches={len(mismatches)}")
    return {"checked": True, "path": str(source_manifest), "missing_from_manifest": [], "mismatches": []}


def build_structure_ledger(
    research_v11: Path,
    research_v1: Path,
    output: Path,
    years: list[int],
    source_manifest: Path | None = None,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    instruments_path = research_v1 / "raw" / "deribit" / "instruments.json"
    deliveries_path = research_v1 / "raw" / "deribit" / "delivery_prices.json"
    rows, source_files = read_model_rows(research_v11, years)
    source_files[str(instruments_path)] = {"sha256": sha256_file(instruments_path), "bytes": instruments_path.stat().st_size}
    source_files[str(deliveries_path)] = {"sha256": sha256_file(deliveries_path), "bytes": deliveries_path.stat().st_size}
    manifest_check = _verify_source_manifest(source_files, _source_manifest_path(output, source_manifest))

    contracts, by_expiry_side = load_instruments(instruments_path)
    deliveries = load_deliveries(deliveries_path)
    pairs: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []

    for row in rows:
        delivery_date = row.get("delivery_date")
        official_delivery = deliveries.get(str(delivery_date))
        if official_delivery is None:
            gaps.append(_gap_row(row, "missing_official_delivery", str(delivery_date)))
            continue
        _fatal_if_source_mismatch(row, official_delivery)
        outward_short, outward_long, reason = find_outward_pair(row, contracts, by_expiry_side)
        if reason is not None or outward_short is None or outward_long is None:
            gaps.append(_gap_row(row, reason or "unknown_pairing_gap"))
            continue
        pairs.append(build_pair_row(row, outward_short, outward_long, official_delivery))

    pair_frame = pd.DataFrame(pairs, columns=PAIR_COLUMNS)
    gap_frame = pd.DataFrame(gaps, columns=GAP_COLUMNS)
    pair_path = output / "paired_structure_rows.csv"
    gap_path = output / "pairing_gaps.csv"
    pair_frame.to_csv(pair_path, index=False, encoding="utf-8-sig")
    gap_frame.to_csv(gap_path, index=False, encoding="utf-8-sig")
    outputs = {
        pair_path.name: {"sha256": sha256_file(pair_path), "rows": len(pair_frame)},
        gap_path.name: {"sha256": sha256_file(gap_path), "rows": len(gap_frame)},
    }

    manifest = _manifest(rows, pairs, gaps, years, source_files, manifest_check, outputs)
    write_json(output / "manifest.json", manifest)
    return manifest


def _coverage(rows: list[dict[str, Any]], pairs: list[dict[str, Any]], gaps: list[dict[str, Any]]) -> dict[str, Any]:
    paired_ids = {row["row_id"] for row in pairs}
    by_year_side: dict[str, dict[str, int]] = defaultdict(lambda: {"input": 0, "paired": 0, "gaps": 0})
    dates: dict[str, set[str]] = defaultdict(set)
    paired_dates: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        key = f"{int(row['delivery_year'])}:{_side(row['side'])}"
        by_year_side[key]["input"] += 1
        dates[key].add(str(row.get("delivery_date")))
        if row.get("row_id") in paired_ids:
            by_year_side[key]["paired"] += 1
            paired_dates[key].add(str(row.get("delivery_date")))
        else:
            by_year_side[key]["gaps"] += 1
    for key, item in by_year_side.items():
        item["delivery_dates"] = len(dates[key])
        item["paired_delivery_dates"] = len(paired_dates[key])
        item["paired_fraction"] = item["paired"] / item["input"] if item["input"] else None
    return {
        "input_rows": len(rows),
        "paired_rows": len(pairs),
        "gap_rows": len(gaps),
        "paired_fraction": len(pairs) / len(rows) if rows else None,
        "by_year_side": dict(sorted(by_year_side.items())),
        "gap_reasons": dict(Counter(row["reason"] for row in gaps)),
    }


def _distribution(rows: list[dict[str, Any]], column: str) -> dict[str, float | int | None]:
    values = sorted(_num(row.get(column)) for row in rows)
    values = [value for value in values if value is not None]
    if not values:
        return {"count": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None}
    return {
        "count": len(values),
        "min": values[0],
        "p25": _quantile(values, 0.25),
        "median": _quantile(values, 0.5),
        "p75": _quantile(values, 0.75),
        "max": values[-1],
    }


def _quantile(values: list[float], q: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _distance_to_width(row: dict[str, Any]) -> float | None:
    entry_price = _num(row.get("entry_price"))
    short_strike = _num(row.get("short_strike"))
    width = _num(row.get("actual_width"))
    if entry_price is None or short_strike is None or width is None or width <= 0:
        return None
    return abs(entry_price - short_strike) / width


def _width_fraction(row: dict[str, Any]) -> float | None:
    entry_price = _num(row.get("entry_price"))
    width = _num(row.get("actual_width"))
    if entry_price is None or width is None or entry_price <= 0:
        return None
    return width / entry_price


def _metric_distribution(rows: list[dict[str, Any]], metric: str) -> dict[str, float | int | None]:
    if metric == "distance_to_width":
        values = [_distance_to_width(row) for row in rows]
    elif metric == "width_fraction":
        values = [_width_fraction(row) for row in rows]
    else:
        values = [_num(row.get(metric)) for row in rows]
    filtered = [{"value": value} for value in values if value is not None]
    return _distribution(filtered, "value")


def _geometry_distributions(
    rows: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    base_metrics = ("distance_to_width", "width_fraction", "dte_hours")
    return {
        "original_all_rows": {metric: _metric_distribution(rows, metric) for metric in base_metrics},
        "paired_rows": {metric: _metric_distribution(pairs, metric) for metric in base_metrics},
        "gap_rows": {metric: _metric_distribution(gaps, metric) for metric in base_metrics},
        "paired_shift_to_width": _metric_distribution(pairs, "shift_to_width"),
    }


def _data_integrity(gaps: list[dict[str, Any]]) -> dict[str, Any]:
    integrity = Counter(row["reason"] for row in gaps if row.get("reason") in INTEGRITY_GAP_REASONS)
    availability = Counter(row["reason"] for row in gaps if row.get("reason") not in INTEGRITY_GAP_REASONS)
    return {
        "ok": sum(integrity.values()) == 0,
        "integrity_gap_rows": sum(integrity.values()),
        "integrity_gap_reasons": dict(integrity),
        "availability_gap_rows": sum(availability.values()),
        "availability_gap_reasons": dict(availability),
    }


def _manifest(
    rows: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    years: list[int],
    source_files: dict[str, dict[str, Any]],
    manifest_check: dict[str, Any],
    outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    data_integrity = _data_integrity(gaps)
    return {
        "schema": SCHEMA,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": "same-side nearest outward exact-width structure label ledger; research-only; no credit, true EV, win-rate, or deployment conclusion",
        "years": years,
        "selection_rule": "target_width=2000 and outcome_status=settled; nearest strictly outward short leg with exact original actual_width protection; both legs listed by entry_ms; same expiry/side/base/settlement/contract-size attributes",
        "label": "delta_btc=payout_btc-outward_payout_btc; delta_normalized=delta_btc/(actual_width/entry_price); uncapped inverse BTC tails retained",
        "source_files": source_files,
        "source_manifest_check": manifest_check,
        "outputs": outputs,
        "data_integrity_ok": data_integrity["ok"],
        "data_integrity": data_integrity,
        "columns": {
            "paired_structure_rows.csv": list(PAIR_COLUMNS),
            "pairing_gaps.csv": list(GAP_COLUMNS),
        },
        "coverage": _coverage(rows, pairs, gaps),
        "geometry_distribution_label_free": _geometry_distributions(rows, pairs, gaps),
        "non_conclusions": [
            "paired payout saving is not a historical executable credit or EV estimate",
            "positive delta identity is not a deployable rule",
            "gaps are retained and are not imputed by approximate width or future listings",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-v11", type=Path, required=True)
    parser.add_argument("--research-v1", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--years", type=int, nargs="+", default=[2020, 2021, 2022, 2023, 2024, 2025])
    parser.add_argument("--source-manifest", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_structure_ledger(args.research_v11, args.research_v1, args.output, args.years, args.source_manifest)
    print(json.dumps({"schema": manifest["schema"], "coverage": manifest["coverage"]}, ensure_ascii=False, indent=2))
    if not manifest.get("data_integrity_ok", False):
        raise SystemExit("data integrity gaps were written; manifest data_integrity_ok=false")


if __name__ == "__main__":
    main()
