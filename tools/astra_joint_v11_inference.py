"""Stdlib inference for Astra joint research v1.1 artifacts.

This module intentionally has no sklearn/numpy dependency. Training lives in
``astra_joint_v11_model.py`` and exports enough preprocessing/model parameters
for deterministic server-side scoring.
"""

from __future__ import annotations

import csv
import json
import math
import struct
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

EPSILON = 1e-12
SCHEMA = "astra_joint_v11_inference@1.0.0"


def finite_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def clamp(value: float, lower: float = EPSILON, upper: float = 1.0 - EPSILON) -> float:
    return min(max(float(value), lower), upper)


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def logit(probability: float) -> float:
    p = clamp(probability)
    return math.log(p / (1.0 - p))


def load_artifact(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        artifact = json.load(handle)
    if artifact.get("schema") != "astra_joint_v11_model_artifact@1.0.0":
        raise ValueError(f"unsupported artifact schema: {artifact.get('schema')}")
    return artifact


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _basis_values(x: float, knots: Sequence[float], degree: int) -> list[float]:
    """Evaluate B-spline basis values for the exported sklearn knot vector."""
    t = [float(item) for item in knots]
    k = int(degree)
    n_basis = len(t) - k - 1
    if n_basis <= 0:
        return []
    # sklearn SplineTransformer(extrapolation="constant") clips to the boundary
    # basis values outside the base knot interval.
    lower = t[k]
    upper = t[-k - 1]
    x = min(max(float(x), lower), upper)

    values = [0.0] * n_basis
    for i in range(n_basis):
        right = t[i + 1]
        left = t[i]
        if (left <= x < right) or (x == upper and left <= x <= right and i == n_basis - 1):
            values[i] = 1.0
    for d in range(1, k + 1):
        next_values = [0.0] * n_basis
        for i in range(n_basis):
            left_den = t[i + d] - t[i]
            right_den = t[i + d + 1] - t[i + 1]
            left = 0.0
            right = 0.0
            if abs(left_den) > EPSILON:
                left = ((x - t[i]) / left_den) * values[i]
            if abs(right_den) > EPSILON and i + 1 < n_basis:
                right = ((t[i + d + 1] - x) / right_den) * values[i + 1]
            next_values[i] = left + right
        values = next_values
    return values


def _apply_bspline(feature: Mapping[str, Any], scaled_value: float) -> list[float]:
    knots = feature.get("knots") or []
    degree = int(feature.get("degree") or 0)
    coefficients = feature.get("coefficients")
    basis = _basis_values(scaled_value, knots, degree)
    if not coefficients:
        return basis
    # scipy BSpline stores coefficients as n_basis x n_outputs. Export keeps the
    # same orientation; identity coefficients reproduce sklearn output exactly.
    output_count = len(coefficients[0]) if coefficients and coefficients[0] else len(basis)
    outputs = [0.0] * output_count
    for i, basis_value in enumerate(basis):
        if i >= len(coefficients):
            break
        for j, coef in enumerate(coefficients[i]):
            outputs[j] += basis_value * float(coef)
    return outputs


def transform_row(row: Mapping[str, Any], preprocessor: Mapping[str, Any]) -> tuple[list[float], dict[str, Any]]:
    values: list[float] = []
    trace: dict[str, Any] = {"schema": "astra_joint_v11_preprocess_trace@1.0.0", "features": []}
    for feature in preprocessor.get("trained_features", []):
        name = str(feature["name"])
        raw = finite_float(row.get(name))
        missing = raw is None
        imputed = float(feature.get("median", 0.0)) if missing else float(raw)
        mean = float(feature.get("mean", 0.0))
        scale = float(feature.get("scale", 1.0)) or 1.0
        scaled = (imputed - mean) / scale
        if feature.get("kind") == "constant":
            transformed = [1.0]
        elif feature.get("kind") == "linear":
            transformed = [scaled]
        elif feature.get("kind") == "bspline":
            transformed = _apply_bspline(feature, scaled)
        else:
            raise ValueError(f"unsupported feature transform: {feature.get('kind')}")
        if feature.get("include_missing_indicator", True):
            transformed.append(1.0 if missing else 0.0)
        values.extend(transformed)
        trace["features"].append(
            {
                "name": name,
                "raw": raw,
                "missing": missing,
                "imputed": imputed,
                "scaled": scaled,
                "outputs": transformed,
            }
        )
    return values, trace


def linear_score(model: Mapping[str, Any], vector: Sequence[float]) -> float:
    intercept = float(model.get("intercept", 0.0))
    coefficients = [float(item) for item in model.get("coefficients", [])]
    total = intercept
    for coef, value in zip(coefficients, vector):
        total += coef * float(value)
    return total


def apply_probability_calibrator(probability: float, calibrator: Mapping[str, Any] | None) -> float:
    if not calibrator or calibrator.get("status") != "available":
        return clamp(probability)
    delta = float(calibrator.get("logit_delta", 0.0))
    return clamp(sigmoid(logit(probability) + delta))


def apply_scale(value: float, calibrator: Mapping[str, Any] | None) -> float:
    if not calibrator or calibrator.get("status") != "available":
        return max(0.0, float(value))
    return max(0.0, float(value) * float(calibrator.get("scale", 1.0)))


def width_value_btc(row: Mapping[str, Any]) -> float | None:
    actual_width = finite_float(row.get("actual_width"))
    entry_price = finite_float(row.get("entry_price"))
    if actual_width is None or entry_price is None or actual_width <= 0 or entry_price <= 0:
        return None
    return actual_width / entry_price


def _predict_gam(row: Mapping[str, Any], model: Mapping[str, Any]) -> dict[str, Any]:
    vector, trace = transform_row(row, model["preprocessor"])
    classifier = model["classifier"]
    severity = model["positive_severity"]
    occurrence_score = linear_score(classifier, vector)
    raw_probability = sigmoid(occurrence_score) if classifier.get("kind") == "logistic" else float(classifier.get("constant_probability", 0.0))
    probability = apply_probability_calibrator(raw_probability, (model.get("calibrators") or {}).get("occurrence"))

    if severity.get("kind") == "gamma_log_link":
        raw_positive_loss = math.exp(linear_score(severity, vector))
    else:
        raw_positive_loss = float(severity.get("constant_positive_loss", 0.0))
    positive_loss = apply_scale(raw_positive_loss, (model.get("calibrators") or {}).get("positive_severity"))
    expected_loss = probability * positive_loss

    tail_head = model.get("tail_head") or {"status": "unavailable"}
    tail_probability = None
    tail_raw_probability = None
    if tail_head.get("status") == "available" and tail_head.get("kind") == "logistic":
        tail_raw_probability = sigmoid(linear_score(tail_head, vector))
        tail_probability = apply_probability_calibrator(tail_raw_probability, (model.get("calibrators") or {}).get("tail"))

    width_btc = width_value_btc(row)
    expected_payout_btc = expected_loss * width_btc if width_btc is not None else None
    feature_traces = trace.get("features", [])
    eligibility = dict(model.get("eligibility", {}))
    if "eligible" not in eligibility:
        eligibility["eligible"] = eligibility.get("status") == "available"
    if "qualified" not in eligibility:
        eligibility["qualified"] = eligibility.get("status") == "available"
    required_names = [str(name) for name in model.get("required_feature_names") or [item["name"] for item in feature_traces]]
    missing_names = [item["name"] for item in feature_traces if item.get("missing")]
    required_missing = [name for name in required_names if name in missing_names]
    input_support = {
        "feature_count": len(feature_traces),
        "required_feature_count": len(required_names),
        "missing_features": missing_names,
        "missing_feature_count": len(missing_names),
        "required_missing_features": required_missing,
        "required_missing_feature_count": len(required_missing),
        "qualified": len(required_missing) == 0,
    }
    if required_missing:
        eligibility["qualified"] = False
    return {
        "schema": "astra_joint_v11_prediction@1.0.0",
        "status": "available",
        "model_id": model.get("model_id"),
        "model_hash": model.get("model_hash"),
        "training_cutoff_ms": model.get("training_cutoff_ms"),
        "training_cutoff": model.get("training_cutoff"),
        "model_family": model.get("model_family"),
        "feature_group": model.get("feature_group"),
        "row_id": row.get("row_id"),
        "side": row.get("side"),
        "probability_positive": probability,
        "raw_probability_positive": raw_probability,
        "conditional_positive_loss": positive_loss,
        "raw_conditional_positive_loss": raw_positive_loss,
        "expected_loss_normalized": expected_loss,
        "expected_payout_btc": expected_payout_btc,
        "tail_probability": tail_probability,
        "breach_probability": tail_probability,
        "raw_tail_probability": tail_raw_probability,
        "tail_probability_status": tail_head.get("status", "unavailable"),
        "es95_training_all_normalized": model.get("tail_summary", {}).get("es95_all_normalized"),
        "eligibility": eligibility,
        "input_support": input_support,
        "preprocess_trace": trace,
    }


def _catboost_feature_vector(row: Mapping[str, Any], names: Sequence[str]) -> tuple[list[float | None], dict[str, Any]]:
    values: list[float | None] = []
    missing: list[str] = []
    for name in names:
        value = finite_float(row.get(str(name)))
        values.append(value)
        if value is None:
            missing.append(str(name))
    return values, {
        "feature_count": len(names),
        "missing_features": missing,
        "missing_feature_count": len(missing),
    }


def _predict_catboost_raw(model: Mapping[str, Any], features: Sequence[float | None]) -> tuple[float, dict[str, Any]]:
    trees = model.get("oblivious_trees")
    if not isinstance(trees, list):
        raise ValueError("CatBoost model has no oblivious_trees")
    scale = float(model.get("scale", 1.0))
    bias = float(model.get("bias", 0.0))
    raw_sum = 0.0
    leaf_indexes: list[int] = []
    for tree in trees:
        if not isinstance(tree, dict):
            raise ValueError("invalid CatBoost tree")
        splits = tree.get("splits") or []
        leaves = tree.get("leaf_values")
        if not isinstance(splits, list) or not isinstance(leaves, list):
            raise ValueError("invalid CatBoost tree splits or leaves")
        leaf_index = 0
        for depth, split in enumerate(splits):
            feature_index = int(split.get("feature_index", -1))
            if feature_index < 0 or feature_index >= len(features):
                raise ValueError("CatBoost split feature index out of range")
            value = features[feature_index]
            if value is None:
                goes_right = bool(split.get("missing_goes_right", False))
            else:
                goes_right = float32(float(value)) > float32(float(split.get("border", 0.0)))
            if goes_right:
                leaf_index |= 1 << depth
        if leaf_index >= len(leaves):
            raise ValueError("CatBoost leaf index out of range")
        raw_sum += float(leaves[leaf_index])
        leaf_indexes.append(leaf_index)
    return scale * raw_sum + bias, {"raw_sum": raw_sum, "scale": scale, "bias": bias, "leaf_indexes": leaf_indexes}


def _predict_catboost(row: Mapping[str, Any], model: Mapping[str, Any]) -> dict[str, Any]:
    names = model.get("feature_names") or []
    features, input_support = _catboost_feature_vector(row, names)
    classifier = model.get("catboost_classifier")
    regressor = model.get("catboost_regressor")
    if not isinstance(classifier, dict) or not isinstance(regressor, dict):
        return {
            "schema": "astra_joint_v11_prediction@1.0.0",
            "status": "unavailable",
            "reason": "missing_catboost_classifier_or_regressor",
            "model_id": model.get("model_id"),
            "row_id": row.get("row_id"),
            "side": row.get("side"),
            "input_support": input_support,
        }
    raw_logit, classifier_trace = _predict_catboost_raw(classifier, features)
    raw_positive_loss, regressor_trace = _predict_catboost_raw(regressor, features)
    raw_probability = sigmoid(raw_logit)
    probability = apply_probability_calibrator(raw_probability, (model.get("calibrators") or {}).get("occurrence"))
    positive_loss = apply_scale(max(0.0, raw_positive_loss), (model.get("calibrators") or {}).get("positive_severity"))
    expected_loss = probability * positive_loss
    tail_probability = None
    tail_raw_probability = None
    tail_trace = None
    tail_head = model.get("catboost_tail") or model.get("tail_head") or {"status": "unavailable"}
    if isinstance(tail_head, dict) and tail_head.get("status") == "available":
        raw_tail, tail_trace = _predict_catboost_raw(tail_head, features)
        tail_raw_probability = sigmoid(raw_tail)
        tail_probability = apply_probability_calibrator(tail_raw_probability, (model.get("calibrators") or {}).get("tail"))
    width_btc = width_value_btc(row)
    eligibility = dict(model.get("eligibility", {}))
    if "eligible" not in eligibility:
        eligibility["eligible"] = eligibility.get("status") == "available"
    if "qualified" not in eligibility:
        eligibility["qualified"] = eligibility.get("status") == "available"
    required_names = [str(name) for name in model.get("required_feature_names") or names]
    required_missing = [name for name in required_names if name in input_support["missing_features"]]
    input_support["required_feature_count"] = len(required_names)
    input_support["required_missing_features"] = required_missing
    input_support["required_missing_feature_count"] = len(required_missing)
    input_support["qualified"] = len(required_missing) == 0
    if required_missing:
        eligibility["qualified"] = False
    return {
        "schema": "astra_joint_v11_prediction@1.0.0",
        "status": "available",
        "model_id": model.get("model_id"),
        "model_hash": model.get("model_hash"),
        "training_cutoff_ms": model.get("training_cutoff_ms"),
        "training_cutoff": model.get("training_cutoff"),
        "model_family": model.get("model_family"),
        "feature_group": model.get("feature_group"),
        "row_id": row.get("row_id"),
        "side": row.get("side"),
        "probability_positive": probability,
        "raw_probability_positive": raw_probability,
        "conditional_positive_loss": positive_loss,
        "raw_conditional_positive_loss": raw_positive_loss,
        "expected_loss_normalized": expected_loss,
        "expected_payout_btc": expected_loss * width_btc if width_btc is not None else None,
        "tail_probability": tail_probability,
        "breach_probability": tail_probability,
        "raw_tail_probability": tail_raw_probability,
        "tail_probability_status": tail_head.get("status", "unavailable") if isinstance(tail_head, dict) else "unavailable",
        "es95_training_all_normalized": model.get("tail_summary", {}).get("es95_all_normalized"),
        "eligibility": eligibility,
        "input_support": input_support,
        "preprocess_trace": {
            "schema": "astra_joint_v11_catboost_trace@1.0.0",
            "features": features,
            "catboost_classifier": classifier_trace,
            "catboost_regressor": regressor_trace,
            "catboost_tail": tail_trace,
        },
    }


def _tail_probability_policy(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    policy = artifact.get("publication_policy")
    return policy if isinstance(policy, Mapping) else {}


def _apply_publication_policy(prediction: dict[str, Any], artifact: Mapping[str, Any]) -> dict[str, Any]:
    policy = _tail_probability_policy(artifact)
    if policy.get("tail_probability") != "research_only":
        return prediction
    result = dict(prediction)
    trace = dict(result.get("preprocess_trace") or {})
    trace["research_only_tail_probability"] = {
        "schema": "astra_joint_v11_research_only_tail_probability@1.0.0",
        "reason": policy.get("reason_cn") or policy.get("tail_probability_reason"),
        "evidence_hash": policy.get("evidence_sha256") or policy.get("tail_probability_evidence_hash"),
        "tail_probability": result.get("tail_probability"),
        "breach_probability": result.get("breach_probability"),
        "raw_tail_probability": result.get("raw_tail_probability"),
        "tail_probability_status": result.get("tail_probability_status"),
    }
    result["preprocess_trace"] = trace
    result["tail_probability"] = None
    result["breach_probability"] = None
    result["raw_tail_probability"] = None
    result["tail_probability_status"] = "research_only"
    return result


def predict_row(row: Mapping[str, Any], artifact: Mapping[str, Any], feature_group: str | None = None) -> dict[str, Any]:
    group = feature_group or artifact.get("selected_feature_group")
    models = artifact.get("models") or {}
    if group not in models:
        raise KeyError(f"feature group not available in artifact: {group}")
    model = models[group]
    if model.get("model_family") == "gam":
        return _apply_publication_policy(_predict_gam(row, model), artifact)
    if model.get("model_family") == "catboost":
        return _apply_publication_policy(_predict_catboost(row, model), artifact)
    raise ValueError(f"unsupported v1.1 model family: {model.get('model_family')}")


def predict_rows(rows: Iterable[Mapping[str, Any]], artifact: Mapping[str, Any], feature_group: str | None = None) -> list[dict[str, Any]]:
    return [predict_row(row, artifact, feature_group=feature_group) for row in rows]


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Astra joint v1.1 stdlib inference")
    parser.add_argument("artifact")
    parser.add_argument("csv")
    parser.add_argument("--feature-group")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    artifact = load_artifact(args.artifact)
    rows = read_csv_rows(args.csv)
    predictions = predict_rows(rows, artifact, feature_group=args.feature_group)
    payload = {"schema": "astra_joint_v11_predictions@1.0.0", "count": len(predictions), "predictions": predictions}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
