"""Astra joint research v1.2 finite optimization experiment.

This module is deliberately narrow. It implements only the sealed v1.2
challengers:

* S50: fixed 50/50 geometry/statistical expected-loss blend.
* PAIR_RIDGE10: direct Put-minus-Call normalized payout-difference ridge.
* NESTED_LOGIT_C01: conditional protection-tail probability, multiplied by
  the sealed statistical occurrence probability.

All outputs are research artifacts. Nothing here replaces the v1.1 qualified
portable model or publishes per-card tail percentages.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import sys
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

for _thread_env_name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_MAX_THREADS"):
    os.environ.setdefault(_thread_env_name, "6")

import numpy as np
import pandas as pd

try:
    from threadpoolctl import threadpool_info, threadpool_limits
except Exception:  # pragma: no cover - exercised only when optional runtime helper is absent
    threadpool_info = None
    threadpool_limits = None

from astra_joint_v12_inference import (
    ARTIFACT_SCHEMA,
    finite_float,
    linear_score,
    logit,
    predict_nested_tail,
    predict_pair_delta,
    sigmoid,
    transform_row,
)

SCHEMA = "astra_joint_v12_experiment@1.0.0"
PAIR_FEATURES = [
    "put_short_distance_fraction",
    "call_short_distance_fraction",
    "width_fraction",
    "dte_hours",
    "ret_30",
    "ret_240",
    "ret_1440",
    "vol_30",
    "vol_240",
    "net_flow_30",
    "net_flow_240",
]
TAIL_FEATURES = [
    "dte_hours",
    "short_distance_fraction",
    "width_fraction",
    "side_sign",
    "ret_30",
    "ret_240",
    "vol_30",
    "vol_240",
    "net_flow_30",
    "net_flow_240",
]
MODEL_INPUT_COLUMNS = sorted(
    {
        "row_id",
        "observation_id",
        "as_of_ms",
        "entry_ms",
        "expiry_ms",
        "delivery_date",
        "side",
        "target_width",
        "actual_width",
        "entry_price",
        "loss_normalized",
        "payout_btc",
        "protection_breached",
        "protection_leg_breached",
        "short_leg_breached",
        "dte_hours",
        "short_distance_fraction",
        "width_fraction",
        "side_sign",
        "ret_30",
        "ret_240",
        "ret_1440",
        "vol_30",
        "vol_240",
        "net_flow_30",
        "net_flow_240",
    }
)
V11_CONTROL_FILES = {
    "geometry": "gam__geometry__C0.1__A0.1.csv",
    "statistical": "catboost__statistical__D4.csv",
    "joint": "catboost__joint__D3.csv",
}


@dataclass(frozen=True)
class SplitSpec:
    name: str
    fit_start: datetime
    cal_start: datetime
    eval_start: datetime
    eval_end: datetime


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_value(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def digest_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def threadpool_snapshot() -> dict[str, Any]:
    if threadpool_info is None:
        return {"status": "unavailable", "reason": "threadpoolctl_import_unavailable"}
    try:
        info = threadpool_info()
    except Exception as exc:
        return {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "status": "available",
        "libraries": [
            {
                "user_api": item.get("user_api"),
                "internal_api": item.get("internal_api"),
                "num_threads": item.get("num_threads"),
                "prefix": item.get("prefix"),
                "filepath": item.get("filepath"),
                "version": item.get("version"),
            }
            for item in info
        ],
    }


def thread_limit_context(max_threads: int = 6):
    if threadpool_limits is None:
        return nullcontext()
    return threadpool_limits(limits=int(max_threads))


def date_utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def add_months(dt: datetime, months: int) -> datetime:
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    return datetime(year, month, min(dt.day, 28), tzinfo=timezone.utc)


def bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "breached"}:
        return True
    if text in {"0", "false", "no", "n", "not_breached"}:
        return False
    return None


def canonical_side(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"put", "put_credit", "bull_put"}:
        return "put_credit"
    if text in {"call", "call_credit", "bear_call"}:
        return "call_credit"
    return None


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float | None:
    pairs = [(float(v), float(w)) for v, w in zip(values, weights) if math.isfinite(float(v)) and float(w) > 0]
    total = sum(w for _, w in pairs)
    if not pairs or total <= 0:
        return None
    return sum(v * w for v, w in pairs) / total


def date_weights(frame: pd.DataFrame) -> np.ndarray:
    if frame.empty:
        return np.asarray([], dtype=float)
    counts = frame.groupby("delivery_date")["delivery_date"].transform("size").astype(float)
    days = max(1, frame["delivery_date"].nunique())
    return (1.0 / days / counts).to_numpy(dtype=float)


def day_equal_mean(frame: pd.DataFrame, values: Sequence[float]) -> float | None:
    if frame.empty:
        return None
    series = pd.Series(np.asarray(values, dtype=float), index=frame.index)
    return float(series.groupby(frame["delivery_date"]).mean().mean())


def weighted_es95(values: Sequence[float], weights: Sequence[float]) -> float | None:
    pairs = sorted(
        [(float(v), float(w)) for v, w in zip(values, weights) if math.isfinite(float(v)) and float(w) > 0],
        key=lambda item: item[0],
    )
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    cutoff = 0.95 * total
    running = 0.0
    tail: list[tuple[float, float]] = []
    for value, weight in pairs:
        nxt = running + weight
        if nxt > cutoff:
            tail.append((value, nxt - cutoff if running < cutoff else weight))
        running = nxt
    denom = sum(w for _, w in tail)
    return sum(v * w for v, w in tail) / denom if denom > 0 else pairs[-1][0]


def weighted_brier(labels: Sequence[float], probs: Sequence[float], weights: Sequence[float]) -> float | None:
    return weighted_mean([(float(p) - float(y)) ** 2 for y, p in zip(labels, probs)], weights)


def load_protocol(protocol_path: str | Path, seal_path: str | Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol_path = Path(protocol_path)
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    seal: dict[str, Any] = {}
    if seal_path:
        seal = json.loads(Path(seal_path).read_text(encoding="utf-8"))
        actual = digest_file(protocol_path)
        expected = str(seal.get("protocol_sha256") or "").lower()
        if actual.lower() != expected:
            raise ValueError(f"protocol hash mismatch: {actual} != {expected}")
    return protocol, seal


def verify_model_input_hashes(research_root: str | Path, contract_path: str | Path) -> dict[str, Any]:
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    expected = contract.get("source_files_sha256") or {}
    actual: dict[str, str] = {}
    mismatches = []
    model_input = Path(research_root) / "step30" / "model_input"
    for name, expected_hash in expected.items():
        path = model_input / name
        if not path.exists():
            mismatches.append({"file": name, "reason": "missing"})
            continue
        actual_hash = digest_file(path)
        actual[name] = actual_hash
        if actual_hash.lower() != str(expected_hash).lower():
            mismatches.append({"file": name, "expected": expected_hash, "actual": actual_hash})
    if mismatches:
        raise ValueError(f"model input hash mismatch: {mismatches[:3]}")
    return {
        "schema": "astra_joint_v12_input_hash_verification@1.0.0",
        "contract_path": str(contract_path),
        "contract_sha256": digest_file(contract_path),
        "verified_files": actual,
        "rows_checked": contract.get("rows_checked"),
        "primary_width_rows": contract.get("primary_width_rows"),
        "training_permitted_on_frozen_rows": contract.get("training_permitted_on_frozen_rows"),
    }


def read_primary_model_rows(research_root: str | Path, years: Iterable[int] | None = None) -> pd.DataFrame:
    model_input = Path(research_root) / "step30" / "model_input"
    wanted_years = {str(year) for year in years} if years is not None else None
    frames = []
    for path in sorted(model_input.glob("model_rows-*.csv")):
        year = path.stem.rsplit("-", 1)[-1]
        if wanted_years is not None and year not in wanted_years:
            continue
        for chunk in pd.read_csv(path, usecols=lambda column: column in MODEL_INPUT_COLUMNS, chunksize=40000):
            chunk["target_width_num"] = to_numeric(chunk["target_width"])
            chunk = chunk[np.isclose(chunk["target_width_num"], 2000.0, atol=1e-9, rtol=0)].copy()
            if not chunk.empty:
                frames.append(chunk)
    if not frames:
        raise ValueError("no primary-width model rows loaded")
    frame = pd.concat(frames, ignore_index=True)
    frame["side"] = frame["side"].map(canonical_side)
    frame = frame[frame["side"].isin(["put_credit", "call_credit"])].copy()
    for column in [
        "as_of_ms",
        "entry_ms",
        "expiry_ms",
        "actual_width",
        "entry_price",
        "loss_normalized",
        "payout_btc",
        *TAIL_FEATURES,
    ]:
        if column in frame:
            frame[column] = to_numeric(frame[column])
    missing_side_sign = frame["side_sign"].isna()
    frame.loc[missing_side_sign & (frame["side"] == "put_credit"), "side_sign"] = -1.0
    frame.loc[missing_side_sign & (frame["side"] == "call_credit"), "side_sign"] = 1.0
    frame["actual_loss_normalized"] = frame["loss_normalized"].clip(lower=0)
    frame["delivery_date"] = frame["delivery_date"].astype(str).str[:10]
    frame["protection_breached_bool"] = frame.apply(
        lambda row: bool_or_none(row.get("protection_leg_breached"))
        if bool_or_none(row.get("protection_leg_breached")) is not None
        else bool_or_none(row.get("protection_breached")),
        axis=1,
    )
    if frame["row_id"].duplicated().any():
        raise ValueError("duplicate model row_id")
    return frame.reset_index(drop=True)


def read_v11_predictions(research_root: str | Path) -> dict[str, pd.DataFrame]:
    folder = Path(research_root) / "step30" / "models_annual_calibrated_20260921" / "rolling_candidate_predictions"
    frames: dict[str, pd.DataFrame] = {}
    for key, filename in V11_CONTROL_FILES.items():
        path = folder / filename
        if not path.exists():
            raise FileNotFoundError(path)
        frame = pd.read_csv(path)
        if frame["row_id"].duplicated().any():
            raise ValueError(f"duplicate prediction row_id in {path}")
        for column in [
            "as_of_ms",
            "entry_ms",
            "expiry_ms",
            "target_width",
            "actual_width",
            "expected_loss_normalized",
            "probability_positive",
            "breach_probability",
            "actual_loss_normalized",
            "actual_payout_btc",
        ]:
            frame[column] = to_numeric(frame[column])
        frames[key] = frame.set_index("row_id").sort_index()
    base = frames["geometry"]
    for key, frame in frames.items():
        pd.testing.assert_index_equal(base.index, frame.index)
        for column in ["observation_id", "side", "delivery_date", "actual_width", "actual_loss_normalized"]:
            pd.testing.assert_series_equal(base[column], frame[column], check_names=False)
    return frames


def attach_v11_predictions(rows: pd.DataFrame, frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    out = rows.copy()
    out = out.set_index("row_id", drop=False)
    for key, frame in frames.items():
        out[f"{key}_expected_loss"] = frame["expected_loss_normalized"]
        out[f"{key}_probability_positive"] = frame["probability_positive"]
        out[f"{key}_tail_probability"] = frame["breach_probability"]
        out[f"{key}_fold"] = frame["fold"]
    return out.reset_index(drop=True)


def build_pairs(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    base_columns = [
        "row_id",
        "observation_id",
        "as_of_ms",
        "entry_ms",
        "expiry_ms",
        "delivery_date",
        "actual_width",
        "actual_loss_normalized",
        "protection_breached_bool",
        "dte_hours",
        "short_distance_fraction",
        "width_fraction",
        "ret_30",
        "ret_240",
        "ret_1440",
        "vol_30",
        "vol_240",
        "net_flow_30",
        "net_flow_240",
        "geometry_expected_loss",
        "statistical_expected_loss",
        "joint_expected_loss",
        "geometry_probability_positive",
        "statistical_probability_positive",
        "joint_probability_positive",
    ]
    available = [column for column in base_columns if column in rows.columns]
    put = rows[rows["side"] == "put_credit"][available].copy()
    call = rows[rows["side"] == "call_credit"][available].copy()
    joined = put.merge(call, on="observation_id", suffixes=("_put", "_call"), validate="one_to_one")
    same_expiry = joined["expiry_ms_put"].eq(joined["expiry_ms_call"])
    same_width = np.isclose(joined["actual_width_put"], joined["actual_width_call"], atol=1e-9, rtol=0)
    filtered = joined[same_expiry & same_width].copy().reset_index(drop=True)
    counts = {
        "all_pairs": int(len(joined)),
        "same_expiry_same_width_pairs": int(len(filtered)),
        "excluded_expiry_or_width_pairs": int(len(joined) - len(filtered)),
        "unpaired_rows": int(len(rows) - 2 * len(joined)),
    }
    if filtered.empty:
        return filtered, counts
    filtered["delivery_date"] = filtered["delivery_date_put"].astype(str).str[:10]
    filtered["as_of_ms"] = filtered["as_of_ms_put"]
    filtered["entry_ms"] = filtered["entry_ms_put"]
    filtered["expiry_ms"] = filtered["expiry_ms_put"]
    filtered["actual_width"] = filtered["actual_width_put"]
    filtered["actual_put"] = filtered["actual_loss_normalized_put"]
    filtered["actual_call"] = filtered["actual_loss_normalized_call"]
    filtered["actual_delta"] = filtered["actual_put"] - filtered["actual_call"]
    filtered["put_short_distance_fraction"] = filtered["short_distance_fraction_put"]
    filtered["call_short_distance_fraction"] = filtered["short_distance_fraction_call"]
    for column in ["width_fraction", "dte_hours", "ret_30", "ret_240", "ret_1440", "vol_30", "vol_240", "net_flow_30", "net_flow_240"]:
        filtered[column] = filtered[f"{column}_put"]
    for control in V11_CONTROL_FILES:
        put_col = f"{control}_expected_loss_put"
        call_col = f"{control}_expected_loss_call"
        if put_col in filtered and call_col in filtered:
            filtered[f"{control}_put"] = filtered[put_col]
            filtered[f"{control}_call"] = filtered[call_col]
            filtered[f"{control}_delta"] = filtered[put_col] - filtered[call_col]
    if "geometry_delta" in filtered and "statistical_delta" in filtered:
        filtered["S50_put"] = 0.5 * filtered["geometry_put"] + 0.5 * filtered["statistical_put"]
        filtered["S50_call"] = 0.5 * filtered["geometry_call"] + 0.5 * filtered["statistical_call"]
        filtered["S50_delta"] = filtered["S50_put"] - filtered["S50_call"]
        filtered["geometry_center"] = (filtered["geometry_put"] + filtered["geometry_call"]) / 2.0
    return filtered, counts


def rolling_splits(protocol: Mapping[str, Any]) -> list[SplitSpec]:
    folds = protocol["folds"]
    lookback = int(folds.get("lookback_months", 24))
    cal_months = int(folds.get("calibration_months", 3))
    out: list[SplitSpec] = []
    for year in folds.get("years", [2022, 2023, 2024, 2025]):
        eval_start = date_utc(f"{int(year)}-01-01")
        eval_end = date_utc(f"{int(year) + 1}-01-01")
        out.append(
            SplitSpec(
                name=f"{eval_start.date()}__{eval_end.date()}",
                fit_start=add_months(eval_start, -lookback),
                cal_start=add_months(eval_start, -cal_months),
                eval_start=eval_start,
                eval_end=eval_end,
            )
        )
    return out


def split_frame(frame: pd.DataFrame, split: SplitSpec) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    def select(start: datetime, end: datetime) -> tuple[pd.DataFrame, dict[str, int]]:
        left, right = ms(start), ms(end)
        observed = (frame["as_of_ms"] >= left) & (frame["as_of_ms"] < right)
        missing_expiry = frame["expiry_ms"].isna() & observed
        expiry_cross = observed & frame["expiry_ms"].notna() & (frame["expiry_ms"] >= right)
        selected = frame[observed & frame["expiry_ms"].notna() & (frame["expiry_ms"] < right)].copy()
        return selected, {"expiry_cross_rows": int(expiry_cross.sum()), "missing_expiry_rows": int(missing_expiry.sum())}

    fit, fit_purge = select(split.fit_start, split.cal_start)
    cal, cal_purge = select(split.cal_start, split.eval_start)
    ev, ev_purge = select(split.eval_start, split.eval_end)
    owners: dict[str, set[str]] = defaultdict(set)
    for name, part in {"fit": fit, "calibration": cal, "evaluation": ev}.items():
        for day in part["delivery_date"].astype(str).unique():
            owners[day].add(name)
    overlaps = {day for day, names in owners.items() if len(names) > 1}
    removed = {}
    parts = {}
    for name, part in {"fit": fit, "calibration": cal, "evaluation": ev}.items():
        kept = part[~part["delivery_date"].isin(overlaps)].copy()
        removed[name] = int(len(part) - len(kept))
        parts[name] = kept
    meta = {
        "fit": fit_purge,
        "calibration": cal_purge,
        "evaluation": ev_purge,
        "overlapping_delivery_dates": sorted(overlaps),
        "overlap_removed_rows": removed,
    }
    return parts, meta


def final_split_frame(frame: pd.DataFrame, final_end: str, protocol: Mapping[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    end = date_utc(final_end)
    end_exclusive = end.replace(hour=23, minute=59, second=59, microsecond=999000)
    month_start = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    lookback = int(protocol["folds"].get("lookback_months", 24))
    cal_months = int(protocol["folds"].get("calibration_months", 3))
    split = SplitSpec(
        name=f"final__{final_end}",
        fit_start=add_months(month_start, -lookback + 1),
        cal_start=add_months(month_start, -cal_months + 1),
        eval_start=month_start,
        eval_end=end_exclusive,
    )
    left_fit, cal_start_ms, end_ms = ms(split.fit_start), ms(split.cal_start), ms(end_exclusive)
    fit = frame[(frame["as_of_ms"] >= left_fit) & (frame["as_of_ms"] < cal_start_ms) & (frame["expiry_ms"] < cal_start_ms)].copy()
    cal = frame[(frame["as_of_ms"] >= cal_start_ms) & (frame["as_of_ms"] <= end_ms) & (frame["expiry_ms"] <= end_ms)].copy()
    owners: dict[str, set[str]] = defaultdict(set)
    for name, part in {"fit": fit, "calibration": cal}.items():
        for day in part["delivery_date"].astype(str).unique():
            owners[day].add(name)
    overlaps = {day for day, names in owners.items() if len(names) > 1}
    fit2 = fit[~fit["delivery_date"].isin(overlaps)].copy()
    cal2 = cal[~cal["delivery_date"].isin(overlaps)].copy()
    return fit2, cal2, {
        "fit_start": str(split.fit_start.date()),
        "calibration_start": str(split.cal_start.date()),
        "window_end": final_end,
        "fit_rows": int(len(fit2)),
        "calibration_rows": int(len(cal2)),
        "overlapping_delivery_dates": sorted(overlaps),
        "overlap_removed_rows": {"fit": int(len(fit) - len(fit2)), "calibration": int(len(cal) - len(cal2))},
    }


def fit_preprocessor(frame: pd.DataFrame, feature_names: Sequence[str], weights: Sequence[float]) -> dict[str, Any]:
    weight_array = np.asarray(weights, dtype=float)
    features = []
    for name in feature_names:
        values = to_numeric(frame[name]) if name in frame else pd.Series([math.nan] * len(frame))
        finite = values.notna().to_numpy()
        observed = values[finite].to_numpy(dtype=float)
        observed_weights = weight_array[finite]
        if observed.size and observed_weights.sum() > 0:
            mean = float(np.average(observed, weights=observed_weights))
        elif observed.size:
            mean = float(np.mean(observed))
        else:
            mean = 0.0
        imputed = values.fillna(mean).to_numpy(dtype=float)
        if weight_array.sum() > 0:
            variance = float(np.average((imputed - mean) ** 2, weights=weight_array))
        else:
            variance = float(np.mean((imputed - mean) ** 2)) if imputed.size else 0.0
        scale = math.sqrt(max(0.0, variance))
        if scale < 1e-12:
            scale = 1.0
        features.append(
            {
                "name": str(name),
                "mean": mean,
                "impute": mean,
                "scale": scale,
                "include_missing_indicator": True,
                "missing_rate": float((~finite).mean()) if len(finite) else None,
                "fit_weighting": "delivery_day_equal",
            }
        )
    return {"schema": "astra_joint_v12_standard_preprocessor@1.0.0", "features": features}


def matrix(frame: pd.DataFrame, preprocessor: Mapping[str, Any]) -> np.ndarray:
    records = frame.to_dict("records")
    return np.asarray([transform_row(row, preprocessor)[0] for row in records], dtype=float)


def calibrate_logit_intercept(labels: Sequence[float], probs: Sequence[float], weights: Sequence[float]) -> dict[str, Any]:
    triples = [
        (float(y), min(max(float(p), 1e-12), 1.0 - 1e-12), float(w))
        for y, p, w in zip(labels, probs, weights)
        if math.isfinite(float(y)) and math.isfinite(float(p)) and float(w) > 0
    ]
    if not triples:
        return {"status": "unavailable", "reason": "empty_calibration"}
    ys = [y for y, _, _ in triples]
    ps = [p for _, p, _ in triples]
    ws = [w for _, _, w in triples]
    observed = weighted_mean(ys, ws)
    predicted = weighted_mean(ps, ws)
    if observed is None or predicted is None or observed <= 0.0 or observed >= 1.0:
        return {"status": "unavailable", "reason": "one_class_or_empty_calibration", "observed_rate": observed, "predicted_rate": predicted}
    logits = [logit(p) for p in ps]

    def shifted(delta: float) -> float:
        return float(weighted_mean([sigmoid(value + delta) for value in logits], ws) or 0.0)

    lower, upper = -80.0, 80.0
    for _ in range(100):
        mid = (lower + upper) / 2.0
        if shifted(mid) < observed:
            lower = mid
        else:
            upper = mid
    delta = (lower + upper) / 2.0
    return {
        "status": "available",
        "kind": "logit_delta",
        "solver": "weighted_intercept_bisection",
        "logit_delta": delta,
        "observed_rate": observed,
        "predicted_rate": predicted,
        "calibrated_predicted_rate": shifted(delta),
        "first_order_residual": shifted(delta) - observed,
    }


def fit_pair_ridge(fit: pd.DataFrame, cal: pd.DataFrame, protocol: Mapping[str, Any]) -> dict[str, Any]:
    from sklearn.linear_model import Ridge

    support = support_summary(fit, cal)
    if fit.empty or fit["delivery_date"].nunique() < 10:
        return {"status": "unavailable", "reason": "insufficient_pair_fit_support", "support": support}
    w = date_weights(fit)
    prep = fit_preprocessor(fit, PAIR_FEATURES, w)
    X = matrix(fit, prep)
    y = fit["actual_delta"].to_numpy(dtype=float)
    ridge = Ridge(alpha=float(protocol["candidates"]["PAIR_RIDGE10"]["alpha"]), fit_intercept=True)
    ridge.fit(X, y, sample_weight=w * len(w) if len(w) else None)
    raw_cal = predict_linear_frame(cal, prep, ridge.intercept_, ridge.coef_) if not cal.empty else np.asarray([], dtype=float)
    cw = date_weights(cal)
    residual = weighted_mean((cal["actual_delta"].to_numpy(dtype=float) - raw_cal).tolist(), cw.tolist()) if len(raw_cal) else None
    model = {
        "status": "available",
        "kind": "pair_ridge_delta",
        "candidate_id": "PAIR_RIDGE10",
        "direction": "Put minus Call normalized payout difference",
        "alpha": float(protocol["candidates"]["PAIR_RIDGE10"]["alpha"]),
        "preprocessor": prep,
        "linear_model": {"kind": "ridge", "intercept": float(ridge.intercept_), "coefficients": [float(x) for x in ridge.coef_.reshape(-1).tolist()]},
        "calibrator": {
            "status": "available" if residual is not None else "unavailable",
            "kind": "day_weighted_residual_intercept",
            "residual_intercept": float(residual or 0.0),
            "calibration_rows": int(len(cal)),
            "calibration_delivery_days": int(cal["delivery_date"].nunique()) if not cal.empty else 0,
        },
        "support": support,
    }
    model["model_hash"] = digest_value({key: value for key, value in model.items() if key != "model_hash"})
    return model


def predict_linear_frame(frame: pd.DataFrame, preprocessor: Mapping[str, Any], intercept: float, coefficients: Sequence[float]) -> np.ndarray:
    X = matrix(frame, preprocessor)
    coef = np.asarray(coefficients, dtype=float)
    return X.dot(coef) + float(intercept)


def pair_predictions(frame: pd.DataFrame, model: Mapping[str, Any]) -> pd.DataFrame:
    if frame.empty or model.get("status") != "available":
        return pd.DataFrame()
    raw = predict_linear_frame(frame, model["preprocessor"], model["linear_model"]["intercept"], model["linear_model"]["coefficients"])
    delta = raw + float((model.get("calibrator") or {}).get("residual_intercept", 0.0))
    out = frame[["observation_id", "delivery_date", "as_of_ms", "expiry_ms", "row_id_put", "row_id_call", "actual_put", "actual_call", "actual_delta"]].copy()
    out["candidate_id"] = "PAIR_RIDGE10"
    out["predicted_put_minus_call_loss"] = delta
    out["raw_predicted_put_minus_call_loss"] = raw
    out["selected_side"] = np.where(delta <= 0.0, "put_credit", "call_credit")
    return out


def fit_nested_tail(fit: pd.DataFrame, cal: pd.DataFrame, protocol: Mapping[str, Any]) -> dict[str, Any]:
    from sklearn.linear_model import LogisticRegression

    support = support_summary(fit, cal)
    positive_fit = fit[(fit["actual_loss_normalized"] > 0) & fit["protection_breached_bool"].notna()].copy()
    positive_cal = cal[(cal["actual_loss_normalized"] > 0) & cal["protection_breached_bool"].notna()].copy()
    tail_days_fit = positive_fit.loc[positive_fit["protection_breached_bool"] == True, "delivery_date"].nunique()  # noqa: E712
    tail_days_cal = positive_cal.loc[positive_cal["protection_breached_bool"] == True, "delivery_date"].nunique()  # noqa: E712
    min_fit = int(protocol["candidates"]["NESTED_LOGIT_C01"].get("min_tail_days_fit", 10))
    min_cal = int(protocol["candidates"]["NESTED_LOGIT_C01"].get("min_tail_days_calibration", 10))
    labels_fit = positive_fit["protection_breached_bool"].map(lambda x: 1 if x is True else 0).to_numpy(dtype=int)
    if positive_fit.empty or tail_days_fit < min_fit or len(set(labels_fit.tolist())) < 2:
        return {
            "status": "unavailable",
            "reason": "insufficient_conditional_tail_fit_support",
            "support": {**support, "tail_days_fit": int(tail_days_fit), "tail_days_calibration": int(tail_days_cal)},
        }
    parent_fit_weights = pd.Series(date_weights(fit), index=fit.index)
    prep = fit_preprocessor(positive_fit, TAIL_FEATURES, parent_fit_weights.loc[positive_fit.index].to_numpy(dtype=float))
    X = matrix(positive_fit, prep)
    w = parent_fit_weights.loc[positive_fit.index].to_numpy(dtype=float)
    clf = LogisticRegression(C=float(protocol["candidates"]["NESTED_LOGIT_C01"]["C"]), solver="lbfgs", max_iter=1000)
    clf.fit(X, labels_fit, sample_weight=w * len(w) if len(w) else None)
    calibrator = {"status": "unavailable", "reason": "insufficient_conditional_tail_calibration_support"}
    if not positive_cal.empty and tail_days_cal >= min_cal:
        raw_cal = sigmoid_array(predict_linear_frame(positive_cal, prep, clf.intercept_[0], clf.coef_.reshape(-1)))
        parent_cal_weights = pd.Series(date_weights(cal), index=cal.index)
        labels_cal = positive_cal["protection_breached_bool"].map(lambda x: 1.0 if x is True else 0.0).to_numpy(dtype=float)
        calibrator = calibrate_logit_intercept(labels_cal.tolist(), raw_cal.tolist(), parent_cal_weights.loc[positive_cal.index].to_numpy(dtype=float).tolist())
    model = {
        "status": "available",
        "kind": "nested_conditional_tail_logistic",
        "candidate_id": "NESTED_LOGIT_C01",
        "C": float(protocol["candidates"]["NESTED_LOGIT_C01"]["C"]),
        "tail_probability_status": "research_only",
        "parent_probability_source": "sealed_statistical_probability_positive",
        "preprocessor": prep,
        "conditional_tail_model": {
            "kind": "logistic",
            "intercept": float(clf.intercept_[0]),
            "coefficients": [float(x) for x in clf.coef_.reshape(-1).tolist()],
        },
        "calibrator": calibrator,
        "support": {**support, "positive_fit_rows": int(len(positive_fit)), "positive_calibration_rows": int(len(positive_cal)), "tail_days_fit": int(tail_days_fit), "tail_days_calibration": int(tail_days_cal)},
    }
    model["model_hash"] = digest_value({key: value for key, value in model.items() if key != "model_hash"})
    return model


def sigmoid_array(values: np.ndarray) -> np.ndarray:
    return np.asarray([sigmoid(float(value)) for value in values], dtype=float)


def nested_tail_predictions(frame: pd.DataFrame, model: Mapping[str, Any]) -> pd.DataFrame:
    if frame.empty or model.get("status") != "available":
        return pd.DataFrame()
    raw = sigmoid_array(predict_linear_frame(frame, model["preprocessor"], model["conditional_tail_model"]["intercept"], model["conditional_tail_model"]["coefficients"]))
    calibrator = model.get("calibrator") or {}
    delta = float(calibrator.get("logit_delta", 0.0)) if calibrator.get("status") == "available" else 0.0
    conditional = sigmoid_array(np.asarray([logit(value) + delta for value in raw], dtype=float))
    parent_series = to_numeric(frame["statistical_probability_positive"])
    if parent_series.isna().any():
        missing_ids = frame.loc[parent_series.isna(), "row_id"].astype(str).head(5).tolist() if "row_id" in frame else []
        raise ValueError(f"missing_parent_probability_positive rows={missing_ids}")
    out_of_range = (parent_series < 0.0) | (parent_series > 1.0)
    if out_of_range.any():
        bad_ids = frame.loc[out_of_range, "row_id"].astype(str).head(5).tolist() if "row_id" in frame else []
        raise ValueError(f"parent_probability_positive_out_of_range rows={bad_ids}")
    parent = parent_series.to_numpy(dtype=float)
    tail = parent * conditional
    out = frame[["row_id", "observation_id", "delivery_date", "as_of_ms", "expiry_ms", "side", "actual_loss_normalized", "protection_breached_bool", "statistical_probability_positive"]].copy()
    out["candidate_id"] = "NESTED_LOGIT_C01"
    out["parent_probability_positive"] = parent
    out["raw_conditional_tail_given_positive"] = raw
    out["conditional_tail_given_positive"] = conditional
    out["tail_probability"] = tail
    out["breach_probability"] = tail
    out["tail_probability_status"] = "research_only"
    out["order_violation"] = tail > parent + 1e-12
    return out


def support_summary(fit: pd.DataFrame, cal: pd.DataFrame) -> dict[str, Any]:
    return {
        "fit_rows": int(len(fit)),
        "fit_delivery_days": int(fit["delivery_date"].nunique()) if not fit.empty else 0,
        "calibration_rows": int(len(cal)),
        "calibration_delivery_days": int(cal["delivery_date"].nunique()) if not cal.empty else 0,
    }


def side_metrics(frame: pd.DataFrame, put_pred: Sequence[float], call_pred: Sequence[float]) -> dict[str, Any]:
    if frame.empty:
        return {"status": "unavailable", "pairs": 0}
    put = np.asarray(put_pred, dtype=float)
    call = np.asarray(call_pred, dtype=float)
    delta = put - call
    actual_put = frame["actual_put"].to_numpy(dtype=float)
    actual_call = frame["actual_call"].to_numpy(dtype=float)
    actual_delta = actual_put - actual_call
    selected = np.where(delta <= 0.0, actual_put, actual_call)
    actual_tie = np.isclose(actual_delta, 0.0, atol=1e-12, rtol=0)
    weights = date_weights(frame)
    correct_mask = (delta < 0.0) == (actual_delta < 0.0)
    unequal = ~actual_tie
    date_equal_unequal = None
    if unequal.any():
        date_equal_unequal = float(pd.Series(correct_mask[unequal].astype(float), index=frame.index[unequal]).groupby(frame.loc[unequal, "delivery_date"]).mean().mean())
    return {
        "status": "available",
        "pairs": int(len(frame)),
        "delivery_days": int(frame["delivery_date"].nunique()),
        "selected_actual_loss": weighted_mean(selected.tolist(), weights.tolist()),
        "selected_positive_payout_rate": weighted_mean((selected > 0).astype(float).tolist(), weights.tolist()),
        "selected_es95": weighted_es95(selected.tolist(), weights.tolist()),
        "pair_delta_mse": weighted_mean(((delta - actual_delta) ** 2).tolist(), weights.tolist()),
        "paired_row_mse": weighted_mean((((put - actual_put) ** 2 + (call - actual_call) ** 2) / 2.0).tolist(), weights.tolist()),
        "put_choice_fraction": weighted_mean((delta <= 0.0).astype(float).tolist(), weights.tolist()),
        "correct_on_unequal_actual": weighted_mean(correct_mask[unequal].astype(float).tolist(), weights[unequal].tolist()) if unequal.any() else None,
        "correct_on_unequal_actual_date_equal_within_unequal_days": date_equal_unequal,
        "correct_on_unequal_actual_weighting": "conditional on unequal actual Put/Call outcomes; uses original delivery-day-equal pair weights and renormalizes after removing equal-outcome rows",
        "predicted_ties": int(np.isclose(delta, 0.0, atol=1e-12, rtol=0).sum()),
    }


def fixed_direction_metrics(frame: pd.DataFrame, side: str) -> dict[str, Any]:
    if frame.empty:
        return {"status": "unavailable", "pairs": 0}
    if side not in {"put_credit", "call_credit"}:
        raise ValueError(f"unsupported fixed side: {side}")
    actual_put = frame["actual_put"].to_numpy(dtype=float)
    actual_call = frame["actual_call"].to_numpy(dtype=float)
    actual_delta = actual_put - actual_call
    selected = actual_put if side == "put_credit" else actual_call
    predicted_put = side == "put_credit"
    actual_tie = np.isclose(actual_delta, 0.0, atol=1e-12, rtol=0)
    correct_mask = (actual_delta < 0.0) if predicted_put else (actual_delta > 0.0)
    unequal = ~actual_tie
    weights = date_weights(frame)
    date_equal_unequal = None
    if unequal.any():
        date_equal_unequal = float(pd.Series(correct_mask[unequal].astype(float), index=frame.index[unequal]).groupby(frame.loc[unequal, "delivery_date"]).mean().mean())
    return {
        "status": "available",
        "control_kind": "fixed_direction_not_prediction_model",
        "pairs": int(len(frame)),
        "delivery_days": int(frame["delivery_date"].nunique()),
        "selected_actual_loss": weighted_mean(selected.tolist(), weights.tolist()),
        "selected_positive_payout_rate": weighted_mean((selected > 0).astype(float).tolist(), weights.tolist()),
        "selected_es95": weighted_es95(selected.tolist(), weights.tolist()),
        "pair_delta_mse": None,
        "paired_row_mse": None,
        "model_error_reason": "fixed direction controls do not estimate Put-minus-Call delta or single-side expected loss",
        "put_choice_fraction": 1.0 if side == "put_credit" else 0.0,
        "correct_on_unequal_actual": weighted_mean(correct_mask[unequal].astype(float).tolist(), weights[unequal].tolist()) if unequal.any() else None,
        "correct_on_unequal_actual_date_equal_within_unequal_days": date_equal_unequal,
        "correct_on_unequal_actual_weighting": "conditional on unequal actual Put/Call outcomes; uses original delivery-day-equal pair weights and renormalizes after removing equal-outcome rows",
        "predicted_ties": 0,
    }


def side_metrics_delta(frame: pd.DataFrame, predicted_delta: Sequence[float]) -> dict[str, Any]:
    if frame.empty:
        return {"status": "unavailable", "pairs": 0}
    delta = np.asarray(predicted_delta, dtype=float)
    actual_put = frame["actual_put"].to_numpy(dtype=float)
    actual_call = frame["actual_call"].to_numpy(dtype=float)
    actual_delta = actual_put - actual_call
    selected = np.where(delta <= 0.0, actual_put, actual_call)
    actual_tie = np.isclose(actual_delta, 0.0, atol=1e-12, rtol=0)
    weights = date_weights(frame)
    correct_mask = (delta < 0.0) == (actual_delta < 0.0)
    unequal = ~actual_tie
    date_equal_unequal = None
    if unequal.any():
        date_equal_unequal = float(pd.Series(correct_mask[unequal].astype(float), index=frame.index[unequal]).groupby(frame.loc[unequal, "delivery_date"]).mean().mean())
    return {
        "status": "available",
        "pairs": int(len(frame)),
        "delivery_days": int(frame["delivery_date"].nunique()),
        "selected_actual_loss": weighted_mean(selected.tolist(), weights.tolist()),
        "selected_positive_payout_rate": weighted_mean((selected > 0).astype(float).tolist(), weights.tolist()),
        "selected_es95": weighted_es95(selected.tolist(), weights.tolist()),
        "pair_delta_mse": weighted_mean(((delta - actual_delta) ** 2).tolist(), weights.tolist()),
        "paired_row_mse": None,
        "paired_row_mse_reason": "PAIR_RIDGE10 predicts only Put-minus-Call delta, not single-side expected loss",
        "put_choice_fraction": weighted_mean((delta <= 0.0).astype(float).tolist(), weights.tolist()),
        "correct_on_unequal_actual": weighted_mean(correct_mask[unequal].astype(float).tolist(), weights[unequal].tolist()) if unequal.any() else None,
        "correct_on_unequal_actual_date_equal_within_unequal_days": date_equal_unequal,
        "correct_on_unequal_actual_weighting": "conditional on unequal actual Put/Call outcomes; uses original delivery-day-equal pair weights and renormalizes after removing equal-outcome rows",
        "predicted_ties": int(np.isclose(delta, 0.0, atol=1e-12, rtol=0).sum()),
    }


def equal_side_mix_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"status": "unavailable", "pairs": 0}
    pair_weights = date_weights(frame)
    put = frame["actual_put"].to_numpy(dtype=float)
    call = frame["actual_call"].to_numpy(dtype=float)
    random_values = np.concatenate([put, call])
    random_weights = np.concatenate([pair_weights / 2.0, pair_weights / 2.0])
    averaged = (put + call) / 2.0
    return {
        "status": "available",
        "control_kind": "equal_probability_random_single_side",
        "pairs": int(len(frame)),
        "draws": int(2 * len(frame)),
        "delivery_days": int(frame["delivery_date"].nunique()),
        "selected_actual_loss": weighted_mean(random_values.tolist(), random_weights.tolist()),
        "selected_positive_payout_rate": weighted_mean((random_values > 0).astype(float).tolist(), random_weights.tolist()),
        "selected_es95": weighted_es95(random_values.tolist(), random_weights.tolist()),
        "weighting": "each same-width pair contributes half its delivery-day-equal pair weight to Put and half to Call",
        "simultaneous_averaged_two_side_portfolio": {
            "status": "available",
            "description": "not a random single-side control; both sides are averaged inside each pair before tail aggregation",
            "selected_actual_loss": weighted_mean(averaged.tolist(), pair_weights.tolist()),
            "selected_es95": weighted_es95(averaged.tolist(), pair_weights.tolist()),
            "pairs": int(len(frame)),
        },
    }


def daily_side_difference(frame: pd.DataFrame, candidate_delta: Sequence[float], geometry_delta: Sequence[float]) -> pd.Series:
    actual_put = frame["actual_put"].to_numpy(dtype=float)
    actual_call = frame["actual_call"].to_numpy(dtype=float)
    cand = np.where(np.asarray(candidate_delta, dtype=float) <= 0.0, actual_put, actual_call)
    geom = np.where(np.asarray(geometry_delta, dtype=float) <= 0.0, actual_put, actual_call)
    return pd.Series(cand - geom, index=frame.index).groupby(frame["delivery_date"]).mean().sort_index()


def calendar_week_blocks(daily: pd.Series) -> list[dict[str, Any]]:
    if daily.empty:
        return []
    values = {str(index): float(value) for index, value in daily.items()}
    start = datetime.strptime(min(values), "%Y-%m-%d").date()
    end = datetime.strptime(max(values), "%Y-%m-%d").date()
    blocks = []
    cursor = start
    while cursor <= end:
        dates = [(cursor + pd.Timedelta(days=offset)).isoformat() for offset in range(7)]
        chunk = [values[day] for day in dates if day in values]
        if chunk:
            blocks.append(
                {
                    "start_delivery_date": dates[0],
                    "end_delivery_date": dates[-1],
                    "day_count": len(chunk),
                    "missing_day_count": 7 - len(chunk),
                    "mean_selected_loss_difference_vs_geometry": float(sum(chunk) / len(chunk)),
                }
            )
        cursor = cursor + pd.Timedelta(days=7)
    return blocks


def seven_day_block_bootstrap_ci(daily: pd.Series, repetitions: int = 2000, seed: int = 20260921) -> dict[str, Any]:
    if daily.empty:
        return {"repetitions": repetitions, "ci95": None, "mean": None}
    values = {str(index): float(value) for index, value in daily.items()}
    start = datetime.strptime(min(values), "%Y-%m-%d").date()
    end = datetime.strptime(max(values), "%Y-%m-%d").date()
    calendar = [(start + pd.Timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]
    rng = random.Random(seed)
    draws = []
    for _ in range(int(repetitions)):
        picked: list[str] = []
        while len(picked) < len(calendar):
            length = min(7, len(calendar))
            start_index = rng.randrange(len(calendar) - length + 1)
            picked.extend(calendar[start_index : start_index + length])
        sample = [values[day] for day in picked[: len(calendar)] if day in values]
        if sample:
            draws.append(float(sum(sample) / len(sample)))
    if not draws:
        return {"repetitions": repetitions, "ci95": None, "mean": float(np.mean(list(values.values())))}
    return {
        "repetitions": repetitions,
        "seed": seed,
        "mean": float(np.mean(list(values.values()))),
        "ci95": [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))],
    }


def evaluate_side_candidates(pair_eval: pd.DataFrame, pair_pred: pd.DataFrame, protocol: Mapping[str, Any], repetitions: int) -> tuple[dict[str, Any], pd.DataFrame]:
    frame = pair_eval.dropna(subset=["geometry_put", "geometry_call", "statistical_put", "statistical_call"]).copy()
    pred_map = pair_pred.set_index("observation_id") if not pair_pred.empty else pd.DataFrame()
    if not pred_map.empty:
        frame["PAIR_RIDGE10_delta"] = frame["observation_id"].map(pred_map["predicted_put_minus_call_loss"])
    controls = {
        "geometry": ("geometry_put", "geometry_call"),
        "old_statistical": ("statistical_put", "statistical_call"),
        "joint_mechanism": ("joint_put", "joint_call"),
        "S50": ("S50_put", "S50_call"),
    }
    metrics = {}
    for name, (put_col, call_col) in controls.items():
        if put_col in frame and call_col in frame:
            subset = frame.dropna(subset=[put_col, call_col])
            metrics[name] = side_metrics(subset, subset[put_col].to_numpy(dtype=float), subset[call_col].to_numpy(dtype=float))
    if "PAIR_RIDGE10_delta" in frame:
        subset = frame.dropna(subset=["PAIR_RIDGE10_delta"])
        metrics["PAIR_RIDGE10"] = side_metrics_delta(subset, subset["PAIR_RIDGE10_delta"].to_numpy(dtype=float))
    metrics["fixed_put"] = fixed_direction_metrics(frame, "put_credit")
    metrics["fixed_call"] = fixed_direction_metrics(frame, "call_credit")
    metrics["equal_single_side_mix"] = equal_side_mix_metrics(frame)
    metrics["evaluation_metadata"] = {
        "equal_single_side_mix": "randomized single-side control; Put and Call are separate half-weight draws per pair for tail metrics",
        "simultaneous_averaged_two_side_portfolio": "reported only inside equal_single_side_mix for continuity with the previous averaged-pair statistic",
        "fixed_direction_mse": "fixed_put/fixed_call are controls, not prediction models; model-error fields are null by design",
        "correct_on_unequal_actual": "conditional on unequal actual outcomes using original delivery-day-equal pair weights; date_equal_within_unequal_days is also reported",
    }
    candidate_assessments = {}
    daily_rows = []
    geom_delta = frame["geometry_delta"].to_numpy(dtype=float)
    geom = metrics.get("geometry") or {}
    for candidate in ["S50", "PAIR_RIDGE10"]:
        delta_col = f"{candidate}_delta"
        if delta_col not in frame:
            continue
        subset = frame.dropna(subset=[delta_col, "geometry_delta"])
        daily = daily_side_difference(subset, subset[delta_col].to_numpy(dtype=float), subset["geometry_delta"].to_numpy(dtype=float))
        bootstrap = seven_day_block_bootstrap_ci(daily, repetitions=repetitions, seed=int((protocol.get("evaluation") or {}).get("bootstrap_seed", 20260921) or 20260921))
        by_year = {}
        for year, part in subset.groupby(subset["delivery_date"].astype(str).str[:4], sort=True):
            if candidate == "PAIR_RIDGE10":
                item = side_metrics_delta(part, part[f"{candidate}_delta"].to_numpy(dtype=float))
            else:
                item = side_metrics(part, part[f"{candidate}_put"].to_numpy(dtype=float), part[f"{candidate}_call"].to_numpy(dtype=float))
            base = side_metrics(part, part["geometry_put"].to_numpy(dtype=float), part["geometry_call"].to_numpy(dtype=float))
            by_year[str(year)] = {
                "candidate_selected_actual_loss": item.get("selected_actual_loss"),
                "geometry_selected_actual_loss": base.get("selected_actual_loss"),
                "nonworse_mean": item.get("selected_actual_loss") is not None and base.get("selected_actual_loss") is not None and item["selected_actual_loss"] <= base["selected_actual_loss"],
            }
        nonworse_years = sum(1 for item in by_year.values() if item["nonworse_mean"])
        item = metrics.get(candidate) or {}
        passed = (
            item.get("selected_actual_loss") is not None
            and geom.get("selected_actual_loss") is not None
            and item["selected_actual_loss"] < geom["selected_actual_loss"]
            and item.get("pair_delta_mse") is not None
            and geom.get("pair_delta_mse") is not None
            and item["pair_delta_mse"] < geom["pair_delta_mse"]
            and bootstrap.get("ci95") is not None
            and bootstrap["ci95"][1] < 0.0
            and nonworse_years >= 3
            and item.get("selected_es95") is not None
            and geom.get("selected_es95") is not None
            and item["selected_es95"] <= geom["selected_es95"]
        )
        candidate_assessments[candidate] = {
            "qualification": "development_support_only_pass" if passed else "failed_protocol_retention",
            "bootstrap_selected_loss_difference_vs_geometry": bootstrap,
            "nonworse_years": nonworse_years,
            "yearly": by_year,
            "seven_day_blocks": calendar_week_blocks(daily),
        }
        for day, value in daily.items():
            daily_rows.append({"candidate_id": candidate, "delivery_date": str(day), "selected_loss_difference_vs_geometry": float(value)})
    return {"metrics": metrics, "candidate_assessments": candidate_assessments}, pd.DataFrame(daily_rows)


def calibration_bins(frame: pd.DataFrame, prob_col: str, label_col: str, bins: int = 10) -> list[dict[str, Any]]:
    data = frame[[prob_col, label_col, "delivery_date"]].dropna().copy()
    if data.empty:
        return []
    data["bin"] = pd.cut(data[prob_col], np.linspace(0, 1, bins + 1), include_lowest=True)
    rows = []
    for interval, part in data.groupby("bin", observed=True):
        weights = date_weights(part)
        rows.append(
            {
                "bin": str(interval),
                "rows": int(len(part)),
                "delivery_days": int(part["delivery_date"].nunique()),
                "predicted": weighted_mean(part[prob_col].to_numpy(dtype=float).tolist(), weights.tolist()),
                "actual": weighted_mean(part[label_col].to_numpy(dtype=float).tolist(), weights.tolist()),
            }
        )
    return rows


def calibration_abs_error(frame: pd.DataFrame, prob_col: str, label_col: str) -> float | None:
    bins = calibration_bins(frame, prob_col, label_col, bins=10)
    values, weights = [], []
    for item in bins:
        if item["predicted"] is not None and item["actual"] is not None:
            values.append(abs(float(item["predicted"]) - float(item["actual"])))
            weights.append(float(item["rows"]))
    return weighted_mean(values, weights)


def weighted_top_fraction_actual(frame: pd.DataFrame, prob_col: str, actual_col: str, fraction: float = 0.10) -> float | None:
    data = frame[[prob_col, actual_col, "delivery_date"]].dropna().copy()
    if data.empty:
        return None
    weights = date_weights(data)
    rows = sorted(zip(data[prob_col].to_numpy(dtype=float), data[actual_col].to_numpy(dtype=float), weights), reverse=True, key=lambda item: item[0])
    target = sum(w for _, _, w in rows) * fraction
    if target <= 0:
        return None
    total = 0.0
    used = 0.0
    for _, actual, weight in rows:
        take = min(weight, target - used)
        if take <= 0:
            break
        total += actual * take
        used += take
    return total / target if used > 0 else None


def tail_metrics(frame: pd.DataFrame, prob_col: str, positive_col: str, actual_col: str = "actual_loss_normalized") -> dict[str, Any]:
    data = frame.dropna(subset=[prob_col, positive_col, "protection_breached_bool"]).copy()
    if data.empty:
        return {"status": "unavailable", "rows": 0}
    data["tail_label"] = data["protection_breached_bool"].map(lambda x: 1.0 if x is True else 0.0)
    weights = date_weights(data)
    probs = data[prob_col].to_numpy(dtype=float)
    labels = data["tail_label"].to_numpy(dtype=float)
    positive = data[positive_col].to_numpy(dtype=float)
    return {
        "status": "available",
        "rows": int(len(data)),
        "delivery_days": int(data["delivery_date"].nunique()),
        "tail_days": int(data.loc[data["tail_label"] > 0, "delivery_date"].nunique()),
        "order_violations": int((probs > positive + 1e-12).sum()),
        "brier": weighted_brier(labels.tolist(), probs.tolist(), weights.tolist()),
        "calibration_abs_error": calibration_abs_error(data, prob_col, "tail_label"),
        "calibration_bins": calibration_bins(data, prob_col, "tail_label", bins=10),
        "top10pct_predicted_tail_realized_loss": weighted_top_fraction_actual(data, prob_col, actual_col, fraction=0.10),
    }


def evaluate_tail(row_eval: pd.DataFrame, nested_pred: pd.DataFrame, fold_support: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    base = row_eval.dropna(subset=["geometry_tail_probability", "statistical_tail_probability", "statistical_probability_positive"]).copy()
    if not nested_pred.empty:
        nested = nested_pred.set_index("row_id")
        base["NESTED_LOGIT_C01_tail_probability"] = base["row_id"].map(nested["tail_probability"])
        base["NESTED_LOGIT_C01_parent_probability_positive"] = base["row_id"].map(nested["parent_probability_positive"])
    metrics = {
        "geometry": tail_metrics(base, "geometry_tail_probability", "geometry_probability_positive"),
        "old_statistical": tail_metrics(base, "statistical_tail_probability", "statistical_probability_positive"),
    }
    if "NESTED_LOGIT_C01_tail_probability" in base:
        metrics["NESTED_LOGIT_C01"] = tail_metrics(base, "NESTED_LOGIT_C01_tail_probability", "NESTED_LOGIT_C01_parent_probability_positive")
    yearly = {}
    for year, part in base.groupby(base["delivery_date"].astype(str).str[:4], sort=True):
        yearly[str(year)] = {
            "geometry": tail_metrics(part, "geometry_tail_probability", "geometry_probability_positive"),
            "old_statistical": tail_metrics(part, "statistical_tail_probability", "statistical_probability_positive"),
            "NESTED_LOGIT_C01": tail_metrics(part, "NESTED_LOGIT_C01_tail_probability", "NESTED_LOGIT_C01_parent_probability_positive") if "NESTED_LOGIT_C01_tail_probability" in part else {"status": "unavailable"},
        }
    nested_metrics = metrics.get("NESTED_LOGIT_C01") or {}
    geom = metrics.get("geometry") or {}
    brier_year_ok = all(
        (item["NESTED_LOGIT_C01"].get("brier") is not None and item["geometry"].get("brier") is not None and item["NESTED_LOGIT_C01"]["brier"] <= item["geometry"]["brier"])
        for item in yearly.values()
    )
    support_ok = all(
        int((item.get("model") or {}).get("support", {}).get("tail_days_fit", 0)) >= 10
        and int((item.get("model") or {}).get("support", {}).get("tail_days_calibration", 0)) >= 10
        for item in fold_support
    )
    passed = (
        nested_metrics.get("order_violations") == 0
        and brier_year_ok
        and nested_metrics.get("calibration_abs_error") is not None
        and geom.get("calibration_abs_error") is not None
        and nested_metrics["calibration_abs_error"] <= geom["calibration_abs_error"]
        and support_ok
        and nested_metrics.get("top10pct_predicted_tail_realized_loss") is not None
        and geom.get("top10pct_predicted_tail_realized_loss") is not None
        and nested_metrics["top10pct_predicted_tail_realized_loss"] >= geom["top10pct_predicted_tail_realized_loss"]
    )
    return {
        "metrics": metrics,
        "yearly": yearly,
        "fold_support": list(fold_support),
        "candidate_assessment": {
            "qualification": "research_only_development_support_pass" if passed else "failed_protocol_tail_retention",
            "research_only": True,
            "support_ok": support_ok,
            "brier_nonworse_every_year": brier_year_ok,
        },
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_experiment(
    research_root: str | Path,
    output_dir: str | Path,
    protocol_path: str | Path,
    seal_path: str | Path | None = None,
    contract_path: str | Path | None = None,
    amendment_path: str | Path | None = None,
    bootstrap_repetitions: int = 2000,
    allow_existing_output: bool = False,
) -> dict[str, Any]:
    research_root = Path(research_root)
    output_dir = Path(output_dir)
    if output_dir.exists() and not allow_existing_output:
        raise FileExistsError(output_dir)
    output_dir.mkdir(parents=True, exist_ok=allow_existing_output)
    protocol, seal = load_protocol(protocol_path, seal_path)
    hash_verification = verify_model_input_hashes(research_root, contract_path) if contract_path else {"status": "not_requested"}
    amendment = json.loads(Path(amendment_path).read_text(encoding="utf-8")) if amendment_path and Path(amendment_path).exists() else None
    max_threads = 6
    thread_control: dict[str, Any] = {
        "schema": "astra_joint_v12_thread_control@1.0.0",
        "env_before_numpy_import_defaults": {
            name: os.environ.get(name)
            for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_MAX_THREADS")
        },
        "training_context_limit": max_threads,
        "threadpoolctl_available": threadpool_limits is not None,
        "snapshot_after_import": threadpool_snapshot(),
    }

    rows = read_primary_model_rows(research_root, years=[2020, 2021, 2022, 2023, 2024, 2025, 2026])
    frames = read_v11_predictions(research_root)
    rows = attach_v11_predictions(rows, frames)
    pairs, pair_coverage = build_pairs(rows)
    row_eval_all = rows.dropna(subset=["geometry_expected_loss", "statistical_expected_loss", "joint_expected_loss"]).copy()
    pair_eval_all = pairs.dropna(subset=["geometry_put", "geometry_call", "statistical_put", "statistical_call", "joint_put", "joint_call"]).copy()

    failure_records: list[dict[str, Any]] = []
    pair_prediction_rows: list[dict[str, Any]] = []
    row_prediction_rows: list[dict[str, Any]] = []
    nested_prediction_frames = []
    pair_prediction_frames = []
    pair_fold_models = []
    tail_fold_models = []

    for split in rolling_splits(protocol):
        print(json.dumps({"event": "v12_fold_start", "fold": split.name}, ensure_ascii=False), file=sys.stderr, flush=True)
        pair_parts, pair_purge = split_frame(pairs, split)
        row_parts, row_purge = split_frame(rows, split)
        with thread_limit_context(max_threads):
            pair_model = fit_pair_ridge(pair_parts["fit"], pair_parts["calibration"], protocol)
            tail_model = fit_nested_tail(row_parts["fit"], row_parts["calibration"], protocol)
            thread_control[f"snapshot_during_{split.name}"] = threadpool_snapshot()
        pair_fold_models.append({"fold": split.name, "model": pair_model, "purged_rows": pair_purge})
        tail_fold_models.append({"fold": split.name, "model": tail_model, "purged_rows": row_purge})
        if pair_model.get("status") != "available":
            failure_records.append({"candidate_id": "PAIR_RIDGE10", "fold": split.name, "reason": pair_model.get("reason"), "support": pair_model.get("support")})
        if tail_model.get("status") != "available":
            failure_records.append({"candidate_id": "NESTED_LOGIT_C01", "fold": split.name, "reason": tail_model.get("reason"), "support": tail_model.get("support")})

        ev_pairs = pair_parts["evaluation"].dropna(subset=["geometry_put", "geometry_call"]).copy()
        ev_rows = row_parts["evaluation"].dropna(subset=["statistical_probability_positive"]).copy()
        if pair_model.get("status") == "available":
            pp = pair_predictions(ev_pairs, pair_model)
            pp["fold"] = split.name
            pair_prediction_frames.append(pp)
            pair_prediction_rows.extend(pp.to_dict("records"))
            for _, item in pp.iterrows():
                pair_row = ev_pairs.loc[ev_pairs["observation_id"] == item["observation_id"]].iloc[0]
                for side in ("put", "call"):
                    row_prediction_rows.append(
                        {
                            "candidate_id": "PAIR_RIDGE10",
                            "fold": split.name,
                            "row_id": pair_row[f"row_id_{side}"],
                            "observation_id": item["observation_id"],
                            "delivery_date": item["delivery_date"],
                            "side": f"{side}_credit",
                            "expected_loss_normalized": None,
                            "expected_loss_status": "not_modeled_pair_delta_only",
                            "pair_predicted_put_minus_call_loss": item["predicted_put_minus_call_loss"],
                            "pair_selected_side": item["selected_side"],
                            "probability_positive": None,
                            "tail_probability": None,
                            "tail_probability_status": "not_modeled",
                            "actual_loss_normalized": pair_row[f"actual_{side}"],
                        }
                    )
        if tail_model.get("status") == "available":
            npred = nested_tail_predictions(ev_rows, tail_model)
            npred["fold"] = split.name
            nested_prediction_frames.append(npred)
            for _, item in npred.iterrows():
                row_prediction_rows.append(
                    {
                        "candidate_id": "NESTED_LOGIT_C01",
                        "fold": split.name,
                        "row_id": item["row_id"],
                        "observation_id": item["observation_id"],
                        "delivery_date": item["delivery_date"],
                        "side": item["side"],
                        "expected_loss_normalized": None,
                        "expected_loss_status": "not_modeled_tail_only",
                        "probability_positive": None,
                        "parent_probability_positive": item["parent_probability_positive"],
                        "tail_probability": item["tail_probability"],
                        "breach_probability": item["tail_probability"],
                        "tail_probability_status": "research_only",
                        "actual_loss_normalized": item["actual_loss_normalized"],
                        "protection_breached": item["protection_breached_bool"],
                    }
                )
        print(
            json.dumps(
                {
                    "event": "v12_fold_complete",
                    "fold": split.name,
                    "PAIR_RIDGE10": pair_model.get("status"),
                    "NESTED_LOGIT_C01": tail_model.get("status"),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )

    pair_pred_all = pd.concat(pair_prediction_frames, ignore_index=True) if pair_prediction_frames else pd.DataFrame()
    nested_pred_all = pd.concat(nested_prediction_frames, ignore_index=True) if nested_prediction_frames else pd.DataFrame()

    side_report, side_daily = evaluate_side_candidates(pair_eval_all, pair_pred_all, protocol, bootstrap_repetitions)
    tail_report = evaluate_tail(row_eval_all, nested_pred_all, tail_fold_models)

    for _, item in row_eval_all.iterrows():
        row_prediction_rows.append(
            {
                "candidate_id": "S50",
                "fold": item.get("geometry_fold"),
                "row_id": item["row_id"],
                "observation_id": item["observation_id"],
                "delivery_date": item["delivery_date"],
                "side": item["side"],
                "expected_loss_normalized": 0.5 * float(item["geometry_expected_loss"]) + 0.5 * float(item["statistical_expected_loss"]),
                "expected_loss_status": "fixed_half_blend_ranking_research_only",
                "probability_positive": None,
                "tail_probability": None,
                "tail_probability_status": "not_modeled",
                "actual_loss_normalized": item["actual_loss_normalized"],
            }
        )

    final_pair_fit, final_pair_cal, final_pair_meta = final_split_frame(pairs, str(protocol.get("final_fit_cutoff", "2026-08-31")), protocol)
    final_tail_fit, final_tail_cal, final_tail_meta = final_split_frame(rows, str(protocol.get("final_fit_cutoff", "2026-08-31")), protocol)
    with thread_limit_context(max_threads):
        final_pair_model = fit_pair_ridge(final_pair_fit, final_pair_cal, protocol)
        final_tail_model = fit_nested_tail(final_tail_fit, final_tail_cal, protocol)
        thread_control["snapshot_during_final_fit"] = threadpool_snapshot()
    if final_pair_model.get("status") != "available":
        failure_records.append({"candidate_id": "PAIR_RIDGE10", "fold": "final_fit", "reason": final_pair_model.get("reason"), "support": final_pair_model.get("support")})
    if final_tail_model.get("status") != "available":
        failure_records.append({"candidate_id": "NESTED_LOGIT_C01", "fold": "final_fit", "reason": final_tail_model.get("reason"), "support": final_tail_model.get("support")})

    artifact = {
        "schema": ARTIFACT_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": digest_file(protocol_path),
        "protocol_seal": seal,
        "data_contract_recheck": hash_verification,
        "data_contract_amendment": amendment,
        "thread_control": thread_control,
        "scope": protocol.get("scope"),
        "production": protocol.get("production"),
        "research_only": True,
        "selected_candidate_id": None,
        "selection_status": "no_selected_candidate",
        "selection_reason": "all v1.2 challengers are research-only and failed retention criteria; callers must pass candidate_id explicitly",
        "models": {
            "S50": {"status": "available", "kind": "fixed_half_blend", "candidate_id": "S50", "alpha": 0.5, "refit": False},
            "PAIR_RIDGE10": final_pair_model,
            "NESTED_LOGIT_C01": final_tail_model,
        },
        "final_windows": {"PAIR_RIDGE10": final_pair_meta, "NESTED_LOGIT_C01": final_tail_meta},
        "no_auto_upgrade": True,
    }
    artifact["artifact_hash_without_self"] = digest_value(artifact)

    paths = {
        "row_predictions_csv": output_dir / "v12_row_predictions.csv",
        "pair_predictions_csv": output_dir / "v12_pair_predictions.csv",
        "daily_side_differences_csv": output_dir / "v12_daily_side_differences.csv",
        "model_artifact_json": output_dir / "v12_model_artifact.json",
        "summary_json": output_dir / "v12_summary.json",
        "failure_records_jsonl": output_dir / "v12_failure_records.jsonl",
    }
    write_csv(paths["row_predictions_csv"], row_prediction_rows)
    write_csv(paths["pair_predictions_csv"], pair_prediction_rows)
    write_csv(paths["daily_side_differences_csv"], side_daily.to_dict("records") if not side_daily.empty else [])
    paths["model_artifact_json"].write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    with paths["failure_records_jsonl"].open("w", encoding="utf-8") as handle:
        for item in failure_records:
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "schema": SCHEMA,
        "created_at_utc": artifact["created_at_utc"],
        "protocol_sha256": digest_file(protocol_path),
        "protocol_stop_conditions": protocol.get("stop_conditions"),
        "data_contract_recheck": hash_verification,
        "data_contract_amendment_sha256": digest_file(amendment_path) if amendment_path and Path(amendment_path).exists() else None,
        "thread_control": thread_control,
        "source_prediction_hashes": {
            key: digest_file(Path(research_root) / "step30" / "models_annual_calibrated_20260921" / "rolling_candidate_predictions" / filename)
            for key, filename in V11_CONTROL_FILES.items()
        },
        "scope": "2022-2025 previously studied development folds; no independent unseen test and no economic edge claim",
        "row_count": int(len(rows)),
        "evaluation_row_count": int(len(row_eval_all)),
        "pair_coverage": pair_coverage,
        "side": side_report,
        "tail": tail_report,
        "failure_record_count": len(failure_records),
        "final_model_status": {
            "PAIR_RIDGE10": final_pair_model.get("status"),
            "NESTED_LOGIT_C01": final_tail_model.get("status"),
            "S50": "available_no_refit",
        },
        "output_paths": {key: str(path) for key, path in paths.items()},
        "output_hashes": {},
        "summary_json_hash_policy": "excluded_from_embedded_output_hashes_to_avoid_self_referential_hash_drift; compute externally when needed",
        "qualification": "research_only_no_replacement",
        "limitations": [
            "historical credit absent, so net win rate, mean net result, and economic edge are unknown",
            "all evaluation years were already studied; intervals are descriptive development checks",
            "NESTED_LOGIT_C01 tail output remains research_only even if mathematical order holds",
            "S50 blended expected loss does not invent blended occurrence or tail probabilities",
            "PAIR_RIDGE10 exports only Put-minus-Call pair delta and selected side, not single-side expected loss",
        ],
    }
    paths["summary_json"].write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    summary["output_hashes"] = {key: digest_file(path) for key, path in paths.items() if key != "summary_json"}
    paths["summary_json"].write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sealed Astra joint v1.2 finite experiment")
    parser.add_argument("--research-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--seal")
    parser.add_argument("--data-contract")
    parser.add_argument("--data-amendment")
    parser.add_argument("--bootstrap-repetitions", type=int, default=2000)
    parser.add_argument("--allow-existing-output", action="store_true")
    args = parser.parse_args(argv)
    result = run_experiment(
        args.research_root,
        args.output_dir,
        args.protocol,
        args.seal,
        args.data_contract,
        args.data_amendment,
        bootstrap_repetitions=args.bootstrap_repetitions,
        allow_existing_output=args.allow_existing_output,
    )
    print(json.dumps({"summary": result["output_paths"]["summary_json"], "qualification": result["qualification"], "final_model_status": result["final_model_status"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
