"""Stdlib inference helpers for Astra joint research v1.2 challengers.

The v1.2 artifacts are research-only wrappers around sealed v1.1 outputs and
small exported linear heads. They do not create trading permission, premiums,
or production tail percentages.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "astra_joint_v12_inference@1.0.0"
ARTIFACT_SCHEMA = "astra_joint_v12_model_artifact@1.0.0"
EPSILON = 1e-12


class InferenceError(ValueError):
    pass


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


def clamp_probability(value: float) -> float:
    return min(max(float(value), EPSILON), 1.0 - EPSILON)


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def logit(probability: float) -> float:
    p = clamp_probability(probability)
    return math.log(p / (1.0 - p))


def load_artifact(path: str | Path) -> dict[str, Any]:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    if artifact.get("schema") != ARTIFACT_SCHEMA:
        raise InferenceError(f"unsupported artifact schema: {artifact.get('schema')}")
    return artifact


def transform_row(row: Mapping[str, Any], preprocessor: Mapping[str, Any]) -> tuple[list[float], dict[str, Any]]:
    vector: list[float] = []
    trace = {"schema": "astra_joint_v12_preprocess_trace@1.0.0", "features": []}
    for item in preprocessor.get("features", []):
        name = str(item["name"])
        raw = finite_float(row.get(name))
        missing = raw is None
        imputed = float(item.get("impute", item.get("mean", 0.0))) if missing else float(raw)
        mean = float(item.get("mean", 0.0))
        scale = float(item.get("scale", 1.0)) or 1.0
        standardized = (imputed - mean) / scale
        vector.append(standardized)
        if item.get("include_missing_indicator", True):
            vector.append(1.0 if missing else 0.0)
        trace["features"].append(
            {
                "name": name,
                "raw": raw,
                "missing": missing,
                "imputed": imputed,
                "standardized": standardized,
            }
        )
    return vector, trace


def linear_score(model: Mapping[str, Any], vector: Sequence[float]) -> float:
    coefficients = [float(value) for value in model.get("coefficients", [])]
    if len(coefficients) != len(vector):
        raise InferenceError(f"coefficient size mismatch: {len(coefficients)} != {len(vector)}")
    total = float(model.get("intercept", 0.0))
    for coef, value in zip(coefficients, vector):
        total += coef * float(value)
    return total


def apply_logit_delta(probability: float, calibrator: Mapping[str, Any] | None) -> float:
    if not calibrator or calibrator.get("status") != "available":
        return clamp_probability(probability)
    return clamp_probability(sigmoid(logit(probability) + float(calibrator.get("logit_delta", 0.0))))


def predict_s50(row: Mapping[str, Any], model: Mapping[str, Any]) -> dict[str, Any]:
    geometry = finite_float(row.get("geometry_expected_loss_normalized"))
    statistical = finite_float(row.get("statistical_expected_loss_normalized"))
    if geometry is None or statistical is None:
        return {
            "schema": "astra_joint_v12_prediction@1.0.0",
            "candidate_id": model.get("candidate_id", "S50"),
            "status": "unavailable",
            "reason": "missing_geometry_or_statistical_expected_loss",
            "row_id": row.get("row_id"),
        }
    alpha = float(model.get("alpha", 0.5))
    expected = (1.0 - alpha) * geometry + alpha * statistical
    return {
        "schema": "astra_joint_v12_prediction@1.0.0",
        "candidate_id": model.get("candidate_id", "S50"),
        "status": "available",
        "row_id": row.get("row_id"),
        "side": row.get("side"),
        "expected_loss_normalized": expected,
        "expected_loss_status": "fixed_blend_ranking_research_only",
        "probability_positive": None,
        "tail_probability": None,
        "tail_probability_status": "not_modeled",
    }


def predict_pair_delta(row: Mapping[str, Any], model: Mapping[str, Any]) -> dict[str, Any]:
    vector, trace = transform_row(row, model["preprocessor"])
    raw_delta = linear_score(model["linear_model"], vector)
    delta = raw_delta + float((model.get("calibrator") or {}).get("residual_intercept", 0.0))
    selected_side = "put_credit" if delta <= 0.0 else "call_credit"
    result = {
        "schema": "astra_joint_v12_pair_prediction@1.0.0",
        "candidate_id": model.get("candidate_id", "PAIR_RIDGE10"),
        "status": "available",
        "row_id": row.get("row_id"),
        "observation_id": row.get("observation_id"),
        "predicted_put_minus_call_loss": delta,
        "raw_predicted_put_minus_call_loss": raw_delta,
        "selected_side": selected_side,
        "prediction_kind": "signed_pair_delta_only",
        "preprocess_trace": trace,
    }
    return result


def predict_nested_tail(row: Mapping[str, Any], model: Mapping[str, Any]) -> dict[str, Any]:
    parent_probability = None
    for name in ("parent_probability_positive", "statistical_probability_positive", "probability_positive"):
        candidate = finite_float(row.get(name))
        if candidate is not None:
            parent_probability = candidate
            break
    if parent_probability is None:
        return {
            "schema": "astra_joint_v12_prediction@1.0.0",
            "candidate_id": model.get("candidate_id", "NESTED_LOGIT_C01"),
            "status": "unavailable",
            "reason": "missing_parent_probability_positive",
            "row_id": row.get("row_id"),
        }
    if parent_probability < 0.0 or parent_probability > 1.0:
        return {
            "schema": "astra_joint_v12_prediction@1.0.0",
            "candidate_id": model.get("candidate_id", "NESTED_LOGIT_C01"),
            "status": "unavailable",
            "reason": "parent_probability_positive_out_of_range",
            "row_id": row.get("row_id"),
            "parent_probability_positive": parent_probability,
        }
    vector, trace = transform_row(row, model["preprocessor"])
    raw_conditional = sigmoid(linear_score(model["conditional_tail_model"], vector))
    conditional = apply_logit_delta(raw_conditional, model.get("calibrator"))
    tail = parent_probability * conditional
    return {
        "schema": "astra_joint_v12_prediction@1.0.0",
        "candidate_id": model.get("candidate_id", "NESTED_LOGIT_C01"),
        "status": "available",
        "row_id": row.get("row_id"),
        "side": row.get("side"),
        "parent_probability_positive": parent_probability,
        "conditional_tail_given_positive": conditional,
        "raw_conditional_tail_given_positive": raw_conditional,
        "tail_probability": tail,
        "breach_probability": tail,
        "tail_probability_status": "research_only",
        "order_violation": tail > parent_probability + 1e-12,
        "preprocess_trace": trace,
    }


def predict_row(row: Mapping[str, Any], artifact: Mapping[str, Any], candidate_id: str | None = None) -> dict[str, Any]:
    if artifact.get("schema") != ARTIFACT_SCHEMA:
        raise InferenceError(f"unsupported artifact schema: {artifact.get('schema')}")
    models = artifact.get("models") or {}
    key = candidate_id or artifact.get("selected_candidate_id")
    if key not in models:
        raise InferenceError(f"candidate not available in artifact: {key}")
    model = models[key]
    kind = model.get("kind")
    if kind == "fixed_half_blend":
        return predict_s50(row, model)
    if kind == "pair_ridge_delta":
        return predict_pair_delta(row, model)
    if kind == "nested_conditional_tail_logistic":
        return predict_nested_tail(row, model)
    raise InferenceError(f"unsupported v1.2 model kind: {kind}")
