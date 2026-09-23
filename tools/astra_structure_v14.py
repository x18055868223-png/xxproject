"""v1.4 structure calibration experiment on sealed v13 exact pairs.

Research-only. The module estimates payout-saving labels already present in the
v13 structure ledger. It does not train a production model, use option credit,
call an API, deploy, or alter older artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_MAX_THREADS"):
    os.environ[_name] = "6"

import numpy as np
import pandas as pd


SCHEMA = "astra_structure_v14@1.0.0"
CANDIDATES = ("COARSE_GEOM_SHRINK", "COARSE_GEOM_CAL3M")
OLD_COMPARATORS = ("HIST_SIDE_MEAN", "GEOMETRY_TABLE", "GEOMETRY_VOL_TABLE")
PAIR_FILE = "structure_ledger_01/paired_structure_rows.csv"
GAP_FILE = "structure_ledger_01/pairing_gaps.csv"
LEDGER_MANIFEST = "structure_ledger_01/manifest.json"
V13_PREDICTIONS = "structure_evaluation_01/structure_predictions.csv"
EPS = 1e-12


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(_jsonable(obj), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "item"):
        return _jsonable(value.item())
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def day_weights(frame: pd.DataFrame) -> np.ndarray:
    if frame.empty:
        return np.asarray([], dtype=float)
    return (1.0 / frame.groupby("delivery_date")["delivery_date"].transform("size")).to_numpy(float)


def weighted_mean(values: Any, weights: Any) -> float | None:
    x = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    valid = np.isfinite(x) & np.isfinite(w) & (w > 0)
    if not valid.any():
        return None
    return float(np.average(x[valid], weights=w[valid]))


def weighted_es(values: Any, weights: Any, quantile: float = 0.95) -> float | None:
    x = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    valid = np.isfinite(x) & np.isfinite(w) & (w > 0)
    if not valid.any():
        return None
    x, w = x[valid], w[valid]
    order = np.argsort(-x, kind="stable")
    x, w = x[order], w[order] / w.sum()
    tail = 1.0 - quantile
    taken = np.minimum(w, np.maximum(0.0, tail - (np.cumsum(w) - w)))
    return float(np.dot(x, taken) / tail)


def calendar_bootstrap_daily(daily: pd.Series, confidence: float, reps: int, seed: int) -> dict[str, Any]:
    if daily.empty:
        return {"lower": None, "upper": None, "blocks": 0, "confidence": confidence, "replicates": reps, "seed": seed}
    dates = pd.to_datetime(daily.index)
    blocks = np.asarray((dates - pd.Timestamp("1970-01-01")).days // 7)
    block_values = (
        pd.DataFrame({"block": blocks, "value": daily.to_numpy(float)})
        .groupby("block")
        .agg(total=("value", "sum"), count=("value", "size"))
    )
    if len(block_values) < 2:
        mean = float(daily.mean())
        return {"lower": mean, "upper": mean, "blocks": len(block_values), "confidence": confidence, "replicates": reps, "seed": seed}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(block_values), size=(reps, len(block_values)))
    totals = block_values.total.to_numpy()[idx].sum(axis=1)
    counts = block_values["count"].to_numpy()[idx].sum(axis=1)
    draws = totals / counts
    alpha = (1.0 - confidence) / 2.0
    lower, upper = np.quantile(draws, [alpha, 1.0 - alpha])
    return {
        "lower": float(lower),
        "upper": float(upper),
        "blocks": len(block_values),
        "confidence": confidence,
        "replicates": reps,
        "seed": seed,
        "calendar_block_days": 7,
    }


def bin_value(value: Any, edges: list[float]) -> int:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite geometry feature")
    return int(np.searchsorted(np.asarray(edges, dtype=float), number, side="right"))


def geometry_key(row: pd.Series, protocol: dict[str, Any]) -> str:
    edges = protocol["structure"]["geometry_edges"]
    return "|".join(
        str(bin_value(row[column], edges[column]))
        for column in ("dte_hours", "distance_to_width", "shift_to_width", "width_fraction")
    )


def add_geometry(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["distance_to_width"] = (out.entry_price - out.short_strike).abs() / out.actual_width
    out["shift_to_width"] = (out.outward_short_strike - out.short_strike).abs() / out.actual_width
    out["width_fraction"] = out.actual_width / out.entry_price
    required = [
        "delta_normalized",
        "actual_width",
        "entry_price",
        "distance_to_width",
        "shift_to_width",
        "width_fraction",
        "dte_hours",
    ]
    if not np.isfinite(out[required].to_numpy(float)).all():
        raise ValueError("nonfinite structure geometry or label")
    if (out.actual_width <= 0).any() or (out.entry_price <= 0).any() or (out.shift_to_width <= 0).any():
        raise ValueError("invalid structure geometry")
    if (out.delta_normalized < -EPS).any():
        raise ValueError("negative payout saving label")
    return out


def split_period(frame: pd.DataFrame, start: str, end: str) -> tuple[pd.DataFrame, int]:
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    in_asof = (frame.as_of_ms >= start_ms) & (frame.as_of_ms < end_ms)
    purged = int((in_asof & (frame.expiry_ms >= end_ms)).sum())
    return frame.loc[in_asof & (frame.expiry_ms < end_ms)].copy(), purged


def split_year(frame: pd.DataFrame, year: int) -> tuple[tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame], dict[str, Any]]:
    ranges = [
        (f"{year - 2}-01-01", f"{year - 1}-10-01"),
        (f"{year - 1}-10-01", f"{year}-01-01"),
        (f"{year}-01-01", f"{year + 1}-01-01"),
    ]
    parts: list[pd.DataFrame] = []
    purged: list[int] = []
    for start, end in ranges:
        part, count = split_period(frame, start, end)
        parts.append(part)
        purged.append(count)
    for left in range(3):
        for right in range(left + 1, 3):
            if set(parts[left].delivery_date) & set(parts[right].delivery_date):
                raise ValueError("delivery-date leakage across fit/calibration/evaluation")
    return (parts[0], parts[1], parts[2]), {
        "ranges": ranges,
        "purged_cross_boundary_rows": purged,
        "rows": [len(part) for part in parts],
        "delivery_days": [int(part.delivery_date.nunique()) for part in parts],
    }


def daily_mean(values: pd.Series, dates: pd.Series) -> pd.Series:
    return pd.DataFrame({"date": dates, "value": values}).groupby("date").value.mean()


def fit_coarse_model(fit: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, Any]:
    if fit.empty or fit.side.nunique() != 1:
        raise ValueError("fit_coarse_model needs one nonempty side")
    minimum_days = int(protocol["structure"]["minimum_cell_days"])
    prior_days = float(protocol["structure"]["prior_days"])
    side_daily = daily_mean(fit.delta_normalized, fit.delivery_date)
    side_mean = float(side_daily.mean())
    work = fit.copy()
    work["geometry_key"] = work.apply(lambda row: geometry_key(row, protocol), axis=1)
    cells: dict[str, dict[str, Any]] = {}
    for key, group in work.groupby("geometry_key", sort=True):
        daily = daily_mean(group.delta_normalized, group.delivery_date)
        if len(daily) >= minimum_days:
            cells[key] = {
                "days": int(len(daily)),
                "daily_mean": float(daily.mean()),
                "prediction": float((daily.sum() + prior_days * side_mean) / (len(daily) + prior_days)),
            }
    return {
        "side": str(fit.side.iloc[0]),
        "side_mean": side_mean,
        "minimum_cell_days": minimum_days,
        "prior_days": prior_days,
        "cells": cells,
    }


def predict_coarse(frame: pd.DataFrame, model: dict[str, Any], protocol: dict[str, Any]) -> pd.DataFrame:
    out = frame.copy()
    out["geometry_key_v14"] = out.apply(lambda row: geometry_key(row, protocol), axis=1)
    out["COARSE_GEOM_SHRINK"] = [
        model["cells"].get(key, {}).get("prediction", model["side_mean"])
        for key in out["geometry_key_v14"]
    ]
    out["COARSE_GEOM_SHRINK_supported"] = [key in model["cells"] for key in out["geometry_key_v14"]]
    return out


def fit_calibration(calibration: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, Any]:
    prior_days = float(protocol["structure"]["calibration_prior_days"])
    minimum_days = int(protocol["structure"]["calibration_minimum_days"])
    days = int(calibration.delivery_date.nunique()) if not calibration.empty else 0
    if days < minimum_days:
        return {"scale": 1.0, "days": days, "qualified": False, "reason": "insufficient_calibration_days"}
    weights = day_weights(calibration)
    mean_actual = weighted_mean(calibration.delta_normalized, weights)
    mean_pred = weighted_mean(calibration.COARSE_GEOM_SHRINK, weights)
    if mean_actual is None or mean_pred is None:
        return {"scale": 1.0, "days": days, "qualified": False, "reason": "nonfinite_calibration"}
    if abs(mean_pred) <= EPS:
        if abs(mean_actual) <= EPS:
            return {"scale": 1.0, "days": days, "qualified": True, "reason": "zero_pred_zero_actual", "mean_actual": mean_actual, "mean_pred": mean_pred}
        return {"scale": 1.0, "days": days, "qualified": False, "reason": "zero_pred_positive_actual", "mean_actual": mean_actual, "mean_pred": mean_pred}
    scale = (days * mean_actual + prior_days * mean_pred) / ((days + prior_days) * mean_pred)
    if not math.isfinite(scale) or scale < 0:
        return {"scale": 1.0, "days": days, "qualified": False, "reason": "invalid_negative_scale", "mean_actual": mean_actual, "mean_pred": mean_pred}
    return {"scale": float(scale), "days": days, "qualified": True, "reason": "qualified", "mean_actual": mean_actual, "mean_pred": mean_pred}


def apply_calibration(frame: pd.DataFrame, calibration: dict[str, Any]) -> pd.DataFrame:
    out = frame.copy()
    out["COARSE_GEOM_CAL3M"] = out.COARSE_GEOM_SHRINK * float(calibration["scale"])
    out["COARSE_GEOM_CAL3M_supported"] = out.COARSE_GEOM_SHRINK_supported & bool(calibration["qualified"])
    out["COARSE_GEOM_CAL3M_scale"] = float(calibration["scale"])
    out["COARSE_GEOM_CAL3M_calibration_qualified"] = bool(calibration["qualified"])
    return out


def validate_protocol(protocol_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol_path = Path(protocol_path)
    protocol = read_json(protocol_path)
    seal = read_json(protocol_path.parent / "protocol_seal.json")
    protocol_hash = sha256_file(protocol_path)
    if seal.get("protocol_sha256") != protocol_hash:
        raise ValueError("protocol seal mismatch")
    source_manifest_path = protocol_path.parent / "source_manifest.json"
    source_hash = sha256_file(source_manifest_path)
    if protocol.get("source_manifest_sha256") != source_hash:
        raise ValueError("source manifest seal mismatch")
    if protocol.get("schema") != "astra_entry_exit_protocol@1.4.0":
        raise ValueError("unexpected v14 protocol schema")
    structure = protocol.get("structure", {})
    if structure.get("candidates") != list(CANDIDATES):
        raise ValueError("unexpected v14 structure candidates")
    amendment_path = protocol_path.parent / "preresult_amendment_01.json"
    amendment_hash = sha256_file(amendment_path) if amendment_path.exists() else None
    return protocol, {
        "protocol_path": str(protocol_path),
        "protocol_sha256": protocol_hash,
        "preresult_amendment_01_sha256": amendment_hash,
        "source_manifest_path": str(source_manifest_path),
        "source_manifest_sha256": source_hash,
        "source_manifest": read_json(source_manifest_path),
    }


def _manifest_item(source_manifest: dict[str, Any], path: Path) -> dict[str, Any]:
    item = source_manifest.get("files", {}).get(str(path))
    if item is None:
        raise ValueError(f"source manifest missing input: {path}")
    return item


def verify_file_hash(source_manifest: dict[str, Any], path: Path) -> dict[str, Any]:
    item = _manifest_item(source_manifest, path)
    actual = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    if item.get("sha256") != actual["sha256"] or int(item.get("bytes", -1)) != actual["bytes"]:
        raise ValueError(f"frozen input hash mismatch: {path}")
    return actual


def verify_inputs(research_v13: Path, meta: dict[str, Any]) -> dict[str, Any]:
    source_manifest = meta["source_manifest"]
    paths = {
        "paired_ledger": research_v13 / PAIR_FILE,
        "pairing_gaps": research_v13 / GAP_FILE,
        "ledger_manifest": research_v13 / LEDGER_MANIFEST,
        "v13_predictions": research_v13 / V13_PREDICTIONS,
    }
    hashes = {name: verify_file_hash(source_manifest, path) | {"path": str(path)} for name, path in paths.items()}
    ledger_manifest = read_json(paths["ledger_manifest"])
    data_integrity = ledger_manifest.get("data_integrity")
    if not isinstance(data_integrity, dict) or data_integrity.get("ok") is not True:
        raise ValueError("v13 ledger data_integrity.ok is not true")
    for name, filename in (("paired_ledger", "paired_structure_rows.csv"), ("pairing_gaps", "pairing_gaps.csv")):
        expected = ledger_manifest.get("outputs", {}).get(filename, {})
        if expected.get("sha256") != hashes[name]["sha256"]:
            raise ValueError(f"v13 ledger output hash mismatch: {filename}")
    return hashes


def load_delivery_prices(source_manifest: dict[str, Any]) -> dict[str, float]:
    matches = [Path(path) for path in source_manifest.get("files", {}) if Path(path).name == "delivery_prices.json"]
    if len(matches) != 1:
        raise ValueError("ambiguous official delivery prices in source manifest")
    verify_file_hash(source_manifest, matches[0])
    prices = {}
    for row in read_json(matches[0]):
        prices[str(row["date"])] = float(row["delivery_price"])
    return prices


def validate_pair_arithmetic(frame: pd.DataFrame, delivery_prices: dict[str, float]) -> dict[str, Any]:
    required = [
        "row_id",
        "side",
        "delivery_date",
        "actual_width",
        "entry_price",
        "short_strike",
        "long_strike",
        "outward_short_strike",
        "outward_long_strike",
        "settlement_price",
        "payout_btc",
        "outward_payout_btc",
        "delta_btc",
        "loss_normalized",
        "outward_loss_normalized",
        "delta_normalized",
    ]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"paired ledger missing columns: {missing}")
    if frame.row_id.duplicated().any():
        raise ValueError("duplicate paired row_id")
    put = frame.side.eq("put").to_numpy()
    settlement = frame.delivery_date.map(delivery_prices).to_numpy(float)
    if not np.isfinite(settlement).all() or not np.allclose(settlement, frame.settlement_price.to_numpy(float), rtol=0, atol=1e-9):
        raise ValueError("official delivery mismatch")
    k = frame.short_strike.to_numpy(float)
    l = frame.long_strike.to_numpy(float)
    ko = frame.outward_short_strike.to_numpy(float)
    lo = frame.outward_long_strike.to_numpy(float)
    width = frame.actual_width.to_numpy(float)
    entry = frame.entry_price.to_numpy(float)
    payout = np.where(put, np.maximum(k - settlement, 0) - np.maximum(l - settlement, 0), np.maximum(settlement - k, 0) - np.maximum(settlement - l, 0)) / settlement
    outward = np.where(put, np.maximum(ko - settlement, 0) - np.maximum(lo - settlement, 0), np.maximum(settlement - ko, 0) - np.maximum(settlement - lo, 0)) / settlement
    expected = {
        "payout_btc": payout,
        "outward_payout_btc": outward,
        "delta_btc": payout - outward,
        "loss_normalized": payout / (width / entry),
        "outward_loss_normalized": outward / (width / entry),
        "delta_normalized": (payout - outward) / (width / entry),
    }
    errors = {}
    for column, values in expected.items():
        error = np.abs(frame[column].to_numpy(float) - values)
        if not np.isfinite(error).all() or (error > 1e-9).any():
            raise ValueError(f"independent original/outward payout parity failed: {column}")
        errors[column] = float(error.max()) if len(error) else 0.0
    return {"rows": len(frame), "maximum_absolute_errors": errors, "uncapped_tail_rows": int((frame.loss_normalized > 1).sum())}


def load_inputs(research_v13: Path, source_manifest: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    pairs = add_geometry(pd.read_csv(research_v13 / PAIR_FILE))
    old = pd.read_csv(research_v13 / V13_PREDICTIONS)
    required_old = ["row_id", "delta_normalized", *OLD_COMPARATORS]
    missing = [column for column in required_old if column not in old.columns]
    if missing:
        raise ValueError(f"v13 predictions missing columns: {missing}")
    if old.row_id.duplicated().any():
        raise ValueError("duplicate v13 prediction row_id")
    if not old.row_id.isin(pairs.row_id).all():
        raise ValueError("v13 prediction row_id is not present in paired ledger")
    parity = validate_pair_arithmetic(pairs, load_delivery_prices(source_manifest))
    gaps = pd.read_csv(research_v13 / GAP_FILE)
    return pairs, old[required_old], gaps, parity


def attach_old_comparators(result: pd.DataFrame, old: pd.DataFrame) -> pd.DataFrame:
    merged = result.merge(old, on="row_id", suffixes=("", "_v13"), how="inner", validate="one_to_one")
    if len(merged) != len(result) or len(merged) != len(old):
        raise ValueError("v13 prediction row_id is not one-to-one with v14 evaluation rows")
    if not np.allclose(merged.delta_normalized, merged.delta_normalized_v13, rtol=0, atol=1e-12):
        raise ValueError("v13 prediction label does not match paired ledger")
    return merged.drop(columns=["delta_normalized_v13"])


def evaluation_gap_rows(gaps: pd.DataFrame, years: list[int], side: str) -> pd.DataFrame:
    if gaps.empty:
        return gaps.copy()
    required = {"side", "as_of_ms", "expiry_ms", "delivery_date"}
    missing = sorted(required - set(gaps.columns))
    if missing:
        raise ValueError(f"gap ledger missing evaluation-window columns: {missing}")
    side_gaps = gaps[gaps.side == side].copy()
    parts = []
    for year in years:
        part, _ = split_period(side_gaps, f"{year}-01-01", f"{year + 1}-01-01")
        parts.append(part)
    return pd.concat(parts, ignore_index=True) if parts else side_gaps.iloc[0:0].copy()


def year_support(result: pd.DataFrame, gaps: pd.DataFrame, years: list[int], side: str, supported_column: str, calibration_required: bool) -> dict[str, Any]:
    side_eval = result[result.side == side]
    side_gaps = evaluation_gap_rows(gaps, years, side)
    original_rows = len(side_eval) + len(side_gaps)
    paired_fraction = len(side_eval) / original_rows if original_rows else 0.0
    annual_days = {str(year): int(side_eval[side_eval.evaluation_year == year].delivery_date.nunique()) for year in years}
    weights = day_weights(side_eval)
    nonfallback = weighted_mean(side_eval[supported_column].astype(float), weights) if len(side_eval) else 0.0
    calibration_ok = True
    if calibration_required and "COARSE_GEOM_CAL3M_calibration_qualified" in side_eval:
        calibration_ok = bool(side_eval.groupby("evaluation_year").COARSE_GEOM_CAL3M_calibration_qualified.all().all())
    support = {
        "paired_days_at_least_200": int(side_eval.delivery_date.nunique()) >= 200,
        "each_year_at_least_30_days": all(value >= 30 for value in annual_days.values()) and len(annual_days) == len(years),
        "paired_fraction_at_least_half": paired_fraction >= 0.5,
        "nonfallback_date_weight_at_least_half": (nonfallback or 0.0) >= 0.5,
        "calibration_all_folds_qualified": calibration_ok,
    }
    return {
        "passes": all(support.values()),
        "checks": support,
        "paired_rows": len(side_eval),
        "original_rows": original_rows,
        "paired_fraction": paired_fraction,
        "paired_days": int(side_eval.delivery_date.nunique()),
        "annual_paired_days": annual_days,
        "nonfallback_date_weight_fraction": nonfallback,
    }


def summarize_model(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    weights = day_weights(frame)
    error = frame[column].to_numpy(float) - frame.delta_normalized.to_numpy(float)
    denom = frame.actual_width / frame.entry_price
    return {
        "rows": len(frame),
        "days": int(frame.delivery_date.nunique()),
        "prediction_mean": weighted_mean(frame[column], weights),
        "actual_mean": weighted_mean(frame.delta_normalized, weights),
        "mse": weighted_mean(error * error, weights),
        "mae": weighted_mean(np.abs(error), weights),
        "bias": weighted_mean(error, weights),
        "predicted_credit_concession_btc_mean": weighted_mean(frame[column] * denom, weights),
        "actual_credit": None,
        "true_ev": None,
        "true_win_rate": None,
    }


def compare_candidate(frame: pd.DataFrame, candidate: str, baseline: str, protocol: dict[str, Any], support: dict[str, Any]) -> dict[str, Any]:
    work = frame.copy()
    work["mse_difference"] = (work[candidate] - work.delta_normalized) ** 2 - (work[baseline] - work.delta_normalized) ** 2
    daily = work.groupby("delivery_date").mse_difference.mean()
    annual = {
        str(year): float(part.groupby("delivery_date").mse_difference.mean().mean())
        for year, part in work.groupby("evaluation_year")
    }
    interval = calendar_bootstrap_daily(
        daily,
        0.9875,
        int(protocol["uncertainty"]["replicates"]),
        int(protocol["uncertainty"]["seed"]),
    )
    candidate_summary = summarize_model(work, candidate)
    baseline_summary = summarize_model(work, baseline)
    hist_summary = summarize_model(work, "HIST_SIDE_MEAN")
    without_best = daily.sort_values().iloc[10:]
    finite_nonnegative = bool(np.isfinite(work[candidate].to_numpy(float)).all() and (work[candidate] >= -EPS).all())
    gates = {
        "mse_ci_upper_negative_vs_v13_geometry": interval["upper"] is not None and interval["upper"] < 0,
        "nonworse_three_of_four_years": sum(value <= 0 for value in annual.values()) >= 3 and len(annual) == 4,
        "pooled_abs_bias_no_worse_than_geometry_and_hist": abs(candidate_summary["bias"]) <= abs(baseline_summary["bias"]) and abs(candidate_summary["bias"]) <= abs(hist_summary["bias"]),
        "mse_no_worse_than_hist_side_mean": candidate_summary["mse"] <= hist_summary["mse"],
        "improvement_without_ten_most_favorable_dates": len(without_best) > 0 and float(without_best.mean()) < 0,
        "support_passes": bool(support["passes"]),
        "finite_nonnegative_predictions": finite_nonnegative,
    }
    return {
        "candidate": candidate,
        "baseline": baseline,
        "mse_difference": float(daily.mean()),
        "interval": interval,
        "annual_mse_difference": annual,
        "without_ten_most_favorable_dates": float(without_best.mean()) if len(without_best) else None,
        "ten_most_favorable_dates": [{"date": date, "mse_difference": float(value)} for date, value in daily.nsmallest(10).items()],
        "ten_most_adverse_dates": [{"date": date, "mse_difference": float(value)} for date, value in daily.nlargest(10).items()],
        "gates": gates,
        "development_supported": all(gates.values()),
        "production_qualified": False,
    }


def build_predictions(frame: pd.DataFrame, protocol: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    predictions = []
    models: dict[str, Any] = {}
    for year in protocol["structure"]["evaluation_years"]:
        (fit, cal, ev), metadata = split_year(frame, int(year))
        models[str(year)] = {"window": metadata, "sides": {}}
        for side in ("put", "call"):
            fit_side = fit[fit.side == side].copy()
            cal_side = cal[cal.side == side].copy()
            ev_side = ev[ev.side == side].copy()
            if fit_side.empty or ev_side.empty:
                raise ValueError(f"missing fold population for {year}/{side}")
            model = fit_coarse_model(fit_side, protocol)
            cal_pred = predict_coarse(cal_side, model, protocol) if not cal_side.empty else cal_side.copy()
            calibration = fit_calibration(cal_pred, protocol) if not cal_side.empty else {"scale": 1.0, "days": 0, "qualified": False, "reason": "empty_calibration"}
            ev_pred = apply_calibration(predict_coarse(ev_side, model, protocol), calibration)
            ev_pred["evaluation_year"] = int(year)
            predictions.append(ev_pred)
            models[str(year)]["sides"][side] = {"model": model, "calibration": calibration}
    result = pd.concat(predictions, ignore_index=True)
    if result.row_id.duplicated().any():
        raise ValueError("evaluation overlap across folds")
    return result, models


def output_hashes(output: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for name in ("predictions.csv", "models.json", "summary.md", "failure.json"):
        path = output / name
        if path.exists():
            result[name] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    return result


def receipt_hashes(output: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted(output.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name != "receipt_manifest.json":
            result[path.name] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    return result


def run(research_v13: Path, protocol_path: Path, output: Path) -> dict[str, Any]:
    research_v13 = Path(research_v13)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    try:
        protocol, meta = validate_protocol(protocol_path)
        input_hashes = verify_inputs(research_v13, meta)
        frame, old, gaps, parity = load_inputs(research_v13, meta["source_manifest"])
        result, models = build_predictions(frame, protocol)
        result = attach_old_comparators(result, old)
        keep = [
            "row_id",
            "side",
            "evaluation_year",
            "delivery_date",
            "as_of_ms",
            "entry_ms",
            "expiry_ms",
            "actual_width",
            "entry_price",
            "dte_hours",
            "distance_to_width",
            "shift_to_width",
            "width_fraction",
            "delta_btc",
            "delta_normalized",
            "loss_normalized",
            "outward_loss_normalized",
            "HIST_SIDE_MEAN",
            "GEOMETRY_TABLE",
            "GEOMETRY_VOL_TABLE",
            "COARSE_GEOM_SHRINK",
            "COARSE_GEOM_CAL3M",
            "COARSE_GEOM_SHRINK_supported",
            "COARSE_GEOM_CAL3M_supported",
            "COARSE_GEOM_CAL3M_scale",
            "COARSE_GEOM_CAL3M_calibration_qualified",
        ]
        if "vol_240" in result.columns:
            keep.insert(13, "vol_240")
        result[keep].to_csv(output / "predictions.csv", index=False, encoding="utf-8")
        save_json(output / "models.json", {"schema": "astra_structure_v14_models@1.0.0", "models": models, "protocol_sha256": meta["protocol_sha256"]})

        summary: dict[str, Any] = {
            "schema": SCHEMA,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "protocol_sha256": meta["protocol_sha256"],
            "preresult_amendment_01_sha256": meta["preresult_amendment_01_sha256"],
            "source_manifest_sha256": meta["source_manifest_sha256"],
            "input_hashes": input_hashes,
            "input_parity": parity,
            "rows": len(result),
            "days": int(result.delivery_date.nunique()),
            "scope": "payoff-saving calibration on already-studied v13 exact pairs; no credit, true EV, win rate, runtime replacement, or natural NR effect",
            "actual_credit": None,
            "true_ev": None,
            "true_win_rate": None,
            "runtime_replacement": False,
            "natural_nr_effect": None,
            "sides": {},
        }
        for side, part in result.groupby("side", sort=True):
            side_summary = {
                "models": {name: summarize_model(part, name) for name in (*OLD_COMPARATORS, *CANDIDATES)},
                "full_tail": {
                    "original_es95": weighted_es(part.loss_normalized, day_weights(part)),
                    "outward_es95": weighted_es(part.outward_loss_normalized, day_weights(part)),
                    "original_over_one_rows": int((part.loss_normalized > 1).sum()),
                    "outward_over_one_rows": int((part.outward_loss_normalized > 1).sum()),
                },
                "comparisons": [],
            }
            for candidate in CANDIDATES:
                support = year_support(
                    result,
                    gaps,
                    [int(y) for y in protocol["structure"]["evaluation_years"]],
                    side,
                    candidate + "_supported",
                    candidate == "COARSE_GEOM_CAL3M",
                )
                side_summary[candidate + "_support"] = support
                side_summary["comparisons"].append(compare_candidate(part, candidate, "GEOMETRY_TABLE", protocol, support))
            summary["sides"][side] = side_summary
        save_json(output / "summary.json", summary)
        lines = [
            "# v1.4 同侧结构校准实验",
            "",
            "本实验只估计已封存精确外移结构的赔付节省标签，不含同期信用、真实EV或自然NR效果。",
            "",
            "| Side | Candidate | MSE diff vs v13 GEOMETRY_TABLE | Supported |",
            "|---|---|---:|---|",
        ]
        for side, side_summary in summary["sides"].items():
            for comp in side_summary["comparisons"]:
                lines.append(f"| {side} | {comp['candidate']} | {comp['mse_difference']:.8f} | {comp['development_supported']} |")
        (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        save_json(output / "failure.json", {"failed": False})
        summary["outputs"] = output_hashes(output)
        save_json(output / "summary.json", summary)
        save_json(output / "receipt_manifest.json", {"schema": "astra_structure_v14_receipt@1.0.0", "outputs": receipt_hashes(output)})
        return summary
    except Exception as exc:
        save_json(output / "failure.json", {"failed": True, "error": str(exc), "traceback": traceback.format_exc(), "preserved": True})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-v13", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = run(args.research_v13, args.protocol, args.output)
    print(json.dumps({"rows": summary["rows"], "days": summary["days"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
