from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import astra_joint_inference as inference


def simple_artifact() -> dict[str, object]:
    return {
        "schema": inference.MODEL_SCHEMA,
        "status": "available",
        "model_kind": "two_part_gam",
        "model_version": "unit",
        "feature_group": "unit",
        "training_cutoff": "2021-12-31",
        "scope": {"target": "unit"},
        "design_size": 4,
        "preprocess": {
            "version": "unit",
            "numeric_features": [
                {
                    "name": "ret_15",
                    "median": 0.0,
                    "mean": 0.0,
                    "scale": 1.0,
                    "spline": {
                        "degree": 0,
                        "knots": [0.0, 1.0],
                        "coefficient_matrix": [[1.0]],
                        "basis_count": 1,
                    },
                },
                {
                    "name": "side_sign",
                    "median": -1.0,
                    "mean": 0.0,
                    "scale": 1.0,
                    "spline": {
                        "degree": 0,
                        "knots": [-2.0, 2.0],
                        "coefficient_matrix": [[1.0]],
                        "basis_count": 1,
                    },
                },
            ],
        },
        "logistic_model": {"link": "logit", "intercept": 0.0, "coef": [1.0, 0.0, -1.0, 0.0]},
        "gamma_model": {"link": "log", "intercept": math.log(2.0), "coef": [0.0, 0.0, 0.0, 0.0]},
    }


def test_predict_row_uses_stdlib_artifact_and_side_mapping() -> None:
    prediction = inference.predict_row(simple_artifact(), {"row_id": "R1", "side": "call_credit", "ret_15": 0.4})

    assert prediction["schema"] == inference.PREDICTION_SCHEMA
    assert prediction["status"] == "available"
    assert prediction["side"] == "call_credit"
    assert prediction["probability_positive"] == pytest.approx(0.5)
    assert prediction["conditional_positive_loss"] == pytest.approx(2.0)
    assert prediction["expected_loss_normalized"] == pytest.approx(1.0)
    assert str(prediction["prediction_hash"]).startswith("sha256:")


def test_missing_indicator_reaches_linear_models() -> None:
    artifact = simple_artifact()
    artifact["logistic_model"] = {"link": "logit", "intercept": 0.0, "coef": [0.0, 2.0, 0.0, 0.0]}

    present = inference.predict_row(artifact, {"side": "put_credit", "ret_15": 0.5})
    missing = inference.predict_row(artifact, {"side": "put_credit"})

    assert present["probability_positive"] == pytest.approx(0.5)
    assert missing["probability_positive"] == pytest.approx(1.0 / (1.0 + math.exp(-2.0)))


def test_design_vector_rejects_coefficient_size_mismatch() -> None:
    artifact = simple_artifact()
    artifact["logistic_model"] = {"link": "logit", "intercept": 0.0, "coef": [1.0]}

    with pytest.raises(inference.InferenceArtifactError):
        inference.predict_row(artifact, {"side": "put_credit", "ret_15": 0.5})


def test_unsupported_schema_is_not_silently_used() -> None:
    artifact = simple_artifact()
    artifact["schema"] = "other"

    with pytest.raises(inference.InferenceArtifactError):
        inference.predict_row(artifact, {"side": "put_credit", "ret_15": 0.5})


def test_catboost_tree_artifact_predicts_without_catboost_dependency() -> None:
    artifact = {
        "schema": inference.MODEL_SCHEMA,
        "status": "available",
        "model_kind": "two_part_catboost",
        "model_version": "unit-catboost",
        "feature_group": "unit",
        "feature_names": ["ret_15", "side_sign"],
        "catboost_classifier": {
            "format": "catboost_oblivious_trees_json@1.0.0",
            "role": "classifier",
            "scale": 1.0,
            "bias": 0.0,
            "oblivious_trees": [
                {
                    "splits": [{"feature_index": 0, "border": 0.2, "missing_goes_right": False}],
                    "leaf_values": [-2.0, 2.0],
                }
            ],
        },
        "catboost_regressor": {
            "format": "catboost_oblivious_trees_json@1.0.0",
            "role": "regressor",
            "scale": 1.0,
            "bias": 0.0,
            "oblivious_trees": [{"splits": [], "leaf_values": [0.5]}],
        },
    }

    prediction = inference.predict_row(artifact, {"row_id": "C1", "side": "call", "ret_15": 0.4}, include_intermediates=True)

    assert prediction["side"] == "call_credit"
    assert prediction["probability_positive"] == pytest.approx(1.0 / (1.0 + math.exp(-2.0)))
    assert prediction["conditional_positive_loss"] == pytest.approx(0.5)
    assert prediction["expected_loss_normalized"] == pytest.approx(prediction["probability_positive"] * 0.5)
    assert prediction["intermediates"]["features"] == [0.4, 1.0]
