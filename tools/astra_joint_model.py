"""Model training utilities for Astra joint research v1.

This module is intentionally research-side only.  It trains exportable
two-part GAM artifacts and can record an optional CatBoost challenger when the
dependency is available, but production/FMZ behavior is untouched.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
import tempfile
from collections import defaultdict
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from astra_joint_contract import FEATURE_GROUPS, PROTOCOL, SEED
import astra_joint_inference as inference


EPSILON = 1e-12
MODEL_SCHEMA = inference.MODEL_SCHEMA
MODEL_SELECTION_SCHEMA = "astra_joint_model_selection@1.0.0"
PRIMARY_WIDTH = float(PROTOCOL["primary_width"])
TRAIN_SPLITS = {"train", "training"}
SELECTION_SPLITS = {"selection", "select", "validation"}
LOCKED_TEST_SPLITS = {"locked_test", "sealed_test", "test_2023"}
DEVELOPMENT_SPLITS = {"development", "dev", "research"}
DEFAULT_HOLDOUT_PURGE_SPLITS = {"selection", "locked_test"}


class MissingModelDependency(RuntimeError):
    """Raised when the local training environment lacks model dependencies."""


def finite_float(value: object) -> float | None:
    return inference.finite_float(value)


def stable_hash(payload: object) -> str:
    return inference.stable_hash(payload)


def read_rows(path: Path) -> list[dict[str, Any]]:
    return inference.read_rows(path)


def write_json(path: Path, payload: object) -> None:
    inference.write_json(path, payload)


def write_csv(path: Path, rows: Iterable[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    inference.write_csv(path, rows, fieldnames)


def canonical_side(value: object) -> str | None:
    return inference.normalize_side(value)


def side_sign(row: dict[str, Any]) -> float | None:
    return inference.side_sign_from_row(row)


def configure_training_threads() -> None:
    limit_int = int(PROTOCOL["max_training_threads"])
    limit = str(limit_int)
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        current = finite_float(os.environ.get(name))
        if current is None or int(current) > limit_int:
            os.environ[name] = limit


def training_thread_context() -> Any:
    configure_training_threads()
    try:
        from threadpoolctl import threadpool_limits  # type: ignore
    except Exception:
        return nullcontext()
    return threadpool_limits(limits=int(PROTOCOL["max_training_threads"]))


def split_for_row(row: dict[str, Any]) -> str:
    raw = str(row.get("split") or row.get("dataset_split") or "").strip().lower()
    if raw in TRAIN_SPLITS:
        return "train"
    if raw in SELECTION_SPLITS:
        return "selection"
    if raw in LOCKED_TEST_SPLITS:
        return "locked_test"
    if raw in DEVELOPMENT_SPLITS:
        return "development"
    year = _delivery_year(row)
    if year in PROTOCOL["train_years"]:
        return "train"
    if year in PROTOCOL["selection_years"]:
        return "selection"
    if year in PROTOCOL["test_years"]:
        return "locked_test"
    if year in PROTOCOL["previously_used_years"]:
        return "development"
    return "unknown"


def feature_names(group_name: str) -> tuple[str, ...]:
    if group_name not in FEATURE_GROUPS:
        raise ValueError(f"unknown feature group: {group_name}")
    return tuple(FEATURE_GROUPS[group_name])


def target_loss(row: dict[str, Any]) -> float | None:
    direct = finite_float(row.get("loss_normalized"))
    if direct is not None:
        return direct
    payout = finite_float(row.get("payout_btc"))
    width = finite_float(row.get("actual_width"))
    entry_price = finite_float(row.get("entry_price"))
    if payout is None or width is None or entry_price is None or width <= 0 or entry_price <= 0:
        return None
    return payout / (width / entry_price)


def usable_model_row(
    row: dict[str, Any],
    *,
    split_names: set[str] | None = None,
    primary_width_only: bool = True,
) -> bool:
    if split_names is not None and split_for_row(row) not in split_names:
        return False
    if primary_width_only:
        width = finite_float(row.get("target_width"))
        if width is None or abs(width - PRIMARY_WIDTH) > EPSILON:
            return False
    if canonical_side(row.get("side")) is None or side_sign(row) is None:
        return False
    if target_loss(row) is None:
        return False
    if not str(row.get("delivery_date") or "").strip():
        return False
    return True


def filter_rows(
    rows: Iterable[dict[str, Any]],
    *,
    split_names: set[str] | None = None,
    primary_width_only: bool = True,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if usable_model_row(row, split_names=split_names, primary_width_only=primary_width_only)
    ]


def filter_boundary_rows(
    rows: Iterable[dict[str, Any]],
    *,
    split_names: set[str] | None,
    primary_width_only: bool = True,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if split_names is not None and split_for_row(row) not in split_names:
            continue
        if primary_width_only:
            width = finite_float(row.get("target_width"))
            if width is not None and abs(width - PRIMARY_WIDTH) > EPSILON:
                continue
        if boundary_keys(row):
            result.append(dict(row))
    return result


def boundary_keys(row: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    episode = str(row.get("episode_id") or "").strip()
    if episode:
        keys.add(("episode", episode))
    expiry = finite_float(row.get("expiry_ms"))
    if expiry is not None:
        keys.add(("expiry_ms", str(int(expiry))))
    else:
        delivery = str(row.get("delivery_date") or "").strip()
        if delivery:
            keys.add(("delivery_date", delivery))
    return keys


def purge_boundary_overlap(
    source_rows: Sequence[dict[str, Any]],
    boundary_rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    boundary = set()
    for row in boundary_rows:
        boundary.update(boundary_keys(row))
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = defaultdict(int)
    for row in source_rows:
        overlap = boundary_keys(row) & boundary
        if overlap:
            removed.append(dict(row))
            for kind, _ in overlap:
                reason_counts[kind] += 1
        else:
            kept.append(dict(row))
    return kept, {
        "source_rows": len(source_rows),
        "boundary_rows": len(boundary_rows),
        "kept_rows": len(kept),
        "removed_rows": len(removed),
        "removed_by_key_type": dict(sorted(reason_counts.items())),
        "boundary_key_count": len(boundary),
    }


def training_rows_for_selection(
    rows: Sequence[dict[str, Any]],
    *,
    holdout_splits: set[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    train_rows = filter_rows(rows, split_names={"train"}, primary_width_only=True)
    if not holdout_splits:
        return train_rows, {
            "holdout_splits": [],
            "source_rows": len(train_rows),
            "boundary_rows": 0,
            "kept_rows": len(train_rows),
            "removed_rows": 0,
            "removed_by_key_type": {},
            "boundary_key_count": 0,
        }
    holdout_rows = filter_boundary_rows(rows, split_names=holdout_splits, primary_width_only=True)
    kept, report = purge_boundary_overlap(train_rows, holdout_rows)
    report["holdout_splits"] = sorted(holdout_splits)
    return kept, report


def training_qualification(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    delivery_dates = {str(row["delivery_date"]) for row in rows}
    positive_dates = {
        str(row["delivery_date"])
        for row in rows
        if (target_loss(row) or 0.0) > 0.0
    }
    positive_rows = sum(1 for row in rows if (target_loss(row) or 0.0) > 0.0)
    negative_rows = len(rows) - positive_rows
    min_days = int(PROTOCOL["min_training_delivery_days"])
    min_positive_days = int(PROTOCOL["min_positive_delivery_days"])
    reasons: list[str] = []
    if len(delivery_dates) < min_days:
        reasons.append("insufficient_training_delivery_days")
    if len(positive_dates) < min_positive_days:
        reasons.append("insufficient_positive_delivery_days")
    if positive_rows == 0 or negative_rows == 0:
        reasons.append("single_class_training_target")
    return {
        "status": "available" if not reasons else "insufficient",
        "rows": len(rows),
        "delivery_dates": len(delivery_dates),
        "positive_delivery_dates": len(positive_dates),
        "positive_rows": positive_rows,
        "negative_rows": negative_rows,
        "reasons": reasons,
        "minimum_delivery_dates": min_days,
        "minimum_positive_delivery_dates": min_positive_days,
    }


def equal_delivery_day_weights(rows: Sequence[dict[str, Any]]) -> list[float]:
    by_day: dict[str, int] = defaultdict(int)
    for row in rows:
        by_day[str(row["delivery_date"])] += 1
    if not rows or not by_day:
        return []
    day_weight = len(rows) / len(by_day)
    return [day_weight / by_day[str(row["delivery_date"])] for row in rows]


def make_matrix(rows: Sequence[dict[str, Any]], names: Sequence[str]) -> tuple[list[list[float | None]], list[float], list[float]]:
    features: list[list[float | None]] = []
    targets: list[float] = []
    signs: list[float] = []
    for row in rows:
        line: list[float | None] = []
        for name in names:
            value = side_sign(row) if name == "side_sign" else finite_float(row.get(name))
            line.append(value)
        target = target_loss(row)
        sign = side_sign(row)
        if target is None or sign is None:
            raise ValueError("model row contains invalid target or side")
        features.append(line)
        targets.append(float(target))
        signs.append(float(sign))
    return features, targets, signs


def feature_support_from_rows(rows: Sequence[dict[str, Any]], names: Sequence[str]) -> dict[str, dict[str, float | None]]:
    support: dict[str, dict[str, float | None]] = {}
    for name in names:
        values = [
            side_sign(row) if name == "side_sign" else finite_float(row.get(name))
            for row in rows
        ]
        finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
        support[str(name)] = {
            "min": min(finite) if finite else None,
            "max": max(finite) if finite else None,
            "missing_rate": (len(values) - len(finite)) / len(values) if values else None,
        }
    return support


def fit_two_part_gam(
    rows: Sequence[dict[str, Any]],
    *,
    feature_group: str,
    logistic_c: float,
    gamma_alpha: float,
    model_version: str = "astra_joint_gam@1.0.0",
    purge_against_splits: set[str] | None = None,
    keep_estimators: bool = False,
) -> dict[str, Any]:
    """Fit a two-part GAM and return an exportable artifact plus diagnostics."""

    names = feature_names(feature_group)
    holdout_splits = DEFAULT_HOLDOUT_PURGE_SPLITS if purge_against_splits is None else set(purge_against_splits)
    train_rows, purge_report = training_rows_for_selection(rows, holdout_splits=holdout_splits)
    qualification = training_qualification(train_rows)
    if qualification["status"] != "available":
        return _insufficient_artifact(feature_group, model_version, qualification, purge_report)

    try:
        np, LogisticRegression, GammaRegressor, SplineTransformer = _require_sklearn()
    except MissingModelDependency as exc:
        return _dependency_unavailable_result(feature_group, model_version, qualification, purge_report, exc)
    with training_thread_context():
        raw_matrix, targets, _ = make_matrix(train_rows, names)
        weights = equal_delivery_day_weights(train_rows)
        preprocessor = _fit_preprocessor(np, SplineTransformer, raw_matrix, names)
        x_design = _transform_with_preprocessor(np, preprocessor, raw_matrix)
        y = np.asarray(targets, dtype=float)
        y_positive = (y > 0.0).astype(int)
        sample_weight = np.asarray(weights, dtype=float)

        logistic = LogisticRegression(
            C=float(logistic_c),
            solver="lbfgs",
            max_iter=2000,
            random_state=SEED,
        )
        logistic.fit(x_design, y_positive, sample_weight=sample_weight)

        positive_mask = y > 0.0
        gamma = GammaRegressor(alpha=float(gamma_alpha), max_iter=2000)
        gamma.fit(x_design[positive_mask], y[positive_mask], sample_weight=sample_weight[positive_mask])

    artifact = export_two_part_gam(
        preprocessor=preprocessor,
        logistic=logistic,
        gamma=gamma,
        feature_group=feature_group,
        model_version=model_version,
        logistic_c=float(logistic_c),
        gamma_alpha=float(gamma_alpha),
        training_rows=train_rows,
        qualification=qualification,
        purge_report=purge_report,
    )
    result: dict[str, Any] = {
        "status": "available",
        "model_kind": "two_part_gam",
        "feature_group": feature_group,
        "logistic_c": float(logistic_c),
        "gamma_alpha": float(gamma_alpha),
        "qualification": qualification,
        "purge_report": purge_report,
        "artifact": artifact,
        "artifact_hash": artifact.get("artifact_hash"),
    }
    if keep_estimators:
        result["_runtime"] = {
            "preprocessor": preprocessor,
            "logistic": logistic,
            "gamma": gamma,
            "feature_names": names,
        }
    return result


def export_two_part_gam(
    *,
    preprocessor: dict[str, Any],
    logistic: Any,
    gamma: Any,
    feature_group: str,
    model_version: str,
    logistic_c: float,
    gamma_alpha: float,
    training_rows: Sequence[dict[str, Any]],
    qualification: dict[str, Any] | None = None,
    purge_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    np = _require_numpy()
    numeric_specs = []
    design_columns: list[str] = []
    for name, trained_feature in zip(preprocessor["feature_names"], preprocessor["trained_features"]):
        if trained_feature["kind"] == "constant":
            spline_payload = {
                "type": "constant",
                "basis_count": 1,
                "actual_n_knots": 0,
            }
            output_count = 1
        else:
            spline = trained_feature["transformer"].bsplines_[0]
            coef_matrix = np.asarray(spline.c, dtype=float)
            if coef_matrix.ndim == 1:
                coef_matrix = np.eye(coef_matrix.shape[0])
            basis_count = int(coef_matrix.shape[0])
            output_count = int(coef_matrix.shape[1])
            spline_payload = {
                "type": "bspline",
                "degree": int(spline.k),
                "knots": [float(x) for x in spline.t],
                "coefficient_matrix": coef_matrix.tolist(),
                "basis_count": basis_count,
                "actual_n_knots": int(trained_feature["actual_n_knots"]),
                "extrapolation": "constant",
            }
        numeric_specs.append(
            {
                "name": name,
                "median": float(trained_feature["median"]),
                "mean": float(trained_feature["mean"]),
                "scale": float(trained_feature["scale"]),
                "training_min": trained_feature.get("training_min"),
                "training_max": trained_feature.get("training_max"),
                "training_missing_rate": trained_feature.get("training_missing_rate"),
                "spline": spline_payload,
            }
        )
        design_columns.extend(f"{name}__spline_{i}" for i in range(output_count))
        design_columns.append(f"{name}__missing")

    artifact = {
        "schema": MODEL_SCHEMA,
        "status": "available",
        "model_kind": "two_part_gam",
        "model_version": model_version,
        "feature_group": feature_group,
        "training_cutoff": _max_delivery_date(training_rows),
        "protocol": {
            "source_schema": PROTOCOL["schema"],
            "seed": PROTOCOL["seed"],
            "primary_width": PROTOCOL["primary_width"],
            "train_years": PROTOCOL["train_years"],
            "selection_years": PROTOCOL["selection_years"],
            "test_years": PROTOCOL["test_years"],
            "previously_used_years": PROTOCOL["previously_used_years"],
        },
        "scope": {
            "target": "normalized inverse-BTC spread payout; may exceed one and is not clipped",
            "probability_positive": "model probability that expiry payout is greater than zero; not calibrated as trade win rate",
            "conditional_positive_loss": "expected normalized payout conditional on payout being positive",
            "conditional_loss_weighting": "positive-loss model uses full-sample delivery-day weights restricted to positive rows, so positive frequency remains in the logistic component",
            "target_width": PROTOCOL["primary_width"],
            "side_sign": "put_credit=-1, call_credit=+1",
            "training_feature_support": {
                str(item["name"]): {
                    "min": item.get("training_min"),
                    "max": item.get("training_max"),
                    "missing_rate": item.get("training_missing_rate"),
                }
                for item in numeric_specs
            },
        },
        "hyperparameters": {
            "logistic_c": logistic_c,
            "gamma_alpha": gamma_alpha,
            "spline_degree": PROTOCOL["spline_degree"],
            "spline_knots": PROTOCOL["spline_knots"],
        },
        "qualification": qualification or training_qualification(training_rows),
        "purge_report": purge_report or {},
        "preprocess": {
            "version": "numeric_spline_with_missing_indicator@1.0.0",
            "numeric_features": numeric_specs,
        },
        "design_columns": design_columns,
        "design_size": len(design_columns),
        "logistic_model": {
            "link": "logit",
            "intercept": float(logistic.intercept_[0]),
            "coef": [float(x) for x in logistic.coef_[0]],
        },
        "gamma_model": {
            "link": "log",
            "intercept": float(gamma.intercept_),
            "coef": [float(x) for x in gamma.coef_],
        },
    }
    artifact["artifact_hash"] = stable_hash({key: value for key, value in artifact.items() if key != "artifact_hash"})
    return artifact


def predict_with_runtime(fit_result: dict[str, Any], rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Predict with kept sklearn estimators.  Used only for export parity tests."""

    runtime = fit_result.get("_runtime")
    if not isinstance(runtime, dict):
        raise ValueError("fit result was not created with keep_estimators=True")
    np = _require_numpy()
    names = runtime["feature_names"]
    raw_matrix, _, _ = make_matrix(rows, names)
    design = _transform_with_preprocessor(np, runtime["preprocessor"], raw_matrix)
    probabilities = runtime["logistic"].predict_proba(design)[:, 1]
    conditional = runtime["gamma"].predict(design)
    predictions = []
    for row, probability, positive_loss in zip(rows, probabilities, conditional):
        predictions.append(
            {
                "row_id": row.get("row_id"),
                "side": canonical_side(row.get("side")),
                "probability_positive": float(probability),
                "conditional_positive_loss": float(positive_loss),
                "expected_loss_normalized": float(probability * positive_loss),
            }
        )
    return predictions


def evaluate_artifact(
    artifact: dict[str, Any],
    rows: Sequence[dict[str, Any]],
    *,
    split_names: set[str] | None = None,
) -> dict[str, Any]:
    eval_rows = filter_rows(rows, split_names=split_names, primary_width_only=True)
    if artifact.get("status") != "available" or not eval_rows:
        return {
            "status": "unavailable" if not eval_rows else str(artifact.get("status") or "unavailable"),
            "rows": len(eval_rows),
            "delivery_dates": len({str(row.get("delivery_date")) for row in eval_rows}),
        }
    targets = [float(target_loss(row) or 0.0) for row in eval_rows]
    predictions = inference.predict_rows(artifact, eval_rows)
    return evaluate_predictions(eval_rows, targets, predictions)


def evaluate_predictions(
    eval_rows: Sequence[dict[str, Any]],
    targets: Sequence[float],
    predictions: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    if not eval_rows:
        return {"status": "unavailable", "rows": 0, "delivery_dates": 0}
    weights = equal_delivery_day_weights(eval_rows)
    abs_errors = [abs(float(pred["expected_loss_normalized"]) - y) for pred, y in zip(predictions, targets)]
    squared_errors = [(float(pred["expected_loss_normalized"]) - y) ** 2 for pred, y in zip(predictions, targets)]
    probability_errors = [
        (float(pred["probability_positive"]) - (1.0 if y > 0 else 0.0)) ** 2
        for pred, y in zip(predictions, targets)
    ]
    positive_errors = [
        abs(float(pred["conditional_positive_loss"]) - y)
        for pred, y in zip(predictions, targets)
        if y > 0
    ]
    underestimates = [
        max(0.0, y - float(pred["expected_loss_normalized"]))
        for pred, y in zip(predictions, targets)
    ]
    by_day_mae = _delivery_day_metric(eval_rows, abs_errors)
    by_day_mse = _delivery_day_metric(eval_rows, squared_errors)
    mae = _weighted_mean(abs_errors, weights)
    se = _standard_error(list(by_day_mae.values()))
    return {
        "status": "available",
        "rows": len(eval_rows),
        "delivery_dates": len(by_day_mae),
        "positive_delivery_dates": len({
            str(row.get("delivery_date"))
            for row in eval_rows
            if (target_loss(row) or 0.0) > 0.0
        }),
        "primary_metric": "expected_loss_mae",
        "primary_metric_definition": "delivery-day equal weighted mean absolute error between predicted normalized payout and realized normalized payout",
        "expected_loss_mae": mae,
        "expected_loss_mae_se_by_delivery": se,
        "expected_loss_mse": _weighted_mean(squared_errors, weights),
        "expected_loss_mse_se_by_delivery": _standard_error(list(by_day_mse.values())),
        "payout_probability_brier": _weighted_mean(probability_errors, weights),
        "reliability_bins": reliability_bins(predictions, targets, weights),
        "probability_calibration_mae": calibration_mae(predictions, targets, weights),
        "positive_loss_mae": statistics.fmean(positive_errors) if positive_errors else None,
        "tail_underestimate_p95": _quantile(underestimates, 0.95),
        "side_choice_accuracy": side_choice_accuracy(eval_rows, predictions, targets),
    }


def evaluate_runtime_predictions(
    eval_rows: Sequence[dict[str, Any]],
    targets: Sequence[float],
    probabilities: Any,
    conditional_losses: Any,
) -> dict[str, Any]:
    predictions = [
        {
            "row_id": row.get("row_id"),
            "side": canonical_side(row.get("side")),
            "probability_positive": float(probability),
            "conditional_positive_loss": max(EPSILON, float(positive_loss)),
            "expected_loss_normalized": float(probability) * max(EPSILON, float(positive_loss)),
        }
        for row, probability, positive_loss in zip(eval_rows, probabilities, conditional_losses)
    ]
    return evaluate_predictions(eval_rows, targets, predictions)


def fit_gam_grid_candidates(
    rows: Sequence[dict[str, Any]],
    *,
    feature_group: str,
    model_version: str = "astra_joint_gam@1.0.0",
    purge_against_splits: set[str] | None = None,
) -> list[dict[str, Any]]:
    purge_against_splits = DEFAULT_HOLDOUT_PURGE_SPLITS if purge_against_splits is None else set(purge_against_splits)
    names = feature_names(feature_group)
    train_rows, purge_report = training_rows_for_selection(rows, holdout_splits=purge_against_splits)
    qualification = training_qualification(train_rows)
    grid = [
        (float(logistic_c), float(gamma_alpha))
        for logistic_c in PROTOCOL["gam_logistic_C"]
        for gamma_alpha in PROTOCOL["gam_gamma_alpha"]
    ]
    if qualification["status"] != "available":
        return [
            {
                "model_family": "gam",
                "feature_group": feature_group,
                "logistic_c": logistic_c,
                "gamma_alpha": gamma_alpha,
                "status": "insufficient",
                "qualification": qualification,
                "purge_report": purge_report,
                "exportable_stdlib": False,
                "artifact": None,
                "artifact_hash": None,
                "selection_metrics": None,
            }
            for logistic_c, gamma_alpha in grid
        ]
    try:
        np, LogisticRegression, GammaRegressor, SplineTransformer = _require_sklearn()
    except MissingModelDependency as exc:
        return [
            {
                "model_family": "gam",
                "feature_group": feature_group,
                "logistic_c": logistic_c,
                "gamma_alpha": gamma_alpha,
                "status": "dependency_unavailable",
                "qualification": qualification,
                "purge_report": purge_report,
                "exportable_stdlib": False,
                "artifact": None,
                "artifact_hash": None,
                "selection_metrics": None,
                "reason": f"{type(exc).__name__}: {exc}",
            }
            for logistic_c, gamma_alpha in grid
        ]

    candidates: list[dict[str, Any]] = []
    with training_thread_context():
        try:
            raw_matrix, targets, _ = make_matrix(train_rows, names)
            weights = equal_delivery_day_weights(train_rows)
            preprocessor = _fit_preprocessor(np, SplineTransformer, raw_matrix, names)
            x_design = _transform_with_preprocessor(np, preprocessor, raw_matrix)
            y = np.asarray(targets, dtype=float)
            y_positive = (y > 0.0).astype(int)
            sample_weight = np.asarray(weights, dtype=float)
            positive_mask = y > 0.0
            selection_rows = filter_rows(rows, split_names={"selection"}, primary_width_only=True)
            selection_targets = [float(target_loss(row) or 0.0) for row in selection_rows]
            selection_design = None
            if selection_rows:
                selection_raw_matrix, _, _ = make_matrix(selection_rows, names)
                selection_design = _transform_with_preprocessor(np, preprocessor, selection_raw_matrix)
        except Exception as exc:
            return [
                {
                    "model_family": "gam",
                    "feature_group": feature_group,
                    "logistic_c": logistic_c,
                    "gamma_alpha": gamma_alpha,
                    "status": "preprocess_failed",
                    "qualification": qualification,
                    "purge_report": purge_report,
                    "exportable_stdlib": False,
                    "artifact": None,
                    "artifact_hash": None,
                    "selection_metrics": None,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
                for logistic_c, gamma_alpha in grid
            ]

        logistic_models: dict[float, Any] = {}
        logistic_failures: dict[float, str] = {}
        for logistic_c in [float(value) for value in PROTOCOL["gam_logistic_C"]]:
            try:
                logistic = LogisticRegression(
                    C=logistic_c,
                    solver="lbfgs",
                    max_iter=2000,
                    random_state=SEED,
                )
                logistic.fit(x_design, y_positive, sample_weight=sample_weight)
                logistic_models[logistic_c] = logistic
            except Exception as exc:
                logistic_failures[logistic_c] = f"{type(exc).__name__}: {exc}"

        gamma_models: dict[float, Any] = {}
        gamma_failures: dict[float, str] = {}
        for gamma_alpha in [float(value) for value in PROTOCOL["gam_gamma_alpha"]]:
            try:
                gamma = GammaRegressor(alpha=gamma_alpha, max_iter=2000)
                gamma.fit(x_design[positive_mask], y[positive_mask], sample_weight=sample_weight[positive_mask])
                gamma_models[gamma_alpha] = gamma
            except Exception as exc:
                gamma_failures[gamma_alpha] = f"{type(exc).__name__}: {exc}"

        for logistic_c, gamma_alpha in grid:
            reason = logistic_failures.get(logistic_c) or gamma_failures.get(gamma_alpha)
            if reason:
                candidates.append(
                    {
                        "model_family": "gam",
                        "feature_group": feature_group,
                        "logistic_c": logistic_c,
                        "gamma_alpha": gamma_alpha,
                        "status": "fit_failed",
                        "qualification": qualification,
                        "purge_report": purge_report,
                        "exportable_stdlib": False,
                        "artifact": None,
                        "artifact_hash": None,
                        "selection_metrics": None,
                        "reason": reason,
                    }
                )
                continue
            try:
                artifact = export_two_part_gam(
                    preprocessor=preprocessor,
                    logistic=logistic_models[logistic_c],
                    gamma=gamma_models[gamma_alpha],
                    feature_group=feature_group,
                    model_version=model_version,
                    logistic_c=logistic_c,
                    gamma_alpha=gamma_alpha,
                    training_rows=train_rows,
                    qualification=qualification,
                    purge_report=purge_report,
                )
                if selection_rows and selection_design is not None:
                    probabilities = logistic_models[logistic_c].predict_proba(selection_design)[:, 1]
                    conditional_losses = gamma_models[gamma_alpha].predict(selection_design)
                    metrics = evaluate_runtime_predictions(selection_rows, selection_targets, probabilities, conditional_losses)
                else:
                    metrics = {"status": "unavailable", "rows": 0, "delivery_dates": 0}
                status = "available"
                exportable = True
                artifact_hash = artifact.get("artifact_hash")
                export_reason = None
            except Exception as exc:
                artifact = None
                metrics = None
                status = "export_failed"
                exportable = False
                artifact_hash = None
                export_reason = f"{type(exc).__name__}: {exc}"
            candidates.append(
                {
                    "model_family": "gam",
                    "feature_group": feature_group,
                    "logistic_c": logistic_c,
                    "gamma_alpha": gamma_alpha,
                    "status": status,
                    "qualification": qualification,
                    "purge_report": purge_report,
                    "exportable_stdlib": exportable,
                    "artifact": artifact,
                    "artifact_hash": artifact_hash,
                    "selection_metrics": metrics,
                    "reason": export_reason,
                }
            )
    return candidates


def train_select(
    rows: Sequence[dict[str, Any]],
    *,
    feature_groups: Sequence[str] = ("statistical", "mechanism", "joint"),
    include_catboost: bool = True,
    model_version: str = "astra_joint_gam@1.0.0",
    holdout_purge_splits: set[str] | None = None,
) -> dict[str, Any]:
    holdout_purge_splits = DEFAULT_HOLDOUT_PURGE_SPLITS if holdout_purge_splits is None else set(holdout_purge_splits)
    candidates: list[dict[str, Any]] = []
    for group in feature_groups:
        candidates.extend(
            fit_gam_grid_candidates(
                rows,
                feature_group=group,
                model_version=model_version,
                purge_against_splits=holdout_purge_splits,
            )
        )

    if include_catboost:
        candidates.extend(train_catboost_challengers(rows, feature_groups=feature_groups, holdout_splits=holdout_purge_splits))

    for index, candidate in enumerate(candidates):
        candidate.setdefault("candidate_id", _candidate_id(candidate, index))

    global_pick = select_best_candidate(candidates)
    best_artifacts = {
        group: select_best_candidate([candidate for candidate in candidates if candidate.get("feature_group") == group])
        for group in feature_groups
    }
    sealed_comparisons = sealed_primary_comparisons(best_artifacts)
    if global_pick["status"] != "available":
        return {
            "schema": MODEL_SELECTION_SCHEMA,
            "status": "insufficient",
            "reason": "no_available_selection_candidate",
            "selection_metric": selection_metric_definition(),
            "holdout_purge_splits": sorted(holdout_purge_splits),
            "best_artifacts": best_artifacts,
            "sealed_primary_comparisons": sealed_comparisons,
            "candidates": _strip_runtime_candidates(candidates),
            "candidate_artifacts": candidate_artifacts(candidates),
            "selected": None,
        }

    return {
        "schema": MODEL_SELECTION_SCHEMA,
        "status": "available",
        "selection_metric": selection_metric_definition(),
        "selection_note": global_pick["selection_note"],
        "holdout_purge_splits": sorted(holdout_purge_splits),
        "selected": global_pick["selected"],
        "selected_artifact": global_pick["selected_artifact"],
        "selected_artifact_hash": global_pick["selected_artifact_hash"],
        "best_artifacts": best_artifacts,
        "sealed_primary_comparisons": sealed_comparisons,
        "candidates": [_candidate_summary(item) for item in candidates],
        "candidate_artifacts": candidate_artifacts(candidates),
    }


def train_catboost_challengers(
    rows: Sequence[dict[str, Any]],
    *,
    feature_groups: Sequence[str],
    holdout_splits: set[str] | None = None,
) -> list[dict[str, Any]]:
    holdout_splits = DEFAULT_HOLDOUT_PURGE_SPLITS if holdout_splits is None else set(holdout_splits)
    planned = [
        (group, int(depth))
        for group in feature_groups
        for depth in PROTOCOL["catboost_depths"]
    ]
    try:
        import numpy as np  # type: ignore
        from catboost import CatBoostClassifier, CatBoostRegressor  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional local package
        return [
            {
                "model_family": "catboost",
                "feature_group": group,
                "depth": depth,
                "status": "dependency_unavailable",
                "reason": f"{type(exc).__name__}: {exc}",
                "exportable_stdlib": False,
                "selection_metrics": None,
            }
            for group, depth in planned
        ]

    candidates: list[dict[str, Any]] = []
    train_rows, purge_report = training_rows_for_selection(rows, holdout_splits=holdout_splits)
    qualification = training_qualification(train_rows)
    if qualification["status"] != "available":
        return [
            {
                "model_family": "catboost",
                "feature_group": group,
                "depth": depth,
                "status": "insufficient",
                "qualification": qualification,
                "purge_report": purge_report,
                "exportable_stdlib": False,
                "selection_metrics": None,
            }
            for group, depth in planned
        ]
    for group in feature_groups:
        names = feature_names(group)
        with training_thread_context():
            x_raw, targets, _ = make_matrix(train_rows, names)
            x = np.asarray([[np.nan if value is None else float(value) for value in line] for line in x_raw], dtype=float)
            y = np.asarray(targets, dtype=float)
            full_weights = np.asarray(equal_delivery_day_weights(train_rows), dtype=float)
            full_positive_mask = y > 0
            train_indexes, eval_indexes = _internal_catboost_validation_indexes(train_rows)
            internal_validation = {
                "fit_rows": len(train_indexes),
                "validation_rows": len(eval_indexes),
                "fit_delivery_dates": len({str(train_rows[index].get("delivery_date")) for index in train_indexes}),
                "validation_delivery_dates": len({str(train_rows[index].get("delivery_date")) for index in eval_indexes}),
            }
            fit_rows = [train_rows[index] for index in train_indexes]
            fit_x = x[train_indexes]
            fit_y = y[train_indexes]
            fit_weights = np.asarray(equal_delivery_day_weights(fit_rows), dtype=float)
            eval_set_classifier = None
            eval_set_regressor = None
            if eval_indexes:
                eval_x = x[eval_indexes]
                eval_y = y[eval_indexes]
                eval_set_classifier = (eval_x, (eval_y > 0).astype(int))
                positive_eval = eval_y > 0
                if positive_eval.any():
                    eval_set_regressor = (eval_x[positive_eval], eval_y[positive_eval])
            positive_fit_mask = fit_y > 0
            for depth in PROTOCOL["catboost_depths"]:
                artifact = None
                metrics = None
                reason = None
                status = "available"
                exportable = True
                try:
                    classifier_probe = CatBoostClassifier(
                        depth=int(depth),
                        learning_rate=float(PROTOCOL["catboost_learning_rate"]),
                        iterations=int(PROTOCOL["catboost_max_iterations"]),
                        loss_function="Logloss",
                        random_seed=SEED,
                        verbose=False,
                        allow_writing_files=False,
                        thread_count=int(PROTOCOL["max_training_threads"]),
                        od_type="Iter",
                        od_wait=int(PROTOCOL["catboost_early_stopping_rounds"]),
                    )
                    regressor_probe = CatBoostRegressor(
                        depth=int(depth),
                        learning_rate=float(PROTOCOL["catboost_learning_rate"]),
                        iterations=int(PROTOCOL["catboost_max_iterations"]),
                        loss_function="RMSE",
                        random_seed=SEED,
                        verbose=False,
                        allow_writing_files=False,
                        thread_count=int(PROTOCOL["max_training_threads"]),
                        od_type="Iter",
                        od_wait=int(PROTOCOL["catboost_early_stopping_rounds"]),
                    )
                    classifier_probe.fit(
                        fit_x,
                        (fit_y > 0).astype(int),
                        sample_weight=fit_weights,
                        eval_set=eval_set_classifier,
                        use_best_model=eval_set_classifier is not None,
                    )
                    regressor_probe.fit(
                        fit_x[positive_fit_mask],
                        fit_y[positive_fit_mask],
                        sample_weight=fit_weights[positive_fit_mask],
                        eval_set=eval_set_regressor,
                        use_best_model=eval_set_regressor is not None,
                    )
                    classifier_iterations = _catboost_selected_iterations(classifier_probe)
                    regressor_iterations = _catboost_selected_iterations(regressor_probe)
                    classifier = CatBoostClassifier(
                        depth=int(depth),
                        learning_rate=float(PROTOCOL["catboost_learning_rate"]),
                        iterations=classifier_iterations,
                        loss_function="Logloss",
                        random_seed=SEED,
                        verbose=False,
                        allow_writing_files=False,
                        thread_count=int(PROTOCOL["max_training_threads"]),
                    )
                    regressor = CatBoostRegressor(
                        depth=int(depth),
                        learning_rate=float(PROTOCOL["catboost_learning_rate"]),
                        iterations=regressor_iterations,
                        loss_function="RMSE",
                        random_seed=SEED,
                        verbose=False,
                        allow_writing_files=False,
                        thread_count=int(PROTOCOL["max_training_threads"]),
                    )
                    classifier.fit(
                        x,
                        (y > 0).astype(int),
                        sample_weight=full_weights,
                    )
                    regressor.fit(
                        x[full_positive_mask],
                        y[full_positive_mask],
                        sample_weight=full_weights[full_positive_mask],
                    )
                    artifact = export_catboost_pair(
                        classifier=classifier,
                        regressor=regressor,
                        feature_names_=names,
                        feature_group=group,
                        depth=int(depth),
                        training_rows=train_rows,
                        qualification=qualification,
                        purge_report=purge_report,
                    )
                    metrics = evaluate_catboost_pair(classifier, regressor, rows, names, split_names={"selection"})
                    internal_validation_for_candidate = {
                        **internal_validation,
                        "classifier_selected_iterations": classifier_iterations,
                        "regressor_selected_iterations": regressor_iterations,
                        "full_refit_rows": len(train_rows),
                        "full_refit_delivery_dates": qualification.get("delivery_dates"),
                    }
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    status = "export_failed"
                    exportable = False
                    internal_validation_for_candidate = internal_validation
                candidates.append(
                    {
                        "model_family": "catboost",
                        "feature_group": group,
                        "depth": int(depth),
                        "status": status,
                        "qualification": qualification,
                        "purge_report": purge_report,
                        "catboost_internal_validation": internal_validation_for_candidate,
                        "exportable_stdlib": exportable,
                        "selection_metrics": metrics,
                        "artifact": artifact,
                        "artifact_hash": artifact.get("artifact_hash") if artifact else None,
                        "reason": reason,
                    }
                )
    return candidates


def selection_metric_definition() -> dict[str, str]:
    return {
        "primary_metric": "expected_loss_mae",
        "definition": "delivery-day equal weighted mean absolute error between predicted normalized payout and realized normalized payout",
        "secondary_metrics": "payout_probability_brier, probability_calibration_mae, tail_underestimate_p95, side_choice_accuracy",
        "tie_rule": "rank by primary metric on the selection split; if a challenger wins but the best GAM is within one delivery-day standard error, select the GAM",
    }


def select_best_candidate(candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    available = [
        item
        for item in candidates
        if item.get("exportable_stdlib")
        and item.get("artifact")
        and isinstance(item.get("selection_metrics"), dict)
        and item["selection_metrics"].get("status") == "available"
        and item["selection_metrics"].get("expected_loss_mae") is not None
    ]
    if not available:
        return {
            "status": "unavailable",
            "reason": "no_exportable_candidate_with_selection_metrics",
            "candidate_ids_considered": [str(item.get("candidate_id")) for item in candidates],
            "selected": None,
            "selected_artifact": None,
            "selected_artifact_hash": None,
        }
    ranked = sorted(
        available,
        key=lambda item: (
            float(item["selection_metrics"]["expected_loss_mae"]),
            float(item["selection_metrics"].get("payout_probability_brier") or math.inf),
            float(item["selection_metrics"].get("tail_underestimate_p95") or math.inf),
            0 if item["model_family"] == "gam" else 1,
        ),
    )
    best = ranked[0]
    selected = best
    selection_note = "lowest_selection_expected_loss_mae"
    if best["model_family"] != "gam":
        best_mae = float(best["selection_metrics"]["expected_loss_mae"])
        best_se = float(best["selection_metrics"].get("expected_loss_mae_se_by_delivery") or 0.0)
        gam_candidates = [item for item in ranked if item["model_family"] == "gam"]
        within_one_se = [
            item
            for item in gam_candidates
            if float(item["selection_metrics"]["expected_loss_mae"]) <= best_mae + best_se
        ]
        if within_one_se:
            selected = within_one_se[0]
            selection_note = "gam_within_one_delivery_day_standard_error_of_challenger"
        else:
            selection_note = "challenger_selected_after_selection_metrics"
    return {
        "status": "available",
        "selection_note": selection_note,
        "candidate_ids_considered": [str(item.get("candidate_id")) for item in candidates],
        "selected": _candidate_summary(selected),
        "selected_artifact": selected.get("artifact"),
        "selected_artifact_hash": selected.get("artifact_hash"),
    }


def candidate_artifacts(candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    inventory = []
    for candidate in candidates:
        inventory.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "model_family": candidate.get("model_family"),
                "feature_group": candidate.get("feature_group"),
                "status": candidate.get("status"),
                "artifact_hash": candidate.get("artifact_hash"),
                "artifact": candidate.get("artifact"),
                "reason": candidate.get("reason"),
            }
        )
    return inventory


def sealed_primary_comparisons(best_artifacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons = []
    for comparison in PROTOCOL["primary_comparisons"]:
        if comparison == "joint_minus_statistical":
            left, right = "joint", "statistical"
        elif comparison == "joint_minus_mechanism":
            left, right = "joint", "mechanism"
        else:
            comparisons.append({"comparison": comparison, "status": "unknown_comparison"})
            continue
        left_pick = best_artifacts.get(left, {})
        right_pick = best_artifacts.get(right, {})
        if left_pick.get("status") == "available" and right_pick.get("status") == "available":
            comparisons.append(
                {
                    "comparison": comparison,
                    "status": "sealed_pending_locked_test",
                    "selection_split": "selection",
                    "locked_test_split": "locked_test",
                    "left_feature_group": left,
                    "right_feature_group": right,
                    "left_artifact_hash": left_pick.get("selected_artifact_hash"),
                    "right_artifact_hash": right_pick.get("selected_artifact_hash"),
                }
            )
        else:
            comparisons.append(
                {
                    "comparison": comparison,
                    "status": "unavailable",
                    "left_feature_group": left,
                    "right_feature_group": right,
                    "left_status": left_pick.get("status"),
                    "right_status": right_pick.get("status"),
                }
            )
    return comparisons


def _candidate_id(candidate: dict[str, Any], index: int) -> str:
    parts = [
        str(candidate.get("model_family") or "model"),
        str(candidate.get("feature_group") or "group"),
    ]
    if candidate.get("logistic_c") is not None:
        parts.append(f"C{candidate['logistic_c']}")
    if candidate.get("gamma_alpha") is not None:
        parts.append(f"A{candidate['gamma_alpha']}")
    if candidate.get("depth") is not None:
        parts.append(f"D{candidate['depth']}")
    parts.append(str(index))
    return "__".join(parts)


def _internal_catboost_validation_indexes(rows: Sequence[dict[str, Any]]) -> tuple[list[int], list[int]]:
    by_day: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_day[str(row.get("delivery_date"))].append(index)
    days = sorted(by_day)
    if len(days) < 20:
        return list(range(len(rows))), []
    eval_day_count = max(1, min(len(days) // 5, len(days) - int(PROTOCOL["min_training_delivery_days"])))
    if eval_day_count <= 0:
        return list(range(len(rows))), []
    eval_days = set(days[-eval_day_count:])
    train_indexes = [index for day in days if day not in eval_days for index in by_day[day]]
    eval_indexes = [index for day in days if day in eval_days for index in by_day[day]]
    if not train_indexes or not eval_indexes:
        return list(range(len(rows))), []
    train_targets = [target_loss(rows[index]) or 0.0 for index in train_indexes]
    eval_targets = [target_loss(rows[index]) or 0.0 for index in eval_indexes]
    if not any(value > 0.0 for value in train_targets) or all(value > 0.0 for value in train_targets):
        return list(range(len(rows))), []
    if not any(value > 0.0 for value in eval_targets):
        return list(range(len(rows))), []
    return train_indexes, eval_indexes


def _catboost_selected_iterations(trained_model: Any) -> int:
    best_iteration = None
    try:
        best_iteration = trained_model.get_best_iteration()
    except Exception:
        best_iteration = None
    if best_iteration is not None and int(best_iteration) >= 0:
        return max(1, int(best_iteration) + 1)
    tree_count = getattr(trained_model, "tree_count_", None)
    if tree_count is not None:
        return max(1, int(tree_count))
    return max(1, int(PROTOCOL["catboost_max_iterations"]))


def export_catboost_pair(
    *,
    classifier: Any,
    regressor: Any,
    feature_names_: Sequence[str],
    feature_group: str,
    depth: int,
    training_rows: Sequence[dict[str, Any]],
    qualification: dict[str, Any],
    purge_report: dict[str, Any],
) -> dict[str, Any]:
    artifact = {
        "schema": MODEL_SCHEMA,
        "status": "available",
        "model_kind": "two_part_catboost",
        "model_version": "astra_joint_catboost@1.0.0",
        "feature_group": feature_group,
        "feature_names": list(feature_names_),
        "training_cutoff": _max_delivery_date(training_rows),
        "protocol": {
            "source_schema": PROTOCOL["schema"],
            "seed": PROTOCOL["seed"],
            "primary_width": PROTOCOL["primary_width"],
            "train_years": PROTOCOL["train_years"],
            "selection_years": PROTOCOL["selection_years"],
            "test_years": PROTOCOL["test_years"],
            "previously_used_years": PROTOCOL["previously_used_years"],
        },
        "scope": {
            "target": "normalized inverse-BTC spread payout; may exceed one and is not clipped",
            "probability_positive": "model probability that expiry payout is greater than zero; not calibrated as trade win rate",
            "conditional_positive_loss": "positive-payout regressor output clipped to a small positive floor for expected-loss arithmetic",
            "conditional_loss_weighting": "positive-loss model uses full-sample delivery-day weights restricted to positive rows, so positive frequency remains in the classifier component",
            "target_width": PROTOCOL["primary_width"],
            "side_sign": "put_credit=-1, call_credit=+1",
            "training_feature_support": feature_support_from_rows(training_rows, feature_names_),
        },
        "hyperparameters": {
            "depth": int(depth),
            "learning_rate": PROTOCOL["catboost_learning_rate"],
            "max_iterations": PROTOCOL["catboost_max_iterations"],
            "early_stopping_rounds": PROTOCOL["catboost_early_stopping_rounds"],
            "thread_count": PROTOCOL["max_training_threads"],
        },
        "qualification": qualification,
        "purge_report": purge_report,
        "catboost_classifier": _export_catboost_model(classifier, feature_names_, role="classifier"),
        "catboost_regressor": _export_catboost_model(regressor, feature_names_, role="regressor"),
    }
    artifact["artifact_hash"] = stable_hash({key: value for key, value in artifact.items() if key != "artifact_hash"})
    return artifact


def _export_catboost_model(model: Any, feature_names_: Sequence[str], *, role: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="astra_catboost_") as tmp:
        path = Path(tmp) / f"{role}.json"
        model.save_model(str(path), format="json")
        payload = json.loads(path.read_text(encoding="utf-8"))
    return _portable_catboost_from_json(payload, feature_names_, role=role)


def _portable_catboost_from_json(payload: dict[str, Any], feature_names_: Sequence[str], *, role: str) -> dict[str, Any]:
    features_info = payload.get("features_info") or {}
    float_features = features_info.get("float_features") or []
    nan_treatments: dict[int, str] = {}
    for feature in float_features:
        if not isinstance(feature, dict):
            continue
        flat_index = feature.get("flat_feature_index", feature.get("feature_index"))
        if flat_index is None:
            continue
        nan_treatments[int(flat_index)] = str(feature.get("nan_value_treatment") or "")

    trees = []
    for tree in payload.get("oblivious_trees") or []:
        if not isinstance(tree, dict):
            raise ValueError("invalid CatBoost JSON tree")
        portable_splits = []
        for split in tree.get("splits") or []:
            if not isinstance(split, dict):
                raise ValueError("invalid CatBoost JSON split")
            split_type = str(split.get("split_type") or "")
            if split_type != "FloatFeature":
                raise ValueError(f"unsupported CatBoost split type: {split_type}")
            raw_index = split.get("float_feature_index", split.get("flat_feature_index"))
            if raw_index is None:
                raise ValueError("CatBoost float split lacks feature index")
            feature_index = int(raw_index)
            if feature_index < 0 or feature_index >= len(feature_names_):
                raise ValueError(f"CatBoost split feature index out of range: {feature_index}")
            treatment = str(split.get("nan_value_treatment") or nan_treatments.get(feature_index) or "")
            portable_splits.append(
                {
                    "feature_index": feature_index,
                    "feature_name": feature_names_[feature_index],
                    "border": float(split["border"]),
                    "missing_goes_right": treatment in {"AsTrue", "Max"},
                    "nan_value_treatment": treatment or None,
                }
            )
        trees.append(
            {
                "splits": portable_splits,
                "leaf_values": _scalar_leaf_values(tree.get("leaf_values")),
            }
        )
    scale, bias = _catboost_scale_and_bias(payload)
    return {
        "format": "catboost_oblivious_trees_json@1.0.0",
        "role": role,
        "prediction_type": "raw_formula",
        "scale": scale,
        "bias": bias,
        "oblivious_trees": trees,
        "tree_count": len(trees),
        "feature_count": len(feature_names_),
    }


def _scalar_leaf_values(raw_values: object) -> list[float]:
    if not isinstance(raw_values, list):
        raise ValueError("CatBoost tree leaf_values must be a list")
    if all(isinstance(value, (int, float)) for value in raw_values):
        return [float(value) for value in raw_values]
    flattened: list[float] = []
    for value in raw_values:
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], (int, float)):
            flattened.append(float(value[0]))
        elif isinstance(value, list) and len(value) == 2 and all(isinstance(item, (int, float)) for item in value):
            flattened.append(float(value[1]) - float(value[0]))
        else:
            raise ValueError("CatBoost leaf values are not scalar binary leaves")
    return flattened


def _catboost_scale_and_bias(payload: dict[str, Any]) -> tuple[float, float]:
    scale_and_bias = payload.get("scale_and_bias")
    if isinstance(scale_and_bias, list) and scale_and_bias:
        scale = float(scale_and_bias[0])
        bias_value = 0.0
        if len(scale_and_bias) > 1:
            raw_bias = scale_and_bias[1]
            if isinstance(raw_bias, list) and raw_bias:
                bias_value = float(raw_bias[0])
            elif isinstance(raw_bias, (int, float)):
                bias_value = float(raw_bias)
        return scale, bias_value
    return 1.0, 0.0


def evaluate_catboost_pair(
    classifier: Any,
    regressor: Any,
    rows: Sequence[dict[str, Any]],
    feature_names_: Sequence[str],
    *,
    split_names: set[str] | None,
) -> dict[str, Any]:
    np = _require_numpy()
    eval_rows = filter_rows(rows, split_names=split_names, primary_width_only=True)
    if not eval_rows:
        return {"status": "unavailable", "rows": 0, "delivery_dates": 0}
    raw_matrix, targets, _ = make_matrix(eval_rows, feature_names_)
    x = np.asarray([[np.nan if value is None else float(value) for value in line] for line in raw_matrix], dtype=float)
    probabilities = classifier.predict_proba(x)[:, 1]
    conditional = regressor.predict(x)
    return evaluate_runtime_predictions(eval_rows, targets, probabilities, conditional)


def bootstrap_metric_difference(
    left_by_day: dict[str, float],
    right_by_day: dict[str, float],
    *,
    repetitions: int | None = None,
    block_days: int | None = None,
    seed: int = SEED,
) -> dict[str, Any]:
    """Bootstrap paired delivery-day metric differences, left minus right."""

    days = sorted(set(left_by_day) & set(right_by_day))
    if not days:
        return {"status": "unavailable", "paired_delivery_dates": 0}
    diffs = [float(left_by_day[day]) - float(right_by_day[day]) for day in days]
    observed = statistics.fmean(diffs)
    repetitions = int(repetitions or PROTOCOL["bootstrap_repetitions"])
    block_days = max(1, int(block_days or PROTOCOL["bootstrap_block_days"]))
    rng = random.Random(seed)
    # Group by calendar distance, not the position of available observations:
    # seven sparse observations must not masquerade as seven consecutive days.
    calendar_groups = {}
    first_day = datetime.fromisoformat(days[0]).date()
    for day, value in zip(days, diffs):
        key = (datetime.fromisoformat(day).date() - first_day).days // block_days
        calendar_groups.setdefault(key, []).append(value)
    blocks = list(calendar_groups.values())
    draws = []
    for _ in range(repetitions):
        sample: list[float] = []
        for _block in range(len(blocks)):
            sample.extend(rng.choice(blocks))
        draws.append(statistics.fmean(sample))
    return {
        "status": "available",
        "paired_delivery_dates": len(days),
        "observed_difference": observed,
        "ci95": [_quantile(draws, 0.025), _quantile(draws, 0.975)],
        "p_value_two_sided_centered": (1 + sum(abs(value-observed) >= abs(observed) for value in draws)) / (len(draws)+1),
        "calendar_blocks": len(blocks),
        "method": "paired_calendar_block_percentile_ci_centered_tail_plus_one@1.0.1",
        "repetitions": repetitions,
        "block_days": block_days,
    }


def holm_adjust(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(((name, float(value)) for name, value in p_values.items()), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running_max = 0.0
    m = len(ordered)
    for rank, (name, value) in enumerate(ordered):
        corrected = min(1.0, (m - rank) * value)
        running_max = max(running_max, corrected)
        adjusted[name] = running_max
    return adjusted


def _fit_preprocessor(np: Any, SplineTransformer: Any, raw_matrix: list[list[float | None]], names: Sequence[str]) -> dict[str, Any]:
    raw = np.asarray([[np.nan if value is None else float(value) for value in line] for line in raw_matrix], dtype=float)
    trained_features = []
    for index in range(raw.shape[1]):
        column = raw[:, index]
        finite = column[np.isfinite(column)]
        missing_count = int(column.size - finite.size)
        median = float(np.median(finite)) if finite.size else 0.0
        imputed = column.copy()
        imputed[~np.isfinite(imputed)] = median
        mean = float(imputed.mean()) if imputed.size else 0.0
        scale = float(imputed.std()) if imputed.size else 1.0
        if abs(scale) < EPSILON:
            scale = 1.0
        scaled = ((imputed - mean) / scale).reshape(-1, 1)
        unique_count = int(np.unique(scaled).size)
        if unique_count < 2:
            trained_features.append(
                {
                    "name": names[index],
                    "kind": "constant",
                    "median": median,
                    "mean": mean,
                    "scale": scale,
                    "transformer": None,
                    "actual_n_knots": 0,
                    "training_min": float(np.min(finite)) if finite.size else None,
                    "training_max": float(np.max(finite)) if finite.size else None,
                    "training_missing_rate": missing_count / int(column.size) if column.size else None,
                }
            )
            continue
        actual_n_knots = max(2, min(int(PROTOCOL["spline_knots"]), unique_count))
        transformer = SplineTransformer(
            degree=int(PROTOCOL["spline_degree"]),
            n_knots=actual_n_knots,
            knots="quantile",
            extrapolation="constant",
            include_bias=True,
        )
        transformer.fit(scaled)
        trained_features.append(
            {
                "name": names[index],
                "kind": "bspline",
                "median": median,
                "mean": mean,
                "scale": scale,
                "transformer": transformer,
                "actual_n_knots": actual_n_knots,
                "training_min": float(np.min(finite)) if finite.size else None,
                "training_max": float(np.max(finite)) if finite.size else None,
                "training_missing_rate": missing_count / int(column.size) if column.size else None,
            }
        )
    return {
        "feature_names": tuple(names),
        "trained_features": trained_features,
    }


def _transform_with_preprocessor(np: Any, preprocessor: dict[str, Any], raw_matrix: list[list[float | None]]) -> Any:
    raw = np.asarray([[np.nan if value is None else float(value) for value in line] for line in raw_matrix], dtype=float)
    pieces = []
    for index, trained_feature in enumerate(preprocessor["trained_features"]):
        column = raw[:, index]
        missing = ~np.isfinite(column)
        imputed = column.copy()
        imputed[missing] = float(trained_feature["median"])
        scaled = ((imputed - float(trained_feature["mean"])) / float(trained_feature["scale"])).reshape(-1, 1)
        if trained_feature["kind"] == "constant":
            pieces.append(np.ones((raw.shape[0], 1), dtype=float))
        else:
            pieces.append(trained_feature["transformer"].transform(scaled))
        pieces.append(missing.reshape(-1, 1).astype(float))
    return np.hstack(pieces)


def _insufficient_artifact(
    feature_group: str,
    model_version: str,
    qualification: dict[str, Any],
    purge_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = {
        "schema": MODEL_SCHEMA,
        "status": "insufficient",
        "model_kind": "insufficient",
        "model_version": model_version,
        "feature_group": feature_group,
        "qualification": qualification,
        "purge_report": purge_report or {},
        "scope": {
            "target": "normalized inverse-BTC spread payout; may exceed one and is not clipped",
            "target_width": PROTOCOL["primary_width"],
        },
    }
    return {
        "status": "insufficient",
        "model_kind": "insufficient",
        "feature_group": feature_group,
        "qualification": qualification,
        "purge_report": purge_report or {},
        "artifact": artifact,
        "artifact_hash": stable_hash(artifact),
    }


def _dependency_unavailable_result(
    feature_group: str,
    model_version: str,
    qualification: dict[str, Any],
    purge_report: dict[str, Any],
    exc: Exception,
) -> dict[str, Any]:
    return {
        "status": "dependency_unavailable",
        "model_kind": "untrained",
        "feature_group": feature_group,
        "model_version": model_version,
        "qualification": qualification,
        "purge_report": purge_report,
        "reason": f"{type(exc).__name__}: {exc}",
        "artifact": None,
        "artifact_hash": None,
    }


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "candidate_id",
        "model_family",
        "feature_group",
        "status",
        "logistic_c",
        "gamma_alpha",
        "depth",
        "qualification",
        "purge_report",
        "catboost_internal_validation",
        "exportable_stdlib",
        "selection_metrics",
        "artifact_hash",
        "note",
        "reason",
    ]
    return {key: candidate[key] for key in keep if key in candidate}


def _strip_runtime_candidates(candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_candidate_summary(candidate) for candidate in candidates]


def reliability_bins(
    predictions: Sequence[dict[str, Any]],
    targets: Sequence[float],
    weights: Sequence[float],
    bins: int = 5,
) -> list[dict[str, Any]]:
    if not predictions:
        return []
    items = sorted(
        (
            float(prediction["probability_positive"]),
            1.0 if target > 0 else 0.0,
            float(weight),
        )
        for prediction, target, weight in zip(predictions, targets, weights)
    )
    groups: list[list[tuple[float, float, float]]] = [[] for _ in range(min(bins, len(items)))]
    for index, item in enumerate(items):
        groups[min(len(groups) - 1, int(index * len(groups) / len(items)))].append(item)
    result = []
    for index, group in enumerate(groups):
        weight_sum = sum(item[2] for item in group)
        if weight_sum <= 0:
            continue
        mean_probability = sum(item[0] * item[2] for item in group) / weight_sum
        observed = sum(item[1] * item[2] for item in group) / weight_sum
        result.append(
            {
                "bin": index + 1,
                "rows": len(group),
                "weight": weight_sum,
                "probability_min": min(item[0] for item in group),
                "probability_max": max(item[0] for item in group),
                "mean_predicted_probability": mean_probability,
                "observed_positive_rate": observed,
                "absolute_gap": abs(mean_probability - observed),
            }
        )
    return result


def calibration_mae(predictions: Sequence[dict[str, Any]], targets: Sequence[float], weights: Sequence[float], bins: int = 5) -> float | None:
    bins_payload = reliability_bins(predictions, targets, weights, bins=bins)
    if not bins_payload:
        return None
    errors = [float(item["absolute_gap"]) for item in bins_payload]
    group_weights = [float(item["weight"]) for item in bins_payload]
    return _weighted_mean(errors, group_weights) if errors else None


def side_choice_accuracy(
    rows: Sequence[dict[str, Any]],
    predictions: Sequence[dict[str, Any]],
    targets: Sequence[float],
) -> dict[str, Any]:
    grouped: dict[tuple[Any, ...], list[tuple[dict[str, Any], dict[str, Any], float]]] = defaultdict(list)
    for row, prediction, target in zip(rows, predictions, targets):
        grouped[_pair_key(row)].append((row, prediction, float(target)))
    decided = 0
    correct = 0
    ties = 0
    for group in grouped.values():
        by_side = {canonical_side(row.get("side")): (row, prediction, target) for row, prediction, target in group}
        if "put_credit" not in by_side or "call_credit" not in by_side:
            continue
        put = by_side["put_credit"]
        call = by_side["call_credit"]
        put_pred = float(put[1]["expected_loss_normalized"])
        call_pred = float(call[1]["expected_loss_normalized"])
        put_target = put[2]
        call_target = call[2]
        if abs(put_pred - call_pred) <= EPSILON or abs(put_target - call_target) <= EPSILON:
            ties += 1
            continue
        decided += 1
        predicted_side = "put_credit" if put_pred < call_pred else "call_credit"
        actual_side = "put_credit" if put_target < call_target else "call_credit"
        if predicted_side == actual_side:
            correct += 1
    return {
        "comparable_pairs": decided + ties,
        "decided_pairs": decided,
        "ties": ties,
        "accuracy": correct / decided if decided else None,
    }


def _pair_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("event_family"),
        row.get("episode_id"),
        row.get("observation_kind"),
        row.get("as_of_ms"),
        row.get("entry_ms"),
        row.get("expiry_ms"),
        row.get("target_width"),
    )


def _delivery_day_metric(rows: Sequence[dict[str, Any]], values: Sequence[float]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row, value in zip(rows, values):
        grouped[str(row.get("delivery_date"))].append(float(value))
    return {day: statistics.fmean(day_values) for day, day_values in grouped.items()}


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    if not values:
        return math.nan
    total_weight = sum(weights)
    if total_weight <= 0:
        return statistics.fmean(values)
    return sum(float(value) * float(weight) for value, weight in zip(values, weights)) / total_weight


def _standard_error(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    return statistics.stdev(values) / math.sqrt(len(values))


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _delivery_year(row: dict[str, Any]) -> int | None:
    value = str(row.get("delivery_date") or "")
    if len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    expiry = finite_float(row.get("expiry_ms"))
    if expiry is not None:
        return datetime.utcfromtimestamp(expiry / 1000).year
    return None


def _max_delivery_date(rows: Sequence[dict[str, Any]]) -> str | None:
    dates = [str(row.get("delivery_date")) for row in rows if str(row.get("delivery_date") or "")]
    return max(dates) if dates else None


def _require_numpy() -> Any:
    try:
        import numpy as np  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on local environment
        raise MissingModelDependency(f"numpy is required for training: {exc}") from exc
    return np


def _require_sklearn() -> tuple[Any, Any, Any, Any]:
    try:
        import numpy as np  # type: ignore
        from sklearn.linear_model import GammaRegressor, LogisticRegression  # type: ignore
        from sklearn.preprocessing import SplineTransformer  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on local environment
        raise MissingModelDependency(f"scikit-learn stack is required for training: {exc}") from exc
    return np, LogisticRegression, GammaRegressor, SplineTransformer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train or evaluate Astra joint-research statistical models.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train-select")
    train_parser.add_argument("--rows", required=True, type=Path)
    train_parser.add_argument("--out", required=True, type=Path)
    train_parser.add_argument("--no-catboost", action="store_true")

    fit_parser = subparsers.add_parser("fit-gam")
    fit_parser.add_argument("--rows", required=True, type=Path)
    fit_parser.add_argument("--out", required=True, type=Path)
    fit_parser.add_argument("--feature-group", choices=sorted(FEATURE_GROUPS), default="joint")
    fit_parser.add_argument("--logistic-c", type=float, default=1.0)
    fit_parser.add_argument("--gamma-alpha", type=float, default=1.0)

    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--artifact", required=True, type=Path)
    evaluate_parser.add_argument("--rows", required=True, type=Path)
    evaluate_parser.add_argument("--out", required=True, type=Path)
    evaluate_parser.add_argument("--split", choices=["train", "selection", "locked_test", "development"], default="selection")

    args = parser.parse_args(argv)
    if args.command == "train-select":
        rows = read_rows(args.rows)
        result = train_select(rows, include_catboost=not args.no_catboost)
        write_json(args.out, result)
        return 0
    if args.command == "fit-gam":
        rows = read_rows(args.rows)
        result = fit_two_part_gam(
            rows,
            feature_group=args.feature_group,
            logistic_c=args.logistic_c,
            gamma_alpha=args.gamma_alpha,
        )
        write_json(args.out, result)
        return 0
    if args.command == "evaluate":
        artifact = inference.read_json(args.artifact)
        rows = read_rows(args.rows)
        metrics = evaluate_artifact(artifact, rows, split_names={args.split})
        write_json(args.out, metrics)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
