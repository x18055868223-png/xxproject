"""Stdlib inference for Astra joint-research statistical artifacts.

Training is allowed to depend on scikit-learn, but server inference should be
able to evaluate the exported two-part GAM JSON with the Python standard
library only.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


MODEL_SCHEMA = "astra_two_part_payout_model@1.0.0"
PREDICTION_SCHEMA = "astra_payout_prediction@1.0.0"
PREDICTION_BATCH_SCHEMA = "astra_payout_prediction_batch@1.0.0"
EPSILON = 1e-12


class InferenceArtifactError(ValueError):
    """Raised when an exported artifact is not usable for stdlib inference."""


def finite_float(value: object) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def stable_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [dict(row) for row in payload]
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return [dict(row) for row in payload["rows"]]
        raise ValueError("JSON input must be a list or an object with rows")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def write_csv(path: Path, rows: Iterable[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    materialized = list(rows)
    if fieldnames is None:
        fieldnames = []
        seen: set[str] = set()
        for row in materialized:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)


def normalize_side(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"put", "put_credit", "bull_put", "bull_put_spread"}:
        return "put_credit"
    if text in {"call", "call_credit", "bear_call", "bear_call_spread"}:
        return "call_credit"
    return None


def side_sign_from_row(row: dict[str, Any]) -> float | None:
    parsed = finite_float(row.get("side_sign"))
    if parsed is not None:
        if parsed > 0:
            return 1.0
        if parsed < 0:
            return -1.0
    side = normalize_side(row.get("side"))
    if side == "put_credit":
        return -1.0
    if side == "call_credit":
        return 1.0
    return None


def predict_row(artifact: dict[str, Any], row: dict[str, Any], *, include_intermediates: bool = False) -> dict[str, Any]:
    """Evaluate one row with an exported two-part model artifact."""

    _validate_artifact(artifact)
    if artifact.get("status") != "available":
        return {
            "schema": PREDICTION_SCHEMA,
            "status": str(artifact.get("status") or "unavailable"),
            "row_id": row.get("row_id"),
            "side": normalize_side(row.get("side")),
            "model_version": artifact.get("model_version"),
            "model_kind": artifact.get("model_kind"),
            "feature_group": artifact.get("feature_group"),
            "probability_positive": None,
            "conditional_positive_loss": None,
            "expected_loss_normalized": None,
        }

    if artifact.get("model_kind") == "two_part_gam":
        design, intermediates = design_vector(artifact, row)
        logistic = artifact["logistic_model"]
        gamma = artifact["gamma_model"]
        logit = _linear_predict(logistic, design)
        log_mu = _linear_predict(gamma, design)
        probability_positive = _sigmoid(logit)
        conditional_positive_loss = math.exp(_clamp_link(log_mu))
    elif artifact.get("model_kind") == "two_part_catboost":
        probability_positive, conditional_positive_loss, intermediates = predict_catboost_pair(artifact, row)
    else:  # pragma: no cover - protected by _validate_artifact
        raise InferenceArtifactError(f"unsupported model kind: {artifact.get('model_kind')}")
    expected_loss_normalized = probability_positive * conditional_positive_loss
    result = {
        "schema": PREDICTION_SCHEMA,
        "status": "available",
        "row_id": row.get("row_id"),
        "episode_id": row.get("episode_id"),
        "delivery_date": row.get("delivery_date"),
        "side": normalize_side(row.get("side")),
        "model_version": artifact.get("model_version"),
        "model_kind": artifact.get("model_kind"),
        "feature_group": artifact.get("feature_group"),
        "training_cutoff": artifact.get("training_cutoff"),
        "probability_positive": probability_positive,
        "conditional_positive_loss": conditional_positive_loss,
        "expected_loss_normalized": expected_loss_normalized,
        "scope": artifact.get("scope", {}),
        "prediction_hash": None,
    }
    if include_intermediates:
        result["intermediates"] = intermediates
    payload_for_hash = {key: value for key, value in result.items() if key != "prediction_hash"}
    result["prediction_hash"] = stable_hash(payload_for_hash)
    return result


def predict_rows(
    artifact: dict[str, Any],
    rows: Iterable[dict[str, Any]],
    *,
    include_intermediates: bool = False,
) -> list[dict[str, Any]]:
    return [predict_row(artifact, row, include_intermediates=include_intermediates) for row in rows]


def design_vector(artifact: dict[str, Any], row: dict[str, Any]) -> tuple[list[float], dict[str, Any]]:
    preprocess = artifact.get("preprocess")
    if not isinstance(preprocess, dict):
        raise InferenceArtifactError("artifact preprocess is missing")
    specs = preprocess.get("numeric_features")
    if not isinstance(specs, list):
        raise InferenceArtifactError("artifact numeric_features is missing")

    values: list[float] = []
    intermediates: dict[str, Any] = {
        "raw": {},
        "imputed": {},
        "scaled": {},
        "missing_indicators": {},
        "basis": {},
    }
    for spec in specs:
        if not isinstance(spec, dict) or not spec.get("name"):
            raise InferenceArtifactError("invalid feature spec")
        name = str(spec["name"])
        raw = side_sign_from_row(row) if name == "side_sign" else finite_float(row.get(name))
        missing = raw is None
        median = _required_float(spec, "median")
        mean = _required_float(spec, "mean")
        scale = _required_float(spec, "scale")
        if abs(scale) < EPSILON:
            scale = 1.0
        imputed = median if missing else float(raw)
        scaled = (imputed - mean) / scale
        basis = evaluate_spline(spec.get("spline"), scaled)
        values.extend(basis)
        values.append(1.0 if missing else 0.0)
        intermediates["raw"][name] = raw
        intermediates["imputed"][name] = imputed
        intermediates["scaled"][name] = scaled
        intermediates["missing_indicators"][name] = 1 if missing else 0
        intermediates["basis"][name] = basis

    expected_size = artifact.get("design_size")
    if expected_size is not None and int(expected_size) != len(values):
        raise InferenceArtifactError(f"design size mismatch: expected {expected_size}, got {len(values)}")
    return values, intermediates


def evaluate_spline(spline: object, value: float) -> list[float]:
    if not isinstance(spline, dict):
        raise InferenceArtifactError("feature spline is missing")
    if spline.get("type") == "constant":
        return [1.0]
    knots = [float(x) for x in spline.get("knots", [])]
    degree = int(spline.get("degree", 0))
    coefficients = spline.get("coefficient_matrix")
    if coefficients is not None:
        coef_matrix = [[float(cell) for cell in row] for row in coefficients]
        n_basis = len(coef_matrix)
        n_outputs = len(coef_matrix[0]) if coef_matrix else 0
    else:
        n_basis = int(spline.get("basis_count", 0))
        n_outputs = n_basis
        coef_matrix = [[1.0 if i == j else 0.0 for j in range(n_basis)] for i in range(n_basis)]
    if n_basis <= 0 or len(knots) != n_basis + degree + 1:
        raise InferenceArtifactError("invalid spline knot or basis count")
    if any(len(row) != n_outputs for row in coef_matrix):
        raise InferenceArtifactError("invalid spline coefficient matrix")
    raw_basis = _bspline_basis(knots, degree, n_basis, value)
    return [
        sum(raw_basis[i] * coef_matrix[i][j] for i in range(n_basis))
        for j in range(n_outputs)
    ]


def _bspline_basis(knots: list[float], degree: int, n_basis: int, value: float) -> list[float]:
    if degree < 0:
        raise InferenceArtifactError("spline degree must be non-negative")
    left = knots[degree]
    right = knots[n_basis]
    x = value
    if x < left:
        x = left
    if x >= right:
        x = math.nextafter(right, left)

    previous = [
        1.0 if knots[i] <= x < knots[i + 1] else 0.0
        for i in range(n_basis)
    ]
    for current_degree in range(1, degree + 1):
        current = [0.0] * n_basis
        for i in range(n_basis):
            left_den = knots[i + current_degree] - knots[i]
            right_den = knots[i + current_degree + 1] - knots[i + 1] if i + 1 < len(knots) else 0.0
            left_value = ((x - knots[i]) / left_den) * previous[i] if left_den > 0 else 0.0
            right_value = 0.0
            if i + 1 < n_basis and right_den > 0:
                right_value = ((knots[i + current_degree + 1] - x) / right_den) * previous[i + 1]
            current[i] = left_value + right_value
        previous = current
    return previous


def _linear_predict(model: dict[str, Any], design: list[float]) -> float:
    coef = [float(value) for value in model.get("coef", [])]
    if len(coef) != len(design):
        raise InferenceArtifactError(f"coefficient size mismatch: expected {len(design)}, got {len(coef)}")
    return float(model.get("intercept", 0.0)) + sum(c * x for c, x in zip(coef, design))


def predict_catboost_pair(artifact: dict[str, Any], row: dict[str, Any]) -> tuple[float, float, dict[str, Any]]:
    features = catboost_feature_vector(artifact, row)
    classifier = artifact.get("catboost_classifier")
    regressor = artifact.get("catboost_regressor")
    if not isinstance(classifier, dict) or not isinstance(regressor, dict):
        raise InferenceArtifactError("CatBoost pair artifact is missing classifier or regressor")
    logit, classifier_trace = predict_catboost_raw(classifier, features)
    positive_loss_raw, regressor_trace = predict_catboost_raw(regressor, features)
    return (
        _sigmoid(logit),
        max(EPSILON, positive_loss_raw),
        {
            "features": features,
            "catboost_classifier": classifier_trace,
            "catboost_regressor": regressor_trace,
        },
    )


def catboost_feature_vector(artifact: dict[str, Any], row: dict[str, Any]) -> list[float | None]:
    names = artifact.get("feature_names")
    if not isinstance(names, list) or not names:
        raise InferenceArtifactError("CatBoost artifact feature_names is missing")
    values: list[float | None] = []
    for name in names:
        text = str(name)
        value = side_sign_from_row(row) if text == "side_sign" else finite_float(row.get(text))
        values.append(value)
    return values


def predict_catboost_raw(model: dict[str, Any], features: list[float | None]) -> tuple[float, dict[str, Any]]:
    trees = model.get("oblivious_trees")
    if not isinstance(trees, list):
        raise InferenceArtifactError("CatBoost model has no oblivious_trees")
    scale = float(model.get("scale", 1.0))
    bias = float(model.get("bias", 0.0))
    raw_sum = 0.0
    leaf_indexes: list[int] = []
    for tree in trees:
        if not isinstance(tree, dict):
            raise InferenceArtifactError("invalid CatBoost tree")
        splits = tree.get("splits") or []
        leaves = tree.get("leaf_values")
        if not isinstance(splits, list) or not isinstance(leaves, list):
            raise InferenceArtifactError("invalid CatBoost tree splits or leaves")
        leaf_index = 0
        for depth, split in enumerate(splits):
            if not isinstance(split, dict):
                raise InferenceArtifactError("invalid CatBoost split")
            feature_index = int(split.get("feature_index", -1))
            if feature_index < 0 or feature_index >= len(features):
                raise InferenceArtifactError("CatBoost split feature index out of range")
            border = float(split.get("border", 0.0))
            value = features[feature_index]
            if value is None:
                goes_right = bool(split.get("missing_goes_right", False))
            else:
                goes_right = float(value) > border
            if goes_right:
                leaf_index |= 1 << depth
        if leaf_index >= len(leaves):
            raise InferenceArtifactError("CatBoost leaf index out of range")
        raw_sum += float(leaves[leaf_index])
        leaf_indexes.append(leaf_index)
    return scale * raw_sum + bias, {"raw_sum": raw_sum, "scale": scale, "bias": bias, "leaf_indexes": leaf_indexes}


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _clamp_link(value: float) -> float:
    return min(700.0, max(-700.0, value))


def _required_float(spec: dict[str, Any], key: str) -> float:
    value = finite_float(spec.get(key))
    if value is None:
        raise InferenceArtifactError(f"feature {spec.get('name')} is missing {key}")
    return value


def _validate_artifact(artifact: dict[str, Any]) -> None:
    if not isinstance(artifact, dict):
        raise InferenceArtifactError("artifact must be a JSON object")
    if artifact.get("schema") != MODEL_SCHEMA:
        raise InferenceArtifactError(f"unsupported artifact schema: {artifact.get('schema')}")
    model_kind = artifact.get("model_kind")
    if model_kind not in {"two_part_gam", "two_part_catboost", "insufficient"}:
        raise InferenceArtifactError(f"unsupported model kind: {model_kind}")
    if model_kind == "two_part_gam" and artifact.get("status") == "available":
        for key in ("preprocess", "logistic_model", "gamma_model", "feature_group"):
            if key not in artifact:
                raise InferenceArtifactError(f"artifact missing {key}")
    if model_kind == "two_part_catboost" and artifact.get("status") == "available":
        for key in ("feature_names", "catboost_classifier", "catboost_regressor", "feature_group"):
            if key not in artifact:
                raise InferenceArtifactError(f"artifact missing {key}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run stdlib inference for an Astra joint-research artifact.")
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--rows", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--include-intermediates", action="store_true")
    args = parser.parse_args(argv)

    artifact = read_json(args.artifact)
    rows = read_rows(args.rows)
    predictions = predict_rows(artifact, rows, include_intermediates=args.include_intermediates)
    if args.out.suffix.lower() == ".json":
        write_json(
            args.out,
            {
                "schema": PREDICTION_BATCH_SCHEMA,
                "prediction_schema": PREDICTION_SCHEMA,
                "model_schema": MODEL_SCHEMA,
                "predictions": predictions,
            },
        )
    else:
        write_csv(args.out, predictions)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
